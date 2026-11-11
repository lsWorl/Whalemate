/* T24 整帧序列：用画好的过渡帧衔接，任意时刻只显示一个不透明角色。 */
(function (root) {
  'use strict';
  const sign = x => x < 0 ? -1 : 1;
  const clamp = (x,a,b) => Math.max(a,Math.min(b,x));
  const INJURY = ['land','dizzy','recover'];
  const AIR = ['pickup','drag_left','drag_right','release','fall'];

  class Controller {
    constructor(config) {
      this.config=config; this.state='idle'; this.age=0; this.phase=0; this.distance=0; this.speed=0;
      this.held=false; this.airborne=false; this.walking=0; this.facing=1; this.targetFacing=1;
      this.dragDirection=-1; this.lastMirror=false; this.hard=false; this.ignoreGrab=false; this.ignoreFall=false;
      this.turnTo=1;
      this.dragTurn=null;
      this.history=['idle']; this.serial=0;
    }
    enter(state) {
      if (state===this.state) return;
      this.state=state; this.age=0; this.serial++; this.history.push(state);
      if(state==='turn')this.turnTo=this.targetFacing;
      if(!state.startsWith('drag_'))this.dragTurn=null;
      if (this.history.length>40) this.history.shift();
    }
    spec() { return this.config.actions[this.state] || null; }
    duration() { const c=this.spec(); return c ? (c.hold_seconds || c.count/c.fps) : 0; }
    mirrored() {
      if (this.state==='drag_right') return true;
      if (this.state==='drag_left') return false;
      if (this.state==='turn') return this.turnTo===1;
      if (['start','walk','run','brake'].includes(this.state)) return this.facing<0;
      return this.lastMirror;
    }
    frame() {
      const c=this.spec(); if (!c) return 0;
      if(this.dragTurn && this.state.startsWith('drag_'))return c.center_index ?? 1;
      if (['walk','run'].includes(this.state)) return Math.min(c.count-1,Math.floor(this.phase*c.count));
      const frame=Math.floor(this.age*c.fps);
      return c.loop ? frame%c.count : Math.min(c.count-1,frame);
    }
    travel(distanceRig,seconds) {
      if (![distanceRig,seconds].every(Number.isFinite) || seconds<=0) return;
      const source=Math.abs(distanceRig)/this.config.source_scale;
      this.distance+=source;
      // 像素取整会把一小步抖成 1/2px；用短时间平滑速度，避免正常走路反复切跑步。
      const instant=Math.abs(distanceRig)/seconds;
      this.speed+=(instant-this.speed)*(1-Math.exp(-Math.min(seconds,1)/.12));
      const c=this.config.actions[this.state==='run'?'run':'walk'];
      if (c && c.stride_source>0) this.phase=(this.phase+source/c.stride_source)%1;
    }
    event(name,data={}) {
      if (name==='drag') {
        const active=!!data.active;
        if (active && !this.held) {
          this.held=true;this.airborne=false;this.ignoreGrab=false;this.ignoreFall=true;this.enter('pickup');
        } else if (!active && this.held) {
          this.held=false;this.ignoreGrab=true;this.enter('release');
        }
      } else if (name==='fall') {
        this.airborne=!!data.active;this.held=false;this.ignoreGrab=true;this.ignoreFall=false;
        if (data.active && !['release','fall'].includes(this.state)) this.enter('release');
      } else if (name==='land') {
        this.airborne=false;this.held=false;this.ignoreGrab=true;this.ignoreFall=true;
        this.hard=!!data.hard;this.enter('land');
      }
    }
    update(host) {
      const grab=!!host.dragging || (host.pose==='grabbed' && !this.ignoreGrab);
      if (host.pose!=='grabbed' && !host.dragging) this.ignoreGrab=false;
      if (host.pose!=='falling') this.ignoreFall=false;
      if (grab && !this.held) this.event('drag',{active:true});
      else if (!grab && this.held) this.event('drag',{active:false});
      if (Number.isFinite(host.dragVx) && Math.abs(host.dragVx)>35) this.dragDirection=sign(host.dragVx);
      if (this.held) {
        if (this.state.startsWith('drag_')) {
          const target=this.dragDirection<0?'drag_left':'drag_right';
          if(target!==(this.dragTurn?.target || this.state))this.dragTurn={target,age:0,switched:false};
          this.lastMirror=this.state==='drag_right';
        } else this.lastMirror=this.dragDirection>0;
      }
      if (host.pose==='falling' && !this.ignoreFall && !this.airborne && !INJURY.includes(this.state)) this.event('fall',{active:true});
      this.walking=Math.sign(host.walking || 0);this.targetFacing=sign(host.facing || this.targetFacing);
      if (this.held || this.airborne || AIR.includes(this.state) || INJURY.includes(this.state)) return;
      if (!['idle','talking','falling','grabbed','dizzy'].includes(host.pose || 'idle')) {this.enter('idle');return;}
      if (this.state!=='turn' && this.facing!==this.targetFacing) {this.enter('turn');return;}
      if (this.state==='turn') return;
      if (this.walking && this.state==='idle') this.enter('start');
      if (!this.walking && ['start','walk','run'].includes(this.state)) this.enter('brake');
      if (this.walking && this.state==='walk' && this.speed>=this.config.run_enter_speed) this.enter('run');
      if (this.walking && this.state==='run' && this.speed<this.config.run_exit_speed) this.enter('walk');
      if (['start','walk','run','brake'].includes(this.state)) this.lastMirror=this.facing<0;
    }
    tick(seconds) {
      const dt=clamp(Number.isFinite(seconds)?seconds:0,0,.05);this.age+=dt;
      if(this.dragTurn) {
        const turn=this.dragTurn,hold=clamp((this.config.direction_change_ms || 70)/1000,.06,.08);
        turn.age+=dt;
        if(!turn.switched && turn.age>=hold) {this.enter(turn.target);turn.switched=true;}
        if(turn.age>=hold*2) {this.dragTurn=null;this.age=((this.spec().center_index ?? 1)+1)/this.spec().fps;}
        return;
      }
      const c=this.spec();if (!c || this.age<this.duration()) return;
      let next=null;
      switch(this.state) {
        case 'pickup':next=this.held?(this.dragDirection<0?'drag_left':'drag_right'):'release';break;
        case 'release':next=this.airborne?'fall':'recover';break;
        case 'land':next=this.hard?'dizzy':'recover';break;
        case 'dizzy':next='recover';break;
        case 'recover':next=this.walking?'start':'idle';break;
        case 'start':next=this.walking?(this.speed>=this.config.run_enter_speed?'run':'walk'):'brake';break;
        case 'brake':next=this.walking?'start':'idle';break;
        case 'turn':this.facing=this.turnTo;this.lastMirror=this.facing<0;next=this.walking?'start':'idle';break;
      }
      if (next) this.enter(next);
    }
    diagnostics() {
      return {type:'full-frame-sequence',state:this.state,age:this.age,index:this.frame(),count:this.spec()?.count || 0,
        phase:this.phase,distance:this.distance,speed:this.speed,mirrored:this.mirrored(),history:this.history.slice(),
        pivot:AIR.includes(this.state)?this.config.grab_pivot:this.config.anchor,extra_swing:0,
        direction_bridge:this.dragTurn?{...this.dragTurn}:null};
    }
  }

  function wrap(app, built, base) {
    const config=built.frameConfig, controller=new Controller(config), cache=new Map(), pending=new Map();
    const layer=new PIXI.Container(), sprite=new PIXI.Sprite(PIXI.Texture.EMPTY);
    layer.addChild(sprite);layer.visible=false;app.stage.addChild(layer);
    const errors=[],failed=new Set();let disposed=false, displayed='idle', displayMirror=false, displayedIndex=0, switches=0;
    const dimensions=()=>[app.renderer.width/app.renderer.resolution,app.renderer.height/app.renderer.resolution];
    function atlasKey(action) {return config.actions[action]?.atlas || null;}
    function trim() {
      const keep=atlasKey(displayed);
      while(cache.size>3) {
        const candidate=[...cache.keys()].find(k=>k!==keep && k!==atlasKey(controller.state));
        if (!candidate) break;
        const item=cache.get(candidate);cache.delete(candidate);
        item.frames.forEach(t=>t.destroy(false));item.texture.destroy(true);
      }
    }
    function ensure(action) {
      const key=atlasKey(action);if(!key || failed.has(key))return Promise.resolve(null);
      if(cache.has(key)) {const item=cache.get(key);cache.delete(key);cache.set(key,item);return Promise.resolve(item);}
      if(pending.has(key))return pending.get(key);
      const promise=window.RigRenderer.loadOwnTexture(built.frameBase+key).then(texture=>{
        if(disposed){pending.delete(key);texture.destroy(true);return null;}
        const spec=config.actions[action], cell=config.atlas_cell;
        if(texture.width!==config.atlas_columns*cell || texture.height!==Math.ceil(spec.count/config.atlas_columns)*cell){
          texture.destroy(true);throw new Error('整帧图集尺寸错误：'+key);
        }
        const alpha=window.RigRenderer.alphaOf(texture), frames=[];
        for(let i=0;i<spec.count;i++)frames.push(new PIXI.Texture(texture.baseTexture,new PIXI.Rectangle((i%config.atlas_columns)*cell,Math.floor(i/config.atlas_columns)*cell,cell,cell)));
        const item={texture,alpha,frames,width:texture.width};cache.set(key,item);pending.delete(key);trim();return item;
      }).catch(error=>{pending.delete(key);if(!disposed){failed.add(key);errors.push(String(error));}return null;});
      pending.set(key,promise);return promise;
    }
    function switchTo(action) {
      // pickup/release/land/start/brake 自身就是绘制的过渡，不能再把两张整帧透明叠放。
      // 新图集未就绪时保留当前不透明画帧；就绪后从过渡首帧接上，距离相位不归零。
      displayed=action;switches++;
      built.figure.visible=action==='idle';built.figure.alpha=1;
      layer.visible=action!=='idle';layer.alpha=1;
      const next={pickup:'drag_left',release:'fall',fall:'land',land:'dizzy',dizzy:'recover',recover:'start',start:'walk',walk:'run',run:'brake',brake:'turn'}[action];
      if(next)ensure(next);trim();
    }
    function layout() {
      const [w,h]=dimensions(), view=built.rig.space.view;
      const scale=Math.min(h*.92/(view[3]-view[1]),w*.86/(view[2]-view[0]))*config.source_scale;
      const pivot=AIR.includes(displayed)?config.grab_pivot:config.anchor;
      const flip=displayMirror?-1:1;
      layer.pivot.set(pivot[0],pivot[1]);layer.position.set(w/2+(pivot[0]-config.anchor[0])*scale*flip,h-2+(pivot[1]-config.anchor[1])*scale);
      layer.scale.set(scale*flip,scale);layer.rotation=0;
      sprite.scale.set(config.canvas[0]/config.atlas_cell,config.canvas[1]/config.atlas_cell);
    }
    function tick(ms) {
      if(disposed)return;
      const dt=clamp(ms/1000,0,.05);base.tick(ms);controller.update(base.state);
      const desired=controller.state,key=atlasKey(desired);
      if(desired==='idle' || cache.has(key)) {
        if(desired!==displayed)switchTo(desired);
        displayMirror=controller.mirrored();
        displayedIndex=controller.frame();
        if(displayed!=='idle')sprite.texture=cache.get(key).frames[displayedIndex];
        // 先画本帧，再推进时钟；即使打断发生在加载中也不会跳过动作首帧。
        controller.tick(dt);
      } else ensure(desired);
      if(displayed!=='idle')layout();
    }
    function anchors() {
      const [w,h]=dimensions();const p=layer.toGlobal(new PIXI.Point(config.anchor[0],130));
      return {head:[p.x,p.y],feet:[w/2,h-2]};
    }
    function probe(x,y) {
      if(displayed==='idle')return base.probe(x,y);
      const item=cache.get(atlasKey(displayed));let hit=false;
      if(item) {
        const p=sprite.toLocal(new PIXI.Point(x,y)),cell=config.atlas_cell;
        const index=Math.max(0,item.frames.indexOf(sprite.texture));
        const px=Math.floor(p.x),py=Math.floor(p.y);
        if(px>=0&&py>=0&&px<cell&&py<cell)hit=!item.alpha||item.alpha.alpha[(Math.floor(index/config.atlas_columns)*cell+py)*item.width+(index%config.atlas_columns)*cell+px]>24;
      }
      return {hit,...anchors()};
    }
    ensure('pickup');ensure('start');
    return {...base,tick,probe,
      hitmap(){const result=base.hitmap();return displayed==='idle'?result:{...result,...anchors()};},
      busy:()=>displayed!=='idle'||pending.size>0||base.busy(),
      motionEvent(name,data){
        if(name==='travel'){
          const [w,h]=dimensions(),v=built.rig.space.view,scale=Math.min(h*.92/(v[3]-v[1]),w*.86/(v[2]-v[0]));
          controller.travel(data.distance/scale,data.seconds);
        }else controller.event(name,data);
      },
      diagnostics:()=>({...base.diagnostics(),motion:{...controller.diagnostics(),displayed,ready:displayed==='idle'||cache.has(atlasKey(displayed)),
        displayed_index:displayedIndex,displayed_mirrored:displayMirror,transition_mode:'opaque-keyframes',crossfade_ms:0,fading:false,
        visible_character_layers:Number(layer.visible && layer.alpha>0)+Number(built.figure.visible && built.figure.alpha>0),
        layer_alpha:layer.visible?layer.alpha:built.figure.alpha,switches,atlases:cache.size,pending:pending.size,errors:errors.slice()}}),
      dispose(){disposed=true;layer.destroy({children:true});for(const item of cache.values()){item.frames.forEach(t=>t.destroy(false));item.texture.destroy(true);}cache.clear();pending.clear();base.dispose();},
    };
  }
  root.FullFrameActions={Controller,wrap};
  if (typeof module!=='undefined') module.exports=root.FullFrameActions;
})(typeof window!=='undefined'?window:globalThis);
