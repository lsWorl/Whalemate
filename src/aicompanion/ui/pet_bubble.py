"""桌宠头顶的对话气泡与双击弹出的快捷输入框。

两者都是独立的小顶层窗口：不改变桌宠窗口的鼠标穿透遮罩，也不抢焦点（输入框除外）。
文字由 QLabel 自己排版，避免手工测量误差导致换行后被裁掉。
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from aicompanion.ui import desktop_integration as desktop

TOOL_FLAGS = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
              | Qt.WindowType.NoDropShadowWindowHint)
ACCENT = QColor('#2F5BEA')


def bubble_font(point_size=10.5):
    font = QFont()
    font.setFamilies(['Microsoft YaHei UI', 'Microsoft YaHei', 'PingFang SC', 'Noto Sans CJK SC', 'Sans Serif'])
    font.setPointSizeF(point_size)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


class SpeechBubble(QWidget):
    """单个气泡。kind：reply（她的回复）、line（本地台词）、user（你说的话，虚线）、activity（活动图标）。"""
    MAX_TEXT_WIDTH = 230
    MAX_LINES = 6
    TAIL = 10
    PAD_X, PAD_Y = 14, 9
    closed = Signal(object)

    def __init__(self, kind='reply'):
        super().__init__(None, TOOL_FLAGS | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.kind = kind; self.key = None; self.offset = 0; self.tail_visible = True
        self.text = ''; self.full_text = ''; self.anchor = QPoint(); self.pop = 1.0
        self.label = QLabel(self); self.label.setFont(bubble_font(11.5 if kind == 'activity' else 10.5)); self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setStyleSheet('color:%s;background:transparent;' % ('#4a5470' if kind == 'user' else '#1f2a44'))
        layout = QVBoxLayout(self); layout.setContentsMargins(self.PAD_X, self.PAD_Y, self.PAD_X, self.PAD_Y+self.TAIL)
        layout.addWidget(self.label)
        self.hide_timer = QTimer(self); self.hide_timer.setSingleShot(True); self.hide_timer.timeout.connect(self.fade_out)
        self.type_timer = QTimer(self); self.type_timer.setInterval(28); self.type_timer.timeout.connect(self._type_step)
        self.pop_anim = QVariantAnimation(self); self.pop_anim.setDuration(220)
        self.pop_anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self.pop_anim.valueChanged.connect(self._pop_changed)

    def show_text(self, text, anchor: QPoint, seconds=8.0, typewriter=False):
        """anchor 是角色头顶中点的屏幕坐标；气泡出现在它上方（再加上堆叠偏移），尾巴指向角色。"""
        text = ' '.join(str(text).split())
        if not text: self.fade_out(); return
        appearing = not self.isVisible()
        self.anchor = QPoint(anchor)
        text = self._fit(text)
        self.full_text = text
        if typewriter and appearing:
            self.text = text[:1]; self.type_timer.start()
        else:
            self.type_timer.stop(); self.text = text
        self._relayout(final_text=text)
        if appearing:
            self.setWindowOpacity(0.0); self.show(); desktop.remove_window_frame(self)
            self.pop_anim.stop(); self.pop_anim.setStartValue(0.0); self.pop_anim.setEndValue(1.0); self.pop_anim.start()
        self.raise_(); self.update()
        if seconds > 0: self.hide_timer.start(int(seconds*1000))
        else: self.hide_timer.stop()

    def show_image(self,data,mime,anchor,seconds=8.0):
        from aicompanion.ui.expression_image import ExpressionImage
        self.image_label=ExpressionImage(self,144)
        if not self.image_label.load(data,mime):self.image_label.deleteLater();return False
        self.label.hide();self.layout().addWidget(self.image_label)
        self.anchor=QPoint(anchor);self.text='';self.full_text=''
        self.resize(self.image_label.width()+2*self.PAD_X,self.image_label.height()+2*self.PAD_Y+self.TAIL)
        self._place();self.show();self.raise_();self.hide_timer.start(int(seconds*1000));return True

    def _fit(self, text):
        """最多 6 行：流式回复太长时只保留最后一段，前面用省略号。"""
        metrics = QFontMetrics(self.label.font())
        limit = metrics.lineSpacing()*self.MAX_LINES+2
        def height(t):
            return metrics.boundingRect(0, 0, self.MAX_TEXT_WIDTH, 10000, Qt.TextFlag.TextWordWrap, t).height()
        if height(text) <= limit: return text
        lo, hi = 1, len(text)
        while lo < hi:   # 二分找能放下的最长结尾
            mid = (lo+hi+1)//2
            if height('…'+text[-mid:]) <= limit: lo = mid
            else: hi = mid-1
        return '…'+text[-lo:]

    def _type_step(self):
        if len(self.text) >= len(self.full_text): self.type_timer.stop(); return
        self.text = self.full_text[:len(self.text)+1]; self.label.setText(self.text)

    def _pop_changed(self, value):
        self.pop = float(value); self.setWindowOpacity(max(0.0, min(1.0, self.pop*1.3)))
        # 文字是子控件，不随画笔缩放；气泡长到一半以上再显示文字。
        self.label.setVisible(self.pop > 0.55); self.update()

    def move_anchor(self, anchor: QPoint):
        if self.isVisible(): self.anchor = QPoint(anchor); self._place()

    def _relayout(self, final_text):
        # 先按完整文字定尺寸（打字机效果时尺寸不跳动），再显示当前进度。
        metrics = QFontMetrics(self.label.font())
        natural = metrics.horizontalAdvance(final_text)+4
        width = max(36, min(self.MAX_TEXT_WIDTH, natural))
        self.label.setText(final_text); self.label.setFixedWidth(width)
        height = self.label.heightForWidth(width)
        if height <= 0: height = metrics.lineSpacing()
        self.label.setFixedHeight(height)
        self.label.setText(self.text)
        self.resize(width+2*self.PAD_X, height+2*self.PAD_Y+self.TAIL)
        self._place()

    def _place(self):
        x = self.anchor.x()-self.width()//2; y = self.anchor.y()-self.height()-self.offset
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen:
            x = max(screen.left()+4, min(x, screen.right()-self.width()-4)); y = max(screen.top()+4, y)
        self.move(x, y); self.update()

    def fade_out(self):
        was_visible = self.isVisible()
        if hasattr(self,'image_label'):self.image_label.stop()
        self.hide_timer.stop(); self.type_timer.stop(); self.hide(); self.text = ''; self.full_text = ''
        if was_visible: self.closed.emit(self)

    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 弹出时从尾巴处放大，OutBack 曲线带一点回弹。
        scale = 0.6+0.4*self.pop
        tail_x = max(18.0, min(float(self.mapFromGlobal(self.anchor).x()), self.width()-18.0))
        painter.translate(tail_x, self.height()); painter.scale(scale, scale); painter.translate(-tail_x, -self.height())
        body = QRectF(1.5, 1.5, self.width()-3, self.height()-self.TAIL-3)
        path = QPainterPath(); path.addRoundedRect(body, 13, 13)
        if self.tail_visible and self.kind != 'activity':
            tail = QPainterPath(); tail.moveTo(tail_x-8, body.bottom()-1); tail.lineTo(tail_x, body.bottom()+self.TAIL-1); tail.lineTo(tail_x+8, body.bottom()-1); tail.closeSubpath()
            path = path.united(tail)
        if self.kind == 'user':
            pen = QPen(QColor('#8a93ad'), 1.5); pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen); painter.setBrush(QColor('#f4f6fc'))
        elif self.kind == 'activity':
            painter.setPen(QPen(QColor('#c9d2ee'), 1.2)); painter.setBrush(QColor(255, 255, 255, 235))
        else:
            painter.setPen(QPen(ACCENT, 1.8)); painter.setBrush(QColor(255, 255, 255))
        painter.drawPath(path)
        painter.end()


ACTIVITY = {'thinking': '💭 在想…', 'reading': '📖 在看…', 'searching': '🔍 在搜…', 'writing': '✍️ 在写…',
            'computer': '🖱️ 在操作电脑…', 'memory': '🧠 在整理记忆…', 'listening': '👂 在听…'}


class BubbleStack:
    """最多 3 个气泡从角色头顶往上叠，最新的在最下面、带尾巴；旧的被挤出时淡出。

    同时保留单个气泡的接口（show_text / fade_out / move_anchor / isVisible / text / label），旧代码与自检照常可用。
    """
    MAX = 3
    GAP = 4

    def __init__(self):
        self.bubbles = []; self.anchor = QPoint()

    # ---- 主要接口 ----
    def say(self, text, anchor, kind='line', key=None, seconds=8.0, typewriter=False):
        self.anchor = QPoint(anchor)
        bubble = next((b for b in self.bubbles if key and b.key == key and b.isVisible()), None)
        if bubble is None:
            bubble = SpeechBubble(kind); bubble.key = key; bubble.closed.connect(self._closed)
            self.bubbles.append(bubble)
            visible = [b for b in self.bubbles if b.isVisible() or b is bubble]
            for old in visible[:-self.MAX]: old.fade_out()
        bubble.show_text(text, self.anchor, seconds, typewriter)
        self.relayout()
        return bubble

    def say_image(self,data,mime,anchor,key=None):
        self.anchor=QPoint(anchor);bubble=SpeechBubble('reply');bubble.key=key
        bubble.closed.connect(self._closed)
        if not bubble.show_image(data,mime,self.anchor):bubble.deleteLater();return False
        self.bubbles.append(bubble)
        for old in [b for b in self.bubbles if b.isVisible()][:-self.MAX]:old.fade_out()
        self.relayout();return True

    def release(self, key, delay=1.0):
        """这个气泡不再更新（例如一轮回复结束）：delay 秒后淡出，之后同 key 会开新气泡。"""
        for bubble in self.bubbles:
            if bubble.key == key:
                bubble.key = None
                if bubble.isVisible(): bubble.hide_timer.start(int(delay*1000))

    def relayout(self):
        offset = 0; newest = True
        for bubble in reversed([b for b in self.bubbles if b.isVisible()]):
            bubble.offset = offset; bubble.tail_visible = newest; newest = False
            bubble.anchor = QPoint(self.anchor); bubble._place()
            offset += bubble.height()-bubble.TAIL+self.GAP

    def _closed(self, bubble):
        if bubble in self.bubbles: self.bubbles.remove(bubble)
        bubble.deleteLater(); self.relayout()

    # ---- 兼容单个气泡的接口 ----
    @property
    def latest(self):
        visible = [b for b in self.bubbles if b.isVisible()]
        return visible[-1] if visible else None

    def show_text(self, text, anchor, seconds=8.0, typewriter=False):
        return self.say(text, anchor, 'line', None, seconds, typewriter)

    def move_anchor(self, anchor):
        self.anchor = QPoint(anchor); self.relayout()

    def fade_out(self):
        for bubble in list(self.bubbles): bubble.fade_out()

    def close(self):
        for bubble in list(self.bubbles): bubble.close()

    def isVisible(self):
        return self.latest is not None

    @property
    def text(self):
        return self.latest.text if self.latest else ''

    @property
    def full_text(self):
        return self.latest.full_text if self.latest else ''

    @property
    def label(self):
        return self.latest.label if self.latest else SpeechBubble().label

    def grab(self):
        return self.latest.grab() if self.latest else QWidget().grab()


class QuickInput(QWidget):
    submitted = Signal(str)

    def __init__(self):
        super().__init__(None, TOOL_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        layout = QHBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(); self.edit.setFont(bubble_font(10)); self.edit.setPlaceholderText('说点什么，回车发送，Esc 取消')
        self.edit.setStyleSheet('QLineEdit{background:#ffffff;border:1.6px solid #2F5BEA;border-radius:15px;padding:7px 12px;color:#1f2a44;}')
        self.edit.returnPressed.connect(self._submit); layout.addWidget(self.edit)
        self.resize(260, 40)

    def open_at(self, point: QPoint):
        screen = self.screen().availableGeometry() if self.screen() else None
        x, y = point.x()-self.width()//2, point.y()
        if screen: x = max(screen.left()+4, min(x, screen.right()-self.width()-4)); y = min(y, screen.bottom()-self.height()-4)
        self.move(x, y); self.show(); desktop.remove_window_frame(self)
        self.raise_(); self.activateWindow(); self.edit.setFocus()

    def _submit(self):
        text = self.edit.text().strip(); self.edit.clear(); self.hide()
        if text: self.submitted.emit(text)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape: self.edit.clear(); self.hide()
        else: super().keyPressEvent(event)

    def changeEvent(self, event):
        # 点到别处就收起，不留一个悬空的输入框。
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow() and not self.edit.text(): self.hide()
        super().changeEvent(event)


class ConfirmBubble(QWidget):
    """确认气泡：角色口吻说明要做什么，下面是按钮；可展开看完整参数。点哪个按钮发 chosen(value)。"""
    chosen = Signal(str)
    MAX_TEXT_WIDTH = 270

    def __init__(self, text, choices, details=''):
        super().__init__(None, TOOL_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.anchor = QPoint(); self.answered = False
        layout = QVBoxLayout(self); layout.setContentsMargins(14, 10, 14, 12); layout.setSpacing(8)
        label = QLabel(text); label.setFont(bubble_font(10.5)); label.setWordWrap(True); label.setTextFormat(Qt.TextFormat.PlainText)
        label.setFixedWidth(self.MAX_TEXT_WIDTH); label.setStyleSheet('color:#1f2a44;background:transparent;')
        layout.addWidget(label)
        self.details = None
        if details:
            self.details = QLabel(details); self.details.setFont(bubble_font(9)); self.details.setWordWrap(True)
            self.details.setTextFormat(Qt.TextFormat.PlainText); self.details.setFixedWidth(self.MAX_TEXT_WIDTH)
            self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.details.setStyleSheet('color:#4a5470;background:#f2f4fa;border-radius:6px;padding:6px;'); self.details.hide()
            layout.addWidget(self.details)
        row = QHBoxLayout(); row.setSpacing(6)
        from PySide6.QtWidgets import QPushButton
        for label_text, value in choices:
            button = QPushButton(label_text); button.setFont(bubble_font(9.5)); button.setCursor(Qt.CursorShape.PointingHandCursor)
            primary = value in ('yes', 'go', 'once')
            button.setStyleSheet('QPushButton{padding:4px 10px;border-radius:9px;border:1px solid %s;background:%s;color:%s;}'
                                 'QPushButton:hover{background:%s;}' % (('#2F5BEA', '#2F5BEA', 'white', '#3d58e0') if primary else ('#c9d2ee', 'white', '#1f2a44', '#eef1fb')))
            button.clicked.connect(lambda checked=False, v=value: self.choose(v)); row.addWidget(button)
        if details:
            more = QPushButton('详情'); more.setFont(bubble_font(9)); more.setFlat(True); more.setCursor(Qt.CursorShape.PointingHandCursor)
            more.setStyleSheet('QPushButton{color:#2F5BEA;border:none;background:transparent;padding:4px;}')
            more.clicked.connect(self._toggle_details); row.addWidget(more)
        row.addStretch(); layout.addLayout(row)

    def _toggle_details(self):
        self.details.setVisible(not self.details.isVisible()); self.adjustSize(); self._place()

    def choose(self, value):
        if self.answered: return
        self.answered = True; self.chosen.emit(value); self.close()

    _choose = choose

    def show_at(self, anchor: QPoint):
        self.anchor = QPoint(anchor); self.adjustSize(); self._place(); self.show(); self.raise_()
        desktop.remove_window_frame(self)

    def move_anchor(self, anchor: QPoint):
        if self.isVisible(): self.anchor = QPoint(anchor); self._place()

    def _place(self):
        x = self.anchor.x()-self.width()//2; y = self.anchor.y()-self.height()-6
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen: x = max(screen.left()+4, min(x, screen.right()-self.width()-4)); y = max(screen.top()+4, y)
        self.move(x, y)

    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#F2A541'), 2.0)); painter.setBrush(QColor(255, 255, 255, 250))
        painter.drawRoundedRect(QRectF(1.5, 1.5, self.width()-3, self.height()-3), 13, 13)
        painter.end()
