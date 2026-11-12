"""首次启动与设置概览共用的新手向导。只保存用户填写或选择过的配置。"""
from pathlib import Path
import json
import sys

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QProgressBar, QPushButton, QRadioButton, QScrollArea,
                               QStackedWidget, QVBoxLayout, QWidget)

from aicompanion.providers.admin import ModelAdmin, check_base_url
from aicompanion.ui import desktop_integration as desktop, theme
from aicompanion.ui.aboutme_preview import AboutmePreview
from aicompanion.ui.chat_worker import TaskWorker
from aicompanion.ui.icons import app_icon, line_icon, portrait
from aicompanion.ui.settings_window import play_voice_preview


def needs_onboarding(controller):
    db = controller.database
    if db.get_setting('onboarding_done', False):
        return False
    router = controller.router
    config = router.store.load() if hasattr(router, 'store') else None
    configured = router.configured('chat', controller.character.id)
    if configured or (config and any(m.provider in config.providers for m in config.models.values())):
        db.set_setting('onboarding_done', True)
        return False
    return True


def label(text='', style='Hint'):
    field = QLabel(text)
    field.setObjectName(style)
    field.setWordWrap(True)
    field.setTextFormat(Qt.TextFormat.PlainText)
    return field


def password(hint):
    field = QLineEdit()
    field.setEchoMode(QLineEdit.EchoMode.Password)
    field.setPlaceholderText(hint)
    return field


def connection_problem(message):
    text = message.lower()
    if any(code in text for code in ('402', 'balance', '余额', 'insufficient_quota')):
        return '账户余额不足，请先在服务商平台检查余额。'
    if any(code in text for code in ('401', '403', 'api key', 'unauthorized', '密钥')):
        return '密钥未被接受，请检查复制是否完整、服务和账户地区是否匹配。'
    if any(code in text for code in ('timeout', 'timed out', 'connect', 'network', '网络', '超时')):
        return '网络暂时不通，请检查网络和服务地址后重试。'
    return '服务未能回复，请检查模型名称、服务地址和账户状态。'


