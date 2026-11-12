"""将固定版本的 Aboutme 大肥鱼图集安装到本机；不随 PR 分发第三方贴图。"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import urllib.parse
import urllib.request
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
LOCK = Path(__file__).with_name('aboutme-pet.lock.json')
DEST = ROOT/'src/aicompanion/characters/deepseek-whale/animation-aboutme'
CACHE = ROOT/'output/aboutme-cache'
SIZE = (512,288)
MAX_BYTES = 20_000_000
ALIASES = {'falling':'grabbed','dizzy':'error','drag':'grabbed','poked':'pout','patted':'happy',
           'neutral':'idle','love':'happy','surprised':'shy','click_wave':'wave',
           'shy_peek':'shy','happy_hops':'happy','pout_stomp':'pout','bow':'curtsy','tilt':'curious'}

def read_lock(path=LOCK):
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if data['repository']!='https://github.com/hunmer/aboutme-pets' or not re.fullmatch('[0-9a-f]{40}',data['revision']):
        raise ValueError('资源仓库或固定版本无效')
    keys=set()
    for e in data['actions']:
        if (not re.fullmatch('[a-z_]+',e['key']) or e['key'] in keys
                or e['path']!='pets/'+e['pack']+'/spritesheet/'+e['name']+'.webp'
                or e['pack'] not in ('dsh','dsh1') or any(c in e['name'] for c in '/\\:')
                or not re.fullmatch('[0-9a-f]{64}',e['sha256']) or not 0<e['bytes']<=MAX_BYTES):
            raise ValueError('资源锁定记录无效')
        keys.add(e['key'])
    return data

def verify(raw, entry):
    if len(raw)!=entry['bytes'] or hashlib.sha256(raw).hexdigest()!=entry['sha256']:
        raise ValueError('资源校验失败：'+entry['key'])

def cached_source(entry, revision, cache, offline=False):
    path=Path(cache)/(entry['key']+'.webp')
    if path.is_file():
        verify(path.read_bytes(),entry)
        return path
    if offline:raise FileNotFoundError('缺少本机缓存：'+entry['key'])
    url='https://raw.githubusercontent.com/hunmer/aboutme-pets/'+revision+'/'+urllib.parse.quote(entry['path'])
    with urllib.request.urlopen(url,timeout=60) as response:
        raw=response.read(MAX_BYTES+1)
    verify(raw,entry)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.partial');temporary.write_bytes(raw);temporary.replace(path)
    return path

def convert(source, entry, dest, size=SIZE):
    """统一整帧等比缩小；不逐帧对齐、不拆肢体、不修改帧顺序。"""
    spec=entry['spritesheet'];w,h=spec['frameWidth'],spec['frameHeight']
    columns,rows,count=spec['columns'],spec['rows'],spec['frameCount']
    if (w,h)!=(640,360) or spec['fps']!=12 or count>columns*rows or w*h*columns*rows>50_000_000:
        raise ValueError('上游图集规格无效')
    first,last=entry.get('frames',[0,count])
    if not 0<=first<last<=count:raise ValueError('帧范围无效')
    count=last-first;out_rows=math.ceil(count/8)
    if size[0]*size[1]*8*out_rows>32_000_000:raise ValueError('本机图集超过播放器上限')
    with Image.open(source) as opened:
        if opened.size!=(w*columns,h*rows) or 'A' not in opened.getbands():
            raise ValueError('上游图集尺寸或透明通道无效')
        image=opened.convert('RGBA')
    sheet=Image.new('RGBA',(size[0]*8,size[1]*out_rows))
    poster=None
    for index,original in enumerate(range(first,last)):
        x=original%columns*w;y=original//columns*h
        tile=image.crop((x,y,x+w,y+h)).resize(size,Image.Resampling.LANCZOS)
        sheet.paste(tile,(index%8*size[0],index//8*size[1]))
        if index==0:poster=tile
    sheet.save(dest,format='WEBP',lossless=True,method=3)
    return dict(name=entry['name'],image=dest.name,frameWidth=size[0],frameHeight=size[1],
                columns=8,rows=out_rows,frameCount=count,fps=spec['fps'],facing=entry['facing']),poster

def install(dest=DEST, cache=CACHE, *, offline=False):
    data=read_lock();dest=Path(dest);dest.mkdir(parents=True,exist_ok=True)
    actions={}
    for e in data['actions']:
        source=cached_source(e,data['revision'],cache,offline)
        temporary=dest/(e['key']+'.partial.webp')
        action,poster=convert(source,e,temporary)
        target=dest/(e['key']+'.webp');temporary.replace(target);action['image']=target.name
        actions[e['key']]=action
        if e['key']=='idle':poster.save(dest/'poster.png')
        print('已安装 '+e['key'],flush=True)
    walk=actions.pop('walking')
    # 上游只提供左行走；右行走在播放器中整帧镜像，不生成新角色。
    for key in ('walking_left','walking_right'):
        actions[key]=dict(walk,cycleDistance=80/12*40)
    poses={key:key for key in actions};poses.update(ALIASES);poses['walking']='walking_right'
    manifest=dict(format='AICompanionAnimationPack',version=1,source='aboutme',size=list(SIZE),
                  poster='poster.png',head=[.34,.12,.32,.45],actions=actions,poses=poses)
    (dest/'SOURCE.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary=dest/'animations.partial.json';temporary.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(dest/'animations.json')
    return dest/'animations.json'

def install_directory(program_dir=None):
    if program_dir is None and not (ROOT/'AICompanion.exe').is_file():
        return DEST
    directory = Path(program_dir or ROOT).resolve()
    whale = directory/'_internal/aicompanion/characters/deepseek-whale'
    if not (directory/'AICompanion.exe').is_file() or not (whale/'character.json').is_file():
        raise ValueError('请选择包含 AICompanion.exe 和 _internal 的桌宠程序目录。')
    return whale/'animation-aboutme'


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline',action='store_true',help='仅使用校验通过的本机缓存')
    parser.add_argument('--program-dir', type=Path, help='安装到指定的 Windows 桌宠程序目录')
    args=parser.parse_args()
    print(install(dest=install_directory(args.program_dir), offline=args.offline))
