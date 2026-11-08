"""验收脚本本身的离线自检：脚本化假模型、仓库自带图，不联网、不用真实模型。"""
import sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import expression_eval   # noqa: E402


class EvalHarnessTests(unittest.TestCase):
    def test_scenarios_cover_required_groups(self):
        scenarios = expression_eval.load_scenarios(ROOT/'tools'/'expression_scenarios.json')
        self.assertGreaterEqual(len(scenarios), 30)
        groups = {s['group'] for s in scenarios}
        self.assertTrue({'情绪表达', '社交表达', '主动表达', '不适合发表情'} <= groups)
        self.assertEqual(len({s['id'] for s in scenarios}), len(scenarios))
        self.assertTrue(all(s['expect'] in ('expressive', 'either', 'text') for s in scenarios))
        steps = [step for s in scenarios for step in (s.get('chain') or [s])]
        self.assertTrue(all(step.get('query') or step.get('proactive') for step in steps))
        self.assertTrue(all(step['expect'] in ('expressive', 'either', 'text') for step in steps))

    def test_chain_runs_in_one_conversation(self):
        with tempfile.TemporaryDirectory() as out:
            client = expression_eval.ScriptedClient()
            args = expression_eval.argparse.Namespace(pack=None, scenarios=str(ROOT/'tools'/'expression_scenarios.json'),
                                                      rounds=1, only='chain-evening', out=out)
            report = expression_eval.run(args, client)
            self.assertEqual([r['id'] for r in report['rows']], [f'chain-evening#{n}' for n in range(1, 6)])
            self.assertEqual(client.calls, 5)

    def test_offline_run_uses_one_request_per_scenario_and_writes_report(self):
        with tempfile.TemporaryDirectory() as out:
            client = expression_eval.ScriptedClient()
            args = expression_eval.argparse.Namespace(pack=None, scenarios=str(ROOT/'tools'/'expression_scenarios.json'),
                                                      rounds=1, only='emo-happy,txt-tech,pro-mood,grp-joke', out=out)
            report = expression_eval.run(args, client)
            self.assertEqual(client.calls, 4)
            by_id = {r['id']: r for r in report['rows']}
            self.assertTrue(by_id['emo-happy']['sticker']); self.assertFalse(by_id['txt-tech']['sticker'])
            self.assertEqual(by_id['emo-happy']['stage'], 'OK_displayed')
            self.assertTrue(by_id['pro-mood']['sticker'])
            self.assertTrue((Path(out)/'expression-eval.md').is_file())
            self.assertFalse(report['real_model'])


if __name__ == '__main__':
    unittest.main()
