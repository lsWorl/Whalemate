"""聊天面板顶部的一行余量提示（T29）：今日本地额度还剩多少，以及支持查询的服务商账户余额。

本地额度直接读数据库，30 秒刷新一次；账户余额要联网，在后台线程里查，10 分钟最多查一次，点一下可以立即重查。
窗口不可见时都不刷新。离线模式或没有可查询的服务商时不联网。
"""
from __future__ import annotations

import time

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import QApplication, QLabel

from aicompanion.usage import balance, meter

LOCAL_INTERVAL_MS = 30_000
BALANCE_INTERVAL = 600.0
QUERY_TIMEOUT = 5


class BalanceWorker(QThread):
    finished_with = Signal(object)

    def __init__(self, router, parent=None):
        super().__init__(parent)
        self.router = router

    def run(self):
        try:
            self.finished_with.emit(balance.query(self.router, timeout=QUERY_TIMEOUT))
        except Exception:
            self.finished_with.emit([])


def local_text(database) -> str:
    left = meter.Quota(database).remaining()
    if '*' in left:
        limit, used, rest = left['*']
        return f'今日额度剩 {rest:.2f} / {limit:.2f} 元'
    spent = meter.Quota(database).spent().get('*', 0.0)
    return f'今日已用约 {spent:.2f} 元（未设额度）'


class QuotaBadge(QLabel):
    def __init__(self, database, router, parent=None):
        super().__init__(parent)
        self.database, self.router = database, router
        self.setObjectName('Presence')
        self.setWordWrap(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip('本地额度是按价目表估算的今日花费；账户余额来自服务商接口。点一下立即刷新余额。')
        self.balances: list = []
        self.checked_at = 0.0
        self.worker = None
        self.timer = QTimer(self)
        self.timer.setInterval(LOCAL_INTERVAL_MS)
        self.timer.timeout.connect(self.refresh)
        self.refresh(fetch=False)
        # 退出时等后台查询结束（单个请求最多 QUERY_TIMEOUT 秒），避免线程随窗口销毁时仍在运行。
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def mousePressEvent(self, event):
        self.refresh(force=True)
        super().mousePressEvent(event)

    def refresh(self, fetch=True, force=False):
        try:
            parts = [local_text(self.database)]
        except Exception:
            parts = ['今日额度暂时读不到']
        parts += [b.text() for b in self.balances]
        self.setText(' · '.join(parts))
        if fetch and (force or time.monotonic() - self.checked_at >= BALANCE_INTERVAL):
            self.fetch_balances()

    def fetch_balances(self):
        if self.worker is not None or getattr(self.router, 'offline', False):
            return
        try:
            if not balance.supported(self.router.store.load()):
                return
        except Exception:
            return
        self.checked_at = time.monotonic()
        self.worker = BalanceWorker(self.router, self)
        self.worker.finished_with.connect(self._balances_ready)
        self.worker.finished.connect(self._worker_done)
        self.worker.start()

    def _balances_ready(self, results):
        self.balances = list(results)
        self.refresh(fetch=False)

    def _worker_done(self):
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.deleteLater()

    def shutdown(self):
        self.timer.stop()
        if self.worker is not None:
            self.worker.wait((QUERY_TIMEOUT + 1) * 1000 * max(1, len(self.balances) or 1))
