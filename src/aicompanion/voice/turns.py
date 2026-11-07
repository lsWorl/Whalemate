"""收音轮次：把麦克风帧切成"一句话"。不依赖 Qt，便于单元测试。

三种收音方式：
- continuous 持续通话：VAD 判断开口和说完；播放期间经回声门控，连续 200 ms 确认是你在说才打断她；
- ptt 按住说话：按下即打断并开始录，松开送去识别；
- toggle 切换式：按一下开始录，再按一下（或停顿 1.5 秒）结束。

feed() 返回事件列表：('start', info) 开始听（界面据此打断播放）、('end', pcm) 一句话、('cancel', reason) 作废。
"""
from __future__ import annotations

from array import array
from collections import deque
import math

from aicompanion.voice.echo import EchoGate

MODES = ('continuous', 'ptt', 'toggle')
START_FRAMES = 8          # 安静时：连续 160 ms 人声算开口
BARGE_FRAMES = 10         # 她在说话时：连续 200 ms 超出回声才算插话
END_SILENCE = 40          # 持续通话：静音 800 ms 算说完
TOGGLE_SILENCE = 75       # 切换式：说过话后静音 1.5 s 自动结束
TOGGLE_NO_SPEECH = 400    # 切换式：8 秒都没说话则作废
MAX_FRAMES = 1400         # 28 秒上限，给接口 30 秒限制留余量
MIN_KEY_FRAMES = 15       # 按键收音至少 300 ms
MIN_KEY_VOICED = 5        # 且至少 100 ms 人声


def frame_rms(frame: bytes) -> float:
    samples = array('h'); samples.frombytes(frame[:len(frame)//2*2])
    if not samples: return 0.0
    return math.sqrt(sum(x*x for x in samples)/len(samples))/32768


def _webrtc_vad(level=2):
    import webrtcvad
    vad = webrtcvad.Vad(level)
    return lambda frame, rate: vad.is_speech(frame, rate)


class TurnDetector:
    def __init__(self, sample_rate: int = 16000, mode: str = 'continuous', vad=None, gate: EchoGate | None = None):
        self.rate = sample_rate
        self.mode = mode if mode in MODES else 'continuous'
        self.vad = vad or _webrtc_vad()
        self.gate = gate or EchoGate()
        self.frame_bytes = sample_rate//50*2
        self.buffer = bytearray()
        self.pre: deque = deque(maxlen=10)
        self.frames: list[bytes] = []
        self.voiced = self.silence = self.voiced_total = 0
        self.active = False; self.key_was_down = False; self.barge_in = False
        self.last_rms = 0.0; self.echo_blocked = 0

    def reset(self):
        self.active = False; self.frames = []; self.voiced = self.silence = self.voiced_total = 0
        self.pre.clear(); self.barge_in = False

    def set_mode(self, mode: str):
        if mode in MODES and mode != self.mode:
            self.mode = mode; self.reset(); self.key_was_down = False

    # ---- 主入口 -----------------------------------------------------------------
    def feed(self, data: bytes, now: float = 0.0, ref_at=None, key_down: bool = False) -> list:
        """data 为 16-bit 单声道 PCM；ref_at(t) 返回 t 时刻正在播放的音量；key_down 为收音键当前是否按着。"""
        self.buffer.extend(data)
        count = len(self.buffer)//self.frame_bytes
        events = []
        if self.mode != 'continuous':
            events += self._key_edge(key_down)
        for i in range(count):
            frame = bytes(self.buffer[:self.frame_bytes]); del self.buffer[:self.frame_bytes]
            t = now-(count-1-i)*0.02
            rms = frame_rms(frame); self.last_rms = rms
            ref = float(ref_at(t)) if ref_at else 0.0
            beyond_echo = self.gate.feed(rms, ref)
            speech = bool(self.vad(frame, self.rate))
            if self.mode == 'continuous': events += self._continuous(frame, speech, beyond_echo)
            else: events += self._keyed(frame, speech)
        return events

    def _continuous(self, frame, speech, beyond_echo):
        self.pre.append(frame)
        playing = self.gate.playing
        if not self.active:
            user = speech and beyond_echo
            if speech and not beyond_echo: self.echo_blocked += 1
            self.voiced = self.voiced+1 if user else 0
            need = BARGE_FRAMES if playing else START_FRAMES
            if self.voiced >= need:
                self.active = True; self.barge_in = playing
                self.frames = list(self.pre); self.silence = 0; self.voiced_total = self.voiced
                return [('start', {'barge_in': playing, 'mode': 'continuous'})]
            return []
        self.frames.append(frame)
        self.silence = 0 if speech else self.silence+1
        if speech: self.voiced_total += 1
        if self.silence >= END_SILENCE or len(self.frames) >= MAX_FRAMES:
            pcm = b''.join(self.frames); self.reset()
            return [('end', pcm)]
        return []

    def _key_edge(self, key_down):
        events = []
        pressed = key_down and not self.key_was_down
        released = not key_down and self.key_was_down
        self.key_was_down = key_down
        if self.mode == 'ptt':
            if pressed and not self.active: events.append(self._begin())
            elif released and self.active: events.append(self._finish())
        elif self.mode == 'toggle' and pressed:
            events.append(self._finish() if self.active else self._begin())
        return events

    def _begin(self):
        self.active = True; self.frames = list(self.pre); self.voiced_total = self.silence = 0
        return ('start', {'barge_in': self.gate.playing, 'mode': self.mode})

    def _finish(self):
        frames, voiced = self.frames, self.voiced_total
        self.reset()
        if len(frames) >= MIN_KEY_FRAMES and voiced >= MIN_KEY_VOICED:
            return ('end', b''.join(frames))
        return ('cancel', 'too_short')

    def _keyed(self, frame, speech):
        self.pre.append(frame)
        if not self.active: return []
        self.frames.append(frame)
        if speech: self.voiced_total += 1; self.silence = 0
        else: self.silence += 1
        if len(self.frames) >= MAX_FRAMES: return [self._finish()]
        if self.mode == 'toggle':
            if self.voiced_total >= MIN_KEY_VOICED and self.silence >= TOGGLE_SILENCE: return [self._finish()]
            if self.voiced_total == 0 and len(self.frames) >= TOGGLE_NO_SPEECH:
                self.reset(); return [('cancel', 'no_speech')]
        return []
