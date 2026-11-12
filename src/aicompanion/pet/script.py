"""台词标注：模型在回复里用【开心】或 <挥手> 标出表情和动作，界面执行后从文字里去掉。

- 流式解析：标注可能被拆在两个 token 里，未闭合时先缓存。
- 词表里没有的词按原文保留（例如"1<2"或正文里本来就有的【注意】），不会被吞掉。
- 不依赖 Qt，便于测试。
"""
from __future__ import annotations

from dataclasses import dataclass
import re

OPENERS = {'【': '】', '<': '>'}
MAX_TAG = 8   # 标注内容最长字数，超过就当普通文字
TASK_OPEN, TASK_CLOSE = '<task>', '</task>'   # P5：陪伴通路里"需要动手"的标记，界面据此问"要我动手吗"
MAX_TASK = 1500   # 任务说明可能带一整段文字，太短会让标记解析失败、原样显示出来
EXPRESSION_TAGS = ('<sticker', '<meme', '<expression', '<mind')
CONTROL_TAGS = EXPRESSION_TAGS + ('<img', '<leave', TASK_OPEN)


@dataclass(frozen=True)
class Cue:
    kind: str    # 'expression' | 'motion' | 'task'
    name: str    # 角色包里的 id，如 happy / nod


class Vocab:
    def __init__(self, expressions: dict | None = None, motions: dict | None = None):
        self.expressions = dict(expressions or {}); self.motions = dict(motions or {})

    @classmethod
    def from_pack(cls, pack) -> 'Vocab':
        data = (pack.renderer.get('vocab') if pack else None) or {}
        return cls(data.get('expressions'), data.get('motions'))

    def lookup(self, word: str) -> Cue | None:
        word = word.strip()
        if word in self.expressions: return Cue('expression', self.expressions[word])
        if word in self.motions: return Cue('motion', self.motions[word])
        return None

    def __bool__(self):
        return bool(self.expressions or self.motions)

    def prompt(self) -> str:
        """给模型的说明：可以用哪些标注、怎么用。"""
        if not self: return ''
        exp = '、'.join(self.expressions); mot = '、'.join(self.motions)
        return ('\n【表情与动作】你可以在台词里用全角方括号标注表情或动作，例如“【开心】太好了！<挥手>再见啦”。'
                f'可用表情：{exp}。可用动作：{mot}。只能用这些词；一轮回复最多两处，不必每句都用；'
                '标注会被执行并从显示的文字里去掉，所以不要写成句子的一部分，也不要用其他括号描写动作。')


