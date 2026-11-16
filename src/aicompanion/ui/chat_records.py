"""聊天面板里的消息和记忆管理。选择使用数据库主键，同样的文字也能分开删除。"""
from datetime import datetime, timezone

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QTabWidget, QVBoxLayout)

from aicompanion.ui import theme


class ChatRecords(QDialog):
    PAGE_SIZE = 60

    def __init__(self, window):
        super().__init__(window)
        self.host = window
        self.db = window.database
        self.character = window.controller.character
        self.before_id = None
        self.setWindowTitle('聊天记录与记忆 · ' + self.character.name)
        self.setStyleSheet(theme.DIALOG)
        self.resize(520, 500)
        box = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.messages = QListWidget()
        self.memories = QListWidget()
        for widget, label in ((self.messages, '聊天记录'), (self.memories, '记忆卡')):
            widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
            widget.setWordWrap(True)
            widget.setAccessibleName(label)
            self.tabs.addTab(widget, label)
            widget.itemSelectionChanged.connect(self._selection)
        box.addWidget(self.tabs)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        box.addWidget(self.hint)
        row = QHBoxLayout()
        self.older = QPushButton('加载更早记录')
        self.older.clicked.connect(self._load_messages)
        row.addWidget(self.older)
        row.addStretch()
        self.remove = QPushButton()
        self.remove.setObjectName('Danger')
        self.remove.clicked.connect(self.delete_selected)
        row.addWidget(self.remove)
        done = QPushButton('完成')
        done.clicked.connect(self.accept)
        row.addWidget(done)
        box.addLayout(row)
        self.tabs.currentChanged.connect(self._selection)
        self._load_messages()
        self._load_memories()
        self._selection()

    @staticmethod
    def _add(widget, record_id, text):
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, record_id)
        widget.addItem(item)

    def _load_messages(self):
        rows = self.db.recent_messages(self.PAGE_SIZE + 1, self.character.id, self.before_id)
        self.older.setEnabled(len(rows) > self.PAGE_SIZE)
        for r in reversed(rows[-self.PAGE_SIZE:]):
            sender = '你' if r['role'] == 'user' else self.character.name
            text = r['content'] or '［表情］'
            when = datetime.fromisoformat(r['created_at']).replace(tzinfo=timezone.utc).astimezone()
            self._add(self.messages, r['id'], f"{sender} · {when:%Y-%m-%d %H:%M}\n{text}")
            self.before_id = r['id']

    def _load_memories(self):
        self.memories.clear()
        for row in self.db.memory_card(self.character.id):
            self._add(self.memories, row['id'], row['content'])

    def _selection(self):
        widget = self.tabs.currentWidget()
        messages = widget is self.messages
        self.older.setVisible(messages)
        self.remove.setText('删除选中消息' if messages else '忘掉选中记忆')
        self.remove.setEnabled(bool(widget.selectedItems()))
        self.hint.setText('消息按从新到旧排列。可按住 Ctrl 多选，也可按住 Shift 选中一段对话。'
                          if messages else '这些记忆属于当前角色，在不同人格间共享。可按住 Ctrl 多选。')

    def delete_selected(self):
        widget = self.tabs.currentWidget()
        items = widget.selectedItems()
        if not items:
            return
        ids = [item.data(Qt.ItemDataRole.UserRole) for item in items]
        messages = widget is self.messages
        note = (f'删除选中的 {len(ids)} 条本地消息？直接引用它们的记忆卡、心事和聊天摘要也会移除。'
                if messages else f'忘掉选中的 {len(ids)} 条记忆？对应的存档和心事也会移除，聊天记录仍可查看。')
        if QMessageBox.question(self, self.remove.text(), note,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        if messages:
            # 只有删消息会改动正在生成的上下文；忘掉记忆卡不打断她正在说的话，下一轮自然不再引用。
            self.host.interrupt()
            self.db.delete_chat_messages(self.character.id, ids)
            self._load_memories()
        else:
            self.db.delete_memories(ids, self.character.id)
        for item in items:
            widget.takeItem(widget.row(item))
        if messages:
            self.host.items.clear()
            self.host._load_history()
        self._selection()
