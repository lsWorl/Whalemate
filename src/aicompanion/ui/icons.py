"""应用图标（鲸娘头像徽章，Codex 生成，见 data/icons 的说明）。窗口、托盘、任务栏和打包后的 exe 共用。"""
from __future__ import annotations

import sys
from pathlib import Path

ICON_DIR = Path(__file__).resolve().parents[1]/'assets'/'icons'
ICO = ICON_DIR/'aicompanion.ico'
PNG = ICON_DIR/'aicompanion-256.png'
APP_ID = 'AICompanion.DesktopPet'


def app_icon():
    """多尺寸图标：.ico 里有 16～256 像素各档，小尺寸更清楚；缺文件时退回 PNG，再不行返回空图标。"""
    from PySide6.QtGui import QIcon
    icon = QIcon()
    for path in (ICO, PNG):
        if path.is_file(): icon.addFile(str(path))
    return icon


def use_own_taskbar_identity() -> None:
    """Windows 任务栏默认按 python.exe 分组并显示 Python 图标；声明自己的应用 ID 后显示鲸娘图标。"""
    if sys.platform != 'win32': return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError, OSError):
        pass
