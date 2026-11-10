"""动画素材校验与真实 Qt 解码回归；只使用合成图片和临时目录。"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QPoint,QSize,Qt
from PySide6.QtGui import QColor,QImage,QImageReader,QPainter
from PySide6.QtWidgets import QApplication
from aicompanion.characters import CharacterPackError,PACKS_DIR,choose_renderer,load_pack
from aicompanion.characters.animation_pack import load_animation_pack
from aicompanion.ui.animation_view import AnimationAvatar


def make_pack(root):
    image=QImage(128,64,QImage.Format.Format_ARGB32);image.fill(Qt.GlobalColor.transparent)
    painter=QPainter(image);painter.fillRect(16,16,32,40,QColor('red'));painter.fillRect(80,16,32,40,QColor('blue'));painter.end()
    assert image.save(str(root/'idle.webp'),'WEBP',100)
    assert image.copy(0,0,64,64).save(str(root/'poster.png'))
    data={'format':'AICompanionAnimationPack','version':1,'size':[64,64],'poster':'poster.png',
        'actions':{'idle':{'image':'idle.webp','frameWidth':64,'frameHeight':64,'columns':2,'rows':1,'frameCount':2,'fps':12}},
        'poses':{p:'idle' for p in ('idle','talking','thinking','grabbed','falling','patted','walking','wave')}}
    path=root/'animations.json';path.write_text(json.dumps(data),encoding='utf-8');return path,data


class AnimationPackTests(unittest.TestCase):
    def test_bundled_assets_are_own_original_art_and_all_states_resolve(self):
        path=PACKS_DIR/'deepseek-whale/animation-original/animations.json';pack=load_animation_pack(path)
        source=json.loads((path.parent/'SOURCE.json').read_text(encoding='utf-8'))
        self.assertEqual(source['newly_drawn_keys'],66)
        self.assertFalse(source['foreign_images_included'])
        self.assertFalse(source['foreign_audio_included'])
        self.assertEqual(set(source['sources']),set(pack.actions))
        for item in source['files']:
            self.assertEqual(hashlib.sha256((path.parent/item['file']).read_bytes()).hexdigest(),item['sha256'])
        for pose in ('idle','talking','thinking','working','wait','celebrate','sleepy','error','grabbed','poked','patted','walking','falling','dizzy'):
            self.assertIn(pack.poses[pose],pack.actions)
        project=PACKS_DIR.parents[2]
        for record in source['sources'].values():
            origin=project/record['source']
            self.assertTrue(record['source'].startswith('art/deepseek-whale/'))
            self.assertEqual(hashlib.sha256(origin.read_bytes()).hexdigest(),record['source_sha256'])
        self.assertEqual(sum(a.count for a in pack.actions.values()),1368)
        self.assertEqual(pack.actions['error'].count,72)
        self.assertEqual(pack.actions['idle'].fps,24)

    def test_frame_clock_wraps_actual_frame_count(self):
        pack=load_animation_pack(PACKS_DIR/'deepseek-whale/animation-original/animations.json')
        action=pack.actions['error']
        self.assertEqual(action.frame_at(71/24+.001),71)
        self.assertEqual(action.frame_at(72/24+.001),0)
        self.assertEqual(action.frame_at(-1),0)

    def test_default_animation_keeps_character_and_explicit_legacy_fallback(self):
        whale=load_pack(PACKS_DIR/'deepseek-whale')
        self.assertEqual(choose_renderer(whale,None)[0],'spritesheet')
        self.assertEqual(choose_renderer(whale,'sprites',animation_enabled=False)[0],'sprites')
        self.assertEqual(choose_renderer(whale,'live2d','model.model3.json')[0],'cubism')
        self.assertEqual(whale.id,'deepseek-whale')

    def test_malformed_specs_paths_and_missing_pose_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path,data=make_pack(Path(directory))
            for update in ({'blendGroup':[]},{'cycleDistance':True},{'cycleDistance':0},{'cycleDistance':float('inf')},{'frameCount':3},{'fps':True},{'frameWidth':0},{'rows':64,'columns':32,'frameWidth':1024,'frameHeight':1024},
                           {'image':'../outside.webp'},{'image':'https://example.invalid/a.webp'}):
                bad=json.loads(json.dumps(data));bad['actions']['idle'].update(update)
                path.write_text(json.dumps(bad),encoding='utf-8')
                with self.subTest(update=update),self.assertRaises(CharacterPackError):load_animation_pack(path)
            data['poses'].pop('thinking');path.write_text(json.dumps(data),encoding='utf-8')
            with self.assertRaises(CharacterPackError):load_animation_pack(path)

    def test_broken_json_never_escapes_as_unhandled_structure_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'animations.json'
            for value in ('[]','{}','null','{'):
                path.write_text(value,encoding='utf-8')
                with self.subTest(value=value),self.assertRaises(CharacterPackError):load_animation_pack(path)


class AnimationViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.path,self.data=make_pack(self.root);self.view=AnimationAvatar(self.path);self.view.resize(128,128)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.view.shutdown();self.view.close();self.view.deleteLater();self.app.processEvents();self.temp.cleanup()

    def wait_for(self,predicate):
        until=time.monotonic()+3
        while not predicate() and time.monotonic()<until:
            self.app.processEvents();time.sleep(.005)
        self.assertTrue(predicate())

    def test_async_decode_advances_real_frames_without_empty_or_opaque_background(self):
        frames=[];self.view.frameAdvanced.connect(frames.append);self.view.show()
        self.wait_for(lambda:len(set(frames))==2)
        self.assertFalse(self.view.decoder.thread() is self.app.thread())
        self.assertEqual(self.view._sheet.size(),QSize(128,64))
        self.assertFalse(self.view.hit(QPoint(1,1)));self.assertTrue(self.view.hit(QPoint(64,64)))
        self.assertEqual(self.view.grab().toImage().pixelColor(1,1).alpha(),0)

    def test_hit_test_uses_mirror_and_current_frame_alpha(self):
        self.view._frame=QImage(64,64,QImage.Format.Format_ARGB32);self.view._frame.fill(Qt.GlobalColor.transparent)
        painter=QPainter(self.view._frame);painter.fillRect(4,16,16,32,QColor('red'));painter.end()
        self.view._display=self.view._frame
        self.assertTrue(self.view.hit(QPoint(16,64)));self.assertFalse(self.view.hit(QPoint(112,64)))
        self.view.face(1)
        self.assertFalse(self.view.hit(QPoint(16,64)));self.assertTrue(self.view.hit(QPoint(112,64)))

    def test_hidden_view_releases_sheet_and_stops_timers_then_resumes(self):
        self.view.show();self.wait_for(lambda:self.view._loaded is not None)
        self.view.hide();self.assertFalse(self.view.timer.isActive());self.assertTrue(self.view._sheet.isNull())
        self.view.show();self.wait_for(lambda:self.view._loaded is not None)
        self.assertTrue(self.view.timer.isActive())
        self.view.shutdown();self.assertFalse(self.view.thread.isRunning())

    def test_drag_and_fall_override_expression_then_return_to_state(self):
        self.view.set_pose('thinking');self.view.perform('motion','wave')
        self.view.set_pose('grabbed');self.assertEqual(self.view.action,self.view.pack.poses['grabbed'])
        self.view.set_pose('falling');self.assertEqual(self.view.pose,'falling')
        self.view.set_pose('idle');self.view._cue=None;self.view._select()
        self.assertEqual(self.view.action,'idle')

    def test_decoder_failure_is_reported_instead_of_blank_success(self):
        errors=[];self.view.failed.connect(errors.append)
        (self.root/'idle.webp').write_bytes(b'not-webp');self.view.show()
        self.wait_for(lambda:bool(errors));self.assertIsNone(self.view._loaded)
        self.assertFalse(self.view._frame.isNull())

    def test_original_facing_is_preserved_without_mirroring_on_startup(self):
        self.view.shutdown();self.view.close()
        self.data['actions']['idle']['facing']='right'
        self.path.write_text(json.dumps(self.data),encoding='utf-8')
        self.view=AnimationAvatar(self.path);self.view.resize(128,128)
        point=self.view._image_point(QPoint(16,64))
        self.assertAlmostEqual(point[0],.125)

    def test_fast_state_changes_decode_only_latest_requested_action(self):
        self.view.shutdown();self.view.close()
        for pose in ('thinking','talking'):
            self.data['actions'][pose]=dict(self.data['actions']['idle'])
            self.data['poses'][pose]=pose
        self.path.write_text(json.dumps(self.data),encoding='utf-8')
        self.view=AnimationAvatar(self.path);self.view.resize(128,128)
        requests=[];self.view.decodeRequested.connect(lambda a:requests.append(a.id))
        self.view.show()
        self.view.set_pose('thinking');self.view.set_pose('talking')
        self.wait_for(lambda:self.view._loaded=='talking')
        self.assertEqual(requests,['idle','talking'])
        self.assertIsNone(self.view._loading)


if __name__=='__main__':unittest.main()
