"""QQ 设置中的逐群话题管理。"""
from datetime import datetime

from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel,
                               QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from aicompanion.services.circle_topics import CircleTopics, GROUP, switch_key
from aicompanion.services.group_glance import group_names


class CircleTopicsPage(QWidget):
    def __init__(self, database, cid, parent=None):
        super().__init__(parent)
        self.db, self.cid = database, cid
        self.topics = CircleTopics(database)
        layout = QVBoxLayout(self)
        self.groups = QComboBox()
        self.groups.setPlaceholderText('先在“QQ 聊天”里允许一个群')
        layout.addWidget(self.groups)
        self.enabled_box = QCheckBox('群里安静时，允许她主动接上这个群的话题')
        layout.addWidget(self.enabled_box)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['话题', '摘要', '热度', '出现天数', '最近出现'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.table)
        layout.addWidget(QLabel('主要参与者与来源消息'))
        self.evidence = QPlainTextEdit()
        self.evidence.setReadOnly(True)
        layout.addWidget(self.evidence)
        buttons = QHBoxLayout()
        self.delete_one = QPushButton('删除选中话题')
        self.clear_group = QPushButton('清空本群话题')
        buttons.addWidget(self.delete_one)
        buttons.addWidget(self.clear_group)
        layout.addLayout(buttons)
        self.groups.currentIndexChanged.connect(self.refresh_rows)
        self.enabled_box.clicked.connect(self.toggle)
        self.table.itemSelectionChanged.connect(self.show_sources)
        self.delete_one.clicked.connect(lambda: self.remove(False))
        self.clear_group.clicked.connect(lambda: self.remove(True))
        self.refresh_groups()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_groups()

    def refresh_groups(self):
        previous = self.groups.currentData()
        names = group_names()
        self.groups.blockSignals(True)
        self.groups.clear()
        for value in self.db.get_setting('bridge_groups', []):
            group = str(value)
            channel = 'qq:group:' + group
            if GROUP.fullmatch(channel):
                self.groups.addItem(f'{names[group]}（{group}）' if names.get(group) else group, channel)
        self.groups.setCurrentIndex(max(0, self.groups.findData(previous)) if self.groups.count() else -1)
        self.groups.blockSignals(False)
        self.refresh_rows()

    def refresh_rows(self):
        channel = self.groups.currentData()
        self.enabled_box.setEnabled(bool(channel))
        self.enabled_box.setChecked(bool(channel and self.db.get_setting(switch_key(self.cid, channel), False)))
        self.records = self.topics.rows(self.cid, channel) if channel else []
        self.table.setRowCount(0)
        for i, row in enumerate(self.records):
            self.table.insertRow(i)
            last = datetime.fromtimestamp(row['last']).strftime('%Y-%m-%d %H:%M')
            for j, value in enumerate((row['name'], row['summary'], f"{row['heat']:.1f}", str(row['days']), last)):
                self.table.setItem(i, j, QTableWidgetItem(value))
        self.table.resizeColumnToContents(4)
        self.clear_group.setEnabled(bool(self.records))
        self.show_sources()

    def toggle(self, on):
        channel = self.groups.currentData()
        if channel:
            self.db.set_setting(switch_key(self.cid, channel), on)

    def show_sources(self):
        selected = self.table.selectionModel().selectedRows()
        self.delete_one.setEnabled(bool(selected))
        self.evidence.clear()
        if not selected:
            return
        from collections import Counter
        topic = self.records[selected[0].row()]
        sources = self.topics.sources(self.cid, self.groups.currentData(), topic['id'])
        people = Counter(s['person_id'] for s in sources if s['person_id'] is not None)
        lines = ['主要参与者：' + '、'.join(f'群内 #{p}（{n} 条）' for p, n in people.most_common(5))] if people else []
        lines.extend(f"消息 #{s['id']}　{s['content']}" for s in sources)
        self.evidence.setPlainText('\n'.join(lines))

    def remove(self, all_topics):
        selected = self.table.selectionModel().selectedRows()
        if not all_topics and not selected:
            return
        channel = self.groups.currentData()
        title = '本群全部话题' if all_topics else self.records[selected[0].row()]['name']
        if QMessageBox.question(self, '删除话题', f'删除「{title}」？原消息保留，旧记录不会重新提取。') != QMessageBox.Yes:
            return
        self.topics.remove(self.cid, channel, None if all_topics else self.records[selected[0].row()]['id'])
        self.refresh_rows()
