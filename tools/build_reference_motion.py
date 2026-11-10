"""唯一参考图的十动作：固定构图、共享站姿、10 秒 / 12 FPS / 120 帧。"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,ImageFilter,ImageFont
from animation_inbetween import components,optical_flow,interpolate,rgba_float,rgba_image,sample

ROOT=Path(__file__).resolve().parents[1]
ART=ROOT/'art/deepseek-whale/reference-motion-v1'
OUT=ROOT/'src/aicompanion/characters/deepseek-whale/animation-reference-v1'
PREVIEW=ROOT/'output/reference-motion-v1'
SIZE=384;FPS=12;COUNT=120
ACTIONS={'walking_left':'向左自然行走','walking_right':'向右自然行走','click_wave':'点击回应·元气挥手',
 'curtsy':'女仆礼仪·屈膝礼','curious':'好奇·歪头观察','shy_peek':'害羞·捂脸偷看',
 'happy_hops':'开心·连续小跳','self_hug':'撒娇·抱住自己','pout_stomp':'生气·鼓起脸颊','giggle':'撒娇式笑场·捂嘴偷笑'}


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def clean_parts(path,count):
    with Image.open(path) as im:image=im.convert('RGBA')
    parts=[p for p in components(np.asarray(image)[:,:,3]>40) if p[1]>1000]
    if len(parts)!=count:raise ValueError(f'{path.name}: 需要 {count} 个独立完整主体，实际 {len(parts)}')
    if count==6:parts.sort(key=lambda p:((p[0][1]+p[0][3])/2>image.height/2,(p[0][0]+p[0][2])/2))
    tiles=[]
    for box,area,mask in parts:
        if min(box[:2])<2 or box[2]>image.width-2 or box[3]>image.height-2:raise ValueError('主体碰边，拒绝裁切图纸。')
        keep=np.asarray(Image.fromarray(mask).filter(ImageFilter.MaxFilter(3)))
        a=np.array(image);a[:,:,3]=(a[:,:,3].astype(np.uint16)*keep//255).astype(np.uint8)
        tile=Image.fromarray(a).crop(box)
        tiles.append(tile)
    return tiles


def face_box(tile):
    a=np.asarray(tile);r,g,b=[a[:,:,i].astype(float) for i in range(3)]
    y=np.arange(tile.height)[:,None]
    mask=(a[:,:,3]>160)&(r>140)&(g>75)&(r>g+7)&(r>b+12)&(g>b-10)&(y<tile.height*.6)
    parts=components(mask)
    if not parts or parts[0][1]<40:raise ValueError('无法定位脸部，不能自动缩放角色。')
    return parts[0][0]


def place(tile,scale,walking=False):
    rgba=np.asarray(tile);ys,xs=np.where(rgba[:,:,3]>80)
    if walking:
        box=face_box(tile);anchor_x=(box[0]+box[2])/2
        x=round(SIZE*.5-anchor_x*scale);y=round(SIZE*.18)
    else:
        feet=xs[ys>=ys.max()-max(2,round(tile.height*.025))]
        x=round(SIZE*.5-float(feet.min()+feet.max())/2*scale)
        y=round(SIZE*.85-float(ys.max())*scale)
    tile=tile.resize((round(tile.width*scale),round(tile.height*scale)),Image.Resampling.LANCZOS)
    frame=Image.new('RGBA',(SIZE,SIZE));frame.alpha_composite(tile,(x,y))
    return frame


def extract_all():
    stand_tile=clean_parts(ART/'standard-stand-source.png',1)[0]
    stand_scale=SIZE*.67/stand_tile.height
    stand=place(stand_tile,stand_scale)
    box=face_box(stand_tile);face_height=(box[3]-box[1])*stand_scale
    keys={};reports={}
    for key in ACTIONS:
        tiles=clean_parts(ART/(key+'-sheet.png'),6)
        neutral=tiles if key.startswith('walking') else [tiles[0],tiles[-1]]
        heights=[face_box(tile)[3]-face_box(tile)[1] for tile in neutral]
        scale=SIZE*.67/float(np.median([tile.height for tile in neutral]))
        frames=[place(tile,scale,key.startswith('walking')) for tile in tiles]
        if not key.startswith('walking'):frames[0]=stand.copy();frames[-1]=stand.copy()
        keys[key]=frames;reports[key]={'face_scale':scale,'face_height_ratio_to_standard':float(np.median(heights))*scale/face_height,'keys':6,'shared_standard_endpoints':not key.startswith('walking')}
        folder=ART/key;folder.mkdir(exist_ok=True)
        for i,frame in enumerate(frames):frame.save(folder/f'{i:02}.png')
    stand.save(ART/'standard-stand.png')
    return stand,keys,reports


def time_sample(key,t):
    if key.startswith('walking'):
        position=(t/10*8%1)*6
        return int(position)%6,(int(position)+1)%6,position%1
    if key=='happy_hops':
        if t<2:return 0,1,(t/2)**2*(3-2*t/2)
        if t<6:
            phase=(t-2)/4*3%1
            if phase<.45:return 1,2,phase/.45
            if phase<.62:return 2,3,(phase-.45)/.17
            return 3,1,(phase-.62)/.38
        if t<8:return 1,4,(t-6)/2
        return 4,0,(t-8)/2
    times=[0,2,4,6,8,10]
    if key=='curtsy':times=[0,2,4,5.5,7,10]
    elif key=='click_wave':times=[0,2,3.5,5,8,10]
    elif key=='happy_hops':times=[0,2,6,6.1,8,10]
    segment=min(4,next((i for i in range(5) if times[i]<=t<times[i+1]),4))
    amount=max(0,min(1,(t-times[segment])/(times[segment+1]-times[segment])))
    return segment,segment+1,amount*amount*(3-2*amount)


def jump_offset(t,amplitude=18):
    return amplitude*math.sin(math.pi*((t-2)/4*3%1))**2 if 2<=t<6 else 0.


def compile_action(key,keys):
    if key.startswith('walking'):
        from build_walk_cels import available,build
        if available():return build(key.removeprefix('walking_'))
    pairs={};frames=[];open_key=None
    if key.startswith('walking'):
        # 两次偶尔眨眼，不在每个迈步周期都闭眼。
        f=optical_flow(keys[3],keys[5],80);b=optical_flow(keys[5],keys[3],80)
        open_key=interpolate(keys[3],keys[5],.5,f,b,single_cel=True)
    for i in range(COUNT):
        if i in (0,COUNT-1):frame=keys[0].copy()
        else:
            t=i/(COUNT-1)*10;a,b,amount=time_sample(key,t)
            active=keys
            if open_key is not None and int(t/10*8) not in (2,6):
                active=keys.copy();active[4]=open_key
            pair=(a,b,active is keys)
            if amount<1e-7:frame=active[a].copy()
            elif amount>1-1e-7:frame=active[b].copy()
            else:
                if pair not in pairs:pairs[pair]=(optical_flow(active[a],active[b],80),optical_flow(active[b],active[a],80))
                # 每帧只采样一张原画，避免两双手 / 两张脸的交叉叠影。
                frame=interpolate(active[a],active[b],amount,*pairs[pair],single_cel=True)
            if key=='happy_hops':
                lift=jump_offset(t);frame=frame.transform((SIZE,SIZE),Image.Transform.AFFINE,(1,0,0,0,1,lift),Image.Resampling.BICUBIC)
        frames.append(frame)
    frames[-1]=frames[0].copy()
    return frames


def durations(unit=1000,count=COUNT,fps=FPS):
    return [round((i+1)*unit/fps)-round(i*unit/fps) for i in range(count)]


def export(key,name,frames,write_png=False,*,fps=FPS,frame_map=None):
    count=len(frames);columns=8
    stored=frames[:max(frame_map)+1] if frame_map else frames
    rows=math.ceil(len(stored)/columns);atlas=Image.new('RGBA',(SIZE*columns,SIZE*rows))
    for i,frame in enumerate(stored):atlas.paste(frame,(i%columns*SIZE,i//columns*SIZE))
    path=OUT/(key+'.webp');atlas.save(path,'WEBP',lossless=True,exact=True,method=4)
    rgb=[]
    if write_png:(PREVIEW/'frames'/key).mkdir(parents=True,exist_ok=True)
    for i,frame in enumerate(frames):
        if write_png:frame.save(PREVIEW/'frames'/key/f'{i:03}.png')
        bg=Image.new('RGB',(SIZE,SIZE),'#edf2fc');bg.paste(frame,mask=frame.getchannel('A'));rgb.append(bg)
    # GIF 时长按10ms量化分配，12或24FPS均保持精确总时长。
    rgb[0].save(PREVIEW/(key+'.gif'),save_all=True,append_images=rgb[1:],duration=[x*10 for x in durations(100,count,fps)],loop=0,optimize=True)
    frames[0].save(PREVIEW/(key+'-animated.webp'),save_all=True,append_images=frames[1:],duration=durations(count=count,fps=fps),loop=0,lossless=True,exact=True,method=4)
    with Image.open(path) as encoded:
        first=frame_map[0] if frame_map else 0;last=frame_map[-1] if frame_map else count-1
        def cell(index):return encoded.crop((index%columns*SIZE,index//columns*SIZE,index%columns*SIZE+SIZE,index//columns*SIZE+SIZE))
        if cell(first).tobytes()!=cell(last).tobytes():raise ValueError('图集编码后首尾像素不一致。')
    boxes=[f.getbbox() for f in frames]
    min_margin=min(min(b[0],b[1],SIZE-b[2],SIZE-b[3]) for b in boxes)
    record={'frames':len(frames),'fps':fps,'duration_ms':sum(durations(count=count,fps=fps)),'first_last_identical':frames[0].tobytes()==frames[-1].tobytes(),
      'min_margin_px':min_margin,'safe_margin_passed':min_margin>=math.ceil(SIZE*.1),'image_sha256':digest(path)}
    facing='left' if key=='walking_left' else 'right'
    entry={'name':name,'image':path.name,'frameWidth':SIZE,'frameHeight':SIZE,'columns':columns,'rows':rows,'frameCount':count,'fps':fps,'facing':facing}
    if frame_map is not None:entry['frameMap']=frame_map
    if key.startswith('walking'):
        from build_walk_cels import available,CYCLE_DISTANCE,CYCLES,write_metadata
        entry.update(cycleDistance=CYCLE_DISTANCE if available() else 256,strideCycles=CYCLES if available() else 8)
        if available():write_metadata(key,entry)
    return entry,record


def main(only=None,write_png=False):
    OUT.mkdir(exist_ok=True);PREVIEW.mkdir(exist_ok=True)
    stand,keys,reports=extract_all()
    actions={};validation={};all_frames={}
    for key,name in ACTIONS.items():
        if only and key not in only:continue
        print('编排',key,flush=True);frames=compile_action(key,keys[key])
        kwargs={}
        if key.startswith('walking'):
            from build_walk_cels import dense_available,DENSE_FPS,sequence_order
            if dense_available():kwargs={'fps':DENSE_FPS,'frame_map':sequence_order()}
        actions[key],validation[key]=export(key,name,frames,write_png,**kwargs);all_frames[key]=frames
    if only:
        path=OUT/'animations.json';manifest=json.loads(path.read_text(encoding='utf-8'));manifest['actions'].update(actions)
        path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        path=OUT/'SOURCE.json';source=json.loads(path.read_text(encoding='utf-8'));source['automatic_validation'].update(validation);source['alignment']=reports
        source['input_files']=[{'file':p.name,'sha256':digest(p)} for p in sorted(ART.glob('*-sheet.png'))]
        from build_walk_cels import available,annotate_source
        if available():annotate_source(source)
        path.write_text(json.dumps(source,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        (PREVIEW/'partial-validation.json').write_text(json.dumps(validation,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'updated':list(actions),'failed':[k for k,r in validation.items() if not r['safe_margin_passed']]},ensure_ascii=False))
        return
    stand.save(OUT/'poster.png')
    actions['idle'],validation['idle']=export('idle','统一标准站姿',[stand]*COUNT,False)
    manifest={'format':'AICompanionAnimationPack','version':1,'size':[SIZE,SIZE],'poster':'poster.png','head':[.32,.27,.36,.23],
      'actions':actions,'poses':{'idle':'idle','talking':'idle','thinking':'curious','walking':'walking_right','walking_left':'walking_left','walking_right':'walking_right',**{key:key for key in ACTIONS}},'transitions':[]}
    (OUT/'animations.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    source={'mode':'built-in image_gen keys plus offline single-cel flow interpolation','identity_reference':'art/deepseek-whale/reference-motion-v1/user-identity-reference.png',
      'identity_sha256':digest(ART/'user-identity-reference.png'),'requested_actions':10,'frames_per_action':COUNT,'fps':FPS,'duration_seconds':10,
      'not_1200_hand_drawn_frames':True,'automatic_validation':validation,'alignment':reports,
      'input_files':[{'file':p.name,'sha256':digest(p)} for p in sorted(ART.glob('*-sheet.png'))],
      'manual_review_required':['五官与服装细节一致性','肢体结构与遮挡','插值中间帧是否自然','头发裙摆尾巴惯性是否可信']}
    from build_walk_cels import available,annotate_source
    if available():annotate_source(source)
    (OUT/'SOURCE.json').write_text(json.dumps(source,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    original=OUT.parent/'animation-original/animations.json'
    original_data=json.loads(original.read_text(encoding='utf-8'));original_data['replacementPack']=OUT.name
    original.write_text(json.dumps(original_data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (ART/'alignment.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    montage=[];font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',12)
    preview_count=max(len(frames) for frames in all_frames.values());preview_fps=preview_count/10
    for i in range(preview_count):
        bg=Image.new('RGB',(960,424),'#edf2fc');draw=ImageDraw.Draw(bg)
        for n,(key,name) in enumerate(ACTIONS.items()):
            tile=all_frames[key][min(len(all_frames[key])-1,int(i/preview_count*len(all_frames[key])))].resize((192,192),Image.Resampling.LANCZOS);x=n%5*192;y=n//5*212
            bg.paste(tile,(x,y+20),tile.getchannel('A'));draw.text((x+8,y+4),name,font=font,fill='#29466d')
        montage.append(bg)
    montage[0].save(PREVIEW/'all-actions.gif',save_all=True,append_images=montage[1:],duration=[x*10 for x in durations(100,preview_count,preview_fps)],loop=0,optimize=True)
    failures=[key for key,r in validation.items() if not r['safe_margin_passed'] or not r['first_last_identical']]
    print(json.dumps({'actions':10,'frames':sum(len(frames) for frames in all_frames.values()),'validation_failures':failures},ensure_ascii=False),flush=True)
    if failures:raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--only',nargs='+',choices=list(ACTIONS));parser.add_argument('--png-frames',action='store_true')
    args=parser.parse_args();main(args.only,args.png_frames)
