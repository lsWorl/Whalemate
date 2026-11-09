"""T15 人物关系与自主意愿。全部临时库、假模型，不读真实数据、不联网。
覆盖任务说明第十节的 18 项，以及多轮对话行为（不只测数据库分数）。"""
from contextlib import closing
from dataclasses import replace
from datetime import date, timedelta
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest

from aicompanion.bridge.service import BridgeService
from aicompanion.controllers.chat_controller import ChatController
from aicompanion.domain.cancellation import CancellationToken
from aicompanion.domain.character import DEFAULT_CHARACTER
from aicompanion.relationship.analyzer import RelationshipAnalyzer, remember_sender
from aicompanion.relationship.service import (AUTONOMY, OWNER_DESKTOP, STRANGER, PersonRef, RelationshipService, scope_of)
from aicompanion.services.memory_service import MemoryService
from aicompanion.storage.database import Database
from aicompanion.storage.schema import SCHEMA_VERSION

CID = 'deepseek-whale'


class Clock:
    def __init__(self): self.now = 1_800_000_000.0; self.day = date(2026, 11, 9)
    def __call__(self): return self.now
    def today(self): return self.day
    def advance(self, seconds=0, days=0):
        self.now += seconds+days*86400; self.day += timedelta(days=days)


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.db = Database(Path(self.temp.name)/'synthetic.sqlite3'); self.db.initialize()
        self.character = replace(DEFAULT_CHARACTER, id=CID, name='鲸娘'); self.db.add_character(self.character)
        self.clock = Clock(); self.rel = RelationshipService(self.db, clock=self.clock, today=self.clock.today)
    def tearDown(self): self.temp.cleanup()
    def person(self, user='111', bot='90000', name='小明'): return self.rel.resolve(PersonRef('qq', bot, user, name), CID)
    def score(self, pid): p = self.rel.profile(CID, pid); return (round(p['affinity'], 2), round(p['familiarity'], 2), round(p['trust'], 2))


class IdentityTests(Base):
    def test_1_qq_identity(self):
        a = self.person('111', name='小明'); b = self.person('111', name='小明改名了')        # 改昵称：同一个人
        self.assertEqual(a, b); self.assertEqual(self.rel.profile(CID, a)['nicknames'], ['小明', '小明改名了'])
        self.assertNotEqual(a, self.person('222', name='小明'))                              # 同昵称不同号：不同人
        self.assertNotEqual(a, self.person('111', bot='90001'))                                # 不同机器人账号不自动合并
        self.assertEqual(self.rel.resolve(PersonRef('qq', '90000', '111', ''), CID), a)         # 群里、私聊都是同一个平台身份
        self.assertEqual(self.rel.profile(CID, a)['affinity'], STRANGER['affinity'])          # 陌生人初始，不是 50

    def test_3_owner_binding_and_desktop_are_one_person_stranger_does_not_touch_owner(self):
        self.db.set_setting('bridge_owner:qq', '10001')
        owner = self.rel.resolve(OWNER_DESKTOP, CID)
        self.assertEqual(self.rel.resolve(PersonRef('qq', '90000', '10001', '爹'), CID), owner)
        before = self.score(owner)
        stranger = self.person('333')
        self.rel.record(CID, stranger, 'insult', 0.95, evidence='骂她')
        self.assertEqual(self.score(owner)[0], before[0]); self.assertEqual(self.score(owner)[2], before[2])
        self.assertTrue(self.rel.profile(CID, owner)['is_owner'])
        self.db.set_setting('bridge_owner:qq', '')                                            # 解绑：这个号不再是主人
        self.assertNotEqual(self.rel.resolve(PersonRef('qq', '90000', '10001', ''), CID), owner)
        with self.assertRaises(ValueError): self.rel.delete(owner)                             # 主人只能重置

    def test_2_people_are_isolated(self):
        a, b = self.person('111'), self.person('222')
        self.rel.record(CID, a, 'care', 0.9, evidence='关心')
        self.assertGreater(self.score(a)[0], self.score(b)[0])


