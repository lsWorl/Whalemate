"""一次性过渡、透明合成及抢占；用临时图集和可控时钟验证真实 Qt 状态机。"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QPoint,Qt
from PySide6.QtGui import QColor,QImage,QPainter
from PySide6.QtWidgets import QApplication
from aicompanion.characters import CharacterPackError,PACKS_DIR
from aicompanion.characters.animation_pack import load_animation_pack
from aicompanion.ui.animation_view import AnimationAvatar,mix_frames


def picture(color):
    image=QImage(64,64,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent)
    painter=QPainter(image);painter.fillRect(16,16,32,40,QColor(color));painter.end();return image


def make_pack(root):
    def sheet(name,colors):
        image=QImage(64*len(colors),64,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent)
        painter=QPainter(image)
        for i,color in enumerate(colors):painter.drawImage(i*64,0,picture(color))
        painter.end();assert image.save(str(root/(name+'.webp')),'WEBP',100)
        return {'image':name+'.webp','frameWidth':64,'frameHeight':64,'columns':len(colors),'rows':1,'frameCount':len(colors),'fps':12}
    actions={name:sheet(name,[color,color]) for name,color in [('idle','red'),('working','blue'),('thinking','green'),('grabbed','yellow')]}
    picture('red').save(str(root/'poster.png'))
    transitions=[]
    for source,target,colors in [('idle','working',['red','purple','blue']),('working','idle',['blue','purple','red']),
                                 ('idle','thinking',['red','cyan','green']),('thinking','idle',['green','cyan','red'])]:
        transitions.append({'from':source,'to':target,**sheet(source+'-'+target,colors)})
    data={'format':'AICompanionAnimationPack','version':1,'size':[64,64],'poster':'poster.png','actions':actions,
          'poses':{'idle':'idle','talking':'idle','thinking':'thinking','working':'working','grabbed':'grabbed','falling':'grabbed'},'transitions':transitions}
    path=root/'animations.json';path.write_text(json.dumps(data),encoding='utf-8');return path,data


class TransitionPackTests(unittest.TestCase):
    def test_routes_use_direct_bridge_or_at_most_idle_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            path,data=make_pack(Path(directory));pack=load_animation_pack(path)
            self.assertEqual([(x.source,x.target) for x in pack.transition_route('idle','working')],[('idle','working')])
            self.assertEqual([(x.source,x.target) for x in pack.transition_route('working','thinking')],[('working','idle'),('idle','thinking')])
            self.assertEqual(pack.transition_route('grabbed','working'),[])

    def test_invalid_transition_endpoints_paths_and_duration_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path,data=make_pack(Path(directory))
            for update in ({'from':'absent'},{'to':'idle'},{'image':'../outside.webp'},{'fps':1}):
                bad=json.loads(json.dumps(data));bad['transitions'][0].update(update);path.write_text(json.dumps(bad),encoding='utf-8')
                with self.subTest(update=update),self.assertRaises(CharacterPackError):load_animation_pack(path)
            data['transitions'].append(dict(data['transitions'][0]));path.write_text(json.dumps(data),encoding='utf-8')
            with self.assertRaises(CharacterPackError):load_animation_pack(path)

    def test_bundled_bridges_have_lossless_matching_endpoints_and_source_hashes(self):
        root=PACKS_DIR/'deepseek-whale/animation-original';pack=load_animation_pack(root/'animations.json')
        records=json.loads((root/'TRANSITIONS.json').read_text(encoding='utf-8'))
        self.assertEqual(len(pack.transitions),22);self.assertEqual(records['newly_drawn_keys'],8)
        cache={}
        def first(action):
            if action.id not in cache:
                image=QImage(str(action.path)).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
                cache[action.id]=image.copy(0,0,384,384)
            return cache[action.id]
        for record in records['records']:
            source,target=record['from'],record['to'];bridge=pack.transitions[source,target].clip
            self.assertEqual(hashlib.sha256((root/record['file']).read_bytes()).hexdigest(),record['sha256'])
            for key in ('source','target'):
                self.assertEqual(hashlib.sha256((root/record[key+'_first_frame_file']).read_bytes()).hexdigest(),record[key+'_sha256'])
            sheet=QImage(str(bridge.path)).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            a=sheet.copy(0,0,384,384);index=bridge.count-1
            b=sheet.copy(index%bridge.columns*384,index//bridge.columns*384,384,384)
            # 全透明像素 RGB 无视觉含义，转成预乘颜色后比较真正显示的端点。
            self.assertEqual(a,first(pack.actions[source]));self.assertEqual(b,first(pack.actions[target]))


class TransitionViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.path,self.data=make_pack(self.root)
        self.view=AnimationAvatar(self.path);self.view.resize(128,128);self.view.show()
        until=time.monotonic()+3
        while (self.view._loaded!='idle' or self.view.transitioning) and time.monotonic()<until:
            self.app.processEvents();time.sleep(.005)
        self.assertEqual(self.view._loaded,'idle');self.assertFalse(self.view.transitioning)
        self.view.timer.stop();self.view.decodeRequested.disconnect(self.view.decoder.decode)
        self.now=100.;self.clock=patch('aicompanion.ui.animation_view.time.monotonic',side_effect=lambda:self.now);self.clock.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.clock.stop();self.view.shutdown();self.view.close();self.view.deleteLater();self.app.processEvents();self.temp.cleanup()

    def finish_decode(self,clip):
        self.assertEqual(self.view._loading,clip.id)
        image=QImage(str(clip.path)).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        self.view._decoded(clip,image,'')

    def begin_working(self):
        self.view.set_pose('working');bridge=self.view.pack.transitions['idle','working']
        self.finish_decode(bridge.clip);return bridge

    def test_premultiplied_mix_keeps_body_opaque(self):
        image=mix_frames(picture('red'),picture('blue'),.5);color=image.pixelColor(32,32)
        self.assertGreaterEqual(color.alpha(),254);self.assertGreater(color.red(),120);self.assertGreater(color.blue(),120)
        self.assertEqual(image.pixelColor(0,0).alpha(),0)

    def test_bridge_plays_once_then_starts_target_at_matching_first_frame(self):
        started=[];finished=[]
        self.view.transitionStarted.connect(lambda a,b:started.append((a,b)))
        self.view.transitionFinished.connect(lambda a,b:finished.append((a,b)))
        self.begin_working();self.finish_decode(self.view.pack.actions['working'])
        self.now+=.12;self.view.advance();self.assertEqual(self.view._bridge_index,1)
        self.assertGreaterEqual(self.view._display.pixelColor(32,32).alpha(),254)
        self.now+=.2;self.view.advance()
        self.assertEqual(self.view._loaded,'working');self.assertIsNone(self.view._bridge)
        self.assertEqual(started,[('idle','working')]);self.assertEqual(finished,started)
        self.assertGreater(self.view._display.pixelColor(32,32).blue(),240)

    def test_slow_target_load_holds_last_bridge_frame_instead_of_replaying(self):
        self.begin_working();self.now+=1.;self.view.advance()
        self.assertEqual(self.view._bridge_index,2);self.assertIsNotNone(self.view._bridge)
        self.assertGreater(self.view._display.pixelColor(32,32).blue(),240)
        self.finish_decode(self.view.pack.actions['working']);self.view.advance()
        self.assertEqual(self.view._loaded,'working')

    def test_interruption_starts_from_current_picture_and_stale_decode_is_ignored(self):
        self.begin_working();self.now+=.12;self.view.advance();before=self.view._display.copy()
        self.view.set_pose('thinking');self.assertEqual(self.view._display,before)
        self.finish_decode(self.view.pack.actions['working'])
        self.assertEqual(self.view._display,before)
        self.finish_decode(self.view.pack.actions['thinking'])
        self.assertEqual(self.view._display,before)
        self.now+=.3;self.view.advance();self.assertEqual(self.view._loaded,'thinking')
        self.assertIsNone(self.view._bridge)

    def test_drag_preempts_route_and_uses_short_settle(self):
        self.begin_working();self.now+=.12;self.view.advance();self.view.set_drag(True)
        self.finish_decode(self.view.pack.actions['working']);self.finish_decode(self.view.pack.actions['grabbed'])
        self.assertIsNone(self.view._bridge);self.assertLessEqual(self.view._fade_duration,.1)
        self.now+=.11;self.view.advance();self.assertEqual(self.view._loaded,'grabbed')

    def test_hit_testing_uses_visible_mix_instead_of_only_destination(self):
        left=QImage(64,64,QImage.Format.Format_ARGB32_Premultiplied);left.fill(Qt.GlobalColor.transparent)
        right=left.copy();painter=QPainter(left);painter.fillRect(4,16,12,32,QColor('red'));painter.end()
        painter=QPainter(right);painter.fillRect(48,16,12,32,QColor('blue'));painter.end()
        self.view._frame=right;self.view._display=mix_frames(left,right,.5)
        self.assertTrue(self.view.hit(QPoint(16,64)));self.assertTrue(self.view.hit(QPoint(112,64)))
        self.assertFalse(self.view.hit(QPoint(64,64)))

    def test_manual_action_duration_begins_after_transition(self):
        self.view.play_action('working');self.finish_decode(self.view.pack.transitions['idle','working'].clip)
        self.finish_decode(self.view.pack.actions['working']);self.now+=.5;self.view.advance()
        self.assertTrue(self.view._cue_started)
        self.assertAlmostEqual(self.view._cue_until,self.now+2/12)

    def test_hide_discards_transition_and_late_result_then_show_resumes(self):
        bridge=self.begin_working();self.view.hide();self.assertTrue(self.view._bridge_sheet.isNull())
        self.finish_decode(self.view.pack.actions['working']);self.assertIsNone(self.view._loaded)
        self.view.show();self.view.timer.stop();self.finish_decode(self.view.pack.actions['working'])
        self.assertEqual(self.view._loaded,'working')


if __name__=='__main__':unittest.main()
