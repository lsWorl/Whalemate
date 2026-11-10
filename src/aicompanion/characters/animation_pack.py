"""透明逐帧动画清单；只解析本包文件，不执行脚本或解析网络地址。"""
from __future__ import annotations
from dataclasses import dataclass, field
import json
import math
from pathlib import Path

from aicompanion.characters import CharacterPackError


@dataclass(frozen=True)
class Animation:
    id: str
    name: str
    path: Path
    width: int
    height: int
    columns: int
    rows: int
    count: int
    fps: int
    facing: str = 'left'
    blend_group: str = ''
    cycle_distance: float = 70.
    gait_cycles: int = 1
    content_scale: float = 1.
    content_offset: tuple[float,float] = (0.,0.)
    frame_map: tuple[int,...] = ()

    def cell_at(self,index):
        return self.frame_map[index%self.count] if self.frame_map else index

    def frame_at(self, elapsed):
        return int(max(0, elapsed)*self.fps)%self.count


@dataclass(frozen=True)
class Transition:
    source: str
    target: str
    clip: Animation


@dataclass(frozen=True)
class AnimationPack:
    size: tuple[int, int]
    actions: dict[str, Animation]
    poses: dict[str, str]
    poster: Path
    head: tuple[float, float, float, float]
    transitions: dict[tuple[str, str], Transition] = field(default_factory=dict)
    replacement_path: Path | None = None

    source: str = ''

    def transition_route(self, source, target):
        if source in self.actions and target in self.actions:
            group=self.actions[source].blend_group
            if group and group==self.actions[target].blend_group:return []
        direct=self.transitions.get((source,target))
        if direct is not None:return [direct]
        # 经过待机最多两段；不无限遍历动作图，避免积压一长串过期动作。
        first=self.transitions.get((source,'idle')); second=self.transitions.get(('idle',target))
        return [first,second] if first is not None and second is not None else []


def local_file(root, value):
    if (not isinstance(value, str) or not value or Path(value).name!=value
            or '/' in value or '\\' in value or ':' in value or value in ('.', '..')):
        raise CharacterPackError('动画文件必须位于清单同目录。')
    path = root/value
    if path.resolve().parent!=root.resolve() or not path.is_file():
        raise CharacterPackError('动画文件不存在或越出角色包。')
    return path


def integer(value, low, high):
    if type(value) is not int or not low<=value<=high:
        raise CharacterPackError('动画帧规格超出支持范围。')
    return value


def animation_entry(root,key,entry,size):
    width=integer(entry['frameWidth'],16,1024); height=integer(entry['frameHeight'],16,1024)
    columns=integer(entry['columns'],1,32); rows=integer(entry['rows'],1,64)
    count=integer(entry['frameCount'],1,600); fps=integer(entry['fps'],1,30)
    frame_map=entry.get('frameMap',[])
    if (not isinstance(frame_map,list) or (frame_map and len(frame_map)!=count)
            or any(type(v) is not int or not 0<=v<columns*rows for v in frame_map)):
        raise CharacterPackError('动画帧序列必须逐帧指向图集内的有效格子。')
    cells=max(frame_map)+1 if frame_map else count
    if (width,height)!=size or cells>columns*rows or (rows-1)*columns>=cells or width*height*columns*rows>32_000_000:
        raise CharacterPackError('动画帧数、画布或图集尺寸不一致。')
    facing=entry.get('facing','left')
    if facing not in ('left','right'):raise CharacterPackError('动画朝向无效。')
    image=local_file(root,entry['image'])
    if image.suffix.lower()!='.webp':raise CharacterPackError('动画图集必须为 WebP。')
    group=entry.get('blendGroup','');distance=entry.get('cycleDistance',70.)
    if not isinstance(group,str) or len(group)>32 or type(distance) not in (int,float) or not math.isfinite(distance) or not 8<=distance<=300:
        raise CharacterPackError('动画混合组或步幅无效。')
    cycles=integer(entry.get('strideCycles',1),1,32)
    scale=entry.get('contentScale',1.);offset=entry.get('contentOffset',[0.,0.])
    if (type(scale) not in (int,float) or not math.isfinite(scale) or not .5<=scale<=1
            or not isinstance(offset,(tuple,list)) or len(offset)!=2
            or any(type(v) not in (int,float) or not math.isfinite(v) or v<0 or v+scale>1.0001 for v in offset)):
        raise CharacterPackError('动画构图缩放或偏移无效。')
    return Animation(key,str(entry.get('name',key)),image,width,height,columns,rows,count,fps,facing,group,float(distance),cycles,float(scale),tuple(offset),tuple(frame_map))


def load_animation_pack(path):
    path=Path(path)
    try:
        data=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or data.get('format')!='AICompanionAnimationPack' or data.get('version')!=1:
            raise CharacterPackError('不支持的动画清单格式。')
        size=tuple(integer(v,16,1024) for v in data['size'])
        if len(size)!=2: raise CharacterPackError('动画画布规格无效。')
        entries=data['actions']; poses=data['poses']
        if not isinstance(entries, dict) or not 1<=len(entries)<=128 or not isinstance(poses, dict):
            raise CharacterPackError('动画动作清单无效。')
        if any(not isinstance(key,str) or not key or len(key)>120 or key.startswith('@transition:') for key in entries):
            raise CharacterPackError('动作 ID 为空、过长或使用了过渡保留前缀。')
        actions={key:animation_entry(path.parent,key,entry,size) for key,entry in entries.items()}
        transitions={}
        transition_entries=data.get('transitions',[])
        if not isinstance(transition_entries,list) or len(transition_entries)>256:
            raise CharacterPackError('过渡动作清单无效。')
        for entry in transition_entries:
            source,target=entry['from'],entry['to']
            if source not in actions or target not in actions or source==target or (source,target) in transitions:
                raise CharacterPackError('过渡端点不存在、相同或重复。')
            clip=animation_entry(path.parent,'@transition:'+json.dumps([source,target],ensure_ascii=False,separators=(',',':')),entry,size)
            if clip.count<2 or clip.count/clip.fps>.75:
                raise CharacterPackError('单段过渡应为 2 帧以上且不超过 0.75 秒。')
            transitions[source,target]=Transition(source,target,clip)
        if any(not isinstance(p,str) or a not in actions for p,a in poses.items()):
            raise CharacterPackError('动画姿势指向不存在的动作。')
        if any(p not in poses for p in ('idle','talking','thinking')):
            raise CharacterPackError('动画缺少待机、说话或思考。')
        head=tuple(data.get('head',[.33,.15,.34,.48]))
        if (len(head)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in head)
                or head[2]<=0 or head[3]<=0 or head[0]+head[2]>1 or head[1]+head[3]>1):
            raise CharacterPackError('动画头部区域无效。')
        replacement=None
        if 'replacementPack' in data:
            name=data['replacementPack']
            if not isinstance(name,str) or not name or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in name):
                raise CharacterPackError('替换动画包名称无效。')
            directory=path.parent.parent/name
            if directory.resolve().parent!=path.parent.parent.resolve():raise CharacterPackError('替换动画包越界。')
            replacement=local_file(directory,'animations.json')
        source=data.get('source','')
        if not isinstance(source,str) or len(source)>80:raise CharacterPackError('动画来源标记无效。')
        return AnimationPack(size,actions,poses,local_file(path.parent,data['poster']),head,transitions,replacement,source)
    except (OSError,ValueError,TypeError,KeyError) as error:
        if isinstance(error,CharacterPackError): raise
        raise CharacterPackError('无法读取动画清单或帧规格。') from error
