"""生成线程跨过半小时回收时限后恢复，只允许重新选择的那一轮送到 QQ。"""
from datetime import datetime
from types import SimpleNamespace

from aicompanion.brain.purpose import PurposeEngine
from aicompanion.brain.scheduler import ProactiveScheduler
from aicompanion.bridge.outbox import OutboxStore
from aicompanion.ui.main_window import MainWindow
from test_qq_proactive import Base


class ExpiredGenerationTests(Base):
    def test_old_worker_and_callback_cannot_undo_retry(self):
        start = datetime(2026, 11, 12, 14).timestamp()
        now = [start]
        self.service.clock = lambda: now[0]
        self.db.set_setting('quiet_start', 3)
        self.db.set_setting('quiet_end', 4)
        cid = self.character.id
        due = datetime.fromtimestamp(start - 60).isoformat(timespec='seconds')
        self.db.add_purpose(cid, 'chat', '周末读哪本书', '读书计划', [], due)
        purposes = PurposeEngine(self.db, clock=lambda: datetime.fromtimestamp(now[0]))
        scheduler = ProactiveScheduler(self.db, purposes, clock=self.service.clock, rng=lambda: 0)
        workers = []

        def route(intent, done, taken, attached):
            def run():
                turn = self.service.proactive_qq(intent, on_done=done, on_taken=taken, on_queued=attached)
                if turn is None:
                    done(False)  # UI 工作线程结束信号
                return turn
            workers.append(run)
            return True

        window = SimpleNamespace(database=self.db, outreach_scheduler=scheduler, _proactive_on_qq=route)
        first = scheduler.next(cid, 0, 99999)
        self.assertEqual(first.source, 'purpose')
        MainWindow._deliver_outreach(window, cid, first)

        def generate(*args, **kwargs):
            if now[0] == start:
                now[0] += 31 * 60
                retry = scheduler.next(cid, 0, 99999)
                self.assertEqual(retry.key, first.key)
                MainWindow._deliver_outreach(window, cid, retry)
            yield '周末读哪本书呀？'

        self.model.generate_reply_stream = generate
        self.assertIsNone(workers.pop(0)())
        self.assertEqual(self.db.recent_messages(5, cid), [])
        with self.db.transaction() as c:
            self.assertEqual(c.execute('SELECT status FROM purposes').fetchone()[0], 'delivering')
        new_turn = workers.pop(0)()
        self.assertEqual([item['id'] for item in self.service.take_outbox()], [new_turn])
        self.assertTrue(self.claim(new_turn)['allowed'])
        self.assertTrue(self.service.outbox_ack({'id': new_turn, 'status': 'sent', 'sent': 1})['ok'])
        self.assertEqual(len(self.db.recent_messages(5, cid)), 1)
        self.assertEqual(scheduler.today_count(cid), 1)
        with self.db.transaction() as c:
            self.assertCountEqual([row[0] for row in c.execute('SELECT status FROM qq_outbox')], ['failed', 'sent'])

    def test_sweep_cannot_fail_a_turn_that_advanced_since_selection(self):
        store = OutboxStore(self.db)
        turn = 'a' * 32
        store.begin(turn, self.character.id)
        self.assertTrue(store.add(turn, self.character.id))
        self.assertIsNone(store.settle(turn, 'failed', expected='generating'))
        self.assertEqual(store.get(turn)['status'], 'queued')

    def test_restart_cleans_reply_written_after_generation_timed_out(self):
        store = OutboxStore(self.db)
        turn = 'b' * 32
        store.begin(turn, self.character.id)
        store.settle(turn, 'failed')
        self.db.add_message('assistant', '过期回复', self.character.id, turn)
        self.db.add_message('user', '保留主人的话', self.character.id, turn)
        self.db.initialize()
        self.assertEqual([row['role'] for row in self.db.recent_messages(5, self.character.id)], ['user'])
