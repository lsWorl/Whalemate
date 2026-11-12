"""QQ 回执迟到、旧版升级和 SQLite 写锁的调度回归。数据库、时钟和主人消息均为测试夹具。"""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from aicompanion.brain.purpose import PurposeEngine
from aicompanion.brain.scheduler import Outreach, ProactiveScheduler
from aicompanion.bridge.outbox import OutboxStore
from aicompanion.storage.database import Database
from test_scheduler import CID, FakeGreetings


class OutreachRecoveryTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'synthetic.sqlite3'
        self.start = datetime(2026, 11, 12, 10).timestamp()
        self.now = self.start
        self.restart()

    def restart(self):
        self.db = Database(self.path)
        self.db.initialize()
        self.engine = PurposeEngine(self.db, clock=lambda: datetime.fromtimestamp(self.now))
        self.scheduler = ProactiveScheduler(self.db, self.engine, FakeGreetings(), clock=lambda: self.now, rng=lambda: 0)
        self.store = OutboxStore(self.db, clock=lambda: self.now)

    def say(self, offset):
        stamp = datetime.fromtimestamp(self.start + offset, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        with self.db.transaction() as c:
            c.execute("INSERT INTO chat_messages(role,content,character_id,channel,source,created_at) "
                      "VALUES ('user','我在呢',?,'desktop','qq',?)", (CID, stamp))

    def claimed(self, item):
        rid = self.scheduler.reserve(CID, item)
        turn = f'{rid:032x}'
        self.store.add(turn, CID)
        self.scheduler.attach(rid, turn, 'qq')
        self.store.dequeued(turn)
        self.assertTrue(self.store.claim(turn))
        self.scheduler.handed(rid, turn)
        return rid, turn

    def purpose(self):
        due = datetime.fromtimestamp(self.start - 60).isoformat(timespec='seconds')
        pid = self.db.add_purpose(CID, 'ask', '周末读哪本书', '', [], due)
        self.db.update_purpose(pid, 'delivering')
        return pid, Outreach('purpose', f'purpose:{pid}', '周末读哪本书')

    def late_first_arrival(self, reply_second, status, next_source):
        _, turn = self.claimed(Outreach('greeting', 'hello', '在吗'))
        self.say(reply_second)
        self.now = self.start + 12 * 60
        self.scheduler.next(CID, 0, 9999)
        self.assertEqual(self.scheduler.last(CID)['status'], 'answered')
        self.store.settle(turn, 'sent', self.start + 5)
        self.restart()
        self.scheduler.reconcile(CID)
        row = self.scheduler.last(CID)
        self.assertEqual((row['status'], row['sent_at']), (status, self.start + 5))
        self.now = self.start + 92 * 60
        self.assertEqual(self.scheduler.next(CID, 0, 9999).source, next_source)

    def test_late_receipt_excludes_message_before_visibility(self):
        self.late_first_arrival(2, 'sent', 'follow')

    def test_late_receipt_keeps_message_after_visibility(self):
        self.late_first_arrival(8, 'answered', 'greeting')

    def test_repairs_answered_row_already_timestamp_corrected_by_old_version(self):
        rid, turn = self.claimed(Outreach('greeting', 'hello', '在吗'))
        self.say(2)
        self.store.settle(turn, 'sent', self.start + 5)
        with self.db.transaction() as c:
            c.execute("UPDATE outreach_log SET status='answered', sent_at=? WHERE id=?", (self.start + 5, rid))
        self.restart()
        self.scheduler.reconcile(CID)
        self.assertEqual(self.scheduler.last(CID)['status'], 'sent')

    def test_pre_receipt_answer_check_cannot_overwrite_corrected_visibility(self):
        _, turn = self.claimed(Outreach('greeting', 'hello', '在吗'))
        self.say(2)
        self.now += 12 * 60
        self.scheduler.reconcile(CID)
        spoke = self.scheduler._user_spoke_since

        def late_receipt(*args, **kwargs):
            reply = spoke(*args, **kwargs)
            self.store.settle(turn, 'sent', self.start + 5)
            self.scheduler.reconcile(CID)
            return reply

        with patch.object(self.scheduler, '_user_spoke_since', late_receipt):
            self.scheduler.settle(CID)
        self.assertEqual(self.scheduler.last(CID)['status'], 'sent')

    def failed_follow(self, replied):
        parent = self.scheduler.record(CID, Outreach('greeting', 'initial', '在吗'))
        self.now += 91 * 60
        follow = self.scheduler.next(CID, 0, 9999)
        self.assertEqual(follow.payload['parent_id'], parent)
        _, turn = self.claimed(follow)
        self.now += 11 * 60
        self.scheduler.reconcile(CID)
        self.assertEqual(self.scheduler.last(CID)['status'], 'unknown')
        if replied:
            self.say(100 * 60)
            self.scheduler.settle(CID)
        self.store.settle(turn, 'failed')
        self.restart()
        self.now = self.start + 104 * 60
        choice = self.scheduler.next(CID, 0, 9999)
        self.assertEqual(self.scheduler.today_count(CID), 1)
        self.assertEqual(self.scheduler.last(CID)['status'], 'answered' if replied else 'sent')
        self.assertEqual(choice.source, 'greeting' if replied else 'follow')

    def test_late_failed_follow_reopens_original_topic(self):
        self.failed_follow(False)

    def test_late_failed_follow_preserves_actual_answer_to_parent(self):
        self.failed_follow(True)

    def test_early_v13_handed_purpose_is_qq_and_never_selected_again(self):
        pid, item = self.purpose()
        with self.db.transaction() as c:
            # e38ed29 的 handed 只改 status/handed_at/turn_id，channel 留在 desktop，当时没有发件箱。
            c.execute("INSERT INTO outreach_log(character_id,source,key,channel,status,sent_at,handed_at,turn_id) "
                      "VALUES (?,'purpose',?,'desktop','delivering',?,?,?)",
                      (CID, item.key, self.start, self.start + 3, 'e' * 32))
            c.execute('DROP TABLE qq_outbox')
        self.restart()
        self.assertEqual(self.db.purposes(CID)[0]['status'], 'pending')
        self.now += 31 * 60
        self.assertFalse(self.scheduler.pending(CID))
        row = self.scheduler.last(CID)
        self.assertEqual((row['status'], row['channel'], row['sent_at']), ('unknown', 'qq', self.start + 3))
        self.say(40 * 60)
        self.now = self.start + 91 * 60
        self.assertNotEqual(self.scheduler.next(CID, 0, 9999).key, f'purpose:{pid}')
        self.assertEqual(self.db.purposes(CID)[0]['status'], 'delivered')

    def test_busy_purpose_write_leaves_receipt_replayable(self):
        _, item = self.purpose()
        rid, turn = self.claimed(item)
        vote = self.store.settle(turn, 'sent', self.start + 5)
        connect = self.db.connect
        delivered = self.engine.delivered

        def no_wait():
            connection = connect()
            connection.execute('PRAGMA busy_timeout=0')
            return connection

        def contend(*args, **kwargs):
            # 在心事落库点引入另一个真正的 SQLite 写者；哪个写者先拿锁，有一个一定返回 SQLITE_BUSY。
            with closing(no_wait()) as blocker:
                try:
                    blocker.execute('BEGIN IMMEDIATE')
                    delivered(*args, **kwargs)
                except sqlite3.OperationalError as error:
                    self.assertEqual(error.sqlite_errorcode, sqlite3.SQLITE_BUSY)
                    raise
                finally:
                    blocker.rollback()

        with patch.object(self.db, 'connect', no_wait), patch.object(self.engine, 'delivered', contend):
            try:
                self.scheduler.settle_delivery(CID, rid, item, vote)
            except sqlite3.OperationalError:
                pass   # QQ 回调失败时，桥接保留 qq_outbox 的终局，下一轮对账。
        with self.db.transaction() as c:
            self.assertEqual(c.execute('SELECT status FROM outreach_log WHERE id=?', (rid,)).fetchone()[0], 'delivering')
        self.restart()
        self.scheduler.reconcile(CID)
        self.assertEqual(self.scheduler.last(CID)['status'], 'sent')
        self.assertEqual(self.db.purposes(CID)[0]['status'], 'delivered')
        self.say(40 * 60)
        self.now += 91 * 60
        self.assertNotEqual(self.scheduler.next(CID, 0, 9999).key, item.key)
