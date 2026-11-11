"""持续情绪的存取与事件来源（计划书 v4 功能三）。

事件从哪来（尽量不额外调用模型）：
- 她这一轮回复里顺带写的 <mind at voice tone feel toward/>：同一个大脑已经判断了“冲谁、是不是转述、玩笑还是认真、让她高兴还是不爽”，
  这里直接换算成心情事件（chat_controller._after_turn 调用）；
- 用户消息里明显的词（道歉、喜欢、谢谢、难过……）：作为轻量兜底，力度减半；
- 好久没见又来了；一起把一件事办成了（办事完成）；设置里手动“哄好”。

结果写回 emotion_state.mood（心情词），形象、语音语气、表情包选择这些已有的地方照常读它。
"""
from __future__ import annotations

import json
import re
import time

from aicompanion.domain.mood import EVENT_LABELS, EVENTS, NEGATIVE, Mood, apply, describe

KEEP_EVENTS = 200
REUNION_HOURS = 20.0
_WORDS = (
    ('apology', re.compile(r'对不起|抱歉|我错了|别生气|消消气|不生气了|哄哄你|原谅我')),
    ('affection', re.compile(r'喜欢你|爱你|抱抱|亲亲|贴贴|想你了?|可爱')),
    ('praised', re.compile(r'谢谢|多谢|辛苦了|厉害|真棒|好棒|太好了|干得漂亮|开心')),
    ('teased', re.compile(r'讨厌你|笨蛋|闭嘴|烦死了|滚开?|走开|没用')),
    ('worried', re.compile(r'难受|低落|失落|伤心|想哭|好累|崩溃|不开心')),
)
CHANNEL_SCALE = (('qq:group:', 0.5), ('qq:', 0.8))


def channel_scale(channel: str) -> float:
    return next((s for prefix, s in CHANNEL_SCALE if (channel or '').startswith(prefix)), 1.0)


