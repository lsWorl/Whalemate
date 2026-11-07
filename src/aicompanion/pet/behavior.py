"""作息与台词节奏：决定什么时候说一句本地台词，避免刷屏。不依赖 Qt。"""
from __future__ import annotations

AWAY_SECONDS = 10*60       # 系统闲置超过该时长视为"离开"
LINE_COOLDOWN = 8.0        # 两句本地台词之间的最短间隔
SAME_KEY_COOLDOWN = {'poke': 2.0, 'pat': 6.0, 'welcome_back': 30*60, 'sleepy': 30*60, 'token_burn': 10*60}
QUIET_KEYS = {'poke', 'poke_angry', 'grabbed', 'dropped', 'wake', 'pat', 'thrown', 'dizzy'}  # 用户主动交互引起的，深夜也回应


def greeting_key(hour: int) -> str:
    if 5 <= hour < 11: return 'greet_morning'
    if 11 <= hour < 14: return 'greet_noon'
    if 14 <= hour < 23: return 'greet_evening'
    return 'greet_night'


class LinePacer:
    def __init__(self):
        self.last_any = -1e9; self.last_by_key = {}; self.away = False

    def allow(self, key: str, now: float, hour: int, quiet: bool = False, night: bool | None = None) -> bool:
        """quiet=True 表示勿扰、专注或全屏中：只回应用户主动的交互。night 为作息里的安静时段。"""
        if quiet and key not in QUIET_KEYS: return False
        night = (hour >= 23 or hour < 7) if night is None else night
        if night and key not in QUIET_KEYS and key not in ('greet_night', 'welcome_back'): return False
        if now-self.last_any < (1.0 if key in QUIET_KEYS else LINE_COOLDOWN): return False
        if now-self.last_by_key.get(key, -1e9) < SAME_KEY_COOLDOWN.get(key, 60.0): return False
        self.last_any = now; self.last_by_key[key] = now
        return True

    def system_idle(self, idle_seconds: float) -> str:
        """根据系统级闲置时间判断离开/回来，返回 'welcome_back' 或空串。"""
        if idle_seconds >= AWAY_SECONDS: self.away = True; return ''
        if self.away and idle_seconds < 5:
            self.away = False; return 'welcome_back'
        return ''
