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


class DefaultsTests(unittest.TestCase):
    def test_smart_is_default_style(self):
        from aicompanion.pet import interjection
        self.assertEqual(interjection.DEFAULT_STYLE, 'smart'); self.assertIn('smart', interjection.STYLES)


if __name__ == '__main__':
    unittest.main()
