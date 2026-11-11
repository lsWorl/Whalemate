from __future__ import annotations

from array import array
from collections import deque
from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path
import math
from time import monotonic
import re
import wave
from PySide6.QtCore import QObject, Signal, QBuffer, QIODevice, QUrl, QLocale, QTimer
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtTextToSpeech import QTextToSpeech
from aicompanion.speech.qwen_client import validate_wav
from aicompanion.ui.chat_worker import TaskWorker


def spoken_text(text):
    """保留台词，去掉常见 Markdown 标记和链接地址，避免念出格式符号。"""
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    return re.sub(r'[*`#_]', '', text).strip()


class SentenceBuffer:
    def __init__(self):
        self.text = ''

    def feed(self, text, final=False):
        self.text += text
        result = []
        while self.text:
            match = re.search(r'[。！？!?；;\n]|…+|\.{3,}', self.text)
            length = match.end() if match else 0
            if not length and len(self.text) >= 160:
                length = 160
            if not length and final:
                length = len(self.text)
            if not length:
                break
            sentence, self.text = self.text[:length].strip(), self.text[length:]
            if sentence:
                result.append(sentence)
        return result


@dataclass(eq=False)
class SpeechSegment:
    # 每句捕获配置，预取结果不会因切换角色或语气而串到另一条台词。
    text: str
    generation_id: str
    provider: str
    voice: str
    mood: str
    style: str
    language: str = 'zh'
    role_style: str = ''
    target: object = None      # Breeze 等模型页配置的语音：入队时取好的完整 TtsTarget，合成和缓存都用它（Codex 审计 R4）
    requested: bool = False
    audio: bytes | None = None


CACHE_CHARS = 16   # 语气词这类短句合成一次存盘，之后不再请求接口


def cache_dir() -> Path:
    return Path(os.getenv('LOCALAPPDATA') or Path.home()/'.cache')/'AICompanion'/'voice-cache'


def cache_key(job) -> str:
    raw = '|'.join((job.provider, job.voice, job.mood, job.style, job.language, job.role_style, job.text))
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()


