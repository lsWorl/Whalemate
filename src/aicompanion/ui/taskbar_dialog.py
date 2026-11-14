"""从桌宠直接打开的主对话框也需要有自己的 Windows 任务栏入口。"""
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from aicompanion.ui.icons import app_icon


class TaskbarDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowIcon(app_icon())

    def showEvent(self, event):
        if sys.platform == 'win32':
            from ctypes import windll
            from ctypes.wintypes import HWND
            # 保留 Qt 父窗口和模态行为。Windows 默认隐藏有所有者的窗口，需要在显示之前要求任务栏收录。
            hwnd = HWND(int(self.winId()))
            user32 = windll.user32
            ex_style = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
            user32.SetWindowLongW(hwnd, -20, ex_style | 0x40000)  # WS_EX_APPWINDOW
        super().showEvent(event)
