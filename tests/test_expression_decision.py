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
        self.path.unlink()   # 夹具里那张图现在默认可用（自己的图），这里只要各测试自己加的图

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
        (self.library/'happy'/'b.png').write_bytes(png(0xff445566))
        (self.library/'manifest.json').write_text('{"asset_defaults": {"license": "unknown"}}', encoding='utf-8')
        self.cat.scan()                                              # 清单写明许可不明 → 待确认
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

    def test_img_tag_variants_never_reach_yield_or_storage(self):
        # Codex 审计：经原 ChatController，yield 的正文（也是 TTS 的输入）和存库正文都不能带 <img 变体
        self.add('smile', semantic='开心到笑出声')
        long_src = '你好<img src=' + 'x'*1100 + '>晚安'          # Codex 审计（e28c1ac）的复现：超长 src
        for n, reply in enumerate(('你好<img\tsrc="合成地址">晚安', '你好<img\nsrc="合成地址">晚安', '你好<img\r\nsrc="合成地址">晚安', long_src)):
            text, _ = self.turn(reply, turn=f'img{n}')
            self.assertEqual(text, '你好晚安')
            self.assertEqual(self.db.recent_messages(1, self.character.id)[0]['content'], '你好晚安')
            final = [e for e in self.events if getattr(e, 'kind', '') == 'final'][-1]
            self.assertEqual(final.result.text, '你好晚安'); self.assertEqual(final.result.status, 'complete')

    def test_trace_has_no_chat_text_or_paths(self):
        self.add('smile', semantic='开心到笑出声')
        _, trace = self.turn('秘密内容<sticker id="{h}"/>', query='用户的私事')
        dumped = str(self.controller.expression.traces())
        for leak in ('秘密内容', '用户的私事', str(self.library), 'base64'):
            self.assertNotIn(leak, dumped)
        self.assertEqual(ExpressionService.describe(trace).count('开心到笑出声'), 1)

    def test_invalid_id_text_never_reaches_trace_or_debug_output(self):
        # Codex 审计 P2：模型可能把聊天内容写进无效编号；观测和控制台只留形态
        import io, contextlib
        self.add('smile', semantic='开心到笑出声'); self.db.set_setting('expression_debug', True)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            _, trace = self.turn('好<sticker id="合成住院细节"/>')
        self.assertEqual((trace['stage'], trace['rejected']), ('D_asset_invalid', ['free_text']))
        self.assertNotIn('合成住院细节', str(self.db.get_setting('expression_traces')) + err.getvalue())
        self.assertIn('无效编号形态 free_text', err.getvalue())

    def test_early_display_receipt_is_not_overwritten_by_final_trace(self):
        # Codex 审计 P2：图在前时界面在生成结束前就回执 displayed，最终观测不能写回 pending
        self.add('smile', semantic='开心到笑出声')
        def receipt_now(event):
            self.events.append(event)
            if event.kind == 'attachment_ready': self.controller.expression.receipt(event.attachment.delivery_id, 'displayed')
        self.model.reply = '<sticker id="{h}"/>哈哈'
        ctx = ExpressionContext('early', self.character.id, 'desktop:'+self.character.id, early_before=True, relationship=owner_relationship())
        ''.join(self.controller.generate('early', self.character, CancellationToken(), '好耶', expression_context=ctx, on_expression_event=receipt_now))
        self.assertTrue(any(e.kind == 'attachment_ready' for e in self.events))
        self.assertEqual(self.controller.expression.traces(1)[-1]['stage'], 'OK_displayed')

    def _pending_attachment(self):
        self.add('smile', semantic='开心到笑出声')
        ctx = ExpressionContext('race', self.character.id, 'desktop:'+self.character.id, relationship=owner_relationship())
        self.model.reply = '<sticker id="{h}"/>哈哈'
        ''.join(self.controller.generate('race', self.character, CancellationToken(), '好耶', expression_context=ctx,
                                         on_expression_event=self.events.append))
        return ctx, self.events[-1].result.attachments[0]

    def test_receipt_between_delivery_update_and_trace_update_is_not_lost(self):
        # Codex 审计 P2：回执已更新投递、但"推进观测"那一步还没做时写最终观测——必须读到 displayed
        service = self.controller.expression; ctx, attachment = self._pending_attachment()
        original = service._trace_delivery; service._trace_delivery = lambda *a: None
        service.receipt(attachment.delivery_id, 'displayed'); service._trace_delivery = original
        entry = service.record_trace('race2', ctx, dict(candidates=1), (), 1, (attachment.asset_id, 'before_text'), attachment, 'complete', True)
        self.assertEqual(entry['stage'], 'OK_displayed')

    def test_receipt_arriving_while_trace_is_written_still_ends_displayed(self):
        # 回执在写最终观测的过程中到达：它会等这次写入提交，再把这条记录推进到 displayed
        import threading
        from aicompanion.storage.database import Database
        service = self.controller.expression; ctx, attachment = self._pending_attachment()
        with self.db.transaction() as c: c.execute("UPDATE expression_deliveries SET status='pending' WHERE delivery_id=?", (attachment.delivery_id,))
        real = Database.setting_in; started = []
        def hook(connection, key, default=None):
            if key == service.TRACE_KEY and not started:
                started.append(threading.Thread(target=service.receipt, args=(attachment.delivery_id, 'displayed'))); started[0].start()
                started[0].join(0.3)   # 回执被写锁挡住：读投递状态和写观测之间插不进来
                started.append(started[0].is_alive())
            return real(connection, key, default)
        Database.setting_in = staticmethod(hook)
        try:
            service.record_trace('race3', ctx, dict(candidates=1), (), 1, (attachment.asset_id, 'before_text'), attachment, 'complete', True)
        finally:
            Database.setting_in = staticmethod(real)
        started[0].join(5); self.assertTrue(started[1])
        self.assertEqual(self.controller.expression.delivery(attachment.delivery_id)['status'], 'displayed')
        last = [e for e in service.traces(10) if e.get('delivery') == attachment.delivery_id][-1]
        self.assertEqual(last['stage'], 'OK_displayed')

    def test_sticker_from_visible_history_is_offered_again(self):
        # Codex 连续对话审计：她照抄历史里发过的图，图却不在本轮候选 → history_echo 被丢；现在合格的历史图留在候选里
        for n in range(14):
            self.add(f'h{n}', semantic=f'开心{n}号', intents=['分享开心'], emotions=['开心'])
        self.add('special', semantic='抱着红薯傻乐', intents=['分享日常'], emotions=['开心'])
        service = self.controller.expression
        special = next(r['asset_id'] for r in self.cat.rows() if r['metadata'].get('semantic') == '抱着红薯傻乐')
        # 先让她真的发过一次这张图
        self.db.set_setting('qq_meme_cooldown', 0)
        with self.db.transaction() as c:
            c.execute("""INSERT INTO expression_deliveries(delivery_id,turn_id,character_id,scope,channel,asset_id,revision,placement,alt_text,status,created_at,updated_at)
                         SELECT 'd'||hex(randomblob(15)),'old','deepseek-whale','desktop:deepseek-whale','desktop',asset_id,revision,'after_text','抱着红薯傻乐','displayed',?,?
                         FROM expression_assets WHERE asset_id=?""", (self.now-3600, self.now-3600, special))
        found = [c.asset_id for c in service.prepare(ExpressionContext('next', self.character.id, 'desktop:'+self.character.id,
                                                                          relationship=owner_relationship()), self.character, '嗯嗯')]
        self.assertIn(special, found)
        self.now += 10
        _, trace = self.turn('哈哈<sticker sent="抱着红薯傻乐"/>', turn='echo')
        self.assertEqual(trace['stage'], 'E_pending'); self.assertIn('history_echo_matched', trace['diagnostics'])

    def test_comforting_images_come_first_when_the_other_person_is_sad(self):
        self.add('cry', semantic='战利品不满意抱着哭', intents=['委屈', '求安慰'], emotions=['哭'])
        self.add('hug', semantic='含泪微笑陪着对方', intents=['慰问', '陪伴'], emotions=['安慰'])
        found = ExpressionService(self.db).prepare(ExpressionContext('a', self.character.id, 'desktop:x', relationship=owner_relationship()),
                                                   self.character, '唉，期待了好久的演唱会票没抢到，好难受')
        self.assertEqual(found[0].summary, '含泪微笑陪着对方')
        found = ExpressionService(self.db).prepare(ExpressionContext('b', self.character.id, 'desktop:x', relationship=owner_relationship()),
                                                   self.character, '肯定是你，冤枉谁也不冤枉你')
        self.assertEqual(found[0].summary, '战利品不满意抱着哭')

    def test_feeling_cues_follow_context_negation_and_turns(self):
        from aicompanion.expression.service import feeling_cues
        # 同一句"陪我待会就好"：前面在说难过 → 安慰排前；前面说的是好消息 → 安慰不靠前也不掉，祝贺仍在
        sad = feeling_cues('先别讲道理，陪我待会就好', ['今天被骂了，好难受'])
        self.assertEqual(sad.get('安慰'), 6)
        plain = feeling_cues('嗯嗯', ['今天被骂了，好难受'])
        self.assertEqual(plain.get('安慰'), 4)                        # 只靠前文：权重低一点但仍有
        happy = feeling_cues('嗯嗯', ['我面试过了！'])
        self.assertIn('祝贺', happy); self.assertNotIn('安慰', happy)
        # 否定：不难过 / 没失败 不算
        self.assertNotIn('安慰', feeling_cues('我一点也不难过，也没失败'))
        # 转折：后半句算满分，前半句打对折
        turned = feeling_cues('本来挺难受的，不过面试过了！')
        self.assertEqual((turned.get('安慰'), turned.get('祝贺')), (3, 6))

    def test_current_need_is_offered_before_old_history_sticker(self):
        # Codex 实聊：陪对方时候选里只有旧的睡觉图；现在先补线索指向的安慰图，再考虑旧图
        for n in range(12):
            self.add(f'h{n}', semantic=f'开心{n}号', intents=['分享开心'], emotions=['开心'])
        self.add('sleep', semantic='睡眠符号表示睡啦', intents=['晚安'], emotions=['疲惫'])
        self.add('hug', semantic='含泪微笑陪着对方', intents=['慰问', '陪伴'], emotions=['安慰'])
        sleep = next(r['asset_id'] for r in self.cat.rows() if r['metadata'].get('semantic') == '睡眠符号表示睡啦')
        with self.db.transaction() as c:
            c.execute("""INSERT INTO expression_deliveries(delivery_id,turn_id,character_id,scope,channel,asset_id,revision,placement,alt_text,status,created_at,updated_at)
                         SELECT 'e'||hex(randomblob(15)),'old','deepseek-whale','desktop:deepseek-whale','desktop',asset_id,revision,'after_text','睡眠符号表示睡啦','displayed',?,?
                         FROM expression_assets WHERE asset_id=?""", (self.now-3600, self.now-3600, sleep))
        from aicompanion.llm.base import ChatTurn
        found = [c.summary for c in self.controller.expression.prepare(
            ExpressionContext('n', self.character.id, 'desktop:'+self.character.id, relationship=owner_relationship()), self.character,
            '先别讲道理，陪我待会就好', [ChatTurn('user', '今天被骂了，好难受')])]
        self.assertIn('含泪微笑陪着对方', found)
        self.assertLess(found.index('含泪微笑陪着对方'), found.index('睡眠符号表示睡啦') if '睡眠符号表示睡啦' in found else 99)

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
