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
    def __init__(self): self.rows = []
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
            _proactive_on_qq=lambda intent, done, taken=None: receipts.append(done) or True)
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

    def window(self, receipts, hand=None):
        """真实的 MainWindow._deliver_outreach；_proactive_on_qq 换成替身：记下回执回调，按需模拟插件取件。"""
        from aicompanion.ui.main_window import MainWindow
        def on_qq(intent, done, taken=None):
            receipts.append(done)
            if hand and taken: taken(hand)
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
        """插件取走了追问、回执丢了：30 分钟后不再撤掉重来，按已发出算；同一个话题只追问一次。"""
        self.send(self.s.next(CID, 0, 9999))
        self.later(FOLLOW_AFTER[1]+60)
        receipts = []; cls, window = self.window(receipts, hand='f'*32)
        follow = self.s.next(CID, 30, 9999); self.assertEqual(follow.source, 'follow')
        cls._deliver_outreach(window, CID, follow)
        for _ in range(6):                                                                # 往后两个多小时，每 20 分钟一次 tick
            self.later(20*60); self.assertIsNone(self.s.next(CID, 30, 9999))
        self.assertEqual(len(receipts), 1)
        self.assertEqual([r[0] for r in self.rows()], ['greeting', 'follow'])
        receipts[0](True)                                                                 # 迟到的回执：不再补记一条
        self.assertEqual(len(self.rows()), 2)

    def test_reply_before_final_receipt_counts_as_answer(self):
        """分段发送：主人看到第一段就回了，12 秒后才收到整条回执。回复时间早于回执也算接上了，不追问。"""
        receipts = []; cls, window = self.window(receipts, hand='a'*32)
        cls._deliver_outreach(window, CID, self.s.next(CID, 0, 9999))                    # 选中即交给插件（取件）
        self.later(5); self.say('user', '在呢')
        self.later(7); receipts[0](True)                                                  # 回执
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
        """QQ 先生成、插件 12 秒后才取件：追问仍能按轮次找回上一句原话。"""
        turn = 'b'*32
        receipts = []; cls, window = self.window(receipts)
        cls._deliver_outreach(window, CID, self.s.next(CID, 0, 9999))
        self.say_turn('assistant', '你看今天的晚霞好好看', turn)
        self.later(12)
        with self.db.transaction() as c: oid = c.execute('SELECT id FROM outreach_log').fetchone()[0]
        self.s.handed(oid, turn); receipts[0](True)
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


if __name__ == '__main__':
    unittest.main()
