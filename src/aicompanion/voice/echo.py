"""外放回声门控：判断麦克风里的声音是"她自己刚播出去的"还是"你在说话"。

不依赖 numpy，每 20 ms 一帧：
- 记录麦克风能量 m（扣掉环境底噪）和同一时刻正在播放的参考能量 r；
- 播放期间每隔几帧，在最近 1 秒里搜索 0–240 ms 的延迟，用最小二乘拟合 m ≈ c·r(t-lag)。
  拟合得好（说明这段几乎全是回声）就更新耦合系数 c 和延迟；你插话时拟合变差，不会被学进去；
- 播放期间，只有 m 明显超过回声预测（margin 倍）的帧才算"你在说"。没在播放时不拦截。

这是"播放期间提高门槛"的自适应版本，配合 Chromium 回声消除时 c 会很小，几乎不拦截。
"""
from __future__ import annotations

from collections import deque

FRAME_MS = 20
MAX_LAG = 20            # 最多 400 ms 的播放→收音延迟（蓝牙耳机、音箱的缓冲可能有 200–300 ms）
WINDOW = 50             # 用最近 1 秒拟合
TAIL_FRAMES = 20        # 播放停止后 400 ms 内仍按回声尾巴处理
REF_ACTIVE = 0.003      # 参考能量高于此值视为正在出声
GOOD_FIT = 0.25         # 未解释能量占比低于此值，认为这段只有回声


class EchoGate:
    def __init__(self, coupling: float = 0.6, margin: float = 2.2, floor: float = 0.006, calibrated: bool = False):
        self.c = float(coupling); self.lag = 4; self.margin = margin; self.floor = floor
        self.calibrated = calibrated
        self.mic: deque = deque(maxlen=WINDOW+MAX_LAG+2)
        self.ref: deque = deque(maxlen=WINDOW+MAX_LAG+2)
        self.noise = 0.0; self.since_ref = 10**6; self.frames = 0
        self.fit_error = 1.0; self.fits = 0

    # ---- 状态 -----------------------------------------------------------------
    @property
    def playing(self) -> bool:
        return self.since_ref <= TAIL_FRAMES+MAX_LAG

    def predicted(self) -> float:
        """当前帧按延迟和耦合系数预测的回声能量（取延迟附近 3 帧的最大值，留余量）。"""
        if not self.ref: return 0.0
        refs = list(self.ref)
        if not self.calibrated:
            # 还不知道延迟：整个可能的延迟范围都当作回声来源
            return self.c*max(refs[-1-MAX_LAG:] or [0.0])
        i = len(refs)-1-self.lag
        near = [refs[j] for j in (i-1, i, i+1) if 0 <= j < len(refs)]
        return self.c*max(near or [0.0])

    # ---- 每帧 -----------------------------------------------------------------
    def feed(self, mic_rms: float, ref_rms: float) -> bool:
        """返回该帧的能量是否超出回声预测（True = 可能是你在说话）。"""
        self.frames += 1
        if ref_rms > REF_ACTIVE: self.since_ref = 0
        else: self.since_ref += 1
        if not self.playing:
            # 安静时跟踪环境底噪：下降快、上升慢
            self.noise = mic_rms if mic_rms < self.noise else self.noise+0.002*(mic_rms-self.noise)
        m = max(0.0, mic_rms-self.noise)
        self.mic.append(m); self.ref.append(max(0.0, ref_rms))
        if not self.playing:
            return True
        if self.frames % 5 == 0: self._fit()
        return m > max(self.floor, self.margin*self.predicted())

    def _fit(self):
        mic, ref = list(self.mic), list(self.ref)
        n = min(WINDOW, len(mic)-MAX_LAG)
        if n < 15: return
        best = None
        for lag in range(MAX_LAG+1):
            srr = smr = smm = 0.0
            for k in range(len(mic)-n, len(mic)):
                r = ref[k-lag]; m = mic[k]
                srr += r*r; smr += m*r; smm += m*m
            if srr < (REF_ACTIVE**2)*n*0.3 or smm <= 1e-12: continue
            err = 1.0-smr*smr/(srr*smm)
            if best is None or err < best[0]: best = (err, lag, smr/srr)
        # 上界估计：麦克风能量 / 播放能量 的 80 分位。回声很弱（耳机、音箱离得远）时，
        # 最小二乘拟合不出来，靠它把门槛降下来；只往下调，往上只信任拟合，避免把你的声音学成回声。
        ratios = sorted(mic[k]/max(ref[k-j] for j in range(MAX_LAG+1)) for k in range(len(mic)-n, len(mic))
                        if max(ref[k-j] for j in range(MAX_LAG+1)) > REF_ACTIVE*3)
        if len(ratios) >= 15:
            upper = max(0.01, ratios[int(len(ratios)*0.8)]*1.2)
            if upper < self.c: self.c = upper; self.lowered = getattr(self, 'lowered', 0)+1
        if best is None: return
        self.fit_error = best[0]
        if best[0] < GOOD_FIT and best[2] > 0:
            c = min(3.0, max(0.005, best[2]))
            self.c = c if not self.calibrated else 0.7*self.c+0.3*c
            self.lag = best[1]; self.calibrated = True; self.fits += 1

    def stats(self) -> dict:
        return {'coupling': round(self.c, 4), 'lag_ms': self.lag*FRAME_MS, 'calibrated': self.calibrated,
                'fit_error': round(self.fit_error, 3), 'noise': round(self.noise, 4), 'fits': self.fits,
                'lowered': getattr(self, 'lowered', 0)}
