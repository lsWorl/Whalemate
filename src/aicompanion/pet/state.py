"""桌宠状态机：把聊天、语音、交互和作息事件折叠成"当前该摆哪个姿势"。

设计参考 DSH 鲸鱼桌宠的做法：事件经纯函数 reduce 成快照，界面只负责渲染快照。
不依赖 Qt，未知事件保持原状，便于单元测试。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

# 数字越小优先级越高。
PRIORITY = ['grabbed', 'falling', 'dizzy', 'error', 'wait', 'celebrate', 'talking', 'patted', 'working', 'thinking', 'poked', 'sleepy', 'idle']
MIN_DWELL = 0.6          # 每个姿势至少停留的秒数，避免闪烁
POKE_SECONDS = 0.8
PAT_SECONDS = 2.2
DIZZY_SECONDS = 2.6
CELEBRATE_SECONDS = 1.6
ERROR_SECONDS = 8.0
TALK_TAIL = 1.2          # 文字回复结束后保持说话姿势的秒数
SLEEPY_IDLE = 20*60      # 白天闲置多久开始犯困
NIGHT_SLEEPY_IDLE = 3*60 # 深夜闲置多久开始犯困
ANGRY_POKES = 6          # 短时间内连戳次数达到即"生气"
POKE_WINDOW = 4.0


@dataclass(frozen=True)
class PetSnapshot:
    thinking: bool = False
    talking_text: bool = False
    speaking: bool = False
    working: str = ''
    waiting: bool = False
    grabbed: bool = False
    falling: bool = False
    sleepy: bool = False
    error: str = ''
    timers: dict = field(default_factory=dict)   # 状态名 -> 截止时间
    pokes: tuple = ()
    task: str = ''

    def active(self, now: float) -> set[str]:
        states = {'idle'}
        if self.error: states.add('error')
        if self.waiting: states.add('wait')
        if self.grabbed: states.add('grabbed')
        if self.falling: states.add('falling')
        if self.speaking or self.talking_text: states.add('talking')
        if self.working: states.add('working')
        if self.thinking: states.add('thinking')
        if self.sleepy: states.add('sleepy')
        states.update(name for name, until in self.timers.items() if until > now)
        return states

    def pose(self, now: float) -> str:
        active = self.active(now)
        return next(name for name in PRIORITY if name in active)


def is_night(hour: int) -> bool:
    return hour >= 23 or hour < 7


def reduce(snapshot: PetSnapshot, event: str, now: float, **data) -> tuple[PetSnapshot, list[str]]:
    """返回 (新快照, 副作用提示)。副作用是台词分类名，例如 'poke_angry'，由界面决定是否说出来。"""
    s, effects = snapshot, []
    timers = {name: until for name, until in s.timers.items() if until > now}
    if event == 'message_sent':
        s = replace(s, thinking=True, talking_text=False, error='', sleepy=False)
        timers.pop('talking', None)
    elif event == 'reply_token':
        s = replace(s, thinking=False, talking_text=True)
    elif event == 'reply_finished':
        ok = data.get('ok', True)
        s = replace(s, thinking=False, talking_text=False)
        if ok:
            if not s.speaking: timers['talking'] = now+TALK_TAIL
            if data.get('celebrate'): timers['celebrate'] = now+CELEBRATE_SECONDS; effects.append('celebrate')
        elif data.get('error'):
            s = replace(s, error=str(data['error'])); timers['error'] = now+ERROR_SECONDS; effects.append('error')
    elif event == 'speaking':
        s = replace(s, speaking=bool(data.get('active')))
        if not s.speaking: timers.pop('talking', None)
    elif event == 'interrupted':
        s = replace(s, thinking=False, talking_text=False, speaking=False)
        timers.pop('talking', None)
    elif event == 'error':
        s = replace(s, error=str(data.get('message', '出错了')), thinking=False, talking_text=False)
        timers['error'] = now+ERROR_SECONDS; effects.append('error')
    elif event == 'clear_error':
        s = replace(s, error='')
    elif event == 'work_started':
        s = replace(s, working=str(data.get('tool', 'task')), task=str(data.get('task', '')), sleepy=False); effects.append('working')
    elif event == 'work_finished':
        ok = data.get('ok', True)
        s = replace(s, working='', task='')
        if ok: timers['celebrate'] = now+CELEBRATE_SECONDS; effects.append('celebrate')
        else:
            s = replace(s, error=str(data.get('error', '任务失败'))); timers['error'] = now+ERROR_SECONDS; effects.append('error')
    elif event == 'waiting':
        s = replace(s, waiting=bool(data.get('active')))
    elif event == 'grab':
        active = bool(data.get('active'))
        s = replace(s, grabbed=active, sleepy=False)
        effects.append('grabbed' if active else 'dropped')
    elif event == 'poke':
        pokes = tuple(t for t in s.pokes if now-t < POKE_WINDOW)+(now,)
        was_sleepy = s.sleepy
        s = replace(s, pokes=pokes, sleepy=False); timers['poked'] = now+POKE_SECONDS
        if was_sleepy: effects.append('wake')
        elif len(pokes) >= ANGRY_POKES: effects.append('poke_angry'); s = replace(s, pokes=())
        else: effects.append('poke')
    elif event == 'fall':
        active = bool(data.get('active'))
        s = replace(s, falling=active, grabbed=False if active else s.grabbed, sleepy=False)
        if active and data.get('thrown'): effects.append('thrown')
    elif event == 'landed':
        s = replace(s, falling=False)
        if data.get('hard'): timers['dizzy'] = now+DIZZY_SECONDS; effects.append('dizzy')
        else: effects.append('dropped')
    elif event == 'pat':
        was_sleepy = s.sleepy
        s = replace(s, sleepy=False, pokes=()); timers['patted'] = now+PAT_SECONDS
        effects.append('wake' if was_sleepy else 'pat')
    elif event == 'celebrate':
        timers['celebrate'] = now+CELEBRATE_SECONDS
    elif event == 'tick':
        idle, hour = float(data.get('idle_seconds', 0)), int(data.get('hour', 12))
        busy = s.thinking or s.talking_text or s.speaking or s.working or s.waiting or s.grabbed or s.falling
        night = data.get('night'); night = is_night(hour) if night is None else bool(night)
        sleepy = not busy and idle >= (NIGHT_SLEEPY_IDLE if night else SLEEPY_IDLE)
        if s.error and 'error' not in timers: s = replace(s, error='')
        s = replace(s, sleepy=sleepy)
    elif event == 'activity':
        if s.sleepy: effects.append('wake')
        s = replace(s, sleepy=False)
    elif event == 'force_pose':
        pose = data.get('pose')
        if pose in PRIORITY and pose != 'idle': timers[pose] = now+float(data.get('seconds', 2.0))
    else:
        return snapshot, []
    return replace(s, timers=timers), effects


class PetStateMachine:
    """在纯 reduce 之上加"最短停留时间"，输出界面实际显示的姿势。"""

    def __init__(self, now: float = 0.0):
        self.snapshot = PetSnapshot(); self.shown = 'idle'; self.since = now

    def dispatch(self, event: str, now: float, **data) -> list[str]:
        self.snapshot, effects = reduce(self.snapshot, event, now, **data)
        return effects

    def current(self, now: float) -> str:
        wanted = self.snapshot.pose(now)
        if wanted != self.shown:
            # 更高优先级的状态（如被拎起、出错）立即切换；同级或更低的要等最短停留时间。
            urgent = PRIORITY.index(wanted) < PRIORITY.index(self.shown) and wanted in ('error', 'wait', 'grabbed', 'falling', 'dizzy', 'poked', 'patted')
            if urgent or now-self.since >= MIN_DWELL:
                self.shown, self.since = wanted, now
        return self.shown
