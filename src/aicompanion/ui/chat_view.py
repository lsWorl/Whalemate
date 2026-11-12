"""聊天面板的新外观：半透明立绘背景、圆角气泡对话、系统消息小标签。

`ChatView.append(sender, text)` 返回的句柄兼容旧的 QListWidgetItem 用法：
`handle.setText('名字: 内容')` / `handle.text()`，名字前缀会被自动去掉。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from aicompanion.ui import theme

SYSTEM_SENDERS = {'任务', '步骤', '确认', '提议', '提醒'}


class Backdrop(QWidget):
    """面板底板：浅色渐变 + 右侧半透明立绘。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pixmap: QPixmap | None = None; self.opacity = 0.22; self._scaled = None
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def set_image(self, path: str | Path | None, opacity: float = 0.22):
        self.pixmap = None; self._scaled = None; self.opacity = max(0.0, min(1.0, float(opacity)))
        if path and Path(path).exists():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull(): self.pixmap = pixmap
        self.update()

    def resizeEvent(self, event):
        self._scaled = None; super().resizeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QColor(theme.BG_TOP)); gradient.setColorAt(1, QColor(theme.BG_BOTTOM))
        painter.fillRect(self.rect(), gradient)
        if self.pixmap and self.opacity > 0:
            if self._scaled is None or self._scaled.height() != int(self.height()*1.02):
                self._scaled = self.pixmap.scaledToHeight(int(self.height()*1.02), Qt.TransformationMode.SmoothTransformation)
            x = self.width()-int(self._scaled.width()*0.86)   # 靠右，稍微出框一点更自然
            painter.setOpacity(self.opacity); painter.drawPixmap(x, self.height()-self._scaled.height(), self._scaled)
            painter.setOpacity(1.0)
        painter.end()


def avatar_from(path: str | Path | None, size: int = 44) -> QPixmap | None:
    """从全身立绘上部截一个圆形头像（脸大约在宽 43%、高 14.5% 处）。"""
    if not path or not Path(path).exists(): return None
    source = QPixmap(str(path))
    if source.isNull(): return None
    side = int(min(source.width()*0.3, source.height()*0.17))
    x = int(source.width()*0.43-side/2); y = int(source.height()*0.145-side/2)
    crop = source.copy(max(0, x), max(0, y), side, side).scaled(size*2, size*2, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    out = QPixmap(size*2, size*2); out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath(); clip.addEllipse(QRectF(0, 0, size*2, size*2)); painter.setClipPath(clip)
    painter.drawPixmap(0, 0, crop); painter.end()
    out.setDevicePixelRatio(2.0)
    return out


class Bubble(QFrame):
    def __init__(self, kind: str, text: str):
        super().__init__()
        self.kind = kind
        self.setObjectName({'user': 'UserBubble', 'her': 'HerBubble'}.get(kind, 'SystemChip'))
        layout = QVBoxLayout(self); layout.setContentsMargins(*((14, 10, 14, 10) if kind != 'system' else (10, 3, 10, 3)))
        self.label = QLabel(text); self.label.setWordWrap(True); self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.label.setObjectName('BubbleText' if kind != 'system' else 'ChipText')
        layout.addWidget(self.label)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)

    def set_attachment(self,data,mime,placement):
        from aicompanion.ui.expression_image import ExpressionImage
        if hasattr(self,'image'):self.image.stop();self.layout().removeWidget(self.image);self.image.deleteLater()
        self.image=ExpressionImage(self)
        valid=self.image.load(data,mime)
        self.layout().insertWidget(0 if placement=='before_text' else self.layout().count(),self.image)
        self.label.setVisible(bool(self.label.text()))
        return valid


class MessageHandle:
    def __init__(self, view: 'ChatView', sender: str, row: QWidget, bubble: Bubble):
        self.view = view; self.sender = sender; self.row = row; self.bubble = bubble

    def text(self) -> str:
        return f'{self.sender}: {self.bubble.label.text()}'

    def setText(self, text: str):
        prefix = f'{self.sender}: '
        if text.startswith(prefix): text = text[len(prefix):]
        elif text.startswith(f'{self.sender}:'): text = text[len(self.sender)+1:]
        self.bubble.label.setText(text); self.bubble.label.setVisible(bool(text)); self.view._limit(self.bubble); self.view.follow()

    def setAttachment(self,data,mime,placement='after_text'):
        valid=self.bubble.set_attachment(data,mime,placement);self.view.follow();return valid


