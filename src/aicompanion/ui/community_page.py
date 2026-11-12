"""T18 社区与黑话页：她学到的梗 / 黑话（含义、语气、圈子、来源、演变历史、在哪儿见过），以及各个群的共同记忆。
可以删掉学错的词、忘掉某条群记忆，开关"从群聊学梗"和"联网查梗"。"""
import time
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                               QPlainTextEdit, QCheckBox, QComboBox, QListWidget, QListWidgetItem)
from aicompanion.community.lexicon import Lexicon
from aicompanion.community.lore import KINDS, GroupLore
from aicompanion.community.search import WebSearch

ORIGINS = {'web': '网上查到', 'wiki': '百科', 'observed': '群里的用法（推断）', 'manual': '手动', 'trend': '最近热梗', 'seed': '预置'}


def when(value):
    return time.strftime('%Y-%m-%d', time.localtime(value)) if value else '至今'


class CommunityPage(QWidget):
    def __init__(self, database, character_id, parent=None, client=None):
        super().__init__(parent)
        self.db, self.character_id, self.client = database, character_id, client
        self.lexicon = Lexicon(database); self.lore = GroupLore(database)
        layout = QVBoxLayout(self)
        hint = QLabel('她从群聊里学到的梗和黑话：先看大家怎么用，用得多了再推断意思；能联网时查出处（只搜词本身，不搜聊天原文，'
                      '只有一个人说过的词、群友外号不外传）。意思变了不会删旧的，这里能看到演变。')
        hint.setWordWrap(True); layout.addWidget(hint)
        toggles = QHBoxLayout()
        self.learning = QCheckBox('从群聊里学梗 / 黑话'); self.learning.setChecked(bool(database.get_setting('community_learning', True)))
        self.learning.toggled.connect(lambda on: database.set_setting('community_learning', bool(on)))
        self.web = QCheckBox('联网查出处和最近热梗'); self.web.setChecked(bool(database.get_setting('community_web', True)))
        self.web.toggled.connect(lambda on: database.set_setting('community_web', bool(on)))
        toggles.addWidget(self.learning); toggles.addWidget(self.web); toggles.addStretch(1)
        self.usage = QLabel(); toggles.addWidget(self.usage); layout.addLayout(toggles)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['词', '圈子', '现在的意思', '来源', '见过几次', '更新'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemSelectionChanged.connect(self.select); layout.addWidget(self.table)
        self.details = QPlainTextEdit(); self.details.setReadOnly(True); self.details.setMaximumHeight(160); layout.addWidget(self.details)
        buttons = QHBoxLayout()
        for label, fn in (('删掉这个词', self.delete), ('刷新', self.refresh)):
            button = QPushButton(label); button.clicked.connect(fn); buttons.addWidget(button)
        buttons.addStretch(1); layout.addLayout(buttons)
        layout.addWidget(QLabel('群里的共同记忆（群梗、一起经历的事、群里的叫法）：'))
        self.groups = QComboBox(); self.groups.currentIndexChanged.connect(self.show_lore); layout.addWidget(self.groups)
        self.lore_list = QListWidget(); self.lore_list.setMaximumHeight(120); layout.addWidget(self.lore_list)
        forget = QPushButton('忘掉选中的这条'); forget.clicked.connect(self.forget_lore); layout.addWidget(forget)
        self.status = QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.refresh()

    def selected(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh(self):
        used = WebSearch(self.db, self.client).used_today()
        self.usage.setText(f"今天联网：搜索 {used['search']} 次、读页 {used['fetch']} 次")
        terms = [t for t in self.lexicon.terms() if t['is_jargon'] or t['current']]
        self.table.setRowCount(len(terms))
        for n, t in enumerate(terms):
            now = next((s for s in self.lexicon.senses(t['term'], include_old=False)), None)
            name = QTableWidgetItem(t['term']); name.setData(Qt.ItemDataRole.UserRole, t['term']); self.table.setItem(n, 0, name)
            values = [t['circle'], now['meaning'] if now else ('（还在看大家怎么用）' if t['is_jargon'] else '（普通词）'),
                      (ORIGINS.get(now['origin'], now['origin']) + (f"：{now['source_name']}" if now and now['source_name'] else '')) if now else '',
                      str(t['seen']), time.strftime('%m-%d', time.localtime(t['updated_at']))]
            for col, value in enumerate(values, 1): self.table.setItem(n, col, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        channels = sorted({r['channel'] for r in self._lore_channels()})
        current = self.groups.currentData()
        self.groups.blockSignals(True); self.groups.clear()
        for ch in channels: self.groups.addItem('群 '+ch.split(':')[-1][-4:].rjust(4, '*'), ch)
        if current in channels: self.groups.setCurrentIndex(channels.index(current))
        self.groups.blockSignals(False); self.show_lore()

    def _lore_channels(self):
        with self.db.transaction() as c:
            return [dict(r) for r in c.execute('SELECT DISTINCT channel FROM group_lore WHERE character_id=?', (self.character_id,))]

    def select(self):
        term = self.selected()
        if not term: return
        lines = []
        for s in self.lexicon.senses(term):
            state = '现在' if s['valid_to'] is None else f"已过时（{when(s['valid_to'])} 起不再这么用）"
            scope = '只在这个群' if s['scope'] else '通用'
            lines.append(f"[{state}·{scope}] {s['meaning']}" + (f"（{s['tone']}）" if s['tone'] else '') +
                         (f" 用法：{s['usage']}" if s['usage'] else '') + f" —— {ORIGINS.get(s['origin'], s['origin'])}"
                         + (f" {s['source_name']} {s['source_url']}" if s['source_url'] else '') + f"，{when(s['valid_from'])} 学到")
        seen = self.lexicon.sightings(term)
        if seen:
            groups = sum(1 for s in seen if s['channel'].startswith('qq:group:'))
            lines.append(f"见过：{sum(s['count'] for s in seen)} 次，{groups} 个群，{len({p for s in seen for p in s['speakers']})} 个人用过")
        self.details.setPlainText('\n'.join(lines) or '还没有推断出意思。')

    def delete(self):
        term = self.selected()
        if not term: return
        self.lexicon.delete(term); self.status.setText(f'已删掉"{term}"。'); self.refresh()

    def show_lore(self):
        self.lore_list.clear()
        channel = self.groups.currentData()
        if not channel: return
        for r in self.lore.items(self.character_id, channel):
            item = QListWidgetItem(f"{KINDS.get(r['kind'], (r['kind'],))[0]}：{r['text']}（提到 {r['mentions']} 次 · 清晰度 {r['strength']:.1f}）")
            item.setData(Qt.ItemDataRole.UserRole, r['id']); self.lore_list.addItem(item)

    def forget_lore(self):
        item = self.lore_list.currentItem()
        if item is None: return
        self.lore.forget(item.data(Qt.ItemDataRole.UserRole)); self.status.setText('已忘掉这条。'); self.show_lore()
