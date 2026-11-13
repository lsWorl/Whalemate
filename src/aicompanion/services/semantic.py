"""云端语义召回。向量只是可重建的索引，事实、来源、可见范围始终以当前记忆为准。"""
from array import array
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import threading
import time
from urllib.request import Request

from aicompanion import net
from aicompanion.services import local_embedding as local
from aicompanion.brain.memory_card import visible_in
from aicompanion.domain.cancellation import CancellationToken
from aicompanion.providers.admin import check_base_url
from aicompanion.usage import meter

SETTING = 'semantic_consent'
DIMENSIONS = {
    'text-embedding-v4': (64, 128, 256, 512, 768, 1024, 1536, 2048),
    'qwen3.7-text-embedding': (256, 512, 768, 1024, 1536, 2048, 2560),
    'qwen3.7-text-embedding-flash': (256, 512, 768, 1024),
    'embedding-3': (256, 512, 1024, 2048),
}
BATCH = 10
MIN_SIMILARITY = 0.58


@dataclass(frozen=True)
class Target:
    profile: str
    provider: str
    endpoint: str
    model: str
    dimension: int

    def signature(self):
        return self.provider, self.endpoint, self.model, self.dimension


LOCAL = Target('builtin:bge', 'builtin:local', local.REVISION, local.MODEL, local.DIMENSION)
QUERY_TIMEOUT = 1.5
FALLBACK_LIMIT = 256


def rank_vectors(needle, blobs):
    """输入均为已归一化的 float32。可选 NumPy 在需要打分时才载入。"""
    if not blobs:
        return []
    try:
        import numpy as np
    except ImportError:
        return [(k, sum(x*y for x, y in zip(needle, memoryview(blob).cast('f'), strict=True)))
                for k, blob in blobs[:FALLBACK_LIMIT]]
    matrix = np.frombuffer(b''.join(blob for _, blob in blobs), dtype=np.float32).reshape(len(blobs), len(needle))
    scores = np.einsum('ij,j->i', matrix, np.asarray(needle, dtype=np.float32))
    return [(k, float(score)) for (k, _), score in zip(blobs, scores, strict=True)]


def target_for(router, profile, dimension):
    config = router.store.load()
    model = config.models.get(profile)
    account = config.providers.get(model.provider) if model else None
    if not account or 'embedding' not in model.capabilities or account.kind != 'openai_compatible':
        raise ValueError('请先在“模型与 Key”添加文本向量账户和模型')
    endpoint = check_base_url(account.base_url)
    if (type(dimension) is not int or not 1 <= dimension <= 4096
            or model.model_id in DIMENSIONS and dimension not in DIMENSIONS[model.model_id]):
        raise ValueError('此模型不支持所选维度')
    target = Target(profile, account.id, endpoint, model.model_id, dimension)
    return target, account


def digest(content):
    return sha256(content.encode('utf-8')).hexdigest()


def normalize(values, dimension):
    if not isinstance(values, list) or len(values) != dimension or any(type(v) not in (int, float) for v in values):
        raise ValueError('向量维度或数值无效')
    length = math.hypot(*values)
    if not math.isfinite(length) or length == 0:
        raise ValueError('向量数值无效')
    return array('f', (v / length for v in values))


