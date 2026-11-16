"""按播放帧与原片时间分别度量，生成审片材料。"""
import csv
import json
import math
from statistics import mean

from PIL import Image

if __package__:
    from . import edges, idle, review
else:
    import edges
    import idle
    import review


def foot_stats(records, order, *, cyclic):
    """支撑脚滑动用同一只鞋在相邻两张均接近地面时的横移衡量。"""
    valid = [r for r in records if r["feet"]]
    reference = tuple(map(mean,zip(*records[0]["feet"])))
    distances = []
    ground = max(f[1] for r in valid for f in r["feet"])
    tolerance = max(2, round(ground*.008))
    for r in valid:
        midpoint = tuple(map(mean,zip(*r["feet"])))
        point = [a+b for a,b in zip(midpoint,r["offset"])]
        distances.append(math.dist(reference,point))
    shifts = []
    pairs = list(zip(order,order[1:]))
    if cyclic:
        pairs.append((order[-1],order[0]))
    for i,j in pairs:
        a,b=records[i],records[j]
        if not a["feet"] or not b["feet"]:
            continue
        for k in range(2):
            x,y = a["feet"][k]
            u,v = b["feet"][k]
            if min(y,v) >= ground-tolerance:
                shifts.append(abs(u+b["offset"][0]-x-a["offset"][0]))
    return dict(drift=max(distances),valid=len(valid),ground=ground,tolerance=tolerance,
                slip_count=len(shifts),slip_mean=mean(shifts) if shifts else None,
                slip_max=max(shifts) if shifts else None)


def measurements(played, records, order, raw, anchor, loop):
    qc = [edges.metrics(f) for f in played]
    hairs = [r for r in records if r["hair_before"]]
    foot = foot_stats(records,order,cyclic=loop["method"]=="同脚完整周期")
    return dict(anchor=idle.compare(raw[0],anchor),ends=idle.compare(raw[0],raw[-1]),
                seam=idle.compare(played[-1],played[0]),loop=loop,feet=foot,
                count=len(played),raw_dark=max(x["raw_dark"] for x in qc),
                dark=max(x["adjusted_dark"] for x in qc),green=max(x["green"] for x in qc),
                luma_ratio=min(x["ratio"] for x in qc), hair_n=len(hairs),
                hair_range=[min(r["hair_after"] for r in hairs),max(r["hair_after"] for r in hairs)],
                hair_loss=max(0,max(1-r["hair_after"]/r["hair_before"] for r in hairs))), qc


def face_gallery(target, anchor_face, faces, records):
    times = [record["time"] for record in records]
    samples = [min(range(len(times)), key=lambda i: abs(times[i] - tick / 2))
               for tick in range(int(2 * times[-1]) + 1)]
    items = [("Anchor", anchor_face)]
    items.extend((f"{times[i]:.2f} s", faces[i]) for i in samples)
    review.gallery(target / "faces.png", items, (234, 203), 4)


def previews(target, played, raw, faces, records, anchor_face, wallpaper, once):
    target.mkdir(parents=True, exist_ok=True)
    durations=[round((i+1)*1000/idle.FPS)-round(i*1000/idle.FPS) for i in range(len(played))]
    played[0].save(target/"preview.apng",format="PNG",save_all=True,append_images=played[1:],
                   duration=durations,loop=1 if once else 0,disposal=0,blend=0)
    times=[r["time"] for r in records]
    def source_cell(t):
        return min(range(len(times)),key=lambda i:abs(t-times[i]))
    seconds=[source_cell(t) for t in range(int(times[-1])+1)]
    review.gallery(target/"seconds.png",[(f"{times[i]:.2f} s",raw[i]) for i in seconds],(420,236),3)
    review.gallery(target/"first-last.png",[("Source first",raw[0]),("Source last",raw[-1]),
                   ("Playback last",played[-1]),("Playback first",played[0])],idle.TILE,2)
    face_gallery(target, anchor_face, faces, records)
    review.four_backgrounds(target/"first-four-backgrounds.png",played[0],wallpaper)


