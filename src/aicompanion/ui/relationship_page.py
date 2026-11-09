"""T15 人物关系页：她认识哪些人、对每个人的好感 / 熟悉 / 信任、最近发生的关系事件、知道的兴趣和共同经历。
手动调整的维度 72 小时内不会被自动打分改掉；可以重置档案（分数回初始、清空事件）或删除档案（主人只能重置）。"""
import time
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                               QDoubleSpinBox, QPlainTextEdit, QMessageBox, QCheckBox)
from aicompanion.relationship.service import RelationshipService, DIMENSIONS

NAMES = {'affinity': '好感', 'familiarity': '熟悉', 'trust': '信任'}
KINDS = {'care': '关心她', 'friendly': '友好交流', 'help_her': '帮了她', 'respect_boundary': '尊重她的边界', 'shared_achievement': '一起做成事',
         'apology': '道歉', 'personal_share': '分享自己的事', 'insult': '辱骂', 'harassment': '骚扰', 'deception': '欺骗', 'attack': '恶意攻击',
         'boundary_violation': '反复越界', 'chat': '聊天', 'question': '提问', 'disagreement': '意见不同', 'news': '分享新闻',
         'emotion': '表达情绪', 'request': '请她做事'}
NOTES = {'low_confidence': '把握不够，不计分', 'duplicate': '重复', 'daily_cap': '今天已到上限', 'manual_hold': '手动调整后暂停',
         'first_negative_halved': '第一次、把握不大，减半'}


class RelationshipPage(QWidget):
    def __init__(self, database, character_id, parent=None, relationships=None):
        super().__init__(parent)
        self.db, self.character_id = database, character_id
        self.service = relationships or RelationshipService(database)
        layout = QVBoxLayout(self)
        hint = QLabel('她对每个人的感觉由真实聊天慢慢变化（模型只判断发生了什么，分数按固定规则算、每天有上限）。'
                      '手动改过的数值 72 小时内不会被自动改掉。关系只影响她怎么相处、愿不愿意帮忙，不给任何人额外权限。')
        hint.setWordWrap(True); layout.addWidget(hint)
        self.learning = QCheckBox('从聊天里自动理解关系变化（后台少量调用模型）')
        self.learning.setChecked(bool(database.get_setting('relationship_learning', True)))
        self.learning.toggled.connect(lambda on: database.set_setting('relationship_learning', bool(on))); layout.addWidget(self.learning)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(['名称', '平台身份', '好感', '熟悉', '信任', '最近互动', '互动次数'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemSelectionChanged.connect(self.select); layout.addWidget(self.table)
        edit = QHBoxLayout(); self.spins = {}
        for d in DIMENSIONS:
            spin = QDoubleSpinBox(); spin.setRange(0, 100); spin.setDecimals(1); spin.setSingleStep(5)
            self.spins[d] = spin; edit.addWidget(QLabel(NAMES[d])); edit.addWidget(spin)
        for label, fn in (('保存调整', self.save), ('重置档案', self.reset), ('删除档案', self.delete), ('刷新', self.refresh)):
            button = QPushButton(label); button.clicked.connect(fn); edit.addWidget(button)
        layout.addLayout(edit)
        self.details = QPlainTextEdit(); self.details.setReadOnly(True); self.details.setMaximumHeight(180); layout.addWidget(self.details)
        self.status = QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.refresh()

    def selected(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh(self):
        people = self.service.people(self.character_id)
        self.table.setRowCount(len(people))
        for n, p in enumerate(people):
            name = QTableWidgetItem(('★ ' if p['is_owner'] else '')+(p['display_name'] or '（没有昵称）'))
            name.setData(Qt.ItemDataRole.UserRole, p['person_id']); self.table.setItem(n, 0, name)
            ids = '；'.join(f"{i['platform']}:{i['platform_user_id']}" for i in p['identities'])
            values = [ids, f"{p['affinity']:.0f}", f"{p['familiarity']:.0f}", f"{p['trust']:.0f}",
                      time.strftime('%m-%d %H:%M', time.localtime(p['last_seen'])), str(p['interactions'])]
            for col, value in enumerate(values, 1): self.table.setItem(n, col, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        if not people: self.details.setPlainText('还没有认识的人。和她聊几句、或者在 QQ 上有人跟她说话后，这里会出现档案。')

    def select(self):
        pid = self.selected()
        p = self.service.profile(self.character_id, pid) if pid else None
        if not p: return
        for d in DIMENSIONS: self.spins[d].setValue(p[d])
        lines = []
        if p['nicknames']: lines.append('用过的昵称：'+'、'.join(p['nicknames']))
        held = [NAMES[d] for d, at in p['manual'].items() if time.time()-float(at or 0) < 72*3600]
        if held: lines.append('手动调整过（72 小时内不自动改）：'+'、'.join(held))
        if p['interests']: lines.append('知道的兴趣：'+'、'.join(i['text'] for i in p['interests']))
        if p['experiences']: lines.append('共同经历：'+'；'.join(i['text'] for i in p['experiences']))
        lines.append('最近的关系事件：')
        for e in self.service.events(self.character_id, pid, 15):
            change = '，'.join(f"{NAMES[d]}{e['d_'+d]:+.1f}" for d in DIMENSIONS if e['d_'+d]) or '不变'
            lines.append(f"  {time.strftime('%m-%d %H:%M', time.localtime(e['created_at']))} {KINDS.get(e['kind'], e['kind'])}"
                         f"（把握 {e['confidence']:.1f}）{change}" + (f" · {NOTES.get(e['note'], e['note'])}" if e['note'] else '')
                         + (f" · {e['evidence']}" if e['evidence'] else ''))
        self.details.setPlainText('\n'.join(lines))

    def save(self):
        pid = self.selected()
        if not pid: return
        p = self.service.profile(self.character_id, pid)
        changed = {d: self.spins[d].value() for d in DIMENSIONS if abs(self.spins[d].value()-p[d]) >= 0.05}
        if not changed: self.status.setText('没有改动。'); return
        self.service.adjust(self.character_id, pid, **changed)
        self.status.setText('已保存：'+'、'.join(NAMES[d] for d in changed)+'；72 小时内不会被自动改掉。'); self.refresh()

    def reset(self):
        pid = self.selected()
        if not pid: return
        if QMessageBox.question(self, '重置档案', '分数回到初始值，清空关系事件、兴趣和共同经历。确定吗？') != QMessageBox.StandardButton.Yes: return
        self.service.reset(self.character_id, pid); self.status.setText('已重置。'); self.refresh()

    def delete(self):
        pid = self.selected()
        if not pid: return
        if QMessageBox.question(self, '删除档案', '删除这个人的档案、平台身份和所有关系记录，不能恢复。确定吗？') != QMessageBox.StandardButton.Yes: return
        try: self.service.delete(pid)
        except ValueError as error: self.status.setText(str(error)); return
        self.status.setText('已删除。'); self.refresh()
