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
    base_url = 'https://api.deepseek.com'; api_key = 'synthetic-key'; timeout_seconds = 30
    def __init__(self): self.calls = []; self.answer = '看到了，是蓝色方块。'
    def generate_reply_stream(self, history, prompt, cancellation=None):
        self.calls.append((history, prompt)); yield self.answer


class Vision:
    def __init__(self): self.calls = []; self.failure = False
    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.failure: raise RuntimeError('上游敏感详情 synthetic-key data:image/png;base64,DO-NOT-LEAK')
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
        self.cid = self.controller.character.id; self.quota = Quota(); self.now = 1000
        def factory(**kwargs): self.factory_args.append(kwargs); return self.vision
        self.media = QQMedia(self.db, self.controller, vision_factory=factory, clock=lambda: self.now, choose=lambda paths: paths[0])
        self.service = BridgeService(self.db, self.controller, quota=self.quota, media=self.media)
        self.db.set_setting('bridge_owner:qq', '10001'); self.db.set_setting('bridge_groups', ['30003'])
        self.db.set_setting('bridge_memory_every', 0)
        self.image = inline_image(picture(), 'image/png')

    def tearDown(self): self.tmp.cleanup()

    def ask(self, **kwargs):
        return self.service.handle({'platform': 'qq', 'sender_id': '10001', 'text': '看看图', 'images': [self.image], **kwargs})

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
        self.assertTrue(history[-1].content.startswith('[收到图片]'))
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
        for images in ([self.image]*4, ['file:///C:/private.png'], ['https://example.invalid/image.png'],
                       ['data:image/png;base64,YmFk'], ['data:image/png;base64,'+'A'*(MAX_IMAGE_BYTES*2)], 'not-a-list'):
            with self.subTest(images_type=type(images).__name__):
                self.assertEqual(self.ask(images=images)['reason'], 'bad_image')
        self.assertEqual(self.vision.calls, []); self.assertEqual(self.chat.calls, [])

    def test_pixel_limit_checked_before_decode(self):
        with self.assertRaises(MediaError): image_bytes(inline_image(picture(8193, 1), 'image/png'))

    def test_vision_failure_and_disabled_do_not_invent_observations_or_leak_error(self):
        self.vision.failure = True; self.ask()
        history = self.chat.calls[-1][0][-1].content
        self.assertIn('识别失败', history); self.assertNotIn('synthetic-key', history); self.assertNotIn('DO-NOT-LEAK', history)
        self.db.set_setting('qq_vision_enabled', False); self.ask()
        self.assertEqual(len(self.vision.calls), 1); self.assertIn('识图已关闭', self.chat.calls[-1][0][-1].content)

    def test_other_provider_key_is_never_reused(self):
        self.chat.base_url = 'https://another-provider.example/v1'
        self.ask()
        self.assertEqual(self.factory_args, []); self.assertIn('不是 DeepSeek 官方接口', self.chat.calls[-1][0][-1].content)

    def test_quota_rechecked_after_vision_spends_budget(self):
        original = self.vision.chat
        def consume(*args, **kwargs): self.quota.stop = True; return original(*args, **kwargs)
        self.vision.chat = consume
        self.assertEqual(self.ask()['reason'], 'quota'); self.assertEqual(self.chat.calls, [])

    def make_pack(self):
        root = self.root/'pack'; (root/'memes'/'happy').mkdir(parents=True)
        (root/'memes'/'happy'/'blue.png').write_bytes(picture())
        (root/'manifest.json').write_text(json.dumps({'schema_version': 1, 'categories': {'happy': {'description': '开心'}}}), encoding='utf-8-sig')
        self.db.set_setting('qq_meme_root', str(root))
        return root

    def test_community_pack_sends_one_image_and_stores_no_transport_tag(self):
        self.make_pack(); self.chat.answer = '好耶！<meme name="happy"/>'
        result = self.ask(images=[])
        self.assertEqual(result['reply'], '好耶！'); self.assertEqual(len(result['images']), 1)
        image_bytes(result['images'][0])
        rows = self.db.recent_messages(10, self.cid)
        self.assertIn('[发送表情：happy]', rows[-1]['content'])
        self.assertNotIn('<meme', rows[-1]['content']); self.assertNotIn('base64', rows[-1]['content'])

    def test_image_only_reply_and_cooldown_then_reenable(self):
        self.make_pack(); self.chat.answer = '<meme name="happy"/>'
        first = self.ask(images=[])
        self.assertFalse(first['silent']); self.assertEqual(first['reply'], '')
        self.assertIn('images', first)
        second = self.ask(images=[]); self.assertNotIn('images', second)
        self.now += 61; self.assertIn('images', self.ask(images=[]))

    def test_unknown_path_tag_disabled_or_deleted_image_never_sends_file(self):
        pack = self.make_pack(); self.chat.answer = '<meme name="../../private"/>'
        self.assertNotIn('images', self.ask(images=[]))
        self.chat.answer = '<meme name="happy"/>'; self.db.set_setting('qq_memes_enabled', False)
        self.assertNotIn('images', self.ask(images=[])); self.db.set_setting('qq_memes_enabled', True)
        offer = self.media.offer('desktop'); (pack/'memes'/'happy'/'blue.png').unlink()
        self.assertEqual(self.media.render(self.chat.answer, 'complete', offer, 'desktop')['images'], [])

    def test_builtin_original_whale_images_are_available(self):
        offer = self.media.offer('desktop')
        self.assertIn('happy', offer.categories)
        result = self.media.render('<meme name="happy"/>', 'complete', offer, 'desktop')
        self.assertEqual(len(result['images']), 1); image_bytes(result['images'][0])

    def test_failed_partial_generation_does_not_send_meme(self):
        self.make_pack()
        def fail(*args):
            yield '还没发<meme name="happy"/>'
            raise RuntimeError('synthetic failure')
        self.chat.generate_reply_stream = fail
        self.assertEqual(self.ask(images=[])['reason'], 'error')
        self.assertNotIn('<meme', self.db.recent_messages(10, self.cid)[-1]['content'])
        self.assertIsNone(self.db.get_setting(self.media._cooldown_key('desktop')))

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
                   'images': [inline_image(bytes(buffer.data()), 'image/png')]}
        body = json.dumps(payload).encode('utf8')
        self.assertGreater(len(body), 64*1024)  # 原先的文本接口上限不能承载普通图片
        server = BridgeServer(self.service, 0, 'synthetic-token')
        try:
            request = urllib.request.Request(f'http://127.0.0.1:{server.port}/v1/chat', data=body,
                                             headers={'Authorization': 'Bearer synthetic-token', 'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=10) as response: result = json.loads(response.read())
            self.assertEqual(result['reply'], '看到了！'); self.assertEqual(len(result['images']), 1)
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
        self.assertEqual(len(result['images']), 1)
        self.assertIn('不需要等别人要求', self.chat.calls[-1][1])
        self.assertIn('不能虚构自己实际抽卡', self.chat.calls[-1][1])
        self.now += 61
        self.ask(images=[], text='你以后多带点表情包')
        prompt = self.chat.calls[-1][1]; self.assertIn('不必等人要求', prompt)
        self.now += 61
        self.ask(images=[], text='不要发表情包')
        self.assertNotIn('images', self.ask(images=[], text='嗯嗯'))

    def test_group_can_remain_silent_instead_of_customer_service_reply(self):
        self.chat.answer = '【不说】'
        result = self.ask(images=[], text='嗯嗯', sender_id='20002', group_id='30003')
        self.assertTrue(result['silent']); self.assertEqual(result['reply'], '')
        rows = self.db.recent_messages(10, self.cid, channel='qq:group:30003')
        self.assertFalse(any(row['role'] == 'assistant' for row in rows))
        self.assertIn('不是每条消息的答题客服', self.chat.calls[-1][1])


if __name__ == '__main__': unittest.main()
