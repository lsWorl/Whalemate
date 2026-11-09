"""多段回复拆成几条消息（桌面几个气泡 / QQ 几条消息）的规则。"""
import unittest
from aicompanion.expression.segments import split_segments, MAX_SEGMENTS


class SegmentTests(unittest.TestCase):
    def test_every_line_is_a_message(self):
        # 用户 2026-11-09：像发微信一样，每一行单独一条（单个换行也拆）
        self.assertEqual(split_segments('哇！\n\n真的假的\n一箱啊\n\n\n分我一半'), ['哇！', '真的假的', '一箱啊', '分我一半'])

    def test_no_commas_or_full_stops(self):
        # "。"处另起一条，"，"换成空格，句末句号去掉；问号、感叹号、～、…、emoji 保留
        self.assertEqual(split_segments('好耶，冰奶茶！我先喝一半。剩下的给爹～'), ['好耶 冰奶茶！我先喝一半', '剩下的给爹～'])
        self.assertEqual(split_segments('啊？真的吗，那我也要😋'), ['啊？真的吗 那我也要😋'])
        self.assertEqual(split_segments('无语。。。'), ['无语…'])
        self.assertEqual(split_segments('行吧。'), ['行吧'])

    def test_quotes_are_not_split(self):
        self.assertEqual(split_segments('他说“好的。我知道了，马上来”然后就跑了。'), ['他说“好的。我知道了，马上来”然后就跑了'])
        self.assertEqual(split_segments('《你好，再见》挺好看'), ['《你好，再见》挺好看'])

    def test_empty_and_whitespace(self):
        self.assertEqual(split_segments(''), []); self.assertEqual(split_segments('\n\n  \n'), [])
        self.assertEqual(split_segments('\n\n哈哈\n\n'), ['哈哈'])

    def test_too_many_segments_are_merged_into_the_last(self):
        parts = split_segments('\n\n'.join(str(n) for n in range(9)))
        self.assertEqual(len(parts), MAX_SEGMENTS); self.assertEqual(parts[-1], '5\n6\n7\n8')

    def test_long_form_replies_are_not_split(self):
        for text in ('写好了：\n\n```python\nprint(1)\n\nprint(2)\n```\n\n就这样',
                     '步骤：\n\n1. git add\n\n2. git rebase --continue',
                     '> 尊敬的领导：\n\n> 您好',
                     '很长的讲解。'*120+'\n\n第二段'):
            self.assertEqual(split_segments(text), [text.strip()])   # 文档类回复标点也原样保留

    def test_crlf(self):
        self.assertEqual(split_segments('一\r\n\r\n二'), ['一', '二'])


if __name__ == '__main__':
    unittest.main()
