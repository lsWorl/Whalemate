"""从聊天面板管理历史、记忆和试聊。所有记录和模型回复均为合成数据。"""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from aicompanion.domain.cancellation import CancellationToken
from aicompanion.domain.character import DEFAULT_CHARACTER
from aicompanion.services.memory_service import MemoryService
from aicompanion.services.persona_service import PersonaService
from aicompanion.storage.database import Database
from aicompanion.ui.chat_records import ChatRecords
from aicompanion.ui.persona_trial import PersonaTrial
from aicompanion.usage.meter import Quota

CID = DEFAULT_CHARACTER.id


class RecordsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Database(Path(directory.name) / 'test.sqlite3')
        self.db.initialize()
        self.host = QWidget()
        self.host.database = self.db
        self.host.controller = SimpleNamespace(character=DEFAULT_CHARACTER)
        self.host.quota = Quota(self.db)
        self.host.interrupt = Mock()
        self.host.items = {}
        self.host._load_history = Mock()
        self.host.tasks = set()
        self.host._task_finished = self.host.tasks.discard
        self.addCleanup(self.host.deleteLater)

    def test_selected_ids_only_and_no_late_reply_resurrection(self):
        first = self.db.add_message('user', '重复的文字')
        second = self.db.add_message('user', '重复的文字')
        group = self.db.add_message('user', '群记录', channel='qq:group:12')
        self.db.add_character(replace(DEFAULT_CHARACTER, id='other'))
        other = self.db.add_message('user', '另一角色', 'other')
        self.db.start_turn('turn', CID)
        self.db.finish_turn('turn', CID, '回答', 'complete')
        reply = self.db.recent_messages()[-1]['id']
        self.db.upsert_memory('待删记忆', fact_key='test', source_ids=[second])
        self.db.upsert_memory('保留记忆', fact_key='keep', source_ids=[first])
        mid = self.db.memory_card(CID)[0]['id']
        self.db.add_purpose(CID, 'ask', '派生心事', '', [mid], '2026-11-01')
        self.db.save_summary(CID, '含待删内容', second)
        with self.db.transaction() as c:
            c.execute('INSERT INTO stage_summaries(character_id,channel,first_id,last_id,source_ids,summary) VALUES (?,?,?,?,?,?)',
                      (CID, 'desktop', first, second, json.dumps([first, second]), '一个阶段'))
        self.assertEqual(self.db.delete_chat_messages(CID, [second, group, other, reply]), 2)
        self.db.set_delivered_reply('turn', CID, '迟到的朗读回执', 'complete')
        self.db.finish_turn('turn', CID, '迟到的生成', 'complete')
        self.assertEqual([r['id'] for r in self.db.recent_messages()], [first])
        self.assertEqual(self.db.recent_messages(channel='qq:group:12')[0]['id'], group)
        self.assertEqual(self.db.recent_messages(character_id='other')[0]['id'], other)
        self.assertEqual([r['fact_key'] for r in self.db.memory_card(CID)], ['keep'])
        self.assertEqual([r['fact_key'] for r in self.db.memory_archive(CID)], ['keep'])
        self.assertEqual(self.db.load_summary(CID), '')
        self.assertFalse(self.db.purposes(CID))
        self.assertFalse(self.db.upsert_memory('迟到的直接提取', CID, source_ids=[second]))
        self.assertIsNone(self.db.add_purpose(CID, 'ask', '迟到的心事', '', [mid], '2026-11-01'))
        with self.db.transaction() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM stage_summaries').fetchone()[0], 0)
        self.assertEqual(self.db.delete_chat_messages(CID, [second, reply]), 0)

    def test_deletion_during_analysis_discards_stale_batch(self):
        source = self.db.add_message('user', '测试代号松果')
        def generate(*args):
            self.db.delete_chat_messages(CID, [source])
            yield json.dumps({'memories': [dict(content='用户喜欢松果', fact_key='pine', category='preference', source_ids=[source])]})
        MemoryService(self.db).analyze(CID, SimpleNamespace(generate_reply_stream=generate), CancellationToken())
        self.assertFalse(self.db.memory_card(CID))

    def test_choose_old_message_cancel_then_delete_and_forget_card(self):
        for _ in range(ChatRecords.PAGE_SIZE + 1):
            self.db.add_message('user', '重复的消息')
        self.db.upsert_memory('独立记忆', fact_key='separate')
        dialog = ChatRecords(self.host)
        self.addCleanup(dialog.deleteLater)
        dialog.older.click()
        dialog.messages.item(ChatRecords.PAGE_SIZE).setSelected(True)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.No):
            dialog.remove.click()
        self.assertEqual(dialog.messages.count(), ChatRecords.PAGE_SIZE + 1)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            dialog.remove.click()
            dialog.tabs.setCurrentWidget(dialog.memories)
            dialog.memories.item(0).setSelected(True)
            dialog.remove.click()
        self.assertEqual(self.db.recent_messages(100)[0]['id'], 2)
        self.assertEqual(dialog.messages.count(), ChatRecords.PAGE_SIZE)
        self.assertTrue(self.db.has_deleted_memories(CID))
        self.assertEqual(dialog.memories.count(), 0)

    def test_forgetting_memory_does_not_interrupt_reply(self):
        self.db.upsert_memory('独立记忆', fact_key='separate')
        dialog = ChatRecords(self.host)
        self.addCleanup(dialog.deleteLater)
        dialog.tabs.setCurrentWidget(dialog.memories)
        dialog.memories.item(0).setSelected(True)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            dialog.remove.click()
        self.host.interrupt.assert_not_called()
        self.assertFalse(self.db.memory_card(CID))
        dialog.tabs.setCurrentWidget(dialog.messages)
        self.db.add_message('user', '要删的消息')
        dialog.before_id = None
        dialog._load_messages()
        dialog.messages.item(0).setSelected(True)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            dialog.remove.click()
        self.host.interrupt.assert_called_once()

    def test_trial_switch_reset_and_close_keep_real_history_separate(self):
        service = PersonaService(self.db)
        active = service.ensure(DEFAULT_CHARACTER)
        alternate = service.create(CID, dict(title='试验版', description='爱讲冷笑话', speaking_style='短句'))
        self.db.add_message('user', '正式聊天秘密')
        self.db.upsert_memory('正式记忆秘密')
        calls = []
        def generate(history, prompt, token):
            calls.append(([t.content for t in history], prompt))
            yield '试聊回答'
        client = SimpleNamespace(generate_reply_stream=generate)
        self.host.controller.router = SimpleNamespace(client=lambda *args: (client, None))
        dialog = PersonaTrial(self.host)
        self.addCleanup(dialog.deleteLater)
        def send(text):
            dialog.input.setText(text)
            dialog.send()
            self.assertTrue(dialog.worker.wait(3000))
            self.app.processEvents()
            self.assertIsNone(dialog.worker)
        send('第一句')
        send('接着说')
        self.assertEqual(calls[-1][0], ['第一句', '试聊回答', '接着说'])
        dialog.picker.setCurrentText(alternate.title)
        send('新人格')
        self.assertEqual(calls[-1][0], ['新人格'])
        self.assertIn(alternate.description, calls[-1][1])
        self.assertNotIn('正式', repr(calls))
        dialog.reset()
        send('清空后')
        self.assertEqual(calls[-1][0], ['清空后'])
        dialog.input.setText('迟到的消息')
        dialog.send()
        pending = dialog.worker
        dialog.reject()
        self.assertTrue(pending.wait(3000))
        self.app.processEvents()
        self.assertFalse(self.host.tasks)
        self.assertFalse(dialog.history)
        self.assertEqual(service.active(CID).id, active.id)
        self.assertEqual([r['content'] for r in self.db.recent_messages()], ['正式聊天秘密'])
        self.assertEqual([r['content'] for r in self.db.memory_card(CID)], ['正式记忆秘密'])
