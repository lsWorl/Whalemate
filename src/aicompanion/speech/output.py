from __future__ import annotations

from collections import deque
from array import array
import io
import math
import re
import wave
from PySide6.QtCore import QObject,Signal,QBuffer,QIODevice,QUrl,QLocale
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
from PySide6.QtTextToSpeech import QTextToSpeech
from aicompanion.ui.chat_worker import TaskWorker


class SentenceBuffer:
    def __init__(self): self.text=''

    def feed(self,text,final=False):
        self.text += text
        result=[]
        while self.text:
            match=re.search(r'[。！？!?\n]',self.text)
            length=match.end() if match else 0
            if not length and len(self.text)>=160: length=160
            if not length and final: length=len(self.text)
            if not length: break
            sentence,self.text=self.text[:length].strip(),self.text[length:]
            if sentence: result.append(sentence)
        return result


class SpeechOutput(QObject):
    subtitle = Signal(str)
    mouth = Signal(float)
    speaking = Signal(bool)
    error = Signal(str)
    usage = Signal(int)

    def __init__(self,client,parent=None):
        super().__init__(parent)
        self.client=client; self.queue=deque(); self.workers=set(); self.epoch=0
        self.current=''; self.busy=False; self.enabled=False; self.provider='system'; self.voice='tongtong'
        self.tts=QTextToSpeech(self)
        self.tts.setLocale(QLocale('zh_CN'))
        self.tts.stateChanged.connect(self._system_state)
        self.tts.sayingWord.connect(self._word)
        self.tts.errorOccurred.connect(lambda *args:self._error(self.tts.errorString()))
        self.player=QMediaPlayer(self); self.audio=QAudioOutput(self); self.player.setAudioOutput(self.audio)
        self.player.positionChanged.connect(self._position)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.errorOccurred.connect(lambda *args:self._error(self.player.errorString()))
        self.wav_buffer=None; self.pcm=b''; self.rate=16000

    def enqueue(self,text):
        if not self.enabled: return
        if len(self.queue)>=12:
            self.error.emit('语音队列已满，后续内容仅显示文字。'); return
        self.queue.append(text)
        self._next()

    def _next(self):
        if self.busy or not self.queue: return
        self.busy=True; self.current=self.queue.popleft(); epoch=self.epoch
        if self.provider=='system':
            if self.tts.state()==QTextToSpeech.State.Error:
                self._error(self.tts.errorString()); return
            self.subtitle.emit(self.current); self.speaking.emit(True)
            self.tts.say(self.current)
        else:
            text=self.current
            worker=TaskWorker(lambda token:self.client.synthesize(text,self.voice,token),self)
            self.workers.add(worker)
            worker.succeeded.connect(lambda raw,e=epoch:self._play(raw,e))
            worker.failed.connect(lambda error,e=epoch:self._error(error) if e==self.epoch else None)
            worker.finished.connect(lambda w=worker:self._dispose(w))
            worker.start()

    def _dispose(self,worker):
        self.workers.discard(worker); worker.deleteLater()

    def _play(self,raw,epoch):
        if epoch!=self.epoch: return
        with wave.open(io.BytesIO(raw),'rb') as wav:
            self.pcm=wav.readframes(wav.getnframes()); self.rate=wav.getframerate()
        self.wav_buffer=QBuffer(self); self.wav_buffer.setData(raw); self.wav_buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self.player.setSourceDevice(self.wav_buffer,QUrl('memory.wav'))
        self.subtitle.emit(self.current); self.speaking.emit(True); self.usage.emit(len(self.current))
        self.player.play()

    def _position(self,milliseconds):
        start=int(milliseconds*self.rate/1000)*2
        frame=self.pcm[start:start+self.rate//50*2]
        values=array('h'); values.frombytes(frame[:len(frame)//2*2])
        energy=math.sqrt(sum(x*x for x in values)/len(values))/32768 if values else 0
        self.mouth.emit(min(1,energy*6))

    def _word(self,word,index,start,length):
        # 系统朗读引擎不暴露播放 PCM，词边界口型仅为降级展示。
        self.mouth.emit(.35)

    def _system_state(self,state):
        if self.provider=='system' and state==QTextToSpeech.State.Ready and self.busy:
            self._done()

    def _media_status(self,status):
        if status==QMediaPlayer.MediaStatus.EndOfMedia: self._done()

    def _done(self):
        self.busy=False; self.mouth.emit(0); self.speaking.emit(False)
        self.player.setSource(QUrl())
        if self.wav_buffer:
            self.wav_buffer.close(); self.wav_buffer.deleteLater(); self.wav_buffer=None
        self._next()

    def _error(self,text):
        self.error.emit('语音播放失败：'+text); self.queue.clear(); self.busy=False
        self.speaking.emit(False); self.mouth.emit(0)

    def stop(self):
        self.epoch+=1; self.queue.clear(); self.busy=False
        for worker in self.workers: worker.cancellation.cancel()
        self.tts.stop(QTextToSpeech.BoundaryHint.Immediate)
        self.player.stop(); self.player.setSource(QUrl())
        if self.wav_buffer:
            self.wav_buffer.close(); self.wav_buffer.deleteLater(); self.wav_buffer=None
        self.pcm=b''; self.mouth.emit(0); self.speaking.emit(False)
