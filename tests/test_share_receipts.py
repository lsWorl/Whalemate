"""分段投递、迟到的 QQ 回执和同一秒内换话题时，活动卡只记录实际送达文章的回应。"""
from datetime import datetime, timezone
from unittest.mock import patch

from aicompanion.brain.life import LifeService
from aicompanion.brain.scheduler import ProactiveScheduler
from aicompanion.bridge.outbox import ACK_TTL
from aicompanion.community.search import Hit
from aicompanion.storage.database import Database
from test_life import CID, FakeClient, FakeSearch, HIT
import test_life_review as fixtures


class ShareReceiptTests(fixtures.ScheduledShareTests):
    def setUp(self):
        super().setUp()
        self.s = self.scheduler()
        self.start = self.now[0]
        self.turn = 'a' * 32

    def prepare_qq(self):
        item, rid, self.store = fixtures.MergedOutboxTests.qq_share(self, self.s, self.turn)
        self.activity = item.payload['activity']['id']
        return rid

    def restart(self):
        self.db = Database(self.db.database_path)
        self.db.initialize()
        self.life = LifeService(self.db, clock=lambda: self.now[0])
        self.s = ProactiveScheduler(self.db, clock=lambda: self.now[0], rng=lambda: 0, life=self.life)

    def marks(self):
        with self.db.transaction() as c:
            return c.execute("SELECT COUNT(*) FROM mood_events WHERE kind='praised' AND character_id=?", (CID,)).fetchone()[0]

    def timeout(self):
        self.now[0] += ACK_TTL + 1
        self.s.reconcile(CID)
        self.assertEqual(self.row(self.activity)['status'], 'shared')

    def reply(self):
        self.now[0] += 60
        self.say('user', '在呢，有什么事？')
        self.s.settle(CID)

    def test_late_partial_restores_article_after_restart(self):
        rid = self.prepare_qq()
        self.timeout()
        self.store.settle(self.turn, 'partial', self.start)
        self.restart()
        self.s.reconcile(CID)
        self.assertEqual(self.status(rid)['status'], 'sent')  # 寒暄确实送到了
        self.assertEqual(self.row(self.activity)['status'], 'done')
        self.assertEqual(self.life.shareable(CID)['id'], self.activity)

    def test_reply_to_opener_only_does_not_reward_unseen_article(self):
        rid = self.prepare_qq()
        self.store.settle(self.turn, 'partial', self.start)
        self.s.reconcile(CID)
        self.reply()
        self.assertEqual(self.status(rid)['status'], 'answered')
        self.assertEqual((self.row(self.activity)['response'], self.marks()), ('', 0))

    def test_unknown_then_failed_never_rewards_unseen_article(self):
        rid = self.prepare_qq()
        self.timeout()
        self.reply()
        self.store.settle(self.turn, 'failed')
        self.restart()
        self.s.reconcile(CID)
        self.assertIsNone(self.status(rid))
        self.assertEqual((self.row(self.activity)['response'], self.marks()), ('', 0))

    def test_unknown_then_confirmed_rewards_real_reply_once_after_restart(self):
        self.prepare_qq()
        self.timeout()
        self.reply()
        self.assertEqual((self.row(self.activity)['response'], self.marks()), ('', 0))
        # 首达时间与旧的占位时间完全相同，对账仍须识别 unknown → sent。
        self.store.settle(self.turn, 'sent', self.start)
        for _ in range(2):
            self.restart()
            self.s.reconcile(CID)
            self.s.settle(CID)
            self.assertEqual((self.row(self.activity)['response'], self.marks()), ('answered', 1))

    def same_second(self, reply_offset, precise):
        self.db.set_setting('proactive_gap_minutes', 10)
        self.s = self.scheduler(roll=0.99)
        first, _, _ = self.shared_once(self.s)
        self.now[0] += 91 * 60
        self.assertIsNone(self.s.next(CID, 30, 9999))
        hit = Hit('第二篇', 'https://example.org/two', HIT.summary, '科普网')
        second = self.life.run(CID, FakeSearch([hit]), FakeClient('{"thought":"第二篇不错","worth":0.98}'))
        self.now[0] += 11 * 60 + 0.4
        second_start = self.now[0]
        b = self.s.next(CID, 30, 9999)
        self.assertEqual(b.key, f'share:{second}')
        self.s.record(CID, b)
        self.now[0] = int(second_start) + reply_offset
        if precise:
            with patch('aicompanion.storage.database.datetime', wraps=datetime) as wall:
                wall.now.return_value = datetime.fromtimestamp(self.now[0], timezone.utc)
                self.db.add_message('user', '就是这篇', CID, source='qq')
        else:
            self.say('user', '旧库只记得这一秒')
        self.now[0] += 4 * 60
        self.s.settle(CID)
        return self.row(first)['response'], self.row(second)['response'], self.marks()

    def test_legacy_second_precision_assigns_boundary_to_latest_topic_only(self):
        self.assertEqual(self.same_second(0.7, False), ('', 'answered', 1))

    def test_reply_after_second_share_in_same_second(self):
        self.assertEqual(self.same_second(0.7, True), ('', 'answered', 1))

    def test_reply_before_second_share_in_same_second(self):
        self.assertEqual(self.same_second(0.2, True), ('answered', '', 1))
