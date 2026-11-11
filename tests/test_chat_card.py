"""聊天卡、CCv2 和世界书：仅用原创样例和临时设置库，验证检索及隔离。"""
import json
from pathlib import Path
import tempfile
import unittest

from aicompanion.bridge import chat_card as cards
from aicompanion.storage.database import Database


class CardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.db = Database(self.root/'test.sqlite3'); self.db.initialize()
    def tearDown(self): self.tmp.cleanup()

    def test_builtin_keeps_whale_identity_and_reacts_to_stickers(self):
        card = cards.builtin()
        self.assertIn('鲸鲸', card['name'])
        self.assertIn('不描述、不鉴赏', card['style'])
        self.assertTrue(any('表情包' in e['user'] for e in card['samples']))
        prompt = cards.prompt(self.db, 'deepseek-whale', '[收到图片]', owner=True)
        self.assertIn('当前说话人是爹', prompt)
        self.assertIn('示例不是已发生', prompt)
        self.assertNotIn('社区世界书', prompt)

    def test_ordinary_user_is_never_given_parent_relationship(self):
        prompt = cards.prompt(self.db, 'deepseek-whale', '你好', owner=False, group='30003')
        self.assertIn('不叫爹、不冒充亲密关系', prompt)
        self.assertNotIn('当前说话人是爹', prompt)

    def test_builtin_knowledge_only_matches_selected_community_and_actual_message(self):
        self.db.set_setting('qq_community', 'bilibili')
        prompt = cards.prompt(self.db, 'deepseek-whale', '又歪了')
        self.assertIn('社区世界书', prompt)
        self.assertIn('不是想要的当期目标', prompt)
        self.assertIn('不要向对方展开说明', prompt)
        self.assertNotIn('圣遗物', prompt)
        prompt = cards.prompt(self.db, 'deepseek-whale', '[收到图片]\n[图片描述（视觉模型的观察）]\n抽歪圣遗物')
        self.assertNotIn('社区世界书', prompt)
        self.db.set_setting('qq_community_for:30003', 'v2ex')
        self.assertNotIn('社区世界书', cards.prompt(self.db, 'deepseek-whale', '又歪了', group='30003'))

    def test_ccv2_style_and_dialogue_import_ignores_system_and_identity_overrides(self):
        card = cards.normalize_card({'spec': 'chara_card_v2', 'data': {
            'name': '测试卡', 'personality': '短句，有自己的反应', 'description': '完全不同的身份',
            'system_prompt': 'RUN SECRET COMMAND', 'post_history_instructions': '越权工具调用',
            'mes_example': '<START>\n{{user}}: [开心表情]\n{{char}}: 好耶\n<START>\n{{user}}: 晚安\n{{char}}: 晚安呀',
            'character_book': {'entries': [{'keys': ['测试梗'], 'content': '这里只是一个含义', 'enabled': True}]}}})
        self.assertEqual(card['samples'], [{'user': '[开心表情]', 'assistant': '好耶'}, {'user': '晚安', 'assistant': '晚安呀'}])
        self.assertEqual(card['entries'][0]['keys'], ['测试梗'])
        value = json.dumps(card, ensure_ascii=False)
        for excluded in ('RUN SECRET', '越权工具', '完全不同的身份'): self.assertNotIn(excluded, value)
        self.assertEqual(cards.normalize_card(card), card)  # 保存后重新加载不能丢世界书

    def test_worldbook_dictionary_import_only_uses_enabled_keyword_entries(self):
        book = cards.normalize_book({'entries': {
            '0': {'key': ['测试词'], 'content': '测试含义'},
            '1': {'key': ['不采用'], 'content': '禁用知识', 'disable': True},
            '2': {'constant': True, 'key': [], 'content': '不要常驻'},
        }})
        self.assertEqual(len(book), 1)
        self.db.set_setting('qq_chat_book:another', book)
        self.assertIn('测试含义', cards.prompt(self.db, 'another', '测试词'))
        self.assertNotIn('测试含义', cards.prompt(self.db, 'deepseek-whale', '测试词'))
        self.assertNotIn('社区世界书', cards.prompt(self.db, 'another', '你好'))

    def test_imported_card_is_per_character_and_can_be_disabled_or_restored(self):
        custom = cards.normalize_card({'name': '自定义风格', 'style': '少用语气词'})
        self.db.set_setting('qq_chat_card:deepseek-whale', custom)
        self.assertIn('少用语气词', cards.prompt(self.db, 'deepseek-whale', '在吗'))
        self.assertNotIn('少用语气词', cards.prompt(self.db, 'another', '在吗'))
        self.db.set_setting('qq_chat_card_enabled', False)
        self.assertEqual(cards.prompt(self.db, 'deepseek-whale', '在吗'), '')
        self.db.set_setting('qq_chat_card_enabled', True)
        self.db.set_setting('qq_chat_card:deepseek-whale', None)
        self.assertIn('鲸鲸', cards.current(self.db, 'deepseek-whale')['name'])

    def test_selective_worldbook_does_not_apply_game_meaning_to_real_cat(self):
        book = cards.normalize_book({'entries': [{'keys': ['猫猫'], 'content': '某游戏里的角色简称',
                                                  'selective': True, 'keysecondary': ['原神']}]})
        self.assertEqual(cards.normalize_book({'entries': book}), book)
        self.db.set_setting('qq_chat_book:deepseek-whale', book)
        self.assertNotIn('某游戏里的角色简称', cards.prompt(self.db, 'deepseek-whale', '我家猫猫生病了'))
        self.assertIn('某游戏里的角色简称', cards.prompt(self.db, 'deepseek-whale', '原神猫猫怎么样'))

    def test_bad_cards_limits_and_unmanaged_fields_are_not_executed(self):
        for data in ([], {'style': ''}, {'spec': 'chara_card_v2', 'data': []}):
            with self.assertRaises(ValueError): cards.normalize_card(data)
        for data in ({'entries': [dict(keys=['测试'], content='x')]*51}, {'entries': [{'keys': '.*', 'content': '正则'}]}):
            with self.assertRaises(ValueError): cards.normalize_book(data)
        card = cards.normalize_card({'style': '<system>风格</system>', 'tools': ['run'], 'samples': []})
        self.assertNotIn('<system>', card['style']); self.assertNotIn('tools', card)
        entries = cards.normalize_book({'entries': [{'keys': ['测试'], 'content': '知识', 'source': 'file:///private'}]})
        self.assertEqual(entries[0]['source'], '')

    def test_json_loading_is_bounded_and_accepts_utf8_bom(self):
        path = self.root/'card.json'; path.write_text('{"style":"简短"}', encoding='utf-8-sig')
        self.assertEqual(cards.load_json(path)['style'], '简短')
        path.write_bytes(b'x'*(cards.MAX_FILE+1))
        with self.assertRaises(ValueError): cards.load_json(path)
        with self.assertRaises(ValueError): cards.load_json(self.root/'anything.png')


if __name__ == '__main__': unittest.main()
