"""语气词挑选：表情标注优先，其次标点和心情；角色包可覆盖词表。"""
import random
import unittest
from pathlib import Path

from aicompanion.pet import interjection

PACK = Path(__file__).resolve().parents[1]/'src'/'aicompanion'/'characters'/'deepseek-whale'


class InterjectionTests(unittest.TestCase):
    def test_kind(self):
        self.assertEqual(interjection.kind_for('今天怎么样？', ['happy']), 'happy')
        self.assertEqual(interjection.kind_for('今天怎么样？', []), 'question')
        self.assertEqual(interjection.kind_for('好的，我来整理。', []), 'agree')
        self.assertEqual(interjection.kind_for('太棒了！', []), 'excited')
        self.assertEqual(interjection.kind_for('嗯，我在。', [], 'sad'), 'sad')
        self.assertEqual(interjection.kind_for('嗯，我在。', ['dizzy']), 'worried')

    def test_pack_table_and_avoid_repeat(self):
        table = interjection.load(PACK)
        self.assertIn('本鲸厉害吧', table['smug'])
        rng = random.Random(1)
        for _ in range(20):
            self.assertNotEqual(interjection.pick(table, 'happy', rng, avoid='嘿嘿'), '嘿嘿')

    def test_brief(self):
        self.assertEqual(interjection.brief_text('好呀。'), '好呀。')
        self.assertEqual(interjection.brief_text('这'*40), '')


if __name__ == '__main__': unittest.main()