class SegmentedReply:
    """她的一轮回复在聊天面板里的样子：多段话显示成几个连续气泡，表情包单独一个气泡（图在前就在最前，图在后就在最后）。
    拆分规则见 expression/segments.py；数据库里仍是一轮一条完整回复。"""
    def __init__(self, view: 'ChatView', sender: str, first: 'MessageHandle'):
        self.view = view; self.sender = sender; self.texts = [first]; self.image = None; self.content = ''

    def _new(self, before=None) -> 'MessageHandle':
        return self.view.append(self.sender, '', before=before.row if before else None)

    def set_text(self, text: str):
        from aicompanion.expression.segments import split_segments
        self.content = text
        parts = split_segments(text)
        if not parts and not self.texts: return
        parts = parts or ['']
        while len(self.texts) < len(parts): self.texts.append(self._new())
        for handle, part in zip(self.texts, parts): handle.row.setVisible(True); handle.setText(part)
        for handle in self.texts[len(parts):]: handle.setText(''); handle.row.setVisible(False)

    def set_attachment(self, data, mime, placement='after_text') -> bool:
        if self.image is None:
            first = self.texts[0] if self.texts else None
            if placement == 'before_text' and first is not None and not self.content.strip():
                # 还没开始说话：占位气泡直接变成图片气泡，后面的话另起气泡
                self.image = self.texts.pop(0)
            elif placement == 'before_text' and first is not None:
                self.image = self._new(before=first)
            else:
                self.image = self._new()
            self.image.setText('')
        self.image.row.setVisible(True)
        return self.image.setAttachment(data, mime, placement)

    def withdraw(self):
        """她还没开口就撤回了这一轮（例如主动分享生成时关掉了“自己的生活”）：占位气泡整个藏起来，不留“正在想…”。"""
        for handle in self.texts+([self.image] if self.image else []): handle.setText(''); handle.row.setVisible(False)

    def mark(self, note: str):
        """打断 / 失败时在最后一个文字气泡后面注明。"""
        # 用 isHidden 而不是 isVisible：聊天面板关着时所有行都"不可见"（桌宠 Smoke 在面板隐藏时切换角色触发过越界）
        shown = [h for h in self.texts if not h.row.isHidden()]
        if shown and self.content.strip():
            last = shown[-1]
            last.setText(last.bubble.label.text()+'\n'+note)
        else:
            if not self.texts: self.texts.append(self._new())
            self.texts[0].row.setVisible(True); self.texts[0].setText(note)


