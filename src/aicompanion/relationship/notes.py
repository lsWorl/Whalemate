"""她记得的关于每个人的事：喜好、雷点、相处习惯、身份背景、一起经历过的事、最近的状况。

负责人 2026-11-09：不需要场景隔离——在某个群里了解到经常聊天的人的喜好也该记下，但要和这个人的平台身份绑定；
而且不能什么都记，要区分。参考：
- MaiBot（Mai-with-u/MaiBot）人物画像把证据分成身份、关系、稳定事实、互动偏好、近期互动、存疑等桶，
  "临时状态、计划、可能、玩笑、自嘲"不能升级为长期事实；
- mem0 的记忆更新：新信息和已有记忆对比后决定新增、更新、删除还是不变。

所以这里：
- 每条记忆绑定 person_id（= 平台 + 机器人账号 + 用户号），跨群、私聊通用，origin 只记"在哪类场合知道的"；
- 分类不同，保留规则不同：喜好 / 雷点 / 相处习惯半衰期半年，经历一年，"最近的状况"一周左右就淡、三周后忘掉；
- 再次提到会强化（权重上升），喜好变了会更新或忘掉旧的（喜欢 ↔ 不喜欢同一样东西时只留新的）；
- 不记：隐私敏感信息（联系方式、证件、账号密码、住址、银行卡等，模型被要求不输出，应用再用规则拦一道）、
  别人转述的关于他的事（除非是一起经历的）、玩笑反话、一时情绪。
"""
from __future__ import annotations

from difflib import SequenceMatcher
import math
import re
import time

MIN_CONFIDENCE = 0.6
MAX_PER_PERSON = 60
# 分类：名称、提示里最多列几条、半衰期（天）
CATEGORIES = {
    'like': ('喜欢', 5, 180),
    'dislike': ('不喜欢 / 雷点', 3, 180),
    'style': ('相处习惯', 3, 180),
    'identity': ('身份背景', 3, 365),
    'experience': ('你们一起经历过', 3, 365),
    'recent': ('他最近', 2, 7),
}
RECENT_FORGET_DAYS = 21
OPPOSITE = {'like': 'dislike', 'dislike': 'like'}

# 应用层最后一道拦截：明显的隐私标识不进记忆，哪怕模型输出了
SENSITIVE = re.compile(
    r'\d{5,}|[\w.+-]+@[\w-]+\.\w+|密码|验证码|账号|帐号|身份证|银行卡|卡号|手机号|电话号|住址|门牌|'
    r'住在.{0,12}(路|街|号|小区|栋|单元|室)|病史|确诊|抑郁症|怀孕|性取向|宗教信仰|政治立场|工资|存款|欠款')


def similar(a: str, b: str) -> bool:
    a, b = a.strip().lower(), b.strip().lower()
    if not a or not b: return False
    if a in b or b in a: return min(len(a), len(b)) >= 2
    return SequenceMatcher(None, a, b).ratio() >= 0.75


