"""合成办事记录、离线工具和可控时钟。"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from aicompanion.agent import attachments
from aicompanion.agent.checkpoints import Journey, current_artifacts, history
from aicompanion.agent.tools_server import Task, ToolBox
from aicompanion.storage.database import Database

CID = 'deepseek-whale'


class CheckpointTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.workspace = self.root/'work'
        self.workspace.mkdir()
        self.db = Database(self.root/'synthetic.sqlite3')
        self.db.initialize()
        self.run = self.db.add_agent_run(CID, '整理项目', workspace=self.workspace)
        self.exec = Mock(return_value='成功')
        self.box = ToolBox(trash=self.root/'trash', run_command=self.exec)
        self.box.approve = Mock(return_value='once')
        self.box.reserve_effect = self.db.reserve_agent_effect
        self.start(False)

    def start(self, resuming):
        task = self.box.task = Task(run_id=self.run, allowed=[self.workspace], resuming=resuming)
        def pause(reason):
            self.db.pause_agent_work(self.run, (), reason=reason)
            task.cancelled.set()
        self.journey = task.journey = Journey(self.db, self.run, task.allowed, [], pause)

    def call(self, tool, **args):
        text, error = self.box.call(tool, args)
        self.assertFalse(error, text)
        return text

    def stage(self, action, **kwargs):
        return self.call('task_stage', action=action, summary='整理项目清单', **kwargs)

    def test_checkpoint_plan_resume_and_reserved_effects(self):
        self.stage('plan', steps=['列清单', '核对'])
        path = self.workspace/'清单.txt'
        self.call('write_file', path=str(path), content='abc')
        command = dict(command='Set-Content x.txt ok', cwd=str(self.workspace), reason='生成索引')
        self.call('run_powershell', **command)
        rows = self.db.agent_checkpoints(self.run)
        self.assertTrue(all(row['created_at'] for row in rows))
        expected = current_artifacts(rows)[path]
        self.assertEqual((expected['size'], expected['sha256']), (3, hashlib.sha256(b'abc').hexdigest()))
        self.call('task_stage', action='wait', summary='用户补充来源')
        self.db = Database(self.db.database_path)
        self.db.initialize()
        saved = self.db.agent_run(self.run)
        self.assertEqual((saved['goal'], saved['workspace'], saved['status']), ('整理项目', str(self.workspace), 'paused'))
        self.assertEqual(json.loads(saved['plan']), ['列清单', '核对'])
        context = history(self.db, self.run)
        for word in ('用户补充来源', '列清单', expected['sha256'], '生成索引'):
            self.assertIn(word, context)
        self.assertIsNone(self.db.resume_agent_run(self.run, CID, saved['revision']+1))
        self.assertEqual(self.db.resume_agent_run(self.run, CID, saved['revision']), 0)
        self.start(True)
        for name, args in [('write_file',dict(path=str(path), content='abc')), ('run_powershell',command)]:
            text, failed = self.box.call(name, args)
            self.assertTrue(failed)
            self.assertIn('已经尝试过', text)
        self.exec.assert_called_once()
        self.stage('deliver', files=[str(path)])
        self.journey.verify()

    def test_verification_compares_to_write_checkpoint_and_opens_format(self):
        path = self.workspace/'清单.json'
        self.call('write_file', path=str(path), content='{}')
        self.stage('deliver', files=[str(path)])
        path.write_text('[]')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            self.journey.verify()
        self.start(True)
        self.call('write_file', path=str(path), content='损坏')
        self.stage('deliver', files=[str(path)])
        with self.assertRaisesRegex(ValueError, '清单.json'):
            self.journey.verify()
        self.start(True)
        self.stage('deliver', files=[str(path)])
        path.unlink()
        with self.assertRaisesRegex(ValueError, '清单.json'):
            self.journey.verify()

    def test_command_outputs_need_independent_stage_and_unfinished_calls_block_delivery(self):
        path = self.workspace/'外部产物.txt'
        self.assertTrue(self.box.call('task_stage', dict(action='deliver', summary='好了', files=[str(path)]))[1])
        with self.assertRaisesRegex(ValueError, '缺少阶段检查点'):
            self.journey.verify()
        self.start(True)
        path.write_text('data')
        self.stage('checkpoint', files=[str(path)])
        self.stage('deliver', files=[str(path)])
        self.journey.enter()
        with self.assertRaisesRegex(ValueError, '工具在运行'):
            self.journey.verify()
        self.journey.leave()

    def test_command_updates_existing_checkpoint_and_checks_paths_before_execution(self):
        path = self.workspace/'命令.txt'
        self.call('write_file', path=str(path), content='初稿')
        def produce(*args):
            path.write_bytes(b'final')
            return '成功'
        self.exec.side_effect = produce
        args = dict(command='Set-Content x.txt final', cwd=str(self.workspace))
        text, rejected = self.box.call('run_powershell', dict(args, outputs=[str(self.root/'其他.txt')]))
        self.assertTrue(rejected, text)
        self.exec.assert_not_called()
        self.call('run_powershell', **args, outputs=[str(path)])
        self.stage('deliver', files=[str(path)])
        self.journey.verify()
        final = json.loads(self.db.agent_checkpoints(self.run)[-1]['artifacts'])
        self.assertEqual(final[0]['sha256'], hashlib.sha256(b'final').hexdigest())

    def test_approval_timeout_manual_pause_and_late_completion(self):
        from aicompanion.agent.approval import Approvals
        from aicompanion.ui.agent_session import AgentSession
        approvals = Approvals(clock=lambda:0)
        session = Mock()
        session.box = self.box
        session.auto_approve = None
        session.tasks.approvals = approvals
        session.tasks.current_peer.return_value = None
        session._send_request.side_effect = lambda req: approvals.answer(req.code, 'timeout')
        self.assertEqual(AgentSession._request(session, 'write', '写第一份', '工作区外', None), ('timeout', ''))
        row = self.db.agent_run(self.run)
        self.assertIn('审批超时', row['pause_reason'])
        self.assertEqual(self.db.resume_agent_run(self.run, CID, row['revision']), 0)
        self.start(True)
        AgentSession.stop(session)
        self.db.finish_agent_run(self.run, 'done', '迟来的结果')
        self.assertEqual(self.db.agent_run(self.run)['pause_reason'], '主人手动暂停')
        self.assertEqual(self.db.agent_run(self.run)['result'], '')

    def test_public_plan_and_wait_do_not_store_thinking(self):
        self.call('task_stage', action='plan', summary='安排', steps=['<think>隐藏</think>读清单'])
        self.call('task_stage', action='wait', summary='还缺数量<think>隐藏')
        self.assertNotIn('隐藏', history(self.db, self.run))
        self.assertIn('读清单', history(self.db, self.run))

    def test_abandon_moves_only_copies_once(self):
        original = self.root/'原件.txt'
        original.write_bytes(b'data')
        run = self.db.add_agent_run(CID, '办事', workspace=self.workspace, attachments=attachments.selection([original]))
        rows = self.db.agent_attachments(run)
        attachments.copy_batch(self.db, rows, [])
        self.db.pause_agent_work(run, (), reason='手动')
        revision = self.db.agent_run(run)['revision']
        self.assertFalse(self.db.abandon_agent_run(run, CID, revision+1))
        self.assertTrue(Path(rows[0]['copy']).exists())
        self.assertTrue(self.db.abandon_agent_run(run, CID, revision))
        self.assertFalse(self.db.abandon_agent_run(run, CID, revision))
        attachments.recycle_finished(self.db, self.root/'trash')
        self.assertFalse(Path(rows[0]['copy']).exists())
        self.assertEqual(Path(self.db.agent_attachments(run)[0]['recycled']).read_bytes(), original.read_bytes())

    def test_v20_migration_preserves_old_records_and_marks_interrupted(self):
        old = self.root/'old.sqlite3'
        with closing(sqlite3.connect(old)) as c:
            c.executescript("""CREATE TABLE agent_runs(id INTEGER PRIMARY KEY, character_id TEXT, goal TEXT, status TEXT,
                               started_at TEXT, ended_at TEXT, steps INTEGER, tokens INTEGER, cost REAL, result TEXT, error TEXT);
                               INSERT INTO agent_runs VALUES(1,'deepseek-whale','旧任务','running','2026-10-10',NULL,3,9,1.2,'','');
                               INSERT INTO agent_runs VALUES(2,'deepseek-whale','已交付','done','2026-10-09','2026-10-10',3,9,1.2,'旧结果','');
                               PRAGMA user_version=20;""")
        db = Database(old)
        db.initialize()
        self.assertEqual((db.agent_run(1)['goal'], db.agent_run(1)['pause_reason']), ('旧任务', '程序中断'))
        self.assertEqual((db.agent_run(2)['status'], db.agent_run(2)['result']), ('done', '旧结果'))
        self.assertEqual(db.agent_run(1)['steps'], 3)
