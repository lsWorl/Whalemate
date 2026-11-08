"""鲸娘人设 v4（2026-11-08）：允许 emoji、回复长短随内容；从 v2 / v3 原文都能升级，用户改过的不动。"""
import json, tempfile, unittest
from pathlib import Path
from aicompanion.characters import discover
from aicompanion.characters.registry import CharacterRegistry
from aicompanion.domain.character import CharacterProfile
from aicompanion.storage.database import Database

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT/'src/aicompanion/characters/deepseek-whale/character.json'


class PersonaUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.db = Database(Path(self.temp.name)/'p.sqlite3'); self.db.initialize()
        self.persona = json.loads(PACK.read_text(encoding='utf-8'))['persona']
        self.pack = next(p for p in discover()[0] if p.id == 'deepseek-whale')
    def tearDown(self): self.temp.cleanup()

    def start_from(self, description, style, version):
        self.db.add_character(CharacterProfile(self.pack.name, description, style, 'deepseek-whale'))
        self.db.set_setting('persona_version:deepseek-whale', version)
        CharacterRegistry(self.db, None, [self.pack], environ={}).sync()
        return next(c for c in self.db.characters() if c.id == 'deepseek-whale')

    def test_v4_allows_emoji_and_longer_replies(self):
        style = self.persona['speaking_style']
        self.assertEqual(self.persona['version'], 4)
        self.assertIn('可以用 emoji', style); self.assertNotIn('emoji、状态数值', style)
        self.assertIn('连着说好几句', style); self.assertNotIn('一般一两句', style)

    def test_upgrades_from_v3_and_v2_originals(self):
        current = self.start_from(self.persona['previous_description'], self.persona['previous_speaking_style'], 3)
        self.assertEqual(current.speaking_style, self.persona['speaking_style'])
        self.temp.cleanup(); self.setUp()
        old = self.persona['older'][0]
        current = self.start_from(old['description'], old['speaking_style'], 2)
        self.assertEqual(current.speaking_style, self.persona['speaking_style'])
        self.assertEqual(self.db.get_setting('persona_backup:deepseek-whale')['speaking_style'], old['speaking_style'])

    def test_user_edited_persona_is_kept(self):
        current = self.start_from('我自己写的人设', '我自己写的说话风格', 3)
        self.assertEqual(current.speaking_style, '我自己写的说话风格')
        self.assertEqual(self.db.get_setting('persona_version:deepseek-whale'), 4)


if __name__ == '__main__':
    unittest.main()
