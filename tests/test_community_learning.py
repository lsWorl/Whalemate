"""T18 社区知识学习：黑话 / 梗的来源、含义、演变（双时间线），联网搜索（假的 opener，不联网），群里的共同记忆，检索。"""
import io
import json
import unittest
from unittest import mock

from aicompanion.community.learner import CommunityLearner
from aicompanion.community.lexicon import Lexicon
from aicompanion.community.lore import GroupLore
from aicompanion.community.search import Hit, WebSearch, searchable
from aicompanion.relationship.analyzer import RelationshipAnalyzer, remember_sender
from test_relationship import CID, Base, BridgeFixture

GROUP = 'qq:group:30003'


class FakeResponse(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Opener:
    def __init__(self, *answers): self.answers = list(answers); self.requests = []
    def __call__(self, request, timeout=0):
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception): raise answer
        return FakeResponse(json.dumps(answer).encode())


class Client:
    """按说明里的关键字给出脚本化的 JSON；记录每次调用。"""
    base_url = 'https://api.deepseek.com'; api_key = 'synthetic-key'; model = 'mock'
    def __init__(self, **scripts): self.scripts = scripts; self.calls = []
    def generate_reply_stream(self, history, prompt, cancellation=None):
        self.calls.append(prompt)
        for key, value in self.scripts.items():
            if key in prompt: yield value if isinstance(value, str) else json.dumps(value, ensure_ascii=False); return
        yield '{}'


class LexiconTests(Base):
    def test_meaning_evolution_keeps_history(self):
        lex = Lexicon(self.db, clock=self.clock)
        self.assertEqual(lex.learn('绷不住', '忍不住笑', origin='web', source_name='百科', source_url='https://example.org/a'), 'added')
        self.assertEqual(lex.learn('绷不住', '忍不住笑', origin='web', change='same'), 'reinforced')
        self.clock.advance(days=30)
        self.assertEqual(lex.learn('绷不住', '情绪崩溃、受不了', origin='web', change='changed'), 'changed')
        senses = lex.senses('绷不住')
        self.assertEqual(len(senses), 2)
        old, new = senses
        self.assertIsNotNone(old['valid_to']); self.assertEqual(old['superseded_by'], new['id']); self.assertIsNone(new['valid_to'])   # 旧的不删
        prompt = lex.prompt('这也太绷不住了')
        self.assertIn('情绪崩溃', prompt); self.assertIn('以前的意思：忍不住笑', prompt); self.assertIn('不用解释笑点', prompt)

    def test_group_specific_meaning_and_matching(self):
        lex = Lexicon(self.db, clock=self.clock)
        lex.learn('小糖人', '可爱的人', origin='web')
        lex.learn('小糖人', '群里对机器人的戏称', origin='observed', scope='group:'+GROUP)
        lex.learn('糖人', '糖做的人偶', origin='web')
        here = lex.match('你个小糖人', scope='group:'+GROUP)
        self.assertEqual([h['term'] for h in here], ['小糖人'])                                   # 长词优先，"糖人"被包含
        self.assertEqual(here[0]['now']['meaning'], '群里对机器人的戏称')                          # 本群用法优先
        self.assertIn('一般的意思是：可爱的人', lex.prompt('你个小糖人', scope='group:'+GROUP))
        self.assertEqual(lex.match('你个小糖人')[0]['now']['meaning'], '可爱的人')                  # 别的地方用通用义

    def test_sightings_and_thresholds(self):
        learner = CommunityLearner(self.db, clock=self.clock)
        self.assertFalse(learner.saw('急急国王', GROUP, '1', '急急国王来了'))
        self.assertTrue(learner.saw('急急国王', GROUP, '2', '你个急急国王'))                     # 第 2 次：该推断了
        learner.lexicon.mark('急急国王', is_jargon=True, inferred_at_count=2)
        for n in range(2): self.assertFalse(learner.saw('急急国王', GROUP, '3', f'x{n}'))
        self.assertTrue(learner.saw('急急国王', GROUP, '3', 'y'))                                  # 第 5 次
        seen = learner.lexicon.sightings('急急国王')[0]
        self.assertEqual(seen['count'], 5); self.assertEqual(sorted(seen['speakers']), ['1', '2', '3'])
        self.db.set_setting('community_learning', False)
        self.assertFalse(learner.saw('急急国王', GROUP, '4', 'z'))


