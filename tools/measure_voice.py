"""P4 外放打断实测：两种回声处理方案对比 + 本地识别检查。

需要外放音箱和麦克风，音量调到平时听的大小。不联网、不读 .env、不写真实数据库。
每个方案分两段：
  A. 安静段：她连续说 ~25 秒，你不说话 → 统计"自我打断"次数和残余回声；
  B. 打断段：她说话时，听到提示后随时开口说一句 → 统计能否打断、从开口到停下用了多久。
结果写到 output/voice-check/report.json。
"""
import argparse
import io
import json
from pathlib import Path
import sys
import time
import wave

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'output'/'voice-check'


def say(text):
    print(text, flush=True)
    with open(OUT/'measure.log', 'a', encoding='utf-8') as log: log.write(text+'\n')


def load_clips():
    """拼一段约 25 秒的台词音频（项目自带的合成试听，不含私人录音）。"""
    frames, rate = [], None
    for path in sorted((ROOT/'docs'/'audio').glob('*.wav')):
        with wave.open(str(path), 'rb') as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2: continue
            if rate is None: rate = w.getframerate()
            if w.getframerate() != rate: continue
            frames.append(w.readframes(w.getnframes()))
    if not frames: raise SystemExit('docs/audio 下没有可用的 WAV 试听音频')
    pcm = b''.join(frames)
    while len(pcm) < rate*2*25: pcm += pcm
    return pcm[:rate*2*26], rate


def configured_input():
    """只读打开真实数据库，取设置里选的麦克风（没设置则用系统默认）。不写入。"""
    import sqlite3
    path = ROOT/'data'/'aicompanion.sqlite3'
    if not path.exists(): return ''
    try:
        con = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True)
        row = con.execute("SELECT value FROM app_settings WHERE key='input_device'").fetchone(); con.close()
        return json.loads(row[0]) if row else ''
    except sqlite3.Error:
        return ''


def device_names(device_id):
    from PySide6.QtMultimedia import QMediaDevices
    inputs = QMediaDevices.audioInputs()
    mic = next((d for d in inputs if bytes(d.id()).hex() == device_id), QMediaDevices.defaultAudioInput())
    return mic.description(), QMediaDevices.defaultAudioOutput().description()


def wav_bytes(pcm, rate):
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
    return out.getvalue()


class Trial:
    def __init__(self, app, backend, pcm, rate, barge_rounds, device_id='', device_label=''):
        from aicompanion.audio.capture import Microphone
        from aicompanion.speech.output import SpeechOutput, SpeechSegment
        from aicompanion.voice.echo import EchoGate
        self.device_id = device_id; self.device_label = device_label
        self.app = app; self.backend = backend; self.rate = rate; self.Segment = SpeechSegment
        self.long_wav = wav_bytes(pcm, rate)
        self.short_wav = wav_bytes(pcm[:rate*2*12], rate)
        self.speech = SpeechOutput(None)
        self.speech.enabled = True
        if backend == 'web':
            from aicompanion.voice.web_voice import WebMicrophone
            self.mic = WebMicrophone(); self.speech.set_sink(self.mic.sink)
            self.mic.capture_info.connect(lambda info: self.result.update(capture=info))
        else:
            self.mic = Microphone()
        self.gate = EchoGate()
        self.mic.configure('continuous', self.speech.playback_rms, None, self.gate)
        self.mic.speech_started.connect(self._started)
        self.mic.error.connect(lambda text: self.result['errors'].append(text))
        self.result = {'backend': backend, 'errors': [], 'quiet': {}, 'barge': []}
        self.phase = 'idle'; self.false_starts = []; self.mic_sum = self.ref_sum = 0.0; self.frames = 0
        self.rounds_left = barge_rounds; self.round = None
        # 记录每帧的麦克风和参考能量，用于算残余回声
        original = self.gate.feed
        def feed(mic_rms, ref_rms):
            if self.phase == 'quiet' and ref_rms > 0.003:
                self.mic_sum += mic_rms; self.ref_sum += ref_rms; self.frames += 1
            beyond = original(mic_rms, ref_rms)
            # 提示之后第一帧"超出回声的响声"，近似你开口的时刻
            if self.phase == 'barge' and self.round is not None and 'prompt' in self.round and beyond and mic_rms > 0.02:
                self.round.setdefault('first_loud', time.monotonic())
            return beyond
        self.gate.feed = feed

    def play(self, raw, text):
        job = self.Segment(text, 'measure', 'qwen', '', 'calm', '', requested=True, audio=raw)
        self.speech.queue.append(job); self.speech._next()

    def run(self, done):
        self.done = done
        self.mic.start(self.device_label if self.backend == 'web' else self.device_id)
        say(f'\n=== 方案：{"浏览器回声消除" if self.backend == "web" else "回声门控"} ===')
        say('A. 安静段：接下来约 25 秒她会一直说话，请保持安静、不要出声……')
        QTimer.singleShot(1500, self._quiet)

    def _quiet(self):
        self.phase = 'quiet'; self.t0 = time.monotonic()
        self.play(self.long_wav, '安静段')
        self.speech.speaking.connect(self._quiet_end)

    def _quiet_end(self, active):
        if self.phase != 'quiet' or active: return
        self.speech.speaking.disconnect(self._quiet_end)
        self.phase = 'pause'
        self.result['quiet'] = {
            'seconds': round(time.monotonic()-self.t0, 1),
            'false_barge_ins': len(self.false_starts),
            'false_at_seconds': [round(t-self.t0, 1) for t in self.false_starts],
            'echo_ratio': round(self.mic_sum/self.ref_sum, 4) if self.ref_sum else None,
            'gate': self.gate.stats(),
            'echo_blocked_frames': getattr(self.mic.detector, 'echo_blocked', 0),
        }
        say(f'   自我打断 {len(self.false_starts)} 次；残余回声比 {self.result["quiet"]["echo_ratio"]}')
        QTimer.singleShot(1200, self._barge)

    def _barge(self):
        if self.rounds_left <= 0: return self._finish()
        self.rounds_left -= 1
        self.phase = 'barge'; self.round = {'start': time.monotonic()}
        say(f'B. 打断段（还剩 {self.rounds_left+1} 轮）：她开口 2 秒后会提示你，听到"现在说"就直接说一句话，比如"等一下"。')
        self.play(self.short_wav, '打断段')
        QTimer.singleShot(2000, self._prompt)
        self.timeout = QTimer(); self.timeout.setSingleShot(True); self.timeout.timeout.connect(self._missed); self.timeout.start(11500)

    def _prompt(self):
        if self.round is not None and 'stopped' not in self.round:
            self.round['prompt'] = time.monotonic(); say('   >>> 现在说！')

    def _started(self):
        now = time.monotonic()
        if self.phase == 'quiet':
            self.false_starts.append(now); return
        if self.phase == 'barge' and self.round is not None and 'stopped' not in self.round:
            self.speech.stop()   # 和真实界面一样：判定开口后立即停下
            self.round['stopped'] = time.monotonic()
            self.round['stop_call_ms'] = round((self.round['stopped']-now)*1000, 1)
            loud = self.round.get('first_loud')
            r = {'interrupted': True, 'early': 'prompt' not in self.round,
                 'detect_ms': round((now-loud)*1000) if loud else None,
                 'since_prompt_ms': round((now-self.round['prompt'])*1000) if 'prompt' in self.round else None}
            self.result['barge'].append(r)
            say(f'   打断成功：从声音变大到判定开口 {r["detect_ms"]} ms' + ('（提示前就触发了，可能是自我打断）' if r['early'] else ''))
            self.timeout.stop(); self.round = None
            QTimer.singleShot(2500, self._barge)

    def _missed(self):
        if self.round is None: return
        self.result['barge'].append({'interrupted': False})
        say('   没有打断到她（可能声音太小，或门槛太高）。')
        self.speech.stop(); self.round = None
        QTimer.singleShot(1500, self._barge)

    def _finish(self):
        self.phase = 'done'
        self.mic.stop(); self.speech.stop(); self.speech.set_sink(None)
        if self.backend == 'web': self.result.setdefault('capture', self.mic.info)
        self.done(self.result)


