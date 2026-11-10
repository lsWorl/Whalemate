"""补画完整人物过渡姿势，24FPS播放；只裁格与整帧等比对齐，不拆肢体或生成光流。"""
from __future__ import annotations
import json,math
import numpy as np
from PIL import Image,ImageFilter,ImageDraw,ImageFont
from animation_inbetween import components
from build_reference_motion import ROOT,OUT,PREVIEW,SIZE,export,digest
BASE_ART=ROOT/'art/deepseek-whale/walk-cels-v1'
ART=ROOT/'art/deepseek-whale/walk-cels-v2'
BASE_CELS=17
CELS=34
CYCLES=7
CYCLE_DISTANCE=280
DENSE_FPS=24
COUNT=240
FPS=DENSE_FPS
METHOD='whole_character_painted_cels'

def dense_available():
    return all((ART/f'{d}-sheet.png').is_file() for d in ('left','right'))
available=dense_available

def sequence_order():
    return list(range(CELS))*CYCLES+[0,0]

def read_sheet(path,rows=2):
    with Image.open(path) as source:image=source.convert('RGBA')
    frames=[]
    for i in range(3*rows):
        bleed=round(image.height/rows*.075)
        cell=image.crop((round(i%3*image.width/3),max(0,round(i//3*image.height/rows)-bleed),
                         round((i%3+1)*image.width/3),min(image.height,round((i//3+1)*image.height/rows)+bleed)))
        groups=components(np.asarray(cell)[:,:,3]>80)
        if not groups or groups[0][1]<1000:raise ValueError(f'{path.name}/{i}: 完整人物缺失')
        if len(groups)>1 and groups[1][1]>groups[0][1]*.6:raise ValueError(f'{path.name}/{i}: 一格出现多个完整人物')
        keep=np.asarray(Image.fromarray(groups[0][2]).filter(ImageFilter.MaxFilter(3)))
        raw=np.array(cell);raw[:,:,3]=(raw[:,:,3].astype(np.uint16)*keep//255).astype(np.uint8)
        cell=Image.fromarray(raw);bounds=cell.getbbox()
        if min(bounds[:2])<2 or bounds[2]>cell.width-2 or bounds[3]>cell.height-2:raise ValueError(f'{path.name}/{i}: 人物触边或背景不透明')
        frames.append(cell.crop(bounds))
    return frames

def build(direction):
    if not dense_available():raise ValueError('左右完整过渡原画尚未齐全')
    base=[]
    for i in range(BASE_CELS):
        with Image.open(BASE_ART/direction/f'{i:02}.png') as image:base.append(image.convert('RGBA'))
    mids=read_sheet(ART/f'{direction}-sheet.png',rows=6)
    frames=[];records=[]
    for i in range(BASE_CELS):
        previous=base[i];following=base[(i+1)%BASE_CELS]
        frames.append(previous.copy())
        records.append({'cel':i*2,'kind':'original','source':(BASE_ART/direction/f'{i:02}.png').relative_to(ROOT).as_posix(),
                        'source_sha256':digest(BASE_ART/direction/f'{i:02}.png'),'bounds':list(previous.getbbox())})
        # 仅等比对齐完整新画，使高度落在相邻两张原画之间；不改变手脚相对位置。
        boxes=[previous.getbbox(),following.getbbox()]
        target_height=round(sum(b[3]-b[1] for b in boxes)/2)
        tile=mids[i];scale=target_height/tile.height
        whole=tile.resize((round(tile.width*scale),target_height),Image.Resampling.LANCZOS)
        x=round((SIZE-whole.width)/2);y=326-whole.height
        frame=Image.new('RGBA',(SIZE,SIZE));frame.alpha_composite(whole,(x,y));box=frame.getbbox()
        if min(box[0],box[1],SIZE-box[2],SIZE-box[3])<39:raise ValueError('补画安全边距不足')
        frames.append(frame)
        records.append({'cel':i*2+1,'kind':'new_inbetween','endpoints':[i,(i+1)%BASE_CELS],
                        'source_size':list(tile.size),'scale':scale,'whole_frame_offset':[x,y],'bounds':list(box)})
    folder=ART/direction;folder.mkdir(exist_ok=True)
    for i,frame in enumerate(frames):frame.save(folder/f'{i:02}.png')
    order=sequence_order()
    if len(order)!=COUNT:raise ValueError('补帧周期与交付帧数不匹配')
    record={'method':METHOD,'painted_cels':CELS,'newly_painted_cels':17,'cycles':CYCLES,
            'fps':FPS,'frame_count':COUNT,'sequence':order,'no_limb_rig':True,'no_optical_flow':True,
            'registration':records,'cel_sha256':[digest(folder/f'{i:02}.png') for i in range(CELS)]}
    (folder/'sequence.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return [frames[i].copy() for i in order]

def annotate_source(source):
    source['walking_method']=METHOD;source['mode']='built-in image_gen full-character inbetweens, original cels retained'
    source['walking_identity_reference']='art/deepseek-whale/reference-motion-v1/user-identity-reference.png'
    source['walking_cel_sources']={}
    for d in ('left','right'):
        key='walking_'+d
        source['walking_cel_sources'][key]={'sheets':[{'file':(ART/f'{d}-sheet.png').relative_to(ROOT).as_posix(),
                            'sha256':digest(ART/f'{d}-sheet.png'),'columns':3,'rows':6}],
            'base_cels':(BASE_ART/d).relative_to(ROOT).as_posix(),
            'sequence':(ART/d/'sequence.json').relative_to(ROOT).as_posix(),
            'painted_cels':CELS,'newly_painted_cels':17,'whole_frame_only':True}
        source['alignment'][key]={'method':METHOD,'painted_cels':CELS,'cycles':CYCLES,'fps':FPS,'frames':COUNT,
                                 'original_cels_unchanged':True,'floor_y':326}
    source['per_action_timing']={key:{'frames':v['frames'],'fps':v['fps'],'duration_ms':v['duration_ms']}
                                 for key,v in source['automatic_validation'].items()}

def write_metadata(key,entry):
    metadata={'Source':'built-in image_gen whole-character cels','FrameWidth':SIZE,'FrameHeight':SIZE,
              'Columns':entry['columns'],'Rows':entry['rows'],'FrameCount':entry['frameCount'],
              'SampleFps':entry['fps'],'FrameMap':entry.get('frameMap',[]),'Alpha':'RGBA',
              'SourceDurationSeconds':10,'PaintedCels':CELS,'StrideCycles':CYCLES}
    (OUT/(key+'.json')).write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def main():
    preview=ROOT/'output/walk-cels-v2';preview.mkdir(exist_ok=True)
    manifest=json.loads((OUT/'animations.json').read_text(encoding='utf-8'))
    source=json.loads((OUT/'SOURCE.json').read_text(encoding='utf-8'))
    for d in ('left','right'):
        print('整帧加密 '+d,flush=True);frames=build(d);key='walking_'+d
        entry,validation=export(key,'向'+('左' if d=='left' else '右')+'自然行走',frames,True,fps=FPS,frame_map=sequence_order())
        manifest['actions'][key]=entry;source['automatic_validation'][key]=validation
        board=Image.new('RGB',(SIZE*6,SIZE*6),'#edf2fc')
        for i in range(CELS):
            tile=frames[i];board.paste(tile,(i%6*SIZE,i//6*SIZE),tile.getchannel('A'))
        board.save(preview/(d+'-34-cels.jpg'))
    annotate_source(source)
    (OUT/'animations.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (OUT/'SOURCE.json').write_text(json.dumps(source,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('左右34张整帧原画与24FPS序列已导出',flush=True)
if __name__=='__main__':main()
