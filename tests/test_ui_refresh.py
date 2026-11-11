"""界面翻新（2026-11-11）：应用图标、分组设置窗口和概览引导、聊天面板的办事开关。不联网、不需要音频设备。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

try:
    from PySide6.QtWidgets import QApplication, QPushButton
except ImportError:   # pragma: no cover
    QApplication = None

from aicompanion.config import Settings
from aicompanion.controllers.chat_controller import ChatController
from aicompanion.providers.credentials import CredentialStore
from aicompanion.providers.router import ModelRouter
from aicompanion.services.memory_service import MemoryService
from aicompanion.storage.database import Database


class FakeProtector:
    name = 'fake'
    def protect(self, data): return data[::-1]
    def unprotect(self, data): return data[::-1]


@unittest.skipIf(QApplication is None, '需要 PySide6')
class UiRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.db = Database(root/'s.sqlite3'); self.db.initialize()
        self.settings = Settings('AICompanion', root/'s.sqlite3', offline=False)
        self.router = ModelRouter(self.db, self.settings, credentials=CredentialStore(self.db, {}, protector=FakeProtector()))
        self.controller = ChatController(self.db, MemoryService(self.db), self.router)
        self.controller.character = self.db.characters()[0]

    def tearDown(self): self.tmp.cleanup()

    def window(self, **kwargs):
        import aicompanion.ui.settings_window as module
        with mock.patch.object(module, 'QMediaDevices', SimpleNamespace(audioInputs=lambda: [])):   # 容器里没有音频设备
            return module.SettingsWindow(self.controller, self.settings, **kwargs)

    def test_icon_files_ship_with_package(self):
        from aicompanion.ui.icons import ICO, PNG, app_icon
        self.assertTrue(ICO.is_file() and PNG.is_file())
        self.assertFalse(app_icon().isNull())

    def test_settings_pages_are_grouped_and_reachable(self):
        dialog = self.window(page='models')
        try:
            self.assertEqual(dialog.nav.current(), 'models')
            for key in dialog.PAGES:
                with self.subTest(page=key): self.assertTrue(dialog.open_page(key)); self.assertEqual(dialog.nav.current(), key)
            groups = [dialog.nav.list.item(i).text() for i in range(dialog.nav.list.count()) if not dialog.nav.keys[i]]
            self.assertEqual(groups, ['她', '能力', '连接', '系统'])
            self.assertLess(len(dialog.PAGES), 14)                                    # 原来 14 个平铺标签
            self.assertFalse(dialog.open_page('nope'))
        finally:
            dialog.deleteLater()

    def test_overview_shows_what_is_missing_and_jumps_there(self):
        dialog = self.window()
        try:
            self.assertEqual(dialog.nav.current(), 'overview')
            overview = dialog.nav.pages['overview'].widget()
            texts = ' '.join(label.text() for label in overview.findChildren(type(dialog.archive_label)))
            self.assertIn('还没有配置', texts); self.assertIn('即将推出', texts)
            go = [b for b in overview.findChildren(QPushButton) if b.text() == '去设置']
            self.assertEqual(len(go), 5)
            go[0].click(); self.assertEqual(dialog.nav.current(), 'models')                 # 第一项：聊天模型
        finally:
            dialog.deleteLater()

    def test_save_still_writes_settings_from_regrouped_pages(self):
        dialog = self.window()
        try:
            dialog.quiet_start.setValue(22); dialog.proactive.setChecked(False)
            dialog.name.setText('大肥鱼二号'); dialog.speech_style.setCurrentIndex(dialog.speech_style.findData('full'))
            dialog.save()
            self.assertEqual((self.db.get_setting('quiet_start'), self.db.get_setting('proactive_enabled'), self.db.get_setting('speech_style')),
                             (22, False, 'full'))
            self.assertEqual(self.db.characters()[0].name, '大肥鱼二号')
        finally:
            dialog.deleteLater()

    def test_agent_toggle_turns_message_into_task(self):
        from aicompanion.ui.main_window import MainWindow
        sent = []
        from PySide6.QtWidgets import QLineEdit
        fake = SimpleNamespace(input_box=QLineEdit(), agent_toggle=QPushButton(), AGENT_PREFIXES=MainWindow.AGENT_PREFIXES,
                               submit=lambda text: sent.append(text))
        fake.agent_toggle.setCheckable(True)
        fake.input_box.setText('整理桌面'); fake.agent_toggle.setChecked(True)
        MainWindow._send_current_message(fake)
        self.assertEqual(sent, ['/做 整理桌面']); self.assertFalse(fake.agent_toggle.isChecked())   # 一次一件事
        fake.input_box.setText('你好'); MainWindow._send_current_message(fake)
        self.assertEqual(sent[-1], '你好')
        fake.agent_toggle.setChecked(True); fake.input_box.setText('/做 写周报'); MainWindow._send_current_message(fake)
        self.assertEqual(sent[-1], '/做 写周报')                                              # 已经带前缀不重复加


if __name__ == '__main__':
    unittest.main()
