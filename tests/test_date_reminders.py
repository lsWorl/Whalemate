"""合成日期和临时库验证提醒时机、调度结算与渠道隔离。"""
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from aicompanion.brain.scheduler import Outreach, ProactiveScheduler
from aicompanion.bridge.outbox import OutboxStore
from aicompanion.services.companion_dates import CompanionDates
from aicompanion.storage.database import Database
from test_relationship import BridgeFixture, CID
from test_qq_proactive import Base as QQFixture


class ReminderTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Database(Path(temp.name) / 'synthetic.sqlite3')
        self.db.initialize()
        self.now = datetime(2026, 12, 31, 11)
        self.clock = lambda: self.now.timestamp()
        self.dates = CompanionDates(self.db, self.clock)
        self.dates.set_options(CID, holidays=False)
        self.scheduler = ProactiveScheduler(self.db, clock=self.clock)

    def test_modes_and_cross_year(self):
        for mode in ('day', 'eve', 'both', 'off'):
            self.dates.add(CID, 'custom', mode, date(2027, 1, 1), yearly=False, reminder=mode)
        today = self.now.date()
        self.assertEqual({d.title for d in self.dates.reminders(CID, today, eve=True)}, {'eve', 'both'})
        self.now += timedelta(days=1)
        prompt, keys = self.dates.prompt(CID)
        self.assertEqual(len(keys), 2)
        self.assertIn('day、both', prompt)
        self.assertEqual({d.title for d in self.dates.events_on(CID)}, {'day', 'eve', 'both', 'off'})

    def test_lunar_eve_excludes_computed_events(self):
        self.dates.set_options(CID, holidays=True)
        self.dates.add(CID, 'birthday', '', date(2000, 2, 5), lunar=True)
        for eve in (date(2026, 2, 16), date(2027, 2, 5)):
            self.assertEqual([d.title for d in self.dates.reminders(CID, eve, eve=True)], ['你的生日'])
            self.assertIn('春节', [d.title for d in self.dates.reminders(CID, eve + timedelta(days=1))])

    def test_delivery_retry_and_restart(self):
        self.dates.add(CID, 'custom', '跨年聚会', date(2027, 1, 1), reminder='both')
        def reserve(turn):
            outreach = self.scheduler.next(CID, 0, 99999)
            self.assertEqual(outreach.source, 'date_eve')
            rid = self.scheduler.reserve(CID, outreach)
            self.assertTrue(self.scheduler.attach(rid, turn, 'qq'))
            self.assertIn('明天是跨年聚会', self.dates.eve_prompt(CID, turn))
            return rid, outreach
        rid, outreach = reserve('failed')
        self.dates.release(CID, 'failed')
        self.scheduler.settle_delivery(CID, rid, outreach, False)
        rid, outreach = reserve('sent')
        keys, day = self.dates.held(CID, 'sent')
        self.assertEqual((len(keys), day), (1, '2026-12-31'))
        box = OutboxStore(self.db, self.clock)
        box.begin('sent', CID)
        self.assertTrue(box.add('sent', CID, keys, day))
        box.dequeued('sent'); self.assertTrue(box.claim('sent'))
        self.dates.release(CID, 'sent')
        db = Database(self.db.database_path)
        db.initialize()
        OutboxStore(db, self.clock).settle('sent', 'sent', self.clock())
        dates = CompanionDates(db, self.clock)
        self.assertTrue(dates.mentioned(CID, keys[0]))
        scheduler = ProactiveScheduler(db, clock=self.clock)
        self.now += timedelta(hours=5)
        self.assertIsNone(scheduler.next(CID, 0, 99999))
        self.now += timedelta(days=1)
        self.assertEqual(scheduler.next(CID, 0, 99999).source, 'date')
        self.assertIn('今天是特别的日子：跨年聚会', dates.prompt(CID)[0])

    def test_scheduler_gates_and_eve_without_owner_turn(self):
        self.dates.add(CID, 'custom', '跨年聚会', date(2027, 1, 1), reminder='eve')
        self.now = self.now.replace(hour=23)
        self.assertIsNone(self.scheduler.next(CID, 0, 99999))
        self.now = self.now.replace(hour=11)
        self.db.set_setting('proactive_daily_limit', 0)
        self.assertIsNone(self.scheduler.next(CID, 0, 99999))
        self.db.set_setting('proactive_daily_limit', 5)
        self.scheduler.record(CID, Outreach('greeting', 'hello', '你好'))
        self.assertIsNone(self.scheduler.next(CID, 0, 99999))
        self.now += timedelta(hours=2)
        self.assertNotEqual(getattr(self.scheduler.next(CID, 0, 99999), 'source', None), 'date_eve')
        self.assertEqual(self.dates.eve_prompt(CID, 'unbound'), '')
        self.assertNotIn('跨年聚会', self.dates.prompt(CID)[0])

    def test_pending_eve_is_invalid_after_midnight_or_switch_off(self):
        row = self.dates.add(CID, 'birthday', '', date(2000, 1, 1))
        outreach = self.scheduler.next(CID, 0, 99999)
        self.assertTrue(self.scheduler.still_valid(outreach))
        self.dates.set_reminder(CID, row, 'off')
        self.assertFalse(self.scheduler.still_valid(outreach))
        self.dates.set_reminder(CID, row, 'both')
        self.now += timedelta(days=1)
        self.assertFalse(self.scheduler.still_valid(outreach))

    def test_v14_migration_preserves_dates_mentions_and_edits(self):
        when = self.now.date()
        for kind in ('birthday', 'her_birthday', 'custom'):
            self.dates.add(CID, kind, kind, when)
        rows = self.dates.user_dates(CID)
        self.dates.mark(CID, ['already-sent'])
        with self.db.transaction() as c:
            c.execute('ALTER TABLE companion_dates DROP COLUMN reminder')
            c.execute('PRAGMA user_version=14')
        self.db.initialize()
        self.assertEqual(self.dates.user_dates(CID), rows)
        self.assertEqual([r['reminder'] for r in rows], ['both', 'both', 'day'])
        self.assertTrue(self.dates.mentioned(CID, 'already-sent'))
        self.dates.set_reminder(CID, rows[0]['id'], 'off')
        self.db.initialize()
        self.assertEqual(self.dates.user_dates(CID)[0]['reminder'], 'off')


