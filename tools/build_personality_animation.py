"""补画的性格 / 日常动作与正反步态；离线编译，不改变角色人格或用户数据。"""
from __future__ import annotations
import json
import math
from pathlib import Path
from PIL import Image
from animation_inbetween import extract_keys,optical_flow,interpolate
from build_original_animation import ROOT,OUT,SIZE,FPS,digest

ART=ROOT/'art/deepseek-whale/personality-keys-v1'
LOW=ROOT/'art/deepseek-whale/locomotion-keys-v2'
ACTIONS={'smug':'叉腰得意','pout':'抱臂气鼓鼓','shy':'害羞揪袖','surprised':'惊讶后仰',
         'milk_tea':'捧奶茶偷闲','stretch':'伸懒腰','walking':'迈步行走','walking_back':'背身迈步'}


def compile_loop(keys,walking=False):
    count=24 if walking else 72
    times=[0,4,8,12,16,20,24] if walking else [0,20,25,29,35,54,72]
    pairs={};frames=[]
    for i in range(count):
        segment=next(j for j in range(6) if times[j]<=i<times[j+1])
        a,b=segment,(segment+1)%6
        t=(i-times[segment])/(times[segment+1]-times[segment])
        if t==0:frame=keys[a].copy()
        else:
            if (a,b) not in pairs:
                pairs[a,b]=(optical_flow(keys[a],keys[b],max_displacement=48 if walking else 20),
                            optical_flow(keys[b],keys[a],max_displacement=48 if walking else 20))
            # 步态匀速推进；表情使用缓入缓出，保留眨眼短停顿。
            amount=t if walking else t*t*(3-2*t)
            frame=interpolate(keys[a],keys[b],amount,*pairs[a,b],single_cel=walking)
        frames.append(frame)
    return frames,{'frame_keys':times,'walking':walking,'gait_cycle_distance':32 if walking else None,'interpolation':'single_cel_flow_warp' if walking else 'bidirectional_flow_blend'}


def save_action(key,name,frames):
    columns=8;rows=math.ceil(len(frames)/columns)
    atlas=Image.new('RGBA',(SIZE*columns,SIZE*rows))
    for i,frame in enumerate(frames):atlas.paste(frame,(i%columns*SIZE,i//columns*SIZE))
    path=OUT/(key+'.webp');atlas.save(path,'WEBP',quality=92,method=6,alpha_quality=100)
    return {'image':path.name,'name':name,'frameWidth':SIZE,'frameHeight':SIZE,'columns':columns,
            'rows':rows,'frameCount':len(frames),'fps':FPS,'facing':'right'}


def preview(key,frames):
    folder=ROOT/'output/personality-animation';folder.mkdir(parents=True,exist_ok=True)
    rgb=[]
    for frame in frames:
        bg=Image.new('RGB',frame.size,'#edf2fc');bg.paste(frame,mask=frame.getchannel('A'));rgb.append(bg)
    rgb[0].save(folder/(key+'.gif'),save_all=True,append_images=rgb[1:],duration=42,loop=0,optimize=True)


def main(only=None):
    path=OUT/'animations.json';manifest=json.loads(path.read_text(encoding='utf-8'))
    source_path=OUT/'SOURCE.json';source=json.loads(source_path.read_text(encoding='utf-8'))
    alignment={};posters=[]
    for key,name in ACTIONS.items():
        if only is not None and key not in only:continue
        walking=key.startswith('walking');origin=LOW if walking else ART;sheet=origin/(key+'-sheet.png')
        keys,report=extract_keys(sheet,SIZE,horizontal_anchor='head' if walking else 'feet')
        folder=origin/key;folder.mkdir(exist_ok=True)
        for i,frame in enumerate(keys):frame.save(folder/f'{i:02}.png')
        frames,timing=compile_loop(keys,walking)
        if walking:
            from low_step_walk import low_step_frames
            frames,timing['leg_motion']=low_step_frames(frames)
        manifest['actions'][key]=save_action(key,name,frames)
        if walking:manifest['actions'][key]['cycleDistance']=32
        manifest['poses'][key]=key;alignment[key]=report
        source['sources'][key]={'kind':'imagegen_keys_and_low_step_layer_motion' if walking else 'imagegen_keys_with_bidirectional_interpolation','source':sheet.relative_to(ROOT).as_posix(),
            'source_sha256':digest(sheet),'keyframes':6,'new_drawing':True,'alignment':report,**timing}
        preview(key,frames);posters.append((key,name,keys[0]))
        print('新增动作',key,len(frames),'帧',flush=True)
    for key,entry in manifest['actions'].items():
        entry.pop('blendGroup',None)
        if key in ('idle','talking','thinking','smug','pout','shy','surprised','patted','wave'):entry['blendGroup']='standing'
    manifest['poses']['poked']='pout'  # 轻戳用新抱臂表情；旧原画仍保留右键点播。
    source['newly_drawn_keys']=18+6*len(ACTIONS)
    source['files']=[{'file':entry['image'],'bytes':(OUT/entry['image']).stat().st_size,'sha256':digest(OUT/entry['image'])}
                     for entry in manifest['actions'].values()]
    source['personality_keys_mode']='built-in image_gen'
    source_path.write_text(json.dumps(source,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for folder,keys in ((ART,[k for k in ACTIONS if not k.startswith('walking')]),(LOW,['walking','walking_back'])):
        records={k:source['sources'][k]['alignment'] for k in keys}
        (folder/'alignment.json').write_text(json.dumps(records,indent=2)+'\n',encoding='utf-8')
    # 静态对照和各动作循环由同一编译结果生成，方便核对原图和动画差异。
    posters=[]
    for key,name in ACTIONS.items():
        with Image.open(OUT/manifest['actions'][key]['image']) as image:posters.append((key,name,image.crop((0,0,SIZE,SIZE)).convert('RGBA')))
    board=Image.new('RGB',(SIZE*4,SIZE*2),'#edf2fc')
    for i,(_,_,frame) in enumerate(posters):board.paste(frame,(i%4*SIZE,i//4*SIZE),frame.getchannel('A'))
    board.save(ROOT/'output/personality-animation/actions-preview.png')
    from build_pose_transitions import main as transitions
    transitions()


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--only',nargs='+',choices=list(ACTIONS))
    main(parser.parse_args().only)
