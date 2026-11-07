"""AstrBot 插件：把选定的 QQ 会话转给本机的 AICompanion 桌宠，由桌宠的大脑回复（人格和记忆只有一份）。

只做转发：谁是主人、群里能不能说、陌生人回不回、记忆怎么隔离，都由桌宠那边判断。
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

QQ_ADAPTERS = {'aiocqhttp': 'qq', 'qq_official': 'qq', 'qq_official_webhook': 'qq'}
DEFAULTS = {'pet_url': 'http://127.0.0.1:18765', 'token': '', 'groups': [], 'group_only_when_called': True,
            'offline_reply': '她现在不在电脑旁，晚点再来找她吧～', 'timeout': 60, 'skip_prefixes': ['/']}


class AICompanionBridge(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self.session: aiohttp.ClientSession | None = None

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
            if group_id not in [str(g).strip() for g in self.option('groups')]: return None
            if self.option('group_only_when_called') and not getattr(event, 'is_at_or_wake_command', False): return None
        return {'platform': platform, 'sender_id': str(event.get_sender_id()), 'sender_name': event.get_sender_name() or '',
                'group_id': group_id, 'text': text}

    async def ask_pet(self, payload: dict) -> dict:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        url = str(self.option('pet_url')).rstrip('/')+'/v1/chat'
        headers = {'Authorization': f'Bearer {self.option("token")}'}
        timeout = aiohttp.ClientTimeout(total=float(self.option('timeout')))
        async with self.session.post(url, json=payload, headers=headers, timeout=timeout) as response:
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
        if self.session and not self.session.closed:
            await self.session.close()