class SearchTests(Base):
    def test_privacy_filter(self):
        self.assertFalse(searchable('急急国王', speakers=1))                                         # 只有一个人说过
        self.assertTrue(searchable('急急国王', speakers=2))
        self.assertFalse(searchable('阿明', speakers=5, names=['阿明']))                              # 群友外号不外传
        self.assertFalse(searchable('1428903832', speakers=5)); self.assertFalse(searchable('看https://x.y', speakers=5))

    def test_deepseek_backend_same_provider_budget_and_errors(self):
        opener = Opener({'results': [{'title': '急急国王', 'url': 'https://example.org/jj', 'summary': '形容人很着急', 'site_name': '某百科'}]})
        web = WebSearch(self.db, Client(), opener=opener, today=self.clock.today)
        hits = web.search('急急国王 梗 意思')
        self.assertEqual(hits[0].summary, '形容人很着急')
        request = opener.requests[0]
        self.assertEqual(request.full_url, 'https://api.deepseek.com/v1/tools/search')
        self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-key')
        self.assertEqual(json.loads(request.data)['query'], '急急国王 梗 意思')
        self.db.set_setting('community_search_daily', 1)
        self.assertEqual(web.search('再来一次'), []); self.assertEqual(len(opener.requests), 1)        # 超额不发请求
        self.assertEqual(web.used_today()['search'], 1)
        self.db.set_setting('community_search_daily', 5)
        web.open = Opener(OSError('synthetic-key boom'))
        self.assertEqual(web.search('出错'), [])                                                    # 出错不抛、不外传
        self.db.set_setting('community_web', False)
        self.assertEqual(WebSearch(self.db, Client(), opener=Opener(), today=self.clock.today).search('关了'), [])

    def test_other_provider_falls_back_to_wikipedia(self):
        client = Client(); client.base_url = 'https://another.example/v1'
        opener = Opener({'query': {'search': [{'title': '凡尔赛文学'}]}},
                        {'query': {'pages': {'1': {'title': '凡尔赛文学', 'extract': '一种以低调方式炫耀的网络用语。'}}}})
        hits = WebSearch(self.db, client, opener=opener, today=self.clock.today).search('凡尔赛 梗 意思')
        self.assertTrue(opener.requests[0].full_url.startswith('https://zh.wikipedia.org/w/api.php'))
        self.assertNotIn('synthetic-key', str(opener.requests[0].headers))                          # 密钥不发给别家
        self.assertEqual(hits[0].site, '维基百科'); self.assertIn('炫耀', hits[0].summary)


