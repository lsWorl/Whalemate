"""真实发件箱、调度器和插件，仅替换 QQ 适配器与传输时钟。"""
import asyncio
from datetime import date, datetime, timezone
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from aicompanion.brain.purpose import PurposeEngine
from aicompanion.brain.scheduler import Outreach, ProactiveScheduler
from aicompanion.bridge.outbox import OutboxStore
from aicompanion.bridge.server import BridgeServer, runtime
from aicompanion.controllers.chat_controller import ChatController
from aicompanion.services.companion_dates import CompanionDates
from aicompanion.services.memory_service import MemoryService
from aicompanion.ui.main_window import MainWindow
from test_astrbot_plugin import load_plugin
from test_expression_qq import Model as StickerModel
from test_qq_proactive import ActionFailed, Base


class ArrivalTests(Base):
    def setUp(self):
        super().setUp()
        self.now = datetime.combine(date.today(), datetime.min.time().replace(hour=10)).timestamp()
        self.module = load_plugin()
        runtime.last_heartbeat['astrbot'] = 'plugin-' + self.module.PLUGIN_VERSION
        self.model.reply = '晚霞好美呀'
        self.service.clock = lambda: self.now
        self.db.set_setting('quiet_start', 0)
        self.db.set_setting('quiet_end', 0)
        self.purposes = PurposeEngine(self.db, clock=lambda: datetime.fromtimestamp(self.now))
        self.scheduler = ProactiveScheduler(self.db, purposes=self.purposes, clock=self.service.clock, rng=lambda: 0)
        self.cid = self.character.id
        CompanionDates(self.db).set_options(self.cid, holidays=False, milestones=False)
        self.visible = []
        self.reports = []

    def queue(self, choice):
        def proactive(intent, done, taken, queued):
            return bool(self.service.proactive_qq(intent, on_done=done, on_taken=taken, on_queued=queued))

        window = SimpleNamespace(database=self.db, outreach_scheduler=self.scheduler, _proactive_on_qq=proactive)
        MainWindow._deliver_outreach(window, self.cid, choice)
        heartbeat = BridgeServer.heartbeat(SimpleNamespace(service=self.service), runtime.last_heartbeat)
        item, = heartbeat['outbox']
        return item

    def user_says(self, at):
        stamp = datetime.fromtimestamp(at, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        with self.db.transaction() as c:
            c.execute("INSERT INTO chat_messages(role, content, created_at, character_id, status, channel, source) "
                      "VALUES ('user','我的另一件事',?,?,'complete','desktop','qq')", (stamp, self.cid))

    def deliver(self, item, *, claim_request=0, claim_reply=0, ack_request=0, skew=0, result=True):
        elapsed_start = self.now
        async def post(path, payload):
            if path == '/v1/outbox/claim':
                self.now += claim_request
                response = self.service.outbox_claim(payload)
                self.now += claim_reply
                return response
            self.assertEqual(path, '/v1/outbox/ack')
            self.now += ack_request
            self.reports.append(payload)
            return self.service.outbox_ack(payload)

        async def send(origin, message):
            if isinstance(result, Exception):
                raise result
            if result:
                self.visible.append(self.now)
            return result

        async def expression_control(action, payload):
            return self.service.expression_claim(payload) if action == 'claim' else self.service.expression_ack(payload)

        events = ModuleType('astrbot.api.event')
        events.MessageChain = SimpleNamespace
        parts = ModuleType('astrbot.api.message_components')
        parts.Image = SimpleNamespace(fromBase64=lambda data: data)
        parts.Plain = str
        with patch.dict(sys.modules, {'astrbot.api.event': events, 'astrbot.api.message_components': parts}):
            plugin = self.module.AICompanionBridge(SimpleNamespace(send_message=send), {})
            plugin.post_json = post
            plugin.expression_control = expression_control
            self.module.time = SimpleNamespace(monotonic=lambda: self.now - elapsed_start)
            self.module.wall_clock = lambda: self.now + skew
            asyncio.run(plugin.deliver_outbox(item))
        self.assertEqual(len(self.reports), 1)

    def test_user_before_claim_reply_does_not_answer_outreach(self):
        start = self.now
        item = self.queue(Outreach('greeting', 'evening', '聊晚霞'))
        self.user_says(start + 1)
        self.deliver(item, claim_reply=3, ack_request=4)
        self.assertEqual(self.visible, [start + 3])
        self.assertEqual(self.scheduler.last(self.cid)['sent_at'], self.visible[0])
        self.now += 91 * 60
        self.assertEqual(self.scheduler.next(self.cid, 20, 99999).source, 'follow')

    def test_first_arrival_after_midnight_uses_new_day_limit_even_with_clock_skew(self):
        self.now = datetime(2026, 11, 13, 23, 59, 58).timestamp()
        self.db.set_setting('proactive_daily_limit', 1)
        item = self.queue(Outreach('greeting', 'evening', '聊晚霞'))
        self.deliver(item, claim_reply=3, skew=-3600)
        self.assertEqual(self.scheduler.last(self.cid)['sent_at'], self.visible[0])
        self.user_says(self.now + 1)
        self.now += 91 * 60
        self.assertEqual(self.scheduler.today_count(self.cid), 1)
        self.assertIsNone(self.scheduler.next(self.cid, 20, 99999))

    def test_delayed_claim_request_does_not_shift_first_arrival_by_round_trip(self):
        item = self.queue(Outreach('greeting', 'evening', '聊晚霞'))
        self.deliver(item, claim_request=4, claim_reply=2, ack_request=5)
        self.assertEqual(self.scheduler.last(self.cid)['sent_at'], self.visible[0])

    def test_ack_in_transit_over_midnight_does_not_consume_next_day(self):
        self.now = datetime(2026, 11, 13, 23, 59, 58).timestamp()
        item = self.queue(Outreach('greeting', 'evening', '聊晚霞'))
        self.deliver(item, ack_request=5, skew=3600)
        self.assertEqual(self.scheduler.last(self.cid)['sent_at'], self.visible[0])
        self.assertEqual(self.scheduler.today_count(self.cid), 0)

    def test_zero_confirmed_parts_cannot_settle_as_sent(self):
        item = self.queue(Outreach('greeting', 'evening', '聊晚霞'))
        claim = {'id': item['id']}
        if item.get('require_plugin_version'):
            claim['astrbot'] = 'plugin-' + self.module.PLUGIN_VERSION
        self.assertTrue(self.service.outbox_claim(claim)['allowed'])
        receipt = {'id': item['id'], 'status': 'sent', 'sent': 0}
        self.assertFalse(self.service.outbox_ack(receipt)['ok'])
        self.assertEqual(OutboxStore(self.db).get(item['id'])['status'], 'claimed')
        self.assertTrue(self.service.outbox_ack(dict(receipt, status='failed'))['ok'])
        self.assertEqual(self.scheduler.today_count(self.cid), 0)

    def sticker_only(self, source, result, expected):
        model = StickerModel()
        model.position = 'before_text'
        model.pure = True
        self.service.controller = ChatController(self.db, MemoryService(self.db), model)
        self.service.controller.character = self.character
        dates = CompanionDates(self.db)
        if source == 'date':
            dates.add(self.cid, 'birthday', '', date.today())
        else:
            due = datetime.fromtimestamp(self.now - 60).isoformat(timespec='seconds')
            self.db.add_purpose(self.cid, 'chat', '聊合成图', '合成图片', [], due)
        choice = self.scheduler.next(self.cid, 0, 99999)
        self.assertEqual(choice.source, source)
        item = self.queue(choice)
        self.assertEqual(item['segments'], [])
        attachment, = item['expression']['attachments']
        self.deliver(item, result=result)
        delivered = OutboxStore(self.db).get(item['id'])
        self.assertEqual((self.reports[0]['status'], delivered['status']), (expected, expected))
        self.assertEqual(self.reports[0]['sent'], int(expected == 'sent'))
        receipt = self.service.controller.expression.delivery(attachment['delivery_id'])
        self.assertEqual(receipt['status'], 'accepted' if expected == 'sent' else expected)
        self.assertEqual(self.scheduler.today_count(self.cid), int(expected != 'failed'))
        if source == 'purpose':
            with self.db.transaction() as c:
                self.assertEqual(c.execute('SELECT status FROM purposes').fetchone()[0],
                                 'pending' if expected == 'failed' else 'delivered')
        elif expected == 'failed':
            self.assertFalse(any(dates.mentioned(self.cid, day.key) for day in dates.events_on(self.cid)))

    def test_image_only_date_explicit_false_releases_daily_slot(self):
        self.sticker_only('date', False, 'failed')

    def test_image_only_purpose_refused_returns_to_pending(self):
        self.sticker_only('purpose', ActionFailed(), 'failed')

    def test_image_only_timeout_stays_unknown(self):
        self.sticker_only('date', TimeoutError(), 'unknown')

    def test_image_only_accepted_counts_once(self):
        self.sticker_only('date', True, 'sent')