class MarkdownLiterals:
    """把展示用的代码和引用交给调用方原样输出；普通正文可以逐字处理。"""

    LIST_MARKER = re.compile(r'(?:[-+*]|[0-9]{1,9}[.)])[ \t]+')
    # 单个反引号候选最多推迟两次软换行或 256 字的动画；任务等协议仍等真正的配对结果。
    ANIMATION_WAIT = 256
    SPAN_SPECIAL = '`\\<{[【'
    STREAMABLE = re.compile(
        r'[^`\\<{\[【]+|\\[^`]|'                        # 普通文字、路径或转义字符
        r'<(?:[^`\\<>{\[【\r\n]{9}|[^`\\<>{\[【\r\n]{0,8}[\r\n])|'  # 已超过动画长度或遇换行的比较式
        r'\[(?!角色已发送表情[：:])[^\\`\[\]{}<【\r\n]{0,256}\]|'              # 普通链接或数组的纯文字部分
        r'(?P<cue>【[^\s`\\<>{}\[\]【】]{1,8}】|<[^\s`\\<>{}\[\]【】/]{1,8}>)'
    )

    def __init__(self):
        self.fence = ''
        self.fence_indent = 0
        self.line_kind = ''
        self.indent = 0
        self.list_indents = []
        self.line_head = ''
        self.fence_line = []
        self.after_blank = True
        self.after_indent = False
        self.after_paragraph = False
        self.after_cr = False
        self.literal_newline = False
        self.escaped = False
        # 分隔符和没有控制含义的前缀直接显示；只保留从第一个歧义字符开始的后缀。
        self.span = []
        self.open_size = self.tick_run = self.soft_breaks = self.span_width = 0
        self.in_opener = False

    def _take_span(self):
        raw = ''.join(self.span)
        self.span.clear()
        self.open_size = self.tick_run = self.soft_breaks = self.span_width = 0
        self.in_opener = False
        return raw

    def _release_streamable(self):
        raw = ''.join(self.span)
        allow_cues = self.open_size == 1 and (self.soft_breaks >= 2 or self.span_width >= self.ANIMATION_WAIT)
        i = 0
        while match := self.STREAMABLE.match(raw, i):
            part = match[0]
            if part.lower().startswith(CONTROL_TAGS): break
            if match.lastgroup == 'cue':
                if not allow_cues: break
                for ch in part: yield ch, False
            else:
                yield part, True
            i = match.end()
        if i: del self.span[:i]

    def _finish_span(self, syntax):
        if not self.open_size: return
        closed = self.tick_run == self.open_size
        raw = self._take_span()
        if closed:
            yield raw, True
            return
        # 如果最早的分隔符落单，后面的分隔符仍可彼此配对。一次反向索引，避免逐一重扫余下全文。
        next_run = {}
        next_by_size = {}
        for run in reversed(list(re.finditer(r'`+', raw))):
            size = run.end() - run.start()
            next_run[run.start()] = (run.end(), next_by_size.get(size))
            if size > 1:  # 反斜杠只转义第一个反引号，余下的一串仍能打开行内代码。
                next_run[run.start() + 1] = (run.end(), next_by_size.get(size - 1))
            next_by_size[size] = run.end()
        i = 0
        while i < len(raw):
            if self.escaped:
                self.escaped = False
                yield raw[i], True
            elif i in next_run and syntax():
                end, closing = next_run[i]
                yield raw[i:closing or end], True
                i = closing or end
                continue
            else:
                self.escaped = raw[i] == '\\' and syntax()
                yield raw[i], False
            i += 1

    def _inline(self, text, syntax):
        for ch in text:
            if self.open_size:
                if self.in_opener and ch == '`':
                    self.open_size += 1
                    yield ch, True
                    continue
                self.in_opener = False
                if ch == '`':
                    self.tick_run += 1
                    self.span.append(ch)
                    continue
                if self.tick_run == self.open_size:
                    yield self._take_span(), True
                else:
                    self.tick_run = 0
                    self.span_width += 1
                    if self.span or ch in self.SPAN_SPECIAL:
                        self.span.append(ch)
                        # 第一次到达上限时检查已积累的前缀。之后只在短前缀有新判据时检查，长协议不反复扫描。
                        n = len(self.span)
                        if (self.open_size == 1 and self.span_width == self.ANIMATION_WAIT
                                or n == 2 and self.span[0] == '\\'
                                or n <= MAX_TAG + 2 and ch in '】>\r\n'
                                or n == MAX_TAG + 2 and self.span[0] == '<'
                                or n <= 258 and ch == ']' and self.span[0] == '['):
                            yield from self._release_streamable()
                    else:
                        yield ch, True
                    continue
            if self.escaped:
                self.escaped = False
                yield ch, True
            elif ch == '`' and syntax():
                self.open_size = 1
                self.in_opener = True
                yield ch, True
            else:
                self.escaped = ch == '\\' and syntax()
                yield ch, False

    def _start_line(self, ch, syntax):
        if self.fence and self.indent < self.fence_indent:
            self.fence = ''  # 离开列表项时其中的围栏也结束。
        while self.list_indents and self.indent < self.list_indents[-1]:
            self.list_indents.pop()
        relative = self.indent - (self.list_indents[-1] if self.list_indents else 0)
        if relative < 4 and ch in ('`', '~') and (self.fence or syntax()):
            return 'fence_line'
        if self.fence: return 'literal'
        if syntax() and relative < 4 and ch in '>＞': return 'literal'
        if syntax() and relative >= 4 and (self.after_blank or self.after_indent): return 'indent'
        return 'text'

    def _end_line(self, newline, syntax):
        kind = self.line_kind
        if kind == 'fence_line':
            line = ''.join(self.fence_line)
            self.fence_line.clear()
            run = re.match(r'`+|~+', line)[0]
            rest = line[len(run):]
            if self.fence:
                if run.startswith(self.fence) and not rest.strip(' \t'):
                    self.fence = ''
                kind = 'literal'
            elif len(run) >= 3 and (run[0] == '~' or '`' not in rest):
                self.fence = run
                self.fence_indent = self.list_indents[-1] if self.list_indents else 0
                kind = 'literal'
            else:
                kind = 'text'
            if kind == 'literal':
                yield from self._finish_span(syntax)
                yield line, True
            else:
                yield from self._inline(line, syntax)
        self.literal_newline = kind in ('literal', 'indent') or bool(self.fence)
        if not kind:
            yield from self._finish_span(syntax)
        if self.literal_newline:
            if newline: yield newline, True
        else:
            yield from self._inline(newline, syntax)
            if newline and kind == 'text' and self.open_size:
                self.soft_breaks += 1
                if self.open_size == 1 and self.soft_breaks == 2 and self.span:
                    yield from self._release_streamable()
        if kind == 'text' and not self.after_paragraph:
            marker = self.LIST_MARKER.fullmatch(self.line_head)
            if marker:
                self.list_indents.append(len((' ' * self.indent + marker[0]).expandtabs(4)))
        self.after_blank, self.after_indent = not kind, kind == 'indent'
        self.after_paragraph = kind == 'text' and not self.list_indents
        self.line_head = self.line_kind = ''
        self.indent = 0

    def feed(self, text, syntax, *, final=False):
        for ch in text:
            if ch == '\n' and self.after_cr:
                self.after_cr = False
                if self.literal_newline: yield ch, True
                else: yield from self._inline(ch, syntax)
                continue
            self.after_cr = ch == '\r'
            if ch in '\r\n':
                yield from self._end_line(ch, syntax)
                continue
            if (self.line_kind == 'text' and ch not in ' \t' and self.LIST_MARKER.fullmatch(self.line_head)
                    and (not self.after_paragraph or self.line_head.startswith(('1.', '1)', '-', '+', '*')))):
                # “1. ~~~xml” 的围栏始于项目内容的首列。后续行以同一内容列为边界。
                yield from self._finish_span(syntax)
                self.indent = len((' ' * self.indent + self.line_head).expandtabs(4))
                self.list_indents.append(self.indent)
                self.line_head = self.line_kind = ''
            if not self.line_kind:
                if ch in ' \t':
                    self.indent += 1 if ch == ' ' else 4 - self.indent % 4
                    yield from self._inline(ch, syntax)
                    continue
                self.line_kind = self._start_line(ch, syntax)
                if self.line_kind in ('literal', 'indent'):
                    yield from self._finish_span(syntax)
            if self.line_kind == 'fence_line':
                self.fence_line.append(ch)
                if len(self.fence_line) <= 3 and ch not in ('`', '~'):
                    # 单个标点开头的普通行可以立即交给正文或行内解析。
                    prefix = ''.join(self.fence_line)
                    self.fence_line.clear()
                    self.line_kind = 'literal' if self.fence else 'text'
                    if self.fence: yield prefix, True
                    else: yield from self._inline(prefix, syntax)
            elif self.line_kind == 'text':
                if len(self.line_head) < 16: self.line_head += ch
                yield from self._inline(ch, syntax)
            else:
                yield ch, True
        if final:
            yield from self._end_line('', syntax)
            yield from self._finish_span(syntax)


