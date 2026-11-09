"""负责人 2026-11-09：QQ 上不要用空格当停顿、两条之间隔一会儿、发几条随情况和上下文变（不是固定上限）、
主动程度随关系变；没看到的图片别说"识别不了"，含糊接或者不回。全部临时库、假模型。"""
from datetime import datetime
import unittest

import random

from aicompanion.bridge.service import SILENCE
from aicompanion.expression.segments import chat_burst, qq_segments, split_segments, typing_delays
from aicompanion.relationship.service import OWNER_DESKTOP, PersonRef
from aicompanion.services.proactive_service import ProactiveMessageService
from test_relationship import CID, Base, BridgeFixture

ALWAYS, NEVER = (lambda: 0.99), (lambda: 0.0)   # 碎句合并：ALWAYS 一定合成一条，NEVER 一定分开发


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

    def test_count_is_not_capped_and_follows_the_moment(self):
        five = qq_segments('一下\n两下\n三下\n四下\n五下', chance=ALWAYS)
        self.assertEqual(len(five), 5)                                                       # 她换行想分几条就几条，不砍到 3
        self.assertEqual(len(qq_segments('\n'.join(f'第{n}句' for n in range(12)), chance=ALWAYS)), 8)   # 只防刷屏
        doc = '1. 第一步\n2. 第二步'
        self.assertEqual(qq_segments(doc), [doc])                                             # 像文档的不拆
        text = '行吧 你自己弄。我看看 这啥啊。算了 不管了'
        def average(burst):
            rng = random.Random(7)
            return sum(len(qq_segments(text, chance=rng.random, burst=burst)) for _ in range(300))/300
        self.assertGreater(average(0.8), average(0.2)+1.5)                                   # 聊得热时更容易连发，平时多半一条

    def test_burst_reads_context(self):
        calm = chat_burst('嗯 好的', peer_burst=1, closeness=0.3)
        self.assertGreater(chat_burst('哈哈哈哈 笑死！！', peer_burst=1, closeness=0.3), calm)  # 情绪
        self.assertEqual(chat_burst('嗯 好的', peer_burst=4, closeness=0.3), calm)             # 对方连发不再让她跟着多拆（实测越聊越刷屏）
        self.assertGreater(chat_burst('嗯 好的', peer_burst=1, closeness=0.9), calm)            # 关系亲近
        for value in (calm, chat_burst('啊啊啊！'*20, peer_burst=9, closeness=1)): self.assertTrue(0.1 <= value <= 0.9)

    def test_desktop_bubbles_unchanged(self):
        self.assertEqual(split_segments('在呢，刚摸完鱼。'), ['在呢 刚摸完鱼'])

    def test_typing_delays_follow_length_and_pace(self):
        steady = lambda: 0.5                                                                  # 不触发"想了一下再补一句"
        delays = typing_delays(['哟', '嗯', '这次是真的逆天了谁想出来的'], chance=steady)
        self.assertEqual(delays[0], 0.0)
        self.assertTrue(0.6 <= delays[1] < delays[2] <= 12.0)
        self.assertLess(typing_delays(['a', '这次是真的逆天了'], chance=steady, burst=0.9)[1],
                        typing_delays(['a', '这次是真的逆天了'], chance=steady, burst=0.1)[1])   # 聊得快打得也快
        values = iter([0.5, 0.1, 0.5])                                                         # 偶尔停久一点再补一句
        self.assertGreater(typing_delays(['a', '嗯'], chance=lambda: next(values))[1], 2.0)


