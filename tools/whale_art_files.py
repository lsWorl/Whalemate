"""供两种发布工具共用，只收录内置鲸鲸运行清单实际引用的图片。"""
import json
from pathlib import Path


def whale_art_files(whale):
    """返回相对角色目录的路径，涵盖骨骼变体和立绘的闭眼、闭嘴状态。"""
    whale = Path(whale)
    result = set()
    for folder, manifest in (('rig', 'rig.json'), ('sprites', 'sprites.json')):
        directory = whale / folder
        index = directory / manifest
        if not index.is_file():
            continue
        poses = json.loads(index.read_text(encoding='utf-8'))['poses'].values()
        names = set()
        for pose in poses:
            if folder == 'rig':
                names.update(part['tex'] for part in pose['parts'])
                for parts in pose.get('variants', {}).values():
                    names.update(part['tex'] for part in parts)
            else:
                for state in ('base', 'eyes_closed', 'mouth_closed', 'eyes_closed_mouth_closed'):
                    if state in pose:
                        names.add(pose[state])
        result.add(Path(folder, manifest))
        for name in names:
            texture = directory / name
            if (Path(name).is_absolute() or '..' in Path(name).parts
                    or texture.suffix.lower() != '.png'
                    or not texture.resolve().is_relative_to(directory.resolve())
                    or not texture.is_file()):
                raise ValueError(f'{index} 引用了无效的内置贴图：{name}')
            result.add(Path(folder, name))
    result.update(animation_files(whale / 'animation-video'))
    return sorted(result)


def animation_files(directory):
    """视频逐帧动画包：清单、海报，以及动作和过渡引用的图集。"""
    index = directory / 'animations.json'
    if not index.is_file():
        return set()
    data = json.loads(index.read_text(encoding='utf-8'))
    names = {data['poster']}
    names.update(entry['image'] for entry in data['actions'].values())
    names.update(entry['image'] for entry in data.get('transitions', []))
    result = {Path(directory.name, 'animations.json')}
    for name in names:
        image = directory / name
        if (Path(name).is_absolute() or '..' in Path(name).parts
                or image.suffix.lower() not in ('.png', '.webp')
                or not image.resolve().is_relative_to(directory.resolve())
                or not image.is_file()):
            raise ValueError(f'{index} 引用了无效的内置图集：{name}')
        result.add(Path(directory.name, name))
    return result
