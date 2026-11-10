"""T18 社交语境与攻击意图：判断"是不是在骂她"时的六个问题。临时库、假模型，不联网。"""
import json
import unittest

from aicompanion.controllers.chat_controller import ChatController
from aicompanion.relationship.analyzer import RelationshipAnalyzer
from aicompanion.relationship.intent import brief, cues
from aicompanion.relationship.service import STRANGER, PersonRef
from test_relationship import CID, Base, BridgeFixture


class CueTests(unittest.TestCase):
    def test_1_who_is_it_aimed_at(self):
        self.assertIn('@ 了你', cues('你真蠢', at_me=True).target)
        self.assertTrue(any('别人' in t for t in cues('你个傻逼', at_others=['30003']).target))
        self.assertIn('引用的是你说的话', cues('就这？', reply_to={'self': True}).target)
        self.assertTrue(any('引用的是阿明' in t for t in cues('就这？', reply_to={'self': False, 'name': '阿明'}).target))
        self.assertTrue(any('提到了你' in t for t in cues('这 AI 真笨').target))
        self.assertTrue(any('一群人' in t for t in cues('你们都是废物').target))

    def test_2_own_words_or_quoting_someone(self):
        self.assertTrue(cues('他说你是个废物').report)
        self.assertTrue(cues('网上有人说：这游戏玩家都是傻逼').report)
        self.assertTrue(cues('“傻逼”这个词别乱用').report)
        self.assertFalse(cues('我说你是个废物').report)                                      # 我说 / 你说不是转述
        self.assertFalse(cues('你是个废物').report)

    def test_3_joke_sarcasm_or_attack(self):
        for text in ('你个sb', 'nmsl', '真是若智', '傻b吧你', '煞笔'):                         # 变体、谐音、缩写
            self.assertTrue(cues(text).insult, text)
        for text in ('您可真厉害呢🙂', '好家伙，不愧是你', '啊对对对', '就这？'):
            self.assertTrue(cues(text).sarcasm, text)
        found = cues('哈哈哈哈笑死 你个笨蛋[doge]')
        self.assertTrue(found.joke)
        self.assertFalse(cues('今天吃啥').worth_reading)                                      # 平常聊天不加线索

    def test_4_5_6_context_relationship_and_persistence_in_brief(self):
        text = brief(cues('你真是个废物', at_me=True), reading='mocking', hostility=3, bullied_others=2, upset=True, last_said='我觉得这版不好')
        for words in ('我觉得这版不好', '刚闹过不愉快', '不熟', '今天已经冲你来了 3 次', '攻击过别人 2 次', 'at="me|other|all|none"', 'voice="own|quote"'):
            self.assertIn(words, text)
        self.assertIn('更可能是在逗你', brief(cues('你个笨蛋 哈哈', at_me=True), reading='teasing'))


class SnarkTests(unittest.TestCase):
    def test_low_affinity_insult_or_sarcasm_gets_snark_back(self):
        """负责人 2026-11-10：有人骂她 / 阴阳她、且好感低时，可以阴阳回去。"""
        self.assertIn('阴阳回去', brief(cues('你个sb', at_me=True), reading='mocking', affinity=20))
        self.assertIn('阴阳回去', brief(cues('您可真厉害呢🙂', at_me=True), reading='unsure', affinity=35))
        self.assertNotIn('阴阳回去', brief(cues('你个笨蛋 哈哈', at_me=True), reading='teasing', affinity=85))      # 熟人逗她不算
        self.assertNotIn('阴阳回去', brief(cues('他说你是傻逼', at_me=True), reading='mocking', affinity=20))       # 转述不算
        self.assertNotIn('阴阳回去', brief(cues('他说你是傻逼', at_me=True), reading='unsure', affinity=60))

    def test_snark_never_decides_the_target_for_her(self):
        """Codex 审查 906cd6b P2：反击说明是条件策略，不先断定"他在骂你"；明确冲别人时不加。"""
        def snark(**kw): return brief(cues('你个sb', **kw), reading='mocking', affinity=15)
        for text in (snark(at_me=True), snark()):                                                  # @ 她 / 对象不明
            self.assertIn('如果你结合上下文判断，这句确实是他本人冲你来的', text)
            self.assertNotIn('现在又在骂你', text)
        self.assertNotIn('阴阳回去', snark(at_others=['40004']))                                   # @ 了别人
        self.assertNotIn('阴阳回去', snark(reply_to={'self': False, 'name': '阿明'}))              # 回复别人
        self.assertIn('阴阳回去', snark(at_others=['40004'], at_me=True))                          # 同时 @ 了她：交给她判断
        self.assertIn('阴阳回去', snark(reply_to={'self': True}))