class BridgeMessageTests(BridgeFixture):
    def test_reply_split_with_delays_and_prompt_leaves_count_to_context(self):
        self.service.chance = NEVER
        self.model.replies.append('哟 逆天这次都用上了')
        result = self.ask('10001', '看我新写的')
        self.assertEqual(result['segments'], ['哟', '逆天这次都用上了'])
        self.assertEqual(len(result['segment_delays']), 2); self.assertGreaterEqual(result['segment_delays'][1], 1.0)
        prompt = self.model.prompts[-1]
        self.assertIn('发几条没有固定数', prompt); self.assertIn('别用空格', prompt); self.assertNotIn('一句一行', prompt)
        self.assertNotIn('停顿空一格', prompt)                                                # 基础提示原来教她用空格停顿（实测'哟 逆天'的来源）
        self.service.chance = ALWAYS
        self.model.replies.append('哟 逆天这次都用上了')
        result = self.ask('10001', '再看看')
        self.assertEqual((result['reply'], result.get('segments')), ('哟，逆天这次都用上了', None))

    def test_unseen_picture_is_never_called_unrecognisable(self):
        # 旧插件带原因的说明进来也换成中性的 [图片]；她被要求别说看不到图，只发了图时可以不回
        self.db.set_setting('qq_vision_enabled', False)
        self.model.replies.append(SILENCE)
        result = self.ask('10001', '[收到图片]\n[图片未识别：识图已关闭，不能猜测图片内容。]')
        self.assertTrue(result['silent'])
        prompt = self.model.prompts[-1]
        self.assertIn('别说自己看不到', prompt); self.assertIn(SILENCE, prompt)
        stored = '\n'.join(r['content'] for r in self.db.recent_messages(5, CID, channel='desktop'))
        self.assertIn('[图片]', stored)
        for word in ('识图已关闭', '不能猜测', '未识别'): self.assertNotIn(word, stored)
        self.model.replies.append('哈哈哈 绝了')                                              # 有文字：照常接文字，不提看不到
        result = self.ask('10001', '今天抽卡出金了！\n[图片]')
        self.assertFalse(result['silent']); self.assertIn('别说自己看不到', self.model.prompts[-1])

    def test_rapid_messages_are_answered_once_together(self):
        """实测：对方把一句话拆成几条连发，她每条都回，8 秒刷出 7 条。0.9.3 插件带 debounce：等他停下再一起回一次。"""
        import threading, time as _time
        self.db.set_setting('bridge_debounce', 0.4)
        self.model.replies.append('就 hev 啊，你直说嘛')
        results = {}
        def send(n, text):
            results[n] = self.service.handle(dict(platform='qq', sender_id='20002', sender_name='tangle', group_id='', text=text,
                                                  called=True, expression_version=1, bot_account_id='90000', debounce=True))
        self.db.set_setting('bridge_strangers', True)
        threads = []
        for n, text in enumerate(('就是hev', '的', '意思')):
            t = threading.Thread(target=send, args=(n, text)); t.start(); threads.append(t); _time.sleep(0.1)
        for t in threads: t.join(10)
        self.assertEqual(len(self.model.prompts), 1)                                          # 只回一次
        self.assertEqual([results[n]['reason'] for n in (0, 1)], ['merged', 'merged'])
        self.assertEqual(results[2]['reply'], '就 hev 啊，你直说嘛')
        self.assertIn('连着发了 3 条', self.model.prompts[-1])
        stored = [r['content'] for r in self.db.recent_messages(5, CID, channel='qq:user:20002')]
        self.assertIn('就是hev\n的\n意思', stored)                                            # 按顺序合成一条
        # 没带 debounce 的旧插件：照旧逐条回
        self.model.replies.append('嗯')
        self.ask('20002', '在吗'); self.assertEqual(len(self.model.prompts), 2)

    def test_angry_she_may_ignore_him(self):
        guy = self.rel.resolve(PersonRef('qq', '90000', '20002', 'tangle'), CID)
        self.db.set_setting('bridge_strangers', True)
        self.model.replies.append('<mind feel="生气" toward="-3"/>你有完没完')
        self.ask('20002', '笑你蠢啊')
        self.model.replies.append(SILENCE)
        result = self.ask('20002', '哈哈哈 急了')
        self.assertIn('气头上', self.model.prompts[-1]); self.assertTrue(result['silent'])   # 气头上可以不理他
        self.assertIn('别一两句就自嘲或让步收场', self.model.prompts[-1])

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
