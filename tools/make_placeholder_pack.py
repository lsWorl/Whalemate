"""按 T6 形象包规格生成一套合成占位图层（简单几何图形），用来验证编译和渲染，不当正式形象。

用法：python tools/make_placeholder_pack.py <输出目录> [--canvas 2048]
输出目录里会有 pack.json 和 layers/。测试里用小画布（如 512）以加快速度。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

NAVY, BLUE, SKIN, DRESS, WHITE, DARK, PINK = (38, 52, 110), (77, 107, 254), (255, 232, 220), (40, 46, 70), (255, 255, 255), (30, 30, 40), (240, 120, 150)

# 以 2048 画布为基准的形状：(图层, 形状, 坐标, 颜色)。坐标会按画布大小缩放。
BODY = [
    ('hair_back', 'ellipse', (330, 380, 1470, 1650), NAVY),
    ('tail', 'ellipse', (190, 1380, 860, 1700), BLUE),
    ('leg_front', 'rect', (1090, 1560, 1220, 1975), SKIN),
    ('leg_back', 'rect', (840, 1560, 970, 1975), SKIN),
    ('arm_far', 'rect', (1170, 1260, 1300, 1610), SKIN),
    ('body', 'rect', (850, 1180, 1200, 1580), DRESS),
    ('skirt', 'polygon', ((900, 1340), (1150, 1340), (1430, 1830), (650, 1830)), DRESS),
    ('waist_bow', 'ellipse', (930, 1340, 1110, 1460), BLUE),
    ('arm_near', 'rect', (790, 1240, 960, 1700), SKIN),
    ('fin_far', 'polygon', ((1330, 1150), (1640, 830), (1500, 1150)), BLUE),
    ('sidelocks', 'rect', (700, 990, 1490, 1380), NAVY),
    ('face', 'ellipse', (800, 590, 1410, 1250), SKIN),
    ('fin_near', 'polygon', ((780, 1150), (300, 800), (560, 1150)), BLUE),
    ('bangs', 'polygon', ((650, 980), (760, 470), (1100, 400), (1450, 470), (1545, 980), (1100, 760)), NAVY),
    ('ahoge', 'polygon', ((930, 460), (1080, 160), (1010, 460)), NAVY),
]
EYES = {'eyeL': 1050, 'eyeR': 1270}
EYE_Y, MOUTH = 960, (1160, 1130)
PIVOTS = {'body': [1024, 1984], 'waist': [1024, 1447], 'neck': [1039, 1174], 'armNear': [880, 1265], 'armFar': [1235, 1288],
          'legBack': [905, 1601], 'legFront': [1155, 1601], 'tail': [800, 1540], 'finNear': [700, 1100], 'finFar': [1400, 1100],
          'ahoge': [990, 440]}


def make(out: str | Path, canvas: int = 2048) -> Path:
    from PIL import Image, ImageDraw
    out = Path(out); layers = out/'layers'; layers.mkdir(parents=True, exist_ok=True)
    k = canvas/2048

    def scaled(points):
        if isinstance(points[0], (tuple, list)): return [(x*k, y*k) for x, y in points]
        return [v*k for v in points]

    def save(path: Path, draw_fn):
        image = Image.new('RGBA', (canvas, canvas), (0, 0, 0, 0)); draw_fn(ImageDraw.Draw(image))
        path.parent.mkdir(parents=True, exist_ok=True); image.save(path)

    for name, shape, coords, color in BODY:
        def draw(d, shape=shape, coords=coords, color=color):
            c = scaled(coords)
            if shape == 'ellipse': d.ellipse(c, fill=color + (255,))
            elif shape == 'rect': d.rounded_rectangle(c, radius=30*k, fill=color + (255,))
            else: d.polygon(c, fill=color + (255,))
        save(layers/f'{name}.png', draw)

    face = layers/'face'
    def eye_part(side, kind):
        x = EYES[side]
        def draw(d):
            if kind == 'ball': d.ellipse(scaled((x-55, EYE_Y-75, x+55, EYE_Y+75)), fill=WHITE + (255,))
            elif kind == 'iris': d.ellipse(scaled((x-30, EYE_Y-42, x+30, EYE_Y+42)), fill=BLUE + (255,))
            elif kind == 'hl': d.ellipse(scaled((x-16, EYE_Y-26, x-2, EYE_Y-12)), fill=WHITE + (255,))
            else: d.arc(scaled((x-60, EYE_Y-85, x+60, EYE_Y+40)), 200, 340, fill=DARK + (255,), width=max(2, int(12*k)))
        return draw
    for side in EYES:
        for kind in ('ball', 'iris', 'hl', 'lash'):
            save(face/'neutral'/f'{side}_{kind}.png', eye_part(side, kind))

    def mouth(kind):
        x, y = MOUTH
        def draw(d):
            if kind == 'open': d.ellipse(scaled((x-40, y-35, x+40, y+35)), fill=(150, 40, 60, 255))
            elif kind == 'o': d.ellipse(scaled((x-22, y-22, x+22, y+22)), fill=(150, 40, 60, 255))
            elif kind == 'wave': d.line(scaled([(x-50, y), (x-25, y-15), (x, y), (x+25, y-15), (x+50, y)]), fill=DARK + (255,), width=max(2, int(10*k)))
            else: d.arc(scaled((x-45, y-35, x+45, y+25)), 20, 160, fill=DARK + (255,), width=max(2, int(10*k)))
        return draw

    def eye(kind, side):
        x = EYES[side]
        def draw(d):
            w = max(2, int(14*k))
            if kind == 'happy': d.arc(scaled((x-50, EYE_Y-30, x+50, EYE_Y+50)), 200, 340, fill=DARK + (255,), width=w)
            elif kind == 'closed': d.line(scaled([(x-50, EYE_Y+10), (x+50, EYE_Y+10)]), fill=DARK + (255,), width=w)
            elif kind == 'love': d.polygon(scaled([(x, EYE_Y-50), (x+50, EYE_Y), (x, EYE_Y+60), (x-50, EYE_Y)]), fill=PINK + (255,))
            elif kind == 'round': d.ellipse(scaled((x-60, EYE_Y-70, x+60, EYE_Y+70)), outline=DARK + (255,), width=w)
            elif kind == 'drag': d.line(scaled([(x-45, EYE_Y-40), (x+40, EYE_Y), (x-45, EYE_Y+40)]), fill=DARK + (255,), width=w)
            elif kind == 'dizzy':
                for r in (60, 38, 16): d.ellipse(scaled((x-r, EYE_Y-r, x+r, EYE_Y+r)), outline=DARK + (255,), width=max(2, int(8*k)))
            else: d.line(scaled([(x-50, EYE_Y), (x+50, EYE_Y-15)]), fill=DARK + (255,), width=w)   # 半眯
        return draw

    save(face/'neutral'/'mouth.png', mouth('smile'))
    for name, eye_kind, mouth_kind in (('happy', 'happy', 'open'), ('love', 'love', 'smile'), ('surprised', 'round', 'o'),
                                       ('sleep', 'closed', 'smile'), ('drag', 'drag', 'wave'), ('dizzy', 'dizzy', 'wave'),
                                       ('shy', 'happy', 'wave'), ('smug', 'half', 'smile')):
        for side in EYES: save(face/name/f'{side}.png', eye(eye_kind, side))
        save(face/name/'mouth.png', mouth(mouth_kind))
    save(face/'talk'/'mouth_open.png', mouth('open'))

    pack = {'format': 'AICompanionPackSource', 'version': 1, 'id': 'placeholder', 'name': '占位形象',
            'canvas': [canvas, canvas], 'origin': {'center_x': canvas/2, 'ground_y': 1984*k},
            'credits': [{'role': '占位图形', 'by': 'tools/make_placeholder_pack.py', 'license': '随项目', 'notes': '程序生成的几何图形，仅用于测试'}],
            'palette': {'blush': [255, 120, 140], 'skin': list(SKIN), 'accent': '#4D6BFE'},
            'pivots': {name: [round(x*k, 2), round(y*k, 2)] for name, (x, y) in PIVOTS.items()}}
    (out/'pack.json').write_text(json.dumps(pack, ensure_ascii=False, indent=1), encoding='utf-8')
    return out


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='生成 T6 规格的占位形象包源图')
    parser.add_argument('out'); parser.add_argument('--canvas', type=int, default=2048)
    args = parser.parse_args()
    print(make(args.out, args.canvas))
