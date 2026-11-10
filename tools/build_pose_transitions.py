"""编译原鲸娘动作之间的一次性过渡；新画的站坐关键帧与小幅光流分别记来源。"""
from __future__ import annotations
import hashlib
import json
import math
import re
from pathlib import Path
import numpy as np
from PIL import Image,ImageFilter
from animation_inbetween import components,optical_flow,interpolate
from pose_bridge import prepare,pose_interpolate

ROOT=Path(__file__).resolve().parents[1]
ART=ROOT/'art/deepseek-whale/transition-keys-v1'
ART2=ROOT/'art/deepseek-whale/transition-keys-v2'
OUT=ROOT/'src/aicompanion/characters/deepseek-whale/animation-original'
SIZE=384;FPS=24


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def painted_keys(pose,art=ART):
    image=Image.open(art/(pose+'-sheet.png')).convert('RGBA')
    parts=[item for item in components(np.asarray(image)[:,:,3]>40) if item[1]>1000]
    if len(parts)!=4:raise ValueError('站坐过渡必须包含四个完整独立主体。')
    parts.sort(key=lambda p:((p[0][1]+p[0][3])/2>image.height/2,(p[0][0]+p[0][2])/2))
    # 全部使用同一尺度，坐下后头会降低，不把坐姿重新放大到与站姿等高。
    scale=SIZE*.835/max(item[0][3]-item[0][1] for item in parts)
    frames=[];records=[];folder=art/pose;folder.mkdir(exist_ok=True)
    for i,(box,area,mask) in enumerate(parts):
        if box[0]<2 or box[1]<2 or box[2]>image.width-2 or box[3]>image.height-2:raise ValueError('中间姿势碰到图纸边缘。')
        keep=np.asarray(Image.fromarray(mask).filter(ImageFilter.MaxFilter(5)))
        rgba=np.array(image);rgba[:,:,3]=(rgba[:,:,3].astype(np.uint16)*keep//255).astype(np.uint8)
        tile=Image.fromarray(rgba).crop(box)
        a=np.asarray(tile)[:,:,3];ys,xs=np.where(a>80);feet=xs[ys>=ys.max()-max(2,round(a.shape[0]*.025))]
        anchor=(float(feet.min()+feet.max())/2,float(ys.max()))
        tile=tile.resize((round(tile.width*scale),round(tile.height*scale)),Image.Resampling.LANCZOS)
        x=round(SIZE*.5-anchor[0]*scale);y=round(SIZE*.922-anchor[1]*scale)
        if x<2 or y<2 or x+tile.width>SIZE-2 or y+tile.height>SIZE-2:raise ValueError('归一化中间帧越界。')
        frame=Image.new('RGBA',(SIZE,SIZE));frame.alpha_composite(tile,(x,y));frame.save(folder/f'{i:02}.png')
        frames.append(frame);records.append({'source_box':box,'placement':[x,y],'scale':scale,'foot_anchor':anchor})
    return frames,records


def first_frame(entry):
    name=entry['image'];path=OUT/name
    if Path(name).name!=name or ':' in name or '\\' in name or path.resolve().parent!=OUT.resolve():
        raise ValueError('端点必须是运行素材同目录内的文件。')
    with Image.open(path) as image:return image.crop((0,0,SIZE,SIZE)).convert('RGBA')


def bridge_frames(keys,count,flow=False):
    geometry={};flows={};result=[]
    for i in range(count):
        progress=i/(count-1);progress=progress*progress*(3-2*progress)
        position=progress*(len(keys)-1);a=min(len(keys)-2,int(position));fraction=position-a
        if fraction<=1e-8:frame=keys[a].copy()
        elif fraction>=1-1e-8:frame=keys[a+1].copy()
        else:
            if flow:
                if a not in flows:flows[a]=(optical_flow(keys[a],keys[a+1],48),optical_flow(keys[a+1],keys[a],48))
                frame=interpolate(keys[a],keys[a+1],fraction,*flows[a])
            else:
                if a not in geometry:geometry[a]=prepare(keys[a],keys[a+1])
                frame=pose_interpolate(keys[a],keys[a+1],fraction,geometry[a],soft=True)
        result.append(frame)
    return result


def save_clip(name,frames):
    columns=4;rows=math.ceil(len(frames)/columns)
    sheet=Image.new('RGBA',(SIZE*columns,SIZE*rows))
    for i,frame in enumerate(frames):sheet.paste(frame,(i%columns*SIZE,i//columns*SIZE))
    path=OUT/(name+'.webp');sheet.save(path,'WEBP',lossless=True,method=4)
    return {'image':path.name,'frameWidth':SIZE,'frameHeight':SIZE,'columns':columns,'rows':rows,'frameCount':len(frames),'fps':FPS,'facing':'right'}


def gif_preview(name,frames):
    preview=ROOT/'output/transitions';preview.mkdir(parents=True,exist_ok=True)
    rgb=[]
    for frame in frames:
        bg=Image.new('RGB',frame.size,'#edf2fc');bg.paste(frame,mask=frame.getchannel('A'));rgb.append(bg)
    rgb[0].save(preview/(name+'.gif'),save_all=True,append_images=rgb[1:],duration=42,loop=0,optimize=True)


def main():
    path=OUT/'animations.json';manifest=json.loads(path.read_text(encoding='utf-8'))
    actions=manifest['actions'];transitions=[];records=[];alignment={}
    start=first_frame(actions['idle'])
    for target,entry in actions.items():
        if not re.fullmatch(r'[A-Za-z0-9_-]+',target):raise ValueError('动作 ID 不能用作文件路径。')
        if target in ('idle','grabbed'):continue
        end=first_frame(entry);keys=[start,end];kind='contour_aligned_soft_blend';count=8;painted=None
        if target in ('working','sleepy'):
            middle,alignment[target]=painted_keys(target);keys=[start,*middle,end];kind='four_new_painted_inbetweens';count=14;painted=ART/(target+'-sheet.png')
        if target in ('milk_tea','stretch','walking_back') and (ART2/(target+'-sheet.png')).is_file():
            middle,alignment[target]=painted_keys(target,ART2);keys=[start,*middle,end]
            kind='four_new_painted_inbetweens_soft_flow';count=18;painted=ART2/(target+'-sheet.png')
        frames=bridge_frames(keys,count,flow=kind.endswith('soft_flow'))
        for source,dest,clip_frames in [('idle',target,frames),(target,'idle',list(reversed(frames)))]:
            name=f'transition-{source}-to-{dest}';clip=save_clip(name,clip_frames)
            transitions.append({'from':source,'to':dest,'name':f'{actions[source]["name"]} → {actions[dest]["name"]}',**clip})
            records.append({'from':source,'to':dest,'kind':kind,'file':clip['image'],'sha256':digest(OUT/clip['image']),
                'source_first_frame_file':actions[source]['image'],'source_sha256':digest(OUT/actions[source]['image']),
                'target_first_frame_file':actions[dest]['image'],'target_sha256':digest(OUT/actions[dest]['image']),
                'painted_sheet':painted.relative_to(ROOT).as_posix() if painted else None,
                'painted_sheet_sha256':digest(painted) if painted else None})
        if target in ('working','sleepy','thinking','milk_tea','stretch','walking_back'):
            gif_preview('idle-to-'+target,[start]*12+frames+[end]*12+list(reversed(frames)))
        print('过渡',target,'双向',len(frames),'帧',flush=True)
    manifest['transitions']=transitions;path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (OUT/'TRANSITIONS.json').write_text(json.dumps({'newly_drawn_keys':8+12 if ART2.is_dir() else 8,'from_current_endpoints':True,
        'endpoints_lossless':True,'fps':FPS,'inbetween_method':'contour_aligned_soft_blend_or_painted_soft_flow','records':records},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (ART2/'alignment.json' if ART2.is_dir() else ART/'alignment.json').write_text(json.dumps(alignment,indent=2)+'\n',encoding='utf-8')
    print('过渡清单已保存',len(transitions),flush=True)


if __name__=='__main__':main()