class Onboarding(QDialog):
    titles = ('你好，我是鲸鲸！', '给她连上“大脑”', '选一个她说话的声音', '她在桌面上长什么样？', '都准备好了')
    poses = ('celebrate', 'thinking', 'talking', 'idle', 'celebrate')
    captions = ('', '我需要一个“大脑”才能想事情和说话。它来自网上的 AI 服务，按用量付费，平时聊天花得很少。',
                '嘿嘿，想听听我的声音吗？点“试听”就好。', '', '准备好啦！以后请多多关照～')

    def __init__(self, window, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.controller = window.controller
        self.db = self.controller.database
        self.cid = self.controller.character.id
        self.router = self.controller.router
        self.admin = ModelAdmin(self.router) if hasattr(self.router, 'store') else None
        self.destination = None
        self.account_id = None
        self.model_dirty = False
        self.worker = None
        self.previewing = False
        self.voice_dirty = False
        self.setWindowTitle('鲸伴 · 新手向导')
        self.setStyleSheet(theme.DIALOG)
        self.resize(760, 520)
        self.setMinimumSize(660, 440)
        shell = QHBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        rail = QFrame()
        rail.setObjectName('Illustration')
        self.rail = rail
        rail.setFixedWidth(300)
        self.left = QVBoxLayout(rail)
        self.left.setContentsMargins(22, 22, 22, 28)
        self.brand = QWidget()
        brand = QHBoxLayout(self.brand)
        brand.setContentsMargins(0, 0, 0, 0)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(26, 26))
        brand.addWidget(logo)
        brand.addWidget(label('鲸伴', 'CardTitle'))
        brand.addStretch()
        self.left.addWidget(self.brand)
        self.caption = label('', 'PageIntro')
        self.caption.setStyleSheet('background:white; border-radius:16px; padding:10px 14px')
        self.left.addWidget(self.caption)
        self.left.addStretch()
        self.art = QLabel()
        self.art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.left.addWidget(self.art)
        self.indicator = QWidget()
        dots = QHBoxLayout(self.indicator)
        dots.setContentsMargins(0, 14, 0, 0)
        dots.setSpacing(8)
        self.points = []
        for _ in self.titles:
            point = QFrame()
            self.points.append(point)
            dots.addWidget(point)
        self.left.addWidget(self.indicator, 0, Qt.AlignmentFlag.AlignHCenter)
        shell.addWidget(rail)
        right = QVBoxLayout()
        right.setContentsMargins(30, 28, 30, 22)
        shell.addLayout(right, 1)
        self.step = label()
        self.heading = label('', 'PageTitle')
        self.heading_row = QHBoxLayout()
        self.heading_row.addWidget(self.step, 1)
        right.addLayout(self.heading_row)
        right.addWidget(self.heading)
        self.pages = QStackedWidget()
        for build in (self.welcome, self.model_page, self.voice_page, self.look_page, self.finish_page):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            body = QWidget()
            box = QVBoxLayout(body)
            box.setContentsMargins(2, 6, 6, 6)
            box.setSpacing(10)
            build(box)
            box.addStretch()
            scroll.setWidget(body)
            self.pages.addWidget(scroll)
        right.addWidget(self.pages, 1)
        nav = QHBoxLayout()
        self.back = QPushButton('上一步')
        self.back.clicked.connect(lambda: self.navigate(self.pages.currentIndex() - 1))
        self.skip = QPushButton()
        self.skip.setObjectName('Link')
        self.skip.clicked.connect(self.skip_step)
        self.next = QPushButton()
        self.next.setObjectName('Primary')
        self.next.clicked.connect(self.advance)
        nav.addWidget(self.back)
        nav.addStretch()
        nav.addWidget(self.skip)
        nav.addWidget(self.next)
        right.addLayout(nav)
        self.show_step(0)

    def welcome(self, box):
        box.addWidget(label('接下来大约 3 分钟，我们一起把我准备好。每一步都可以跳过，以后在设置里随时改。', 'PageIntro'))
        for icon, title, text in (('platforms', '陪你聊天', '记得你说过的事，会开心也会闹小脾气'),
                                  ('voice', '开口说话', '回复会念出来，也能直接和她语音聊天'),
                                  ('agent', '帮你做事', '整理文件、查资料，动手前都会先问你')):
            row = QHBoxLayout()
            row.setSpacing(12)
            glyph = QLabel()
            glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            glyph.setFixedSize(34, 34)
            glyph.setStyleSheet('background:#E6EEFF; border-radius:12px')
            glyph.setPixmap(line_icon(icon, theme.PRIMARY).pixmap(24, 24))
            row.addWidget(glyph)
            words = QVBoxLayout()
            words.addWidget(label(title, 'CardTitle'))
            words.addWidget(label(text))
            row.addLayout(words, 1)
            box.addLayout(row)

    def model_page(self, box):
        self.services = QButtonGroup(self)
        cards = QHBoxLayout()
        for i, name in enumerate(('DeepSeek\n推荐 · 中文好', 'MiniMax\n也支持语音', '其他服务\n填网址和密钥')):
            choice = QPushButton(name)
            choice.setCheckable(True)
            choice.setMinimumHeight(48)
            self.services.addButton(choice, i)
            cards.addWidget(choice)
        self.services.button(0).setChecked(True)
        box.addLayout(cards)
        self.region = QComboBox()
        self.region.addItem('MiniMax 国内站', 'minimax-cn')
        self.region.addItem('MiniMax 国际站', 'minimax-global')
        box.addWidget(self.region)
        self.endpoint = QLineEdit()
        self.endpoint.setPlaceholderText('兼容服务地址，如 https://服务地址/v1')
        self.model = QLineEdit()
        self.model.setPlaceholderText('服务商提供的模型名')
        for field in (self.endpoint, self.model):
            box.addWidget(field)
        self.instructions = QLabel('还没有密钥？三步拿到<br>1　打开 <a href="https://platform.deepseek.com/">DeepSeek 开放平台</a> 注册'
                                   '<br>2　在“充值”里充一点余额<br>3　在“API keys”里创建并复制密钥')
        self.instructions.setStyleSheet('background:white; border:1px solid #E3E8F3; border-radius:14px; padding:12px')
        self.instructions.setOpenExternalLinks(True)
        self.instructions.setWordWrap(True)
        box.addWidget(self.instructions)
        self.key = password('把密钥粘贴到这里')
        self.test = QPushButton('测试一下')
        self.test.clicked.connect(self.test_model)
        row = QHBoxLayout()
        row.addWidget(self.key, 1)
        row.addWidget(self.test)
        box.addLayout(row)
        self.model_status = label()
        box.addWidget(self.model_status)
        box.addWidget(label('密钥加密保存在这台电脑上。测试会向所选服务发送一句话。'))
        self.services.idToggled.connect(lambda index, on: self.change_service(index) if on else None)
        self.region.currentIndexChanged.connect(lambda: self.change_service(1))
        self.endpoint.textEdited.connect(self.clear_model_key)
        self.key.textChanged.connect(self.mark_model_dirty)
        self.model.textChanged.connect(self.mark_model_dirty)
        self.change_service(0)

    def mark_model_dirty(self):
        self.model_dirty = True

    def clear_model_key(self):
        self.account_id = None
        self.key.clear()
        self.model_dirty = False
        self.key.setPlaceholderText('把密钥粘贴到这里')
        self.model_status.clear()

    def change_service(self, index):
        self.clear_model_key()
        self.region.setVisible(index == 1)
        self.endpoint.setVisible(index == 2)
        self.model.setVisible(index == 2)
        self.instructions.setVisible(index == 0)

    def save_model(self):
        key = self.key.text().strip()
        if not self.model_dirty or (not key and not self.account_id):
            return True
        try:
            if self.admin is None:
                raise ValueError('此离线演示没有可修改的账户。')
            service = self.services.checkedId()
            preset = ('deepseek', self.region.currentData(), '')[service]
            url = check_base_url(self.endpoint.text()) if service == 2 else ''
            if service == 2 and not self.model.text().strip():
                raise ValueError('请填写服务商提供的模型名。')
            if not self.account_id:
                self.account_id = self.admin.add_account(preset, key=key, base_url=url).id
            elif key:
                self.admin.update_account(self.account_id, key=key)
            self.key.clear()
            if service == 2:
                model = self.admin.add_model(self.account_id, self.model.text())
            else:
                model = next(m for m in self.admin.models('chat') if m.provider == self.account_id)
            self.admin.choose('chat', self.cid, model.id)
        except (ValueError, RuntimeError, OSError) as error:
            self.model_status.setText(str(error))
            return False
        self.model_dirty = False
        self.key.setPlaceholderText('已加密保存，填写可更换密钥')
        return True

    def test_model(self):
        if not self.save_model():
            return
        if not self.account_id or not self.admin or not self.router.configured('chat', self.cid):
            self.model_status.setText('请先填写密钥。')
            return
        if self.router.offline:
            self.model_status.setText('应用处于离线模式，关闭离线模式后再测试。')
            return
        self.model_status.setText('正在等她回复…')
        self.art.setPixmap(portrait('thinking', 190, 250))
        self.pages.currentWidget().setEnabled(False)
        worker = TaskWorker(lambda token: self.admin.test_chat(self.cid, token), self.window)
        self.worker = worker
        self.window.tasks.add(worker)
        worker.succeeded.connect(self.test_ok)
        worker.failed.connect(self.test_failed)
        worker.finished.connect(self.test_finished)
        worker.finished.connect(lambda: self.window._task_finished(worker))
        worker.start()

    def test_ok(self, result):
        self.model_status.setText('✓ 连上了　她刚刚说：“' + result.partition('：')[-1] + '”')
        if self.pages.currentIndex() == 1:
            self.art.setPixmap(portrait('celebrate', 190, 250))

    def test_failed(self, message):
        self.model_status.setText(connection_problem(message))
        if self.pages.currentIndex() == 1:
            self.art.setPixmap(portrait('error', 190, 250))

    def test_finished(self):
        self.pages.widget(1).setEnabled(True)
        self.worker = None

    def voice_page(self, box):
        self.voices = QButtonGroup(self)
        for i, (title, hint) in enumerate((('电脑自带的声音', '免费，马上能用。'),
                                           ('鲸鲸专属声线', '软软的少女音，使用 MiniMax 语音服务，按用量付费，平时聊天花得很少。'),
                                           ('先不出声', '只打字聊天，以后点聊天窗口的喇叭就能打开。'))):
            card = QFrame()
            card.setObjectName('Card')
            content = QVBoxLayout(card)
            content.setContentsMargins(12, 4, 12, 8)
            choice = QRadioButton(title)
            choice.setMinimumHeight(36)
            self.voices.addButton(choice, i)
            content.addWidget(choice)
            content.addWidget(label(hint))
            box.addWidget(card)
        self.voice_region = QComboBox()
        self.voice_region.addItem('MiniMax 国内站', 'minimax-cn')
        self.voice_region.addItem('MiniMax 国际站', 'minimax-global')
        self.voice_key = password('语音服务密钥（已有可用声线时可留空）')
        self.voice_region.currentIndexChanged.connect(lambda: self.voice_key.clear())
        box.addWidget(self.voice_region)
        box.addWidget(self.voice_key)
        self.smart = QCheckBox('长回复只念重点，代码和表格不念')
        self.smart.setChecked(self.db.get_setting('speech_style', 'smart') == 'smart')
        self.smart.toggled.connect(lambda on: self.db.set_setting('speech_style', 'smart' if on else 'full'))
        box.addWidget(self.smart)
        voice = 0 if self.db.get_setting('tts_provider', 'system') == 'system' else 1
        choice = voice if self.db.get_setting('speech_enabled', True) else 2
        self.voices.button(choice).setChecked(True)
        self.listen = QPushButton('试听')
        self.listen.clicked.connect(self.preview_voice)
        box.addWidget(self.listen)
        self.voice_status = label()
        box.addWidget(self.voice_status)
        self.display_voice(choice)
        self.voices.idClicked.connect(self.select_voice)

    def display_voice(self, index):
        self.voice_region.setVisible(index == 1)
        self.voice_key.setVisible(index == 1)
        self.listen.setVisible(index != 2)

    def select_voice(self, index):
        self.voice_dirty = True
        self.display_voice(index)

    def save_voice(self):
        index = self.voices.checkedId()
        if index == 1 and self.voice_key.text():
            from aicompanion.domain.voice_profile import WHALE
            try:
                if not self.admin:
                    raise ValueError('此离线演示没有可修改的账户。')
                account = self.admin.add_account(self.voice_region.currentData(), key=self.voice_key.text())
                model = next(m for m in self.admin.models('tts') if m.provider == account.id and m.model_id == WHALE.minimax_model)
                self.admin.choose('tts', self.cid, model.id, voice=WHALE.minimax_voice, language='zh')
            except (ValueError, RuntimeError, OSError) as error:
                self.voice_status.setText(str(error))
                return False
            self.voice_key.clear()
            self.voice_dirty = True
        if self.voice_dirty:
            self.db.set_setting('speech_enabled', index != 2)
            if index != 2:
                self.db.set_setting('tts_provider', 'system' if index == 0 else 'breeze')
            self.voice_dirty = False
        return True

    def preview_voice(self):
        if not self.save_voice():
            return
        target = self.router.tts_target(self.cid) if self.voices.checkedId() == 1 else None
        if self.voices.checkedId() == 1 and not target:
            self.voice_status.setText('先填一个可用的 MiniMax 语音密钥，或以后再设置。')
            return
        speech = self.window.speech
        speech.stop()
        speech.enabled = True
        speech.provider = 'breeze' if target else 'system'
        speech.voice = target.cache_id if target else ''
        self.previewing = True
        play_voice_preview(speech, target)
        self.voice_status.setText('正在试听')

    def look_page(self, box):
        cards = QHBoxLayout()
        cards.setSpacing(16)
        builtin = QFrame()
        builtin.setObjectName('Card')
        content = QHBoxLayout(builtin)
        art = QLabel()
        art.setObjectName('Illustration')
        art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        art.setPixmap(portrait('idle', 106, 142))
        content.addWidget(art)
        words = QVBoxLayout()
        words.addWidget(label('自带的鲸鲸', 'CardTitle'))
        words.addWidget(label('✓ 已经装好', 'Good'), 0, Qt.AlignmentFlag.AlignLeft)
        words.addWidget(label('十种表情和动作，会眨眼、说话时张嘴。不用下载。'))
        content.addLayout(words, 1)
        cards.addWidget(builtin, 1)
        animated = QFrame()
        animated.setObjectName('AboutmeChoice')
        animated.setStyleSheet('QFrame#AboutmeChoice {background:white; border:2px solid #2F5BEA; border-radius:20px}')
        content = QHBoxLayout(animated)
        self.preview = AboutmePreview()
        self.preview.setFixedSize(110, 142)
        self.preview.placeholder.setText('Aboutme\n动画预览\n安装后显示')
        content.addWidget(self.preview)
        words = QVBoxLayout()
        words.addWidget(label('大肥鱼动画版', 'CardTitle'))
        words.addWidget(label('推荐', 'Status'))
        words.addWidget(label('社区画师的逐帧动画：挥手、伸懒腰、喝奶茶、左右走。装好后优先显示它。'))
        content.addLayout(words, 1)
        cards.addWidget(animated, 1)
        box.addLayout(cards)
        box.addWidget(label('仅限个人非商用。作者 PC2005-cloud（dsh-pet），整理 hunmer（aboutme-pets）。'))
        self.install_status = label()
        self.progress = QProgressBar()
        self.progress.hide()
        self.install = QPushButton('安装')
        self.install.clicked.connect(self.install_or_cancel)
        box.addWidget(self.install_status)
        box.addWidget(self.progress)
        box.addWidget(self.install)
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.install_progress)
        self.process.finished.connect(self.install_finished)
        self.process.errorOccurred.connect(self.install_error)
        self.script = Path(__file__).resolve().parents[3] / 'tools' / 'install_aboutme_pet.py'
        self.cancelled = False
        from aicompanion.ui.icons import WHALE
        if (WHALE / 'animation-aboutme' / 'animations.json').is_file():
            self.install.setText('已安装')
            self.install.setEnabled(False)

    def install_or_cancel(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.cancelled = True
            self.process.kill()
            return
        if getattr(sys, 'frozen', False) or not self.script.is_file():
            self.install_status.setText('请在源码目录安装 tools/aboutme-requirements.txt 中的依赖，再运行 '
                'tools/install_aboutme_pet.py --program-dir "桌宠程序目录"。完成后重启桌宠。详见 docs/aboutme-pet.md。')
            return
        lock = json.loads(self.script.with_name('aboutme-pet.lock.json').read_text(encoding='utf-8'))
        self.progress.setRange(0, len(lock['actions']))
        self.progress.setValue(0)
        self.progress.show()
        self.cancelled = False
        self.install.setText('取消安装')
        self.install_status.setText('正在下载并校验本机动画…')
        env = QProcessEnvironment.systemEnvironment()
        env.insert('PYTHONIOENCODING', 'utf-8')
        self.process.setProcessEnvironment(env)
        self.process.start(sys.executable, [str(self.script)])

    def install_progress(self):
        while self.process.canReadLine():
            if bytes(self.process.readLine()).decode('utf-8', 'replace').startswith('已安装 '):
                self.progress.setValue(self.progress.value() + 1)

    def install_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.install_finished(1)

    def install_finished(self, code, *_):
        self.progress.hide()
        if code == 0 and not self.cancelled:
            self.install.setEnabled(False)
            self.install.setText('已安装')
            self.install_status.setText('动画已就绪。')
            self.preview.refresh()
        else:
            self.install.setText('安装')
            self.install_status.setText('已取消，可以稍后继续。' if self.cancelled else
                                      '安装未完成。请检查网络和 tools/aboutme-requirements.txt 中的依赖后重试。')

    def finish_page(self, box):
        card = QFrame()
        card.setObjectName('Card')
        tips = QVBoxLayout(card)
        tips.setContentsMargins(16, 12, 16, 12)
        tips.addWidget(label('记住这三招就够了', 'CardTitle'))
        for title, hint in (('双击她', '直接说话'), ('右键她', '聊天窗口、语音、让她做事、设置'),
                            ('拖动她', '把她放到你喜欢的位置')):
            row = QHBoxLayout()
            action = label(title)
            action.setFixedWidth(72)
            action.setStyleSheet('color:#2F5BEA')
            row.addWidget(action)
            row.addWidget(label(hint, 'PageIntro'), 1)
            tips.addLayout(row)
        box.addWidget(card)
        box.addWidget(label('想要更多？随时可以在设置里打开'))
        for key, title, hint, link in (('platforms', '在 QQ 上也和她聊天', '约 5 分钟', '去设置'),
                                       ('agent', '让她帮你做事', '需要多装一个组件', '了解一下')):
            card = QFrame()
            card.setObjectName('Card')
            row = QHBoxLayout(card)
            row.setContentsMargins(12, 2, 8, 2)
            icon = QLabel()
            icon.setPixmap(line_icon(key, theme.PRIMARY).pixmap(20, 20))
            row.addWidget(icon)
            row.addWidget(label(title + '　' + hint, 'PageIntro'), 1)
            button = QPushButton(link)
            button.setObjectName('Link')
            button.clicked.connect(lambda checked=False, k=key: self.finish_at(k))
            row.addWidget(button)
            box.addWidget(card)
        self.autostart = QCheckBox('开机时让她自动出现')
        self.autostart.setChecked(desktop.autostart_enabled())
        self.autostart.setEnabled(desktop.IS_WINDOWS)
        self.autostart.clicked.connect(self.change_autostart)
        box.addWidget(self.autostart)
        self.start_status = label()
        box.addWidget(self.start_status)

    def change_autostart(self, checked):
        if not desktop.set_autostart(checked):
            self.autostart.setChecked(desktop.autostart_enabled())
            self.start_status.setText('开机启动项未能保存，请检查系统权限。')

    def finish_at(self, key):
        self.destination = key
        self.accept()

    def show_step(self, index):
        self.pages.setCurrentIndex(index)
        # 样子页按设计稿把插图放进横向对照卡，进度点移到标题行。
        self.rail.setVisible(index != 3)
        if index == 3:
            self.heading_row.addWidget(self.indicator)
        else:
            self.left.addWidget(self.indicator, 0, Qt.AlignmentFlag.AlignHCenter)
        self.brand.setVisible(index == 0)
        self.heading.setStyleSheet('font-size:28px' if index == 0 else '')
        suffix = ('', ' · 必需', ' · 可选', ' · 可选', '')[index]
        self.step.setText(f'第 {index + 1} 步，共 5 步' + suffix)
        self.heading.setText(self.titles[index])
        self.caption.setText(self.captions[index])
        self.caption.setVisible(bool(self.captions[index]))
        self.art.setPixmap(portrait(self.poses[index], 220 if index == 0 else 200, 295 if index == 0 else 268))
        for i, point in enumerate(self.points):
            point.setFixedSize(22 if i == index else 8, 8)
            color = theme.PRIMARY if i <= index else '#B9C8EE'
            point.setStyleSheet(f'background:{color}; border-radius:4px')
        self.back.setVisible(index > 0)
        self.skip.setVisible(index < 4)
        self.skip.setText(('先随便看看', '先跳过（离线试用）', '先跳过', '用自带的就好', '')[index])
        self.next.setText(('开始准备', '下一步', '下一步', '下一步', '开始陪伴')[index])

    def navigate(self, target, skip=False):
        current = self.pages.currentIndex()
        if current == 1 and not skip and not self.save_model():
            return
        if current == 2 and not skip:
            if self.db.get_setting('tts_provider') is None:
                self.voice_dirty = True
            if not self.save_voice():
                return
        self.show_step(target)

    def skip_step(self):
        current = self.pages.currentIndex()
        self.navigate(4 if current == 0 else current + 1, skip=True)

    def advance(self):
        current = self.pages.currentIndex()
        if current == 4:
            self.accept()
        else:
            self.navigate(current + 1)

    def done(self, result):
        current = self.pages.currentIndex()
        if current == 1:
            self.save_model()
        if current == 2:
            self.save_voice()
        if self.worker:
            self.worker.cancellation.cancel()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.cancelled = True
            self.process.kill()
            self.process.waitForFinished(1000)
        if self.previewing:
            self.window.speech.stop()
        self.preview.shutdown()
        self.db.set_setting('onboarding_done', True)
        super().done(result)
