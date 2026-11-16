"""绿幕动作后期入口。--action 可处理不同尺寸与循环策略，默认仍可复跑 idle-v1。"""
import argparse
from bisect import bisect_left
from dataclasses import dataclass
from fractions import Fraction
from itertools import groupby
import json
import math
import os
from pathlib import Path
import re
from statistics import median
import subprocess
from tempfile import TemporaryDirectory

from PIL import Image, ImageChops, ImageMath, ImageStat

if __package__:
    from . import edge_audit, edges, review
else:
    import edge_audit
    import edges
    import review

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "art/deepseek-whale-video"
NATIVE = (1280, 720)
TILE = (512, 288)  # 本机 Aboutme 安装器使用的画布
FPS = 12
COLUMNS = 8
STAMP_MASK = (8, 8, 132, 78)
FACE = (535, 100, 842, 362)


def decode(source, ffmpeg, directory):
    """保留显示顺序与真实 PTS。不同容器的音频时长不能用于取视频帧。"""
    args = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(source), "-map", "0:v:0",
            "-an", "-vf", "showinfo", "-fps_mode", "passthrough", "-start_number", "0",
            str(directory / "%04d.png")]
    proc = subprocess.run(args, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if proc.returncode:
        raise RuntimeError(proc.stderr[-3000:])
    timebase = Fraction(re.search(r"config in time_base:\s*(\d+/\d+)", proc.stderr)[1])
    times = [int(pts) * timebase for pts in re.findall(r"\bn:\s*\d+\s+pts:\s*(-?\d+)\s", proc.stderr)]
    if len(times) < 2 or not all((directory / f"{i:04}.png").is_file() for i in range(len(times))):
        raise ValueError("解码帧与时间戳数量不一致")
    times = [time - times[0] for time in times]
    if any(a >= b for a, b in zip(times, times[1:])):
        raise ValueError("显示时间戳必须严格递增")
    return times


def nearest(times, t):
    right = min(bisect_left(times, t), len(times) - 1)
    left = max(0, right - 1)
    return left if t - times[left] <= times[right] - t else right


def playback_indices(times):
    # 末张是视频模型返回锚点的采样，仍用于报告，但不写入循环。
    playable = times[:-1]
    return [nearest(playable, Fraction(i, FPS)) for i in range(math.ceil(times[-1] * FPS))]


def visible(alpha, threshold=8):
    return alpha.point(lambda v: 255 if v >= threshold else 0)


def green_count(frame):
    r, g, b, a = frame.split()
    excess = ImageChops.subtract(g, ImageChops.lighter(r, b)).point(lambda x: 255 if x > 12 else 0)
    return ImageChops.darker(visible(a), excess).histogram()[255]


def chroma_key(rgb, *, character_strand=None, measure=None):
    """由局部背景场估算覆盖率，软边沿用最近的实色。"""
    r, g, b = rgb.split()
    br, bg, bb = edges.background(rgb).split()
    # 黄绿色幕上 R 接近 G。G−B 对比度更大，仍可分离浅色发梢。
    lime = ImageStat.Stat(br.resize((1, 1))).mean[0] - ImageStat.Stat(bb.resize((1, 1))).mean[0] > 36
    screen = ImageChops.subtract(bg, ImageChops.lighter(br, bb))
    excess = ImageChops.subtract(g, ImageChops.lighter(r, b))
    if lime:
        screen = ImageChops.subtract(bg, bb)
        excess = ImageChops.subtract(g, b)
    hue, saturation, _ = rgb.convert("HSV").split()
    gate = ImageChops.darker(hue.point(lambda h: 255 if 39 <= h <= 132 else 0),
                             saturation.point(lambda s: min(255, s * 4)))
    key = ImageChops.multiply(excess, gate)
    fraction = ImageMath.lambda_eval(
        lambda p: p["key"] * 255 / (p["screen"] + (p["screen"] == 0)),
        key=key, screen=screen).convert("L")
    alpha = fraction.point(lambda v: round(255 * max(0, min(1, (.86 - v / 255) / .82))))
    if character_strand is False:
        # 卡通形态的地面投影仍是饱和绿，亮度比绿幕低；从绿色优势而非亮度排除。
        projected = ImageChops.darker(excess.point(lambda v: 255 if v > 76 else 0),
                                      hue.point(lambda h: 255 if 65 <= h <= 105 else 0))
        alpha.paste(0, mask=projected)
    box, area = None, None
    if character_strand or (character_strand is None and rgb.size == NATIVE):
        silhouette = rgb.copy()
        silhouette.putalpha(alpha)
        try:
            box = edges.top_strand_box(silhouette, keying=True)
        except ValueError:
            # 道具（如飞走的金币）高过头顶时，最高处不再是呆毛，本帧不做呆毛保护，按普通边缘羽化。
            box = None
        if box:
            # 低覆盖率的发丝在压缩图中仍可见。记录其支持范围，供羽化时保留。
            area = sum(alpha.crop(box).histogram()[12:])
    cleaned = edges.inner_borrow(rgb, edges.feather(alpha, box, area))
    if measure is not None:
        measure.update(strand_box=box, hair_before=edges.area(silhouette, box) if box else None,
                       hair_after=edges.area(cleaned, box) if box else None)
    return cleaned



def tile_frame(frame):
    # 重采样会改变边缘覆盖率。最终输出的软边再次从本尺寸实色取样。
    tile = frame.resize(TILE, Image.Resampling.LANCZOS)
    alpha = tile.getchannel("A").point(lambda v: 0 if v < 26 else v)
    return edges.inner_borrow(tile.convert("RGB"), alpha)


def stamp_bounds(rgb):
    # 标记是半透明白字，左上区绿幕的 R/B 很低；区域比实际遮罩大，可检测标记越界。
    r, _, b = rgb.crop((0, 0, 260, 140)).split()
    box = ImageChops.lighter(r, b).point(lambda v: 255 if v > 25 else 0).getbbox()
    if not box or any((box[0] < STAMP_MASK[0], box[1] < STAMP_MASK[1],
                       box[2] > STAMP_MASK[2], box[3] > STAMP_MASK[3])):
        raise ValueError(f"水印区域已变化，请检查原片 {box}")
    return box


def stamp_distance(alpha, stamp):
    """实测角色可见像素中心到水印包围框的最短欧氏距离。水印位于角色左上方。"""
    mask = visible(alpha)
    x0, y0, x1, y1 = mask.getbbox()
    if x0 < stamp[2] or y0 < stamp[3]:
        raise ValueError("角色超出本片预期活动区域，请重新检查水印遮罩")
    best = math.inf
    for y in range(y0, y1):
        dy = y - (stamp[3] - 1)
        if dy >= best:
            break
        row = mask.crop((x0, y, x1, y + 1)).getbbox()
        if row:
            best = min(best, math.hypot(x0 + row[0] - (stamp[2] - 1), dy))
    return best


def shoe_windows(first):
    mask = visible(first.getchannel("A"), 190)
    sole = mask.getbbox()[3]
    strip = mask.crop((0, sole - 22, mask.width, sole))
    spans = []
    x = 0
    for on, run in groupby(strip.getprojection()[0]):
        width = sum(1 for _ in run)
        if on and width > 6:
            spans.append((x, x + width))
        x += width
    if len(spans) != 2:
        raise ValueError(f"首帧没有找到两只独立的鞋 {spans}")
    mid = (spans[0][1] + spans[1][0]) // 2
    return [(spans[0][0] - 18, sole - 29, mid, sole + 18),
            (mid, sole - 29, spans[1][1] + 18, sole + 18)]


def foot_point(frame, windows):
    mask = visible(frame.getchannel("A"), 190)
    shoes = []
    for x0, y0, x1, y1 in windows:
        local = mask.crop((x0, y0, x1, y1))
        box = local.getbbox()
        if not box or box[3] == local.height:
            raise ValueError("鞋部离开固定追踪窗口")
        bottom = box[3]
        lower = local.crop((0, bottom - 10, local.width, bottom)).getprojection()[0]
        shoes.append((x0 + median(x for x, on in enumerate(lower) if on), y0 + bottom - 1))
    return tuple((a + b) / 2 for a, b in zip(*shoes))


def translate(frame, dx, dy):
    result = Image.new("RGBA", frame.size)
    result.paste(frame, (dx, dy))
    return result


def compare(a, b):
    """在两角色可见区域的并集度量，透明区不稀释数值。"""
    aa, ab = a.getchannel("A"), b.getchannel("A")
    union = visible(ImageChops.lighter(aa, ab))
    diff = ImageChops.difference(a.convert("RGBa"), b.convert("RGBa"))
    means = ImageStat.Stat(diff, mask=union).mean
    left, right = visible(aa, 128), visible(ab, 128)
    overlap = ImageChops.darker(left, right).histogram()[255]
    total = ImageChops.lighter(left, right).histogram()[255]
    return (sum(means[:3]) / 3, means[3], overlap / total)


def write_pack(frames, target):
    target.mkdir(parents=True, exist_ok=True)
    rows = math.ceil(len(frames) / COLUMNS)
    sheet = Image.new("RGBA", (TILE[0] * COLUMNS, TILE[1] * rows))
    for i, frame in enumerate(frames):
        sheet.paste(frame, (i % COLUMNS * TILE[0], i // COLUMNS * TILE[1]))
    sheet.save(target / "idle.webp", lossless=True, method=4, exact=True)
    frames[0].save(target / "poster.png")
    entry = dict(name="待机", image="idle.webp", frameWidth=TILE[0], frameHeight=TILE[1],
                 columns=COLUMNS, rows=rows, frameCount=len(frames), fps=FPS, facing="right")
    manifest = dict(format="AICompanionAnimationPack", version=1, source="jingjing-video",
                    size=TILE, poster="poster.png", head=[.43,.18,.22,.31],
                    actions={"idle": entry}, poses={p: "idle" for p in ("idle", "talking", "thinking")})
    (target / "animations.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                            encoding="utf-8")


@dataclass
class Track:
    index: int
    stamp: tuple
    distance: float
    feet: tuple
    offset: tuple
    green: int


def process(args, directory):
    reference = edge_audit.load_baseline(args.baseline, args.input)
    times = decode(args.input, args.ffmpeg, directory)
    if len(times) != len(reference["source"]):
        raise ValueError("源视频帧数与验收基线不匹配")
    source_audit = []
    selected = playback_indices(times)
    needed = set(selected)
    seconds = [nearest(times, Fraction(i)) for i in range(int(times[-1]) + 1)]
    halves = [nearest(times, Fraction(i, 2)) for i in range(int(times[-1] * 2) + 1)]
    tiles, stills, faces, tracks = {}, {}, {}, []
    for i in range(len(times)):
        with Image.open(directory / f"{i:04}.png") as image:
            rgb = image.convert("RGB")
        if rgb.size != NATIVE:
            raise ValueError("本片的水印和鞋部测量以 1280×720 为准")
        stamp = stamp_bounds(rgb)
        rgba = chroma_key(rgb)
        rgba.paste((0, 0, 0, 0), STAMP_MASK)
        distance = stamp_distance(rgba.getchannel("A"), stamp)
        if i == 0:
            windows = shoe_windows(rgba)
            origin = foot_point(rgba, windows)
            first = rgba
        feet = foot_point(rgba, windows)
        offset = tuple(round(a - b) for a, b in zip(origin, feet))
        frame = translate(rgba, *offset)
        source_audit.append(edge_audit.hair_counts(frame, reference["source"][i]))
        tracks.append(Track(i, stamp, distance, feet, offset, green_count(frame)))
        if i in needed:
            tiles[i] = tile_frame(frame)
        if i in seconds or i == len(times) - 1:
            stills[i] = frame
        if i in halves:
            faces[i] = frame.crop(FACE)
    with Image.open(args.anchor) as im:
        anchor = chroma_key(im.convert("RGB").resize(NATIVE, Image.Resampling.LANCZOS))
    frames = [tiles[i] for i in selected]
    tile_green = max(map(green_count, frames))
    if tile_green or any(t.green for t in tracks):
        raise ValueError("检测到残留偏绿像素")
    args.review.mkdir(parents=True, exist_ok=True)
    edge_report, failures = edge_audit.assess(frames, reference, source_audit, args.review)
    if failures:
        raise ValueError(f"边缘验收未通过，请查看两份逐帧 CSV：{failures}")
    write_pack(frames, args.pack)
    review.previews(args.review, frames, seconds, halves, times, stills, faces, FACE, anchor, args.wallpaper)
    diffs = [compare(first, rgba), compare(first, frame), compare(first, anchor),
             compare(frames[0], frames[-1])]
    anchor_foot = foot_point(anchor, windows)
    review.write_report(args, times, selected, tracks, diffs, tile_green, anchor_foot, edge_report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--input", type=Path, default=ART / "clips/idle-v1.mp4")
    parser.add_argument("--anchor", type=Path, default=ART / "anchor/anchor-a-16x9.png")
    parser.add_argument("--pack", type=Path, help="输出图集目录")
    parser.add_argument("--review", type=Path, help="审片目录")
    parser.add_argument("--action", help="动画清单中的动作 ID")
    parser.add_argument("--strategy", choices=("loop", "once", "gait"), default="loop")
    parser.add_argument("--cache", type=Path, help="可选的本次解码与抠绿缓存目录")
    parser.add_argument("--loop", help="人工指定循环区间 START:END（12 fps 取样帧序号，END 不含），用于侧身行走等自动检测不适用的片段")
    parser.add_argument("--baseline", type=Path, default=ART / "clips/idle-v1-edge-baseline.json")
    parser.add_argument("--wallpaper", type=Path,
                        default=Path(os.environ.get("WINDIR", "C:/Windows")) / "Web/Wallpaper/Windows/img0.jpg")
    args = parser.parse_args()
    if args.loop:
        m = re.fullmatch(r"(\d+):(\d+)", args.loop)
        if not m:
            parser.error("--loop 格式应为 START:END")
        args.loop = (int(m.group(1)), int(m.group(2)))
    if args.action:
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", args.action):
            parser.error("无效的动作 ID")
        if __package__:
            from .batch import process_clip
        else:
            from batch import process_clip
        args.pack = args.pack or ROOT / "output/t24-pack"
        args.review = args.review or ROOT / "output/t24-review" / args.input.stem
        with TemporaryDirectory(prefix="t24-action-") as temp:
            process_clip(args, args.cache or Path(temp))
        return
    args.pack = args.pack or ROOT / "output/t24-pack/idle"
    args.review = args.review or ROOT / "output/t24-review/idle-v1"
    with TemporaryDirectory(prefix="t24-idle-") as temp:
        process(args, Path(temp))


if __name__ == "__main__":
    main()