class LearnerTests(Base):
    def learner(self, hits=()):
        fake = mock.Mock(); fake.search.return_value = list(hits)
        return CommunityLearner(self.db, clock=self.clock, search=lambda client: fake), fake

    def seed(self, learner, term, speakers=('1', '2')):
        for n, sp in enumerate(speakers): learner.saw(term, GROUP, sp, f'{term} 用法{n}')

    def test_same_meaning_with_and_without_context_is_not_jargon(self):
        learner, web = self.learner()
        self.seed(learner, '吃饭')
        client = Client(**{'只看这个词本身': {'in_context': '吃饭', 'plain': '吃饭', 'same': True, 'tone': '中性'}})
        self.assertEqual(learner.infer('吃饭', client, GROUP), 'plain_word')
        self.assertEqual(learner.lexicon.senses('吃饭'), []); self.assertFalse(learner.lexicon.info('吃饭')['is_jargon'])
        web.search.assert_not_called()

    def test_jargon_learned_from_web_with_source(self):
        learner, web = self.learner([Hit('急急国王是什么梗', 'https://example.org/jj', '形容一个人非常着急，带调侃', '某百科')])
        self.seed(learner, '急急国王')
        client = Client(**{'只看这个词本身': {'in_context': '催别人别急', 'plain': '一个国王', 'same': False, 'tone': '调侃', 'circle': '网络通用'},
                           '网上搜': {'meaning': '形容很着急的人', 'origin': '游戏直播', 'tone': '调侃', 'circle': '网络通用', 'source': '1', 'change': 'same'}})
        self.assertEqual(learner.infer('急急国王', client, GROUP), 'web')
        web.search.assert_called_once_with('急急国王 梗 意思')
        senses = learner.lexicon.senses('急急国王')
        self.assertEqual(len(senses), 1); self.assertEqual(senses[0]['source_url'], 'https://example.org/jj')
        self.assertIn('出处：游戏直播', senses[0]['usage'])

    def test_private_term_is_not_searched_and_kept_as_group_usage(self):
        learner, web = self.learner([Hit('x', 'https://e.org', 'y')])
        self.seed(learner, '小糖人', speakers=('1',))                                               # 只有一个人说过：不外传
        learner.saw('小糖人', GROUP, '1', '小糖人又来了')
        client = Client(**{'只看这个词本身': {'in_context': '群里对机器人的戏称', 'plain': '糖做的小人', 'same': False, 'tone': '调侃'}})
        self.assertEqual(learner.infer('小糖人', client, GROUP), 'observed')
        web.search.assert_not_called()
        sense = learner.lexicon.senses('小糖人')[0]
        self.assertEqual((sense['origin'], sense['scope']), ('observed', 'group:'+GROUP))

    def test_meaning_change_is_recorded_as_evolution(self):
        learner, _ = self.learner()
        learner.lexicon.learn('摆烂', '放弃努力', origin='observed', scope='group:'+GROUP)
        self.seed(learner, '摆烂', speakers=('1',))
        client = Client(**{'只看这个词本身': {'in_context': '故意躺平给老板看', 'plain': '摆放烂东西', 'same': False}, '只回一个词': 'changed'})
        learner.infer('摆烂', client, GROUP)
        senses = learner.lexicon.senses('摆烂')
        self.assertEqual([s['valid_to'] is None for s in senses], [False, True])
        self.assertEqual({s['scope'] for s in senses}, {'group:'+GROUP})

    def test_two_groups_same_term_different_meanings_never_leak(self):
        """Codex 审查 9217558 P2：第二个群出现同一个词后，群内推断义被升成通用义，第三个群和桌面也看得到。"""
        class ByContext(Client):
            def generate_reply_stream(self, history, prompt, cancellation=None):
                self.calls.append(prompt)
                here = 'A群：合成的内部项目' if 'A群' in history[-1].content else 'B群：合成的秘密代号'
                yield json.dumps({'in_context': here, 'plain': '一种鲸鱼', 'same': False}, ensure_ascii=False)
        learner, web = self.learner()
        self.db.set_setting('community_web', False)
        A, B, C = 'qq:group:1001', 'qq:group:1002', 'qq:group:1003'
        for n in range(2): learner.saw('蓝鲸行动', A, str(n), f'A群上下文{n}')
        learner.infer('蓝鲸行动', ByContext(), A)
        for n in range(3): learner.saw('蓝鲸行动', B, str(10+n), f'B群上下文{n}')
        learner.infer('蓝鲸行动', ByContext(), B)
        scopes = {s['scope']: s['meaning'] for s in learner.lexicon.senses('蓝鲸行动')}
        self.assertEqual(scopes, {'group:'+A: 'A群：合成的内部项目', 'group:'+B: 'B群：合成的秘密代号'})   # 没有通用义
        lex = learner.lexicon
        self.assertIn('A群', lex.prompt('蓝鲸行动启动', 'group:'+A)); self.assertNotIn('B群', lex.prompt('蓝鲸行动启动', 'group:'+A))
        self.assertIn('B群', lex.prompt('蓝鲸行动启动', 'group:'+B))
        self.assertEqual(lex.prompt('蓝鲸行动启动', 'group:'+C), ''); self.assertEqual(lex.prompt('蓝鲸行动启动', 'chat:desktop'), '')
        web.search.assert_not_called()

    def test_trending_once_a_week_with_sources(self):
        learner, web = self.learner([Hit('2026 热梗盘点', 'https://example.org/hot', '"哈基米"指可爱的猫……', '某站')])
        client = Client(**{'最近的网络热梗': {'terms': [{'term': '哈基米', 'meaning': '对猫的爱称', 'tone': '调侃', 'source': '1'}]}})
        self.assertEqual(learner.trending(client), 1)
        self.assertEqual(learner.trending(client), 0)                                              # 一周内不重复
        self.assertEqual(learner.lexicon.senses('哈基米')[0]['origin'], 'trend')
        self.assertEqual(learner.recent_trends()[0]['term'], '哈基米')


