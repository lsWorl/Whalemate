"""计划书 v4 功能四：她自己的生活。按兴趣去看（有真实来源才算做过）、形成看法、值得说的交给调度器分享、你回了她开心一点。
搜索和模型都用替身，不联网。"""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from aicompanion.brain.life import DEFAULT_INTERESTS, LifeService
from aicompanion.brain.scheduler import ProactiveScheduler
from aicompanion.community.search import Hit
from aicompanion.storage.database import Database

CID = 'deepseek-whale'
HOUR = 3600


class FakeSearch:
    def __init__(self, hits): self.hits, self.queries = hits, []
    def search(self, query, count=5): self.queries.append(query); return list(self.hits)


class FakeClient:
    def __init__(self, reply): self.reply, self.prompts = reply, []
    def generate_reply_stream(self, history, prompt, cancellation=None):
        self.prompts.append((history[-1].content, prompt)); yield self.reply


HIT = Hit('抹香鲸会用“方言”交流', 'https://example.org/whale', '研究人员发现不同海域的抹香鲸群有各自的咔嗒声节奏，像方言一样代代相传。' * 2, '科普网')


class LifeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name)/'l.sqlite3'); self.db.initialize()
        self.db.set_setting('quiet_start', 23); self.db.set_setting('quiet_end', 8)
        self.now = [datetime(2026, 11, 12, 14, 0).timestamp()]
        self.life = LifeService(self.db, clock=lambda: self.now[0])

    def tearDown(self): self.tmp.cleanup()

    def row(self, activity_id):
        return next(r for r in self.life.recent(CID, 50) if r['id'] == activity_id)

    def test_reads_real_source_and_forms_a_view(self):
        client = FakeClient('{"thought":"原来鲸鱼也有口音，好想听听本鲸的","worth":0.8,"opener":"你知道鲸鱼也有方言吗"}')
        search = FakeSearch([HIT])
        aid = self.life.run(CID, search, client, character_name='鲸鲸')
        row = self.row(aid)
        self.assertEqual((row['status'], row['title'], row['url'], row['site']), ('done', HIT.title, HIT.url, '科普网'))
        self.assertIn('口音', row['thought']); self.assertAlmostEqual(row['worth'], 0.8)
        self.assertIn(row['interest'], DEFAULT_INTERESTS); self.assertEqual(search.queries, [row['interest']])
        self.assertIn('不要补充摘要里没有的事实', client.prompts[0][1])
        self.assertEqual(self.life.shareable(CID)['id'], aid)
        intent = LifeService.intent(self.life.shareable(CID))
        self.assertIn('科普网', intent); self.assertIn(HIT.title, intent); self.assertIn('不要编造细节', intent)

    def test_nothing_found_is_not_done_and_no_invention(self):
        aid = self.life.run(CID, FakeSearch([]), FakeClient('{}'))
        self.assertEqual(self.row(aid)['status'], 'failed'); self.assertIsNone(self.life.shareable(CID))
        aid = self.life.run(CID, FakeSearch([Hit('短', 'https://x.org/a', '太短', '')]), FakeClient('{}'))   # 没有正文摘要
        self.assertEqual(self.row(aid)['status'], 'failed')

    def test_no_model_means_low_worth_and_same_url_not_twice(self):
        aid = self.life.run(CID, FakeSearch([HIT]), None)
        self.assertEqual(self.row(aid)['status'], 'done'); self.assertIsNone(self.life.shareable(CID))   # 没有自己的看法就不拿来说
        again = self.life.run(CID, FakeSearch([HIT]), None)
        self.assertEqual(self.row(again)['status'], 'failed')                                           # 30 天内看过的不再算

    def test_when_she_goes(self):
        self.assertTrue(self.life.should_start(CID))
        self.life.run(CID, FakeSearch([HIT]), None)
        self.assertFalse(self.life.should_start(CID))                                  # 两件之间至少隔 2 小时
        self.now[0] += 2*HOUR; self.assertTrue(self.life.should_start(CID))
        self.db.set_setting('life_daily_activities', 1); self.assertFalse(self.life.should_start(CID))   # 今天做够了
        self.db.set_setting('life_daily_activities', 3)
        self.now[0] = datetime(2026, 11, 12, 23, 30).timestamp(); self.assertFalse(self.life.should_start(CID))   # 安静时段
        self.now[0] = datetime(2026, 11, 13, 10, 0).timestamp(); self.db.set_setting('life_enabled', False)
        self.assertFalse(self.life.should_start(CID))

    def test_persona_interests_and_rotation(self):
        from aicompanion.services.persona_service import PersonaService
        service = PersonaService(self.db)
        character = next(c for c in self.db.characters() if c.id == CID); service.ensure(character)
        mine = service.create(CID, {'title': '爱猫', 'description': 'd', 'speaking_style': 's', 'interests': ['猫', '咖啡']})
        service.switch(CID, mine.id)
        first = self.life.pick_interest(CID); self.assertIn(first, ('猫', '咖啡'))
        self.life.run(CID, FakeSearch([]), None)
        picked = {self.life.pick_interest(CID) for _ in range(10)}
        self.assertEqual(len(picked), 1)                                               # 最近看过的往后排


