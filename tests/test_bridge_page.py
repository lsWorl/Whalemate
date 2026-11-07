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

    def test_start_error_remains_visible_after_status_refresh(self):
        from aicompanion.ui.bridge_page import BridgePage
        self.db.set_setting('qq_mode', 'auto')
        page = BridgePage(self.db); page.timer.stop(); self.wait(page)
        self.addCleanup(page.deleteLater)
        page.qq.ensure_bridge()
        page.show()
        with mock.patch.object(page.qq, 'ready', return_value=[]), \
             mock.patch.object(page.qq, 'configure', side_effect=PermissionError('配置文件被占用')):
            page.refresh(); self.wait(page)
            self.assertEqual(page.status.code, 'stopped')
            self.assertTrue(page.start_btn.isHidden())  # 状态按钮已经是“启动”，不再重复一枚
            page.action.click(); self.wait(page)
            for _ in range(3):
                page.refresh(); self.wait(page)
                self.assertEqual(page.status.code, 'start_failed')
                self.assertIn('配置文件被占用', page.detail.text())
            self.assertIn('配置文件被占用', page.qq.diagnostics_text())
        page.close()


if __name__ == '__main__':
    unittest.main()
