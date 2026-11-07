"""虚拟麦克风驱动真实 Qt 通话流程；临时数据库、缓存音频、无网络。"""
import io
import json
from pathlib import Path
import tempfile
from threading import Lock
import time
import wave

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from aicompanion.config import Settings
from aicompanion.controllers.chat_controller import ChatController
from aicompanion.domain.cancellation import ReplyCancelled
from aicompanion.llm.mock_client import MockLLMClient
from aicompanion.services.memory_service import MemoryService
from aicompanion.storage.database import Database
from aicompanion.ui.main_window import MainWindow
from aicompanion.ui.settings_window import SettingsWindow

ROOT = Path(__file__).resolve().parents[1]
PCM = bytes(3200)


class CallFixture:
    def __init__(self):
        self.asr_provider = 'qwen'; self.calls = 0; self.cancelled = 0; self.lock = Lock()
        with wave.open(str(ROOT/'docs/audio/anime-ja.wav'), 'rb') as source:
            buffer = io.BytesIO()
            with wave.open(buffer, 'wb') as clip:
                clip.setnchannels(1); clip.setsampwidth(2); clip.setframerate(source.getframerate())
                clip.writeframes(source.readframes(source.getframerate()*3))
        self.audio = buffer.getvalue()

    def ensure_recognition_ready(self): pass

    def transcribe(self, pcm, rate, token):
        with self.lock:
            self.calls += 1; number = self.calls
        # 前两次延迟模拟网络慢于用户下一段讲话，最后一次用于取消验收。
        delays = {1: 1.3, 2: 1.4, 3: .2, 4: .1, 5: 2}
        deadline = time.monotonic()+delays.get(number, .1)
        try:
            while time.monotonic()<deadline:
                time.sleep(.02); token.check()
            return {1: '今天有点累。', 2: '先听我说就好。', 3: '我还想说说今天的事情。', 4: ''}.get(number, '过期语音')
        except ReplyCancelled:
            with self.lock: self.cancelled += 1
            raise

    def synthesize(self, text, voice, token, **kwargs):
        time.sleep(.05); token.check()
        return self.audio


def main():
    app = QApplication([]); app.setQuitOnLastWindowClosed(False)
    temp = tempfile.TemporaryDirectory()
    db = Database(Path(temp.name)/'call.sqlite3'); db.initialize()
    settings = Settings('AICompanion', db.database_path, 'mock', 'https://example.invalid', '', 'mock', .8, 10,
        asr_provider='qwen', tts_provider='qwen', qwen_api_key='fixture', qwen_tts_voice='fixture',
        speech_language='ja', avatar_preview='emilia', character_preset='emilia')
    window = MainWindow(ChatController(db, MemoryService(db), MockLLMClient()), settings)
    fixture = CallFixture(); window.speech_client = fixture; window.speech.client = fixture
    state = {'virtual_microphone': True, 'avatar_ready': False, 'audio_played': False, 'errors': []}
    window.microphone.start = lambda device='': state.update(microphone_open=True)
    window.microphone.stop = lambda: state.update(microphone_open=False)
    window.avatar.bridge.loaded.connect(lambda value: state.update(avatar_ready=value=='illustration-ready'))
    window.speech.error.connect(lambda error: state['errors'].append(error))
    window.show()

    def start_speech(): window.microphone.speech_started.emit()
    def end_speech(): window.microphone.utterance.emit(PCM, 16000)
    QTimer.singleShot(1400, window._toggle_call)
    QTimer.singleShot(1500, start_speech)
    QTimer.singleShot(1600, end_speech)
    QTimer.singleShot(1750, start_speech)
    QTimer.singleShot(2600, end_speech)
    QTimer.singleShot(2750, start_speech)

    def verify_waiting():
        state['queued_start_preserves_speaking'] = window.user_speaking
        state['no_early_reply'] = not window.workers and not window.texts
    QTimer.singleShot(3100, verify_waiting)
    QTimer.singleShot(3300, end_speech)

    def verify_recognition_wait():
        state['waits_for_pending_asr'] = bool(window.asr_worker) and not window.texts
    QTimer.singleShot(4000, verify_recognition_wait)

    def on_speaking(active):
        if not active or state['audio_played']: return
        state['audio_played'] = True
        state['users_combined_before_reply'] = len([r for r in db.recent_messages(100) if r['role']=='user'])==3
        QTimer.singleShot(80, interrupt)

    def interrupt():
        start_speech()
        state['voice_interrupt_stops_audio'] = not window.speech.busy and not window.speech.queue
        state['users_before_empty'] = len([r for r in db.recent_messages(100) if r['role']=='user'])
        end_speech()
        QTimer.singleShot(350, verify_empty_and_cancel)

    def verify_empty_and_cancel():
        state['empty_not_saved'] = len([r for r in db.recent_messages(100) if r['role']=='user'])==state['users_before_empty']
        start_speech(); end_speech()
        QTimer.singleShot(100, end_call)

    def end_call():
        epoch = window.call_epoch
        window._end_call()
        window._recognized(epoch, window.controller.character.id, '不应该保存的迟到语音')
        state['late_result_ignored'] = len([r for r in db.recent_messages(100) if r['role']=='user'])==3
        QTimer.singleShot(350, verify_failure_pause)

    def verify_failure_pause():
        window._toggle_call()
        epoch = window.call_epoch; role = window.controller.character.id
        for _ in range(3): window._recognition_failed(epoch, role, '测试网络中断')
        state['three_errors_pause_call'] = not window.call_active and not state['microphone_open']
        state['cancelled_queue_empty'] = not window.asr_queue
        window.grab().save(str(ROOT/'docs/ui-call-smoke.png'))
        # 同时验收识别服务切换及保存；不写入真实用户数据库。
        dialog = SettingsWindow(window.controller, settings, window, speech_output=window.speech)
        dialog.tabs.setCurrentIndex(1); dialog.show()
        dialog.asr_provider.setCurrentIndex(dialog.asr_provider.findData('zhipu'))
        state['settings_model_switch'] = dialog.asr_model.text()==settings.asr_model
        dialog.asr_provider.setCurrentIndex(dialog.asr_provider.findData('qwen'))
        dialog.save()
        state['settings_provider_saved'] = db.get_setting('asr_provider')=='qwen'
        QTimer.singleShot(150, window.request_exit)

    window.speech.speaking.connect(on_speaking)
    # 不满足播放前置条件也必须释放所有线程，并以失败退出，不让自检挂住。
    QTimer.singleShot(12000, window.request_exit)
    code = app.exec()
    state.update(exit_code=code, recognition_requests=fixture.calls, cancelled_requests=fixture.cancelled,
        remaining_workers=len(window.workers)+len(window.tasks)+len(window.speech.workers))
    required = ['avatar_ready', 'audio_played', 'queued_start_preserves_speaking', 'no_early_reply',
        'waits_for_pending_asr', 'users_combined_before_reply', 'voice_interrupt_stops_audio',
        'empty_not_saved', 'late_result_ignored', 'three_errors_pause_call', 'cancelled_queue_empty',
        'settings_model_switch', 'settings_provider_saved']
    state['passed'] = all(state.get(key) for key in required) and not state['errors'] and code==0 and state['remaining_workers']==0 and fixture.cancelled>=1
    (ROOT/'docs/call-smoke.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(state, ensure_ascii=False, indent=2), flush=True)
    temp.cleanup()
    return 0 if state['passed'] else 1


if __name__ == '__main__': raise SystemExit(main())
