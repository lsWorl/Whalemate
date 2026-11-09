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

    def test_parse_sticker_analysis(self):
        self.assertEqual(parse_sticker_analysis('```json\n'+json.dumps(GOOD, ensure_ascii=False)+'\n```')['meaning'], '无语到翻白眼')
        self.assertIsNone(parse_sticker_analysis('看不清'))
        self.assertIsNone(parse_sticker_analysis('{"meaning": ""}'))
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
                                          images=[sticker], stickers=[sticker], expression_version=1, bot_account_id='90009'))
        self.assertEqual((result['silent'], result['reason']), (True, 'sticker_seen'))
        self.assertEqual(described, [])                              # 分析过的表情包不再识图
        self.assertIn('无语到翻白眼', self.model.prompts[-1]); self.assertIn('不用接话', self.model.prompts[-1])
        self.assertNotEqual(self.db.recent_messages(1, self.character.id)[-1]['role'], 'assistant')

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
