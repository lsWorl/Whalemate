"""原生 Qt 立绘渲染：多姿势立绘 + 眨眼/口型差分 + 弹簧驱动的程序动画。

不使用 WebEngine，常驻桌面时更省内存和 CPU；透明区域可以生成鼠标穿透遮罩。

动画都由几根阻尼弹簧驱动（挤压拉伸、摆动、转身），换姿势时先"弹"一下再淡入，
被拎起时随拖动速度像钟摆一样晃，落地会压扁再回弹，跳跃是抛物线加落地缓冲。
"""
from __future__ import annotations

import math
import random
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBitmap, QColor, QFont, QImage, QPainter, QPixmap, QRegion, QTransform
from PySide6.QtWidgets import QWidget

from aicompanion.characters import load_sprite_manifest

# 缺少某个姿势时退回的替代姿势（例如以后的角色只画了基础三张）。
FALLBACK = {'working': 'thinking', 'wait': 'thinking', 'celebrate': 'talking', 'error': 'idle',
            'sleepy': 'idle', 'grabbed': 'idle', 'poked': 'idle', 'patted': 'idle', 'falling': 'grabbed', 'dizzy': 'error'}
# 没有单独立绘、借用其他姿势的特殊帧：被摸头时用待机的笑眼帧。
ALIASES = {'patted': ('idle', 'eyes_closed')}
FADE = 0.22
MASK_PADDING = 6
HEADROOM = 0.92
SIDE_PADDING = 0.16      # 左右各留的比例，摆动和转身时不被裁掉


class Spring:
    """临界附近的阻尼弹簧：x 追随 target，带一点过冲，显得有弹性。"""

    def __init__(self, stiffness, damping, value=0.0):
        self.k, self.c, self.x, self.v = stiffness, damping, value, 0.0

    def step(self, target, dt):
        # 半隐式欧拉，分几步积分避免卡顿时数值爆掉。
        steps = max(1, int(dt/0.008))
        h = dt/steps
        for _ in range(steps):
            self.v += (self.k*(target-self.x)-self.c*self.v)*h
            self.x += self.v*h
        return self.x

    def kick(self, velocity):
        self.v += velocity


