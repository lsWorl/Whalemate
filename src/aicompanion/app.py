from __future__ import annotations

import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from aicompanion.characters.registry import CharacterRegistry

from aicompanion.config import load_settings
from aicompanion.controllers.chat_controller import ChatController
from aicompanion.services.memory_service import MemoryService
from aicompanion.storage.database import Database
from aicompanion.ui.main_window import MainWindow


INSTANCE_NAME = 'AICompanion-single-instance'


def _already_running() -> bool:
    """已有实例在运行时通知它召回角色，并返回 True。"""
    socket = QLocalSocket()
    socket.connectToServer(INSTANCE_NAME)
    if socket.waitForConnected(300):
        socket.write(b'summon'); socket.flush(); socket.waitForBytesWritten(300); socket.disconnectFromServer()
        return True
    return False


def _listen_for_second_instance(window):
    server = QLocalServer(window)
    QLocalServer.removeServer(INSTANCE_NAME)  # 上次异常退出可能留下失效的管道名
    def connected():
        while server.hasPendingConnections():
            connection = server.nextPendingConnection()
            connection.readyRead.connect(lambda c=connection: (c.readAll(), window.pet.summon()))
            connection.disconnected.connect(connection.deleteLater)
    server.newConnection.connect(connected)
    server.listen(INSTANCE_NAME)
    return server


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
    #QApplication用于创建 GUI 应用程序
    from aicompanion.body.webengine_flags import apply_webengine_flags
    apply_webengine_flags()
    from aicompanion.ui.icons import app_icon, use_own_taskbar_identity
    use_own_taskbar_identity()
    app = QApplication(sys.argv)
    app.setWindowIcon(app_icon())   # 所有窗口和对话框默认用鲸娘图标
    # 发布包自检只使用临时数据库和离线模型，不读取或上传用户聊天。
    smoke_directory = tempfile.TemporaryDirectory() if any(flag in sys.argv for flag in ('--smoke-test','--pet-smoke-test')) else None
    try:
        settings = load_settings()
        if smoke_directory:
            settings = replace(settings, offline=True, database_path=Path(smoke_directory.name)/'smoke.sqlite3',
                               tts_provider='system',qwen_api_key='',speech_language='zh')
    except Exception as error:
        if smoke_directory:
            print('配置错误:',error,flush=True)
            return 1
        QMessageBox.critical(None,'配置错误',str(error))
        return 1
    app.setApplicationName(settings.app_name)
    if not smoke_directory and _already_running():
        return 0

    # Database 每次操作都会创建短生命周期连接，避免在 UI 线程和工作线程之间
    # 共享同一个 sqlite3.Connection。
    try:
        if not smoke_directory:
            from aicompanion.storage.locations import prepare
            prepare(settings)
        database = Database(settings.database_path)
        database.initialize()
        if not smoke_directory:
            # 聊天平台的局部保存按钮也可选图库：已有选择优先，不用旧路径记录倒灌。
            if not database.get_setting('qq_meme_root', ''):
                database.set_setting('qq_meme_root', str(settings.meme_directory))
        if not smoke_directory:
            # T21：第一次启动时装入预置词库（常用梗），以后只补新版本新增的词；失败不影响启动
            try:
                from aicompanion.community.seed import install as install_seed_lexicon
                install_seed_lexicon(database)
            except Exception:
                pass
        memory_service = MemoryService(database)
        # 角色包同步到数据库（鲸娘等），并恢复上次选择的角色。
        registry = CharacterRegistry(database, settings)
        registry.sync()
        # 模型按“角色 + 能力 + 用途”从设置里的模型配置解析（计划书 v4 功能一）
        from aicompanion.providers.router import ModelRouter
        controller = ChatController(database, memory_service, ModelRouter(database, settings))
        controller.character = registry.active()
        window = MainWindow(controller=controller, settings=settings, registry=registry)
    except Exception as error:
        if smoke_directory:
            import traceback
            traceback.print_exc()
            return 1
        QMessageBox.critical(None, '启动失败', f'无法初始化应用：{error}')
        return 1

    # 窗口管理托盘、主动调度与资源退出，网络任务不会阻塞 Qt 主线程。
    app.setQuitOnLastWindowClosed(False)

    # v2：启动即显示桌面角色；聊天面板只在需要时从角色右键或双击托盘打开。
    window.pet.show()
    if smoke_directory:
        window.show()
    else:
        window._instance_server = _listen_for_second_instance(window)
        # 聊天平台接口（T9，默认关闭）：设置里打开后，AstrBot 插件把 QQ 消息交给同一个她
        from aicompanion.bridge.server import runtime as bridge
        problem = bridge.apply(database, controller)
        if problem: window._status(problem)
        app.aboutToQuit.connect(bridge.close)
        # 一键接入 QQ（T9 第二步）：自动管理模式下打开桌宠时在后台启动 NapCat / AstrBot，退出时停掉
        from aicompanion.qq import runtime as qq
        qq.autostart(database)
        app.aboutToQuit.connect(lambda: qq.shutdown(database))
        from aicompanion.ui.onboarding import needs_onboarding
        if needs_onboarding(controller):
            QTimer.singleShot(0, window.open_onboarding)
        QTimer.singleShot(1500, window.pet.drop_in)  # 从屏幕顶部掉下来，落地后打招呼
    if smoke_directory:
        if '--pet-smoke-test' in sys.argv:
            from aicompanion.ui.pet_smoke_check import schedule_pet_smoke_check
            schedule_pet_smoke_check(window, database, Path.cwd()/'bundle-pet-smoke')
        else:
            from aicompanion.ui.smoke_check import schedule_smoke_check
            schedule_smoke_check(window, database, Path.cwd()/'bundle-smoke')
    code = app.exec()
    if smoke_directory:
        smoke_directory.cleanup()
    return code
