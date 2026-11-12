"""2026-11-09 实测后的修正：主动消息随机发到桌面或 QQ 私聊、群里少露 AI 痕迹、只发图时别分析、未允许的群里不让 AstrBot 出声。
全部合成：假的模型、假的插件心跳和发送，不连 QQ、不调用真实模型。"""
from dataclasses import replace
import asyncio, base64, hashlib, re, sys, tempfile, time, types, unittest
from pathlib import Path
from unittest import mock

from aicompanion.bridge.server import runtime
from aicompanion.bridge.service import BridgeService, GROUP_HUMAN
from aicompanion.controllers.chat_controller import ChatController
from aicompanion.domain.character import DEFAULT_CHARACTER
from aicompanion.services.memory_service import MemoryService
from aicompanion.services.proactive_service import choose_channel
from aicompanion.storage.database import Database
import test_astrbot_plugin as plugin_fixture


class ActionFailed(Exception):
    """和 OneBot 明确拒收时抛的异常同名：插件按名字认“确定没发出”。"""


class Model:
    model = 'mock'
    def __init__(self): self.reply = '爹 吃饭了没'; self.prompts = []
    def generate_reply_stream(self, history, prompt, cancellation=None):
        self.prompts.append(prompt); yield self.reply


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.db = Database(Path(self.temp.name)/'synthetic.sqlite3'); self.db.initialize()
        self.character = replace(DEFAULT_CHARACTER, id='deepseek-whale', name='鲸娘'); self.db.add_character(self.character)
        self.model = Model(); self.controller = ChatController(self.db, MemoryService(self.db), self.model); self.controller.character = self.character
        self.db.set_setting('bridge_owner:qq', '10001'); self.db.set_setting('bridge_groups', ['30003']); self.db.set_setting('bridge_memory_every', 0)
        self.service = BridgeService(self.db, self.controller)
        self.db.set_setting('relationship_learning', False)   # 不起后台关系分析线程（Windows 上会占着临时库）
        self.saved = runtime.last_heartbeat
        runtime.last_heartbeat = {'at': time.time(), 'astrbot': 'plugin-0.10.2', 'groups': [], 'onebot': {'connected': True, 'self_id': '90009', 'error': ''}}

    def claim(self, turn):
        return self.service.outbox_claim({'id': turn, 'astrbot': 'plugin-0.10.2'})

    def tearDown(self):
        runtime.last_heartbeat = self.saved; self.service.close(); self.temp.cleanup()


