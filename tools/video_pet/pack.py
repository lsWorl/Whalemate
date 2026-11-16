"""把已审片的图集组合成 T24 的桌面预览包。"""
import argparse
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
BATCH = {
    "idle": ("idle-v2", "待机"),
    "talking": ("talk-v2", "开心说话"),
    "wave": ("wave-v1", "挥手打招呼"),
    "walk": ("walk-v1", "原地踏步"),
}


def assemble(target, reviews, sleepy_pack):
    previous = json.loads((sleepy_pack / "animations.json").read_text(encoding="utf-8"))
    actions = {}
    for action, (source, name) in BATCH.items():
        entry = json.loads((reviews / source / "action.json").read_text(encoding="utf-8"))
        if entry["image"] != f"{action}.webp" or not (target / entry["image"]).is_file():
            raise ValueError(f"{source} 的图集缺失或与动作不符")
        actions[action] = dict(entry, name=name)

    sleepy = previous["actions"][previous["poses"]["idle"]]
    shutil.copyfile(sleepy_pack / sleepy["image"], target / "sleepy.webp")
    actions["sleepy"] = dict(sleepy, name="犯困", image="sleepy.webp")
    manifest = dict(previous, actions=actions, poses={
        "idle": "idle", "talking": "talking", "thinking": "idle",
        "walking": "walk", "wave": "wave", "sleepy": "sleepy",
    })
    path = target / "animations.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, default=ROOT / "output/t24-pack")
    parser.add_argument("--review", type=Path, default=ROOT / "output/t24-review")
    parser.add_argument("--sleepy-pack", type=Path, help="上批已修边的 idle-v1 图集目录")
    args = parser.parse_args()
    print(assemble(args.pack, args.review, args.sleepy_pack or args.pack / "idle"))
