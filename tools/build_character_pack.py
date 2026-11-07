"""把美术素材包（asset-pack-vN）转换为运行时角色立绘包。

处理内容（原图不改动）：
1. 内部 alpha 252~254 统一为 255，避免整只角色 1% 透明及穿透判断不稳。
2. 清除去背景残留的孤立小杂点（不然会变成桌面上看不见却能点中的区域）。
3. 所有姿势按同一个联合包围盒裁切，锚点保持一致，再缩放到运行时高度。
4. 用差分蒙版合成"说话时闭眼+闭嘴"帧。
5. 生成 sprites.json 与预览拼图。

用法（需要开发依赖 Pillow、numpy，scipy 可选）：
    python tools/build_character_pack.py output/desktop-pet/asset-pack-v3 \
        src/aicompanion/characters/deepseek-whale/sprites
"""
from __future__ import annotations

import argparse
import json
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image

POSES = ['idle', 'talking', 'thinking', 'working', 'celebrate', 'error', 'wait', 'sleepy', 'grabbed', 'poked']
VARIANTS = {'eyes_closed', 'mouth_closed'}
ALPHA_SNAP = 240      # 不低于该值的内部像素视为完全不透明
ALPHA_FLOOR = 8       # 低于该值的像素视为背景残留
ISLAND_LIMIT = 400    # 小于该面积的孤立块视为杂点
MARGIN = 24
TARGET_HEIGHT = 640


def components(mask: np.ndarray):
    """返回 (标签图, 每个标签的面积)。优先使用 scipy，缺失时使用纯 Python 广度优先。"""
    try:
        from scipy import ndimage
        labels, count = ndimage.label(mask)
        sizes = np.bincount(labels.ravel(), minlength=count+1)
        return labels, sizes
    except ImportError:
        labels = np.zeros(mask.shape, dtype=np.int32); sizes = [0]
        height, width = mask.shape
        for y0, x0 in zip(*np.nonzero(mask)):
            if labels[y0, x0]: continue
            label = len(sizes); sizes.append(0); queue = deque([(y0, x0)]); labels[y0, x0] = label
            while queue:
                y, x = queue.popleft(); sizes[label] += 1
                for ny, nx in ((y-1, x), (y+1, x), (y, x-1), (y, x+1)):
                    if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not labels[ny, nx]:
                        labels[ny, nx] = label; queue.append((ny, nx))
        return labels, np.array(sizes)


def cleanup_mask(alpha: np.ndarray) -> np.ndarray:
    """返回需要清零的像素：极低 alpha 残留 + 小面积孤立块。"""
    visible = alpha > ALPHA_FLOOR
    labels, sizes = components(visible)
    small = np.zeros_like(visible)
    for label, size in enumerate(sizes):
        if label and size < ISLAND_LIMIT: small |= labels == label
    return (~visible & (alpha > 0)) | small


def fix_alpha(rgba: np.ndarray, erase: np.ndarray) -> np.ndarray:
    out = rgba.copy()
    alpha = out[..., 3]
    alpha[alpha >= ALPHA_SNAP] = 255
    out[erase] = 0
    return out


def resize(image: Image.Image, size) -> Image.Image:
    # 预乘 alpha 后缩放，避免透明区域的隐藏颜色渗到描边上。
    return image.convert('RGBa').resize(size, Image.Resampling.LANCZOS).convert('RGBA')


