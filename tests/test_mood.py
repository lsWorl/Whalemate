"""计划书 v4 功能三：持续情绪。事件推动、按半衰期回落、人格情绪倾向、来源（mind / 用户消息 / 久别 / 办成事）、
进提示词、和好感分开、手动哄好。用可控时钟，不联网。"""
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from aicompanion.domain.mood import STRONG_AT, Mood, apply, describe
from aicompanion.services.mood_service import MoodService
from aicompanion.storage.database import Database

HOUR = 3600.0


class MoodMathTests(unittest.TestCase):
    def test_events_push_and_time_brings_back(self):
        mood = apply(Mood(), 'insulted', 0.0)
        self.assertEqual(mood.label(), 'angry')
        self.assertEqual(mood.decayed(4*HOUR).levels['angry'], round(mood.levels['angry']/2, 1))   # 生气半衰期 4 小时
        self.assertEqual(mood.decayed(48*HOUR).label(), 'calm')
        calmer = apply(mood, 'apology', 1*HOUR)
        self.assertLess(calmer.levels.get('angry', 0), mood.decayed(1*HOUR).levels['angry'])      # 道歉会消气，但不是一下清零
        self.assertGreater(calmer.levels.get('angry', 0), 0)

    def test_persona_temper_changes_how_upset_and_how_fast(self):
        touchy = apply(Mood(), 'teased', 0.0, touchy=1.0)
        calm = apply(Mood(), 'teased', 0.0, touchy=0.0)
        self.assertGreater(touchy.levels['angry'], 2*calm.levels['angry'])
        slow = apply(Mood(), 'insulted', 0.0, forgiving=0.0).decayed(4*HOUR, forgiving=0.0)
        fast = apply(Mood(), 'insulted', 0.0, forgiving=1.0).decayed(4*HOUR, forgiving=1.0)
        self.assertGreater(slow.levels['angry'], fast.levels['angry'])

    def test_describe_keeps_help_and_safety(self):
        strong = Mood({'angry': STRONG_AT+5}, 0.0)
        text = describe(strong, '被骂了')
        self.assertIn('很生气', text); self.assertIn('因为被骂了', text); self.assertIn('不太想说话', text)
        self.assertIn('办事或涉及安全的事照常认真回应', text)
        self.assertEqual(describe(Mood()), '当前心情：平静。')


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name)/'m.sqlite3'); self.db.initialize()
        self.now = [time.time()]   # 提示词那一项用真实时钟
        self.mood = MoodService(self.db, clock=lambda: self.now[0])
        self.cid = 'deepseek-whale'

    def tearDown(self): self.tmp.cleanup()

    def test_mood_persists_and_syncs_label_for_avatar_and_voice(self):
        self.mood.event(self.cid, 'insulted', reason='说她笨')
        self.assertEqual(self.db.load_emotion(self.cid).mood, 'angry')
        self.now[0] += 2*HOUR
        self.assertEqual(MoodService(self.db, clock=lambda: self.now[0]).current(self.cid).label(), 'angry')   # 跨实例、隔一会儿还在
        self.assertEqual(self.mood.reason(self.cid), '说她笨')
        self.now[0] += 30*HOUR
        self.assertEqual(self.mood.current(self.cid).label(), 'calm')
        self.assertEqual(self.db.load_emotion(self.cid).mood, 'calm')                                     # 回落后同步

    def test_mind_mapping_respects_target_and_quotes(self):
        self.assertIsNone(self.mood.on_mind(self.cid, {'voice': 'quote', 'tone': 'attack', 'toward': -3}))
        self.assertIsNone(self.mood.on_mind(self.cid, {'at': 'other', 'tone': 'attack'}))
        self.assertEqual(self.mood.on_mind(self.cid, {'at': 'me', 'tone': 'attack', 'toward': -3, 'feel': '生气'}), 'insulted')
        self.assertEqual(self.mood.current(self.cid).label(), 'angry')
        self.assertEqual(self.mood.on_mind(self.cid, {'toward': 2, 'feel': '害羞'}), 'affection')
        group = MoodService(self.db, clock=lambda: self.now[0])
        before = group.current('custom').levels.get('angry', 0)
        group.on_mind('custom', {'tone': 'attack', 'toward': -3}, 'qq:group:1')
        desk = MoodService(self.db, clock=lambda: self.now[0]); desk.on_mind('custom2', {'tone': 'attack', 'toward': -3}, 'desktop')
        self.assertLess(group.current('custom').levels['angry']-before, desk.current('custom2').levels['angry'])   # 群里打折扣

    def test_user_message_fallback_and_reunion(self):
        from aicompanion.services.emotion_service import EmotionService
        emotions = EmotionService(self.db, clock=lambda: self.now[0])
        emotions.update(self.cid, '抱抱')
        self.assertEqual(self.mood.current(self.cid).label(), 'shy')
        self.now[0] += 3*24*HOUR
        emotions.update(self.cid, '我回来了')
        events = [e['kind'] for e in self.mood.recent(self.cid)]
        self.assertEqual(events[0], 'reunion'); self.assertIn('3 天', self.mood.recent(self.cid)[0]['reason'])
        self.assertGreater(self.mood.current(self.cid).levels.get('sad', 0), 0)                     # 有点委屈，但不指责
        emotions.update(self.cid, '还在吗', 'qq:user:1')                                            # QQ 消息不算桌面久别
        self.assertEqual([e['kind'] for e in self.mood.recent(self.cid)][0], 'reunion')

    def test_mood_does_not_touch_relationship_scores(self):
        before = self.db.relationship(self.cid)
        for _ in range(5): self.mood.event(self.cid, 'insulted')
        self.assertEqual(self.db.relationship(self.cid), before)

    def test_soothe_clears_negative_only(self):
        self.mood.event(self.cid, 'insulted'); self.mood.event(self.cid, 'praised')
        mood = self.mood.soothe(self.cid)
        self.assertNotIn('angry', mood.levels); self.assertIn('happy', mood.levels)
        self.assertEqual(self.mood.recent(self.cid)[0]['kind'], 'soothed')

    def test_persona_temper_is_used(self):
        from aicompanion.services.persona_service import PersonaService
        personas = PersonaService(self.db)
        character = next(c for c in self.db.characters() if c.id == self.cid)
        personas.ensure(character)
        touchy = personas.create(self.cid, {'title': '暴躁', 'description': 'd', 'speaking_style': 's', 'temper': {'touchy': 1.0, 'forgiving': 0.0}})
        personas.switch(self.cid, touchy.id)
        self.assertEqual(self.mood.temper(self.cid), (1.0, 0.0))

    def test_prompt_mentions_mood_and_reason(self):
        from aicompanion.services.context_service import ContextService
        from aicompanion.services.memory_service import MemoryService
        self.mood.event(self.cid, 'ignored', reason='分享没人理')
        character = next(c for c in self.db.characters() if c.id == self.cid)
        _, prompt = ContextService(self.db, MemoryService(self.db)).build(character, '在吗')
        self.assertRegex(prompt, r'当前心情：(有点|很)委屈（因为分享没人理）')
        self.assertIn('精力：', prompt)

    def test_old_reset_behaviour_is_gone(self):
        from aicompanion.services.emotion_service import EmotionService
        emotions = EmotionService(self.db, clock=lambda: self.now[0])
        self.mood.event(self.cid, 'insulted')
        emotions.update(self.cid, '今天天气不错')                                                     # 普通的一句话不再把心情清回平静
        self.assertEqual(self.db.load_emotion(self.cid).mood, 'angry')


try:
    from PySide6.QtWidgets import QApplication
except ImportError:   # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, '需要 PySide6')
class CardTests(ServiceTests):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def test_card_shows_reason_and_soothes(self):
        from aicompanion.ui.mood_card import MoodCard
        self.mood.event(self.cid, 'insulted', reason='说她笨')
        card = MoodCard(self.db, self.cid, clock=lambda: self.now[0])
        try:
            self.assertIn('生气', card.summary.text()); self.assertIn('说她笨', card.summary.text())
            self.assertGreater(card.bars['angry'].value(), 0); self.assertTrue(card.soothe_button.isEnabled())
            card.soothe(); self.assertIn('平静', card.summary.text()); self.assertFalse(card.soothe_button.isEnabled())
        finally:
            card.deleteLater()


if __name__ == '__main__':
    unittest.main()
