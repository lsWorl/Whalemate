"""多段回复拆成几条消息（桌面几个气泡 / QQ 几条消息）的规则。"""
import unittest
from aicompanion.expression.segments import split_segments, MAX_SEGMENTS


class SegmentTests(unittest.TestCase):
    def test_blank_lines_split_single_newlines_stay(self):
        self.assertEqual(split_segments('哇！\n\n真的假的\n一箱啊\n\n\n分我一半'), ['哇！', '真的假的\n一箱啊', '分我一半'])

    def test_empty_and_whitespace(self):
        self.assertEqual(split_segments(''), []); self.assertEqual(split_segments('\n\n  \n'), [])
        self.assertEqual(split_segments('\n\n哈哈\n\n'), ['哈哈'])

    def test_too_many_segments_are_merged_into_the_last(self):
        parts = split_segments('\n\n'.join(str(n) for n in range(8)))
        self.assertEqual(len(parts), MAX_SEGMENTS); self.assertEqual(parts[-1], '4\n\n5\n\n6\n\n7')

    def test_long_form_replies_are_not_split(self):
        for text in ('写好了：\n\n```python\nprint(1)\n\nprint(2)\n```\n\n就这样',
                     '步骤：\n\n1. git add\n\n2. git rebase --continue',
                     '> 尊敬的领导：\n\n> 您好',
                     '很长的讲解。'*120+'\n\n第二段'):
            self.assertEqual(split_segments(text), [text.strip()])

    def test_crlf(self):
        self.assertEqual(split_segments('一\r\n\r\n二'), ['一', '二'])


if __name__ == '__main__':
    unittest.main()
