"""设置 → 模型与 Key：账户与 Key、每种能力用哪个模型（可按角色单独指定）、Breeze Blue 音色、测试连接。

计划书 v4 功能一。界面只负责展示和收集输入，所有操作交给 providers.admin.ModelAdmin。
Key 输入框始终遮蔽；保存后只显示“已加密保存 / 未设置”，不回显 Key 的任何部分。
"""
from __future__ import annotations


from PySide6.QtCore import Signal, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from aicompanion.providers.admin import CAPABILITY_LABELS, TTS_LANGUAGES, ModelAdmin
from aicompanion.providers.models import PROVIDER_PRESETS

PRESET_CHOICES = (('deepseek', 'DeepSeek'), ('minimax-global', 'MiniMax 国际站（minimax.io）'),
                  ('minimax-cn', 'MiniMax 国内站（minimaxi.com）'), ('breezeblue', 'Breeze Blue（语音合成）'),
                  ('', '其他 OpenAI 兼容服务'))
KEY_HINTS = {
    'minimax-global': '国际站和国内站是两套账户，Key 不通用；订阅（M Plan）Key 与按量 Key 也不通用。',
    'minimax-cn': '国内站地址以你的账户页面为准（不同文档写作 api.minimaxi.com 或 api.minimax.cn）。订阅 Key 与按量 Key 不通用。',
    'breezeblue': '在 Breeze Blue 控制台创建 Key；音色 ID（voc_ 开头）在控制台的音色列表里。',
}


class AccountDialog(QDialog):
    """添加账户或更换 Key。编辑时 Key 留空表示不修改。"""

    def __init__(self, parent=None, editing=None):
        super().__init__(parent)
        self.setWindowTitle('修改账户' if editing else '添加账户')
        form = QFormLayout(self)
        self.preset = QComboBox()
        for value, label in PRESET_CHOICES: self.preset.addItem(label, value)
        self.label = QLineEdit(); self.base_url = QLineEdit()
        self.key = QLineEdit(); self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText('留空表示不修改' if editing else '粘贴 Key（加密保存，不会显示）')
        self.key_env = QLineEdit(); self.key_env.setPlaceholderText('或者：保存 Key 的环境变量名，例如 DEEPSEEK_API_KEY')
        self.hint = QLabel(); self.hint.setWordWrap(True)
        if editing:
            self.preset.setEnabled(False)
            self.label.setText(editing.label); self.base_url.setText(editing.base_url)
        else:
            form.addRow('服务', self.preset)
        form.addRow('名称', self.label); form.addRow('地址', self.base_url)
        form.addRow('Key', self.key); form.addRow('', self.key_env); form.addRow(self.hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); form.addRow(buttons)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        if not editing: self._preset_changed()

    def _preset_changed(self, *args):
        preset = PROVIDER_PRESETS.get(self.preset.currentData())
        self.label.setText(preset.label if preset else '')
        self.base_url.setText(preset.base_url if preset else 'https://')
        self.hint.setText(KEY_HINTS.get(self.preset.currentData(), '自定义服务按 OpenAI Chat Completions 接口调用；能力需要你自己声明。'))


class ModelDialog(QDialog):
    """给账户添加一个模型：模型名照填，支持哪些能力由用户声明。"""

    def __init__(self, accounts, parent=None):
        super().__init__(parent)
        self.setWindowTitle('添加模型')
        form = QFormLayout(self)
        self.account = QComboBox()
        for account in accounts: self.account.addItem(account.label, account.id)
        self.model_id = QLineEdit(); self.model_id.setPlaceholderText('服务商文档里的模型名，例如 deepseek-flash')
        self.caps = {}
        row = QHBoxLayout()
        for cap, label in (('chat', '聊天'), ('vision', '识图'), ('tools', '工具调用'), ('tts', '语音合成')):
            box = QCheckBox(label); box.setChecked(cap == 'chat'); self.caps[cap] = box; row.addWidget(box)
        form.addRow('账户', self.account); form.addRow('模型名', self.model_id); form.addRow('支持', row)
        note = QLabel('服务返回的模型列表不等于能力证明；没有确认支持的能力不要勾选。'); note.setWordWrap(True); form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); form.addRow(buttons)

    def capabilities(self):
        return tuple(cap for cap, box in self.caps.items() if box.isChecked())


