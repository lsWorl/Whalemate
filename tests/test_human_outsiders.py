"""T19 对外人更像真人：被使唤时按真人的方式回、和外人聊尽量不用标点。临时库、假模型、假搜索，不联网。"""
import unittest

from aicompanion.bridge import favor
from aicompanion.community.lexicon import Lexicon
from aicompanion.community.search import Hit
from aicompanion.expression.segments import qq_segments
from aicompanion.relationship.service import PersonRef
from test_relationship import CID, BridgeFixture

ALWAYS = lambda: 0.0      # 每个断点都拆
NEVER = lambda: 0.99      # 每个断点都合


class Search:
    """假搜索：记下查了什么；不联网。"""
    def __init__(self, hits=(), kind='deepseek', page=''): self.hits = list(hits); self.queries = []; self.fetched = []; self.timeout = 20; self.kind = kind; self.page = page
    def search(self, query, count=5): self.queries.append(query); return list(self.hits)
    def backend(self): return self.kind
    def fetch(self, url): self.fetched.append(url); return self.page


class FavorCueTests(unittest.TestCase):
    def test_recognises_being_asked_to_do_things(self):
        for text in ('去帮我查下这是什么梗', '帮我写个作业', '给我翻译一下这句', '你搜一下', '急急国王是啥梗'):
            self.assertTrue(favor.wants_favor(text), text)
        for text in ('今天吃啥', '我帮他搬家去了', '笑死'):
            self.assertFalse(favor.wants_favor(text), text)

    def test_which_term_he_is_asking_about(self):
        self.assertEqual(favor.asked_term('帮我查下急急国王是什么梗'), '急急国王')
        self.assertEqual(favor.asked_term('你知道绷不住是啥意思吗'), '绷不住')
        self.assertEqual(favor.asked_term('“依托答辩”是什么梗'), '依托答辩')
        self.assertEqual(favor.asked_term('来财是什么梗'), '来财')
        for text in ('去帮我查下这是什么梗', '这个什么意思', '帮我写个作业'):                 # 指代、不是问梗：没有词可查
            self.assertEqual(favor.asked_term(text), '', text)


class FavorBehaviourTests(BridgeFixture):
    def setUp(self):
        super().setUp(); self.db.set_setting('community_web', True)
        self.web = Search([Hit('急急国王', 'https://example.org/jj', '急急国王是网络用语，形容一个人非常着急，出自某游戏主播的直播。', '某百科')])
        self.service.web_search = self.web; self.service.lookup_background = False

    def guy(self): return self.rel.resolve(PersonRef('qq', '90000', '20002', 'tangle'), CID)

    def say(self, text, *replies, sender='20002', group='30003', name='tangle'):
        self.model.replies.extend(replies or ('嗯',))
        return self.service.handle(dict(platform='qq', sender_id=sender, sender_name=name, group_id=group, text=text, called=True,
                                        expression_version=1, bot_account_id='90000'))

    def test_outsider_asking_her_to_look_something_up_gets_a_human_answer(self):
        """她愿意帮：自己决定去查（<mind lookup>），查完用自己的话说。"""
        learned = '{"meaning":"形容人很着急","tone":"调侃","circle":"游戏","usage":"调侃催促的人","source":"1","change":"same"}'
        result = self.say('帮我查下急急国王是什么梗', '<mind lookup="急急国王"/>', learned, '好像是说人很急的意思')
        first, second = self.model.prompts[0], self.model.prompts[-1]
        self.assertIn('你不是他的助手，也不是搜索引擎', first); self.assertIn('别用助手腔', first); self.assertIn('【遇到不懂的梗】', first)
        self.assertNotIn('你顺手搜了一下', first)                                              # 她没决定查之前不替她搜
        self.assertIn('你顺手搜了一下"急急国王"', second); self.assertIn('形容一个人非常着急', second)
        self.assertNotIn('https://example.org/jj', second)                                     # 链接不给她，免得贴出来
        self.assertEqual(self.web.queries, ['急急国王 梗 意思']); self.assertEqual(self.web.timeout, 8)
        self.assertEqual(result['reply'], '好像是说人很急的意思')

    def test_she_can_refuse_without_any_search(self):
        self.rel.adjust(CID, self.guy(), affinity=15)
        result = self.say('帮我查下急急国王是什么梗', '自己查')
        self.assertIn('没好感就不帮', self.model.prompts[-1]); self.assertEqual(self.web.queries, []); self.assertEqual(result['reply'], '自己查')

    def test_vague_request_has_nothing_to_search_and_she_may_ask_which(self):
        self.say('去帮我查下这是什么梗', '哪个')
        prompt = self.model.prompts[-1]
        self.assertIn('你不是他的助手', prompt); self.assertIn('问一句"哪个"', prompt); self.assertEqual(self.web.queries, [])

    def test_owner_is_not_treated_as_an_outsider(self):
        self.say('帮我查下急急国王是什么梗', '好嘞', sender='10001', name='爹')                # 群里的爹
        self.assertNotIn('你不是他的助手', self.model.prompts[-1])
        self.say('帮我查下急急国王是什么梗', '好嘞', sender='10001', group='', name='爹')      # 和爹私聊
        self.assertNotIn('你不是他的助手', self.model.prompts[-1]); self.assertEqual(self.web.queries, [])

    def test_ordinary_chat_gets_no_favor_block(self):
        self.say('今天吃啥'); self.assertNotIn('你不是他的助手', self.model.prompts[-1])


