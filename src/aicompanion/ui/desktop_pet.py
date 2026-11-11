"""常驻桌面的角色窗口：应用启动即显示，聊天面板按需从角色身上打开。

- 鲸娘等立绘角色使用原生 SpriteAvatar；用户导入的 Cubism 模型沿用 WebEngine 视图。
- 状态由 pet.state 的状态机决定，本窗口只负责显示、交互、气泡和桌面集成。
"""
from __future__ import annotations

import random
import time
from dataclasses import replace
from datetime import datetime

from PySide6.QtCore import QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QCursor, QGuiApplication, QRegion
from PySide6.QtWidgets import QApplication, QMenu, QVBoxLayout, QWidget

from aicompanion.brain.focus import focus_until, in_quiet_hours, is_focused, set_focus
from aicompanion.pet.behavior import LinePacer, greeting_key
from aicompanion.pet.state import PetStateMachine
from aicompanion.ui import desktop_integration as desktop
from aicompanion.ui.pet_bubble import ACTIVITY, BubbleStack, QuickInput
from aicompanion.ui.pet_geometry import fit_pet_geometry

DRAG_THRESHOLD = 6
SCALES = (0.7, 0.85, 1.0, 1.2, 1.45, 1.75)
DEMO_POSES = [('庆祝', 'celebrate'), ('思考', 'thinking'), ('干活', 'working'), ('等待确认', 'wait'), ('犯困', 'sleepy'), ('出错', 'error')]


