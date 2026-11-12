"""鲸鲸主题。尺寸使用 Qt 逻辑像素，由系统缩放。"""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QRadioButton, QComboBox, QGraphicsDropShadowEffect, QGraphicsEffect, QLineEdit,
                               QPlainTextEdit, QPushButton, QSizePolicy, QStyle, QStyleOptionViewItem, QStyledItemDelegate, QTextEdit)

from aicompanion.ui.icons import ICON_DIR

PRIMARY = '#2F5BEA'
PRIMARY_DARK = '#1E3FB8'
INK = '#13234A'
MUTED = '#5A6787'
SECONDARY = '#4B587A'
LINE = '#E3E8F3'
INPUT_LINE = '#D9E1F2'
BUBBLE = '#E6EEFF'
BG_TOP = BG_BOTTOM = '#FBF8F3'
AQUA = '#3FB7C9'
SUCCESS = '#16683F'
WARNING = '#8A4A00'
DANGER = '#A3361A'
FONT = '"Microsoft YaHei UI","Microsoft YaHei","PingFang SC","Noto Sans CJK SC","Segoe UI Symbol",sans-serif'

COMMON = f"""
QWidget {{ font-family: {FONT}; font-size: 14px; color: {INK}; }}
QMainWindow, QDialog, QWidget#PageBody {{ background: {BG_TOP}; }}
QFrame#Card, QGroupBox {{ background: white; border: 1px solid {LINE}; border-radius: 16px; }}
QLabel#PageTitle {{ font-size: 24px; font-weight: bold; }}
QLabel#CardTitle, QPushButton#Name {{ font-size: 16px; font-weight: 600; }}
QLabel#PageIntro, QLabel#Hint, QLabel#Status, QLabel#Subtitle {{ color: {MUTED}; font-size: 13px; }}
QLabel#Good {{ color: {SUCCESS}; background: #E3F6EC; border-radius: 8px; padding: 4px 8px; }}
QLabel#Warn {{ color: {WARNING}; background: #FFF1DC; border-radius: 8px; padding: 4px 8px; }}
QPushButton {{ background: white; color: {PRIMARY}; border: 1.5px solid #C9D5F2;
    border-radius: 18px; min-height: 32px; padding: 0 15px; }}
QPushButton:hover {{ background: #F3F6FF; border-color: {PRIMARY}; }}
QPushButton:checked {{ background: {BUBBLE}; border-color: {PRIMARY}; }}
QPushButton:focus {{ border-color: {PRIMARY}; }}
QPushButton:disabled {{ background: white; border-color: #E3E8F3; color: #9AA8C7; }}
QPushButton#Primary, QPushButton#Send {{ background: {PRIMARY}; color: white; border: none;
    min-height: 44px; border-radius: 22px; padding: 0 20px; font-weight: 600; }}
QPushButton#Primary:hover, QPushButton#Send:hover {{ background: {PRIMARY_DARK}; }}
QPushButton#Primary:disabled, QPushButton#Send:disabled {{ background: #C9D5F2; color: white; }}
QPushButton#Send, QPushButton#Round {{ min-width: 36px; max-width: 36px; min-height: 36px;
    max-height: 36px; padding: 0; border-radius: 18px; border: none; }}
QPushButton#Round {{ background: transparent; }}
QPushButton#Round:hover, QPushButton#Round:checked {{ background: {BUBBLE}; }}
QPushButton#Link, QPushButton#Flat, QPushButton#Name, QPushButton#Danger {{
    background: transparent; color: {PRIMARY}; border: none; padding: 0 8px; min-height: 36px; }}
QPushButton#Flat {{ color: {SECONDARY}; }}
QPushButton#Danger {{ color: {DANGER}; }}
QPushButton#Danger:hover {{ background: #FBEAE5; }}
QPushButton#Name {{ text-align: left; color: {INK}; min-height: 22px; padding: 0; }}
QPushButton#Name::menu-indicator {{ image: none; }}
QLineEdit, QComboBox, QAbstractSpinBox, QPlainTextEdit, QTextEdit, QListWidget#GroupList {{
    background: white; color: {INK}; border: 1.5px solid {INPUT_LINE}; border-radius: 12px;
    padding: 0 10px; selection-background-color: {BUBBLE}; selection-color: {INK}; }}
QLineEdit, QComboBox, QAbstractSpinBox {{ min-height: 36px; }}
QPlainTextEdit, QTextEdit, QListWidget#GroupList {{ padding: 8px 10px; }}
QLineEdit:focus, QComboBox:focus, QAbstractSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {PRIMARY}; }}
QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled, QPlainTextEdit:disabled, QTextEdit:disabled {{
    background: #F4F1EC; color: #9AA8C7; }}
QLineEdit[invalid="true"], QComboBox[invalid="true"], QAbstractSpinBox[invalid="true"],
QTextEdit[invalid="true"], QPlainTextEdit[invalid="true"] {{ border-color: #C2410C; }}
QLabel#FieldError {{ color: {DANGER}; font-size: 13px; }}
QComboBox QLineEdit, QAbstractSpinBox QLineEdit {{ background: transparent; border: none; min-height: 0; padding: 0; }}
QComboBox {{ combobox-popup: 0; }}
QComboBoxPrivateContainer {{ border: none; background: transparent; }}
QComboBox, QAbstractSpinBox {{ padding-right: 26px; }}
QComboBox::drop-down {{ subcontrol-origin: border; subcontrol-position: right center;
    width: 26px; border: none; background: transparent; }}
QComboBox::down-arrow {{ image: url("{(ICON_DIR / 'chevron-down.svg').as_posix()}"); width: 15px; height: 15px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
    subcontrol-origin: padding; background: transparent; border: none; width: 22px; height: 16px; }}
QAbstractSpinBox::up-button {{ subcontrol-position: right top; margin-top: 2px; }}
QAbstractSpinBox::down-button {{ subcontrol-position: right bottom; margin-bottom: 2px; }}
QAbstractSpinBox::up-arrow {{ image: url("{(ICON_DIR / 'chevron-up.svg').as_posix()}"); width: 12px; height: 12px; }}
QAbstractSpinBox::down-arrow {{ image: url("{(ICON_DIR / 'chevron-down.svg').as_posix()}"); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: white; border: 1px solid {INPUT_LINE};
    border-radius: 12px; padding: 4px; outline: none; }}
QComboBox QAbstractItemView::item {{ min-height: 34px; padding: 0 10px; border-radius: 8px; }}
QComboBox QAbstractItemView::item:hover {{ background: #F3F6FF; }}
QComboBox QAbstractItemView::item:selected {{ background: {BUBBLE}; color: {PRIMARY_DARK}; }}
QCheckBox, QRadioButton {{ spacing: 10px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator, QListWidget#GroupList::indicator {{
    width: 14px; height: 14px; background: white; border: 1.5px solid #9AA8C7; border-radius: 5px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {PRIMARY}; }}
QCheckBox::indicator:checked, QListWidget#GroupList::indicator:checked {{
    background: {PRIMARY}; border-color: {PRIMARY}; image: url("{(ICON_DIR / 'check-white.svg').as_posix()}"); }}
QRadioButton::indicator:checked {{ width: 8px; height: 8px; border: 5px solid {PRIMARY}; border-radius: 9px; }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ background: #F4F1EC; border-color: #D9E1F2; }}
QCheckBox::indicator:checked:disabled {{ background: #C9D5F2; border-color: #C9D5F2; }}
QCheckBox#Switch::indicator {{ width: 40px; height: 24px; border: none; background: transparent; image: url("{(ICON_DIR / 'switch-off.svg').as_posix()}"); }}
QCheckBox#Switch::indicator:checked {{ image: url("{(ICON_DIR / 'switch-on.svg').as_posix()}"); }}
QFrame#RadioCard {{ background: white; border: 1.5px solid {INPUT_LINE}; border-radius: 16px; }}
QFrame#RadioCard[selected="true"] {{ border: 2px solid {PRIMARY}; }}
QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar {{ background: transparent; qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {SECONDARY}; border: none; border-bottom: 2px solid transparent;
    margin: 0; padding: 8px 14px; }}
QTabBar::tab:selected {{ color: {PRIMARY}; border-bottom-color: {PRIMARY}; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0 0 0 4px; border: none; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 4px 0 0 0; border: none; }}
QScrollBar::handle {{ background: #C9D5F2; border-radius: 4px; }}
QScrollBar::handle:vertical {{ min-height: 24px; }}
QScrollBar::handle:horizontal {{ min-width: 24px; }}
QScrollBar::handle:hover {{ background: #9AB0E8; }}
QScrollBar::add-line, QScrollBar::sub-line {{ background: transparent; border: none; height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; border: none; }}
QScrollArea, QStackedWidget#Stack {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QFrame#Segments {{ background: #F1ECE3; border-radius: 18px; }}

QPushButton#Mode {{ border: none; border-radius: 16px; background: transparent; color: {SECONDARY}; }}
QPushButton#Mode:checked {{ background: white; color: {PRIMARY}; }}
QProgressBar {{ background: {BUBBLE}; border: none; border-radius: 4px; }}
QProgressBar::chunk {{ background: {AQUA}; border-radius: 4px; }}
QMenu {{ background: white; border: 1px solid {LINE}; border-radius: 16px; padding: 8px; }}
QMenu::item {{ padding: 7px 22px 7px 10px; min-height: 22px; border-radius: 8px; }}
QMenu::item:selected {{ background: {BUBBLE}; color: {PRIMARY}; }}
QMenu::item:disabled {{ color: {MUTED}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 5px 8px; }}
"""

