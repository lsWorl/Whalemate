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
        # 这些导航用例不会等待 QQ 轮询，避免工作线程在临时数据库回收后才交付结果。
        with (mock.patch.object(module, 'QMediaDevices', SimpleNamespace(audioInputs=lambda: [])),
              mock.patch('aicompanion.ui.bridge_page.BridgePage.refresh', lambda page: None)):
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
            self.assertIn('还没有配置', texts); self.assertNotIn('即将推出', texts); self.assertIn('还差', texts)
            go = [b for b in overview.findChildren(QPushButton) if b.text() == '连接模型']
            self.assertEqual(len(go), 1)
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
                               STOP_WORDS=MainWindow.STOP_WORDS, submit=lambda text: sent.append(text))
        fake.agent_toggle.setCheckable(True)
        fake.input_box.setText('整理桌面'); fake.agent_toggle.setChecked(True)
        MainWindow._send_current_message(fake)
        self.assertEqual(sent, ['/做 整理桌面']); self.assertFalse(fake.agent_toggle.isChecked())   # 一次一件事
        fake.input_box.setText('你好'); MainWindow._send_current_message(fake)
        self.assertEqual(sent[-1], '你好')
        fake.agent_toggle.setChecked(True); fake.input_box.setText('/做 写周报'); MainWindow._send_current_message(fake)
        self.assertEqual(sent[-1], '/做 写周报')                                              # 已经带前缀不重复加
        self.assertFalse(fake.agent_toggle.isChecked())                                         # Codex PR #33 P2：手写前缀也复位
        fake.input_box.setText('谢谢'); MainWindow._send_current_message(fake)
        self.assertEqual(sent[-1], '谢谢')                                                      # 下一句仍是普通聊天
        fake.agent_toggle.setChecked(True); fake.input_box.setText('停！'); MainWindow._send_current_message(fake)
        self.assertEqual(sent[-1], '停！'); self.assertFalse(fake.agent_toggle.isChecked())    # 停止词不补前缀

    def test_stop_word_reaches_stop_branch_while_busy(self):
        # Codex PR #33 P2 的第二个场景：经生产 _send_current_message + submit，任务忙时说“停”要进入停止分支，不能变成新任务
        from aicompanion.ui.main_window import MainWindow
        from PySide6.QtWidgets import QLineEdit
        calls = []
        fake = SimpleNamespace(input_box=QLineEdit(), agent_toggle=QPushButton(), AGENT_PREFIXES=MainWindow.AGENT_PREFIXES,
                               STOP_WORDS=MainWindow.STOP_WORDS, closing=False,
                               agent=SimpleNamespace(busy=True, start=lambda goal: calls.append(('start', goal))),
                               _stop_agent=lambda: calls.append(('stop',)), pet=SimpleNamespace(show_user=lambda text: None),
                               database=SimpleNamespace(add_message=lambda *a, **k: None),
                               controller=SimpleNamespace(character=SimpleNamespace(id='whale')))
        fake.submit = lambda text, source='text': MainWindow.submit(fake, text, source)
        fake.agent_toggle.setCheckable(True)
        fake.agent_toggle.setChecked(True); fake.input_box.setText('/做 整理合成文件'); MainWindow._send_current_message(fake)
        self.assertFalse(fake.agent_toggle.isChecked())
        fake.input_box.setText('停'); MainWindow._send_current_message(fake)                   # 开关已复位的情况
        fake.agent_toggle.setChecked(True); fake.input_box.setText('停'); MainWindow._send_current_message(fake)   # 又打开了“办事”
        self.assertEqual(calls, [('start', '整理合成文件'), ('stop',), ('stop',)])               # 两次都停，没有启动新任务


if __name__ == '__main__':
    unittest.main()
