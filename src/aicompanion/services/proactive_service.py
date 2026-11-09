from datetime import datetime
import random


class ProactiveMessageService:
    """只决定调度意图；生成与用户回复共用协调器。"""

    def __init__(self,database,clock=datetime.now):
        self.database=database; self.clock=clock

    def due(self,character_id,idle_seconds):
        if not self.database.get_setting('proactive_enabled',True): return None
        now=self.clock(); hour=now.hour
        last=self.database.get_setting('last_proactive_attempt:'+character_id,0)
        if now.timestamp()-last<3600: return None
        quiet_start=self.database.get_setting('quiet_start',23)
        quiet_end=self.database.get_setting('quiet_end',8)
        quiet=(hour>=quiet_start or hour<quiet_end) if quiet_start>quiet_end else quiet_start<=hour<quiet_end
        if quiet: return None
        key=None; intent=''
        if 8<=hour<10:
            key=now.strftime('%Y-%m-%d')+':morning'; intent='早晨问候，关心今天的心情'
        elif 20<=hour<22:
            key=now.strftime('%Y-%m-%d')+':evening'; intent='晚间问候，温和关心今天的经历'
        elif idle_seconds>=3600:
            key=now.strftime('%Y-%m-%d:%H')+':idle'; intent='从有记录的共同话题开启一句自然闲聊'
        if key and self.database.claim_proactive(character_id,key):
            self.database.set_setting('last_proactive_attempt:'+character_id,now.timestamp())
            return key,intent
        return None


AWAY_SECONDS = 900   # 电脑闲置超过 15 分钟算"不在电脑前"


def choose_channel(database, idle_seconds, qq_ready, chance=random.random) -> str:
    """主动消息这次发到哪（用户 2026-11-09：桌面和 QQ 都可能随机发送）。
    QQ 能发时：在电脑前 50% 发 QQ（设置 proactive_qq_chance）；不在电脑前（闲置超过 15 分钟）85% 发 QQ——人不在，桌面上说了也没人看。"""
    if not qq_ready or not database.get_setting('proactive_qq_enabled', True): return 'desktop'
    try: base = min(1.0, max(0.0, float(database.get_setting('proactive_qq_chance', 0.5))))
    except (TypeError, ValueError): base = 0.5
    p = max(base, 0.85) if idle_seconds >= AWAY_SECONDS else base
    return 'qq' if chance() < p else 'desktop'