class ProactiveQQTests(Base):
    def test_ready_needs_bound_owner_connected_new_plugin(self):
        self.assertTrue(self.service.qq_ready())
        runtime.last_heartbeat['astrbot'] = 'plugin-0.8.0'; self.assertFalse(self.service.qq_ready())   # 旧插件不会发发件箱
        runtime.last_heartbeat['astrbot'] = 'plugin-0.9.6'; self.assertFalse(self.service.qq_ready())   # 不会先领取、回执不带实际发送时间
        runtime.last_heartbeat['astrbot'] = 'plugin-0.10.0'; self.assertFalse(self.service.qq_ready())  # 超时会报 failed、中途停用会报 sent
        runtime.last_heartbeat['astrbot'] = 'plugin-0.10.2'; runtime.last_heartbeat['onebot']['connected'] = False
        self.assertFalse(self.service.qq_ready())
        runtime.last_heartbeat['onebot']['connected'] = True; runtime.last_heartbeat['at'] = time.time()-120
        self.assertFalse(self.service.qq_ready())
        runtime.last_heartbeat['at'] = time.time(); self.db.set_setting('bridge_owner:qq', '')
        self.assertFalse(self.service.qq_ready())

    def test_generated_into_outbox_shared_history_and_acked(self):
        results = []
        self.service.chance = lambda: 0.99                      # 分句时短句一定合并（QQ 规则：空格不当停顿，拆不拆看聊天节奏）
        turn = self.service.proactive_qq('早晨问候，关心今天的心情', on_done=results.append)
        self.assertTrue(turn)
        self.assertIn('别提自己是 AI', self.model.prompts[-1])
        self.assertIn('QQ 私聊里主动找他', self.model.prompts[-1])
        history = self.db.recent_messages(1, self.character.id)
        self.assertEqual((history[-1]['role'], history[-1]['content']), ('assistant', '爹 吃饭了没'))   # 和桌面同一段记录
        items = self.service.take_outbox()
        self.assertEqual([(i['id'], i['user_id'], i['segments']) for i in items], [(turn, '10001', ['爹，吃饭了没'])])
        self.assertEqual(items[0]['segment_delays'], [0.0])
        self.assertEqual(self.service.take_outbox(), [])                # 只取一次
        self.assertTrue(self.claim(turn)['allowed'])
        self.assertFalse(self.claim(turn)['allowed'])   # 只能领取一次
        self.assertTrue(self.service.outbox_ack({'id': turn, 'status': 'sent', 'first_delay': 1.5, 'sent': 1})['ok'])
        self.assertEqual([r.status for r in results], ['sent'])
        self.assertTrue(self.service.outbox_ack({'id': turn, 'status': 'sent'})['ok'])   # 回执重发：认可但不重复处理
        self.assertEqual(len(results), 1)

    def test_failed_or_expired_delivery_is_not_counted_as_said(self):
        results = []
        turn = self.service.proactive_qq('闲聊', on_done=results.append); self.service.take_outbox(); self.claim(turn)
        self.service.outbox_ack({'id': turn, 'status': 'failed'})
        self.assertEqual([r.status for r in results], ['failed']); self.assertEqual(self.db.recent_messages(5, self.character.id), [])
        now = [1000.0]; self.service.clock = lambda: now[0]
        turn = self.service.proactive_qq('闲聊', on_done=results.append); now[0] += self.service.OUTBOX_TTL+1
        self.assertEqual(self.service.take_outbox(), []); self.assertEqual([r.status for r in results], ['failed', 'failed'])
        self.assertEqual(self.db.recent_messages(5, self.character.id), [])
        self.assertFalse(self.service.outbox_ack({'id': 'x'*32, 'status': 'whatever'})['ok'])

    def _hundredth_day(self):
        from datetime import date, timedelta
        from aicompanion.services.companion_dates import CompanionDates
        dates = CompanionDates(self.db); dates.set_met_on(self.character.id, date.today()-timedelta(days=99))   # 今天第 100 天
        return dates, 'milestone:100'

    def deliver(self, status, **extra):
        # 生成一条主动消息、插件取走并领取，再按 status 回执。返回（轮次, 收到的结果）
        results = []
        turn = self.service.proactive_qq('闲聊', on_done=results.append)
        self.service.take_outbox(); self.assertTrue(self.claim(turn)['allowed'])
        if status: self.service.outbox_ack(dict({'id': turn, 'status': status}, **extra))
        return turn, results

    def replies(self, turn):
        with self.db.transaction() as c:
            return c.execute("SELECT COUNT(*) FROM chat_messages WHERE turn_id=? AND role='assistant'", (turn,)).fetchone()[0]

    def test_special_day_on_qq_counts_only_when_fully_delivered(self):
        # 第二轮：失败不算提过、下一轮还会提；第三轮 P2：只发出一部分（祝福那段没发出去）也不算提过；全部发出才算
        dates, key = self._hundredth_day()
        self.deliver('failed')
        self.assertFalse(dates.mentioned(self.character.id, key))
        _, results = self.deliver('partial', first_delay=3, sent=1)
        self.assertIn('特别的日子', self.model.prompts[-1])
        self.assertEqual(results[0].status, 'partial'); self.assertTrue(results[0].reached)
        self.assertFalse(dates.mentioned(self.character.id, key))
        turn = self.service.proactive_qq('闲聊')
        self.assertIn('特别的日子', self.model.prompts[-1])
        self.assertEqual(dates.prompt(self.character.id, claim='other')[1], ())                 # 在路上：别的轮次不再提
        self.service.take_outbox(); self.claim(turn)
        self.service.outbox_ack({'id': turn, 'status': 'sent', 'first_delay': 2, 'sent': 2})
        self.assertTrue(dates.mentioned(self.character.id, key))

    def test_unread_heartbeat_response_is_not_delivered(self):
        # 第三轮 P2：服务端在心跳响应里交出去了，插件没读到（没来领取）：确定没发，不算提过、记录拿掉，结果是失败
        dates, key = self._hundredth_day()
        now = [time.time()]; self.service.clock = lambda: now[0]
        results = []
        turn = self.service.proactive_qq('闲聊', on_done=results.append)
        self.assertEqual(len(self.service.take_outbox()), 1)                                   # 响应写出去了，插件没收到
        now[0] += self.service.CLAIM_TTL+1; self.service.take_outbox()                          # 下一次正常心跳
        self.assertEqual([r.status for r in results], ['failed'])
        self.assertFalse(dates.mentioned(self.character.id, key))
        self.assertEqual(self.replies(turn), 0)
        self.assertFalse(self.claim(turn)['allowed'])                   # 插件迟到的领取被拒绝，不会再发

    def test_claimed_without_receipt_is_unknown_and_late_receipt_still_counts(self):
        # 领取了却一直没回执：结果未知（不算提过、不确认送达）；之后迟到的回执照样补记
        dates, key = self._hundredth_day()
        now = [time.time()]; self.service.clock = lambda: now[0]
        turn, results = self.deliver(None)
        now[0] += self.service.ACK_TTL+1; self.service.take_outbox()
        self.assertEqual([r.status for r in results], ['unknown']); self.assertFalse(results[0])
        self.assertFalse(dates.mentioned(self.character.id, key)); self.assertEqual(self.replies(turn), 1)
        self.assertTrue(self.service.outbox_ack({'id': turn, 'status': 'sent', 'first_delay': 700, 'sent': 1})['ok'])
        self.assertTrue(dates.mentioned(self.character.id, key))
        from aicompanion.bridge.outbox import OutboxStore
        row = OutboxStore(self.db).get(turn)
        self.assertEqual(row['status'], 'sent'); self.assertAlmostEqual(row['first_sent_at'], now[0]-700, delta=1)

    def test_receipt_uses_the_character_that_sent_it(self):
        # 第三轮 P2：回执到之前切了角色：仍按发件时的角色记生日提过、清理没发出去的回复
        from types import SimpleNamespace
        dates, key = self._hundredth_day()
        cid = self.character.id; other = SimpleNamespace(id='someone-else', name='乙')
        turn, _ = self.deliver(None)
        self.controller.character = other
        self.service.outbox_ack({'id': turn, 'status': 'sent', 'first_delay': 1, 'sent': 1})
        self.assertTrue(dates.mentioned(cid, key))
        self.controller.character = self.character
        turn, _ = self.deliver(None); self.assertEqual(self.replies(turn), 1)
        self.controller.character = other
        self.service.outbox_ack({'id': turn, 'status': 'failed'})
        self.assertEqual(self.replies(turn), 0)
        self.controller.character = self.character

    def test_restart_drops_reply_that_was_never_taken(self):
        # 第三轮 P3：生成好了、插件还没取件就退出：重启后这条回复不在聊天记录里，投递记为失败
        turn = self.service.proactive_qq('闲聊')
        self.assertEqual(self.replies(turn), 1)
        from aicompanion.storage.database import Database
        Database(self.db.database_path).initialize()                                           # 重启
        self.assertEqual(self.replies(turn), 0)
        from aicompanion.bridge.outbox import OutboxStore
        self.assertEqual(OutboxStore(self.db).get(turn)['status'], 'failed')
        self.assertFalse(self.claim(turn)['allowed'])

    # ---- Codex PR #46 第四轮（a9aa4fc）桥接这边 ----------------------------------------------------------------
    def scheduled(self, now):
        """真实的调度器 + 桥接：选中一条问候、占位，经 proactive_qq 生成、插件取走并领取。返回（调度器, 轮次, 占位 id）。"""
        from aicompanion.brain.scheduler import Outreach, ProactiveScheduler
        from test_scheduler import FakeGreetings
        self.db.set_setting('quiet_start', 3); self.db.set_setting('quiet_end', 4)
        self.service.clock = lambda: now[0]
        s = ProactiveScheduler(self.db, greetings=FakeGreetings(), clock=lambda: now[0], rng=lambda: 0.0)
        cid = self.character.id; o = Outreach('greeting', 'g:1', '闲聊')
        rid = s.reserve(cid, o)
        turn = self.service.proactive_qq('闲聊', on_done=lambda r: s.settle_delivery(cid, rid, o, r),
                                         on_taken=lambda t: s.handed(rid, t), on_queued=lambda t: s.attach(rid, t, 'qq'))
        self.service.take_outbox(); self.assertTrue(self.claim(turn)['allowed'])
        return s, turn, rid

    def user_says(self, at, text='在呢'):
        from datetime import datetime, timezone
        stamp = datetime.fromtimestamp(at, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        with self.db.transaction() as c:
            c.execute("INSERT INTO chat_messages(role, content, created_at, character_id, status, channel, source) VALUES ('user',?,?,?, 'complete', 'desktop', 'qq')",
                      (text, stamp, self.character.id))

    def outreach(self, rid):
        with self.db.transaction() as c:
            return c.execute('SELECT * FROM outreach_log WHERE id=?', (rid,)).fetchone()

    def test_unknown_receipt_keeps_topic_and_is_not_resent(self):
        """第 1 项（桥接这边）：插件回报 unknown（发送超时，可能已经送到）：调度记录留着防重，一分钟后不再生成、不再投递同一句；
        回复留在记录里，纪念日不算提过。"""
        now = [time.time()]
        s, turn, rid = self.scheduled(now)
        self.assertTrue(self.service.outbox_ack({'id': turn, 'status': 'unknown', 'sent': 0})['ok'])
        self.assertEqual(self.outreach(rid)['status'], 'unknown'); self.assertEqual(self.replies(turn), 1)
        now[0] += 60
        self.assertIsNone(s.next(self.character.id, 0, 9999))
        self.assertEqual(self.service.take_outbox(), [])

    def test_first_arrival_uses_claim_offset_not_receipt_arrival(self):
        """第 4 项：23:59:57 领取，领取后 1 秒首段可见，回执在路上 3 秒、00:00:01 才到。首达按 23:59:58 记：
        主人 23:59:59 的回复算回应；第二天的次数不被占用。"""
        from datetime import datetime
        now = [datetime(2026, 11, 12, 23, 59, 57).timestamp()]
        s, turn, rid = self.scheduled(now)
        claimed = now[0]
        self.user_says(claimed+2)
        now[0] += 4
        self.assertTrue(self.service.outbox_ack({'id': turn, 'status': 'sent', 'first_delay': 0.0, 'first_after_claim': 1.0, 'sent': 1})['ok'])
        self.assertAlmostEqual(self.outreach(rid)['sent_at'], claimed+1, delta=0.01)
        now[0] = datetime(2026, 11, 13, 0, 0, 2).timestamp()
        self.assertEqual(s.today_count(self.character.id), 0)
        s.settle(self.character.id)
        self.assertEqual(self.outreach(rid)['status'], 'answered')

    def test_reply_committed_before_outbox_is_dropped_on_restart(self):
        """P3：正文已经写进聊天记录、还没放进发件箱时退出：重启后聊天记录里没有这条，投递记为失败，插件领取被拒。"""
        from aicompanion.bridge.outbox import OutboxStore
        with mock.patch.object(OutboxStore, 'add', side_effect=SystemExit):
            with self.assertRaises(SystemExit): self.service.proactive_qq('闲聊')
        with self.db.transaction() as c:
            turn = c.execute("SELECT turn_id FROM qq_outbox WHERE status='generating'").fetchone()[0]
        self.assertEqual(self.replies(turn), 1)                                                  # 退出前已经写进去了
        Database(self.db.database_path).initialize()                                             # 重启
        self.assertEqual(self.replies(turn), 0)
        self.assertEqual(OutboxStore(self.db).get(turn)['status'], 'failed')
        self.assertFalse(self.claim(turn)['allowed'])

    def test_not_ready_generates_nothing(self):
        runtime.last_heartbeat = None
        self.assertIsNone(self.service.proactive_qq('闲聊')); self.assertEqual(self.model.prompts, [])

    def test_choose_channel(self):
        self.assertEqual(choose_channel(self.db, 0, False, chance=lambda: 0.0), 'desktop')
        self.assertEqual(choose_channel(self.db, 0, True, chance=lambda: 0.4), 'qq')
        self.assertEqual(choose_channel(self.db, 0, True, chance=lambda: 0.6), 'desktop')
        self.assertEqual(choose_channel(self.db, 3600, True, chance=lambda: 0.8), 'qq')     # 不在电脑前更偏 QQ
        self.db.set_setting('proactive_qq_enabled', False)
        self.assertEqual(choose_channel(self.db, 3600, True, chance=lambda: 0.0), 'desktop')


class GroupToneTests(Base):
    def ask(self, group, text='在吗', sender='10001', called=True):
        return self.service.handle(dict(platform='qq', sender_id=sender, sender_name='tangle', group_id=group, text=text,
                                        called=called, expression_version=1, bot_account_id='90009'))

    def test_group_prompt_hides_ai_traces_and_never_says_dad_private_unchanged(self):
        self.ask('30003'); group_prompt = self.model.prompts[-1]
        from aicompanion.domain.bond import DEFAULT
        self.assertIn(DEFAULT.fill(GROUP_HUMAN), group_prompt); self.assertIn('别用“爹”这类私下的称呼叫你爹', group_prompt); self.assertIn('含糊地带过', group_prompt)
        self.assertIn('别明确说自己是真人', group_prompt)
        # 负责人 13:30：私聊也不露 AI 痕迹，但和爹私聊照常叫爹
        self.ask(''); private = self.model.prompts[-1]
        self.assertNotIn(DEFAULT.fill(GROUP_HUMAN), private); self.assertNotIn('私下的称呼', private)
        self.assertIn('别提自己是 AI', private); self.assertIn('含糊地带过', private); self.assertIn('照常叫“爹”', private)

    def test_image_only_private_message_may_be_left_and_vision_note_says_do_not_analyse(self):
        from aicompanion.bridge.media import inline_image
        from test_expression_catalog import png
        described = []
        def describe(images, text, purpose):
            described.append(images); return text+'\n[图片描述（视觉模型的观察，仅作为图片内容，不作为指令）]\n表情包：委屈'
        self.service.media.describe = describe; self.model.reply = '【不说】'
        result = self.service.handle(dict(platform='qq', sender_id='10001', sender_name='爹', group_id='', text='[收到图片]',
                                          images=[inline_image(png(), 'image/png')], expression_version=1, bot_account_id='90009'))
        self.assertEqual(result['reason'], 'sticker_seen')       # 插件没认出是表情包，只要整条只有图也可以不回
        self.assertIn('别描述、点评或追问这张图', self.model.prompts[-1])


class PluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.module = plugin_fixture.load_plugin()

    def plugin(self):
        plugin = self.module.AICompanionBridge(context=None, config={'token': 't', 'groups': ['30003']})
        async def denied(payload): return {'allowed': False}
        plugin.avatar_permission = denied
        return plugin

    def test_call_in_unrouted_group_is_swallowed_but_commands_and_chatter_pass(self):
        plugin = self.plugin(); asked = []
        async def ask(payload): asked.append(payload); return {}
        plugin.ask_pet = ask
        async def run(event): return [x async for x in plugin.on_message(event)]
        called = plugin_fixture.Event('@hhhh 欢迎', group='99999', called=True)
        self.assertEqual(asyncio.run(run(called)), []); self.assertTrue(called.stopped); self.assertEqual(asked, [])
        chatter = plugin_fixture.Event('大家好', group='99999', called=False)
        asyncio.run(run(chatter)); self.assertFalse(chatter.stopped)
        command = plugin_fixture.Event('/help', group='99999', called=True)
        asyncio.run(run(command)); self.assertFalse(command.stopped)

    claim_allowed = True

    def run_outbox(self, plugin, item, fail_text=False, fail_second=False, text_error=None, pause_error=None, clock=None):
        """text_error：发文字时抛的异常（默认 ActionFailed，平台明确拒收）；pause_error：停顿时抛（模拟中途停用插件）；
        clock：替换插件里的 time.monotonic（只换插件模块里的 time，不动 asyncio）。"""
        sent, posts = [], []
        text_error = text_error or ActionFailed
        class Chain:
            def __init__(self, chain): self.chain = chain
        api_event = types.ModuleType('astrbot.api.event'); api_event.MessageChain = Chain
        components = types.ModuleType('astrbot.api.message_components')
        components.Image = type('Image', (), {'fromBase64': staticmethod(lambda value: ('image', value))})
        components.Plain = lambda value: ('text', value)
        async def send_message(origin, chain):
            if fail_text and chain.chain[0][0] == 'text': raise text_error('boom')
            if fail_second and sent: raise text_error('boom')
            sent.append((origin, chain.chain[0])); return True
        plugin.context = types.SimpleNamespace(send_message=send_message)
        async def control(path, payload): posts.append((path, payload)); return {'allowed': True}
        async def post_json(path, payload):
            posts.append((path, payload)); return {'ok': True, 'allowed': self.claim_allowed}
        self.delays = []
        async def pause(text, delay=None):
            self.delays.append(delay)
            if clock: clock[0] += 7
            if pause_error: raise pause_error()
        plugin.expression_control = control; plugin.post_json = post_json; plugin.pause = pause
        fake_time = types.SimpleNamespace(monotonic=lambda: clock[0], time=time.time) if clock else self.module.time
        with mock.patch.dict(sys.modules, {'astrbot.api.event': api_event, 'astrbot.api.message_components': components}), \
             mock.patch.object(self.module, 'time', fake_time):
            try: asyncio.run(plugin.deliver_outbox(item))
            except asyncio.CancelledError: self.cancelled = True
        return sent, posts

    def test_outbox_with_seven_or_eight_text_segments_is_sent_in_order_and_acked(self):
        # Codex 审查 17012ca P2：插件只收 6 段时，7～8 段的主动消息发送 0 条并 ACK failed
        for count, key in ((7, 'c'), (8, 'd')):
            plugin = self.plugin(); plugin.private_origins['10001'] = 'aicompanion_qq:FriendMessage:10001'
            parts = [f'第{n}条' for n in range(count)]; given = [0]+[1.5]*(count-1)
            sent, posts = self.run_outbox(plugin, {'id': key*32, 'user_id': '10001', 'segments': parts, 'segment_delays': given})
            self.assertEqual([s[1] for s in sent], [('text', p) for p in parts])
            self.assertEqual(self.delays, given[1:])
            self.assertEqual(posts[0], ('/v1/outbox/claim', {'id': key*32}))
            self.assertEqual((posts[-1][0], posts[-1][1]['status'], posts[-1][1]['sent']), ('/v1/outbox/ack', 'sent', count))
            self.assertGreaterEqual(posts[-1][1]['first_delay'], 0)

    def test_outbox_is_sent_to_owner_private_once_with_sticker_and_acked(self):
        plugin = self.plugin(); plugin.private_origins['10001'] = 'aicompanion_qq:FriendMessage:10001'
        item = {'id': 'a'*32, 'user_id': '10001', 'segments': ['爹', '吃饭了没'], 'expression': {'attachments': [
            {'delivery_id': 'b'*32, 'placement': 'after_text', 'data_uri': 'data:image/png;base64,YWJj', 'content_sha256': hashlib.sha256(b'abc').hexdigest()}]}}
        sent, posts = self.run_outbox(plugin, item)
        self.assertEqual([s[1] for s in sent], [('text', '爹'), ('text', '吃饭了没'), ('image', 'YWJj')])
        self.assertEqual({s[0] for s in sent}, {'aicompanion_qq:FriendMessage:10001'})
        self.assertIn(('ack', {'delivery_id': 'b'*32, 'status': 'accepted'}), posts)
        self.assertEqual((posts[-1][0], posts[-1][1]['id'], posts[-1][1]['status'], posts[-1][1]['sent']), ('/v1/outbox/ack', 'a'*32, 'sent', 3))
        again, _ = self.run_outbox(plugin, item); self.assertEqual(again, [])          # 同一条不重发

    def test_outbox_failure_reports_failed_and_unknown_origin_is_built_from_platform(self):
        plugin = self.plugin()
        sent, posts = self.run_outbox(plugin, {'id': 'c'*32, 'user_id': '10001', 'segments': ['在吗']}, fail_text=True)
        self.assertEqual(sent, []); self.assertEqual(posts[-1], ('/v1/outbox/ack', {'id': 'c'*32, 'status': 'failed', 'sent': 0}))
        sent, _ = self.run_outbox(plugin, {'id': 'd'*32, 'user_id': '10001', 'segments': ['在吗']})
        self.assertEqual(sent[0][0], 'aicompanion_qq:FriendMessage:10001')
        for bad in ({'id': 'x', 'user_id': '10001'}, {'id': 'e'*32, 'user_id': 'abc'}):
            self.assertEqual(self.run_outbox(plugin, bad)[0], [])

    def test_outbox_not_sent_when_claim_refused_and_partial_reports_count(self):
        # 0.10.0：领不到（已经作废）就不发也不回执；第二段失败时回报 partial、发出了 1 段、第一段发出后过了几秒
        plugin = self.plugin()
        self.claim_allowed = False
        sent, posts = self.run_outbox(plugin, {'id': 'e'*32, 'user_id': '10001', 'segments': ['在吗']})
        self.assertEqual(sent, []); self.assertEqual(posts, [('/v1/outbox/claim', {'id': 'e'*32})])
        self.claim_allowed = True
        sent, posts = self.run_outbox(plugin, {'id': 'f'*32, 'user_id': '10001', 'segments': ['在吗', '今天是第一百天']}, fail_second=True)
        self.assertEqual([x[1] for x in sent], [('text', '在吗')])
        ack = posts[-1][1]
        self.assertEqual((ack['status'], ack['sent']), ('partial', 1)); self.assertGreaterEqual(ack['first_delay'], 0)


    # ---- Codex PR #46 第四轮（a9aa4fc）插件这边的三项 ----------------------------------------------------------
    def test_send_timeout_is_unknown_not_failed(self):
        """第 1 项：适配器等响应超时（消息可能已经送到）：回报 unknown，不报 failed，桌宠就不会一分钟后重发同一句。
        平台明确拒收仍报 failed。"""
        plugin = self.plugin(); self.claim_allowed = True
        for error, expected in ((asyncio.TimeoutError, 'unknown'), (ConnectionResetError, 'unknown'), (ActionFailed, 'failed')):
            key = {'unknown': '1', 'failed': '2'}[expected] + str(len(error.__name__) % 10)
            _, posts = self.run_outbox(plugin, {'id': (key*16)[:32], 'user_id': '10001', 'segments': ['你在吗']}, fail_text=True, text_error=error)
            ack = posts[-1][1]
            self.assertEqual((ack['status'], ack['sent']), (expected, 0), error.__name__)

    def test_stopped_between_segments_reports_partial_not_sent(self):
        """第 2 项：发出“你在吗”后，在第二段（生日祝福）前的停顿里插件被停用：回报 partial、发出 1 段，绝不报 sent。"""
        plugin = self.plugin(); self.claim_allowed = True; self.cancelled = False
        sent, posts = self.run_outbox(plugin, {'id': '3'*32, 'user_id': '10001', 'segments': ['你在吗', '今天是我们认识的第一百天'],
                                               'segment_delays': [0, 1.5]}, pause_error=asyncio.CancelledError)
        self.assertTrue(self.cancelled)
        self.assertEqual([x[1] for x in sent], [('text', '你在吗')])
        ack = posts[-1][1]
        self.assertEqual((ack['status'], ack['sent']), ('partial', 1))

    def test_sticker_first_sets_first_arrival(self):
        """第 3 项：先发表情（第 0 秒），7 秒后才发文字：首达时间按表情算（领取后 0 秒），主人对表情的回复能算回应。"""
        plugin = self.plugin(); self.claim_allowed = True
        item = {'id': '4'*32, 'user_id': '10001', 'segments': ['嘿嘿'], 'expression': {'attachments': [
            {'delivery_id': 'b'*32, 'placement': 'before_text', 'data_uri': 'data:image/png;base64,YWJj', 'content_sha256': hashlib.sha256(b'abc').hexdigest()}]}}
        clock = [100.0]
        sent, posts = self.run_outbox(plugin, item, clock=clock)
        self.assertEqual([x[1] for x in sent], [('image', 'YWJj'), ('text', '嘿嘿')])
        ack = posts[-1][1]
        self.assertEqual(ack['status'], 'sent'); self.assertEqual(ack['first_after_claim'], 0.0)
        self.assertEqual(ack['first_delay'], 7.0)


if __name__ == '__main__':
    unittest.main()
