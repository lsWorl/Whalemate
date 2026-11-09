"""收集 QQ 聊天里别人发的表情包，分析含义后放进她自己的图库（用户 2026-11-09 决定）。

负责人的选择：
- 私聊对方和群聊里别人发的表情包都收集；收集来的图分析过含义、判断适合发以后，**任何地方都能用**（桌面、所有群、私聊）；
- 每张新表情用识图模型分析一次含义（按内容哈希，同一张不重复分析），**每天最多 100 张**（设置 sticker_analysis_daily）；
- 分析只是"看懂"，不代表要回复。

边界：
- 只存图片本身（按内容哈希命名），不存发图人的号码、昵称、群号；来源只记"QQ 群 / QQ 私聊"和群的不可逆短哈希。
- 只往用户已选择的图库目录里的"收集的表情"子目录写；没有选图库（仍是仓库自带图）时不收集，不往项目目录写文件。
- 没分析过、分析失败、或模型判断不适合发（色情、血腥、广告二维码、含个人信息、聊天截图、普通照片等）的图不会进候选。
- 上限：收集总数 MAX_COLLECTED、每天新收 DAILY_COLLECT 张；到上限只是不再收新图，不删旧图。
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import threading
import time

COLLECT_DIR = '收集的表情'
LICENSE = 'qq-collected'
MAX_COLLECTED = 3000
DAILY_COLLECT = 300
ANALYSIS_DAILY = 100
MAX_ATTEMPTS = 2
EXT = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif', 'image/webp': '.webp'}
ANALYZING = threading.Lock()


def source_tag(scope: str) -> str:
    """群 / 私聊的不可逆短标记：只用来判断"是不是同一个群学到的"，看不出群号。"""
    return hashlib.sha256(('aicompanion-sticker:'+str(scope)).encode()).hexdigest()[:12]


def meaning_of(metadata: dict) -> str:
    """分析过且适合发的图的含义；其他情况返回空。"""
    if metadata.get('analysis') != 'done' or not metadata.get('suitable', True): return ''
    return str(metadata.get('semantic') or '')[:40]


class StickerCollector:
    def __init__(self, database, catalog, clock=time.time, today=date.today):
        self.db, self.catalog, self.clock, self.today = database, catalog, clock, today

    # ---- 设置 ---------------------------------------------------------------------------
    def enabled(self) -> bool:
        return bool(self.db.get_setting('sticker_collect_enabled', True))

    def analysis_daily(self) -> int:
        try: return max(0, int(self.db.get_setting('sticker_analysis_daily', ANALYSIS_DAILY)))
        except (TypeError, ValueError): return ANALYSIS_DAILY

    def root(self) -> Path | None:
        root = self.catalog.root
        return None if root == self.catalog.builtin_root.resolve() else root

    # ---- 收图 ---------------------------------------------------------------------------
    def known(self, digest: str):
        """按内容哈希找已经在图库里的图（收集来的或用户自己的）。返回 (asset_id, metadata) 或 None。"""
        if not isinstance(digest, str) or len(digest) != 64: return None
        with self.db.transaction() as c:
            row = c.execute('SELECT asset_id,metadata FROM expression_assets WHERE sha256=? ORDER BY imported_at LIMIT 1', (digest,)).fetchone()
        return (row['asset_id'], json.loads(row['metadata'])) if row else None

    def collected_count(self) -> int:
        with self.db.transaction() as c:
            return c.execute("SELECT COUNT(*) FROM expression_assets WHERE json_extract(metadata,'$.collected')=1").fetchone()[0]

    def ingest(self, data: bytes, *, character_id: str, origin: str) -> dict:
        """收一张图。返回 {status, asset_id, meaning, sha256}；status：known / collected / disabled / no_library / full / daily_limit / invalid。
        已经在图库里的图直接认出（不重复写文件、不重复分析）。"""
        digest = hashlib.sha256(data).hexdigest() if data else ''
        found = self.known(digest)
        if found: return {'status': 'known', 'asset_id': found[0], 'meaning': meaning_of(found[1]), 'sha256': digest}
        if not self.enabled(): return {'status': 'disabled', 'sha256': digest}
        root = self.root()
        if root is None: return {'status': 'no_library', 'sha256': digest}
        if self.collected_count() >= MAX_COLLECTED: return {'status': 'full', 'sha256': digest}
        try:
            from aicompanion.bridge.media import inspect_image
            mime = inspect_image(data)
        except Exception:
            return {'status': 'invalid', 'sha256': digest}
        if not self.db.reserve_daily(f'sticker_collect_count:{self.today().isoformat()}', DAILY_COLLECT):
            return {'status': 'daily_limit', 'sha256': digest}
        folder = root/COLLECT_DIR; folder.mkdir(parents=True, exist_ok=True)
        path = folder/(digest[:20]+EXT[mime])
        try:
            with path.open('xb') as out: out.write(data)
        except FileExistsError:
            pass   # 同一张图别的线程刚写好
        metadata = dict(category=COLLECT_DIR, semantic='', license=LICENSE, author='QQ 聊天中收集', source=origin,
                        character_id=character_id, collected=True, analysis='pending', attempts=0, collected_at=round(self.clock()),
                        annotation_defaults=['semantic', 'intents', 'emotions'])
        try: asset_id, _ = self.catalog.register(path, root, metadata)
        except (ValueError, OSError): return {'status': 'invalid', 'sha256': digest}
        return {'status': 'collected', 'asset_id': asset_id, 'meaning': '', 'sha256': digest}

    # ---- 分析 ---------------------------------------------------------------------------
    def pending(self, limit: int = 5) -> list[str]:
        with self.db.transaction() as c:
            rows = c.execute("""SELECT asset_id FROM expression_assets WHERE json_extract(metadata,'$.collected')=1
                 AND json_extract(metadata,'$.analysis')='pending' ORDER BY imported_at LIMIT ?""", (limit,)).fetchall()
        return [r['asset_id'] for r in rows]

    def analyze_pending(self, analyzer, limit: int = 5) -> int:
        """分析排队的图（后台线程调用）。analyzer(data, mime) → dict 或 None（失败）。每张先预留当天额度，额度用完就停。"""
        done = 0
        for asset_id in self.pending(limit):
            if not self.db.reserve_daily(f'sticker_analysis_count:{self.today().isoformat()}', self.analysis_daily()): break
            media = self.catalog.resolve(asset_id)
            result = None
            if media is not None:
                try: result = analyzer(*media)
                except Exception: result = None
            self._apply(asset_id, result); done += 1
        return done

    def _apply(self, asset_id: str, result) -> None:
        def write(c):
            row = c.execute('SELECT metadata FROM expression_assets WHERE asset_id=?', (asset_id,)).fetchone()
            if row is None: return
            metadata = json.loads(row['metadata'])
            if metadata.get('analysis') != 'pending': return
            enabled = None
            if not isinstance(result, dict):
                metadata['attempts'] = int(metadata.get('attempts') or 0)+1
                if metadata['attempts'] >= MAX_ATTEMPTS: metadata['analysis'] = 'failed'
            else:
                suitable = bool(result.get('sticker', True)) and bool(result.get('suitable', True))
                metadata.update(analysis='done', suitable=suitable, analyzed_at=round(self.clock()))
                if not metadata.get('user_annotated'):
                    metadata['semantic'] = str(result.get('meaning') or '')[:40]
                    metadata['intents'] = [str(x)[:20] for x in (result.get('use_when') or [])[:4] if str(x).strip()]
                    metadata['emotions'] = [str(x)[:12] for x in (result.get('emotions') or [])[:3] if str(x).strip()]
                    metadata['image_text'] = str(result.get('text') or '')[:40]
                    metadata['annotation_defaults'] = []
                if not suitable:
                    metadata['unsuitable_reason'] = str(result.get('reason') or '')[:40]; enabled = 0
            c.execute('UPDATE expression_assets SET metadata=? WHERE asset_id=?', (json.dumps(metadata, ensure_ascii=False), asset_id))
            if enabled is not None: c.execute('UPDATE expression_assets SET enabled=? WHERE asset_id=?', (enabled, asset_id))
        self.db.locked(write)

    def stats(self) -> dict:
        with self.db.transaction() as c:
            rows = c.execute("""SELECT json_extract(metadata,'$.analysis') AS state, enabled, COUNT(*) AS n FROM expression_assets
                 WHERE json_extract(metadata,'$.collected')=1 GROUP BY state, enabled""").fetchall()
        result = dict(collected=0, pending=0, ready=0, unsuitable=0, failed=0)
        for row in rows:
            result['collected'] += row['n']
            if row['state'] == 'pending': result['pending'] += row['n']
            elif row['state'] == 'failed': result['failed'] += row['n']
            elif row['state'] == 'done' and row['enabled']: result['ready'] += row['n']
            else: result['unsuitable'] += row['n']
        result['analyzed_today'] = int(self.db.get_setting(f'sticker_analysis_count:{self.today().isoformat()}', 0) or 0)
        return result
