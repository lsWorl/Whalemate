"""T15 人物关系页：真实 Qt 控件，合成临时库（只在装了 PySide6 的环境里跑，如 Windows 检查）。"""
import os, tempfile, unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QMessageBox
from aicompanion.domain.character import DEFAULT_CHARACTER
from aicompanion.relationship.service import OWNER_DESKTOP, PersonRef, RelationshipService
from aicompanion.storage.database import Database
from aicompanion.ui.relationship_page import RelationshipPage

CID = 'deepseek-whale'


class RelationshipPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.db = Database(Path(self.temp.name)/'synthetic.sqlite3'); self.db.initialize()
        self.db.add_character(replace(DEFAULT_CHARACTER, id=CID, name='鲸娘'))
        self.rel = RelationshipService(self.db)
        self.owner = self.rel.resolve(OWNER_DESKTOP, CID)
        self.friend = self.rel.resolve(PersonRef('qq', '90000', '20002', '群友A'), CID)
        self.rel.record(CID, self.friend, 'help_her', 0.9, scope='group:x', evidence='帮她找到文件', interest='原神')
        self.page = RelationshipPage(self.db, CID, relationships=self.rel)

    def tearDown(self): self.page.deleteLater(); self.app.processEvents(); self.temp.cleanup()

    def row_of(self, pid):
        for row in range(self.page.table.rowCount()):
            if self.page.table.item(row, 0).data(0x0100) == pid: return row
        self.fail('没找到这个人')

    def test_list_details_adjust_reset_delete(self):
        self.assertEqual(self.page.table.rowCount(), 2)
        self.assertTrue(self.page.table.item(self.row_of(self.owner), 0).text().startswith('★'))
        self.page.table.selectRow(self.row_of(self.friend))
        text = self.page.details.toPlainText()
        self.assertIn('帮了她', text); self.assertIn('原神', text); self.assertIn('群友A', text)
        self.page.spins['affinity'].setValue(66); self.page.save()
        self.assertEqual(self.rel.profile(CID, self.friend)['affinity'], 66)
        self.assertIn('72 小时', self.page.status.text())
        with mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
            self.page.table.selectRow(self.row_of(self.friend)); self.page.reset()
            self.assertEqual(self.rel.events(CID, self.friend), [])
            self.page.table.selectRow(self.row_of(self.owner)); self.page.delete()
            self.assertIsNotNone(self.rel.profile(CID, self.owner))                       # 主人只能重置
            self.page.table.selectRow(self.row_of(self.friend)); self.page.delete()
        self.assertIsNone(self.rel.profile(CID, self.friend)); self.assertEqual(self.page.table.rowCount(), 1)

    def test_learning_switch_writes_setting(self):
        self.page.learning.setChecked(False)
        self.assertFalse(self.db.get_setting('relationship_learning', True))


if __name__ == '__main__':
    unittest.main()
