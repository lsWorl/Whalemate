"""真实模型验收器的离线保护检查，不启动 DSH、不调用 API。"""
import importlib.util
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
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
        fixture = self.module.make_fixture(self.root/'fixture', dsh_home=self.root/'runtime')
        for name in ('workspace', 'outside', 'original', 'delete', 'secret', 'document', 'drafts', 'trash'):
            self.assertTrue(getattr(fixture, name).is_relative_to(fixture.root))
        self.assertEqual(len(list(fixture.drafts.glob('*.md'))), 6)
        with self.assertRaisesRegex(ValueError, '新建空目录'):
            self.module.make_fixture(fixture.root, dsh_home=fixture.dsh_home)
        self.assertEqual(other.read_text(), 'leave unchanged')

    def test_dsh_home_cleanup_is_limited_to_fresh_directory_even_on_failure(self):
        parent = self.root/'low-root'; parent.mkdir()
        sibling = parent/'existing-session'; sibling.mkdir()
        marker = sibling/'keep.txt'; marker.write_text('keep')
        with patch('aicompanion.agent.sandbox.low_root', return_value=parent):
            with self.assertRaisesRegex(RuntimeError, 'startup failed'):
                with self.module.temporary_dsh_home() as home:
                    self.assertEqual(home.parent, parent)
                    self.assertEqual(home.drive.lower(), parent.drive.lower())
                    (home/'new-session.txt').write_text('synthetic session')
                    raise RuntimeError('startup failed')
        self.assertFalse(home.exists())
        self.assertEqual(marker.read_text(), 'keep')

    def test_both_live_scripts_use_low_root_drive_for_runtime(self):
        import check_dsh
        from aicompanion.agent import dsh_backend
        parent = self.root/'low-root'
        for number, module in enumerate((self.module, check_dsh)):
            homes = []
            def run(*args, **kwargs):
                home = dsh_backend.dsh_home()
                self.assertEqual(home.parent, parent)
                self.assertEqual(home.drive.lower(), parent.drive.lower())
                self.assertTrue(home.is_dir())
                self.assertFalse(home.is_relative_to(out))
                homes.append(home)
                return SimpleNamespace(status='done', text='模拟响应',
                    usage=SimpleNamespace(prompt=0, completion=0, cached=0))
            out = self.root/f'run-{number}'
            with self.subTest(script=module.__name__), \
                    patch.object(module, 'OUT', out), \
                    patch.object(module, 'credentials', return_value=('synthetic-key', 'http://127.0.0.1:9')), \
                    patch.object(module, 'problem', return_value=''), \
                    patch.object(module, 'dependency_problem', return_value=''), \
                    patch('dsh_test_support.fixture_parent', return_value=self.root/f'isolated-{number}'), \
                    patch('aicompanion.agent.sandbox.low_root', return_value=parent), \
                    patch.object(module, 'DshAgent') as agent, \
                    patch.object(module, 'McpToolServer'), redirect_stdout(StringIO()):
                agent.return_value.run.side_effect = run
                # 不模拟模型写文件，所以场景断言失败；这里只检查实际入口选择的会话目录。
                self.assertEqual(module.main(), 1)
                agent.return_value.close.assert_called_once()
            self.assertEqual(len(homes), 8 if module is self.module else 1)
            self.assertTrue(all(not home.exists() for home in homes))

    def test_fixture_ancestors_are_instruction_free_and_existing_data_survives(self):
        import dsh_test_support as support
        parent = self.root/'isolated'; parent.mkdir()
        sibling = parent/'keep.txt'; sibling.write_text('keep')
        with patch.object(support, 'fixture_parent', return_value=parent):
            with support.temporary_fixture_root() as fixture:
                support.require_instruction_free(fixture)
                for ancestor in (fixture, *fixture.parents):
                    for name in ('AGENTS.md', 'CLAUDE.md'):
                        self.assertFalse((ancestor/name).is_file())
                self.assertEqual(fixture.parent, parent)
                (fixture/'test.txt').write_text('synthetic')
        self.assertFalse(fixture.exists())
        self.assertEqual(sibling.read_text(), 'keep')

    def test_fixture_rejects_project_instructions_in_any_ancestor(self):
        import dsh_test_support as support
        for name in ('AGENTS.md', 'CLAUDE.md'):
            parent = self.root/name.replace('.', '-')
            parent.mkdir()
            (parent/name).write_text('合成开发规则')
            with patch.object(support, 'fixture_parent', return_value=parent/'child'):
                with self.assertRaisesRegex(ValueError, name):
                    with support.temporary_fixture_root():
                        self.fail('不能进入带项目规则的验收工作区')
            self.assertFalse((parent/'child').exists())

    def test_copy_comparison_only_tolerates_trailing_line_endings(self):
        from dsh_test_support import same_text_except_final_newlines as same
        for ending in ('', '\n', '\r\n', '\n\n'):
            self.assertTrue(same('原文', '原文'+ending))
        for changed in (' 原文', '原文 ', '改文', '原\n文'):
            self.assertFalse(same('原文', changed))

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
        self.assertEqual(self.module.credentials({}), ('', ''))
        # 只认 DeepSeek 自己的变量，不再读 LLM_*（计划书 v4：模型配置只来自设置页）
        self.assertEqual(self.module.credentials({'LLM_API_KEY': 'old', 'DEEPSEEK_API_KEY': 'fake-key', 'DEEPSEEK_BASE_URL': 'https://example.invalid'}),
                         ('fake-key', 'https://example.invalid'))
        target = self.module.target('fake-key', '')
        self.assertEqual((target.route, dict(target.env)), ('deepseek-official', {'DEEPSEEK_API_KEY': 'fake-key'}))
        self.assertNotIn('fake-key', self.module.redact('message contains fake-key', 'fake-key'))