class AnalyzerAndLoreTests(Base):
    def test_analyzer_collects_terms_and_group_lore_then_infers_due_terms(self):
        a, b = self.person('111'), self.person('222')
        for pid, sender in ((a, 'qq:111'), (b, 'qq:222')): remember_sender(self.db, CID, sender, pid)
        for n in range(3):
            self.db.add_message('user', f'甲#1：急急国王{n}', CID, channel=GROUP, sender='qq:111')
            self.db.add_message('user', f'乙#2：你才急急国王{n}', CID, channel=GROUP, sender='qq:222')
        analysis = {'events': [], 'notes': [],
                    'terms': [{'term': '急急国王', 'here': '催人别急', 'who': 'P1'}, {'term': '急急国王', 'here': '回怼', 'who': 'P2'},
                              {'term': '1428903832', 'here': '号码', 'who': 'P1'}],
                    'lore': [{'kind': 'inside_joke', 'text': '大家互相叫对方急急国王', 'confidence': 0.9},
                             {'kind': 'event', 'text': '手机号13812345678', 'confidence': 0.9}]}
        client = Client(**{'整理人际关系': analysis,
                           '只看这个词本身': {'in_context': '催人别急', 'plain': '一个国王', 'same': False, 'tone': '调侃'}})
        fake = mock.Mock(); fake.search.return_value = []
        self.db.set_setting('community_trend_at', self.clock())                                    # 这次不顺带刷新热梗
        with mock.patch('aicompanion.community.learner.CommunityLearner.web', lambda self, client: fake):
            RelationshipAnalyzer(self.db, self.rel).maybe_run(CID, GROUP, client, background=False)
        lex = Lexicon(self.db)
        self.assertIsNone(lex.info('1428903832'))                                                  # 号码不收
        self.assertTrue(lex.senses('急急国王'))                                                      # 两个人用过：推断了
        self.assertTrue(any('只看这个词本身' in p for p in client.calls))
        fake.search.assert_called_once()                                                          # 两个人用过：允许查
        lore = GroupLore(self.db).items(CID, GROUP)
        self.assertEqual([r['text'] for r in lore], ['大家互相叫对方急急国王'])                       # 敏感的不记
        self.assertIn('急急国王', GroupLore(self.db).prompt(CID, GROUP))

    def test_lore_reinforces_and_fades(self):
        lore = GroupLore(self.db, clock=self.clock)
        self.assertEqual(lore.add(CID, GROUP, 'event', '一起熬夜修好了群机器人', 0.9), 'added')
        self.assertEqual(lore.add(CID, GROUP, 'event', '一起熬夜修好了群机器人', 0.9), 'reinforced')
        self.assertEqual(lore.add(CID, GROUP, 'secret', '随便', 0.9), 'rejected')
        self.clock.advance(days=400)
        self.assertEqual(lore.prompt(CID, GROUP), '')                                              # 太久远，淡了


class RetrievalTests(BridgeFixture):
    def test_known_memes_and_group_lore_reach_the_prompt_qq_and_desktop(self):
        Lexicon(self.db).learn('急急国王', '形容很着急的人', origin='web', source_name='某百科', source_url='https://example.org/jj', circle='网络通用')
        GroupLore(self.db).add(CID, GROUP, 'inside_joke', '大家互相叫对方急急国王', 0.9)
        self.ask('20002', '你个急急国王', group='30003', name='tangle')
        prompt = self.model.prompts[-1]
        self.assertIn('形容很着急的人', prompt); self.assertIn('这个群的共同记忆', prompt); self.assertIn('会玩梗', prompt)
        self.desktop('今天又当急急国王了', '哈哈')
        self.assertIn('形容很着急的人', self.model.prompts[-1]); self.assertNotIn('这个群的共同记忆', self.model.prompts[-1])

    def test_chime_can_bring_up_a_related_fresh_topic(self):
        Lexicon(self.db, clock=self.clock).learn('哈基米', '对猫的爱称', origin='trend')
        self.service.clock = self.clock
        text = self.service.community_topics(GROUP, '我家猫今天又拆家了，哈基米真能闹')
        self.assertIn('哈基米', text); self.assertIn('别播报', text)
        self.assertEqual(self.service.community_topics(GROUP, '今天写代码'), '')


