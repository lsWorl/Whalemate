"""Aboutme 安装边界和整帧播放回归；使用合成图，不联网、不依赖本机贴图。"""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
AVAILABLE=importlib.util.find_spec('PIL') is not None
if AVAILABLE:
    from PIL import Image,ImageDraw
    import install_aboutme_pet as installer
from aicompanion.characters import PACKS_DIR,load_pack,choose_renderer
from aicompanion.characters.animation_pack import load_animation_pack
from aicompanion.ui.animation_view import AnimationAvatar
from PySide6.QtWidgets import QApplication
from test_animation_pack import make_pack

@unittest.skipUnless(AVAILABLE,"素材安装测试需要已有 studio/Pillow；运行桌宠不需要")
class AboutmeInstallTests(unittest.TestCase):
    def test_full_cells_keep_order_alpha_and_canvas_when_moving_range_is_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);sheet=Image.new('RGBA',(1280,720))
            draw=ImageDraw.Draw(sheet)
            for i,color in enumerate(('red','green','blue','yellow')):
                x=i%2*640;y=i//2*360
                draw.rectangle((x+100,y+50,x+400,y+320),fill=color)
            source=root/'source.webp';sheet.save(source,lossless=True)
            entry={'name':'走路','facing':'left','frames':[1,4],
                   'spritesheet':{'frameWidth':640,'frameHeight':360,'columns':2,'rows':2,'frameCount':4,'fps':12}}
            action,poster=installer.convert(source,entry,root/'out.webp',size=(128,72))
            self.assertEqual((action['frameCount'],action['fps']),(3,12))
            with Image.open(root/'out.webp') as result:
                for i,original in enumerate(range(1,4)):
                    x=original%2*640;y=original//2*360
                    expected=sheet.crop((x,y,x+640,y+360)).resize((128,72),Image.Resampling.LANCZOS)
                    actual=result.crop((i*128,0,i*128+128,72)).convert('RGBA')
                    background=Image.new('RGBA',(128,72),'black')
                    self.assertEqual(Image.alpha_composite(background,actual).tobytes(),
                                     Image.alpha_composite(background,expected).tobytes())
            self.assertEqual(poster.getpixel((0,0))[3],0)

    def test_corrupt_cache_and_missing_offline_source_never_trigger_network(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(installer.urllib.request,'urlopen') as request:
            root=Path(directory);raw=b'valid'
            entry={'key':'idle','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
            with self.assertRaises(FileNotFoundError):installer.cached_source(entry,'a'*40,root,True)
            (root/'idle.webp').write_bytes(b'wrong')
            with self.assertRaises(ValueError):installer.cached_source(entry,'a'*40,root)
            request.assert_not_called()

    def test_catalog_pins_source_integrity_and_runtime_states(self):
        data=installer.read_lock();entries={e['key']:e for e in data['actions']}
        self.assertEqual(len(entries),21)
        for key in ('idle','talking','thinking','working','grabbed','wave','milk_tea','walking'):
            self.assertIn(key,entries)
        self.assertEqual(entries['walking']['frames'],[15,95])
        self.assertEqual(entries['walking']['spritesheet']['fps'],12)
        self.assertEqual(entries['walking']['name'],'走路')
        with tempfile.TemporaryDirectory() as directory:
            data['actions'][0]['path']='../outside.webp'
            path=Path(directory)/'lock.json';path.write_text(json.dumps(data),encoding='utf-8')
            with self.assertRaises(ValueError):installer.read_lock(path)

    def test_missing_all_local_art_still_preserves_explicit_model(self):
        with tempfile.TemporaryDirectory() as directory:
            whale=replace(load_pack(PACKS_DIR/'deepseek-whale'),root=Path(directory))
            self.assertEqual(choose_renderer(whale,None),('placeholder',None))
            self.assertEqual(choose_renderer(whale,'live2d','model.model3.json')[0],'cubism')

@unittest.skipUnless(AVAILABLE,"素材安装测试需要已有 studio/Pillow；运行桌宠不需要")
class AboutmePlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_right_mirrors_source_left_and_route_does_not_retime_upstream_gait(self):
        with tempfile.TemporaryDirectory() as directory:
            path,data=make_pack(Path(directory));data['source']='aboutme'
            for key in ('walking_left','walking_right'):
                data['actions'][key]=dict(data['actions']['idle'],facing='left',cycleDistance=80.)
                data['poses'][key]=key
            data['poses']['walking']='walking_right'
            path.write_text(json.dumps(data),encoding='utf-8')
            view=AnimationAvatar(path)
            try:
                self.assertTrue(view.using_aboutme);self.assertFalse(view.supports_vertical_walk)
                view.play_action('walking_right');self.assertEqual(view.direction,1)
                view.perform('expression','neutral');view.set_walking_vector(-1,0)
                self.assertEqual(view.action,'walking_left');self.assertEqual(view.direction,-1)
                view.plan_walking(51.)
                self.assertEqual(view._gait_cycle_distance,80.)
                view._gait_distance=40.
                self.assertAlmostEqual(view._gait_elapsed(view.pack.actions['walking_left']),1/12)
            finally:view.shutdown();view.close();view.deleteLater();self.app.processEvents()
