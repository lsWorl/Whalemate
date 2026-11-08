"""聊天平台消息的处理规则（不依赖 Qt 和网络，可单独测试）。

谁在说话决定能用什么：
- 已绑定的"爹"（主人 QQ）在私聊里：和桌面是同一段相处（频道 desktop、私下场景），读写聊天历史、摘要、记忆、心情；
- 群聊（只限设置里允许的群）和陌生人私聊（默认关闭）：公开场合。每个群一段群里共享的历史（频道 qq:group:<群号>），
  陌生人各自一段（qq:user:<号>），看不到桌面的聊天记录和对话摘要。
- 记忆在桌面和聊天平台之间共享（用户 2026-11-07 决定）：群里听到的事会整理成记忆（写清楚是谁的），桌面上她也知道；
  桌面上记住的事在群里也能用，只有爹在记忆页标成"只在私下用"的不会在群里用。
聊天平台上不提议、也不执行"动手干活"。

并发：一条消息的"按最新设置判断身份 → 检查额度 → 预留当日条数 → 记下用户消息 → 生成 → 记下回复"整段在一把进程级锁里串行完成
（设置保存时重建服务实例也共用这把锁），生成时只看到这条消息为止的历史，后来的消息不会混进这一轮。
桌面聊天和爹的 QQ 私聊共用 desktop 频道：两边同时说话时按消息先后排进同一段记录，QQ 这一轮只回答到自己那条为止。
"""
from __future__ import annotations

from datetime import date
import random
import re
import time
import threading
from uuid import uuid4

from aicompanion.domain.cancellation import CancellationToken

PLATFORM_NAMES = {'qq': 'QQ'}
MAX_TEXT = 1500
# 群里没人叫她时插话：off = 只在被叫（@、唤醒词、提到名字）时说话；sometimes = 偶尔；often = 爱聊天（用户 2026-11-07 选的默认）
CHIME = {'off': None, 'sometimes': {'chance': 0.12, 'cooldown': 300, 'after': 4}, 'often': {'chance': 0.3, 'cooldown': 90, 'after': 2}}
SILENCE = '【不说】'
DEFAULTS = {'bridge_chime': 'often', 'bridge_chime_daily': 80, 'bridge_memory_every': 20, 'bridge_enabled': False, 'bridge_port': 18765, 'bridge_token': '', 'bridge_owner:qq': '',
            'bridge_groups': [], 'bridge_strangers': False, 'bridge_public_daily': 100}


def setting(database, key):
    return database.get_setting(key, DEFAULTS[key])


def setting_in(connection, key):
    from aicompanion.storage.database import Database
    return Database.setting_in(connection, key, DEFAULTS[key])


# 进程级：Runtime 重启服务实例时新旧实例共用，不会出现两把锁各放一条
PROCESS_LOCK = threading.Lock()
# 旁听记录的同步边界：最终授权判定（closed + 数据库里的群 / 身份）和写入在这把锁 + 同一个写事务里完成；
# close() 也拿这把锁，所以 close 返回之后不会再有旁听记录落库。只包住几次本地数据库读写，不等模型。
RECORD_LOCK = threading.Lock()
REMEMBERING_LOCK = threading.Lock(); REMEMBERING: set = set()   # 正在整理记忆的频道（同一频道不并发整理）


def alias(database, stable: str) -> str:
    """群里给模型看的说话人标记 #序号：每个平台账号第一次出现时分配一个全局不重复的序号（存在设置里，之后不变）。
    昵称会重名、号码后几位也会撞，序号不会；完整号码不交给模型。读改写在一个写事务里，并发分配不会撞号。"""
    return database.locked(lambda connection: alias_in(connection, stable))


def alias_in(connection, stable: str) -> str:
    from aicompanion.storage.database import Database
    table = Database.setting_in(connection, 'bridge_aliases', {}) or {}
    if stable not in table:
        table[stable] = len(table)+1; Database.set_setting_in(connection, 'bridge_aliases', table)
    return f'#{table[stable]}'


