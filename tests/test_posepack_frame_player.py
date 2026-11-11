"""整帧播放器：异步图集载入、单层不透明显示、换向回中及打断续接。"""
from pathlib import Path
import shutil,subprocess,unittest

ROOT=Path(__file__).resolve().parents[1]
NODE=shutil.which('node')
FIXTURE=r'''
const assert=require('node:assert/strict');
const {wrap}=require(process.argv[1]),config=require(process.argv[2]);
const point=()=>({x:0,y:0,set(x,y=x){this.x=x;this.y=y;}});
class Container {
 constructor(){this.children=[];this.visible=true;this.alpha=1;this.scale=point();this.pivot=point();this.position=point();}
 addChild(child){this.children.push(child);return child;}
 destroy(){this.destroyed=true;}
 toGlobal(p){return p;}
}
class Texture {
 constructor(base,rect){this.baseTexture=base;this.frame=rect;this.width=rect?rect.width:base.width;this.height=rect?rect.height:base.height;}
 destroy(){this.destroyed=true;}
}
Texture.EMPTY={};
class Sprite extends Container{constructor(texture){super();this.texture=texture;} toLocal(p){return p;}}
global.PIXI={Container,Sprite,Texture,Rectangle:class{constructor(x,y,width,height){Object.assign(this,{x,y,width,height});}},Point:class{constructor(x,y){Object.assign(this,{x,y});}}};
const loads=[],textures=[],gates=new Map(),bad=new Set();
function texture(key){const spec=Object.values(config.actions).find(s=>s.atlas===key);
 const t=new Texture({width:bad.has(key)?1:2048,height:Math.ceil(spec.count/4)*512});textures.push(t);return t;}
global.window={RigRenderer:{alphaOf:()=>null,loadOwnTexture(url){const key=url.split('/').at(-1);loads.push(key);
 if(gates.has(key))return new Promise(resolve=>gates.set(key,()=>resolve(texture(key))));return Promise.resolve(texture(key));}}};
const app={stage:new Container(),renderer:{width:280,height:280,resolution:1,render(){throw new Error('不应分配叠影快照');}}};
const built={figure:new Container(),rig:{space:{view:[-12,16,268,268]}},frameBase:'pack/',frameConfig:config};app.stage.addChild(built.figure);
const base={state:{pose:'idle',dragging:false,walking:0,facing:1,dragVx:0},tick(){},set(v){Object.assign(this.state,v);},busy:()=>false,
 diagnostics:()=>({kind:'posepack'}),dispose(){this.disposed=true;},hitmap:()=>({}),probe:()=>({hit:false})};
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
let player;
async function step(seconds){for(let i=0;i<Math.ceil(seconds/.01);i++){player.tick(10);await flush();}}
const diag=()=>player.diagnostics().motion;
function opaque(){const d=diag();assert.equal(d.visible_character_layers,1);assert.equal(d.layer_alpha,1);assert.equal(d.fading,false);}
'''

