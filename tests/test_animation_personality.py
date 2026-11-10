"""性格动作不能全部退化为同一表情；步态跟实际距离而非时间走。"""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from aicompanion.characters import PACKS_DIR,load_pack
from aicompanion.pet.script import Vocab
from aicompanion.ui.animation_view import AnimationAvatar
from test_animation_pack import make_pack


class PersonalityViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();path,data=make_pack(Path(self.temp.name))
        for key in ('patted','poked','smug','pout','shy','surprised','milk_tea','stretch','walking','walking_back'):
            data['actions'][key]=dict(data['actions']['idle']);data['poses'][key]=key
        for key,entry in data['actions'].items():
            if not key.startswith('walking'):entry['blendGroup']='standing'
        path.write_text(json.dumps(data),encoding='utf-8')
        self.view=AnimationAvatar(path);self.addCleanup(self.cleanup)

    def cleanup(self):
        self.view.shutdown();self.view.close();self.view.deleteLater();self.app.processEvents();self.temp.cleanup()

    def test_compatible_stances_skip_idle_and_keep_outgoing_motion_and_phase(self):
        self.assertEqual(self.view.pack.transition_route('smug','shy'),[])
        old=self.view.pack.actions['idle'];new=self.view.pack.actions['smug']
        self.view._loaded='idle';self.view._sheet=QImage(str(old.path))
        self.view._started=100.;self.view._frame=self.view._sheet.copy(0,0,64,64)
        self.view._display=self.view._frame;self.view._previous=QImage()
        target=QImage(self.view._sheet.size(),self.view._sheet.format());target.fill('green')
        self.view._ready_next=((new,None),target)
        with patch('aicompanion.ui.animation_view.time.monotonic',return_value=100.10):
            self.view._consume_ready(100.10)
        self.assertAlmostEqual(self.view._started,100.)
        self.assertIsNotNone(self.view._outgoing)
        with patch('aicompanion.ui.animation_view.time.monotonic',return_value=100.21):
            self.view.advance()
        pixel=self.view._display.pixelColor(32,32)
        # 旧片段已从蓝色帧继续走到红色帧；冻结旧截图会留下蓝色分量。
        self.assertGreater(pixel.red(),30);self.assertLess(pixel.blue(),5)
        with patch('aicompanion.ui.animation_view.time.monotonic',return_value=100.5):self.view.advance()
        self.assertIsNone(self.view._outgoing)

    def test_expression_and_daily_actions_select_distinct_clips(self):
        for kind,key in [('expression','smug'),('expression','pout'),('expression','shy'),
                         ('expression','surprised'),('motion','milk_tea'),('motion','stretch')]:
            self.view.perform(kind,key);self.assertEqual(self.view.action,key)
        self.view.perform('expression','neutral');self.assertEqual(self.view.action,'idle')

    def test_direction_selects_rear_gait_only_for_upward_routes(self):
        for vector,key,direction in [((1,-1),'walking_back',1),((-1,-1),'walking_back',-1),
                                     ((0,-1),'walking_back',-1),((0,1),'walking',-1),((1,0),'walking',1)]:
            self.view.set_walking_vector(*vector);self.assertEqual(self.view.action,key)
            self.assertEqual(self.view.direction,direction)
        self.view.set_walking(0);self.assertEqual(self.view.action,'idle')

    def test_walk_frames_advance_with_distance_and_stay_still_without_steps(self):
        self.view.set_walking_vector(1,0)
        clip=self.view.pack.actions['walking']
        sheet=QImage(str(clip.path))
        self.view._loaded='walking';self.view._sheet=sheet;self.view._previous=QImage()
        self.view._queue=[];self.view._index=-1
        with patch('aicompanion.ui.animation_view.time.monotonic',return_value=100):
            self.view.advance();self.assertEqual(self.view._index,0)
        with patch('aicompanion.ui.animation_view.time.monotonic',return_value=200):
            self.view.advance();self.assertEqual(self.view._index,0)
            self.view.step_walking(70/12);self.assertEqual(self.view._index,1)
            self.view.step_walking(70/12);self.assertEqual(self.view._index,0)

    def test_expression_interrupts_walking_then_drag_interrupts_expression(self):
        self.view.set_walking_vector(1,1);self.view.perform('expression','shy')
        self.assertEqual(self.view.action,'shy')
        self.view.set_drag(True);self.assertEqual(self.view.action,'idle') # 合成包的 grabbed 指向 idle
        self.assertFalse(self.view.walking_ready)

    def test_bundled_walk_uses_new_drawings_and_alternates_support(self):
        import hashlib
        import numpy as np
        from PIL import Image
        root=PACKS_DIR.parents[2]
        for key in ('walking','walking_back'):
            folder=root/'art/deepseek-whale/locomotion-keys-v2'/key
            centers=[];hashes=[]
            for i in range(6):
                path=folder/f'{i:02}.png';hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
                with Image.open(path) as image:alpha=np.asarray(image)[:,:,3]
                ys,xs=np.where(alpha>80);centers.append(float(np.median(xs[ys>ys.max()-6])))
            self.assertEqual(len(set(hashes)),6)
            self.assertLess(max(Image.open(folder/f'{i:02}.png').getbbox()[3] for i in range(6))-min(Image.open(folder/f'{i:02}.png').getbbox()[3] for i in range(6)),4)
        source=json.loads((PACKS_DIR/'deepseek-whale/animation-original/SOURCE.json').read_text(encoding='utf-8'))
        for key in ('walking','walking_back'):
            self.assertTrue(source['sources'][key]['new_drawing'])
            self.assertEqual(source['sources'][key]['gait_cycle_distance'],32)

    def test_new_cues_are_available_without_changing_persona(self):
        vocab=Vocab.from_pack(load_pack(PACKS_DIR/'deepseek-whale'))
        for text,name in [('得意','smug'),('气鼓鼓','pout'),('害羞','shy'),('惊讶','surprised'),
                          ('喝奶茶','milk_tea'),('伸懒腰','stretch')]:
            self.assertEqual(vocab.lookup(text).name,name)


if __name__=='__main__':unittest.main()