class ChatView(QScrollArea):
    def __init__(self, me: str = '你', parent=None):
        super().__init__(parent)
        self.me = me; self.her = ''
        self.setWidgetResizable(True); self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setObjectName('ChatView'); self.viewport().setAutoFillBackground(False)
        self.body = QWidget(); self.body.setObjectName('ChatBody'); self.body.setAutoFillBackground(False)
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(14, 12, 14, 12)
        self.column = QVBoxLayout()
        self.column.setSpacing(10)
        self.column.addSpacing(0)
        body_layout.addLayout(self.column)
        body_layout.addStretch(1)
        self.setWidget(self.body)
        from aicompanion.ui.icons import app_icon, portrait
        self.avatar = app_icon().pixmap(28, 28)
        self.empty = QLabel('还没有聊天记录', self.viewport())
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setObjectName('Hint')
        self.empty_art = QLabel(self.viewport())
        self.empty_art.setPixmap(portrait('sleepy', 100, 108))
        self.empty_art.adjustSize()
        self._stick = True
        self.verticalScrollBar().valueChanged.connect(lambda v: setattr(self, '_stick', v >= self.verticalScrollBar().maximum()-40))
        self.verticalScrollBar().rangeChanged.connect(lambda lo, hi: self.verticalScrollBar().setValue(hi) if self._stick else None)

    def clear(self):
        while self.column.count() > 1:
            item = self.column.takeAt(1)
            if item.widget(): item.widget().deleteLater()
        self._stick = True
        self.empty.show()
        self.empty_art.show()

    def set_avatar(self, avatar):
        self.avatar = avatar.scaled(28, 28, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        for label in self.body.findChildren(QLabel, 'ReplyAvatar'):
            label.setPixmap(self.avatar)

    def _kind(self, sender):
        if sender == self.me: return 'user'
        if sender in SYSTEM_SENDERS: return 'system'
        return 'her'

    def _row(self, sender, text):
        kind = self._kind(sender)
        if kind == 'system': text = f'{sender} · {text}' if sender != '步骤' else text
        bubble = Bubble(kind, text)
        row = QWidget(); line = QHBoxLayout(row); line.setContentsMargins(0, 0, 0, 0); line.setSpacing(0)
        if kind == 'user': line.addStretch(1); line.addWidget(bubble)
        elif kind == 'her':
            face = QLabel()
            face.setObjectName('ReplyAvatar')
            face.setPixmap(self.avatar)
            line.addWidget(face, 0, Qt.AlignmentFlag.AlignBottom)
            line.addSpacing(8)
            line.addWidget(bubble)
            line.addStretch(1)
        else: line.addStretch(1); line.addWidget(bubble); line.addStretch(1)
        self._limit(bubble)
        return row, bubble

    def _limit(self, bubble):
        """短消息按文字自然宽度显示（不被挤成两行），长消息在最大宽度处换行。"""
        width = max(220, int(self.viewport().width()*(0.86 if bubble.kind == 'system' else 0.76)))-24
        metrics = bubble.label.fontMetrics()
        natural = max((metrics.horizontalAdvance(line) for line in bubble.label.text().split('\n')), default=0)+6
        bubble.label.setMinimumWidth(min(natural, width)); bubble.label.setMaximumWidth(width)

    def append(self, sender, text, index=None, before=None, timestamp=None) -> MessageHandle:
        """index 不为空时插到历史顶部的第 index 条（加载更早记录用）；before 是某个已有的行，新行插在它前面。"""
        self.empty.hide()
        self.empty_art.hide()
        when = (datetime.fromisoformat(timestamp).replace(tzinfo=timezone.utc).astimezone()
                if timestamp else datetime.now().astimezone())
        content, bubble = self._row(sender, text)
        row = QWidget()
        box = QVBoxLayout(row)
        box.setContentsMargins(0, 0, 0, 0)
        row.when = when
        day = '今天' if when.date() == datetime.now().date() else when.strftime('%m月%d日')
        row.stamp = QLabel(f'{day} {when:%H:%M}')
        row.stamp.setObjectName('Timestamp')
        box.addWidget(row.stamp, 0, Qt.AlignmentFlag.AlignHCenter)
        box.addWidget(content)
        if before is not None and self.column.indexOf(before) >= 0: self.column.insertWidget(self.column.indexOf(before), row)
        elif index is not None: self.column.insertWidget(1+index, row)
        else: self.column.addWidget(row); self._stick = True
        pos = self.column.indexOf(row)
        for i in range(pos, min(pos + 2, self.column.count())):
            current = self.column.itemAt(i).widget()
            prev = self.column.itemAt(i - 1).widget()
            current.stamp.setVisible(prev is None or abs((current.when - prev.when).total_seconds()) >= 900)
        return MessageHandle(self, sender, row, bubble)

    def reply(self, sender, placeholder='') -> SegmentedReply:
        return SegmentedReply(self, sender, self.append(sender, placeholder))

    def follow(self):
        if self._stick: QTimer.singleShot(0, self, lambda: self.verticalScrollBar().setValue(self.verticalScrollBar().maximum()))

    def scrollToBottom(self):
        self._stick = True; self.follow()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        center = self.viewport().rect().center()
        self.empty.adjustSize()
        self.empty.move(center.x() - self.empty.width() // 2, center.y() + 46)
        self.empty_art.move(center.x() - self.empty_art.width() // 2, center.y() - 64)
        for i in range(1, self.column.count()):
            row = self.column.itemAt(i).widget()
            bubble = row.findChild(Bubble) if row else None
            if bubble: self._limit(bubble)

    def sizeHint(self):
        return QSize(420, 520)
