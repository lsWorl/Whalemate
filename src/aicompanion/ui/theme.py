"""鲸鲸主题。尺寸使用 Qt 逻辑像素，由系统缩放。"""

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
QPushButton {{ background: white; border: 1px solid {LINE}; border-radius: 16px;
               min-height: 34px; padding: 0 12px; }}
QPushButton:hover, QPushButton:checked {{ background: {BUBBLE}; color: {PRIMARY}; }}
QPushButton:focus {{ border: 1px solid {PRIMARY}; }}
QPushButton#Primary, QPushButton#Send {{ background: {PRIMARY}; color: white; border: none;
    min-height: 44px; border-radius: 22px; padding: 0 20px; font-weight: 600; }}
QPushButton#Primary:hover, QPushButton#Send:hover {{ background: {PRIMARY_DARK}; }}
QPushButton#Send, QPushButton#Round {{ min-height: 36px; padding: 0; border-radius: 20px; }}
QPushButton#Round {{ background: transparent; border: none; }}
QPushButton#Round:hover, QPushButton#Round:checked {{ background: {BUBBLE}; }}
QPushButton#Link, QPushButton#Flat, QPushButton#Name {{ background: transparent; color: {PRIMARY}; border: none; }}
QPushButton#Name {{ text-align: left; color: {INK}; min-height: 22px; padding: 0; }}
QPushButton#Name::menu-indicator {{ image: none; }}
QPushButton:disabled {{ color: {MUTED}; }}
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateTimeEdit {{
    background: white; border: 1px solid {INPUT_LINE}; border-radius: 10px; padding: 6px 8px; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {PRIMARY}; }}
QScrollArea, QStackedWidget#Stack {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
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
QFrame#Segments {{ background: #F1ECE3; border-radius: 18px; }}
QPushButton#Send {{ border-radius: 16px; }}
QPushButton#Mode {{ border: none; background: transparent; color: {SECONDARY}; }}
QPushButton#Mode:checked {{ background: white; color: {PRIMARY}; }}
QWidget#Banner {{ background: #FFF1DC; border-radius: 14px; }}
QWidget#Banner QLabel {{ color: {WARNING}; font-size: 13px; }}
"""

DIALOG = COMMON + f"""
QGroupBox {{ margin-top: 20px; padding: 12px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 6px; font-weight: 600; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ padding: 8px 14px; }}
QTabBar::tab:selected {{ color: {PRIMARY}; border-bottom: 2px solid {PRIMARY}; }}
QListWidget#Nav {{ background: white; border: none; padding: 6px; outline: 0; }}
QListWidget#Nav::item {{ padding: 4px 8px; min-height: 28px; border-radius: 12px; }}
QListWidget#Nav::item:disabled {{ color: {MUTED}; min-height: 16px; }}
QListWidget#Nav::item:selected {{ background: {BUBBLE}; color: {PRIMARY}; font-weight: 600; }}
QTableWidget {{ background: white; gridline-color: {LINE}; border: 1px solid {LINE}; }}
QHeaderView::section {{ background: {BUBBLE}; border: none; padding: 4px; }}
QFrame#Soon {{ border: 1px dashed {INPUT_LINE}; border-radius: 16px; }}
QWidget#Footer {{ background: white; border-top: 1px solid {LINE}; }}
QFrame#Illustration {{ background: {BUBBLE}; }}
QFrame#OverviewBanner {{ background: {BUBBLE}; border-radius: 20px; }}
QFrame#Brand {{ background: white; }}
"""