class PunctuationTests(BridgeFixture):
    def test_casual_segments_drop_sentence_punctuation(self):
        self.assertEqual(qq_segments('嗯，知道了。', chance=NEVER, casual=True), ['嗯知道了'])               # 短的直接连着写
        self.assertEqual(qq_segments('嗯，知道了。', chance=ALWAYS, casual=True), ['嗯', '知道了'])           # 或者另发一条
        self.assertEqual(qq_segments('好像是说人很急的意思，B站那边传出来的。', chance=NEVER, casual=True),
                         ['好像是说人很急的意思，B站那边传出来的'])                                        # 长句留一个逗号才读得通
        self.assertEqual(qq_segments('行吧、', chance=NEVER, casual=True), ['行吧'])
        for text in ('你自己不会搜吗？', '真的假的？？？', '等等...', '啊这…', '好耶！'):                    # 问号、带情绪的照留
            self.assertEqual(qq_segments(text, chance=NEVER, casual=True), [text])
        self.assertEqual(qq_segments('他说"好，的。"然后走了', chance=ALWAYS, casual=True), ['他说"好，的。"然后走了'])   # 引号里不动
        doc = '步骤如下：\n1. 打开设置。\n2. 点保存。'
        self.assertEqual(qq_segments(doc, casual=True), [doc])                                               # 像文档的照常
        for sent in qq_segments('我觉得还行，就是有点贵。不过也能接受，看你。', chance=ALWAYS, casual=True):
            self.assertNotRegex(sent, r'[，。、；]$')
        self.assertEqual(qq_segments('这也太离谱了吧，谁写的。', chance=NEVER), ['这也太离谱了吧，谁写的'])   # 不是 casual：和原来一样

    def test_commas_split_less_readily_than_full_stops(self):
        half = lambda: 0.3                                                                                    # burst 0.5：句号处拆，逗号处合
        self.assertEqual(qq_segments('行。走了', chance=half, casual=True), ['行', '走了'])
        self.assertEqual(qq_segments('行，走了', chance=half, casual=True), ['行走了'])

    def test_bridge_uses_casual_punctuation_for_outsiders_but_not_for_owner_private(self):
        self.service.chance = NEVER
        self.model.replies.append('嗯，知道了。')
        out = self.service.handle(dict(platform='qq', sender_id='20002', sender_name='tangle', group_id='30003', text='在吗', called=True,
                                       expression_version=1, bot_account_id='90000'))
        self.assertEqual(out['reply'], '嗯知道了'); self.assertIn('标点尽量少', self.model.prompts[-1])
        self.db.set_setting('bridge_strangers', True); self.model.replies.append('嗯，知道了。')
        stranger = self.service.handle(dict(platform='qq', sender_id='20002', sender_name='tangle', group_id='', text='在吗', called=True,
                                            expression_version=1, bot_account_id='90000'))
        self.assertEqual(stranger['reply'], '嗯知道了'); self.assertIn('标点尽量少', self.model.prompts[-1])
        self.model.replies.append('嗯，知道了。')
        dad = self.service.handle(dict(platform='qq', sender_id='10001', sender_name='爹', group_id='', text='在吗', called=True,
                                       expression_version=1, bot_account_id='90000'))
        self.assertEqual(dad['reply'], '嗯，知道了'); self.assertNotIn('标点尽量少', self.model.prompts[-1])


if __name__ == '__main__':
    unittest.main()
