"""T21 预置词库：数据本身的质量、安装规则、和检索 / 查梗 / 后台学习怎么配合。临时库，不联网。"""
import re
import unittest

from aicompanion.community import seed
from aicompanion.community.learner import CommunityLearner
from aicompanion.community.lexicon import TONES, Lexicon
from aicompanion.community.seed_data import SEED, SEED_VERSION
from aicompanion.community.search import Hit
from test_community_learning import Client
from test_human_outsiders import Search
from test_relationship import Base, BridgeFixture

GROUP = 'qq:group:30003'


class DataTests(unittest.TestCase):
    def test_entries_are_well_formed(self):
        terms = [t for t, *_ in seed.entries()]
        self.assertGreaterEqual(len(terms), 480); self.assertEqual(len(terms), len(set(terms)), '有重复的词')
        for names, circle, tone, meaning, usage, version in SEED:
            self.assertIn(circle, ('B站', '游戏', '二次元', '抽象', '网络'), names)
            self.assertIn(tone, TONES, names); self.assertTrue(1 <= version <= SEED_VERSION, names)
            self.assertTrue(4 <= len(meaning) <= 160, names); self.assertLessEqual(len(usage), 120, names)
            self.assertNotRegex(meaning+usage, r'https?://', names)                              # 不带链接
            for term in names.split('|'): self.assertTrue(2 <= len(term) <= 24, term)
        circles = {c: sum(1 for _t, circle, *_ in seed.entries() if circle == c) for c in ('B站', '游戏', '二次元', '抽象')}
        for circle, count in circles.items(): self.assertGreaterEqual(count, 40, circle)       # 四个圈子都有一定数量

    def test_second_batch_covers_mihoyo_and_abstract_memes(self):
        """负责人 2026-11-10 18:44：二游、抽象梗多加，比如米哈游相关的。"""
        new = {t: circle for t, circle, *_ in seed.entries(1)}
        self.assertGreaterEqual(len(new), 150)
        for term in ('原来你也玩原神', '原神怎么你了', '不如原神', '应急食品', '米孝子', '圣遗物', '歪七七', '大保底', '崩铁', '绝区零', '粥游',
                     '糖完了', '🍬完了', '小糖人', '难绷', '典孝急', '你先别急', '这位更是重量级', '汗流浃背了吧', '已读乱回', 'fw', '哈气', '曼波'):
            self.assertIn(term, new, term)

    def test_everyday_words_are_not_seeded(self):
        """和日常用语大面积重合的词不收，免得平常聊天也被当成梗。"""
        terms = {t for t, *_ in seed.entries()}
        for word in ('确实', '启动', '建议', '毕业', '钓鱼', '离谱', '包的', '更新', '直播', '谢谢', '节奏', '流水', '文案', '这一块', '小学生', '旅行者'):
            self.assertNotIn(word, terms)


