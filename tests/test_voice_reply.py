"""计划书 v4 功能八：回复带语音。智能朗读的取舍与分段、云端失败退回本地朗读、打断后不再念，
以及先修的基线问题：已经完成的回复不会被之后的打断改成“被打断”。不联网，不需要音频设备。"""
import random
import tempfile
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace

from aicompanion.speech.output import SentenceBuffer, SpeechSegment
from aicompanion.speech.smart import SEGMENT_MAX, TAILS, TOTAL, SmartReader


def read(text, **kw):
    """按聊天时的流程：逐字流入 SentenceBuffer，每句交给 SmartReader，最后 finish。"""
    reader = SmartReader(rng=random.Random(0), **kw); buffer = SentenceBuffer(); out = []
    for char in text:
        for sentence in buffer.feed(char): out += reader.feed(sentence)
    for sentence in buffer.feed('', True): out += reader.feed(sentence)
    return out + reader.finish()


class SmartReaderTests(unittest.TestCase):
    def test_short_reply_is_read_in_full(self):
        out = read('诶嘿，你回来啦！\n今天想吃点什么？')
        self.assertEqual(''.join(out), '诶嘿，你回来啦！今天想吃点什么？')
        self.assertEqual(out[0], '诶嘿，你回来啦！')                                    # 第一段只放一句，尽快出声
        self.assertFalse(any(t in ''.join(out) for t in TAILS))

    def test_long_agent_result_keeps_conclusion_and_todo(self):
        body = '\n'.join(f'第{i}步我先检查了文件夹里的第{i}批文件，确认格式都没问题，然后把它们挪到了新目录里面' for i in range(1, 18))
        text = ('搞定啦，桌面上的截图都整理好了。\n' + body + '\n结论：一共整理了 128 张截图，按月份放好了。\n'
                '有 3 张文件名重复，需要你确认一下要不要保留？\n嘿嘿，夸我！')
        self.assertGreater(len(text), 700)
        out = read(text); spoken = ''.join(out)
        self.assertLessEqual(len(spoken), TOTAL+60)                                     # 约 75 秒以内
        self.assertTrue(spoken.startswith('搞定啦'))
        self.assertIn('一共整理了 128 张截图', spoken); self.assertIn('需要你确认', spoken)
        self.assertIn('嘿嘿，夸我', spoken)                                              # 结尾的情绪话补上
        self.assertEqual(sum(spoken.count(t) for t in TAILS), 1)                        # 省掉了内容：补一次提示
        self.assertTrue(all(len(seg) <= SEGMENT_MAX for seg in out))

    def test_lists_tables_code_paths_are_not_read_verbatim(self):
        text = ('给你列了几个方案：\n- 方案一：先备份 C:\\Users\\me\\a.txt\n- 方案二：直接覆盖\n- 方案三：问问同事\n'
                '| 名字 | 大小 |\n|---|---|\n| a.txt | 3KB |\n'
                '```python\nprint("secret_code")\n```\n你选哪个？')
        spoken = ''.join(read(text))
        self.assertIn('我列了3条，第一条是方案一', spoken); self.assertIn('我做了张表', spoken); self.assertIn('一段代码', spoken)
        for raw in ('方案二', 'a.txt', '3KB', 'secret_code', 'Users', '|'): self.assertNotIn(raw, spoken)
        self.assertIn('你选哪个', spoken)

    def test_duplicates_and_segment_sizes(self):
        out = read('好的。\n' + '我知道啦。\n'*3 + '这件事我记下了，明天早上九点提醒你去交周报，别担心。')
        spoken = ''.join(out)
        self.assertEqual(spoken.count('我知道啦'), 1)
        self.assertEqual(out[0], '好的。')

    def test_single_item_list_is_read_normally(self):
        self.assertIn('只有这一条', ''.join(read('注意：\n- 只有这一条')))


