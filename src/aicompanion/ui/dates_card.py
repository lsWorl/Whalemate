"""“我们的日子”（计划书 v4 功能九）：认识第几天、聊过几天、接下来有哪些日子；可以改认识那天、添加生日和纪念日、关掉某一类提醒。"""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QAbstractItemView, QHeaderView)

from aicompanion.services.companion_dates import KINDS, CompanionDates

WEEKDAYS = '一二三四五六日'


def _qdate(value: date) -> QDate: return QDate(value.year, value.month, value.day)
def _pydate(value: QDate) -> date: return date(value.year(), value.month(), value.day())


class DatesCard(QWidget):
    def __init__(self, database, character_id: str, parent=None, clock=None):
        super().__init__(parent)
        self.dates = CompanionDates(database, clock=clock) if clock else CompanionDates(database)
        self.cid = character_id
        box = QVBoxLayout(self); box.setContentsMargins(0, 0, 0, 0)
        self.summary = QLabel(); self.summary.setWordWrap(True); box.addWidget(self.summary)

        met = QHBoxLayout()
        self.met = QDateEdit(); self.met.setCalendarPopup(True); self.met.setDisplayFormat('yyyy-MM-dd')
        change = QPushButton('改成这天'); change.clicked.connect(self.change_met)
        met.addWidget(QLabel('认识那天')); met.addWidget(self.met); met.addWidget(change)
        hint = QLabel('以前在别处养过她，可以把这天改早一点'); hint.setObjectName('Hint'); met.addWidget(hint, 1)
        box.addLayout(met)

        box.addWidget(QLabel('接下来的日子（60 天内）'))
        self.upcoming = QListWidget(); self.upcoming.setMaximumHeight(120); box.addWidget(self.upcoming)

        box.addWidget(QLabel('你加的日子'))
        self.table = QTableWidget(0, 4); self.table.setHorizontalHeaderLabels(['类型', '名称', '日期', '每年'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False); self.table.setMaximumHeight(140)
        box.addWidget(self.table)

        add = QHBoxLayout()
        self.kind = QComboBox()
        for key, label in KINDS.items(): self.kind.addItem(label, key)
        self.title = QLineEdit(); self.title.setPlaceholderText('名称（纪念日用，例如：期末考试）'); self.title.setMaxLength(30)
        self.when = QDateEdit(); self.when.setCalendarPopup(True); self.when.setDisplayFormat('yyyy-MM-dd')
        self.yearly = QCheckBox('每年'); self.yearly.setChecked(True)
        plus = QPushButton('添加'); plus.clicked.connect(self.add)
        minus = QPushButton('删除选中'); minus.clicked.connect(self.remove)
        for widget in (self.kind, self.title, self.when, self.yearly, plus, minus): add.addWidget(widget)
        box.addLayout(add)

        toggles = QHBoxLayout()
        self.milestones = QCheckBox('认识第 100 天、周年这类纪念日'); self.holidays = QCheckBox('节日（元旦、国庆等公历节日）')
        for check in (self.milestones, self.holidays):
            check.toggled.connect(self.save_options); toggles.addWidget(check)
        toggles.addStretch(1); box.addLayout(toggles)
        note = QLabel('这些日子只在你们私下聊天时用，QQ 群里不会说出你的生日。农历生日和农历节日下一步支持。')
        note.setObjectName('Hint'); note.setWordWrap(True); box.addWidget(note)
        self.refresh()

    def refresh(self):
        d = self.dates
        n = d.day_number(self.cid); met = d.met_on(self.cid)
        if n is None:
            self.summary.setText('你们还没正式聊过天：第一次说话的那天就是认识的第 1 天。')
        else:
            self.summary.setText(f'认识第 {n} 天，其中 {d.talked_days(self.cid)} 天聊过天。')
        today = d.today()
        self.met.setDate(_qdate(met or today)); self.met.setMaximumDate(_qdate(today)); self.when.setDate(_qdate(today))
        self.upcoming.clear()
        for day, events in d.upcoming(self.cid):
            when = '今天' if day == today else ('明天' if (day - today).days == 1 else f'{day.month} 月 {day.day} 日（周{WEEKDAYS[day.weekday()]}）')
            self.upcoming.addItem(f'{when}：' + '、'.join(e.title for e in events))
        if self.upcoming.count() == 0: self.upcoming.addItem('60 天内没有特别的日子')
        rows = d.user_dates(self.cid)
        self.table.setRowCount(0)
        for r in rows:
            i = self.table.rowCount(); self.table.insertRow(i)
            values = (KINDS.get(r['kind'], r['kind']), r['title'], f"{r['year'] or ''}-{r['month']:02d}-{r['day']:02d}".lstrip('-'),
                      '是' if r['yearly'] else '只一次')
            for j, value in enumerate(values):
                item = QTableWidgetItem(value); item.setData(256, r['id']); self.table.setItem(i, j, item)
        options = d.options(self.cid)
        for check, key in ((self.milestones, 'milestones'), (self.holidays, 'holidays')):
            check.blockSignals(True); check.setChecked(options[key]); check.blockSignals(False)

    def change_met(self):
        try: self.dates.set_met_on(self.cid, _pydate(self.met.date()))
        except ValueError as error: self.summary.setText(str(error)); return
        self.refresh()

    def add(self):
        self.dates.add(self.cid, self.kind.currentData(), self.title.text(), _pydate(self.when.date()), self.yearly.isChecked())
        self.title.clear(); self.refresh()

    def remove(self):
        ids = {self.table.item(r.row(), 0).data(256) for r in self.table.selectionModel().selectedRows()}
        for date_id in ids: self.dates.remove(self.cid, date_id)
        self.refresh()

    def save_options(self, *args):
        self.dates.set_options(self.cid, milestones=self.milestones.isChecked(), holidays=self.holidays.isChecked())
        self.refresh()
