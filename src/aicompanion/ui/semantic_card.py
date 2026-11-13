"""记忆页中的云端向量授权与阶段摘要原文。"""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QListWidget,
                               QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from aicompanion.providers.admin import ModelAdmin
from aicompanion.services import stages
from aicompanion.services.semantic import DIMENSIONS, LOCAL, SETTING, target_for


class SemanticCard(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.service = controller.semantic
        self.cid = controller.character.id
        self.mode = QComboBox()
        self.mode.addItem('云端', 'cloud')
        self.mode.addItem('本地 · BGE 中文（512 维）', 'local')
        self.mode.setCurrentIndex(int(self.service.db.get_setting(SETTING) == vars(LOCAL)))
        self.model, self.dimension = QComboBox(), QComboBox()
        self.dimension.setEditable(True)
        self.status = QLabel()
        self.status.setWordWrap(True)
        form = QFormLayout(self)
        form.addRow('向量计算位置', self.mode)
        form.addRow('云端模型', self.model)
        form.addRow('向量维度', self.dimension)
        form.addRow('当前状态', self.status)
        buttons = QHBoxLayout()
        self.enable = QPushButton('确认并启用')
        self.disable = QPushButton('关闭语义召回')
        self.originals = QPushButton('查看阶段摘要和原文')
        for button in (self.enable, self.disable, self.originals):
            buttons.addWidget(button)
        form.addRow(buttons)
        self.enable.clicked.connect(self.confirm)
        self.disable.clicked.connect(self.deactivate)
        self.originals.clicked.connect(self.show_sources)
        self.model.currentIndexChanged.connect(self.dimensions)
        self.mode.currentIndexChanged.connect(self.show_mode)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh_status)
        self.reload()

    def refresh_status(self):
        choice = self.service.db.get_setting(SETTING)
        active = ('本地' if choice == vars(LOCAL) else '云端') if choice else ''
        self.status.setText((active + ' · ' if active else '') + self.service.state)
        self.disable.setEnabled(bool(self.service.db.get_setting(SETTING)))

    def reload(self):
        saved = self.service.db.get_setting(SETTING) or {}
        previous = self.model.currentData() or saved.get('profile')
        self.model.clear()
        router = self.service.router
        self.profiles = {}
        if hasattr(router, 'store'):
            admin = ModelAdmin(router)
            accounts = {a.id: a.label for a in admin.accounts()}
            for model in sorted(admin.models('embedding'), key=lambda m: m.model_id != 'text-embedding-v4'):
                self.profiles[model.id] = model
                self.model.addItem(f'{accounts[model.provider]} · {model.model_id}', model.id)
        if not self.profiles:
            self.model.addItem('请先在“模型与 Key”添加百炼、智谱或自定义向量模型', None)
        self.model.setCurrentIndex(max(0, self.model.findData(previous)))
        self.dimensions()
        self.show_mode()
        self.refresh_status()

    def show_mode(self):
        local = self.mode.currentData() == 'local'
        form = self.layout()
        form.setRowVisible(self.model, not local)
        form.setRowVisible(self.dimension, not local)
        self.enable.setEnabled(local or bool(self.profiles))
        self.enable.setText('启用本地 / 重试' if local else '确认并启用云端')

    def showEvent(self, event):
        super().showEvent(event)
        self.reload()
        self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def dimensions(self):
        self.dimension.clear()
        model = getattr(self, 'profiles', {}).get(self.model.currentData())
        if model:
            known = DIMENSIONS.get(model.model_id)
            self.dimension.setEditable(known is None)
            self.dimension.addItems([str(n) for n in known or (256, 512, 1024, 2048)])
            saved = self.service.db.get_setting(SETTING) or {}
            self.dimension.setCurrentText(str(saved['dimension'] if saved.get('profile') == model.id else 1024))

    def confirm(self):
        if self.mode.currentData() == 'local':
            if QMessageBox.question(self, '启用本地语义召回',
                    '首次启用将从 Hugging Face 下载约 24.5 MB 模型文件。记忆和查询在本机计算。'
                    '可用“关闭语义召回”取消下载，或稍后重试。继续？') == QMessageBox.StandardButton.Yes:
                self.service.enable_local()
                self.refresh_status()
            return
        try:
            target, account = target_for(self.service.router, self.model.currentData(), int(self.dimension.currentText()))
            if QMessageBox.question(self, '启用云端语义召回',
                    f'开启云端后，记忆和你说的话会发给所选厂商计算向量。\n\n{account.label}\n'
                    f'{target.endpoint}\n{target.model} · {target.dimension} 维\n\n确认启用？') != QMessageBox.StandardButton.Yes:
                return
            self.service.enable(target.profile, target.dimension)
        except ValueError as error:
            QMessageBox.information(self, '请检查向量配置', str(error))
        self.refresh_status()

    def deactivate(self):
        self.service.disable()
        self.refresh_status()

    def show_sources(self):
        db = self.service.db
        with db.transaction() as c:
            rows = c.execute("""SELECT id,channel,first_id,last_id,summary FROM stage_summaries WHERE character_id=?
                             AND first_id>? ORDER BY last_id DESC""",
                             (self.cid, db.setting_in(c, f'context_after:{self.cid}', 0))).fetchall()
        dialog = QDialog(self)
        dialog.setWindowTitle('阶段摘要和原文')
        dialog.resize(650, 520)
        layout = QVBoxLayout(dialog)
        entries = QListWidget()
        original = QPlainTextEdit()
        original.setReadOnly(True)
        layout.addWidget(entries)
        layout.addWidget(original)
        for row in rows:
            entries.addItem(f"{row['channel']} · {row['first_id']}–{row['last_id']}  {row['summary']}")
        def select(index):
            messages = stages.sources(db, self.cid, rows[index]['id']) if 0 <= index < len(rows) else []
            original.setPlainText('\n\n'.join(f"#{r['id']} · {r['created_at']} · {r['role']}\n{r['content']}" for r in messages))
        entries.currentRowChanged.connect(select)
        if rows:
            entries.setCurrentRow(0)
        else:
            original.setPlaceholderText('还没有积累出阶段摘要。')
        dialog.exec()
