"""麦克风收音。两个后端共用同一套轮次判断（voice/turns.py）：

- Microphone：Qt 直接收音，外放靠回声门控（播放期间提高门槛）；
- WebMicrophone（voice/web_voice.py）：在 Chromium 里收音和播放，用浏览器自带的回声消除。
"""
from time import monotonic

from PySide6.QtCore import QObject, Signal
from PySide6.QtMultimedia import QAudioSource, QAudioFormat, QMediaDevices, QAudio

from aicompanion.voice.echo import EchoGate
from aicompanion.voice.turns import TurnDetector

# 旧名字保留给旧测试和脚本
UtteranceDetector = TurnDetector


class TurnSource(QObject):
    """把 PCM 喂给轮次判断，发出统一信号。子类负责真正采集。"""
    speech_started = Signal()
    utterance = Signal(bytes, int)
    cancelled = Signal(str)
    error = Signal(str)
    level = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.detector = None
        self.mode = 'continuous'; self.reference = None; self.key = None
        self.gate = EchoGate(); self.last_start = None

    def configure(self, mode='continuous', reference=None, key=None, gate=None):
        """mode 收音方式；reference(t) 给出 t 时刻的播放音量；key() 返回收音键是否按着。"""
        self.mode = mode; self.reference = reference; self.key = key
        if gate is not None: self.gate = gate
        if self.detector:
            self.detector.gate = self.gate; self.detector.set_mode(mode)

    def _process(self, data, rate):
        if self.detector is None or self.detector.rate != rate:
            self.detector = TurnDetector(rate, self.mode, gate=self.gate)
        key_down = False
        if self.key:
            try: key_down = bool(self.key())
            except Exception: key_down = False
        events = self.detector.feed(data, monotonic(), self.reference, key_down)
        self.level.emit(min(1.0, self.detector.last_rms*5))
        for kind, payload in events:
            if kind == 'start':
                self.last_start = (monotonic(), payload); self.speech_started.emit()
            elif kind == 'end': self.utterance.emit(payload, rate)
            else: self.cancelled.emit(str(payload))

    def reset_turn(self):
        if self.detector: self.detector.reset()


class Microphone(TurnSource):
    backend = 'qt'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source = None
        self.device = None
        self.rate = 16000

    def start(self, device_id=''):
        self.stop()
        devices = QMediaDevices.audioInputs()
        device = next((d for d in devices if bytes(d.id()).hex() == device_id), QMediaDevices.defaultAudioInput())
        if device.isNull():
            raise RuntimeError('没有可用的麦克风，请连接设备。')
        fmt = QAudioFormat(); fmt.setChannelCount(1); fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        for rate in (16000, 32000, 48000):
            fmt.setSampleRate(rate)
            if device.isFormatSupported(fmt):
                break
        else:
            raise RuntimeError('此麦克风不支持 VAD 所需 PCM 格式，请更换设备。')
        self.rate = fmt.sampleRate()
        self.detector = TurnDetector(self.rate, self.mode, gate=self.gate)
        self.source = QAudioSource(device, fmt, self)
        self.source.stateChanged.connect(self._state)
        self.device = self.source.start()
        if self.device is None:
            self.stop(); raise RuntimeError('无法开启麦克风，请检查系统麦克风权限。')
        self.device.readyRead.connect(self._read)

    def _read(self):
        if self.device is None:
            return
        data = bytes(self.device.readAll())
        if data: self._process(data, self.rate)

    def _state(self, state):
        # 只在采集真正停止时报错；欠载 / 过载这类瞬时错误不打断通话
        if self.source and state == QAudio.State.StoppedState and self.source.error() not in (QAudio.Error.NoError, QAudio.Error.UnderrunError):
            self.error.emit('麦克风已断开或采集失败，请重新开启通话。')

    def stop(self):
        self.device = None
        if self.source:
            source, self.source = self.source, None
            source.stop(); source.deleteLater()
        self.reset_turn()
