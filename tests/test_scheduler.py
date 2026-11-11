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


if __name__ == '__main__':
    unittest.main()
