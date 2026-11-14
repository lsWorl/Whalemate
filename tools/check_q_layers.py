"""检查 T23 送审纹理。仅依赖标准库、Pillow 和仓库的图层规格。"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, ImageStat

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aicompanion.body.rig_pack import EYE_LAYERS, EXPRESSIONS, LAYERS

EXTRA = {
    "tail",
    "ahoge",
    "headdress",
    "hair_back_curl",
    "waist_bow",
    "hair_front_right",
    "hair_front_left",
}
GREEN = (0, 255, 0)
WHITE = (255, 255, 255)
DARK = (29, 30, 43)
MAX_MEAN = 3
MAX_TILE = 40
MAX_GREEN = 0
MAX_PALE = 100
MAX_GAPS = 0
MIN_COMPONENT = 16
MAX_ISLANDS = 0
MAX_BLINK_SHIFT = 4


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def flat(im, color):
    result = Image.new("RGB", im.size, color)
    result.paste(im, mask=im.getchannel("A"))
    return result


def count(mask):
    return sum(mask.histogram()[1:])


def small_components(alpha):
    """逐行扫描非零 alpha 的连续段；邻接行相接或斜接都属于同一块。"""
    bounds = alpha.getbbox()
    if bounds is None:
        return []
    tile = alpha.crop(bounds)
    data = tile.tobytes()
    width = tile.width
    parent = []
    runs = []
    previous = []

    def root(i):
        while i != parent[i]:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for y in range(tile.height):
        current = []
        cursor = 0
        for match in re.finditer(rb"[^\x00]+", data[y * width : (y + 1) * width]):
            start, end = match.span()
            i = len(parent)
            parent.append(i)
            current.append((start, end, i))
            runs.append((start + bounds[0], end + bounds[0], y + bounds[1]))
            while cursor < len(previous) and previous[cursor][1] < start:
                cursor += 1
            j = cursor
            while j < len(previous) and previous[j][0] <= end:
                parent[root(previous[j][2])] = root(i)
                j += 1
        previous = current
    areas = {}
    for i, (start, end, _) in enumerate(runs):
        group = root(i)
        areas[group] = areas.get(group, 0) + end - start
    small = {group: [] for group, size in areas.items() if size < MIN_COMPONENT}
    for i, run in enumerate(runs):
        group = root(i)
        if group in small:
            small[group].append(run)
    return list(small.values())


def island_boxes(alpha):
    return [
        [
            min(s for s, _, _ in group),
            group[0][2],
            max(e for _, e, _ in group),
            group[-1][2] + 1,
        ]
        for group in small_components(alpha)
    ]


def edge_counts(im):
    red, green, blue, alpha = im.split()
    excess = ImageChops.subtract(green, ImageChops.lighter(red, blue))
    visible = alpha.point(lambda x: 255 if x >= 8 else 0)
    translucent = alpha.point(lambda x: 255 if 8 <= x <= 247 else 0)
    pale = ImageChops.darker(red, ImageChops.darker(green, blue)).point(
        lambda x: 255 if x >= 245 else 0
    )
    return {
        "green_excess_12": count(
            ImageChops.darker(visible, excess.point(lambda x: 255 if x > 12 else 0))
        ),
        "near_white_translucent": count(ImageChops.darker(translucent, pale)),
    }


def load_layers(art, pack):
    required = {name for name, spec in LAYERS.items() if spec["required"]} | EXTRA
    listed = pack["parts"] | pack.get("review_layers", {})
    missing = required - listed.keys()
    paths = {p.stem: p for p in (art / "layers").glob("*.png")}
    if missing or set(paths) != set(listed):
        raise ValueError(
            f"图层清单与磁盘不符：缺少 {missing | (set(listed) - paths.keys())}，清单外 {paths.keys() - listed.keys()}"
        )
    images = {}
    for name in sorted(listed, key=lambda n: listed[n]["z"]):
        with Image.open(paths[name]) as source:
            if source.mode != "RGBA" or source.size != tuple(pack["canvas"]):
                raise ValueError(f"{paths[name]} 尺寸或通道错误")
            if not source.getbbox() or source.getchannel("A").getextrema()[0] != 0:
                raise ValueError(f"{name} 缺少有效纹理或透明背景")
            images[name] = source.copy()
    return images


def plate(im, width, height, background, crop=False):
    if crop:
        im = im.crop(im.getbbox())
    part = ImageOps.contain(im, (width, height), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (width, height), background)
    canvas.paste(
        part,
        ((width - part.width) // 2, (height - part.height) // 2),
        part if part.mode == "RGBA" else None,
    )
    return canvas


def sheet(items, path, columns, width, height):
    canvas = Image.new(
        "RGB",
        (columns * width, math.ceil(len(items) / columns) * (height + 36)),
        "#eeeef3",
    )
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=16)
    for i, (name, im) in enumerate(items):
        x, y = i % columns * width, i // columns * (height + 36)
        canvas.paste(im, (x, y))
        draw.text((x + 9, y + height + 7), name, fill="#242535", font=font)
    canvas.save(path)


def difference(master, merged):
    green_view = flat(merged, GREEN)
    delta = ImageChops.difference(master.convert("RGB"), green_view)
    pure = Image.new("RGB", master.size, GREEN)
    bands = ImageChops.difference(master.convert("RGB"), pure).split()
    mask = ImageChops.lighter(bands[0], ImageChops.lighter(bands[1], bands[2])).point(
        lambda x: 255 if x else 0
    )
    union = ImageChops.lighter(
        mask, merged.getchannel("A").point(lambda x: 255 if x else 0)
    )
    if not mask.getbbox():
        raise ValueError("主图只有背景色")
    stats = ImageStat.Stat(delta, mask)
    worst = None
    over_limit = []
    for y in range(0, master.height, 64):
        for x in range(0, master.width, 64):
            box = (x, y, min(x + 64, master.width), min(y + 64, master.height))
            active = union.crop(box)
            if not active.getbbox():
                continue
            value = sum(ImageStat.Stat(delta.crop(box), active).mean) / 3
            if value > MAX_TILE:
                over_limit.append({"box": list(box), "mean": value})
            if worst is None or value > worst[0]:
                worst = value, box
    channels = delta.split()
    if worst is None:
        box = union.getbbox()
        worst = sum(ImageStat.Stat(delta.crop(box), union.crop(box)).mean) / 3, box
    red, green, blue = master.convert("RGB").split()
    surplus = ImageChops.subtract(green, ImageChops.lighter(red, blue))
    strongest = ImageChops.lighter(
        channels[0], ImageChops.lighter(channels[1], channels[2])
    )
    measured = {
        "master_pixels": count(mask),
        "master_mixed_green_excess_12": count(
            ImageChops.darker(mask, surplus.point(lambda v: 255 if v > 12 else 0))
        ),
        "mean_abs_rgb": sum(stats.mean) / 3,
        "tiles_over_limit": over_limit,
        "per_channel_mean": [round(n, 4) for n in stats.mean],
        "max_channel_error": max(b[1] for b in stats.extrema),
        "worst_64px_box": list(worst[1]),
        "worst_box_mean": round(worst[0], 3),
        "outside_master": count(ImageChops.subtract(union, mask)),
    }
    return (
        measured,
        green_view,
        ImageOps.colorize(
            strongest.point(lambda x: min(255, x * 5)), "#151624", "#ffae43"
        ),
    )


def spring_limits():
    js = (ROOT / "src/aicompanion/assets/rig/rig.js").read_text(encoding="utf-8")
    return {
        k: float(v)
        for k, v in re.findall(r"(\w+): new Spring\([^,]+,\s*[^,]+,\s*([0-9.]+)\)", js)
    }


def smooth(lo, hi, v):
    t = max(0, min(1, (v - lo) / (hi - lo)))
    return t * t * (3 - 2 * t)


def rotate(x, y, pivot, degrees):
    angle = math.radians(degrees)
    dx, dy = x - pivot[0], y - pivot[1]
    return (
        pivot[0] + math.cos(angle) * dx - math.sin(angle) * dy,
        pivot[1] + math.sin(angle) * dx + math.cos(angle) * dy,
    )


def deform(name, point, group, sign, bounds, pack, limit):
    """按 rig.js 的局部形变公式构造审图姿态，坐标为源画布像素（8 px/骨骼单位）。"""
    x, y = point
    pivot = pack["pivots"]
    deformer = LAYERS[name]["deformer"] if name in LAYERS else "headFeat"
    if group in ("head", "hair") and deformer in (
        "armNear",
        "armFar",
        "legFront",
        "legBack",
        "body",
        "tailBend",
        "skirt",
    ):
        return point
    if group == "arms" and deformer in ("armNear", "armFar"):
        mult = 1 if deformer == "armNear" else -1
        return rotate(
            x, y, pivot[deformer], sign * mult * limit["armN" if mult == 1 else "armF"]
        )
    if group == "legs" and deformer in ("legFront", "legBack"):
        front = deformer == "legFront"
        a = sign * (1 if front else -1) * limit["legF" if front else "legB"]
        px, py = pivot[deformer]
        w = smooth(py - 48, py + 112, y)
        nx, ny = rotate(x, y, (px, py), a)
        ty = max(0, -a if front else a) * 0.05 * 8
        return x + w * (nx - x), y + w * (ny - y - ty)
    if group == "tail" and deformer == "tailBend":
        left, _, right, _ = bounds["tail"]
        width = right - left
        k = (1 - max(0, min(1, (x - left) / width))) ** 1.6
        a = sign * limit["tail"]
        return rotate(
            x - abs(a) * 0.004 * width * k,
            y + a * 0.012 * width * k,
            pivot["tail"],
            a * 0.3,
        )
    if group == "head":
        if deformer in ("finNear", "finFar", "ahoge"):
            spring = {"finNear": "finN", "finFar": "finF", "ahoge": "ahoge"}[deformer]
            mult = -1 if deformer == "finFar" else 1
            x, y = rotate(x, y, pivot[deformer], sign * mult * limit[spring])
        x, y = rotate(x, y, pivot["neck"], sign * limit["head"])
        return x, y + sign * 8 * limit["nod"]
    if group == "lean" and deformer not in (
        "body",
        "skirt",
        "legFront",
        "legBack",
        "tailBend",
    ):
        return rotate(x, y, pivot["waist"], sign * limit["lean"])
    if group == "turn":
        depth = {
            "hairSway": -0.6,
            "finFar": -0.6,
            "headFeat": 0.55,
            "bangsSway": 0.8,
            "ahoge": 0.8,
        }.get(deformer, 0)
        l, t, r, b = pack["head"]
        u, v = (x - l) / (r - l), (y - t) / (b - t)
        w = (
            smooth(0, 0.18, u)
            * smooth(0, 0.18, 1 - u)
            * smooth(0, 0.12, v)
            * smooth(0, 0.25, 1 - v)
        )
        return x + sign * 8 * limit["turnX"] * depth * w, y + sign * 8 * limit[
            "turnY"
        ] * depth * w
    if group == "hair" and deformer in ("hairSway", "bangsSway"):
        _, top, _, bottom = bounds["hair_back" if deformer == "hairSway" else "bangs"]
        w = smooth(top, bottom, y) ** 1.3
        dx = sign * 8 * limit["hairX" if deformer == "hairSway" else "bangs"]
        dy = sign * 8 * limit["hairY"] if deformer == "hairSway" else 0
        return x + dx * w, y + (dy + abs(dx) * 0.08) * w
    if group == "skirt" and deformer == "skirt":
        l, t, r, b = bounds["skirt"]
        u = max(0, min(1, (x - l) / (r - l)))
        w = smooth(0.05, 1, (y - t) / (b - t))
        flare = sign * limit["flare"] * 8
        return x + (sign * limit["skirt"] * 8 + flare * (2 * u - 1)) * w, y - abs(
            flare
        ) * 0.25 * w
    return x, y


def stress_image(source, mapper, crop, steps):
    """将每个网格的两个源三角形映射到目标顶点，用 Pillow 的逆仿射采样纹理。"""
    bounds = source.getbbox()
    l, t, r, b = bounds
    nx, ny = steps
    scale = 0.5
    view = Image.new(
        "RGBA", (round((crop[2] - crop[0]) * scale), round((crop[3] - crop[1]) * scale))
    )

    def texture_vertex(i, j):
        return l + (r - l) * i / nx, t + (b - t) * j / ny

    def triangle(src):
        dst = [
            ((x - crop[0]) * scale, (y - crop[1]) * scale) for x, y in map(mapper, src)
        ]
        x0 = math.floor(min(x for x, y in dst))
        y0 = math.floor(min(y for x, y in dst))
        w = math.ceil(max(x for x, y in dst)) - x0
        h = math.ceil(max(y for x, y in dst)) - y0
        (ax, ay), (bx, by), (cx, cy) = dst
        det = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
        if w <= 0 or h <= 0 or abs(det) < 0.01:
            return
        coeff = []
        for k in (0, 1):
            p, q, s = (point[k] for point in src)
            u = ((q - p) * (cy - ay) - (s - p) * (by - ay)) / det
            v = ((bx - ax) * (s - p) - (cx - ax) * (q - p)) / det
            coeff.extend([u, v, p + u * (x0 - ax) + v * (y0 - ay)])
        tile = source.transform(
            (w, h), Image.Transform.AFFINE, coeff, Image.Resampling.BILINEAR
        )
        stencil = Image.new("L", (w, h))
        ImageDraw.Draw(stencil).polygon([(x - x0, y - y0) for x, y in dst], fill=255)
        view.paste(tile, (x0, y0), stencil)

    for j in range(ny):
        for i in range(nx):
            a, b1, c, d = (
                texture_vertex(i, j),
                texture_vertex(i + 1, j),
                texture_vertex(i, j + 1),
                texture_vertex(i + 1, j + 1),
            )
            triangle((a, b1, c))
            triangle((b1, d, c))
    return view


def stress(images, pack, dest):
    limits = spring_limits()
    bounds = {name: im.getbbox() for name, im in images.items()}
    # 共同扩大画布，尾巴极限弯曲及抬臂不会因超出原画框而被裁切。
    crop = (-200, -240, 2248, 2250)
    panels = []
    measured = {}
    islands = {}
    joints = {
        "arms": ["armNear", "armFar"],
        "legs": ["legFront", "legBack"],
        "tail": ["tail"],
        "head": ["neck", "finNear", "finFar", "ahoge"],
        "hair": ["neck"],
        "skirt": ["waist"],
        "lean": ["waist"],
        "turn": ["neck"],
    }
    for group in joints:
        for sign in (-1, 1):
            angle = sign * limits["armN"]

            def z(name):
                if group == "arms" and angle > 60 and name in ("arm_near", "arm_far"):
                    return 12.6 + (pack["parts"][name]["z"] - 6) * 0.01
                if name == "neutral_features":
                    return 8.3
                return (pack["parts"] | pack.get("review_layers", {}))[name]["z"]

            key = f"{group}-{'plus' if sign > 0 else 'minus'}"
            composed = Image.new("RGBA", (1224, 1245))
            own = {}
            for name in sorted(images, key=z):
                mapper = lambda pt: deform(name, pt, group, sign, bounds, pack, limits)
                im = images[name]
                steps = LAYERS.get(name, {}).get("grid", [4, 4])
                corners = [
                    (x, y) for x in bounds[name][::2] for y in bounds[name][1::2]
                ]
                center = (
                    (bounds[name][0] + bounds[name][2]) / 2,
                    (bounds[name][1] + bounds[name][3]) / 2,
                )
                # 头部视差在网格外沿为零，不能仅据四角决定它是否变形。
                if (
                    group != "turn"
                    and all(mapper(pt) == pt for pt in corners)
                    and mapper(center) == center
                ):
                    projected = Image.new("RGBA", composed.size)
                    tex = im.crop(bounds[name])
                    tex = tex.resize(
                        (max(1, round(tex.width / 2)), max(1, round(tex.height / 2))),
                        Image.Resampling.BILINEAR,
                    )
                    projected.paste(
                        tex,
                        (
                            round((bounds[name][0] - crop[0]) / 2),
                            round((bounds[name][1] - crop[1]) / 2),
                        ),
                    )
                else:
                    projected = stress_image(im, mapper, crop, steps)
                composed.alpha_composite(projected)
                if name in (
                    "arm_near",
                    "arm_far",
                    "leg_front",
                    "leg_back",
                    "tail",
                    "fin_near",
                    "fin_far",
                    "ahoge",
                    "hair_back",
                    "skirt",
                ):
                    own[name] = projected.getchannel("A")
            joint_result = {}
            mapping = {
                "armNear": "arm_near",
                "armFar": "arm_far",
                "legFront": "leg_front",
                "legBack": "leg_back",
                "tail": "tail",
                "finNear": "fin_near",
                "finFar": "fin_far",
                "ahoge": "ahoge",
                "neck": "hair_back",
                "waist": "skirt",
            }
            for joint in joints[group]:
                name = mapping[joint]
                x, y = deform(
                    name, pack["pivots"][joint], group, sign, bounds, pack, limits
                )
                x, y = round((x - crop[0]) / 2), round((y - crop[1]) / 2)
                box = (x - 3, y - 3, x + 4, y + 4)
                local = composed.getchannel("A").crop(box)
                joint_result[joint] = {
                    "transparent_in_7x7": local.point(
                        lambda p: 255 if p < 24 else 0
                    ).histogram()[255],
                    "part_center_alpha": own[name].getpixel((x, y)),
                }
            # 臂与尾的长接缝不只检查支点。取活动部件内部相邻像素，测量躯干端的实际连接。
            if group in ("arms", "legs", "tail", "head"):
                for joint in joints[group]:
                    name = mapping[joint]
                    alpha = own[name]
                    x, y = deform(
                        name, pack["pivots"][joint], group, sign, bounds, pack, limits
                    )
                    x, y = round((x - crop[0]) / 2), round((y - crop[1]) / 2)
                    radius = 8 if joint == "ahoge" else 15
                    sample = (x - radius, y - radius, x + radius + 1, y + radius + 1)
                    count_part = count(
                        alpha.crop(sample).point(lambda p: 255 if p >= 128 else 0)
                    )
                    joint_result[joint]["part_pixels_near_joint"] = count_part
            measured[key] = joint_result
            islands[key] = island_boxes(composed.getchannel("A"))
            # 全姿态在相同画框下查看。另存无标注、可放大的截图。
            image = flat(composed, DARK)
            image.save(dest / (key + ".png"))
            panels.append((key, plate(image, 270, 280, DARK)))
    sheet(panels, dest / "extremes.png", 4, 270, 280)
    return limits, measured, islands


def expression_parts(art, name, canvas):
    directory = art / "layers" / "face"
    base = "neutral" if name == "talk" else name
    eyes = (
        [f"{side}_{layer}" for side in ("eyeL", "eyeR") for layer in EYE_LAYERS]
        if base == "neutral"
        else ["eyeL", "eyeR"]
    )
    files = [directory / base / (piece + ".png") for piece in eyes]
    brow = directory / base / "brows.png"
    if brow.is_file():
        files.append(brow)
    files.append(
        directory / "talk/mouth_open.png"
        if name == "talk"
        else directory / base / "mouth.png"
    )
    feature = Image.new("RGBA", canvas)
    for path in files:
        with Image.open(path) as im:
            if im.mode != "RGBA" or im.size != canvas or not im.getbbox():
                raise ValueError(f"表情纹理的尺寸、通道或透明度错误 {path}")
            feature.alpha_composite(im)
    return feature


def blink_errors(art):
    """检查共同横向范围内的上眼线位置；同时检查两端位置，避免错位眼线仅中心重合。"""
    result = {}
    for side in ("eyeL", "eyeR"):

        def contour(path):
            with Image.open(path) as im:
                alpha = im.getchannel("A").point(lambda v: 255 if v >= 128 else 0)
            left, top, right, bottom = alpha.getbbox()
            return {
                x: top + b[1]
                for x in range(left, right)
                if (b := alpha.crop((x, top, x + 1, bottom)).getbbox())
            }

        face = art / "layers/face"
        a = contour(face / "neutral" / (side + "_lash.png"))
        b = contour(face / "sleep" / (side + ".png"))
        common = a.keys() & b.keys()
        if not common:
            raise ValueError(f"{side} 闭眼与中性眼线无重叠")
        result[side] = max(
            abs(min(a) - min(b)),
            abs(max(a) - max(b)),
            max(abs(a[x] - b[x]) for x in common),
        )
    return result


def run(art, dest):
    dest.mkdir(parents=True, exist_ok=True)
    pack = read_json(art / "pack.json")
    layers = load_layers(art, pack)
    has_faces = (art / "layers/face").is_dir()
    if not has_faces and "neutral_ink" not in layers:
        raise ValueError("缺少正式表情目录 layers/face")
    if has_faces:
        if "neutral_ink" in layers:
            raise ValueError("已经有正式五官，不能再叠加静态五官校样")
        layers["neutral_features"] = expression_parts(
            art, "neutral", tuple(pack["canvas"])
        )
        layers = dict(
            sorted(
                layers.items(),
                key=lambda item: 8.3
                if item[0] == "neutral_features"
                else pack["parts"][item[0]]["z"],
            )
        )
    with Image.open(art / "master.png") as source:
        master = source.convert("RGB")
    merged = Image.new("RGBA", master.size)
    for im in layers.values():
        merged.alpha_composite(im)
    errors, green, heat = difference(master, merged)
    edges = {name: edge_counts(im) for name, im in layers.items()}
    edges["composite"] = edge_counts(merged)
    areas = {
        "hair_tips": (340, 1380, 840, 1848),
        "fin_lower": (555, 645, 827, 725),
        "lace": (611, 1550, 1491, 1734),
    }
    regions = {k: edge_counts(merged.crop(box)) for k, box in areas.items()}
    sheet(
        [
            (name, plate(im, 226, 226, (218, 218, 225), True))
            for name, im in layers.items()
        ],
        dest / "layers-overview.png",
        5,
        226,
        226,
    )
    sheet(
        [
            (tag, plate(im, 500, 500, DARK))
            for tag, im in [
                ("A / master", master),
                ("z / composite", green),
                ("RGB error x5", heat),
            ]
        ],
        dest / "comparison.png",
        3,
        500,
        500,
    )
    box = errors["worst_64px_box"]
    views = [("A / master", master), ("z / composite", green), ("RGB error x5", heat)]
    sheet(
        [
            (tag, im.crop(box).resize((256, 256), Image.Resampling.NEAREST))
            for tag, im in views
        ],
        dest / "worst-region.png",
        3,
        256,
        256,
    )
    edge_views = []
    for bgname, bg in [("white", WHITE), ("dark", DARK)]:
        image = flat(merged, bg)
        # 按角色的真实首尾行把人像本身缩到 300 像素高。
        edge_views.append(
            (bgname + " / 300 px", plate(image.crop(merged.getbbox()), 300, 300, bg))
        )
        for name, box in areas.items():
            edge_views.append(
                (bgname + " / " + name, plate(image.crop(box), 300, 300, bg))
            )
    sheet(edge_views, dest / "edges.png", 4, 300, 300)
    reasons = pack.get("review_tile_notes", {})
    for tile in errors["tiles_over_limit"]:
        x, y, _, _ = tile["box"]
        tile["reason"] = reasons.get(f"{x},{y}")
        tile["image"] = f"tile-{x}-{y}.png"
        sheet(
            [
                (tag, im.crop(tile["box"]).resize((256, 256), Image.Resampling.NEAREST))
                for tag, im in views
            ],
            dest / tile["image"],
            3,
            256,
            256,
        )
    limits, joints, pose_islands = stress(layers, pack, dest)
    islands = {k: island_boxes(im.getchannel("A")) for k, im in layers.items()}
    islands["composite"] = island_boxes(merged.getchannel("A"))
    blink = {}
    if has_faces:
        blink = blink_errors(art)
        panels = []
        for path in sorted((art / "layers/face").glob("*/*.png")):
            with Image.open(path) as im:
                key = path.relative_to(art / "layers").as_posix()
                islands[key] = island_boxes(im.getchannel("A"))
                edges[key] = edge_counts(im)
        for name in [*EXPRESSIONS, "talk"]:
            version = Image.new("RGBA", master.size)
            features = expression_parts(art, name, master.size)
            for key, im in layers.items():
                version.alpha_composite(features if key == "neutral_features" else im)
            cropped = version.crop(version.getbbox())
            h = 300
            scaled = cropped.resize(
                (round(h * cropped.width / cropped.height), h), Image.Resampling.LANCZOS
            )
            panels.append((name, scaled))
        width = max(im.width for _, im in panels) + 12
        sheet(
            [(name, plate(im, width, 300, DARK)) for name, im in panels],
            dest / "expressions-300.png",
            len(panels),
            width,
            300,
        )
    violations = []
    if errors["mean_abs_rgb"] > MAX_MEAN:
        violations.append("回拼平均误差超限")
    if any(not tile["reason"] for tile in errors["tiles_over_limit"]):
        violations.append("局部误差超限且缺少逐格说明")
    if any(
        e["green_excess_12"] > MAX_GREEN or e["near_white_translucent"] > MAX_PALE
        for e in edges.values()
    ):
        violations.append("偏绿或近白半透明像素超限")
    if any(
        s["transparent_in_7x7"] > MAX_GAPS
        for group in joints.values()
        for s in group.values()
    ):
        violations.append("支点窗口有透明像素")
    if any(
        len(boxes) > MAX_ISLANDS
        for boxes in [*islands.values(), *pose_islands.values()]
    ):
        violations.append("存在面积小于 16 px 的孤立块")
    if any(v > MAX_BLINK_SHIFT for v in blink.values()):
        violations.append("闭眼与中性眼线上沿偏差超过 4 px")
    result = {
        "layer_count": len(layers),
        "difference": errors,
        "edges": edges,
        "edge_regions": regions,
        "spring_limits": limits,
        "joint_samples": joints,
        "isolated_boxes": islands,
        "pose_isolated_boxes": pose_islands,
        "blink_max_error_px": blink,
        "thresholds": {
            "mean_abs_rgb": MAX_MEAN,
            "tile_mean": MAX_TILE,
            "green_pixels": MAX_GREEN,
            "near_white_pixels": MAX_PALE,
            "joint_gap_pixels": MAX_GAPS,
            "isolated_components": MAX_ISLANDS,
            "component_min_area_px": MIN_COMPONENT,
            "blink_px": MAX_BLINK_SHIFT,
        },
        "violations": violations,
        "passed": not violations,
    }
    (dest / "report.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--art", type=Path, default=ROOT / "art/deepseek-whale-q")
    parser.add_argument("--output", type=Path, default=ROOT / "output/t23-layers")
    args = parser.parse_args()
    result = run(args.art, args.output)
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "violations": result["violations"],
                "mean_abs_rgb": result["difference"]["mean_abs_rgb"],
                "over_limit_tiles": len(result["difference"]["tiles_over_limit"]),
            },
            ensure_ascii=False,
        )
    )
    print(args.output.resolve())
    raise SystemExit(0 if result["passed"] else 1)
