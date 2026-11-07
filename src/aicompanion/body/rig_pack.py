"""自己的形象包：把分层源图（AICompanionPackSource v1）编译成运行时骨骼模型（AICompanionRig v1）。

规格见 docs/tasks/T6-rig-studio.md。源图都是同一张画布上对齐的全尺寸 PNG；编译时按透明度裁出有内容的区域，
把画布像素换算到骨骼空间（中线 x=128、地面 y=256，和本机导入的大肥鱼相同），生成 rig.json 和 textures/。

编译只在出图 / 标注时离线做（tools/rig_studio.py），需要 Pillow；运行时只读编译结果，不需要 Pillow。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import shutil

PACK_FORMAT, PACK_VERSION = 'AICompanionPackSource', 1
RIG_FORMAT, RIG_VERSION = 'AICompanionRig', 1
CENTER_X, GROUND_Y, RIG_HEIGHT = 128, 256, 256       # 骨骼空间：中线、地面、画布高度对应的单位数
VIEW = [-12, -8, 268, 272]                           # 渲染视口（和大肥鱼一致，RigAvatar 按它排版）
PAD = 4                                              # 裁剪时四周留的像素

# 部件 id（= 文件名）→ 变形器、默认 z、是否必需、可选：网格、混合、组。
# 前后顺序和挂接关系与本机导入的大肥鱼一致（那套已经实测过动作自然），角色面朝画面右侧。
LAYERS = {
    'hair_back':        dict(deformer='hairSway', z=1.0, required=True, grid=[6, 6]),
    'tail':             dict(deformer='tailBend', z=1.5, required=False, grid=[8, 3]),
    'hair_back_curl':   dict(deformer='hairSway', z=1.75, required=False, grid=[4, 6]),
    'leg_front':        dict(deformer='legFront', z=3.0, required=True, grid=[3, 6]),
    'leg_back':         dict(deformer='legBack', z=4.0, required=True, grid=[3, 6]),
    'skirt_shadow':     dict(deformer='skirt', z=4.2, required=False, grid=[6, 4], blend='multiply'),
    'arm_far':          dict(deformer='armFar', z=4.5, required=True),
    'body':             dict(deformer='body', z=5.0, required=True),
    'hair_front_right': dict(deformer='hairSway', z=5.05, required=False, grid=[3, 6]),
    'arm_far_hand':     dict(deformer='armFar', z=5.1, required=False),
    'skirt':            dict(deformer='skirt', z=5.2, required=True, grid=[6, 4]),
    'skirt_sit':        dict(deformer='skirtSit', z=5.3, required=False, grid=[6, 4], group='sit'),
    'hair_front_left':  dict(deformer='hairSway', z=5.4, required=False, grid=[3, 6]),
    'waist_bow':        dict(deformer='skirt', z=5.5, required=False),
    'arm_near':         dict(deformer='armNear', z=6.0, required=True),
    'fin_far':          dict(deformer='finFar', z=7.0, required=True),
    'sidelocks':        dict(deformer='bangsSway', z=7.5, required=True, grid=[6, 4]),
    'face':             dict(deformer='headMid', z=8.0, required=True),
    'fin_near':         dict(deformer='finNear', z=11.0, required=True),
    'headdress':        dict(deformer='headMid', z=12.5, required=False),
    'bangs':            dict(deformer='bangsSway', z=13.0, required=True, grid=[6, 4]),
    'bow':              dict(deformer='headMid', z=13.8, required=False),
    'ahoge':            dict(deformer='ahoge', z=14.0, required=False, grid=[3, 4]),
}
HEAD_PARTS = ('face', 'bangs', 'sidelocks', 'headdress', 'bow', 'fin_near', 'fin_far', 'ahoge', 'hair_back')
PIVOTS = ('body', 'waist', 'neck', 'armNear', 'armFar', 'legBack', 'legFront', 'tail', 'finNear', 'finFar', 'ahoge')
PIVOT_PART = {'armNear': 'arm_near', 'armFar': 'arm_far', 'legBack': 'leg_back', 'legFront': 'leg_front', 'tail': 'tail',
              'finNear': 'fin_near', 'finFar': 'fin_far', 'ahoge': 'ahoge', 'neck': 'face', 'waist': 'body'}   # body 支点在脚底，不检查
EYE_LAYERS = ('ball', 'iris', 'hl', 'lash')
EXPRESSIONS = {'neutral': True, 'happy': True, 'love': True, 'surprised': True, 'sleep': True, 'drag': True, 'dizzy': True,
               'shy': False, 'smug': False}


class PackError(ValueError):
    """编译失败：problems 里逐条列出问题（缺图层、尺寸不对、没对齐、支点离谱……）。"""

    def __init__(self, problems: list[str]):
        super().__init__('形象包有问题：\n' + '\n'.join(f'· {p}' for p in problems)); self.problems = problems


@dataclass
class Space:
    """画布像素 ↔ 骨骼空间。"""
    canvas: tuple[int, int]
    center_x: float
    ground_y: float

    @property
    def scale(self) -> float:
        return RIG_HEIGHT/self.canvas[1]

    def x(self, px: float) -> float: return round(CENTER_X+(px-self.center_x)*self.scale, 3)

    def y(self, py: float) -> float: return round(GROUND_Y+(py-self.ground_y)*self.scale, 3)

    def point(self, p) -> list[float]: return [self.x(p[0]), self.y(p[1])]

    def rect(self, box) -> list[float]:   # (x0, y0, x1, y1) 像素 → [x0, y0, x1, y1] 骨骼
        return [self.x(box[0]), self.y(box[1]), self.x(box[2]), self.y(box[3])]


@dataclass
class Layer:
    path: Path
    bbox: tuple[int, int, int, int]       # 裁剪后的像素范围（含留边）


@dataclass
class Report:
    warnings: list[str] = field(default_factory=list)
    parts: int = 0
    expressions: list[str] = field(default_factory=list)


def read_pack(art: Path) -> dict:
    path = Path(art)/'pack.json'
    if not path.is_file(): raise PackError([f'找不到 {path}'])
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('format') != PACK_FORMAT or data.get('version') != PACK_VERSION:
        raise PackError([f'pack.json 的 format / version 应为 {PACK_FORMAT} / {PACK_VERSION}'])
    return data


def _scan(path: Path, canvas, problems) -> Layer | None:
    from PIL import Image
    with Image.open(path) as image:
        if image.size != tuple(canvas):
            problems.append(f'{path.name} 的尺寸是 {image.size[0]}×{image.size[1]}，应为 {canvas[0]}×{canvas[1]}（所有图层同一张画布对齐，不要裁切）')
            return None
        alpha = image.convert('RGBA').getchannel('A').point(lambda a: 255 if a > 8 else 0)
        box = alpha.getbbox()
    if box is None:
        problems.append(f'{path.name} 是空的（全透明）'); return None
    x0, y0, x1, y1 = box
    return Layer(path, (max(0, x0-PAD), max(0, y0-PAD), min(canvas[0], x1+PAD), min(canvas[1], y1+PAD)))


def _near(point, bbox, slack) -> bool:
    x, y = point; x0, y0, x1, y1 = bbox
    return x0-slack <= x <= x1+slack and y0-slack <= y <= y1+slack


def compile_pack(art: str | Path, out: str | Path) -> Report:
    """编译 art/<角色>/ → out/（rig.json + textures/）。有问题时抛 PackError，列出全部问题而不是第一个。"""
    from PIL import Image
    art, out = Path(art), Path(out)
    pack = read_pack(art); report = Report(); problems: list[str] = []
    canvas = tuple(pack.get('canvas') or (2048, 2048))
    origin = pack.get('origin') or {}
    space = Space(canvas, float(origin.get('center_x', canvas[0]/2)), float(origin.get('ground_y', canvas[1]*0.96875)))
    layers_dir = art/'layers'

    # ---- 身体图层 ----
    found: dict[str, Layer] = {}
    for part, spec in LAYERS.items():
        path = layers_dir/f'{part}.png'
        if not path.is_file():
            if spec['required']: problems.append(f'缺少必需图层 layers/{part}.png')
            continue
        layer = _scan(path, canvas, problems)
        if layer: found[part] = layer
    extra = sorted(p.stem for p in layers_dir.glob('*.png') if p.stem not in LAYERS) if layers_dir.is_dir() else []
    if extra: report.warnings.append('这些图层不在规格里，没有编进去：' + '、'.join(extra))

    # ---- 五官 ----
    face_dir = layers_dir/'face'; features: dict[str, Layer] = {}
    for side in ('eyeL', 'eyeR'):
        for layer in EYE_LAYERS:
            path = face_dir/'neutral'/f'{side}_{layer}.png'
            if path.is_file():
                scanned = _scan(path, canvas, problems)
                if scanned: features[f'eye:{side}:{layer}'] = scanned
            elif layer in ('ball', 'iris'):
                problems.append(f'缺少中性眼 face/neutral/{side}_{layer}.png')
    for name, required in EXPRESSIONS.items():
        pieces = ['mouth', 'brows'] if name == 'neutral' else ['eyeL', 'eyeR', 'mouth', 'brows']
        have = []
        for piece in pieces:
            path = face_dir/name/f'{piece}.png'
            if path.is_file():
                scanned = _scan(path, canvas, problems)
                if scanned: features[f'{name}_{piece}'] = scanned; have.append(piece)
        needed = ['mouth'] if name == 'neutral' else ['eyeL', 'eyeR', 'mouth']
        missing = [p for p in needed if p not in have]
        if missing and (required or have):
            problems.append(f'表情 {name} 缺少 ' + '、'.join(f'face/{name}/{p}.png' for p in missing))
        if not missing: report.expressions.append(name)
    talk = face_dir/'talk'/'mouth_open.png'
    if talk.is_file():
        scanned = _scan(talk, canvas, problems)
        if scanned: features['talk_mouth'] = scanned

    # ---- 支点 ----
    pivots_px = pack.get('pivots') or {}
    for name in PIVOTS:
        if name not in pivots_px:
            if name == 'tail' and 'tail' not in found or name == 'ahoge' and 'ahoge' not in found: continue
            problems.append(f'pack.json 缺少支点 pivots.{name}（用 rig_studio 标注）')
        else:
            part = PIVOT_PART.get(name)
            if part in found and not _near(pivots_px[name], found[part].bbox, canvas[1]*0.06):
                problems.append(f'支点 {name} {pivots_px[name]} 离图层 {part} 太远，可能标错了')
    if problems: raise PackError(problems)

    # ---- 输出 ----
    if out.exists(): shutil.rmtree(out)
    textures = out/'textures'; textures.mkdir(parents=True)

    def save(layer: Layer, name: str) -> str:
        with Image.open(layer.path) as image:
            image.convert('RGBA').crop(layer.bbox).save(textures/f'{name}.png', optimize=True)
        return f'textures/{name}.png'

    overrides = pack.get('parts') or {}
    parts = []
    for part, layer in found.items():
        spec = {**LAYERS[part], **(overrides.get(part) or {})}
        x0, y0, x1, y1 = space.rect(layer.bbox)
        parts.append({'id': part, 'tex': save(layer, part), 'box': [x0, y0, round(x1-x0, 3), round(y1-y0, 3)], 'z': spec['z'],
                      'grid': spec.get('grid', [4, 4]), 'deformer': spec['deformer'], 'blend': spec.get('blend', 'normal'),
                      'group': spec.get('group', 'stand')})
    parts.sort(key=lambda p: p['z'])
    report.parts = len(parts)

    pivots = {name: space.point(p) for name, p in pivots_px.items() if name in PIVOTS}
    pivots.setdefault('tail', pivots['body']); pivots.setdefault('ahoge', pivots['neck'])
    rect = lambda part, fallback: space.rect(found[part].bbox) if part in found else fallback
    around = lambda p, r=6: [p[0]-r, p[1]-r, p[0]+r, p[1]+r]
    if pack.get('head'): head = space.rect(pack['head'])
    else:
        boxes = [space.rect(found[p].bbox) for p in HEAD_PARTS if p in found and p != 'hair_back']
        head = [min(b[0] for b in boxes)-4, min(b[1] for b in boxes)-4, max(b[2] for b in boxes)+4, pivots['neck'][1]+12]
    skirt = rect('skirt', around(pivots['waist']))
    deformers = {   # 名字和关系与大肥鱼相同（动作引擎写死）
        'body': {'kind': 'rot', 'pivot': pivots['body']},
        'skirt': {'kind': 'warp', 'parent': 'body', 'rect': skirt, 'fn': 'skirt'},
        'skirtSit': {'kind': 'warp', 'parent': 'body', 'rect': rect('skirt_sit', skirt), 'fn': 'skirt'},
        'waist': {'kind': 'rot', 'parent': 'body', 'pivot': pivots['waist']},
        'armNear': {'kind': 'rot', 'parent': 'waist', 'pivot': pivots['armNear']},
        'armFar': {'kind': 'rot', 'parent': 'waist', 'pivot': pivots['armFar']},
        'legBack': {'kind': 'warp', 'parent': 'body', 'rect': rect('leg_back', around(pivots['legBack'])), 'fn': 'hinge', 'pivot': pivots['legBack']},
        'legFront': {'kind': 'warp', 'parent': 'body', 'rect': rect('leg_front', around(pivots['legFront'])), 'fn': 'hinge', 'pivot': pivots['legFront']},
        'tail': {'kind': 'rot', 'parent': 'body', 'pivot': pivots['tail']},
        'tailBend': {'kind': 'warp', 'parent': 'tail', 'rect': rect('tail', around(pivots['tail'])), 'fn': 'bend'},
        'neck': {'kind': 'rot', 'parent': 'waist', 'pivot': pivots['neck']},
        'headBack': {'kind': 'warp', 'parent': 'neck', 'rect': head, 'fn': 'parallax', 'depth': -0.6},
        'headMid': {'kind': 'warp', 'parent': 'neck', 'rect': head, 'fn': 'parallax', 'depth': 0.0},
        'headFeat': {'kind': 'warp', 'parent': 'neck', 'rect': head, 'fn': 'parallax', 'depth': 0.55},
        'headFront': {'kind': 'warp', 'parent': 'neck', 'rect': head, 'fn': 'parallax', 'depth': 0.8},
        'hairSway': {'kind': 'warp', 'parent': 'headBack', 'rect': rect('hair_back', head), 'fn': 'sway'},
        'bangsSway': {'kind': 'warp', 'parent': 'headFront', 'rect': rect('bangs', head), 'fn': 'sway'},
        'finNear': {'kind': 'rot', 'parent': 'headMid', 'pivot': pivots['finNear']},
        'finFar': {'kind': 'rot', 'parent': 'headBack', 'pivot': pivots['finFar']},
        'ahoge': {'kind': 'rot', 'parent': 'headFront', 'pivot': pivots['ahoge']},
    }
    eyes = {side: {layer: {'tex': save(features[f'eye:{side}:{layer}'], f'feat_{side}_{layer}'),
                           'rect': space.rect(features[f'eye:{side}:{layer}'].bbox)}
                   for layer in EYE_LAYERS if f'eye:{side}:{layer}' in features} for side in ('eyeL', 'eyeR')}
    sprites = {key: {'tex': save(layer, f'feat_{key}'), 'rect': space.rect(layer.bbox)}
               for key, layer in features.items() if not key.startswith('eye:')}
    palette = pack.get('palette') or {}
    rig = {
        'format': RIG_FORMAT, 'version': RIG_VERSION, 'name': pack.get('name', pack.get('id', '')),
        'source': {'kind': 'pack', 'id': pack.get('id'), 'credits': pack.get('credits', []), 'canvas': list(canvas),
                   'scale': space.scale},
        'space': {'view': pack.get('view') or VIEW, 'ground': GROUND_Y, 'center': CENTER_X},
        'pivots': pivots, 'head': head, 'deformers': deformers, 'parts': parts,
        'features': {'eyes': eyes, 'sprites': sprites, 'z': 8.3, 'deformer': 'headFeat',
                     'blush': palette.get('blush', [255, 120, 140]), 'skin': palette.get('skin')},
        'accent': palette.get('accent', '#4D6BFE'), 'schemes': [],
    }
    (out/'rig.json').write_text(json.dumps(rig, ensure_ascii=False, indent=1), encoding='utf-8')
    return report
