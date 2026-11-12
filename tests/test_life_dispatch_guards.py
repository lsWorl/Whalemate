"""用合成兴趣、真实搜索计数、SQLite 写锁和桥接重启复核活动的外发边界。"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import io
import json
import sqlite3
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aicompanion.brain.life import LifeService
from aicompanion.brain.scheduler import Outreach, ProactiveScheduler
from aicompanion.bridge.outbox import OutboxStore
from aicompanion.bridge.server import BridgeServer, runtime
from aicompanion.bridge.service import BridgeService
from aicompanion.community.search import WebSearch
from aicompanion.ui.main_window import MainWindow
from test_life import FakeClient, HIT
from test_qq_proactive import Base


class DispatchGuardTests(Base):
    def setUp(self):
        super().setUp()
        self.life = LifeService(self.db)
        self.scheduler = ProactiveScheduler(self.db, life=self.life)
        self.cid = self.character.id
        self.calls = []
        def transport(request, timeout=None):
            self.calls.append(request)
            return io.BytesIO(json.dumps({'results': [dict(title=HIT.title, url=HIT.url, summary=HIT.summary)]}).encode())
        self.search = WebSearch(self.db, opener=transport,
                                target=SimpleNamespace(backend='deepseek', base_url='https://synthetic.invalid', api_key='synthetic'))
        self.summary = FakeClient('{"thought":"有意思","worth":0.9}')

    def paused_interest(self, reopen):
        entered, resume = Event(), Event()
        def pick(cid):
            entered.set()
            if not resume.wait(10): raise TimeoutError('等待兴趣选择超时')
            return '鲸鱼的方言'
        with patch.object(self.life, 'pick_interest', pick), ThreadPoolExecutor(1) as pool:
            future = pool.submit(self.life.run, self.cid, self.search, self.summary)
            try:
                self.assertTrue(entered.wait(5))
                self.life.set_enabled(False)
                if reopen: self.life.set_enabled(True)
            finally:
                resume.set()
            future.result(timeout=10)
        self.assertEqual((self.calls, self.search.used_today()['search'], self.summary.prompts), ([], 0, []))
        self.assertIsNone(self.life.shareable(self.cid))

    def test_disabled_during_interest_selection_does_not_spend_search(self):
        self.paused_interest(False)

    def test_disable_enable_during_selection_cannot_revive_worker(self):
        self.paused_interest(True)
        self.life.run(self.cid, self.search, self.summary)
        self.assertEqual((len(self.calls), self.search.used_today()['search']), (1, 1))

    def test_disable_enable_before_queued_worker_starts(self):
        # 把 UI 已创建的任务留在队列里，直到设置关、开都保存完再执行。
        control = Mock(character=self.character)
        control.search_factory.return_value.return_value = self.search
        control.router.ready.return_value = False
        window = SimpleNamespace(controller=control, life=self.life, life_worker=None, closing=False,
                                 call_active=False, tasks=set(), _task_finished=Mock())
        with patch.object(self.life, 'should_start', return_value=True), patch('aicompanion.ui.main_window.TaskWorker') as worker:
            MainWindow._maybe_live(window)
            job = worker.call_args.args[0]
        self.life.set_enabled(False)
        self.life.set_enabled(True)
        self.assertIsNone(job(None))
        self.assertEqual((self.calls, self.search.used_today()['search']), ([], 0))

    def test_locked_source_binding_stops_before_handover(self):
        rid = self.scheduler.reserve(self.cid, Outreach('share', 'share:1', '分享'))
        errors = []
        def lock_before_attach(turn):
            with closing(sqlite3.connect(self.db.database_path)) as other:
                other.execute('BEGIN IMMEDIATE')
                try:
                    return self.scheduler.attach(rid, turn, 'qq')
                except sqlite3.OperationalError as exc:
                    errors.append(exc.sqlite_errorcode)
                    raise
        turn = self.service.proactive_qq('分享', on_queued=lock_before_attach, still_valid=lambda: True)
        self.assertEqual(errors, [sqlite3.SQLITE_BUSY])
        self.life.set_enabled(False)
        self.assertIsNone(turn)
        self.assertEqual(self.service.take_outbox(), [])
        self.assertEqual(self.model.prompts, [])

    def test_restart_refuses_dequeued_share_with_missing_source(self):
        rid = self.scheduler.reserve(self.cid, Outreach('share', 'share:1', '分享'))
        turn = self.service.proactive_qq('分享', on_queued=lambda t: self.scheduler.attach(rid, t, 'qq'), still_valid=lambda: True)
        handed = self.claim_requests()
        self.assertEqual(list(handed), [turn])
        with self.db.transaction() as c:
            c.execute('DELETE FROM outreach_log WHERE id=?', (rid,))
        self.life.set_enabled(False)
        self.service.close()
        self.service = BridgeService(self.db, self.controller)
        self.assertFalse(self.service.outbox_claim(handed[turn])['allowed'])
        self.assertEqual(OutboxStore(self.db).get(turn)['status'], 'failed')

    def test_restart_allows_direct_item_but_cannot_recreate_callback(self):
        direct = self.service.proactive_qq('普通问候')
        guarded = self.service.proactive_qq('临时检查', still_valid=lambda: True)
        handed = self.claim_requests()
        self.assertEqual(len(handed), 2)
        self.service.close()
        self.service = BridgeService(self.db, self.controller)
        self.assertTrue(self.service.outbox_claim(handed[direct])['allowed'])
        self.assertFalse(self.service.outbox_claim(handed[guarded])['allowed'])

    def claim_requests(self):
        result = BridgeServer.heartbeat(SimpleNamespace(service=self.service), runtime.last_heartbeat)
        return {item['id']: ({'id': item['id'], 'astrbot': 'plugin-0.10.2'} if item.get('require_plugin_version')
                             else {'id': item['id']}) for item in result['outbox']}

    def test_earlier_v14_outbox_without_guard_upgrades_conservatively(self):
        with self.db.transaction() as c:
            if 'guard' in {r[1] for r in c.execute('PRAGMA table_info(qq_outbox)')}:
                c.execute('ALTER TABLE qq_outbox DROP COLUMN guard')
            c.execute("INSERT INTO qq_outbox(turn_id,character_id,status,created_at) VALUES ('old',?,'unknown',0)", (self.cid,))
        self.db.initialize()
        self.assertEqual(OutboxStore(self.db).get('old')['guard'], 'scheduled')