class ScoringTests(Base):
    def test_4_positive_5_negative_slow_and_evidence_based(self):
        p = self.person(); base = self.score(p)
        self.rel.record(CID, p, 'help_her', 0.9, evidence='帮她找到文件')
        self.assertGreater(self.score(p)[0], base[0]); self.assertGreater(self.score(p)[2], base[2])
        q = self.person('222'); start = self.score(q)
        first = self.rel.record(CID, q, 'insult', 0.7, evidence='骂了一句')
        self.assertEqual(first['note'], 'first_negative_halved')                              # 单次、把握不大：减半
        self.assertAlmostEqual(self.score(q)[0], start[0]-1.05, places=2)
        self.rel.record(CID, q, 'harassment', 0.9, evidence='反复刷屏骂')                       # 7 天内第二次：全量
        self.assertLess(self.score(q)[0], start[0]-3)

    def test_6_neutral_does_not_move_affinity_or_trust(self):
        p = self.person(); before = self.score(p)
        for kind in ('chat', 'question', 'disagreement', 'news', 'emotion', 'request'):
            self.rel.record(CID, p, kind, 0.95, evidence=kind)
        after = self.score(p)
        self.assertEqual((after[0], after[2]), (before[0], before[2])); self.assertGreaterEqual(after[1], before[1])

    def test_7_dimensions_are_independent(self):
        p = self.person()
        for _ in range(5):                                                                  # 天天聊：熟悉涨，好感 / 信任不涨
            self.clock.advance(days=1); self.rel.resolve(PersonRef('qq', '90000', '111'), CID)
        aff, fam, trust = self.score(p)
        self.assertGreater(fam, 2.5); self.assertEqual((aff, trust), (STRANGER['affinity'], STRANGER['trust']))
        q = self.person('222'); self.rel.record(CID, q, 'friendly', 0.9); self.rel.record(CID, q, 'deception', 0.9, evidence='骗她说已经修好了')
        aff2, _, trust2 = self.score(q)
        self.assertLess(trust2, STRANGER['trust']-1.5); self.assertGreater(aff2, STRANGER['affinity']-1)   # 友好但骗人：信任掉，好感没怎么掉
        fam_today = self.score(p)[1]
        for _ in range(20): self.rel.resolve(PersonRef('qq', '90000', '111'), CID)                # 同一天刷屏不涨熟悉
        self.assertEqual(self.score(p)[1], fam_today)

    def test_8_daily_cap(self):
        p = self.person(); start = self.score(p)[0]
        for n in range(6): self.rel.record(CID, p, 'care', 1.0, evidence=f'关心{n}')
        self.assertAlmostEqual(self.score(p)[0], start+4.0, places=2)
        self.assertEqual(self.rel.record(CID, p, 'care', 1.0, evidence='再关心')['note'], 'daily_cap')
        self.clock.advance(days=1); self.rel.record(CID, p, 'care', 1.0, evidence='第二天')
        self.assertAlmostEqual(self.score(p)[0], start+6.0, places=2)

    def test_9_low_confidence_unknown_and_duplicate_are_ignored(self):
        p = self.person(); before = self.score(p)
        self.assertEqual(self.rel.record(CID, p, 'care', 0.4, evidence='好像关心')['note'], 'low_confidence')
        self.assertFalse(self.rel.record(CID, p, 'love_bomb', 1.0)['applied'])
        self.rel.record(CID, p, 'care', 0.9, key='k1'); self.assertEqual(self.rel.record(CID, p, 'care', 0.9, key='k1')['note'], 'duplicate')
        self.assertAlmostEqual(self.score(p)[0], before[0]+1.8, places=2)

    def test_16_manual_adjust_is_kept_then_auto_continues(self):
        p = self.person()
        self.rel.adjust(CID, p, affinity=70)
        self.rel.record(CID, p, 'insult', 0.95, evidence='骂')
        self.assertEqual(self.score(p)[0], 70.0)                                           # 72 小时内不自动改
        self.assertIn('affinity', self.rel.profile(CID, p)['manual'])
        self.clock.advance(seconds=73*3600, days=3); self.rel.record(CID, p, 'care', 1.0, evidence='关心')
        self.assertAlmostEqual(self.score(p)[0], 72.0, places=2)                            # 从手动值继续
        self.rel.reset(CID, p); self.assertEqual(self.score(p)[0], STRANGER['affinity']); self.assertEqual(self.rel.events(CID, p), [])