class SpeechOutputLogicTests(unittest.TestCase):
    """不创建真正的播放器（容器里没有音频设备），只测队列逻辑。"""

    def output(self):
        from aicompanion.speech.output import SpeechOutput
        notes = []
        out = SimpleNamespace(queue=deque(), busy=False, current_job=None, epoch=0, enabled=True, failed_generation=None,
                              fallback_generation=None, provider='breeze', voice='v', mood='calm', style='', language='zh', role_style='',
                              client=SimpleNamespace(tts_source=lambda: SimpleNamespace(cache_id='breeze:v')),
                              fallback=SimpleNamespace(emit=notes.append), errors=[], started=[])
        out._error = out.errors.append
        out._next = lambda: out.started.append(out.queue.popleft().provider) if out.queue and not out.busy else None
        out._prefetch = lambda: None
        out._synth_failed = lambda job, error: SpeechOutput._synth_failed(out, job, error)
        out.enqueue = lambda text, gid: SpeechOutput.enqueue(out, text, gid)
        return out, notes

    def test_cloud_failure_falls_back_to_local_for_rest_of_turn(self):
        out, notes = self.output()
        first = SpeechSegment('第一段', 'g1', 'breeze', 'v', 'calm', ''); second = SpeechSegment('第二段', 'g1', 'breeze', 'v', 'calm', '')
        other = SpeechSegment('别的轮次', 'g2', 'breeze', 'v', 'calm', '')
        out.current_job, out.busy = first, True; out.queue.extend([second, other])
        out._synth_failed(first, '没有配置声线')
        self.assertEqual((first.provider, second.provider, other.provider), ('system', 'system', 'breeze'))   # 只改这一轮
        self.assertEqual(out.started, ['system']); self.assertEqual(out.errors, [])     # 回复不中断
        self.assertIn('本地朗读', notes[0])
        out.busy = True; out.enqueue('第三段', 'g1')                                      # 这一轮后面的句子直接本地念
        self.assertEqual(out.queue[-1].provider, 'system')

    def test_japanese_cannot_fall_back(self):
        out, _ = self.output()
        job = SpeechSegment('こんにちは', 'g', 'qwen', 'v', 'calm', '', language='ja')
        out._synth_failed(job, 'x'); self.assertEqual(out.errors, ['x'])


class FinishedReplyStatusTests(unittest.TestCase):
    """基线问题：已经完成并展示完的回复，被下一轮 submit 或挂断通话时的 interrupt 改成 cancelled，重启显示“被打断”。"""

    def setUp(self):
        from aicompanion.storage.database import Database
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name)/'v.sqlite3'); self.db.initialize()

    def tearDown(self): self.tmp.cleanup()

    def window(self, generation, status, voice):
        from aicompanion.ui.main_window import MainWindow
        marks = []
        fake = SimpleNamespace(
            coordinator=SimpleNamespace(active=generation, cancel=lambda clear: None), workers=[],
            items={generation: SimpleNamespace(mark=marks.append)}, reply_status={generation: status},
            reply_characters={generation: 'deepseek-whale'}, reply_voice={generation: voice},
            texts={generation: '完整的一段回复。第二句。'}, delivered={generation: '完整的一段回复。'}, database=self.db,
            controller=SimpleNamespace(expression=SimpleNamespace(cancel=lambda *a: marks.append('expr-cancel')),
                                       character=SimpleNamespace(id='deepseek-whale')),
            speech=SimpleNamespace(stop=lambda: marks.append('speech-stop')), subtitle=SimpleNamespace(clear=lambda: None, hide=lambda: None),
            pet=SimpleNamespace(dispatch=lambda *a, **k: None, hide_activity=lambda: None, finish_reply=lambda: None),
            sentence_cues={}, call_active=False, _pet_subtitle=lambda text: None, _status=lambda text: None)
        fake._sync_delivery = lambda g: MainWindow._sync_delivery(fake, g)
        return fake, marks, MainWindow

    def status(self, generation):
        with self.db.transaction() as c:
            row = c.execute("SELECT status, content FROM chat_messages WHERE turn_id=?", (generation,)).fetchone()
        return tuple(row) if row else None

    def test_completed_reply_stays_complete(self):
        self.db.start_turn('g1', 'deepseek-whale')
        self.db.finish_turn('g1', 'deepseek-whale', '完整的一段回复。第二句。', 'complete')
        fake, marks, MainWindow = self.window('g1', 'complete', True)
        MainWindow._sync_delivery(fake, 'g1')                                              # 朗读还没念完时同步一次
        MainWindow.interrupt(fake)                                                        # 下一句话 / 挂断通话 / 退出
        self.assertEqual(fake.reply_status['g1'], 'complete')
        self.assertEqual(self.status('g1'), ('complete', '完整的一段回复。第二句。'))         # 全文都在气泡里显示过
        self.assertNotIn('[已打断]', marks); self.assertNotIn('expr-cancel', marks); self.assertIn('speech-stop', marks)

    def test_reply_still_generating_is_really_cancelled(self):
        self.db.start_turn('g2', 'deepseek-whale')
        fake, marks, MainWindow = self.window('g2', 'generating', True)
        MainWindow.interrupt(fake)
        self.assertEqual(fake.reply_status['g2'], 'cancelled'); self.assertIn('[已打断]', marks)
        self.assertEqual(self.status('g2'), ('cancelled', '完整的一段回复。'))               # 只记已经念出来的部分

    def test_old_cancelled_history_is_not_rewritten(self):
        self.db.start_turn('g3', 'deepseek-whale'); self.db.finish_turn('g3', 'deepseek-whale', '旧的', 'cancelled')
        from aicompanion.storage.database import Database
        Database(self.db.database_path).initialize()                                      # 重新打开不批量改旧记录
        self.assertEqual(self.status('g3'), ('cancelled', '旧的'))


