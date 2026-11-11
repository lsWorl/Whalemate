"""分层网格骨骼角色视图：WebEngine + Pixi 渲染（assets/rig），接口与 SpriteAvatar 一致。

鼠标由桌宠窗口统一处理。窗口不做遮罩裁剪（裁剪会在动作变化时切掉边缘像素）：
每 30 ms 把光标位置发给网页，网页用 CPU 按网格三角形反算贴图透明度，回答"光标是否在角色身上"，
桌宠窗口据此切换"是否接收鼠标"。同一个回答里带头顶和脚底位置，用作气泡锚点。
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QCursor
from PySide6.QtGui import QColor, QRegion
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QWidget

ASSETS = Path(__file__).resolve().parents[1]/'assets'/'rig'


def parse_hitmap(raw: str) -> dict | None:
    """校验并解析网页回传的命中图；格式不对时返回 None。"""
    try:
        data = json.loads(raw)
        width, height, cell, bits = int(data['width']), int(data['height']), int(data['cell']), str(data['bits'])
        if width <= 0 or height <= 0 or cell <= 0 or len(bits) != width*height: return None
        data.update(width=width, height=height, cell=cell, bits=bits)
        return data
    except (ValueError, KeyError, TypeError):
        return None


def dilate(bits: str, width: int, height: int, radius: int = 1) -> list[bytearray]:
    rows = [bytearray(width) for _ in range(height)]
    for y in range(height):
        row = bits[y*width:(y+1)*width]
        for x, ch in enumerate(row):
            if ch == '1':
                for yy in range(max(0, y-radius), min(height, y+radius+1)):
                    target = rows[yy]
                    for xx in range(max(0, x-radius), min(width, x+radius+1)): target[xx] = 1
    return rows


def region_from_hitmap(data: dict, radius: int = 2) -> QRegion:
    """命中图 → 外扩后的遮罩区域（按行合并连续格子成矩形）。"""
    cell = data['cell']; region = QRegion()
    for y, row in enumerate(dilate(data['bits'], data['width'], data['height'], radius)):
        x = 0
        while x < len(row):
            if row[x]:
                start = x
                while x < len(row) and row[x]: x += 1
                region = region.united(QRegion(start*cell, y*cell, (x-start)*cell, cell))
            else:
                x += 1
    return region


class RigBridge(QObject):
    stateChanged = Signal(str)
    commandIssued = Signal(str)
    modelChanged = Signal(str)
    loaded = Signal(str)
    hitmapUpdated = Signal()
    probeRequested = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_url = ''; self.hit = None; self._bits = ''
        self.probe_hit = False; self.head = None; self.feet = None; self.probe_point = None

    @Slot(result=str)
    def initialModel(self):
        return self.model_url

    @Slot(str)
    def report(self, value):
        self.loaded.emit(value)

    @Slot(str)
    def probed(self, raw):
        try:
            data = json.loads(raw)
            self.probe_hit = bool(data.get('hit')); self.head = tuple(data['head']); self.feet = tuple(data['feet'])
        except (ValueError, KeyError, TypeError):
            self.probe_hit = False

    @Slot(str)
    def hitmap(self, raw):
        data = parse_hitmap(raw)
        if data is None: return
        changed = data['bits'] != self._bits
        self.hit = data; self._bits = data['bits']
        if changed: self.hitmapUpdated.emit()


class RigAvatar(QWebEngineView):
    poseShown = Signal(str)
    landed = Signal()

    def __init__(self, rig_path, parent=None):
        super().__init__(parent)
        self.rig_path = Path(rig_path)
        self.native_size = (280, 280)   # 骨骼空间视口 [-12,-8,268,272]
        self.bridge = RigBridge(self)
        self.channel = QWebChannel(self.page()); self.channel.registerObject('rig', self.bridge); self.page().setWebChannel(self.channel)
        self.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.pose = 'idle'; self.dragging = False; self._hop_pending = False; self._mask_version = 0
        self.state = {'pose': 'idle', 'mood': 'calm', 'mouth': 0, 'voice': False, 'walking': 0, 'dragging': False,
                      'dragVx': 0, 'facing': 1, 'gazeX': 0, 'gazeY': 0}
        self.bridge.model_url = QUrl.fromLocalFile(str(self.rig_path)).toString()
        self.bridge.hitmapUpdated.connect(self._hitmap_changed)
        self.loadFinished.connect(self._page_loaded)
        self.set_surface(True)
        self.load(QUrl.fromLocalFile(str(ASSETS/'rig.html')))
        self.probe_timer = QTimer(self); self.probe_timer.setInterval(30); self.probe_timer.timeout.connect(self._probe)
        self.probe_timer.start()

    def _probe(self):
        if not self.isVisible(): return
        local = self.mapFromGlobal(QCursor.pos())
        self._probe_ticks = getattr(self, '_probe_ticks', 0)+1
        if not self.rect().adjusted(-4, -4, 4, 4).contains(local):
            # 光标不在窗口范围内：不必判断命中，只每 0.3 秒刷新一次头顶位置（气泡锚点）
            self.bridge.probe_hit = False
            if self.bridge.head is not None and self._probe_ticks % 10: return
        self.bridge.probe_point = (local.x(), local.y())
        self.bridge.probeRequested.emit(float(local.x()), float(local.y()))

    @property
    def cursor_hit(self) -> bool:
        return self.bridge.probe_hit
    # ---- 外观 ----------------------------------------------------------------------
    def set_surface(self, desktop):
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAutoFillBackground(False)
        self.page().setBackgroundColor(QColor(0, 0, 0, 0))
        self.setStyleSheet('background:transparent;')

    def _page_loaded(self, ok):
        # WebEngine 的实际渲染子控件在页面加载后才出现；让它们不接收鼠标，由桌宠窗口统一处理。
        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        if not ok: self.bridge.loaded.emit('error:页面加载失败')
        self._push()

    def _push(self):
        self.bridge.stateChanged.emit(json.dumps(self.state))

    def _set(self, **values):
        changed = {k: v for k, v in values.items() if self.state.get(k) != v}
        if changed: self.state.update(changed); self._push()

    # ---- 与 SpriteAvatar 一致的接口 ---------------------------------------------------
    def set_state(self, mood='calm', mouth=0):
        self._set(mood=mood or 'calm', mouth=round(max(0.0, min(1.0, float(mouth))), 2))

    def set_voice_active(self, active):
        self._set(voice=bool(active))

    def set_pose(self, pose):
        if pose == self.pose: return
        self.pose = pose; self._set(pose=pose); self.poseShown.emit(pose)

    def hop(self, height=0.06, duration=0.42):
        self.bridge.commandIssued.emit(json.dumps({'hop': {'height': height*280, 'duration': duration}}))

    def bounce(self, strength=1.0):
        self.bridge.commandIssued.emit(json.dumps({'bounce': strength}))

    @property
    def hopping(self):
        return False

    def face(self, direction):
        self._set(facing=1 if direction >= 0 else -1)

    def set_drag(self, active, velocity_x=0.0):
        changed = self.dragging != bool(active)
        self.dragging = bool(active)
        self._set(dragging=self.dragging, dragVx=round(velocity_x if active else 0.0))
        if changed: self.motion_event("drag", active=self.dragging)

    def motion_event(self, name, **data):
        """只转发物理事件；动作时序与整帧淡入由 posepack 播放器管理。"""
        self.bridge.commandIssued.emit(json.dumps({"motion_event": {"name": name, "data": data}}))

    def advance_motion(self, distance, seconds):
        """使用窗口实际位移，缩放换算由知道视口的网页完成。"""
        self.motion_event("travel", distance=float(distance), seconds=float(seconds))

    def perform(self, kind, name):
        if kind == 'expression': self.bridge.commandIssued.emit(json.dumps({'express': name, 'seconds': 3.2}))
        elif kind == 'motion': self.bridge.commandIssued.emit(json.dumps({'motion': name}))

    def set_walking(self, direction):
        self._set(walking=int(direction))
        if direction: self.face(direction)

    def set_gaze(self, x, y):
        self._set(gazeX=round(max(-1.0, min(1.0, x)), 2), gazeY=round(max(-1.0, min(1.0, y)), 2))

    # ---- 命中图 --------------------------------------------------------------------
    def _hitmap_changed(self):
        self._mask_version += 1

    def _cell(self, point):
        hit = self.bridge.hit
        if not hit: return None
        cell = hit['cell']
        return hit, int(point.x()//cell), int(point.y()//cell)

    def hit(self, point) -> bool:
        # 按下鼠标时光标就在 point：直接用最近一次探测结果
        last = self.bridge.probe_point
        if last is not None and abs(last[0]-point.x()) <= 8 and abs(last[1]-point.y()) <= 8: return self.bridge.probe_hit
        located = self._cell(point)
        if located is None: return False
        hit, cx, cy = located
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                x, y = cx+dx, cy+dy
                if 0 <= x < hit['width'] and 0 <= y < hit['height'] and hit['bits'][y*hit['width']+x] == '1': return True
        return False

    def head_region(self, point) -> bool:
        if self.bridge.head is None or self.bridge.feet is None: return False
        head_y, feet_y = self.bridge.head[1], self.bridge.feet[1]
        return point.y() < head_y+(feet_y-head_y)*0.42 and self.hit(point)

    def content_rect(self) -> QRectF:
        if self.bridge.head is not None and self.bridge.feet is not None:
            (hx, hy), (_, fy) = self.bridge.head, self.bridge.feet
            height = max(20.0, fy-hy)
            return QRectF(hx-height*0.35, hy, height*0.7, height)
        return QRectF(self.width()*0.2, self.height()*0.1, self.width()*0.6, self.height()*0.9)

    def mask_key(self):
        return 'input-toggle'   # 不使用遮罩，桌宠窗口按 cursor_hit 切换是否接收鼠标

    def mask_region(self) -> QRegion:
        return QRegion()

    def text_quad(self):
        return None

    def shutdown(self):
        self.probe_timer.stop(); self.stop()
