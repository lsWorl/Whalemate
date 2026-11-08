"""T14 接手修正（Claude，2026-11-08）：候选召回原因、亲昵图的对象、单轮表达决策观测 A～F。
只用合成图和临时库；模型只替换流式字节，不联网。"""
import re, unittest
from dataclasses import replace
import test_expression_catalog as fixtures
from aicompanion.controllers.chat_controller import ChatController
from aicompanion.domain.cancellation import CancellationToken
from aicompanion.domain.character import DEFAULT_CHARACTER
from aicompanion.expression.domain import ExpressionContext, RelationshipSnapshot, owner_relationship
from aicompanion.expression.service import ExpressionService
from aicompanion.services.memory_service import MemoryService
png = fixtures.png


class Scripted:
    """按脚本回复；reply 可以引用本轮第一个候选编号 {h}。"""
    def __init__(self, reply=''): self.reply = reply; self.prompts = []; self.histories = []
    def generate_reply_stream(self, history, prompt, cancellation):
        self.prompts.append(prompt); self.histories.append(list(history))
        handles = re.findall(r'^(s\d+)｜', prompt, re.M)
        yield self.reply.format(h=handles[0] if handles else 'none')


class DecisionTests(unittest.TestCase):
    tearDown = fixtures.CatalogTests.tearDown

    def setUp(self):
        fixtures.CatalogTests.setUp(self)
        self.character = replace(DEFAULT_CHARACTER, id='deepseek-whale', name='鲸娘'); self.db.add_character(self.character)
        self.model = Scripted(); self.controller = ChatController(self.db, MemoryService(self.db), self.model)
        self.now = 1000.0; self.controller.expression = ExpressionService(self.db, lambda: self.now)
        self.controller.character = self.character; self.events = []

    def add(self, name, **meta):
        self.colors = getattr(self, 'colors', 0)+1   # 每张颜色不同：图库按内容哈希去重
        path = self.library/'happy'/f'{name}.png'; path.write_bytes(png(0xff000000+self.colors*4099))
        return self.cat.register(path, self.library, dict(self.meta, category=name, **meta))[0]

    def turn(self, reply, turn='g1', relationship=None, supports=True, query='今天好开心'):
        self.model.reply = reply
        ctx = ExpressionContext(turn, self.character.id, 'desktop:'+self.character.id, relationship=relationship or owner_relationship())
        text = ''.join(self.controller.generate(turn, self.character, CancellationToken(), query, expression_context=ctx,
                                                on_expression_event=self.events.append if supports else None))
        return text, self.controller.expression.traces(1)[-1]

    # ---- 候选召回 ----
    def test_owner_on_desktop_gets_affectionate_images_strangers_and_groups_do_not(self):
        self.add('sajiao', semantic='想撒娇求关注时', intents=['撒娇'], audiences=['owner_family'])
        self.add('smile', semantic='开心到笑出声', intents=['分享开心'])
        service = ExpressionService(self.db)
        owner = ExpressionContext('a', self.character.id, 'desktop:x', relationship=owner_relationship())
        stranger = ExpressionContext('b', self.character.id, 'qq:1:user:2', scene='qq', relationship=RelationshipSnapshot())
        group = ExpressionContext('c', self.character.id, 'qq:1:group:3', scene='qq', relationship=owner_relationship())
        meanings = lambda ctx: sorted(c.summary for c in service.prepare(ctx, self.character))
        self.assertEqual(meanings(owner), ['开心到笑出声', '想撒娇求关注时'])
        self.assertEqual(meanings(stranger), ['开心到笑出声'])
        self.assertEqual(meanings(group), ['开心到笑出声'])          # 群里即使是爹也不发亲昵图
        _, report = service.prepare_report(stranger, self.character)
        self.assertEqual(report['filtered'].get('audience'), 1)

    def test_report_explains_empty_candidates(self):
        service = ExpressionService(self.db)
        ctx = ExpressionContext('a', self.character.id, 'desktop:x')
        self.cat.scan()                                              # 文件夹里的图没有许可 → 待审核
        candidates, report = service.prepare_report(ctx, self.character)
        self.assertEqual((candidates, report['reason'], report['filtered']), ((), 'no_eligible_assets', {'pending_review': 1}))
        self.assertEqual(service.prepare_report(replace(ctx, supports_images=False), self.character)[1]['reason'], 'client_no_image_support')
        self.db.set_setting('qq_memes_enabled', False)
        self.assertEqual(service.prepare_report(ctx, self.character)[1]['reason'], 'memes_disabled')

    def test_candidates_cover_varied_feelings_not_only_literal_matches(self):
        for n, (meaning, intent, emotion) in enumerate([('开心庆祝', '庆祝', '开心'), ('开心转圈', '庆祝', '开心'), ('开心蹦跳', '庆祝', '开心'),
                                                       ('得意叉腰', '炫耀', '得意'), ('心虚望天', '心虚', '尴尬'), ('困到睡着', '晚安', '困'),
                                                       ('无语凝噎', '吐槽', '无语')]):
            self.add(f'v{n}', semantic=meaning, intents=[intent], emotions=[emotion])
        found = ExpressionService(self.db).prepare(ExpressionContext('a', self.character.id, 'desktop:x'), self.character, '今天开心庆祝')
        intents = {c.intents[0] for c in found}
        self.assertTrue({'炫耀', '心虚', '晚安', '吐槽'} <= intents)    # 她自己想表达的情绪也有图可选
        self.assertLessEqual(sum(1 for c in found if c.intents[0] == '庆祝'), 3)   # 最相关的最多 3 张，其余名额按情绪铺开
        self.assertEqual([c.handle for c in found], [f's{n}' for n in range(1, len(found)+1)])

    def test_each_feeling_group_gets_an_image_even_without_word_overlap(self):
        # 第一轮真实验收：词面相关度总是 0，候选每轮都是同一批；"都凌晨一点了，你困不困"时手边没有困的图
        for n in range(8):
            self.add(f'h{n}', semantic=f'开心{n}', intents=['分享开心'], emotions=['开心'])
        self.add('sleep', semantic='困到闭眼却还在机械工作时', intents=['表达疲惫'], emotions=['疲惫'])
        self.add('angry', semantic='被惹恼时用头槌抗议', intents=['抗议'], emotions=['生气'])
        self.add('smug', semantic='得意地反击', intents=['炫耀'], emotions=['得意'])
        service = ExpressionService(self.db)
        found = service.prepare(ExpressionContext('a', self.character.id, 'desktop:x'), self.character, '都凌晨一点了，你困不困')
        meanings = [c.summary for c in found]
        self.assertEqual(meanings[0], '困到闭眼却还在机械工作时')              # 对话里的情绪线索排在最前
        self.assertTrue({'被惹恼时用头槌抗议', '得意地反击'} <= set(meanings))  # 其他情绪也各有一张

    def test_same_feeling_rotates_between_turns(self):
        for n in range(6):
            self.add(f'h{n}', semantic=f'开心{n}', intents=['分享开心'], emotions=['开心'])
        service = ExpressionService(self.db)
        firsts = {service.prepare(ExpressionContext(f'turn{k}', self.character.id, 'desktop:x'), self.character, '嗯')[0].summary
                  for k in range(12)}
        self.assertGreater(len(firsts), 1)

    def test_prompt_presents_meaning_and_use_and_does_not_demand_a_text_only_marker(self):
        self.add('smile', semantic='开心到笑出声', intents=['分享开心'], emotions=['开心'])
        self.turn('好耶')
        prompt = self.model.prompts[-1]
        self.assertIn('s1｜开心到笑出声｜情绪：开心｜用来：分享开心', prompt)
        self.assertIn('表达你自己此刻的情绪', prompt); self.assertIn('只发一张图当作整条回复', prompt)
        self.assertNotIn('text_only', prompt)                         # 不再要求每轮先写"纯文字"标记
        self.assertNotIn('st_', prompt)

    # ---- 观测：A～F ----
    def test_stage_a_no_candidates(self):
        _, trace = self.turn('好耶')
        self.assertEqual((trace['stage'], trace['reason'], trace['candidates']), ('A_no_candidates', 'no_eligible_assets', 0))

    def test_stage_b_model_chose_text(self):
        self.add('smile', semantic='开心到笑出声')
        _, trace = self.turn('好耶')
        self.assertEqual((trace['stage'], trace['candidates'], trace['injected'], trace['markers']), ('B_model_chose_text', 1, True, 0))

    def test_stage_c_marker_unparsed(self):
        self.add('smile', semantic='开心到笑出声')
        _, trace = self.turn('好耶<sticker id="{h}"')
        self.assertEqual(trace['stage'], 'C_marker_unparsed')

    def test_stage_d_asset_invalid(self):
        self.add('smile', semantic='开心到笑出声')
        text, trace = self.turn('好耶<sticker id="s9"/>')
        self.assertEqual((text, trace['stage']), ('好耶', 'D_asset_invalid'))

    def test_stage_e_result_not_delivered_when_no_receiver(self):
        self.add('smile', semantic='开心到笑出声')
        _, trace = self.turn('好耶<sticker id="{h}"/>', supports=False)
        self.assertEqual((trace['stage'], trace['reason']), ('A_no_candidates', 'client_no_image_support'))

    def test_stage_pending_then_displayed_or_failed(self):
        self.add('smile', semantic='开心到笑出声')
        text, trace = self.turn('<sticker id="{h}"/>哈哈')
        self.assertEqual((text, trace['stage'], trace['placement'], trace['meaning']), ('哈哈', 'E_pending', 'before_text', '开心到笑出声'))
        self.assertTrue(trace['parsed'] and trace['validated'] and trace['result'])
        delivery = self.events[-1].result.attachments[0].delivery_id
        self.controller.expression.receipt(delivery, 'displayed')
        self.assertEqual(self.controller.expression.traces(1)[-1]['stage'], 'OK_displayed')
        self.now = 10**9
        self.add('other', semantic='无语')
        _, trace = self.turn('<sticker id="{h}"/>', turn='g2')
        self.controller.expression.receipt(self.events[-1].result.attachments[0].delivery_id, 'failed')
        self.assertEqual(self.controller.expression.traces(1)[-1]['stage'], 'F_display_failed')

    def test_blank_lines_after_leading_sticker_are_trimmed(self):
        self.add('smile', semantic='开心到笑出声')
        text, _ = self.turn('<sticker id="{h}"/>\n\n哈哈\n\n\n\n真的')
        self.assertTrue(text.startswith('哈哈'))
        self.assertEqual(self.events[-1].result.text, '哈哈\n\n真的')
        text, _ = self.turn('哈哈\n\n<sticker id="{h}"/>\n\n', turn='g9')
        self.assertEqual(text, '哈哈')                                # 图在后时结尾不留空行

    def test_trace_has_no_chat_text_or_paths(self):
        self.add('smile', semantic='开心到笑出声')
        _, trace = self.turn('秘密内容<sticker id="{h}"/>', query='用户的私事')
        dumped = str(self.controller.expression.traces())
        for leak in ('秘密内容', '用户的私事', str(self.library), 'base64'):
            self.assertNotIn(leak, dumped)
        self.assertEqual(ExpressionService.describe(trace).count('开心到笑出声'), 1)

    def test_trace_ring_is_bounded(self):
        service = ExpressionService(self.db); ctx = ExpressionContext('x', self.character.id, 'desktop:x')
        for n in range(service.TRACE_LIMIT+5):
            service.record_trace(f't{n}', ctx, dict(candidates=0))
        self.assertEqual(len(self.db.get_setting(service.TRACE_KEY)), service.TRACE_LIMIT)

    # ---- 历史：她自己发过的图在她的台词里 ----
    def test_history_shows_her_own_sent_stickers_in_place(self):
        self.add('smile', semantic='开心到笑出声')
        self.turn('好耶<sticker id="{h}"/>')
        self.controller.expression.receipt(self.events[-1].result.attachments[0].delivery_id, 'displayed')
        self.db.add_message('user', '再来', self.character.id)
        history, _ = self.controller.context.build(self.character)
        self.assertEqual(history[-2].content, '好耶<sticker sent="开心到笑出声"/>')
        self.assertEqual(self.db.recent_messages(5, self.character.id)[-2]['content'], '好耶')   # 数据库原文不变
        # 模型照抄历史写法、含义又正好是本轮候选：当作选了这张；否则静默隐藏
        self.now += 400
        text, trace = self.turn('<sticker sent="开心到笑出声"/>再来一张', turn='g3')
        self.assertEqual((text, trace['stage']), ('再来一张', 'E_pending'))

    def test_undelivered_sticker_is_not_shown_in_history(self):
        self.add('smile', semantic='开心到笑出声')
        self.turn('好耶<sticker id="{h}"/>')                              # 没有显示回执（pending）
        history, _ = self.controller.context.build(self.character)
        self.assertEqual(history[-1].content, '好耶')


if __name__ == '__main__':
    unittest.main()