class SpriteAvatar(QWidget):
    poseShown = Signal(str)
    landed = Signal()

    def __init__(self, manifest_path, parent=None, seed=None):
        super().__init__(parent)
        path = Path(manifest_path)
        self.manifest = load_sprite_manifest(path)
        self.native_size = tuple(self.manifest['size'])
        self.frames = {}
        for pose, entry in self.manifest['poses'].items():
            self.frames[pose] = {}
            for kind in ('base', 'eyes_closed', 'mouth_closed', 'eyes_closed_mouth_closed'):
                if kind in entry:
                    image = QImage(str(path.parent/entry[kind]))
                    if image.isNull(): raise ValueError(f'无法加载立绘 {entry[kind]}')
                    self.frames[pose][kind] = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self.random = random.Random(seed)
        self.start = time.monotonic(); self.now = 0.0; self.last_tick = 0.0
        self.pose = 'idle'; self.previous = None; self.pose_started = 0.0
        self.mood = 'calm'; self.mouth = 0.0; self.voice_active = False
        self.next_blink = 1.5; self.blink_until = -1.0
        self.flap_open = True; self.next_flap = 0.0
        # 弹簧：挤压拉伸、摆角、朝向（1 朝右，-1 朝左，中间值即"转身中"）。
        self.squash = Spring(260, 14); self.swing = Spring(38, 4.2); self.facing = Spring(220, 26, 1.0)
        self.facing_target = 1.0; self.drag_velocity = 0.0; self.dragging = False
        self.hop_start = -1.0; self.hop_duration = 0.0; self.hop_height = 0.0
        self.shake_until = -1.0; self._was_hopping = False
        self._cache = {}
        self.timer = QTimer(self); self.timer.setInterval(16); self.timer.timeout.connect(self._tick); self.timer.start()

    # ---- 与 AvatarView 一致的接口 -------------------------------------------------
    def set_state(self, mood='calm', mouth=0):
        self.mood = mood or 'calm'; self.mouth = max(0.0, min(1.0, float(mouth)))

    def set_surface(self, desktop):
        pass

    def set_voice_active(self, active):
        self.voice_active = bool(active)

    # ---- 动作接口（桌宠窗口调用）----------------------------------------------------
    def hop(self, height=0.06, duration=0.42):
        """原地小跳；height 为立绘高度的比例。正在跳时不叠加。"""
        if self.hopping: return
        self.hop_start, self.hop_duration, self.hop_height = self.now, duration, height
        self.squash.kick(-1.6)   # 起跳前先蹲一下

    @property
    def hopping(self):
        return 0 <= self.now-self.hop_start < self.hop_duration

    def bounce(self, strength=1.0):
        """挤压一下再回弹（换姿势、被戳、落地时用）。"""
        self.squash.kick(-2.6*strength)

    def face(self, direction):
        self.facing_target = 1.0 if direction >= 0 else -1.0

    def set_drag(self, active, velocity_x=0.0):
        was = self.dragging
        self.dragging = bool(active); self.drag_velocity = velocity_x if active else 0.0
        if was and not active: self.bounce(1.3)

    # ---- 姿势 --------------------------------------------------------------------
    def resolve(self, pose):
        if pose in ALIASES and ALIASES[pose][0] in self.frames: return pose
        seen = set()
        while pose not in self.frames and pose not in seen:
            seen.add(pose); pose = FALLBACK.get(pose, 'idle')
        return pose if pose in self.frames else 'idle'

    def _source(self, pose):
        """姿势 -> 实际使用的立绘姿势名。"""
        return ALIASES[pose][0] if pose in ALIASES else pose

    def set_pose(self, pose):
        pose = self.resolve(pose)
        if pose == self.pose: return
        self.previous = (self._source(self.pose), self._frame_kind(self.pose))
        self.pose = pose; self.pose_started = self.now
        # 换姿势时先压扁再弹起，比单纯淡入淡出更有"活过来"的感觉。
        self.bounce(0.8 if pose not in ('grabbed', 'sleepy') else 0.3)
        if pose == 'celebrate': self.hop(0.07, 0.45)
        if pose == 'error': self.shake_until = self.now+0.5
        self.poseShown.emit(pose); self.update()

    def _tick(self):
        now = time.monotonic()-self.start
        dt = min(0.05, max(0.0, now-self.now)); self.now = now
        if self.now >= self.next_blink:
            self.blink_until = self.now+0.12
            # 偶尔连眨两次，更像活物。
            self.next_blink = self.now+(0.25 if self.random.random() < .15 else 2.6+self.random.random()*3.2)
        if self.now >= self.next_flap:
            self.flap_open = not self.flap_open
            self.next_flap = self.now+(0.09+self.random.random()*0.12 if self.flap_open else 0.07+self.random.random()*0.08)
        # 庆祝时连续小跳。
        if self.pose == 'celebrate' and not self.hopping and self.now-self.pose_started < 1.6: self.hop(0.06, 0.4)
        hopping = self.hopping
        if self._was_hopping and not hopping:
            self.hop_start = -1.0; self.bounce(0.9); self.landed.emit()
        self._was_hopping = hopping
        self.squash.step(0.0, dt)
        swing_target = max(-14.0, min(14.0, -self.drag_velocity*0.012)) if self.dragging else 0.0
        self.swing.step(swing_target, dt)
        self.facing.step(self.facing_target, dt)
        self.drag_velocity *= 0.85
        self.update()

    def _frame_kind(self, pose):
        if pose in ALIASES: return ALIASES[pose][1] if ALIASES[pose][1] in self.frames[ALIASES[pose][0]] else 'base'
        frames = self.frames[pose]
        eyes = self.now < self.blink_until and pose not in ('sleepy', 'celebrate', 'poked', 'error')
        mouth_closed = False
        if pose == 'talking':
            mouth_closed = self.mouth < .12 if self.voice_active else not self.flap_open
        if eyes and mouth_closed and 'eyes_closed_mouth_closed' in frames: return 'eyes_closed_mouth_closed'
        if eyes and 'eyes_closed' in frames and (not mouth_closed or 'mouth_closed' not in frames): return 'eyes_closed'
        if mouth_closed and 'mouth_closed' in frames: return 'mouth_closed'
        return 'base'

    # ---- 绘制 --------------------------------------------------------------------
    def content_rect(self) -> QRectF:
        """立绘在控件中的位置：保持比例，水平居中，底部对齐，顶部和两侧留出动画余量。"""
        w, h = self.native_size
        usable_w = self.width()/(1+2*SIDE_PADDING)
        scale = min(usable_w/w, self.height()*HEADROOM/h) if w and h else 1
        dw, dh = w*scale, h*scale
        return QRectF((self.width()-dw)/2, self.height()-dh, dw, dh)

    def _pixmap(self, pose, kind, rect):
        ratio = self.devicePixelRatioF()
        size = (max(1, round(rect.width()*ratio)), max(1, round(rect.height()*ratio)))
        key = (pose, kind, size)
        pixmap = self._cache.get(key)
        if pixmap is None:
            if len(self._cache) > 64: self._cache.clear()
            image = self.frames[pose][kind].scaled(*size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            pixmap = QPixmap.fromImage(image); pixmap.setDevicePixelRatio(ratio)
            self._cache[key] = pixmap
        return pixmap

    def _point(self, rect, point):
        scale = rect.width()/self.native_size[0]
        return rect.x()+point[0]*scale, rect.y()+point[1]*scale

    def _transform(self, pose, rect, age):
        """程序动画：以脚底（被拎起时以后领）为支点做挤压、摆动、转身和跳跃。"""
        source = self._source(pose)
        w, h = self.native_size
        anchor = self.manifest['poses'][source].get('anchor', self.manifest.get('anchor', [w/2, h]))
        ax, ay = self._point(rect, anchor)
        t = self.now
        squash = self.squash.x
        sx, sy = 1-squash*0.55, 1+squash
        angle = 0.0; dx = dy = 0.0
        if self.hopping:
            u = (self.now-self.hop_start)/self.hop_duration
            dy = -4*self.hop_height*rect.height()*u*(1-u)
            sy *= 1+0.04*math.sin(u*math.pi); sx *= 1-0.025*math.sin(u*math.pi)
        if pose == 'sleepy':
            sy *= 1+0.018*math.sin(t*1.1); angle = 2.5*math.sin(t*0.45)
        elif pose == 'error':
            if self.now < self.shake_until: dx = math.sin(age*48)*3.0*(self.shake_until-self.now)/0.5
            sy *= 1+0.008*math.sin(t*1.6)
        elif pose == 'talking':
            sy *= 1+0.011*math.sin(t*1.7)+0.012*(self.mouth if self.voice_active else (1 if self.flap_open else 0))
            angle = 1.6*math.sin(t*1.3)
        elif pose == 'patted':
            angle = 3.0*math.sin(t*5.0); sy *= 1+0.02*math.sin(t*10)
        elif pose != 'grabbed':
            sy *= 1+0.011*math.sin(t*1.7)   # 呼吸
            sx *= 1-0.004*math.sin(t*1.7)
            angle = 1.1*math.sin(t*0.8)     # 轻轻左右晃
        sx *= self.facing.x
        transform = QTransform()
        if pose == 'grabbed' or self.dragging:
            grab = self.manifest['poses'].get('grabbed', {}).get('grab_point', anchor)
            gx, gy = self._point(rect, grab)
            if self.facing.x < 0: gx = 2*(rect.x()+rect.width()/2)-gx
            pendulum = self.swing.x+4*math.sin(t*3.4)
            transform.translate(gx, gy); transform.rotate(pendulum); transform.scale(sx, 1+squash*0.5); transform.translate(-gx, -gy)
            return transform
        transform.translate(ax+dx, ay+dy); transform.rotate(angle+self.swing.x); transform.scale(sx, sy); transform.translate(-ax, -ay)
        return transform

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        rect = self.content_rect(); age = self.now-self.pose_started
        fade = min(1.0, age/FADE)
        fade = fade*fade*(3-2*fade)   # 平滑淡入
        if self.previous and age < FADE:
            pose, kind = self.previous
            painter.save(); painter.setOpacity(1-fade); painter.setTransform(self._transform(pose, rect, age))
            painter.drawPixmap(rect.topLeft(), self._pixmap(pose, kind, rect)); painter.restore()
        elif age >= FADE:
            self.previous = None
        painter.save(); painter.setOpacity(fade if self.previous else 1.0)
        painter.setTransform(self._transform(self.pose, rect, age))
        painter.drawPixmap(rect.topLeft(), self._pixmap(self._source(self.pose), self._frame_kind(self.pose), rect))
        painter.restore(); painter.end()

    # ---- 命中与遮罩 ----------------------------------------------------------------
    def _image_point(self, point):
        rect = self.content_rect()
        if not rect.contains(QPointF(point)): return None
        scale = rect.width()/self.native_size[0]
        x = (point.x()-rect.x())/scale
        if self.facing.x < 0: x = self.native_size[0]-x
        return int(x), int((point.y()-rect.y())/scale)

    def hit(self, point) -> bool:
        """point 为控件坐标；只有点在角色本体（alpha 足够高）上才算命中。"""
        located = self._image_point(point)
        if located is None: return False
        x, y = located
        image = self.frames[self._source(self.pose)]['base']
        # 附近几像素内有角色也算命中，细小的呆毛和手指也容易点到。
        for ox, oy in ((0, 0), (4, 0), (-4, 0), (0, 4), (0, -4)):
            px, py = x+ox, y+oy
            if 0 <= px < image.width() and 0 <= py < image.height() and image.pixelColor(px, py).alpha() > 40: return True
        return False

    def head_region(self, point) -> bool:
        """是否在头部区域（立绘上方约 45%），用于判断摸头。"""
        located = self._image_point(point)
        return located is not None and located[1] < self.native_size[1]*0.45 and self.hit(point)

    def mask_key(self):
        moving = self.pose in ('celebrate', 'grabbed', 'patted') or self.dragging or self.hopping or abs(self.facing.x) < .98
        return (self.pose, moving, self.facing_target)

    def mask_region(self) -> QRegion:
        """鼠标穿透遮罩：当前姿势的不透明区域外扩若干像素，容纳呼吸等小幅动画。"""
        rect = self.content_rect()
        moving = self.pose in ('celebrate', 'grabbed', 'patted') or self.dragging or self.hopping or abs(self.facing.x) < .98
        if moving or rect.width() < 2:
            return QRegion(self.rect())
        size = (max(1, round(rect.width())), max(1, round(rect.height())))
        image = self.frames[self._source(self.pose)]['base'].scaled(*size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
        if self.facing.x < 0: image = image.mirrored(True, False)
        bitmap = QBitmap.fromImage(image.createAlphaMask())
        base = QRegion(bitmap).translated(round(rect.x()), round(rect.y()))
        region = QRegion(base)
        for dx, dy in ((MASK_PADDING, 0), (-MASK_PADDING, 0), (0, MASK_PADDING), (0, -MASK_PADDING),
                       (MASK_PADDING, MASK_PADDING), (-MASK_PADDING, -MASK_PADDING), (MASK_PADDING, -MASK_PADDING), (-MASK_PADDING, MASK_PADDING)):
            region = region.united(base.translated(dx, dy))
        return region

    def text_quad(self):
        """出错木牌上的文字区域（控件坐标），没有时返回 None。"""
        quad = self.manifest['poses'].get(self.pose, {}).get('text_quad')
        if not quad: return None
        rect = self.content_rect(); scale = rect.width()/self.native_size[0]
        xs = [rect.x()+p[0]*scale for p in quad]; ys = [rect.y()+p[1]*scale for p in quad]
        return QRect(round(min(xs)), round(min(ys)), round(max(xs)-min(xs)), round(max(ys)-min(ys)))

    def shutdown(self):
        self.timer.stop()


class PlaceholderAvatar(QWidget):
    """没有任何可用形象时的占位：一个半透明圆形剪影，不冒充任何角色。"""
    poseShown = Signal(str)
    landed = Signal()

    def __init__(self, text='未设置形象', parent=None):
        super().__init__(parent); self.text = text; self.pose = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.native_size = (240, 320); self.dragging = False

    def set_state(self, mood='calm', mouth=0): pass
    def set_surface(self, desktop): pass
    def set_voice_active(self, active): pass
    def set_pose(self, pose):
        if self.pose != pose:
            self.pose = pose
            self.poseShown.emit(pose)

    def hop(self, *args, **kwargs): pass
    def bounce(self, *args, **kwargs): pass
    def face(self, direction): pass
    def set_drag(self, active, velocity_x=0.0): pass
    def shutdown(self): pass
    def text_quad(self): return None

    @property
    def hopping(self): return False

    def content_rect(self):
        size = min(self.width(), self.height())*.7
        return QRectF((self.width()-size)/2, self.height()-size-8, size, size)

    def hit(self, point): return self.content_rect().contains(QPointF(point))
    def head_region(self, point): return False
    def mask_region(self): return QRegion(self.content_rect().toAlignedRect(), QRegion.RegionType.Ellipse)

    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.content_rect()
        painter.setBrush(QColor(60, 80, 120, 170)); painter.setPen(Qt.PenStyle.NoPen); painter.drawEllipse(rect)
        painter.setPen(QColor('white')); painter.setFont(QFont(self.font().family(), 10))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text); painter.end()
