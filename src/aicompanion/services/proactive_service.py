from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from aicompanion.controllers.chat_controller import ChatController


class ProactiveMessageService(QObject):
    """主动消息定时服务。

    这个服务不直接决定“说什么”，它只负责定时触发检查。
    真正的主动消息规则放在 ChatController.maybe_create_proactive_message()。
    """

    message_ready = Signal(str)

    def __init__(self, controller: ChatController, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.timer = QTimer(self)
        self.timer.setInterval(60_000) # 60 秒
        self.timer.timeout.connect(self._on_timer)

    def start(self) -> None:
        """启动定时检查。"""

        self.timer.start()

    def _on_timer(self) -> None:
        """每次定时器触发时，询问控制器是否需要发主动消息。"""

        message = self.controller.maybe_create_proactive_message()
        if message:
            self.message_ready.emit(message)
