"""模板只更新目标角色一次，不覆盖用户后续编辑或其他角色。"""
from dataclasses import replace
import unittest
from unittest.mock import Mock

from aicompanion.domain.character import DEFAULT_CHARACTER
from aicompanion.domain.personas import apply_emilia_preset, EMILIA_STYLE, EMILIA_JAPANESE_STYLE


class PersonaTests(unittest.TestCase):
    def setUp(self):
        self.settings = {}
        self.database = Mock()
        self.database.get_setting.side_effect = lambda key, default=None: self.settings.get(key, default)
        self.database.set_setting.side_effect = lambda key, value: self.settings.__setitem__(key, value)

    def test_default_role_gets_persona_but_keeps_model_and_voice(self):
        original = replace(DEFAULT_CHARACTER, model_path='custom.model3.json', voice='existing-voice')
        updated = apply_emilia_preset(self.database, original, True)
        self.assertEqual(updated.id, original.id)
        self.assertEqual((updated.model_path, updated.voice), ('custom.model3.json', 'existing-voice'))
        self.assertIn('非官方同人', updated.description)
        self.assertEqual(updated.speaking_style, EMILIA_STYLE)
        self.assertEqual(self.settings['persona_previous:emilia']['description'], original.description)
        self.assertEqual(self.settings['japanese_style:emilia'], EMILIA_JAPANESE_STYLE)

    def test_disabled_preset_does_not_touch_database(self):
        self.assertIs(apply_emilia_preset(self.database, DEFAULT_CHARACTER, False), DEFAULT_CHARACTER)
        self.database.update_character.assert_not_called()
        self.database.set_setting.assert_not_called()

    def test_other_role_is_not_replaced(self):
        other = replace(DEFAULT_CHARACTER, id='another', name='Other')
        self.assertIs(apply_emilia_preset(self.database, other, True), other)
        self.database.update_character.assert_not_called()

    def test_later_custom_edits_are_respected(self):
        apply_emilia_preset(self.database, DEFAULT_CHARACTER, True)
        custom = replace(DEFAULT_CHARACTER, description='我的自定义设定')
        self.database.update_character.reset_mock()
        self.assertIs(apply_emilia_preset(self.database, custom, True), custom)
        self.database.update_character.assert_not_called()


if __name__ == '__main__':
    unittest.main()