def wav(ms=40):
    import io, wave
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b'\0\0'*int(24*ms))
    return buffer.getvalue()


try:
    from PySide6.QtWidgets import QApplication
except ImportError:   # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, '需要 PySide6')
class ProductionQueueTests(unittest.TestCase):
    """用真正的 SpeechOutput、真正的 TaskWorker 线程并发合成（Codex PR #39 复核要求的组合用例）。
    容器里没有音频设备，只把系统朗读引擎和播放器换成会按时“念完”的假对象；队列、预取、回退、出错逻辑都是生产代码。"""

    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def make(self, synthesize):
        from unittest import mock
        from PySide6.QtCore import QObject, QTimer, Signal
        from PySide6.QtMultimedia import QMediaPlayer
        from PySide6.QtTextToSpeech import QTextToSpeech
        import aicompanion.speech.output as module
        said, played = [], []

        class FakeTTS(QObject):
            State, BoundaryHint = QTextToSpeech.State, QTextToSpeech.BoundaryHint
            stateChanged = Signal(object); sayingWord = Signal(str, int, int, int); errorOccurred = Signal(object, str)
            def __init__(self, parent=None): super().__init__(parent); self._state = QTextToSpeech.State.Ready
            def setLocale(self, locale): pass
            def state(self): return self._state
            def errorString(self): return ''
            def stop(self, *a): self._state = QTextToSpeech.State.Ready
            def say(self, text):
                said.append(text); self._state = QTextToSpeech.State.Speaking
                def end():
                    self._state = QTextToSpeech.State.Ready; self.stateChanged.emit(QTextToSpeech.State.Ready)
                QTimer.singleShot(30, end)

        class FakePlayer(QObject):
            MediaStatus = QMediaPlayer.MediaStatus
            positionChanged = Signal(int); mediaStatusChanged = Signal(object); errorOccurred = Signal(object, str)
            def setAudioOutput(self, audio): pass
            def stop(self): pass
            def setSource(self, url): pass
            def setSourceDevice(self, device, url): pass
            def errorString(self): return ''
            def play(self):
                played.append(owner.current)
                QTimer.singleShot(30, lambda: self.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia))

        client = SimpleNamespace(calls=[], synthesize=None, tts_source=lambda: SimpleNamespace(cache_id='breeze:test-voice'))
        def synth(text, voice, token, **kw):
            client.calls.append(text); return synthesize(text)
        client.synthesize = synth
        with mock.patch.object(module, 'QTextToSpeech', FakeTTS), mock.patch.object(module, 'QMediaPlayer', FakePlayer), \
             mock.patch.object(module, 'QAudioOutput', lambda parent=None: QObject(parent)):
            owner = module.SpeechOutput(client)
        cache = tempfile.TemporaryDirectory(); self.addCleanup(cache.cleanup)                 # 短句缓存别写到真实目录，也别读到上次的
        patcher = mock.patch.object(module, 'cache_dir', lambda: Path(cache.name)); patcher.start(); self.addCleanup(patcher.stop)
        owner.enabled, owner.provider, owner.GAP_MS = True, 'breeze', 0
        errors = []; owner.error.connect(errors.append)
        self.addCleanup(lambda: (owner.stop(), [w.wait(2000) for w in list(owner.workers)]))
        return owner, client, said, played, errors

    def run_until(self, done, seconds=5.0):
        import time
        end = time.monotonic()+seconds
        while not done() and time.monotonic() < end:
            self.app.processEvents(); time.sleep(0.005)
        self.app.processEvents()

    def test_two_concurrent_cloud_failures_still_finish_locally(self):
        import time
        def fail(text):
            time.sleep(0.05); raise RuntimeError('服务暂时不可用')
        owner, client, said, played, errors = self.make(fail)
        texts = ['第一句先说这个事情', '第二句接着往下讲讲', '第三句最后收个尾巴']
        for text in texts: owner.enqueue(text, 'g1')
        self.run_until(lambda: len(said) == 3 and not owner.busy and not owner.workers)
        self.assertEqual(said, texts); self.assertEqual(errors, [])                        # 第二个云端失败不会打断本地朗读
        self.assertGreaterEqual(len(client.calls), 2)                                     # 确实有两句同时在云端合成

    def test_completed_full_reply_can_queue_more_than_twelve_sentences(self):
        owner, client, said, played, errors = self.make(lambda text: wav())
        sentences = [f'这是一轮的第{i}句。' for i in range(16)]
        for sentence in sentences:
            owner.enqueue(sentence, 'long-full', completed_reply=True)
        self.run_until(lambda: len(played) == len(sentences) and not owner.busy)
        self.assertEqual(played, sentences)
        self.assertEqual(errors, [])

    def test_each_agent_result_gets_its_own_turn(self):
        from aicompanion.ui.main_window import MainWindow
        state = {'ok': False}
        def maybe(text):
            if not state['ok']: raise RuntimeError('云端故障')
            return wav()
        owner, client, said, played, errors = self.make(maybe)
        fake = SimpleNamespace(speech=owner, _full_voice=lambda: True, _speech_style=lambda: 'smart')
        MainWindow.speak_reply(fake, '第一份结果做好了。')
        self.run_until(lambda: said and not owner.busy and not owner.workers)
        self.assertEqual(said, ['第一份结果做好了。'])
        state['ok'] = True; before = len(client.calls)
        MainWindow.speak_reply(fake, '第二份结果也好了。')
        self.run_until(lambda: played and not owner.busy and not owner.workers)
        self.assertGreater(len(client.calls), before)                                     # 云端恢复后重新用云端，不沿用上一份的回退
        self.assertEqual(played, ['第二份结果也好了。']); self.assertEqual(said, ['第一份结果做好了。']); self.assertEqual(errors, [])

    def test_one_shot_long_agent_result_is_still_trimmed(self):
        from aicompanion.ui.main_window import MainWindow
        queued = []
        speech = SimpleNamespace(enabled=True, enqueue=lambda text, gid: queued.append((text, gid)))
        fake = SimpleNamespace(speech=speech, _full_voice=lambda: True, _speech_style=lambda: 'smart')
        text = '，'.join(f'第{i}步检查了第{i}批文件并挪到新目录' for i in range(1, 60)) + '。'
        self.assertGreater(len(text), 700)
        MainWindow.speak_reply(fake, text)
        spoken = ''.join(t for t, _ in queued)
        self.assertLessEqual(len(spoken), TOTAL+60); self.assertTrue(all(len(t) <= SEGMENT_MAX for t, _ in queued))
        self.assertTrue(any(t in spoken for t in TAILS)); self.assertEqual(len({g for _, g in queued}), 1)
        self.assertTrue(queued[0][1].startswith('agent:'))

    def test_reading_does_not_depend_on_how_text_arrives(self):
        text = ('搞定啦。' + '，'.join(f'第{i}步检查了第{i}批文件' for i in range(40)) + '。\n- 一\n- 二\n| a | b |\n'
                + '\n'.join(f'- 第{i}组' for i in range(5)) + '\n| c |\n需要你确认吗？')
        whole = SmartReader(rng=random.Random(0)); buffer = SentenceBuffer(); one_shot = []
        for sentence in buffer.feed(text)+buffer.feed('', True): one_shot += whole.feed(sentence)
        one_shot += whole.finish()
        self.assertEqual(one_shot, read(text))
        self.assertLessEqual(len(''.join(one_shot)), TOTAL+60)


class DefaultsTests(unittest.TestCase):
    def test_smart_is_default_style(self):
        from aicompanion.pet import interjection
        self.assertEqual(interjection.DEFAULT_STYLE, 'smart'); self.assertIn('smart', interjection.STYLES)


if __name__ == '__main__':
    unittest.main()
