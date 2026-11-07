"""AstrBot 插件（integrations/astrbot_plugin_aicompanion）：用假的 astrbot 接口检查转发规则和离线行为。
不需要安装 AstrBot；aiohttp 没装时也用假模块代替。"""
import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest

PLUGIN = Path(__file__).resolve().parents[1]/'integrations'/'astrbot_plugin_aicompanion'/'main.py'


def load_plugin():
    fakes = {}
    api = types.ModuleType('astrbot.api'); api.logger = types.SimpleNamespace(error=lambda *a: None, warning=lambda *a: None)
    event = types.ModuleType('astrbot.api.event')

    class Filter:
        class EventMessageType: ALL = 'all'
        @staticmethod
        def event_message_type(kind, priority=0): return lambda fn: fn
    event.filter = Filter; event.AstrMessageEvent = object
    star = types.ModuleType('astrbot.api.star')

    class Star:
        def __init__(self, context): self.context = context
    star.Star = Star; star.Context = object
    fakes.update({'astrbot': types.ModuleType('astrbot'), 'astrbot.api': api, 'astrbot.api.event': event, 'astrbot.api.star': star})
    if importlib.util.find_spec('aiohttp') is None:
        aiohttp = types.ModuleType('aiohttp')
        class ClientError(Exception): pass
        aiohttp.ClientError = ClientError; aiohttp.ClientSession = object; aiohttp.ClientTimeout = lambda **k: None
        fakes['aiohttp'] = aiohttp
    saved = {k: sys.modules.get(k) for k in fakes}
    sys.modules.update(fakes)
    try:
        spec = importlib.util.spec_from_file_location('aicompanion_astrbot_plugin', PLUGIN)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module
    finally:
        for k, v in saved.items():
            if v is None: sys.modules.pop(k, None)
            else: sys.modules[k] = v


class Event:
    def __init__(self, text, sender='10001', group='', platform='aiocqhttp', called=True):
        self.message_str = text; self.sender = sender; self.group = group; self.platform = platform
        self.is_at_or_wake_command = called; self.stopped = False
    def get_platform_name(self): return self.platform
    def get_group_id(self): return self.group
    def get_sender_id(self): return self.sender
    def get_sender_name(self): return '阿青'
    def stop_event(self): self.stopped = True
    def plain_result(self, text): return ('plain', text)


class PluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_plugin()

    def plugin(self, **config):
        return self.module.AICompanionBridge(context=None, config={'token': 't', 'groups': ['30003'], **config})

    def run_handler(self, plugin, event, answer=None, error=None):
        async def fake(payload):
            plugin.sent = payload
            if error: raise error
            return answer
        plugin.sent = None; plugin.ask_pet = fake
        async def collect(): return [item async for item in plugin.on_message(event)]
        return asyncio.run(collect())

    def test_routes_only_qq_sessions_it_owns(self):
        p = self.plugin()
        self.assertEqual(p.route(Event('在吗')), {'platform': 'qq', 'sender_id': '10001', 'sender_name': '阿青', 'group_id': '', 'text': '在吗'})
        self.assertIsNone(p.route(Event('在吗', platform='telegram')))        # 其他平台交还给 AstrBot
        self.assertIsNone(p.route(Event('/help')))                             # AstrBot 指令
        self.assertIsNone(p.route(Event('')))                                  # 只有图片
        self.assertIsNone(p.route(Event('大家好', group='99999')))            # 没交给她的群
        self.assertIsNone(p.route(Event('大家好', group='30003', called=False)))   # 群里没叫她
        self.assertEqual(p.route(Event('大家好', group='30003'))['group_id'], '30003')
        self.assertIsNotNone(self.plugin(group_only_when_called=False).route(Event('大家好', group='30003', called=False)))

    def test_reply_and_stop(self):
        event = Event('在吗')
        out = self.run_handler(self.plugin(), event, {'reply': '在呢', 'silent': False})
        self.assertEqual(out, [('plain', '在呢')]); self.assertTrue(event.stopped)
        event = Event('在吗')
        self.assertEqual(self.run_handler(self.plugin(), event, {'reply': '', 'silent': True, 'reason': 'not_allowed'}), [])
        self.assertTrue(event.stopped)   # 桌宠不回的也不交给 AstrBot 自己的模型

    def test_offline_never_falls_back_to_astrbot_model(self):
        event = Event('在吗')
        out = self.run_handler(self.plugin(), event, error=OSError('connection refused'))
        self.assertEqual(out, [('plain', '她现在不在电脑旁，晚点再来找她吧～')]); self.assertTrue(event.stopped)
        event = Event('在吗')
        self.assertEqual(self.run_handler(self.plugin(offline_reply=''), event, error=OSError('x')), []); self.assertTrue(event.stopped)

    def test_untouched_events_keep_propagating(self):
        event = Event('/help')
        self.assertEqual(self.run_handler(self.plugin(), event, {'reply': 'x'}), []); self.assertFalse(event.stopped)


if __name__ == '__main__':
    unittest.main()
