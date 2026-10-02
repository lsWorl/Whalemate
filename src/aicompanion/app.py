from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from aicompanion.config import load_settings
from aicompanion.controllers.chat_controller import ChatController
from aicompanion.llm.factory import create_llm_client
from aicompanion.services.memory_service import MemoryService
from aicompanion.services.proactive_service import ProactiveMessageService
from aicompanion.storage.database import Database
from aicompanion.ui.main_window import MainWindow


def main() -> int:
    """应用程序入口。

    这里负责把所有模块“装配”起来：
    1. 读取配置。
    2. 创建 QApplication。
    3. 初始化 SQLite。
    4. 创建服务层、控制器和主窗口。
    5. 启动主动消息定时器。

    业务逻辑不要写在这里；这里应该保持像“接线板”一样清晰。
    """
    # 加载配置
    settings = load_settings()
    #QApplication用于创建 GUI 应用程序
    app = QApplication(sys.argv)
    app.setApplicationName(settings.app_name)

    # Database 每次操作都会创建短生命周期连接，避免在 UI 线程和工作线程之间
    # 共享同一个 sqlite3.Connection。
    database = Database(settings.database_path)
    database.initialize()

    memory_service = MemoryService(database)
    llm_client = create_llm_client(settings)
    controller = ChatController(
        database=database,
        memory_service=memory_service,
        llm_client=llm_client,
    )

    window = MainWindow(controller=controller)

    # ProactiveMessageService 使用 QTimer 定期检查是否需要主动发消息。
    # 目前它仍然是规则触发，后续可以升级为“规则判断 + LLM 生成主动消息”。
    proactive_service = ProactiveMessageService(controller=controller, parent=window)
    proactive_service.message_ready.connect(window.append_assistant_message)
    proactive_service.start()

    window.resize(920, 640)
    window.show()

    return app.exec()
