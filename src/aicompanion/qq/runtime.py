"""进程内唯一的 QQManager，和桥接接口共用心跳；应用启动时按设置自动启动组件，退出时停掉我们启动的。"""
from __future__ import annotations

import threading

_manager = None


def manager(database):
    global _manager
    if _manager is None or _manager.db is not database:
        from aicompanion.bridge.server import runtime as bridge
        from aicompanion.qq.manager import QQManager
        _manager = QQManager(database, heartbeat=lambda: bridge.last_heartbeat)
    return _manager


def autostart(database) -> None:
    """打开桌宠时：自动管理模式、组件齐全、勾了"自动启动"就在后台启动（不阻塞界面）。失败原因记在 manager.last_error，设置页显示。"""
    qq = manager(database)
    if qq.mode != 'auto' or not database.get_setting('qq_autostart', True) or qq.ready(): return

    def work():
        try: qq.start()
        except Exception:
            pass   # start 已保留脱敏后的失败原因，设置页和复制诊断都能看到
    threading.Thread(target=work, daemon=True, name='aicompanion-qq-start').start()


def shutdown(database) -> None:
    """退出桌宠时停掉我们启动的 NapCat 和 AstrBot（"自动启动"关掉时保留它们）。"""
    if _manager is not None and _manager.mode == 'auto' and database.get_setting('qq_autostart', True):
        _manager.stop()
