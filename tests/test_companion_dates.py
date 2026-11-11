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


    def test_milestone_switch_covers_anniversaries(self):
        """Codex PR #43 R1：关掉“纪念日”后，认识周年（含默认的她的生日）和第一次办成事周年也不提。"""
        with self.db.transaction() as c:
            c.execute("INSERT INTO agent_runs(character_id, goal, status, started_at, ended_at) VALUES (?,?,?,?,?)",
                      (CID, '整理桌面', 'done', '2026-03-01 09:00:00', '2026-03-01 09:05:00'))
        self.go(date(2027, 3, 1))
        self.assertEqual(len(self.dates.events_on(CID)), 2)
        self.dates.set_options(CID, milestones=False, holidays=False)
        self.assertEqual(self.dates.events_on(CID), [])
        self.dates.add(CID, 'her_birthday', '', date(2020, 3, 1))                                  # 用户自己加的日子不跟这个开关
        self.assertEqual([d.title for d in self.dates.events_on(CID)], ['她的生日'])

    def test_first_achievement_uses_completion_day(self):
        """Codex PR #43 R3：跨午夜办成的事按完成那天算；旧记录没有完成时间时退回开始时间。"""
        with self.db.transaction() as c:
            c.execute("INSERT INTO agent_runs(character_id, goal, status, started_at, ended_at) VALUES (?,?,?,?,?)",
                      (CID, '跨年任务', 'done', '2025-12-31 23:55:00', '2026-01-01 00:05:00'))
            c.execute("INSERT INTO agent_runs(character_id, goal, status, started_at, ended_at) VALUES (?,?,?,?,?)",
                      (CID, '早开工晚完成', 'done', '2025-12-30 08:00:00', '2026-01-02 08:00:00'))
        self.assertEqual(self.dates.first_achievement(CID), date(2026, 1, 1))
        with self.db.transaction() as c:
            c.execute("INSERT INTO agent_runs(character_id, goal, status, started_at, ended_at) VALUES (?,?,?,?,NULL)",
                      (CID, '旧记录', 'done', '2025-11-01 10:00:00'))
        self.assertEqual(self.dates.first_achievement(CID), date(2025, 11, 1))

    def test_one_off_lunar_keeps_chosen_day(self):
        """Codex PR #43 R2：只一次的日子勾了农历也按选中的那天存，不会因为闰月落到普通月。2025-08-01 是闰六月初八。"""
        self.dates.add(CID, 'custom', '一次性', date(2025, 8, 1), yearly=False, lunar=True)
        row = self.dates.user_dates(CID)[0]
        self.assertEqual((row['calendar'], row['year'], row['month'], row['day']), ('solar', 2025, 8, 1))
        self.go(date(2025, 8, 1)); self.assertIn('一次性', [d.title for d in self.dates.events_on(CID)])
        self.go(date(2025, 7, 2)); self.assertNotIn('一次性', [d.title for d in self.dates.events_on(CID)])


