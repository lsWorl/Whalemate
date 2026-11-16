"""把源片里从画面上缘伸进来的手和手臂涂成背景绿，再交给 idle.py 抠图。

即梦在“被拎起”这类动作里常常画出拎人的手。手总是从画面上缘伸进来，
而角色自己的脸和手不碰上缘，所以只取与上缘连通的肤色区域：
先按颜色找肤色像素，再从最上面几行出发在肤色掩码内反复膨胀（测地重建），
最后把该区域外扩几个像素，涂成该帧四边的背景中位色（即梦的“纯绿”并不是 #00FF00）。

用法：
    python -m tools.video_pet.dehand --input clips/grabbed-v3.mp4 --output out.mp4 --ffmpeg ffmpeg
"""
from __future__ import annotations

import argparse
import subprocess

import numpy as np

def skin_mask(rgb: np.ndarray) -> np.ndarray:
    r, g, b = (rgb[..., i].astype(np.int16) for i in range(3))
    return (r > 170) & (g > 120) & (b > 90) & (r > g) & (g > b) & (r - b > 25) & (r - b < 120)


def dilate(mask: np.ndarray, steps: int = 1) -> np.ndarray:
    out = mask.copy()
    for _ in range(steps):
        grown = out.copy()
        grown[1:] |= out[:-1]
        grown[:-1] |= out[1:]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        out = grown
    return out


def top_connected(mask: np.ndarray, seed_rows: int = 4) -> np.ndarray:
    region = np.zeros_like(mask)
    region[:seed_rows] = mask[:seed_rows]
    if not region.any():
        return region
    while True:
        grown = dilate(region, 8) & mask
        if (grown == region).all():
            return region
        region = grown


def hand_region(rgb: np.ndarray, pad: int = 6) -> np.ndarray:
    # 肤色掩码先闭合一下，指缝和阴影不至于把手切成几块。
    skin = dilate(skin_mask(rgb), 2)
    hand = top_connected(skin)
    if not hand.any():
        return hand
    # 只外扩几个像素盖住肤色边缘的描线，不按外接框整块清除，免得削掉头饰和呆毛。
    return dilate(hand, pad)


def run(source: str, output: str, ffmpeg: str = "ffmpeg") -> list[int]:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate", "-of", "csv=p=0", source],
        check=True, capture_output=True, text=True,
    ).stdout.strip().split(",")
    width, height, rate = int(probe[0]), int(probe[1]), probe[2]
    reader = subprocess.Popen([ffmpeg, "-v", "error", "-i", source, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    writer = subprocess.Popen(
        [ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", rate, "-i", "-",
         "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p", output],
        stdin=subprocess.PIPE,
    )
    size = width * height * 3
    touched = []
    index = 0
    while True:
        buffer = reader.stdout.read(size)
        if len(buffer) < size:
            break
        frame = np.frombuffer(buffer, dtype=np.uint8).reshape(height, width, 3).copy()
        region = hand_region(frame)
        if region.any():
            border = np.concatenate([frame[:4].reshape(-1, 3), frame[-4:].reshape(-1, 3), frame[:, :4].reshape(-1, 3), frame[:, -4:].reshape(-1, 3)])
            frame[region] = np.median(border, axis=0).astype(np.uint8)
            touched.append(index)
        writer.stdin.write(frame.tobytes())
        index += 1
    writer.stdin.close()
    writer.wait()
    reader.wait()
    return touched


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()
    touched = run(args.input, args.output, args.ffmpeg)
    print(f"涂掉手的帧：{len(touched)} 张，序号 {touched[:3]}…{touched[-3:]}" if touched else "未检出手。")


if __name__ == "__main__":
    main()
