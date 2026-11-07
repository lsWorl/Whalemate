"""聊天平台消息的处理规则（不依赖 Qt 和网络，可单独测试）。

谁在说话决定能用什么：
- 已绑定的主人在私聊里：和桌面是同一段相处（频道 desktop、私下场景），读写聊天历史、摘要、记忆、心情；
- 群聊（只限设置里允许的群）和陌生人私聊（默认关闭）：公开场合，只用公开范围的记忆；每个群 / 每个人一段独立的
  历史（频道 qq:group:<群号> / qq:user:<号>），不进摘要和记忆整理，看不到桌面上的聊天。
聊天平台上不提议、也不执行"动手干活"。

并发：一条消息的"检查额度 → 预留当日条数 → 记下用户消息 → 生成 → 记下回复"整段在一把进程级锁里串行完成
（设置保存时重建服务实例也共用这把锁），生成时只看到这条消息为止的历史，后来的消息不会混进这一轮。
桌面聊天和主人的 QQ 私聊共用 desktop 频道：两边同时说话时按消息先后排进同一段记录，QQ 这一轮只回答到自己那条为止。
"""
from __future__ import annotations

from datetime import date
import re
import threading
from uuid import uuid4

from aicompanion.domain.cancellation import CancellationToken

PLATFORM_NAMES = {'qq': 'QQ'}
MAX_TEXT = 1500
DEFAULTS = {'bridge_enabled': False, 'bridge_port': 18765, 'bridge_token': '', 'bridge_owner:qq': '',
            'bridge_groups': [], 'bridge_strangers': False, 'bridge_public_daily': 100}


def setting(database, key):
    return database.get_setting(key, DEFAULTS[key])


# 进程级：Runtime 重启服务实例时新旧实例共用，不会出现两把锁各放一条
PROCESS_LOCK = threading.Lock()


def short_id(sender_id: str) -> str:
    """群里给模型看的说话人标记：昵称会重名，带上号码后四位区分，不把完整号码交给模型。"""
    return '#'+sender_id[-4:]


class BridgeService:
    def __init__(self, database, controller, quota=None, today=date.today):
        self.database, self.controller, self.today = database, controller, today
        if quota is None:
            from aicompanion.usage.meter import Quota
            quota = Quota(database)
        self.quota = quota
        self.lock = PROCESS_LOCK

    # ---- 规则 --------------------------------------------------------------------
    def classify(self, platform: str, sender_id: str, group_id: str) -> dict | None:
        """返回这条消息的处理方式；None 表示不处理（不在允许范围内）。"""
        owner = str(self.database.get_setting(f'bridge_owner:{platform}', '') or '').strip()
        is_owner = bool(owner) and sender_id == owner
        where = PLATFORM_NAMES.get(platform, platform)
        if group_id:
            if group_id not in [str(g).strip() for g in setting(self.database, 'bridge_groups') or []]: return None
            return {'channel': f'{platform}:group:{group_id}', 'scene': platform, 'owner': is_owner, 'group': True, 'where': where}
        if is_owner:
            return {'channel': 'desktop', 'scene': 'pet', 'owner': True, 'group': False, 'where': where}
        if not setting(self.database, 'bridge_strangers'): return None
        return {'channel': f'{platform}:user:{sender_id}', 'scene': platform, 'owner': False, 'group': False, 'where': where}

    def _reserve_public(self) -> bool:
        """原子地预留一条当日公开回复（判断和加一在同一个数据库事务里）。"""
        return self.database.reserve_daily(f'bridge_public_count:{self.today().isoformat()}',
                                           int(setting(self.database, 'bridge_public_daily') or 0))

    # ---- 处理一条消息 -----------------------------------------------------------------
    def handle(self, payload: dict) -> dict:
        """payload：platform、sender_id、sender_name、group_id（私聊为空）、text。返回 {reply, silent, reason}。"""
        platform = str(payload.get('platform') or '').strip().lower()
        sender_id = str(payload.get('sender_id') or '').strip()
        group_id = str(payload.get('group_id') or '').strip()
        name = re.sub(r'[\r\n]+', ' ', str(payload.get('sender_name') or '').strip())[:30] or sender_id
        text = str(payload.get('text') or '').strip()[:MAX_TEXT]
        if not re.fullmatch(r'[a-z0-9_-]{1,20}', platform) or not re.fullmatch(r'[\w@.:-]{1,64}', sender_id):
            return {'reply': '', 'silent': True, 'reason': 'bad_request'}
        if group_id and not re.fullmatch(r'[\w@.:-]{1,64}', group_id):
            return {'reply': '', 'silent': True, 'reason': 'bad_request'}
        if not text: return {'reply': '', 'silent': True, 'reason': 'empty'}
        mode = self.classify(platform, sender_id, group_id)
        if mode is None: return {'reply': '', 'silent': True, 'reason': 'not_allowed'}
        private = mode['owner'] and not mode['group']
        purpose = 'companion' if private else 'qq'
        with self.lock:
            # 额度和条数在锁里、真正生成之前核验：排队期间额度用完的请求不会再发给模型
            if self.quota.check_silent(purpose) == 'stop':
                return {'reply': '今天的额度用完啦，明天再聊～' if private else '', 'silent': not private, 'reason': 'quota'}
            if not private and not self._reserve_public():
                return {'reply': '', 'silent': True, 'reason': 'daily_limit'}
            controller = self.controller; character = controller.character
            stable = f'{platform}:{sender_id}'
            if private:
                # 主人：和桌面同一段相处，正常更新心情、提取记忆
                message_id = controller.record_user_message(text, source=platform, channel='desktop')
                situation = (f'【场合】主人现在不在电脑前，正在{mode["where"]}私聊里和你说话。'
                             '回复像聊天软件里的消息，一两句，不用动作描写。')
            else:
                # 公开场合：只记在这个群 / 这个人自己的频道，不改心情、不提取记忆；记下稳定 ID，群里按"昵称#号码后四位"区分人
                line = f'{name}{short_id(sender_id)}：{text}' if mode['group'] else text
                message_id = self.database.add_message('user', line, character.id, source=platform, channel=mode['channel'], sender=stable)
                who = '你的主人' if mode['owner'] else f'{name}{short_id(sender_id)}'
                situation = (f'【场合】你在{mode["where"]}{"群聊" if mode["group"] else "私聊"}里，{who}在跟你说话'
                             + ('，群里其他人都看得到你的回复；每条消息前面是说话人的昵称和号码后四位' if mode['group'] else '') + '。'
                             '回复像聊天软件里的消息，一两句，不用动作描写；不要提桌面上发生的事。')
            from aicompanion.pet.script import Vocab, strip_cues
            parts = []
            try:
                for token in controller.generate(str(uuid4()), character, CancellationToken(), text, channel=mode['channel'],
                                                 scene=mode['scene'], situation=situation, source=platform,
                                                 offer_tasks=False, purpose=purpose, through_id=message_id):
                    parts.append(token)
            except Exception as error:
                return {'reply': '', 'silent': True, 'reason': 'error', 'error': str(error)[:200]}
        reply = strip_cues(''.join(parts), controller.vocabs.get(character.id) or Vocab()).strip()
        return {'reply': reply, 'silent': not reply, 'reason': 'ok' if reply else 'empty_reply'}