class AnalyzerTests(Base):
    def test_model_only_describes_events_and_scope_stays_in_its_group(self):
        a = self.person('111'); b = self.person('222')
        analyzer = RelationshipAnalyzer(self.db, self.rel)
        raw = json.dumps({'events': [
            {'who': 'P1', 'type': 'care', 'confidence': 0.9, 'evidence': '问她累不累', 'affinity': 100, 'interest': '原神', 'experience': '一起修好了脚本'},
            {'who': 'P2', 'type': 'insult', 'confidence': 0.3, 'evidence': '开玩笑'},
            {'who': 'P9', 'type': 'care', 'confidence': 1.0}, {'who': 'P1', 'type': 'not_a_type', 'confidence': 1.0}]}, ensure_ascii=False)
        self.assertEqual(analyzer.apply(CID, 'qq:group:30003', '```json\n'+raw+'\n```', {'P1': a, 'P2': b}, last_id=5), 1)
        self.assertLess(self.score(a)[0], 40)                                                # 模型给的 affinity:100 被忽略
        self.assertEqual(self.score(b)[0], STRANGER['affinity'])                             # 低置信度的"辱骂"不计
        here = self.rel.prompt(CID, a, scope_of('qq:group:30003')); there = self.rel.prompt(CID, a, scope_of('qq:group:40004'))
        self.assertIn('原神', here); self.assertIn('一起修好了脚本', here)
        self.assertNotIn('原神', there); self.assertNotIn('脚本', there)                       # 不把一个群的细节带到另一个群
        self.assertEqual(analyzer.apply(CID, 'qq:group:30003', '不是 JSON', {'P1': a}, last_id=6), 0)

    def test_background_runs_only_when_enough_new_messages(self):
        class Model:
            model = 'mock'; calls = 0
            def generate_reply_stream(self, history, prompt, cancellation=None):
                Model.calls += 1
                yield json.dumps({'events': [{'who': 'P1', 'type': 'friendly', 'confidence': 0.9, 'evidence': '聊得开心'}]})
        a = self.person('111'); remember_sender(self.db, CID, 'qq:111', a)
        analyzer = RelationshipAnalyzer(self.db, self.rel)
        for n in range(5): self.db.add_message('user', f'小明#1：第{n}句', CID, channel='qq:group:30003', sender='qq:111')
        self.assertFalse(analyzer.maybe_run(CID, 'qq:group:30003', Model(), background=False))
        self.db.add_message('user', '小明#1：第六句', CID, channel='qq:group:30003', sender='qq:111')
        self.assertTrue(analyzer.maybe_run(CID, 'qq:group:30003', Model(), background=False))
        self.assertEqual(Model.calls, 1); self.assertEqual(self.rel.events(CID, a)[0]['kind'], 'friendly')
        self.assertFalse(analyzer.maybe_run(CID, 'qq:group:30003', Model(), background=False))   # 已处理过的不重复
        self.db.set_setting('relationship_learning', False)
        for n in range(6): self.db.add_message('user', f'小明#1：又{n}', CID, channel='qq:group:30003', sender='qq:111')
        self.assertFalse(analyzer.maybe_run(CID, 'qq:group:30003', Model(), background=False))


class Model:
    """记录每轮提示；按脚本回复（可以带 <mind/>）。"""
    model = 'mock'
    def __init__(self): self.replies = []; self.prompts = []
    def generate_reply_stream(self, history, prompt, cancellation=None):
        self.prompts.append(prompt)
        yield self.replies.pop(0) if self.replies else '嗯'


