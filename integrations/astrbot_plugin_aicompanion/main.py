"""AstrBot 插件：把选定的 QQ 会话转给本机的 AICompanion 桌宠，由桌宠的大脑回复（人格和记忆只有一份）。

只做转发：谁是主人、群里能不能说、陌生人回不回、记忆怎么隔离，都由桌宠那边判断。
每 15 秒向桌宠发一次心跳：OneBot（NapCat）有没有连上、机器人在哪些群；桌宠回复允许的群（以桌宠设置为准，只需填一次）。
桌宠不在线时不回退到 AstrBot 自己的模型（那会变成第二个人格），只回一句可配置的离线提示或不回。
"""
from __future__ import annotations

import asyncio
import base64
import io
from pathlib import Path
import re
import time

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

try:   # 新旧版本的配置对象位置不同；拿不到时用插件自带默认值
    from astrbot.api import AstrBotConfig
except ImportError:   # pragma: no cover
    AstrBotConfig = dict

HEARTBEAT_SECONDS = 15
PLUGIN_VERSION = '0.9.3'
MAX_IMAGES = 3
MAX_IMAGE_BYTES = 1024 * 1024
MAX_STICKERS = 3
# 和桌宠 expression/segments.py 的 QQ_FLOOD_LIMIT 保持一致（Codex 审查 17012ca P2：两边不一致时 7～8 段会退回整段或主动消息整条丢失）
MAX_SEGMENTS = 8
AT_ONLY = '[@了你一下，没说别的]'
# 商城表情（mface）的图片只从 QQ 自己的固定主机下载
MARKET_HOSTS = {'gxh.vip.qq.com', 'i.gtimg.cn'}
MAGIC = ((b'GIF87a', 'gif'), (b'GIF89a', 'gif'), (b'\x89PNG\r\n\x1a\n', 'png'), (b'\xff\xd8\xff', 'jpeg'))
QQ_ADAPTERS = {'aiocqhttp': 'qq', 'qq_official': 'qq', 'qq_official_webhook': 'qq'}
DEFAULTS = {'pet_url': 'http://127.0.0.1:18765', 'token': '', 'groups': [], 'group_only_when_called': False,
            'offline_reply': '她现在不在电脑旁，晚点再来找她吧～', 'timeout': 60, 'skip_prefixes': ['/']}


def markers_of(segments):
    """原始消息段里有没有图片 / 商城表情（用于决定要不要记一行收图诊断）。"""
    return any(seg.get('type') in ('image', 'mface') for seg in segments)


