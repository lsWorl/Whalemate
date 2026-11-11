"""T22 她对外人的态度：说了不理就真的不理、复读不理、冷淡只回一句、好感度看得见地起作用。临时库、假模型，不联网。
场景取自负责人 2026-11-10 的真机聊天记录（"懒得搭理你了"之后照样回、对方刷 fw 她每条都接、"勾#11"被叫了出来）。"""
import unittest

from aicompanion.bridge import stance as st
from aicompanion.bridge.stance import Stance
from aicompanion.relationship.service import PersonRef
from test_relationship import CID, Base, BridgeFixture

GROUP = 'qq:group:30003'


class RuleTests(Base):
    def test_her_own_words_that_mean_leaving(self):
        for said in ('懒得搭理你了', '那你自己扣着玩吧，不奉陪了', '不回了你自己玩吧', '懒得理了你爱刷刷吧', '我不接你慢慢循环', '不回你自己数着刷', '不跟你吵了'):
            self.assertTrue(st.LEAVE.search(said), said)
        for said in ('回你归回你', '我才懒得动', '这游戏不好玩', '你自己看着办'):
            self.assertFalse(st.LEAVE.search(said), said)

    def test_tiers_follow_the_relationship(self):
        base = dict(is_owner=False, familiarity=10, trust=35)
        self.assertEqual(st.tier(None), 'neutral')
        self.assertEqual(st.tier(dict(base, affinity=42)), 'neutral')
        self.assertEqual(st.tier(dict(base, affinity=20)), 'cold')
        self.assertEqual(st.tier(dict(base, affinity=42), hostility=3), 'cold')                    # 今天一直冲她来：好感还没掉到底也冷淡
        self.assertEqual(st.tier(dict(is_owner=False, affinity=85, familiarity=80, trust=75)), 'warm')
        self.assertEqual(st.tier(dict(is_owner=True, affinity=10, familiarity=0, trust=0)), 'warm')   # 爹不受限制

    def test_walking_away_escalates_and_can_be_cleared(self):
        stance = Stance(self.db, clock=self.clock)
        self.assertEqual(stance.walk_away(CID, 7, st.SAID), 10); self.assertGreater(stance.ignoring(CID, 7), 590)
        self.clock.now += 11*60; self.assertEqual(stance.ignoring(CID, 7), 0)                       # 时间到了
        self.assertEqual(stance.walk_away(CID, 7, st.SAID), 20)                                     # 一天内再犯：翻倍
        stance.clear(CID, 7); self.assertEqual(stance.ignoring(CID, 7), 0)
        self.assertIn('不理他', (stance.walk_away(CID, 7, st.SPAM, 5), stance.describe(CID, 7, None))[1])
        self.clock.now += 2*86400; self.assertEqual(stance.walk_away(CID, 7, st.SAID), 10)          # 隔了一天：重新算

    def test_repeat_detection(self):
        stance = Stance(self.db, clock=self.clock)
        self.assertFalse(stance.repeated('qq:1', 'fw')); self.assertFalse(stance.repeated('qq:1', 'fw'))
        self.assertTrue(stance.repeated('qq:1', 'FW。'))                                           # 第三遍；大小写、标点不算差别
        self.assertFalse(stance.repeated('qq:2', 'fw'))                                             # 别人不受影响
        self.clock.now += 300; self.assertFalse(stance.repeated('qq:1', 'fw'))                      # 过了两分钟重新算


