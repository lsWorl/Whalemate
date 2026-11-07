"""AstrBot 插件（integrations/astrbot_plugin_aicompanion）：用假的 astrbot 接口检查转发规则和离线行为。
不需要安装 AstrBot；aiohttp 没装时也用假模块代替。"""
import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest import mock
import tempfile

PLUGIN = Path(__file__).resolve().parents[1]/'integrations'/'astrbot_plugin_aicompanion'/'main.py'


def load_plugin():
    fakes = {}
    api = types.ModuleType('astrbot.api'); api.logger = types.SimpleNamespace(error=lambda *a: None, warning=lambda *a: None, debug=lambda *a: None)
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
    def __init__(self, text, sender='10001', group='', platform='aiocqhttp', called=True, images=None):
        self.message_str = text; self.sender = sender; self.group = group; self.platform = platform
        self.is_at_or_wake_command = called; self.stopped = False
        self.images = images or []
    def get_platform_name(self): return self.platform
    def get_group_id(self): return self.group
    def get_sender_id(self): return self.sender
    def get_sender_name(self): return '阿青'
    def stop_event(self): self.stopped = True
    def plain_result(self, text): return ('plain', text)
    def get_messages(self): return self.images
    def chain_result(self, chain): return ('chain', chain)


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
        self.assertEqual(p.route(Event('在吗')), {'platform': 'qq', 'sender_id': '10001', 'sender_name': '阿青', 'group_id': '', 'text': '在吗', 'called': True})
        self.assertIsNone(p.route(Event('在吗', platform='telegram')))        # 其他平台交还给 AstrBot
        self.assertIsNone(p.route(Event('/help')))                             # AstrBot 指令
        self.assertIsNone(p.route(Event('')))                                  # 完全空消息
        self.assertIsNone(p.route(Event('大家好', group='99999')))            # 没交给她的群
        self.assertIs(p.route(Event('大家好', group='30003', called=False))['called'], False)   # 群里没叫她也转发（旁听），标明没叫
        self.assertIs(p.route(Event('大家好', group='30003'))['called'], True)
        self.assertIsNone(self.plugin(group_only_when_called=True).route(Event('大家好', group='30003', called=False)))   # 旧行为仍可选

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

    def test_overheard_message_gets_no_offline_reply(self):
        event = Event('大家好', group='30003', called=False)
        self.assertEqual(self.run_handler(self.plugin(), event, error=OSError('x')), [])   # 桌宠不在时，旁听的消息不替她出声
        self.assertTrue(event.stopped)

    def test_untouched_events_keep_propagating(self):
        event = Event('/help')
        self.assertEqual(self.run_handler(self.plugin(), event, {'reply': 'x'}), []); self.assertFalse(event.stopped)

    def test_picture_only_message_uses_managed_converter(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'photo.png'
            try:
                from PIL import Image
            except ImportError: self.skipTest('压缩测试需要 AstrBot 自带的 Pillow')
            Image.new('RGB', (1800, 1000), 'blue').save(path)
            class Segment:
                type = 'Image'
                async def convert_to_file_path(self): return str(path)
            plugin = self.plugin(); plugin.managed_temp_root = lambda: folder
            event = Event('', images=[Segment()])
            out = self.run_handler(plugin, event, {'reply': '蓝色图片', 'silent': False})
            self.assertEqual(out, [('plain', '蓝色图片')])
            self.assertTrue(plugin.sent['images'][0].startswith('data:image/jpeg;base64,'))
            self.assertNotIn(folder, str(plugin.sent)); self.assertEqual(plugin.sent['text'], '[收到图片]')

    def test_picture_converter_refuses_unmanaged_path(self):
        with tempfile.TemporaryDirectory() as folder:
            managed = Path(folder)/'temp'; managed.mkdir()
            outside = Path(folder)/'outside.png'; outside.write_bytes(b'not-an-image')
            class Segment:
                type = 'Image'
                async def convert_to_file_path(self): return str(outside)
            plugin = self.plugin(); plugin.managed_temp_root = lambda: str(managed)
            self.run_handler(plugin, Event('', images=[Segment()]), {'reply': '没能读取', 'silent': False})
            self.assertNotIn('images', plugin.sent); self.assertIn('未能读取', plugin.sent['text'])

    def test_group_passive_or_vision_disabled_does_not_open_image(self):
        class Segment:
            type = 'Image'
            async def convert_to_file_path(self): raise AssertionError('不应读取图片')
        plugin = self.plugin(group_only_when_called=False)
        self.run_handler(plugin, Event('', group='30003', called=False, images=[Segment()]), {'reply': '', 'silent': True})
        self.assertNotIn('images', plugin.sent)
        self.assertIn('旁听不自动识图', plugin.sent['text'])
        plugin.pet_vision = False
        self.run_handler(plugin, Event('', images=[Segment()]), {'reply': '', 'silent': True})
        self.assertNotIn('images', plugin.sent)
        self.assertIn('识图已关闭', plugin.sent['text'])

    def test_meme_is_sent_as_astrbot_image_message_even_without_text(self):
        components = types.ModuleType('astrbot.api.message_components')
        components.Image = type('Image', (), {'fromBase64': staticmethod(lambda value: ('image', value))})
        components.Plain = lambda value: ('text', value)
        with mock.patch.dict(sys.modules, {'astrbot.api.message_components': components}):
            result = self.run_handler(self.plugin(), Event('发表情'), {'reply': '', 'silent': False, 'images': ['data:image/png;base64,YWJj']})
            self.assertEqual(result, [('chain', [('image', 'YWJj')])])
            result = self.run_handler(self.plugin(), Event('发表情'), {'reply': '好呀', 'silent': False, 'images': ['file:///private.png']})
            self.assertEqual(result, [('chain', [('text', '好呀')])])


class HeartbeatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_plugin()

    def test_heartbeat_reports_onebot_and_uses_pet_groups(self):
        class Api:
            async def call_action(self, action):
                return {'get_login_info': {'user_id': 888}, 'get_group_list': [{'group_id': 30003, 'group_name': '测试群'}]}[action]
        client = type('Client', (), {'api': Api()})()
        platform = type('P', (), {'get_client': lambda self: client})()
        context = type('Ctx', (), {'get_platform': lambda self, key: platform if key == 'aiocqhttp' else None})()
        plugin = self.module.AICompanionBridge(context=context, config={'token': 't', 'groups': []})
        sent = {}
        class Response:
            status = 200
            async def json(self): return {'ok': True, 'groups': ['30003']}
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
        async def post(path, payload, timeout): sent.update(path=path, payload=payload); return Response()
        plugin.post = post
        asyncio.run(plugin.heartbeat_once())
        self.assertEqual(sent['path'], '/v1/heartbeat')
        self.assertEqual(sent['payload']['onebot'], {'connected': True, 'self_id': '888'})
        self.assertEqual(sent['payload']['groups'], [{'id': '30003', 'name': '测试群'}])
        self.assertEqual(plugin.pet_groups, ['30003'])
        self.assertIsNotNone(plugin.route(Event('大家好', group='30003')))       # 群以桌宠为准，插件配置里没填也行

    def test_onebot_not_connected(self):
        class Api:
            async def call_action(self, action): raise RuntimeError('ApiNotAvailable')
        platform = type('P', (), {'get_client': lambda self: type('C', (), {'api': Api()})()})()
        context = type('Ctx', (), {'get_platform': lambda self, key: platform})()
        plugin = self.module.AICompanionBridge(context=context, config={})
        state, groups = asyncio.run(plugin.onebot_state())
        self.assertFalse(state['connected']); self.assertEqual(groups, [])
        empty = self.module.AICompanionBridge(context=type('Ctx', (), {'get_platform': lambda self, key: None})(), config={})
        self.assertIn('aiocqhttp', asyncio.run(empty.onebot_state())[0]['error'])

if __name__ == '__main__':
    unittest.main()
