"""按源画面的绿幕、鞋子及动作类型处理首帧驱动的视频。"""
import json
import math
from statistics import mean, median

from PIL import Image, ImageChops, ImageStat

if __package__:
    from . import edges, idle
else:
    import edges
    import idle


def corner_mark(rgb):
    """扫描四角，用局部颜色而非固定绿色检测白色/半透明标记。"""
    w, h = rgb.size
    cw, ch = w // 5, h // 6
    rectangles = []
    for x in (0, w - cw):
        for y in (0, h - ch):
            patch = rgb.crop((x, y, x + cw, y + ch))
            r, _, b = patch.split()
            rb = ImageChops.lighter(r, b)
            base = ImageStat.Stat(rb).median[0]
            mask = rb.point(lambda v: 255 if v > base + 35 else 0)
            box = mask.getbbox()
            if box and mask.histogram()[255] >= 18:
                rectangles.append((x + box[0], y + box[1], x + box[2], y + box[3]))
    return rectangles


def components(mask):
    """仅扫描脚部的小窗口。八邻接的扫描线游程可避开鞋面高光和压缩噪声。"""
    previous = []
    labels = []
    parents = []
    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    for y in range(mask.height):
        row = mask.crop((0, y, mask.width, y + 1)).tobytes()
        current = []
        x = row.find(b"\xff")
        while x >= 0:
            right = row.find(b"\x00", x)
            if right < 0:
                right = len(row)
            touching = [root(k) for a, b, k in previous if a <= right and b >= x]
            if touching:
                k = min(touching)
                for j in touching:
                    parents[j] = k
            else:
                k = len(parents)
                parents.append(k)
            labels.append((x, y, right, k))
            current.append((x, right, k))
            x = row.find(b"\xff", right)
        previous = current
    groups = {}
    for a, y, b, k in labels:
        groups.setdefault(root(k), []).append((a, y, b))
    return groups.values()


def find_feet(frame, search=None):
    mask = frame.getchannel("A").point(lambda a: 255 if a >= 128 else 0)
    bounds = mask.getbbox()
    if bounds is None:
        return None
    left, top, right, bottom = bounds
    height = bottom - top
    box = search or (left, bottom - round(height * .085), right, bottom + 1)
    r, g, b, a = frame.crop(box).split()
    ink = ImageChops.darker(ImageChops.lighter(r, g).point(lambda v: 255 if v < 115 else 0),
                            a.point(lambda v: 255 if v >= 180 else 0))
    ink = ImageChops.darker(ink, b.point(lambda v: 255 if 22 < v < 165 else 0))
    shoes = []
    for runs in components(ink):
        x0 = min(a for a, _, _ in runs)
        x1 = max(b for _, _, b in runs)
        y1 = max(y for _, y, _ in runs)
        y0 = min(y for _, y, _ in runs)
        mass = sum(b - a for a, _, b in runs)
        if not (.022 * height < x1 - x0 < .15 * height and
                .012 * height < y1 - y0 < .16 * height and mass > .00034 * height**2):
            continue
        pad = max(2, round(height * .009))
        xs = [x for a, y, b in runs if y >= y1 - pad for x in range(a, b)]
        shoes.append((box[0] + median(xs), box[1] + y1, mass))
    shoes.sort(key=lambda s: s[1], reverse=True)
    if len(shoes) < 2:
        return None
    if shoes[0][1] - shoes[1][1] > height * .12:
        return None  # 一只鞋被遮住时，裙摆不能充当第二只脚。
    return sorted([(x, y) for x, y, _ in shoes[:2]])


def foot_window(frame, feet):
    height = frame.getbbox()[3] - frame.getbbox()[1]
    x0, y0 = feet[0]
    x1, y1 = feet[1]
    return (max(0, round(x0-height*.23)), max(0, round(min(y0,y1)-height*.16)),
            min(frame.width, round(x1+height*.23)), min(frame.height, round(max(y0,y1)+height*.09)))


def face_box(frame, feet):
    box = frame.getbbox()
    height = box[3] - box[1]
    cx = mean(f[0] for f in feet)
    return (round(cx - height * .35), round(box[1] - height*.025),
            round(cx + height * .35), round(box[1] + height * .52))


