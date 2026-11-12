"""设置窗口的骨架：左侧分组导航 + 右侧页面，每页有标题、一句说明和若干卡片。

为什么单独成文件：设置项会随计划书 v4 越来越多（人格包、心情、生活、约定……），页面只要“往某组加一页、往某页加一张卡”，
不用再去改一长串标签页。还没做的功能用“即将推出”卡片占位，用户知道以后会在哪里出现。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QScrollArea, QStackedWidget,
                               QTabWidget, QVBoxLayout, QWidget)


class Section(QFrame):
    """一张卡片：小标题 + 可选说明 + 内容。content 可以是控件或布局。"""

    def __init__(self, title: str, hint: str = '', content=None, parent=None):
        super().__init__(parent)
        self.setObjectName('Card')
        self.box = QVBoxLayout(self); self.box.setContentsMargins(16, 12, 16, 14); self.box.setSpacing(8)
        head = QLabel(title); head.setObjectName('CardTitle'); self.box.addWidget(head)
        if hint:
            note = QLabel(hint); note.setObjectName('Hint'); note.setWordWrap(True); self.box.addWidget(note)
        if content is not None: self.add(content)

    def add(self, content):
        if isinstance(content, QWidget): self.box.addWidget(content)
        else: self.box.addLayout(content)
        return content


class ComingSoon(QFrame):
    """还没做的功能：告诉用户它会出现在这里、能做什么。"""

    def __init__(self, title: str, text: str, parent=None):
        super().__init__(parent)
        self.setObjectName('Soon')
        box = QVBoxLayout(self); box.setContentsMargins(16, 10, 16, 12); box.setSpacing(4)
        head = QLabel(f'{title}　<span style="font-size:11px;color:#8A93AD;">即将推出</span>'); head.setObjectName('CardTitle')
        head.setTextFormat(Qt.TextFormat.RichText); box.addWidget(head)
        body = QLabel(text); body.setObjectName('Hint'); body.setWordWrap(True); box.addWidget(body)


class Page(QScrollArea):
    """一页设置：标题、一句话说明，下面依次放卡片。"""

    def __init__(self, title: str, intro: str, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True); self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget(); body.setObjectName('PageBody'); self.setWidget(body)
        self.box = QVBoxLayout(body); self.box.setContentsMargins(22, 18, 22, 18); self.box.setSpacing(12)
        head = QLabel(title); head.setObjectName('PageTitle'); self.box.addWidget(head)
        if intro:
            text = QLabel(intro); text.setObjectName('PageIntro'); text.setWordWrap(True); self.box.addWidget(text)
        self._stretch = False

    def add(self, widget, stretch: int = 0):
        self.box.addWidget(widget, stretch); return widget

    def finish(self):
        if not self._stretch: self.box.addStretch(1); self._stretch = True
        return self


def scrolled(widget):
    """给没有自带滚动的长页面套一层滚动（放进小标签页时用，避免撑高整页出现两层滚动条）。"""
    area = QScrollArea(); area.setWidgetResizable(True); area.setFrameShape(QFrame.Shape.NoFrame); area.setWidget(widget)
    return area


def tabs(*pairs):
    """同一页里的几块内容（例如“办事设置 / 操作日志”）用小标签切换。"""
    widget = QTabWidget(); widget.setObjectName('SubTabs'); widget.setDocumentMode(True)
    for widget_, label in pairs: widget.addTab(widget_, label)
    return widget


class SettingsNav(QWidget):
    """左侧分组导航 + 右侧页面。add_group 加分组标题，add_page 加一页（key 用来从别处直接跳过来）。"""
    page_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(0)
        self.list = QListWidget(); self.list.setObjectName('Nav'); self.list.setFixedWidth(176)
        self.stack = QStackedWidget(); self.stack.setObjectName('Stack')
        row.addWidget(self.list); row.addWidget(self.stack, 1)
        self.keys: list[str] = []          # 与 list 的行一一对应；分组标题行为空串
        self.pages: dict[str, QWidget] = {}
        self.list.currentRowChanged.connect(self._row_changed)

    def add_group(self, title: str):
        item = QListWidgetItem(title); item.setFlags(Qt.ItemFlag.NoItemFlags); item.setData(Qt.ItemDataRole.UserRole, 'group')
        font = item.font(); font.setPointSizeF(max(7.5, font.pointSizeF()-1)); font.setBold(True); item.setFont(font)
        self.list.addItem(item); self.keys.append('')

    def add_page(self, key: str, label: str, widget: QWidget, hint: str = ''):
        item = QListWidgetItem('　'+label); item.setToolTip(hint or label); self.list.addItem(item)
        self.keys.append(key); self.pages[key] = widget; self.stack.addWidget(widget)
        return widget

    def show_page(self, key: str) -> bool:
        if key not in self.pages: return False
        self.list.setCurrentRow(self.keys.index(key)); return True

    def current(self) -> str:
        row = self.list.currentRow()
        return self.keys[row] if 0 <= row < len(self.keys) else ''

    def _row_changed(self, row):
        key = self.keys[row] if 0 <= row < len(self.keys) else ''
        if not key: return
        self.stack.setCurrentWidget(self.pages[key]); self.page_changed.emit(key)
