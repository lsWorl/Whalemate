"""AstrBot 插件（integrations/astrbot_plugin_aicompanion）：用假的 astrbot 接口检查转发规则和离线行为。
不需要安装 AstrBot；aiohttp 没装时也用假模块代替。"""
import asyncio
import base64
import io
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
    api = types.ModuleType('astrbot.api'); api.logger = types.SimpleNamespace(error=lambda *a: None, warning=lambda *a: None, debug=lambda *a: None, info=lambda *a: None)
    event = types.ModuleType('astrbot.api.event')

    registered = []   # 被装饰器登记为消息处理函数的名字（真实 AstrBot 只调用登记过的）

    class Filter:
        class EventMessageType: ALL = 'all'
        @staticmethod
        def event_message_type(kind, priority=0):
            def mark(fn): registered.append(fn.__name__); return fn
            return mark
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
        module.REGISTERED = list(registered)
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
        self.sent_messages=[]
    def get_platform_name(self): return self.platform
    def get_group_id(self): return self.group
    def get_sender_id(self): return self.sender
    def get_sender_name(self): return '阿青'
    def stop_event(self): self.stopped = True
    def plain_result(self, text): return ('plain', text)
    def get_messages(self): return self.images
    def chain_result(self, chain): return ('chain', chain)
    async def send(self,message):self.sent_messages.append(message)


class PluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_plugin()

    def plugin(self, **config):
        plugin = self.module.AICompanionBridge(context=None, config={'token': 't', 'groups': ['30003'], **config})
        async def denied(payload): return {'allowed': False}
        plugin.avatar_permission = denied
        return plugin

    def run_handler(self, plugin, event, answer=None, error=None):
        async def fake(payload):
            plugin.sent = payload
            if error: raise error
            return answer
        plugin.sent = None; plugin.ask_pet = fake
        async def collect(): return [item async for item in plugin.on_message(event)]
        return asyncio.run(collect())

    def test_handler_is_registered(self):
        # 0.9.0 实测：装饰器挂错函数，插件收不到任何消息，私聊全部回"LLM 请求失败"
        self.assertEqual(self.module.REGISTERED, ['on_message'])
        import inspect
        self.assertTrue(inspect.isasyncgenfunction(self.module.AICompanionBridge.on_message))

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
            self.assertNotIn(folder, str(plugin.sent)); self.assertEqual(plugin.sent['text'], '[图片]')

    def test_picture_converter_refuses_unmanaged_path(self):
        with tempfile.TemporaryDirectory() as folder:
            managed = Path(folder)/'temp'; managed.mkdir()
            outside = Path(folder)/'outside.png'; outside.write_bytes(b'not-an-image')
            class Segment:
                type = 'Image'
                async def convert_to_file_path(self): return str(outside)
            plugin = self.plugin(); plugin.managed_temp_root = lambda: str(managed)
            self.run_handler(plugin, Event('', images=[Segment()]), {'reply': '没能读取', 'silent': False})
            self.assertNotIn('images', plugin.sent); self.assertIn('[图片]', plugin.sent['text']); self.assertNotIn('未能读取', plugin.sent['text'])

    def test_private_gif_and_webp_stickers_are_forwarded_as_actual_pixels(self):
        try:
            from PIL import Image
        except ImportError: self.skipTest('压缩测试需要 AstrBot 自带的 Pillow')
        with tempfile.TemporaryDirectory() as folder:
            for extension in ('gif', 'webp'):
                with self.subTest(extension=extension):
                    path = Path(folder)/('sticker.'+extension)
                    Image.new('RGB', (80, 80), 'blue').save(
                        path, save_all=True, append_images=[Image.new('RGB', (80, 80), 'red')], duration=100, loop=0)
                    class Segment:
                        type = 'Image'
                        async def convert_to_file_path(self): return str(path)
                    plugin = self.plugin(); plugin.managed_temp_root = lambda: folder
                    self.run_handler(plugin, Event('[动画表情]', images=[Segment()]), {'reply': '蓝色', 'silent': False})
                    self.assertTrue(plugin.sent['has_image'])
                    self.assertEqual(len(plugin.sent['images']), 1)
                    with Image.open(io.BytesIO(base64.b64decode(plugin.sent['images'][0].split(',', 1)[1]))) as picture:
                        red, green, blue = picture.getpixel((40, 40))
                        self.assertGreater(blue, red+100)
                        self.assertGreater(blue, green+100)  # 转发首帧图像，不能只转 [动画表情] 提示
                    self.assertNotIn('未能读取', plugin.sent['text'])

    def test_group_passive_or_vision_disabled_does_not_open_image(self):
        class Segment:
            type = 'Image'
            async def convert_to_file_path(self): raise AssertionError('不应读取图片')
        plugin = self.plugin(group_only_when_called=False)
        self.run_handler(plugin, Event('', group='30003', called=False, images=[Segment()]), {'reply': '', 'silent': True})
        self.assertNotIn('images', plugin.sent)
        self.assertIn('[图片]', plugin.sent['text']); self.assertNotIn('识图', plugin.sent['text'])
        plugin.pet_vision = False
        self.run_handler(plugin, Event('', images=[Segment()]), {'reply': '', 'silent': True})
        self.assertNotIn('images', plugin.sent)
        self.assertEqual(plugin.sent['text'].count('[图片]'), 1); self.assertNotIn('识图', plugin.sent['text'])

    def test_meme_is_sent_as_astrbot_image_message_even_without_text(self):
        import hashlib
        components=types.ModuleType('astrbot.api.message_components')
        components.Image=type('Image',(),{'fromBase64':staticmethod(lambda value:('image',value))})
        components.Plain=lambda value:('text',value)
        plugin=self.plugin();controls=[]
        async def control(path,payload):controls.append((path,payload));return {'allowed':True,'ok':True}
        plugin.expression_control=control
        answer={'reply':'','silent':False,'expression_version':1,'expression':{'text':'','attachments':[
            {'delivery_id':'a'*32,'asset_id':'st_test0000','revision':'x','placement':'before_text',
             'data_uri':'data:image/png;base64,YWJj','content_sha256':hashlib.sha256(b'abc').hexdigest()}]}}
        with mock.patch.dict(sys.modules,{'astrbot.api.message_components':components}):
            event=Event('发表情');self.assertEqual(self.run_handler(plugin,event,answer),[])
            self.assertEqual(event.sent_messages,[('chain',[('image','YWJj')])])
            self.assertEqual(controls[-1],('ack',{'delivery_id':'a'*32,'status':'accepted'}))
            event=Event('再发');self.run_handler(plugin,event,answer);self.assertEqual(event.sent_messages,[])
            self.assertEqual(self.run_handler(self.plugin(),Event('发表情'),{'reply':'好呀','silent':False,'images':['file:///private.png']}),[('plain','好呀')])

    def test_bare_at_goes_to_her_and_never_falls_back_to_astrbot_llm(self):
        # 2026-11-09 实测：群里只 @ 她、不带字，AstrBot 自己（没配模型）回"LLM 请求失败"
        class AtEvent(Event):
            def __init__(self, *a, raw=None, me='2776148574', **k):
                super().__init__(*a, **k); self.call_llm = False; self.me = me
                self.message_obj = types.SimpleNamespace(raw_message={'message': raw or []})
            def should_call_llm(self, value): self.call_llm = value
            def get_self_id(self): return self.me
        plugin = self.plugin()
        event = AtEvent('', group='30003', called=True)
        self.run_handler(plugin, event, {'reply': '咋了', 'silent': False})
        self.assertEqual(plugin.sent['text'], self.module.AT_ONLY); self.assertTrue(event.stopped); self.assertTrue(event.call_llm)
        self.assertIs(plugin.sent['debounce'], True)                                 # 0.9.3：允许桌宠合并连发
        # AstrBot 没打唤醒标记、但原始消息里 @ 的是她自己：也算叫她
        event = AtEvent('', group='30003', called=False, raw=[{'type': 'at', 'data': {'qq': '2776148574'}}, {'type': 'text', 'data': {'text': '  '}}])
        self.run_handler(plugin, event, {'reply': '在呢', 'silent': False})
        self.assertEqual(plugin.sent['text'], self.module.AT_ONLY); self.assertTrue(event.call_llm)
        # @ 的是别人、不带字：不是叫她，不接
        event = AtEvent('', group='30003', called=False, raw=[{'type': 'at', 'data': {'qq': '3023143286'}}])
        self.run_handler(plugin, event, {'reply': 'x', 'silent': False}); self.assertIsNone(plugin.sent); self.assertFalse(event.stopped)
        # 她选择不回（silent）：也不能让 AstrBot 的默认模型接手
        event = AtEvent('看图', group='30003', called=True)
        self.assertEqual(self.run_handler(plugin, event, {'reply': '', 'silent': True}), []); self.assertTrue(event.call_llm)
        # 没勾选的群里只 @：同样吞掉，不落到 AstrBot 的模型
        event = AtEvent('', group='99999', called=True)
        self.run_handler(plugin, event, {'reply': 'x', 'silent': False}); self.assertIsNone(plugin.sent)
        self.assertTrue(event.stopped); self.assertTrue(event.call_llm)
        # 私聊里空消息（戳一戳之类）不当成叫她
        event = AtEvent('', called=True); self.run_handler(plugin, event, {'reply': 'x', 'silent': False}); self.assertIsNone(plugin.sent)

    def test_busy_group_reply_quotes_the_message_once(self):
        # 负责人 2026-11-09 22:32：群里多个人 @ 她时分不清她在回谁。桌宠给 quote 时，第一条回复引用对方那条消息
        components=types.ModuleType('astrbot.api.message_components')
        components.Plain=lambda value:('text',value)
        components.Reply=lambda id:('reply',id)
        class QuoteEvent(Event):
            def __init__(self,*a,**k):
                super().__init__(*a,**k); self.message_obj=types.SimpleNamespace(message_id=777,raw_message={})
        with mock.patch.dict(sys.modules,{'astrbot.api.message_components':components}):
            plugin=self.plugin()
            async def pause(text,delay=None):pass
            plugin.pause=pause
            event=QuoteEvent('你好',group='30003',called=True)
            self.run_handler(plugin,event,{'reply':'在\n干嘛','silent':False,'segments':['在','干嘛'],'quote':True})
            self.assertEqual([m[1] for m in event.sent_messages],[[('reply','777'),('text','在')],[('text','干嘛')]])
            event=QuoteEvent('你好',group='30003',called=True)
            out=self.run_handler(plugin,event,{'reply':'在','silent':False,'quote':True})
            self.assertEqual(out,[('chain',[('reply','777'),('text','在')])])
            event=QuoteEvent('你好',called=True)                                       # 私聊不引用
            self.assertEqual(self.run_handler(plugin,event,{'reply':'在','silent':False,'quote':True}),[('plain','在')])

    def test_pause_uses_given_delay_or_estimates_typing_time(self):
        waited=[]
        async def sleep(seconds):waited.append(seconds)
        plugin=self.plugin()
        with mock.patch('asyncio.sleep',sleep):
            for args in (('逆天这次都用上了',2.7),('逆天这次都用上了',),('嗯',)):asyncio.run(plugin.pause(*args))
        self.assertEqual(waited[0],2.7);self.assertGreater(waited[1],2.0);self.assertEqual(waited[2],1.0)

    def test_multi_segment_reply_is_sent_as_separate_messages(self):
        # 负责人要求：多段回复分几条消息发，不塞进一条
        components=types.ModuleType('astrbot.api.message_components')
        components.Image=type('Image',(),{'fromBase64':staticmethod(lambda value:('image',value))})
        components.Plain=lambda value:('text',value)
        plugin=self.plugin();pauses=[]
        async def pause(text,delay=None):pauses.append(text)
        plugin.pause=pause
        with mock.patch.dict(sys.modules,{'astrbot.api.message_components':components}):
            event=Event('好消息')
            out=self.run_handler(plugin,event,{'reply':'哇！\n\n恭喜爹','silent':False,'segments':['哇！','恭喜爹']})
            self.assertEqual(out,[]);self.assertEqual(event.sent_messages,[('chain',[('text','哇！')]),('chain',[('text','恭喜爹')])])
            self.assertEqual(pauses,['恭喜爹'])
            # 负责人 2026-11-09 18:05：每条之间隔一段时间——用桌宠按打字速度算好的间隔；格式不对就自己估
            delays=[]
            async def timed(text,delay=None):delays.append(delay)
            plugin.pause=timed
            self.run_handler(plugin,Event('哈'),{'reply':'哟\n逆天','silent':False,'segments':['哟','逆天这次都用上了'],'segment_delays':[0,2.7]})
            self.run_handler(plugin,Event('哈'),{'reply':'哟\n逆天','silent':False,'segments':['哟','逆天'],'segment_delays':[0,99]})
            self.assertEqual(delays,[2.7,None])
            plugin.pause=pause
            # 旧桌宠没有 segments：整段一条
            self.assertEqual(self.run_handler(plugin,Event('在吗'),{'reply':'在呢','silent':False}),[('plain','在呢')])
            # 不合法的 segments 不用：回退为整段
            self.assertEqual(self.run_handler(plugin,Event('在吗'),{'reply':'在呢','silent':False,'segments':['在呢',3]}),[('plain','在呢')])

    def test_every_segment_is_sent_without_merging(self):
        # Codex 审查 17012ca P2：两边上限要一致；0.9.6 起不按条数防刷屏合并（负责人 2026-11-11：按打字速度慢慢发就不会刷屏）
        from aicompanion.expression.segments import QQ_SEGMENTS_MAX
        self.assertEqual(self.module.MAX_SEGMENTS, QQ_SEGMENTS_MAX)
        components=types.ModuleType('astrbot.api.message_components')
        components.Plain=lambda value:('text',value)
        for count in (7, 8, 11):
            plugin=self.plugin();delays=[]
            async def pause(text,delay=None):delays.append(delay)
            plugin.pause=pause
            parts=[f'第{n}条' for n in range(count)]; given=[0]+[1.0+n%5 for n in range(count-1)]
            with mock.patch.dict(sys.modules,{'astrbot.api.message_components':components}):
                event=Event('聊')
                out=self.run_handler(plugin,event,{'reply':'\n'.join(parts),'silent':False,'segments':parts,'segment_delays':given})
            self.assertEqual(out,[]);self.assertEqual([m[1][0][1] for m in event.sent_messages],parts)
            self.assertEqual(delays,given[1:])

    def test_eight_segments_with_sticker_after_text(self):
        import hashlib
        components=types.ModuleType('astrbot.api.message_components')
        components.Image=type('Image',(),{'fromBase64':staticmethod(lambda value:('image',value))})
        components.Plain=lambda value:('text',value)
        async def control(path,payload):return {'allowed':True,'ok':True}
        plugin=self.plugin();plugin.expression_control=control;delays=[]
        async def pause(text,delay=None):delays.append(delay)
        plugin.pause=pause
        parts=[f'第{n}条' for n in range(8)]
        answer={'reply':'\n'.join(parts),'segments':parts,'segment_delays':[0]+[2.0]*7,'silent':False,'expression_version':1,'expression':{'text':'\n'.join(parts),'attachments':[
            {'delivery_id':'c'*32,'asset_id':'st_test0000','revision':'x','placement':'after_text',
             'data_uri':'data:image/png;base64,YWJj','content_sha256':hashlib.sha256(b'abc').hexdigest()}]}}
        with mock.patch.dict(sys.modules,{'astrbot.api.message_components':components}):
            event=Event('聊');self.run_handler(plugin,event,answer)
        self.assertEqual([m[1][0] for m in event.sent_messages],[('text',p) for p in parts]+[('image','YWJj')])
        self.assertEqual(delays[:7],[2.0]*7)

    def test_sticker_is_its_own_message_before_or_after_the_segments(self):
        import hashlib
        components=types.ModuleType('astrbot.api.message_components')
        components.Image=type('Image',(),{'fromBase64':staticmethod(lambda value:('image',value))})
        components.Plain=lambda value:('text',value)
        async def control(path,payload):return {'allowed':True,'ok':True}
        async def pause(text,delay=None):pass
        for placement,expected in (('before_text',[('image','YWJj'),('text','一'),('text','二')]),
                                   ('after_text',[('text','一'),('text','二'),('image','YWJj')])):
            plugin=self.plugin();plugin.expression_control=control;plugin.pause=pause
            answer={'reply':'一\n\n二','segments':['一','二'],'silent':False,'expression_version':1,'expression':{'text':'一\n\n二','attachments':[
                {'delivery_id':('a' if placement=='before_text' else 'b')*32,'asset_id':'st_test0000','revision':'x','placement':placement,
                 'data_uri':'data:image/png;base64,YWJj','content_sha256':hashlib.sha256(b'abc').hexdigest()}]}}
            with mock.patch.dict(sys.modules,{'astrbot.api.message_components':components}):
                event=Event('来');self.run_handler(plugin,event,answer)
            self.assertEqual([m[1][0] for m in event.sent_messages],expected,placement)

    # ---- v0.8.0：收集别人发的表情包（用户 2026-11-09） ----
    @staticmethod
    def raw(event, segments):
        event.message_obj = types.SimpleNamespace(raw_message={'message': segments}); return event

    def gif(self, folder, name='s.gif'):
        try:
            from PIL import Image
        except ImportError: self.skipTest('需要 AstrBot 自带的 Pillow')
        path = Path(folder)/name
        Image.new('RGB', (40, 40), 'blue').save(path, save_all=True, append_images=[Image.new('RGB', (40, 40), 'red')], duration=100, loop=0)
        return path

    def test_overheard_group_sticker_is_sent_as_original_gif_but_photos_stay_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.gif(folder)
            class Sticker:
                type = 'Image'; file = 'abc.gif'
                async def convert_to_file_path(self): return str(path)
            class Photo:
                type = 'Image'; file = 'photo.jpg'
                async def convert_to_file_path(self): raise AssertionError('旁听的普通照片不应读取')
            plugin = self.plugin(); plugin.managed_temp_root = lambda: folder
            event = self.raw(Event('', group='30003', called=False, images=[Sticker(), Photo()]),
                             [{'type': 'image', 'data': {'file': 'abc.gif', 'sub_type': 1, 'summary': '[动画表情]'}},
                              {'type': 'image', 'data': {'file': 'photo.jpg', 'sub_type': 0}}])
            self.run_handler(plugin, event, {'reply': '', 'silent': True, 'reason': 'observed'})
            self.assertEqual(len(plugin.sent['stickers']), 1)
            self.assertTrue(plugin.sent['stickers'][0].startswith('data:image/gif;base64,'))   # 原图，动画保留
            self.assertEqual(base64.b64decode(plugin.sent['stickers'][0].split(',', 1)[1]), path.read_bytes())
            self.assertNotIn('images', plugin.sent); self.assertNotIn(folder, str(plugin.sent))

    def test_called_mixed_message_reports_which_image_is_the_sticker(self):
        # Codex 审计（7aaa065）：桌宠按对应关系只跳过已看懂的表情，照片照常识图
        with tempfile.TemporaryDirectory() as folder:
            gif = self.gif(folder)
            from PIL import Image
            photo_path = Path(folder)/'p.png'; Image.new('RGB', (30, 30), 'green').save(photo_path)
            class Photo:
                type = 'Image'; file = 'p.png'
                async def convert_to_file_path(self): return str(photo_path)
            class Sticker:
                type = 'Image'; file = 'abc.gif'; sub_type = 1
                async def convert_to_file_path(self): return str(gif)
            plugin = self.plugin(); plugin.managed_temp_root = lambda: folder
            event = self.raw(Event('看', images=[Photo(), Sticker()]),
                             [{'type': 'image', 'data': {'file': 'p.png', 'sub_type': 0}}, {'type': 'image', 'data': {'file': 'abc.gif', 'sub_type': 1}},
                              {'type': 'mface', 'data': {'url': 'https://gxh.vip.qq.com/x/raw300.gif'}}])
            async def market(url): return b'GIF89a'+b'm'*20
            plugin.fetch_market = market
            self.run_handler(plugin, event, {'reply': '好', 'silent': False})
            self.assertEqual(plugin.sent['image_parts'], [0, 1]); self.assertEqual(len(plugin.sent['images']), 2)
            self.assertEqual(plugin.sent['sticker_parts'], [1, -1]); self.assertEqual(len(plugin.sent['stickers']), 2)

    def test_known_sticker_only_sends_hash_and_collect_can_be_turned_off(self):
        import hashlib
        with tempfile.TemporaryDirectory() as folder:
            path = self.gif(folder); digest = hashlib.sha256(path.read_bytes()).hexdigest()
            class Sticker:
                type = 'Image'; file = 'abc.gif'; sub_type = '1'
                async def convert_to_file_path(self): return str(path)
            plugin = self.plugin(); plugin.managed_temp_root = lambda: folder
            self.run_handler(plugin, Event('', group='30003', called=False, images=[Sticker()]),
                             {'reply': '', 'silent': True, 'stickers_known': [digest]})
            self.assertIn(digest, plugin.known_stickers)
            self.run_handler(plugin, Event('', group='30003', called=False, images=[Sticker()]), {'reply': '', 'silent': True})
            self.assertEqual(plugin.sent['stickers'], [{'sha256': digest}])
            plugin.pet_collect = False
            self.run_handler(plugin, Event('', group='30003', called=False, images=[Sticker()]), {'reply': '', 'silent': True})
            self.assertNotIn('stickers', plugin.sent)

    def test_market_face_only_message_is_routed_and_only_fixed_hosts_are_fetched(self):
        plugin = self.plugin(); fetched = []
        async def fetch(url): fetched.append(url); return None
        plugin.fetch_market = fetch
        event = self.raw(Event('', group='30003', called=False),
                         [{'type': 'mface', 'data': {'url': 'https://gxh.vip.qq.com/club/item/parcel/item/ab/abc/raw300.gif', 'summary': '[开心]'}},
                          {'type': 'mface', 'data': {'url': 'https://evil.example.com/x.gif'}},
                          {'type': 'mface', 'data': {'url': 'http://gxh.vip.qq.com/x.gif'}}])
        self.assertIsNotNone(plugin.route(event))     # AstrBot 丢掉了商城表情，原始段里仍能认出来
        self.run_handler(plugin, event, {'reply': '', 'silent': True})
        self.assertEqual(fetched, ['https://gxh.vip.qq.com/club/item/parcel/item/ab/abc/raw300.gif'])
        self.assertFalse(plugin.market_url_ok('https://gxh.vip.qq.com.evil.com/x.gif'))

    def test_sticker_uri_accepts_only_image_headers(self):
        self.assertTrue(self.module.AICompanionBridge.sticker_uri(b'GIF89a'+b'x'*10).startswith('data:image/gif;base64,'))
        self.assertTrue(self.module.AICompanionBridge.sticker_uri(b'RIFF\0\0\0\0WEBPVP8 ').startswith('data:image/webp;base64,'))
        for bad in (b'', b'<svg/>', b'GIF89a'+b'x'*(1024*1024)):
            with self.assertRaises(ValueError): self.module.AICompanionBridge.sticker_uri(bad)

    def test_avatar_only_downloads_after_desktop_permission_and_uses_sender(self):
        plugin = self.plugin(); calls = []
        async def permission(payload):
            calls.append(('permission', payload.copy()))
            return {'allowed': True, 'avatar_needed': True, 'avatar_subject': payload['sender_id']}
        async def fetch(subject): calls.append(('fetch', subject)); return 'data:image/jpeg;base64,YWJj'
        plugin.avatar_permission = permission; plugin.fetch_avatar = fetch
        self.run_handler(plugin, Event('你好'), {'reply': '嗨', 'silent': False})
        self.assertEqual([row[0] for row in calls], ['permission', 'fetch'])
        self.assertEqual(calls[1][1], '10001'); self.assertIn('avatar', plugin.sent)
        self.run_handler(plugin, Event('又来了'), {'reply': '', 'silent': True})
        self.assertEqual(len(calls), 2)  # 防止失败或旧缓存导致每条消息抓取头像

    def test_avatar_passive_denied_or_non_numeric_never_downloads(self):
        plugin = self.plugin(group_only_when_called=False)
        async def fetch(subject): raise AssertionError('不该抓头像')
        plugin.fetch_avatar = fetch
        for event in (Event('你好'), Event('大家好', group='30003', called=False), Event('你好', sender='arbitrary-url')):
            self.run_handler(plugin, event, {'reply': '', 'silent': True})

    def test_avatar_target_accepts_only_numeric_mentions_and_excludes_bot(self):
        plugin = self.plugin()
        event = Event('看看他的头像', group='30003')
        event.get_self_id = lambda: '88888'
        event.get_messages = lambda: [types.SimpleNamespace(type='At', qq=value) for value in ('88888', '40004', 'http://localhost')]
        payload = plugin.route(event)
        self.assertEqual(plugin.avatar_target(event, payload), '40004')
        self.assertEqual(payload['mentioned_ids'], ['40004'])

    def test_avatar_fetch_is_fixed_https_without_redirects_and_is_bounded(self):
        plugin = self.plugin(); requests = []
        class Content:
            async def iter_chunked(self, size): yield b'x'*(129*1024)
        class Response:
            status = 200; content = Content()
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        class Session:
            closed = False
            def get(self, url, **kwargs): requests.append((url, kwargs)); return Response()
        plugin.session = Session()
        self.assertIsNone(asyncio.run(plugin.fetch_avatar('10001')))
        self.assertEqual(requests[0][0], 'https://q1.qlogo.cn/g?b=qq&nk=10001&s=100')
        self.assertFalse(requests[0][1]['allow_redirects'])
        self.assertIsNone(asyncio.run(plugin.fetch_avatar('http://127.0.0.1')))
        self.assertEqual(len(requests), 1)


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
        self.assertEqual(sent['payload']['astrbot'], 'plugin-'+self.module.PLUGIN_VERSION)
        metadata = PLUGIN.with_name('metadata.yaml').read_text(encoding='utf-8')
        self.assertIn('version: v'+self.module.PLUGIN_VERSION+'\n', metadata)
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