class BehaviourTests(Base):
    def setUp(self):
        super().setUp()
        self.db.set_setting('relationship_learning', False)
        self.model = Model(); self.controller = ChatController(self.db, MemoryService(self.db), self.model)
        self.controller.character = self.character; self.controller.relationships = self.rel
        self.controller.relationship_analyzer.relationships = self.rel
        self.db.set_setting('bridge_owner:qq', '10001'); self.db.set_setting('bridge_groups', ['30003']); self.db.set_setting('bridge_memory_every', 0)
        self.service = BridgeService(self.db, self.controller)
    def tearDown(self): self.service.close(); super().tearDown()

    def ask(self, sender, text, group='', name='群友'):
        return self.service.handle(dict(platform='qq', sender_id=sender, sender_name=name, group_id=group, text=text, called=True,
                                        expression_version=1, bot_account_id='90000'))

    def desktop(self, text, reply):
        self.model.replies.append(reply); self.controller.record_user_message(text)
        return ''.join(self.controller.generate(text+'-turn', self.character, CancellationToken(), text))

    def test_10_11_same_request_different_relationships_get_different_context(self):
        friend = self.rel.resolve(PersonRef('qq', '90000', '20002', '老朋友'), CID); self.rel.adjust(CID, friend, affinity=85, familiarity=80, trust=75)
        self.ask('20002', '帮我查下明天天气', group='30003', name='老朋友'); close = self.model.prompts[-1]
        self.ask('30003', '帮我查下明天天气', group='30003', name='路人'); far = self.model.prompts[-1]
        self.assertIn('很喜欢他', close); self.assertIn('非常熟', close)
        self.assertIn('几乎不认识', far); self.assertIn('说不上喜不喜欢', far)
        for prompt in (close, far): self.assertIn(AUTONOMY, prompt)                          # 同一套意愿说明，由她自己结合关系判断
        self.assertNotRegex(close+far, r'好感[:：]\s*\d|熟悉[:：]\s*\d')                       # 不报数值

    def test_12_13_14_refusal_and_interest_are_open_both_ways(self):
        # 高好感也可以拒绝、低好感也可以因为有意思参与、不感兴趣可以推脱——这些写在给大脑的说明里，不是应用层硬规则
        for words in ('关系好也可以拒绝', '关系一般也可以因为有意思而参与', '开玩笑推回去', '不是固定说法'):
            self.assertIn(words, AUTONOMY)
        self.ask('30003', '帮我写个周报', group='30003', name='路人')
        self.assertIn(AUTONOMY, self.model.prompts[-1])
        self.assertNotIn('必须', AUTONOMY)

    def test_multi_turn_requests_mind_marker_and_refusal_then_normal_chat(self):
        owner = self.rel.resolve(OWNER_DESKTOP, CID); start = self.score(owner)
        out1 = self.desktop('帮我查一下 1+1 等于几', '<mind intent="推回去" willing="0.2"/>这你自己按计算器嘛')
        self.assertEqual(out1, '这你自己按计算器嘛')                                          # 标记不显示
        self.assertNotIn('<mind', self.db.recent_messages(1, CID)[-1]['content'])            # 不进记录
        out2 = self.desktop('那帮我把这 200 个文件按日期重命名', '<mind intent="勉强" willing="0.5"/>行吧 不过下次你自己来')
        self.assertEqual(out2, '行吧 不过下次你自己来')
        self.desktop('再帮我整理一下桌面', '<mind intent="不想" willing="0.1"/>今天不想动了')
        self.assertIn('这一天他已经找你做过 2 次事了', self.model.prompts[-1])                   # 第三次时她知道他老找她
        requests = [e for e in self.rel.events(CID, owner) if e['kind'] == 'request']
        self.assertEqual([e['evidence'] for e in requests][::-1], ['推回去 意愿0.2', '勉强 意愿0.5', '不想 意愿0.1'])
        out4 = self.desktop('好吧 那陪我聊会儿', '好呀 聊啥')                                    # 拒绝后照常聊
        self.assertEqual(out4, '好呀 聊啥')
        self.assertEqual((self.score(owner)[0], self.score(owner)[2]), (start[0], start[2]))   # 拒绝 / 被拒绝不改好感信任

    def test_17_relationship_enters_unified_context_desktop_and_qq_owner_are_one(self):
        self.desktop('在吗', '在')
        self.assertIn('最亲近、天天相处', self.model.prompts[-1])
        self.ask('10001', '在吗')                                                            # QQ 私聊的爹 = 同一个人
        self.assertIn('最亲近、天天相处', self.model.prompts[-1])
        self.assertEqual(len([p for p in self.rel.people(CID) if p['is_owner']]), 1)
        self.ask('20002', '在吗', group='30003', name='群友A')
        self.assertIn('几乎不认识', self.model.prompts[-1]); self.assertNotIn('最亲近', self.model.prompts[-1])

    def test_18_expression_still_works_and_closeness_only_reorders(self):
        from aicompanion.expression.domain import RelationshipSnapshot, owner_relationship
        self.assertEqual(RelationshipSnapshot().closeness, 0.0); self.assertGreater(owner_relationship().closeness, 0.5)
        friend = self.rel.resolve(PersonRef('qq', '90000', '20002', '老朋友'), CID); self.rel.adjust(CID, friend, affinity=90, familiarity=90)
        self.assertGreaterEqual(self.service.closeness_of(friend), 0.9)
        self.assertFalse(RelationshipSnapshot(closeness=1.0).intimacy_allowed)              # 熟也不给亲昵图权限


class MigrationTests(unittest.TestCase):
    def test_v6_database_upgrades_with_backup_and_keeps_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'old.sqlite3'; db = Database(path); db.initialize()
            db.add_message('user', '合成旧消息', 'emilia'); db.set_setting('keep', 1)
            with closing(sqlite3.connect(path)) as c:
                for table in ('relationship_events', 'relationship_profiles', 'platform_identities', 'people'): c.execute(f'DROP TABLE {table}')
                c.execute('PRAGMA user_version=6'); c.commit()
            Database(path).initialize()
            self.assertEqual(len(list(Path(tmp).glob('old.backup-*.sqlite3'))), 1)
            with closing(sqlite3.connect(path)) as c:
                self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
                self.assertTrue(c.execute("SELECT 1 FROM sqlite_master WHERE name='relationship_profiles'").fetchone())
                self.assertEqual(c.execute('SELECT trust FROM relationship_state WHERE character_id=?', ('emilia',)).fetchone()[0], 50)
            self.assertEqual(Database(path).recent_messages(1, 'emilia')[0]['content'], '合成旧消息')


if __name__ == '__main__':
    unittest.main()
