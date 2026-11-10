"""透明逐帧动画清单；只解析本包文件，不执行脚本或解析网络地址。"""
from __future__ import annotations
from dataclasses import dataclass
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

    def frame_at(self, elapsed):
        return int(max(0, elapsed)*self.fps)%self.count


@dataclass(frozen=True)
class AnimationPack:
    size: tuple[int, int]
    actions: dict[str, Animation]
    poses: dict[str, str]
    poster: Path
    head: tuple[float, float, float, float]


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
        actions={}
        for key, entry in entries.items():
            width=integer(entry['frameWidth'],16,1024); height=integer(entry['frameHeight'],16,1024)
            columns=integer(entry['columns'],1,32); rows=integer(entry['rows'],1,64)
            count=integer(entry['frameCount'],1,600); fps=integer(entry['fps'],1,30)
            if (width,height)!=size or count>columns*rows or (rows-1)*columns>=count or width*height*columns*rows>32_000_000:
                raise CharacterPackError('动画帧数、画布或图集尺寸不一致。')
            facing=entry.get('facing','left')
            if facing not in ('left','right'): raise CharacterPackError('动画朝向无效。')
            image=local_file(path.parent,entry['image'])
            if image.suffix.lower()!='.webp': raise CharacterPackError('动画图集必须为 WebP。')
            actions[key]=Animation(key,str(entry.get('name',key)),image,width,height,columns,rows,count,fps,facing)
        if any(not isinstance(p,str) or a not in actions for p,a in poses.items()):
            raise CharacterPackError('动画姿势指向不存在的动作。')
        if any(p not in poses for p in ('idle','talking','thinking')):
            raise CharacterPackError('动画缺少待机、说话或思考。')
        head=tuple(data.get('head',[.33,.15,.34,.48]))
        if (len(head)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in head)
                or head[2]<=0 or head[3]<=0 or head[0]+head[2]>1 or head[1]+head[3]>1):
            raise CharacterPackError('动画头部区域无效。')
        return AnimationPack(size,actions,poses,local_file(path.parent,data['poster']),head)
    except (OSError,ValueError,TypeError,KeyError) as error:
        if isinstance(error,CharacterPackError): raise
        raise CharacterPackError('无法读取动画清单或帧规格。') from error
