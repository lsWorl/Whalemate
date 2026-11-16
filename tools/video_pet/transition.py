"""从已抠图的单次播放图集中截取一段，写成过渡（成对或单向）或循环动作。

用法示例（先用 idle.py --action turn --strategy once 把转身片处理成图集）：
    python -m tools.video_pet.transition --pack output/t24-pack-b2 --source output/t24-turn/turn.webp \
        --range 12:30 --frames 9 --from idle --to walk

播放器限制单段过渡 2 帧以上、不超过 3 秒（12 fps 时最多 36 帧）。
"""
import argparse
import json
import math
from pathlib import Path

from PIL import Image

if __package__:
    from . import idle
else:
    import idle

MAX_SECONDS = 3.0  # 与 characters.animation_pack.MAX_TRANSITION_SECONDS 一致


def pick(start, end, count):
    """在 [start, end] 闭区间内等距取 count 帧，保留两个端点。"""
    if count < 2 or end <= start:
        raise ValueError("过渡至少 2 帧，且区间终点要大于起点")
    return [round(start + (end - start) * i / (count - 1)) for i in range(count)]


def read_frames(source, indices):
    w, h = idle.TILE
    with Image.open(source) as sheet:
        sheet = sheet.convert("RGBA")
        columns = sheet.width // w
        total = columns * (sheet.height // h)
        if max(indices) >= total:
            raise ValueError(f"区间终点 {max(indices)} 超出图集 {total} 格")
        return [sheet.crop((i % columns * w, i // columns * h, i % columns * w + w, i // columns * h + h))
                for i in indices]


def segment_name(source, start, end, count):
    """同一源图集、同一区间和帧数的过渡共用一张图，多对动作之间不重复存图。"""
    return f"transition-{Path(source).stem}-{start}-{end}-{count}.webp"


def write_atlas(pack, image, frames, overwrite=False):
    """写图集并返回清单里的公共字段（不含 frameMap）。过渡图集同名时直接复用；动作图集用 overwrite 覆盖。"""
    w, h = idle.TILE
    cols = min(idle.COLUMNS, len(frames))
    rows = math.ceil(len(frames) / cols)
    if overwrite or not (pack / image).is_file():
        atlas = Image.new("RGBA", (w * cols, h * rows))
        for i, frame in enumerate(frames):
            atlas.paste(frame, (i % cols * w, i // cols * h))
        atlas.save(pack / image, lossless=True, method=4, exact=True)
    return dict(image=image, frameWidth=w, frameHeight=h, columns=cols, rows=rows,
                frameCount=len(frames), fps=idle.FPS, facing="right")


def load_manifest(pack):
    path = pack / "animations.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path, manifest):
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build(pack, source, start, end, count, first, second, name):
    """同一段画面写成一对过渡：正放 first→second，倒放 second→first。"""
    if count / idle.FPS > MAX_SECONDS:
        raise ValueError(f"{count} 帧超过 {MAX_SECONDS} 秒上限")
    frames = read_frames(source, pick(start, end, count))
    base = write_atlas(pack, segment_name(source, start, end, count), frames)
    forward = dict(base, name=f"{name}（正放）", **{"from": first, "to": second})
    backward = dict(base, name=f"{name}（倒放）", frameMap=list(range(count - 1, -1, -1)),
                    **{"from": second, "to": first})
    path, manifest = load_manifest(pack)
    for key in (first, second):
        if key not in manifest["actions"]:
            raise ValueError(f"动作 {key} 不在 {path} 中")
    kept = [t for t in manifest.get("transitions", [])
            if {t["from"], t["to"]} != {first, second}]
    manifest["transitions"] = kept + [forward, backward]
    save_manifest(path, manifest)
    return forward, backward


def build_one_way(pack, source, start, end, count, first, second, name):
    """单向过渡：起步段用于 first→second，收尾段另行写 second→first（参考 Aboutme 行走片的起步与收步）。"""
    if count / idle.FPS > MAX_SECONDS:
        raise ValueError(f"{count} 帧超过 {MAX_SECONDS} 秒上限")
    frames = read_frames(source, pick(start, end, count))
    entry = dict(write_atlas(pack, segment_name(source, start, end, count), frames), name=name,
                 **{"from": first, "to": second})
    path, manifest = load_manifest(pack)
    for key in (first, second):
        if key not in manifest["actions"]:
            raise ValueError(f"动作 {key} 不在 {path} 中")
    manifest["transitions"] = [t for t in manifest.get("transitions", [])
                               if (t["from"], t["to"]) != (first, second)] + [entry]
    save_manifest(path, manifest)
    return entry


def build_action(pack, source, start, end, key, name, pose=None, pingpong=True):
    """把源片 [start, end] 写成动作。

    pingpong=True 时正放接倒放（frameMap 复用同一图集），用于循环；False 时按顺序播放一次。
    首尾帧模式的源片第 0 格和最后一格几乎就是锚点原图，亮度和饱和度比相邻帧高 3～7，
    放进循环会在每次掉头时闪一下，所以区间应避开这两格。
    """
    if end - start < 2:
        raise ValueError("动作至少 3 帧")
    frames = read_frames(source, list(range(start, end + 1)))
    count = len(frames)
    entry = dict(write_atlas(pack, f"{key}.webp", frames, overwrite=True), name=name)
    if pingpong:
        order = list(range(count)) + list(range(count - 2, 0, -1))
        entry.update(frameCount=len(order), frameMap=order)
    path, manifest = load_manifest(pack)
    manifest["actions"][key] = entry
    if pose:
        manifest.setdefault("poses", {})[pose] = key
    save_manifest(path, manifest)
    return entry


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pack", type=Path, required=True, help="含 animations.json 的素材包目录")
    parser.add_argument("--source", type=Path, required=True, help="已抠图的单次播放图集（WebP）")
    parser.add_argument("--range", required=True, help="取帧区间 START:END（图集格序号，含两端）")
    parser.add_argument("--frames", type=int, default=9, help="输出帧数，默认 9（0.75 秒）")
    parser.add_argument("--from", dest="first")
    parser.add_argument("--to", dest="second")
    parser.add_argument("--name", default="转身")
    parser.add_argument("--one-way", action="store_true", help="只写 from→to 一条，不自动生成倒放")
    parser.add_argument("--action", help="改为把区间写成循环动作，值为动作 ID（此时不需要 --from/--to）")
    parser.add_argument("--pose", help="与 --action 同用：把该姿势指向新动作，例如 working")
    parser.add_argument("--once", action="store_true", help="与 --action 同用：按顺序播放一次，不做正放接倒放")
    args = parser.parse_args()
    start, end = (int(v) for v in args.range.split(":"))
    if args.action:
        result = build_action(args.pack, args.source, start, end, args.action, args.name, args.pose, not args.once)
    elif not (args.first and args.second):
        parser.error("写过渡时需要 --from 和 --to")
    elif args.one_way:
        result = build_one_way(args.pack, args.source, start, end, args.frames, args.first, args.second, args.name)
    else:
        result = build(args.pack, args.source, start, end, args.frames, args.first, args.second, args.name)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
