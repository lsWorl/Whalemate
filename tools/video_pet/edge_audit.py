"""按旧版同帧的固定呆毛区域进行验收，并保留逐帧追溯数据。"""
import csv
import hashlib
import json

if __package__:
    from . import edges
else:
    import edges


def load_baseline(path, clip):
    ref = json.loads(path.read_text(encoding="utf-8"))
    if hashlib.sha256(clip.read_bytes()).hexdigest() != ref["video_sha256"]:
        raise ValueError("输入视频与边缘修复前的验收基线不匹配")
    return ref


def hair_counts(frame, old):
    after = edges.area(frame, tuple(old["box"]))
    return dict(index=old["index"], before_area=old["area"], after_area=after,
                loss=1 - after / old["area"])


def assess(frames, reference, sources, directory):
    tiles = []
    for frame, old in zip(frames, reference["tiles"], strict=True):
        check = hair_counts(frame, old)
        new = edges.metrics(frame)
        check.update({f"before_{k}": old[k] for k in new})
        check.update({f"after_{k}": v for k, v in new.items()})
        tiles.append(check)
    for name, rows in (("edge-frames.csv", tiles), ("edge-source-frames.csv", sources)):
        with (directory / name).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
    failed = [(s["index"], "源帧呆毛") for s in sources if s["loss"] > .100000001]
    for row in tiles:
        for ok, item in [(row["after_adjusted_dark"] <= .03, "偏暗比例"),
                         (row["after_ratio"] >= .90, "边缘亮度"),
                         (row["after_green"] == 0, "偏绿像素"),
                         (row["loss"] <= .100000001, "图集呆毛")]:
            if not ok:
                failed.append((row["index"], item))
    first = tiles[0]
    worst_native = max(sources, key=lambda r: r["loss"])
    worst_tile = max(tiles, key=lambda r: r["loss"])
    result = "通过" if not failed else f"失败 {failed}"
    lines = ["## 边缘修复对照", "",
             "源帧比较使用同一视频帧、同一足部配准；图集比较使用相同的 512×288 播放格。"
             "旧图集 SHA-256 为 " + reference["old_sheet_sha256"] + "。", "",
             "| 指标 | 修复前 | 修复后 |", "| --- | ---: | ---: |",
             f"| 首帧软边平均亮度 | {first['before_edge_luma']:.2f} | {first['after_edge_luma']:.2f} |",
             f"| 首帧内侧实色一圈平均亮度 | {first['before_inner_luma']:.2f} | {first['after_inner_luma']:.2f} |",
             f"| 首帧偏暗比例（含深色原线稿） | {first['before_raw_dark']:.2%} | {first['after_raw_dark']:.2%} |",
             f"| 全片最差偏暗比例（排除内侧原色也深的像素） | {max(r['before_adjusted_dark'] for r in tiles):.2%} | {max(r['after_adjusted_dark'] for r in tiles):.2%} |",
             f"| 全片最低软边 / 内圈亮度 | {min(r['before_ratio'] for r in tiles):.2%} | {min(r['after_ratio'] for r in tiles):.2%} |",
             f"| 任一播放帧偏绿像素最大值 | {max(r['before_green'] for r in tiles)} | {max(r['after_green'] for r in tiles)} |",
             f"| 源帧呆毛减幅最大的一帧 #{worst_native['index']} | {worst_native['before_area']} px | {worst_native['after_area']} px（减少 {worst_native['loss']:.2%}） |",
             f"| 图集呆毛减幅最大的一帧 #{worst_tile['index']} | {worst_tile['before_area']} px | {worst_tile['after_area']} px（减少 {worst_tile['loss']:.2%}） |", "",
             "亮度采用 Pillow 的 L（BT.601），软边包括所有 0<alpha<255 像素；内侧一圈为与软边八邻接、"
             "alpha=255 的实色像素。偏暗指亮度<60，校正比例的分子、分母都排除最近 alpha≥250 内侧颜色本身<60 的位置。"
             "边缘借色使用欧氏距离，源帧和缩小图集均在自身尺寸处理。720p 上先内收 1 px，再作 σ=0.6 px 羽化，"
             "alpha<26 清零；细线预留覆盖率，呆毛依据自身软覆盖范围作局部补偿。", "",
             "呆毛以旧帧的发顶与帽檐上缘定位，两版在同一矩形内统计 alpha≥26 的像素。"
             f"以旧版为分母，源帧 {len(sources)} 张、播放帧 {len(tiles)} 张逐张检查。"
             f"自动检查结果为 **{result}**。阈值为校正偏暗比例≤3%、软边亮度≥内圈90%、"
             "偏绿像素为0、呆毛减幅≤10%。[逐播放帧数据](edge-frames.csv) · [逐源帧数据](edge-source-frames.csv)。", ""]
    return lines, failed
