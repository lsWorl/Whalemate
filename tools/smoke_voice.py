"""真实 Qt 音频/口型/设置/打断自检；复用合成试听，不发网络请求。"""
from pathlib import Path
import argparse
import io
import json
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

ROOT=Path(__file__).resolve().parents[1]


class FixtureClient:
    def __init__(self,japanese=False,experimental=False):
        path=ROOT/'data/private-voice/actor-preview.wav' if experimental else ROOT/'docs/audio'/('anime-ja.wav' if japanese else 'anime-calm.wav')
        with wave.open(str(path),'rb') as source:
            buffer=io.BytesIO()
            with wave.open(buffer,'wb') as clip:
                clip.setnchannels(1); clip.setsampwidth(2); clip.setframerate(source.getframerate())
                clip.writeframes(source.readframes(source.getframerate()*3))
        self.raw=buffer.getvalue(); self.lock=Lock()
        self.calls=0; self.active=0; self.peak=0; self.cancelled=0
        self.languages=set()

    def synthesize(self,text,voice,token,**kwargs):
        with self.lock:
            self.calls+=1; number=self.calls; self.active+=1; self.peak=max(self.peak,self.active)
            self.languages.add(kwargs.get('language','zh'))
        try:
            # 第二个任务故意延迟，让打断测试覆盖尚未完成的预取请求。
            for _ in range(70 if number==2 else 2):
                time.sleep(.05); token.check()
            return self.raw
        except ReplyCancelled:
            with self.lock: self.cancelled+=1
            raise
        finally:
            with self.lock: self.active-=1


def main():
    parser=argparse.ArgumentParser(description='Offline GUI and audio smoke test.')
    parser.add_argument('--japanese',action='store_true')
    parser.add_argument('--experimental',action='store_true')
    args=parser.parse_args()
    suffix='-actor' if args.experimental else '-japanese' if args.japanese else ''
    app=QApplication([]); app.setQuitOnLastWindowClosed(False)
    temp=tempfile.TemporaryDirectory()
    db=Database(Path(temp.name)/'smoke.sqlite3'); db.initialize()
    db.set_setting('speech_style','full')   # 这个自检验证逐句朗读与字幕
    settings=Settings('AICompanion',db.database_path,offline=True,
        tts_provider='qwen',qwen_tts_voice='fixture',speech_language='ja' if args.japanese else 'zh')
    window=MainWindow(ChatController(db,MemoryService(db),MockLLMClient()),settings)
    fixture=FixtureClient(args.japanese,args.experimental); window.speech.client=fixture
    window.speak_toggle.setChecked(True); window.show()
    state={'speaking':False,'mouth_peak':0,'errors':[],'subtitles':[]}
    window.speech.speaking.connect(lambda active:state.update(speaking=state['speaking'] or active))
    window.speech.mouth.connect(lambda value:state.update(mouth_peak=max(state['mouth_peak'],value)))
    window.speech.error.connect(lambda error:state['errors'].append(error))
    window.speech.subtitle.connect(lambda text:state['subtitles'].append(text))
    QTimer.singleShot(1500,lambda:window.submit('今天有点累。'))
    QTimer.singleShot(1800,lambda:window.submit('先听我说就好。'))

    def interrupt():
        generation=window.coordinator.active
        state['prefetched_before_interrupt']=fixture.calls>=2
        state['heard_before_interrupt']=bool(window.delivered.get(generation))
        window.interrupt()
        state['stopped']=not window.speech.busy and not window.speech.queue
    QTimer.singleShot(4600,interrupt)

    dialog=SettingsWindow(window.controller,settings,window,speech_output=window.speech)
    dialog.tabs.setCurrentIndex(1)
    QTimer.singleShot(5100,dialog.show)
    QTimer.singleShot(5300,dialog.preview_voice)
    QTimer.singleShot(6500,lambda:dialog.grab().save(str(ROOT/f'docs/ui-voice-settings{suffix}.png')))

    def finish():
        dialog.reject(); window.grab().save(str(ROOT/f'docs/ui-voice-smoke{suffix}.png'))
        window.request_exit()
    QTimer.singleShot(8000,finish)
    code=app.exec()
    state.update(exit_code=code,requests=fixture.calls,peak_requests=fixture.peak,cancelled_requests=fixture.cancelled,
        remaining_workers=len(window.speech.workers),languages=sorted(fixture.languages))
    # 夹具使用真实日语 WAV，但播放器字幕必须仍为中文原句。
    state['chinese_subtitles']=bool(state['subtitles']) and all(any('\u4e00'<=c<='\u9fff' for c in text) for text in state['subtitles'])
    state['passed']=all([state['speaking'],state['mouth_peak']>0,
        state.get('prefetched_before_interrupt'),state.get('heard_before_interrupt'),state.get('stopped'),
        fixture.peak<=2,fixture.cancelled>=1,not window.speech.workers,not state['errors'],code==0,
        state['chinese_subtitles'],fixture.languages=={'ja' if args.japanese else 'zh'}])
    (ROOT/f'docs/voice-smoke{suffix}.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(state,ensure_ascii=False,indent=2),flush=True)
    temp.cleanup()
    return 0 if state['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
