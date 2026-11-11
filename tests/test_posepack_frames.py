"""T24：完整画帧的事件优先级、距离相位、转身和资源契约。"""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
JS=ROOT/'src/aicompanion/assets/rig/posepack_frames.js'
PACK=ROOT/'src/aicompanion/characters/deepseek-whale/rig/frame-actions'
NODE=shutil.which('node')

@unittest.skipUnless(NODE,'离线 JS 单元测试需要本机已有 Node；桌宠运行不依赖 Node')
class FullFrameControllerTest(unittest.TestCase):
    def run_js(self,body):
        source='''const assert=require('node:assert/strict');
const {Controller}=require(process.argv[1]);
const config=require(process.argv[2]);
const c=new Controller(config);
let host={pose:'idle',dragging:false,walking:0,facing:1,dragVx:0};
function step(seconds){for(let n=0;n<Math.ceil(seconds/.01);n++){c.update(host);c.tick(.01);}}
'''+body
        result=subprocess.run([NODE,'-e',source,str(JS),str(PACK/'actions.json')],capture_output=True,text=True,encoding='utf-8',timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_pickup_direction_release_and_stale_host_pose(self):
        self.run_js('''host.pose='grabbed';host.dragging=true;host.dragVx=-500;
step(.19);assert.equal(c.state,'drag_left');assert.equal(c.mirrored(),false);
host.dragVx=500;step(.02);assert.equal(c.state,'drag_left');assert.equal(c.frame(),1);assert.equal(c.mirrored(),false);
step(.07);assert.equal(c.state,'drag_right');assert.equal(c.frame(),1);assert.equal(c.mirrored(),true);
step(.08);assert.equal(c.dragTurn,null);
host.dragging=false;c.event('drag',{active:false});c.event('fall',{active:true});
step(.3);assert.equal(c.state,'fall');assert.ok(!c.held);
c.event('land',{hard:true});host.pose='falling';step(.31);assert.equal(c.state,'dizzy');
assert.equal(c.diagnostics().extra_swing,0);assert.deepEqual(c.config.grab_pivot,[627,80]);''')

    def test_hard_and_soft_landing_have_distinct_recovery(self):
        self.run_js('''c.event('land',{hard:true});step(3.1);assert.equal(c.state,'idle');
assert.ok(c.history.includes('dizzy'));assert.ok(c.history.includes('recover'));
const before=c.history.length;c.event('land',{hard:false});step(.65);
assert.equal(c.state,'idle');assert.ok(!c.history.slice(before).includes('dizzy'));''')

    def test_clock_cannot_advance_gait_without_distance(self):
        self.run_js('''host.walking=1;step(.3);assert.equal(c.state,'walk');
const frame=c.frame();step(2);assert.equal(c.frame(),frame);
c.travel(10,.2);assert.notEqual(c.frame(),frame);
const phase=c.phase;step(.5);assert.equal(c.phase,phase);''')

    def test_equal_travel_has_equal_phase_with_different_intervals(self):
        self.run_js('''const other=new Controller(config);c.enter('walk');other.enter('walk');
for(let i=0;i<10;i++)c.travel(3,.04);for(let i=0;i<3;i++)other.travel(10,.1);
assert.ok(Math.abs(c.phase-other.phase)<1e-12);
c.travel(-400,.5);assert.ok(c.phase>=0&&c.phase<1);''')

    def test_speed_hysteresis_and_stop_transition(self):
        self.run_js('''host.walking=1;c.travel(20,.1);step(.3);assert.equal(c.state,'run');
const phase=c.phase;c.travel(9.5,.1);step(.01);assert.equal(c.state,'run');
c.travel(8,.1);c.travel(8,.1);step(.01);assert.equal(c.state,'walk');assert.notEqual(c.phase,0);
host.walking=0;step(.01);assert.equal(c.state,'brake');step(.3);assert.equal(c.state,'idle');''')

    def test_turn_has_three_frames_and_defers_mirror(self):
        self.run_js('''host.facing=-1;step(.01);assert.equal(c.state,'turn');
assert.equal(c.facing,1);assert.equal(c.frame(),0);assert.equal(c.mirrored(),false);
step(.11);assert.equal(c.frame(),1);step(.1);assert.equal(c.frame(),2);
step(.15);assert.equal(c.facing,-1);assert.equal(c.state,'idle');assert.equal(c.mirrored(),true);''')

    def test_drag_interrupts_locomotion_and_injury_immediately(self):
        self.run_js('''host.walking=1;c.travel(10,.1);step(.4);const phase=c.phase;
host.dragging=true;step(.01);assert.equal(c.state,'pickup');assert.equal(c.phase,phase);
const serial=c.serial;c.event('land',{hard:true});host.dragging=false;host.walking=0;step(.32);
assert.equal(c.state,'dizzy');host.dragging=true;step(.01);
assert.equal(c.state,'pickup');assert.ok(c.serial>serial);''')

    def test_invalid_distance_does_not_corrupt_phase(self):
        self.run_js('''const phase=c.phase;for(const [d,t] of [[NaN,.1],[1,0],[Infinity,1],[1,-1]])c.travel(d,t);
assert.equal(c.phase,phase);assert.ok(Number.isFinite(c.speed));''')

class FullFrameResourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PIL import Image
        cls.Image=Image;cls.config=json.loads((PACK/'actions.json').read_text(encoding='utf-8'));cls.sources=ROOT/cls.config['source_root']

    def test_canvas_anchor_counts_and_timing(self):
        c=self.config;self.assertEqual(c['type'],'full-frame-sequence');self.assertEqual(c['canvas'],[1254,1254]);self.assertEqual(c['anchor'],[627,1156])
        self.assertLessEqual(c['actions']['pickup']['count']/c['actions']['pickup']['fps'],.2)
        self.assertEqual(c['transition_mode'],'opaque-keyframes');self.assertEqual(c['crossfade_ms'],0)
        self.assertTrue(60<=c['direction_change_ms']<=80)
        for name in ('walk','run'):
            self.assertEqual(c['actions'][name]['count'],8);self.assertEqual(c['actions'][name]['phase_driver'],'distance')
        self.assertEqual(c['actions']['turn']['count'],3)

    def test_all_frames_are_complete_rgba_and_heads_aligned(self):
        for name,spec in self.config['actions'].items():
            self.assertEqual(len(spec['source_frames']),spec['count'])
            for frame in spec['source_frames']:
                with self.subTest(action=name,path=frame['file']):
                    with self.Image.open(self.sources/frame['file']) as im:
                        self.assertEqual(list(im.size),self.config['source_frame_size']);self.assertEqual(im.mode,'RGBA');self.assertEqual(im.getchannel('A').getextrema(),(0,255))
                    self.assertLessEqual(abs(frame['head_width_qa']['error_percent']),2)
                    if frame['grounded']:self.assertEqual(frame['feet_y'],1156)
                    else:self.assertLess(frame['feet_y'],1156)

    def test_runtime_atlas_contains_full_frame_in_every_cell(self):
        for name,spec in self.config['actions'].items():
            with self.Image.open(PACK/spec['atlas']) as atlas:
                self.assertEqual(atlas.size,(2048,((spec['count']+3)//4)*512))
                for i in range(spec['count']):
                    with self.Image.open(self.sources/spec['source_frames'][i]['file']) as original:
                        expected=original.convert('RGBA').resize((512,512),self.Image.Resampling.LANCZOS)
                    cell=atlas.crop(((i%4)*512,(i//4)*512,(i%4+1)*512,(i//4+1)*512)).convert('RGBA')
                    self.assertEqual(cell.tobytes(),expected.tobytes(),name)

    def test_mirrored_drag_uses_one_atlas_and_pendulum_order(self):
        left=self.config['actions']['drag_left'];right=self.config['actions']['drag_right']
        self.assertEqual(left['atlas'],right['atlas']);self.assertEqual(right['mirror_of'],'drag_left')
        self.assertEqual(left['phase_labels'],['最大甩开','回中','小幅反摆','回中'])

    def test_bit_masks_exactly_match_original_alpha_threshold(self):
        self.assertEqual(self.config['hit_mask_format'],'alpha>24-msb-bitset')
        self.assertEqual(self.config['actions']['drag_left']['hit_mask'],self.config['actions']['drag_right']['hit_mask'])
        for name,spec in self.config['actions'].items():
            with self.subTest(action=name),self.Image.open(PACK/spec['atlas']) as atlas:
                bits=(PACK/spec['hit_mask']).read_bytes()
                self.assertEqual(len(bits),atlas.width*atlas.height//8)
                restored=self.Image.frombytes('1',atlas.size,bits).convert('L')
                expected=atlas.getchannel('A').point([0]*25+[255]*231)
                self.assertEqual(restored.tobytes(),expected.tobytes())

    def test_rebuild_sources_are_outside_the_runtime_package(self):
        self.assertFalse(self.sources.is_relative_to(ROOT/'src'))
        self.assertFalse((PACK/'source').exists());self.assertEqual(len(list(self.sources.glob('*/*.webp'))),42)

    def test_brake_bridge_is_the_approved_idle_complete_image(self):
        original=self.Image.open(ROOT/'art/deepseek-whale/originals/idle.png').convert('RGBA')
        translated=self.Image.new('RGBA',original.size,(0,0,0,0));translated.alpha_composite(original,(0,1))
        expected=translated.resize((512,512),self.Image.Resampling.LANCZOS)
        spec=self.config['actions']['brake'];frame=spec['source_frames'][-1]
        self.assertTrue(frame['baseline_bridge'])
        with self.Image.open(self.sources/frame['file']) as actual:self.assertEqual(actual.convert('RGBA').tobytes(),expected.tobytes())

if __name__=='__main__':unittest.main()
