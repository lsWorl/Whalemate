"""QQ 图文和表情：只用合成图片、临时数据库、假的视觉 / 聊天模型，不读用户数据。"""
import base64
import json
from pathlib import Path
import tempfile
import unittest

from aicompanion.bridge.media import QQMedia, MediaError, MAX_IMAGE_BYTES, image_bytes, inline_image
from aicompanion.bridge.service import BridgeService
from aicompanion.controllers.chat_controller import ChatController
from aicompanion.services.memory_service import MemoryService
from aicompanion.storage.database import Database

try:
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage
except ImportError:
    QImage = None


def picture(width=24, height=24):
    image = QImage(width, height, QImage.Format.Format_RGB32); image.fill(0xff3366ff)
    buffer = QBuffer(); buffer.open(QIODevice.OpenModeFlag.WriteOnly); image.save(buffer, 'PNG')
    return bytes(buffer.data())


class Chat:
    base_url = 'https://api.deepseek.com'; api_key = 'synthetic-key'; timeout_seconds = 30; model = 'chat-model'
    def __init__(self): self.calls = []; self.answer = '看到了，是蓝色方块。'
    def generate_reply_stream(self, history, prompt, cancellation=None):
        self.calls.append((history, prompt)); yield self.answer


class Vision:
    def __init__(self): self.calls = []; self.failure = False
    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.failure: raise RuntimeError(self.failure if isinstance(self.failure, str) else '上游敏感详情 synthetic-key data:image/png;base64,DO-NOT-LEAK')
        return {'content': '蓝色方块，图片上没有文字。'}


class Quota:
    def __init__(self): self.stop = False
    def check_silent(self, purpose): return 'stop' if self.stop else 'ok'