def check_local_asr(pcm, rate):
    from aicompanion.voice import local_asr
    problem = local_asr.status()
    if problem: return {'available': False, 'problem': problem}
    t = time.monotonic()
    text = local_asr.transcribe(pcm[:rate*2*8], rate)
    first = time.monotonic()-t
    t = time.monotonic(); text2 = local_asr.transcribe(pcm[:rate*2*4], rate); second = time.monotonic()-t
    return {'available': True, 'model': str(local_asr.find_model()[0]), 'first_call_seconds': round(first, 2),
            'warm_4s_clip_seconds': round(second, 2), 'text_8s': text, 'text_4s': text2}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--backends', default='qt,web')
    parser.add_argument('--rounds', type=int, default=3)
    args = parser.parse_args()
    from aicompanion.body.webengine_flags import apply_webengine_flags
    apply_webengine_flags()
    app = QApplication(sys.argv[:1]); app.setQuitOnLastWindowClosed(False)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/'measure.log').write_text('', encoding='utf-8')
    pcm, rate = load_clips()
    report = {'measured_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'clip_rate': rate, 'trials': []}
    device_id = configured_input()
    mic_name, speaker_name = device_names(device_id)
    report['devices'] = {'input': mic_name, 'output': speaker_name}
    say(f'麦克风：{mic_name}\n播放：{speaker_name}')
    say('外放打断实测：请打开音箱（不要戴耳机），音量调到平时听的大小。')
    live = []
    backends = [b for b in args.backends.split(',') if b in ('qt', 'web')]

    def next_trial():
        if not backends:
            say('\n本地识别检查……')
            try: report['local_asr'] = check_local_asr(pcm, rate)
            except Exception as error: report['local_asr'] = {'available': False, 'error': repr(error)}
            say(json.dumps(report['local_asr'], ensure_ascii=False))
            (OUT/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            say('\n全部完成，结果在 output\\voice-check\\report.json')
            app.quit(); return
        backend = backends.pop(0)
        trial = Trial(app, backend, pcm, rate, args.rounds, device_id, mic_name if device_id else '')
        def done(result):
            report['trials'].append(result)
            (OUT/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            QTimer.singleShot(1500, next_trial)
        live.append(trial)   # 保持引用，避免被回收
        trial.run(done)
    QTimer.singleShot(500, next_trial)
    app.exec()
    (OUT/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
