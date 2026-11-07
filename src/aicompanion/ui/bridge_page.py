"""设置 → 聊天平台（T9）：一键接入 QQ。

普通用户只看到：接入 QQ → 扫码登录 → 填主人 QQ 号、勾选群 → 已连接。端口、令牌、组件目录等放在"高级设置"里。
所有耗时操作（下载、安装、启动、查状态）都在后台线程里做，结果通过信号回到界面线程。
"""
from __future__ import annotations

import re
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QSpinBox, QVBoxLayout,
                               QWidget)

from aicompanion.bridge.service import setting

ACTION_TEXT = {'接入 QQ': '接入 QQ', '准备组件': '准备组件', '启动': '启动', '重试': '重试', '刷新二维码': '刷新二维码',
               '保存': '保存主人和群', '查看步骤': '查看步骤'}


class _Signals(QObject):
    status = Signal(object)
    progress = Signal(str, str, float)
    finished = Signal(str)     # 空串 = 成功，否则是错误说明
    diagnostics = Signal(str)


class BridgePage(QWidget):
    def __init__(self, database, parent=None):
        super().__init__(parent)
        from aicompanion.qq import runtime
        self.database = database; self.qq = runtime.manager(database)
        self.signals = _Signals(); self.busy = False; self.checking = False; self.status = None
        self.signals.status.connect(self._show_status); self.signals.progress.connect(self._show_progress)
        self.signals.finished.connect(self._finished)
        self.signals.diagnostics.connect(self._diagnostics_ready)
        layout = QVBoxLayout(self)

        # ---- 状态卡片 ----
        card = QGroupBox('QQ'); cl = QVBoxLayout(card)
        self.title = QLabel('正在检查…'); self.title.setStyleSheet('font-size:16px;font-weight:600'); self.title.setWordWrap(True)
        self.detail = QLabel(''); self.detail.setWordWrap(True); self.detail.setStyleSheet('color:#666')
        self.qrcode = QLabel(); self.qrcode.setAlignment(Qt.AlignmentFlag.AlignCenter); self.qrcode.hide()
        self.bar = QProgressBar(); self.bar.setRange(0, 1000); self.bar.hide()
        self.action = QPushButton('接入 QQ'); self.action.clicked.connect(self._do_action)
        buttons = QHBoxLayout(); buttons.addWidget(self.action)
        self.start_btn = QPushButton('启动'); self.start_btn.clicked.connect(lambda: self._run(self.qq.start, '正在启动…'))
        self.stop_btn = QPushButton('停止'); self.stop_btn.clicked.connect(lambda: self._run(self.qq.stop, '正在停止…'))
        diag = QPushButton('复制诊断信息'); diag.clicked.connect(self._copy_diagnostics)
        for b in (self.start_btn, self.stop_btn, diag): buttons.addWidget(b)
        buttons.addStretch(1)
        for w in (self.title, self.detail, self.qrcode, self.bar): cl.addWidget(w)
        cl.addLayout(buttons); layout.addWidget(card)

        # ---- 主人和群 ----
        people = QGroupBox('谁能和她说话'); pl = QFormLayout(people)
        self.owner = QLineEdit(str(database.get_setting('bridge_owner:qq', '') or ''))
        self.owner.setPlaceholderText('你自己的 QQ 号（不是机器人的）')
        self.groups = QListWidget(); self.groups.setMaximumHeight(140)
        self.groups_hint = QLabel('机器人登录并连上后，这里会列出它所在的群，勾选允许她说话的群。'); self.groups_hint.setWordWrap(True)
        self.strangers = QCheckBox('也回复陌生人的私聊'); self.strangers.setChecked(bool(setting(database, 'bridge_strangers')))
        pl.addRow('主人的 QQ 号', self.owner); pl.addRow('允许的群', self.groups); pl.addRow('', self.groups_hint); pl.addRow('', self.strangers)
        save_people = QPushButton('保存'); save_people.clicked.connect(self.save_people); pl.addRow('', save_people)
        layout.addWidget(people)
        self._fill_groups([])

        # ---- 高级设置 ----
        self.advanced = QGroupBox('高级设置'); self.advanced.setCheckable(True); self.advanced.setChecked(False)
        inner = QWidget(); form = QFormLayout(inner)
        self.enabled = QCheckBox('打开桌宠的本机接口（插件通过它把消息交给她）'); self.enabled.setChecked(bool(setting(database, 'bridge_enabled')))
        self.port = QSpinBox(); self.port.setRange(1024, 65535); self.port.setValue(int(setting(database, 'bridge_port')))
        self.token = QLineEdit(setting(database, 'bridge_token')); self.token.setReadOnly(True); self.token.setEchoMode(QLineEdit.EchoMode.Password)
        copy = QPushButton('复制'); copy.clicked.connect(lambda: QApplication.clipboard().setText(self.token.text()))
        renew = QPushButton('换一个'); renew.clicked.connect(self._renew)
        token_row = QHBoxLayout(); token_row.addWidget(self.token, 1); token_row.addWidget(copy); token_row.addWidget(renew)
        self.daily = QSpinBox(); self.daily.setRange(0, 2000); self.daily.setValue(int(setting(database, 'bridge_public_daily'))); self.daily.setSuffix(' 条 / 天')
        self.autostart = QCheckBox('打开桌宠时自动启动 QQ 服务，退出时停止'); self.autostart.setChecked(bool(database.get_setting('qq_autostart', True)))
        self.index = QLineEdit(database.get_setting('qq_index_url', '') or ''); self.index.setPlaceholderText('留空 = PyPI 官方源；下载慢时可填镜像地址')
        self.root = QLineEdit(str(self.qq.layout.root)); self.root.setReadOnly(True)
        open_logs = QPushButton('打开组件文件夹'); open_logs.clicked.connect(self._open_root)
        existing = QPushButton('改为接入已有的 AstrBot…'); existing.clicked.connect(self._attach_existing)
        self.versions = QLabel(''); self.versions.setWordWrap(True)
        form.addRow('', self.enabled); form.addRow('接口端口', self.port); form.addRow('插件令牌', token_row)
        form.addRow('群和陌生人每天最多回复', self.daily); form.addRow('', self.autostart); form.addRow('Python 包镜像', self.index)
        form.addRow('组件目录', self.root); form.addRow('', open_logs); form.addRow('', existing); form.addRow('组件版本', self.versions)
        al = QVBoxLayout(self.advanced); al.addWidget(inner); inner.setVisible(False)
        self.advanced.toggled.connect(inner.setVisible)
        layout.addWidget(self.advanced)
        note = QLabel('桌宠会从 NapCat 和 AstrBot 的官方发布地址下载原版组件，放在上面的组件目录里，不改你电脑上的 QQ。'
                      '扫码登录和账号验证需要你自己完成。主人的私聊和桌面是同一段相处；记忆在桌面和 QQ 之间共享，'
                      '不想在群里被提到的记忆，到"记忆"页标成"只在私下用"。聊天平台上她不会动手干活。')
        note.setWordWrap(True); note.setStyleSheet('color:#666'); layout.addWidget(note); layout.addStretch(1)

        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(2500)
        self._show_versions(); self.refresh()

    # ---- 状态 -----------------------------------------------------------------------------
    def refresh(self):
        if self.busy or self.checking or not self.isVisible() and self.status is not None: return
        self.checking = True
        def work():
            try: status = self.qq.diagnose()
            except Exception as error:
                from aicompanion.qq.manager import Status
                status = Status('error', '检查状态时出错', '重试', str(error))
            self.signals.status.emit(status)
        threading.Thread(target=work, daemon=True).start()

    def _show_status(self, status):
        self.checking = False; self.status = status
        if self.busy: return
        title = status.title + (f'（机器人 {status.bot}）' if status.bot else '')
        self.title.setText(('✅ ' if status.code == 'connected' else '') + title)
        detail = status.detail or ''
        if self.qq.last_error and status.code in ('stopped', 'start_failed'): detail = self.qq.last_error + ('\n'+detail if detail else '')
        self.detail.setText(detail[-1200:]); self.detail.setVisible(bool(detail))
        self.action.setText(ACTION_TEXT.get(status.action, status.action) or '刷新'); self.action.setVisible(bool(status.action))
        if status.qrcode:
            pixmap = QPixmap(status.qrcode)
            self.qrcode.setPixmap(pixmap.scaled(220, 220, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            self.qrcode.show()
        else: self.qrcode.hide()
        managed = self.qq.mode == 'auto' and status.code not in ('not_setup', 'not_ready')
        self.start_btn.setVisible(managed); self.stop_btn.setVisible(managed)
        if status.groups: self._fill_groups(status.groups)

    def _fill_groups(self, groups):
        chosen = {str(g) for g in setting(self.database, 'bridge_groups') or []}
        current = {self.groups.item(i).data(Qt.ItemDataRole.UserRole): self.groups.item(i).checkState() == Qt.CheckState.Checked
                   for i in range(self.groups.count())}
        known = {str(g['id']): g.get('name') or '' for g in groups}
        for gid in chosen: known.setdefault(gid, '')
        if set(known) == set(current): return
        self.groups.clear()
        for gid, name in sorted(known.items(), key=lambda x: x[1] or x[0]):
            item = QListWidgetItem(f'{name}（{gid}）' if name else gid); item.setData(Qt.ItemDataRole.UserRole, gid)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if current.get(gid, gid in chosen) else Qt.CheckState.Unchecked)
            self.groups.addItem(item)
        self.groups_hint.setVisible(not groups)

    # ---- 操作 -----------------------------------------------------------------------------
    def _do_action(self):
        code = self.status.code if self.status else ''
        if code in ('not_setup', 'not_ready'): return self._prepare()
        if code in ('need_login', 'offline'): return self._run(self.qq.refresh_qrcode, '正在刷新二维码…')
        if code == 'need_owner': return self.save_people()
        if code == 'stopped': return self._run(self.qq.start, '正在启动…')
        if code == 'bridge_off': return self._run(self._apply_bridge, '正在打开接口…')
        if code == 'plugin_missing' and self.qq.mode == 'existing':
            return QMessageBox.information(self, '接入已有的 AstrBot', '桌宠已经把插件装到你的 AstrBot 里了。请在 AstrBot 管理面板的"插件"里重载 '
                                           'astrbot_plugin_aicompanion（或重启 AstrBot），并确认 NapCat 已经连上 AstrBot。')
        if self.qq.mode == 'auto': return self._run(self._retry, '正在重试…')
        self.refresh()

    def _retry(self):
        self.qq.stop(); self.qq.configure(); self.qq.start()

    def _apply_bridge(self):
        from aicompanion.bridge.server import runtime
        self.database.set_setting('bridge_enabled', True)
        problem = runtime.apply(self.database)
        if problem: raise RuntimeError(problem)

    def _prepare(self):
        from aicompanion.qq import specs
        text = ('桌宠将从官方地址下载并安装以下组件，放在自己的组件目录里（约 300 MB，视网络需要几分钟）：\n\n'
                f'• NapCat {specs.NAPCAT.version}（QQ 登录和收发消息）\n  {specs.NAPCAT.license_note}\n'
                f'• uv {specs.UV.version} 和 Python {specs.ASTRBOT.python}\n'
                f'• AstrBot {specs.ASTRBOT.version}（AGPL-3.0，消息路由）\n\n'
                '不会改你电脑上已安装的 QQ。机器人账号建议用小号。继续吗？')
        if QMessageBox.question(self, '接入 QQ', text) != QMessageBox.StandardButton.Yes: return
        def work():
            self.qq.prepare(progress=lambda step, detail, fraction=0.0: self.signals.progress.emit(step, detail, fraction))
            self.qq.start()
        self._run(work, '正在准备组件…', progress=True)

    def _attach_existing(self):
        folder = QFileDialog.getExistingDirectory(self, '选择 AstrBot 的根目录（里面有 data 文件夹）')
        if not folder: return
        self._run(lambda: self.qq.attach_existing(folder), '正在安装插件…')

    def _run(self, fn, text, progress=False):
        if self.busy: return
        self.busy = True; self.title.setText(text); self.detail.hide(); self.action.setEnabled(False)
        self.bar.setVisible(progress); self.bar.setValue(0)
        def work():
            try: fn(); self.signals.finished.emit('')
            except Exception as error: self.signals.finished.emit(str(error))
        threading.Thread(target=work, daemon=True).start()

    def _show_progress(self, step, detail, fraction):
        self.title.setText(step); self.detail.setText(detail); self.detail.show()
        if fraction: self.bar.setValue(int(fraction*1000))

    def _finished(self, error):
        self.busy = False; self.action.setEnabled(True); self.bar.hide(); self._show_versions()
        if error:
            self.title.setText('没有成功'); self.detail.setText(error[-1200:]); self.detail.show()
            self.action.setText('重试'); self.action.show()
            from aicompanion.qq.manager import Status
            self.status = Status('start_failed', '没有成功', '重试', error)
        else: self.refresh()

    def _copy_diagnostics(self):
        threading.Thread(target=lambda: self.signals.diagnostics.emit(self.qq.diagnostics_text()), daemon=True).start()

    def _diagnostics_ready(self, text):
        QApplication.clipboard().setText(text)
        QMessageBox.information(self, '已复制', '诊断信息已复制（不含令牌和密码）。')

    def _open_root(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        self.qq.layout.root.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.qq.layout.root)))

    def _show_versions(self):
        components = self.qq.layout.manifest().get('components') or {}
        self.versions.setText('；'.join(f'{k} {v.get("version")}' for k, v in components.items()) or '未安装')

    def _renew(self):
        from aicompanion.bridge.server import new_token
        self.token.setText(new_token())

    # ---- 保存 ------------------------------------------------------------------------------
    def save_people(self):
        db = self.database
        owner = self.owner.text().strip()
        if owner and not re.fullmatch(r'\d{5,12}', owner):
            return QMessageBox.warning(self, '主人 QQ 号', 'QQ 号应该是 5 到 12 位数字。')
        db.set_setting('bridge_owner:qq', owner)
        groups = [self.groups.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.groups.count())
                  if self.groups.item(i).checkState() == Qt.CheckState.Checked]
        db.set_setting('bridge_groups', groups)   # 插件下次心跳就会拿到
        db.set_setting('bridge_strangers', self.strangers.isChecked())
        self.refresh()

    def save(self):
        """设置窗口的"保存"：主人、群和高级设置一起保存；接口端口 / 令牌变了就重启接口并同步插件配置。"""
        db = self.database
        self.save_people()
        old = (setting(db, 'bridge_enabled'), setting(db, 'bridge_port'), setting(db, 'bridge_token'))
        db.set_setting('bridge_enabled', self.enabled.isChecked()); db.set_setting('bridge_port', self.port.value())
        if self.token.text(): db.set_setting('bridge_token', self.token.text())
        db.set_setting('bridge_public_daily', self.daily.value()); db.set_setting('qq_autostart', self.autostart.isChecked())
        db.set_setting('qq_index_url', self.index.text().strip())
        new = (setting(db, 'bridge_enabled'), setting(db, 'bridge_port'), setting(db, 'bridge_token'))
        if new != old:
            from aicompanion.bridge.server import runtime
            problem = runtime.apply(db)
            if problem: self.detail.setText('⚠ '+problem); self.detail.show()
            try:
                if self.qq.mode == 'auto' and not self.qq.ready():
                    # 端口 / 令牌变了：重写插件配置并重启组件，让插件用上新的地址和令牌
                    self._run(self._retry if self.qq.supervisor.running('astrbot') else self.qq.configure, '正在同步配置…')
                elif self.qq.mode == 'existing':
                    from aicompanion.qq import configure
                    url, token = self.qq.bridge()
                    configure.plugin_config(db.get_setting('qq_existing_root', ''), url, token)
            except Exception as error:
                self.detail.setText('⚠ 插件配置没能同步：'+str(error)); self.detail.show()
