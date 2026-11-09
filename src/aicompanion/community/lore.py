"""T18 群里的共同经历 / 内部梗 / 叫法：跟群走（不跟人走），让她能自然地提起"上次大家……"。

后台关系分析读群聊时顺带输出（relationship/analyzer.py 的 lore 字段），这里去重、加强、半衰。
"""
from __future__ import annotations

import math
import re
import time

from aicompanion.relationship.notes import SENSITIVE, similar

KINDS = {'inside_joke': ('群里的梗', 90), 'event': ('一起经历过', 45), 'nickname': ('群里的叫法', 120), 'interest': ('大家常聊', 30)}
MAX_PER_GROUP = 40


class GroupLore:
    def __init__(self, database, clock=time.time):
        self.db, self.clock = database, clock

    def add(self, character_id: str, channel: str, kind: str, text: str, confidence=0.7) -> str:
        kind = str(kind or '').strip()
        text = re.sub(r'\s+', ' ', str(text or '')).strip()[:60]
        if kind not in KINDS or len(text) < 4: return 'rejected'
        if SENSITIVE.search(text): return 'rejected:sensitive'
        try: confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError): confidence = 0.0
        if confidence < 0.6: return 'rejected:low_confidence'
        now = self.clock()

        def work(c):
            rows = c.execute('SELECT * FROM group_lore WHERE character_id=? AND channel=?', (character_id, channel)).fetchall()
            for row in rows:
                if row['kind'] == kind and similar(row['text'], text):
                    c.execute('UPDATE group_lore SET mentions=mentions+1, weight=MIN(3.0, weight+0.4), last_at=? WHERE id=?', (now, row['id']))
                    return 'reinforced'
            c.execute('INSERT INTO group_lore(character_id,channel,kind,text,weight,first_at,last_at) VALUES (?,?,?,?,?,?,?)',
                      (character_id, channel, kind, text, confidence, now, now))
            rows = [dict(r) for r in c.execute('SELECT * FROM group_lore WHERE character_id=? AND channel=?', (character_id, channel))]
            for stale in sorted(rows, key=self.strength)[:max(0, len(rows)-MAX_PER_GROUP)]:
                c.execute('DELETE FROM group_lore WHERE id=?', (stale['id'],))
            return 'added'
        return self.db.locked(work)

    def strength(self, row) -> float:
        age = max(0.0, (self.clock()-row['last_at'])/86400)
        return row['weight']*math.pow(0.5, age/KINDS.get(row['kind'], ('', 30))[1])

    def items(self, character_id: str, channel: str) -> list[dict]:
        with self.db.transaction() as c:
            rows = [dict(r) for r in c.execute('SELECT * FROM group_lore WHERE character_id=? AND channel=?', (character_id, channel))]
        for r in rows: r['strength'] = round(self.strength(r), 2)
        return sorted(rows, key=lambda r: -r['strength'])

    def forget(self, lore_id: int) -> None:
        self.db.locked(lambda c: c.execute('DELETE FROM group_lore WHERE id=?', (int(lore_id),)))

    def prompt(self, character_id: str, channel: str, limit=5) -> str:
        items = [r for r in self.items(character_id, channel) if r['strength'] >= 0.3][:limit]
        if not items: return ''
        body = '；'.join(f"{KINDS[r['kind']][0]}：{r['text']}" for r in items)
        return (f'\n【这个群的共同记忆】{body}。（你也是群里的一员：合适的时候可以自然地提一句、接上这些梗，'
                '让懂的人会心一笑；不用解释、不用每次都提，没发生过的别编。）')
