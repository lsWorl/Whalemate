"""AstrBot 插件：把选定的 QQ 会话转给本机的 AICompanion 桌宠，由桌宠的大脑回复（人格和记忆只有一份）。

只做转发：谁是主人、群里能不能说、陌生人回不回、记忆怎么隔离，都由桌宠那边判断。
每 15 秒向桌宠发一次心跳：OneBot（NapCat）有没有连上、机器人在哪些群；桌宠回复允许的群（以桌宠设置为准，只需填一次）。
桌宠不在线时不回退到 AstrBot 自己的模型（那会变成第二个人格），只回一句可配置的离线提示或不回。
"""
from __future__ import annotations

import asyncio

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

try:   # 新旧版本的配置对象位置不同；拿不到时用插件自带默认值
    from astrbot.api import AstrBotConfig
except ImportError:   # pragma: no cover
    AstrBotConfig = dict

HEARTBEAT_SECONDS = 15
QQ_ADAPTERS = {'aiocqhttp': 'qq', 'qq_official': 'qq', 'qq_official_webhook': 'qq'}
DEFAULTS = {'pet_url': 'http://127.0.0.1:18765', 'token': '', 'groups': [], 'group_only_when_called': True,
            'offline_reply': '她现在不在电脑旁，晚点再来找她吧～', 'timeout': 60, 'skip_prefixes': ['/']}


class AICompanionBridge(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self.session: aiohttp.ClientSession | None = None
        self.pet_groups: list | None = None   # 桌宠心跳回来的允许群；None = 还没拿到，用插件配置里的
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
        if not text or any(text.startswith(p) for p in self.option('skip_prefixes') if p): return None   # 图片-only、AstrBot 指令
        group_id = str(event.get_group_id() or '')
        if group_id:
            allowed = self.pet_groups if self.pet_groups is not None else self.option('groups')
            if group_id not in [str(g).strip() for g in allowed]: return None
            if self.option('group_only_when_called') and not getattr(event, 'is_at_or_wake_command', False): return None
        return {'platform': platform, 'sender_id': str(event.get_sender_id()), 'sender_name': event.get_sender_name() or '',
                'group_id': group_id, 'text': text}

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
        payload = {'astrbot': 'plugin-0.2', 'onebot': onebot, 'groups': groups}
        async with await self.post('/v1/heartbeat', payload, 10) as response:
            if response.status == 200:
                data = await response.json()
                if isinstance(data.get('groups'), list): self.pet_groups = [str(g) for g in data['groups']]

    async def heartbeat_loop(self):
        while True:
            try: await self.heartbeat_once()
            except asyncio.CancelledError: raise
            except Exception as error:   # 桌宠没开：下一轮再试，不影响 AstrBot
                logger.debug(f'AICompanion 心跳失败：{type(error).__name__}')
            await asyncio.sleep(HEARTBEAT_SECONDS)

    async def ask_pet(self, payload: dict) -> dict:
        async with await self.post('/v1/chat', payload, float(self.option('timeout'))) as response:
            if response.status == 401:
                logger.error('AICompanion 拒绝了请求：令牌不对，请把桌宠"设置 → 聊天平台"里的令牌填进插件配置。')
                return {'reply': '', 'silent': True, 'reason': 'unauthorized'}
            response.raise_for_status()
            return await response.json()

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10)
    async def on_message(self, event: AstrMessageEvent):
        payload = self.route(event)
        if payload is None: return
        # 交给桌宠以后，AstrBot 自己的模型和其他插件都不再处理这条消息（不出现第二个人格）
        event.stop_event()
        try:
            result = await self.ask_pet(payload)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as error:
            logger.warning(f'AICompanion 不在线或没有回应：{error}')
            offline = str(self.option('offline_reply') or '')
            if offline: yield event.plain_result(offline)
            return
        reply = str(result.get('reply') or '').strip()
        if reply and not result.get('silent'):
            yield event.plain_result(reply)

    async def terminate(self):
        if self.heartbeat_task: self.heartbeat_task.cancel()
        if self.session and not self.session.closed:
            await self.session.close()
