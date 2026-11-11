"""计划书 v4 功能九：陪伴日期。用可控时钟模拟第 100 天、周年、生日、节日；同一个日子一天只提一次、重启不重复；
换人格以后天数不变；群聊里不带主人的日子。不联网。"""
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from aicompanion.services.companion_dates import CompanionDates
from aicompanion.storage.database import Database
from aicompanion.storage.schema import SCHEMA_VERSION
from test_relationship import CID, BridgeFixture


def at(day: date, hour=10) -> float:
    return datetime(day.year, day.month, day.day, hour).timestamp()


class DatesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name)/'d.sqlite3'); self.db.initialize()
        self.now = [at(date(2026, 3, 1), 23)]          # 晚上 11 点认识
        from aicompanion.relationship.service import RelationshipService
        self.rel = RelationshipService(self.db, clock=lambda: self.now[0])
        self._owner()
        self.dates = CompanionDates(self.db, clock=lambda: self.now[0])

    def _owner(self):
        pid = self.rel.owner_person()
        with self.db.transaction() as c:
            c.execute('INSERT OR IGNORE INTO relationship_profiles(character_id,person_id,affinity,familiarity,trust,is_owner,first_met,last_seen,source) '
                      "VALUES (?,?,60,60,60,1,?,?,'owner')", (CID, pid, self.now[0], self.now[0]))

    def tearDown(self): self.tmp.cleanup()

    def go(self, day: date, hour=10): self.now[0] = at(day, hour)

    def test_day_number_uses_calendar_days(self):
        self.assertEqual(SCHEMA_VERSION, 12)
        self.assertEqual(self.dates.day_number(CID), 1)                                          # 见面当天是第 1 天
        self.go(date(2026, 3, 2), 0)
        self.assertEqual(self.dates.day_number(CID), 2)                                          # 过了午夜就是第 2 天（不是按 24 小时）
        self.go(date(2026, 3, 1) + timedelta(days=99))
        self.assertEqual(self.dates.day_number(CID), 100)
        self.assertEqual([d.title for d in self.dates.events_on(CID)], ['认识第 100 天'])

    def test_anniversary_birthday_holiday_and_once_a_day(self):
        self.dates.add(CID, 'birthday', '', date(1999, 6, 9))
        self.go(date(2026, 6, 9))
        text, keys = self.dates.prompt(CID)
        self.assertIn('你的生日', text); self.assertEqual(len(keys), 1)
        self.dates.mark(CID, keys)
        text, keys = CompanionDates(self.db, clock=lambda: self.now[0]).prompt(CID)            # 重启后同一天不再提
        self.assertNotIn('特别的日子', text); self.assertEqual(keys, ()); self.assertIn('认识第 101 天了', text)
        self.go(date(2027, 3, 1))
        titles = [d.title for d in self.dates.events_on(CID)]
        self.assertIn('认识 1 周年，也是她的“生日”', titles)
        self.go(date(2027, 1, 1))
        self.assertIn('元旦', [d.title for d in self.dates.events_on(CID)])
        self.dates.set_options(CID, holidays=False)
        self.assertEqual(self.dates.events_on(CID), [])
        self.dates.add(CID, 'custom', '期末考试', date(2027, 1, 1), yearly=False)
        self.assertEqual([d.title for d in self.dates.events_on(CID)], ['期末考试'])
        self.go(date(2028, 1, 1)); self.assertEqual(self.dates.events_on(CID), [])              # 只一次的不重复

    def test_feb_29_and_met_override_and_her_birthday(self):
        self.dates.add(CID, 'her_birthday', '', date(2024, 2, 29))
        self.go(date(2027, 2, 28)); self.assertIn('她的生日', [d.title for d in self.dates.events_on(CID)])
        self.go(date(2027, 3, 1))
        self.assertEqual([d.title for d in self.dates.events_on(CID)], ['认识 1 周年'])           # 改过她的生日：周年不再说成生日
        self.dates.set_met_on(CID, date(2026, 1, 1))
        self.assertEqual(self.dates.day_number(CID), (date(2027, 3, 1)-date(2026, 1, 1)).days+1)
        with self.assertRaises(ValueError): self.dates.set_met_on(CID, date(2030, 1, 1))
        upcoming = self.dates.upcoming(CID, 400)
        self.assertTrue(any('认识第 520 天' in e.title for _, events in upcoming for e in events))


class PromptTests(BridgeFixture):
    def test_private_prompt_has_days_group_does_not_and_mark_after_reply(self):
        from aicompanion.services.companion_dates import CompanionDates
        dates = CompanionDates(self.db)
        self.controller.relationships.owner_person()
        today = date.today()
        dates.set_met_on(CID, today - timedelta(days=99))                                       # 今天正好第 100 天
        dates.add(CID, 'birthday', '', today)
        self.model.replies.append('嘿嘿 第一百天啦')
        list(self.controller.generate('t1', self.character, __import__('aicompanion.domain.cancellation', fromlist=['x']).CancellationToken(), '在吗'))
        prompt = self.model.prompts[-1]
        self.assertIn('你们认识第 100 天了', prompt); self.assertIn('认识第 100 天、你的生日', prompt)
        self.model.replies.append('嗯')
        list(self.controller.generate('t2', self.character, __import__('aicompanion.domain.cancellation', fromlist=['x']).CancellationToken(), '还在吗'))
        self.assertNotIn('特别的日子', self.model.prompts[-1])                                  # 今天提过了
        self.model.replies.append('在')
        self.service.handle(dict(platform='qq', sender_id='20002', sender_name='群友', group_id='30003', text='在吗', called=True,
                                 expression_version=1, bot_account_id='90000'))
        group = self.model.prompts[-1]
        self.assertNotIn('认识第', group); self.assertNotIn('生日', group)                      # 群里不带主人的日子

    def test_switching_persona_keeps_days(self):
        from aicompanion.services.companion_dates import CompanionDates
        from aicompanion.services.persona_service import PersonaService
        dates = CompanionDates(self.db); dates.set_met_on(CID, date.today() - timedelta(days=9))
        service = PersonaService(self.db); service.ensure(self.character)
        other = service.create(CID, {'title': '另一个', 'description': 'd', 'speaking_style': 's', 'bond': {'preset': 'girlfriend'}})
        service.switch(CID, other.id)
        self.assertEqual(dates.day_number(CID), 10)


if __name__ == '__main__':
    unittest.main()


try:
    from PySide6.QtWidgets import QApplication
except ImportError:   # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, '需要 PySide6')
class CardTests(DatesTests):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def test_card_shows_days_and_adds_dates(self):
        from PySide6.QtCore import QDate
        from aicompanion.ui.dates_card import DatesCard
        self.go(date(2026, 6, 1))
        card = DatesCard(self.db, CID, clock=lambda: self.now[0])
        try:
            self.assertIn('认识第 93 天', card.summary.text())
            self.assertTrue(any('认识第 100 天' in card.upcoming.item(i).text() for i in range(card.upcoming.count())))
            card.kind.setCurrentIndex(card.kind.findData('custom')); card.title.setText('期末考试'); card.when.setDate(QDate(2026, 6, 20))
            card.add()
            self.assertEqual(card.table.rowCount(), 1); self.assertEqual(card.table.item(0, 1).text(), '期末考试')
            card.table.selectRow(0); card.remove(); self.assertEqual(card.table.rowCount(), 0)
            card.holidays.setChecked(False); self.assertFalse(self.dates.options(CID)['holidays'])
        finally:
            card.deleteLater()
