"""负责人 2026-11-09 18:05：QQ 上不要一次发一堆、不要用空格当停顿、两条之间隔一会儿、主动程度随关系变。
全部临时库、假模型。"""
from datetime import datetime
import unittest

from aicompanion.expression.segments import qq_segments, split_segments, typing_delays
from aicompanion.relationship.service import OWNER_DESKTOP, PersonRef
from aicompanion.services.proactive_service import ProactiveMessageService
from test_relationship import CID, Base, BridgeFixture

ALWAYS, NEVER = (lambda: 0.0), (lambda: 0.99)   # chance() 小于合并概率就合并


class SegmentTests(unittest.TestCase):
    def test_space_is_never_a_pause_inside_one_message(self):
        self.assertEqual(qq_segments('哟 逆天这次都用上了', chance=ALWAYS), ['哟，逆天这次都用上了'])
        self.assertEqual(qq_segments('哟 逆天这次都用上了', chance=NEVER), ['哟', '逆天这次都用上了'])
        for chance in (ALWAYS, NEVER):
            for text in qq_segments('行吧 你自己弄。我看看 这啥啊\n笑死 真的', chance=chance):
                self.assertNotRegex(text, r'[一-鿿] +[一-鿿]')

    def test_commas_kept_english_spaces_kept_trailing_period_dropped(self):
        self.assertEqual(qq_segments('这也太离谱了吧，谁写的。', chance=NEVER), ['这也太离谱了吧，谁写的'])
        self.assertEqual(qq_segments('Python 3 的 asyncio 挺好用', chance=NEVER), ['Python 3 的 asyncio 挺好用'])
        self.assertEqual(qq_segments('他说"好 的。"然后走了', chance=NEVER), ['他说"好 的。"然后走了'])

    def test_mostly_one_message_at_most_three(self):
        self.assertEqual(len(qq_segments('好的。我看看。行吧', chance=ALWAYS)), 1)        # 短的大多合成一条
        many = qq_segments('一\n二\n三\n四\n五', chance=NEVER)
        self.assertEqual(len(many), 3); self.assertEqual(many[-1], '三，四，五')
        doc = '1. 第一步\n2. 第二步'
        self.assertEqual(qq_segments(doc), [doc])                                             # 像文档的不拆

    def test_desktop_bubbles_unchanged(self):
        self.assertEqual(split_segments('在呢，刚摸完鱼。'), ['在呢 刚摸完鱼'])

    def test_typing_delays_follow_length(self):
        delays = typing_delays(['哟', '嗯', '这次是真的逆天了谁想出来的'], chance=lambda: 0.5)
        self.assertEqual(delays[0], 0.0)
        self.assertTrue(1.0 <= delays[1] < delays[2] <= 8.0)


class BridgeMessageTests(BridgeFixture):
    def test_reply_split_with_delays_and_prompt_asks_for_one_message(self):
        self.service.chance = NEVER
        self.model.replies.append('哟 逆天这次都用上了')
        result = self.ask('10001', '看我新写的')
        self.assertEqual(result['segments'], ['哟', '逆天这次都用上了'])
        self.assertEqual(len(result['segment_delays']), 2); self.assertGreaterEqual(result['segment_delays'][1], 1.0)
        prompt = self.model.prompts[-1]
        self.assertIn('大多数时候就发一条', prompt); self.assertIn('别用空格', prompt); self.assertNotIn('一句一行', prompt)
        self.service.chance = ALWAYS
        self.model.replies.append('哟 逆天这次都用上了')
        result = self.ask('10001', '再看看')
        self.assertEqual((result['reply'], result.get('segments')), ('哟，逆天这次都用上了', None))

    def test_chime_chance_follows_relationship_with_speaker(self):
        channel = 'qq:group:30003'
        for n in range(3): self.db.add_message('user', f'群友#1：第{n}句', CID, channel=channel, sender='qq:1')
        friend = self.rel.resolve(PersonRef('qq', '90000', '20002', '老朋友'), CID); self.rel.adjust(CID, friend, affinity=90, familiarity=90)
        stranger = self.rel.resolve(PersonRef('qq', '90000', '30003', '路人'), CID)
        disliked = self.rel.resolve(PersonRef('qq', '90000', '40004', '杠精'), CID); self.rel.adjust(CID, disliked, affinity=10)
        factors = [self.rel.chime_factor(CID, p) for p in (friend, stranger, disliked)]
        self.assertTrue(factors[0] > 1.3 > factors[1] > factors[2])
        self.service.chance = lambda: 0.4                                                   # often 基础 0.3
        self.assertTrue(self.service._chime_ok(channel, friend))
        self.db.set_setting('bridge_last_spoke:'+channel, 0)
        self.assertFalse(self.service._chime_ok(channel, stranger))
        self.service.chance = lambda: 0.1
        self.assertFalse(self.service._chime_ok(channel, disliked))
        self.assertTrue(self.service._chime_ok(channel, stranger))


class PaceTests(Base):
    def test_proactive_pace_follows_owner_relationship(self):
        owner = self.rel.resolve(OWNER_DESKTOP, CID)
        self.assertEqual(self.rel.proactive_pace(CID), 1.0)                                  # 初始关系 = 原来的节奏
        self.rel.adjust(CID, owner, affinity=95, familiarity=95); self.assertLess(self.rel.proactive_pace(CID), 0.8)
        self.rel.adjust(CID, owner, affinity=40, familiarity=40); self.assertGreater(self.rel.proactive_pace(CID), 1.5)

    def test_due_uses_pace(self):
        now = datetime(2026, 11, 9, 15, 0)
        for pace, expect in ((2.0, None), (1.0, True)):
            self.db.set_setting('last_proactive_attempt:'+CID, now.timestamp()-5400)          # 1.5 小时前找过他
            service = ProactiveMessageService(self.db, clock=lambda: now, pace=lambda _: pace)
            result = service.due(CID, idle_seconds=5400)
            self.assertEqual(result is not None, bool(expect), pace)
        self.assertEqual(ProactiveMessageService(self.db, pace=lambda _: 1/0).pace_of(CID), 1.0)


del Base, BridgeFixture   # 夹具本身没有用例，不在这里导出

if __name__ == '__main__':
    unittest.main()
