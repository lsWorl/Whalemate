"""Chromium 回声消除后端：一个不显示的网页同时负责收音和播放。

浏览器的回声消除（AEC3）需要知道"正在播什么"，所以开启这个后端时，她的语音也改由这个网页播放
（SpeechOutput.set_sink），麦克风收到的 16 kHz PCM 再交回 Python，走同一套轮次判断和回声门控。
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage

from aicompanion.audio.capture import TurnSource

PAGE = Path(__file__).resolve().parents[1]/'assets'/'voice'/'voice.html'


class VoiceBridge(QObject):
    startCapture = Signal(str)
    stopCapture = Signal()
    playWav = Signal(str)
    stopPlayback = Signal()
    # Python 侧
    pcm_received = Signal(bytes, int)
    capture_state = Signal(dict)
    page_ready = Signal()
    play_started = Signal()
    play_ended = Signal()
    play_failed = Signal(str)

    @Slot(str, float)
    def pcm(self, b64, rate):
        try: self.pcm_received.emit(base64.b64decode(b64), int(rate))
        except (ValueError, TypeError): pass

    @Slot(str)
    def captureState(self, raw):
        try: self.capture_state.emit(json.loads(raw))
        except ValueError: self.capture_state.emit({'ok': False, 'error': raw})

    @Slot()
    def ready(self): self.page_ready.emit()

    @Slot()
    def playbackStarted(self): self.play_started.emit()

    @Slot()
    def playbackEnded(self): self.play_ended.emit()

    @Slot(str)
    def playbackFailed(self, text): self.play_failed.emit(text)


class WebSink(QObject):
    """给 SpeechOutput 用的播放端。"""
    started = Signal()
    ended = Signal()
    failed = Signal(str)

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        host.bridge.play_started.connect(self.started); host.bridge.play_ended.connect(self.ended)
        host.bridge.play_failed.connect(self.failed)

    def play(self, raw: bytes):
        self.host.call(lambda: self.host.bridge.playWav.emit(base64.b64encode(raw).decode('ascii')))

    def stop(self):
        if self.host.is_ready: self.host.bridge.stopPlayback.emit()


class WebMicrophone(TurnSource):
    backend = 'web'
    capture_info = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bridge = VoiceBridge(self)
        self.page = None; self.is_ready = False; self.pending = []; self.capturing = False
        self.info = {}
        self.sink = WebSink(self, self)
        self.bridge.page_ready.connect(self._ready)
        self.bridge.pcm_received.connect(self._pcm)
        self.bridge.capture_state.connect(self._state)

    # ---- 网页 -----------------------------------------------------------------
    def ensure_page(self):
        if self.page: return
        self.page = QWebEnginePage(self)
        self._grant_microphone(self.page)
        self.channel = QWebChannel(self.page); self.channel.registerObject('voice', self.bridge)
        self.page.setWebChannel(self.channel)
        self.page.load(QUrl.fromLocalFile(str(PAGE)))
        try: self.page.setLifecycleState(QWebEnginePage.LifecycleState.Active)
        except AttributeError: pass

    def _grant_microphone(self, page):
        local = QUrl.fromLocalFile(str(PAGE))
        def same_origin(url): return url.scheme() == 'file' or url.host() == local.host() and url.scheme() == local.scheme()
        if hasattr(page, 'permissionRequested'):   # Qt 6.8+
            from PySide6.QtWebEngineCore import QWebEnginePermission
            def requested(permission):
                kind = permission.permissionType()
                if kind == QWebEnginePermission.PermissionType.MediaAudioCapture and same_origin(permission.origin()): permission.grant()
                else: permission.deny()
            page.permissionRequested.connect(requested)
        else:  # pragma: no cover - 旧版 Qt
            def feature(url, kind):
                ok = kind == QWebEnginePage.Feature.MediaAudioCapture and same_origin(url)
                page.setFeaturePermission(url, kind, QWebEnginePage.PermissionPolicy.PermissionGrantedByUser if ok
                                          else QWebEnginePage.PermissionPolicy.PermissionDeniedByUser)
            page.featurePermissionRequested.connect(feature)

    def _ready(self):
        self.is_ready = True
        for fn in self.pending: fn()
        self.pending.clear()

    def call(self, fn):
        self.ensure_page()
        if self.is_ready: fn()
        else: self.pending.append(fn)

    # ---- 收音 -----------------------------------------------------------------
    def start(self, device_label=''):
        self.capturing = True; self.reset_turn()
        self.call(lambda: self.bridge.startCapture.emit(device_label or ''))

    def stop(self):
        self.capturing = False; self.reset_turn()
        if self.page and self.is_ready: self.bridge.stopCapture.emit()

    def _pcm(self, data, rate):
        if self.capturing: self._process(data, rate)

    def _state(self, info):
        self.info = info; self.capture_info.emit(info)
        if not info.get('ok'):
            self.capturing = False
            self.error.emit('网页收音失败：'+str(info.get('error', '未知错误')))

    def shutdown(self):
        self.stop()
        if self.page:
            page, self.page = self.page, None; self.is_ready = False
            page.deleteLater()
