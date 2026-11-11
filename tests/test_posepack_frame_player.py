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
 constructor(base,rect){this.baseTexture=base;this.frame=rect;this.width=rect?rect.width:base.width;this.height=rect?rect.height:base.height;if(!rect)textures.push(this);}
 destroy(base){this.destroyed=true;if(base)this.baseTexture.destroy();}
}
Texture.EMPTY={};
class Sprite extends Container{constructor(texture){super();this.texture=texture;} toLocal(p){return p;}}
const loads=[],textures=[],bitmaps=[],gpuBases=[],bitmapResources=[],gates=new Map(),decodeGates=new Map(),bad=new Set(),badMasks=new Set(),masks=new Map();
class BitmapResource{constructor(source){this.source=source;bitmapResources.push(this);}destroy(){this.source=null;this.destroyed=true;}}
class BaseTexture {
 constructor(resource){this.resource=resource;this.width=resource.source.width;this.height=resource.source.height;gpuBases.push(this);}
 destroy(){assert.ok(!this.destroyed);this.destroyed=true;this.resource=null;gpuBases.splice(gpuBases.indexOf(this),1);}
}
global.PIXI={Container,Sprite,Texture,BaseTexture,ImageBitmapResource:BitmapResource,ALPHA_MODES:{PMA:2},MIPMAP_MODES:{OFF:0},
 Rectangle:class{constructor(x,y,width,height){Object.assign(this,{x,y,width,height});}},Point:class{constructor(x,y){Object.assign(this,{x,y});}}};
global.Blob=class{constructor(parts){this.parts=parts;}};
global.createImageBitmap=blob=>{
 const key=new TextDecoder().decode(blob.parts[0]),spec=Object.values(config.actions).find(s=>s.atlas===key);
 return new Promise(resolve=>{
  const finish=()=>{const bitmap={width:bad.has(key)?1:2048,height:Math.ceil(spec.count/4)*512,closed:0,close(){this.closed++;assert.equal(this.closed,1);}};bitmaps.push(bitmap);resolve(bitmap);};
  if(decodeGates.has(key))decodeGates.set(key,finish);else finish();
 });
};
global.XMLHttpRequest=class {
 open(method,url){this.key=url.split('/').at(-1);}
 send(){
  const key=this.key;if(key.endsWith('.webp'))loads.push(key);
  const finish=()=>{
   if(this.aborted)return;
   const spec=Object.values(config.actions).find(s=>s.atlas===key||s.hit_mask===key);
   this.status=200;this.response=key.endsWith('.webp')?new TextEncoder().encode(key).buffer:
    masks.get(key)||new Uint8Array(badMasks.has(key)?1:2048*Math.ceil(spec.count/4)*512/8).fill(255).buffer;
   if(this.onload)this.onload();
  };
  if(gates.has(key))gates.set(key,finish);else finish();
 }
 abort(){this.aborted=true;if(this.onabort)this.onabort();}
};
global.window={RigRenderer:{alphaOf(){throw new Error('整帧不应再建立 Canvas 读回 alpha');},loadOwnTexture(){throw new Error('整帧不应再使用 URL 图片缓存');}}};
const app={stage:new Container(),renderer:{width:280,height:280,resolution:1,texture:{managedTextures:gpuBases},render(){throw new Error('不应分配叠影快照');}}};
const built={figure:new Container(),rig:{space:{view:[-12,16,268,268]}},frameBase:'pack/',frameConfig:config};app.stage.addChild(built.figure);
const base={state:{pose:'idle',dragging:false,walking:0,facing:1,dragVx:0},tick(){},set(v){Object.assign(this.state,v);},busy:()=>false,
 diagnostics:()=>({kind:'posepack'}),dispose(){this.disposed=true;},hitmap:()=>({}),probe:()=>({hit:false})};
