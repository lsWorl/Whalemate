"""P3：会遗忘的记忆卡、全量存档、收件箱、目的机制、专注模式。纯 Python，可注入时间。"""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from aicompanion.brain.focus import in_quiet_hours, is_focused, set_focus
from aicompanion.brain.memory_card import CAPACITY, MemoryOrganizer, decayed, level, render, select_for_prompt
from aicompanion.brain.purpose import PurposeEngine
from aicompanion.storage.database import Database


class Clock:
    def __init__(self, start): self.now = start
    def __call__(self): return self.now
    def advance(self, **kw): self.now += timedelta(**kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name)/'db.sqlite3'); self.db.initialize()
        self.clock = Clock(datetime(2026, 11, 6, 14, 0))


class DecayTests(Base):
    def test_half_life_and_levels(self):
        self.assertAlmostEqual(decayed(1.0, 1, 'event', 7), 0.5)
        self.assertAlmostEqual(decayed(1.0, 2, 'event', 21), 0.5)
        self.assertEqual(decayed(1.0, 5, 'event', 999), 1.0)
        self.assertEqual(decayed(1.0, 1, 'identity', 999), 1.0)
        self.assertEqual([level(x) for x in (0.9, 0.5, 0.1)], ['clear', 'fuzzy', 'forgotten'])

    def test_organizer_fades_then_forgets_but_archive_keeps(self):
        self.db.upsert_memory('用户今天吃了拉面', 'deepseek-whale', 'event:ramen', 'event', 1)
        self.db.upsert_memory('用户的名字是小林', 'deepseek-whale', 'name', 'identity', 3)
        organizer = MemoryOrganizer(self.db, self.clock)
        organizer.run('deepseek-whale')                     # 第一次只记录时间点
        self.clock.advance(days=7); report = organizer.run('deepseek-whale')
        ramen = next(r for r in self.db.memory_card('deepseek-whale') if r['fact_key'] == 'event:ramen')
        self.assertAlmostEqual(ramen['clarity'], 0.5, places=2); self.assertEqual(report['faded'], 1)
        self.assertIn('印象有点模糊', render(select_for_prompt(self.db.memory_card('deepseek-whale'))))
        self.clock.advance(days=7); report = organizer.run('deepseek-whale')
        self.assertEqual(report['forgotten'], 1)
        keys = [r['fact_key'] for r in self.db.memory_card('deepseek-whale')]
        self.assertEqual(keys, ['name'])            # 名字不会忘
        reasons = [r['reason'] for r in self.db.memory_archive('deepseek-whale') if r['fact_key'] == 'event:ramen']
        self.assertIn('forgotten', reasons)          # 全量存档还在

    def test_forgotten_can_be_relearned_but_deleted_cannot(self):
        self.db.upsert_memory('用户喜欢猫', 'deepseek-whale', 'like:cat', 'preference', 1)
        mid = self.db.memory_card('deepseek-whale')[0]['id']
        self.db.forget_memories([mid])
        self.assertTrue(self.db.upsert_memory('用户喜欢猫', 'deepseek-whale', 'like:cat', 'preference', 1))
        mid = self.db.memory_card('deepseek-whale')[0]['id']
        self.db.delete_memory(mid, 'deepseek-whale')
        self.assertFalse(self.db.upsert_memory('用户喜欢猫', 'deepseek-whale', 'like:cat', 'preference', 1))
        self.assertEqual([r for r in self.db.memory_archive('deepseek-whale') if r['fact_key'] == 'like:cat'], [])

    def test_recall_raises_clarity(self):
        from aicompanion.services.memory_service import MemoryService
        self.db.upsert_memory('用户喜欢喝乌龙茶', 'deepseek-whale', 'like:tea', 'preference', 2)
        mid = self.db.memory_card('deepseek-whale')[0]['id']; self.db.set_clarity([(mid, 0.4)])
        text = MemoryService(self.db).build_memory_context('最近在喝乌龙茶吗', 'deepseek-whale')
        self.assertIn('乌龙茶', text)
        self.assertAlmostEqual(self.db.memory_card('deepseek-whale')[0]['clarity'], 0.65, places=2)
        MemoryService(self.db).build_memory_context('今天天气', 'deepseek-whale')   # 不相关不回升
        self.assertAlmostEqual(self.db.memory_card('deepseek-whale')[0]['clarity'], 0.65, places=2)

    def test_capacity_trims_weakest(self):
        for i in range(CAPACITY+5):
            self.db.upsert_memory(f'事实{i}', 'deepseek-whale', f'k{i}', 'event', 1 if i < 5 else 3)
        report = MemoryOrganizer(self.db, self.clock).run('deepseek-whale')
        self.assertEqual(report['trimmed'], 5)
        self.assertEqual(len(self.db.memory_card('deepseek-whale')), CAPACITY)
        self.assertNotIn('k0', [r['fact_key'] for r in self.db.memory_card('deepseek-whale')])

    def test_interactions_become_small_memories(self):
        for _ in range(3): self.db.add_inbox('deepseek-whale', 'pat')
        self.db.add_inbox('deepseek-whale', 'thrown')
        MemoryOrganizer(self.db, self.clock).run('deepseek-whale')
        contents = [r['content'] for r in self.db.memory_card('deepseek-whale')]
        self.assertTrue(any('被你摸了头3次' in c for c in contents))
        self.assertEqual(self.db.inbox('deepseek-whale'), [])

    def test_scope_filters_public_scene(self):
        """记忆默认在各处共享；只有标成 private 的不在公开场合用（用户 2026-11-07 决定）。"""
        self.db.upsert_memory('用户私下说的秘密', 'deepseek-whale', 'secret', 'event', 3, scope='private')
        self.db.upsert_memory('用户在桌面上说喜欢蓝色', 'deepseek-whale', 'color', 'preference', 2, scope='pet')
        self.db.upsert_memory('用户在群里说喜欢原神', 'deepseek-whale', 'game', 'preference', 2, scope='qq')
        pet = [r['fact_key'] for r, _, _ in select_for_prompt(self.db.memory_card('deepseek-whale'), scene='pet')]
        public = [r['fact_key'] for r, _, _ in select_for_prompt(self.db.memory_card('deepseek-whale'), scene='qq')]
        self.assertEqual(set(pet), {'secret', 'color', 'game'}); self.assertEqual(set(public), {'color', 'game'})
        self.db.upsert_memory('用户私下说的秘密（更新）', 'deepseek-whale', 'secret', 'event', 3)   # 再次提到不会把"只在私下用"改回共享
        self.assertEqual(next(r['scope'] for r in self.db.memory_card('deepseek-whale') if r['fact_key'] == 'secret'), 'private')


