"""十姿态分层包：把原鲸娘的逐姿态切片（AICompanionPosePack v1，源自 pose-layered-v2）编译成运行时模型
（AICompanionPoseRig v1），由 assets/rig/posepack.js 渲染。契约见 docs/tasks/T6-rig-studio.md 第 A 节。

源数据：每个姿态一份 model.json（源像素坐标、anchor、nodes/pivot、parts 的 z/box/parent、variants 替换层），
裁切图按 box 左上角贴回 1254×1254 源画布。编译做三件事：
1. 换算坐标：每个姿态以自己的 anchor 为原点，x = 128 + (x−ax)×0.2，y = 256 + (y−ay)×0.2（grabbed 的 anchor 是后领挂点）；
2. 缩小贴图（默认 0.5 倍，桌面显示用不到原分辨率，显存和仓库体积都小四倍），写 rig.json + textures/；
3. 检查：十个姿态齐全、图层和替换层引用存在、box 在画布内；--verify 时把切片按 z 回拼，和原图逐像素比对（RGBA 和 Alpha 都要零差异）。

局部摆动（鳍、呆毛、尾巴）只有在源清单里标了独立支点并列入 motion_ready 时才启用，否则幅度为 0 并在报告里列出。
需要 Pillow（只在编译时）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import shutil
import tempfile

from aicompanion.body.rig_pack import OWNED, PACK_ID, PackError

SOURCE_FORMAT, RIG_FORMAT, VERSION = 'AICompanionPosePack', 'AICompanionPoseRig', 1
POSES = ('idle', 'talking', 'thinking', 'working', 'celebrate', 'error', 'wait', 'sleepy', 'grabbed', 'poked')
VARIANTS = ('eyes_closed', 'mouth_closed', 'eyes_closed_mouth_closed')
REQUIRED_PARTS = ('hair_back', 'hair_front', 'head_skin', 'torso', 'eye_left', 'eye_right', 'mouth')
SCALE = 0.2                       # 源像素 → 骨骼单位
CENTER_X, GROUND_Y = 128, 256
VIEW = [-12, 16, 268, 268]           # 最高的 celebrate 举手到 y≈25，最低的 grabbed 脚到 y≈258
GRAB_POINT = [128, 120]           # grabbed 的后领挂点放在骨骼空间的这里（和其他姿态的脖子高度相近）
# A.2 的幅度上限（源像素 / 度）；各姿态可在源清单 motion 里降低或置 0
LIMITS = {'breathe': 0.015, 'head': 2.5, 'hair_tip_px': 6, 'fin': 5, 'ahoge': 8, 'tail': 4, 'look': 1.5}
SWING_PARTS = {'fin_left': 'fin', 'fin_right': 'fin', 'ahoge': 'ahoge', 'tail': 'tail'}


@dataclass
class Report:
    poses: list[str] = field(default_factory=list)
    parts: int = 0
    variants: int = 0
    disabled: list[str] = field(default_factory=list)      # 未就绪、幅度为 0 的局部摆动
    verified: dict = field(default_factory=dict)            # {状态: 差异像素数}
    warnings: list[str] = field(default_factory=list)


def read_source(art: Path) -> dict:
    path = Path(art)/'model.json'
    if not path.is_file(): raise PackError([f'找不到 {path}'])
    root = json.loads(path.read_text(encoding='utf-8'))
    if root.get('format') != SOURCE_FORMAT:
        if root.get('version') == 'pose-layered-v2': root = {**root, 'format': SOURCE_FORMAT, 'version': VERSION, 'source_version': 'pose-layered-v2'}
        else: raise PackError([f'model.json 的 format 应为 {SOURCE_FORMAT}'])
    if root.get('version') != VERSION: raise PackError([f'model.json 的 version 应为 {VERSION}'])
    return root


def _pose_model(art: Path, name: str, root: dict) -> dict | None:
    path = art/'poses'/name/'model.json'
    if path.is_file(): return json.loads(path.read_text(encoding='utf-8'))
    embedded = (root.get('poses') or {}).get(name)    # 根清单里也内嵌了一份（pose-layered-v2）
    return embedded if isinstance(embedded, dict) and embedded.get('parts') else None


def _crop_path(art: Path, part: dict) -> Path:
    return art/(part.get('crop_file') or part['file'])


def _origin(model: dict, name: str):
    """姿态坐标原点：站立姿态的 anchor → (128, 256)；grabbed 的挂点 → GRAB_POINT。"""
    ax, ay = model['anchor']
    tx, ty = (GRAB_POINT if name == 'grabbed' else (CENTER_X, GROUND_Y))
    return lambda x, y: [round(tx+(x-ax)*SCALE, 3), round(ty+(y-ay)*SCALE, 3)]


def check(art: Path, root: dict) -> tuple[dict, list[str], Report]:
    problems, report, poses = [], Report(), {}
    canvas = root.get('canvas') or [1254, 1254]
    for name in POSES:
        model = _pose_model(art, name, root)
        if not model: problems.append(f'缺少姿态 {name}'); continue
        if model.get('anchor') is None: problems.append(f'{name} 缺少 anchor')
        ids = {p['id'] for p in model.get('parts', [])}
        for need in REQUIRED_PARTS:
            if need not in ids: problems.append(f'{name} 缺少图层 {need}')
        for part in model.get('parts', []):
            x, y, w, h = part['box']
            if x < 0 or y < 0 or x+w > canvas[0] or y+h > canvas[1]: problems.append(f'{name}/{part["id"]} 的 box 超出画布')
            if not _crop_path(art, part).is_file(): problems.append(f'{name}/{part["id"]} 找不到 {part.get("crop_file") or part["file"]}')
        for variant, spec in (model.get('variants') or {}).items():
            if variant not in VARIANTS: report.warnings.append(f'{name} 的替换 {variant} 不在约定里，没有编进去')
            for part in spec.get('parts', []):
                if not isinstance(part, dict) or not _crop_path(art, part).is_file():
                    problems.append(f'{name}/{variant} 的替换层 {part if isinstance(part, str) else part.get("id")} 找不到')
            for target in (spec.get('overrides') or {}):
                if target not in ids: problems.append(f'{name}/{variant} 替换的 {target} 不是这个姿态的图层')
        poses[name] = model
    return poses, problems, report


def compile_posepack(art: str | Path, out: str | Path, texture_scale: float = 0.5, verify: bool = False) -> Report:
    from PIL import Image
    from aicompanion.body.rig_pack import check_output
    art, out = Path(art), Path(out)
    root = read_source(art)
    problems = check_output(art, out)
    if problems: raise PackError(problems)
    poses, problems, report = check(art, root)
    character = str(root.get('id') or 'deepseek-whale')
    if not PACK_ID.match(character): problems.append('model.json 的 id 只能用小写字母、数字和连字符')
    if problems: raise PackError(problems)
    if verify: report.verified = verify_composites(art, root, poses)

    out.parent.mkdir(parents=True, exist_ok=True)
    final, temp = out, Path(tempfile.mkdtemp(prefix=f'.{out.name}-', dir=out.parent))
    try:
        runtime = {'format': RIG_FORMAT, 'version': VERSION, 'name': root.get('name', '鲸娘'), 'id': character,
                   'source': {'kind': 'posepack', 'source_version': root.get('source_version'), 'scale': SCALE, 'texture_scale': texture_scale,
                              'canvas': root.get('canvas') or [1254, 1254]},
                   'space': {'view': VIEW, 'ground': GROUND_Y, 'center': CENTER_X, 'grab': GRAB_POINT},
                   'limits': LIMITS, 'blush': (root.get('palette') or {}).get('blush', [255, 120, 140]), 'poses': {}}
        cache: dict[str, str] = {}

        def texture(path: Path, pose: str, name: str) -> str:
            key = str(path.resolve())
            if key in cache: return cache[key]
            rel = f'textures/{pose}/{name}.png'; dest = temp/rel; dest.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(path) as image:
                image = image.convert('RGBA')
                if texture_scale != 1:
                    image = image.resize((max(1, round(image.width*texture_scale)), max(1, round(image.height*texture_scale))), Image.LANCZOS)
                image.save(dest, optimize=True)
            cache[key] = rel; return rel

        for name, model in poses.items():
            to_rig = _origin(model, name)
            ready = set(model.get('motion_ready') or [])
            nodes = {}
            for node, spec in (model.get('nodes') or {}).items():
                nodes[node] = {'parent': spec.get('parent'), 'pivot': to_rig(*spec['pivot'])}
            motion = {**LIMITS, **(model.get('motion') or {})}
            for key, limit in LIMITS.items(): motion[key] = min(float(motion[key]), limit) if key != 'hair_tip_px' else min(float(motion[key]), limit)

            def part_entry(part, variant_of=None):
                x, y, w, h = part['box']
                rect = to_rig(x, y) + to_rig(x+w, y+h)
                pid = part['id']; node = part.get('parent') or 'body'
                swing = None
                if pid in SWING_PARTS and not variant_of:
                    if pid in ready and pid in (model.get('nodes') or {}): swing = SWING_PARTS[pid]; node = pid
                    else: report.disabled.append(f'{name}/{pid}')
                entry = {'id': pid, 'tex': texture(_crop_path(art, part), name, pid), 'rect': rect, 'z': part['z'], 'node': node,
                         'hair': pid.startswith('hair_'), 'swing': swing}
                if pid == 'edge_residual': entry['node'] = 'body'; entry['hair'] = False    # 只随身体基础变换
                if variant_of: entry['variant'] = variant_of
                return entry

            parts = [part_entry(p) for p in model['parts']]
            variants = {}
            for variant, spec in (model.get('variants') or {}).items():
                if variant not in VARIANTS: continue
                overrides = spec.get('overrides') or {}
                layers = []
                for part in spec.get('parts', []):
                    target = next((t for t, f in overrides.items() if Path(f).stem == part['id'] or Path(f).name == Path(part.get('file', '')).name), None)
                    target = target or part['id'].removesuffix('_'+variant)
                    base = next((p for p in model['parts'] if p['id'] == target), None)
                    entry = part_entry({**part, 'parent': (base or {}).get('parent', part.get('parent'))}, variant)
                    entry['replaces'] = target; entry['id'] = part['id']; layers.append(entry)
                variants[variant] = layers; report.variants += len(layers)
            eyes = [p for p in parts if p['id'] in ('eye_left', 'eye_right')]
            cheeks = [[(p['rect'][0]+p['rect'][2])/2, p['rect'][3]+1.5] for p in eyes]
            runtime['poses'][name] = {'anchor': model['anchor'], 'airborne': bool(model.get('airborne')), 'nodes': nodes, 'parts': parts,
                                      'variants': variants, 'motion': motion, 'cheeks': cheeks}
            report.poses.append(name); report.parts += len(parts)
        (temp/'rig.json').write_text(json.dumps(runtime, ensure_ascii=False, indent=1), encoding='utf-8')
        final.mkdir(exist_ok=True)
        for item in ('textures', 'rig.json'):
            old = final/item
            if old.is_dir(): shutil.rmtree(old)
            elif old.exists(): old.unlink()
            shutil.move(str(temp/item), str(old))
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    return report


def check_runtime(data: dict, base: Path) -> list[str]:
    """运行时加载前的检查（不需要 Pillow）：版本、十个姿态、贴图文件都在。"""
    if data.get('format') != RIG_FORMAT or data.get('version') != VERSION: return ['不是受支持的姿态包']
    poses, problems = data.get('poses') or {}, []
    for name in POSES:
        if name not in poses: problems.append(f'缺少姿态 {name}'); continue
        layers = list(poses[name].get('parts', [])) + [p for v in (poses[name].get('variants') or {}).values() for p in v]
        for part in layers:
            if not (Path(base)/part['tex']).is_file(): problems.append(f'缺少贴图 {part["tex"]}')
    return problems


def composite(art: Path, model: dict, variant: str | None = None):
    """按 z 把切片贴回源画布（替换层顶替对应图层），返回 RGBA 图像。"""
    from PIL import Image
    canvas = tuple(model.get('canvas') or (1254, 1254))
    parts = {p['id']: p for p in model['parts']}
    if variant:
        spec = model['variants'][variant]
        for part in spec.get('parts', []):
            target = next((t for t, f in (spec.get('overrides') or {}).items() if Path(f).stem == part['id']), None) or part['id'].removesuffix('_'+variant)
            if target in parts: parts[target] = {**part, 'z': parts[target]['z']}
    image = Image.new('RGBA', canvas, (0, 0, 0, 0))
    for part in sorted(parts.values(), key=lambda p: p['z']):
        with Image.open(_crop_path(art, part)) as layer:
            layer = layer.convert('RGBA'); x, y = part['box'][:2]
            sheet = Image.new('RGBA', canvas, (0, 0, 0, 0)); sheet.paste(layer, (x, y))
            image = Image.alpha_composite(image, sheet)
    return image


def verify_composites(art: Path, root: dict, poses: dict) -> dict:
    """回拼检查：每个姿态（及替换状态）的切片回拼和原图比对，返回 {状态: 不同像素数}；全部应为 0。"""
    from PIL import Image, ImageChops
    result = {}
    for name, model in poses.items():
        targets = [(name, None, model.get('source'))] + [(f'{name}/{v}', v, spec.get('source')) for v, spec in (model.get('variants') or {}).items() if v in VARIANTS]
        for label, variant, source in targets:
            if not source or not (art/source).is_file(): result[label] = None; continue
            mine = composite(art, model, variant)
            with Image.open(art/source) as original:
                original = original.convert('RGBA')
                diff = ImageChops.difference(mine, original)
            # 只看可见像素：两边都几乎全透明（alpha ≤ 1）的像素颜色不同不算
            r, g, b, a = diff.split()
            changed = ImageChops.lighter(ImageChops.lighter(r, g), ImageChops.lighter(b, a)).point(lambda v: 255 if v else 0)
            visible = ImageChops.lighter(mine.getchannel('A'), original.getchannel('A')).point(lambda v: 255 if v > 1 else 0)
            result[label] = ImageChops.multiply(changed, visible).histogram()[255]
    return result
