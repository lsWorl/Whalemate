"""代码/引用展示和实际控制标记经过控制器表达解析与桌宠动作解析。"""
import unittest

from aicompanion.expression.stream import ExpressionStream
from aicompanion.pet.script import Cue, CueStream, Vocab

ASSET = 'st_0123456789abcdef'
VOCAB = Vocab({'开心': 'happy'}, {'挥手': 'wave'})
ANIM = '【开心】<挥手>'
TASK = '<task>整理文件</task>'
STICKER = '<sticker id="s1"/>'
MIND = '<mind feel="开心"/>'
BODY = ANIM + TASK + STICKER + MIND
TICK = '`'


class MarkdownCueTests(unittest.TestCase):
    def render(self, chunks):
        expression = ExpressionStream([ASSET], handles={'s1': ASSET}, cue_words={'开心', '挥手'})
        actions = CueStream(VOCAB)
        visible, cues = [], []

        def display(text):
            part, seen = actions.feed(text)
            visible.append(part)
            cues.extend(seen)

        for chunk in chunks:
            display(expression.feed(chunk))
        display(expression.finish())
        part, seen = actions.flush()
        return ''.join(visible) + part, cues + seen, expression

    def test_literal_spans_preserve_all_markers_and_resume_after_real_closer(self):
        fences = TICK * 3
        four = TICK * 4
        cases = {
            '波浪围栏': '~~~xml\n' + BODY + '\n~~~',
            '内层围栏较短': four + 'markdown\n' + fences + 'xml\n' + BODY + '\n' + fences + '\n' + four,
            '围栏种类必须一致': '~~~\n' + fences + '\n' + BODY + '\n~~~',
            '围栏结尾只能有空白': '   ~~~xml\r\n~~~ 后面仍有字\r\n' + BODY + '\r\n   ~~~~ \t',
            '四空格缩进': '展示：\n\n    ' + BODY,
            '制表符缩进': '展示：\n\n \t' + BODY,
            '列表里的围栏': '1. 示例\n    ~~~xml\n    ' + BODY + '\n    ~~~',
            '序号与围栏同一行': '1. ~~~xml\n    ' + BODY + '\n   ~~~',
            '项目符与围栏同一行': '- ' + fences + 'xml\n  ' + BODY + '\n  ' + fences,
            '普通引用': '原文：\n  > ' + BODY,
            '全角引用': '原文：\n＞ ' + BODY,
            '引用行内的分隔符': '> ' + TICK + '引文 ' + BODY,
            '跨行代码片段': '它写的是 ' + TICK * 2 + '第一行\n' + BODY + TICK * 2,
            '跨行单反引号': '它写的是 ' + TICK + '第一行\r\n' + BODY + TICK,
            '新列表项第一段的行内代码': '说明 ' + TICK + '\n1. ' + TICK + BODY + TICK,
            '转义相邻反引号中的第一个': '说明 ' + TICK * 4 + ' 落单，' + '\\' + TICK * 2 + BODY + TICK,
            '不同长度行内分隔': '参见 ' + TICK * 2 + '内含 ' + TICK + BODY + TICK * 3 + ' 字' + TICK * 2,
            '行中三反引号': '示例 ' + fences + BODY + fences,
            '行首三反引号同一行闭合': fences + BODY + fences,
            '反斜线转义的反引号': '符号 \\' + TICK + '，代码 ' + TICK + BODY + TICK,
            '普通比较符紧接行内代码': '符号<' + TICK + BODY + TICK,
        }
        for case, region in cases.items():
            source = region + '\n正文' + BODY
            for step in (1, 2, 3, 4, 13, len(source)):
                with self.subTest(case=case, step=step):
                    text, cues, expression = self.render(source[i:i + step] for i in range(0, len(source), step))
                    self.assertEqual(text, region + '\n正文')
                    self.assertEqual(cues, [Cue('expression', 'happy'), Cue('motion', 'wave'), Cue('task', '整理文件')])
                    self.assertEqual(expression.markers, 1)
                    self.assertEqual(expression.selection, (ASSET, 'after_text'))
                    self.assertEqual(expression.mind['feel'], '开心')

    def test_unclosed_and_invalid_fence_closers_do_not_execute(self):
        for region in ('~~~未闭合\n' + BODY, '~~~~\n~~~\n' + BODY,
                       '~~~\n~~~后面有字\n' + BODY):
            for cut in range(len(region) + 1):
                with self.subTest(region=region, cut=cut):
                    text, cues, expr = self.render((region[:cut], region[cut:]))
                    self.assertEqual((text, cues, expr.selection, expr.mind), (region, [], None, None))

    def assert_active_after(self, prefix):
        """前缀的字面内容原样展示；后接的角色标记只执行一次，气泡内只剩正文。"""
        source = prefix + ANIM + '好啦' + TASK + STICKER + MIND
        expected_cues = [Cue('expression', 'happy'), Cue('motion', 'wave'), Cue('task', '整理文件')]
        for step in (1, 2, 7, len(source)):
            with self.subTest(prefix=prefix, step=step):
                text, cues, expr = self.render(source[i:i + step] for i in range(0, len(source), step))
                self.assertEqual(text, prefix + '好啦')
                self.assertEqual(cues, expected_cues)
                self.assertEqual(expr.selection, (ASSET, 'after_text'))
                self.assertEqual(expr.markers, 1)
                self.assertEqual(expr.mind['feel'], '开心')

    def test_unpaired_backticks_are_text_and_leave_markers_active(self):
        original = '用 ' + TICK + ' 号分隔就行。\n'
        self.assertEqual(CueStream(VOCAB).feed(original + '【开心】好啦', final=True),
                         (original + '好啦', [Cue('expression', 'happy')]))
        for prefix in (original, original.replace('\n', '\r\n'), '同一行的 ' + TICK + ' 字符。 ',
                       '示例 ' + TICK * 2 + '多行\n' + TICK,
                       '用 ' + TICK + '。\n~~~xml\n' + BODY + '\n~~~\n',
                       '落单 ' + TICK + ' 后仍可展示 ' + TICK * 2 + BODY + TICK * 2 + '。'):
            self.assert_active_after(prefix)

    def stream_steps(self, chunks, through_expression, step, vocab=VOCAB):
        """记录每次喂入前端后可见的增量，最后单列回复结束时的输出。"""
        expression = None
        if through_expression:
            words = vocab.expressions.keys() | vocab.motions.keys()
            expression = ExpressionStream([ASSET], handles={'s1': ASSET}, cue_words=words)
        actions = CueStream(vocab)
        for chunk in chunks:
            shown, events = [], []
            for i in range(0, len(chunk), step):
                token = chunk[i:i + step]
                text, cues = actions.feed(expression.feed(token) if expression else token)
                shown.append(text)
                events.extend(cues)
            yield ''.join(shown), events
        yield actions.feed(expression.finish() if expression else '', final=True)

    def test_unpaired_tick_streams_later_text_and_cue_before_final(self):
        chunks = ('用 ' + TICK + ' 号分隔', '就行。\n第二句很长', '第三句。\n第四句【开心】', '结束')
        expected = ''.join(chunks).replace('【开心】', '')
        for through_expression in (False, True):
            for step in (1, 5, max(map(len, chunks))):
                with self.subTest(pipe=through_expression, step=step):
                    turns = list(self.stream_steps(chunks, through_expression, step))
                    self.assertIn('用 ' + TICK + ' 号分隔就行。', ''.join(text for text, _ in turns[:2]))
                    self.assertEqual(''.join(text for text, _ in turns[:3]), expected.removesuffix('结束'))
                    self.assertEqual([cue for _, cues in turns[:3] for cue in cues], [Cue('expression', 'happy')])
                    self.assertEqual(turns[3:], [('结束', []), ('', [])])

    def test_blank_line_releases_pending_markers_across_crlf_tokens(self):
        chunks = ('示例 ' + TICK + ' 号【开心】', '\r', '\n \t\r', '\n', '下一段')
        for through_expression in (False, True):
            with self.subTest(pipe=through_expression):
                turns = list(self.stream_steps(chunks, through_expression, 1))
                self.assertEqual(''.join(text for text, _ in turns[:4]), '示例 ' + TICK + ' 号\r\n \t\r\n')
                self.assertEqual([cue for _, cues in turns[:4] for cue in cues], [Cue('expression', 'happy')])
                self.assertEqual(turns[4:], [('下一段', []), ('', [])])

    def test_multiline_paired_tick_never_dispatches_literal_task_while_streaming(self):
        chunks = ('展示 ' + TICK * 2 + '例子', '\r', '\n' + BODY, TICK * 2 + '。', '\n正文【开心】')
        for through_expression in (False, True):
            for step in (1, 5, max(map(len, chunks))):
                with self.subTest(pipe=through_expression, step=step):
                    turns = list(self.stream_steps(chunks, through_expression, step))
                    self.assertEqual(''.join(text for text, _ in turns[:4]), ''.join(chunks[:4]))
                    self.assertFalse(any(cues for _, cues in turns[:4]))
                    self.assertEqual(turns[4:], [('\n正文', [Cue('expression', 'happy')]), ('', [])])

    def test_long_multiline_code_keeps_tasks_literal_until_its_closing_ticks(self):
        for delimiter, literal in ((TICK, TASK), (TICK * 2, BODY)):
            code = '展示 ' + delimiter + '第一行\n第二行\r\n第三行\n' + '字' * 500
            chunks = (code, literal, delimiter + '。', '正文【开心】<task>整理新文件</task>')
            for through_expression in (False, True):
                for step in (1, 7, len(code)):
                    with self.subTest(delimiter=delimiter, pipe=through_expression, step=step):
                        turns = list(self.stream_steps(chunks, through_expression, step))
                        self.assertEqual(''.join(text for text, _ in turns[:3]), ''.join(chunks[:3]))
                        self.assertFalse(any(cues for _, cues in turns[:3]))
                        self.assertEqual(turns[3:], [
                            ('正文', [Cue('expression', 'happy'), Cue('task', '整理新文件')]), ('', []),
                        ])

    def test_unpaired_tick_streams_long_prose_with_paths_and_comparisons(self):
        for prose in (r'路径是 D:\notes\daily.txt。', '算式里 a < b 成立。', '2<3 成立。', '【注意】后面是正文。'):
            chunks = ('说明 ' + TICK + ' 号。' + prose, '字' * 300, '继续【开心】', '结束')
            for through_expression in (False, True):
                # 短的词表外表情只由 ExpressionStream 隐藏，单独的 CueStream 原样显示。
                expected = ''.join(chunks[:2])
                if through_expression:
                    expected = expected.replace('【注意】', '')
                for step in (1, 13, 300):
                    with self.subTest(prose=prose, pipe=through_expression, step=step):
                        turns = list(self.stream_steps(chunks, through_expression, step))
                        self.assertEqual(''.join(text for text, _ in turns[:2]), expected)
                        self.assertEqual(turns[2:], [('继续', [Cue('expression', 'happy')]), ('结束', []), ('', [])])

    def test_task_named_in_custom_cue_vocab_stays_literal_inside_a_long_code_span(self):
        vocab = Vocab({'task': 'surprise'}, {})
        prefix = '展示 ' + TICK + '第一行\n第二行\n' + '字' * 300
        chunks = (prefix, '<task>示例</task>', TICK + '。', '正文<task>整理新文件</task>')
        for through_expression in (False, True):
            for step in (1, len(prefix)):
                with self.subTest(pipe=through_expression, step=step):
                    turns = list(self.stream_steps(chunks, through_expression, step, vocab))
                    self.assertEqual(''.join(text for text, _ in turns[:3]), ''.join(chunks[:3]))
                    self.assertFalse(any(cues for _, cues in turns[:3]))
                    self.assertEqual(turns[3:], [('正文', [Cue('task', '整理新文件')]), ('', [])])

    def test_list_continuations_and_subitems_leave_markers_active(self):
        original = '1. 第一项\n    '
        self.assertEqual(CueStream(VOCAB).feed(original + '【开心】子项', final=True),
                         (original + '子项', [Cue('expression', 'happy')]))
        for prefix in (original, original.replace('\n', '\r\n'), '- 项目\n    - 子项 ',
                       '1. 第一项\n\n    列表续段 '):
            self.assert_active_after(prefix)

    def test_user_task_can_include_a_code_span_in_its_goal(self):
        goal = '整理 ' + TICK + 'main.py' + TICK + ' 和附属文件'
        text, cues, _ = self.render('好，交给我。<task>' + goal + '</task>')
        self.assertEqual(text, '好，交给我。')
        self.assertEqual(cues, [Cue('task', goal)])