PANEL = COMMON + f"""
QWidget#Header, QWidget#Footer {{ background: white; border-bottom: 1px solid {LINE}; }}
QLabel#Presence {{ font-size: 12px; color: {MUTED}; }}
QFrame#HerBubble {{ background: {BUBBLE}; border: none; border-radius: 14px; border-bottom-left-radius: 6px; }}
QFrame#UserBubble {{ background: {PRIMARY}; border: none; border-radius: 14px; border-bottom-right-radius: 6px; }}
QFrame#UserBubble QLabel {{ color: white; }}
QFrame#SystemChip, QLabel#Timestamp {{ background: #F1ECE3; border-radius: 10px; }}
QLabel#ChipText, QLabel#Timestamp {{ color: {MUTED}; font-size: 12px; }}
QLabel#Timestamp {{ padding: 4px 12px; }}
QWidget#InputBar {{ background: white; border: 1px solid {INPUT_LINE}; border-radius: 22px; }}
QLineEdit#Input {{ background: transparent; border: none; }}
QWidget#Banner {{ background: #FFF1DC; border-radius: 14px; }}
QWidget#Banner QLabel {{ color: {WARNING}; font-size: 13px; }}
"""

DIALOG = COMMON + f"""
QGroupBox {{ margin-top: 20px; padding: 12px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 6px; font-weight: 600; }}
QListWidget#Nav {{ background: white; border: none; padding: 6px; outline: 0; }}
QListWidget#Nav::item {{ padding: 4px 8px; min-height: 28px; border-radius: 12px; }}
QListWidget#Nav::item:disabled {{ color: {MUTED}; min-height: 16px; }}
QListWidget#Nav::item:selected {{ background: {BUBBLE}; color: {PRIMARY}; font-weight: 600; }}
QTableWidget {{ background: white; gridline-color: {LINE}; border: 1px solid {LINE}; }}
QHeaderView::section {{ background: {BUBBLE}; border: none; padding: 4px; }}
QFrame#Soon {{ border: 1px dashed {INPUT_LINE}; border-radius: 16px; }}
QWidget#Footer {{ background: white; border-top: 1px solid {LINE}; }}
QFrame#Illustration {{ background: {BUBBLE}; }}
QLabel#Optional {{ color: {PRIMARY_DARK}; background: {BUBBLE}; padding: 2px 8px; border-radius: 10px; font-size: 12px; }}
QLabel#StepReady {{ color: {SUCCESS}; background: #E3F6EC; border-radius: 14px; }}
QLabel#StepRequired {{ color: {WARNING}; background: #FFF1DC; border-radius: 14px; }}
QLabel#StepPending {{ color: {MUTED}; background: #F1ECE3; border-radius: 14px; }}
QFrame#OverviewBanner {{ background: {BUBBLE}; border-radius: 20px; }}
QFrame#Brand {{ background: white; }}
"""