class CueStream:
    """逐 token 喂入，返回 (可显示文字, 本次解析出的标注)。"""

    def __init__(self, vocab: Vocab):
        self.vocab = vocab
        self.pending = ''
        self.markdown = MarkdownLiterals()

    def feed(self, token: str, final: bool = False) -> tuple[str, list[Cue]]:
        text, cues = [], []
        # 委托正文内可以包含文件名和代码，交给下游的内容仍是原文。
        for ch, literal in self.markdown.feed(token, lambda: not self.pending.startswith(TASK_OPEN), final=final):
            if literal:
                if self.pending:
                    text.append(self.pending)
                    self.pending = ''
                text.append(ch)
                continue
            if not self.pending:
                if ch in OPENERS:
                    self.pending = ch
                else:
                    text.append(ch)
                continue
            self.pending += ch
            if self.pending.startswith(TASK_OPEN):
                if self.pending.endswith(TASK_CLOSE):
                    task = self.pending[len(TASK_OPEN):-len(TASK_CLOSE)].strip()
                    if task: cues.append(Cue('task', task[:MAX_TASK]))
                    self.pending = ''
                elif len(self.pending) > MAX_TASK + len(TASK_OPEN) + len(TASK_CLOSE):
                    text.append(self.pending)
                    self.pending = ''
                continue
            if ch == OPENERS[self.pending[0]]:
                cue = self.vocab.lookup(self.pending[1:-1]) if len(self.pending) <= MAX_TAG + 2 else None
                if cue: cues.append(cue)
                else: text.append(self.pending)
                self.pending = ''
            elif len(self.pending) > MAX_TAG + 1 or ch in '\r\n':
                text.append(self.pending)
                self.pending = ''
        if final and self.pending:
            text.append(self.pending)
            self.pending = ''
        return ''.join(text), cues

    def flush(self) -> tuple[str, list[Cue]]:
        return self.feed('', final=True)


def strip_cues(text: str, vocab: Vocab) -> str:
    """整段文字去掉已知标注（存库、显示历史记录用）。"""
    clean, _ = CueStream(vocab).feed(text, final=True)
    return clean


TASK_PROMPT = ('\n【动手】你真的能在用户的电脑上做事：读写任何文件、在后台跑 PowerShell 命令、统计整理文件、查网页、写博客草稿、用默认程序打开文件。'
               '只要用户要你实际去做事（一句话里有几件事也算一次），就只回一句简短的答应（比如“好，我这就去弄”），'
               '然后在最后单独写 <task>一句话概括</task>。系统会把用户的原话交给干活的你，按原话一次做完所有事。'
               '不要在这里规定用什么程序或步骤，不要说“做不到”“只能用资源管理器”之类的话，也不要假装已经做完。闲聊、提问、出主意不要用这个标记。')
