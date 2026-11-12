"""让真实 Qt 工作线程先跑完、再由主线程处理事件，复现桌面分享首段显示前的撤回。"""
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
import os
import re
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import test_ui_refresh as ui
from test_life import FakeClient, FakeSearch, HIT
from aicompanion.services.companion_dates import CompanionDates


@unittest.skipIf(ui.QApplication is None, '需要 PySide6')
class DesktopShareVisibilityTests(unittest.TestCase):
    def setUp(self):
        from aicompanion.ui.main_window import MainWindow
        from aicompanion.ui.sprite_view import PlaceholderAvatar
        self.app = ui.QApplication.instance() or ui.QApplication([])
        ui.UiRefreshTests.setUp(self)
        self.addCleanup(self.tmp.cleanup)
        for key, value in {'speech_enabled': False, 'speech_default_hint': True, 'community_web': False, 'relationship_learning': False,
                           'quiet_start': 0, 'quiet_end': 0, 'memory_analysis': False, 'agent_enabled': False}.items():
            self.db.set_setting(key, value)
        stack = self.enterContext(ExitStack())
        # SpeechOutput 即使禁用朗读，构造时也会加载系统 TTS 和播放器。
        self.native_tts = stack.enter_context(patch('aicompanion.speech.output.QTextToSpeech',
                                                    side_effect=AssertionError('分享测试不应加载原生语音引擎')))
        speech = stack.enter_context(patch('aicompanion.ui.main_window.SpeechOutput')).return_value
        speech.enabled = speech.busy = False
        speech.queue, speech.workers = (), ()
        speech.playback_rms.return_value = 0.0
        stack.enter_context(patch.object(MainWindow, '_build_tray', lambda w: setattr(w, 'tray', SimpleNamespace(hide=lambda: None))))
        stack.enter_context(patch.object(MainWindow, '_apply_avatar',
                                         lambda w: w.pet.set_view(PlaceholderAvatar('测试'), 'placeholder')))
        self.win = MainWindow(self.controller, self.settings)
        self.win.scheduler.stop()
        self.win.pump.stop()
        self.win.show()
        self.addCleanup(self.close_window)
        self.cid = self.controller.character.id
        self.dates = CompanionDates(self.db)
        self.dates.set_met_on(self.cid, date.today()-timedelta(days=99))
        self.db.add_message('user', '上午打过招呼了', self.cid)
        earlier = datetime.now(timezone.utc)-timedelta(minutes=8)
        with self.db.transaction() as c:
            c.execute("UPDATE chat_messages SET created_at=? WHERE role='user'", (earlier.strftime('%Y-%m-%d %H:%M:%S'),))
        self.win.life.run(self.cid, FakeSearch([HIT]), FakeClient('{"thought":"好想听听","worth":0.95}'))
        self.share = self.win.outreach_scheduler.next(self.cid, 2, 3600)
        self.assertEqual(self.share.source, 'share')

    def close_window(self):
        from PySide6.QtCore import QCoreApplication, QEvent
        for worker in tuple(self.win.workers):
            worker.cancellation.cancel()
            worker.wait(5000)
        self.app.processEvents()
        self.win.hide()
        self.win.pet.shutdown()
        self.win.pet.deleteLater()
        self.win.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def start(self, first='', last='一起一百天啦，给你分享鲸鱼的报道。'):
        entered, resume = threading.Event(), threading.Event()
        self.addCleanup(resume.set)
        prompts = []
        class Model:
            def generate_reply_stream(self, history, prompt, cancellation=None):
                prompts.append(prompt)
                if first:
                    yield first(prompt) if callable(first) else first
                entered.set()
                if not resume.wait(8):
                    raise TimeoutError('测试没有放行工作线程')
                yield last
        self.controller.client = lambda *a, **kw: Model()
        self.win._deliver_outreach(self.cid, self.share)
        worker, = self.win.workers
        self.assertTrue(entered.wait(5))
        self.assertIn('100', prompts[0])
        self.app.processEvents()
        return worker, resume

    def finish(self, worker, resume, before_dispatch=None):
        resume.set()
        self.assertTrue(worker.wait(5000))
        if before_dispatch:
            before_dispatch()
        deadline = time.monotonic()+5
        while self.win.workers and time.monotonic()<deadline:
            self.app.processEvents()
        self.assertFalse(self.win.workers)

    def assert_withdrawn(self, turn):
        self.assertEqual(self.win.texts[turn], '')
        with self.db.transaction() as c:
            self.assertFalse(c.execute('SELECT 1 FROM outreach_log').fetchone())
            self.assertFalse(c.execute("SELECT 1 FROM chat_messages WHERE role='assistant' AND turn_id=?", (turn,)).fetchone())
        self.assertFalse(self.dates.mentioned(self.cid, 'milestone:100'))
        self.assertIn('milestone:100', self.dates.prompt(self.cid, claim='next')[1])
        self.dates.release(self.cid, 'next')

    def test_hidden_task_then_switch_off_before_first_visible_text(self):
        worker, resume = self.start('<task>合成的隐藏动作</task>')
        turn = worker.generation_id
        self.assertEqual(self.win.texts[turn], '')
        self.win.life.set_enabled(False)
        self.finish(worker, resume)
        self.assert_withdrawn(turn)

    def test_late_spoken_summary_replaces_speech_but_not_visible_body(self):
        self.win.speech.enabled = True
        body = '关于这篇报道，我把细节写在这里。'
        summary = '听这一句就好。'
        worker, resume = self.start(first=body, last='<say>' + summary + '</say>')
        turn = worker.generation_id
        self.assertEqual(self.win.items[turn].content, body)
        self.win.speech.enqueue.assert_not_called()
        self.finish(worker, resume)
        self.win.speech.enqueue.assert_called_once_with(summary, 'say:' + turn)
        self.assertEqual(self.win.items[turn].content, body)
        with self.db.transaction() as c:
            row = c.execute("SELECT content FROM chat_messages WHERE turn_id=? AND role='assistant'", (turn,)).fetchone()
        self.assertEqual(row[0], body)

    def test_share_without_native_speech_backend(self):
        worker, resume = self.start()
        self.finish(worker, resume)
        self.assertEqual(self.win.life.recent(self.cid)[0]['status'], 'shared')
        self.win.speech.enqueue.assert_not_called()
        self.native_tts.assert_not_called()

    def exercise_final_task(self, *, share=False, interrupt=False):
        from aicompanion.pet.script import Cue, Vocab
        from aicompanion.speech.output import spoken_text
        original = '请帮我整理桌面，保留文件的原名。'
        visible = '用 ` 号分隔。\n好啦。'
        reply = visible.replace('好啦', '【开心】好啦') + '<task>模型改写过的任务</task>'
        self.controller.client = lambda *a, **kw: FakeClient(reply)
        self.controller.agent_enabled = True
        self.win.vocab = self.controller.vocabs[self.cid] = Vocab({'开心': 'happy'})
        self.db.set_setting('speech_style', 'full')
        self.win.speech.enabled = True
        self.win.speech.enqueue.side_effect = lambda text, turn, **_: self.win._sentence_started(spoken_text(text))
        # 跳过启动 Agent 前的界面等待，调用真实 _offer_task，只替换外部执行入口。
        timer = SimpleNamespace(singleShot=lambda delay, callback: callback())
        with patch('aicompanion.ui.main_window.QTimer', timer), patch.object(self.win.agent, 'start') as start, \
                patch.object(self.win.pet, 'perform') as perform:
            if share:
                self.win._deliver_outreach(self.cid, self.share)
            else:
                self.win.coordinator.active = 'final-user-task'
                self.win._start('final-user-task', original)
            worker, = self.win.workers
            turn = worker.generation_id
            self.assertTrue(worker.wait(5000))
            if interrupt:
                self.win.interrupt()
            self.app.processEvents()
            if share or interrupt:
                start.assert_not_called()
            else:
                start.assert_called_once_with(original, announce=False)
            if interrupt:
                perform.assert_not_called()
                self.win.speech.enqueue.assert_not_called()
            else:
                self.assertEqual(self.win.items[turn].content, visible)
                perform.assert_called_once_with(Cue('expression', 'happy'))
                spoken = [call.args[0] for call in self.win.speech.enqueue.call_args_list if call.args[1] == turn]
                self.assertEqual(spoken, visible.splitlines())

    def test_final_markers_start_user_task_once_with_original_query(self):
        self.exercise_final_task()

    def test_final_markers_in_proactive_share_do_not_start_task(self):
        self.exercise_final_task(share=True)

    def test_interrupted_user_turn_does_not_dispatch_final_markers(self):
        self.exercise_final_task(interrupt=True)

    def test_worker_completes_while_window_has_not_displayed_the_share(self):
        worker, resume = self.start()
        turn = worker.generation_id
        self.win.life.set_enabled(False)
        self.finish(worker, resume, lambda: self.assertFalse(self.dates.mentioned(self.cid, 'milestone:100')))
        self.assert_withdrawn(turn)

    def test_first_visible_text_preserves_delivery_and_confirms_dates_on_completion(self):
        worker, resume = self.start('认识你一百天啦。')
        turn = worker.generation_id
        self.assertIn('一百天', self.win.items[turn].content)
        self.win.life.set_enabled(False)
        self.finish(worker, resume, lambda: self.assertFalse(self.dates.mentioned(self.cid, 'milestone:100')))
        self.assertIn('鲸鱼的报道', self.win.items[turn].content)
        self.assertTrue(self.dates.mentioned(self.cid, 'milestone:100'))
        self.assertEqual(self.win.life.recent(self.cid)[0]['status'], 'shared')

    def test_control_only_reply_never_counts_as_an_article_or_date(self):
        from aicompanion.pet.script import Vocab
        self.win.vocab = self.controller.vocabs[self.cid] = Vocab(motions={'挥手': 'wave'})
        worker, resume = self.start(last='<挥手><task>仅有隐藏标记</task>')
        turn = worker.generation_id
        self.finish(worker, resume)
        self.assert_withdrawn(turn)

    @staticmethod
    def first_image(prompt):
        choice = re.search(r'^(s\d+)｜', prompt, re.MULTILINE)
        if not choice:
            raise AssertionError('本次测试需要内置的表情候选')
        return f'<sticker id="{choice[1]}"/>'

    def test_failed_first_image_keeps_switch_in_force(self):
        from aicompanion.ui.chat_view import SegmentedReply
        with patch.object(SegmentedReply, 'set_attachment', return_value=False) as display:
            worker, resume = self.start(self.first_image)
            turn = worker.generation_id
            display.assert_called_once()
            self.win.life.set_enabled(False)
            self.finish(worker, resume)
        self.assert_withdrawn(turn)

    def test_image_shown_before_text_preserves_share(self):
        worker, resume = self.start(self.first_image)
        turn = worker.generation_id
        self.assertEqual(len(self.win.displayed_attachments), 1)
        self.assertEqual(self.win.texts[turn], '')
        self.win.life.set_enabled(False)
        self.finish(worker, resume)
        self.assertIn('鲸鱼的报道', self.win.items[turn].content)
        self.assertTrue(self.dates.mentioned(self.cid, 'milestone:100'))

    def test_interrupted_worker_releases_its_own_date_claim(self):
        worker, resume = self.start()
        turn = worker.generation_id
        self.finish(worker, resume, lambda: self.win.interrupt())
        self.assert_withdrawn(turn)

    def test_regular_user_chat_keeps_its_date_settlement(self):
        turn = 'user-date'
        self.controller.client = lambda *a, **kw: FakeClient('一起一百天啦。')
        self.win.coordinator.active = turn
        self.win._start(turn, '我们认识多久啦？')
        worker, = self.win.workers
        self.assertTrue(worker.wait(5000))
        self.assertTrue(self.dates.mentioned(self.cid, 'milestone:100'))
        self.app.processEvents()
        self.assertIn('一百天', self.win.items[turn].content)

    def test_late_withdrawal_preserves_newer_turns_date_claim(self):
        worker, resume = self.start()
        turn = worker.generation_id
        def replace_claim():
            self.dates.release(self.cid, turn)
            self.assertIn('milestone:100', self.dates.prompt(self.cid, claim='new-turn')[1])
            self.win.life.set_enabled(False)
        self.finish(worker, resume, replace_claim)
        self.assertFalse(self.dates.mentioned(self.cid, 'milestone:100'))
        self.assertIn('milestone:100', self.dates.held(self.cid, 'new-turn')[0])
        self.dates.release(self.cid, 'new-turn')