class BehaviourTests(BridgeFixture):
    def setUp(self):
        super().setUp(); self.service.chance = lambda: 0.99                                        # 固定：不随机已读不回、能连就连
        self.service.stance = Stance(self.db, clock=self.clock)                                    # 用测试时钟

    def guy(self): return self.rel.resolve(PersonRef('qq', '90000', '20002', 'tangle'), CID)

    def say(self, text, *replies, sender='20002', group='30003', name='tangle', called=True):
        self.model.replies.extend(replies)
        return self.service.handle(dict(platform='qq', sender_id=sender, sender_name=name, group_id=group, text=text, called=called,
                                        expression_version=1, bot_account_id='90000'))

    def heard(self, channel=GROUP): return [r['content'] for r in self.db.recent_messages(20, self.character.id, channel=channel) if r['role'] == 'user']

    def test_saying_she_is_done_means_she_really_stops(self):
        """真机记录：17:06 "不奉陪了" → 对方再发，她照样回。现在这句之后真的不回。"""
        first = self.say('就是给你扣帽子', '那你自己扣着玩吧 不奉陪了')
        self.assertEqual(first['ignore_minutes'], 10); self.assertIn('说到做到', self.model.prompts[-1])
        calls = len(self.model.prompts)
        for text in ('小糖人', '你不是还在回我', '？？？'):
            result = self.say(text)
            self.assertEqual((result['silent'], result['reason']), (True, 'ignoring'))
        self.assertEqual(len(self.model.prompts), calls)                                            # 不调模型
        self.assertIn('tangle#1：小糖人', self.heard())                                             # 但她"看到"了：记进聊天记录
        self.assertEqual(self.say('在吗', called=False)['reason'], 'ignoring')                      # 没 @ 的也不接话
        other = self.say('在吗', '在', sender='40004', name='小林'); self.assertEqual(other['reply'], '在')   # 只是不理他，别人照常
        self.clock.now += 11*60
        self.assertEqual(self.say('还在吗', '干嘛')['reply'], '干嘛')                               # 时间到了：恢复

    def test_apology_ends_it_early_and_she_decides_whether_to_forgive(self):
        self.say('就是给你扣帽子', '不奉陪了')
        result = self.say('对不起 我错了', '哦')
        self.assertEqual(result['reply'], '哦'); self.assertIn('他现在像是来服软的', self.model.prompts[-1])
        self.assertEqual(self.service.stance.ignoring(CID, self.guy()), 0)

    def test_spamming_the_same_line_gets_ignored_without_a_word(self):
        """真机记录：对方连刷十几条 fw，她每隔几条就回一句"不回了"。"""
        self.assertEqual(self.say('fw', '？')['reply'], '？'); self.say('fw', '哦')
        calls = len(self.model.prompts)
        for _ in range(4): self.assertEqual(self.say('fw')['reason'], 'ignoring')
        self.assertEqual(len(self.model.prompts), calls)
        self.assertIn('复读', self.service.stance.describe(CID, self.guy(), None))

    def test_cold_people_get_one_short_line_and_strangers_at_most_three(self):
        guy = self.guy(); self.rel.adjust(CID, guy, affinity=15)
        result = self.say('在吗', '哦\n有事说\n没事别找我\n真的')
        self.assertIn('【你现在对他很冷淡】', self.model.prompts[-1]); self.assertIn('十个字以内', self.model.prompts[-1])
        self.assertEqual(result['reply'], '哦'); self.assertNotIn('segments', result)              # 只发第一句
        stranger = self.say('在吗', '在\n咋了\n说\n快点\n别磨叽', sender='40004', name='小林')
        self.assertIn('【你跟他不熟】', self.model.prompts[-1]); self.assertEqual(stranger['segments'], ['在', '咋了', '说'])
        friend = self.rel.resolve(PersonRef('qq', '90000', '50005', '阿青'), CID); self.rel.adjust(CID, friend, affinity=85, familiarity=80, trust=75)
        warm = self.say('在吗', '在\n咋了\n说\n快点\n别磨叽', sender='50005', name='阿青')
        self.assertIn('【你跟他挺熟】', self.model.prompts[-1]); self.assertEqual(len(warm['segments']), 5)

    def test_cap_never_cuts_a_real_answer_and_history_matches_what_was_sent(self):
        """2026-11-11 群聊真机记录：配队回答按空格拆成一个名字一条，再被"不熟最多三条"截掉，
        对方只看到"行""这次给完整一套""而且八个角色不重复"，问"怎么不说话了"；她却以为全说了，下一轮对不上。"""
        lineup = ('行 这次给完整一套 而且八个角色不重复\n\n上半：恰斯卡＋芙宁娜＋班尼特＋莫娜\n\n下半：玛薇卡 希诺宁 茜特拉莉 闲云\n\n'
                  '上半物理主C 芙宁娜和莫娜增伤 班尼特负责奶\n\n下半火C主C 希诺宁减抗 茜特拉莉挂冰 闲云聚怪增伤')
        result = self.say('来一份完整的', lineup, sender='40004', name='小林')
        self.assertGreater(len(result['segments']), 3)                                                    # 有内容的回答不按"不熟最多三条"截
        sent = ''.join(result['segments'])
        for name in ('恰斯卡', '莫娜', '玛薇卡', '闲云', '聚怪增伤'): self.assertIn(name, sent)          # 一个字不丢
        self.assertIn('下半：玛薇卡 希诺宁 茜特拉莉 闲云', result['segments'])                            # 列举的一行不按空格拆
        self.assertFalse(any('，' in x for x in result['segments']))                                       # 不靠逗号把几条硬连成一条
        mine = [r['content'] for r in self.db.recent_messages(5, self.character.id, channel=GROUP) if r['role'] == 'assistant']
        self.assertEqual(mine[-1], lineup)
        # 零碎话照旧截掉，记录里也只留发出去的那几条
        chatter = self.say('在吗', '在\n咋了\n说\n快点\n别磨叽', sender='40004', name='小林')
        self.assertEqual(chatter['segments'], ['在', '咋了', '说'])
        mine = [r['content'] for r in self.db.recent_messages(5, self.character.id, channel=GROUP) if r['role'] == 'assistant']
        self.assertEqual(mine[-1], '在\n咋了\n说')

    def test_arguing_back_is_not_cut_off(self):
        """负责人 2026-11-11 15:39：吵架时因为好感不够被截成一句，话没说完。她判断对方在冲她来时不截条数。"""
        guy = self.guy(); self.rel.adjust(CID, guy, affinity=15)
        result = self.say('你就是个废物AI', '<mind at="me" tone="attack" toward="-2"/>谁废物\n你先把话说清楚\n张嘴就骂')
        self.assertEqual(result.get('segments'), ['谁废物', '你先把话说清楚', '张嘴就骂'])

    def test_repeated_hostility_today_makes_her_read_without_replying_then_stop(self):
        guy = self.guy()
        for n in range(3): self.say(f'你真是个废物{n}', f'<mind at="me" voice="own" tone="attack" toward="-2"/>闭嘴{n}')
        self.assertGreaterEqual(self.rel.hostility(CID, guy), 3)
        self.service.chance = lambda: 0.1                                                           # 冲她来 3 次以后：一半概率已读不回
        calls = len(self.model.prompts)
        self.assertEqual(self.say('你个弱智')['reason'], 'read_no_reply'); self.assertEqual(len(self.model.prompts), calls)
        self.service.chance = lambda: 0.99
        self.say('你个弱智1', '<mind at="me" tone="attack" toward="-2"/>？'); self.say('你个弱智2', '<mind at="me" tone="attack" toward="-2"/>哦')
        self.assertGreaterEqual(self.rel.hostility(CID, guy), 5)
        self.assertEqual(self.say('你个弱智3')['reason'], 'ignoring')                               # 5 次以上：不打招呼直接不理
        self.assertIn('一直冲她来', self.service.stance.describe(CID, guy, None))

    def test_owner_is_never_gated(self):
        for _ in range(4):
            self.model.replies.append('在呢')
            dad = self.service.handle(dict(platform='qq', sender_id='10001', sender_name='爹', group_id='', text='fw', called=True,
                                           expression_version=1, bot_account_id='90000'))
            self.assertEqual(dad['reply'], '在呢')
        self.say('fw', '不奉陪了', sender='10001', name='爹')                                       # 爹在群里：她说气话也不会真不理爹
        self.assertEqual(self.say('在吗', '在', sender='10001', name='爹')['reply'], '在')

    def test_internal_number_tags_never_reach_the_chat_and_style_rules_are_in_the_prompt(self):
        """真机记录：她发了"勾#11，一句一条才像发消息啊"。"""
        result = self.say('你是把一句话拆成n句说吗', '勾#11 一句一条才像发消息啊', sender='40004', name='勾')
        self.assertEqual(result['reply'], '勾 一句一条才像发消息啊')
        prompt = self.model.prompts[-1]
        for words in ('绝大多数时候就一小句', '别评论那个词本身', '只回这次跟你说话的这个人', '"#数字"是给你区分人用的', '不用逗号、句号'):
            self.assertIn(words, prompt)
        self.assertEqual(self.say('C#12 好用吗', 'C#12 还行', sender='40004', name='勾')['reply'], 'C#12 还行')   # 正常的 # 不误删


