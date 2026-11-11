"""复用真实 DesktopPetWindow._walk_step 验证距离选速和网页 run 链路。"""
import json,shutil,subprocess,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from aicompanion.pet.walk_motion import WalkMotion
from aicompanion.ui.desktop_pet import DesktopPetWindow

ROOT=Path(__file__).resolve().parents[1]
class Timer:
    def __init__(self):self.active=False
    def start(self):self.active=True
    def stop(self):self.active=False
    def isActive(self):return self.active

class View:
    def __init__(self):self.walking=0;self.samples=[]
    def set_walking(self,direction):self.walking=direction
    def advance_motion(self,distance,seconds):self.samples.append({'distance':distance,'seconds':seconds})

class Pet:
    walk_to=DesktopPetWindow.walk_to
    stop_walking=DesktopPetWindow.stop_walking
    _walk_step=DesktopPetWindow._walk_step
    def __init__(self,scale):
        self.scale=scale;self.position=0;self.view=View();self._walk_target=None;self._walk_motion=None;self._walk_last=0
        self._press_pos=None;self._fall_timer=Timer();self._walk_timer=Timer();self.machine=SimpleNamespace(shown='idle')
        self.bubble=SimpleNamespace(move_anchor=lambda point:None);self.saved=0
    def x(self):return self.position
    def y(self):return 100
    def move(self,x,y):self.position=x
    def save_geometry(self):self.saved+=1
    def _head_point(self):return (0,0)

def replay(distance,scale=1.):
    pet=Pet(scale);now=10.;frames=[]
    with patch('aicompanion.ui.desktop_pet.time.monotonic',side_effect=lambda:now):
        pet.walk_to(distance)
        for i in range(3000):
            now+=.016;before=pet.x();pet._walk_step()
            frames.append(pet.view.samples[-1]|{'walking':pet.view.walking,'x':pet.x()})
            if pet._walk_target is None:break
        else:raise AssertionError('真实 _walk_step 未能到达目标')
    return pet,frames

class WalkMotionTest(unittest.TestCase):
    def test_acceleration_cruise_deceleration_and_no_overshoot(self):
        for scale in (1.,1.5):
            plan=WalkMotion(0,600*scale,scale);previous=0.;speeds=[]
            for i in range(2000):
                position=plan.advance(.016);speeds.append(plan.speed)
                self.assertLessEqual(position,600*scale);self.assertLessEqual(plan.speed,160*scale)
                rate=plan.acceleration if plan.speed>previous else plan.deceleration
                self.assertLessEqual(abs(plan.speed-previous),rate*.016+1e-6);previous=plan.speed
                if plan.done:break
            self.assertTrue(plan.done);self.assertGreater(max(speeds),150*scale)
            self.assertLess(speeds[-10],max(speeds)/2)

    def test_short_and_fractional_steps_reach_exact_target(self):
        for distance in (1,9,180,-180):
            pet,frames=replay(distance)
            self.assertEqual(pet.x(),distance);self.assertEqual(pet.view.walking,0);self.assertFalse(pet._walk_timer.active)
            self.assertEqual(sum(f['distance'] for f in frames),distance);self.assertEqual(pet.saved,1)
            self.assertTrue(any(f['distance']==0 for f in frames))

    def test_drag_cancels_walk_without_moving_again(self):
        pet=Pet(1.);pet.walk_to(500);pet._press_pos=object();pet._walk_step()
        self.assertIsNone(pet._walk_target);self.assertEqual(pet.x(),0);self.assertEqual(pet.view.walking,0)

    @unittest.skipUnless(shutil.which('node'),'离线网页状态机测试需要本机已有 Node')
    def test_real_walk_step_enters_run_for_long_routes_but_not_short_ones(self):
        js='''const fs=require('node:fs');const {Controller}=require(process.argv[1]);const config=require(process.argv[2]);
const data=JSON.parse(fs.readFileSync(0,'utf8')),c=new Controller(config);let states=[],last=1;
for(const f of data.frames){c.travel(f.distance/data.view_scale,f.seconds);last=f.walking;c.update({pose:'idle',walking:last,facing:data.sign});c.tick(f.seconds);states.push(c.state);}
for(let i=0;i<50;i++){c.update({pose:'idle',walking:0,facing:data.sign});c.tick(.01);states.push(c.state);}
process.stdout.write(JSON.stringify({states:[...new Set(states)],distance:c.distance}));'''
        for scale in (1.,1.5):
            for distance,expect_run in ((180,False),(260,True),(600,True),(-600,True)):
                with self.subTest(scale=scale,distance=distance):
                    pet,frames=replay(distance,scale)
                    viewport=round(300*scale*1.05);view_scale=min(viewport*.92/252,viewport*.86/280)
                    result=subprocess.run([shutil.which('node'),'-e',js,str(ROOT/'src/aicompanion/assets/rig/posepack_frames.js'),
                        str(ROOT/'src/aicompanion/characters/deepseek-whale/rig/frame-actions/actions.json')],input=json.dumps({'frames':frames,'view_scale':view_scale,'sign':1 if distance>0 else -1}),
                        capture_output=True,text=True,encoding='utf-8',timeout=15)
                    self.assertEqual(result.returncode,0,result.stderr);states=json.loads(result.stdout)['states']
                    self.assertEqual('run' in states,expect_run);self.assertIn('brake',states);self.assertIn('idle',states)

class DragForwardTest(unittest.TestCase):
    def test_drag_command_is_emitted_only_by_desktop_dispatch(self):
        from aicompanion.body.rig_view import RigAvatar
        calls=[];view=SimpleNamespace(dragging=False,_set=lambda **kw:None,motion_event=lambda name,**data:calls.append((name,data)))
        RigAvatar.set_drag(view,True,200)
        self.assertEqual(calls,[])
        pet=SimpleNamespace(machine=SimpleNamespace(dispatch=lambda *a,**kw:[]),_apply_state=lambda:None,view=view)
        DesktopPetWindow.dispatch(pet,'grab',active=True)
        self.assertEqual(calls,[('drag',{'active':True})])

if __name__=='__main__':unittest.main()
