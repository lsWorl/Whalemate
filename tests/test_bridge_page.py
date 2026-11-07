"""设置 → 聊天平台的真实 Qt 页面流程（Codex 审计 PR #7 的 P2）：首次一键接入后接口真的在运行，页面同步显示，保存不会把它关掉。
需要 PySide6（Windows 检查里会跑）；组件下载和启动换成无副作用的桩，不连网、不启动外部程序。"""
import os
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest import mock

try:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError:
    QApplication = None


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); return s.getsockname()[1]


@unittest.skipIf(QApplication is None, '需要 PySide6')
class BridgePageFlowTest(unittest.TestCase):
    def setUp(self):
        from aicompanion.bridge.server import runtime
        from aicompanion.storage.database import Database
        self.app = QApplication.instance() or QApplication([])
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name)/'pet.sqlite3'); self.db.initialize()
        self.db.set_setting('qq_root', str(Path(self.tmp.name)/'qq')); self.db.set_setting('bridge_port', free_port())
        self.runtime = runtime; runtime.close(); runtime.database = None
        runtime.controller = type('Controller', (), {'character': type('C', (), {'name': '鲸娘'})()})()
        import aicompanion.qq.runtime as qq_runtime
        qq_runtime._manager = None

    def tearDown(self):
        self.runtime.close(); self.runtime.controller = None; self.runtime.database = None
        self.tmp.cleanup()

    def wait(self, page, seconds=10):
        deadline = time.monotonic()+seconds
        while (page.busy or page.checking) and time.monotonic() < deadline:
            self.app.processEvents(); time.sleep(0.02)
        self.app.processEvents()

    def test_first_connect_keeps_bridge_running_after_save(self):
        from aicompanion.bridge.service import setting
        from aicompanion.ui.bridge_page import BridgePage
        self.assertFalse(setting(self.db, 'bridge_enabled')); self.assertIsNone(self.runtime.server)
        page = BridgePage(self.db); self.wait(page)
        self.assertFalse(page.enabled.isChecked())
        qq = page.qq
        def prepare(progress=None, cancelled=None):          # 只做 configure 里和接口有关的部分：真实的 ensure_bridge
            self.db.set_setting('qq_mode', 'auto'); qq.ensure_bridge()
        with mock.patch.object(qq, 'prepare', side_effect=prepare), mock.patch.object(qq, 'start'), \
             mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
            page._prepare(); self.wait(page)
        self.assertIsNotNone(self.runtime.server)                                  # 接口真的在运行
        self.assertTrue(page.enabled.isChecked()); self.assertTrue(setting(self.db, 'bridge_enabled'))   # 页面同步
        server = self.runtime.server
        page.save(); self.wait(page)
        self.assertIs(self.runtime.server, server); self.assertTrue(setting(self.db, 'bridge_enabled'))  # 保存不关掉它
        page.enabled.setChecked(False); page.save(); self.wait(page)               # 用户主动关掉：尊重
        self.assertIsNone(self.runtime.server); self.assertFalse(setting(self.db, 'bridge_enabled'))
        page.timer.stop(); page.deleteLater()

    def test_media_settings_are_saved_with_people(self):
        from aicompanion.ui.bridge_page import BridgePage
        page = BridgePage(self.db); page.timer.stop(); self.wait(page)
        self.addCleanup(page.deleteLater)
        self.assertTrue(page.vision_enabled.isChecked()); self.assertTrue(page.memes_enabled.isChecked())
        page.vision_enabled.setChecked(False); page.memes_enabled.setChecked(False)
        page.meme_root.setText(str(Path(self.tmp.name)/'memes'))
        page.vision_model.setText('deepseek-flash'); page.meme_cooldown.setValue(120)
        page.save_people(); self.wait(page)
        self.assertFalse(self.db.get_setting('qq_vision_enabled')); self.assertFalse(self.db.get_setting('qq_memes_enabled'))
        self.assertEqual(self.db.get_setting('qq_meme_root'), page.meme_root.text())
        self.assertEqual(self.db.get_setting('qq_meme_cooldown'), 120)

    def test_start_error_remains_visible_after_status_refresh(self):
        from aicompanion.ui.bridge_page import BridgePage
        self.db.set_setting('qq_mode', 'auto')
        page = BridgePage(self.db); page.timer.stop(); self.wait(page)
        self.addCleanup(page.deleteLater)
        page.qq.ensure_bridge()
        page.show()
        with mock.patch.object(page.qq, 'ready', return_value=[]), \
             mock.patch.object(page.qq, 'configure', side_effect=PermissionError('配置文件被占用')) as configure, \
             mock.patch.object(page.qq, 'start', wraps=page.qq.start) as start:
            page.refresh(); self.wait(page)
            self.assertEqual(page.status.code, 'stopped')
            self.assertTrue(page.start_btn.isHidden())  # 状态按钮已经是“启动”，不再重复一枚
            page.action.click(); self.wait(page)
            for _ in range(3):
                page.refresh(); self.wait(page)
                self.assertEqual(page.status.code, 'start_failed')
                self.assertIn('配置文件被占用', page.detail.text())
            self.assertIn('配置文件被占用', page.qq.diagnostics_text())
            page.action.click(); self.wait(page)  # start_failed 下实际点“重试”，配置仍失败
            self.assertEqual(start.call_count, 2)  # 重试也必须进入 start 的异常保留逻辑
            self.assertEqual(configure.call_count, 2)  # 每次启动只同步一次配置
            for _ in range(3):
                page.refresh(); self.wait(page)
                self.assertEqual(page.status.code, 'start_failed')
                self.assertIn('配置文件被占用', page.detail.text())
            self.assertIn('配置文件被占用', page.qq.diagnostics_text())
        page.close()

    def test_save_config_error_remains_visible_and_redacted(self):
        from aicompanion.ui.bridge_page import BridgePage
        self.db.set_setting('qq_mode', 'auto')
        page = BridgePage(self.db); page.timer.stop(); self.wait(page)
        self.addCleanup(page.deleteLater)
        page.qq.ensure_bridge(); page.show()
        secret = page.qq.secrets()['webui']
        with mock.patch.object(page.qq, 'ready', return_value=[]), \
             mock.patch.object(page.qq.supervisor, 'running', return_value=False), \
             mock.patch.object(page.qq, 'configure', side_effect=PermissionError(f'同步配置失败 {secret}')):
            page.port.setValue(page.port.value()+1)
            page.save(); self.wait(page)  # 未运行时保存端口会直接同步 configure，不启动组件
            for _ in range(3):
                page.refresh(); self.wait(page)
                self.assertEqual(page.status.code, 'start_failed')
                self.assertIn('同步配置失败', page.detail.text())
                self.assertNotIn(secret, page.detail.text())
            diagnostic = page.qq.diagnostics_text()
            self.assertIn('同步配置失败', diagnostic); self.assertNotIn(secret, diagnostic)
        page.close()

    def test_prepare_error_remains_visible_when_components_are_missing(self):
        from aicompanion.ui.bridge_page import BridgePage
        page = BridgePage(self.db); page.timer.stop(); self.wait(page)
        self.addCleanup(page.deleteLater)
        page.show()
        secret = page.qq.secrets()['onebot']
        def prepare(**kwargs):
            self.db.set_setting('qq_mode', 'auto')
            raise ValueError(f'下载哈希不匹配 {secret}')
        with mock.patch.object(page.qq, 'prepare', side_effect=prepare), \
             mock.patch.object(page.qq, 'start') as start, \
             mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
            page.action.click(); self.wait(page)
            start.assert_not_called()
            for _ in range(3):
                page.refresh(); self.wait(page)
                self.assertEqual(page.status.code, 'not_ready')
                self.assertEqual(page.action.text(), '准备组件')
                self.assertIn('下载哈希不匹配', page.detail.text())
                self.assertNotIn(secret, page.detail.text())
            diagnostic = page.qq.diagnostics_text()
            self.assertIn('下载哈希不匹配', diagnostic); self.assertNotIn(secret, diagnostic)
        # 下一次准备成功后，旧失败原因必须清除；启动仍用桩，不创建外部进程。
        with mock.patch.object(page.qq, 'prepare'), mock.patch.object(page.qq, 'start'), \
             mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
            page.action.click(); self.wait(page)
            self.assertEqual(page.qq.last_error, '')
        page.close()

    def test_group_activity_dropdown_saves(self):
        from aicompanion.bridge.service import setting
        from aicompanion.ui.bridge_page import BridgePage
        page = BridgePage(self.db); self.wait(page)
        self.assertEqual(page.chime.currentData(), 'often')            # 用户选的默认：爱聊天
        page.chime.setCurrentIndex(page.chime.findData('off')); page.save_people(); self.wait(page)
        self.assertEqual(setting(self.db, 'bridge_chime'), 'off')
        page.timer.stop(); page.deleteLater()


if __name__ == '__main__':
    unittest.main()
