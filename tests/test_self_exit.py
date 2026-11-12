"""临时资料、离线回复和可控时钟验证告别、离开和接续。"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aicompanion.agent.tools_server import Task, ToolBox
from aicompanion.brain.life import LifeService
from aicompanion.brain.scheduler import Outreach, ProactiveScheduler
from aicompanion.brain.self_exit import remember_departure, take_return_greeting
from aicompanion.bridge.outbox import OutboxStore
from aicompanion.controllers.chat_controller import ChatController
from aicompanion.domain.cancellation import CancellationToken
from aicompanion.expression.stream import ExpressionStream
from aicompanion.llm.mock_client import MockLLMClient
from aicompanion.services.conversation_coordinator import ConversationCoordinator
from aicompanion.services.memory_service import MemoryService
from aicompanion.services.mood_service import MoodService
from aicompanion.storage.database import Database
from aicompanion.ui.main_window import MainWindow

CID = 'deepseek-whale'
TAG = '<leave reason="想静一静"/>'


class LeaveStreamTests(unittest.TestCase):
    def test_every_split_is_hidden_before_display(self):
        for split in range(len(TAG)+1):
            parser = ExpressionStream()
            self.assertEqual(parser.feed('再见。'+TAG[:split]), '再见。')
            self.assertEqual(parser.feed(TAG[split:]) + parser.finish(), '')
            self.assertEqual(parser.leave_reason, '想静一静')

    def test_incomplete_or_invalid_leave_returns_original_text(self):
        for raw in ('我说的是 <leave 这个词，后面还有很多话。还有一句。', '<leav', '<leave',
                    TAG[:-1], '<leave/>', '<leave reason="a>b"/>后面的话。', '<leave reason="歇会儿">',
                    '<leave reason="' + '长'*1000 + '"/>', '<leave reason="a\nb"/>'):
            for chunks in ((raw,), raw):
                with self.subTest(raw=raw[:30], chunks=len(chunks)):
                    parser = ExpressionStream()
                    self.assertEqual(''.join(parser.feed(c) for c in chunks) + parser.finish(), raw)
                    self.assertFalse(parser.leave_reason)

    def test_newline_and_overflow_release_text_and_resume_stream(self):
        prefix = '<leave reason="'
        for raw in (prefix+'未闭合\n', prefix+'未闭合\r\n', prefix.ljust(129, '长')):
            for chunks in ((raw,), raw):
                with self.subTest(boundary=repr(raw[-2:]), chunks=len(chunks)):
                    parser = ExpressionStream()
                    self.assertEqual(''.join(parser.feed(c) for c in chunks), raw)
                    self.assertFalse(parser.leave_reason)
                    tail = '后面的话。'+TAG+'再见。'
                    rest = (tail,) if len(chunks) == 1 else tail
                    self.assertEqual(''.join(parser.feed(c) for c in rest) + parser.finish(), '后面的话。再见。')
                    self.assertEqual(parser.leave_reason, '想静一静')

    def test_quoted_and_cancelled_leave_has_no_effect(self):
        for literal in ('`'+TAG+'`', '{"示例":\''+TAG+'\'}', '<leaves/>'):
            parser = ExpressionStream()
            self.assertEqual(parser.feed(literal)+parser.finish(), literal)
            self.assertFalse(parser.leave_reason)
        parser = ExpressionStream()
        parser.feed(TAG)
        parser.cancel()
        self.assertFalse(parser.leave_reason)


class SelfExitTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.db = Database(self.root/'pet.sqlite3')
        self.db.initialize()
        self.life = LifeService(self.db)
        self.scheduler = ProactiveScheduler(self.db, life=self.life)

    def test_only_local_private_complete_reply_carries_intent(self):
        class Model(MockLLMClient):
            def generate_reply_stream(self, messages, prompt, cancellation):
                self.prompt = prompt
                yield from '我先走啦。'+TAG
        model = Model()
        ctl = ChatController(self.db, MemoryService(self.db), model)
        self.db.set_setting('relationship_learning', False)
        cases = [({}, True), ({'source':'voice'}, True), ({'source':'qq'}, False), ({'source':'unknown'}, False),
                 ({'source':'qq', 'channel':'qq:group:42', 'scene':'qq'}, False), ({'scene':'public'}, False),
                 ({'proactive':'分享外部文章'}, False)]
        for i, (args, local) in enumerate(cases):
            with self.subTest(args=args):
                events = []
                tokens = ctl.generate(str(i), ctl.character, CancellationToken(), query='你好',
                                      on_expression_event=events.append, **args)
                self.assertEqual(''.join(tokens), '我先走啦。')
                self.assertEqual(events[-1].leave_reason, '想静一静' if local else '')
                self.assertEqual('<leave' in model.prompt, local)
                self.assertNotIn('想静一静', str(events[-1].result.wire()))

    def test_unclosed_leave_alone_completes_chat(self):
        raw = '<leave 只是回复里的一个词。'
        class Model(MockLLMClient):
            def generate_reply_stream(self, messages, prompt, cancellation):
                yield from raw
        ctl = ChatController(self.db, MemoryService(self.db), Model())
        events = []
        stream = ctl.generate('literal', ctl.character, CancellationToken(), on_expression_event=events.append)
        self.assertEqual(''.join(stream), raw)
        self.assertEqual(events[-1].result.status, 'complete')
        self.assertFalse(events[-1].leave_reason)

    def window(self):
        coord = ConversationCoordinator()
        coord.active = 'g'
        w = SimpleNamespace(closing=False, coordinator=coord, call_active=False, agent=SimpleNamespace(busy=False),
                            pet=SimpleNamespace(confirm=None), scheduled_offers={}, database=self.db,
                            reply_status={'g':'complete'}, reply_characters={'g':CID},
                            controller=SimpleNamespace(character=SimpleNamespace(id=CID)), speech=SimpleNamespace(busy=True,queue=[]),
                            departure=('g','想静一静',10), outreach_scheduler=self.scheduler, request_exit=Mock(), _status=Mock())
        w._can_self_exit = lambda gen: MainWindow._can_self_exit(w, gen)
        return w

    def test_failed_stream_keeps_visible_words_and_discards_leave_intent(self):
        class BrokenModel(MockLLMClient):
            def generate_reply_stream(self, messages, prompt, cancellation):
                yield '我先歇会儿。'+TAG
                raise RuntimeError('合成流中断')
        ctl = ChatController(self.db, MemoryService(self.db), BrokenModel())
        events = []
        stream = ctl.generate('broken', ctl.character, CancellationToken(), on_expression_event=events.append)
        self.assertEqual(next(stream), '我先歇会儿。')
        self.assertFalse(events)
        with self.assertRaisesRegex(RuntimeError, '合成流中断'):
            next(stream)
        self.assertEqual(events[-1].result.status, 'failed')
        self.assertFalse(events[-1].leave_reason)

    def test_each_gate_cancels_farewell(self):
        for obj, key, value in [('agent','busy',True), ('pet','confirm',object()), (None,'call_active',True),
                                (None,'reply_status',{'g':'generating'}), ('coordinator','pending',['等等']),
                                ('coordinator','active','新一轮'), (None,'scheduled_offers',{'other':1})]:
            w = self.window()
            self.assertTrue(w._can_self_exit('g'))
            setattr(getattr(w, obj) if obj else w, key, value)
            MainWindow._poll_self_exit(w)
            self.assertIsNone(w.departure)
            w.request_exit.assert_not_called()
        self.db.set_setting('self_exit_enabled', False)
        w = self.window()
        self.assertFalse(w._can_self_exit('g'))
        self.assertFalse(remember_departure(self.db, CID, '晚安', self.scheduler))

    def test_waits_for_speech_but_caps_wait_and_cancels_if_save_fails(self):
        for finished, elapsed, allowed in [(True, .2, False), (True, 2, True), (False, 4, False), (False, 6, True)]:
            w = self.window()
            w.speech.busy = not finished
            with patch('aicompanion.ui.main_window.monotonic', return_value=10+elapsed):
                MainWindow._poll_self_exit(w)
            self.assertEqual(w.request_exit.called, allowed)
        w = self.window()
        with patch.object(self.scheduler, 'release_on_exit', side_effect=RuntimeError):
            MainWindow._poll_self_exit(w)
        w.request_exit.assert_not_called()
        w._status.assert_called_once()

    def test_return_remembers_reason_and_mood_once(self):
        MoodService(self.db, clock=lambda:1000).event(CID, 'insulted')
        for now, expected in [(1100, '还有一点气'), (1000+86400, '缓过来')]:
            remember_departure(self.db, CID, '想静一静', self.scheduler, clock=lambda:1000)
            db = Database(self.db.database_path)
            db.initialize()
            record = db.get_setting('self_exit:'+CID)
            self.assertEqual((record['reason'], record['mood'], record['at']), ('想静一静','angry',1000))
            self.assertEqual(take_return_greeting(db, 'other'), '')
            with ThreadPoolExecutor(2) as pool:
                lines = list(pool.map(lambda _:take_return_greeting(db, CID, clock=lambda:now), range(2)))
            self.assertEqual(lines.count(''), 1)
            self.assertIn(expected, ''.join(lines))
            self.assertIn('想静一静', ''.join(lines))
            self.assertEqual(take_return_greeting(db, CID), '')

    def test_paused_work_requires_claim_and_cannot_replay_a_write(self):
        run = self.db.add_agent_run(CID, '写两个文件')
        box = ToolBox(trash=self.root/'trash')
        box.reserve_effect = self.db.reserve_agent_effect
        box.task = Task(run_id=run, allowed=[self.root])
        args = dict(path=str(self.root/'one.txt'), content='第一步')
        self.assertFalse(box.call('write_file', args)[1])
        # 模拟“文件已写、步骤结果尚未写入”时关窗和迟到的取消回调。
        self.db.pause_agent_work(run, [(CID, '尚未开始的提议')])
        self.db.finish_agent_run(run, 'cancelled')
        self.db.initialize()
        self.assertTrue(all(r['status']=='paused' for r in self.db.agent_runs()))
        self.assertIsNone(self.db.resume_agent_run(run, 'other'))
        self.assertEqual(self.db.resume_agent_run(run, CID), 0)
        self.assertIsNone(self.db.resume_agent_run(run, CID))
        box.task = Task(run_id=run, allowed=[self.root], resuming=True)
        box.approve = Mock(return_value='once')
        (self.root/'one.txt').write_text('用户刚改过', encoding='utf-8')
        self.assertTrue(box.call('write_file', args)[1])
        self.assertEqual((self.root/'one.txt').read_text(encoding='utf-8'), '用户刚改过')
        fresh = dict(path=str(self.root/'two.txt'), content='第二步')
        box.approve.return_value = 'no'
        self.assertTrue(box.call('write_file', fresh)[1])
        self.assertFalse((self.root/'two.txt').exists())
        box.approve.return_value = 'once'
        self.assertFalse(box.call('write_file', fresh)[1])

    def test_resumed_writes_explain_all_applicable_risks(self):
        allowed = self.root/'allowed'
        box = ToolBox()
        box.task = Task(allowed=[allowed], resuming=True)
        for base in (allowed, self.root/'outside'):
            for folder in (base, base/'.git'):
                with self.subTest(folder=folder):
                    path = folder/'新目录'
                    box.approve = Mock(return_value='no')
                    self.assertTrue(box.call('make_dir', {'path':str(path)})[1])
                    self.assertFalse(path.exists())
                    box.approve.assert_called_once()
                    kind, title, detail = box.approve.call_args.args
                    self.assertEqual(kind, 'resume')
                    self.assertIn(str(path), title)
                    self.assertIn('还没做的部分', detail)
                    self.assertEqual('config、hooks' in detail, folder.name == '.git')
                    self.assertEqual('超出' in title, base != allowed)
                    if base != allowed:
                        self.assertIn(str(allowed), detail)
                    self.assertNotIn('本次都可以', detail)

    def test_exit_releases_unsent_and_preserves_uncertain_delivery(self):
        store = OutboxStore(self.db)
        with self.db.transaction() as c:
            aid = c.execute("INSERT INTO activities(character_id,kind,interest,query,status,started_at) "
                            "VALUES (?,'read','合成','合成','doing',1)", (CID,)).lastrowid
        for turn, claimed in [('未领',False), ('已领',True)]:
            rid = self.scheduler.reserve(CID, Outreach('greeting', turn, '你好'))
            self.db.claim_proactive(CID, turn)
            store.begin(turn, CID, 'scheduled')
            self.scheduler.attach(rid, turn, 'qq')
            store.add(turn, CID)
            store.dequeued(turn)
            if claimed:
                self.assertTrue(store.claim(turn))
                self.scheduler.handed(rid, turn)
        self.scheduler.reserve(CID, Outreach('share', f'share:{aid}', '桌面分享'))
        old_epoch = self.life.epoch()
        remember_departure(self.db, CID, '晚安', self.scheduler)
        self.assertNotEqual(self.life.epoch(), old_epoch)
        self.life._finish(aid, 'failed')
        self.assertEqual(self.life.recent(CID)[0]['status'], 'interrupted')
        self.db.initialize()
        self.assertFalse(store.claim('未领'))
        self.assertEqual([store.get(t)['status'] for t in ('未领','已领')], ['failed','unknown'])
        self.assertEqual(self.scheduler.today_count(CID), 1)
        self.assertEqual(self.scheduler.last(CID)['status'], 'unknown')
        self.assertFalse(self.scheduler.pending(CID))
        self.assertTrue(store.settle('已领', 'sent', 200))
        self.scheduler.reconcile(CID)
        self.assertEqual(self.scheduler.last(CID)['status'], 'sent')

    def test_partially_visible_desktop_outreach_is_not_retried_at_boot(self):
        rid = self.scheduler.reserve(CID, Outreach('greeting', '早安', '早'))
        self.scheduler.attach(rid, 'partial')
        self.db.locked(lambda c: self.scheduler.release_on_exit(c, {'partial'}))
        self.db.initialize()
        self.assertFalse(self.scheduler.pending(CID))
        self.assertEqual(self.scheduler.last(CID)['status'], 'unknown')