class LunarTests(DatesTests):
    """农历（lunar_python，负责人 2026-11-11 同意引入）：春节、中秋、除夕；农历生日每年落在不同的公历日期。"""
    def test_lunar_holidays(self):
        for day, title in ((date(2026, 2, 16), '除夕'), (date(2026, 2, 17), '春节'), (date(2026, 9, 25), '中秋节'),
                           (date(2027, 2, 6), '春节'), (date(2027, 9, 15), '中秋节'), (date(2026, 8, 19), '七夕')):
            self.go(day); self.assertIn(title, [d.title for d in self.dates.events_on(CID)], day)
        self.go(date(2026, 9, 24)); self.assertNotIn('中秋节', [d.title for d in self.dates.events_on(CID)])

    def test_lunar_birthday_moves_each_year(self):
        from aicompanion.services.companion_dates import lunar_to_solar
        self.dates.add(CID, 'birthday', '', date(2000, 2, 5), lunar=True)                      # 2000-02-05 是正月初一
        row = self.dates.user_dates(CID)[0]
        self.assertEqual((row['calendar'], row['month'], row['day']), ('lunar', 1, 1))
        for day in (date(2026, 2, 17), date(2027, 2, 6)):
            self.go(day); self.assertIn('你的生日', [d.title for d in self.dates.events_on(CID)])
        self.go(date(2027, 2, 17)); self.assertNotIn('你的生日', [d.title for d in self.dates.events_on(CID)])
        self.assertEqual(lunar_to_solar(2025, 12, 30), date(2026, 2, 16))                       # 腊月只有 29 天：三十按二十九算


    def test_lunar_yearly_waits_for_its_first_year(self):
        """Codex PR #43 R5：按农历每年重复的日子，在第一次发生的那个农历年之前不提。2027-11-11 是农历十月十四。"""
        self.go(date(2026, 11, 11))
        self.dates.add(CID, 'custom', '未来纪念日', date(2027, 11, 11), yearly=True, lunar=True)
        self.assertFalse(any(e.title == '未来纪念日' for _, events in self.dates.upcoming(CID) for e in events))
        self.go(date(2026, 11, 22)); self.assertNotIn('未来纪念日', [d.title for d in self.dates.events_on(CID)])
        self.go(date(2027, 11, 11)); self.assertIn('未来纪念日', [d.title for d in self.dates.events_on(CID)])


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

    def _special_today(self):
        from aicompanion.services.companion_dates import CompanionDates
        dates = CompanionDates(self.db)
        self.controller.relationships.owner_person()
        dates.set_met_on(CID, date.today() - timedelta(days=99))
        return dates

    def test_concurrent_turns_mention_once(self):
        """Codex PR #43 R4：桌面这一轮还在说（只出了第一段），主人 QQ 私聊同时来一轮：只有一轮带提醒；说完以后都不再带。"""
        from aicompanion.domain.cancellation import CancellationToken
        self._special_today()
        self.model.replies.append('第一百天啦')
        desktop = self.controller.generate('t-desk', self.character, CancellationToken(), '在吗')
        next(desktop)
        self.assertIn('特别的日子', self.model.prompts[-1])
        self.model.replies.append('在呢')
        self.ask('10001', '在吗')                                                                 # 主人 QQ 私聊（同一个 ChatController）
        self.assertIn('你们认识第 100 天了', self.model.prompts[-1])
        self.assertNotIn('特别的日子', self.model.prompts[-1])                                    # 被桌面这一轮占着
        list(desktop)
        self.model.replies.append('嗯')
        list(self.controller.generate('t-after', self.character, CancellationToken(), '还在吗'))
        self.assertNotIn('特别的日子', self.model.prompts[-1])                                    # 说完记成今天提过

    def test_cancelled_turn_releases_reminder(self):
        """取消 / 失败的一轮不算提过，放掉以后下一轮还能提。"""
        from aicompanion.domain.cancellation import CancellationToken
        self._special_today()
        self.model.replies.append('第一百天')
        desktop = self.controller.generate('t-cut', self.character, CancellationToken(), '在吗')
        next(desktop); desktop.close()                                                           # 用户打断
        self.model.replies.append('第一百天啦')
        list(self.controller.generate('t-next', self.character, CancellationToken(), '在吗'))
        self.assertIn('特别的日子', self.model.prompts[-1])
        self.model.replies.append('嗯')
        list(self.controller.generate('t-last', self.character, CancellationToken(), '还在吗'))
        self.assertNotIn('特别的日子', self.model.prompts[-1])

    def test_prompt_without_turn_has_background_only(self):
        """只看提示词、没有轮次的调用（context.build）不带提醒，免得提了没人记。"""
        self._special_today()
        _, prompt = self.controller.context.build(self.character, '在吗')
        self.assertIn('你们认识第 100 天了', prompt); self.assertNotIn('特别的日子', prompt)

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

    def test_toggle_keeps_entered_date(self):
        """Codex PR #43 R6：填了日期还没点添加，先切一下提醒开关，填的日期不能被改回今天。"""
        from PySide6.QtCore import QDate
        from aicompanion.ui.dates_card import DatesCard
        self.go(date(2026, 11, 11))
        card = DatesCard(self.db, CID, clock=lambda: self.now[0])
        try:
            card.kind.setCurrentIndex(card.kind.findData('birthday')); card.when.setDate(QDate(1999, 6, 9))
            card.met.setDate(QDate(2026, 1, 1))
            card.holidays.setChecked(not card.holidays.isChecked())
            self.assertEqual(card.when.date(), QDate(1999, 6, 9)); self.assertEqual(card.met.date(), QDate(2026, 1, 1))
            card.add()
            row = self.dates.user_dates(CID)[0]
            self.assertEqual((row['year'], row['month'], row['day']), (1999, 6, 9))
            self.assertEqual(card.when.date(), QDate(2026, 11, 11))                              # 添加完才重置
        finally:
            card.deleteLater()
