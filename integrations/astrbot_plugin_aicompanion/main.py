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
PLUGIN_VERSION = '0.8.0'
MAX_IMAGES = 3
MAX_IMAGE_BYTES = 1024 * 1024
MAX_STICKERS = 3
# 商城表情（mface）的图片只从 QQ 自己的固定主机下载
MARKET_HOSTS = {'gxh.vip.qq.com', 'i.gtimg.cn'}
MAGIC = ((b'GIF87a', 'gif'), (b'GIF89a', 'gif'), (b'\x89PNG\r\n\x1a\n', 'png'), (b'\xff\xd8\xff', 'jpeg'))
QQ_ADAPTERS = {'aiocqhttp': 'qq', 'qq_official': 'qq', 'qq_official_webhook': 'qq'}
DEFAULTS = {'pet_url': 'http://127.0.0.1:18765', 'token': '', 'groups': [], 'group_only_when_called': False,
            'offline_reply': '她现在不在电脑旁，晚点再来找她吧～', 'timeout': 60, 'skip_prefixes': ['/']}


class AICompanionBridge(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self.session: aiohttp.ClientSession | None = None
        self.pet_groups: list | None = None   # 桌宠心跳回来的允许群；None = 还没拿到，用插件配置里的
        self.pet_vision = True
        self.pet_collect = True        # 桌宠心跳告诉我们要不要收集别人发的表情包
        self.known_stickers = set()    # 桌宠已经收过的表情包（内容哈希）：再看到只传哈希，不重复传图
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
        if (not text and not has_images) or any(text.startswith(p) for p in self.option('skip_prefixes') if p): return None
        group_id = str(event.get_group_id() or '')
        if group_id:
            allowed = self.pet_groups if self.pet_groups is not None else self.option('groups')
            if group_id not in [str(g).strip() for g in allowed]: return None
            called = bool(getattr(event, 'is_at_or_wake_command', False))
            if self.option('group_only_when_called') and not called: return None
        else: called = True
        return {'platform': platform, 'sender_id': str(event.get_sender_id()), 'sender_name': event.get_sender_name() or '',
                'group_id': group_id, 'text': text or '[收到图片]', 'called': called}

    # ---- 表情包（用户 2026-11-09：收集别人发的表情包，学大家的发图习惯） ----------------------------
    @staticmethod
    def raw_segments(event):
        """OneBot 原始消息段（AstrBot 把商城表情 mface 丢掉了，表情包的 sub_type 也只在原始段里稳定存在）。"""
        raw = getattr(getattr(event, 'message_obj', None), 'raw_message', None)
        message = raw.get('message') if hasattr(raw, 'get') else None
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

    def remember_stickers(self, result):
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
        if not self.pet_vision:
            payload['text'] += '\n[图片未识别：识图已关闭，不能猜测图片内容。]'
            return
        if payload['group_id'] and not called:
            payload['text'] += '\n[收到图片，旁听不自动识图，不能猜测图片内容。]'
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
        if failed: payload['text'] += '\n[部分图片未能读取或超过 3 张，不能猜测这些图片的内容。]'

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

    async def heartbeat_loop(self):
        while True:
            try: await self.heartbeat_once()
            except asyncio.CancelledError: raise
            except Exception as error:   # 桌宠没开：下一轮再试，不影响 AstrBot
                logger.debug(f'AICompanion 心跳失败：{type(error).__name__}')
            await asyncio.sleep(HEARTBEAT_SECONDS)

    async def ask_pet(self, payload: dict) -> dict:
        # 识图先调用视觉接口再由原大脑回复，给额外一步留出时间。
        timeout = float(self.option('timeout')) + 45*int(bool(payload.get('images'))) + 45*int(bool(payload.get('avatar')))
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
        """桌宠拆好的多段回复（每段一条消息）；旧桌宠没有 segments 时整段作为一条。"""
        reply=str(result.get('reply') or '').strip()
        segments=result.get('segments')
        if (isinstance(segments,list) and 1<=len(segments)<=6 and all(isinstance(x,str) and x.strip() and len(x)<=4000 for x in segments)):
            return [x.strip() for x in segments]
        return [reply] if reply else []

    async def pause(self,text):
        """连发几条时像真人打字一样隔一小会儿（按下一条的长度，0.6～2.5 秒）。"""
        await asyncio.sleep(min(2.5,0.6+len(text)*0.04))

    async def send_texts(self,event,segments):
        """按顺序一条一条发；某条发送失败就停下，不重发、不打乱顺序。"""
        from astrbot.api.message_components import Plain
        for index,text in enumerate(segments):
            if index:await self.pause(text)
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
                if not await self.send_texts(event,segments):segments=None
                else:await self.pause('')
            if segments is not None or placement=='before_text':
                attempted=True;status='unknown'
                # AstrBot 4.29.3 的公开事件发送接口等待 OneBot 调用；yield 不作为 ACK。ACK 只针对图片这一条。
                await asyncio.wait_for(event.send(event.chain_result([image])),timeout=min(30,float(self.option('timeout'))))
                status='accepted'
                if placement=='before_text' and segments:await self.pause(segments[0]);await self.send_texts(event,segments)
        except asyncio.CancelledError as error:cancelled=error
        except Exception as error:
            # 明确的 OneBot 拒绝可记失败；连接断开或超时可能已送达，只记未知。图没尝试过仍是 cancelled。
            if attempted and status=='unknown' and type(error).__name__=='ActionFailed':status='failed'
        try:
            await asyncio.shield(self.expression_control('ack',{'delivery_id':delivery,'status':status}))
        except Exception:pass  # ACK 可幂等补报；不能据此重发图片。
        if cancelled:raise cancelled
        return True

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10)
    async def on_message(self, event: AstrMessageEvent):
        payload = self.route(event)
        if payload is None: return
        # 交给桌宠以后，AstrBot 自己的模型和其他插件都不再处理这条消息（不出现第二个人格）
        event.stop_event()
        payload['expression_version']=1
        payload['bot_account_id']=str(getattr(event,'get_self_id',lambda:'')() or '')
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
        if len(segments)>1:await self.send_texts(event,segments);return
        if reply:yield event.plain_result(reply)

    async def terminate(self):
        if self.heartbeat_task: self.heartbeat_task.cancel()
        if self.session and not self.session.closed:
            await self.session.close()