class FeltTests(unittest.TestCase):
    def test_her_judgement_maps_to_events_deterministically(self):
        felt = ChatController._felt
        self.assertEqual(felt({'voice': 'quote', 'at': 'me', 'tone': 'attack', 'toward': -3})[0], 'report')    # 转述：不扣他
        self.assertEqual(felt({'at': 'other', 'tone': 'attack'})[0], 'third_party_attack')
        self.assertEqual(felt({'at': 'other', 'tone': 'joke'})[0], None)
        kind, impact, _ = felt({'at': 'me', 'tone': 'attack'}); self.assertEqual(kind, 'insult'); self.assertLess(impact['affinity'], -2)
        self.assertEqual(felt({'at': 'me', 'tone': 'sarcasm'})[0], 'quarrel')
        self.assertEqual(felt({'at': 'me', 'tone': 'tease', 'toward': 1})[0], 'friendly')
        self.assertEqual(felt({'toward': -2})[0], 'quarrel')                                      # 旧写法照常


class BehaviourTests(BridgeFixture):
    def guy(self): return self.rel.resolve(PersonRef('qq', '90000', '20002', 'tangle'), CID)

    def say(self, text, reply, context=None, group='30003'):
        self.model.replies.append(reply)
        payload = dict(platform='qq', sender_id='20002', sender_name='tangle', group_id=group, text=text, called=True,
                       expression_version=1, bot_account_id='90000')
        if context: payload['context'] = context
        return self.service.handle(payload)

    def test_quoting_someone_is_not_insulting_her_but_saying_it_is(self):
        guy = self.guy(); start = self.score(guy); self.service.chance = lambda: 0.0          # 固定成拆开发（T19）
        result = self.say('他说你是个废物', '<mind at="me" voice="quote" tone="neutral"/>谁说的，让他自己来')
        self.assertEqual(result['segments'], ['谁说的', '让他自己来'])
        self.assertIn('像在转述别人的话', self.model.prompts[-1]); self.assertIn('不等于他在骂你', self.model.prompts[-1])
        self.assertEqual(self.rel.events(CID, guy)[0]['kind'], 'report'); self.assertEqual(self.score(guy)[0], start[0])
        self.say('你就是个废物', '<mind at="me" voice="own" tone="attack" toward="-3"/>你才是')
        self.assertEqual(self.rel.events(CID, guy)[0]['kind'], 'insult'); self.assertLess(self.score(guy)[0], start[0]-1)

    def test_attacking_someone_else_is_not_counted_against_her_relationship_as_insult(self):
        guy = self.guy(); start = self.score(guy)
        self.say('你个傻逼', '<mind at="other" tone="attack"/>你骂他干嘛', context={'mentions': ['40004']})
        self.assertTrue(any('@ 了别人' in line for line in self.model.prompts[-1].split('；')))
        self.assertEqual(self.rel.events(CID, guy)[0]['kind'], 'third_party_attack')
        self.assertGreater(self.score(guy)[0], start[0]-1)                                      # 只略降
        self.say('你又在干嘛', '<mind feel="不爽" toward="-1"/>看着呢')                      # 普通的话不加线索块
        self.assertIn('最近还在群里攻击过别人 1 次', self.say('你也是个sb', '<mind at="me" tone="attack"/>哈？')
                      and self.model.prompts[-1])

    def test_disliked_person_mocking_her_is_answered_with_snark(self):
        guy = self.guy(); self.rel.adjust(CID, guy, affinity=15)
        self.say('您可真厉害呢🙂', '<mind at="me" tone="sarcasm"/>嗯嗯，还是你懂')
        prompt = self.model.prompts[-1]
        self.assertIn('阴阳回去', prompt); self.assertIn('正话反说', prompt)

    def test_bridge_brief_does_not_turn_an_attack_on_someone_else_into_one_on_her(self):
        """Codex 复现：没 @ 她、@ 了别人并引用别人，正文"你个sb"，好感低。"""
        guy = self.guy(); self.rel.adjust(CID, guy, affinity=15)
        mode = {'channel': 'qq:group:30003'}
        aimed = self.service.intent_brief(mode, guy, '你个sb', {'context': {'mentions': ['40004'], 'reply_to': {'self': False, 'name': '阿明'}}}, called=False)
        self.assertIn('@ 了别人', aimed); self.assertIn('引用的是阿明的消息', aimed)
        self.assertNotIn('阴阳回去', aimed); self.assertNotIn('又在骂你', aimed)
        unclear = self.service.intent_brief(mode, guy, '你个sb', {}, called=False)
        self.assertIn('如果你结合上下文判断', unclear); self.assertNotIn('又在骂你', unclear)

    def test_persistent_targeting_is_noticed(self):
        self.service.chance = lambda: 0.99                                                    # T22：冲她来 3 次以后有一半概率已读不回，这里固定成回
        for n in range(3):
            self.say(f'你真是个废物{n}', f'<mind at="me" voice="own" tone="attack" toward="-2"/>闭嘴{n}')
        self.say('你个sb', '<mind at="me" tone="attack"/>你有完没完')
        prompt = self.model.prompts[-1]
        self.assertIn('他今天已经冲你来了 3 次', prompt); self.assertIn('明显是在针对你', prompt)

    def test_friend_teasing_reads_as_teasing(self):
        friend = self.guy(); self.rel.adjust(CID, friend, affinity=85, familiarity=80, trust=75)
        start = self.score(friend)
        self.say('你个笨蛋鲸 哈哈哈哈', '<mind at="me" tone="tease" toward="1"/>你才笨')
        self.assertIn('更可能是在逗你', self.model.prompts[-1])
        self.assertEqual(self.rel.events(CID, friend)[0]['kind'], 'friendly'); self.assertGreaterEqual(self.score(friend)[0], start[0])


class AnalyzerTests(Base):
    def test_background_analysis_separates_quotes_and_attacks_on_others(self):
        a = self.person('111'); b = self.person('222')
        raw = json.dumps({'events': [
            {'who': 'P1', 'type': 'insult', 'confidence': 0.9, 'evidence': '转述别人骂她', 'voice': 'quote', 'target': 'her'},
            {'who': 'P2', 'type': 'insult', 'confidence': 0.9, 'evidence': '骂群里别人', 'voice': 'own', 'target': 'other', 'impact': {'affinity': -3}}]})
        RelationshipAnalyzer(self.db, self.rel).apply(CID, 'qq:group:30003', raw, {'P1': a, 'P2': b}, last_id=1)
        self.assertEqual(self.rel.events(CID, a)[0]['kind'], 'report'); self.assertEqual(self.score(a)[0], STRANGER['affinity'])
        self.assertEqual(self.rel.events(CID, b)[0]['kind'], 'third_party_attack')
        self.assertGreater(self.score(b)[0], STRANGER['affinity']-1)                            # 模型给的 -3 不用在攻击别人上


if __name__ == '__main__':
    unittest.main()