class ShareLoopTests(LifeTests):
    """分享经过统一调度器：他在电脑前才来说；说出去记 shared；他回了 → answered、她开心一点。"""

    def say(self, role, text):
        stamp = datetime.fromtimestamp(self.now[0], timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        with self.db.transaction() as c:
            c.execute("INSERT INTO chat_messages(role, content, created_at, character_id, status, channel) VALUES (?,?,?,?, 'complete', 'desktop')",
                      (role, text, stamp, CID))

    def test_share_answer_updates_activity_and_mood(self):
        aid = self.life.run(CID, FakeSearch([HIT]), FakeClient('{"thought":"好可爱","worth":0.9,"opener":"给你说个好玩的"}'))
        scheduler = ProactiveScheduler(self.db, clock=lambda: self.now[0], rng=lambda: 0.0, life=self.life)
        self.assertIsNone(scheduler.next(CID, 1200, 9999))                            # 他不在电脑前
        share = scheduler.next(CID, 30, 9999)
        self.assertEqual(share.source, 'share'); self.assertEqual(share.payload['activity']['id'], aid)
        self.say('assistant', '给你说个好玩的'); scheduler.record(CID, share)
        self.assertEqual(self.row(aid)['status'], 'shared'); self.assertIsNone(self.life.shareable(CID))
        self.now[0] += 600; self.say('user', '哈哈真的假的'); scheduler.settle(CID)
        self.assertEqual(self.row(aid)['response'], 'answered')
        from aicompanion.services.mood_service import MoodService
        events = MoodService(self.db, clock=lambda: self.now[0]).recent(CID)
        self.assertEqual(events[0]['kind'], 'praised'); self.assertIn('接住', events[0]['reason'])


try:
    from PySide6.QtWidgets import QApplication
except ImportError:   # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, '需要 PySide6')
class CardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def test_card_lists_activities_and_saves(self):
        from aicompanion.ui.life_card import LifeCard
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        db = Database(Path(tmp.name)/'c.sqlite3'); db.initialize()
        LifeService(db).run(CID, FakeSearch([HIT]), FakeClient('{"thought":"<b>好玩</b>","worth":0.9}'))
        card = LifeCard(db, CID)
        try:
            self.assertIn('https://example.org/whale', card.recent.text()); self.assertIn('&lt;b&gt;', card.recent.text())   # 转义，不当成 HTML
            card.enabled.setChecked(False); card.daily.setValue(1); card.save()
            self.assertFalse(db.get_setting('life_enabled')); self.assertEqual(db.get_setting('life_daily_activities'), 1)
        finally:
            card.deleteLater()


if __name__ == '__main__':
    unittest.main()
