"""P4 语音：回声门控、收音轮次、本地识别辅助函数。不需要 Qt、麦克风或模型。"""
from array import array
from pathlib import Path
import random
import tempfile
import unittest

from aicompanion.voice.echo import EchoGate
from aicompanion.voice.turns import TurnDetector, frame_rms
from aicompanion.voice import local_asr, hotkey


def frame(amplitude, rate=16000):
    n = rate//50; v = int(max(0.0, min(1.0, amplitude))*32767)
    return array('h', [v if i % 2 else -v for i in range(n)]).tobytes()


def fake_vad(data, rate):
    return frame_rms(data) > 0.01


class Scene:
    """逐帧驱动 TurnDetector：ref 为播放音量序列，echo 为回声耦合，lag 为延迟帧数。"""

    def __init__(self, detector, coupling=0.3, lag=5, seed=1):
        self.d = detector; self.coupling = coupling; self.lag = lag
        self.refs = []; self.t = 0.0; self.rng = random.Random(seed)

    def step(self, ref=0.0, user=0.0, key=False):
        self.refs.append(ref)
        delayed = self.refs[-1-self.lag] if len(self.refs) > self.lag else 0.0
        mic = self.coupling*delayed+user+0.002
        self.t += 0.02
        ref_at = lambda t, r=ref: r
        return self.d.feed(frame(mic), self.t, ref_at, key)

    def speech_ref(self):
        # 类似说话的起伏：每 3–8 帧换一次音量，偶有停顿
        if not hasattr(self, '_left') or self._left <= 0:
            self._level = self.rng.choice([0.0, 0.05, 0.1, 0.15, 0.2, 0.25]); self._left = self.rng.randint(3, 8)
        self._left -= 1
        return self._level


class EchoGateTest(unittest.TestCase):
    def test_learns_coupling_and_lag_from_echo(self):
        d = TurnDetector(mode='continuous', vad=fake_vad, gate=EchoGate())
        scene = Scene(d, coupling=0.3, lag=5)
        events = []
        for _ in range(250): events += scene.step(ref=scene.speech_ref())
        self.assertEqual([e for e in events if e[0] == 'start'], [], '只有回声时不能自我打断')
        self.assertTrue(d.gate.calibrated)
        self.assertAlmostEqual(d.gate.c, 0.3, delta=0.08)
        self.assertLessEqual(abs(d.gate.lag-5), 1)

    def test_loud_echo_never_self_interrupts(self):
        # 回声和原声一样大（音箱贴着麦克风），十秒都不能误触发
        d = TurnDetector(mode='continuous', vad=fake_vad, gate=EchoGate())
        scene = Scene(d, coupling=0.9, lag=8, seed=3)
        events = []
        for _ in range(500): events += scene.step(ref=scene.speech_ref())
        self.assertFalse(any(e[0] == 'start' for e in events))

    def test_user_can_barge_in_over_echo(self):
        d = TurnDetector(mode='continuous', vad=fake_vad, gate=EchoGate())
        scene = Scene(d, coupling=0.3, lag=5)
        for _ in range(150): scene.step(ref=scene.speech_ref())
        started_at = None
        for i in range(40):
            if any(e[0] == 'start' for e in scene.step(ref=scene.speech_ref(), user=0.15)):
                started_at = i; break
        self.assertIsNotNone(started_at, '你开口要能打断她')
        self.assertLessEqual(started_at*20, 300, '打断判定不超过 300 ms')
        self.assertTrue(d.barge_in)

    def test_weak_echo_lowers_threshold(self):
        # 耳机 / 音箱离得远：几乎没有回声，门槛要自动降下来，小声说话也能打断
        d = TurnDetector(mode='continuous', vad=fake_vad, gate=EchoGate())
        scene = Scene(d, coupling=0.005, lag=12)
        for _ in range(100): scene.step(ref=scene.speech_ref())
        self.assertLess(d.gate.c, 0.1)
        started = None
        for i in range(30):
            if any(e[0] == 'start' for e in scene.step(ref=scene.speech_ref(), user=0.03)): started = i; break
        self.assertIsNotNone(started); self.assertLessEqual(started*20, 300)

    def test_bluetooth_delay_does_not_self_interrupt(self):
        d = TurnDetector(mode='continuous', vad=fake_vad, gate=EchoGate())
        scene = Scene(d, coupling=0.4, lag=15, seed=7)   # 300 ms 延迟
        events = []
        for _ in range(500): events += scene.step(ref=scene.speech_ref())
        self.assertFalse(any(e[0] == 'start' for e in events))

    def test_echo_tail_after_playback(self):
        d = TurnDetector(mode='continuous', vad=fake_vad, gate=EchoGate())
        scene = Scene(d, coupling=0.3, lag=10)
        for _ in range(150): scene.step(ref=0.2)
        events = []
        for _ in range(30): events += scene.step(ref=0.0)   # 播放停了，延迟里的回声还在
        self.assertFalse(any(e[0] == 'start' for e in events))

    def test_no_gating_when_silent(self):
        gate = EchoGate(coupling=2.0)
        self.assertTrue(gate.feed(0.05, 0.0))
        self.assertFalse(gate.playing)


