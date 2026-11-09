"""收集别人发的表情包、分析含义、向群友学发图习惯（用户 2026-11-09）。全部合成图和临时库，不调用真实模型、不发 QQ。"""
from dataclasses import replace
import json
from pathlib import Path
import re
import tempfile
import unittest

from aicompanion.bridge.media import inline_image, parse_sticker_analysis
from aicompanion.bridge.service import BridgeService
from aicompanion.controllers.chat_controller import ChatController
from aicompanion.domain.character import DEFAULT_CHARACTER
from aicompanion.expression import usage
from aicompanion.expression.collect import COLLECT_DIR, StickerCollector, source_tag
from aicompanion.expression.domain import ExpressionContext, owner_relationship
from aicompanion.services.memory_service import MemoryService
from aicompanion.storage.database import Database
from test_expression_catalog import png

GOOD = {'sticker': True, 'suitable': True, 'meaning': '无语到翻白眼', 'use_when': ['对方说了离谱的话'], 'emotions': ['无语'], 'text': ''}


class Model:
    model = 'mock'
    def __init__(self): self.reply = '好耶'; self.prompts = []
    def generate_reply_stream(self, history, prompt, cancellation=None):
        self.prompts.append(prompt); yield self.reply


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); root = Path(self.temp.name)
        self.db = Database(root/'synthetic.sqlite3'); self.db.initialize()
        self.library = root/'library'; (self.library/'自带').mkdir(parents=True)
        self.db.set_setting('qq_meme_root', str(self.library))
        self.character = replace(DEFAULT_CHARACTER, id='deepseek-whale', name='鲸娘'); self.db.add_character(self.character)
        self.model = Model(); self.controller = ChatController(self.db, MemoryService(self.db), self.model)
        self.controller.character = self.character
        self.collector = StickerCollector(self.db, self.controller.expression.catalog)
    def tearDown(self): self.temp.cleanup()

    def ingest(self, color=0xff102030, origin='qq:group:abc'):
        return self.collector.ingest(png(color), character_id=self.character.id, origin=origin)

    def candidates(self, query='好吧', scope='desktop:deepseek-whale', turn='g'):
        context = ExpressionContext(turn, self.character.id, scope, relationship=owner_relationship(), supports_images=True)
        return self.controller.expression.prepare_report(context, self.character, query)


