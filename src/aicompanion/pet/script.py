"""台词标注：模型在回复里用【开心】或 <挥手> 标出表情和动作，界面执行后从文字里去掉。

- 流式解析：标注可能被拆在两个 token 里，未闭合时先缓存。
- 词表里没有的词按原文保留（例如"1<2"或正文里本来就有的【注意】），不会被吞掉。
- 不依赖 Qt，便于测试。
"""
from __future__ import annotations

from dataclasses import dataclass

OPENERS = {'【': '】', '<': '>'}
MAX_TAG = 8   # 标注内容最长字数，超过就当普通文字
TASK_OPEN, TASK_CLOSE = '<task>', '</task>'   # P5：陪伴通路里"需要动手"的标记，界面据此问"要我动手吗"
MAX_TASK = 1500   # 任务说明可能带一整段文字，太短会让标记解析失败、原样显示出来


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
    """逐字符判断 Markdown 展示区域。分隔符可以横跨任意 token，不缓存正文。"""

    def __init__(self):
        self.fence = ''
        self.fence_size = self.inline_size = 0
        self.run = ''
        self.run_size = 0
        self.at_margin = self.closing = self.literal_line = False
        self.opening_line = False
        self.escaped = False
        self.indent = 0  # 行首缩进列数，遇到正文后设为 None

    def protected(self, ch: str, *, syntax: bool = True) -> bool:
        if self.run:
            if ch == self.run:
                self.run_size += 1
                return True
            if self.opening_line and self.run == self.fence == '`':
                # 反引号围栏的语言说明不能含反引号；同一行再出现时，这里是行内代码。
                self.fence = ''
                self.opening_line = False
                self.inline_size = 0 if self.run_size == self.fence_size else self.fence_size
            elif self.fence:
                self.closing = self.at_margin and self.run == self.fence and self.run_size >= self.fence_size
            elif self.inline_size:
                if self.run == '`' and self.run_size == self.inline_size:
                    self.inline_size = 0
            elif self.at_margin and self.run_size >= 3:
                self.fence, self.fence_size = self.run, self.run_size
                self.opening_line = True
            elif self.run == '`':
                self.inline_size = self.run_size
            self.run = ''

        escaped, self.escaped = self.escaped, False
        if ch in '\r\n':
            if self.closing:
                self.fence = ''
            self.closing = self.literal_line = self.opening_line = False
            self.indent = 0
            return bool(self.fence or self.inline_size)

        margin = self.indent is not None and self.indent < 4
        if ch in ' \t':
            if self.indent is not None:
                self.indent += 1 if ch == ' ' else 4 - self.indent % 4
                if self.indent >= 4 and syntax and not (self.fence or self.inline_size):
                    self.literal_line = True
        else:
            self.closing = False  # 围栏后的非空白字符使这一行失去闭合资格
            self.indent = None
            if syntax and not (self.fence or self.inline_size) and margin and ch in '>＞':
                self.literal_line = True
        if escaped:
            return True
        if syntax and not (self.fence or self.inline_size or self.literal_line) and ch == '\\':
            self.escaped = True
        if syntax and not self.literal_line and ch in ('`', '~'):
            self.run, self.run_size, self.at_margin = ch, 1, margin
            return True
        return bool(self.literal_line or self.fence or self.inline_size)


class CueStream:
    """逐 token 喂入，返回 (可显示文字, 本次解析出的标注)。"""

    def __init__(self, vocab: Vocab):
        self.vocab = vocab
        self.pending = ''
        self.markdown = MarkdownLiterals()

    def feed(self, token: str, final: bool = False) -> tuple[str, list[Cue]]:
        text, cues = [], []
        for ch in token:
            # 一个从普通正文开始的委托本身可以含文件名、代码等；照原样交给下游。
            if self.markdown.protected(ch, syntax=not self.pending.startswith(TASK_OPEN)):
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