class InstallTests(Base):
    def test_install_once_with_source_marked_as_seed(self):
        added = seed.install(self.db)
        self.assertEqual(added, len(list(seed.entries()))); self.assertEqual(seed.install(self.db), 0)   # 重复调用不重复装
        sense = Lexicon(self.db).senses('依托答辩')[0]
        self.assertEqual((sense['origin'], sense['scope'], sense['source_name'], sense['source_url']), ('seed', '', '预置词库', ''))
        self.assertLess(sense['confidence'], 0.75)                                               # 比联网查到的低
        self.assertEqual(Lexicon(self.db).info('蚌埠住了')['circle'], '抽象')                    # 别的写法也各自能匹配
        self.assertEqual(self.db.get_setting(seed.SETTING), SEED_VERSION)

    def test_upgrade_from_first_batch_only_adds_the_new_terms(self):
        lexicon = Lexicon(self.db)
        for term, circle, tone, meaning, usage in seed.entries():
            if term in {t for t, *_ in seed.entries(1)}: continue                               # 模拟只装过第 1 版的库
            lexicon.learn(term, meaning, origin='seed', usage=usage, tone=tone, circle=circle, source_name='预置词库', confidence=seed.CONFIDENCE)
        self.db.set_setting(seed.SETTING, 1); lexicon.delete('依托答辩')                        # 用户删过一个第 1 版的词
        self.assertEqual(seed.install(self.db), len(list(seed.entries(1))))
        self.assertIsNone(lexicon.info('依托答辩')); self.assertTrue(lexicon.senses('原来你也玩原神'))
        self.assertEqual(self.db.get_setting(seed.SETTING), SEED_VERSION)

    def test_short_latin_terms_only_match_as_whole_words(self):
        seed.install(self.db); lexicon = Lexicon(self.db)
        self.assertEqual(lexicon.match('please stop, I want to open the printf docs'), [])        # op / nt 不在英文单词里乱认
        self.assertEqual([h['term'] for h in lexicon.match('你就是个fw')], ['fw'])
        self.assertEqual([h['term'] for h in lexicon.match('fw一个 nt')], ['fw', 'nt'])

    def test_existing_general_meaning_is_kept_and_deleted_terms_stay_deleted(self):
        lexicon = Lexicon(self.db)
        lexicon.learn('哈基米', '联网查到的意思', origin='web', source_name='某百科', source_url='https://example.org/h', confidence=0.75)
        seed.install(self.db)
        self.assertEqual([s['meaning'] for s in lexicon.senses('哈基米')], ['联网查到的意思'])   # 已有通用义：不覆盖
        lexicon.delete('典中典'); self.assertEqual(seed.install(self.db), 0)
        self.assertIsNone(lexicon.info('典中典'))                                                # 用户删掉的不会又冒出来

    def test_group_only_meaning_does_not_block_the_general_seed(self):
        lexicon = Lexicon(self.db)
        lexicon.learn('依托答辩', '本群指某个人的作业', origin='observed', scope='group:'+GROUP)
        seed.install(self.db)
        hit = lexicon.match('又是依托答辩', 'group:'+GROUP)[0]
        self.assertEqual(hit['now']['meaning'], '本群指某个人的作业'); self.assertIn('一坨大便', hit['general']['meaning'])

    def test_web_learning_later_supersedes_the_seed_and_keeps_history(self):
        seed.install(self.db)
        learner = CommunityLearner(self.db, search=lambda client: None)
        client = Client(**{'网上搜': {'meaning': '现在多指做得很烂的东西', 'tone': '贬', 'circle': '游戏', 'source': '1', 'change': 'changed'},
                           '她以前知道的意思': 'changed'})
        learner.learn_web('依托答辩', [Hit('依托答辩', 'https://example.org/yt', '……', '某百科')], client)
        senses = Lexicon(self.db).senses('依托答辩')
        self.assertEqual([s['origin'] for s in senses], ['seed', 'web'])
        self.assertIsNotNone(senses[0]['valid_to']); self.assertEqual(senses[0]['superseded_by'], senses[1]['id'])   # 旧义留着做演变记录

    def test_background_inference_does_not_demote_a_seeded_term_to_a_plain_word(self):
        seed.install(self.db); lexicon = Lexicon(self.db)
        for who in ('qq:111', 'qq:222'): lexicon.sight('绷不住了', GROUP, who, '我真绷不住了')
        self.db.set_setting('community_web', False)
        learner = CommunityLearner(self.db, search=lambda client: None)
        learner.infer('绷不住了', Client(**{'理解群里的黑话': {'in_context': '忍不住笑', 'plain': '忍不住笑', 'same': True}}), GROUP)
        self.assertEqual(lexicon.info('绷不住了')['is_jargon'], 1); self.assertTrue(lexicon.match('绷不住了'))


class ChatTests(BridgeFixture):
    def setUp(self):
        super().setUp(); self.db.set_setting('community_web', True); seed.install(self.db)
        self.web = Search([]); self.service.web_search = self.web

    def say(self, text, *replies):
        self.model.replies.extend(replies)
        return self.service.handle(dict(platform='qq', sender_id='20002', sender_name='tangle', group_id='30003', text=text, called=True,
                                        expression_version=1, bot_account_id='90000'))

    def test_seeded_meme_is_in_the_prompt_from_day_one_without_any_search(self):
        self.say('这游戏真是依托答辩', '确实')
        prompt = self.model.prompts[-1]
        self.assertIn('「依托答辩」（抽象）', prompt); self.assertIn('一坨大便', prompt); self.assertIn('常见用法', prompt)
        self.assertIn('不用解释笑点', prompt); self.assertEqual(self.web.queries, [])

    def test_plain_chat_is_not_flooded_and_at_most_six_are_shown(self):
        self.say('今天吃啥', '随便'); self.assertNotIn('这几句里的梗', self.model.prompts[-1])
        self.say('yyds xswl awsl 绷不住了 破防 麻了 摆烂 逆天 红温', '？')
        block = re.search(r'【这几句里的梗 / 黑话[^\n]*', self.model.prompts[-1])[0]
        self.assertEqual(block.count('「'), 6)


if __name__ == '__main__':
    unittest.main()