def embed(target, key, texts, cid, cancellation, *, timeout=4):
    body = {'model': target.model, 'input': texts, 'dimensions': target.dimension}
    request = Request(target.endpoint + '/embeddings', data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                      headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    raw = net.fetch(request, cancellation, timeout=timeout, limit=4*1024*1024)
    reply = json.loads(raw)
    usage = reply.get('usage') or {}
    tokens = usage.get('prompt_tokens') or usage.get('total_tokens')
    with meter.using('embedding', cid):
        meter.report(target.model, {'prompt_tokens': tokens} if tokens else None, sum(map(len, texts)))
    data = reply['data']
    if not isinstance(data, list) or len(data) != len(texts):
        raise ValueError('向量返回数量不符')
    ordered = sorted(data, key=lambda item: item['index'])
    if any(type(row['index']) is not int or row['index'] != index for index, row in enumerate(ordered)):
        raise ValueError('向量序号无效')
    return [normalize(row['embedding'], target.dimension) for row in ordered]


def source_ids(row):
    try:
        ids = json.loads(row['source_ids'])
    except (ValueError, TypeError):
        return None
    if isinstance(ids, list) and len(ids) <= 100 and all(type(i) is int for i in ids):
        return ids
    return None


def message_channels(c, cid, ids):
    ids = list(ids)
    channels = {}
    for offset in range(0, len(ids), 500):
        batch = ids[offset:offset + 500]
        marks = ','.join('?' for _ in batch)
        channels.update(c.execute(f"""SELECT id,channel FROM chat_messages WHERE character_id=? AND status='complete'
                                   AND id IN ({marks})""", (cid, *batch)).fetchall())
    return channels


def origin_for(ids, scope, channels):
    if ids == []:
        return 'desktop' if scope in ('pet', 'private', 'public') else ''
    if not ids or any(mid not in channels for mid in ids):
        return ''
    origins = {channels[mid] for mid in ids}
    return origins.pop() if len(origins) == 1 else ''


def source_channel(c, cid, row):
    ids = source_ids(row)
    return origin_for(ids, row['scope'], message_channels(c, cid, ids or []))


def shared_here(c, cid, row, channel):
    if channel == 'desktop' or row['scope'] not in ('qq', 'bili'):
        return True
    # 迁移前没有消息编号的共享卡片沿用当时的可见范围；不把这些卡片发往向量服务。
    return row['source_ids'] == '[]' or source_channel(c, cid, row) == channel


def candidates(c, cid):
    from aicompanion.services.recall import SCAN_ARCHIVE
    active = c.execute('SELECT * FROM memories WHERE character_id=? ORDER BY id DESC', (cid,)).fetchall()
    archived = c.execute("""SELECT * FROM memory_archive WHERE character_id=? AND reason IN ('forgotten','trimmed')
                            ORDER BY id DESC LIMIT ?""", (cid, SCAN_ARCHIVE)).fetchall()
    seen = {r[0] for r in c.execute('SELECT fact_key FROM memory_tombstones WHERE character_id=?', (cid,))}
    channels = message_channels(c, cid, {mid for r in (*active, *archived) for mid in source_ids(r) or []})
    for old, records in ((False, active), (True, archived)):
        for row in records:
            key = row['fact_key']
            if key in seen:
                continue
            seen.add(key)
            if not old and row['clarity'] < 0.3:
                continue
            channel = origin_for(source_ids(row), row['scope'], channels)
            if channel and visible_in(row['scope'], 'pet' if channel == 'desktop' else 'qq') and row['content'].strip():
                yield key, (row, old, channel)


class SemanticRecall:
    def __init__(self, db, router, client=embed, clock=time.monotonic):
        self.db, self.router, self.client, self.clock = db, router, client, clock
        self.quota = meter.Quota(db)
        self.lock = threading.RLock()
        self.worker = None
        self.wake = threading.Event()
        self.stop_token = CancellationToken()
        self.retry_at = 0
        self.failures = 0
        self.state = '未开启，使用关键词召回'
        self.index_states = {}
        self.local_root = db.database_path.parent / 'models' / local.MODEL / local.REVISION
        self.encoder = None
        self.loader_token = None
        self.loader = None
        self.requests = set()
        self.local_error = False

    def enable(self, profile, dimension):
        target, account = target_for(self.router, profile, dimension)
        if not self.router.credentials.get(account.credential, endpoint=target.endpoint):
            raise ValueError('请先在“模型与 Key”配置此账户的 Key')
        # 同意与实际目的地址、模型、维度绑定；账户改地址后必须重新确认。
        self._select(target)
        self.start()

    def _select(self, target):
        choice = vars(target) if target else None
        def select(c):
            old = self.db.setting_in(c, SETTING)
            if choice and old != choice:
                for row in c.execute('SELECT id FROM characters'):
                    # 仅已有不同签名的向量时等待整批切换。日常新增记忆继续使用有效部分。
                    changed = c.execute("""SELECT 1 FROM memory_vectors WHERE character_id=? AND
                        (provider,endpoint,model,dimension) != (?,?,?,?) LIMIT 1""",
                        (row[0], *target.signature())).fetchone()
                    self.db.set_setting_in(c, 'semantic_rebuild:' + row[0], choice if changed else None)
            self.db.set_setting_in(c, SETTING, choice)
        with self.lock:
            self.db.locked(select)
            for request in self.requests:
                request.cancel()
            if self.loader_token:
                self.loader_token.cancel()
            self.encoder = None
            self.local_error = False
            self.retry_at = self.failures = 0
            self.index_states.clear()
            self.state = '等待后台建索引' if target else '未开启，使用关键词召回'
            self.wake.set()

    def enable_local(self, *, download=True):
        with self.lock:
            self._select(LOCAL)
            self._load_local(download)
        self.start()

    def _load_local(self, download):
        with self.lock:
            if (self.stop_token.event.is_set() or self.encoder or self.local_error
                    or self.db.get_setting(SETTING) != vars(LOCAL)):
                return
            if self.loader and self.loader.is_alive() and not self.loader_token.event.is_set():
                return
            token = CancellationToken()
            self.loader_token = token
            self.state = '正在准备本地模型，使用关键词召回'

            def progress(message):
                with self.lock:
                    if self.loader_token is token and not token.event.is_set():
                        self.state = message

            def load():
                try:
                    encoder = local.LocalEncoder(self.local_root, token, download, progress)
                except Exception as error:
                    with self.lock:
                        if self.loader_token is token and not token.event.is_set():
                            self.local_error = True
                            self.state = (str(error) if isinstance(error, local.ModelFilesError)
                                          else '本地模型未能加载，请检查下载或运行库后重试') + '，使用关键词召回'
                    return
                with self.lock:
                    if self.loader_token is token and not token.event.is_set():
                        self.encoder = encoder
                        self.state = '本地模型已就绪，正在检查索引'
                        self.wake.set()
            self.loader = threading.Thread(target=load, daemon=True, name='aicompanion-local-model')
            self.loader.start()

    def disable(self):
        self._select(None)

    def target(self):
        choice = self.db.get_setting(SETTING)
        if not choice:
            self.state = '未开启，使用关键词召回'
            return None
        if choice == vars(LOCAL):
            if self.encoder is None:
                self._load_local(False)  # 重启只从磁盘加载，重新下载必须由用户点击。
                return None
            return (LOCAL, self.encoder) if self.clock() >= self.retry_at else None
        if getattr(self.router, 'offline', True) or not hasattr(self.router, 'store'):
            self.state = '离线，使用关键词召回'
            return None
        try:
            target, account = target_for(self.router, choice['profile'], choice['dimension'])
            if vars(target) != choice:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            self.state = '模型或地址已变更，请重新确认'
            return None
        if not self.quota.allowed('embedding'):
            self.state = '今日额度已用完，使用关键词召回'
            return None
        key = self.router.credentials.get(account.credential, endpoint=target.endpoint)
        if not key:
            self.state = '缺少 Key，使用关键词召回'
            return None
        if self.clock() < self.retry_at:
            return None
        return target, key

    def start(self):
        if not self.db.get_setting(SETTING):
            return
        with self.lock:
            self.wake.set()
            if self.worker is None or not self.worker.is_alive():
                self.worker = threading.Thread(target=self._run, daemon=True, name='aicompanion-semantic')
                self.worker.start()

    def close(self):
        with self.lock:
            self.stop_token.cancel()
            for request in self.requests:
                request.cancel()
            if self.loader_token:
                self.loader_token.cancel()
        self.wake.set()
        if self.loader:
            self.loader.join(1)
        if self.worker:
            self.worker.join(5)

    def _run(self):
        while not self.stop_token.event.is_set():
            self.wake.clear()
            try:
                # 每轮每个角色一批，剩余工作下轮继续。写入通过下一次轮询发现。
                pending = False
                if not self.db.get_setting(SETTING):
                    with self.lock:
                        if not self.db.get_setting(SETTING):
                            self.worker = None
                            return
                for character in self.db.characters():
                    if self.stop_token.event.is_set():
                        return
                    pending |= self.index_batch(character.id)
            except Exception:
                self._failed()
                pending = False
            self.wake.wait(0.15 if pending else 10)

    def _encode(self, target, key, texts, cid, cancellation, *, query=False):
        if target == LOCAL:
            return key.encode(texts, cancellation, query=query)
        token = CancellationToken()
        with self.lock:
            if self.db.get_setting(SETTING) != vars(target) or self.stop_token.event.is_set():
                return []
            self.requests.add(token)
            cancellation.register(token.cancel)
        try:
            token.check()
            if query and self.client is embed:
                return embed(target, key, texts, cid, token, timeout=QUERY_TIMEOUT)
            return self.client(target, key, texts, cid, token)
        finally:
            cancellation.unregister(token.cancel)
            with self.lock:
                self.requests.discard(token)

    def _failed(self):
        with self.lock:
            self.failures = min(self.failures + 1, 7)
            self.retry_at = self.clock() + min(300, 3 * 2**(self.failures - 1))
            self.state = '向量服务暂不可用，使用关键词召回，稍后自动重试'

    def _snapshot(self, cid, target, *, vectors=False):
        with self.db.transaction() as c:
            c.execute('BEGIN')
            rows = dict(candidates(c, cid))
            columns = 'fact_key, content_hash, vector' if vectors else 'fact_key, content_hash'
            indexed = {r['fact_key']: r for r in c.execute(
                f"""SELECT {columns} FROM memory_vectors
                   WHERE character_id=? AND provider=? AND endpoint=? AND model=? AND dimension=?""",
                (cid, *target.signature()))}
        missing = [key for key, (row, _, _) in rows.items()
                   if key not in indexed or indexed[key]['content_hash'] != digest(row['content'])]
        return rows, indexed, missing

    def index_batch(self, cid):
        resolved = self.target()
        if not resolved:
            return False
        target, key = resolved
        rows, _, missing = self._snapshot(cid, target)
        self.index_states[cid] = len(missing)
        remaining = sum(self.index_states.values())
        self.state = f'正在建索引，剩余 {remaining} 条' if remaining else '向量索引就绪'
        if not missing:
            if self.db.get_setting('semantic_rebuild:' + cid) == vars(target):
                def finish(c):
                    if self.db.setting_in(c, SETTING) == vars(target):
                        self.db.set_setting_in(c, 'semantic_rebuild:' + cid, None)
                self.db.locked(finish)
            return False
        channel = rows[missing[0]][2]
        keys = [k for k in missing if rows[k][2] == channel][:BATCH]
        texts = [rows[k][0]['content'] for k in keys]
        if self.db.get_setting(SETTING) != vars(target):
            return False
        try:
            vectors = self._encode(target, key, texts, cid, self.stop_token)
            if not vectors:
                return False
        except Exception:
            if self.db.get_setting(SETTING) == vars(target):
                self._failed()
            return False
        def save(c):
            if self.db.setting_in(c, SETTING) != vars(target):
                return
            current = dict(candidates(c, cid))
            for k, text, vec in zip(keys, texts, vectors, strict=True):
                if k in current and current[k][0]['content'] == text and current[k][2] == channel:
                    c.execute('INSERT OR REPLACE INTO memory_vectors VALUES (?,?,?,?,?,?,?,?)',
                              (cid, k, *target.signature(), digest(text), vec.tobytes()))
        self.db.locked(save)
        self.failures = 0
        return True

    def search(self, cid, query, scene, channel, cancellation):
        target = None
        try:
            resolved = self.target()
            if not resolved or not query.strip():
                return []
            target, key = resolved
            rows, indexed, missing = self._snapshot(cid, target, vectors=True)
            if missing:
                self.start()
                if self.db.get_setting('semantic_rebuild:' + cid) == vars(target):
                    return []
            stale = set(missing)
            allowed = [k for k, (row, _, origin) in rows.items()
                       if k not in stale and origin == channel and visible_in(row['scope'], scene)]
            if not allowed:
                return []
            if self.db.get_setting(SETTING) != vars(target):
                return []
            vectors = self._encode(target, key, [query[:2000]], cid, cancellation, query=True)
            if not vectors:
                return []
            needle, = vectors
            if self.db.get_setting(SETTING) != vars(target):
                return []
            hits = []
            blobs = [(k, indexed[k]['vector']) for k in allowed]
            for k, similarity in rank_vectors(needle, blobs):
                if similarity >= MIN_SIMILARITY:
                    hits.append((k, indexed[k]['content_hash'], similarity))
            self.failures = 0
            return sorted(hits, key=lambda item: item[2], reverse=True)[:12]
        except Exception:
            if not cancellation.event.is_set() and target and self.db.get_setting(SETTING) == vars(target):
                self._failed()
            return []
