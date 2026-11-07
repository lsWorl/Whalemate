"""聊天面板与设置窗口的统一样式：鲸鱼蓝 + 白，圆角、留白、半透明卡片。"""

PRIMARY = '#4D6BFE'
PRIMARY_DARK = '#3B56E0'
INK = '#1F2A44'
MUTED = '#6B7590'
LINE = '#D9E1F5'
BG_TOP = '#F4F7FF'
BG_BOTTOM = '#E6EEFF'
FONT = '"Microsoft YaHei UI","Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif'

PANEL = f"""
QMainWindow {{ background: {BG_TOP}; }}
QWidget {{ font-family: {FONT}; font-size: 14px; color: {INK}; }}
QWidget#Header {{ background: rgba(255,255,255,199); border-bottom: 1px solid {LINE}; }}
QLabel#Name {{ font-size: 16px; font-weight: 600; color: {INK}; }}
QLabel#Model {{ font-size: 12px; color: {MUTED}; }}
QLabel#StatusPill {{ font-size: 12px; max-width: 220px; color: {PRIMARY}; background: rgba(77,107,254,26); border-radius: 10px; padding: 2px 10px; }}
QLabel#Subtitle {{ font-size: 13px; color: {MUTED}; padding: 2px 16px; }}
QScrollArea#ChatView, QWidget#ChatBody {{ background: transparent; border: none; }}
QFrame#HerBubble {{ background: rgba(255,255,255,235); border: 1px solid {LINE}; border-radius: 14px; border-top-left-radius: 4px; }}
QFrame#UserBubble {{ background: {PRIMARY}; border-radius: 14px; border-top-right-radius: 4px; }}
QFrame#UserBubble QLabel {{ color: white; }}
QFrame#SystemChip {{ background: rgba(255,255,255,225); border: 1px solid rgba(217,225,245,230); border-radius: 10px; }}
QLabel#ChipText {{ font-size: 12px; color: {MUTED}; }}
QLabel#BubbleText {{ font-size: 14px; line-height: 150%; }}
QWidget#InputBar {{ background: rgba(255,255,255,219); border: 1px solid {LINE}; border-radius: 22px; }}
QLineEdit#Input {{ background: transparent; border: none; padding: 8px 6px; font-size: 14px; }}
QPushButton {{ background: rgba(255,255,255,230); border: 1px solid {LINE}; border-radius: 16px; padding: 6px 14px; color: {INK}; }}
QPushButton:hover {{ background: #EEF2FF; border-color: #C3CEF5; }}
QPushButton#Send {{ background: {PRIMARY}; color: white; border: none; border-radius: 17px; min-width: 34px; min-height: 34px; padding: 0 14px; font-weight: 600; }}
QPushButton#Send:hover {{ background: {PRIMARY_DARK}; }}
QPushButton#Round {{ border-radius: 17px; min-width: 34px; min-height: 34px; padding: 0 10px; }}
QPushButton#Round:checked, QPushButton#Call:checked {{ background: rgba(77,107,254,36); border-color: {PRIMARY}; color: {PRIMARY}; }}
QPushButton#Flat {{ background: transparent; border: none; color: {MUTED}; padding: 4px 8px; }}
QPushButton#Flat:hover {{ color: {PRIMARY}; }}
QCheckBox {{ spacing: 6px; color: {MUTED}; }}
QComboBox {{ background: rgba(255,255,255,230); border: 1px solid {LINE}; border-radius: 14px; padding: 5px 12px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QProgressBar#Level {{ background: rgba(77,107,254,26); border: none; border-radius: 2px; max-height: 4px; }}
QProgressBar#Level::chunk {{ background: {PRIMARY}; border-radius: 2px; }}
QLabel#Status {{ font-size: 12px; color: {MUTED}; padding: 0 18px 6px 18px; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: rgba(77,107,254,71); border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: rgba(77,107,254,128); }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}
"""

DIALOG = f"""
QDialog {{ background: {BG_TOP}; }}
QWidget {{ font-family: {FONT}; font-size: 13px; color: {INK}; }}
QTabWidget::pane {{ border: 1px solid {LINE}; border-radius: 12px; background: rgba(255,255,255,219); top: -1px; }}
QTabBar::tab {{ background: transparent; padding: 8px 14px; margin-right: 2px; color: {MUTED}; border: none; }}
QTabBar::tab:selected {{ color: {PRIMARY}; border-bottom: 2px solid {PRIMARY}; font-weight: 600; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateTimeEdit {{ background: white; border: 1px solid {LINE}; border-radius: 8px; padding: 6px 8px; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {PRIMARY}; }}
QPushButton {{ background: white; border: 1px solid {LINE}; border-radius: 14px; padding: 6px 14px; }}
QPushButton:hover {{ background: #EEF2FF; border-color: #C3CEF5; }}
QTableWidget, QListWidget {{ background: white; border: 1px solid {LINE}; border-radius: 8px; gridline-color: #EEF1F8; selection-background-color: rgba(77,107,254,38); selection-color: {INK}; }}
QHeaderView::section {{ background: #F4F6FC; border: none; border-bottom: 1px solid {LINE}; padding: 6px; color: {MUTED}; }}
QLabel {{ color: {INK}; }}
"""
