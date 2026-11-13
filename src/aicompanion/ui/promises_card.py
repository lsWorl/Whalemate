"""设置中只编辑本角色的约定。选中记录后保留它原来的人物和会话。"""
from datetime import datetime
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from aicompanion.services.promises import Promises, STATES


class PromisesCard(QWidget):
    def __init__(self, db, cid, parent=None):
        super().__init__(parent)
        self.service, self.cid = Promises(db), cid
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['和谁', '会话', '约定', '时间或条件', '状态'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        layout.addWidget(self.table)
        self.title, self.when, self.until = QLineEdit(), QLineEdit(), QLineEdit()
        self.title.setMaxLength(240)
        self.title.setPlaceholderText('约定内容')
        self.when.setPlaceholderText('年-月-日 时:分、下次聊天 或 聊到：关键词')
        layout.addWidget(self.title)
        layout.addWidget(self.when)
        self.until.setPlaceholderText('截止时间（可留空），格式为 年-月-日 时:分')
        layout.addWidget(self.until)
        buttons = QHBoxLayout()
        self.save, self.cancel, refresh = QPushButton('保存修改'), QPushButton('取消约定'), QPushButton('刷新')
        for button in (self.save, self.cancel, refresh): buttons.addWidget(button)
        layout.addLayout(buttons)
        self.notice = QLabel()
        layout.addWidget(self.notice)
        self.table.itemSelectionChanged.connect(self.select)
        self.save.clicked.connect(lambda: self.apply(False))
        self.cancel.clicked.connect(lambda: self.apply(True))
        refresh.clicked.connect(self.refresh)
        self.refresh()

    def refresh(self):
        self.rows = self.service.rows(self.cid)
        self.table.setRowCount(0)
        for index, row in enumerate(self.rows):
            self.table.insertRow(index)
            when = row['condition'] or datetime.fromtimestamp(row['due_at']).strftime('%Y-%m-%d %H:%M')
            place = '主人私聊' if row['scope']=='desktop' else '群 ' + row['scope'].split(':')[-1]
            state = STATES[row['status']]
            if row['status']=='pending' and not row['editable']: state = '已交付，等回执'
            for col, value in enumerate((row['display_name'], place, row['title'], when, state)):
                self.table.setItem(index, col, QTableWidgetItem(value))
        self.select()

    def select(self):
        chosen = self.table.selectionModel().selectedRows()
        row = chosen[0].row() if chosen else -1
        self.selected = self.rows[row] if 0 <= row < len(self.rows) else None
        active = bool(self.selected and self.selected['editable'])
        for field in (self.title, self.when, self.until, self.save, self.cancel): field.setEnabled(active)
        self.title.setText(self.selected['title'] if self.selected else '')
        self.when.setText(self.table.item(row,3).text() if self.selected else '')
        expires = self.selected['expires_at'] if self.selected else None
        self.until.setText(datetime.fromtimestamp(expires).strftime('%Y-%m-%d %H:%M') if expires else '')

    def apply(self, cancel):
        if not self.selected: return
        value = self.when.text().strip()
        condition = value if value == '下次聊天' or value.startswith(('聊到：','聊到:')) else ''
        try:
            changed = self.service.change(self.cid,self.selected['id'],self.title.text(),
                                          '' if condition else value,condition,until=self.until.text(),cancel=cancel)
        except ValueError as exc:
            self.notice.setText(str(exc))
            return
        self.notice.setText(('已取消' if cancel else '已保存') if changed else '约定已更新或已交付，请查看最新状态。')
        self.refresh()
