"""鲸娘人设 v5（2026-11-09）：v4 的 emoji、长短随内容，加上"不用逗号句号、一句一行"；从 v2 / v3 / v4 原文都能升级，用户改过的不动。"""
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

    def test_v5_keeps_v4_and_drops_commas_and_full_stops(self):
        style = self.persona['speaking_style']
        self.assertEqual(self.persona['version'], 5)
        self.assertIn('不用逗号和句号', style); self.assertIn('换行另起一条', style)
        self.assertIn('可以用 emoji', style); self.assertNotIn('emoji、状态数值', style)
        self.assertIn('连着说好几句', style); self.assertNotIn('一般一两句', style)

    def test_upgrades_from_v4_v3_and_v2_originals(self):
        current = self.start_from(self.persona['previous_description'], self.persona['previous_speaking_style'], 4)
        self.assertEqual(current.speaking_style, self.persona['speaking_style'])
        for version, old in zip((3, 2), self.persona['older']):
            self.temp.cleanup(); self.setUp()
            current = self.start_from(old['description'], old['speaking_style'], version)
            self.assertEqual(current.speaking_style, self.persona['speaking_style'])
            self.assertEqual(self.db.get_setting('persona_backup:deepseek-whale')['speaking_style'], old['speaking_style'])

    def test_user_edited_persona_is_kept(self):
        current = self.start_from('我自己写的人设', '我自己写的说话风格', 3)
        self.assertEqual(current.speaking_style, '我自己写的说话风格')
        self.assertEqual(self.db.get_setting('persona_version:deepseek-whale'), 5)


if __name__ == '__main__':
    unittest.main()