class DesktopPetWindow(QWidget):
    chat_requested = Signal()
    call_requested = Signal()
    interrupt_requested = Signal()
    quit_requested = Signal()
    settings_requested = Signal()
    message_submitted = Signal(str)
    character_requested = Signal(str)
    visibility_changed = Signal(bool)
    appearance_changed = Signal()
    interacted = Signal(str)   # 摸头、戳、被甩、摔晕等互动事件，进入记忆收件箱
    agent_requested = Signal()
    agent_stop_requested = Signal()

    def __init__(self, database):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setWindowTitle('AICompanion 桌宠')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.winId()  # 在放入任何视图前创建带 alpha 的原生表面
        self.database = database
        self.view = None; self.view_kind = 'none'; self.pack = None
        self.character_name = '角色'; self.model_label = ''; self.characters = []; self.active_id = ''
        self.call_active = False; self.subtitle_text = ''; self.status_text = ''
        self._drag_offset = None; self._press_pos = None; self._dragging = False
        self._shutting_down = False; self._auto_hidden = False; self._user_hidden = False
        self.machine = PetStateMachine(time.monotonic()); self.pacer = LinePacer(); self.rng = random.Random()
        self.last_line = ''; self._mask_key = None
        self._drag_samples = []; self._pat_points = []; self._pat_last_dir = 0
        self.setMouseTracking(True)
        self._walk_timer = QTimer(self); self._walk_timer.setInterval(16); self._walk_timer.timeout.connect(self._walk_step)
        self._fall_timer = QTimer(self); self._fall_timer.setInterval(16); self._fall_timer.timeout.connect(self._fall_step)
        self._velocity = [0.0, 0.0]; self._fall_last = 0.0; self._thrown = False; self._greet_on_land = False
        self._walk_target = None; self._walk_last = 0.0; self._next_wander = time.monotonic()+self.rng.uniform(60, 150)
        # 骨骼形象：不裁剪窗口，按光标是否在角色身上切换"是否接收鼠标"（与 Coopanion 相同思路）
        self._input_transparent = None
        self._input_timer = QTimer(self); self._input_timer.setInterval(30); self._input_timer.timeout.connect(self._update_input)
        self._input_timer.start()
        self.bubble = BubbleStack(); self.quick = QuickInput(); self.quick.submitted.connect(self.message_submitted)
        self.layout_ = QVBoxLayout(self); self.layout_.setContentsMargins(0, 0, 0, 0); self.layout_.setSpacing(0)
        self.scale = float(database.get_setting('desktop_pet_scale', 1.0) or 1.0)
        self._save_timer = QTimer(self); self._save_timer.setSingleShot(True); self._save_timer.timeout.connect(self.save_geometry)
        self._state_timer = QTimer(self); self._state_timer.setInterval(100); self._state_timer.timeout.connect(self._apply_state); self._state_timer.start()
        self._life_timer = QTimer(self); self._life_timer.setInterval(2000); self._life_timer.timeout.connect(self._life_tick); self._life_timer.start()

    # ---- 视图 --------------------------------------------------------------------
    def set_view(self, view, kind, pack=None):
        """kind: 'sprites' | 'webengine' | 'placeholder'。旧视图由调用方负责回收。"""
        old = self.view
        if old is view and kind == self.view_kind: self.pack = pack; return
        if old is not None:
            self.layout_.removeWidget(old)
            if self.view_kind == 'webengine':
                try:
                    old.bridge.pointerChanged.disconnect(self._pointer); old.bridge.menuRequested.disconnect(self.show_menu)
                except (RuntimeError, TypeError):
                    pass
            elif hasattr(old, 'poseShown'):
                try: old.poseShown.disconnect(self._update_mask)
                except (RuntimeError, TypeError): pass
            old.hide()
        self.view, self.view_kind, self.pack = view, kind, pack
        view.setParent(self)
        if kind == 'webengine':
            # WebEngine 必须在第一次显示前声明透明，否则 Windows 合成时会出现黑底。
            view.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            view.set_surface(True)
            view.bridge.pointerChanged.connect(self._pointer); view.bridge.menuRequested.connect(self.show_menu)
        self.layout_.addWidget(view, 1); view.show()
        if kind == 'webengine':
            pass
        else:
            # 原生立绘由本窗口统一处理鼠标，便于区分点击、拖拽和穿透。
            view.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            view.poseShown.connect(self._update_mask)
        self._apply_size(); self._update_mask()

    def _base_size(self):
        if self.view_kind == 'rig':
            # 骨骼视口是正方形；左右已含张开手臂、尾巴和拎起摆动的余量。
            height = (self.pack.display_height if self.pack else 300)*self.scale*1.05
            return round(height), round(height)
        if self.view_kind in ('sprites', 'placeholder') and self.view is not None:
            w, h = self.view.native_size
            height = (self.pack.display_height if self.pack else 300)*self.scale
            # 左右留出摆动和转身的余量（与 SpriteAvatar 的 SIDE_PADDING、HEADROOM 一致）。
            from aicompanion.ui.sprite_view import HEADROOM, SIDE_PADDING
            return round(height*HEADROOM*w/h*(1+2*SIDE_PADDING)), round(height)
        height = (self.pack.display_height if self.pack else 420)*self.scale
        return round(height*.63), round(height)

    def _apply_size(self):
        width, height = self._base_size()
        saved = self.database.get_setting('desktop_pet_geometry')
        if self.isVisible() or not saved:
            # 以脚底为基准调整大小，角色不会"跳"到别处。
            bottom = self.y()+self.height() if self.isVisible() else None
            center = self.x()+self.width()//2 if self.isVisible() else None
            x = center-width//2 if center is not None else None
            y = bottom-height if bottom is not None else None
            geometry = (x, y, width, height) if x is not None else None
        else:
            geometry = (saved[0]+saved[2]//2-width//2, saved[1]+saved[3]-height, width, height) if isinstance(saved, list) and len(saved) == 4 else None
        self.setGeometry(*fit_pet_geometry(geometry, self._screens(), default_size=(width, height)))

    @staticmethod
    def _screens():
        return [(s.availableGeometry().x(), s.availableGeometry().y(), s.availableGeometry().width(), s.availableGeometry().height()) for s in QGuiApplication.screens()]

    def set_identity(self, name, model_label, characters, active_id):
        self.character_name, self.model_label = name, model_label
        self.characters, self.active_id = list(characters), active_id

    # ---- 状态与台词 ----------------------------------------------------------------
    def dispatch(self, event, **data):
        if event == 'tick': data.setdefault('night', in_quiet_hours(self.database, data.get('hour', datetime.now().hour)))
        effects = self.machine.dispatch(event, time.monotonic(), **data)
        for key in effects:
            if key in ('poke', 'poke_angry', 'pat', 'thrown', 'dizzy'): self.interacted.emit('poke' if key == 'poke_angry' else key)
            self.say_line(key)
        self._apply_state()
        if hasattr(self.view, "motion_event"):
            if event == "grab": self.view.motion_event("drag", active=bool(data.get("active")))
            elif event == "fall": self.view.motion_event("fall", active=bool(data.get("active")))
            elif event == "landed": self.view.motion_event("land", hard=bool(data.get("hard")))

    def say_line(self, key, force=False):
        if not self.pack or not self.database.get_setting('pet_bubble_enabled', True): return False
        hour = datetime.now().hour
        quiet = bool(self.database.get_setting('pet_quiet_mode', False)) or self._auto_hidden or is_focused(self.database)
        if not force and not self.pacer.allow(key, time.monotonic(), hour, quiet, in_quiet_hours(self.database, hour)): return False
        text = self.pack.line(key, self.rng, avoid=self.last_line)
        if not text: return False
        self.last_line = text
        # 正在显示回复字幕时不用台词覆盖。
        if self.subtitle_text and self.bubble.isVisible() and key not in ('poke', 'poke_angry', 'grabbed', 'wake'): return False
        from aicompanion.expression.service import ExpressionService
        result=ExpressionService.local_result(text,self.active_id if hasattr(self,'active_id') else '')
        self.show_bubble(result.text, 4.5, typewriter=True)
        return True

    def show_bubble(self, text, seconds=8.0, typewriter=False, kind='line', key=None):
        if not self.database.get_setting('pet_bubble_enabled', True) or not self.isVisible(): return
        self.bubble.say(text, self._head_point(), kind, key, seconds, typewriter)

    def show_user(self, text):
        """你说的话：虚线气泡，6 秒后淡出。"""
        self.show_bubble(text, 6.0, kind='user')

    def show_activity(self, kind='thinking'):
        self.show_bubble(ACTIVITY.get(kind, ACTIVITY['thinking']), 0, kind='activity', key='activity')

    def ask(self, text, choices, details=''):
        """确认气泡（干活前的审批、"要我动手吗"）。返回气泡对象，连接它的 chosen 信号拿结果。"""
        from aicompanion.ui.pet_bubble import ConfirmBubble
        self.close_confirm()
        bubble = ConfirmBubble(text, choices, details); self.confirm = bubble
        bubble.chosen.connect(lambda value, b=bubble: setattr(self, 'confirm', None) if getattr(self, 'confirm', None) is b else None)
        bubble.show_at(self._head_point())
        self.bubble.release('reply', 0.1)
        return bubble

    def close_confirm(self):
        if getattr(self, 'confirm', None) is not None:
            confirm, self.confirm = self.confirm, None
            if not confirm.answered: confirm.answered = True; confirm.chosen.emit('timeout')
            confirm.close()

    def hide_activity(self):
        self.bubble.release('activity', 0.15)

    def finish_reply(self):
        """一轮回复结束：回复气泡停留片刻后淡出，下一轮开新气泡。"""
        self.bubble.release('reply', 6.0)

    def perform(self, cue):
        """执行台词里的表情 / 动作标注。"""
        view = self.view
        if view is None: return
        if hasattr(view, 'perform'): view.perform(cue.kind, cue.name); return
        if self.view_kind == 'webengine':
            if cue.kind == 'motion' and hasattr(view, 'play_motion'): view.play_motion(cue.name)
            elif cue.kind == 'expression' and hasattr(view, 'set_state'): view.set_state(cue.name)
            return
        # 整张立绘：用相近姿势代替
        if cue.kind == 'motion':
            if cue.name == 'jump' and hasattr(view, 'hop'): view.hop(0.06, 0.42)
            elif cue.name in ('spin',) and hasattr(view, 'face'): view.face(-1); QTimer.singleShot(600, lambda: view.face(1))
            elif hasattr(view, 'bounce'): view.bounce(0.8)
        else:
            pose = {'happy': 'patted', 'love': 'patted', 'smug': 'patted', 'shy': 'patted', 'surprised': 'poked',
                    'sleep': 'sleepy', 'dizzy': 'error', 'drag': 'grabbed'}.get(cue.name)
            if pose: self.dispatch('force_pose', pose=pose, seconds=1.8)

    def _head_point(self):
        if self.view is not None and hasattr(self.view, 'content_rect'):
            rect = self.view.content_rect()
            return self.mapToGlobal(QPoint(round(rect.center().x()), round(rect.top()+rect.height()*0.04)))
        return self.mapToGlobal(QPoint(self.width()//2, 10))

    def _apply_state(self):
        if self.view_kind not in ('sprites', 'rig') or self.view is None: return
        self.view.set_pose(self.machine.current(time.monotonic()))
        if hasattr(self.view, 'set_gaze') and self.isVisible():
            # 眼神跟着鼠标：离得近才看，太远就自己东张西望。
            head = self._head_point(); cursor = QCursor.pos()
            dx, dy = cursor.x()-head.x(), cursor.y()-head.y()-30
            if abs(dx) < 700 and abs(dy) < 500: self.view.set_gaze(dx/260, dy/200)
            else: self.view.set_gaze(0, 0)
        # 跳跃、拖动、转身时遮罩放宽为整窗，结束后再收回到角色轮廓。
        key = self.view.mask_key() if hasattr(self.view, 'mask_key') else None
        if key != self._mask_key: self._mask_key = key; self._update_mask()

    def _life_tick(self):
        idle = desktop.system_idle_seconds()
        hour = datetime.now().hour
        self.dispatch('tick', idle_seconds=idle, hour=hour)
        back = self.pacer.system_idle(idle)
        if back: self.dispatch('activity'); self.say_line(back)
        if self.machine.snapshot.sleepy and self.machine.shown == 'sleepy': self.say_line('sleepy')
        self._guard_desktop()
        self._maybe_wander()

    # ---- 走动：闲着时偶尔沿屏幕底边走几步 ---------------------------------------------
    def _maybe_wander(self):
        if not hasattr(self.view, 'set_walking') or self._walk_target is not None or not self.isVisible(): return
        if not self.database.get_setting('pet_wander', True) or self.database.get_setting('pet_quiet_mode', False) or is_focused(self.database): return
        now = time.monotonic()
        if now < self._next_wander: return
        self._next_wander = now+self.rng.uniform(90, 240)
        if self.machine.shown != 'idle' or self._press_pos is not None: return
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is None: return
        distance = self.rng.choice([-1, 1])*self.rng.uniform(80, 260)
        target = max(screen.left(), min(self.x()+distance, screen.right()-self.width()))
        if abs(target-self.x()) < 40: return
        self.walk_to(target)

    def walk_to(self, x):
        self._walk_target = int(x); self._walk_last = time.monotonic()
        self.view.set_walking(1 if x > self.x() else -1); self._walk_timer.start()

    def stop_walking(self):
        if self._walk_target is None: return
        self._walk_target = None; self._walk_timer.stop()
        if hasattr(self.view, 'set_walking'): self.view.set_walking(0)
        self.save_geometry()

    def _walk_step(self):
        if self._walk_target is None or self._press_pos is not None or self._fall_timer.isActive() or self.machine.shown not in ('idle', 'talking'):
            self.stop_walking(); return
        now = time.monotonic(); dt = min(0.05, now-self._walk_last); self._walk_last = now
        step = 70*dt*self.scale   # 每秒约 70 像素
        remaining = self._walk_target-self.x()
        previous_x = self.x()
        if abs(remaining) <= step:
            self.move(self._walk_target, self.y())
            if hasattr(self.view, "advance_motion"): self.view.advance_motion(self.x()-previous_x, dt)
            self.stop_walking(); return
        self.move(self.x()+round(step if remaining > 0 else -step), self.y())
        if hasattr(self.view, "advance_motion"): self.view.advance_motion(self.x()-previous_x, dt)
        self.bubble.move_anchor(self._head_point())

    def set_focus(self, minutes):
        """专注模式：不主动说话、不走动、不主动搭话；你找她时照常回应。"""
        set_focus(self.database, minutes); self.stop_walking()
        self.say_line('focus_on' if minutes else 'focus_off', force=True)

    def greet(self):
        self.say_line(greeting_key(datetime.now().hour))

    # ---- 对外接口（兼容旧桌宠）-----------------------------------------------------
    def set_subtitle(self, text):
        self.subtitle_text = text[-220:]
        # 一轮回复始终更新同一个气泡（key='reply'），结束时由 finish_reply 释放
        if text: self.show_bubble(text, 12.0, kind='reply', key='reply')

    def set_status(self, text):
        self.status_text = text

    def set_call_active(self, active):
        self.call_active = bool(active)

    # ---- 鼠标：点击=戳，拖动=拎起，双击=快捷输入，右键=菜单 -------------------------
    def _hit(self, pos):
        return self.view is None or not hasattr(self.view, 'hit') or self.view.hit(pos)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._hit(event.position().toPoint()):
            self.stop_walking()
            self._press_pos = event.globalPosition().toPoint(); self._drag_offset = QCursor.pos()-self.pos(); self._dragging = False
            self._drag_samples = [(time.monotonic(), QCursor.pos().x(), QCursor.pos().y())]
            self._stop_fall()
            event.accept(); return
        event.ignore()

    def mouseMoveEvent(self, event):
        if self._press_pos is None:
            self._track_pat(event.position().toPoint()); return
        if not self._dragging and (event.globalPosition().toPoint()-self._press_pos).manhattanLength() > DRAG_THRESHOLD:
            self._dragging = True; self.dispatch('grab', active=True)
        if self._dragging:
            self.move(QCursor.pos()-self._drag_offset)
            self.bubble.move_anchor(self._head_point())
            # 用最近 0.12 秒的水平速度驱动钟摆摆动。
            now = time.monotonic(); self._drag_samples.append((now, QCursor.pos().x(), QCursor.pos().y()))
            self._drag_samples = [s for s in self._drag_samples if now-s[0] < 0.12]
            if hasattr(self.view, 'set_drag'): self.view.set_drag(True, self._drag_velocity()[0])

    def mouseReleaseEvent(self, event):
        if self._press_pos is None: return
        if self._dragging:
            if hasattr(self.view, 'set_drag'): self.view.set_drag(False)
            if self.database.get_setting('pet_gravity', True):
                # 松手：带着拖动速度飞出去，受重力落回屏幕底边
                vx, vy = self._drag_velocity()
                self._press_pos = None; self._drag_offset = None; self._dragging = False
                self.start_fall(vx, vy); return
            self.dispatch('grab', active=False); self.constrain_geometry()
        else:
            if hasattr(self.view, 'bounce'): self.view.bounce(1.1)
            self.dispatch('poke')
        self._press_pos = None; self._drag_offset = None; self._dragging = False

    def _drag_velocity(self):
        if len(self._drag_samples) < 2: return 0.0, 0.0
        t0, x0, y0 = self._drag_samples[0]; t1, x1, y1 = self._drag_samples[-1]
        dt = max(0.016, t1-t0)
        return (x1-x0)/dt, (y1-y0)/dt

    # ---- 重力：甩出、下落、落地、摔晕 --------------------------------------------------
    GRAVITY = 2600.0        # 像素/秒²
    HARD_LANDING = 1500.0   # 落地速度超过它就摔晕
    MAX_SPEED = 3200.0

    def _ground_y(self):
        screen = self.screen().availableGeometry() if self.screen() else None
        return screen.bottom()-self.height()+1 if screen else self.y()

    def start_fall(self, vx=0.0, vy=0.0):
        self.stop_walking()
        speed = (vx*vx+vy*vy)**0.5
        if speed > self.MAX_SPEED: vx, vy = vx*self.MAX_SPEED/speed, vy*self.MAX_SPEED/speed
        self._velocity = [vx, vy]; self._fall_last = time.monotonic(); self._thrown = speed > 700
        if self.y() >= self._ground_y()-2 and vy <= 50 and abs(vx) < 200:
            # 本来就在地上：直接落地
            self.dispatch('fall', active=True); self.dispatch('landed', hard=False); self.save_geometry(); return
        if abs(vx) > 200 and hasattr(self.view, 'face'): self.view.face(1 if vx > 0 else -1)
        self.dispatch('fall', active=True, thrown=self._thrown)
        self._fall_timer.start()

    def _stop_fall(self):
        if self._fall_timer.isActive():
            self._fall_timer.stop(); self.machine.snapshot = replace(self.machine.snapshot, falling=False)

    def _fall_step(self):
        now = time.monotonic(); dt = min(0.04, now-self._fall_last); self._fall_last = now
        vx, vy = self._velocity
        vy += self.GRAVITY*dt; vx *= 0.995
        x, y = self.x()+vx*dt, self.y()+vy*dt
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen:
            # 撞到屏幕左右边缘弹回来
            if x < screen.left(): x, vx = screen.left(), -vx*0.45
            if x > screen.right()-self.width()+1: x, vx = screen.right()-self.width()+1, -vx*0.45
        ground = self._ground_y()
        if y >= ground:
            self.move(round(x), ground); self._fall_timer.stop(); self._velocity = [0.0, 0.0]
            hard = vy > self.HARD_LANDING
            if hasattr(self.view, 'bounce'): self.view.bounce(min(1.8, 0.6+vy/1200))
            self.dispatch('landed', hard=hard); self.save_geometry()
            if self._greet_on_land: self._greet_on_land = False; QTimer.singleShot(700, self.greet)
            return
        self._velocity = [vx, vy]
        self.move(round(x), round(y)); self.bubble.move_anchor(self._head_point())

    def drop_in(self):
        """开场：从屏幕顶部掉下来，落地后打招呼。关闭重力时直接打招呼。"""
        screen = self.screen().availableGeometry() if self.screen() else None
        if not self.database.get_setting('pet_gravity', True) or screen is None:
            self.greet(); return
        self._greet_on_land = True
        self.move(self.x(), screen.top()-int(self.height()*0.6))
        self.start_fall(0.0, 0.0)

    def _track_pat(self, pos):
        """鼠标在头上来回划（不按键）算摸头：1.5 秒内方向反转 4 次以上。"""
        if not hasattr(self.view, 'head_region') or not self.view.head_region(pos):
            self._pat_points.clear(); self._pat_last_dir = 0; return
        now = time.monotonic(); self._pat_points = [p for p in self._pat_points if now-p[0] < 1.5]
        if self._pat_points:
            dx = pos.x()-self._pat_points[-1][1]
            if abs(dx) >= 3:
                direction = 1 if dx > 0 else -1
                if self._pat_last_dir and direction != self._pat_last_dir: self._pat_points.append((now, pos.x(), 'turn'))
                self._pat_last_dir = direction
        self._pat_points.append((now, pos.x(), ''))
        if sum(1 for p in self._pat_points if p[2] == 'turn') >= 4:
            self._pat_points.clear(); self._pat_last_dir = 0; self.dispatch('pat')

    def mouseDoubleClickEvent(self, event):
        if self._hit(event.position().toPoint()): self.open_quick_input()

    def contextMenuEvent(self, event):
        if self._hit(event.pos()): self.show_menu()

    def open_quick_input(self):
        rect = self.geometry()
        self.quick.open_at(QPoint(rect.center().x(), rect.bottom()-30))

    def _pointer(self, kind):
        # WebEngine 视图通过 WebChannel 传递拖动阶段；使用 Qt 屏幕坐标处理多屏 DPI。
        if kind == 'press': self._drag_offset = QCursor.pos()-self.pos()
        elif kind == 'move' and self._drag_offset is not None:
            self.move(QCursor.pos()-self._drag_offset)
        elif kind == 'release':
            self._drag_offset = None; self.constrain_geometry()

    # ---- 几何、遮罩与桌面集成 ---------------------------------------------------------
    def constrain_geometry(self):
        rect = self.geometry()
        self.setGeometry(*fit_pet_geometry((rect.x(), rect.y(), rect.width(), rect.height()), self._screens()))
        self.bubble.move_anchor(self._head_point())

    def set_scale(self, scale):
        self.scale = scale; self.database.set_setting('desktop_pet_scale', scale); self._apply_size(); self._update_mask()

    def _update_input(self):
        if self.view_kind != 'rig' or self.view is None or not self.isVisible() or self.windowHandle() is None:
            if self._input_transparent: self._set_input_transparent(False)
            return
        # 按住、拖动、菜单打开时保持接收鼠标；否则光标在角色身上才接收
        if getattr(self, 'input_locked', False):   # 自检期间完全不接收鼠标，避免手动操作干扰结果
            self._set_input_transparent(True); return
        busy = self._press_pos is not None or self._dragging or QApplication.activePopupWidget() is not None
        self._set_input_transparent(not (busy or self.view.cursor_hit))

    def _set_input_transparent(self, transparent):
        if transparent == self._input_transparent: return
        self._input_transparent = transparent
        # 只改原生窗口的输入标志（WS_EX_TRANSPARENT），不重建窗口、不影响绘制
        self.windowHandle().setFlag(Qt.WindowType.WindowTransparentForInput, transparent)

    def _update_mask(self, *args):
        if self.view_kind == 'rig':
            self.clearMask(); return
        if self.view_kind in ('sprites', 'placeholder') and self.database.get_setting('pet_click_through', True) and self.view is not None:
            region = self.view.mask_region()
            self.setMask(region.translated(self.view.pos()) if not region.isEmpty() else QRegion())
        else:
            self.clearMask()

    def _guard_desktop(self):
        if self._user_hidden or self._shutting_down: return
        avoid = self.database.get_setting('pet_hide_fullscreen', True)
        if avoid and desktop.foreground_is_fullscreen([self.winId()]):
            if self.isVisible(): self._auto_hidden = True; self.bubble.fade_out(); self.hide()
            return
        if self._auto_hidden:
            self._auto_hidden = False; self.show()
        if self.database.get_setting('pet_topmost', True): desktop.ensure_topmost(self)

    def summon(self):
        self._user_hidden = False; self._auto_hidden = False
        self.show(); self.raise_(); desktop.ensure_topmost(self)
        self.visibility_changed.emit(True); self.say_line('summon', force=True)

    def rest(self):
        self.say_line('hide', force=True)
        QTimer.singleShot(1200, self._hide_by_user)

    def _hide_by_user(self):
        self._user_hidden = True; self.bubble.fade_out(); self.quick.hide(); self.hide(); self.visibility_changed.emit(False)

    def showEvent(self, event):
        super().showEvent(event)
        desktop.remove_window_frame(self)

    def _move_confirm(self):
        if getattr(self, 'confirm', None) is not None: self.confirm.move_anchor(self._head_point())

    def moveEvent(self, event):
        super().moveEvent(event)
        # winId() 在构造早期就可能触发移动/缩放事件，此时计时器还没创建。
        if hasattr(self, '_save_timer'): self._save_timer.start(400)
        self._move_confirm()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_save_timer'): self._save_timer.start(400); QTimer.singleShot(0, self._update_mask)

    def save_geometry(self):
        rect = self.geometry()
        self.database.set_setting('desktop_pet_geometry', [rect.x(), rect.y(), rect.width(), rect.height()])

    # ---- 右键菜单 ------------------------------------------------------------------
    def show_menu(self):
        self._drag_offset = None; self._press_pos = None
        menu = QMenu(self)
        menu.addAction(f'{self.character_name}  ·  {self.model_label}' if self.model_label else self.character_name).setEnabled(False)
        if self.status_text: menu.addAction(self.status_text[:60]).setEnabled(False)
        menu.addSeparator()
        menu.addAction('说点什么…（双击角色）', self.open_quick_input)
        menu.addAction('打开聊天面板', self.chat_requested.emit)
        menu.addAction('结束通话' if self.call_active else '开启通话', self.call_requested.emit)
        menu.addAction('打断回复', self.interrupt_requested.emit)
        if getattr(self, 'agent_busy', False): menu.addAction('让她停下手里的活', self.agent_stop_requested.emit)
        else: menu.addAction('交给她做件事…', self.agent_requested.emit)
        focus = menu.addMenu('专注模式')
        until = focus_until(self.database)
        if until: focus.addAction(f'专注中，到 {until:%H:%M}').setEnabled(False)
        for label, minutes in [('25 分钟', 25), ('1 小时', 60), ('2 小时', 120)]:
            focus.addAction(label, lambda checked=False, m=minutes: self.set_focus(m))
        if until: focus.addAction('结束专注', lambda: self.set_focus(None))
        if len(self.characters) > 1:
            roles = menu.addMenu('切换角色')
            for character_id, name in self.characters:
                action = roles.addAction(name, lambda checked=False, cid=character_id: self.character_requested.emit(cid))
                action.setCheckable(True); action.setChecked(character_id == self.active_id)
        if self.view_kind in ('sprites', 'rig'):
            poses = menu.addMenu('做个动作')
            for label, pose in DEMO_POSES:
                poses.addAction(label, lambda checked=False, p=pose: self.dispatch('force_pose', pose=p, seconds=2.5))
        elif self.view_kind == 'webengine' and hasattr(self.view, 'play_motion') and self.view.bridge.path.name.endswith('.animation.json'):
            actions = menu.addMenu('做个动作')
            for label, name in [('轻轻问候', 'greet'), ('点头', 'nod'), ('轻轻摇头', 'shake'), ('歪头', 'tilt'), ('害羞低头', 'shy'), ('轻摆手', 'wave')]:
                actions.addAction(label, lambda checked=False, motion=name: self.view.play_motion(motion))
        size = menu.addMenu('大小')
        for scale in SCALES:
            action = size.addAction(f'{int(scale*100)}%', lambda checked=False, s=scale: self.set_scale(s))
            action.setCheckable(True); action.setChecked(abs(scale-self.scale) < .01)
        options = menu.addMenu('桌宠设置')
        self._toggle(options, '始终置顶', 'pet_topmost', True, self._topmost_changed)
        self._toggle(options, '全屏应用时自动隐藏', 'pet_hide_fullscreen', True)
        self._toggle(options, '显示对话气泡', 'pet_bubble_enabled', True, lambda on: on or self.bubble.fade_out())
        self._toggle(options, '只在角色身上响应鼠标', 'pet_click_through', True, lambda on: self._update_mask())
        self._toggle(options, '勿扰（不主动说话）', 'pet_quiet_mode', False)
        if desktop.IS_WINDOWS:
            autostart = options.addAction('开机自动启动'); autostart.setCheckable(True); autostart.setChecked(desktop.autostart_enabled())
            autostart.toggled.connect(desktop.set_autostart)
        if self.pack is not None and self.pack.renderer.get('rig'):
            looks = menu.addMenu('形象')
            rig_on = looks.addAction('分层骨骼（会动的大肥鱼）'); rig_on.setCheckable(True)
            rig_on.setChecked(bool(self.database.get_setting('rig_enabled', True)))
            rig_on.toggled.connect(lambda on: (self.database.set_setting('rig_enabled', bool(on)), self.appearance_changed.emit()))
            if self.view_kind == 'rig':
                schemes = looks.addMenu('配色')
                current = self.database.get_setting('rig_scheme', 'deepseek')
                for scheme, label in [('deepseek', 'DeepSeek 原版'), ('claude', 'Claude 赤陶'), ('chatgpt', 'ChatGPT 银白'), ('gemini', 'Gemini 四色'),
                                      ('qwen', '千问 紫'), ('kimi', 'Kimi 黑蓝'), ('minimax', 'MiniMax 玫红橙'), ('harness', 'Harness 纯黑')]:
                    action = schemes.addAction(label, lambda checked=False, sc=scheme: (self.database.set_setting('rig_scheme', sc), self.appearance_changed.emit()))
                    action.setCheckable(True); action.setChecked(scheme == current)
            self._toggle(looks, '闲着时到处走走', 'pet_wander', True, lambda on: on or self.stop_walking())
            self._toggle(looks, '松手后落到屏幕底边（重力）', 'pet_gravity', True)
        menu.addAction('设置…', self.settings_requested.emit)
        menu.addSeparator()
        menu.addAction('让她休息（隐藏到托盘）', self.rest)
        menu.addAction('退出应用', self.quit_requested.emit)
        menu.exec(QCursor.pos())

    def _toggle(self, menu, label, key, default, callback=None):
        action = QAction(label, menu); action.setCheckable(True); action.setChecked(bool(self.database.get_setting(key, default)))
        def changed(on, key=key):
            self.database.set_setting(key, bool(on))
            if callback: callback(bool(on))
        action.toggled.connect(changed); menu.addAction(action)
        return action

    def _topmost_changed(self, on):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on); self.show()

    # ---- 生命周期 ------------------------------------------------------------------
    def shutdown(self):
        self.close_confirm()
        self._shutting_down = True; self._save_timer.stop(); self._state_timer.stop(); self._life_timer.stop(); self._walk_timer.stop(); self._input_timer.stop(); self._fall_timer.stop()
        if self.isVisible(): self.save_geometry()
        self.bubble.close(); self.quick.close()
        if self.view_kind == 'webengine' and self.view is not None:
            try:
                self.view.bridge.pointerChanged.disconnect(self._pointer); self.view.bridge.menuRequested.disconnect(self.show_menu)
            except (RuntimeError, TypeError):
                pass
        self.close()

    def closeEvent(self, event):
        if self._shutting_down: event.accept()
        else: event.ignore(); self.rest()
