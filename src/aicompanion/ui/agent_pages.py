"""设置里的三页：干活（工作区、模型、博客目录、回收区）、操作日志（审计）、用量（今日、7 日、分用途、额度）。"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget, QHeaderView, QInputDialog)

from aicompanion.usage import meter

STATUS = {'done': '完成', 'failed': '失败', 'cancelled': '已取消', 'stopped': '额度停止', 'limit': '超出上限', 'running': '进行中', 'interrupted': '中断', 'paused': '已暂停'}
APPROVAL = {'auto': '自动', 'approved': '同意', 'approved_session': '本次都同意', 'denied': '拒绝', 'timeout': '超时未答'}


class AgentPage(QWidget):
    """干活由 DeepSeek Harness 完成：这里只设默认工作区、模型和博客目录。"""

    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.database = database
        from aicompanion.agent.dsh_backend import dsh_home, problem
        layout = QVBoxLayout(self)
        status = problem()
        layout.addWidget(QLabel(('⚠️ '+status) if status else f'DeepSeek Harness 已安装。运行数据在 {dsh_home()}。'))
        self.enabled = QCheckBox('聊天时允许她提议并动手（回复里带 <task> 标记）')
        self.enabled.setChecked(bool(database.get_setting('agent_enabled', True))); layout.addWidget(self.enabled)
        self.auto = QCheckBox('聊天里说要她做事时直接开始（不用 /做）')
        self.auto.setChecked(bool(database.get_setting('agent_auto', True))); layout.addWidget(self.auto)
        form = QFormLayout()
        self.workspace = QLineEdit(database.get_setting('dsh_workspace', '')); self.workspace.setPlaceholderText(str(Path.home()))
        pick = QPushButton('选择…'); pick.clicked.connect(lambda: self._pick(self.workspace))
        row = QHBoxLayout(); row.addWidget(self.workspace, 1); row.addWidget(pick)
        form.addRow('默认工作区', row)
        form.addRow('干活用的模型', QLabel('在“模型与 Key”页的“办事”一项里选择'))
        self.blog = QLineEdit(database.get_setting('blog_root', '')); self.blog.setPlaceholderText(r'例如 D:\MyBlog（写博客时告诉她在哪）')
        pick_blog = QPushButton('选择…'); pick_blog.clicked.connect(lambda: self._pick(self.blog))
        blog_row = QHBoxLayout(); blog_row.addWidget(self.blog, 1); blog_row.addWidget(pick_blog)
        form.addRow('博客目录', blog_row)
        self.keep_days = QSpinBox(); self.keep_days.setRange(0, 365); self.keep_days.setSuffix(' 天')
        self.keep_days.setValue(int(float(database.get_setting('trash_keep_days', 14) or 0)))
        self.keep_days.setToolTip('0 = 不按天数清理（总量上限仍然生效）')
        form.addRow('回收区保留', self.keep_days)
        self.max_mb = QSpinBox(); self.max_mb.setRange(100, 100_000); self.max_mb.setSingleStep(500); self.max_mb.setSuffix(' MB')
        self.max_mb.setValue(int(float(database.get_setting('trash_max_mb', 2048) or 2048)))
        clean = QPushButton('立即清理'); clean.clicked.connect(self._clean)
        size_row = QHBoxLayout(); size_row.addWidget(self.max_mb, 1); size_row.addWidget(clean)
        form.addRow('回收区上限', size_row)
        layout.addLayout(form)
        self.clean_note = QLabel(database.get_setting('trash_last_clean', '') or '回收区：她改文件前的旧版本、删掉的文件，超过保留天数或总量上限会自动清理。')
        self.clean_note.setWordWrap(True); layout.addWidget(self.clean_note)
        layout.addWidget(QLabel('DeepSeek Harness 负责想和安排（也负责搜网页、分身）；读写文件、删除、跑命令由桌宠自己执行，执行前把关：\n'
                                '· 你话里点名的文件夹和工作区里，写和改直接做，覆盖前旧版本留底在回收区；\n'
                                '· 在外面写、删除任何文件、运行会改东西的命令，先弹确认气泡问你；删除是移到回收区，可以找回；\n'
                                '· .env、数据库、凭据和浏览器数据她读不到；命令以低权限运行，改不了 C 盘的系统和用户目录。\n'
                                '已关闭 DSH 默认的"随请求上传会话日志"和遥测。说"停"或右键"让她停下手里的活"会立即停止。'))
        layout.addStretch()

    def _clean(self):
        from aicompanion.agent import housekeeping
        from aicompanion.agent.dsh_backend import dsh_home
        self.save()
        report = housekeeping.clean(self.keep_days.value(), self.max_mb.value()*1024*1024, [self.blog.text().strip()], dsh_home())
        self.database.set_setting('trash_last_clean', report.text()); self.clean_note.setText(report.text())

    def _pick(self, edit):
        path = QFileDialog.getExistingDirectory(self, '选择目录', edit.text() or str(Path.home()))
        if path: edit.setText(str(Path(path)))

    def save(self):
        self.database.set_setting('agent_enabled', self.enabled.isChecked())
        self.database.set_setting('agent_auto', self.auto.isChecked())
        self.database.set_setting('dsh_workspace', self.workspace.text().strip())
        self.database.set_setting('blog_root', self.blog.text().strip())
        self.database.set_setting('trash_keep_days', self.keep_days.value())
        self.database.set_setting('trash_max_mb', self.max_mb.value())


class AuditPage(QWidget):
    """每次干活的记录：任务、状态、用到的工具和审批。"""

    def __init__(self, database, parent=None, *, resume=None):
        super().__init__(parent)
        self.database = database
        self.resume = resume
        layout = QVBoxLayout(self)
        self.runs = QTableWidget(0, 6); self.runs.setHorizontalHeaderLabels(['开始', '任务', '状态', '步数', '词元', '花费（元）'])
        self.runs.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers); self.runs.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.runs.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.runs.itemSelectionChanged.connect(self._show_steps); layout.addWidget(self.runs, 2)
        self.steps = QTableWidget(0, 4); self.steps.setHorizontalHeaderLabels(['#', '工具', '审批', '内容'])
        self.steps.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.steps.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch); layout.addWidget(self.steps, 3)
        row = QHBoxLayout()
        refresh = QPushButton('刷新'); refresh.clicked.connect(self.refresh); row.addWidget(refresh)
        self.resume_button = QPushButton('继续选中的暂停任务')
        self.resume_button.clicked.connect(self._resume)
        self.resume_button.setEnabled(False)
        row.addWidget(self.resume_button)
        export = QPushButton('导出 JSON…'); export.clicked.connect(self._export); row.addWidget(export)
        row.addStretch(); layout.addLayout(row)
        self.refresh()

    def refresh(self):
        rows = self.database.agent_runs(100); self.runs.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for j, v in enumerate((r['started_at'][5:16], r['goal'], STATUS.get(r['status'], r['status']), r['steps'], r['tokens'], f'{r["cost"]:.4f}')):
                item = QTableWidgetItem(str(v)); item.setData(Qt.ItemDataRole.UserRole, r['id']); self.runs.setItem(i, j, item)

    def _show_steps(self):
        row = self.runs.currentRow()
        status = self.runs.item(row, 2)
        self.resume_button.setEnabled(bool(self.resume and status and status.text() == STATUS['paused']))
        if row < 0 or not self.runs.item(row, 0): return
        steps = self.database.agent_steps(self.runs.item(row, 0).data(Qt.ItemDataRole.UserRole)); self.steps.setRowCount(len(steps))
        for i, s in enumerate(steps):
            result = ('✗ '+s['error']) if s['error'] else (s['result_summary'] or s['args_json'])
            for j, v in enumerate((s['seq'], s['tool'], APPROVAL.get(s['approval'], s['approval']), result)):
                item = QTableWidgetItem(str(v)); item.setToolTip(s['args_json']); self.steps.setItem(i, j, item)

    def _resume(self):
        row = self.runs.currentRow()
        if row < 0 or not self.resume_button.isEnabled(): return
        run_id = self.runs.item(row, 0).data(Qt.ItemDataRole.UserRole)
        remaining, ok = QInputDialog.getMultiLineText(self, '接着办这件事',
            '请核对下面的操作日志和实际结果，再填写还需要她做的部分。确认后才开始。')
        if ok and remaining.strip():
            self.resume(run_id, remaining.strip())
            self.refresh()

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, '导出操作日志', 'agent-audit.json', 'JSON (*.json)')
        if not path: return
        data = [dict(r, steps=[dict(s) for s in self.database.agent_steps(r['id'])]) for r in self.database.agent_runs(1000)]
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


class UsagePage(QWidget):
    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.database = database
        layout = QVBoxLayout(self)
        self.summary = QLabel(''); self.summary.setWordWrap(True); layout.addWidget(self.summary)
        self.today = QTableWidget(0, 7); self.today.setHorizontalHeaderLabels(['用途', '调用', '输入词元', '其中缓存', '输出词元', '花费（元）', '额度'])
        self.today.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers); layout.addWidget(self.today, 2)
        self.trend = QLabel(''); self.trend.setTextFormat(Qt.TextFormat.PlainText); self.trend.setStyleSheet('font-family:Consolas,"Microsoft YaHei UI",monospace;')
        layout.addWidget(QLabel('近 7 日')); layout.addWidget(self.trend)
        self.top = QLabel(''); self.top.setWordWrap(True); layout.addWidget(self.top)
        form = QFormLayout()
        self.total = self._money(database.get_setting('quota_daily_total', 0))
        self.limits = {p: self._money(database.get_setting(f'quota_daily:{p}', 0)) for p in ('companion', 'qq', 'agent', 'memory', 'purpose')}
        form.addRow('每日总额度（元，0 = 不限）', self.total)
        for p, box in self.limits.items(): form.addRow(f'　{meter.PURPOSES[p]}额度（元）', box)
        layout.addLayout(form)
        self.prices = QPlainTextEdit(json.dumps({k: v for k, v in meter.load_prices(database).items()}, ensure_ascii=False, indent=1))
        self.prices.setMaximumHeight(120)
        layout.addWidget(QLabel('价目表（元 / 百万词元，按模型名包含的关键词匹配；以官网为准）')); layout.addWidget(self.prices)
        self.price_error = QLabel(); self.price_error.setObjectName('Warn'); self.price_error.hide(); layout.addWidget(self.price_error)
        layout.addWidget(QLabel('到 80% 时她会提醒一次；到 100% 该用途停止（总额到了全部停止），陪伴聊天改用本地台词。数字是本地估算，以账单为准。'))
        self.refresh()

    @staticmethod
    def _money(value):
        box = QDoubleSpinBox(); box.setRange(0, 10000); box.setDecimals(2); box.setSingleStep(0.5); box.setValue(float(value or 0)); return box

    def refresh(self):
        since = meter.today_start(); rows = self.database.token_usage_by_purpose(since)
        quota = meter.Quota(self.database); limits = quota.limits(); spent = quota.spent()
        self.today.setRowCount(len(rows))
        for i, r in enumerate(rows):
            limit = limits.get(r['purpose'], 0)
            quota_text = f'{(r["cost"] or 0)/limit:.0%} / {limit:.2f}' if limit else '—'
            values = (meter.PURPOSES.get(r['purpose'], r['purpose'])+('（含估算）' if r['estimated'] else ''), r['calls'], r['prompt'], r['cached'], r['completion'], f'{r["cost"] or 0:.4f}', quota_text)
            for j, v in enumerate(values): self.today.setItem(i, j, QTableWidgetItem(str(v)))
        total = limits.get('*', 0)
        self.summary.setText(f'今天共花费约 {spent.get("*", 0):.4f} 元'+(f'，总额度 {total:.2f} 元已用 {spent.get("*", 0)/total:.0%}' if total else '（未设总额度）'))
        days = self.database.token_usage_daily(7); peak = max([d['cost'] or 0 for d in days] or [0]) or 1
        self.trend.setText('\n'.join(f'{d["day"][5:]}  {"█"*max(1, int(20*(d["cost"] or 0)/peak)) if d["cost"] else "·"}  {d["cost"] or 0:.4f} 元  {d["tokens"]} 词元' for d in days) or '还没有记录')
        top = self.database.token_usage_top(since, 3)
        self.top.setText('今天最贵的几次：'+('；'.join(f'{meter.PURPOSES.get(t["purpose"], t["purpose"])} {t["model"]} {t["prompt_tokens"]}+{t["completion_tokens"]} 词元 {t["cost"]:.4f} 元' for t in top) or '无'))

    def save(self):
        try:
            table = json.loads(self.prices.toPlainText())
            if not isinstance(table, dict):
                raise ValueError
        except ValueError:
            self.price_error.setText('价目表需要是 JSON 对象。')
            self.price_error.show()
            raise ValueError(self.price_error.text()) from None
        self.price_error.hide()
        self.database.set_setting('quota_daily_total', self.total.value())
        for p, box in self.limits.items(): self.database.set_setting(f'quota_daily:{p}', box.value())
        self.database.set_setting('price_table', table)
