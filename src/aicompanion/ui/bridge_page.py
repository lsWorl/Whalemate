"""设置 → 聊天平台（T9）：一键接入 QQ。

普通用户只看到：接入 QQ → 扫码登录 → 填你的 QQ 号（她管你叫爹）、勾选群 → 已连接。端口、令牌、组件目录等放在"高级设置"里。
所有耗时操作（下载、安装、启动、查状态）都在后台线程里做，结果通过信号回到界面线程。
"""
from __future__ import annotations

import re
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSpinBox, QVBoxLayout,
                               QPlainTextEdit, QWidget)

from aicompanion.bridge.service import CHIME, setting

CHIME_TEXT = {'off': '只在被叫时说话（@ 她或提到她名字）', 'sometimes': '偶尔插句话', 'often': '爱聊天，常插话'}

ACTION_TEXT = {'接入 QQ': '接入 QQ', '准备组件': '准备组件', '启动': '启动', '重试': '重试', '刷新二维码': '刷新二维码',
               '保存': '保存 QQ 号和群', '查看步骤': '查看步骤'}


class _Signals(QObject):
    status = Signal(object)
    progress = Signal(str, str, float)
    finished = Signal(str)     # 空串 = 成功，否则是错误说明
    diagnostics = Signal(str)
    community = Signal(str)