class MoodService:
    def __init__(self, database, clock=time.time):
        self.db, self.clock = database, clock

    # ---- 读取 ------------------------------------------------------------------------------
    def temper(self, character_id: str) -> tuple[float, float]:
        """(容易不高兴, 消气快)，来自当前人格（功能二）；没有人格时各 0.5。"""
        try:
            from aicompanion.services.persona_service import PersonaService
            persona = PersonaService(self.db).active(character_id)
            values = dict(persona.temper) if persona else {}
        except Exception:
            values = {}
        return float(values.get('touchy', 0.5)), float(values.get('forgiving', 0.5))

    def current(self, character_id: str) -> Mood:
        """回落到此刻的心情；心情词变了就同步到 emotion_state（形象、语音、表情包读那里）。"""
        with self.db.transaction() as c:
            row = c.execute('SELECT data, at FROM mood_state WHERE character_id=?', (character_id,)).fetchone()
        mood = Mood(json.loads(row['data']), float(row['at'])) if row else Mood()
        mood = mood.decayed(self.clock(), self.temper(character_id)[1])
        self._sync_label(character_id, mood.label())
        return mood

    def recent(self, character_id: str, limit: int = 8) -> list[dict]:
        with self.db.transaction() as c:
            rows = c.execute('SELECT kind, reason, scale, channel, at FROM mood_events WHERE character_id=? ORDER BY id DESC LIMIT ?',
                             (character_id, limit)).fetchall()
        return [dict(r) | {'label': EVENT_LABELS.get(r['kind'], r['kind'])} for r in rows]

    def reason(self, character_id: str, mood: Mood | None = None) -> str:
        """现在这个心情最可能是因为什么：最近 24 小时里推高它的最新一件事。"""
        mood = mood or self.current(character_id)
        label = mood.label()
        if label == 'calm': return ''
        since = self.clock()-24*3600
        for event in self.recent(character_id, 30):
            if event['at'] < since: break
            if EVENTS.get(event['kind'], {}).get(label, 0) > 0 or (event['kind'] == 'reunion' and label == 'sad'):
                return event['reason'] or event['label']
        return ''

    def prompt(self, character_id: str) -> str:
        mood = self.current(character_id)
        return describe(mood, self.reason(character_id, mood))

    # ---- 事件 ------------------------------------------------------------------------------
    def event(self, character_id: str, kind: str, *, reason: str = '', scale: float = 1.0, channel: str = 'desktop',
              extra: dict | None = None) -> Mood:
        if kind not in EVENTS: raise ValueError(f'未知情绪事件：{kind}')
        touchy, forgiving = self.temper(character_id)
        now = self.clock()

        def work(c):
            row = c.execute('SELECT data, at FROM mood_state WHERE character_id=?', (character_id,)).fetchone()
            before = Mood(json.loads(row['data']), float(row['at'])) if row else Mood()
            after = apply(before, kind, now, touchy=touchy, forgiving=forgiving, scale=scale, extra=extra)
            c.execute('INSERT INTO mood_state(character_id, data, at) VALUES (?,?,?) ON CONFLICT(character_id) DO UPDATE SET data=excluded.data, at=excluded.at',
                      (character_id, json.dumps(after.levels), now))
            c.execute('INSERT INTO mood_events(character_id, kind, reason, scale, channel, at) VALUES (?,?,?,?,?,?)',
                      (character_id, kind, reason[:60], round(scale, 2), channel, now))
            c.execute('DELETE FROM mood_events WHERE character_id=? AND id NOT IN (SELECT id FROM mood_events WHERE character_id=? ORDER BY id DESC LIMIT ?)',
                      (character_id, character_id, KEEP_EVENTS))
            return after
        mood = self.db.locked(work)
        self._sync_label(character_id, mood.label())
        return mood

    def soothe(self, character_id: str) -> Mood:
        """设置里手动“哄好”：委屈和生气清零（开心、撒娇不动）。"""
        return self.event(character_id, 'soothed', reason='你在设置里哄好了她')

    def on_user_message(self, character_id: str, text: str, channel: str = 'desktop', previous_at: float | None = None) -> None:
        """用户消息的轻量兜底：明显的词按七成力度记（她这一轮的 <mind/> 会再给出更准的判断）；隔了很久又来了记“好久不见”（委屈按隔了几天加一点，不指责）。"""
        scale = 0.7*channel_scale(channel)
        for kind, pattern in _WORDS:
            if pattern.search(text or ''):
                self.event(character_id, kind, reason='', scale=scale, channel=channel); break
        if previous_at and channel == 'desktop':
            hours = (self.clock()-previous_at)/3600
            if hours >= REUNION_HOURS:
                days = hours/24
                self.event(character_id, 'reunion', reason=f'隔了 {days:.0f} 天才见到你' if days >= 1.5 else '好久没见',
                           extra={'sad': min(30.0, 6.0*days)}, channel=channel)

    def on_mind(self, character_id: str, mind: dict, channel: str = 'desktop') -> str | None:
        """她这一轮的 <mind/> → 心情事件。转述别人的话、在说别人的不算冲她。返回记下的事件类型。"""
        if not mind: return None
        at, voice, tone = mind.get('at') or '', mind.get('voice') or '', mind.get('tone') or ''
        toward, feel = mind.get('toward'), str(mind.get('feel') or '')[:20]
        if voice == 'quote' or at in ('other', 'all', 'none'): return None
        scale = channel_scale(channel)
        kind, strength = None, 1.0
        if tone == 'attack': kind, strength = 'insulted', (abs(toward)/3 if toward else 1.0)
        elif tone == 'sarcasm' and not (toward and toward > 0): kind, strength = 'teased', (abs(toward)/2 if toward else 1.0)
        elif toward is not None and toward <= -1: kind, strength = 'teased', abs(toward)/2
        elif toward is not None and toward >= 1:
            kind = 'affection' if re.search(r'害羞|撒娇|喜欢|心动|甜', feel) else 'praised'; strength = toward/2
        elif re.search(r'委屈|难过|失落', feel): kind = 'ignored'
        if kind is None: return None
        self.event(character_id, kind, reason=feel, scale=min(1.5, max(0.3, strength))*scale, channel=channel)
        return kind

    # ---- 内部 ------------------------------------------------------------------------------
    def _sync_label(self, character_id: str, label: str) -> None:
        try:
            emotion = self.db.load_emotion(character_id)
            if emotion.mood != label:
                emotion.mood = label; self.db.save_emotion(emotion, character_id)
        except Exception:
            pass