class CollectorTests(Base):
    def test_new_sticker_is_saved_and_only_usable_after_analysis(self):
        result = self.ingest()
        self.assertEqual(result['status'], 'collected')
        files = list((self.library/COLLECT_DIR).iterdir()); self.assertEqual(len(files), 1)
        row = self.controller.expression.catalog.get(result['asset_id'])
        self.assertEqual((row['review_status'], row['enabled'], row['metadata']['analysis']), ('approved', 1, 'pending'))
        self.assertNotIn('qq:group:abc'.split(':')[-1]+'0', json.dumps(row['metadata']))
        items, report = self.candidates()
        self.assertNotIn(result['asset_id'], [c.asset_id for c in items]); self.assertEqual(report['filtered'].get('not_analyzed'), 1)
        self.assertEqual(self.collector.analyze_pending(lambda data, mime: dict(GOOD)), 1)
        meta = self.controller.expression.catalog.get(result['asset_id'])['metadata']
        self.assertEqual((meta['analysis'], meta['semantic'], meta['intents']), ('done', '无语到翻白眼', ['对方说了离谱的话']))
        items, _ = self.candidates(turn='g2')
        self.assertIn(result['asset_id'], [c.asset_id for c in items])   # 用户选择：分析过的收集图任何地方都能用
        again = self.ingest()
        self.assertEqual((again['status'], again['asset_id'], again['meaning']), ('known', result['asset_id'], '无语到翻白眼'))
        self.assertEqual(len(list((self.library/COLLECT_DIR).iterdir())), 1)

    def test_unsuitable_or_failed_analysis_never_becomes_a_candidate(self):
        bad = self.ingest(0xff00ff00); fail = self.ingest(0xff0000ff)
        def analyzer(data, mime):
            return None if data == png(0xff0000ff) else dict(GOOD, suitable=False, reason='广告二维码')
        self.collector.analyze_pending(analyzer); self.collector.analyze_pending(analyzer)
        catalog = self.controller.expression.catalog
        self.assertEqual(catalog.get(bad['asset_id'])['enabled'], 0)
        self.assertEqual(catalog.get(fail['asset_id'])['metadata']['analysis'], 'failed')   # 两次失败后不再重试
        ids = [c.asset_id for c in self.candidates()[0]]
        self.assertNotIn(bad['asset_id'], ids); self.assertNotIn(fail['asset_id'], ids)
        self.assertEqual(self.collector.stats()['unsuitable'], 1); self.assertEqual(self.collector.stats()['failed'], 1)

    def test_daily_analysis_limit(self):
        self.db.set_setting('sticker_analysis_daily', 1)
        self.ingest(0xff111111); self.ingest(0xff222222)
        self.assertEqual(self.collector.analyze_pending(lambda d, m: dict(GOOD)), 1)
        self.assertEqual(len(self.collector.pending()), 1)
        self.assertEqual(self.collector.stats()['analyzed_today'], 1)

    def test_no_library_or_disabled_collects_nothing(self):
        self.db.set_setting('qq_meme_root', '')
        self.assertEqual(self.ingest()['status'], 'no_library')   # 不往仓库自带图目录里写
        self.db.set_setting('qq_meme_root', str(self.library)); self.db.set_setting('sticker_collect_enabled', False)
        self.assertEqual(self.ingest()['status'], 'disabled')
        self.assertFalse((self.library/COLLECT_DIR).exists())

    def test_user_annotation_is_not_overwritten_by_analysis(self):
        result = self.ingest()
        self.controller.expression.catalog.annotate(result['asset_id'], {'semantic': '我自己写的含义'})
        self.collector.analyze_pending(lambda d, m: dict(GOOD))
        self.assertEqual(self.controller.expression.catalog.get(result['asset_id'])['metadata']['semantic'], '我自己写的含义')

    def test_non_boolean_verdicts_never_release_a_sticker(self):
        # Codex 审计（7aaa065）：字符串 "false"、null、缺字段都按分析失败，不能被当成"适合发"
        for raw in ('{"meaning": "synthetic private screenshot", "suitable": "false", "sticker": "false"}',
                    '{"meaning": "x", "suitable": null}', '{"meaning": "x"}', '{"meaning": "x", "suitable": true, "sticker": "true"}'):
            self.assertIsNone(parse_sticker_analysis(raw), raw)
        result = self.ingest(0xff0a0b0c)
        for _ in range(2): self.collector.analyze_pending(lambda d, m: parse_sticker_analysis('{"meaning": "截图", "suitable": "false"}'))
        meta = self.controller.expression.catalog.get(result['asset_id'])['metadata']
        self.assertEqual(meta['analysis'], 'failed'); self.assertNotIn(result['asset_id'], [c.asset_id for c in self.candidates()[0]])

    def test_neutral_or_photo_like_sticker_is_still_usable(self):
        # 负责人 2026-11-09：分类别太严，中性的表情也能发；QQ 本来就标成表情包的反应照片也算，只有 suitable=false 才停用
        result = self.ingest(0xff0d0e0f)
        self.collector.analyze_pending(lambda d, m: {'sticker': False, 'suitable': True, 'meaning': '收到', 'use_when': ['回应'], 'emotions': []})
        self.assertIn(result['asset_id'], [c.asset_id for c in self.candidates('好的')[0]])

    def test_parse_sticker_analysis(self):
        self.assertEqual(parse_sticker_analysis('```json\n'+json.dumps(GOOD, ensure_ascii=False)+'\n```')['meaning'], '无语到翻白眼')
        self.assertIsNone(parse_sticker_analysis('看不清'))
        self.assertIsNone(parse_sticker_analysis('{"meaning": "", "suitable": true}'))
        self.assertFalse(parse_sticker_analysis('{"meaning": "", "suitable": false}')['suitable'])


