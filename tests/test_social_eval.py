"""T20 评测工具自检：场景文件格式、离线流程（假模型、固定搜索）、真实搜索验收脚本的流程（假搜索）。不联网。"""
import argparse, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import social_eval          # noqa: E402
import verify_web_search    # noqa: E402
from aicompanion.community.search import Hit   # noqa: E402
from test_relationship import Base

SCENARIOS = ROOT/'tools'/'social_scenarios.json'


class ScenarioFileTests(unittest.TestCase):
    def test_scenarios_are_well_formed_and_cover_the_contrasts(self):
        data = social_eval.load_scenarios(SCENARIOS); items = data['scenarios']
        self.assertGreaterEqual(len(items), 30)
        kinds = {s['kind'] for s in items}
        for kind in ('attack_self', 'sarcasm_self', 'tease_friend', 'quote', 'third_party', 'meme_unknown', 'meme_known', 'favor_outsider'):
            self.assertIn(kind, kinds)
        for s in items:
            self.assertTrue(s['message'].strip()); self.assertEqual(set(s['relation']), {'aff', 'fam', 'trust'})
            for key, allowed in s['expect'].items():
                self.assertIn(key, ('at', 'voice', 'tone')); self.assertTrue(allowed)
            self.assertIn(s['expect_lookup'], (True, False, None))
        # 同一句话、不同关系的对照
        by = {s['id']: s for s in items}
        self.assertEqual(by['tease-friend-sb']['message'], by['same-words-stranger']['message'])
        self.assertNotEqual(by['tease-friend-sb']['expect']['tone'], by['same-words-stranger']['expect']['tone'])


class OfflineRunTests(Base):
    def test_offline_run_goes_through_the_real_bridge_and_reports(self):
        with tempfile.TemporaryDirectory() as out:
            args = argparse.Namespace(scenarios=str(SCENARIOS), rounds=1, only=None, out=out, dotenv=None)
            report = social_eval.run(args, social_eval.ScriptedClient())
            self.assertEqual(report['summary']['total'], len(social_eval.load_scenarios(SCENARIOS)['scenarios']))
            self.assertEqual(report['summary']['auto_pass'], report['summary']['total'], [r['wrong'] for r in report['rows'] if r['wrong']])
            rows = {r['id']: r for r in report['rows']}
            self.assertTrue(rows['meme-unknown-wants-in']['looked']); self.assertFalse(rows['meme-known-no-search']['looked'])
            self.assertEqual(rows['quote-he-said']['event'], 'report'); self.assertTrue(rows['third-at-other']['heard_only'])   # 骂的是别人、没 @ 她：旁听
            self.assertEqual(rows['attack-stranger-direct']['event'], 'insult')
            self.assertLess(rows['attack-stranger-direct']['affinity_change'], 0)
            text = (Path(out)/'social-eval.md').read_text(encoding='utf-8')
            self.assertIn('人工：像真人吗', text); self.assertIn('same-words-stranger', text)

    def test_checker_flags_wrong_judgement_and_assistant_tone(self):
        scenario = dict(expect={'tone': ['tease', 'joke']}, expect_lookup=False, forbid=['好的'])
        wrong = social_eval.check(scenario, {'tone': 'attack'}, True, '好的，以下是查询结果 https://example.org')
        self.assertEqual(len(wrong), 5)   # 语气不对、不该查却查了、"好的"、"以下是"、链接


class VerifySearchTests(Base):
    def test_search_acceptance_script_reports_without_network(self):
        class Web:
            timeout = 20
            def backend(self): return 'deepseek'
            def used_today(self): return {'search': 2, 'fetch': 0}
            def fetch(self, url): return ''
            def search(self, query, count=5):
                return [Hit('哈基米', 'https://example.org/h', '哈基米原是日语蜂蜜的音译，后来在 B 站被用来指可爱的猫，流行于各种猫咪视频。', '某百科')] if '哈基米' in query else []
        report = verify_web_search.run(argparse.Namespace(terms='哈基米,不存在的词', out=None, dotenv=None), client=object(), web=Web())
        self.assertEqual((report['backend'], report['terms'], report['found']), ('deepseek', 2, 1)); self.assertTrue(report['passed'])
        self.assertNotIn('key', str(report).lower())


if __name__ == '__main__':
    unittest.main()
