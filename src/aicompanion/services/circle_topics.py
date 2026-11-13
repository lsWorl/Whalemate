"""长期话题跟着群和角色走。只保存有本群原话支撑的观察，不把它们写进共享记忆。"""
from __future__ import annotations

import re
import time
import unicodedata
from collections import defaultdict
from contextlib import nullcontext
from datetime import date, datetime, timezone

from aicompanion.storage.database import Database

INTENT = '接续原群的长期话题'
GROUP = re.compile(r'qq:group:(\d{5,12})')
COLD_SECONDS = 30 * 60
RECENT_DAYS = 7
TOP_COUNT = 3


def allowed(c, channel):
    match = GROUP.fullmatch(channel)
    return bool(match and match[1] in map(str, Database.setting_in(c, 'bridge_groups', [])))


def switch_key(cid, channel):
    return f'circle_enabled:{cid}:{channel}'


def cutoff(c, cid, channel):
    row = c.execute('SELECT through_id FROM circle_tombstones WHERE character_id=? AND channel=?',
                    (cid, channel)).fetchone()
    return max(row[0] if row else 0, Database.setting_in(c, f'context_after:{cid}', 0) or 0)


def _text(value, limit):
    return ' '.join(value.split())[:limit] if isinstance(value, str) else ''


def _stamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


def keywords(rows, known):
    """先认本群出现过的社区词、标题和英文词，再找两条不同原话里重复的中文短语。"""
    hits = defaultdict(dict)
    preferred = set()
    for row in rows:
        line = re.sub(r'^.{0,80}?#\d+[：:]', '', row['content'])[:500]
        terms = {term for term in known if term and term in line}
        terms.update(re.findall(r'[《#]([^#《》\n]{2,24})[》#]', line))
        terms.update(re.findall(r'\b[A-Za-z][A-Za-z0-9_+-]{2,23}\b', line, re.ASCII))
        for term in terms:
            preferred.add(term.casefold())
            hits[term.casefold()][row['id']] = (term, line)
        for chunk in re.findall(r'[\u4e00-\u9fff]{3,}', line):
            for size in range(3, min(len(chunk), 8) + 1):
                for start in range(len(chunk) - size + 1):
                    term = chunk[start:start + size]
                    if term in ('今天的', '有没有', '我觉得', '大家好', '不知道', '哈哈哈') or len(set(term)) == 1:
                        continue
                    hits[term][row['id']] = (term, line)
    candidates = sorted((k for k in hits if len(hits[k]) >= 2),
                        key=lambda k: (k in preferred, len(hits[k]), len(k)), reverse=True)
    result = []
    selected = []
    for key in candidates:
        ids = set(hits[key])
        if any(key in prior or (ids == prev and key not in preferred) for prior, prev in selected):
            continue
        name, line = next(reversed(hits[key].values()))
        result.append(dict(name=name, summary='最近群里提到：' + _text(line, 80), source_ids=sorted(ids)))
        selected.append((key, ids))
        if len(result) == 6:
            break
    return result


def private_facts(c, cid):
    return [r[0] for r in c.execute("SELECT content FROM memories WHERE character_id=? AND scope='private'", (cid,))]


def visible(name, summary, private):
    from aicompanion.services.memory_service import similar
    return not any(similar(name + ' ' + summary, fact) for fact in private)


