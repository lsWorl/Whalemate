"""设置 → 聊天平台（T9）：打开本机接口、绑定主人的 QQ 号、允许哪些群。"""
from __future__ import annotations

from PySide6.QtWidgets import (QApplication, QCheckBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from aicompanion.bridge.service import setting


class BridgePage(QWidget):
    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.database = database
        layout = QVBoxLayout(self); form = QFormLayout()
        self.enabled = QCheckBox('打开本机接口，让 AstrBot 插件把 QQ 消息交给她')
        self.enabled.setChecked(bool(setting(database, 'bridge_enabled')))
        self.port = QSpinBox(); self.port.setRange(1024, 65535); self.port.setValue(int(setting(database, 'bridge_port')))
        self.token = QLineEdit(setting(database, 'bridge_token')); self.token.setReadOnly(True)
        self.token.setEchoMode(QLineEdit.EchoMode.Password); self.token.setPlaceholderText('第一次打开接口时自动生成')
        copy = QPushButton('复制令牌'); copy.clicked.connect(lambda: QApplication.clipboard().setText(self.token.text()))
        renew = QPushButton('换一个'); renew.clicked.connect(self._renew)
        row = QHBoxLayout(); row.addWidget(self.token, 1); row.addWidget(copy); row.addWidget(renew)
        self.owner = QLineEdit(str(database.get_setting('bridge_owner:qq', '') or ''))
        self.owner.setPlaceholderText('你自己的 QQ 号；只有这个号的私聊算"主人"')
        self.groups = QLineEdit('，'.join(str(g) for g in setting(database, 'bridge_groups') or []))
        self.groups.setPlaceholderText('允许她说话的群号，用逗号分开；留空 = 不进任何群')
        self.strangers = QCheckBox('也回复陌生人的私聊')
        self.strangers.setChecked(bool(setting(database, 'bridge_strangers')))
        self.daily = QSpinBox(); self.daily.setRange(0, 2000); self.daily.setValue(int(setting(database, 'bridge_public_daily')))
        self.daily.setSuffix(' 条 / 天')
        form.addRow('', self.enabled); form.addRow('端口', self.port); form.addRow('令牌', row)
        form.addRow('主人的 QQ 号', self.owner); form.addRow('允许的群', self.groups); form.addRow('', self.strangers)
        form.addRow('群和陌生人每天最多回复', self.daily)
        layout.addLayout(form)
        self.status = QLabel(''); self.status.setWordWrap(True); layout.addWidget(self.status)
        note = QLabel('QQ 接入用 AstrBot + NapCat（另装），再装项目里的 astrbot_plugin_aicompanion 插件，'
                      '把这里的端口和令牌填进插件配置。步骤见 integrations/astrbot_plugin_aicompanion/README.md。\n'
                      '主人的私聊和桌面是同一段相处（共用聊天记录）；每个群共用一段群聊记录。记忆在桌面和 QQ 之间共享：'
                      '群里听来的事她在桌面上也记得，桌面上记住的事在群里也能用——不想在群里被提到的，到"记忆"页标成"只在私下用"。'
                      '聊天平台上她不会动手干活。')
        note.setWordWrap(True); layout.addWidget(note); layout.addStretch(1)
        self._show_status()

    def _renew(self):
        from aicompanion.bridge.server import new_token
        self.token.setText(new_token())

    def _show_status(self, problem=''):
        from aicompanion.bridge.server import runtime
        if problem or runtime.problem: self.status.setText('⚠ '+(problem or runtime.problem))
        elif runtime.server: self.status.setText(f'接口运行中：http://127.0.0.1:{runtime.server.port}')
        else: self.status.setText('接口未打开。')

    def save(self):
        import re
        db = self.database
        db.set_setting('bridge_enabled', self.enabled.isChecked()); db.set_setting('bridge_port', self.port.value())
        if self.token.text(): db.set_setting('bridge_token', self.token.text())
        owner = self.owner.text().strip()
        db.set_setting('bridge_owner:qq', owner if re.fullmatch(r'\d{5,12}', owner) else '')
        db.set_setting('bridge_groups', [g for g in re.split(r'[,，\s]+', self.groups.text()) if re.fullmatch(r'\d{5,12}', g)])
        db.set_setting('bridge_strangers', self.strangers.isChecked()); db.set_setting('bridge_public_daily', self.daily.value())
        from aicompanion.bridge.server import runtime
        self._show_status(runtime.apply(db))
