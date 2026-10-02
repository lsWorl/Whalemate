from collections import deque
from array import array
import math
import webrtcvad
from PySide6.QtCore import QObject, Signal
from PySide6.QtMultimedia import QAudioSource,QAudioFormat,QMediaDevices,QAudio


class UtteranceDetector:
    """WebRTC VAD 加前置缓冲，连续讲话达到阈值后才触发打断。"""

    def __init__(self,sample_rate=16000):
        self.rate = sample_rate
        self.vad = webrtcvad.Vad(2)
        self.frame_bytes = sample_rate//50*2
        self.buffer = bytearray()
        self.pre = deque(maxlen=10)
        self.frames = []
        self.voiced = self.silence = 0
        self.active = False

    def feed(self,data):
        self.buffer.extend(data)
        events = []
        while len(self.buffer)>=self.frame_bytes:
            frame = bytes(self.buffer[:self.frame_bytes]); del self.buffer[:self.frame_bytes]
            speaking = self.vad.is_speech(frame,self.rate)
            self.pre.append(frame)
            if not self.active:
                self.voiced = self.voiced+1 if speaking else 0
                if self.voiced>=8:
                    self.active = True
                    self.frames = list(self.pre)
                    self.silence = 0
                    events.append(('start',None))
            else:
                self.frames.append(frame)
                self.silence = 0 if speaking else self.silence+1
                # 28 秒上限留出接口的 30 秒约束余量。
                if self.silence>=40 or len(self.frames)>=1400:
                    events.append(('end',b''.join(self.frames)))
                    self.active=False; self.frames=[]; self.voiced=0; self.silence=0
                    self.pre.clear()
        return events


class Microphone(QObject):
    speech_started = Signal()
    utterance = Signal(bytes,int)
    error = Signal(str)
    level = Signal(float)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.source = None
        self.device = None

    def start(self,device_id=''):
        self.stop()
        devices = QMediaDevices.audioInputs()
        device = next((d for d in devices if bytes(d.id()).hex()==device_id),QMediaDevices.defaultAudioInput())
        if device.isNull():
            raise RuntimeError('没有可用的麦克风，请连接设备。')
        fmt = QAudioFormat(); fmt.setChannelCount(1); fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        for rate in (16000,32000,48000):
            fmt.setSampleRate(rate)
            if device.isFormatSupported(fmt):
                break
        else:
            raise RuntimeError('此麦克风不支持 VAD 所需 PCM 格式，请更换设备。')
        self.detector = UtteranceDetector(fmt.sampleRate())
        self.source = QAudioSource(device,fmt,self)
        self.source.stateChanged.connect(self._state)
        self.device = self.source.start()
        if self.device is None:
            self.stop(); raise RuntimeError('无法开启麦克风，请检查系统麦克风权限。')
        self.device.readyRead.connect(self._read)

    def _read(self):
        if self.device is None:
            return
        data = bytes(self.device.readAll())
        samples = array('h'); samples.frombytes(data[:len(data)//2*2])
        if samples:
            rms = math.sqrt(sum(x*x for x in samples)/len(samples))/32768
            self.level.emit(min(1,rms*5))
        for kind,pcm in self.detector.feed(data):
            if kind=='start': self.speech_started.emit()
            else: self.utterance.emit(pcm,self.detector.rate)

    def _state(self,state):
        if self.source and self.source.error()!=QAudio.Error.NoError:
            self.error.emit('麦克风已断开或采集失败，请重新开启通话。')

    def stop(self):
        self.device = None
        if self.source:
            source,self.source = self.source,None
            source.stop(); source.deleteLater()