def is_whale_scene(rgb):
    # 蓝白卡通鲸布景是淡绿幕。用整幅四角的中位数定位该片变身段（其他动作不使用此判据）。
    w, h = rgb.size
    corners = [(w//12,h//12),(w-w//12,h//12),(w//12,h-h//12),(w-w//12,h-h//12)]
    return (median(rgb.getpixel(p)[0] for p in corners) >= 75 and
            median(abs(rgb.getpixel(p)[0]-rgb.getpixel(p)[2]) for p in corners) < 35)


def keyed_frame(directory, index, rgb, transformed):
    """抠绿是最慢的一步；结果按帧缓存到 --cache 目录，长片可以分几次跑完。"""
    keyed = directory / "keyed"
    keyed.mkdir(exist_ok=True)
    image, record = keyed / f"{index:04}.png", keyed / f"{index:04}.json"
    if image.exists() and record.exists():
        measure = json.loads(record.read_text(encoding="utf-8"))
        if measure.get("strand_box") is not None:
            measure["strand_box"] = tuple(measure["strand_box"])
        with Image.open(image) as im:
            return im.convert("RGBA"), measure
    measure = {}
    frame = idle.chroma_key(rgb, character_strand=not transformed, measure=measure)
    frame.save(image)
    record.write_text(json.dumps(measure, ensure_ascii=False, default=list), encoding="utf-8")
    return frame, measure


def sample_clip(args, directory):
    directory.mkdir(parents=True, exist_ok=True)
    raw = directory / "raw"
    raw.mkdir(exist_ok=True)
    times = idle.decode(args.input, args.ffmpeg, raw)
    selection = idle.playback_indices(times)
    needed = sorted(set(selection + [len(times)-1]))
    mark_union = set()
    # 全片检测角落，再决定是否遮罩；无水印时保留原画幅。
    for i in range(len(times)):
        with Image.open(raw / f"{i:04}.png") as im:
            mark_union.update(corner_mark(im.convert("RGB")))
    tiles = []
    data = []
    faces = []
    for i in needed:
        with Image.open(raw / f"{i:04}.png") as im:
            rgb = im.convert("RGB")
        for x0,y0,x1,y1 in mark_union:
            rect = (max(0,x0-3),max(0,y0-3),min(rgb.width,x1+3), min(rgb.height,y1+3))
            rgb.paste(rgb.getpixel((rgb.width//8, rgb.height//2)), rect)
        # 旧说话原片的变身段经过人工核实。其他视频使用淡绿幕时仍按女仆处理。
        transformed = args.input.stem == "talk-v1" and is_whale_scene(rgb)
        frame, measure = keyed_frame(directory, i, rgb, transformed)
        if not tiles:
            first_feet = find_feet(frame)
            if first_feet is None:
                raise ValueError("首帧双鞋未识别，请检查足部窗口")
            window = foot_window(frame, first_feet)
            origin = tuple(map(mean, zip(*first_feet)))
            face = face_box(frame, first_feet)
        feet = None if transformed else find_feet(frame, window)
        # 挥手片有跳跃，步行片有抬脚；保留腾空位移和足底滑移供实测。
        offset = (0,0)
        if args.strategy=="loop" and feet:
            midpoint = tuple(map(mean, zip(*feet)))
            offset = tuple(round(a-b) for a,b in zip(origin, midpoint))
        framed = idle.translate(frame,*offset)
        tiles.append(idle.tile_frame(framed))
        face_rect = face
        if transformed:
            l,t,r,b = frame.getbbox()
            face_rect = (l,t,round(l+(r-l)*.76),round(t+(b-t)*.65))
        faces.append(framed.crop(face_rect))
        data.append(dict(source=i,time=float(times[i]),feet=feet,offset=offset,whale=transformed,
                         hair_before=measure["hair_before"],hair_after=measure["hair_after"],
                         hair_box=measure["strand_box"]))
    meta = dict(size=rgb.size, count=len(times), selection=selection, foot_window=window,
                face=face, marks=sorted(mark_union), times=[float(t) for t in times])
    return tiles, data, faces, meta


def premultiplied_mix(a,b,t):
    frame = Image.blend(a.convert("RGBa"), b.convert("RGBa"), t).convert("RGBA")
    alpha = frame.getchannel("A").point(lambda v: 0 if v<26 else v)
    return edges.inner_borrow(frame.convert("RGB"),alpha)


def gait_candidates(records, frames):
    """以同脚相邻峰为周期，两次峰间必须观测到对侧抬脚，排除半步。"""
    heights = [r["feet"][1][1] - r["feet"][0][1] if r["feet"] else None for r in records]
    first_feet = records[0]["feet"]
    lift = max(2, round(.20 * (first_feet[1][0] - first_feet[0][0])))
    smooth = []
    for i in range(len(heights)):
        window = [v for v in heights[max(0, i - 1):i + 2] if v is not None]
        smooth.append(median(window) if window else 0)

    candidates = []
    for sign in (-1, 1):
        run = []
        peaks = []
        for i, height in enumerate(smooth + [0]):
            if sign * height > lift:
                run.append(i)
            elif run:
                peaks.append(max(run, key=lambda j: sign * smooth[j]))
                run = []
        for a, b in zip(peaks, peaks[1:]):
            if not 6 <= b - a <= 36 or min(sign * v for v in smooth[a:b]) >= -lift:
                continue
            for start in range(max(1, a - 2), min(len(frames) - 1, a + 3)):
                for end in range(max(start + 2, b - 2), min(len(frames) - 1, b + 3)):
                    if heights[start] is None or heights[end] is None:
                        continue
                    if abs(heights[start] - heights[end]) > lift:
                        continue
                    rising = smooth[start + 1] - smooth[start - 1]
                    returning = smooth[end + 1] - smooth[end - 1]
                    if rising * returning < 0:
                        continue
                    # end 是下个周期的起点。按实际播放的最后一格与首格评分。
                    seam = idle.compare(frames[start], frames[end - 1])[0]
                    phase = idle.compare(frames[start], frames[end])[0]
                    # 若原片自身精确回到同一格，截在重复格之前，免得造出一格停顿。
                    exact_return = phase == 0 and frames[start].tobytes() == frames[end].tobytes()
                    candidates.append((not exact_return, seam, phase, start, end))
    if not candidates:
        raise ValueError(f"未找到左右交替的完整步态周期：{heights}")
    _, _, phase, start, end = min(candidates)
    return phase, start, end


def plan_loop(strategy, frames, records):
    if strategy=="once":
        return frames, list(range(len(frames))), dict(method="单次播放")
    if strategy=="gait":
        error,a,b=gait_candidates(records, frames)
        return frames[a:b],list(range(a,b)),dict(method="同脚完整周期",start=a,end=b,phase_mae=error)
    overlap = min(round(.3 * idle.FPS), len(frames) // 2)
    if overlap == 0:
        return frames, [0], dict(method="单帧静止")
    # 4 个格点覆盖连续 0.3 秒淡化。将接头旋回片尾，统一用实际播放末格接首格评分。
    duration = .3
    blended=[premultiplied_mix(a,b,min(1,(i+.5)/idle.FPS/duration))
             for i,(a,b) in enumerate(zip(frames[-overlap:],frames[:overlap]))]
    fade=frames[overlap:-overlap]+blended
    back=frames+frames[-2:0:-1]
    diffs=dict(fade=idle.compare(fade[-1],fade[0])[0],reverse=idle.compare(back[-1],back[0])[0])
    if diffs["fade"]<=diffs["reverse"]:
        return fade, list(range(overlap,len(frames))), dict(method="首尾交叉淡化 0.3 秒",**diffs)
    return back, list(range(len(frames)))+list(range(len(frames)-2,0,-1)), dict(method="正放接倒放",**diffs)


def make_sheet(frames, folder, action):
    folder.mkdir(parents=True,exist_ok=True)
    w,h=idle.TILE
    cols=idle.COLUMNS
    rows=math.ceil(len(frames)/cols)
    atlas=Image.new("RGBA",(w*cols,h*rows))
    for i,frame in enumerate(frames):
        atlas.paste(frame,(i%cols*w,i//cols*h))
    atlas.save(folder/f"{action}.webp",lossless=True,method=4,exact=True)
    return dict(name=action,image=f"{action}.webp",frameWidth=w,frameHeight=h,columns=cols,
                rows=rows,frameCount=len(frames),fps=idle.FPS,facing="right")


def render_clip(args, raw, records, faces, meta):
    if __package__:
        from . import action_review
    else:
        import action_review
    with Image.open(args.anchor) as im:
        # 锚点和原片的纵横比略有差别，按实际视频画幅比较。
        anchor_native=im.convert("RGB").resize(tuple(meta["size"]),Image.Resampling.LANCZOS)
    keyed_anchor = idle.chroma_key(anchor_native, character_strand=True)
    anchor = idle.tile_frame(keyed_anchor)
    # 在最后的 1/12 秒内显示真正的源末帧，保留 5 秒并检查实际回姿。
    slot_by_source = {record["source"]: index for index, record in enumerate(records)}
    primary = [slot_by_source[source] for source in meta["selection"]]
    if len(primary) > 1:
        primary[-1] = len(raw) - 1
    sampled=[raw[i] for i in primary]
    manual=getattr(args,"loop",None)
    if manual:
        # 侧身行走双脚重叠，自动步态检测不适用；由审片人按步态周期指定 [start, end)。
        a,b=manual
        if not 1<=a<b<=len(sampled):
            raise ValueError(f"--loop 超出取样范围 1..{len(sampled)}")
        played,order,loop=sampled[a:b],list(range(a,b)),dict(method="人工指定步态周期",start=a,end=b,
                                                    phase_mae=idle.compare(sampled[a],sampled[min(b,len(sampled)-1)])[0])
    else:
        played,order,loop=plan_loop(args.strategy,sampled,[records[i] for i in primary])
    order=[primary[i] for i in order]
    stats,qc=action_review.measurements(played,records,order,raw,anchor,loop)
    if stats["green"] or stats["dark"]>.03 or stats["luma_ratio"]<.9 or stats["hair_loss"]>.1:
        raise ValueError(f"边缘验收失败 {stats}")
    target=args.review
    action_review.previews(target, played, raw, faces, records, keyed_anchor.crop(meta["face"]),
                           args.wallpaper, args.strategy == "once")
    note=f"{clip_source(args.input)}。动作 ID 为 {args.action}。"
    if any(r["whale"] for r in records):
        time=next(r["time"] for r in records if r["whale"])
        note+=f" **角色失真**。约 {time:.3f} 秒开始换成悬空的蓝色卡通鲸，失去女仆造型、鞋子和呆毛。此版仅供新旧对照。"
    elif args.input.stem=="talk-v2":
        note+=" 新版保留女仆和双脚，转向另一侧说话，背景由纯绿转为黄绿；首帧朝向与中段并不相同。"
    elif args.action=="wave":
        note+=" 抬手时角色跳起，随后放下手。首末轮廓见下表。"
    action_review.write(target,args.input.stem,args.action,stats,records,qc,order,meta,note)
    entry=make_sheet(played,args.pack,args.action)
    # 此文件只描述本动作；整包入口由组包时一次性写入。
    (target/"action.json").write_text(json.dumps(entry,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    if args.action=="idle":
        played[0].save(args.pack/"poster.png")
    return entry


def clip_source(path):
    """从 prompts.json 读取本片的生成平台、模型和模式，读不到时如实说明。"""
    try:
        data=json.loads((path.parent.parent/"prompts.json").read_text(encoding="utf-8"))
        entry=next(v for v in data["videos"] if v.get("id")==path.stem)
        return f"{entry.get('platform','未知平台')} {entry.get('model','未知模型')}，{entry.get('mode','未知模式')}"
    except (OSError,ValueError,StopIteration,KeyError):
        return "生成来源未在 prompts.json 中登记"


def process_clip(args, directory):
    return render_clip(args,*sample_clip(args,directory))
