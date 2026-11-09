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


def neutral_images(text: str) -> str:
    """把旧插件 / 旧记录里带原因的图片说明统一换成中性的 [图片]，连续几个只留一个。"""
    from aicompanion.bridge.media import UNSEEN
    text = IMAGE_NOTES.sub(UNSEEN, str(text or ''))
    return re.sub(r'(?:\s*\[图片\]){2,}', '\n'+UNSEEN, text).strip()


def unseen_image(text: str) -> bool:
    """消息里有没看到内容的图（有 [图片] 标记、后面没有视觉描述）。"""
    return '[图片]' in text and '[图片描述' not in text


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
ANALYZE_LOCK = threading.Lock(); ANALYZING = [False]   # 收集来的表情包后台分析：同一时间只跑一个
MAX_STICKERS = 3
# 负责人 2026-11-09：群里尽量看不出 AI 痕迹，不叫爹。只是"不主动暴露、说话像群友"；
# 被问是不是 AI：负责人要求含糊带过或顺着上下文转移话题；唯一不做的是明确声称自己是真人（诚实底线）。
# 负责人 13:30 补充：私聊也不要露出 AI 痕迹。群聊和私聊共用 HUMAN_STYLE；群里另外不叫爹、不提父女。
# 负责人 2026-11-09：不要一次生成一堆消息、不要用空格当停顿（"哟 逆天这次都用上了"）；
# 18:27 补充：也不是固定最多几条，要随情况和上下文变。真实聊天记录里大约六成消息只有一句、其余两三句（Ivanovic 2005），
# 想到哪说到哪：随口应一句就一条，激动、好笑、吐槽、一下子想到好几件事会连着发好几条短的。
MESSAGES = ('发几条没有固定数，像真人一样看情况：随口应一句、回个"嗯"、接个梗，一条就够；'
            '激动、笑疯了、吐槽、一下子想到好几件事、要把一件事讲清楚，就连着发好几条短的（换行就是另一条消息，每条一个意思）；'
            '对方连发了好几条、聊得正热，你也可以多说几句；对方很冷淡或你没兴趣，就一两个字。别为了凑条数硬拆，也别把几件事硬塞进一条。'
            '一条消息里别用空格把词句隔开（不要写成"哟 逆天"），要停顿就用逗号，或者干脆拆成两条。')
# 负责人 2026-11-09 18:27：说"无法识别图片"正常人一看就知道是 AI。没看到的图只有中性标记 [图片]，处理方式交给她按上下文定
UNSEEN_RULE = ('\n这条消息里的图片你没看到内容（只知道对方发了图）。别说自己看不到、识别不了、加载不出来、看不清图片，'
               '也别问"发的啥""啥图"——正常人不会这么说。能从上下文猜到大概，就顺着含糊接一句（比如跟着情绪笑一下、接前面的话）；'
               '猜不到又没别的话要接，就只回复'+SILENCE+'。')
HUMAN_STYLE = ('说话像真人发 QQ 消息：短、随口，有时只回几个字或一张图；'+MESSAGES+'不列点、不总结、不解释每个梗，'
               '不说"好的""当然可以""作为…""希望对你有帮助"这类话。别提自己是 AI / 模型 / 程序 / 桌宠，'
               '也别提词元、token、上下文、提示词、训练这些词；不熟的就说不太懂，别一本正经长篇大论。'
               '有人问你是不是 AI / 机器人：含糊地带过（"你猜""这很重要吗"之类），或者顺着刚才的话题把话岔开，不用正面回答；'
               '只是别明确说自己是真人。')
GROUP_HUMAN = ('【群里的说话方式】群里人人看得到：别叫你爹"爹""老爹"，别提你们是父女、你住在谁电脑上，'
               '像普通群友一样叫他的昵称或者直接接话，也不用每条都回。'+HUMAN_STYLE)
PRIVATE_HUMAN = '\n【QQ 私聊的说话方式】'+HUMAN_STYLE+'（和爹私聊照常叫爹。）'
STRANGER_HUMAN = '\n【QQ 私聊的说话方式】别提你住在谁电脑上。'+HUMAN_STYLE
# 负责人 2026-11-09：在 QQ 里她没必要什么都懂
QQ_NOT_EXPERT = ('\n在 QQ 上你不用什么都懂：问到不熟的专业问题、冷门知识，可以直接发一张可爱的表情包，说自己不太懂（"这个本鲸不会诶"），'
                 '不用硬答，更不要编；熟的照常好好说。')