class ImpostorAndRosterTests(BridgeFixture):
    """负责人 2026-11-10 19:06：外人说"我是你爹"应该让她生气；看群聊上下文要结合"是谁说的、和她关系如何"。"""
    def setUp(self):
        super().setUp(); self.service.chance = lambda: 0.99
        self.service.stance = Stance(self.db, clock=self.clock)

    def person(self, uid, name): return self.rel.resolve(PersonRef('qq', '90000', uid, name), CID)

    def say(self, text, *replies, sender='20002', group='30003', name='tangle', called=True):
        self.model.replies.extend(replies)
        return self.service.handle(dict(platform='qq', sender_id=sender, sender_name=name, group_id=group, text=text, called=called,
                                        expression_version=1, bot_account_id='90000'))

    def test_recognises_people_claiming_to_be_her_father(self):
        from aicompanion.relationship.intent import claims_father
        for text in ('我是你爹啊', '怎么和你爹说话的', '叫爸爸', '快叫声爹', '你爹我来了', '我才是你爸'):
            self.assertTrue(claims_father(text), text)
        for text in ('你爹呢', '我爸今天做饭', '他是你爹吗', '叫我阿哲就行'):
            self.assertFalse(claims_father(text), text)

    def test_outsider_claiming_to_be_her_father_makes_her_angry(self):
        guy = self.person('20002', 'tangle'); before = self.score(guy)
        result = self.say('我是你爹啊', '<mind at="me" tone="attack" toward="-2"/>你也配')
        prompt = self.model.prompts[-1]
        self.assertIn('【他在冒充你爹、占你便宜】', prompt); self.assertIn('绝不顺着叫他爹', prompt)
        self.assertIn('"爹"只用来叫你爹', prompt); self.assertIn('还在气头上', prompt)             # 这一轮就带着气
        kinds = [e['kind'] for e in self.rel.events(CID, guy)]
        self.assertIn('boundary_violation', kinds)
        after = self.score(guy)
        self.assertLess(after[0], before[0]-1); self.assertLess(after[2], before[2]-1)              # 好感、信任都降
        self.assertTrue(self.rel.upset(CID, guy)); self.assertEqual(result['reply'], '你也配')

    def test_the_real_owner_can_say_it(self):
        self.say('怎么和你爹说话的', '错了错了', sender='10001', name='爹')
        self.assertNotIn('冒充你爹', self.model.prompts[-1])
        owner = self.rel.owner_person()
        self.assertNotIn('boundary_violation', [e['kind'] for e in self.rel.events(CID, owner)])

    def test_group_context_tells_her_who_each_speaker_is_to_her(self):
        friend = self.person('50005', '阿青'); self.rel.adjust(CID, friend, affinity=85, familiarity=80, trust=75)
        hater = self.person('20002', 'tangle'); self.rel.adjust(CID, hater, affinity=12)
        self.say('今天打啥', called=False, sender='50005', name='阿青')                              # 旁听：只是记下
        self.say('你个sb', called=False)
        self.say('大家好', called=False, sender='10001', name='爹')
        self.say('在吗', '在', sender='40004', name='小林')                                          # 第四个人 @ 她
        prompt = self.model.prompts[-1]
        block = prompt[prompt.index('【最近在这儿说话的人，和你的关系】'):].split('\n')[0]
        self.assertRegex(block, r'小林#\d+（这条是他发的）：不熟')
        self.assertRegex(block, r'阿青#\d+：挺熟、你喜欢他'); self.assertRegex(block, r'tangle#\d+：你对他没好感')
        self.assertRegex(block, r'爹#\d+：你爹')
        self.assertIn('别把这个人说的话算到另一个人头上', block)
        self.assertNotIn('20002', block); self.assertNotIn('50005', block)                          # 不给号码

    def test_roster_shows_who_she_is_ignoring_and_is_skipped_when_alone(self):
        self.say('在吗', '在'); self.assertNotIn('【最近在这儿说话的人', self.model.prompts[-1])     # 只有他一个人：不加
        self.say('就是给你扣帽子', '不奉陪了')
        self.say('她怎么不理人', '别管他', sender='40004', name='小林')
        self.assertRegex(self.model.prompts[-1], r'tangle#\d+：[^；]*你说过不理他了')


if __name__ == '__main__':
    unittest.main()