class MigrationTests(Base):
    def test_v1_database_upgrades_with_archive_and_backup(self):
        path = Path(self.temp.name)/'v1.sqlite3'
        db = Database(path); db.initialize(); db.upsert_memory('旧记忆', 'deepseek-whale', 'old', 'event')
        with closing(sqlite3.connect(path)) as c:
            c.execute('PRAGMA user_version=1')
            for column in ('clarity', 'recalled_at', 'scope'): c.execute(f'ALTER TABLE memories DROP COLUMN {column}')
            c.execute('DROP TABLE memory_archive'); c.commit()
        db.initialize(); db.initialize()
        row = db.memory_card('deepseek-whale')[0]
        self.assertEqual((row['content'], row['clarity'], row['scope']), ('旧记忆', 1.0, 'pet'))
        self.assertEqual([r['reason'] for r in db.memory_archive('deepseek-whale')], ['migrated'])
        self.assertEqual(len(list(path.parent.glob('v1.backup-*.sqlite3'))), 1)


class FakeClient:
    def __init__(self, reply): self.reply = reply
    def generate_reply_stream(self, messages, system_prompt, cancellation=None): yield self.reply


class PurposeTests(Base):
    def setUp(self):
        super().setUp()
        self.db.upsert_memory('用户在准备下周的考试', 'deepseek-whale', 'exam', 'event', 3)
        self.mid = self.db.memory_card('deepseek-whale')[0]['id']
        import random
        self.engine = PurposeEngine(self.db, self.clock, random.Random(1))

    def due_now(self, idle=30, since_user=3600):
        self.clock.advance(hours=3)
        return self.engine.due('deepseek-whale', idle, since_user)

    def test_rules_generate_and_deliver(self):
        self.assertTrue(self.engine.should_generate('deepseek-whale'))
        ids = self.engine.generate('deepseek-whale')
        self.assertEqual(len(ids), 1)
        self.assertFalse(self.engine.should_generate('deepseek-whale'))       # 已排下次生成时间
        row = self.due_now()
        self.assertIsNotNone(row); self.assertIn('考试', self.engine.intent(row))
        self.engine.delivered(row['id'])
        self.assertIsNone(self.engine.due('deepseek-whale', 30, 3600))

    def test_model_output_validated(self):
        reply = json.dumps({'purposes': [
            {'kind': 'share', 'content': '想把我新学的复习方法告诉你', 'opener': '我想到一个复习的好办法！', 'memory_ids': [self.mid]},
            {'kind': 'hack', 'content': 'x', 'memory_ids': [self.mid]},
            {'kind': 'ask', 'content': '编造的', 'memory_ids': [999]}]})
        ids = self.engine.generate('deepseek-whale', FakeClient(reply))
        self.assertEqual(len(ids), 1)
        self.assertEqual(self.db.purposes('deepseek-whale', 'pending')[0]['kind'], 'share')

    def test_conditions_block_delivery(self):
        self.engine.generate('deepseek-whale')
        self.assertIsNone(self.due_now(idle=900))                # 你不在电脑前
        self.assertIsNone(self.engine.due('deepseek-whale', 30, 60))      # 刚聊过
        set_focus(self.db, 60, self.clock())
        self.assertIsNone(self.engine.due('deepseek-whale', 30, 3600))   # 专注
        set_focus(self.db, None)
        self.clock.now = self.clock.now.replace(hour=2)
        self.assertIsNone(self.engine.due('deepseek-whale', 30, 3600))   # 深夜

    def test_daily_limit_and_expiry(self):
        self.db.set_setting('purpose_daily_limit', 1)
        for _ in range(2): self.db.add_purpose('deepseek-whale', 'share', '惦记的事', '', [self.mid], self.clock().isoformat())
        self.clock.advance(minutes=1)
        first = self.engine.due('deepseek-whale', 30, 3600); self.engine.delivered(first['id'])
        self.assertIsNone(self.engine.due('deepseek-whale', 30, 3600))
        self.clock.advance(days=2)
        self.engine.expire_old('deepseek-whale')
        self.assertEqual(self.db.purposes('deepseek-whale', 'pending'), [])

    def test_due_time_avoids_quiet_hours(self):
        self.clock.now = datetime(2026, 11, 6, 22, 50)
        self.engine.generate('deepseek-whale')
        due = datetime.fromisoformat(self.db.purposes('deepseek-whale', 'pending')[0]['due_at'])
        self.assertFalse(in_quiet_hours(self.db, due.hour))


class FocusTests(Base):
    def test_focus_expires(self):
        set_focus(self.db, 25, self.clock())
        self.assertTrue(is_focused(self.db, self.clock()))
        self.clock.advance(minutes=26)
        self.assertFalse(is_focused(self.db, self.clock()))


if __name__ == '__main__': unittest.main()
