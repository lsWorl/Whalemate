"""PR #14 描述里留给 Claude 的三项 P2，在 collaboration-baseline（91d8bef）上复核。全部合成：临时库、合成图、插件替身，不联网。

1. QQ 文字失败 / 超时后仍发图片：被动回复已有 test_expression_qq.test_text_failure_before_image_stops_and_reports_not_attempted、
   test_middle_text_failure_stops_remaining_text_and_image；这里补主动消息（发件箱）那条路。
2. delivery 读状态与最终 trace 写入之间的回执竞态：已有 test_expression_decision.test_receipt_between_delivery_update_and_trace_update_is_not_lost、
   test_receipt_arriving_while_trace_is_written_still_ends_displayed。
3. 自带图自动识别把用户已停用的 pending 图重新启用、覆盖自定义语义：已有 test_explicit_disable_and_custom_meaning_survive_recognition
   （先启用再停用）；这里补 Codex 原始条件——图还在待审核时直接停用、从没启用过。"""
import asyncio
import hashlib
import sys
import types
import unittest
from unittest import mock

import test_astrbot_plugin as plugin_fixture
import test_expression_catalog as catalog_tests   # 只借用夹具；不把它的测试类导进本模块（否则会重复跑）


class PendingDisableTests(unittest.TestCase):
    setUp = catalog_tests.OriginalSpriteCopyTests.setUp; tearDown = catalog_tests.OriginalSpriteCopyTests.tearDown
    copy_pending = catalog_tests.OriginalSpriteCopyTests.copy_pending

    def test_disabled_while_pending_stays_disabled_after_recognition(self):
        aid = self.copy_pending()
        self.assertEqual(self.cat.get(aid)['review_status'], 'pending_review')
        self.cat.annotate(aid, {'semantic': '仅作纪念，不要发送', 'intents': ['纪念'], 'emotions': ['平静']})
        self.cat.enable(aid, False)                                                       # 待审核时直接停用
        self.cat.scan(); self.cat.scan()                                                  # 识别出是自带原创图，再扫一次也一样
        row = self.cat.get(aid)
        self.assertEqual((row['review_status'], row['enabled']), ('approved', 0))
        self.assertEqual(row['metadata']['semantic'], '仅作纪念，不要发送')
        self.assertEqual(row['metadata']['intents'], ['纪念'])
        self.assertEqual(row['metadata']['license'], 'project-original')


class OutboxTextFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.module = plugin_fixture.load_plugin()

    def run_outbox(self, item, fail_at):
        plugin = self.module.AICompanionBridge(context=None, config={'token': 't', 'groups': []})
        plugin.private_origins['10001'] = 'aicompanion_qq:FriendMessage:10001'
        attempted, posts = [], []
        class Chain:
            def __init__(self, chain): self.chain = chain
        api_event = types.ModuleType('astrbot.api.event'); api_event.MessageChain = Chain
        components = types.ModuleType('astrbot.api.message_components')
        components.Image = type('Image', (), {'fromBase64': staticmethod(lambda value: ('image', value))})
        components.Plain = lambda value: ('text', value)
        async def send_message(origin, chain):
            attempted.append(chain.chain[0])
            if len(attempted) == fail_at: raise asyncio.TimeoutError()
            return True
        plugin.context = types.SimpleNamespace(send_message=send_message)
        async def control(path, payload): posts.append((path, payload)); return {'allowed': True}
        # 合入 #46 第三轮（插件 0.10.0 先领取再发）后发件箱会先 POST /v1/outbox/claim：这里一律允许
        async def post_json(path, payload): posts.append((path, payload)); return {'ok': True, 'allowed': True}
        async def pause(text, delay=None): pass
        plugin.expression_control = control; plugin.post_json = post_json; plugin.pause = pause
        with mock.patch.dict(sys.modules, {'astrbot.api.event': api_event, 'astrbot.api.message_components': components}):
            asyncio.run(plugin.deliver_outbox(item))
        return attempted, posts

    def test_after_text_sticker_not_sent_when_text_times_out(self):
        for fail_at in (1, 2):                                                            # 第一段 / 第二段超时
            item = {'id': f'{fail_at}'*32, 'user_id': '10001', 'segments': ['爹', '吃饭了没'], 'expression': {'attachments': [
                {'delivery_id': 'b'*32, 'placement': 'after_text', 'data_uri': 'data:image/png;base64,YWJj',
                 'content_sha256': hashlib.sha256(b'abc').hexdigest()}]}}
            attempted, posts = self.run_outbox(item, fail_at)
            self.assertNotIn(('image', 'YWJj'), attempted, fail_at)
            self.assertNotIn(('/v1/expression/claim', {'delivery_id': 'b'*32}), posts)
            self.assertFalse(any(path == 'ack' and payload.get('status') == 'accepted' for path, payload in posts))


if __name__ == '__main__':
    unittest.main()
