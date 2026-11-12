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


# 路径取自负责人确认的设计稿，集中着色并按高分屏渲染。
GLYPHS = {
    'check': '<path d="m4 12 5 5L20 6"/>',
    'overview': '<path d="M3 11l9-8 9 8v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
    'persona': '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    'relationship': '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 0 0 0-7.8z"/>',
    'memory': '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z"/>',
    'proactive': '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
    'models': '<path d="M12 2a7 7 0 0 0-4 12.7V17h8v-2.3A7 7 0 0 0 12 2z"/><path d="M9 21h6"/>',
    'voice': '<path d="M11 5 6 9H3v6h3l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/>',
    'agent': '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.5-.5-.5-2.5z"/>',
    'library': '<circle cx="12" cy="12" r="9"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><path d="M9 9h.01M15 9h.01"/>',
    'platforms': '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    'storage': '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    'usage': '<path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/>',
    'window': '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M7 9h10M7 13h6"/>',
    'mic': '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/>',
    'clock': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    'star': '<path d="M12 3l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.4 6.8 19.1l1-5.8L3.5 9.2l5.9-.9z"/>',
    'size': '<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>',
    'gear': '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    'moon': '<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/>',
    'quit': '<path d="M18.4 6.6a9 9 0 1 1-12.8 0"/><path d="M12 2v10"/>',
    'attach': '<path d="m21.4 11.6-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5"/>',
    'send': '<path d="M12 19V5"/><path d="m5 12 7-7 7 7"/>',
    'stop': '<rect x="5" y="5" width="14" height="14" rx="2"/>',
}


def line_icon(name, color='#4B587A'):
    from PySide6.QtCore import QByteArray, Qt
    from PySide6.QtGui import QIcon, QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
           f'stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
           f'{GLYPHS[name]}</svg>')
    image = QPixmap(48, 48)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    image.setDevicePixelRatio(2)
    return QIcon(image)


def icon_button(name, label, primary=False):
    from PySide6.QtCore import QSize
    from PySide6.QtWidgets import QPushButton

    button = QPushButton()
    button.setIcon(line_icon(name, '#FFFFFF' if primary else '#4B587A'))
    button.setIconSize(QSize(20, 20))
    button.setFixedSize(40, 40)
    button.setObjectName('Send' if primary else 'Round')
    button.setAccessibleName(label)
    button.setToolTip(label)
    return button


WHALE = Path(__file__).resolve().parents[1] / 'characters' / 'deepseek-whale'


def portrait(pose, width, height):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap

    image = QPixmap(str(WHALE / 'sprites' / f'{pose}.png'))
    image = image.scaled(width * 2, height * 2, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    image.setDevicePixelRatio(2)
    return image
