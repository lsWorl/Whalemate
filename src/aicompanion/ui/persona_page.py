"""设置 → 角色与形象 → 人格（计划书 v4 功能二）：新建、编辑、复制、切换、删除、导入导出、历史版本、预览说话。

人格改的是“她怎么想、怎么说”，不改名字、形象、声音，也不动记忆和关系。保存即生效（当前人格下一句就用新内容）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit,
                               QPushButton, QSlider, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from aicompanion.domain.persona import WARN_CHARS, PersonaError, export_own, export_sillytavern, prompt_extras, read_card
from aicompanion.services.persona_service import PersonaService

PREVIEW_ASK = '今天加班到好晚，好累啊'


class PersonaEditor(QWidget):
    def __init__(self, controller, pack_persona=None, parent=None, run=None):
        super().__init__(parent)
        self.controller, self.db = controller, controller.database
        self.cid = controller.character.id
        self.pack_persona = pack_persona
        self.service = PersonaService(self.db)
        self.service.ensure(controller.character, pack_persona)
        self.run = run or self._run_in_background; self.workers = set()
        self.current_id = None; self._loading = False; self.dirty = False
        self.preview_seq = 0   # 每次预览、每次换人格都加一；回来的结果对不上就丢掉
        box = QVBoxLayout(self); box.setContentsMargins(0, 0, 0, 0); box.setSpacing(8)

        # ---- 选择与管理 ----
        pick = QHBoxLayout()
        self.picker = QComboBox(); self.picker.currentIndexChanged.connect(self._picked)
        pick.addWidget(QLabel('人格')); pick.addWidget(self.picker, 1)
        self.use_button = QPushButton('设为当前'); self.use_button.clicked.connect(self.use); pick.addWidget(self.use_button)
        more = QPushButton('管理 ▾'); menu = QMenu(more)
        for text, slot in (('新建空白人格', self.new), ('复制这个人格', self.copy), ('删除这个人格', self.delete), (None, None),
                           ('导入（本项目格式 / SillyTavern 角色卡）…', self.import_file),
                           ('导出为本项目格式…', lambda: self.export_file('own')), ('导出为 SillyTavern 角色卡…', lambda: self.export_file('st')),
                           (None, None), ('历史版本…', self.history)):
            if text is None: menu.addSeparator()
            else: menu.addAction(text, slot)
        more.setMenu(menu); pick.addWidget(more)
        box.addLayout(pick)
        self.update_note = QWidget(); note_row = QHBoxLayout(self.update_note); note_row.setContentsMargins(0, 0, 0, 0)
        note = QLabel('角色包里的内置人格有新版本；你改过内置人格，所以没有自动替换。'); note.setObjectName('Warn'); note.setWordWrap(True)
        restore = QPushButton('恢复内置原文'); restore.clicked.connect(self.restore_builtin)
        note_row.addWidget(note, 1); note_row.addWidget(restore); box.addWidget(self.update_note)

        # ---- 编辑 ----
        form = QFormLayout(); form.setVerticalSpacing(8)
        self.title = QLineEdit(); self.title.setPlaceholderText('例如：默认、傲娇一点、工作模式')
        self.description = QPlainTextEdit(); self.description.setMinimumHeight(110)
        self.description.setPlaceholderText('她是什么样的人：性格、喜欢什么、忌讳什么（和你的关系在下面“和你的关系”里设）')
        self.style = QPlainTextEdit(); self.style.setMinimumHeight(80); self.style.setPlaceholderText('她怎么说话：口头禅、语气、长短、称呼')
        self.background = QPlainTextEdit(); self.background.setMaximumHeight(80); self.background.setPlaceholderText('可选：她的来历、所处的情境')
        form.addRow('人格名称', self.title)
        # ---- 和你的关系（负责人 2026-11-11：不一定是父女，女朋友、妹妹、朋友都行，也可以自己写）----
        from aicompanion.domain.bond import PRESETS
        self.bond_preset = QComboBox()
        for key, value in PRESETS.items(): self.bond_preset.addItem(value['label'], key)
        self.bond_call = QLineEdit(); self.bond_call.setPlaceholderText('她平时怎么叫你，例如：爹、亲爱的、哥哥、你的名字')
        self.bond_refer = QLineEdit(); self.bond_refer.setPlaceholderText('说明里怎么指你，例如：你爹、你的恋人、你哥')
        self.bond_tag = QLineEdit(); self.bond_tag.setMaxLength(6); self.bond_tag.setPlaceholderText('群聊里标在你消息前，例如：爹、对象、哥')
        self.bond_relation = QLineEdit(); self.bond_relation.setMaxLength(8); self.bond_relation.setPlaceholderText('关系名，例如：父女、情侣、兄妹')
        self.bond_how = QPlainTextEdit(); self.bond_how.setMaximumHeight(90)
        self.bond_how.setPlaceholderText('你们怎么相处。可以写 {叫法} {指代} 代替上面的称呼，换称呼时不用改这段；角色设定、说话风格里也能用')
        self.bond_claims = QLineEdit(); self.bond_claims.setPlaceholderText('外人自称这些身份时她会生气，用 | 分隔，例如：男朋友|老公（可空）')
        bond_row = QHBoxLayout()
        for label, widget in (('叫你', self.bond_call), ('指代', self.bond_refer), ('群里标记', self.bond_tag), ('关系', self.bond_relation)):
            bond_row.addWidget(QLabel(label)); bond_row.addWidget(widget, 1)
        form.addRow('和你的关系', self.bond_preset); form.addRow('', bond_row); form.addRow('相处方式', self.bond_how)
        form.addRow('冒充提醒', self.bond_claims)
        self.bond_preset.currentIndexChanged.connect(self._bond_preset_changed)
        form.addRow('角色设定', self.description); form.addRow('说话风格', self.style)
        form.addRow('背景', self.background)
        self.examples = QTableWidget(0, 2); self.examples.setHorizontalHeaderLabels(['对方说（可空）', '她这样回']); self._table(self.examples)
        form.addRow('说话示例', self._with_buttons(self.examples, '她只学语气和分寸，不会照抄原句'))
        self.interests = QLineEdit(); self.interests.setPlaceholderText('用逗号分隔，例如：深海纪录片, 冰奶茶, 二次元')
        form.addRow('兴趣', self.interests)
        self.touchy = self._slider(); self.forgiving = self._slider()
        temper = QHBoxLayout(); temper.addWidget(QLabel('容易不高兴')); temper.addWidget(self.touchy, 1)
        temper.addWidget(QLabel('消气快')); temper.addWidget(self.forgiving, 1)
        form.addRow('情绪倾向', temper)
        self.lore = QTableWidget(0, 3); self.lore.setHorizontalHeaderLabels(['关键词（逗号分隔）', '设定内容', '每次都带']); self._table(self.lore)
        form.addRow('世界书', self._with_buttons(self.lore, '对话里出现关键词时才带上这条设定；勾“每次都带”的常驻'))
        self.notes = QLineEdit(); self.notes.setPlaceholderText('作者备注，只给自己看，不会发给模型'); form.addRow('备注', self.notes)
        box.addLayout(form)
        self.size = QLabel(); self.size.setWordWrap(True); self.size.setObjectName('Hint'); box.addWidget(self.size)

        # ---- 预览与保存 ----
        preview = QHBoxLayout()
        self.ask = QLineEdit(PREVIEW_ASK); self.ask.setToolTip('预览会用聊天模型按编辑中的内容回一句（会产生少量用量）')
        go = QPushButton('预览她会怎么说'); go.clicked.connect(self.preview)
        preview.addWidget(QLabel('试着对她说')); preview.addWidget(self.ask, 1); preview.addWidget(go); box.addLayout(preview)
        self.preview_text = QLabel(''); self.preview_text.setWordWrap(True); self.preview_text.setObjectName('Hint')
        self.preview_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); box.addWidget(self.preview_text)
        save_row = QHBoxLayout(); self.status = QLabel(''); self.status.setObjectName('Hint'); self.status.setWordWrap(True)
        save = QPushButton('保存这个人格'); save.setObjectName('Primary'); save.clicked.connect(self.save)
        save_row.addWidget(self.status, 1); save_row.addWidget(save); box.addLayout(save_row)

        for widget in (self.title, self.interests, self.notes, self.bond_call, self.bond_refer, self.bond_tag, self.bond_relation,
                       self.bond_claims): widget.textEdited.connect(self._changed)
        for widget in (self.description, self.style, self.background, self.bond_how): widget.textChanged.connect(self._changed)
        for widget in (self.examples, self.lore): widget.itemChanged.connect(self._changed)
        for widget in (self.touchy, self.forgiving): widget.valueChanged.connect(self._changed)
        self.reload()

    # ---- 小部件 ----
    @staticmethod
    def _table(table):
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); table.setMinimumHeight(110)
        table.verticalHeader().setVisible(False)

    def _with_buttons(self, table, hint):
        wrap = QWidget(); col = QVBoxLayout(wrap); col.setContentsMargins(0, 0, 0, 0); col.addWidget(table)
        row = QHBoxLayout(); add = QPushButton('加一行'); remove = QPushButton('删除选中行')
        add.clicked.connect(lambda: self._add_row(table)); remove.clicked.connect(lambda: self._remove_rows(table))
        text = QLabel(hint); text.setObjectName('Hint'); row.addWidget(text, 1); row.addWidget(add); row.addWidget(remove); col.addLayout(row)
        return wrap

    @staticmethod
    def _slider():
        slider = QSlider(Qt.Orientation.Horizontal); slider.setRange(0, 100); slider.setValue(50); return slider

    def _add_row(self, table, values=()):
        n = table.rowCount(); table.insertRow(n)
        for col in range(table.columnCount()):
            if table is self.lore and col == 2:
                item = QTableWidgetItem(''); item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if (values[2] if len(values) > 2 else False) else Qt.CheckState.Unchecked)
            else:
                item = QTableWidgetItem(values[col] if col < len(values) else '')
            table.setItem(n, col, item)
        self._changed()

    def _remove_rows(self, table):
        for row in sorted({i.row() for i in table.selectedIndexes()}, reverse=True): table.removeRow(row)
        self._changed()

    # ---- 展示 ----
    def reload(self, select=None):
        personas = self.service.personas(self.cid); active = self.service.active(self.cid)
        self._loading = True
        self.picker.clear()
        for p in personas:
            mark = '✓ ' if active and p.id == active.id else '　'
            self.picker.addItem(mark+p.title+('（内置）' if p.builtin and '内置' not in p.title else ''), p.id)
        target = select or self.current_id or (active.id if active else None)
        self.picker.setCurrentIndex(max(0, self.picker.findData(target)))
        self._loading = False
        self._load(self.picker.currentData())
        self.update_note.setVisible(bool(self.pack_persona) and self.service.pack_update_pending(self.cid))

    def _picked(self, *args):
        if self._loading: return
        if self.dirty and self.current_id and self.picker.currentData() != self.current_id:
            answer = QMessageBox.question(self, '没保存', '这个人格改了还没保存，要先保存吗？',
                                          QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard)
            if answer == QMessageBox.StandardButton.Save:
                target = self.picker.currentData(); self._loading = True
                self.picker.setCurrentIndex(self.picker.findData(self.current_id)); self._loading = False
                if not self.save(): return
                self.reload(select=target); return
        self._load(self.picker.currentData())

    def _settle_draft(self, why: str) -> bool:
        """编辑器内容要被换掉前，先处理没保存的改动（Codex PR #35 R2）：保存 / 放弃 / 取消。返回 False 表示别继续。"""
        if not (self.dirty and self.current_id): return True
        answer = QMessageBox.question(self, '没保存', f'这个人格改了还没保存。{why}要先保存吗？',
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Save: return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def _load(self, persona_id):
        persona = self.service.get(persona_id) if persona_id else None
        if persona is None: return
        self._loading = True
        if persona.id != self.current_id:
            # 换了人格：上一个人格的预览不再算数（Codex PR #35 复审建议），还在路上的那次也作废
            self.preview_seq += 1; self.preview_text.setText('')
        self.current_id = persona.id
        self.title.setText(persona.title); self.description.setPlainText(persona.description)
        self.style.setPlainText(persona.speaking_style); self.background.setPlainText(persona.background)
        self.examples.setRowCount(0); self.lore.setRowCount(0)
        for e in persona.examples: self._add_row(self.examples, (e.user, e.reply))
        for e in persona.lore: self._add_row(self.lore, ('，'.join(e.keys), e.content, e.always))
        self.interests.setText('，'.join(persona.interests)); self.notes.setText(persona.notes)
        temper = dict(persona.temper)
        self.touchy.setValue(int(temper.get('touchy', .5)*100)); self.forgiving.setValue(int(temper.get('forgiving', .5)*100))
        self._show_bond(persona.relation())
        active = self.service.active(self.cid)
        self.use_button.setEnabled(not (active and active.id == persona.id))
        self.use_button.setText('正在使用' if active and active.id == persona.id else '设为当前')
        self._loading = False; self.dirty = False; self._show_size(); self.status.setText('')

    def content(self) -> dict:
        split = lambda text: [t.strip() for t in text.replace('，', ',').split(',') if t.strip()]
        cell = lambda table, r, c: (table.item(r, c).text() if table.item(r, c) else '')
        return {'title': self.title.text(), 'description': self.description.toPlainText(), 'speaking_style': self.style.toPlainText(),
                'background': self.background.toPlainText(),
                'examples': [{'user': cell(self.examples, r, 0), 'reply': cell(self.examples, r, 1)} for r in range(self.examples.rowCount())],
                'interests': split(self.interests.text()),
                'temper': {'touchy': self.touchy.value()/100, 'forgiving': self.forgiving.value()/100},
                'lore': [{'keys': split(cell(self.lore, r, 0)), 'content': cell(self.lore, r, 1),
                          'always': bool(self.lore.item(r, 2) and self.lore.item(r, 2).checkState() == Qt.CheckState.Checked)}
                         for r in range(self.lore.rowCount())],
                'notes': self.notes.text(),
                'bond': {'preset': self.bond_preset.currentData(), 'call': self.bond_call.text(), 'refer': self.bond_refer.text(),
                         'tag': self.bond_tag.text(), 'relation': self.bond_relation.text(), 'how': self.bond_how.toPlainText(),
                         'claims': self.bond_claims.text()}}

    def _show_bond(self, bond):
        self.bond_preset.blockSignals(True)
        self.bond_preset.setCurrentIndex(max(0, self.bond_preset.findData(bond.preset)))
        self.bond_preset.blockSignals(False)
        self.bond_call.setText(bond.call); self.bond_refer.setText(bond.refer); self.bond_tag.setText(bond.tag)
        self.bond_relation.setText(bond.relation); self.bond_how.setPlainText(bond.how); self.bond_claims.setText(bond.claims)

    def _bond_preset_changed(self, *args):
        """换了预设：各字段填成预设值（之后还能改）。选“自定义”时保留现在填的，只是不再跟预设走。"""
        from aicompanion.domain.bond import from_preset
        key = self.bond_preset.currentData()
        if key == 'custom': self._changed(); return
        self._show_bond(from_preset(key)); self._changed()

    def _changed(self, *args):
        if self._loading: return
        self.dirty = True; self._show_size(); self.status.setText('有改动，记得保存')

    def _show_size(self):
        persona = self.service.get(self.current_id)
        if persona is None: return
        try: chars = persona.with_content(self.content()).chars()
        except PersonaError: return
        text = f'这个人格每句话大约要带 {chars} 字（约 {round(chars*0.7)} 个词元）。'
        if chars > WARN_CHARS:
            text += '内容偏长：每句话都会带上，回复会变慢、变贵。可以把不常用的设定挪进世界书（只在提到时才带上）。'
            self.size.setObjectName('Warn')
        else: self.size.setObjectName('Hint')
        self.size.setText(text); self.size.style().unpolish(self.size); self.size.style().polish(self.size)

    # ---- 操作 ----
    def save(self) -> bool:
        persona = self.service.get(self.current_id) if self.current_id else None
        if persona is None: return True
        if not self.dirty: return True
        try:
            content = self.content()
            if not content['description'].strip(): raise PersonaError('角色设定不能为空')
            saved = self.service.save(persona.with_content(content))
        except PersonaError as error:
            QMessageBox.warning(self, '人格没有保存', str(error)); return False
        self._sync_controller()
        self.dirty = False; self.reload(select=saved.id); self.status.setText('已保存'+('，下一句话就用新内容' if self._is_active(saved.id) else ''))
        return True

    def use(self):
        if not self.save(): return
        self.service.switch(self.cid, self.current_id); self._sync_controller()
        self.reload(select=self.current_id); self.status.setText('已切换，下一句话就是这个人格；记忆和关系都还在')

    def new(self):
        if not self.save(): return
        persona = self.service.create(self.cid, {'title': '新人格', 'description': '', 'speaking_style': ''})
        self.reload(select=persona.id); self.title.setFocus()

    def copy(self):
        if not self.save(): return
        persona = self.service.duplicate(self.current_id); self.reload(select=persona.id)

    def delete(self):
        persona = self.service.get(self.current_id)
        if persona is None: return
        if QMessageBox.question(self, '删除人格', f'删除“{persona.title}”和它的历史版本？记忆和关系不受影响。') != QMessageBox.StandardButton.Yes: return
        try: self.service.delete(persona.id)
        except PersonaError as error: QMessageBox.warning(self, '没有删除', str(error)); return
        self.current_id = None; self.dirty = False; self.reload()

    def import_file(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, '导入人格', '', '人格或角色卡 (*.json *.png)')
        if not path: return
        try:
            with open(path, 'rb') as file: raw = file.read(5*1024*1024)
            content = read_card(raw)
        except (OSError, PersonaError) as error:
            QMessageBox.warning(self, '没有导入', str(error)); return   # 读不进来：编辑中的草稿原样留着
        if not self._settle_draft('导入后编辑器会换成导入的人格。'): return
        try: persona = self.service.import_content(self.cid, content)
        except PersonaError as error:
            QMessageBox.warning(self, '没有导入', str(error)); return
        self.dirty = False; self.reload(select=persona.id)
        self.status.setText(f'已导入为新人格“{persona.title}”；看过没问题再点“设为当前”。')

    def export_file(self, kind, path=None):
        import json
        if not self.save(): return
        persona = self.service.get(self.current_id)
        if persona is None: return
        if not path:
            suffix = '.persona.json' if kind == 'own' else '.card.json'
            path, _ = QFileDialog.getSaveFileName(self, '导出人格', persona.title+suffix, 'JSON (*.json)')
        if not path: return
        data = export_own(persona) if kind == 'own' else export_sillytavern(persona, self.controller.character.name)
        try:
            with open(path, 'w', encoding='utf-8') as file: json.dump(data, file, ensure_ascii=False, indent=2)
        except OSError as error:
            QMessageBox.warning(self, '没有导出', str(error)); return
        self.status.setText('已导出（只含人格本身，不含聊天记录和记忆）')

    def history(self):
        versions = self.service.versions(self.current_id)
        if not versions: return
        dialog = QDialog(self); dialog.setWindowTitle('历史版本'); dialog.resize(560, 420)
        col = QVBoxLayout(dialog); listing = QListWidget(); col.addWidget(listing)
        import time as _time
        for v in versions:
            stamp = _time.strftime('%m-%d %H:%M', _time.localtime(v['saved_at']))
            item = QListWidgetItem(f"{stamp}  {v['note'] or '保存'}  ·  {v['content'].get('title', '')}  ·  {len(v['content'].get('description', ''))} 字")
            item.setData(Qt.ItemDataRole.UserRole, v['id']); listing.addItem(item)
        preview = QPlainTextEdit(); preview.setReadOnly(True); preview.setMaximumHeight(150); col.addWidget(preview)
        listing.currentRowChanged.connect(lambda r: preview.setPlainText(versions[r]['content'].get('description', '') if r >= 0 else ''))
        buttons = QDialogButtonBox(); back = buttons.addButton('回退到这个版本', QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close); buttons.rejected.connect(dialog.reject); buttons.accepted.connect(dialog.accept)
        col.addWidget(buttons); listing.setCurrentRow(0)
        if dialog.exec() != QDialog.DialogCode.Accepted or listing.currentItem() is None: return
        self.service.revert(self.current_id, listing.currentItem().data(Qt.ItemDataRole.UserRole)); self._sync_controller()
        self.dirty = False; self.reload(select=self.current_id); self.status.setText('已回退（回退前的内容也留了一个版本）')

    def restore_builtin(self):
        if not self.pack_persona: return
        persona = self.service.restore_builtin(self.cid, self.pack_persona); self._sync_controller()
        self.dirty = False; self.reload(select=persona.id)

    def preview(self):
        """按编辑中的内容（还没保存也行）让聊天模型回一句。在后台线程里跑，不卡界面。"""
        from aicompanion.domain.cancellation import CancellationToken
        from aicompanion.llm.base import ChatTurn
        persona = self.service.get(self.current_id)
        if persona is None: return
        try: draft = persona.with_content(self.content())
        except PersonaError as error: self.preview_text.setText(str(error)); return
        ask = self.ask.text().strip() or PREVIEW_ASK
        name = self.controller.character.name
        bond = draft.relation()
        prompt = bond.fill(f'你正在扮演 {name}。\n人设：{draft.description}\n{bond.prompt()}\n说话风格：{draft.speaking_style}\n'
                           + prompt_extras(draft, ask) + '\n这是一次说话风格的预览，像平时聊天那样自然地回一句。', name)
        # 预览也是一次聊天请求，按陪伴用途走日金额额度（Codex PR #35 R1）；到限就不发
        from aicompanion.usage.meter import Quota
        state, note = Quota(self.db).check('companion')
        if state == 'stop': self.preview_text.setText('现在没法预览：'+note); return
        try: client = self.controller.client(self.cid, 'companion')
        except Exception as error: self.preview_text.setText('现在没法预览：'+str(error)); return
        self.preview_seq += 1; seq = self.preview_seq; title = draft.title
        self.preview_text.setText(f'“{title}”在想怎么回…')
        def show(text):
            if seq == self.preview_seq: self.preview_text.setText(text)   # 期间换了人格或又点了预览：旧结果不显示
        self.run(lambda token: ''.join(client.generate_reply_stream([ChatTurn('user', ask)], prompt, token or CancellationToken())).strip(),
                 lambda text: show(f'{name}（{title}）：{text[:400]}'),
                 lambda error: show('预览失败：'+str(error)[:200]))

    # ---- 内部 ----
    def _is_active(self, persona_id) -> bool:
        active = self.service.active(self.cid); return bool(active) and active.id == persona_id

    def _sync_controller(self):
        """当前人格改了以后，聊天控制器手里的角色对象也换成最新的（桌面、QQ、语音、办事都读它）。"""
        fresh = next((c for c in self.db.characters() if c.id == self.cid), None)
        if fresh is not None and self.controller.character.id == self.cid: self.controller.character = fresh

    def _run_in_background(self, function, ok, fail):
        # 线程挂在主窗口上（和图库一样）：预览还没回来就关掉设置也不会因为页面销毁而崩溃
        from PySide6.QtWidgets import QApplication
        from aicompanion.ui.chat_worker import TaskWorker
        owner = self.window().parentWidget()
        worker = TaskWorker(function, owner or QApplication.instance()); self.workers.add(worker)
        if owner is not None and hasattr(owner, 'tasks'):
            owner.tasks.add(worker); worker.finished.connect(lambda: owner._task_finished(worker))
        else: worker.finished.connect(worker.deleteLater)
        worker.succeeded.connect(ok); worker.failed.connect(fail)
        worker.finished.connect(lambda w=worker: self.workers.discard(w)); worker.start()