class SpeechOutput(QObject):
    subtitle = Signal(str)
    mouth = Signal(float)
    speaking = Signal(bool)
    error = Signal(str)
    usage = Signal(int)
    delivered = Signal(str, str)

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.queue = deque()
        self.workers = set()
        self.epoch = 0
        self.current_job = None
        self.current = self.current_id = ''
        self.busy = self.enabled = False
        self.provider, self.voice = 'system', 'tongtong'
        self.mood, self.style = 'calm', ''
        self.language = 'zh'
        self.role_style = ''
        self.failed_generation = None
        self.delivered_count = 0
        self.tts = QTextToSpeech(self)
        self.tts.setLocale(QLocale('zh_CN'))
        self.tts.stateChanged.connect(self._system_state)
        self.tts.sayingWord.connect(self._word)
        self.tts.errorOccurred.connect(lambda *args: self._error(self.tts.errorString())
            if self.current_job and self.current_job.provider == 'system' else None)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.positionChanged.connect(self._position)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.errorOccurred.connect(lambda *args: self._error(self.player.errorString())
            if self.busy and self.wav_buffer else None)
        self.wav_buffer = None
        self.pcm = b''
        self.rate, self.duration_ms = 24000, 0
        # P4：外放打断需要知道"此刻正在播多大声"。play_started 为开始播放的 monotonic 时间。
        self.play_started = None
        # 可选的外部播放端（Chromium 回声消除时，播放必须和收音在同一个网页里）
        self.sink = None
        self.sink_timer = QTimer(self); self.sink_timer.setInterval(40); self.sink_timer.timeout.connect(self._sink_tick)

    def set_sink(self, sink):
        """sink 需提供 play(raw_wav) / stop()，以及 started、ended、failed 信号。传 None 回到本机播放器。"""
        if self.sink is sink: return
        self.stop()
        if self.sink:
            for signal, slot in ((self.sink.ended, self._sink_ended), (self.sink.failed, self._sink_failed), (self.sink.started, self._sink_started)):
                try: signal.disconnect(slot)
                except (RuntimeError, TypeError): pass
        self.sink = sink
        if sink:
            sink.ended.connect(self._sink_ended); sink.failed.connect(self._sink_failed); sink.started.connect(self._sink_started)

    def playback_rms(self, at=None):
        """at 时刻（monotonic 秒）正在播放的音量 0–1；没在播放返回 0。系统朗读拿不到波形，按固定音量估计。"""
        if not self.busy: return 0.0
        if self.current_job and self.current_job.provider == 'system': return 0.08
        if self.play_started is None or not self.pcm: return 0.0
        at = monotonic() if at is None else at
        start = int((at-self.play_started)*self.rate)*2
        if start < 0 or start >= len(self.pcm): return 0.0
        values = array('h'); chunk = self.pcm[start:start+self.rate//50*2]
        values.frombytes(chunk[:len(chunk)//2*2])
        return math.sqrt(sum(x*x for x in values)/len(values))/32768 if values else 0.0

    def enqueue(self, text, generation_id=''):
        text = spoken_text(text)
        if not self.enabled or not text or generation_id == self.failed_generation:
            return
        if len(self.queue) >= 12:
            self._error('语音队列已满，本轮后续内容仅显示文字。')
            return
        voice, target = self.voice, None
        if self.provider == 'breeze':
            # 入队时就定下这句用哪个账户和音色；之后改了设置只影响新句子，排队中的句子不会换成新音色、也不会把新音色存进旧缓存
            source = getattr(self.client, 'tts_source', None)
            target = source() if source else None
            voice = target.cache_id if target else ''
        self.queue.append(SpeechSegment(text, generation_id, self.provider, voice, self.mood, self.style, self.language,
                                        self.role_style, target))
        self._next()
        self._prefetch()

    def _next(self):
        if self.busy or not self.queue:
            return
        self.current_job = self.queue.popleft()
        self.current, self.current_id = self.current_job.text, self.current_job.generation_id
        self.delivered_count = 0
        self.busy = True
        if self.current_job.provider == 'system':
            if self.current_job.language == 'ja':
                self._error('日语配音请在设置中选择千问语音。')
                return
            if self.tts.state() == QTextToSpeech.State.Error:
                self._error(self.tts.errorString())
                return
            self.subtitle.emit(self.current)
            self.speaking.emit(True)
            self.tts.say(self.current)
        elif self.current_job.audio is not None:
            self._play(self.current_job.audio)
        self._prefetch()

    def _prefetch(self):
        # 最多准备当前句与下一句，控制并发、内存和被打断时的额外用量。
        jobs = ([self.current_job] if self.current_job else []) + list(self.queue)[:1]
        for job in jobs:
            if len(self.workers) >= 2:
                break
            if job.provider == 'system' or job.requested:
                continue
            job.requested = True
            if len(job.text) <= CACHE_CHARS:
                cached = self._cached(job)
                if cached:
                    job.audio = cached
                    if self.busy and job is self.current_job: self._play(cached)
                    continue
            epoch = self.epoch
            worker = TaskWorker(lambda token, j=job: self.client.synthesize(j.text, j.voice, token,
                provider=j.provider, mood=j.mood, style=j.style, language=j.language, role_style=j.role_style,
                **({'target': j.target} if j.target is not None else {})), self)
            self.workers.add(worker)
            worker.succeeded.connect(lambda raw, j=job, e=epoch: self._ready(j, raw, e))
            worker.failed.connect(lambda error, e=epoch: self._error(error) if e == self.epoch else None)
            worker.finished.connect(lambda w=worker: self._dispose(w))
            worker.start()

    def _ready(self, job, raw, epoch):
        if epoch != self.epoch:
            return
        try:
            job.audio = validate_wav(raw)
        except RuntimeError as error:
            self._error(str(error))
            return
        if len(job.text) <= CACHE_CHARS:
            try:
                folder = cache_dir(); folder.mkdir(parents=True, exist_ok=True)
                (folder/f'{cache_key(job)}.wav').write_bytes(job.audio)
            except OSError:
                pass
        if self.busy and job is self.current_job:
            self._play(job.audio)

    def _cached(self, job):
        try:
            path = cache_dir()/f'{cache_key(job)}.wav'
            return validate_wav(path.read_bytes()) if path.exists() else None
        except (OSError, RuntimeError):
            return None

    def _dispose(self, worker):
        self.workers.discard(worker)
        worker.deleteLater()
        self._prefetch()

    def _play(self, raw):
        with wave.open(io.BytesIO(raw), 'rb') as wav:
            self.pcm = wav.readframes(wav.getnframes())
            self.rate = wav.getframerate()
            self.duration_ms = wav.getnframes() / self.rate * 1000
        self.wav_buffer = QBuffer(self)
        self.wav_buffer.setData(raw)
        self.wav_buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        if not self.sink: self.player.setSourceDevice(self.wav_buffer, QUrl('memory.wav'))
        self.subtitle.emit(self.current)
        self.speaking.emit(True)
        self.usage.emit(len(self.current))
        self.play_started = monotonic()
        if self.sink:
            self.sink.play(raw); self.sink_timer.start()
        else:
            self.player.play()

    def _sink_started(self):
        # 网页真正开始出声的时刻，比发出播放命令晚几十毫秒
        if self.busy: self.play_started = monotonic()

    def _sink_tick(self):
        if not self.busy or self.play_started is None:
            self.sink_timer.stop(); return
        elapsed = (monotonic()-self.play_started)*1000
        self._position(int(elapsed))
        if elapsed > self.duration_ms+1500: self._done()   # 网页没报结束时兜底

    def _sink_ended(self):
        if self.busy and self.sink: self._done()

    def _sink_failed(self, text):
        if self.busy and self.sink: self._error(text)

    def _position(self, milliseconds):
        if not self.busy or not self.wav_buffer:
            return
        start = int(milliseconds * self.rate / 1000) * 2
        frame = self.pcm[start:start + self.rate // 50 * 2]
        values = array('h')
        values.frombytes(frame[:len(frame) // 2 * 2])
        energy = math.sqrt(sum(x * x for x in values) / len(values)) / 32768 if values else 0
        self.mouth.emit(min(1, energy * 8))
        if self.duration_ms > 0:
            # 目前为句级时间比例估计；已听内容不包含尚未播放的预取句子。
            self._mark_delivered(int(len(self.current) * milliseconds / self.duration_ms))

    def _word(self, word, index, start, length):
        if self.busy and self.current_job and self.current_job.provider == 'system':
            self.mouth.emit(.35)
            self._mark_delivered(start + length)

    def _mark_delivered(self, count):
        count = max(self.delivered_count, min(count, len(self.current)))
        if count > self.delivered_count:
            self.delivered.emit(self.current_id, self.current[self.delivered_count:count])
            self.delivered_count = count

    def _system_state(self, state):
        if self.current_job and self.current_job.provider == 'system' and self.busy and state == QTextToSpeech.State.Ready:
            self._done()

    def _media_status(self, status):
        if self.busy and self.wav_buffer and status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._done()

    def _release_audio(self):
        self.sink_timer.stop(); self.play_started = None
        if self.sink: self.sink.stop()
        self.player.stop()
        self.player.setSource(QUrl())
        if self.wav_buffer:
            self.wav_buffer.close()
            self.wav_buffer.deleteLater()
            self.wav_buffer = None
        self.pcm = b''

    def _done(self):
        self._mark_delivered(len(self.current))
        self.busy = False
        self.current_job = None
        self._release_audio()
        self.mouth.emit(0)
        self.speaking.emit(False)
        self._next()

    def _error(self, text):
        self.failed_generation = self.current_id
        self.stop()
        self.error.emit('语音合成或播放失败：' + text)

    def stop(self):
        # 提升轮次使晚到的合成结果失效；网络任务自己负责释放线程和连接。
        self.epoch += 1
        self.queue.clear()
        self.busy = False
        self.current_job = None
        for worker in self.workers:
            worker.cancellation.cancel()
        self.tts.stop(QTextToSpeech.BoundaryHint.Immediate)
        self._release_audio()
        self.mouth.emit(0)
        self.speaking.emit(False)