class SwitchAndScheduleTests(Base):
    def batch(self, client, n=6):
        a = self.person('111'); remember_sender(self.db, CID, 'qq:111', a)
        for i in range(n): self.db.add_message('user', f'甲#1：第{i}句', CID, channel=GROUP, sender='qq:111')
        return RelationshipAnalyzer(self.db, self.rel).maybe_run(CID, GROUP, client, background=False)

    def test_learning_switch_stops_terms_and_lore(self):
        """Codex 审查 9217558 P2：关掉"从群聊里学梗"后仍写入群梗 / 共同记忆。"""
        self.db.set_setting('community_learning', False); self.db.set_setting('community_web', False)
        client = Client(**{'整理人际关系': {'events': [], 'notes': [], 'terms': [{'term': '急急国王', 'here': '催人', 'who': 'P1'}],
                                            'lore': [{'kind': 'inside_joke', 'text': '大家互相叫对方急急国王', 'confidence': 0.9}]}})
        self.assertTrue(self.batch(client))
        self.assertIsNone(Lexicon(self.db).info('急急国王'))
        self.assertEqual(GroupLore(self.db).items(CID, GROUP), []); self.assertEqual(GroupLore(self.db).prompt(CID, GROUP), '')

    def test_weekly_trend_check_runs_from_the_real_analysis_entry(self):
        """Codex 审查 9217558 P2：没有新词跨阈值时，周更热梗永远不会被检查。"""
        fake = mock.Mock(); fake.search.return_value = [Hit('热梗', 'https://example.org/hot', '"哈基米"指猫', '某站')]
        client = Client(**{'整理人际关系': {'events': [], 'notes': [], 'terms': [], 'lore': []},
                           '最近的网络热梗': {'terms': [{'term': '哈基米', 'meaning': '对猫的爱称', 'source': '1'}]}})
        with mock.patch('aicompanion.community.learner.CommunityLearner.web', lambda self, client: fake):
            self.batch(client)
            self.assertEqual(fake.search.call_count, 1); self.assertTrue(Lexicon(self.db).senses('哈基米'))
            self.clock.advance(seconds=3600); self.batch(client)
            self.assertEqual(fake.search.call_count, 1)                                            # 一周内不重复
            self.clock.advance(days=8); self.db.set_setting('community_web', False); self.batch(client)
            self.assertEqual(fake.search.call_count, 1)                                            # 关了联网：不查，也不占掉这周
            self.assertLess(float(self.db.get_setting('community_trend_at')), self.clock()-7*86400)
            self.db.set_setting('community_web', True); self.db.set_setting('community_learning', False); self.batch(client)
            self.assertEqual(fake.search.call_count, 1)                                            # 关了学习：不查
            self.db.set_setting('community_learning', True); self.batch(client)
            self.assertEqual(fake.search.call_count, 2)


class MigrationTests(unittest.TestCase):
    def test_v7_database_upgrades_to_v8_with_backup(self):
        import sqlite3, tempfile
        from contextlib import closing
        from pathlib import Path
        from aicompanion.storage.database import Database
        from aicompanion.storage.schema import SCHEMA_VERSION
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'old.sqlite3'; Database(path).initialize()
            with closing(sqlite3.connect(path)) as c:
                for table in ('group_lore', 'lexicon_sightings', 'lexicon_senses', 'lexicon_terms'): c.execute(f'DROP TABLE {table}')
                c.execute('PRAGMA user_version=7'); c.commit()
            db = Database(path); db.initialize()
            self.assertEqual(len(list(Path(tmp).glob('old.backup-*.sqlite3'))), 1)
            with closing(sqlite3.connect(path)) as c:
                self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(Lexicon(db).learn('急急国王', '很着急的人', origin='web'), 'added')


class PluginContextTests(unittest.TestCase):
    def test_mentions_at_all_and_reply_target(self):
        import importlib.util, sys, types
        from test_astrbot_plugin import PluginTests
        PluginTests.setUpClass(); case = PluginTests('test_reply_and_stop'); case.setUp()
        plugin = case.plugin()
        At = type('At', (), {}); AtAll = type('AtAll', (), {}); Reply = type('Reply', (), {})
        def at(qq): part = At(); part.qq = qq; return part
        reply = Reply(); reply.sender_id = '2776148574'; reply.sender_nickname = 'hhhh'
        event = types.SimpleNamespace(get_self_id=lambda: '2776148574', get_messages=lambda: [at('2776148574'), at('30003'), AtAll(), reply])
        self.assertEqual(plugin.message_context(event), {'mentions': ['30003'], 'at_all': True, 'reply_to': {'self': True, 'name': 'hhhh'}})


if __name__ == '__main__':
    unittest.main()
