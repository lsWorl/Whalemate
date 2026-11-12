"""统一主动调度（计划书 v4 功能四，阶段三第一步）：每天上限、两次间隔、没回不开新话题、追一句或算了、太久没回记一点委屈、
纪念日优先。可控时钟和随机数，不联网。"""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from aicompanion.brain.scheduler import FOLLOW_AFTER, GIVE_UP_SECONDS, ProactiveScheduler
from aicompanion.storage.database import Database

CID = 'deepseek-whale'
HOUR = 3600


class FakeGreetings:
    def __init__(self): self.calls = 0
    def pace_of(self, cid): return 1.0
    def due(self, cid, idle):
        self.calls += 1
        return f'greet:{self.calls}', '从共同话题开启一句闲聊'


class FakePurposes:
    def __init__(self): self.rows = []; self.log = []
    def delivered(self, pid): self.log.append(('delivered', pid))
    def release(self, pid): self.log.append(('release', pid))
    def due(self, cid, idle, since): return self.rows.pop(0) if self.rows else None
    def intent(self, row): return '心事：'+row['content']


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name)/'s.sqlite3'); self.db.initialize()
        self.db.set_setting('quiet_start', 23); self.db.set_setting('quiet_end', 8)
        self.now = [datetime(2026, 11, 12, 10, 0).timestamp()]
        self.roll = [0.0]
        self.greet, self.purposes = FakeGreetings(), FakePurposes()
        self.s = ProactiveScheduler(self.db, self.purposes, self.greet, clock=lambda: self.now[0], rng=lambda: self.roll[0])

    def tearDown(self): self.tmp.cleanup()

    def later(self, seconds): self.now[0] += seconds

    def say(self, role, text):
        stamp = datetime.fromtimestamp(self.now[0], timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        with self.db.transaction() as c:
            c.execute("INSERT INTO chat_messages(role, content, created_at, character_id, status, channel) VALUES (?,?,?,?, 'complete', 'desktop')",
                      (role, text, stamp, CID))

    def send(self, outreach, text='在干嘛呀'):
        self.say('assistant', text); return self.s.record(CID, outreach)

    def status(self, outreach_id):
        with self.db.transaction() as c:
            return c.execute('SELECT status FROM outreach_log WHERE id=?', (outreach_id,)).fetchone()[0]

    def test_no_new_topic_until_he_answers_then_gap(self):
        first = self.s.next(CID, 0, 9999); self.assertEqual(first.source, 'greeting')
        oid = self.send(first)
        self.later(30*60); self.assertIsNone(self.s.next(CID, 0, 9999))                   # 没回：不开新话题，也还没到追问的时候
        self.say('user', '刚在忙'); self.s.settle(CID); self.assertEqual(self.status(oid), 'answered')
        self.later(30*60); self.assertIsNone(self.s.next(CID, 0, 9999))                   # 回了，但离上次主动还不到 90 分钟
        self.later(40*60); self.assertEqual(self.s.next(CID, 0, 9999).source, 'greeting')

    def test_follow_up_once_then_give_up_with_a_little_hurt(self):
        oid = self.send(self.s.next(CID, 0, 9999), '你看今天的云像不像鲸鱼')
        self.later(FOLLOW_AFTER[1]+60)
        self.assertIsNone(self.s.next(CID, 900, 9999))                                     # 他不在电脑前：不算没理她
        follow = self.s.next(CID, 30, 9999)
        self.assertEqual(follow.source, 'follow'); self.assertIn('像不像鲸鱼', follow.intent); self.assertIn('只说一句', follow.intent)
        self.send(follow, '理理我嘛'); self.assertEqual(self.status(oid), 'followed')
        self.later(FOLLOW_AFTER[1]+60); self.assertIsNone(self.s.next(CID, 30, 9999))      # 一个话题最多追一次
        self.later(GIVE_UP_SECONDS)
        self.s.settle(CID); self.assertEqual(self.status(oid), 'ignored')
        from aicompanion.services.mood_service import MoodService
        kinds = [e['kind'] for e in MoodService(self.db, clock=lambda: self.now[0]).recent(CID)]
        self.assertIn('ignored', kinds)
        self.assertIsNone(self.s.next(CID, 30, 9999))                                      # 刚放下：间隔从放下时算
        self.later(2*HOUR); self.assertEqual(self.s.next(CID, 30, 9999).source, 'greeting')   # 之后可以聊别的

    def test_let_go_sometimes(self):
        oid = self.send(self.s.next(CID, 0, 9999))
        self.roll[0] = 0.99; self.later(FOLLOW_AFTER[1]+60)
        self.assertIsNone(self.s.next(CID, 30, 9999)); self.assertEqual(self.status(oid), 'let_go')
        self.later(10); self.assertIsNone(self.s.next(CID, 30, 9999))                     # 刚“算了”，间隔照算
        self.later(2*HOUR); self.assertEqual(self.s.next(CID, 30, 9999).source, 'greeting')

    def test_daily_limit_and_quiet_rules(self):
        self.db.set_setting('proactive_daily_limit', 2)
        for _ in range(2):
            self.send(self.s.next(CID, 0, 9999)); self.say('user', '嗯'); self.later(2*HOUR)
        self.assertIsNone(self.s.next(CID, 0, 9999))                                       # 今天说够了
        self.now[0] = datetime(2026, 11, 13, 10, 0).timestamp()
        self.assertIsNotNone(self.s.next(CID, 0, 9999))                                    # 第二天重新算
        self.assertIsNone(self.s.next(CID, 0, 60))                                         # 他刚说过话：不插嘴
        self.now[0] = datetime(2026, 11, 13, 23, 30).timestamp(); self.assertIsNone(self.s.next(CID, 0, 9999))
        self.db.set_setting('proactive_enabled', False); self.now[0] = datetime(2026, 11, 14, 10, 0).timestamp()
        self.assertIsNone(self.s.next(CID, 0, 9999))

    def test_special_day_comes_first_then_purpose(self):
        from datetime import date
        from aicompanion.services.companion_dates import CompanionDates
        dates = CompanionDates(self.db, clock=lambda: self.now[0])
        dates.add(CID, 'birthday', '', date(2000, 11, 12))
        self.purposes.rows.append({'id': 7, 'content': '想问他周末去哪'})
        first = self.s.next(CID, 0, 9999)
        self.assertEqual(first.source, 'date'); self.assertIn('你的生日', first.intent)
        self.send(first); self.say('user', '谢谢'); self.later(2*HOUR)
        second = self.s.next(CID, 0, 9999)
        self.assertEqual(second.source, 'purpose'); self.assertEqual(second.payload['purpose']['id'], 7)   # 纪念日今天只主动一次

    def test_special_day_waits_if_he_already_came(self):
        from datetime import date
        from aicompanion.services.companion_dates import CompanionDates
        CompanionDates(self.db, clock=lambda: self.now[0]).add(CID, 'birthday', '', date(2000, 11, 12))
        self.later(-2*HOUR); self.say('user', '早'); self.later(2*HOUR)
        self.assertEqual(self.s.next(CID, 0, 9999).source, 'greeting')                    # 他今天来过：聊天时自然会提


class CodexRoundOneTests(SchedulerTests):
    """Codex PR #46 第一轮三项 P2 的回归。"""

    def test_pending_qq_send_blocks_duplicate_follow_up(self):
        """走 QQ 要等插件回执：真实 MainWindow._proactive 选中后先占位，回执到之前下一次 tick 不会再选中同一句追问。"""
        from aicompanion.ui.main_window import MainWindow
        self.send(self.s.next(CID, 0, 9999))
        self.later(FOLLOW_AFTER[1]+60)
        receipts = []
        window = SimpleNamespace(
            controller=SimpleNamespace(character=SimpleNamespace(id=CID)), database=self.db, outreach_scheduler=self.s,
            purposes=SimpleNamespace(delivered=lambda i: None, release=lambda i: None),
            _proactive_on_qq=lambda intent, done, taken=None, queued=None, still_valid=None: receipts.append(done) or True)
        MainWindow._deliver_outreach(window, CID, self.s.next(CID, 30, 9999))
        self.later(60)
        self.assertIsNone(self.s.next(CID, 30, 9999))                                     # 回执没到：占着，不再追第二遍
        self.assertEqual(len(receipts), 1)
        receipts[0](True)                                                                 # 回执到了：确认
        with self.db.transaction() as c:
            rows = c.execute("SELECT source, status, channel FROM outreach_log ORDER BY id").fetchall()
        self.assertEqual([tuple(r) for r in rows], [('greeting', 'followed', 'desktop'), ('follow', 'sent', 'qq')])

    def test_failed_send_releases_and_stale_pending_expires(self):
        outreach = self.s.next(CID, 0, 9999)
        rid = self.s.reserve(CID, outreach)
        self.assertIsNone(self.s.next(CID, 0, 9999)); self.assertEqual(self.s.today_count(CID), 1)   # 发送中也算今天一次
        self.s.release(rid); self.assertEqual(self.s.today_count(CID), 0)
        self.assertIsNotNone(self.s.next(CID, 0, 9999))
        self.s.reserve(CID, outreach); self.later(31*60)
        self.assertIsNotNone(self.s.next(CID, 0, 9999))                                   # 半小时没确认：当作没发出去

    def test_recent_owner_qq_message_counts_as_talking(self):
        self.later(-30)
        stamp = datetime.fromtimestamp(self.now[0], timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        with self.db.transaction() as c:
            c.execute("INSERT INTO chat_messages(role, content, created_at, character_id, status, source, channel) "
                      "VALUES ('user','在干嘛',?,?,'complete','qq','desktop')", (stamp, CID))
        self.later(30)
        self.assertIsNone(self.s.next(CID, 9999, 9999))                                   # 桌面闲置很久，但他 30 秒前在 QQ 私聊说过话

    def test_gap_setting_applies_to_follow_ups(self):
        self.db.set_setting('proactive_gap_minutes', 720)
        self.send(self.s.next(CID, 0, 9999))
        self.later(91*60); self.assertIsNone(self.s.next(CID, 30, 9999))                  # 设成 12 小时：91 分钟后不追问
        self.db.set_setting('proactive_gap_minutes', 30)
        self.assertEqual(self.s.next(CID, 30, 9999).source, 'follow')



class CodexRoundTwoTests(SchedulerTests):
    """Codex PR #46 第二轮（22b2f18）五项 P2、一项 P3 的回归。"""

    def window(self, receipts, hand=None, claim=True):
        """真实的 MainWindow._deliver_outreach；_proactive_on_qq 换成替身：放进发件箱（qq_outbox）、按需模拟插件领取。"""
        from aicompanion.bridge.outbox import OutboxStore
        from aicompanion.ui.main_window import MainWindow
        def on_qq(intent, done, taken=None, queued=None, still_valid=None):
            receipts.append(done)
            if hand:
                store = OutboxStore(self.db, clock=lambda: self.now[0])
                store.add(hand, CID); queued and queued(hand)
                if claim:
                    store.dequeued(hand); store.claim(hand); taken and taken(hand)
            return True
        return MainWindow, SimpleNamespace(
            controller=SimpleNamespace(character=SimpleNamespace(id=CID)), database=self.db, outreach_scheduler=self.s,
            purposes=SimpleNamespace(delivered=lambda i: None, release=lambda i: None), _proactive_on_qq=on_qq)

    def say_turn(self, role, text, turn, source='qq'):
        stamp = datetime.fromtimestamp(self.now[0], timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        with self.db.transaction() as c:
            c.execute("INSERT INTO chat_messages(role, content, created_at, character_id, turn_id, status, source, channel) "
                      "VALUES (?,?,?,?,?, 'complete', ?, 'desktop')", (role, text, stamp, CID, turn, source))

    def rows(self):
        with self.db.transaction() as c:
            return [tuple(r) for r in c.execute('SELECT source, status, parent_id FROM outreach_log ORDER BY id')]

    def test_taken_but_unacked_follow_up_is_never_repeated(self):
        """插件领取了追问、回执丢了：结果未知，留着防重；之后两个多小时既不再追问、也不开新话题；迟到的回执照实补记。"""
        from aicompanion.bridge.outbox import Delivery
        self.send(self.s.next(CID, 0, 9999))
        self.later(FOLLOW_AFTER[1]+60)
        receipts = []; cls, window = self.window(receipts, hand='f'*32)
        follow = self.s.next(CID, 30, 9999); self.assertEqual(follow.source, 'follow')
        cls._deliver_outreach(window, CID, follow)
        for _ in range(7):                                                                # 往后两个多小时，每 20 分钟一次 tick
            self.later(20*60); self.assertIsNone(self.s.next(CID, 30, 9999))
        self.assertEqual(len(receipts), 1)
        # 打招呼那条发出 3 小时没回：照旧记成没理（它确实发到了）；追问那条结果未知，还挂着
        self.assertEqual([r[:2] for r in self.rows()], [('greeting', 'ignored'), ('follow', 'unknown')])
        receipts[0](Delivery('sent', self.now[0]))                                        # 迟到的回执：改记成发出，不另补一条
        self.assertEqual([r[:2] for r in self.rows()], [('greeting', 'ignored'), ('follow', 'sent')])

    def test_reply_before_final_receipt_counts_as_answer(self):
        """分段发送：主人看到第一段就回了，之后才收到整条回执。按插件回报的第一段发出时间判断：算接上了，不追问。"""
        from aicompanion.bridge.outbox import Delivery
        receipts = []; cls, window = self.window(receipts, hand='a'*32)
        cls._deliver_outreach(window, CID, self.s.next(CID, 0, 9999))                    # 插件领取
        self.later(7); first = self.now[0]                                                # 第 7 秒第一段发出
        self.later(2); self.say('user', '在呢')                                           # 第 9 秒他回了
        self.later(3); receipts[0](Delivery('sent', first))                              # 第 12 秒整条回执
        self.later(91*60)
        self.assertNotEqual(getattr(self.s.next(CID, 30, 9999), 'source', None), 'follow')
        self.assertEqual(self.rows()[0][:2], ('greeting', 'answered'))

    def test_restart_before_generation_gives_special_day_back(self):
        """选中生日、占位后还没生成就退出：重启 31 分钟后占位作废，纪念日的“今天已选”也一并放回，还能再说。"""
        from aicompanion.services.companion_dates import CompanionDates
        CompanionDates(self.db, clock=lambda: self.now[0]).add(CID, 'birthday', '', datetime.fromtimestamp(self.now[0]).date())
        first = self.s.next(CID, 0, 9999); self.assertEqual(first.source, 'date')
        self.s.reserve(CID, first)                                                        # 然后程序退出
        db = Database(Path(self.tmp.name)/'s.sqlite3'); db.initialize()
        again = ProactiveScheduler(db, self.purposes, self.greet, clock=lambda: self.now[0], rng=lambda: self.roll[0])
        self.later(31*60)
        self.assertEqual(getattr(again.next(CID, 0, 9999), 'source', None), 'date')
        self.assertEqual(again.today_count(CID), 0)

    def test_close_relationship_never_goes_below_the_set_gap(self):
        """关系很近（节奏 0.6）、设置 120 分钟：91 分钟时不追问，过了 120 分钟才可能追问。"""
        self.greet.pace_of = lambda cid: 0.6
        self.db.set_setting('proactive_gap_minutes', 120)
        self.assertEqual(self.s.gap_seconds(CID), 120*60)
        self.send(self.s.next(CID, 0, 9999))
        self.later(91*60); self.assertIsNone(self.s.next(CID, 30, 9999))
        self.later(30*60); self.assertEqual(self.s.next(CID, 30, 9999).source, 'follow')
        self.greet.pace_of = lambda cid: 1.5                                              # 还不熟：间隔拉长
        self.assertEqual(self.s.gap_seconds(CID), 180*60)

    def test_follow_up_quotes_the_qq_message_by_turn(self):
        """QQ 先生成、插件 12 秒后才发出第一段：追问仍能按轮次找回上一句原话。"""
        from aicompanion.bridge.outbox import Delivery
        turn = 'b'*32
        self.say_turn('assistant', '你看今天的晚霞好好看', turn)
        receipts = []; cls, window = self.window(receipts, hand=turn)
        cls._deliver_outreach(window, CID, self.s.next(CID, 0, 9999))
        self.later(12); receipts[0](Delivery('sent', self.now[0]))
        self.later(91*60)
        follow = self.s.next(CID, 30, 9999)
        self.assertEqual(follow.source, 'follow'); self.assertIn('晚霞', follow.intent)

    def test_outreach_columns_added_to_existing_v13_table(self):
        """先跑过这个分支的 v13 库（outreach_log 还没有两列新列）：重新打开时补上，不丢数据。"""
        path = Path(self.tmp.name)/'old.sqlite3'
        db = Database(path); db.initialize()
        with db.transaction() as c:
            c.execute('DROP TABLE outreach_log')
            c.execute("CREATE TABLE outreach_log (id INTEGER PRIMARY KEY AUTOINCREMENT, character_id TEXT NOT NULL, source TEXT NOT NULL, "
                      "key TEXT NOT NULL, channel TEXT NOT NULL DEFAULT 'desktop', status TEXT NOT NULL DEFAULT 'sent', parent_id INTEGER, "
                      "sent_at REAL NOT NULL, settled_at REAL)")
            c.execute("INSERT INTO outreach_log(character_id, source, key, sent_at) VALUES (?, 'greeting', 'g', 1)", (CID,))
        Database(path).initialize()
        with db.transaction() as c:
            columns = {r[1] for r in c.execute('PRAGMA table_info(outreach_log)')}
            self.assertTrue({'turn_id', 'handed_at'} <= columns)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM outreach_log').fetchone()[0], 1)

    def test_baseline_v12_database_upgrades_to_v13(self):
        """协作基线（91d8bef）的库是 v12：没有 outreach_log、qq_outbox。升级到 v13 要备份一次、保留旧消息和设置、
        建好两张新表；再次打开不重复备份、不改动数据。v13 接在基线最高版本之后，不覆盖基线已有的迁移。"""
        import sqlite3
        from contextlib import closing
        from aicompanion.storage.schema import SCHEMA_VERSION
        self.assertGreaterEqual(SCHEMA_VERSION, 13)
        path = Path(self.tmp.name)/'v12.sqlite3'
        db = Database(path); db.initialize()
        db.add_message('user', '基线里的旧消息', CID); db.set_setting('proactive_daily_limit', 3)
        with closing(sqlite3.connect(path)) as conn:
            conn.execute('DROP TABLE outreach_log'); conn.execute('DROP TABLE qq_outbox'); conn.execute('PRAGMA user_version=12'); conn.commit()
        for backup in Path(self.tmp.name).glob('v12.backup-*'): backup.unlink()
        db = Database(path); db.initialize()
        self.assertEqual(len(list(Path(self.tmp.name).glob('v12.backup-*.sqlite3'))), 1)
        with closing(sqlite3.connect(path)) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({'outreach_log', 'qq_outbox', 'companion_dates', 'companion_date_mentions'} <= tables)
        self.assertEqual([r['content'] for r in db.recent_messages(5, CID)], ['基线里的旧消息'])
        self.assertEqual(int(db.get_setting('proactive_daily_limit', 5)), 3)
        s = ProactiveScheduler(db, self.purposes, self.greet, clock=lambda: self.now[0], rng=lambda: self.roll[0])
        self.assertEqual(s.next(CID, 0, 9999).source, 'greeting')                          # 新表可用
        Database(path).initialize()                                                         # 再次打开
        self.assertEqual(len(list(Path(self.tmp.name).glob('v12.backup-*.sqlite3'))), 1)



class CodexRoundThreeTests(CodexRoundTwoTests):
    """Codex PR #46 第三轮（e38ed29）的回归：按插件报回的事实结算。"""

    def test_message_before_first_actual_send_is_not_an_answer(self):
        """领取后第 2 秒他发了句无关的话，第一段到第 7 秒才发出：那句不算回应。"""
        from aicompanion.bridge.outbox import Delivery
        receipts = []; cls, window = self.window(receipts, hand='c'*32)
        cls._deliver_outreach(window, CID, self.s.next(CID, 0, 9999))
        self.later(2); self.say('user', '我先去洗澡了')
        self.later(5); first = self.now[0]
        self.later(5); receipts[0](Delivery('sent', first))
        self.later(91*60)
        self.assertEqual(self.s.next(CID, 30, 9999).source, 'follow')                     # 还在等回应，可以追一句

    def test_daily_limit_counts_by_first_actual_send(self):
        """23:59:58 领取、00:00:03 才发出：算第二天的一次；每天 1 次时，当天不会再发。"""
        from aicompanion.bridge.outbox import Delivery
        self.db.set_setting('proactive_daily_limit', 1)
        self.db.set_setting('quiet_start', 3); self.db.set_setting('quiet_end', 4)
        self.now[0] = datetime(2026, 11, 12, 23, 59, 58).timestamp()
        receipts = []; cls, window = self.window(receipts, hand='d'*32)
        cls._deliver_outreach(window, CID, self.s.next(CID, 0, 9999))
        self.later(5); receipts[0](Delivery('sent', self.now[0]))
        self.say('user', '早点睡')                                                         # 他回了，不卡在“等回应”
        self.now[0] = datetime(2026, 11, 13, 1, 31, 3).timestamp()
        self.assertEqual(self.s.today_count(CID), 1)
        self.assertIsNone(self.s.next(CID, 0, 9999))

    def test_restart_reconciles_from_outbox(self):
        """心事走 QQ，插件领取后程序重启，回调没了；迟到的回执记进 qq_outbox 后，新调度器对账：记成 QQ 上说过、心事送到。"""
        from aicompanion.bridge.outbox import OutboxStore
        self.purposes.rows.append({'id': 1, 'content': '想问问考试'})
        receipts = []; cls, window = self.window(receipts, hand='e'*32)
        outreach = self.s.next(CID, 0, 9999); self.assertEqual(outreach.source, 'purpose')
        cls._deliver_outreach(window, CID, outreach)
        db = Database(Path(self.tmp.name)/'s.sqlite3'); db.initialize()                    # 重启（插件领取过的保持原样，等回执）
        again = ProactiveScheduler(db, self.purposes, self.greet, clock=lambda: self.now[0], rng=lambda: self.roll[0])
        self.later(20); self.assertIsNone(again.next(CID, 0, 9999))                       # 还在发送中
        OutboxStore(db, clock=lambda: self.now[0]).settle('e'*32, 'sent', self.now[0]-5)  # 迟到的回执到了新进程
        self.later(60*60); again.next(CID, 0, 9999)
        with db.transaction() as c:
            row = c.execute('SELECT source, status, channel FROM outreach_log').fetchone()
        self.assertEqual(tuple(row), ('purpose', 'sent', 'qq'))
        self.assertIn(('delivered', 1), self.purposes.log)

    def test_never_claimed_is_failed_and_given_back(self):
        """交出去了插件却没来领取（没读到心跳响应）：确定没发。撤掉记录，问候的占位放回。"""
        released = []
        self.db.release_proactive = lambda cid, key: released.append(key)
        receipts = []; cls, window = self.window(receipts, hand='9'*32, claim=False)
        outreach = self.s.next(CID, 0, 9999)
        cls._deliver_outreach(window, CID, outreach)
        from aicompanion.bridge.outbox import OutboxStore
        OutboxStore(self.db, clock=lambda: self.now[0]).dequeued('9'*32)
        self.later(3*60); self.s.pending(CID)
        self.assertEqual(self.rows(), []); self.assertEqual(released, [outreach.key])


if __name__ == '__main__':
    unittest.main()
