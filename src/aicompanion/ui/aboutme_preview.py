"""只在本机安装包存在时启动动画解码线程。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from aicompanion.ui.icons import WHALE


class AboutmePreview(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('Illustration')
        self.setFixedHeight(96)
        self.box = QVBoxLayout(self)
        self.placeholder = QLabel('Aboutme 动画预览\n安装后显示')
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.box.addWidget(self.placeholder)
        self.animation = None

    def refresh(self):
        path = WHALE / 'animation-aboutme' / 'animations.json'
        if self.animation or not path.is_file():
            return
        from aicompanion.ui.animation_view import AnimationAvatar
        try:
            self.animation = AnimationAvatar(path, self)
        except (ValueError, OSError) as error:
            self.placeholder.setText(f'动画无法预览：{error}')
            return
        self.placeholder.hide()
        self.box.addWidget(self.animation)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def shutdown(self):
        if self.animation:
            self.animation.shutdown()