class CircleTopics:
    def __init__(self, database, clock=time.time):
        self.db = database
        self.clock = clock

    def collect(self, cid, channel, rows, proposals=None, *, analyzed_after=None):
        """用后台整理实际看到的用户消息核对出处。重复批次不会增加天数或热度。"""
        seen = {r['id']: r for r in rows if r['role'] == 'user' and r['channel'] == channel and r['character_id'] == cid}
        if not seen:
            return []
        if proposals is None:
            with self.db.transaction() as c:
                if not allowed(c, channel):
                    return []
                floor = cutoff(c, cid, channel)
                terms = [r[0] for r in c.execute(
                    'SELECT t.term FROM lexicon_terms t JOIN lexicon_sightings s ON t.id=s.term_id '
                    'WHERE s.channel=? ORDER BY s.last_at DESC LIMIT 100', (channel,))]
            # 频次分析在写锁外做，以免一批长群消息占住桌面聊天的数据库写入。
            proposals = keywords((r for mid, r in seen.items() if mid > floor), terms)
        if not isinstance(proposals, list) or not proposals:
            return []

        def write(c):
            if not allowed(c, channel):
                return []
            floor = cutoff(c, cid, channel)
            if analyzed_after is not None and floor != analyzed_after:
                return  # 生成时读过现已删除的资料，即使模型只引用了新消息也要整批丢弃。
            private = private_facts(c, cid)
            saved = []
            for item in proposals[:8]:
                if not isinstance(item, dict):
                    continue
                name = _text(item.get('name'), 32)
                summary = _text(item.get('summary'), 140)
                ids = item.get('source_ids')
                if not name or not summary or not isinstance(ids, list) or not ids or len(ids) > 40:
                    continue
                if any(type(i) is not int or i not in seen or i <= floor for i in ids):
                    continue
                if not visible(name, summary, private):
                    continue
                key = unicodedata.normalize('NFKC', name).casefold()
                tid = c.execute('INSERT INTO circle_topics(character_id,channel,name,name_key,summary,summary_through) '
                                'VALUES (?,?,?,?,?,?) ON CONFLICT(character_id,channel,name_key) DO UPDATE SET '
                                'summary=CASE WHEN excluded.summary_through>=summary_through THEN excluded.summary ELSE summary END,'
                                'summary_through=max(summary_through,excluded.summary_through) RETURNING id',
                                (cid, channel, name, key, summary, max(ids))).fetchone()[0]
                saved.append((summary, set(ids)))
                for mid in set(ids):
                    row = seen[mid]
                    ts = _stamp(row['created_at'])
                    # 同一发送者在这个群的首条消息 id 充当群内 person_id，跨群时一定不同。
                    person = None
                    if row['sender']:
                        person = c.execute("SELECT min(id) FROM chat_messages WHERE character_id=? AND channel=? "
                                           "AND sender=? AND role='user'", (cid, channel, row['sender'])).fetchone()[0]
                    c.execute('INSERT OR IGNORE INTO circle_evidence VALUES (?,?,?,?,?)',
                              (tid, mid, person, datetime.fromtimestamp(ts).date().isoformat(), ts))
            return saved
        return self.db.locked(write)

    def rows(self, cid, channel, *, connection=None):
        with (self.db.transaction() if connection is None else nullcontext(connection)) as c:
            if not allowed(c, channel):
                return []
            today = datetime.fromtimestamp(self.clock()).date()
            floor = self.db.setting_in(c, f'context_after:{cid}', 0) or 0
            daily = c.execute('SELECT t.id,t.name,t.summary,e.day,count(*) AS n,max(seen_at) AS last '
                              'FROM circle_topics t JOIN circle_evidence e ON e.topic_id=t.id '
                              'WHERE t.character_id=? AND t.channel=? AND e.message_id>? AND t.summary_through>? '
                              'GROUP BY t.id,e.day', (cid, channel, floor, floor))
            topics = {}
            for r in daily:
                topic = topics.setdefault(r['id'], dict(id=r['id'], name=r['name'], summary=r['summary'],
                                           last=0, days=0, recent_days=0, heat=0.0))
                age = max(0, (today - date.fromisoformat(r['day'])).days)
                topic['days'] += 1
                topic['recent_days'] += int(age < RECENT_DAYS)
                topic['heat'] += min(r['n'], 3) * 0.5 ** (age / RECENT_DAYS)
                topic['last'] = max(topic['last'], r['last'])
            private = private_facts(c, cid) if topics else []
            public = (t for t in topics.values() if visible(t['name'], t['summary'], private))
            return sorted(public, key=lambda r: (r['heat'], r['last'], r['id']), reverse=True)

    def sources(self, cid, channel, tid):
        with self.db.transaction() as c:
            if not allowed(c, channel):
                return []
            return c.execute('SELECT m.id,m.content,e.person_id FROM circle_topics t JOIN circle_evidence e ON e.topic_id=t.id '
                             'JOIN chat_messages m ON m.id=e.message_id WHERE t.character_id=? AND t.channel=? AND t.id=? '
                             'AND m.id>? ORDER BY m.id DESC', (cid, channel, tid, self.db.setting_in(c,f'context_after:{cid}',0))).fetchall()

    def remove(self, cid, channel, tid=None):
        def forget(c):
            if not allowed(c, channel):
                return
            if tid is not None and not c.execute('SELECT 1 FROM circle_topics WHERE character_id=? AND channel=? AND id=?',
                                                (cid, channel, tid)).fetchone():
                return
            # 整群共用提取边界，模型即使给旧话题另起名字也不能复活。其余已存的话题仍可用。
            c.execute('INSERT OR REPLACE INTO circle_tombstones SELECT ?,?,coalesce(max(id),0) FROM chat_messages '
                      'WHERE character_id=? AND channel=?', (cid, channel, cid, channel))
            c.execute('DELETE FROM circle_topics WHERE character_id=? AND channel=? AND (? IS NULL OR id=?)',
                      (cid, channel, tid, tid))
        self.db.locked(forget)

    def prompt(self, cid, channel):
        rows = self.rows(cid, channel)[:TOP_COUNT]
        if not rows:
            return ''
        return '\n本群反复聊过的话题（仅作背景，有关联时自然接上）：\n' + '\n'.join(
            f"- {r['name']}：{r['summary']}" for r in rows)

    def available(self, c, cid, channel):
        if not allowed(c, channel) or not self.db.setting_in(c, switch_key(cid, channel), False):
            return []
        now = self.clock()
        if not self.db.setting_in(c, 'proactive_enabled', True) or self.db.setting_in(c, 'bridge_chime', 'often') == 'off':
            return []
        from aicompanion.brain.focus import in_quiet_hours, is_focused
        local = datetime.fromtimestamp(now)
        if in_quiet_hours(self.db, local.hour) or is_focused(self.db, local):
            return []
        last = c.execute("SELECT id,created_at FROM chat_messages WHERE character_id=? AND channel=? AND role='user' "
                         'ORDER BY id DESC LIMIT 1', (cid, channel)).fetchone()
        last_spoke = self.db.setting_in(c, f'bridge_last_spoke:{channel}', 0) or 0
        if not last or now - max(_stamp(last[1]), last_spoke) < COLD_SECONDS:
            return []
        rows = self.rows(cid, channel, connection=c)
        for row in rows:
            row['last_message'] = last['id']
        return [r for r in rows if r['recent_days'] >= 2 and
                self.db.setting_in(c, f"circle_silent:{r['id']}", 0) < last['id']]

    def already_chosen(self, c, cid, key):
        # 插件可能在午夜前领取、午夜后送达。选中日和实际首达日都不能再发同一话题。
        prefix = key.rsplit(':', 1)[0] + ':'
        midnight = datetime.fromtimestamp(self.clock()).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        return c.execute("SELECT 1 FROM outreach_log WHERE character_id=? AND source='circle' "
                         "AND (key=? OR (key GLOB ? AND sent_at>=?)) LIMIT 1",
                         (cid, key, prefix + '*', midnight)).fetchone()

    def due(self, cid, ready):
        day = datetime.fromtimestamp(self.clock()).date().isoformat()
        candidates = []
        with self.db.transaction() as c:
            for group in self.db.setting_in(c, 'bridge_groups', []):
                channel = 'qq:group:' + str(group)
                for row in self.available(c, cid, channel):
                    key = f"circle:{group}:{row['id']}:{day}"
                    if not self.already_chosen(c, cid, key):
                        candidates.append((row['heat'], row['last'], channel, key, str(group)))
                        break
        for _, _, channel, key, group in sorted(candidates, reverse=True):
            if ready(group):
                return channel, key

    def active(self, c, cid, channel, key):
        day = datetime.fromtimestamp(self.clock()).date().isoformat()
        group = channel.removeprefix('qq:group:')
        return next((row for row in self.available(c, cid, channel)
                     if key == f"circle:{group}:{row['id']}:{day}"), None)
