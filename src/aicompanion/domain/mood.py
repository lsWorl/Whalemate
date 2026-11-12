"""持续情绪（计划书 v4 功能三）：心情由事件推动，并随时间慢慢回归平静。

为什么不再每句话重置：原来每收到一条消息就把心情设回 calm 再按关键词改，被冷落、被欺负的感觉一句话就没了。
现在每种情绪有一个强度（0～100），事件把它推高或压低，强度按半衰期回落；人格的情绪倾向决定
“多容易不高兴”（负面事件的力度）和“多快消气”（负面情绪的半衰期、被哄的效果）。

情绪是短期的，好感是长期的：这里只管心情，不改关系分数（关系由 relationship/service.py 按事件另算）。

数值是第一版默认，跑起来后按体感调整（计划书 v4）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

MOODS = ('happy', 'shy', 'sad', 'angry')          # 和形象、语音、表情包已有的心情词一致；都不明显时是 calm
NEGATIVE = ('sad', 'angry')
LABELS = {'calm': '平静', 'happy': '开心', 'shy': '想撒娇', 'sad': '委屈', 'angry': '生气'}
HALF_LIFE_HOURS = {'happy': 3.0, 'shy': 2.0, 'sad': 6.0, 'angry': 4.0}
SHOW_AT = 20.0        # 强度到这里才算“有这个情绪”
STRONG_AT = 65.0      # 到这里算“很”：语气明显、可能暂时不想多说

# 事件 → 各情绪的变化。正数是加多少强度；负数是按比例压低多少（%），所以被哄是“慢慢消气”，不会一下清零。
EVENTS = {
    'praised':      {'happy': 22},                         # 被夸、被感谢、聊得开心
    'affection':    {'shy': 30, 'happy': 10},              # 被说喜欢、被抱抱
    'teased':       {'angry': 12, 'sad': 8},                # 被阴阳、被损（不是玩笑那种）
    'insulted':     {'angry': 35, 'sad': 10},              # 被骂、被认真攻击
    'ignored':      {'sad': 20},                            # 分享没人理、被冷落
    'reunion':      {'happy': 18, 'shy': 12},               # 好久不见又来了（委屈另按天数加，见 MoodService）
    'achievement':  {'happy': 30},                          # 一起做成了事
    'worried':      {'sad': 10},                            # 对方难过，她跟着揪心
    'apology':      {'angry': -45, 'sad': -40},            # 道歉、哄
    'comforted':    {'sad': -35, 'angry': -15, 'happy': 8},
    'soothed':      {'angry': -100, 'sad': -100},          # 设置里手动“哄好”：直接清零
}
EVENT_LABELS = {'praised': '被夸了 / 聊得开心', 'affection': '被说喜欢', 'teased': '被阴阳了', 'insulted': '被骂了',
                'ignored': '被冷落了', 'reunion': '好久不见', 'achievement': '一起做成了事', 'worried': '担心对方',
                'apology': '对方道歉了', 'comforted': '被哄了', 'soothed': '手动哄好'}


@dataclass
class Mood:
    """各情绪的强度和最后更新时间（秒）。"""
    levels: dict = field(default_factory=dict)
    at: float = 0.0

    def decayed(self, now: float, forgiving: float = 0.5) -> 'Mood':
        """按半衰期回落到 now。消气快的人格负面情绪半衰期更短（0.5～1.5 倍）。"""
        hours = max(0.0, now-self.at)/3600
        levels = {}
        for mood in MOODS:
            value = float(self.levels.get(mood, 0.0))
            if value <= 0: continue
            half = HALF_LIFE_HOURS[mood]*((1.5-forgiving) if mood in NEGATIVE else 1.0)
            value *= math.pow(0.5, hours/max(0.1, half))
            if value >= 1: levels[mood] = round(value, 1)
        return Mood(levels, now)

    def label(self) -> str:
        top = max(MOODS, key=lambda m: self.levels.get(m, 0.0))
        return top if self.levels.get(top, 0.0) >= SHOW_AT else 'calm'

    def intensity(self) -> float:
        return float(self.levels.get(self.label(), 0.0)) if self.label() != 'calm' else 0.0


def apply(mood: Mood, kind: str, now: float, *, touchy: float = 0.5, forgiving: float = 0.5, scale: float = 1.0,
          extra: dict | None = None) -> Mood:
    """在回落后的心情上叠加一个事件。容易不高兴的人格负面事件力度更大（0.6～1.4 倍）；消气快的人格被哄效果更好。"""
    current = mood.decayed(now, forgiving)
    levels = dict(current.levels)
    for name, delta in {**EVENTS.get(kind, {}), **(extra or {})}.items():
        value = float(levels.get(name, 0.0))
        if delta < 0:
            share = 1.0 if kind == 'soothed' else min(1.0, -delta/100*(0.5+forgiving)*scale)
            levels[name] = value*(1-share); continue
        if name in NEGATIVE: delta *= 0.6+0.8*touchy
        levels[name] = min(100.0, value+delta*scale)
    return Mood({k: round(v, 1) for k, v in levels.items() if v >= 1}, now)


def describe(mood: Mood, reason: str = '') -> str:
    """写进提示词的心情说明：现在什么心情、为什么、该怎么表现，以及不能影响什么。"""
    label = mood.label()
    if label == 'calm':
        return '当前心情：平静。'
    strong = mood.intensity() >= STRONG_AT
    because = f'（因为{reason}）' if reason else ''
    text = f"当前心情：{'很' if strong else '有点'}{LABELS[label]}{because}。"
    if label == 'angry':
        text += ('可以带点小脾气：语气冲一点、回得短一点，或者说暂时不太想说话；' if strong else '语气里可以带点不爽，不用装作没事；')
    elif label == 'sad':
        text += '可以表现得委屈一点、想被安慰，但不指责对方；'
    elif label == 'shy':
        text += '可以撒撒娇、黏人一点；'
    elif label == 'happy':
        text += '心情不错，语气可以轻快一些；'
    text += '对方真心道歉或哄你时，可以慢慢消气，不用一下子就好。不管心情如何，对方求助、让你办事或涉及安全的事照常认真回应。'
    return text