class PrivateReminderTests(BridgeFixture):
    def test_group_and_unbound_private_chat_never_receive_tomorrows_title(self):
        dates = CompanionDates(self.db)
        dates.add(CID, 'custom', '合成的私人纪念日', date.today() + timedelta(days=1), reminder='eve')
        for group in ('30003', ''):
            self.model.replies.append('在')
            self.service.handle(dict(platform='qq', sender_id='20002', sender_name='群友', group_id=group,
                                     text='在吗', called=True, expression_version=1, bot_account_id='90000'))
            self.assertNotIn('合成的私人纪念日', self.model.prompts[-1])


class OwnerQQTests(QQFixture):
    def test_eve_title_is_claimed_for_owner_and_confirmed_only_on_ack(self):
        today = date.today()
        now = datetime.combine(today, datetime.min.time()).replace(hour=11)
        scheduler = ProactiveScheduler(self.db, clock=now.timestamp)
        dates = CompanionDates(self.db)
        dates.set_options(CID, holidays=False, milestones=False)
        dates.add(CID, 'custom', '合成旅行日', today + timedelta(days=1), reminder='eve')
        outreach = scheduler.next(CID, 0, 99999)
        self.assertEqual(outreach.source, 'date_eve')
        rid = scheduler.reserve(CID, outreach)
        self.model.reply = '记得明天出发呀'
        turn = self.service.proactive_qq(outreach.intent, on_queued=lambda t: scheduler.attach(rid, t, 'qq'),
                                         still_valid=lambda: scheduler.still_valid(outreach))
        self.assertIn('明天是合成旅行日', self.model.prompts[-1])
        key, = outreach.payload['date_keys']
        self.assertFalse(dates.mentioned(CID, key))
        item, = self.service.take_outbox()
        self.assertEqual((item['user_id'], item['id']), ('10001', turn))
        self.assertTrue(self.claim(turn)['allowed'])
        self.assertTrue(self.service.outbox_ack({'id': turn, 'status': 'sent', 'sent': 1, 'first_delay': 0})['ok'])
        self.assertTrue(dates.mentioned(CID, key))