class UsageTests(Base):
    def test_similar_situation_ranks_learned_sticker_and_notes_stay_in_their_group(self):
        a = self.ingest(0xff333333)['asset_id']; b = self.ingest(0xff444444)['asset_id']
        self.collector.analyze_pending(lambda d, m: dict(GOOD, meaning='笑死'))
        group = source_tag('group:30003')
        usage.record(self.db, self.character.id, a, '小明#2：我把咖啡倒进泡面里了', group)
        table = usage.learned(self.db, self.character.id, '有人把咖啡倒进泡面里', group)
        self.assertGreater(table[a][0], table.get(b, (0, ''))[0])
        self.assertIn('咖啡倒进泡面', table[a][1]); self.assertNotIn('小明', table[a][1])   # 去掉说话人
        other = usage.learned(self.db, self.character.id, '有人把咖啡倒进泡面里', source_tag('group:40004'))
        self.assertNotIn('咖啡', other[a][1])   # 别的群看不到这个群的聊天内容
        items, _ = self.candidates('我把咖啡倒进泡面里了', scope='qq:90009:group:30003', turn='u1')
        learned = next(c for c in items if c.asset_id == a)
        self.assertIn('咖啡', learned.learned)
        self.assertIn(learned.learned, self.controller.expression.prompt(items)); self.assertIn('群友', self.controller.expression.prompt(items))


class LooserRulesTests(Base):
    """负责人 2026-11-09：分类别太严、中性的表情也能发；QQ 里聊技术也能配图；QQ 里不用什么都懂。"""
    def test_neutral_group_and_substring_grouping(self):
        from aicompanion.expression.service import feeling_group, FEELINGS
        self.assertIn('回应', FEELINGS)
        self.assertEqual(feeling_group(('收到', ), ('收到', )), '回应')
        self.assertEqual(feeling_group(('对方说好的时', '默默围观'), ('对方说好的时',)), '回应')   # 模型写的短语也能认

    def test_qq_prompt_allows_tech_stickers_and_not_knowing(self):
        from aicompanion.expression.domain import ExpressionCandidate
        items = (ExpressionCandidate('a', 'r', '疑惑歪头', handle='s1'),)
        qq = self.controller.expression.prompt(items, qq=True); desktop = self.controller.expression.prompt(items)
        self.assertIn('在 QQ 上聊技术也可以配图', qq); self.assertIn('不用什么都懂', qq); self.assertNotIn('认真讲技术', qq)
        self.assertIn('认真讲技术', desktop); self.assertNotIn('QQ 上聊技术', desktop)
        for text in (qq, desktop): self.assertIn('中性的、平常的图也能发', text)