class ModelPage(QWidget):
    persisted = Signal()
    save_failed = Signal(str)

    def __init__(self, router, characters, current_character_id, parent=None, speech_output=None, run=None):
        """characters：[(角色 ID, 名字)]；run：后台执行网络请求的函数（默认 TaskWorker），测试里可换成同步执行。"""
        super().__init__(parent)
        self.pending = set()
        self.errors = {}
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(500)
        self.debounce.timeout.connect(self.flush)
        self.admin = ModelAdmin(router); self.router = router
        self.characters = list(characters); self.current = current_character_id
        self.speech_output = speech_output; self.run = run or self._run_in_background; self.workers = set()
        layout = QVBoxLayout(self)

        accounts = QGroupBox('账户与 Key'); a = QVBoxLayout(accounts)
        self.table = QTableWidget(0, 4); self.table.setHorizontalHeaderLabels(['名称', '服务', '地址', 'Key'])
        from PySide6.QtWidgets import QHeaderView
        header = self.table.horizontalHeader()
        for col, mode in enumerate((QHeaderView.ResizeMode.ResizeToContents, QHeaderView.ResizeMode.ResizeToContents,
                                    QHeaderView.ResizeMode.Stretch, QHeaderView.ResizeMode.ResizeToContents)):
            header.setSectionResizeMode(col, mode)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); a.addWidget(self.table)
        row = QHBoxLayout()
        for text, slot in (('添加账户', self.add_account), ('修改 / 换 Key', self.edit_account), ('删除账户', self.remove_account),
                           ('添加模型', self.add_model)):
            button = QPushButton(text); button.clicked.connect(slot); row.addWidget(button)
        a.addLayout(row); layout.addWidget(accounts)

        choose = QGroupBox('每种能力用哪个模型'); c = QFormLayout(choose)
        self.scope = QComboBox(); self.scope.addItem('全局默认', None)
        for cid, name in self.characters: self.scope.addItem(f'角色：{name}', cid)
        self.scope.currentIndexChanged.connect(self._scope_changed); self._scope = None
        c.addRow('设置范围', self.scope)
        self.pickers = {}
        for cap in ('chat', 'vision', 'search', 'agent', 'tts'):
            box = QComboBox(); self.pickers[cap] = box; c.addRow(CAPABILITY_LABELS[cap], box)
            box.currentIndexChanged.connect(lambda *_, cap=cap: self._queue_save(cap))
            if cap == 'agent':
                hint = QLabel('办事只列出 Harness 能用的模型（DeepSeek、MiniMax）。注意：“全局默认”里的办事模型会用于所有没有单独设置的角色；'
                              '只想给某个角色换，请先把上面的“设置范围”切到那个角色再选。')
                hint.setWordWrap(True); hint.setStyleSheet('color: gray'); c.addRow('', hint)
        self.voice = QLineEdit(); self.voice.setPlaceholderText('音色 ID：Breeze Blue 如 voc_8rsb3nhb7645；MiniMax 如 Chinese (Mandarin)_Soft_Girl。不填用角色默认声线')
        self.language = QComboBox()
        for value, label in TTS_LANGUAGES: self.language.addItem(label, value)
        self.speed = QDoubleSpinBox(); self.speed.setRange(0.5, 2.0); self.speed.setSingleStep(0.05); self.speed.setValue(1.0)
        self.volume = QDoubleSpinBox(); self.volume.setRange(0.01, 2.0); self.volume.setSingleStep(0.05); self.volume.setValue(1.0)
        self.instructions = QLineEdit(); self.instructions.setPlaceholderText('固定语气说明（可选），例如：声音清亮，语速稍快')
        tts_row = QHBoxLayout(); tts_row.addWidget(QLabel('语言')); tts_row.addWidget(self.language)
        tts_row.addWidget(QLabel('语速')); tts_row.addWidget(self.speed); tts_row.addWidget(QLabel('音量')); tts_row.addWidget(self.volume)
        c.addRow('音色', self.voice); c.addRow('', tts_row); c.addRow('语气', self.instructions)
        # 选了就保存：切换范围、关闭设置都不会丢（实机反馈：退出后又变回“未设置”）
        self._loading = False
        for signal in (self.voice.textChanged, self.instructions.textChanged, self.language.currentIndexChanged,
                       self.speed.valueChanged, self.volume.valueChanged):
            signal.connect(lambda *_: self._queue_save('tts'))
        buttons = QHBoxLayout()
        for text, slot in (('测试聊天', self.test_chat), ('试听语音', self.test_tts)):
            button = QPushButton(text); button.clicked.connect(slot); buttons.addWidget(button)
        c.addRow(buttons)
        self.status = QLabel(); self.status.setWordWrap(True); c.addRow(self.status)
        layout.addWidget(choose)
        note = QLabel('选择后自动保存，下一句就生效。测试和试听会实际调用服务，产生少量费用。角色单独设置的项目优先；选“跟随全局”即恢复继承。'
                      '没有配置聊天模型时，桌宠以离线模拟模式运行。')
        note.setWordWrap(True); layout.addWidget(note); layout.addStretch()
        self.refresh()

    # ---- 展示 --------------------------------------------------------------------------------
    def refresh(self):
        accounts = self.admin.accounts()
        self.table.setRowCount(len(accounts))
        kinds = {'deepseek': 'DeepSeek', 'minimax': 'MiniMax', 'breezeblue': 'Breeze Blue', 'openai_compatible': '兼容服务'}
        for n, account in enumerate(accounts):
            for col, text in enumerate((account.label, kinds.get(account.kind, account.kind), account.base_url,
                                        self.admin.describe_key(account))):
                item = QTableWidgetItem(text)
                if col == 0: item.setData(256, account.id)
                self.table.setItem(n, col, item)
        self._load_scope(self.scope.currentData())

    def _selected_account(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(256) if item else None

    def _load_scope(self, character_id):
        """把这个范围（全局或某个角色）当前的选择填进下拉框。"""
        self._scope = character_id
        self._loading = True
        try: self._fill(character_id)
        finally: self._loading = False
        self._show_effective()

    def _fill(self, character_id):
        inherit = '跟随全局' if character_id else '未设置'
        for cap, box in self.pickers.items():
            box.blockSignals(True); box.clear(); box.addItem(inherit, '')
            if cap == 'search':
                box.addItem('维基百科（免费，不需要 Key）', 'wikipedia')
                for account in self.admin.accounts():
                    if account.kind == 'deepseek': box.addItem(f'DeepSeek 搜索 · {account.label}', 'deepseek:' + account.id)
            else:
                for model in self.admin.models(cap): box.addItem(self.admin.model_label(model), model.id)
            binding = self.admin.binding(cap, character_id)
            value = ''
            if binding:
                value = binding.model if cap != 'search' else (binding.backend + (':' + binding.provider if binding.backend == 'deepseek' else ''))
            index = box.findData(value); box.setCurrentIndex(max(0, index)); box.blockSignals(False)
        tts = self.admin.binding('tts', character_id)
        self.voice.setText(tts.voice if tts else '')
        self.language.setCurrentIndex(max(0, self.language.findData(tts.language if tts else 'zh')))
        self.speed.setValue(tts.speed if tts else 1.0); self.volume.setValue(tts.volume if tts else 1.0)
        self.instructions.setText(tts.instructions if tts else '')

    def _show_effective(self):
        cid = self._scope or self.current
        tts = self.router.tts_target(cid)
        self.status.setText(f'现在生效（{"当前角色" if not self._scope else "这个角色"}）：聊天 {self.router.label(cid)}；'
                            f'语音 {tts.label if tts else "未配置"}')

    def _scope_changed(self, *args):
        if not self.flush():
            self.scope.blockSignals(True)
            self.scope.setCurrentIndex(self.scope.findData(self._scope))
            self.scope.blockSignals(False)
            return
        self._load_scope(self.scope.currentData())

    # ---- 账户 --------------------------------------------------------------------------------
    def add_account(self):
        dialog = AccountDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        try:
            self.admin.add_account(dialog.preset.currentData(), label=dialog.label.text().strip(), base_url=dialog.base_url.text().strip(),
                                   key=dialog.key.text(), key_env=dialog.key_env.text())
        except Exception as error:
            QMessageBox.warning(self, '账户没有添加', str(error)); return
        self.refresh()
        self.persisted.emit()

    def edit_account(self):
        account_id = self._selected_account()
        if not account_id: return
        account = next(a for a in self.admin.accounts() if a.id == account_id)
        dialog = AccountDialog(self, editing=account)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        try:
            self.admin.update_account(account_id, label=dialog.label.text().strip(), base_url=dialog.base_url.text().strip(),
                                      key=dialog.key.text(), key_env=dialog.key_env.text())
        except Exception as error:
            QMessageBox.warning(self, '账户没有修改', str(error)); return
        self.refresh()
        self.persisted.emit()

    def remove_account(self):
        account_id = self._selected_account()
        if not account_id: return
        try: self.admin.remove_account(account_id)
        except Exception as error:
            QMessageBox.warning(self, '账户没有删除', str(error)); return
        self.refresh()
        self.persisted.emit()

    def add_model(self):
        accounts = self.admin.accounts()
        if not accounts:
            QMessageBox.information(self, '先添加账户', '请先添加一个账户。'); return
        dialog = ModelDialog(accounts, self)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        try: self.admin.add_model(dialog.account.currentData(), dialog.model_id.text(), dialog.capabilities())
        except Exception as error:
            QMessageBox.warning(self, '模型没有添加', str(error)); return
        self.refresh()
        self.persisted.emit()

    # ---- 选择与测试 --------------------------------------------------------------------------
    def _save_one(self, cap):
        cid = self._scope
        if cap == 'search':
            backend, _, provider = (self.pickers['search'].currentData() or '').partition(':')
            self.admin.choose('search', cid, backend=backend, provider=provider)
        elif cap == 'tts':
            self.admin.choose('tts', cid, self.pickers['tts'].currentData() or '', voice=self.voice.text(),
                              language=self.language.currentData(), speed=self.speed.value(), volume=self.volume.value(),
                              instructions=self.instructions.text())
        else:
            self.admin.choose(cap, cid, self.pickers[cap].currentData() or '')

    def _queue_save(self, capability):
        if not self._loading:
            self.pending.add(capability)
            self.debounce.start()

    def flush(self):
        self.debounce.stop()
        pending, self.pending = self.pending, set()
        for cap in sorted(pending):
            try:
                self._save_one(cap)
            except Exception as error:
                self.errors[cap] = f'{CAPABILITY_LABELS[cap]}还没有保存：{error}'
            else:
                self.errors.pop(cap, None)
        if self.errors:
            message = next(iter(self.errors.values()))
            self.status.setText(message)
            self.save_failed.emit(message)
            return False
        if pending:
            self._show_effective()
            self.persisted.emit()
        return True

    def apply(self) -> bool:
        """保存当前范围的全部选择（关闭设置、测试前调用）。出错时提示并返回 False，出错那一项不变。"""
        try:
            for cap in ('chat', 'vision', 'search', 'agent', 'tts'): self._save_one(cap)
        except Exception as error:
            QMessageBox.warning(self, '设置没有保存', str(error)); return False
        self._show_effective()
        return True

    def save(self) -> bool:
        return self.apply()

    def test_chat(self):
        # 选择已自动保存；这里不再整体保存，免得没填完的语音设置挡住聊天测试
        self.flush()
        cid = self._scope or self.current
        self.status.setText('正在测试聊天…')
        self.run(lambda token: self.admin.test_chat(cid, token),
                 lambda text: self.status.setText('聊天连接正常：' + text),
                 lambda error: self.status.setText('聊天测试失败：' + error))

    def test_tts(self):
        try: self._save_one('tts')      # 音色可能刚输入还没离开输入框
        except Exception as error:
            self.status.setText('语音合成还没有保存：' + str(error)); return
        cid = self._scope or self.current
        self.status.setText('正在合成试听…')

        def done(audio):
            import io, wave
            with wave.open(io.BytesIO(audio)) as w: seconds = w.getnframes() / w.getframerate()
            self.status.setText(f'语音合成正常：{seconds:.1f} 秒音频')
            if self.speech_output is not None:
                # 把刚合成的这份音频交给正式播放链路，不再合成第二次（Codex 审计 R6）
                try: self.speech_output.play_audio('你好，这是声音测试。', audio)
                except Exception as error: self.status.setText(f'语音合成正常，但播放失败：{error}')
        self.run(lambda token: self.admin.test_tts(cid, token), done, lambda error: self.status.setText('语音测试失败：' + error))

    def _run_in_background(self, function, on_success, on_failure):
        from aicompanion.ui.chat_worker import TaskWorker
        worker = TaskWorker(function, self); self.workers.add(worker)
        worker.succeeded.connect(on_success); worker.failed.connect(on_failure)
        worker.finished.connect(lambda w=worker: self.workers.discard(w)); worker.start()