@unittest.skipUnless(NODE,'离线播放器测试使用本机已有 Node')
class FullFramePlayerTest(unittest.TestCase):
    def run_js(self,body):
        source=FIXTURE+'\n(async()=>{'+body+'})().catch(e=>{console.error(e);process.exitCode=1;});'
        result=subprocess.run([NODE,'-e',source,str(ROOT/'src/aicompanion/assets/rig/posepack_frames.js'),
            str(ROOT/'src/aicompanion/characters/deepseek-whale/rig/frame-actions/actions.json')],capture_output=True,text=True,encoding='utf-8',timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_sequence_loading_selects_cells_and_reuses_mirrored_atlas(self):
        self.run_js('''player=wrap(app,built,base);await flush();
player.set({dragging:true,pose:'grabbed',dragVx:-400});await step(.4);
assert.equal(diag().displayed,'drag_left');assert.equal(diag().ready,true);opaque();
player.set({dragVx:400});await step(.3);assert.equal(diag().displayed,'drag_right');opaque();
assert.equal(loads.filter(k=>k==='drag_left.webp').length,1);assert.deepEqual(diag().errors,[]);
const layer=app.stage.children[1],frame=layer.children[0].texture.frame;
assert.equal(frame.x,diag().displayed_index%4*512);assert.equal(frame.width,512);
player.dispose();assert.ok(base.disposed);assert.ok(textures.every(t=>t.destroyed));''')

    def test_interruption_holds_current_frame_until_new_sequence_is_ready(self):
        self.run_js('''gates.set('pickup.webp',null);player=wrap(app,built,base);await flush();
player.set({walking:1});player.motionEvent('travel',{distance:40,seconds:.1});await step(.5);
assert.equal(diag().displayed,'run');const before=diag();
player.set({dragging:true,pose:'grabbed',dragVx:-400});await step(.1);
assert.equal(diag().displayed,'run');assert.equal(diag().displayed_index,before.displayed_index);opaque();
assert.equal(diag().phase,before.phase);gates.get('pickup.webp')();await flush();player.tick(10);
assert.equal(diag().displayed,'pickup');assert.equal(diag().displayed_index,0);opaque();
assert.equal(diag().phase,before.phase);await step(.3);assert.equal(diag().displayed,'drag_left');''')

    def test_reversal_has_old_and_new_center_frames_without_double_image(self):
        self.run_js('''player=wrap(app,built,base);await flush();player.set({dragging:true,pose:'grabbed',dragVx:-400});await step(.5);
player.set({dragVx:400});player.tick(10);assert.equal(diag().displayed,'drag_left');assert.equal(diag().displayed_index,1);opaque();
let switched=false,newCenter=false;
for(let i=0;i<25;i++){player.tick(10);opaque();const d=diag();
 if(d.displayed==='drag_right'){if(!switched){assert.equal(d.displayed_index,1);switched=true;}if(d.displayed_index===1)newCenter=true;}}
assert.ok(switched&&newCenter);assert.equal(diag().direction_bridge,null);''')

    def test_bad_atlas_is_reported_once_and_keeps_current_character(self):
        self.run_js('''bad.add('start.webp');player=wrap(app,built,base);await flush();player.set({walking:1});await step(.5);
assert.equal(diag().displayed,'idle');assert.equal(loads.filter(k=>k==='start.webp').length,1);
assert.equal(diag().errors.length,1);assert.match(diag().errors[0],/尺寸/);opaque();''')

    def test_dispose_during_loading_releases_late_textures(self):
        self.run_js('''gates.set('pickup.webp',null);player=wrap(app,built,base);await flush();player.dispose();
gates.get('pickup.webp')();await flush();assert.ok(textures.every(t=>t.destroyed));assert.equal(diag().pending,0);assert.equal(diag().atlases,0);''')

    def test_cycles_and_stop_never_stack_transparent_characters(self):
        self.run_js('''player=wrap(app,built,base);await flush();player.set({walking:1});await step(.3);
player.motionEvent('travel',{distance:35,seconds:.2});let indices=new Set();
for(let i=0;i<60;i++){player.motionEvent('travel',{distance:1,seconds:.02});await step(.02);opaque();indices.add(diag().displayed_index);}
assert.ok(indices.size>4);player.set({walking:0});for(let i=0;i<40;i++){await step(.01);opaque();}
assert.equal(diag().displayed,'idle');assert.deepEqual(diag().errors,[]);''')

    def test_hidden_baseline_does_not_start_legacy_walk_hops(self):
        self.run_js('''const walking=[];base.tick=()=>walking.push(base.state.walking);
player=wrap(app,built,base);await flush();player.set({walking:1});await step(.4);
assert.equal(diag().displayed,'walk');assert.equal(base.state.walking,1);assert.ok(walking.every(v=>v===0));''')

if __name__=='__main__':unittest.main()