class BridgeStickerTests(Base):
    def setUp(self):
        super().setUp()
        self.db.set_setting('bridge_owner:qq', '10001'); self.db.set_setting('bridge_groups', ['30003'])
        self.db.set_setting('bridge_memory_every', 0); self.db.set_setting('bridge_chime', 'often')
        self.service = BridgeService(self.db, self.controller, chance=lambda: 0.0)
        self.analyzed = []
        def analyzer(data, mime): self.analyzed.append(mime); return dict(GOOD)
        self.service.media.analyze_sticker = analyzer
        self.service.analyze_soon = lambda background=True: BridgeService.analyze_soon(self.service, background=False)
    def tearDown(self): self.service.close(); super().tearDown()

    def overhear(self, text, stickers=(), sender='20002'):
        return self.service.handle(dict(platform='qq', sender_id=sender, sender_name='群友', group_id='30003', text=text,
                                        called=False, stickers=list(stickers), expression_version=1, bot_account_id='90009'))

    def test_overheard_sticker_is_collected_understood_and_not_replied(self):
        self.overhear('我把咖啡倒进泡面里了')
        sticker = inline_image(png(0xff556677), 'image/png')
        result = self.overhear('[收到图片]\n[收到图片，旁听不自动识图，不能猜测图片内容。]', [sticker])
        self.assertEqual(result['reason'], 'observed')            # 只发图：看懂、记下，不插话
        self.assertEqual(len(result['stickers_known']), 1); self.assertEqual(self.model.prompts, [])
        self.assertEqual(self.analyzed, ['image/png'])             # 后台分析了一次
        line = self.db.recent_messages(1, self.character.id, channel='qq:group:30003')[-1]['content']
        self.assertIn('[发了表情包]', line); self.assertNotIn('旁听不自动识图', line)
        ring = self.db.get_setting(usage.KEY+self.character.id)
        self.assertEqual(ring[-1]['c'], '我把咖啡倒进泡面里了')   # 情境 = 发图前大家在说什么
        # 同一张图再出现：插件只传哈希，记录里直接写含义，不再分析
        self.overhear('', [{'sha256': result['stickers_known'][0]}])
        line = self.db.recent_messages(1, self.character.id, channel='qq:group:30003')[-1]['content']
        self.assertIn('[发了表情包：无语到翻白眼]', line); self.assertEqual(len(self.analyzed), 1)

    def test_private_sticker_only_may_be_left_unanswered_and_known_one_skips_vision(self):
        sticker = inline_image(png(0xff778899), 'image/png')
        self.service.handle(dict(platform='qq', sender_id='10001', sender_name='爹', group_id='', text='[收到图片]',
                                 stickers=[sticker], expression_version=1, bot_account_id='90009'))
        described = []
        self.service.media.describe = lambda images, text, purpose: described.append(images) or text
        self.model.reply = '【不说】'
        result = self.service.handle(dict(platform='qq', sender_id='10001', sender_name='爹', group_id='', text='[收到图片]',
                                          images=[sticker], image_parts=[0], stickers=[sticker], sticker_parts=[0],
                                          expression_version=1, bot_account_id='90009'))
        self.assertEqual((result['silent'], result['reason']), (True, 'sticker_seen'))
        self.assertEqual(described, [])                              # 分析过的表情包不再识图
        self.assertIn('无语到翻白眼', self.model.prompts[-1]); self.assertIn('不用接话', self.model.prompts[-1])
        self.assertNotEqual(self.db.recent_messages(1, self.character.id)[-1]['role'], 'assistant')

    def known_sticker(self, color):
        data = png(color); self.collector.ingest(data, character_id=self.character.id, origin='qq:group:x')
        self.collector.analyze_pending(lambda d, m: dict(GOOD))
        import hashlib
        return hashlib.sha256(data).hexdigest()

    def test_mixed_message_keeps_photo_recognition(self):
        # Codex 审计（7aaa065）：已知表情 + 普通照片，照片仍要交给识图；只跳过插件明确标出的那张表情
        digest = self.known_sticker(0xff313131)
        photo = inline_image(png(0xff989898), 'image/png'); sticker_image = inline_image(png(0xff313131), 'image/png')
        described = []
        self.service.media.describe = lambda images, text, purpose: described.append(list(images)) or text
        base = dict(platform='qq', sender_id='10001', sender_name='爹', group_id='', text='[收到图片]', expression_version=1, bot_account_id='90009')
        # 商城表情（不在 images 里）+ 一张照片
        self.service.handle(dict(base, images=[photo], image_parts=[0], stickers=[{'sha256': digest}], sticker_parts=[-1]))
        self.assertEqual(described[-1], [photo])
        # 图片表情 + 照片：只跳过表情那张
        self.service.handle(dict(base, images=[sticker_image, photo], image_parts=[0, 1], stickers=[{'sha256': digest}], sticker_parts=[0]))
        self.assertEqual(described[-1], [photo])
        # 旧插件没有对应关系：一张都不跳过
        self.service.handle(dict(base, images=[sticker_image, photo], stickers=[{'sha256': digest}]))
        self.assertEqual(described[-1], [sticker_image, photo])
        self.assertIn('无语到翻白眼', self.model.prompts[-1])

    def test_close_during_sticker_decoding_writes_nothing_and_stops_analysis(self):
        # Codex 审计（7aaa065，P1）：解码期间服务被关，close() 返回之后不能再收图、记用法或调用分析
        import threading
        from unittest import mock
        import aicompanion.bridge.media as media
        started, resume = threading.Event(), threading.Event(); original = media.image_bytes
        def slow(value):
            started.set(); resume.wait(5); return original(value)
        sticker = inline_image(png(0xff575757), 'image/png'); results = []
        with mock.patch.object(media, 'image_bytes', slow):
            worker = threading.Thread(target=lambda: results.append(self.overhear('', [sticker])))
            worker.start(); started.wait(5)
            self.service.close(); resume.set(); worker.join(5)
        self.assertEqual(results[0]['reason'], 'closed')
        self.assertEqual(self.collector.collected_count(), 0)
        self.assertFalse(self.db.get_setting(usage.KEY+self.character.id)); self.assertEqual(self.analyzed, [])

    def test_revoked_group_or_disabled_collection_stops_background_analysis(self):
        self.collector.ingest(png(0xff676767), character_id=self.character.id, origin='qq:group:x')
        self.db.set_setting('sticker_collect_enabled', False)
        self.assertFalse(self.service.analyze_soon(background=False) and self.analyzed)
        self.assertEqual(self.analyzed, [])
        self.db.set_setting('sticker_collect_enabled', True); self.service.close()
        self.assertFalse(self.service.analyze_soon(background=False)); self.assertEqual(self.analyzed, [])
        self.db.set_setting('bridge_groups', [])
        fresh = BridgeService(self.db, self.controller)
        try: self.assertEqual(fresh.handle(dict(platform='qq', sender_id='20002', sender_name='群友', group_id='30003', text='',
                                                 called=False, stickers=[inline_image(png(0xff777777), 'image/png')]))['reason'], 'not_allowed')
        finally: fresh.close()
        self.assertEqual(self.collector.collected_count(), 1)   # 撤掉群以后不再收

    def test_qq_situation_says_she_does_not_need_to_know_everything(self):
        self.ingest(0xff232323); self.collector.analyze_pending(lambda d, m: dict(GOOD))
        for group in ('', '30003'):
            self.service.handle(dict(platform='qq', sender_id='10001', sender_name='爹', group_id=group, text='rebase 冲突怎么办',
                                     called=True, expression_version=1, bot_account_id='90009'))
            self.assertIn('不用什么都懂', self.model.prompts[-1]); self.assertIn('在 QQ 上聊技术也可以配图', self.model.prompts[-1])

    def test_trace_marks_owner_private_qq_and_cooldown_only_for_groups(self):
        self.ingest(0xff121212); self.collector.analyze_pending(lambda d, m: dict(GOOD))
        self.model.reply = '收到'
        class Pick(Model):
            def generate_reply_stream(self_, history, prompt, cancellation=None):
                ids = re.findall(r'^(s\d+)｜', prompt, re.M); yield '收到'+(f'<sticker id="{ids[0]}"/>' if ids else '')
        self.controller.llm_client = Pick()
        for _ in range(2):
            self.service.handle(dict(platform='qq', sender_id='10001', sender_name='爹', group_id='', text='好的',
                                     expression_version=1, bot_account_id='90009'))
        traces = self.controller.expression.traces(2)
        self.assertEqual([t['channel'] for t in traces], ['qq:user', 'qq:user'])
        self.assertNotEqual(traces[-1]['reason'], 'qq_cooldown')     # 私聊不再有 60 秒冷却


if __name__ == '__main__':
    unittest.main()