def write(target, clip, action, metrics, tracks, quality, order, meta, note):
    m=metrics
    lp=m["loop"]
    f=m["feet"]
    with (target/"frames.csv").open("w",encoding="utf-8",newline="") as stream:
        writer=csv.writer(stream)
        writer.writerow(["source_index","time_s","feet_xy","translation_xy","strand_box","hair_before","hair_after"])
        for r in tracks:
            writer.writerow([r["source"],r["time"],r["feet"],r["offset"],r["hair_box"],r["hair_before"],r["hair_after"]])
    with (target/"playback.csv").open("w",encoding="utf-8",newline="") as stream:
        writer=csv.writer(stream)
        writer.writerow(["playback_frame","primary_source_index","raw_dark","adjusted_dark","green","luma_ratio"])
        for i,(q,idx) in enumerate(zip(quality,order,strict=True)):
            writer.writerow([i,tracks[idx]["source"],q["raw_dark"],q["adjusted_dark"],q["green"],q["ratio"]])
    (target/"metrics.json").write_text(json.dumps(m,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines=[f"# {clip} 审片", "", note, "",
           f"源片为 {meta['size'][0]}×{meta['size'][1]}、{meta['count']} 个解码帧。首帧 0 秒，末帧 {tracks[-1]['time']:.3f} 秒。"
           f"按 PTS 取样，输出 {m['count']} 帧，12 fps，{m['count']/12:.3f} 秒。单格 512×288。", "",
           f"四角逐帧检测 {meta['count']} 张，显著亮标记区域为 {meta['marks']}（空列表表示未检出，未加遮罩）。"
           f"足部搜索框由本片首帧的鞋底与角色高度确定，为 {meta['foot_window']}。", "",
           "## 动作与接缝", "", f"制作方式为 **{lp['method']}**。"]
    if "fade" in lp:
        lines.append(f"首尾交叉淡化 0.3 秒的接缝 RGB MAE 为 {lp['fade']:.3f}，正放接倒放为 {lp['reverse']:.3f}。"
                     "在 12 Hz 网格上用 4 个重叠采样格实现淡化；倒放省略重复端点。按实际尾格接首格 MAE 较小者选用。")
    if "start" in lp:
        basis=("由审片人按侧身步态周期指定（侧身时双脚重叠，自动检测不适用）。" if lp["method"].startswith("人工")
               else "同一脚两次抬起间包含对侧抬脚。")
        caveat="源片会转身，单周期仍有姿态跳变。" if clip=="walk-v1" else ""
        lines.append(f"{basis}选取 [{tracks[lp['start']]['time']:.3f}, "
                     f"{tracks[lp['end']]['time']:.3f}) 秒，回到同相位时全身 MAE {lp['phase_mae']:.3f}。"
                     f"走动按桌面移动距离推进步态。{caveat}")
    if action=="wave":
        lines.append("按时间顺序播放全部动作，APNG 播放一次。桌宠通过“做个动作”中的挥手指令播放一次后恢复原姿势。"
                     "下面的首末差异用来评估回姿；不把挥手拼成循环。")
    lines.extend(["","| 对照 | 预乘 RGB MAE / 255 | Alpha MAE / 255 | 轮廓 IoU |",
                  "| --- | ---: | ---: | ---: |"])
    for label,key in (("源首帧与锚点","anchor"),("源首末帧","ends"),("播放末格与首格","seam")):
        rgb,alpha,iou=m[key]
        lines.append(f"| {label} | {rgb:.3f} | {alpha:.3f} | {iou:.2%} |")
    lines.extend(["","全部差异均以 512×288 比较，分母为 alpha≥8 的角色像素并集；IoU 用 alpha≥128。",
          "", "## 边缘、呆毛与鞋底", "",
          f"- 每帧校正偏暗比例最大 {m['dark']:.2%}，含原线稿时最大 {m['raw_dark']:.2%}。"
          f"软边／实色内圈亮度比最低 {m['luma_ratio']:.2%}，偏绿像素每帧最多 {m['green']}。"
          "偏暗为亮度<60；校正值排除最近实色本身偏暗的位置。偏绿为 alpha≥8 且 G−max(R,B)>12。",
          f"- 呆毛可测 {m['hair_n']}/{len(tracks)} 张后期取样帧，原分辨率可见面积 {m['hair_range'][0]}–{m['hair_range'][1]} px。"
          f"以每帧羽化前的同一窗口为基准，alpha≥26 面积最大损失 {m['hair_loss']:.2%}。"
          "角色形态变化或双鞋遮挡记为缺测。",
          f"- 双鞋可定位 {f['valid']}/{len(tracks)} 张。对源首帧的双鞋中点最大位移（含校正）为 {f['drift']:.2f} px。"
          "待机与说话对齐双鞋，挥手及行走保留腾空和横移。"])
    if f["slip_count"]:
        lines.append(f"- 支撑脚在相邻两个采样都距实测地面 y={f['ground']} 不超过 {f['tolerance']} px 时计入。"
                     f"共 {f['slip_count']} 对，每次横移均值 {f['slip_mean']:.2f} px，最大 {f['slip_max']:.2f} px。"
                     "按屏幕左右分别追踪鞋底中点，遮挡时剔除。行走另计循环边界，结果以源画布像素计。")
    lines.extend(["","[透明 APNG](preview.apng) · [每秒全身](seconds.png) · [原片首末及实际接缝](first-last.png) · "
                  "[半秒脸部](faces.png) · [四种背景](first-four-backgrounds.png)", "",
                  "[源帧测量](frames.csv) · [成品帧测量](playback.csv) · [指标数据](metrics.json)",""])
    (target/"report.md").write_text("\n".join(lines),encoding="utf-8")
