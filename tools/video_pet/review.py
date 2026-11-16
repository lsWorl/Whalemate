"""为 idle 试点写审片图与测量报告。"""
import hashlib
import math

from PIL import Image, ImageDraw, ImageOps

DARK = (26, 30, 41)
WHITE = (255, 255, 255)


def flat(image, color):
    base = Image.new("RGB", image.size, color)
    base.paste(image, mask=image.getchannel("A"))
    return base


def gallery(path, items, size, columns):
    width, height = size
    canvas = Image.new("RGB", (width * columns, (height + 25) * math.ceil(len(items) / columns)), DARK)
    draw = ImageDraw.Draw(canvas)
    for i, (title, frame) in enumerate(items):
        x, y = i % columns * width, i // columns * (height + 25)
        picture = flat(frame, DARK)
        picture.thumbnail(size, Image.Resampling.LANCZOS)
        canvas.paste(picture, (x + (width - picture.width) // 2, y))
        draw.text((x + 8, y + height + 4), title, fill=WHITE)
    canvas.save(path)


def gif(path, frames, matte):
    # 固定全片调色板，避免相邻帧肤色闪烁。
    background = matte
    samples = Image.new("RGB", (128 * 8, 72 * math.ceil(len(frames) / 8)), background)
    for i, frame in enumerate(frames):
        samples.paste(flat(frame, background).resize((128, 72), Image.Resampling.BOX),
                      (i % 8 * 128, i // 8 * 72))
    palette = samples.quantize(colors=256)
    values = palette.getpalette()
    index = min(range(len(values) // 3), key=lambda i: sum(
        (values[i * 3 + c] - background[c]) ** 2 for c in range(3)))
    values[index * 3:index * 3 + 3] = background
    palette.putpalette(values)
    pictures = []
    for frame in frames:
        picture = flat(frame, background).quantize(palette=palette, dither=Image.Dither.NONE)
        picture.paste(index, mask=frame.getchannel("A").point(lambda a: 255 if a == 0 else 0))
        pictures.append(picture)
    # GIF 延时以 10 ms 计，分摊取整以免整段变速。
    durations = [10 * (round((i + 1) * 100 / 12) - round(i * 100 / 12)) for i in range(len(frames))]
    pictures[0].save(path, save_all=True, append_images=pictures[1:], loop=0,
                     duration=durations, optimize=False, disposal=1)


def four_backgrounds(path, frame, wallpaper):
    backgrounds = [("Black", Image.new("RGB", frame.size, "#000000")),
                   ("White", Image.new("RGB", frame.size, "#ffffff")),
                   ("Gray", Image.new("RGB", frame.size, "#808080"))]
    with Image.open(wallpaper) as windows:
        backgrounds.append(("Windows", ImageOps.fit(windows.convert("RGB"), frame.size)))
    board = Image.new("RGB", (frame.width * 2, (frame.height + 24) * 2), DARK)
    draw = ImageDraw.Draw(board)
    for i, (name, bg) in enumerate(backgrounds):
        bg.paste(frame, mask=frame.getchannel("A"))
        x, y = i % 2 * frame.width, i // 2 * (frame.height + 24)
        board.paste(bg, (x, y))
        draw.text((x + 8, y + frame.height + 4), name, fill=WHITE)
    board.save(path)


def previews(directory, frames, seconds, halves, times, stills, faces, face_box, anchor, wallpaper):
    durations = [round((i + 1) * 1000 / 12) - round(i * 1000 / 12) for i in range(len(frames))]
    frames[0].save(directory / "idle-transparent.apng", format="PNG", save_all=True,
                   append_images=frames[1:], loop=0, duration=durations, disposal=0, blend=0)
    four_backgrounds(directory / "first-four-backgrounds.png", frames[0], wallpaper)
    for name, matte in (("white", WHITE), ("dark", DARK)):
        gif(directory / f"idle-{name}.gif", frames, matte)
    gallery(directory / "seconds.png",
            [(f"{float(times[i]):.2f} s  /  #{i}", stills[i]) for i in seconds], (480, 270), 3)
    full = [(f"{float(times[i]):.2f} s  /  #{i}", stills[i]) for i in (0, len(times) - 1)]
    gallery(directory / "first-last.png", full, (640, 360), 2)
    gallery(directory / "loop-seam.png",
            [("Last played", frames[-1]), ("Next: first", frames[0])], frames[0].size, 2)
    items = [("Input anchor", anchor.crop(face_box))]
    items.extend((f"{float(times[i]):.2f} s  /  #{i}", faces[i]) for i in halves)
    gallery(directory / "faces.png", items, (285, 244), 4)


def extent(values):
    return f"{min(values):g} 至 {max(values):g}"


def write_report(args, times, selected, tracks, diffs, tile_green, anchor_foot, edge_report):
    stamp = (min(t.stamp[0] for t in tracks), min(t.stamp[1] for t in tracks),
             max(t.stamp[2] for t in tracks), max(t.stamp[3] for t in tracks))
    closest = min(tracks, key=lambda t: t.distance)
    origin = tracks[0].feet
    residual = max(math.hypot(*(f + d - base for f, d, base in zip(t.feet, t.offset, origin)))
                   for t in tracks)
    raw_green = max(t.green for t in tracks)
    rows = []
    for label, (rgb, alpha, iou) in zip(
        ("原片首帧与原片末帧（抠绿后、平移前）", "首帧与末帧（抠绿、平移后）",
         "视频首帧与输入锚点（相同画布与抠绿）", "实际循环接缝（图集末格与首格）"), diffs
    ):
        rows.append(f"| {label} | {rgb:.3f} | {alpha:.3f} | {iou:.3%} |")
    path = args.review / "report.md"
    path.write_text("\n".join([
        "# 鲸鲸待机 idle-v1 审片报告", "",
        "即梦网页版 · Seedance 2.0 mini · 首尾帧 · 免费额度。生成时间为 2026-11-14 17:15 HKT。",
        "首尾共用 `anchor-a-16x9.png`。实际输入为长裙、侧身的鲸鲸，尾巴在画面左侧。", "",
        f"- 原始 MP4 为 {args.input.stat().st_size:,} 字节，SHA-256 为 `{hashlib.sha256(args.input.read_bytes()).hexdigest()}`。",
        f"- 锚点 SHA-256 为 `{hashlib.sha256(args.anchor.read_bytes()).hexdigest()}`。", "",
        "## 逐帧处理", "",
        f"- 原片为 1280×720。按显示时间解码 {len(times)} 帧，首帧 {float(times[0]):.3f} 秒，末帧 {float(times[-1]):.3f} 秒。未使用音轨作计时依据。",
        f"- 左上角“AI 生成”水印在全部 {len(tracks)} 帧中的文字与描边外接框并集为 {stamp}（红、蓝任一通道 >25）。"
        f"遮罩为固定矩形 (8, 8, 132, 78)。其余画面保持原幅。水印可见框到角色可见像素全片至少 {closest.distance:.1f} px，"
        f"最近出现在源帧 #{closest.index}，{float(times[closest.index]):.3f} 秒。坐标从左上起，右、下边界不含。",
        "- 在 HSV 绿色范围内按每像素背景场估计透明度。RGB 三通道与绿幕样本权重分别作大半径模糊，再归一化外推。实色内部去溢绿，软边从最近的实色像素复制颜色。"
        "以 alpha ≥8 且 G−max(R,B)>12 计数，"
        f"原片处理后各帧最多 {raw_green} 个偏绿像素，图集缩放后各帧最多 {tile_green} 个。",
        f"- 锚点缩到 720p 后，双鞋中心线及平均鞋底为 ({anchor_foot[0]:g}, {anchor_foot[1]:g})，"
        f"视频首帧为 ({origin[0]:g}, {origin[1]:g})。在首帧确定的两个固定足部窗口中逐帧测量双鞋，"
        f"仅将整帧作整数平移，横向修正 {extent([t.offset[0] for t in tracks])} px，纵向修正 {extent([t.offset[1] for t in tracks])} px。"
        f"足部测点对首帧的最大残差 {residual:.2f} px。保留呼吸、头部和发梢的相对运动。",
        f"- 依据实际 PTS 取距 12 Hz 时刻最近的源帧，排除返回锚点的源末帧 #{len(times)-1}。"
        f"实际播放 {len(selected)} 帧 / {len(selected)/12:.3f} 秒，最后播放源帧 #{selected[-1]}（{float(times[selected[-1]]):.3f} 秒）。",
        f"- 单帧 512×288、每行 8 格、共 {math.ceil(len(selected)/8)} 行；无损 RGBA WebP 规格同本机 Aboutme 安装器。"
        "清单只有 idle 动作。播放器必需的 talking、thinking 姿势均引用 idle。", "",
        "## 差异", "",
        "RGB MAE 为预乘颜色的三通道平均绝对差，alpha MAE 为透明度平均绝对差，均以 0–255 为单位。"
        "分母为两帧 alpha ≥8 的角色区域并集。轮廓 IoU 用 alpha ≥128 的交集除以并集。"
        "前三项在 1280×720 上比较，实际接缝在 512×288 图集尺寸上比较。", "",
        "| 比较对象 | RGB MAE | Alpha MAE | 轮廓 IoU |", "| --- | ---: | ---: | ---: |", *rows, "",
        *edge_report,
        "## 动作观察", "",
        "与提示词相比，闭眼停留明显偏长。0.5、1.0、1.5、2.0 秒的脸部样本均闭眼，其中多个样本还张开嘴。"
        "2.5 秒短暂半睁，3.0 秒歪头时再次闭眼，4.0 秒已睁眼。首尾轮廓接近，中段的表现更像打哈欠。", "",
        "## 审片文件", "",
        "[保留半透明的 APNG](idle-transparent.apng) · [白底循环](idle-white.gif) · [深底循环](idle-dark.gif)。"
        "[黑、白、中灰与 Windows 壁纸上的首帧](first-four-backgrounds.png)。",
        "[每秒全身拼图](seconds.png) · [视频首末帧](first-last.png) · [实际循环接缝](loop-seam.png) · "
        "[锚点及每半秒脸部特写](faces.png)。", "",
        "图集、封面与清单在 [idle 包](../../t24-pack/idle/)。完整提示词、平台信息与原片哈希在 "
        "[生成记录](../../../art/deepseek-whale-video/prompts.json)。", "",
    ]), encoding="utf-8")
