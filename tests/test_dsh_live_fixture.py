"""真实模型验收器的离线保护检查，不启动 DSH、不调用 API。"""
import importlib.util
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


class LiveFixtureTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
        spec = importlib.util.spec_from_file_location('safe_dsh_live', Path(__file__).resolve().parents[1]/'tools/test_dsh_live.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_fixture_stays_in_empty_test_directory(self):
        other = self.root/'existing.txt'; other.write_text('leave unchanged')
        fixture = self.module.make_fixture(self.root/'fixture')
        for name in ('workspace', 'outside', 'original', 'delete', 'secret', 'document', 'drafts', 'trash', 'dsh_home'):
            self.assertTrue(getattr(fixture, name).is_relative_to(fixture.root))
        self.assertEqual(len(list(fixture.drafts.glob('*.md'))), 6)
        with self.assertRaisesRegex(ValueError, '新建空目录'):
            self.module.make_fixture(fixture.root)
        self.assertEqual(other.read_text(), 'leave unchanged')

    def test_missing_key_reports_skip_without_starting_runtime(self):
        import check_dsh
        for number, module in enumerate((self.module, check_dsh)):
            out = self.root/f'report-{number}'
            with self.subTest(script=module.__name__), patch.object(module, 'OUT', out), \
                    patch.object(module, 'credentials', return_value=('', 'https://example.invalid')), \
                    patch.object(module, 'DshAgent') as agent, \
                    patch.object(module, 'McpToolServer') as server, \
                    patch('dotenv.load_dotenv', side_effect=AssertionError('不可读 .env')), redirect_stdout(StringIO()):
                self.assertEqual(module.main(), 2)
            agent.assert_not_called(); server.assert_not_called()
            report = json.loads((out/'report.json').read_text(encoding='utf-8'))
            self.assertTrue(report['skipped'])
            self.assertFalse(report['passed'])
            self.assertEqual(report['cases'], [])

    def test_environment_credentials_and_redaction(self):
        self.assertEqual(self.module.credentials({}), ('', 'https://api.deepseek.com/v1'))
        self.assertEqual(self.module.credentials({'DEEPSEEK_API_KEY': 'fake-key', 'LLM_BASE_URL': 'https://example.invalid'}),
                         ('fake-key', 'https://example.invalid'))
        self.assertNotIn('fake-key', self.module.redact('message contains fake-key', 'fake-key'))