class PersonNotes:
    def __init__(self, database, clock=time.time):
        self.db, self.clock = database, clock

    # ---- 写入（模型判断 → 应用规则把关） ------------------------------------------------------------------
    def apply(self, character_id: str, person_id: int, *, op='add', category='', text='', confidence=0.0,
              ref=None, origin='', self_said=True) -> str:
        """返回结果：added / reinforced / updated / forgotten / rejected:原因。"""
        op = str(op or 'add').strip().lower()
        category = str(category or '').strip().lower()
        text = re.sub(r'\s+', ' ', str(text or '')).strip()[:40]
        try: confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError): confidence = 0.0
        if confidence < MIN_CONFIDENCE: return 'rejected:low_confidence'
        now = self.clock()

        def work(c):
            if op in ('forget', 'update'):
                row = c.execute('SELECT * FROM person_notes WHERE id=? AND character_id=? AND person_id=?',
                                (int(ref or 0), character_id, person_id)).fetchone() if str(ref or '').isdigit() else None
                if row is None: return 'rejected:no_such_note'
                if op == 'forget':
                    c.execute('DELETE FROM person_notes WHERE id=?', (row['id'],)); return 'forgotten'
                reason = self._invalid(category or row['category'], text, self_said)
                if reason: return 'rejected:'+reason
                c.execute('UPDATE person_notes SET category=?, text=?, weight=MAX(weight,?), last_at=? WHERE id=?',
                          (category or row['category'], text, confidence, now, row['id']))
                return 'updated'
            reason = self._invalid(category, text, self_said)
            if reason: return 'rejected:'+reason
            rows = c.execute('SELECT * FROM person_notes WHERE character_id=? AND person_id=?', (character_id, person_id)).fetchall()
            for row in rows:   # 喜好变了：喜欢 ↔ 不喜欢同一样东西，只留新的
                if row['category'] == OPPOSITE.get(category) and similar(row['text'], text):
                    c.execute('DELETE FROM person_notes WHERE id=?', (row['id'],))
            for row in rows:   # 再次提到：强化，不重复记
                if row['category'] == category and similar(row['text'], text):
                    c.execute('UPDATE person_notes SET mentions=mentions+1, weight=MIN(3.0, weight+0.5*?), last_at=?, origin=? WHERE id=?',
                              (confidence, now, origin or row['origin'], row['id']))
                    return 'reinforced'
            c.execute('INSERT INTO person_notes(character_id,person_id,category,text,weight,origin,first_at,last_at) VALUES (?,?,?,?,?,?,?,?)',
                      (character_id, person_id, category, text, confidence, origin, now, now))
            self._prune(c, character_id, person_id)
            return 'added'
        return self.db.locked(work)

    @staticmethod
    def _invalid(category, text, self_said) -> str:
        if category not in CATEGORIES: return 'category'
        if len(text) < 2: return 'empty'
        if SENSITIVE.search(text): return 'sensitive'
        if not self_said and category != 'experience': return 'hearsay'   # 别人说他喜欢什么，不当真
        return ''

    def _prune(self, c, character_id, person_id):
        rows = [dict(r) for r in c.execute('SELECT * FROM person_notes WHERE character_id=? AND person_id=?', (character_id, person_id))]
        stale = [r for r in rows if self.strength(r) <= 0]
        keep = sorted((r for r in rows if self.strength(r) > 0), key=self.strength, reverse=True)
        for r in stale+keep[MAX_PER_PERSON:]:
            c.execute('DELETE FROM person_notes WHERE id=?', (r['id'],))

    def merge(self, c, source: int, target: int) -> int:
        """把 source 这个人的记忆复制给 target（主人改绑时用：之前当群友认识他时记下的喜好，本来就是他的）。
        source 自己的记忆不动，解绑还原时还在。相似的合并为一条（提到次数相加、取较清楚的权重）。返回新增条数。"""
        added = 0
        targets = [dict(r) for r in c.execute('SELECT * FROM person_notes WHERE person_id=?', (target,))]
        for note in c.execute('SELECT * FROM person_notes WHERE person_id=?', (source,)).fetchall():
            same = next((t for t in targets if t['character_id'] == note['character_id'] and t['category'] == note['category']
                         and similar(t['text'], note['text'])), None)
            if same:
                c.execute('UPDATE person_notes SET mentions=mentions+?, weight=MIN(3.0, MAX(weight, ?)), last_at=MAX(last_at, ?) WHERE id=?',
                          (note['mentions'], note['weight'], note['last_at'], same['id']))
            else:
                c.execute('INSERT INTO person_notes(character_id,person_id,category,text,weight,mentions,origin,first_at,last_at) VALUES (?,?,?,?,?,?,?,?,?)',
                          (note['character_id'], target, note['category'], note['text'], note['weight'], note['mentions'], note['origin'],
                           note['first_at'], note['last_at']))
                added += 1
        return added

    # ---- 读取 ------------------------------------------------------------------------------------
    def strength(self, row) -> float:
        """此刻这条记忆还有多清楚：权重 × 半衰。'最近的状况'三周后直接忘掉。"""
        age = max(0.0, (self.clock()-row['last_at'])/86400)
        if row['category'] == 'recent' and age > RECENT_FORGET_DAYS: return 0.0
        half_life = CATEGORIES.get(row['category'], ('', 0, 30))[2]
        return row['weight']*math.pow(0.5, age/half_life)

    def items(self, character_id: str, person_id: int) -> list[dict]:
        with self.db.transaction() as c:
            rows = [dict(r) for r in c.execute('SELECT * FROM person_notes WHERE character_id=? AND person_id=?', (character_id, person_id))]
        for r in rows: r['strength'] = round(self.strength(r), 2)
        return sorted(rows, key=lambda r: r['strength'], reverse=True)

    def forget(self, note_id: int) -> None:
        self.db.locked(lambda c: c.execute('DELETE FROM person_notes WHERE id=?', (int(note_id),)))

    def for_analyzer(self, character_id: str, person_id: int, limit=10) -> list[dict]:
        return [r for r in self.items(character_id, person_id) if r['strength'] > 0][:limit]

    def prompt(self, character_id: str, person_id: int) -> str:
        groups = {}
        for r in self.items(character_id, person_id):
            if r['strength'] < 0.25: continue
            name, limit, _ = CATEGORIES.get(r['category'], ('', 0, 0))
            if name and len(groups.setdefault(name, [])) < limit: groups[name].append(r['text'])
        if not groups: return ''
        body = '；'.join(f'{name}：'+'、'.join(texts) for name, texts in groups.items())
        return f'你记得关于他的：{body}。（自然地用，不用每次都提；没记下的别编。）'
