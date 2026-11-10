"""T18 社区与黑话页：真实 Qt 控件，合成临时库（只在装了 PySide6 的环境里跑，如 Windows 检查）。"""
import os, tempfile, unittest
from dataclasses import replace
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from aicompanion.community.lexicon import Lexicon
from aicompanion.community.lore import GroupLore
from aicompanion.domain.character import DEFAULT_CHARACTER
from aicompanion.storage.database import Database
from aicompanion.ui.community_page import CommunityPage

CID = 'deepseek-whale'


class CommunityPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.db = Database(Path(self.temp.name)/'synthetic.sqlite3'); self.db.initialize()
        self.db.add_character(replace(DEFAULT_CHARACTER, id=CID, name='鲸娘'))
        lex = Lexicon(self.db)
        lex.learn('绷不住', '忍不住笑', origin='web', source_name='某百科', source_url='https://example.org/a')
        lex.learn('绷不住', '情绪崩溃', origin='web', change='changed')
        lex.sight('绷不住', 'qq:group:30003', '1', '这也太绷不住了')
        GroupLore(self.db).add(CID, 'qq:group:30003', 'inside_joke', '大家互相叫对方急急国王', 0.9)
        self.page = CommunityPage(self.db, CID)

    def tearDown(self): self.page.deleteLater(); self.app.processEvents(); self.temp.cleanup()

    def test_terms_history_lore_and_toggles(self):
        self.assertEqual(self.page.table.rowCount(), 1)
        self.assertEqual(self.page.table.item(0, 2).text(), '情绪崩溃')
        self.page.table.selectRow(0)
        text = self.page.details.toPlainText()
        self.assertIn('已过时', text); self.assertIn('忍不住笑', text); self.assertIn('https://example.org/a', text); self.assertIn('1 个群', text)
        self.assertEqual(self.page.lore_list.count(), 1)
        self.page.lore_list.setCurrentRow(0); self.page.forget_lore(); self.assertEqual(self.page.lore_list.count(), 0)
        self.page.web.setChecked(False); self.assertFalse(self.db.get_setting('community_web', True))
        self.page.table.selectRow(0); self.page.delete(); self.assertEqual(self.page.table.rowCount(), 0)


if __name__ == '__main__':
    unittest.main()