# 插件在图片后面附的说明（旁听不识图 / 部分没读到）；整条消息只剩这些就算"只发了图"。
# 新插件只写中性的 [图片]；旧插件那些带原因的说明（"识图已关闭""不能猜测"）进来就换成 [图片]，免得她照着说"看不到图"
IMAGE_NOTES = re.compile(r'\[(?:图片|收到图片[^\]]*|部分图片未能读取[^\]]*|图片未识别[^\]]*|本次图片识别失败[^\]]*)\]')


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
        self.expression_bindings={}; self.expression_lock=threading.Lock()
        self.outbox=[]; self.outbox_waiting={}   # 主动发到 QQ 私聊的消息：等插件心跳取走（见 proactive_qq）
        self.closed = False   # Runtime 关闭 / 重启时置真：还在排队的请求拿到锁后直接放弃，不用旧权限读新上下文

    def close(self) -> None:
        """Runtime 关闭 / 重启时调用：之后排队的回复拿到锁直接放弃，旁听的消息也不再落库（等正在写的那条写完）。"""
        with RECORD_LOCK:
            self.closed = True
            with self.expression_lock:
                ids=list(self.expression_bindings)
            if ids:
                with self.database.transaction() as c:
                    for delivery in ids:
                        c.execute("UPDATE expression_deliveries SET status=CASE WHEN status='sending' THEN 'unknown' ELSE 'cancelled' END WHERE delivery_id=? AND status IN ('pending','sending')",(delivery,))

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

    def vision_ready(self) -> bool:
        """现在接的模型能不能看图（插件心跳时取，决定要不要下载图片）。"""
        try: return bool(self.media.vision_ready())
        except Exception: return False

    def peer_burst(self, channel: str) -> int:
        """她上次说话以后，对方在这个会话里连发了几条（自己这条也算）。"""
        rows = self.database.recent_messages(12, self.controller.character.id, channel=channel)
        count = 0
        for row in reversed(rows):
            if row['role'] == 'assistant': break
            count += 1
        return max(1, count)

    def _chime_ok(self, channel: str, person=None) -> bool:
        """没人叫她时要不要试着插一句：按活跃度的概率、这个群离她上次说话的间隔和条数。
        T15：概率再乘上她和刚说话这个人的关系（RelationshipService.chime_factor）：熟人、喜欢的人说话更容易接，
        不熟的少接，最近让她不舒服的人更少；关系很好时也不用等那么多条。
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
        factor = self.controller.relationships.chime_factor(self.controller.character.id, person) if person else 1.0
        after = rule['after']-1 if factor >= 1.3 else rule['after']
        if since < max(1, after) or self.chance() >= min(0.9, rule['chance']*factor): return False
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

    # ---- T15 人物关系：认出是谁（同一个 QQ 号在群聊和私聊是同一个人；不同机器人账号不合并） ----------------------
    def person_of(self, platform, sender_id, name, request) -> int | None:
        relationships = getattr(self.controller, 'relationships', None)
        if relationships is None or self.closed: return None
        from aicompanion.relationship.service import PersonRef
        from aicompanion.relationship.analyzer import remember_sender
        bot = str((request or {}).get('bot_account_id') or '')
        if not re.fullmatch(r'\d{5,12}', bot): bot = 'unknown'
        try:
            person = relationships.resolve(PersonRef(platform, bot, sender_id, name), self.controller.character.id)
            remember_sender(self.database, self.controller.character.id, f'{platform}:{sender_id}', person)
            return person
        except Exception:
            return None   # 关系记录出错不影响聊天

    def closeness_of(self, person) -> float:
        relationships = getattr(self.controller, 'relationships', None)
        try: return relationships.closeness(self.controller.character.id, person) if relationships and person else 0.0
        except Exception: return 0.0

    # ---- 收集别人发的表情包、学大家的发图习惯（用户 2026-11-09） ----------------------------------------
    @property
    def collector(self):
        expression = getattr(self.controller, 'expression', None)
        if expression is None: return None
        from aicompanion.expression.collect import StickerCollector
        return StickerCollector(self.database, expression.catalog, clock=self.clock, today=self.today)

    def take_stickers(self, mode, platform, sender_id, group_id, payload, text) -> dict:
        """插件附上的表情包（原图 data URI，或插件已知桌宠收过的 {'sha256'}）：收进图库、记一条"大家在什么情境下发了它"，
        并给聊天记录写一句"发了表情包：含义"。返回 {items, note, known, all_known, meanings}；meanings 按插件传来的顺序
        （读不到的那张为 None），用来和插件给的附件对应关系一起判断哪张图片已经看懂、不必再识图。失败的图跳过，不影响这条消息。

        Codex 审计（7aaa065，P1）：收图落库、记用法和这条消息自己的聊天记录一样，要在 RECORD_LOCK 里、按此刻设置做最终授权判定，
        close() 返回之后不再有任何写入；只在入口看一次 closed 不够（解码图片期间服务可能已经关闭）。"""
        empty = {'items': [], 'note': '', 'known': [], 'all_known': False, 'meanings': [], 'statuses': []}
        raw = payload.get('stickers')
        collector = self.collector
        if self.closed or not isinstance(raw, list) or not raw or collector is None or mode is None: return empty
        from aicompanion.bridge.media import MediaError, image_bytes
        from aicompanion.expression.collect import source_tag, meaning_of
        from aicompanion.expression import usage
        kind = ('group:'+group_id) if group_id else ('user:'+sender_id)
        tag = source_tag(kind); origin = f'{platform}:{"group" if group_id else "private"}:{tag}'
        character = self.controller.character
        # 先在锁外解码校验（耗时的部分），落库时再进锁
        decoded = []
        for item in raw[:MAX_STICKERS]:
            try:
                if isinstance(item, dict) and set(item) == {'sha256'} and re.fullmatch(r'[a-f0-9]{64}', str(item['sha256'])):
                    decoded.append(('hash', str(item['sha256'])))
                else:
                    decoded.append(('data', image_bytes(item)[0]))
            except (MediaError, ValueError, TypeError):
                decoded.append(None)
        meanings, items, known = [None]*len(decoded), [], []
        statuses = ['bad_image' if entry is None else '' for entry in decoded]
        with RECORD_LOCK:
            if self.closed or self.classify(platform, sender_id, group_id) is None:
                self.note_intake(['not_allowed']*len(decoded), group_id); return empty
            # 情境：这条消息自己带的话；只发了图就用这个会话里上一条话（发图前大家在说什么）
            cue = IMAGE_NOTES.sub('', text).strip()
            if not cue:
                history = self.database.recent_messages(1, character.id, channel=mode['channel'])
                cue = history[-1]['content'] if history else ''
            for index, entry in enumerate(decoded):
                if entry is None: continue
                try:
                    if entry[0] == 'hash':
                        found = collector.known(entry[1])
                        if not found: statuses[index] = 'unknown_hash'; continue
                        result = {'status': 'known', 'asset_id': found[0], 'meaning': meaning_of(found[1]), 'sha256': entry[1]}
                    else:
                        result = collector.ingest(entry[1], character_id=character.id, origin=origin)
                except (MediaError, ValueError, OSError, TypeError):
                    statuses[index] = 'error'; continue
                statuses[index] = result.get('status') or ''
                if not result.get('asset_id'): continue
                meanings[index] = result.get('meaning') or ''
                items.append(result['asset_id']); known.append(result['sha256'])
                try: usage.record(self.database, character.id, result['asset_id'], cue, tag, clock=self.clock)
                except Exception: pass
        self.note_intake(statuses, group_id)
        if not items: return dict(empty, statuses=statuses)
        found = [m for m in meanings if m is not None]
        parts = [f'[发了表情包：{m}]' if m else '[发了表情包]' for m in found]
        self.analyze_soon()
        return {'items': items, 'note': ''.join(parts), 'known': known, 'all_known': all(found), 'meanings': meanings, 'statuses': statuses}

    INTAKE_KEY = 'sticker_intake'
    def note_intake(self, statuses, group_id) -> None:
        """最近收到的表情包怎么处理的（图库页显示，排查"收不到"）：只记时间、群 / 私聊和结果代码，不记图片、号码或聊天内容。"""
        if not statuses: return
        try:
            def write(c):
                ring = [e for e in (self.database.setting_in(c, self.INTAKE_KEY, []) or []) if isinstance(e, dict)][-29:]
                ring.append({'at': round(self.clock()), 'kind': 'group' if group_id else 'private', 'status': [str(x)[:20] for x in statuses[:MAX_STICKERS]]})
                self.database.set_setting_in(c, self.INTAKE_KEY, ring)
            self.database.locked(write)
        except Exception:
            pass

    @staticmethod
    def skip_known_sticker_images(images, payload, meanings):
        """images 里去掉"已经看懂的表情包"那几张。插件给两份对应关系：image_parts[i] 是第 i 张图片来自消息里第几个图片段，
        sticker_parts[j] 是第 j 个表情包来自第几个图片段（商城表情为 -1，不在 images 里）。对应关系缺失或格式不对就一张都不跳过。"""
        image_parts, sticker_parts = payload.get('image_parts'), payload.get('sticker_parts')
        ints = lambda value, size: (isinstance(value, list) and len(value) == size
                                    and all(type(x) is int and -1 <= x < 64 for x in value))
        if not images or not ints(image_parts, len(images)) or not ints(sticker_parts, len(meanings)): return images
        known = {sticker_parts[j] for j, meaning in enumerate(meanings) if meaning and sticker_parts[j] >= 0}
        return [image for image, part in zip(images, image_parts) if part not in known]

    def analyze_soon(self, background=True) -> bool:
        """收集来、还没分析的表情包在后台分析几张（不耽误回复）；额度用完就等明天。
        每张分析前都重新确认：服务没关、收集和识图都还开着（Codex 审计：关闭后不能再把图交给视觉模型）。"""
        collector = self.collector
        if self.closed or collector is None or not collector.pending(1) or self.quota.check_silent('qq') == 'stop': return False
        with ANALYZE_LOCK:
            if ANALYZING[0]: return False
            ANALYZING[0] = True

        def alive():
            return (not self.closed and collector.enabled() and bool(self.database.get_setting('qq_vision_enabled', True))
                    and self.quota.check_silent('qq') != 'stop')

        def work():
            try: collector.analyze_pending(self.media.analyze_sticker, limit=5, alive=alive)
            except Exception: pass   # 分析失败不影响聊天；下次收到图再试
            finally:
                with ANALYZE_LOCK: ANALYZING[0] = False
        if background: threading.Thread(target=work, daemon=True, name='aicompanion-sticker-analysis').start()
        else: work()
        return True

    def handle(self, payload: dict) -> dict:
        """payload：platform、sender_id、sender_name、group_id（私聊为空）、text、called（群里是否 @ / 唤醒了她，旧插件没有这个字段时当作是）。
        返回 {reply, silent, reason}。群里没叫她的消息也会记进群聊记录（她能"听到"大家在聊什么、整理成记忆），按活跃度偶尔插话。"""
        platform = str(payload.get('platform') or '').strip().lower()
        sender_id = str(payload.get('sender_id') or '').strip()
        group_id = str(payload.get('group_id') or '').strip()
        name = re.sub(r'[\r\n]+', ' ', str(payload.get('sender_name') or '').strip())[:30] or sender_id
        text = neutral_images(str(payload.get('text') or '').strip()[:MAX_TEXT])
        if not re.fullmatch(r'[a-z0-9_-]{1,20}', platform) or not re.fullmatch(r'[\w@.:-]{1,64}', sender_id):
            return {'reply': '', 'silent': True, 'reason': 'bad_request'}
        if group_id and not re.fullmatch(r'[\w@.:-]{1,64}', group_id):
            return {'reply': '', 'silent': True, 'reason': 'bad_request'}
        images = payload.get('images') or []
        if not text and (images or payload.get('stickers')): text = '[图片]'
        if not text: return {'reply': '', 'silent': True, 'reason': 'empty'}
        mode = self.classify(platform, sender_id, group_id)
        if mode is None: return {'reply': '', 'silent': True, 'reason': 'not_allowed'}
        if mode['group'] and payload.get('called', True) is False and not self.called_by_name(text):
            return self._overhear(mode, platform, sender_id, group_id, name, text, payload)
        with self.lock:
            # 拿到锁以后、读任何上下文之前，按此刻的设置重新判断身份：排队期间爹解绑 / 群被移出 / 服务被关，旧请求都作废
            if self.closed: return {'reply': '', 'silent': True, 'reason': 'closed'}
            mode = self.classify(platform, sender_id, group_id)
            if mode is None: return {'reply': '', 'silent': True, 'reason': 'not_allowed'}
            private = mode['owner'] and not mode['group']
            purpose = 'companion' if private else 'qq'
            controller = self.controller; character = controller.character
            stable = f'{platform}:{sender_id}'
            person = self.person_of(platform, sender_id, name, payload)
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
            stickers = self.take_stickers(mode, platform, sender_id, group_id, payload, original_text)
            # 只发了图（认出是表情包，或者插件没认出来但这条只有图片）：都按"只发了表情"处理——不一定要回，回也只接一句
            sticker_only = (bool(stickers['items'] or images or payload.get('has_image') or IMAGE_NOTES.search(original_text))
                            and not IMAGE_NOTES.sub('', original_text).strip())
            if stickers['items']:
                # 收过、分析过的表情包：直接用存好的含义，不再为它调一次识图。
                # Codex 审计（7aaa065）：不能按"数量"认定所有图片都是已知表情——商城表情不在 images 里，同一条消息还可能带普通照片。
                # 只跳过插件明确标明"这张图片就是那张已看懂的表情包"的那几张，其余图片照常识图；旧插件没有对应关系就全部照常识图。
                images = self.skip_known_sticker_images(images, payload, stickers['meanings'])
                text = (IMAGE_NOTES.sub('', text).strip() if not images else text)+stickers['note']
            if images:
                from aicompanion.bridge.media import MediaError
                try: text = self.media.describe(images, text, purpose)
                except MediaError: text = neutral_images(text+'\n[图片]')   # 不回"图片太大""编码无效"这类机器人提示
                if self.quota.check_silent(purpose) == 'stop':
                    return {'reply': '', 'silent': True, 'reason': 'quota'}
            if private:
                # 爹：和桌面同一段相处，正常更新心情、提取记忆
                message_id = controller.record_user_message(text, source=platform, channel='desktop')
                situation = (f'【场合】你爹现在不在电脑前，正在{mode["where"]}私聊里跟你说话。'
                             '像在 QQ 上回爹的消息，随意点，不用动作描写。'
                             + PRIVATE_HUMAN
                             + QQ_NOT_EXPERT)
            else:
                # 公开场合：只记在这个群 / 这个人自己的频道，不改心情、不套"我叫…"之类的规则提取记忆（另由后台整理）；记下稳定 ID，群里按"昵称#编号"区分人
                tag = alias(self.database, stable)
                line = f'{"【爹】" if mode["owner"] else ""}{name}{tag}：{text}' if mode['group'] else text
                message_id = self.database.add_message('user', line, character.id, source=platform, channel=mode['channel'], sender=stable)
                who = '你爹' if mode['owner'] else f'{name}{tag}'
                situation = (f'【场合】你在{mode["where"]}{"群聊" if mode["group"] else "私聊"}里，{who}在叫你'
                             + ('，群里其他人都看得到你的回复；每条消息前面是说话人的昵称和编号（#数字，同一个人编号不变），标着【爹】的是你爹' if mode['group'] else '') + '。'
                             + (GROUP_HUMAN if mode['group'] else STRANGER_HUMAN)
                             + '像群友一样回，随意点，不用动作描写；不要提桌面上发生的事。'
                             + QQ_NOT_EXPERT)
            self.social.cid = character.id
            self.social.observe(mode['channel'], stable, original_text, bool(images) or bool(payload.get('has_image')))
            situation += avatar_info
            if unseen_image(text): situation += UNSEEN_RULE
            if sticker_only:
                # 用户 2026-11-09：表情包发出来她能看懂，但不是每张都要回
                situation += ('\n对方这条只发了图（多半是表情包）：把它当成对方的语气，回一张图或一句短话就行，也可以觉得不用接话就只回复'
                              +SILENCE+'（表示看到了，不发消息）；别描述、点评或追问这张图。')
            if '头像' in original_text and not avatar_info:
                situation += '\n他问到头像，但你这次没看清他的头像：别说看不到、读取不了头像，也别编头像内容；顺着话含糊带过，或者把话题岔开。'
            result = self._reply(controller, character, mode, situation, platform, purpose, message_id, text, request=payload, may_skip=sticker_only, person=person)
            if stickers['known']: result['stickers_known'] = stickers['known']
            if stickers['statuses']: result['stickers_status'] = stickers['statuses']
            return result

    def _overhear(self, mode, platform, sender_id, group_id, name, text, request=None) -> dict:
        """群里没叫她的消息：先记进群聊记录（她"听到"了，之后被叫或整理记忆时都看得到），再看要不要插话。
        记录不排队等 PROCESS_LOCK：她正在别处说话时，群里刷屏的消息不会堆在锁上等到插件超时；这时也不插话。"""
        controller = self.controller; character = controller.character
        stable = f'{platform}:{sender_id}'
        text = neutral_images(text)
        stickers = self.take_stickers(mode, platform, sender_id, group_id, request or {}, text)
        sticker_only = bool(stickers['items']) and not IMAGE_NOTES.sub('', text).strip()
        if stickers['items']:
            # 群里记录写成"发了表情包：含义"，她之后被叫到、整理记忆时都知道大家发了什么图（不用为此专门回复）
            text = (IMAGE_NOTES.sub('', text).strip()+stickers['note']).strip()

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
        person = self.person_of(platform, sender_id, name, request)
        observed = {'reply': '', 'silent': True, 'reason': 'observed'}
        if stickers['known']: observed['stickers_known'] = stickers['known']
        if stickers['statuses']: observed['stickers_status'] = stickers['statuses']
        if sticker_only:
            # 只发了一张表情包：她看懂了、记下了，但不为一张图插话
            self.maybe_remember(character.id, mode['channel'])
            return observed
        if not self.lock.acquire(blocking=False):
            return observed
        try:
            # 拿到锁以后再按此刻设置核验一次：服务被关、群被移出、额度用完都不插话
            now = self.classify(platform, sender_id, group_id) if not self.closed else None
            if now is None: return observed
            mode = now
            if self.quota.check_silent('qq') == 'stop' or not self._chime_ok(mode['channel'], person):
                self.maybe_remember(character.id, mode['channel'])
                return observed
            situation = (f'【场合】你在{mode["where"]}群聊里旁听，这条消息没有叫你。每条消息前面是说话人的昵称和编号（#数字），'
                         '标着【爹】的是你爹，群里其他人都看得到你的回复。'+GROUP_HUMAN+'看看最近大家在聊什么：有想接的话、能接的梗、或者聊到你感兴趣 / 熟悉的事，'
                         f'就像群友一样自然插一句；话题跟你没关系、别人正聊得热乎不需要你、或者没什么好说的，就只回复{SILENCE}，别硬聊。'
                         '不要提桌面上发生的事。'+QQ_NOT_EXPERT+(UNSEEN_RULE if unseen_image(text) else ''))
            result = self._reply(controller, character, mode, situation, platform, 'qq', message_id, text, chime=True, request=request, person=person)
            if stickers['known']: result['stickers_known'] = stickers['known']
            if stickers['statuses']: result['stickers_status'] = stickers['statuses']
            return result
        finally:
            self.lock.release()

    def _reply(self,controller,character,mode,situation,platform,purpose,message_id,text,chime=False,request=None,may_skip=False,person=None):
        """统一大脑生成；这里只提供 QQ 场景和最终静默过滤，不再分类随机选图。"""
        from aicompanion.bridge.social import NATURAL_PROMPT
        from aicompanion.bridge.community import prompt as community_prompt,maybe_refresh
        from aicompanion.bridge.chat_card import prompt as card_prompt,selected_source
        from aicompanion.expression.domain import ExpressionContext,CommunityContext,RelationshipSnapshot,owner_relationship
        from aicompanion.bridge.media import inline_image
        from dataclasses import replace
        request=request or {};private=mode['owner'] and not mode['group'];turn=uuid4().hex
        peer=self.peer_burst(mode['channel'])
        stable=platform+':'+str(request.get('sender_id') or '')
        group=mode['channel'].split(':')[-1] if mode['group'] else ''
        sender=str(request.get('sender_id') or '')
        bot=str(request.get('bot_account_id') or '')
        if not re.fullmatch(r'\d{5,12}',bot):bot='request-'+turn
        scope=f'{platform}:{bot}:'+('group:'+group if group else 'user:'+sender)
        version=type(request.get('expression_version')) is int and request['expression_version']==1
        self.social.cid=character.id;learned=self.social.profile(mode['channel'],stable)
        background=community_prompt(self.database,text,group)
        situation+=NATURAL_PROMPT+self.social.prompt(mode['channel'],stable)
        situation+=card_prompt(self.database,character.id,text,owner=mode['owner'],group=group)+background
        maybe_refresh(self.database,group)   # 原有调度，不增加新的社区任务
        context=ExpressionContext(turn,character.id,scope,mode['channel'],mode['scene'],
            'group_chime' if chime else 'user_reply',community=CommunityContext(selected_source(self.database,group),(background[:1200],) if background else ()),
            no_memes=bool(learned.get('preferences',{}).get('no_memes')),more_memes=bool(learned.get('preferences',{}).get('more_memes')),supports_images=version,
            # 只有绑定的爹在私聊里算本人；群里即使是爹也按公开场合（亲昵图不发到群里）
            relationship=owner_relationship() if private else RelationshipSnapshot(closeness=self.closeness_of(person)))
        delivered=[]
        def event(value):
            if value.kind=='final':delivered.append(value.result)
        def filter_reply(raw,status):
            return '' if (mode['group'] or may_skip) and raw.strip() in (SILENCE,'不说') else raw
        try:
            for _ in controller.generate(turn,character,CancellationToken(),text,channel=mode['channel'],scene=mode['scene'],
                situation=situation,source=platform,offer_tasks=False,purpose=purpose,through_id=message_id,reply_filter=filter_reply,
                expression_context=context,on_expression_event=event,person=person):pass
        except Exception:
            return {'reply':'','silent':True,'reason':'error'}
        if self.closed or controller.character.id!=character.id:
            controller.expression.cancel(turn,character.id);return {'reply':'','silent':True,'reason':'closed'}
        expression=delivered[-1];attachments=expression.attachments
        wire=expression.wire();wire['attachments']=[]
        for attachment in attachments:
            media=controller.expression.catalog.resolve(attachment.asset_id,attachment.revision)
            if media is None:
                controller.expression.receipt(attachment.delivery_id,'failed');continue
            item=attachment.__dict__.copy();item['data_uri']=inline_image(*media);wire['attachments'].append(item)
            with self.expression_lock:
                self.expression_bindings[attachment.delivery_id]=(platform,sender,group,character.id)
                if len(self.expression_bindings)>256:
                    oldest=next(iter(self.expression_bindings));self.expression_bindings.pop(oldest,None)
        reply=expression.text;outgoing=wire['attachments']
        if not outgoing:wire['decision']={'action':'text_only','asset_id':None,'placement':None,'origin':'no_marker'}
        if chime and not (reply or outgoing):
            self.database.drop_reply(turn,character.id);self.maybe_remember(character.id,mode['channel'])
            return {'reply':'','silent':True,'reason':'chime_silent'}
        if may_skip and not (reply or outgoing):
            self.database.drop_reply(turn,character.id)   # 对方只发了表情包、她选择不接：没发出去的话不算她说过
            return {'reply':'','silent':True,'reason':'sticker_seen'}
        if mode['group'] and (reply or outgoing):self.database.set_setting('bridge_last_spoke:'+mode['channel'],self.clock())
        if not private:self.maybe_remember(character.id,mode['channel'])
        result={'reply':reply,'silent':not (reply or outgoing),'reason':('chime' if chime else 'ok') if reply or outgoing else 'empty_reply'}
        # 多段回复：插件按顺序分几条消息发出去，每条之间按打字速度等一会儿（QQ 的分句规则，见 expression/segments.py）；
        # 旧插件只看 reply，不受影响。只有一条时也换成整理后的写法。
        from aicompanion.expression.segments import chat_burst, qq_segments, typing_delays
        burst=chat_burst(reply,peer_burst=peer,closeness=0.8 if private else self.closeness_of(person))
        segments=qq_segments(reply,chance=self.chance,burst=burst)
        if len(segments)>1:result.update(segments=segments,segment_delays=typing_delays(segments,chance=self.chance,burst=burst))
        if len(segments)==1:
            result['reply']=segments[0]
            if version:wire['text']=segments[0]
        if version:result.update(expression_version=1,expression=wire)
        return result

    def expression_claim(self,payload):
        """只领取宿主已绑定的这轮/会话，客户端不能改收件人或文件。"""
        if set(payload)!={'delivery_id'}:return {'allowed':False}
        delivery=str(payload.get('delivery_id') or '')
        with self.expression_lock:binding=self.expression_bindings.get(delivery)
        if not binding:return {'allowed':False}
        platform,sender,group,cid=binding
        expression=getattr(self.controller,'expression',None)
        if expression is None:return {'allowed':False}
        row=expression.delivery(delivery)
        media_valid=bool(row and expression.catalog.resolve(row['asset_id'],row['revision']) is not None)
        with RECORD_LOCK:
            if self.closed:return {'allowed':False}
            def claim(c):
                if self.controller.character.id!=cid or self.classify(platform,sender,group,c) is None:return {'allowed':False,'reason':'revoked'}
                if not media_valid:return {'allowed':False,'reason':'media_unavailable'}
                if not self.database.setting_in(c,'qq_memes_enabled',True):return {'allowed':False,'reason':'media_unavailable'}
                current=c.execute('SELECT * FROM expression_deliveries WHERE delivery_id=?',(delivery,)).fetchone()
                asset=c.execute('SELECT enabled,review_status,root FROM expression_assets WHERE asset_id=?',(row['asset_id'],)).fetchone()
                from pathlib import Path
                configured=str(self.database.setting_in(c,'qq_meme_root','') or '').strip()
                root=Path(configured).expanduser().resolve() if configured else expression.catalog.builtin_root.resolve()
                if asset and Path(asset['root'])!=root:return {'allowed':False,'reason':'media_unavailable'}
                if (not current or current['status']!='pending' or not asset or not asset['enabled']
                    or asset['review_status']!='approved' or expression.clock()-current['created_at']>120):return {'allowed':False,'reason':'stale'}
                c.execute("UPDATE expression_deliveries SET status='sending',updated_at=? WHERE delivery_id=?",(expression.clock(),delivery));return {'allowed':True}
            return self.database.locked(claim)

    def expression_ack(self,payload):
        if set(payload)!={'delivery_id','status'}:return {'ok':False}
        delivery=str(payload.get('delivery_id') or '');status=payload.get('status')
        with self.expression_lock:known=delivery in self.expression_bindings
        # cancelled：插件因前面的文字发送失败而没有尝试发图（如实上报，不算已发送）
        if not known or status not in ('accepted','failed','unknown','cancelled'):return {'ok':False}
        return {'ok':self.controller.expression.receipt(delivery,status)}

    # ---- 主动消息发到 QQ 私聊（用户 2026-11-09：桌面和 QQ 都可能随机发送） ------------------------------
    # 桌面的主动问候 / 心事到点时，桌面按概率决定这次发到哪：选中 QQ 就调用 proactive_qq。
    # 生成和桌面主动消息是同一个大脑、同一段和爹的记录（desktop 频道）；生成完放进发件箱，插件下一次心跳（15 秒内）取走，
    # 用 AstrBot 的主动发送接口发到爹的 QQ 私聊，再回报发没发成。没发出去的话从记录里拿掉（不算她说过）。
    OUTBOX_TTL = 600

    def qq_ready(self) -> bool:
        """能不能主动发 QQ：接口开着、绑定了爹、插件 1 分钟内有心跳且 QQ 已连上、插件版本支持发件箱（0.9.0 起）。"""
        from aicompanion.bridge.server import runtime
        beat = runtime.last_heartbeat or {}
        owner = str(self.database.get_setting('bridge_owner:qq', '') or '')
        version = re.fullmatch(r'plugin-(\d+)\.(\d+)\.(\d+)', str(beat.get('astrbot') or ''))
        return bool(not self.closed and re.fullmatch(r'\d{5,12}', owner) and beat and time.time()-beat.get('at', 0) < 60
                    and (beat.get('onebot') or {}).get('connected') and version
                    and tuple(int(x) for x in version.groups()) >= (0, 9, 0))

    def proactive_qq(self, intent: str, on_done=None) -> str | None:
        """在 QQ 私聊里主动找爹说话。返回发件箱条目 id；没生成出来返回 None（调用方按"没发"处理）。
        on_done(sent: bool) 在插件回报（或过期）时调用一次。"""
        from aicompanion.bridge.server import runtime
        from aicompanion.bridge.media import inline_image
        from aicompanion.expression.domain import ExpressionContext, owner_relationship
        from aicompanion.expression.segments import chat_burst, qq_segments, typing_delays
        with self.lock:
            if not self.qq_ready() or self.quota.check_silent('companion') == 'stop': return None
            owner = str(self.database.get_setting('bridge_owner:qq', '') or '')
            bot = str(((runtime.last_heartbeat or {}).get('onebot') or {}).get('self_id') or '')
            if not re.fullmatch(r'\d{5,12}', bot): bot = 'qq'
            controller = self.controller; character = controller.character; turn = uuid4().hex
            situation = ('【场合】你爹现在不在电脑前，你在 QQ 私聊里主动找他说话（他不一定马上回）。'
                         '像在 QQ 上给爹发消息那样开个头，随意点，一条就行，不用动作描写。'
                         +PRIVATE_HUMAN+QQ_NOT_EXPERT)
            context = ExpressionContext(turn, character.id, f'qq:{bot}:user:{owner}', 'desktop', 'pet', 'proactive', intent,
                                        relationship=owner_relationship(), supports_images=True)
            delivered = []
            def event(value):
                if value.kind == 'final': delivered.append(value.result)
            try:
                for _ in controller.generate(turn, character, CancellationToken(), '', proactive=intent, channel='desktop', scene='pet',
                                             situation=situation, source='qq', offer_tasks=False, purpose='companion',
                                             expression_context=context, on_expression_event=event): pass
            except Exception:
                return None
            if self.closed or controller.character.id != character.id or not delivered:
                if delivered: controller.expression.cancel(turn, character.id)
                self.database.drop_reply(turn, character.id); return None
            expression = delivered[-1]; wire = expression.wire(); wire['attachments'] = []
            for attachment in expression.attachments:
                media = controller.expression.catalog.resolve(attachment.asset_id, attachment.revision)
                if media is None: controller.expression.receipt(attachment.delivery_id, 'failed'); continue
                item = attachment.__dict__.copy(); item['data_uri'] = inline_image(*media); wire['attachments'].append(item)
                with self.expression_lock: self.expression_bindings[attachment.delivery_id] = ('qq', owner, '', character.id)
            burst = chat_burst(expression.text, closeness=self.controller.relationships.closeness(character.id, self.controller.relationships.owner_person()))
            segments = qq_segments(expression.text, chance=self.chance, burst=burst)
            if not segments and not wire['attachments']:
                self.database.drop_reply(turn, character.id); return None
            wire['text'] = '\n'.join(segments)
            with self.expression_lock:
                self.outbox.append({'id': turn, 'user_id': owner, 'segments': segments, 'segment_delays': typing_delays(segments, chance=self.chance, burst=burst),
                                    'expression_version': 1, 'expression': wire,
                                    'at': self.clock(), 'character': character.id})
                self.outbox_waiting[turn] = on_done
            return turn

    def take_outbox(self) -> list:
        """插件心跳时取走待发的主动消息；超过 OUTBOX_TTL 还没取走的作废（插件掉线时不会攒一堆过期问候）。"""
        with self.expression_lock:
            items, self.outbox = self.outbox, []
        fresh = []
        for item in items:
            if self.clock()-item['at'] > self.OUTBOX_TTL: self._outbox_done(item['id'], False, item.get('character'))
            else: fresh.append({k: v for k, v in item.items() if k not in ('at', 'character')})
        return fresh

    def outbox_ack(self, payload) -> dict:
        if set(payload) != {'id', 'status'} or payload.get('status') not in ('sent', 'partial', 'failed'): return {'ok': False}
        with self.expression_lock:
            known = str(payload['id']) in self.outbox_waiting
        if not known: return {'ok': False}
        self._outbox_done(str(payload['id']), payload['status'] != 'failed', self.controller.character.id)
        return {'ok': True}

    def _outbox_done(self, turn, sent, character_id) -> None:
        with self.expression_lock:
            callback = self.outbox_waiting.pop(turn, None)
        if not sent and character_id: self.database.drop_reply(turn, character_id)   # 没发出去：不算她说过
        if callback:
            try: callback(sent)
            except Exception: pass

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