@unittest.skipIf(QImage is None, '需要 PySide6 图像解码器')
class QQMediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.db = Database(self.root/'synthetic.sqlite3'); self.db.initialize()
        self.chat = Chat(); self.vision = Vision(); self.factory_args = []
        self.controller = ChatController(self.db, MemoryService(self.db), self.chat)
        from dataclasses import replace
        self.controller.character=replace(self.controller.character,id='deepseek-whale',name='鲸娘')
        self.db.add_character(self.controller.character)
        self.cid = self.controller.character.id; self.quota = Quota(); self.now = 1000
        def factory(**kwargs): self.factory_args.append(kwargs); return self.vision
        self.media = QQMedia(self.db, self.controller, vision_factory=factory, clock=lambda: self.now)
        self.controller.expression.clock=self.media.clock; self.controller.expression.catalog.clock=self.media.clock
        self.service = BridgeService(self.db, self.controller, quota=self.quota, media=self.media)
        self.db.set_setting('bridge_owner:qq', '10001'); self.db.set_setting('bridge_groups', ['30003'])
        self.db.set_setting('bridge_memory_every', 0)
        self.image = inline_image(picture(), 'image/png')

    def tearDown(self): self.tmp.cleanup()

    def ask(self, **kwargs):
        return self.service.handle({'platform': 'qq', 'sender_id': '10001', 'text': '看看图', 'images': [self.image], 'expression_version':1, 'bot_account_id':'90009', **kwargs})

    def test_image_caption_goes_to_same_brain_and_only_text_is_stored(self):
        result = self.ask()
        self.assertFalse(result['silent']); self.assertEqual(len(self.vision.calls), 1); self.assertEqual(len(self.chat.calls), 1)
        messages, options = self.vision.calls[0]
        self.assertEqual(messages[0]['content'][1]['type'], 'image_url')
        self.assertEqual(self.factory_args[0]['model'], 'deepseek-flash')
        history, prompt = self.chat.calls[-1]
        self.assertIn('蓝色方块', history[-1].content)
        rows = self.db.recent_messages(10, self.cid)
        self.assertIn('图片描述', rows[0]['content'])
        self.assertNotIn('base64', ''.join(r['content'] for r in rows)+prompt)
        self.assertNotIn('synthetic-key', ''.join(r['content'] for r in rows)+prompt)

    def test_image_only_private_and_allowed_group(self):
        self.assertEqual(self.ask(text='')['reason'], 'ok')
        self.assertEqual(self.ask(sender_id='20002', group_id='30003', text='')['reason'], 'ok')
        self.assertEqual(len(self.vision.calls), 2)
        self.assertEqual(len(self.db.recent_messages(10, self.cid, channel='qq:group:30003')), 2)

    def test_sticker_observation_is_internal_and_empty_image_does_not_request_analysis(self):
        self.ask(text='')
        history, prompt = self.chat.calls[-1]
        self.assertTrue(history[-1].content.startswith('[图片]'))
        self.assertNotIn('请看看这张图片', history[-1].content)
        self.assertIn('只在心里读懂情绪和含义', prompt)
        self.assertIn('不分析画面', prompt)
        messages, options = self.vision.calls[-1]
        self.assertIn('内部视觉理解', messages[0]['content'][0]['text'])
        self.assertEqual(options['max_tokens'], 400)
        self.ask(text='这张截图写了什么文字？')
        self.assertEqual(self.vision.calls[-1][1]['max_tokens'], 900)  # 正常截图任务保留原来的识别预算

    def test_unauthorized_closed_and_quota_requests_never_decode_or_call_vision(self):
        self.assertEqual(self.ask(sender_id='20002')['reason'], 'not_allowed')
        self.assertEqual(self.ask(group_id='99999')['reason'], 'not_allowed')
        self.service.closed = True; self.assertEqual(self.ask()['reason'], 'closed'); self.service.closed = False
        self.quota.stop = True; self.assertEqual(self.ask()['reason'], 'quota'); self.quota.stop = False
        self.db.set_setting('bridge_public_daily', 0)
        self.assertEqual(self.ask(sender_id='20002', group_id='30003')['reason'], 'daily_limit')
        self.assertEqual(self.vision.calls, []); self.assertEqual(self.chat.calls, [])

    def test_images_are_not_read_in_group_passive_messages(self):
        self.ask(sender_id='20002', group_id='30003', called=False)
        self.assertEqual(self.vision.calls, [])

    def test_invalid_oversized_and_external_references_are_rejected(self):
        # 坏图 / 外部引用 / 超大：从不解码、不送识图；也不回"图片太大""编码无效"这类机器人提示，只当成没看清的 [图片]
        for images in (['file:///C:/private.png'], ['https://example.invalid/image.png'],
                       ['data:image/png;base64,YmFk'], ['data:image/png;base64,'+'A'*(MAX_IMAGE_BYTES*2)], 'not-a-list'):
            with self.subTest(images=str(images)[:30]):
                result = self.ask(images=images)
                self.assertNotEqual(result['reason'], 'bad_image')
                history, prompt = self.chat.calls[-1]
                self.assertIn('[图片]', history[-1].content); self.assertIn('别说自己看不到', prompt)
                for word in ('MiB', '编码', '不支持', 'private.png', 'example.invalid'): self.assertNotIn(word, history[-1].content)
        self.assertEqual(self.vision.calls, [])
        self.ask(images=[self.image]*4)                                                   # 多于 3 张：只看前 3 张，不回"一次最多 3 张"
        self.assertEqual(sum(part['type'] == 'image_url' for part in self.vision.calls[-1][0][0]['content']), 3)

    def test_pixel_limit_checked_before_decode(self):
        with self.assertRaises(MediaError): image_bytes(inline_image(picture(8193, 1), 'image/png'))

    def test_vision_failure_and_disabled_do_not_invent_observations_or_leak_error(self):
        # 负责人 2026-11-09：识别不了也不能说"识别失败 / 识图已关闭"，只留中性的 [图片]，提示她含糊带过或不回
        self.vision.failure = True; self.ask()
        history, prompt = self.chat.calls[-1]
        self.assertIn('[图片]', history[-1].content); self.assertIn('别说自己看不到', prompt)
        for word in ('识别失败', 'synthetic-key', 'DO-NOT-LEAK', '重发'): self.assertNotIn(word, history[-1].content)
        self.assertTrue(self.media.vision_ready())                                        # 网络 / 未知错误只算这一次
        self.db.set_setting('qq_vision_enabled', False); self.ask()
        self.assertEqual(len(self.vision.calls), 1)
        self.assertNotIn('识图已关闭', self.chat.calls[-1][0][-1].content); self.assertIn('[图片]', self.chat.calls[-1][0][-1].content)

    def test_model_without_vision_is_detected_and_not_retried(self):
        # 接口明确拒收图片（4xx）：记下这家接口 + 模型暂不支持看图，12 小时内不再试，心跳告诉插件不用下载图片
        self.vision.failure = '模型 API 请求失败，HTTP 400。平台返回：image input not supported'
        self.ask(); self.assertFalse(self.media.vision_ready()); self.assertFalse(self.service.vision_ready())
        self.ask(); self.assertEqual(len(self.vision.calls), 1)
        self.now += 13*3600; self.assertTrue(self.media.vision_ready())
        self.vision.failure = False; self.ask(); self.assertEqual(len(self.vision.calls), 2)

    def test_vision_only_uses_the_chat_providers_own_endpoint_and_key(self):
        # 负责人 2026-11-09："要看接入的大模型是否有多模态"——换了别家接口也试着用它自己的模型看图；
        # 密钥只发给聊天本身的那家接口，不会拿去调用别的供应商
        self.chat.base_url = 'https://another-provider.example/v1'
        self.ask()
        self.assertEqual([(a['base_url'], a['api_key'], a['model']) for a in self.factory_args],
                         [('https://another-provider.example/v1', 'synthetic-key', 'chat-model')])
        self.db.set_setting('qq_vision_model', 'vision-pro'); self.ask(); self.assertEqual(self.factory_args[-1]['model'], 'vision-pro')
        self.chat.base_url = 'http://plain-http.example/v1'; self.ask()
        self.assertEqual(len(self.factory_args), 2); self.assertIn('[图片]', self.chat.calls[-1][0][-1].content)   # 非 https 不送图

    def test_quota_rechecked_after_vision_spends_budget(self):
        original = self.vision.chat
        def consume(*args, **kwargs): self.quota.stop = True; return original(*args, **kwargs)
        self.vision.chat = consume
        self.assertEqual(self.ask()['reason'], 'quota'); self.assertEqual(self.chat.calls, [])

    def make_pack(self):
        root = self.root/'pack'; (root/'memes'/'happy').mkdir(parents=True)
        (root/'memes'/'happy'/'blue.png').write_bytes(picture())
        (root/'manifest.json').write_text(json.dumps({'schema_version': 1, 'asset_defaults': {'license':'user-owned','author':'合成作者','source':'synthetic:test'}, 'categories': {'happy': {'description': '开心'}}}), encoding='utf-8-sig')
        self.db.set_setting('qq_meme_root', str(root))
        return root

    @staticmethod
    def sent_images(result):
        return [a['data_uri'] for a in result.get('expression',{}).get('attachments',[])]

    def test_community_pack_sends_one_image_and_stores_no_transport_tag(self):
        self.make_pack();self.chat.answer='好耶！<meme name="happy"/>'
        result=self.ask(images=[])
        self.assertEqual(result['reply'],'好耶！');self.assertEqual(len(self.sent_images(result)),1)
        image_bytes(self.sent_images(result)[0])
        rows=self.db.recent_messages(10,self.cid)
        self.assertNotIn('<meme',rows[-1]['content']);self.assertNotIn('base64',rows[-1]['content'])
        # 生成结果不是发送成功，先通过模拟适配器领取并确认。
        aid=result['expression']['attachments'][0]['delivery_id']
        self.assertTrue(self.service.expression_claim({'delivery_id':aid})['allowed'])
        self.assertTrue(self.service.expression_ack({'delivery_id':aid,'status':'accepted'})['ok'])
        self.assertTrue(self.controller.expression.history(result['expression']['turn_id'],self.cid))

    def test_image_only_reply_and_cooldown_then_reenable(self):
        self.make_pack();self.chat.answer='<meme name="happy"/>'
        first=self.ask(images=[]);self.assertFalse(first['silent']);self.assertEqual(first['reply'],'')
        self.assertTrue(self.sent_images(first))
        aid=first['expression']['attachments'][0]['delivery_id'];self.service.expression_claim({'delivery_id':aid})
        self.service.expression_ack({'delivery_id':aid,'status':'accepted'})
        self.assertFalse(self.sent_images(self.ask(images=[])))
        self.now+=301;self.assertTrue(self.sent_images(self.ask(images=[])))

    def test_unknown_path_tag_disabled_or_deleted_image_never_sends_file(self):
        pack=self.make_pack();self.chat.answer='<meme name="../../private"/>'
        self.assertFalse(self.sent_images(self.ask(images=[])))
        self.chat.answer='<meme name="happy"/>';self.db.set_setting('qq_memes_enabled',False)
        self.assertFalse(self.sent_images(self.ask(images=[])));self.db.set_setting('qq_memes_enabled',True)
        (pack/'memes/happy/blue.png').unlink()
        self.assertFalse(self.sent_images(self.ask(images=[])))

    def test_builtin_original_whale_images_are_available(self):
        self.chat.answer='<meme name="happy"/>'
        result=self.ask(images=[]);self.assertEqual(len(self.sent_images(result)),1);image_bytes(self.sent_images(result)[0])

    def test_failed_partial_generation_does_not_send_meme(self):
        self.make_pack()
        def fail(*args):
            yield '还没发<meme name="happy"/>'
            raise RuntimeError('synthetic failure')
        self.chat.generate_reply_stream=fail
        self.assertEqual(self.ask(images=[])['reason'],'error')
        self.assertNotIn('<meme',self.db.recent_messages(10,self.cid)[-1]['content'])
        with self.db.transaction() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM expression_deliveries WHERE status='accepted'").fetchone()[0],0)

    def test_missing_usage_does_not_charge_base64_as_text(self):
        from aicompanion.llm.openai_compatible_client import OpenAICompatibleClient
        small = [{'type': 'text', 'text': '看图'}, {'type': 'image_url', 'image_url': {'url': 'data:small'}}]
        large = [{'type': 'text', 'text': '看图'}, {'type': 'image_url', 'image_url': {'url': 'data:'+'A'*900000}}]
        self.assertEqual(OpenAICompatibleClient._content_chars(small), OpenAICompatibleClient._content_chars(large))
        self.assertEqual(OpenAICompatibleClient._content_chars(large), 2050)

    def test_local_http_round_trip_for_large_image_and_meme(self):
        import random
        import urllib.request
        from aicompanion.bridge.server import BridgeServer
        self.make_pack(); self.chat.answer = '看到了！<meme name="happy"/>'
        pixels = random.Random(7).randbytes(256*256*3)
        noise = QImage(pixels, 256, 256, 256*3, QImage.Format.Format_RGB888).copy()
        buffer = QBuffer(); buffer.open(QIODevice.OpenModeFlag.WriteOnly); noise.save(buffer, 'PNG')
        payload = {'platform': 'qq', 'sender_id': '10001', 'text': '看看图',
                   'images': [inline_image(bytes(buffer.data()), 'image/png')], 'expression_version':1, 'bot_account_id':'90009'}
        body = json.dumps(payload).encode('utf8')
        self.assertGreater(len(body), 64*1024)  # 原先的文本接口上限不能承载普通图片
        server = BridgeServer(self.service, 0, 'synthetic-token')
        try:
            request = urllib.request.Request(f'http://127.0.0.1:{server.port}/v1/chat', data=body,
                                             headers={'Authorization': 'Bearer synthetic-token', 'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=10) as response: result = json.loads(response.read())
            self.assertEqual(result['reply'], '看到了！'); self.assertEqual(len(self.sent_images(result)), 1)
            self.assertEqual(len(self.vision.calls), 1)
        finally: server.close()

    def test_avatar_permission_and_description_cached_without_raw_image(self):
        payload = {'platform': 'qq', 'sender_id': '10001', 'text': '你好'}
        self.assertTrue(self.service.media_permission(payload)['avatar_needed'])
        self.assertFalse(self.service.media_permission({**payload, 'sender_id': '20002'})['allowed'])
        result = self.ask(images=[], avatar=self.image, text='我的头像是什么')
        self.assertEqual(result['reason'], 'ok'); self.assertEqual(len(self.vision.calls), 1)
        prompt = self.chat.calls[-1][1]; self.assertIn('头像', prompt); self.assertIn('蓝色方块', prompt)
        visual_question = self.vision.calls[-1][0][0]['content'][0]['text']
        self.assertIn('不推断真人身份', visual_question)
        self.assertFalse(self.service.media_permission(payload)['avatar_needed'])
        self.ask(images=[], avatar=self.image, text='今天好呀')
        self.assertEqual(len(self.vision.calls), 1)
        stored = json.dumps(self.db.get_setting(self.media.avatar_key), ensure_ascii=False)
        self.assertNotIn('base64', stored); self.assertNotIn('10001', stored)

    def test_avatar_disabled_quota_passive_and_unauthorized_do_not_run_vision(self):
        self.db.set_setting('qq_avatars_enabled', False)
        self.assertFalse(self.service.media_permission({'platform': 'qq', 'sender_id': '10001'})['avatar_needed'])
        self.ask(images=[], avatar=self.image)
        self.assertEqual(self.vision.calls, [])
        self.db.set_setting('qq_avatars_enabled', True); self.quota.stop = True
        self.assertFalse(self.service.media_permission({'platform': 'qq', 'sender_id': '10001'})['allowed'])
        self.quota.stop = False
        self.service.media_permission({'platform': 'qq', 'sender_id': '20002', 'group_id': '30003', 'called': False, 'text': '吃饭了'})
        self.assertEqual(self.vision.calls, [])
        self.ask(images=[], avatar=self.image, sender_id='20002')
        self.assertEqual(self.vision.calls, [])

    def test_mentioned_group_member_avatar_is_labelled_as_theirs(self):
        self.ask(images=[], sender_id='20002', group_id='30003', text='看看他的头像',
                 avatar_subject='40004', mentioned_ids=['40004'], avatar=self.image)
        self.assertIn('此头像属于本条消息 @ 的群友', self.chat.calls[-1][1])
        self.assertTrue(self.service.media_permission({'platform': 'qq', 'sender_id': '20002', 'group_id': '30003',
                           'avatar_subject': '50005', 'mentioned_ids': [], 'text': '头像'})['avatar_subject'] == '20002')

    def test_spontaneous_meme_does_not_need_a_request_and_learns_feedback(self):
        self.make_pack(); self.chat.answer = '笑死<meme name="happy"/>'
        result = self.ask(images=[], text='哈哈今天运气真好')
        self.assertEqual(len(self.sent_images(result)), 1)
        self.assertIn('不用等对方开口要', self.chat.calls[-1][1])
        self.assertIn('不能虚构自己实际抽卡', self.chat.calls[-1][1])
        self.now += 61
        self.ask(images=[], text='你以后多带点表情包')
        prompt = self.chat.calls[-1][1]; self.assertIn('不必等人要求', prompt)
        self.now += 61
        self.ask(images=[], text='不要发表情包')
        self.assertFalse(self.sent_images(self.ask(images=[], text='嗯嗯')))

    def test_group_can_remain_silent_instead_of_customer_service_reply(self):
        self.chat.answer = '【不说】'
        result = self.ask(images=[], text='嗯嗯', sender_id='20002', group_id='30003')
        self.assertTrue(result['silent']); self.assertEqual(result['reply'], '')
        rows = self.db.recent_messages(10, self.cid, channel='qq:group:30003')
        self.assertFalse(any(row['role'] == 'assistant' for row in rows))
        self.assertIn('不是每条消息的答题客服', self.chat.calls[-1][1])


if __name__ == '__main__': unittest.main()