const flush=async()=>{for(let i=0;i<32;i++)await Promise.resolve();};
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
        self.run_js('''decodeGates.set('pickup.webp',null);player=wrap(app,built,base);
player.set({dragging:true,pose:'grabbed'});await step(.02);player.dispose();
decodeGates.get('pickup.webp')();await flush();assert.ok(bitmaps.every(b=>b.closed===1));
assert.equal(diag().pending,0);assert.equal(diag().atlases,0);assert.equal(gpuBases.length,0);''')

    def test_idle_neither_prefetches_nor_decodes_unused_actions(self):
        self.run_js('''player=wrap(app,built,base);await step(5);assert.equal(loads.length,0);
player.set({walking:1});await step(2);assert.equal(diag().displayed,'walk');
assert.deepEqual(loads,['start.webp','walk.webp']);assert.equal(diag().resources.alpha_readbacks,0);
player.dispose();assert.ok(bitmaps.every(b=>b.closed===1));''')

    def test_recovery_clears_sprite_masks_gpu_and_bitmaps_then_can_reload(self):
        self.run_js('''player=wrap(app,built,base);
for(let cycle=0;cycle<8;cycle++){
 player.set({walking:1});await step(.6);assert.equal(diag().displayed,'walk');
 player.set({walking:0});await step(4);assert.equal(diag().displayed,'idle');
 assert.equal(diag().atlases,0);assert.equal(diag().pending,0);assert.equal(diag().resources.cached_texture_bytes,0);
 assert.equal(diag().resources.mask_bytes,0);assert.equal(gpuBases.length,0);
 assert.equal(app.stage.children[1].children[0].texture,Texture.EMPTY);
 assert.ok(bitmaps.every(b=>b.closed===1));
 assert.ok(bitmapResources.every(r=>r.destroyed && r.source===null));
 assert.equal(diag().resources.loaded,diag().resources.released);
 assert.equal(loads.length,(cycle+1)*3);
}
assert.deepEqual(diag().errors,[]);''')

    def test_eviction_closes_owned_bitmap_and_destroys_gpu_base(self):
        self.run_js('''player=wrap(app,built,base);player.set({walking:1});await step(.5);
player.motionEvent('travel',{distance:40,seconds:.1});await step(.1);assert.equal(diag().displayed,'run');
player.set({walking:0});await step(.3);player.set({facing:-1});await step(.1);
assert.equal(diag().displayed,'turn');assert.ok(diag().atlases<=3);assert.ok(diag().resources.released>=2);
assert.equal(gpuBases.length,diag().atlases);assert.equal(bitmaps.filter(b=>b.closed===0).length,diag().atlases);
assert.equal(diag().resources.mask_bytes,diag().resources.cached_texture_bytes/32);
player.dispose();assert.equal(gpuBases.length,0);assert.ok(bitmaps.every(b=>b.closed===1));''')

    def test_cancelled_decode_cannot_repopulate_idle_cache(self):
        self.run_js('''decodeGates.set('pickup.webp',null);player=wrap(app,built,base);
player.set({dragging:true,pose:'grabbed'});await step(.02);
player.set({dragging:false,pose:'idle'});await step(4);assert.equal(diag().displayed,'idle');
decodeGates.get('pickup.webp')();await flush();assert.equal(diag().atlases,0);assert.equal(diag().pending,0);
assert.ok(bitmaps.every(b=>b.closed===1));assert.deepEqual(diag().errors,[]);''')

    def test_cancelled_request_does_not_clear_new_request_for_same_atlas(self):
        self.run_js('''gates.set('pickup.webp',null);player=wrap(app,built,base);
player.set({dragging:true,pose:'grabbed'});await step(.02);const stale=gates.get('pickup.webp');
player.set({dragging:false,pose:'idle'});await step(.02);
player.set({dragging:true,pose:'grabbed'});await step(.02);const current=gates.get('pickup.webp');
stale();await flush();assert.equal(diag().pending,1);current();await flush();player.tick(10);
assert.equal(diag().displayed,'pickup');assert.equal(diag().displayed_index,0);assert.deepEqual(diag().errors,[]);''')

    def test_invalid_mask_is_reported_once_before_bitmap_decode(self):
        self.run_js('''badMasks.add('start.mask');player=wrap(app,built,base);player.set({walking:1});await step(.5);
assert.equal(diag().displayed,'idle');assert.equal(loads.length,1);assert.equal(bitmaps.length,0);
assert.equal(diag().errors.length,1);assert.match(diag().errors[0],/命中图尺寸/);opaque();''')

    def test_hit_mask_uses_msb_bits_and_correct_atlas_row(self):
        self.run_js('''const mask=new Uint8Array(2048*1024/8);
// walk 第 5 帧在图集第二行；其局部 x=7 是命中，x=8 是空白。
const pixel=512*2048+7;mask[pixel>>3]|=128>>(pixel&7);masks.set('walk.mask',mask.buffer);
player=wrap(app,built,base);player.set({walking:1});await step(.4);
player.motionEvent('travel',{distance:40,seconds:10});player.tick(10);
assert.equal(diag().displayed_index,4);assert.equal(player.probe(7,0).hit,true);assert.equal(player.probe(8,0).hit,false);
assert.equal(player.probe(-1,0).hit,false);assert.equal(player.probe(512,0).hit,false);''')

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