class AICompanionBridge(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self.session: aiohttp.ClientSession | None = None
        self.pet_groups: list | None = None   # 桌宠心跳回来的允许群；None = 还没拿到，用插件配置里的
        self.pet_vision = True
        self.pet_collect = True        # 桌宠心跳告诉我们要不要收集别人发的表情包
        self.known_stickers = set()    # 桌宠已经收过的表情包（内容哈希）：再看到只传哈希，不重复传图
        self.private_origins = {}      # QQ 号 → AstrBot 会话标识（主动发私聊时用；没聊过就按平台 id 拼）
        self.platform_id = 'aicompanion_qq'
        self.outbox_sent = set()       # 已经发过的主动消息 id（心跳重复带回时不重发）
        self.pet_name = '鲸娘'
        self.avatar_backoff = {}
        self.expression_attempts = set()
        self.heartbeat_task = None
        try:
            self.heartbeat_task = asyncio.get_running_loop().create_task(self.heartbeat_loop())
        except RuntimeError:   # 没有事件循环（测试里直接构造）：不发心跳
            self.heartbeat_task = None

    def option(self, key):
        value = self.config.get(key, DEFAULTS[key]) if hasattr(self.config, 'get') else DEFAULTS[key]
        return DEFAULTS[key] if value is None else value

    def route(self, event: AstrMessageEvent) -> dict | None:
        """这条消息要不要交给桌宠；返回请求体，None 表示不管（交还给 AstrBot 的其他插件）。"""
        platform = QQ_ADAPTERS.get(str(event.get_platform_name() or '').lower())
        if not platform: return None
        text = (event.message_str or '').strip()
        has_images = bool(self.image_parts(event)) or bool(self.sticker_parts(event)[1])
        if any(text.startswith(p) for p in self.option('skip_prefixes') if p): return None
        group_id = str(event.get_group_id() or '')
        if group_id:
            allowed = self.pet_groups if self.pet_groups is not None else self.option('groups')
            if group_id not in [str(g).strip() for g in allowed]: return None
            called = self.called(event)
            if self.option('group_only_when_called') and not called: return None
        else: called = True
        if not text and not has_images:
            # 2026-11-09 实测：群里只 @ 她、不带字时，插件不接，AstrBot 自己（没配模型）就在群里回"LLM 请求失败"。
            # 只 @ 了一下也是在叫她：交给她，像真人那样回一句（"咋了""在呢"）
            if not (group_id and called): return None
            text = AT_ONLY
        return {'platform': platform, 'sender_id': str(event.get_sender_id()), 'sender_name': event.get_sender_name() or '',
                'group_id': group_id, 'text': text or '[图片]', 'called': called}

    def called(self, event) -> bool:
        """群里是不是在叫她：AstrBot 的唤醒标记，或者原始消息里有 @ 她自己的账号（只 @ 不带字时不依赖 AstrBot 的标记）。"""
        if bool(getattr(event, 'is_at_or_wake_command', False)): return True
        me = str(getattr(event, 'get_self_id', lambda: '')() or '')
        if not me: return False
        for seg in self.raw_segments(event):
            if seg.get('type') == 'at' and str((seg.get('data') or {}).get('qq') or '') == me: return True
        for part in (getattr(event, 'get_messages', lambda: [])() or []):
            if type(part).__name__ == 'At' and str(getattr(part, 'qq', '') or '') == me: return True
        return False

    @staticmethod
    def swallow(event):
        """这条消息归桌宠管：终止传播，并明确告诉 AstrBot 不要走它自己的默认模型。
        只 stop_event 不够——AstrBot 4.29.3 在没有结果、也没发过消息时（只 @、她选择不回）仍会走默认模型，回"LLM 请求失败"。"""
        event.stop_event()
        should = getattr(event, 'should_call_llm', None)
        if callable(should):
            try: should(True)
            except Exception: pass

    # ---- 表情包（用户 2026-11-09：收集别人发的表情包，学大家的发图习惯） ----------------------------
    @staticmethod
    def raw_segments(event):
        """OneBot 原始消息段（AstrBot 把商城表情 mface 丢掉了，表情包的 sub_type 也只在原始段里稳定存在）。"""
        raw = getattr(getattr(event, 'message_obj', None), 'raw_message', None)
        message = raw.get('message') if hasattr(raw, 'get') else getattr(raw, 'message', None)
        return [seg for seg in message if isinstance(seg, dict)] if isinstance(message, list) else []

    @staticmethod
    def is_sticker(data) -> bool:
        """NapCat：图片段 sub_type=1 是表情包（收藏 / 动画表情）；summary 写着"[动画表情]"之类的也算。"""
        return str(data.get('sub_type', '')) == '1' or '表情' in str(data.get('summary') or '')

    @staticmethod
    def market_url_ok(url) -> bool:
        from urllib.parse import urlsplit
        parts = urlsplit(str(url or ''))
        return parts.scheme == 'https' and parts.hostname in MARKET_HOSTS and len(url) <= 500

    def sticker_parts(self, event):
        """返回 (图片组件里的表情包, 商城表情的图片地址)。普通照片 / 截图不算。"""
        raw = self.raw_segments(event)
        files = {str((seg.get('data') or {}).get('file') or '') for seg in raw
                 if seg.get('type') == 'image' and isinstance(seg.get('data'), dict) and self.is_sticker(seg['data'])}
        files.discard('')
        parts = [part for part in self.image_parts(event)
                 if self.is_sticker({'sub_type': getattr(part, 'sub_type', ''), 'summary': getattr(part, 'summary', '')})
                 or str(getattr(part, 'file', '') or '') in files]
        markets = [str((seg.get('data') or {}).get('url') or '') for seg in raw if seg.get('type') == 'mface' and isinstance(seg.get('data'), dict)]
        return parts, [url for url in markets if self.market_url_ok(url)]

    @staticmethod
    def sticker_uri(data: bytes) -> str:
        """原图（保留 GIF 动画）：只认 GIF / PNG / JPEG / WebP 文件头，1 MiB 以内。"""
        kind = next((name for head, name in MAGIC if data.startswith(head)), None)
        if kind is None and data[:4] == b'RIFF' and data[8:12] == b'WEBP': kind = 'webp'
        if not kind or not data or len(data) > MAX_IMAGE_BYTES: raise ValueError('不是可收集的表情包')
        return f'data:image/{kind};base64,'+base64.b64encode(data).decode('ascii')

    @staticmethod
    def read_sticker(path: str, temp_root: str) -> bytes:
        file = Path(path).resolve(); root = Path(temp_root).resolve()
        if not file.is_relative_to(root) or not file.is_file() or file.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError('表情包不在受管理的临时目录或体积过大')
        return file.read_bytes()

    async def fetch_market(self, url):
        if not self.market_url_ok(url): return None
        if self.session is None or self.session.closed: self.session = aiohttp.ClientSession()
        async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=8), allow_redirects=False) as response:
            if response.status != 200: return None
            data = bytearray()
            async for chunk in response.content.iter_chunked(16384):
                data.extend(chunk)
                if len(data) > MAX_IMAGE_BYTES: return None
        return bytes(data)

    async def attach_stickers(self, event, payload):
        """别人发的表情包（被叫到或旁听都收）：已知的只传内容哈希，新的传原图；读不到的跳过，不影响这条消息。"""
        import hashlib
        if not self.pet_collect: return
        parts, markets = self.sticker_parts(event)
        every = self.image_parts(event)
        items, sources = [], []
        for source in [('part', p) for p in parts]+[('market', u) for u in markets]:
            if len(items) >= MAX_STICKERS: break
            try:
                if source[0] == 'part':
                    path = await asyncio.wait_for(source[1].convert_to_file_path(), timeout=10)
                    data = await asyncio.to_thread(self.read_sticker, path, self.managed_temp_root())
                else:
                    data = await self.fetch_market(source[1])
                    if not data: continue
                digest = hashlib.sha256(data).hexdigest()
                items.append({'sha256': digest} if digest in self.known_stickers else self.sticker_uri(data))
                # sticker_parts[j]：第 j 个表情包来自第几个图片段（和 image_parts 对得上）；商城表情不在图片段里，记 -1
                sources.append(next((i for i, p in enumerate(every) if p is source[1]), -1) if source[0] == 'part' else -1)
            except Exception:
                continue   # 不把下载地址、路径或图片内容打进日志
        if items: payload['stickers'] = items; payload['sticker_parts'] = sources
        if every or markers_of(self.raw_segments(event)):
            # 2026-11-09 实测"收集不到"：日志里只有 NapCat 的"[图片]"，看不出是没认出表情还是桌宠没收。只记数量和类型，不记图片、地址或文字
            raw = self.raw_segments(event)
            kinds = ','.join(f"{seg.get('type')}:{(seg.get('data') or {}).get('sub_type', '-')}" for seg in raw
                             if seg.get('type') in ('image', 'mface') and isinstance(seg.get('data'), dict)) or '无原始段'
            logger.info(f'AICompanion 收到图片 {len(every)} 张，认出表情包 {len(items)} 个（原始段 {kinds}）')

    def remember_stickers(self, result):
        statuses = result.get('stickers_status') if isinstance(result, dict) else None
        if isinstance(statuses, list) and statuses:
            logger.info('AICompanion 表情包收集结果：' + ','.join(str(x)[:20] for x in statuses[:MAX_STICKERS]))
        known = result.get('stickers_known') if isinstance(result, dict) else None
        if isinstance(known, list):
            for digest in known[:MAX_STICKERS]:
                if isinstance(digest, str) and re.fullmatch(r'[a-f0-9]{64}', digest): self.known_stickers.add(digest)
            if len(self.known_stickers) > 4096: self.known_stickers = set(list(self.known_stickers)[-2048:])

    @staticmethod
    def image_parts(event):
        return [part for part in getattr(event, 'get_messages', lambda: [])()
                if getattr(part, 'type', None) == 'Image']

    @staticmethod
    def encode_image(path: str, temp_root: str) -> str:
        """仅读取 AstrBot 已规范化到自己临时目录的图片，不接受外部本机路径。"""
        from PIL import Image, ImageOps
        file = Path(path).resolve(); root = Path(temp_root).resolve()
        if not file.is_relative_to(root) or not file.is_file() or file.stat().st_size > 32*1024*1024:
            raise ValueError('图片不在受管理的临时目录或体积过大')
        with Image.open(file) as original:
            if original.width * original.height > 16_000_000 or max(original.size) > 8192:
                raise ValueError('图片像素过大')
            original.seek(0)   # 动图首版识别首帧，不把整段动画上传
            picture = ImageOps.exif_transpose(original).convert('RGB')
            picture.thumbnail((1600, 1600))
            for quality in (85, 65, 45):
                output = io.BytesIO(); picture.save(output, format='JPEG', quality=quality)
                if output.tell() <= MAX_IMAGE_BYTES:
                    return 'data:image/jpeg;base64,'+base64.b64encode(output.getvalue()).decode('ascii')
        raise ValueError('图片压缩后仍然过大')

    async def attach_images(self, event, payload):
        parts = self.image_parts(event)
        if not parts: return
        payload['has_image'] = True
        called = payload['called'] or any(n and n in payload['text'] for n in (self.pet_name, '鲸娘', '大肥鱼'))
        # 负责人 2026-11-09：说"无法识别图片"一看就是 AI。没识别的图只留中性的 [图片]，原因不写进消息，怎么接交给桌宠按上下文定
        if not self.pet_vision or (payload['group_id'] and not called):
            self.mark_unseen(payload)
            return
        images, sources = [], []
        failed = len(parts) > MAX_IMAGES
        for index, part in enumerate(parts[:MAX_IMAGES]):
            try:
                path = await asyncio.wait_for(part.convert_to_file_path(), timeout=10)
                images.append(await asyncio.to_thread(self.encode_image, path, self.managed_temp_root())); sources.append(index)
            except Exception:
                failed = True   # 不把下载 URL、绝对路径或图片内容打进日志
        if images:
            # image_parts[i]：第 i 张图片来自第几个图片段。桌宠据此只跳过"已经看懂的那张表情包"，同条消息里的普通照片照常识图
            payload['images'] = images; payload['image_parts'] = sources
        if failed: self.mark_unseen(payload)

    @staticmethod
    def mark_unseen(payload):
        """没送去识别的图片：消息里只留一个中性的 [图片]（只发了图时文字本来就是它，不重复加）。"""
        if '[图片]' not in payload['text']: payload['text'] = (payload['text']+'\n[图片]').strip()

    @staticmethod
    def managed_temp_root():
        from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
        return get_astrbot_temp_path()

    @staticmethod
    def avatar_target(event, payload):
        sender = payload['sender_id']
        mentions = [str(getattr(part, 'qq', '') or '') for part in getattr(event, 'get_messages', lambda: [])()
                    if getattr(part, 'type', None) == 'At']
        own = str(getattr(event, 'get_self_id', lambda: '')())
        mentions = [value for value in mentions if value != own and re.fullmatch(r'\d{5,12}', value)][:10]
        payload['mentioned_ids'] = mentions
        return mentions[0] if '头像' in payload['text'] and mentions else sender

    @staticmethod
    def encode_avatar(data):
        from PIL import Image
        with Image.open(io.BytesIO(data)) as picture:
            if picture.width*picture.height > 1_000_000: raise ValueError('头像像素过大')
            picture.seek(0); picture = picture.convert('RGB'); picture.thumbnail((256, 256))
            output = io.BytesIO(); picture.save(output, format='JPEG', quality=85)
            return 'data:image/jpeg;base64,'+base64.b64encode(output.getvalue()).decode('ascii')

    async def avatar_permission(self, payload):
        async with await self.post('/v1/media-permission', payload, 5) as response:
            if response.status != 200: return {}
            return await response.json()

    async def fetch_avatar(self, subject):
        """QQ 号只拼入固定官方 HTTPS 主机；不读取事件或模型给出的任意 URL，不跟随跳转。"""
        if not re.fullmatch(r'\d{5,12}', subject): return None
        if self.session is None or self.session.closed: self.session = aiohttp.ClientSession()
        url = f'https://q1.qlogo.cn/g?b=qq&nk={subject}&s=100'
        async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=8), allow_redirects=False) as response:
            if response.status != 200: return None
            data = bytearray()
            async for chunk in response.content.iter_chunked(16384):
                data.extend(chunk)
                if len(data) > 128*1024: return None
        return await asyncio.to_thread(self.encode_avatar, bytes(data))

    async def attach_avatar(self, event, payload):
        # 普通头像首次 / 24 小时后更新；桌宠先核验身份、额度、缓存和 called，再允许下载。
        if payload['group_id'] and not payload['called'] and not any(n in payload['text'] for n in (self.pet_name, '鲸娘', '大肥鱼') if n): return
        payload['avatar_subject'] = self.avatar_target(event, payload)
        subject = payload['avatar_subject']
        if not re.fullmatch(r'\d{5,12}', subject): return
        key = payload['group_id']+':'+subject
        if key in self.avatar_backoff and time.monotonic()-self.avatar_backoff[key] < 600: return
        try:
            # 不把附件先送进预检；预检只需要身份、触发方式和问题。
            check = {k: payload[k] for k in ('platform', 'sender_id', 'group_id', 'called', 'text', 'avatar_subject', 'mentioned_ids')}
            permission = await self.avatar_permission(check)
            if not permission.get('allowed') or not permission.get('avatar_needed'): return
            if permission.get('avatar_subject') != subject: return
            self.avatar_backoff[key] = time.monotonic()
            self.avatar_backoff = dict(list(self.avatar_backoff.items())[-300:])
            avatar = await self.fetch_avatar(subject)
            if avatar: payload['avatar'] = avatar
        except Exception:
            pass   # 头像获取失败不影响正常聊天，不输出头像 URL、QQ 号或凭据

    async def post(self, path: str, payload: dict, timeout: float) -> 'aiohttp.ClientResponse':
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        url = str(self.option('pet_url')).rstrip('/')+path
        headers = {'Authorization': f'Bearer {self.option("token")}'}
        return self.session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout))

    async def onebot_state(self) -> tuple[dict, list]:
        """问 NapCat（经 AstrBot 的 aiocqhttp 平台）：连上了没有、机器人是谁、在哪些群。"""
        platform = None
        for key in ('aiocqhttp',):
            try: platform = self.context.get_platform(key)
            except Exception: platform = None
            if platform: break
        if platform is None: return {'connected': False, 'error': 'AstrBot 里没有 OneBot（aiocqhttp）平台'}, []
        try:
            client = platform.get_client()
            info = await client.api.call_action('get_login_info')
            groups = await client.api.call_action('get_group_list')
        except Exception as error:
            return {'connected': False, 'error': f'NapCat 还没连上：{type(error).__name__}'}, []
        items = [{'id': str(g.get('group_id')), 'name': g.get('group_name') or ''} for g in (groups or []) if isinstance(g, dict)]
        return {'connected': True, 'self_id': str((info or {}).get('user_id') or '')}, items

    async def heartbeat_once(self):
        onebot, groups = await self.onebot_state()
        payload = {'astrbot': f'plugin-{PLUGIN_VERSION}', 'onebot': onebot, 'groups': groups}
        async with await self.post('/v1/heartbeat', payload, 10) as response:
            if response.status == 200:
                data = await response.json()
                if isinstance(data.get('groups'), list): self.pet_groups = [str(g) for g in data['groups']]
                media = data.get('media') or {}
                self.pet_vision = bool(media.get('vision', True)); self.pet_name = str(media.get('name') or '鲸娘')
                self.pet_collect = bool(media.get('collect', True))
                outbox = data.get('outbox')
                if isinstance(outbox, list) and outbox:
                    for item in outbox[:5]: await self.deliver_outbox(item)

    async def heartbeat_loop(self):
        while True:
            try: await self.heartbeat_once()
            except asyncio.CancelledError: raise
            except Exception as error:   # 桌宠没开：下一轮再试，不影响 AstrBot
                logger.debug(f'AICompanion 心跳失败：{type(error).__name__}')
            await asyncio.sleep(HEARTBEAT_SECONDS)

    async def ask_pet(self, payload: dict) -> dict:
        # 识图先调用视觉接口再由原大脑回复，给额外一步留出时间。
        timeout = float(self.option('timeout')) + 45*int(bool(payload.get('images'))) + 45*int(bool(payload.get('avatar'))) + 10*int(bool(payload.get('debounce')))
        async with await self.post('/v1/chat', payload, timeout) as response:
            if response.status == 401:
                logger.error('AICompanion 拒绝了请求：令牌不对，请把桌宠"设置 → 聊天平台"里的令牌填进插件配置。')
                return {'reply': '', 'silent': True, 'reason': 'unauthorized'}
            response.raise_for_status()
            return await response.json()

    async def expression_control(self,path,payload):
        async with await self.post('/v1/expression/'+path,payload,5) as response:
            if response.status!=200:return {}
            return await response.json()

    @staticmethod
    def segments_of(result) -> list:
        """桌宠拆好的多段回复（每段一条消息）；旧桌宠没有 segments 时整段作为一条。
        防刷屏：超过 MAX_SEGMENTS 段时，多出来的并进最后一条（不丢内容，主动消息也照常发出）。"""
        reply=str(result.get('reply') or '').strip()
        segments=result.get('segments')
        if (isinstance(segments,list) and 1<=len(segments)<=64 and all(isinstance(x,str) and x.strip() and len(x)<=4000 for x in segments)):
            segments=[x.strip() for x in segments]
            if len(segments)>MAX_SEGMENTS:segments=segments[:MAX_SEGMENTS-1]+['，'.join(segments[MAX_SEGMENTS-1:])]
            return segments
        return [reply] if reply else []

    @staticmethod
    def delays_of(result,count) -> list:
        """桌宠按打字速度算好的每条间隔（秒）；格式不对（或段数被防刷屏合并过）就不用，按下面的估算。"""
        delays=result.get('segment_delays')
        if isinstance(delays,list) and len(delays)==count and all(isinstance(x,(int,float)) and not isinstance(x,bool) and 0<=x<=15 for x in delays):
            return [float(x) for x in delays]
        return [None]*count

    async def pause(self,text,delay=None):
        """连发几条时像真人打字一样隔一会儿：用桌宠给的间隔；没有就按下一条的字数估（中文约 0.3 秒一字，1～8 秒）。"""
        if delay is None:delay=min(8.0,max(1.0,0.3+0.3*len(text)))
        await asyncio.sleep(delay)

    async def send_texts(self,event,segments,delays=None):
        """按顺序一条一条发，每条之间等一会儿；某条发送失败就停下，不重发、不打乱顺序。"""
        from astrbot.api.message_components import Plain
        delays=delays or [None]*len(segments)
        for index,text in enumerate(segments):
            if index:await self.pause(text,delays[index])
            try:await asyncio.wait_for(event.send(event.chain_result([Plain(text)])),timeout=min(30,float(self.option('timeout'))))
            except asyncio.CancelledError:raise
            except Exception as error:
                logger.warning(f'AICompanion 第 {index+1} 条消息发送失败：{type(error).__name__}');return False
        return True

    async def send_expression(self,event,result):
        """平台 await send 成功才 ACK；超时结果未知，绝不自动重发。"""
        import hashlib
        expression=result.get('expression')
        if result.get('expression_version')!=1 or not isinstance(expression,dict):return False
        attachments=expression.get('attachments') or []
        if not attachments:return False
        item=attachments[0]
        if not isinstance(item,dict):return False
        delivery=str(item.get('delivery_id') or '')
        if not re.fullmatch(r'[a-f0-9]{32}',delivery):return False
        if delivery in self.expression_attempts:return True
        value=item.get('data_uri');placement=item.get('placement')
        if not isinstance(value,str) or len(value)>MAX_IMAGE_BYTES*4//3+128 or placement not in ('before_text','after_text'):return False
        match=re.fullmatch(r'data:image/(?:png|jpeg|gif|webp);base64,([A-Za-z0-9+/=]+)',value)
        if not match:return False
        try:raw=base64.b64decode(match[1],validate=True)
        except ValueError:return False
        if not raw or len(raw)>MAX_IMAGE_BYTES or hashlib.sha256(raw).hexdigest()!=item.get('content_sha256'):return False
        self.expression_attempts.add(delivery)
        if len(self.expression_attempts)>2048:self.expression_attempts={delivery}
        try:permission=await self.expression_control('claim',{'delivery_id':delivery})
        except Exception:return True
        if not permission.get('allowed'):return permission.get('reason')!='media_unavailable'
        # 图片状态：没尝试过 = cancelled（如实上报"没发"，不伪造 accepted / unknown）；发了以后才是 accepted / failed / unknown
        status='cancelled';cancelled=None;attempted=False
        try:
            from astrbot.api.message_components import Image,Plain
            image=Image.fromBase64(match[1])
            # 文字按段一条一条发，表情包单独一条：图在前就先发图，图在后就最后发图（像真人聊天）
            segments=self.segments_of(dict(result,reply=str(expression.get('text') or result.get('reply') or '')))
            if placement=='after_text' and segments:
                # Codex 审计：前面的文字有一条失败 / 超时就整条停下，后面的图也不发（不乱序续发）
                if not await self.send_texts(event,segments,self.delays_of(result,len(segments))):segments=None
                else:await self.pause('')
            if segments is not None or placement=='before_text':
                attempted=True;status='unknown'
                # AstrBot 4.29.3 的公开事件发送接口等待 OneBot 调用；yield 不作为 ACK。ACK 只针对图片这一条。
                await asyncio.wait_for(event.send(event.chain_result([image])),timeout=min(30,float(self.option('timeout'))))
                status='accepted'
                if placement=='before_text' and segments:await self.pause(segments[0]);await self.send_texts(event,segments,self.delays_of(result,len(segments)))
        except asyncio.CancelledError as error:cancelled=error
        except Exception as error:
            # 明确的 OneBot 拒绝可记失败；连接断开或超时可能已送达，只记未知。图没尝试过仍是 cancelled。
            if attempted and status=='unknown' and type(error).__name__=='ActionFailed':status='failed'
        try:
            await asyncio.shield(self.expression_control('ack',{'delivery_id':delivery,'status':status}))
        except Exception:pass  # ACK 可幂等补报；不能据此重发图片。
        if cancelled:raise cancelled
        return True

    def unrouted_call(self, event) -> bool:
        """没交给她的群里有人 @ 机器人：不能交还给 AstrBot 自己的模型——它没配模型，会在群里回"LLM 请求失败"（2026-11-09 实测），
        有模型也会变成第二个人格。指令（/ 开头）照旧交给 AstrBot。"""
        if not QQ_ADAPTERS.get(str(event.get_platform_name() or '').lower()): return False
        if not str(event.get_group_id() or '') or not self.called(event): return False
        text = (event.message_str or '').strip()
        return not any(text.startswith(p) for p in self.option('skip_prefixes') if p)

    # 注意：这个装饰器必须紧贴在 on_message 上。0.9.0 曾把它错挂到 unrouted_call 上，插件一条消息都收不到，
    # 全部落到 AstrBot 自己（没配模型）的流程里，私聊回"LLM 请求失败"（2026-11-09 实测）。test_handler_is_registered 守着这一点。
    @filter.event_message_type(filter.EventMessageType.ALL, priority=10)
    async def on_message(self, event: AstrMessageEvent):
        payload = self.route(event)
        if payload is None:
            if self.unrouted_call(event):
                self.swallow(event)
                logger.info('AICompanion：这个群还没在桌宠"聊天平台"页勾选，@ 机器人的消息不处理')
            return
        if not payload['group_id']:
            origin = getattr(event, 'unified_msg_origin', None)
            if isinstance(origin, str) and origin: self.private_origins[payload['sender_id']] = origin
            platform_id = getattr(event, 'get_platform_id', lambda: '')()
            if isinstance(platform_id, str) and platform_id: self.platform_id = platform_id
        # 交给桌宠以后，AstrBot 自己的模型和其他插件都不再处理这条消息（不出现第二个人格）
        self.swallow(event)
        payload['expression_version']=1
        payload['bot_account_id']=str(getattr(event,'get_self_id',lambda:'')() or '')
        # 0.9.3：允许桌宠把同一个人连发的几条合在一起回一次（前面的请求会静默返回 merged）
        payload['debounce']=True
        try:
            await self.attach_avatar(event, payload)
            await self.attach_images(event, payload)
            await self.attach_stickers(event, payload)
            result = await self.ask_pet(payload)
            self.remember_stickers(result)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as error:
            logger.warning(f'AICompanion 不在线或没有回应：{error}')
            if not payload['called']: return
            offline = str(self.option('offline_reply') or '')
            if offline: yield event.plain_result(offline)
            return
        reply = str(result.get('reply') or '').strip()
        if result.get('silent'): return
        if await self.send_expression(event,result):return
        segments=self.segments_of(result)
        if len(segments)>1:await self.send_texts(event,segments,self.delays_of(result,len(segments)));return
        if reply:yield event.plain_result(reply)

    # ---- 主动消息（用户 2026-11-09：桌面和 QQ 都可能随机发送） ----------------------------------------------
    async def deliver_outbox(self, item):
        """桌宠主动发给爹的 QQ 私聊：按顺序发文字，表情包单独一条（先领取再发，和回复时一样不重发），最后回报发没发成。"""
        import hashlib
        if not isinstance(item, dict): return
        key = str(item.get('id') or ''); user = str(item.get('user_id') or '')
        if not re.fullmatch(r'[a-f0-9]{32}', key) or not re.fullmatch(r'\d{5,12}', user) or key in self.outbox_sent: return
        self.outbox_sent.add(key)
        if len(self.outbox_sent) > 512: self.outbox_sent = {key}
        segments = self.segments_of({'reply': '', 'segments': item.get('segments')})
        delays = self.delays_of(item, len(segments))
        expression = item.get('expression') if isinstance(item.get('expression'), dict) else {}
        attachments = [a for a in (expression.get('attachments') or [])[:1] if isinstance(a, dict)]
        origin = self.private_origins.get(user) or f'{self.platform_id}:FriendMessage:{user}'
        from astrbot.api.event import MessageChain
        from astrbot.api.message_components import Image, Plain
        async def send(component):
            ok = await asyncio.wait_for(self.context.send_message(origin, MessageChain(chain=[component])), timeout=30)
            if ok is False: raise RuntimeError('会话不存在')
        sent = 0; failed = False
        image = None; delivery = ''
        if attachments:
            a = attachments[0]; delivery = str(a.get('delivery_id') or '')
            match = re.fullmatch(r'data:image/(?:png|jpeg|gif|webp);base64,([A-Za-z0-9+/=]+)', str(a.get('data_uri') or ''))
            if match and re.fullmatch(r'[a-f0-9]{32}', delivery):
                try:
                    raw = base64.b64decode(match[1], validate=True)
                    if raw and len(raw) <= MAX_IMAGE_BYTES and hashlib.sha256(raw).hexdigest() == a.get('content_sha256'):
                        image = (Image.fromBase64(match[1]), a.get('placement'))
                except ValueError: image = None
        async def send_image():
            nonlocal sent
            try: permission = await self.expression_control('claim', {'delivery_id': delivery})
            except Exception: return
            if not permission.get('allowed'): return
            status = 'unknown'
            try:
                await send(image[0]); status = 'accepted'; sent += 1
            except asyncio.CancelledError: raise
            except Exception as error:
                if type(error).__name__ == 'ActionFailed': status = 'failed'
            try: await asyncio.shield(self.expression_control('ack', {'delivery_id': delivery, 'status': status}))
            except Exception: pass
        try:
            if image and image[1] == 'before_text': await send_image()
            for index, text in enumerate(segments):
                if index or sent: await self.pause(text, delays[index] if index else None)
                try: await send(Plain(text)); sent += 1
                except asyncio.CancelledError: raise
                except Exception as error:
                    logger.warning(f'AICompanion 主动消息第 {index+1} 条发送失败：{type(error).__name__}'); failed = True; break
            if image and image[1] == 'after_text' and not failed:
                if segments: await self.pause('')
                await send_image()
        finally:
            status = 'failed' if not sent else ('partial' if failed else 'sent')
            try: await asyncio.shield(self.post_json('/v1/outbox/ack', {'id': key, 'status': status}))
            except Exception: pass

    async def post_json(self, path, payload):
        async with await self.post(path, payload, 5) as response:
            return await response.json() if response.status == 200 else {}

    async def terminate(self):
        if self.heartbeat_task: self.heartbeat_task.cancel()
        if self.session and not self.session.closed:
            await self.session.close()
