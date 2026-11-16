"""局部绿幕估计、透明边缘借色与同帧验收。像素坐标均对应传入图像。"""
from bisect import bisect_left
from functools import lru_cache
from math import ceil
from statistics import median

from PIL import Image, ImageChops, ImageFilter, ImageMath, ImageStat


def background(rgb):
    """将可靠的绿幕样本与权重分别模糊，再相除；被角色遮住的像素不会拉暗背景。"""
    r, g, b = rgb.split()
    rb = ImageChops.lighter(r, b)
    # 浅绿幕的红、蓝通道本身可能超过 100。四角中位数决定保守的样本上界。
    radius = max(1, min(rgb.size) // 32)
    samples_rb = [ImageStat.Stat(rb.crop((x, y, x + radius, y + radius))).median[0]
                  for x in (0, rgb.width - radius) for y in (0, rgb.height - radius)]
    rb_limit = max(48, median(samples_rb) + 18)
    corners_g = [ImageStat.Stat(g.crop((x, y, x + radius, y + radius))).median[0]
                 for x in (0, rgb.width - radius) for y in (0, rgb.height - radius)]
    min_excess = max(12, min(85, .58 * (median(corners_g) - median(samples_rb))))
    samples = ImageChops.darker(
        rb.point(lambda n: 255 if n <= rb_limit else 0),
        ImageChops.subtract(g, rb).point(lambda n: 255 if n >= min_excess else 0))
    if not samples.getbbox():
        raise ValueError("画面中没有足够明确的绿幕样本")
    size = (ceil(rgb.width / 8), ceil(rgb.height / 8))
    def blur(channel):
        return channel.resize(size, Image.Resampling.BOX).filter(ImageFilter.GaussianBlur(8))
    weights = blur(samples)
    reliable = weights.point(lambda v: 255 if v >= 12 else 0)
    result = []
    for channel in (r, g, b):
        numerator = blur(ImageChops.multiply(channel, samples))
        local = ImageMath.lambda_eval(
            lambda p: p["n"] * 255 / (p["w"] + (p["w"] == 0)), n=numerator, w=weights).convert("L")
        fallback = round(ImageStat.Stat(channel, mask=samples).mean[0])
        local = Image.composite(local, Image.new("L", size, fallback), reliable)
        result.append(local.resize(rgb.size, Image.Resampling.BILINEAR))
    return Image.merge("RGB", result)


def top_strand_box(rgba, *, keying=False):
    """用呆毛顶点与下方帽檐的展开处定位。前后均用旧帧给出的同一矩形。"""
    alpha = rgba.getchannel("A")
    mask = alpha.point(lambda v: 255 if v >= 26 else 0)
    if keying:
        mask = mask.crop((alpha.width // 4, 0, alpha.width * 3 // 4, alpha.height))
    top = mask.getbbox()[1]
    scale = alpha.width / 512
    # 顶端压缩亮点也可能近白；连续扫描，等帽檐在一行内达到稳定宽度。
    r, g, b, _ = rgba.split()
    pale = ImageChops.darker(ImageChops.darker(r, g), b).point(lambda v: 255 if v >= 145 else 0)
    pale = ImageChops.darker(pale, alpha.point(lambda v: 255 if v >= 128 else 0))
    for y in range(top + round(5 * scale), min(alpha.height, top + round(33 * scale))):
        row = pale.crop((0, y, alpha.width, y + 1))
        if row.histogram()[255] >= round(8 * scale):
            break
    else:
        raise ValueError("无法定位呆毛与发箍的交界")
    x0, x1 = (alpha.width // 4, alpha.width * 3 // 4) if keying else (0, alpha.width)
    return (x0, max(0, top - round(4 * scale)), x1, y)


def area(frame, box):
    return sum(frame.getchannel("A").crop(box).histogram()[26:])


def feather(alpha, strand_box=None, strand_area=None):
    # 半径 1 px 的内收。先识别会被内收破坏的细线，在它们局部预留覆盖率。
    opened = alpha.filter(ImageFilter.MinFilter(7)).filter(ImageFilter.MaxFilter(7))
    strands = ImageChops.subtract(alpha, opened).point(lambda a: 255 if a > 45 else 0)
    strands = strands.filter(ImageFilter.MaxFilter(5))
    guarded = Image.composite(alpha.filter(ImageFilter.MaxFilter(3)), alpha, strands)
    shrunk = guarded.filter(ImageFilter.MinFilter(3))
    smooth = shrunk.filter(ImageFilter.GaussianBlur(.6))
    if strand_box is not None and strand_area is not None:
        # 只给丢失覆盖率的呆毛补回必要的边缘像素。目标来自同一源帧羽化之前的呆毛覆盖范围。
        # 始终从本帧绿色估计产生 alpha，不借用旧版透明度或旧颜色。
        original = smooth.crop(strand_box)
        reserve = alpha.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.MinFilter(3))
        reserve = reserve.filter(ImageFilter.GaussianBlur(.6)).crop(strand_box)
        target = strand_area
        if sum(original.histogram()[26:]) < target:
            low, high = 0., 1.
            for _ in range(8):
                mid = (low + high) / 2
                blended = Image.blend(original, reserve, mid)
                if sum(blended.histogram()[26:]) < target:
                    low = mid
                else:
                    high = mid
            smooth.paste(Image.blend(original, reserve, high), strand_box)
    return smooth.point(lambda v: 0 if v < 26 else v)


@lru_cache(maxsize=1)
def near_offsets():
    return sorted(((dx, dy) for dx in range(-5, 6) for dy in range(-5, 6)
                   if 0 < dx*dx + dy*dy <= 25), key=lambda d: (d[0]**2 + d[1]**2, d[1], d[0]))


def nearest_colors(rgb, alpha):
    """软边复制欧氏距离最近的 alpha≥250 像素。等距时以行、列顺序打破平局。"""
    core = alpha.point(lambda a: 255 if a >= 250 else 0)
    edge = alpha.point(lambda a: 255 if 0 < a < 250 else 0)
    if not edge.getbbox():
        return rgb.copy()
    if not core.getbbox():
        raise ValueError("半透明轮廓中找不到不透明借色点")
    out = rgb.copy()
    src, dst = rgb.load(), out.load()
    opaque = core.load()
    cached_rows = {}

    def nearest(x, y):
        for dx, dy in near_offsets():
            xx, yy = x + dx, y + dy
            if 0 <= xx < rgb.width and 0 <= yy < rgb.height and opaque[xx, yy]:
                return xx, yy
        # 极细发梢可能离实色超过 5 px。逐行二分定位，按当前最短距离结束搜索。
        best = (float("inf"), 0, 0)
        radius = 0
        while radius * radius <= best[0] and radius < rgb.height:
            rows = (y,) if radius == 0 else (y - radius, y + radius)
            for yy in rows:
                if not 0 <= yy < rgb.height:
                    continue
                if yy not in cached_rows:
                    row = core.crop((0, yy, rgb.width, yy + 1)).tobytes()
                    cached_rows[yy] = [i for i, on in enumerate(row) if on]
                xs = cached_rows[yy]
                pos = bisect_left(xs, x)
                for xx in xs[max(0, pos - 1):pos + 1]:
                    best = min(best, ((xx - x)**2 + radius**2, yy, xx))
            radius += 1
        return best[2], best[1]

    # getbbox 缩小扫描窗口；每一行的非零片段用 bytes.find 在 C 层跳过空白。
    x0, y0, x1, y1 = edge.getbbox()
    for y in range(y0, y1):
        row = edge.crop((x0, y, x1, y + 1)).tobytes()
        i = row.find(b"\xff")
        while i >= 0:
            x = x0 + i
            dst[x, y] = src[nearest(x, y)]
            i = row.find(b"\xff", i + 1)
    return out


def inner_borrow(rgb, alpha):
    # 只在实色内去溢绿。之后由这些实色向外拷贝，无需再改变软边的颜色。
    r, g, b = rgb.split()
    core_rgb = Image.merge("RGB", (r, ImageChops.darker(g, ImageChops.lighter(r, b)), b))
    out = nearest_colors(core_rgb, alpha)
    out.putalpha(alpha)
    out.paste((0, 0, 0, 0), mask=alpha.point(lambda v: 255 if not v else 0))
    return out


def metrics(frame):
    alpha = frame.getchannel("A")
    semi = alpha.point(lambda a: 255 if 0 < a < 255 else 0)
    solid = alpha.point(lambda a: 255 if a == 255 else 0)
    ring = ImageChops.darker(solid, semi.filter(ImageFilter.MaxFilter(3)))
    lum = frame.convert("RGB").convert("L")
    edge_lum = ImageStat.Stat(lum, semi).mean[0]
    inner_lum = ImageStat.Stat(lum, ring).mean[0]
    # 原色本身深的内侧描边不计。排除分母、分子使用同一有效区域。
    donor = nearest_colors(frame.convert("RGB"), alpha).convert("L")
    good = ImageChops.darker(semi, donor.point(lambda y: 255 if y >= 60 else 0))
    dark = lum.point(lambda y: 255 if y < 60 else 0)
    dark_count = ImageChops.darker(good, dark).histogram()[255]
    raw = ImageChops.darker(semi, dark).histogram()[255] / semi.histogram()[255]
    r, g, b, _ = frame.split()
    green = ImageChops.subtract(g, ImageChops.lighter(r, b)).point(lambda n: 255 if n > 12 else 0)
    return dict(edge_luma=edge_lum, inner_luma=inner_lum, ratio=edge_lum / inner_lum,
                raw_dark=raw, adjusted_dark=dark_count / max(1, good.histogram()[255]),
                green=ImageChops.darker(green, alpha.point(lambda a: 255 if a >= 8 else 0)).histogram()[255])