class BridgePage(QWidget):
    def __init__(self, database, parent=None):
        super().__init__(parent)
        from aicompanion.qq import runtime
        self.database = database; self.qq = runtime.manager(database)
        self.signals = _Signals(); self.busy = False; self.checking = False; self.status = None
        self.signals.status.connect(self._show_status); self.signals.progress.connect(self._show_progress)
        self.signals.finished.connect(self._finished)
        self.signals.diagnostics.connect(self._diagnostics_ready)
        self.signals.community.connect(self._community_done)
        shell = QVBoxLayout(self); shell.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget(); layout = QVBoxLayout(body); scroll.setWidget(body); shell.addWidget(scroll)

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

        # ---- 爹和群 ----
        people = QGroupBox('谁能和她说话'); pl = QFormLayout(people)
        self.owner = QLineEdit(str(database.get_setting('bridge_owner:qq', '') or ''))
        self.owner.setPlaceholderText('你自己的 QQ 号（不是机器人的）')
        self.groups = QListWidget(); self.groups.setMaximumHeight(140)
        self.groups_hint = QLabel('机器人登录并连上后，这里会列出它所在的群，勾选允许她说话的群。'); self.groups_hint.setWordWrap(True)
        self.strangers = QCheckBox('也回复陌生人的私聊'); self.strangers.setChecked(bool(setting(database, 'bridge_strangers')))
        self.chime = QComboBox()
        for key in CHIME: self.chime.addItem(CHIME_TEXT[key], key)
        self.chime.setCurrentIndex(max(0, self.chime.findData(setting(database, 'bridge_chime'))))
        self.chime.setToolTip('群里的消息她都"听得到"。没人叫她时按这里决定要不要插话；她也可能觉得没什么好说而不说。')
        pl.addRow('你的 QQ 号（她管你叫爹）', self.owner); pl.addRow('允许的群', self.groups); pl.addRow('', self.groups_hint)
        # 用户 2026-11-09：主动问候 / 心事随机发到桌面或 QQ 私聊（不在电脑前时更常发 QQ）
        self.proactive_qq = QCheckBox('她主动找你说话时，也会发到 QQ 私聊（不在电脑前时更常发 QQ）')
        self.proactive_qq.setChecked(bool(database.get_setting('proactive_qq_enabled', True)))
        pl.addRow('群里活跃度', self.chime); pl.addRow('', self.strangers); pl.addRow('', self.proactive_qq)

        self.vision_enabled = QCheckBox('看懂收到的图片（DeepSeek 官方接口）')
        self.vision_enabled.setChecked(bool(database.get_setting('qq_vision_enabled', True)))
        self.vision_enabled.setToolTip('只识别正在和她说话的人发来的图片；原图不存入聊天记录，识图会产生模型用量。')
        self.memes_enabled = QCheckBox('聊天时可以发表情包')
        self.memes_enabled.setChecked(bool(database.get_setting('qq_memes_enabled', True)))
        self.meme_root = QLineEdit(str(database.get_setting('qq_meme_root', '') or ''))
        self.meme_root.setPlaceholderText('留空使用自带鲸娘表情；也可选本地表情包目录')
        choose_memes = QPushButton('选择图库…'); choose_memes.clicked.connect(self._choose_memes)
        meme_row = QHBoxLayout(); meme_row.addWidget(self.meme_root, 1); meme_row.addWidget(choose_memes)
        pl.addRow('', self.vision_enabled); pl.addRow('', self.memes_enabled); pl.addRow('表情图库', meme_row)
        self.social_group = QGroupBox('聊天习惯与社区'); self.social_group.setCheckable(True); self.social_group.setChecked(False)
        social_inner = QWidget(); social_form = QFormLayout(social_inner)
        social_layout = QVBoxLayout(self.social_group); social_layout.addWidget(social_inner)
        social_inner.hide(); self.social_group.toggled.connect(social_inner.setVisible)
        self.avatar_enabled = QCheckBox('认识对话者的头像（只记画面印象，每天最多更新一次）')
        self.avatar_enabled.setChecked(bool(database.get_setting('qq_avatars_enabled', True)))
        self.learning = QCheckBox('从聊天和反馈中记住表达习惯')
        self.learning.setChecked(bool(database.get_setting('qq_social_learning', True)))
        review_social = QPushButton('查看 / 清除表达习惯…'); review_social.clicked.connect(self._review_social)
        social_form.addRow('', self.avatar_enabled); social_form.addRow('', self.learning); social_form.addRow('', review_social)
        self.chat_card_enabled = QCheckBox('使用聊天卡和社区知识（表情只读懂，不分析）')
        self.chat_card_enabled.setChecked(bool(database.get_setting('qq_chat_card_enabled', True)))
        review_card = QPushButton('查看 / 导入聊天卡与世界书…'); review_card.clicked.connect(self._chat_card)
        social_form.addRow('', self.chat_card_enabled); social_form.addRow('', review_card)
        self.community_source = QComboBox()
        self.community_source.addItem('日常闲聊（不读取热榜）', 'off')
        self.community_source.addItem('哔哩哔哩 / 动漫游戏', 'bilibili'); self.community_source.addItem('V2EX / 技术社区', 'v2ex')
        self.community_source.setCurrentIndex(max(0, self.community_source.findData(database.get_setting('qq_community', 'bilibili'))))
        self.community_auto = QCheckBox('每天在后台刷新所选社区话题')
        self.community_auto.setChecked(bool(database.get_setting('qq_community_auto', True)))
        self.community_groups = QCheckBox('本次社区选择只应用到上面勾选的群')
        refresh_community = QPushButton('现在逛逛社区'); refresh_community.clicked.connect(self._refresh_community)
        self.community_status = QLabel('读取公开话题及受限社区词义，不上传聊天；相关时只供理解，不向群友科普。'); self.community_status.setWordWrap(True)
        social_form.addRow('社区语境', self.community_source); social_form.addRow('', self.community_groups)
        social_form.addRow('', self.community_auto); social_form.addRow('', refresh_community); social_form.addRow('', self.community_status)
        save_people = QPushButton('保存'); save_people.clicked.connect(self.save_people); pl.addRow('', save_people)
        layout.addWidget(people)
        self._fill_groups([])
        layout.addWidget(self.social_group)

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
        self.meme_cooldown = QSpinBox(); self.meme_cooldown.setRange(0, 3600)
        self.meme_cooldown.setValue(int(database.get_setting('qq_meme_cooldown', 60)))
        self.meme_cooldown.setSuffix(' 秒')
        self.autostart = QCheckBox('打开桌宠时自动启动 QQ 服务，退出时停止'); self.autostart.setChecked(bool(database.get_setting('qq_autostart', True)))
        self.index = QLineEdit(database.get_setting('qq_index_url', '') or ''); self.index.setPlaceholderText('留空 = PyPI 官方源；下载慢时可填镜像地址')
        self.root = QLineEdit(str(self.qq.layout.root)); self.root.setReadOnly(True)
        open_logs = QPushButton('打开组件文件夹'); open_logs.clicked.connect(self._open_root)
        existing = QPushButton('改为接入已有的 AstrBot…'); existing.clicked.connect(self._attach_existing)
        self.versions = QLabel(''); self.versions.setWordWrap(True)
        form.addRow('', self.enabled); form.addRow('接口端口', self.port); form.addRow('插件令牌', token_row)
        form.addRow('群和陌生人每天最多回复', self.daily); form.addRow('', self.autostart); form.addRow('Python 包镜像', self.index)
        form.addRow('识图模型', QLabel('在“模型”页的“识图”一项里选择')); form.addRow('同一会话发表情的最短间隔', self.meme_cooldown)
        form.addRow('组件目录', self.root); form.addRow('', open_logs); form.addRow('', existing); form.addRow('组件版本', self.versions)
        al = QVBoxLayout(self.advanced); al.addWidget(inner); inner.setVisible(False)
        self.advanced.toggled.connect(inner.setVisible)
        layout.addWidget(self.advanced)
        note = QLabel('桌宠会从 NapCat 和 AstrBot 的官方发布地址下载原版组件，放在上面的组件目录里，不改你电脑上的 QQ。'
                      '扫码登录和账号验证需要你自己完成。你（爹）的 QQ 私聊和桌面是同一段相处；记忆在桌面和 QQ 之间共享，'
                      '不想在群里被提到的记忆，到"记忆"页标成"只在私下用"。聊天平台上她不会动手干活。')
        note.setWordWrap(True); note.setStyleSheet('color:#666'); layout.addWidget(note); layout.addStretch(1)

        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(2500)
        self._sync_from_db(); self._show_versions(); self.refresh()

    def _sync_from_db(self):
        """后台流程（接入、重试）会改接口开关和令牌：把数据库里的当前值放回页面，并记下来——保存时只写用户真正改过的项，
        不会用页面打开时的旧值把后台刚开的接口又关掉。"""
        db = self.database
        self.enabled.setChecked(bool(setting(db, 'bridge_enabled'))); self.port.setValue(int(setting(db, 'bridge_port')))
        self.token.setText(setting(db, 'bridge_token') or '')
        self.loaded = {'enabled': self.enabled.isChecked(), 'port': self.port.value(), 'token': self.token.text()}

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
        if self.qq.last_error and status.code in ('stopped', 'start_failed') and self.qq.last_error != detail:
            detail = self.qq.last_error + ('\n'+detail if detail else '')
        self.detail.setText(detail[-1200:]); self.detail.setVisible(bool(detail))
        self.action.setText(ACTION_TEXT.get(status.action, status.action) or '刷新'); self.action.setVisible(bool(status.action))
        if status.qrcode:
            pixmap = QPixmap(status.qrcode)
            self.qrcode.setPixmap(pixmap.scaled(220, 220, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            self.qrcode.show()
        else: self.qrcode.hide()
        managed = self.qq.mode == 'auto' and status.code not in ('not_setup', 'not_ready')
        self.start_btn.setVisible(managed and status.action != '启动'); self.stop_btn.setVisible(managed)
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
        self.qq.stop(); self.qq.start()   # start 内部同步配置，异常也由 start 保留

    def _apply_bridge(self):
        self.qq.ensure_bridge()

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
            try:
                fn()
                self.qq.last_error = ''
                self.signals.finished.emit('')
            except Exception as error:
                # 准备组件、保存配置不一定经过 start；同样记录，否则定时刷新会丢掉原因。
                self.signals.finished.emit(self.qq.remember_error(error))
        threading.Thread(target=work, daemon=True).start()

    def _show_progress(self, step, detail, fraction):
        self.title.setText(step); self.detail.setText(detail); self.detail.show()
        if fraction: self.bar.setValue(int(fraction*1000))

    def _finished(self, error):
        self.busy = False; self.action.setEnabled(True); self.bar.hide(); self._show_versions(); self._sync_from_db()
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

    def _choose_memes(self):
        folder = QFileDialog.getExistingDirectory(self, '选择表情包目录（含分类子文件夹或 manifest.json）')
        if folder: self.meme_root.setText(folder)

    def _social_id(self):
        from aicompanion.bridge.server import runtime
        return getattr(getattr(runtime.controller, 'character', None), 'id', None) or self.database.get_setting('active_character', 'deepseek-whale')

    def _review_social(self):
        import json
        from aicompanion.bridge.social import SocialMemory
        memory = SocialMemory(self.database, self._social_id())
        profiles = self.database.get_setting(memory.key, {}) or {}
        box = QMessageBox(self); box.setWindowTitle('表达习惯记忆')
        box.setText(f'当前角色保存了 {len(profiles)} 份按人、按会话隔离的表达习惯。\n只保存短句长度、固定语气词统计和明确偏好，不保存原话。')
        box.setDetailedText(json.dumps({'表达习惯': profiles, '头像印象': self.database.get_setting('qq_avatars:'+self._social_id(), {}) or {}}, ensure_ascii=False, indent=2))
        clear = box.addButton('清除这些习惯和头像印象', QMessageBox.ButtonRole.DestructiveRole)
        box.addButton('关闭', QMessageBox.ButtonRole.RejectRole); box.exec()
        if box.clickedButton() is clear:
            memory.clear(); self.database.set_setting('qq_avatars:'+self._social_id(), {})

    def _chat_card(self):
        import json
        from aicompanion.bridge.chat_card import builtin, load_json, normalize_card, normalize_book
        cid = self._social_id()
        pending = {'card': self.database.get_setting('qq_chat_card:'+cid, None),
                   'book': self.database.get_setting('qq_chat_book:'+cid, []) or []}
        dialog = QDialog(self); dialog.setWindowTitle('聊天卡与社区世界书'); dialog.resize(650, 500)
        layout = QVBoxLayout(dialog)
        hint = QLabel('只调整聊天表达，保留她的身份、关系和记忆。社区知识按当前消息的关键词使用，不逐条科普。\n'
                      '支持 JSON 聊天卡、SillyTavern CCv2 的 personality / mes_example / character_book，以及独立世界书。')
        hint.setWordWrap(True); layout.addWidget(hint)
        preview = QPlainTextEdit(); preview.setReadOnly(True); layout.addWidget(preview)
        def show():
            card = pending['card'] or (builtin() if cid == 'deepseek-whale' else None)
            from aicompanion.bridge.community_knowledge import available
            preview.setPlainText(json.dumps({'聊天卡': card, '额外世界书': pending['book'],
                                            '联网社区知识': available(self.database, self.community_source.currentData())}, ensure_ascii=False, indent=2))
        def import_file(book=False):
            path, _ = QFileDialog.getOpenFileName(dialog, '导入世界书' if book else '导入聊天卡', '', 'JSON 文件 (*.json)')
            if not path: return
            try:
                data = load_json(path)
                if book:
                    if isinstance(data, dict) and data.get('spec') == 'chara_card_v2': data = data.get('data', {})
                    entries = normalize_book(data.get('character_book', data) if isinstance(data, dict) else data)
                    if not entries: raise ValueError('没有可用的关键词知识条目')
                    pending['book'] = entries
                else: pending['card'] = normalize_card(data)
                show()
            except (OSError, ValueError) as error: QMessageBox.warning(dialog, '没有导入', str(error))
        buttons = QHBoxLayout()
        for title, action in (('导入聊天卡 JSON…', lambda: import_file()), ('导入世界书 JSON…', lambda: import_file(True))):
            button = QPushButton(title); button.clicked.connect(action); buttons.addWidget(button)
        reset = QPushButton('恢复内置卡 / 清空额外知识')
        def restore(): pending.update(card=None, book=[]); show()
        reset.clicked.connect(restore); buttons.addWidget(reset); layout.addLayout(buttons)
        save = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        save.button(QDialogButtonBox.StandardButton.Save).setText('保存')
        save.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        save.accepted.connect(dialog.accept); save.rejected.connect(dialog.reject); layout.addWidget(save)
        show()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            from aicompanion.storage.database import Database
            def apply(connection):
                Database.set_setting_in(connection, 'qq_chat_card:'+cid, pending['card'])
                Database.set_setting_in(connection, 'qq_chat_book:'+cid, pending['book'])
            self.database.locked(apply)

    def _refresh_community(self):
        source = self.community_source.currentData()
        if source == 'off':
            self.community_status.setText('先选择一个社区，再刷新。'); return
        self.community_status.setText('正在读取公开社区话题…')
        def work():
            from aicompanion.bridge.community import refresh
            try:
                value = refresh(self.database, source)
                import time
                message = value['source']+' · '+time.strftime('%m-%d %H:%M', time.localtime(value['at']))+'\n'+' / '.join(value['topics'][:5])
                from aicompanion.bridge.community_knowledge import refresh as learn
                try:
                    knowledge = learn(self.database, source)
                    if knowledge: message += f'\n已学习 {len(knowledge["entries"])} 条社区词义（附来源；每周刷新）。'
                except Exception: message += '\n社区词义本次未更新，保留已有知识。'
            except Exception: message = '这次没有逛成，旧话题保留；可以稍后重试。'
            self.signals.community.emit(message)
        threading.Thread(target=work, daemon=True).start()

    def _community_done(self, message):
        self.community_status.setText(message)

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
            return QMessageBox.warning(self, '你的 QQ 号', 'QQ 号应该是 5 到 12 位数字。')
        db.set_setting('bridge_owner:qq', owner)
        groups = [self.groups.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.groups.count())
                  if self.groups.item(i).checkState() == Qt.CheckState.Checked]
        db.set_setting('bridge_groups', groups)   # 插件下次心跳就会拿到
        db.set_setting('bridge_strangers', self.strangers.isChecked())
        db.set_setting('bridge_chime', self.chime.currentData())

        db.set_setting('qq_vision_enabled', self.vision_enabled.isChecked())
        db.set_setting('proactive_qq_enabled', self.proactive_qq.isChecked())
        db.set_setting('qq_memes_enabled', self.memes_enabled.isChecked())
        db.set_setting('qq_meme_root', self.meme_root.text().strip())
        db.set_setting('qq_meme_cooldown', self.meme_cooldown.value())
        db.set_setting('qq_avatars_enabled', self.avatar_enabled.isChecked())
        db.set_setting('qq_social_learning', self.learning.isChecked())
        db.set_setting('qq_chat_card_enabled', self.chat_card_enabled.isChecked())
        db.set_setting('qq_community_auto', self.community_auto.isChecked())
        if self.community_groups.isChecked():
            for group in groups: db.set_setting('qq_community_for:'+str(group), self.community_source.currentData())
        else: db.set_setting('qq_community', self.community_source.currentData())
        self.refresh()

    def save(self):
        """设置窗口的"保存"：QQ 号、群和高级设置一起保存；接口端口 / 令牌变了就重启接口并同步插件配置。"""
        db = self.database
        self.save_people()
        old = (setting(db, 'bridge_enabled'), setting(db, 'bridge_port'), setting(db, 'bridge_token'))
        # 只写用户在页面上改过的项（和上次同步的值比较）；没动的保持数据库里的当前值
        if self.enabled.isChecked() != self.loaded['enabled']: db.set_setting('bridge_enabled', self.enabled.isChecked())
        if self.port.value() != self.loaded['port']: db.set_setting('bridge_port', self.port.value())
        if self.token.text() and self.token.text() != self.loaded['token']: db.set_setting('bridge_token', self.token.text())
        db.set_setting('bridge_public_daily', self.daily.value()); db.set_setting('qq_autostart', self.autostart.isChecked())
        db.set_setting('qq_index_url', self.index.text().strip())
        new = (setting(db, 'bridge_enabled'), setting(db, 'bridge_port'), setting(db, 'bridge_token'))
        if new != old:
            from aicompanion.bridge.server import runtime
            problem = runtime.apply(db)
            if problem: self.detail.setText('⚠ '+problem); self.detail.show()
            try:
                if not setting(db, 'bridge_enabled'): pass   # 用户关掉了接口：尊重这个选择，不在这里重新打开
                elif self.qq.mode == 'auto' and not self.qq.ready():
                    # 端口 / 令牌变了：重写插件配置并重启组件，让插件用上新的地址和令牌
                    self._run(self._retry if self.qq.supervisor.running('astrbot') else self.qq.configure, '正在同步配置…')
                elif self.qq.mode == 'existing':
                    from aicompanion.qq import configure
                    url, token = self.qq.bridge()
                    configure.plugin_config(db.get_setting('qq_existing_root', ''), url, token)
            except Exception as error:
                self.detail.setText('⚠ 插件配置没能同步：'+str(error)); self.detail.show()
        self._sync_from_db()