class TurnTest(unittest.TestCase):
    def test_continuous_start_and_end(self):
        d = TurnDetector(mode='continuous', vad=fake_vad)
        scene = Scene(d)
        events = []
        for _ in range(30): events += scene.step(user=0.1)
        for _ in range(45): events += scene.step()
        kinds = [e[0] for e in events]
        self.assertEqual(kinds, ['start', 'end'])
        self.assertFalse(events[0][1]['barge_in'])
        self.assertGreater(len(events[1][1]), 30*640)

    def test_push_to_talk(self):
        d = TurnDetector(mode='ptt', vad=fake_vad)
        scene = Scene(d)
        events = scene.step(key=True)
        self.assertEqual(events[0][0], 'start')
        for _ in range(30): events += scene.step(user=0.1, key=True)
        events += scene.step(key=False)
        self.assertEqual([e[0] for e in events], ['start', 'end'])

    def test_short_press_is_cancelled(self):
        d = TurnDetector(mode='ptt', vad=fake_vad)
        scene = Scene(d)
        events = scene.step(key=True)+scene.step(key=True)+scene.step(key=False)
        self.assertEqual([e[0] for e in events], ['start', 'cancel'])

    def test_ptt_ignores_speech_without_key(self):
        d = TurnDetector(mode='ptt', vad=fake_vad)
        scene = Scene(d)
        events = []
        for _ in range(60): events += scene.step(user=0.2)
        self.assertEqual(events, [])

    def test_toggle_auto_ends_after_pause(self):
        d = TurnDetector(mode='toggle', vad=fake_vad)
        scene = Scene(d)
        events = scene.step(key=True)+scene.step(key=False)
        for _ in range(20): events += scene.step(user=0.1)
        for _ in range(80): events += scene.step()
        self.assertEqual([e[0] for e in events], ['start', 'end'])

    def test_toggle_second_press_ends(self):
        d = TurnDetector(mode='toggle', vad=fake_vad)
        scene = Scene(d)
        events = scene.step(key=True)+scene.step(key=False)
        for _ in range(20): events += scene.step(user=0.1)
        events += scene.step(key=True)
        self.assertEqual([e[0] for e in events], ['start', 'end'])

    def test_set_mode_resets(self):
        d = TurnDetector(mode='ptt', vad=fake_vad)
        Scene(d).step(key=True)
        self.assertTrue(d.active)
        d.set_mode('continuous'); self.assertFalse(d.active)


class LocalAsrTest(unittest.TestCase):
    def test_clean_text(self):
        self.assertEqual(local_asr.clean_text('<|zh|><|NEUTRAL|><|Speech|><|withitn|>你好呀。'), '你好呀。')

    def test_find_model_in_subfolder(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(local_asr.find_model(Path(tmp)))
            sub = Path(tmp)/'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17'; sub.mkdir()
            (sub/'model.int8.onnx').write_bytes(b'x'); (sub/'tokens.txt').write_text('a 0')
            model, tokens = local_asr.find_model(Path(tmp))
            self.assertEqual(model.name, 'model.int8.onnx'); self.assertEqual(tokens.parent, sub)

    def test_release_if_idle(self):
        local_asr._recognizer = object(); local_asr._last_used = 0.0
        self.assertFalse(local_asr.release_if_idle(now=10.0))
        self.assertTrue(local_asr.release_if_idle(now=10_000.0))
        self.assertFalse(local_asr.loaded())


class HotkeyTest(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(hotkey.label('RCtrl'), '右 Ctrl')
        self.assertEqual(hotkey.label('nope'), hotkey.label(hotkey.DEFAULT_KEY))
        self.assertIn(hotkey.key_reader('F8')(), (True, False))

    def test_default_is_push_to_talk(self):
        # 负责人 2026-11-11：语音默认绑定按键，不按键不收音
        from aicompanion.voice.turns import DEFAULT_MODE, MODES
        self.assertEqual(DEFAULT_MODE, 'ptt'); self.assertIn(DEFAULT_MODE, MODES)


if __name__ == '__main__': unittest.main()
