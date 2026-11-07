/* 已批准立绘的轻量网格动画；该格式不是 Cubism，也不会生成假的 moc3。 */
(function () {
    'use strict';
    const vertex = `
        precision highp float;
        attribute vec2 aVertexPosition;
        attribute vec2 aTextureCoord;
        uniform mat3 translationMatrix;
        uniform mat3 projectionMatrix;
        varying vec2 vUV;
        void main(){
            vUV=aTextureCoord;
            gl_Position=vec4((projectionMatrix*translationMatrix*vec3(aVertexPosition,1.0)).xy,0.0,1.0);
        }`;
    const fragment = `
        precision mediump float;
        varying vec2 vUV;
        uniform sampler2D uBase;
        uniform sampler2D uBlinkTexture;
        uniform sampler2D uTalkTexture;
        uniform vec2 uSize;
        uniform vec4 uEyes;
        uniform vec4 uMouthRect;
        uniform float uBlink;
        uniform float uMouth;
        uniform vec4 uMood;
        float patch(vec2 p,vec4 rect,float feather){
            vec2 edge=abs(p-rect.xy)-rect.zw;
            return 1.0-smoothstep(-feather,feather,max(edge.x,edge.y));
        }
        void main(){
            vec2 p=vUV*uSize;
            vec2 uv=vUV;
            // 眉部局部 UV 变形，与整张立绘的呼吸网格分别处理。
            float brow=patch(p,vec4(518.0,171.0,66.0,10.0),5.0);
            uv.y+=(uMood.z*abs(p.x-518.0)*0.09-uMood.y*2.0)*brow/uSize.y;
            vec4 color=texture2D(uBase,uv);
            vec4 closed=texture2D(uBlinkTexture,vUV);
            color=mix(color,closed,patch(p,uEyes,4.0)*uBlink);
            // 缩放开口纹理的局部 UV，让音量不仅控制淡入，还控制嘴部开合高度。
            vec2 talkPoint=p-uMouthRect.xy;
            talkPoint.y/=mix(0.32,1.0,uMouth);
            vec4 talk=texture2D(uTalkTexture,(talkPoint+uMouthRect.xy)/uSize);
            color=mix(color,talk,patch(p,uMouthRect,4.0)*smoothstep(0.02,0.18,uMouth));
            float cheek=exp(-dot((p-vec2(474.0,218.0))/vec2(20.0,10.0),(p-vec2(474.0,218.0))/vec2(20.0,10.0)));
            cheek+=exp(-dot((p-vec2(565.0,216.0))/vec2(20.0,10.0),(p-vec2(565.0,216.0))/vec2(20.0,10.0)));
            color.rgb=mix(color.rgb,vec3(1.0,0.48,0.58)*color.a,cheek*uMood.w*0.24);
            // 自定义 Mesh 使用预乘混合；透明像素的隐藏白色 RGB 不能参与桌面合成。
            color.rgb*=color.a;
            gl_FragColor=color;
        }`;

    function readJSON(url) {
        // Qt 本地页面使用 XHR 读取 file URL，避免 fetch 对本地文件的协议限制。
        return new Promise((resolve,reject)=>{
            const request=new XMLHttpRequest(); request.open('GET',url);
            request.onload=()=>{
                try {
                    if(request.status!==0&&request.status!==200)throw new Error('动画配置读取失败');
                    resolve(JSON.parse(request.responseText));
                } catch(error){reject(error);}
            };
            request.onerror=()=>reject(new Error('动画配置读取失败')); request.send();
        });
    }
    const ease=(a,b,value)=>{const t=Math.max(0,Math.min(1,(value-a)/(b-a)));return t*t*(3-2*t);};

    window.EmiliaRig={async create(url,app,getState){
        const config=await readJSON(url);
        if(config.Format!=='AICompanionAnimatedIllustration'||config.Version!==1)throw new Error('不支持的立绘动画格式');
        const [width,height]=config.Canvas;
        const textures=[];
        for(const key of ['Base','Blink','Talk']){
            const name=config.Textures[key];
            if(typeof name!=='string'||/[\\:%?#]/.test(name)||name.includes('..'))throw new Error('动画纹理必须位于本地素材目录');
            const texture=await PIXI.Texture.fromURL(new URL(name,url).href);
            if(texture.width!==width||texture.height!==height)throw new Error('动画纹理尺寸不一致');
            textures.push(texture);
        }
        const geometry=new PIXI.PlaneGeometry(width,height,33,49);
        const buffer=geometry.getBuffer('aVertexPosition'); const rest=new Float32Array(buffer.data);
        const uniforms={uBase:textures[0],uBlinkTexture:textures[1],uTalkTexture:textures[2],
            uSize:new Float32Array([width,height]),uEyes:new Float32Array(config.Patches.Eyes),
            uMouthRect:new Float32Array(config.Patches.Mouth),uBlink:0,uMouth:0,uMood:new Float32Array(4)};
        const shader=PIXI.Shader.from(vertex,fragment,uniforms);
        const mesh=new PIXI.Mesh(geometry,shader); mesh.position.set(-width/2,-height/2);
        const rig=new PIXI.Container(); rig.addChild(mesh); rig.sourceSize={width,height};
        let time=0,nextBlink=1.8,blinkStart=-10,seed=71,gazeX=0,gazeY=0;
        let open=0,frame=0,override=null,maxDisplacement=0,blinkCount=0;
        let action=null,actionStarted=0,actionCount=0,nextGesture=15,previousMood='calm';
        const random=()=>{seed=(seed*16807)%2147483647;return seed/2147483647;};
        rig.setGaze=(x,y)=>{gazeX=Math.max(-1,Math.min(1,x));gazeY=Math.max(-1,Math.min(1,y));};
        // 仅供离线验收固定表情和时间，不经聊天模型调用。
        rig.setTestState=value=>{override=value;};
        rig.playAction=name=>{
            if(!['greet','nod','shake','tilt','shy','wave'].includes(name))return false;
            action=name;actionStarted=time;actionCount++;return true;
        };
        const update=()=>{
            const dt=Math.min(.05,app.ticker.deltaMS/1000); frame++;
            if(!override?.freeze)time+=dt;
            const state=getState(); const mood=override?.mood??state.mood;
            const desired=override?.mouth??state.mouth;
            open+=(Math.max(0,Math.min(1,desired))-open)*Math.min(1,dt*22);
            if(time>=nextBlink){blinkStart=time;nextBlink=time+3+random()*2.5;blinkCount++;}
            const age=time-blinkStart;
            let blink=age<.07?ease(0,.07,age):age<.11?1:age<.24?1-ease(.11,.24,age):0;
            if(override?.blink!==undefined)blink=override.blink;
            uniforms.uBlink=blink; uniforms.uMouth=open;
            const target=[mood==='happy'?1:0,mood==='sad'?1:0,mood==='angry'?1:0,mood==='shy'?1:0];
            for(let i=0;i<4;i++)uniforms.uMood[i]+=(target[i]-uniforms.uMood[i])*Math.min(1,dt*7);
            if(!override?.freeze){
                if(mood!==previousMood&&mood==='shy')rig.playAction('shy');
                previousMood=mood;
                if(time>=nextGesture){
                    nextGesture=time+15+random()*12;
                    if(!action&&open<.05)rig.playAction(random()<.5?'tilt':'nod');
                }
            }
            const progress=action?Math.min(1,(time-actionStarted)/2.4):0;
            const envelope=Math.sin(progress*Math.PI)**2;
            let actionAngle=0,nod=0,shake=0,wave=0;
            if(action==='greet')nod=envelope*10;
            if(action==='nod')nod=Math.sin(progress*Math.PI*2)**2*envelope*8;
            if(action==='shake')shake=Math.sin(progress*Math.PI*4)*envelope*8;
            if(action==='tilt')actionAngle=envelope*.085;
            if(action==='shy'){actionAngle=-envelope*.055;nod=envelope*6;}
            if(action==='wave')wave=Math.sin(progress*Math.PI*6)*envelope*.045;
            const angle=Math.sin(time*.65)*.018+gazeX*.018+uniforms.uMood[1]*.025-uniforms.uMood[3]*.022+actionAngle;
            const sin=Math.sin(angle),cos=Math.cos(angle); maxDisplacement=0;
            for(let i=0;i<rest.length;i+=2){
                const x=rest[i],y=rest[i+1];
                const head=1-ease(250,405,y);
                const dx=x-520,dy=y-285;
                const body=(1-ease(780,1490,y))*ease(160,440,y);
                const breath=Math.sin(time*1.65)*2.2*body;
                // 发梢仅在身体两侧施加轻微滞后摆动，不移动脚部锚点。
                const hair=ease(55,180,Math.abs(x-520))*ease(250,460,y)*(1-ease(1050,1290,y));
                // 肩部到袖口的柔和权重只做轻摆手，不伪造缺少遮挡补画的大幅抬臂。
                const arm=ease(590,750,x)*ease(350,620,y)*(1-ease(830,1020,y));
                const shiftX=(dx*cos-dy*sin-dx)*head+Math.sin(time*.58)*2.8*body+Math.sin(time*1.15-y*.004)*4.0*hair+shake*head-(y-360)*wave*arm;
                const shiftY=(dx*sin+dy*cos-dy)*head+breath+gazeY*2.0*head+nod*head+(x-610)*wave*arm;
                buffer.data[i]=x+shiftX; buffer.data[i+1]=y+shiftY;
                maxDisplacement=Math.max(maxDisplacement,Math.abs(shiftX),Math.abs(shiftY));
            }
            buffer.update();
            rig.animationState={frames:frame,time,mouth:open,blink,blinkCount,cheek:uniforms.uMood[3],
                maxDisplacement,engine:'pixi-mesh-not-cubism',vertices:rest.length/2,action,actionCount,actionProgress:progress};
            if(progress>=1)action=null;
        };
        app.ticker.add(update); update();rig.playAction('greet');
        const destroy=rig.destroy.bind(rig);
        rig.destroy=()=>{app.ticker.remove(update);destroy({children:true});geometry.destroy();shader.destroy();};
        return rig;
    }};
})();
