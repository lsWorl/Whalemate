"""唯一参考十动作的导出契约与实际播放器回退检查；仅离线素材 / 临时目录。"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from dataclasses import replace
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PIL import Image
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from aicompanion.characters import PACKS_DIR,CharacterPackError
from aicompanion.characters.animation_pack import load_animation_pack
from aicompanion.characters.animation_variants import with_reference_actions
from aicompanion.ui.animation_view import AnimationAvatar,Decoder
from test_animation_pack import make_pack
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from build_reference_motion import ACTIONS,durations,jump_offset,time_sample

ROOT=PACKS_DIR/'deepseek-whale'


class ReferenceMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_actions_preserve_ten_seconds_with_dense_walk_and_matching_endpoints(self):
        pack=load_animation_pack(ROOT/'animation-reference-v1/animations.json')
        with Image.open(pack.poster) as im:stand=im.convert('RGBA').tobytes()
        for key in ACTIONS:
            clip=pack.actions[key]
            self.assertEqual((clip.count,clip.fps),(240,24) if key.startswith('walking') else (120,12))
            with Image.open(clip.path) as sheet:
                first=sheet.crop((0,0,384,384)).convert('RGBA')
                cell=clip.cell_at(clip.count-1)
                last=sheet.crop((cell%clip.columns*384,cell//clip.columns*384,cell%clip.columns*384+384,cell//clip.columns*384+384)).convert('RGBA')
                self.assertEqual(first.tobytes(),last.tobytes(),key)
                if not key.startswith('walking'):self.assertEqual(first.tobytes(),stand,key)
                for i in range(clip.count):
                    cell=clip.cell_at(i)
                    frame=sheet.crop((cell%clip.columns*384,cell//clip.columns*384,cell%clip.columns*384+384,cell//clip.columns*384+384))
                    box=frame.getbbox()
                    self.assertGreaterEqual(min(box[0],box[1],384-box[2],384-box[3]),39,key)

    def test_timing_is_ten_seconds_and_three_distinct_small_hops(self):
        self.assertEqual(len(durations()),120);self.assertEqual(sum(durations()),10000)
        self.assertEqual(sum(durations(100)),1000)
        heights=[jump_offset(i/120*10) for i in range(121)]
        peaks=[i for i in range(1,120) if heights[i]>heights[i-1] and heights[i]>heights[i+1]]
        self.assertEqual(len(peaks),3)
        self.assertTrue(all(2<i/12<6 for i in peaks))
        self.assertEqual(jump_offset(0),0);self.assertEqual(jump_offset(10),0)
        self.assertEqual(time_sample('happy_hops',6)[0],1)

    def test_merge_keeps_original_pack_and_fits_legacy_to_new_framing(self):
        old=load_animation_pack(ROOT/'animation-original/animations.json')
        new=load_animation_pack(ROOT/'animation-reference-v1/animations.json')
        merged=with_reference_actions(old,new)
        self.assertEqual(old.actions['working'].content_scale,1)
        self.assertEqual(merged.actions['working'].content_scale,.8)
        self.assertEqual(merged.actions['working'].content_offset,(.1,.1124))
        self.assertEqual(merged.poster,new.poster)
        self.assertEqual(merged.poses['poked'],'click_wave')
        self.assertEqual(merged.actions['walking_left'].gait_cycles,7)
        self.assertNotEqual(merged.actions['walking_left'].path,merged.actions['walking_right'].path)

    def test_decoder_applies_framing_to_each_cell_without_opaque_padding(self):
        with tempfile.TemporaryDirectory() as directory:
            path,_=make_pack(Path(directory));clip=load_animation_pack(path).actions['idle']
            clip=replace(clip,content_scale=.5,content_offset=(.25,.25))
            decoder=Decoder();results=[];decoder.decoded.connect(lambda a,i,e:results.append((i,e)))
            decoder.decode(clip);image,error=results[0]
            self.assertFalse(error)
            self.assertEqual(image.pixelColor(18,32).alpha(),0)
            self.assertGreater(image.pixelColor(32,32).red(),240)
            self.assertGreater(image.pixelColor(96,32).blue(),240)

    def test_invalid_replacement_names_and_framing_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path,data=make_pack(Path(directory))
            for name in ('../private','C:/data','nested/dir',''):
                changed=dict(data,replacementPack=name);path.write_text(json.dumps(changed),encoding='utf-8')
                with self.assertRaises(CharacterPackError):load_animation_pack(path)
            for entry in ({'strideCycles':True},{'strideCycles':0},{'contentScale':True},{'contentScale':.4},{'contentScale':.9,'contentOffset':[.2,0]}):
                changed=json.loads(json.dumps(data));changed['actions']['idle'].update(entry);path.write_text(json.dumps(changed),encoding='utf-8')
                with self.assertRaises(CharacterPackError):load_animation_pack(path)

    def wait(self,predicate):
        until=time.monotonic()+5
        while not predicate() and time.monotonic()<until:self.app.processEvents();time.sleep(.005)
        self.assertTrue(predicate())

    def test_reference_mode_keeps_drawn_direction_and_costume_instead_of_mirroring(self):
        from PySide6.QtCore import QPoint
        view=AnimationAvatar(ROOT/'animation-original/animations.json');view.resize(384,384)
        try:
            view.face(-1)
            point=view._image_point(QPoint(100,200))
            self.assertAlmostEqual(point[0],100/384)
            picture=QImage(384,384,QImage.Format.Format_ARGB32_Premultiplied);picture.fill('red')
            view._display=picture;view.direction=1;view.set_walking_vector(-100,0)
            self.assertEqual(view._display,picture)
        finally:view.shutdown();view.close();view.deleteLater();self.app.processEvents()

    def test_real_player_loads_new_then_legacy_then_new_without_stale_same_id(self):
        view=AnimationAvatar(ROOT/'animation-original/animations.json');view.resize(384,384)
        try:
            view.show();self.wait(lambda:view._loaded=='idle' and not view.transitioning)
            self.assertTrue(view.using_reference);self.assertFalse(view.supports_vertical_walk)
            self.assertEqual(view.pack.actions['idle'].count,120)
            view.set_animation_variant(False);self.wait(lambda:view._loaded=='idle' and not view.transitioning)
            self.assertFalse(view.using_reference);self.assertTrue(view.supports_vertical_walk)
            self.assertEqual(view.pack.actions['idle'].count,view._original_pack.actions['idle'].count)
            view.set_animation_variant(True);self.wait(lambda:view._loaded=='idle' and not view.transitioning)
            self.assertTrue(view.using_reference);self.assertEqual(view.pack.actions['idle'].count,120)
            view.set_walking_vector(-100,0);self.assertEqual(view.action,'walking_left')
            view.plan_walking(120);view._gait_distance=120
            clip=view.pack.actions['walking_left'];cycles=view._gait_elapsed(clip)*clip.fps/clip.count*clip.gait_cycles
            self.assertAlmostEqual(cycles*2,round(cycles*2))
        finally:view.shutdown();view.close();view.deleteLater();self.app.processEvents()


if __name__=='__main__':unittest.main()
