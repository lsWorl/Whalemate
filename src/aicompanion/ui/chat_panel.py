"""聊天面板的展示层，发送、通话和角色切换沿用主窗口的入口。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QComboBox
from aicompanion.agent import attachments
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget)

from aicompanion.ui import theme
from aicompanion.ui.chat_view import Backdrop, ChatView
from aicompanion.ui.icons import app_icon, icon_button


class AttachmentPicker(QWidget):
    """只有桌面输入栏持有待发送清单，其他渠道和自动办事入口拿不到它。"""
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.paths = []
        self.dialog = None
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.names = QLabel()
        self.names.setTextFormat(Qt.TextFormat.PlainText)
        self.names.setWordWrap(True)
        row.addWidget(self.names, 1)
        clear = QPushButton('清空附件')
        clear.clicked.connect(self.clear)
        row.addWidget(clear)
        self.hide()

    def open(self):
        if self.dialog:
            self.dialog.raise_()
            return
        dialog = self.dialog = QFileDialog(self.window, '把文件交给她 · '+attachments.LIMIT_HINT)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
        dialog.setOption(QFileDialog.Option.DontResolveSymlinks, True)
        dialog.setNameFilters(['支持的文件 ('+' '.join('*.'+e for e in sorted(
            attachments.TEXT | attachments.DOCUMENTS | attachments.IMAGES.keys()))+')', '全部文件 (*)'])
        dialog.filesSelected.connect(self.add)
        dialog.finished.connect(lambda _: setattr(self, 'dialog', None))
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()

    def add(self, paths):
        try:
            files = attachments.selection(list(dict.fromkeys([*self.paths, *map(str, paths)])),
                                          self.window.agent.box.secrets)
        except (ValueError, OSError) as error:
            self.window._status(str(error))
            self.window.pet.show_bubble(str(error), 7)
            return
        if not files:
            return
        self.paths = [str(path) for path, _ in files]
        self.names.setText('、'.join(path.name for path, _ in files))
        self.names.setToolTip('\n'.join(self.paths))
        self.window.agent_toggle.setChecked(True)
        self.show()

    def clear(self):
        self.paths.clear()
        self.names.clear()
        self.hide()


class TaskCard(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName('Card')
        box = QVBoxLayout(self)
        box.setContentsMargins(14, 10, 14, 12)
        title = QLabel('她正在帮你做事')
        title.setObjectName('CardTitle')
        box.addWidget(title)
        self.detail = QLabel()
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setWordWrap(True)
        box.addWidget(self.detail)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(90)
        self.details.setAccessibleName('待审批的操作详情')
        box.addWidget(self.details)
        progress = QProgressBar()
        progress.setRange(0, 0)
        progress.setFixedHeight(6)
        progress.setTextVisible(False)
        box.addWidget(progress)
        self.actions = QWidget()
        row = QHBoxLayout(self.actions)
        row.setContentsMargins(0, 0, 0, 0)
        self.allow = QPushButton('同意这一次')
        self.allow.setAccessibleName('同意本次操作')
        self.deny = QPushButton('拒绝')
        self.deny.setObjectName('Danger')
        row.addWidget(self.allow)
        row.addWidget(self.deny)
        row.addStretch()
        self.reuse = QPushButton('本次都同意这类')
        self.persist = QPushButton('以后都允许')
        self.queue_count = QLabel()
        self.queue_count.hide()
        box.addWidget(self.queue_count)
        self.batch = QPushButton('全部同意')
        row.insertWidget(1,self.reuse)
        row.insertWidget(2,self.persist)
        self.reuse.clicked.connect(lambda:self.respond('always'))
        self.persist.clicked.connect(lambda:self.respond('persist'))
        box.addWidget(self.actions)
        box.addWidget(self.batch)
        self.batch.hide()
        self.answer = None
        self.allow.clicked.connect(lambda: self.respond('once'))
        self.deny.clicked.connect(lambda: self.respond('no'))
        self.clear_approval()
        self.hide()

    def respond(self, choice):
        callback = self.answer
        self.clear_approval()
        if callback:
            callback(choice)

    def clear_approval(self):
        self.answer = None
        self.actions.hide()
        self.details.hide()


def build(window):
    w = window
    w.setWindowTitle(w.settings.app_name + ' · 聊天面板')
    w.resize(420, 760)
    w.setMinimumSize(400, 480)
    root = w.backdrop = Backdrop()
    layout = QVBoxLayout(root)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    header = QWidget()
    header.setObjectName('Header')
    top = QHBoxLayout(header)
    top.setContentsMargins(16, 12, 14, 12)
    w.avatar_label = QLabel()
    w.avatar_label.setPixmap(app_icon().pixmap(42, 42))
    top.addWidget(w.avatar_label)
    names = QVBoxLayout()
    names.setSpacing(0)
    w.name_label = QPushButton(w.controller.character.name)
    w.name_label.setObjectName('Name')
    names.addWidget(w.name_label, 0, Qt.AlignmentFlag.AlignLeft)
    w.presence = QLabel()
    w.presence.setObjectName('Presence')
    names.addWidget(w.presence)
    top.addLayout(names, 1)
    w._refresh_roles()
    w.speak_toggle = icon_button('voice', '回复时念出来')
    w.speak_toggle.setCheckable(True)
    w.speak_toggle.toggled.connect(w._toggle_speech)
    top.addWidget(w.speak_toggle)
    settings = icon_button('gear', '设置')
    settings.clicked.connect(lambda: w._settings())
    top.addWidget(settings)
    layout.addWidget(header)

    w.setup_banner = QWidget()
    w.setup_banner.setObjectName('Banner')
    banner = QHBoxLayout(w.setup_banner)
    w.setup_text = QLabel()
    w.setup_text.setWordWrap(True)
    banner.addWidget(w.setup_text, 1)
    go = QPushButton('去连接模型')
    go.clicked.connect(lambda: w._settings('models'))
    banner.addWidget(go)
    layout.addWidget(w.setup_banner)
    w.older = QPushButton('加载更早记录')
    w.older.setObjectName('Flat')
    w.older.clicked.connect(lambda: w._load_history(older=True))
    layout.addWidget(w.older, 0, Qt.AlignmentFlag.AlignHCenter)
    w.chat_list = ChatView('你')
    layout.addWidget(w.chat_list, 1)
    w.task_card = TaskCard()
    w.task_card.batch.clicked.connect(w.agent.approve_batch)
    task_row = QHBoxLayout()
    task_row.setContentsMargins(36, 0, 4, 4)
    task_row.addWidget(w.task_card)
    w.chat_list.body.layout().insertLayout(1, task_row)
    w.subtitle = QLabel()
    w.subtitle.setObjectName('Subtitle')
    w.subtitle.setWordWrap(True)
    w.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
    w.subtitle.hide()
    layout.addWidget(w.subtitle)

    footer = QWidget()
    footer.setObjectName('Footer')
    wrap = QVBoxLayout(footer)
    wrap.setContentsMargins(14, 10, 14, 8)
    segments = QFrame()
    segments.setObjectName('Segments')
    modes = QHBoxLayout(segments)
    modes.setContentsMargins(4, 3, 4, 3)
    modes.setSpacing(2)
    w.chat_mode = QPushButton('聊天')
    w.agent_toggle = QPushButton('帮我做事')
    for mode in (w.chat_mode, w.agent_toggle):
        mode.setObjectName('Mode')
        mode.setCheckable(True)
        modes.addWidget(mode)
    w.chat_mode.clicked.connect(lambda: (w.agent_toggle.setChecked(False), w.chat_mode.setChecked(True)))
    w.agent_toggle.toggled.connect(lambda on: w.chat_mode.setChecked(not on))
    w.chat_mode.setChecked(True)
    from aicompanion.agent.approval import DEFAULT_MODE, MODES
    w.approval_mode = QComboBox()
    w.approval_mode.setAccessibleName('当前办事审批模式')
    for value,label in MODES.items():
        w.approval_mode.addItem(label,value)
    w.approval_mode.setCurrentIndex(max(0,w.approval_mode.findData(w.database.get_setting('approval_mode', DEFAULT_MODE))))
    w.approval_mode.currentIndexChanged.connect(lambda _:setattr(w.agent.box.task,'mode',w.approval_mode.currentData()))
    modes.addWidget(w.approval_mode)
    wrap.addWidget(segments, 0, Qt.AlignmentFlag.AlignLeft)
    bar = QWidget()
    bar.setObjectName('InputBar')
    row = QHBoxLayout(bar)
    row.setContentsMargins(4, 4, 4, 4)
    row.setSpacing(2)
    w.attachments = AttachmentPicker(w)
    wrap.addWidget(w.attachments)
    w.setAcceptDrops(True)
    w.attach_button = icon_button('plus', '添加办事附件，可拖入文件。'+attachments.LIMIT_HINT)
    w.attach_button.clicked.connect(w.attachments.open)
    row.addWidget(w.attach_button)
    w.input_box = QLineEdit()
    w.input_box.setAcceptDrops(False)
    w.input_box.setObjectName('Input')
    w.input_box.returnPressed.connect(w._send_current_message)
    row.addWidget(w.input_box, 1)
    w.stop_button = icon_button('stop', '让她停下')
    w.stop_button.clicked.connect(lambda: (w.interrupt(), w._stop_agent()))
    w.stop_button.hide()
    row.addWidget(w.stop_button)
    w.call_button = icon_button('mic', '语音聊天（默认按住右 Ctrl 说话）')
    w.call_button.clicked.connect(w._toggle_call)
    row.addWidget(w.call_button)
    send = icon_button('send', '发送', primary=True)
    send.clicked.connect(w._send_current_message)
    row.addWidget(send)
    wrap.addWidget(bar)
    w.mode_hint = QLabel('可添加或拖入附件。'+attachments.LIMIT_HINT)
    w.mode_hint.setObjectName('Hint')
    wrap.addWidget(w.mode_hint)
    w.agent_toggle.toggled.connect(w._agent_mode_changed)
    w._agent_mode_changed(False)
    w.level = QProgressBar()
    w.level.setFixedHeight(4)
    w.level.setTextVisible(False)
    w.level.hide()
    wrap.addWidget(w.level)
    w.status = QLabel()
    w.status.setTextFormat(Qt.TextFormat.PlainText)
    w.status.setWordWrap(True)
    w.status.setObjectName('Status')
    w.status.hide()
    wrap.addWidget(w.status)
    layout.addWidget(footer)
    w.setCentralWidget(root)
    w.setStyleSheet(theme.PANEL)
    theme.prepare_controls(root)
