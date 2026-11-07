"""聊天平台消息的处理规则（不依赖 Qt 和网络，可单独测试）。

谁在说话决定能用什么：
- 已绑定的主人在私聊里：和桌面是同一段相处（频道 desktop、私下场景），读写聊天历史、摘要、记忆、心情；
- 群聊（只限设置里允许的群）和陌生人私聊（默认关闭）：公开场合。每个群一段群里共享的历史（频道 qq:group:<群号>），
  陌生人各自一段（qq:user:<号>），看不到桌面的聊天记录和对话摘要。
- 记忆在桌面和聊天平台之间共享（用户 2026-11-07 决定）：群里听到的事会整理成记忆（写清楚是谁的），桌面上她也知道；
  桌面上记住的事在群里也能用，只有主人在记忆页标成"只在私下用"的不会在群里用。
聊天平台上不提议、也不执行"动手干活"。

并发：一条消息的"按最新设置判断身份 → 检查额度 → 预留当日条数 → 记下用户消息 → 生成 → 记下回复"整段在一把进程级锁里串行完成
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
DEFAULTS = {'bridge_memory_every': 6, 'bridge_enabled': False, 'bridge_port': 18765, 'bridge_token': '', 'bridge_owner:qq': '',
            'bridge_groups': [], 'bridge_strangers': False, 'bridge_public_daily': 100}


def setting(database, key):
    return database.get_setting(key, DEFAULTS[key])


# 进程级：Runtime 重启服务实例时新旧实例共用，不会出现两把锁各放一条
PROCESS_LOCK = threading.Lock()
REMEMBERING_LOCK = threading.Lock(); REMEMBERING: set = set()   # 正在整理记忆的频道（同一频道不并发整理）


def alias(database, stable: str) -> str:
    """群里给模型看的说话人标记 #序号：每个平台账号第一次出现时分配一个全局不重复的序号（存在设置里，之后不变）。
    昵称会重名、号码后几位也会撞，序号不会；完整号码不交给模型。需在 PROCESS_LOCK 里调用。"""
    table = database.get_setting('bridge_aliases', {}) or {}
    if stable not in table:
        table[stable] = len(table)+1; database.set_setting('bridge_aliases', table)
    return f'#{table[stable]}'


class BridgeService:
    def __init__(self, database, controller, quota=None, today=date.today):
        self.database, self.controller, self.today = database, controller, today
        if quota is None:
            from aicompanion.usage.meter import Quota
            quota = Quota(database)
        self.quota = quota
        self.lock = PROCESS_LOCK
        self.closed = False   # Runtime 关闭 / 重启时置真：还在排队的请求拿到锁后直接放弃，不用旧权限读新上下文

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
        if self.classify(platform, sender_id, group_id) is None: return {'reply': '', 'silent': True, 'reason': 'not_allowed'}
        with self.lock:
            # 拿到锁以后、读任何上下文之前，按此刻的设置重新判断身份：排队期间主人解绑 / 群被移出 / 服务被关，旧请求都作废
            if self.closed: return {'reply': '', 'silent': True, 'reason': 'closed'}
            mode = self.classify(platform, sender_id, group_id)
            if mode is None: return {'reply': '', 'silent': True, 'reason': 'not_allowed'}
            private = mode['owner'] and not mode['group']
            purpose = 'companion' if private else 'qq'
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
                # 公开场合：只记在这个群 / 这个人自己的频道，不改心情、不套"我叫…"之类的规则提取记忆（另由后台整理）；记下稳定 ID，群里按"昵称#编号"区分人
                tag = alias(self.database, stable)
                line = f'{"主人" if mode["owner"] else ""}{name}{tag}：{text}' if mode['group'] else text
                message_id = self.database.add_message('user', line, character.id, source=platform, channel=mode['channel'], sender=stable)
                who = '你的主人' if mode['owner'] else f'{name}{tag}'
                situation = (f'【场合】你在{mode["where"]}{"群聊" if mode["group"] else "私聊"}里，{who}在跟你说话'
                             + ('，群里其他人都看得到你的回复；每条消息前面是说话人的昵称和编号（#数字，同一个人编号不变）' if mode['group'] else '') + '。'
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
        if not private: self.maybe_remember(character.id, mode['channel'])
        return {'reply': reply, 'silent': not reply, 'reason': 'ok' if reply else 'empty_reply'}

    # ---- 群里听到的事整理成记忆（后台，不耽误回复） ------------------------------------------------
    def maybe_remember(self, character_id, channel, background=True):
        memory = self.controller.memory_service
        every = int(self.database.get_setting('bridge_memory_every', DEFAULTS['bridge_memory_every']) or 0)
        if every <= 0 or memory.pending(character_id, channel) < every: return False
        if self.quota.check_silent('memory') == 'stop': return False
        with REMEMBERING_LOCK:
            if channel in REMEMBERING: return False
            REMEMBERING.add(channel)

        def work():
            try:
                from aicompanion.usage.meter import using
                with using('memory', character_id):
                    memory.analyze(character_id, self.controller.llm_client, CancellationToken(), channel=channel)
            except Exception:
                pass   # 整理失败不影响聊天；下次够条数再试
            finally:
                with REMEMBERING_LOCK: REMEMBERING.discard(channel)
        if background: threading.Thread(target=work, daemon=True, name='aicompanion-bridge-memory').start()
        else: work()
        return True
