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


class CueStream:
    """逐 token 喂入，返回 (可显示文字, 本次解析出的标注)。"""

    def __init__(self, vocab: Vocab):
        self.vocab = vocab; self.pending = ''

    def feed(self, token: str, final: bool = False) -> tuple[str, list[Cue]]:
        if not self.vocab and '<' not in token and not self.pending:
            return token, []
        text, cues = [], []
        data = self.pending+token; self.pending = ''
        i = 0
        while i < len(data):
            ch = data[i]
            if ch == '<':
                rest = data[i:]
                if rest.startswith(TASK_OPEN):
                    close = rest.find(TASK_CLOSE)
                    if close != -1:
                        task = rest[len(TASK_OPEN):close].strip()
                        if task: cues.append(Cue('task', task[:MAX_TASK]))
                        i += close+len(TASK_CLOSE); continue
                    if not final and len(rest) <= MAX_TASK+len(TASK_OPEN)+len(TASK_CLOSE):
                        self.pending = rest; break
                elif TASK_OPEN.startswith(rest) and not final:
                    self.pending = rest; break
            if ch in OPENERS:
                close = data.find(OPENERS[ch], i+1)
                if close == -1:
                    if len(data)-i-1 <= MAX_TAG and not final:
                        self.pending = data[i:]; break   # 等下一个 token
                    text.append(ch); i += 1; continue
                word = data[i+1:close]
                cue = self.vocab.lookup(word) if len(word) <= MAX_TAG and self.vocab else None
                if cue: cues.append(cue)
                else: text.append(data[i:close+1])
                i = close+1; continue
            text.append(ch); i += 1
        if final and self.pending:
            text.append(self.pending); self.pending = ''
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
