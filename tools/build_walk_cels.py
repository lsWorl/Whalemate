"""整个人物原画逐帧播放；只裁格、整帧等比缩放和对齐，不拆肢体或补间。"""
from __future__ import annotations
import json
import numpy as np
from PIL import Image,ImageFilter,ImageDraw,ImageFont
from animation_inbetween import components
from build_reference_motion import ROOT,OUT,PREVIEW,SIZE,COUNT,FPS,export,digest
ART=ROOT/'art/deepseek-whale/walk-cels-v1'
CELS=17
CYCLES=7
CYCLE_DISTANCE=280
METHOD='whole_character_painted_cels'

def available():
    return all((ART/f'{d}-sheet.png').is_file() for d in ('left','right'))

def read_sheet(path,rows=2):
    with Image.open(path) as source:image=source.convert('RGBA')
    frames=[]
    for i in range(3*rows):
        # 清稿会有少量跨格留白误差；扩大整个人物的裁格范围，不能裁掉鞋尖。
        bleed=round(image.height/rows*.075)
        cell=image.crop((round(i%3*image.width/3),max(0,round(i//3*image.height/rows)-bleed),
                         round((i%3+1)*image.width/3),min(image.height,round((i//3+1)*image.height/rows)+bleed)))
        groups=components(np.asarray(cell)[:,:,3]>80)
        if not groups or groups[0][1]<1000:raise ValueError(f'{path.name}/{i}: 完整人物缺失')
        keep=groups[0][2]  # 完整人物已连通；排除扩边时进入的相邻帧鞋尖碎片。
        keep=np.asarray(Image.fromarray(keep).filter(ImageFilter.MaxFilter(3)))
        raw=np.array(cell);raw[:,:,3]=(raw[:,:,3].astype(np.uint16)*keep//255).astype(np.uint8)
        cell=Image.fromarray(raw);bounds=cell.getbbox()
        if min(bounds[:2])<2 or bounds[2]>cell.width-2 or bounds[3]>cell.height-2:
            raise ValueError(f'{path.name}/{i}: 人物触边或背景未透明，需重画')
        frames.append(cell.crop(bounds))
    return frames

def normalize(tiles,direction):
    # 全组共用比例，接地线对齐；完整画面移动，手脚相对身体不变。
    scale=SIZE*.67/float(np.median([tile.height for tile in tiles]))
    frames=[];records=[]
    for i,tile in enumerate(tiles):
        whole=tile.resize((round(tile.width*scale),round(tile.height*scale)),Image.Resampling.LANCZOS)
        x=round((SIZE-whole.width)/2);y=round(SIZE*.85-whole.height)
        frame=Image.new('RGBA',(SIZE,SIZE));frame.alpha_composite(whole,(x,y));box=frame.getbbox()
        if min(box[0],box[1],SIZE-box[2],SIZE-box[3])<39:raise ValueError(f'{direction}/{i}: 安全边距不足')
        frames.append(frame);records.append({'cel':i,'source_size':list(tile.size),'scale':scale,
                                            'whole_frame_offset':[x,y],'bounds':list(box)})
    return frames,records

def build(direction):
    if not available():raise ValueError('左右完整逐帧原画尚未齐全')
    tiles=read_sheet(ART/f'{direction}-sheet.png',rows=6)
    # 最后一格为闭环审图稿；成品直接复用首帧，避免端点重绘误差。
    frames,records=normalize(tiles[:CELS],direction)
    folder=ART/direction;folder.mkdir(exist_ok=True)
    for i,frame in enumerate(frames):frame.save(folder/f'{i:02}.png')
    order=list(range(CELS))*CYCLES+[0]
    if len(order)!=COUNT:raise ValueError('周期与120帧交付不匹配')
    record={'method':METHOD,'painted_cels':CELS,'cycles':CYCLES,'fps':FPS,'frame_count':COUNT,
            'sequence':order,'no_limb_rig':True,'no_optical_flow':True,'registration':records,
            'cel_sha256':[digest(folder/f'{i:02}.png') for i in range(CELS)]}
    (folder/'sequence.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return [frames[i].copy() for i in order]

def annotate_source(source):
    source['walking_method']=METHOD
    source['mode']='built-in image_gen full-character cels for walking; existing keys for interactions'
    source['walking_identity_reference']='art/deepseek-whale/reference-motion-v1/user-identity-reference.png'
    source.pop('walking_rig_sources',None);source['walking_cel_sources']={}
    for d in ('left','right'):
        key='walking_'+d
        source['walking_cel_sources'][key]={
            'sheets':[{'file':(ART/f'{d}-sheet.png').relative_to(ROOT).as_posix(),
                       'sha256':digest(ART/f'{d}-sheet.png'),'columns':3,'rows':6}],
            'sequence':(ART/d/'sequence.json').relative_to(ROOT).as_posix(),
            'painted_cels':CELS,'whole_frame_only':True}
        source['alignment'][key]={'method':METHOD,'painted_cels':CELS,'cycles':CYCLES,
                                 'uniform_scale':True,'floor_y':round(SIZE*.85)}
    source['input_files']=[v for v in source.get('input_files',[]) if v['file'] not in ('walking_left-sheet.png','walking_right-sheet.png')]

def write_metadata(key):
    # 字段命名遵循参考仓库的图集格式，项目仍读取原聚合清单。
    metadata={'Source':'built-in image_gen whole-character cels','FrameWidth':SIZE,'FrameHeight':SIZE,
              'Columns':8,'Rows':15,'FrameCount':COUNT,'SampleFps':FPS,'Alpha':'RGBA',
              'SourceDurationSeconds':10,'PaintedCels':CELS,'StrideCycles':CYCLES}
    (OUT/(key+'.json')).write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def main():
    OUT.mkdir(exist_ok=True);PREVIEW.mkdir(exist_ok=True)
    preview=ROOT/'output/walk-cels-v1';preview.mkdir(exist_ok=True)
    manifest=json.loads((OUT/'animations.json').read_text(encoding='utf-8'))
    source=json.loads((OUT/'SOURCE.json').read_text(encoding='utf-8'))
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',16)
    for d in ('left','right'):
        print('完整逐帧行走 '+d,flush=True);frames=build(d);key='walking_'+d
        entry,validation=export(key,'向'+('左' if d=='left' else '右')+'自然行走',frames,True)
        entry.update(strideCycles=CYCLES,cycleDistance=CYCLE_DISTANCE)
        manifest['actions'][key]=entry;source['automatic_validation'][key]=validation
        board=Image.new('RGB',(SIZE*6,SIZE*3),'#edf2fc')
        for i in range(18):
            tile=frames[i if i<CELS else 0];x=i%6*SIZE;y=i//6*SIZE
            board.paste(tile,(x,y),tile.getchannel('A'));ImageDraw.Draw(board).text((x+14,y+350),str(i),font=font,fill='#29466d')
        board.save(preview/(d+'-cels.jpg'))
    annotate_source(source)
    (OUT/'animations.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (OUT/'SOURCE.json').write_text(json.dumps(source,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('左右完整逐帧序列已导出',flush=True)
if __name__=='__main__':main()
