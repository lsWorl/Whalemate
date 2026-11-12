"""离线检查 T5 源素材：清单、来源哈希与原画布回拼，不读取用户配置或数据。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def verify(root: Path) -> dict:
    from PIL import Image, ImageChops

    root = root.resolve()

    def local(relative: str) -> Path:
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f'素材路径不存在或越界：{relative}')
        return path

    index = json.loads(local('model.json').read_text(encoding='utf-8'))
    if (index.get('format'), index.get('version')) != ('AICompanionPosePack', 1):
        raise ValueError('源格式应为 AICompanionPosePack / 1')
    expected = {'idle', 'talking', 'thinking', 'working', 'celebrate', 'error', 'wait', 'sleepy', 'grabbed', 'poked'}
    if set(index['poses']) != expected:
        raise ValueError('姿态清单应保留已确认的十个动作')
    canvas = tuple(index['canvas'])
    checks, textures, originals = [], set(), set()
    base_count = variant_count = 0

    def checked_part(part: dict):
        file = part['file']
        if file != part['crop_file']:
            raise ValueError(f'file / crop_file 不一致：{file}')
        path = local(file)
        x, y, w, h = part['box']
        if min(x, y) < 0 or min(w, h) <= 0 or x + w > canvas[0] or y + h > canvas[1]:
            raise ValueError(f'裁切矩形越界：{file}')
        with Image.open(path) as image:
            if image.mode != 'RGBA' or image.size != (w, h):
                raise ValueError(f'裁切尺寸或 RGBA 格式不符：{file}')
        textures.add(file)

    for pose, embedded in index['poses'].items():
        model = json.loads(local(f'poses/{pose}/model.json').read_text(encoding='utf-8'))
        if model != embedded or tuple(model['canvas']) != canvas:
            raise ValueError(f'根清单与姿态清单不一致：{pose}')
        nodes = model['nodes']
        for name in nodes:
            seen, current = set(), name
            while current is not None:
                if current not in nodes or current in seen:
                    raise ValueError(f'节点引用缺失或循环：{pose}/{name}')
                seen.add(current)
                current = nodes[current]['parent']
        parts = {p['id']: p for p in model['parts']}
        if len(parts) != len(model['parts']):
            raise ValueError(f'图层 id 重复：{pose}')
        for part in parts.values():
            checked_part(part)
            if part['parent'] not in nodes:
                raise ValueError(f'图层父节点不存在：{pose}/{part["id"]}')
        base_count += len(parts)
        states = [(pose, model['source'], parts)]
        for name, variant in model['variants'].items():
            by_file = {p['file']: p for p in variant['parts']}
            if set(by_file) != set(variant['overrides'].values()):
                raise ValueError(f'替换清单与路径不一致：{pose}/{name}')
            replaced = dict(parts)
            for target, file in variant['overrides'].items():
                if target not in parts:
                    raise ValueError(f'被替换图层不存在：{pose}/{name}/{target}')
                replacement = by_file[file]
                checked_part(replacement)
                replaced[target] = {**replacement, 'z': parts[target]['z']}
            variant_count += len(by_file)
            states.append((f'{pose}/{name}', variant['source'], replaced))
        for label, source, layers in states:
            image = Image.new('RGBA', canvas)
            for part in sorted(layers.values(), key=lambda p: p['z']):
                with Image.open(local(part['file'])) as layer:
                    image.alpha_composite(layer, tuple(part['box'][:2]))
            with Image.open(local(source)) as original:
                if original.mode != 'RGBA' or original.size != canvas:
                    raise ValueError(f'原图格式不符：{source}')
                delta = ImageChops.difference(image, original)
                channels = delta.split()
                rgba = channels[0]
                for channel in channels[1:]:
                    rgba = ImageChops.lighter(rgba, channel)
                visible = ImageChops.lighter(image.getchannel('A'), original.getchannel('A')).point(lambda a: 255 if a else 0)
                changed = rgba.point(lambda value: 255 if value else 0)
                visible_count = ImageChops.multiply(changed, visible).histogram()[255]
                alpha_count = channels[3].point(lambda value: 255 if value else 0).histogram()[255]
            originals.add(source)
            checks.append({'state': label, 'changed_visible_rgba_pixels': visible_count, 'changed_alpha_pixels': alpha_count})
    for file, expected_hash in index['source_sha256'].items():
        actual = hashlib.sha256(local('originals/' + file).read_bytes()).hexdigest()
        if actual != expected_hash:
            raise ValueError(f'确认原图 SHA-256 不一致：{file}')
    return {
        'ok': all(c['changed_visible_rgba_pixels'] == c['changed_alpha_pixels'] == 0 for c in checks),
        'canvas': list(canvas), 'poses': len(expected), 'base_layers': base_count,
        'variant_layers': variant_count, 'texture_files': len(textures),
        'reference_states': len(originals), 'checks': checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    report = verify(args.source)
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding='utf-8')
    print(text)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
