"""T20 当场查没见过的梗：她自己决定查不查、用不用；查到的记进词库，下次不再搜。临时库、假模型、假搜索，不联网。"""
import unittest

from aicompanion.community.lexicon import Lexicon
from aicompanion.community.lookup import Lookup, relevant, tidy
from aicompanion.community.search import Hit
from aicompanion.expression.stream import ExpressionStream
from test_human_outsiders import Search
from test_relationship import BridgeFixture

HIT = Hit('依托答辩', 'https://example.org/yt', '依托答辩是"一坨大便"的谐音，用来吐槽东西很烂，出自 B 站游戏区。', '某百科')
LEARNED = '{"meaning":"一坨大便的谐音，吐槽东西很烂","origin":"B 站游戏区","tone":"贬","circle":"游戏","usage":"吐槽","source":"1","change":"same"}'
GROUP = 'qq:group:30003'


class MarkerTests(unittest.TestCase):
    def test_lookup_marker_is_parsed_and_never_shown(self):
        stream = ExpressionStream([], {}, {}, {}, None)
        shown = stream.feed('<mind lookup="“依托答辩”"/>')+stream.finish()
        self.assertEqual(shown.strip(), ''); self.assertEqual(stream.mind['lookup'], '依托答辩')
        long = ExpressionStream([], {}, {}, {}, None); long.feed('<mind lookup="'+'梗'*40+'"/>嗯'); long.finish()
        self.assertEqual(len(long.mind['lookup']), 16)

    def test_only_results_that_mention_the_term_count(self):
        other = Hit('答辩', 'https://example.org/x', '毕业答辩流程', '百科')
        self.assertEqual(relevant('依托答辩', [other, HIT]), [HIT]); self.assertEqual(relevant('依托 答辩', [HIT]), [HIT])
        self.assertEqual(tidy(' “绷不住” '), '绷不住')


class LookupTests(BridgeFixture):
    def setUp(self):
        super().setUp(); self.db.set_setting('community_web', True)
        self.web = Search([HIT]); self.service.web_search = self.web; self.service.lookup_background = False

    def say(self, text, *replies, sender='20002', group='30003', name='tangle'):
        self.model.replies.extend(replies)
        return self.service.handle(dict(platform='qq', sender_id=sender, sender_name=name, group_id=group, text=text, called=True,
                                        expression_version=1, bot_account_id='90000'))

    def test_new_meme_is_looked_up_used_and_learned_then_not_searched_again(self):
        """P0 闭环：第一次见到的梗 → 她决定查 → 结果进当前轮 → 记进词库 → 下次直接带出来。"""
        result = self.say('这游戏真是依托答辩', '<mind lookup="依托答辩"/>', LEARNED, '确实 纯纯答辩')
        self.assertIn('【遇到不懂的梗】', self.model.prompts[0]); self.assertIn('值得弄清楚', self.model.prompts[0])
        again = self.model.prompts[-1]
        self.assertIn('你顺手搜了一下"依托答辩"', again); self.assertIn('用不用由你定', again); self.assertIn('对不上，就当没搜到', again)
        self.assertNotIn('【遇到不懂的梗】', again); self.assertIn('别再写 lookup', again)          # 一轮只查一次
        self.assertFalse(result['silent']); self.assertNotIn('lookup', str(result))
        said = [r['content'] for r in self.db.recent_messages(10, self.character.id, channel=GROUP) if r['role'] == 'assistant']
        self.assertEqual(len(said), 1)                                                            # 第一遍（只有标记）不算她说过的话
        sense = Lexicon(self.db).senses('依托答辩')[0]
        self.assertEqual((sense['origin'], sense['scope'], sense['source_url']), ('web', '', 'https://example.org/yt'))
        self.assertEqual(Lexicon(self.db).sightings('依托答辩')[0]['channel'], GROUP)
        self.say('又是依托答辩', '笑死')
        self.assertIn('一坨大便的谐音', self.model.prompts[-1]); self.assertEqual(len(self.web.queries), 1)   # 词库里有了，不再搜

    def test_she_decides_not_to_look_up_and_nothing_is_searched(self):
        result = self.say('这游戏真是依托答辩', '啊？')
        self.assertEqual(result['reply'], '啊？'); self.assertEqual(self.web.queries, []); self.assertEqual(len(self.model.prompts), 1)

    def test_nothing_relevant_found_she_is_told_not_to_make_it_up(self):
        self.web.hits = [Hit('答辩', 'https://example.org/x', '毕业答辩流程', '百科')]             # 沾边但没提这个词
        self.say('这游戏真是依托答辩', '<mind lookup="依托答辩"/>', '啥意思')
        self.assertIn('没搜到什么靠谱的', self.model.prompts[-1]); self.assertIn('别编', self.model.prompts[-1])
        self.assertEqual(len(self.web.queries), 2)                                                # 换个问法再试一次
        self.assertIsNone(Lexicon(self.db).info('依托答辩'))                                      # 没结果不记

    def test_names_numbers_and_switches_are_respected(self):
        self.say('tangle是什么梗', '<mind lookup="tangle"/>', '不知道')                           # 对方昵称
        self.assertIn('像是人名、号码', self.model.prompts[-1]); self.assertEqual(self.web.queries, [])
        self.say('13812345678啥意思', '<mind lookup="13812345678"/>', '不知道'); self.assertEqual(self.web.queries, [])
        self.db.set_setting('community_web', False)
        result = self.say('这游戏真是依托答辩', '啊？')
        self.assertNotIn('【遇到不懂的梗】', self.model.prompts[-1]); self.assertEqual(result['reply'], '啊？')   # 联网关了：不提查梗
        self.db.set_setting('community_web', True); self.db.set_setting('community_learning', False)
        self.say('这游戏真是依托答辩', '<mind lookup="依托答辩"/>', '懂了')
        self.assertEqual(len(self.web.queries), 1); self.assertIsNone(Lexicon(self.db).info('依托答辩'))       # 学梗关了：查了能用，但不记

    def test_thin_summary_reads_the_page_and_keeps_the_part_about_the_term(self):
        self.web.hits = [Hit('依托答辩', 'https://example.org/yt', '依托答辩 - 词条', '某百科')]
        self.web.page = '导航 首页 ' + 'x'*200 + ' 依托答辩，网络流行语，是"一坨大便"的谐音。' + 'y'*900
        self.say('这游戏真是依托答辩', '<mind lookup="依托答辩"/>', LEARNED, '懂了')
        self.assertEqual(self.web.fetched, ['https://example.org/yt']); self.assertIn('一坨大便', self.model.prompts[-1])

    def test_owner_private_chat_can_look_up_too(self):
        result = self.say('这游戏真是依托答辩', '<mind lookup="依托答辩"/>', LEARNED, '确实', sender='10001', group='', name='爹')
        self.assertEqual(result['reply'], '确实'); self.assertEqual(len(self.web.queries), 1)

    def test_search_failure_does_not_break_the_reply(self):
        self.web.search = lambda *a, **k: (_ for _ in ()).throw(OSError('断网'))
        result = self.say('这游戏真是依托答辩', '<mind lookup="依托答辩"/>', '没听过')
        self.assertEqual(result['reply'], '没听过'); self.assertIn('没搜到什么靠谱的', self.model.prompts[-1])


if __name__ == '__main__':
    unittest.main()