def set_state(widget, name, value):
    """Qt 不会在动态属性改变后自动重算 QSS。"""
    if widget.property(name) != value:
        widget.setProperty(name, value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()


class _ComboDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if index.row() == self.parent().currentIndex():
            option.text += '　✓'

    def paint(self, painter, option, index):
        # 弹出框的当前高亮会跟随鼠标，原先选定的项在确认新值前仍显示对勾与选中色。
        state = QStyle.StateFlag
        option = QStyleOptionViewItem(option)
        if index.row() == self.parent().currentIndex():
            option.state |= state.State_Selected
            option.state &= ~state.State_MouseOver
        elif option.state & state.State_MouseOver:
            option.state &= ~state.State_Selected
        super().paint(painter, option, index)

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(34)
        return size


class _FocusRing(QGraphicsEffect):
    def boundingRectFor(self, rect):
        return rect.adjusted(-3, -3, 3, 3)

    def draw(self, painter):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(BUBBLE))
        painter.drawRoundedRect(self.boundingRectFor(self.sourceBoundingRect()), 15, 15)
        painter.restore()
        self.drawSource(painter)


class _ControlEvents(QObject):
    def eventFilter(self, widget, event):
        if isinstance(widget, (QCheckBox, QRadioButton)):
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                if not widget.hitButton(event.position().toPoint()):
                    self.pressed = widget
                    widget.setDown(True)
                    return True
            elif event.type() == QEvent.Type.MouseButtonRelease and getattr(self, 'pressed', None) is widget:
                self.pressed = None
                widget.setDown(False)
                if widget.rect().contains(event.position().toPoint()):
                    widget.click()
                return True
            return False
        if event.type() == QEvent.Type.FocusIn and not widget.graphicsEffect():
            widget.setGraphicsEffect(_FocusRing(widget))
        elif event.type() == QEvent.Type.FocusOut and isinstance(widget.graphicsEffect(), _FocusRing):
            widget.setGraphicsEffect(None)
        return False


def prepare_controls(root):
    """补齐 QSS 无法表达的焦点外圈、按钮尺寸策略和弹出列表的样式委托。"""
    events = _ControlEvents(root)
    for field in root.findChildren(QObject):
        if not isinstance(field, (QLineEdit, QComboBox, QAbstractSpinBox, QPlainTextEdit, QTextEdit, QCheckBox, QRadioButton)):
            continue
        if isinstance(field, QLineEdit) and isinstance(field.parentWidget(), (QComboBox, QAbstractSpinBox)):
            continue
        field.installEventFilter(events)
        if isinstance(field, QComboBox):
            field.setItemDelegate(_ComboDelegate(field))
            view = field.view()
            popup = view.window()
            if popup.windowType() == Qt.WindowType.Popup:
                popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
                popup.setContentsMargins(4, 2, 4, 6)
                shadow = QGraphicsDropShadowEffect(view)
                shadow.setBlurRadius(7)
                shadow.setOffset(0, 2)
                shadow.setColor(QColor(19, 35, 74, 38))
                view.setGraphicsEffect(shadow)
    for button in root.findChildren(QPushButton):
        if button.objectName() in ('', 'Danger', 'Flat', 'Link') and not button.isCheckable():
            button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
