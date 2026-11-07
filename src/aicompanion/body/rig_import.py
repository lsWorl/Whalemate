"""把本机 Coopanion 里的 DeepSeek 大肥鱼转换成 AICompanionRig v1，缓存在本机。

版权与边界：
- 大肥鱼贴图不在 Coopanion 的 AGPL 授权范围内（原设「溟月」，女仆装二创参考 ZipZipPipe，
  围裙为各家商标）。本模块只在用户自己的电脑上读取用户自己下载的 Coopanion，
  转换结果写进 %LOCALAPPDATA%，不进本仓库、不随安装包分发；仅供个人使用与博客截图署名展示。
- 这里只读取它的数据（贴图、部件矩形、支点、表情贴片位置），渲染与动作代码由本项目自行实现。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

WHALE_SUBDIR = Path('packages/cortico-world-desktop-pet/web/whale')
RIG_FORMAT = 'AICompanionRig'
RIG_VERSION = 1
IMPORTER_VERSION = 2   # 改动转换逻辑时递增，强制重新生成缓存
SIT_PARTS = {'skirt_sit', 'waist_bow_sit_front'}
HEAD_PARTS = {'face', 'bangs', 'sidelocks', 'headdress', 'bow', 'eye_creases', 'brows', 'fin_near', 'fin_far', 'ahoge'}


class RigImportError(RuntimeError):
    pass


def cache_root() -> Path:
    base = os.getenv('LOCALAPPDATA') if sys.platform == 'win32' else os.getenv('XDG_CACHE_HOME', str(Path.home()/'.cache'))
    return Path(base or Path.home())/'AICompanion'/'rigs'


def candidate_roots(project_root: Path, configured: str = '') -> list[Path]:
    roots = [Path(configured)] if configured else []
    roots += [project_root/'Coopanion'/'Coopanion', project_root/'Coopanion', project_root.parent/'Coopanion',
              project_root.parent/'Coopanion'/'Coopanion']
    return roots


def find_whale(project_root: Path, configured: str = '') -> Path | None:
    """在常见位置找 Coopanion 的大肥鱼目录；找不到返回 None（桌宠会退回整张立绘）。"""
    for root in candidate_roots(project_root, configured):
        whale = root/WHALE_SUBDIR if (root/WHALE_SUBDIR).is_dir() else root
        figure = whale/'figure.json'
        if figure.is_file():
            try:
                if json.loads(figure.read_text(encoding='utf-8')).get('id') == 'whale' and (whale/'model.json').is_file():
                    return whale.resolve()
            except (OSError, json.JSONDecodeError):
                continue
    return None


def _fingerprint(whale: Path, scheme: str) -> str:
    digest = hashlib.sha256(f'{IMPORTER_VERSION}:{scheme}'.encode())
    for name in ('model.json', 'figure.json'):
        stat = (whale/name).stat(); digest.update(f'{name}:{stat.st_size}:{int(stat.st_mtime)}'.encode())
    return digest.hexdigest()[:16]


def _rect(box):
    x, y, w, h = box
    return [round(x, 3), round(y, 3), round(x+w, 3), round(y+h, 3)]


def _texture(whale: Path, scheme: str, folder: str, name: str, src: str | None = None) -> Path:
    """配色方案目录里有同名贴图就用它，否则用原版。"""
    if scheme and scheme != 'deepseek':
        candidate = whale/'schemes'/scheme/folder/f'{name}.png'
        if candidate.is_file(): return candidate
    path = whale/src if src else whale/folder/f'{name}.png'
    if not path.is_file(): raise RigImportError(f'缺少贴图：{path.name}')
    return path


def convert(whale: Path, out: Path, scheme: str = 'deepseek') -> dict:
    model = json.loads((whale/'model.json').read_text(encoding='utf-8'))
    figure = json.loads((whale/'figure.json').read_text(encoding='utf-8'))
    schemes = {item['id']: item for item in model.get('schemes', [])}
    if scheme not in schemes: scheme = 'deepseek'
    units, pivots, parts = model['units'], model['pivots'], model['parts']
    S, X0, FEET = units['S'], units['X0'], units['FEET']
    master = lambda r: [round(128+(r[0]-X0)*S, 3), round(256+(r[1]-FEET)*S, 3), round(128+(r[2]-X0)*S, 3), round(256+(r[3]-FEET)*S, 3)]
    boxes = {part['id']: part['box'] for part in parts}
    head = [min(boxes[i][0] for i in HEAD_PARTS if i in boxes)-4, min(boxes[i][1] for i in HEAD_PARTS if i in boxes)-4,
            max(boxes[i][0]+boxes[i][2] for i in HEAD_PARTS if i in boxes)+4, pivots['neck'][1]+12]

    textures = out/'textures'; textures.mkdir(parents=True, exist_ok=True)
    def copy(path: Path, key: str) -> str:
        name = f'{key}.png'; shutil.copy2(path, textures/name); return f'textures/{name}'

    rig_parts = []
    for part in parts:
        rig_parts.append({
            'id': part['id'], 'tex': copy(_texture(whale, scheme, 'tex', part['tex'], part.get('src')), part['id']),
            'box': [round(v, 3) for v in part['box']], 'z': part['z'], 'grid': part.get('grid', [4, 4]),
            'deformer': part['parent'], 'blend': part.get('blend', 'normal'),
            'group': 'sit' if part['id'] in SIT_PARTS else 'stand',
        })
    # 变形器树（本项目自定义）：父变形器在子变形器之后作用；全部定义在静止空间。
    deformers = {
        'body': {'kind': 'rot', 'pivot': pivots['body']},
        'skirt': {'kind': 'warp', 'parent': 'body', 'rect': _rect(boxes['skirt']), 'fn': 'skirt'},
        'skirtSit': {'kind': 'warp', 'parent': 'body', 'rect': _rect(boxes['skirt_sit']), 'fn': 'skirt'},
        'waist': {'kind': 'rot', 'parent': 'body', 'pivot': pivots['waist']},
        'armNear': {'kind': 'rot', 'parent': 'waist', 'pivot': pivots['armNear']},
        'armFar': {'kind': 'rot', 'parent': 'waist', 'pivot': pivots['armFar']},
        'legBack': {'kind': 'warp', 'parent': 'body', 'rect': _rect(boxes['leg_back']), 'fn': 'hinge', 'pivot': pivots['legBack']},
        'legFront': {'kind': 'warp', 'parent': 'body', 'rect': _rect(boxes['leg_front']), 'fn': 'hinge', 'pivot': pivots['legFront']},
        'tail': {'kind': 'rot', 'parent': 'body', 'pivot': pivots['tail']},
        'tailBend': {'kind': 'warp', 'parent': 'tail', 'rect': _rect(boxes['tail']), 'fn': 'bend'},
        'neck': {'kind': 'rot', 'parent': 'waist', 'pivot': pivots['neck']},
        'headBack': {'kind': 'warp', 'parent': 'neck', 'rect': head, 'fn': 'parallax', 'depth': -0.6},
        'headMid': {'kind': 'warp', 'parent': 'neck', 'rect': head, 'fn': 'parallax', 'depth': 0.0},
        'headFeat': {'kind': 'warp', 'parent': 'neck', 'rect': head, 'fn': 'parallax', 'depth': 0.55},
        'headFront': {'kind': 'warp', 'parent': 'neck', 'rect': head, 'fn': 'parallax', 'depth': 0.8},
        'hairSway': {'kind': 'warp', 'parent': 'headBack', 'rect': _rect(boxes['hair_back']), 'fn': 'sway'},
        'bangsSway': {'kind': 'warp', 'parent': 'headFront', 'rect': _rect(boxes['bangs']), 'fn': 'sway'},
        'finNear': {'kind': 'rot', 'parent': 'headMid', 'pivot': pivots['finNear']},
        'finFar': {'kind': 'rot', 'parent': 'headBack', 'pivot': pivots['finFar']},
        'ahoge': {'kind': 'rot', 'parent': 'headFront', 'pivot': pivots['ahoge']},
    }
    feat = model['feat']
    eyes = {}
    for side, data in feat['eyes'].items():
        eyes[side] = {}
        for layer in ('ball', 'iris', 'hl', 'lash'):
            if layer in data:
                eyes[side][layer] = {'tex': copy(_texture(whale, scheme, 'feat', f'{side}_{layer}'), f'feat_{side}_{layer}'),
                                     'rect': master(data[layer])}
    sprites = {}
    for name, rect in feat['sprites'].items():
        try:
            sprites[name] = {'tex': copy(_texture(whale, scheme, 'feat', name), f'feat_{name}'), 'rect': master(rect)}
        except RigImportError:
            continue
    rig = {
        'format': RIG_FORMAT, 'version': RIG_VERSION,
        'name': figure.get('name', {}).get('zh', 'whale'),
        'source': {'kind': 'coopanion-local', 'figure': figure.get('id'), 'version': figure.get('version'), 'scheme': scheme,
                   'credits': figure.get('credits', []),
                   'notice': '贴图版权见 Coopanion THIRD_PARTY_NOTICES；仅本机个人使用，不随 AICompanion 分发。'},
        'space': {'view': model['view'], 'ground': 256, 'center': 128},
        'pivots': pivots, 'head': head, 'deformers': deformers, 'parts': rig_parts,
        'features': {'eyes': eyes, 'sprites': sprites, 'z': 8.3,
                     'deformer': 'headFeat', 'blush': feat.get('blush'), 'skin': feat.get('skin')},
        'accent': schemes.get(scheme, {}).get('accent', '#4D6BFE'),
        'schemes': [{'id': key, 'brand': value.get('brand', key), 'accent': value.get('accent')} for key, value in schemes.items()],
    }
    (out/'rig.json').write_text(json.dumps(rig, ensure_ascii=False, indent=1), encoding='utf-8')
    return rig


def ensure_whale_rig(project_root: Path, configured: str = '', scheme: str = 'deepseek', root: Path | None = None) -> Path | None:
    """返回可用的 rig.json 路径；源没变时直接用缓存。找不到 Coopanion 时返回 None。"""
    whale = find_whale(project_root, configured)
    if whale is None: return None
    out = (root or cache_root())/f'deepseek-whale-{scheme}'
    stamp = out/'.source'
    fingerprint = _fingerprint(whale, scheme)
    if (out/'rig.json').is_file() and stamp.is_file() and stamp.read_text(encoding='utf-8') == fingerprint:
        return out/'rig.json'
    if out.exists(): shutil.rmtree(out, ignore_errors=True)
    convert(whale, out, scheme)
    stamp.write_text(fingerprint, encoding='utf-8')
    return out/'rig.json'


def load_rig(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('format') != RIG_FORMAT or data.get('version') != RIG_VERSION:
        raise RigImportError(f'{path} 不是受支持的骨骼模型。')
    for part in data['parts']:
        if part['deformer'] not in data['deformers']: raise RigImportError(f'部件 {part["id"]} 的变形器不存在。')
        if not (Path(path).parent/part['tex']).is_file(): raise RigImportError(f'缺少贴图 {part["tex"]}')
    return data
