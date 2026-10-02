from __future__ import annotations

from PySide6.QtCore import QThread, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from aicompanion.controllers.chat_controller import ChatController
from aicompanion.ui.chat_worker import ChatWorker


class MainWindow(QMainWindow):
    """应用主窗口。

    这个类只处理界面相关的事情：
    - 创建聊天列表、输入框、发送按钮
    - 把用户输入交给 ChatController
    - 接收后台线程返回的 token，并逐段更新 AI 消息

    它不直接读写 SQLite，也不直接拼 prompt。这样 UI 层会比较干净。
    """

    def __init__(self, controller: ChatController) -> None:
        super().__init__()
        self.controller = controller
        self.current_assistant_item: QListWidgetItem | None = None
        self.current_assistant_text = ""
        self.chat_thread: QThread | None = None
        self.chat_worker: ChatWorker | None = None

        self.setWindowTitle("AICompanion")
        self.chat_list = QListWidget()
        self.input_box = QLineEdit()
        self.input_box.setPlaceholderText("和她说点什么...")
        self.send_button = QPushButton("发送")

        self._build_layout()
        self._connect_signals()
        self._load_history()

    def append_assistant_message(self, content: str) -> None:
        """显示一条非流式的 AI 消息。

        主动消息服务目前会直接生成完整文本，所以走这个方法。
        """

        self._append_message("Emilia", content, align_right=False)

    def _build_layout(self) -> None:
        """创建窗口布局和基础样式。"""

        root = QWidget()
        layout = QVBoxLayout(root)

        title = QLabel("AICompanion")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setObjectName("titleLabel")

        input_row = QHBoxLayout()
        input_row.addWidget(self.input_box, stretch=1)
        input_row.addWidget(self.send_button)

        layout.addWidget(title)
        layout.addWidget(self.chat_list, stretch=1)
        layout.addLayout(input_row)

        self.setCentralWidget(root)
        self.setStyleSheet(
            """
            QMainWindow {
                background: #f7f4ef;
            }
            QLabel#titleLabel {
                font-size: 22px;
                font-weight: 700;
                color: #31343a;
                padding: 10px;
            }
            QListWidget {
                background: #fffaf2;
                border: 1px solid #ded6c8;
                border-radius: 8px;
                padding: 8px;
                font-size: 14px;
            }
            QLineEdit {
                border: 1px solid #cfc8bb;
                border-radius: 6px;
                padding: 9px;
                font-size: 14px;
                background: #ffffff;
            }
            QPushButton {
                background: #446a72;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 9px 18px;
                font-size: 14px;
            }
            QPushButton:hover {
                background: #36575e;
            }
            QPushButton:disabled {
                background: #9aa7aa;
            }
            """
        )

    def _connect_signals(self) -> None:
        """绑定按钮和回车键事件。"""
        # 点击按钮
        self.send_button.clicked.connect(self._send_current_message)
        # 回车键
        self.input_box.returnPressed.connect(self._send_current_message)   

    def _load_history(self) -> None:
        """启动时加载最近聊天记录。"""

        for turn in self.controller.load_recent_history():
            sender = "你" if turn.role == "user" else "Emilia"
            self._append_message(sender, turn.content, align_right=turn.role == "user")

    def _send_current_message(self) -> None:
        """读取输入框内容，并启动后台线程请求模型。"""
        # 检查输入 .strip()用于去除空格
        content = self.input_box.text().strip()
        if not content:
            return

        self.input_box.clear()
        self._set_input_enabled(False)
        self._append_message("你", content, align_right=True)
        self._start_streaming_reply(content)

    def _start_streaming_reply(self, content: str) -> None:
        """创建 QThread 和 ChatWorker，开始接收流式回复。"""

        self.current_assistant_text = ""
        self.current_assistant_item = self._append_message(
            "Emilia",
            "正在思考...",
            align_right=False,
        )

        self.chat_thread = QThread(self)
        self.chat_worker = ChatWorker(self.controller, content)
        self.chat_worker.moveToThread(self.chat_thread)

        self.chat_thread.started.connect(self.chat_worker.run)
        self.chat_worker.token_received.connect(self._append_stream_token)
        self.chat_worker.finished.connect(self._finish_streaming_reply)
        self.chat_worker.failed.connect(self._fail_streaming_reply)
        self.chat_worker.finished.connect(self.chat_thread.quit)
        self.chat_worker.failed.connect(self.chat_thread.quit)
        self.chat_thread.finished.connect(self.chat_worker.deleteLater)
        self.chat_thread.finished.connect(self.chat_thread.deleteLater)
        self.chat_thread.start()

    def _append_stream_token(self, token: str) -> None:
        """把后台线程返回的 token 追加到当前 AI 消息上。"""

        self.current_assistant_text += token
        if self.current_assistant_item is not None:
            self.current_assistant_item.setText(
                f"Emilia: {self.current_assistant_text}"
            )
            self.chat_list.scrollToBottom()

    def _finish_streaming_reply(self) -> None:
        """AI 回复正常结束后恢复输入。"""

        if self.current_assistant_item is not None and not self.current_assistant_text:
            self.current_assistant_item.setText("Emilia: ……")
        self._cleanup_thread_refs()
        self._set_input_enabled(True)

    def _fail_streaming_reply(self, error_message: str) -> None:
        """AI 回复失败时在聊天窗口显示错误信息。"""

        if self.current_assistant_item is not None:
            self.current_assistant_item.setText(f"Emilia: 请求失败：{error_message}")
        self._cleanup_thread_refs()
        self._set_input_enabled(True)

    def _set_input_enabled(self, enabled: bool) -> None:
        """统一控制输入框和按钮是否可用。"""

        self.input_box.setEnabled(enabled)
        self.send_button.setEnabled(enabled)
        if enabled:
            self.input_box.setFocus()

    def _cleanup_thread_refs(self) -> None:
        """清空线程引用，避免下一次发送消息时误用旧对象。"""

        self.chat_thread = None
        self.chat_worker = None

    def _append_message(
        self,
        sender: str,
        content: str,
        align_right: bool,
    ) -> QListWidgetItem:
        """向聊天列表追加一条消息，并返回对应的 QListWidgetItem。"""

        item = QListWidgetItem(f"{sender}: {content}")
        item.setTextAlignment(
            Qt.AlignmentFlag.AlignRight if align_right else Qt.AlignmentFlag.AlignLeft
        )
        self.chat_list.addItem(item)
        self.chat_list.scrollToBottom() # 滚动到底部
        return item
