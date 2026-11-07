"""会遗忘的记忆卡（参考 surfcore 的衰减记忆卡 + 全量存档）。

- 清晰度 clarity ∈ [0,1]，按重要度的半衰期衰减；被想起（用在对话里）时回升。
- ≥ 0.6 清楚；0.3–0.6 模糊（注入提示词时标明"印象有点模糊"）；< 0.3 遗忘（移出记忆卡，存档保留）。
- 重要度 5 和身份类事实（名字等）不衰减。
- 记忆卡容量有限，超出时把"重要度 × 清晰度"最低的移出（存档保留）。
- 收件箱里的互动事件（摸头、戳、被甩、摔晕）按天汇总成小记忆，重要度低，很快就会淡忘。
- 作用范围 scope：pet 为私下相处；qq / bili 等为公开场合。公开场合只注入公开记忆。

纯 Python，不依赖 Qt，时间可注入，便于测试。
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime

HALF_LIFE_DAYS = {1: 7, 2: 21, 3: 60, 4: 180}
CLEAR, FUZZY = 0.6, 0.3
CAPACITY = 150
STABLE_CATEGORIES = {'identity'}
PUBLIC_SCOPES = {'public', 'qq', 'bili'}
INTERACTION_TEXT = {
    'pat': ('被你摸了头', '次'), 'poke': ('被你戳', '下'), 'thrown': ('被你甩出去', '次'), 'dizzy': ('被摔晕', '次'),
}


def decayed(clarity: float, importance: int, category: str, days: float) -> float:
    if days <= 0 or importance >= 5 or category in STABLE_CATEGORIES: return clarity
    half_life = HALF_LIFE_DAYS.get(max(1, min(4, int(importance))), 21)
    return clarity*0.5**(days/half_life)


def level(clarity: float) -> str:
    if clarity >= CLEAR: return 'clear'
    if clarity >= FUZZY: return 'fuzzy'
    return 'forgotten'


def visible_in(scope: str, scene: str) -> bool:
    """scene：当前在哪说话。私下（pet）能用全部记忆；公开场合只用公开记忆。"""
    return scene == 'pet' or scope in PUBLIC_SCOPES


def select_for_prompt(rows, query: str = '', limit: int = 8, scene: str = 'pet'):
    """挑出注入提示词的记忆：相关性优先，其次重要度与清晰度。返回 [(row, level, matched)]。"""
    grams = {query[i:i+2] for i in range(len(query)-1)} if query else set()
    picked = []
    for row in rows:
        clarity = float(row['clarity'] if 'clarity' in row.keys() else 1)
        if level(clarity) == 'forgotten' or not visible_in(row['scope'] if 'scope' in row.keys() else 'pet', scene): continue
        matches = sum(gram in row['content'] for gram in grams)
        score = matches*4+row['importance']+row['confidence']+clarity*2
        picked.append((score, row, level(clarity), matches > 0))
    picked.sort(key=lambda item: item[0], reverse=True)
    return [(row, lvl, matched) for _, row, lvl, matched in picked[:limit]]


def render(selected) -> str:
    lines = []
    for row, lvl, _ in selected:
        lines.append(f"- {row['content']}" if lvl == 'clear' else f"- （印象有点模糊，提到时别说得太肯定）{row['content']}")
    return '\n'.join(lines) or '暂无已确认的长期记忆。'


class MemoryOrganizer:
    """定期整理：互动事件 → 小记忆；衰减；遗忘；容量裁剪。"""

    def __init__(self, database, clock=datetime.now):
        self.database, self.clock = database, clock

    def run(self, character_id: str) -> dict:
        now = self.clock()
        report = {'interactions': self._absorb_inbox(character_id), 'faded': 0, 'forgotten': 0, 'trimmed': 0}
        marker = f'memory_decay_at:{character_id}'
        last = self.database.get_setting(marker)
        days = (now-datetime.fromisoformat(last)).total_seconds()/86400 if last else 0.0
        self.database.set_setting(marker, now.isoformat(timespec='seconds'))
        rows = self.database.memory_card(character_id)
        updates, forget = [], []
        for row in rows:
            new = decayed(float(row['clarity']), row['importance'], row['category'], days)
            if level(new) == 'forgotten': forget.append(row['id'])
            elif abs(new-row['clarity']) > 1e-4: updates.append((row['id'], new))
        if updates: self.database.set_clarity(updates); report['faded'] = len(updates)
        if forget: self.database.forget_memories(forget, 'forgotten'); report['forgotten'] = len(forget)
        remaining = [r for r in self.database.memory_card(character_id)]
        if len(remaining) > CAPACITY:
            weakest = sorted(remaining, key=lambda r: r['importance']*float(r['clarity']))[:len(remaining)-CAPACITY]
            self.database.forget_memories([r['id'] for r in weakest], 'trimmed'); report['trimmed'] = len(weakest)
        return report

    def _absorb_inbox(self, character_id: str) -> int:
        events = self.database.inbox(character_id)
        if not events: return 0
        counts = Counter()
        for event in events:
            day = event['created_at'][:10]
            if event['kind'] in INTERACTION_TEXT: counts[(day, event['kind'])] += 1
        for (day, kind), count in counts.items():
            action, unit = INTERACTION_TEXT[kind]
            text = f'{day} 我{action}{count}{unit}' if count > 1 else f'{day} 我{action}'
            # 同一天同一种互动合并为一条，重要度 1，几天后就会淡忘
            self.database.upsert_memory(text, character_id, f'interaction:{kind}:{day}', 'relationship', 1, 1.0)
        self.database.mark_inbox([e['id'] for e in events])
        return len(counts)


def today_start(now: datetime) -> str:
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec='seconds')


