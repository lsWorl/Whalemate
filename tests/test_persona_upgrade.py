"""鲸鲸人设：v5（2026-11-09）v4 的 emoji、长短随内容，加上"不用逗号句号、一句一行"；v6（2026-11-11）改名鲸鲸，说话风格不变。
从 v2～v5 原文都能升级，用户改过的不动。"""
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
        # 数据库初始化已写入同 ID 的占位角色，这里把它改成要模拟的旧版人设
        self.db.update_character(CharacterProfile(self.pack.name, description, style, 'deepseek-whale'))
        self.db.set_setting('persona_version:deepseek-whale', version)
        CharacterRegistry(self.db, None, [self.pack], environ={}).sync()
        return next(c for c in self.db.characters() if c.id == 'deepseek-whale')

    def test_v5_keeps_v4_and_drops_commas_and_full_stops(self):
        style = self.persona['speaking_style']
        self.assertEqual(self.persona['version'], 6)
        self.assertEqual(style, self.persona['previous_speaking_style'])                          # v6 只改名字，风格同 v5
        self.assertTrue(self.persona['description'].startswith('你是“鲸鲸”'))
        self.assertIn('不用逗号和句号', style); self.assertIn('换行另起一条', style)
        self.assertIn('可以用 emoji', style); self.assertNotIn('emoji、状态数值', style)
        self.assertIn('连着说好几句', style); self.assertNotIn('一般一两句', style)

    def test_upgrades_from_v4_v3_and_v2_originals(self):
        current = self.start_from(self.persona['previous_description'], self.persona['previous_speaking_style'], 5)
        self.assertEqual((current.description, current.speaking_style), (self.persona['description'], self.persona['speaking_style']))
        self.assertEqual(len(self.persona['older']), 3)
        for version, old in zip((4, 3, 2), self.persona['older']):
            self.temp.cleanup(); self.setUp()
            current = self.start_from(old['description'], old['speaking_style'], version)
            self.assertEqual((current.description, current.speaking_style), (self.persona['description'], self.persona['speaking_style']))
            self.assertEqual(self.db.get_setting('persona_backup:deepseek-whale')['speaking_style'], old['speaking_style'])

    def test_user_edited_persona_is_kept(self):
        current = self.start_from('我自己写的人设', '我自己写的说话风格', 3)
        self.assertEqual(current.speaking_style, '我自己写的说话风格')
        self.assertEqual(self.db.get_setting('persona_version:deepseek-whale'), 6)

    def test_pack_rename_updates_untouched_name_once(self):
        # 负责人 2026-11-11：鲸娘改名鲸鲸。库里还是包的旧名字就换；用户自己起的名字不动；换过一次后用户改回旧名也不再换
        self.assertEqual((self.pack.name, self.pack.previous_names), ('鲸鲸', ('鲸娘',)))
        current = self.start_from(self.persona['description'], self.persona['speaking_style'], 6)
        self.db.update_character(CharacterProfile('鲸娘', current.description, current.speaking_style, 'deepseek-whale'))
        self.db.set_setting('pack_name:deepseek-whale', None)
        CharacterRegistry(self.db, None, [self.pack], environ={}).sync()
        name = lambda: next(c for c in self.db.characters() if c.id == 'deepseek-whale').name
        self.assertEqual(name(), '鲸鲸')
        self.db.update_character(CharacterProfile('鲸娘', current.description, current.speaking_style, 'deepseek-whale'))
        CharacterRegistry(self.db, None, [self.pack], environ={}).sync()
        self.assertEqual(name(), '鲸娘')                                                         # 用户改回来了，尊重
        self.temp.cleanup(); self.setUp()
        self.start_from(self.persona['description'], self.persona['speaking_style'], 6)
        self.db.update_character(CharacterProfile('小蓝', current.description, current.speaking_style, 'deepseek-whale'))
        self.db.set_setting('pack_name:deepseek-whale', None)
        CharacterRegistry(self.db, None, [self.pack], environ={}).sync()
        self.assertEqual(name(), '小蓝')                                                         # 用户起的名字不动

    def test_persona_enabled_library_moves_to_jingjing(self):
        # 已经启用人格包的库（负责人现在的情况）：上次启动时包还是 v5、名字鲸娘；这次启动后名字和内置人格都换成鲸鲸
        from dataclasses import replace
        from aicompanion.services.persona_service import PersonaService
        old = dict(self.persona, version=5, description=self.persona['previous_description'], speaking_style=self.persona['previous_speaking_style'])
        v5 = replace(self.pack, name='鲸娘', previous_names=(), persona=old)
        self.db.update_character(CharacterProfile('鲸娘', old['description'], old['speaking_style'], 'deepseek-whale'))
        CharacterRegistry(self.db, None, [v5], environ={}).sync()
        CharacterRegistry(self.db, None, [self.pack], environ={}).sync()
        current = next(c for c in self.db.characters() if c.id == 'deepseek-whale')
        self.assertEqual(current.name, '鲸鲸')
        self.assertTrue(current.description.startswith('你是“鲸鲸”'))
        active = PersonaService(self.db).active('deepseek-whale')
        self.assertTrue(active.builtin); self.assertEqual(active.pack_version, 6)


if __name__ == '__main__':
    unittest.main()
