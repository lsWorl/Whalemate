"""二维路线与真实窗口调度；临时数据库 / 合成视图，不调用模型。"""
import math
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QPoint,Signal
from PySide6.QtWidgets import QApplication,QWidget
from aicompanion.pet.locomotion import advance_walk,clamp_target,braking_step
from aicompanion.storage.database import Database
from aicompanion.ui.desktop_pet import DesktopPetWindow


class LocomotionTests(unittest.TestCase):
    def test_diagonal_and_vertical_have_same_total_speed(self):
        for target in ((100,0),(0,-100),(100,100)):
            position,traveled,arrived=advance_walk((0.,0.),target,3.5)
            self.assertAlmostEqual(math.hypot(*position),3.5)
            self.assertEqual(traveled,3.5);self.assertFalse(arrived)
        position,distance,arrived=advance_walk((1,2),(1,2),3.5)
        self.assertEqual(position,(1,2));self.assertEqual(distance,0);self.assertTrue(arrived)

    def test_last_step_lands_exactly_without_overshoot(self):
        position=(.2,.3);target=(-3.7,8.2);distance=0
        for _ in range(100):
            position,step,arrived=advance_walk(position,target,.17);distance+=step
            if arrived:break
        self.assertTrue(arrived);self.assertEqual(position,target)
        self.assertAlmostEqual(distance,math.hypot(-3.9,7.9))
        self.assertEqual(advance_walk((0,0),(5,5),-1)[0],(0.,0.))

    def test_start_accelerates_and_near_target_brakes(self):
        distance,speed=braking_step(0,100,.016)
        self.assertGreater(distance,0);self.assertLess(distance,.1);self.assertLess(speed,42)
        distance,slower=braking_step(42,1,.05)
        self.assertLess(slower,42);self.assertLessEqual(distance,1)
        self.assertEqual(braking_step(5,.1,.05),(.1,0.))

    def test_negative_monitor_and_oversized_pet_are_clamped(self):
        self.assertEqual(clamp_target((-9999,9999),(-1920,-100,1920,1080),(300,300)),(-1920,680))
        self.assertEqual(clamp_target((0,0),(-100,-50,80,60),(300,300)),(-100,-50))


class View(QWidget):
    poseShown=Signal(str)
    native_size=(384,384)
    walking_ready=True
    cursor_hit=False
    def __init__(self):
        super().__init__();self.vectors=[];self.distance=0
    def set_pose(self,pose):pass
    def set_walking(self,direction):self.vectors.append((direction,0))
    def set_walking_vector(self,dx,dy):self.vectors.append((dx,dy))
    def step_walking(self,distance):self.distance+=distance
    def head_point(self):return QPoint(100,60)


class WindowWalkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.database=Database(Path(self.temp.name)/'test.sqlite3')
        self.database.initialize();self.database.set_setting('pet_quiet_mode',True)
        self.pet=DesktopPetWindow(self.database)
        self.pet._input_timer.stop();self.pet._life_timer.stop();self.pet._state_timer.stop()
        self.view=View();self.pet.set_view(self.view,'spritesheet')
        self.pet.show();self.pet.move(100,100);self.app.processEvents()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.pet.shutdown();self.pet.deleteLater();self.app.processEvents();self.temp.cleanup()

    def tick(self,now):
        with patch('aicompanion.ui.desktop_pet.time.monotonic',return_value=now):self.pet._walk_step()

    def test_window_reaches_diagonal_target_and_accumulates_fractional_steps(self):
        self.pet.walk_to(220,180);self.pet._walk_timer.stop();self.pet._walk_last=100.
        for i in range(1,81):
            self.tick(100+i*.05)
            if self.pet._walk_target is None:break
        self.assertEqual((self.pet.x(),self.pet.y()),(220,180))
        self.assertAlmostEqual(self.view.distance,math.hypot(120,80))
        self.assertEqual(self.view.vectors[0],(120.,80.));self.assertEqual(self.view.vectors[-1],(0,0))

    def test_reference_walk_speed_follows_full_frame_clip_duration(self):
        from types import SimpleNamespace
        self.view.using_reference=True
        self.view.pack=SimpleNamespace(actions={'walking_right':SimpleNamespace(cycle_distance=280.,fps=12,count=120)})
        self.pet.walk_to(300,100);self.pet._walk_timer.stop();self.pet._walk_last=100.
        for i in range(1,21):self.tick(100+i*.05)
        self.assertAlmostEqual(self.pet._walk_speed,28.)
        self.assertGreater(self.view.distance,25.)
        self.assertLess(self.view.distance,28.)

    def test_pending_gait_does_not_slide_and_resume_does_not_jump(self):
        self.view.walking_ready=False;self.pet.walk_to(100,200);self.pet._walk_timer.stop()
        self.pet._walk_last=100.;self.tick(101.)
        self.assertEqual((self.pet.x(),self.pet.y()),(100,100));self.assertEqual(self.view.distance,0)
        self.view.walking_ready=True;self.tick(101.016)
        self.assertGreater(self.view.distance,0);self.assertLess(self.view.distance,.1)
        for i in range(1,25):self.tick(101.016+i*.016)
        self.assertEqual(self.pet.x(),100);self.assertGreater(self.pet.y(),100)

    def test_busy_hidden_and_dragging_stop_both_axes(self):
        for reason in ('busy','hidden','drag'):
            self.pet.show();self.pet.machine.shown='idle';self.pet._press_pos=None
            self.pet.walk_to(180,180);self.pet._walk_timer.stop()
            if reason=='busy':self.pet.machine.shown='thinking'
            elif reason=='hidden':self.pet.hide()
            else:self.pet._press_pos=QPoint(0,0)
            previous=self.pet.pos();self.tick(time.monotonic()+.05)
            self.assertIsNone(self.pet._walk_target);self.assertEqual(self.pet.pos(),previous)

    def test_shutdown_saves_once_and_late_geometry_events_do_not_write(self):
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QMoveEvent,QResizeEvent
        with patch.object(self.database,'set_setting',wraps=self.database.set_setting) as saved:
            self.pet.shutdown()
            self.pet.moveEvent(QMoveEvent(QPoint(110,110),QPoint(100,100)))
            self.pet.resizeEvent(QResizeEvent(QSize(301,301),QSize(300,300)))
            self.pet.save_geometry();self.pet.shutdown();self.app.processEvents()
            self.assertEqual(saved.call_count,1)
            self.assertFalse(self.pet._save_timer.isActive())

    def test_outside_destination_is_clamped_to_current_screen(self):
        self.pet.walk_to(-99999,99999);self.pet._walk_timer.stop()
        screen=self.pet.screen().availableGeometry()
        self.assertEqual(self.pet._walk_target,(screen.left(),screen.top()+max(0,screen.height()-self.pet.height())))


if __name__=='__main__':unittest.main()