def build(source: Path, output: Path) -> dict:
    manifest = json.loads((source/'manifest.json').read_text(encoding='utf-8'))
    assets = {item['key']: item for item in manifest['assets']}
    missing = [pose for pose in POSES if pose not in assets]
    if missing: raise SystemExit('素材包缺少姿势：'+', '.join(missing))
    canvas = tuple(manifest['canvas'])
    left = min(assets[p]['visible_bounds'][0] for p in POSES)-MARGIN
    top = min(assets[p]['visible_bounds'][1] for p in POSES)-MARGIN
    right = max(assets[p]['visible_bounds'][2] for p in POSES)+MARGIN
    bottom = max(assets[p]['visible_bounds'][3] for p in POSES)+MARGIN
    box = (max(0, left), max(0, top), min(canvas[0], right), min(canvas[1], bottom))
    scale = TARGET_HEIGHT/(box[3]-box[1])
    size = (round((box[2]-box[0])*scale), TARGET_HEIGHT)
    convert = lambda point: [round((point[0]-box[0])*scale, 1), round((point[1]-box[1])*scale, 1)]

    output.mkdir(parents=True, exist_ok=True)
    poses, report = {}, {}
    for pose in POSES:
        base_path = source/'assets'/assets[pose]['file']
        base = np.array(Image.open(base_path).convert('RGBA'))
        erase = cleanup_mask(base[..., 3])
        frames = {'base': fix_alpha(base, erase)}
        for key, item in assets.items():
            region = item.get('variant_region')
            if item.get('variant_of') not in (pose, assets[pose]['file']) or not region: continue
            kind = {'eyes': 'eyes_closed', 'mouth': 'mouth_closed'}.get(region, region)
            variant = np.array(Image.open(source/'assets'/item['file']).convert('RGBA'))
            if variant.shape != base.shape or not np.array_equal(variant[..., 3], base[..., 3]):
                raise SystemExit(f'{item["file"]} 与 {pose} 的透明通道不一致，无法作为差分。')
            frames[kind] = fix_alpha(variant, erase)
        if 'eyes_closed' in frames and 'mouth_closed' in frames:
            # 两张差分蒙版几乎不重叠：以闭嘴帧为底，叠加闭眼帧改动过的像素。
            changed = np.any(frames['eyes_closed'] != frames['base'], axis=2)
            combo = frames['mouth_closed'].copy(); combo[changed] = frames['eyes_closed'][changed]
            frames['eyes_closed_mouth_closed'] = combo
        entry = {}
        for kind, pixels in frames.items():
            name = pose+('' if kind == 'base' else '_'+kind)+'.png'
            resize(Image.fromarray(pixels, 'RGBA').crop(box), size).save(output/name, optimize=True)
            entry[kind] = name
        anchor = assets[pose].get('registration_anchor', manifest['default_anchor'])
        entry['anchor'] = convert(anchor)
        if pose == 'grabbed': entry['grab_point'] = convert(anchor)
        if 'suggested_board_text_quad' in assets[pose]:
            entry['text_quad'] = [convert(point) for point in assets[pose]['suggested_board_text_quad']]
        if assets[pose].get('airborne'): entry['airborne'] = True
        poses[pose] = entry
        report[pose] = {'erased_pixels': int(erase.sum()), 'variants': sorted(k for k in frames if k != 'base')}

    sprites = {
        'format': 'AICompanionSpritePack', 'version': 1,
        'size': list(size), 'anchor': convert(manifest['default_anchor']),
        'source': {'pack': source.name, 'canvas': list(canvas), 'crop': list(box), 'scale': round(scale, 6)},
        'poses': poses,
    }
    (output/'sprites.json').write_text(json.dumps(sprites, ensure_ascii=False, indent=2), encoding='utf-8')
    preview(output, sprites)
    return report


def preview(output: Path, sprites: dict) -> None:
    width, height = sprites['size']; cell = (width//2, height//2)
    names = [(pose, kind) for pose, entry in sprites['poses'].items() for kind in entry if kind in ('base', 'eyes_closed', 'mouth_closed', 'eyes_closed_mouth_closed')]
    columns = 6; rows = (len(names)+columns-1)//columns
    sheet = Image.new('RGBA', (cell[0]*columns, cell[1]*rows), (46, 52, 70, 255))
    for index, (pose, kind) in enumerate(names):
        image = Image.open(output/sprites['poses'][pose][kind]).resize(cell, Image.Resampling.LANCZOS)
        sheet.alpha_composite(image, ((index % columns)*cell[0], (index//columns)*cell[1]))
    sheet.convert('RGB').save(output/'_preview.jpg', quality=85)


def main():
    parser = argparse.ArgumentParser(description='Build a runtime sprite pack from an art asset pack.')
    parser.add_argument('source', type=Path); parser.add_argument('output', type=Path)
    args = parser.parse_args()
    report = build(args.source, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