class BridgeService:
    def __init__(self, database, controller, quota=None, today=date.today, chance=random.random, clock=time.time, media=None):
        self.database, self.controller, self.today, self.chance, self.clock = database, controller, today, chance, clock
        from aicompanion.bridge.media import QQMedia
        from aicompanion.bridge.social import SocialMemory
        self.media = media or QQMedia(database, controller)
        self.social = SocialMemory(database, controller.character.id, clock=clock)
        if quota is None:
            from aicompanion.usage.meter import Quota
            quota = Quota(database)
        self.quota = quota
        self.lock = PROCESS_LOCK
        self.closed = False   # Runtime 关闭 / 重启时置真：还在排队的请求拿到锁后直接放弃，不用旧权限读新上下文

    def close(self) -> None:
        """Runtime 关闭 / 重启时调用：之后排队的回复拿到锁直接放弃，旁听的消息也不再落库（等正在写的那条写完）。"""
        with RECORD_LOCK: self.closed = True

    # ---- 规则 --------------------------------------------------------------------
    def classify(self, platform: str, sender_id: str, group_id: str, connection=None) -> dict | None:
        """返回这条消息的处理方式；None 表示不处理（不在允许范围内）。传 connection 时在那个事务里读设置。"""
        get = (lambda key: setting_in(connection, key)) if connection is not None else (lambda key: setting(self.database, key))
        owner = str((setting_in(connection, f'bridge_owner:{platform}') if connection is not None else
                     self.database.get_setting(f'bridge_owner:{platform}', '')) or '').strip()
        is_owner = bool(owner) and sender_id == owner
        where = PLATFORM_NAMES.get(platform, platform)
        if group_id:
            if group_id not in [str(g).strip() for g in get('bridge_groups') or []]: return None
            return {'channel': f'{platform}:group:{group_id}', 'scene': platform, 'owner': is_owner, 'group': True, 'where': where}
        if is_owner:
            return {'channel': 'desktop', 'scene': 'pet', 'owner': True, 'group': False, 'where': where}
        if not get('bridge_strangers'): return None
        return {'channel': f'{platform}:user:{sender_id}', 'scene': platform, 'owner': False, 'group': False, 'where': where}

    def _reserve_public(self) -> bool:
        """原子地预留一条当日公开回复（判断和加一在同一个数据库事务里）。"""
        return self.database.reserve_daily(f'bridge_public_count:{self.today().isoformat()}',
                                           int(setting(self.database, 'bridge_public_daily') or 0))

    def called_by_name(self, text: str) -> bool:
        """群里没 @ 她，但提到了她的名字 / 外号：当作在叫她。"""
        names = {self.controller.character.name, '鲸娘', '大肥鱼'}
        return any(n and n in text for n in names)

    def _chime_ok(self, channel: str) -> bool:
        """没人叫她时要不要试着插一句：按活跃度的概率、这个群离她上次说话的间隔和条数。
        通过后同时预留两份每日额度：设置页的"群和陌生人每天最多回复"总数（和被叫时的回复共用）和插话次数；
        任何一份用完都不插话，也不占另一份。"""
        rule = CHIME.get(setting(self.database, 'bridge_chime') or 'off')
        if not rule: return False
        last = float(self.database.get_setting(f'bridge_last_spoke:{channel}', 0) or 0)
        if self.clock()-last < rule['cooldown']: return False
        history = self.database.recent_messages(30, self.controller.character.id, channel=channel)
        since = 0
        for row in reversed(history):
            if row['role'] == 'assistant': break
            since += 1
        if since < rule['after'] or self.chance() >= rule['chance']: return False
        day = self.today().isoformat()
        return self.database.reserve_daily_all([(f'bridge_public_count:{day}', int(setting(self.database, 'bridge_public_daily') or 0)),
                                                (f'bridge_chime_count:{day}', int(setting(self.database, 'bridge_chime_daily') or 0))])

    # ---- 处理一条消息 -----------------------------------------------------------------
    def media_permission(self, payload):
        """供插件在下载头像前询问；处理消息时仍重新核验，预检不预留预算或授予后续权限。"""
        platform, sender, group = (str(payload.get(k) or '') for k in ('platform', 'sender_id', 'group_id'))
        if platform != 'qq' or not re.fullmatch(r'\d{5,12}', sender): return {'allowed': False}
        mode = self.classify(platform, sender, group)
        if self.closed or mode is None: return {'allowed': False}
        private = mode['owner'] and not mode['group']; purpose = 'companion' if private else 'qq'
        if self.quota.check_silent(purpose) == 'stop': return {'allowed': False}
        if not private:
            day = self.today().isoformat()
            if int(self.database.get_setting('bridge_public_count:'+day, 0) or 0) >= int(setting(self.database, 'bridge_public_daily') or 0):
                return {'allowed': False}
        text = str(payload.get('text') or '')[:1500]
        called = not mode['group'] or payload.get('called', True) is not False or self.called_by_name(text)
        subject = str(payload.get('avatar_subject') or sender)
        mentions = payload.get('mentioned_ids', [])
        if subject != sender and (not group or not isinstance(mentions, list) or subject not in mentions): subject = sender
        need = called and re.fullmatch(r'\d{5,12}', subject) and self.media.avatar_needed(mode['channel'], subject, text)
        return {'allowed': True, 'avatar_needed': bool(need), 'avatar_subject': subject}

    def handle(self, payload: dict) -> dict:
        """payload：platform、sender_id、sender_name、group_id（私聊为空）、text、called（群里是否 @ / 唤醒了她，旧插件没有这个字段时当作是）。
        返回 {reply, silent, reason}。群里没叫她的消息也会记进群聊记录（她能"听到"大家在聊什么、整理成记忆），按活跃度偶尔插话。"""
        platform = str(payload.get('platform') or '').strip().lower()
        sender_id = str(payload.get('sender_id') or '').strip()
        group_id = str(payload.get('group_id') or '').strip()
        name = re.sub(r'[\r\n]+', ' ', str(payload.get('sender_name') or '').strip())[:30] or sender_id
        text = str(payload.get('text') or '').strip()[:MAX_TEXT]
        if not re.fullmatch(r'[a-z0-9_-]{1,20}', platform) or not re.fullmatch(r'[\w@.:-]{1,64}', sender_id):
            return {'reply': '', 'silent': True, 'reason': 'bad_request'}
        if group_id and not re.fullmatch(r'[\w@.:-]{1,64}', group_id):
            return {'reply': '', 'silent': True, 'reason': 'bad_request'}
        images = payload.get('images') or []
        if not text and images: text = '[收到图片]'
        if not text: return {'reply': '', 'silent': True, 'reason': 'empty'}
        mode = self.classify(platform, sender_id, group_id)
        if mode is None: return {'reply': '', 'silent': True, 'reason': 'not_allowed'}
        if mode['group'] and payload.get('called', True) is False and not self.called_by_name(text):
            return self._overhear(mode, platform, sender_id, group_id, name, text)
        with self.lock:
            # 拿到锁以后、读任何上下文之前，按此刻的设置重新判断身份：排队期间爹解绑 / 群被移出 / 服务被关，旧请求都作废
            if self.closed: return {'reply': '', 'silent': True, 'reason': 'closed'}
            mode = self.classify(platform, sender_id, group_id)
            if mode is None: return {'reply': '', 'silent': True, 'reason': 'not_allowed'}
            private = mode['owner'] and not mode['group']
            purpose = 'companion' if private else 'qq'
            controller = self.controller; character = controller.character
            stable = f'{platform}:{sender_id}'
            # 额度和条数在锁里、真正生成之前核验：排队期间额度用完的请求不会再发给模型
            if self.quota.check_silent(purpose) == 'stop':
                return {'reply': '今天的额度用完啦，明天再聊～' if private else '', 'silent': not private, 'reason': 'quota'}
            if not private and not self._reserve_public():
                return {'reply': '', 'silent': True, 'reason': 'daily_limit'}
            original_text = text
            avatar_info = ''
            subject = str(payload.get('avatar_subject') or sender_id)
            mentions = payload.get('mentioned_ids', [])
            if subject != sender_id and (not group_id or not isinstance(mentions, list) or subject not in mentions): subject = sender_id
            if re.fullmatch(r'\d{5,12}', subject):
                from aicompanion.bridge.media import MediaError
                try: avatar_info = self.media.avatar_context(mode['channel'], subject, payload.get('avatar'), text, purpose)
                except MediaError: avatar_info = ''
                if avatar_info and subject != sender_id: avatar_info = '\n此头像属于本条消息 @ 的群友'+alias(self.database, f'{platform}:{subject}')+'：'+avatar_info
            if self.quota.check_silent(purpose) == 'stop': return {'reply': '', 'silent': True, 'reason': 'quota'}
            if images:
                from aicompanion.bridge.media import MediaError
                try: text = self.media.describe(images, text, purpose)
                except MediaError as error: return {'reply': str(error), 'silent': False, 'reason': 'bad_image'}
                if self.quota.check_silent(purpose) == 'stop':
                    return {'reply': '', 'silent': True, 'reason': 'quota'}
            if private:
                # 爹：和桌面同一段相处，正常更新心情、提取记忆
                message_id = controller.record_user_message(text, source=platform, channel='desktop')
                situation = (f'【场合】你爹现在不在电脑前，正在{mode["where"]}私聊里跟你说话。'
                             '像在 QQ 上回爹的消息，一两句，随意点，不用动作描写。')
            else:
                # 公开场合：只记在这个群 / 这个人自己的频道，不改心情、不套"我叫…"之类的规则提取记忆（另由后台整理）；记下稳定 ID，群里按"昵称#编号"区分人
                tag = alias(self.database, stable)
                line = f'{"【爹】" if mode["owner"] else ""}{name}{tag}：{text}' if mode['group'] else text
                message_id = self.database.add_message('user', line, character.id, source=platform, channel=mode['channel'], sender=stable)
                who = '你爹' if mode['owner'] else f'{name}{tag}'
                situation = (f'【场合】你在{mode["where"]}{"群聊" if mode["group"] else "私聊"}里，{who}在叫你'
                             + ('，群里其他人都看得到你的回复；每条消息前面是说话人的昵称和编号（#数字，同一个人编号不变），标着【爹】的是你爹' if mode['group'] else '') + '。'
                             '像群友一样回一两句，随意点，不用动作描写；不要提桌面上发生的事。')
            self.social.cid = character.id
            self.social.observe(mode['channel'], stable, original_text, bool(images) or bool(payload.get('has_image')))
            situation += avatar_info
            if '头像' in original_text and not avatar_info:
                situation += '\n本次没有读取到可用头像，不能猜测头像内容；可以请对方稍后再试或直接发图。'
            return self._reply(controller, character, mode, situation, platform, purpose, message_id, text)

    def _overhear(self, mode, platform, sender_id, group_id, name, text) -> dict:
        """群里没叫她的消息：先记进群聊记录（她"听到"了，之后被叫或整理记忆时都看得到），再看要不要插话。
        记录不排队等 PROCESS_LOCK：她正在别处说话时，群里刷屏的消息不会堆在锁上等到插件超时；这时也不插话。"""
        controller = self.controller; character = controller.character
        stable = f'{platform}:{sender_id}'

        def record(connection):
            # 在写事务里按此刻的设置做最终判定：等编号 / 等数据库期间群被移出、爹解绑，这里都看得到；【爹】标记也按此刻身份打
            now = self.classify(platform, sender_id, group_id, connection)
            if now is None or not now['group']: return None, None
            line = f'{"【爹】" if now["owner"] else ""}{name}{alias_in(connection, stable)}：{text}'
            cursor = connection.execute("INSERT INTO chat_messages(role,content,character_id,source,channel,sender) VALUES ('user',?,?,?,?,?)",
                                        (line, character.id, platform, now['channel'], stable))
            return now, cursor.lastrowid
        with RECORD_LOCK:
            if self.closed: return {'reply': '', 'silent': True, 'reason': 'closed'}
            mode, message_id = self.database.locked(record)
        if mode is None: return {'reply': '', 'silent': True, 'reason': 'not_allowed'}
        self.social.cid = character.id
        self.social.observe(mode['channel'], stable, text)
        observed = {'reply': '', 'silent': True, 'reason': 'observed'}
        if not self.lock.acquire(blocking=False):
            return observed
        try:
            # 拿到锁以后再按此刻设置核验一次：服务被关、群被移出、额度用完都不插话
            now = self.classify(platform, sender_id, group_id) if not self.closed else None
            if now is None: return observed
            mode = now
            if self.quota.check_silent('qq') == 'stop' or not self._chime_ok(mode['channel']):
                self.maybe_remember(character.id, mode['channel'])
                return observed
            situation = (f'【场合】你在{mode["where"]}群聊里旁听，这条消息没有叫你。每条消息前面是说话人的昵称和编号（#数字），'
                         '标着【爹】的是你爹，群里其他人都看得到你的回复。看看最近大家在聊什么：有想接的话、能接的梗、或者聊到你感兴趣 / 熟悉的事，'
                         f'就像群友一样自然插一句；话题跟你没关系、别人正聊得热乎不需要你、或者没什么好说的，就只回复{SILENCE}，别硬聊。'
                         '不要提桌面上发生的事。')
            return self._reply(controller, character, mode, situation, platform, 'qq', message_id, text, chime=True)
        finally:
            self.lock.release()

    def _reply(self, controller, character, mode, situation, platform, purpose, message_id, text, chime=False) -> dict:
        """在锁里生成回复。插话时模型可以回 {SILENCE} 表示不说：这条不发出去，也不留在聊天记录里。"""
        from aicompanion.bridge.social import NATURAL_PROMPT
        from aicompanion.bridge.community import prompt as community_prompt, maybe_refresh
        from aicompanion.bridge.chat_card import prompt as card_prompt
        private = mode['owner'] and not mode['group']
        turn = str(uuid4()); parts = []
        offer = self.media.offer(mode['channel'])
        stable_sender = ''
        rows = self.database.recent_messages(1, character.id, before_id=message_id+1, channel=mode['channel'])
        if rows: stable_sender = rows[-1]['sender'] or ('qq:'+str(self.database.get_setting('bridge_owner:qq', '')) if private else '')
        self.social.cid = character.id
        learned = self.social.profile(mode['channel'], stable_sender)
        if learned.get('preferences', {}).get('no_memes'): offer.categories = {}
        situation += NATURAL_PROMPT + self.social.prompt(mode['channel'], stable_sender) + offer.prompt()
        group = mode['channel'].split(':')[-1] if mode['group'] else ''
        situation += card_prompt(self.database, character.id, text, owner=mode['owner'], group=group)
        situation += community_prompt(self.database, text, group)
        maybe_refresh(self.database, group)
        delivery = {}
        def filter_reply(raw, status):
            if mode['group'] and (not raw.strip() or raw.strip() in (SILENCE, '不说')):
                delivery.update(reply='', images=[], history='')
            else: delivery.update(self.media.render(raw, status, offer, mode['channel']))
            return delivery['history']
        try:
            for token in controller.generate(turn, character, CancellationToken(), text, channel=mode['channel'],
                                             scene=mode['scene'], situation=situation, source=platform,
                                             offer_tasks=False, purpose=purpose, through_id=message_id, reply_filter=filter_reply):
                parts.append(token)
        except Exception as error:
            return {'reply': '', 'silent': True, 'reason': 'error', 'error': str(error)[:200]}
        reply = delivery.get('reply', '')
        outgoing = delivery.get('images', [])
        if chime and not (reply or outgoing):
            self.database.drop_reply(turn, character.id)
            self.maybe_remember(character.id, mode['channel'])
            return {'reply': '', 'silent': True, 'reason': 'chime_silent'}
        if mode['group'] and (reply or outgoing): self.database.set_setting(f'bridge_last_spoke:{mode["channel"]}', self.clock())
        if not private: self.maybe_remember(character.id, mode['channel'])
        result = {'reply': reply, 'silent': not (reply or outgoing), 'reason': ('chime' if chime else 'ok') if reply or outgoing else 'empty_reply'}
        if outgoing: result['images'] = outgoing
        return result

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
