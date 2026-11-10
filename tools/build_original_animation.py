"""把本项目原鲸娘原画和新绘关键帧编译为透明 WebP 动画；全程离线。"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import numpy as np
from PIL import Image
from animation_inbetween import extract_keys,optical_flow,interpolate,rgba_float,rgba_image,sample

ROOT=Path(__file__).resolve().parents[1]
ART=ROOT/'art/deepseek-whale'
KEYS=ART/'animation-keys-v1'
OUT=ROOT/'src/aicompanion/characters/deepseek-whale/animation-original'
SIZE=384
FPS=24
NAMES={'idle':'待机呼吸','talking':'自然说话','thinking':'托腮思考','working':'敲键盘','wait':'等待确认',
       'celebrate':'举手庆祝','sleepy':'抱鲸犯困','error':'出错发晕','grabbed':'被拎起来','poked':'气鼓鼓',
       'patted':'开心眯眼','walking':'轻步走动','wave':'举手回应'}


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def key_loop(pose):
    frames,alignment=extract_keys(KEYS/(pose+'-sheet.png'),SIZE)
    folder=KEYS/pose;folder.mkdir(exist_ok=True)
    for i,frame in enumerate(frames):frame.save(folder/f'{i:02}.png')
    if pose=='talking':
        times=[(0,0),(.28,1),(.52,2),(.92,3),(1.2,4),(1.65,5),(2.,0)]
        count=48
    else:
        times=[(0,0),(1.6,1),(1.7,1),(1.76,2),(1.82,3),(1.9,4),(3.,5),(4.,0)]
        count=96
    flows={};result=[]
    for i in range(count):
        now=i/FPS
        for (start,a),(end,b) in zip(times,times[1:]):
            if start<=now<end:
                if a==b:frame=frames[a].copy()
                else:
                    if (a,b) not in flows:
                        flows[a,b]=(optical_flow(frames[a],frames[b]),optical_flow(frames[b],frames[a]))
                    t=(now-start)/(end-start);t=t*t*(3-2*t)
                    frame=interpolate(frames[a],frames[b],t,*flows[a,b])
                result.append(frame);break
    return result,{'kind':'imagegen_keys_with_bidirectional_interpolation','source':(KEYS/(pose+'-sheet.png')).relative_to(ROOT).as_posix(),
        'source_sha256':digest(KEYS/(pose+'-sheet.png')),'original_reference':f'art/deepseek-whale/originals/{pose}.png',
        'original_reference_sha256':digest(ART/'originals'/(pose+'.png')),'keyframes':6,'timeline':times,'alignment':alignment}


def existing_loop(pose):
    source={'patted':'idle_eyes_closed','walking':'idle','wave':'celebrate'}.get(pose,pose)
    path=ART/'originals'/(source+'.png')
    image=Image.open(path).convert('RGBA').resize((SIZE,SIZE),Image.Resampling.LANCZOS)
    raw=rgba_float(image);yy,xx=np.mgrid[:SIZE,:SIZE].astype(np.float32);x=xx/SIZE;y=yy/SIZE
    upper=np.clip((.922-y)/.7,0,1);edge=np.clip((abs(x-.5)-.11)/.26,0,1)
    result=[]
    for i in range(72):
        phase=i/72*math.tau
        strength=.5 if pose=='sleepy' else 1.
        dx=strength*(.8*math.sin(phase)*upper+.7*math.sin(phase-.65)*edge*np.clip((y-.3)/.6,0,1))
        dy=strength*.8*math.sin(phase)*upper
        if pose=='poked':dx+=1.2*math.sin(phase*2)*upper
        if pose=='walking':dy+=np.sin(phase*2+np.where(x<.5,0,math.pi))*np.clip((y-.7)/.2,0,1)*.9
        result.append(rgba_image(sample(raw,dx,dy)))
    return result,{'kind':'original_art_with_small_continuous_warp','source':path.relative_to(ROOT).as_posix(),
                   'source_sha256':digest(path),'new_drawing':False,'max_displacement_px':3.5}


def save_action(pose,frames):
    columns=8;rows=math.ceil(len(frames)/columns)
    sheet=Image.new('RGBA',(columns*SIZE,rows*SIZE))
    for i,frame in enumerate(frames):sheet.paste(frame,(i%columns*SIZE,i//columns*SIZE))
    path=OUT/(pose+'.webp');sheet.save(path,'WEBP',quality=92,method=6,alpha_quality=100)
    return {'image':path.name,'name':NAMES[pose],'frameWidth':SIZE,'frameHeight':SIZE,'columns':columns,
        'rows':rows,'frameCount':len(frames),'fps':FPS,'facing':'right'}, {'file':path.name,'bytes':path.stat().st_size,'sha256':digest(path)}


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    actions={};sources={};files=[]
    preview=ROOT/'output/original-animation';preview.mkdir(parents=True,exist_ok=True)
    for pose in NAMES:
        print('编译',pose,flush=True)
        frames,record=key_loop(pose) if pose in ('idle','talking','thinking') else existing_loop(pose)
        actions[pose],file=save_action(pose,frames);files.append(file);sources[pose]=record
        if pose=='idle':frames[0].save(OUT/'poster.png')
        if pose in ('idle','talking','thinking'):
            rgb=[]
            for frame in frames:
                bg=Image.new('RGB',frame.size,'#edf2fc');bg.paste(frame,mask=frame.getchannel('A'));rgb.append(bg)
            rgb[0].save(preview/(pose+'.gif'),save_all=True,append_images=rgb[1:],duration=[42,42,41]*math.ceil(len(rgb)/3),loop=0,optimize=True)
            frames[len(frames)//3].save(preview/(pose+'-mid.png'))
    poses={p:p for p in NAMES};poses.update(falling='grabbed',dizzy='error')
    manifest={'format':'AICompanionAnimationPack','version':1,'size':[SIZE,SIZE],'poster':'poster.png',
        'actions':actions,'poses':poses,'head':[.25,.16,.5,.42]}
    (OUT/'animations.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    provenance={'format':'AICompanionOriginalAnimationSource','version':1,
        'character_reference':'art/deepseek-whale/approved-poses-preview.png',
        'foreign_images_included':False,'foreign_audio_included':False,
        'new_art_mode':'built-in image_gen','newly_drawn_keys':18,
        'interpolation':'offline bidirectional pyramidal Horn-Schunck, premultiplied alpha',
        'runtime_dependencies_added':False,'build_numpy_version':np.__version__,
        'sources':sources,'files':files}
    (OUT/'SOURCE.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (KEYS/'alignment.json').write_text(json.dumps({p:sources[p]['alignment'] for p in ('idle','talking','thinking')},indent=2)+'\n',encoding='utf-8')
    print('13 动作编译完成；18 张新绘关键帧，其他沿用原画。',flush=True)
    if (ROOT/'art/deepseek-whale/personality-keys-v1/smug-sheet.png').is_file():
        from build_personality_animation import main as build_personality
        build_personality()
    elif (ROOT/'art/deepseek-whale/transition-keys-v1/working-sheet.png').is_file():
        from build_pose_transitions import main as build_transitions
        build_transitions()


if __name__=='__main__':main()
