"""T29：聊天面板的余量提示。离线假路由，不联网。"""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication

from aicompanion.providers.store import ModelConfig
from aicompanion.storage.database import Database
from aicompanion.usage.balance import Balance
from aicompanion.ui import quota_badge


class QuotaBadgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.db = Database(Path(folder.name)/'t.sqlite3'); self.db.initialize()
        self.router = SimpleNamespace(offline=True, store=SimpleNamespace(load=lambda: ModelConfig()))

    def test_local_quota_and_balances_are_shown(self):
        badge = quota_badge.QuotaBadge(self.db, self.router)
        self.addCleanup(badge.deleteLater)
        self.assertEqual(badge.text(), '今日已用约 0.00 元（未设额度）')
        self.db.set_setting('quota_daily_total', 5)
        badge._balances_ready([Balance('DeepSeek', 12.34)])
        self.assertEqual(badge.text(), '今日额度剩 5.00 / 5.00 元 · DeepSeek 余额 12.34元')

    def test_offline_never_starts_a_query(self):
        badge = quota_badge.QuotaBadge(self.db, self.router)
        self.addCleanup(badge.deleteLater)
        with patch.object(quota_badge, 'BalanceWorker') as worker:
            badge.refresh(force=True)
        worker.assert_not_called()

    def test_multiple_balances_fit_the_narrow_chat_header(self):
        badge = quota_badge.QuotaBadge(self.db, self.router)
        self.addCleanup(badge.deleteLater)
        self.db.set_setting('quota_daily_total', 5)
        badge._balances_ready([Balance('DeepSeek', 12.34), Balance('Kimi', 56.78)])
        # 420 像素聊天窗口的额度区域只有 258 像素宽，完整文字需要多行。
        required_height = badge.heightForWidth(258)
        self.assertGreater(required_height, badge.fontMetrics().height())
        self.assertIn('DeepSeek 余额 12.34元', badge.text())
        self.assertIn('Kimi 余额 56.78元', badge.text())


if __name__ == '__main__':
    unittest.main()
