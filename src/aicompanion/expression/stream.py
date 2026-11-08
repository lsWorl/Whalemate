"""小型增量解析：图片控制先清理，原 cue/task 留给 CueStream。

协议（2026-11-08 Claude 改版）：模型写 `<sticker id="s3"/>` 选本轮候选里的一张图。
- 位置由标记出现的地方决定：前面还没有可见正文 → 图在前（before_text）；否则图在后（after_text）。
  旧写法 `position="before_text|after_text"` 仍接受；正文中间出现的标记按"图在后"处理，不再整张作废。
- 属性顺序、引号、结尾的 `/` 都宽松；`id` / `asset_id` 都认；值可以是本轮短编号（s1～s10）或完整 asset_id。
- 一轮最多一张：采用第一个**有效**标记；无效的标记被隐藏，不占名额；后面多出来的有效标记只隐藏。
- `<sticker sent="含义"/>` 是历史里给模型看"她以前发过什么图"的写法：模型照抄时，含义和本轮某个候选完全一致才算选它，否则静默隐藏。
- `<expression mode="text_only"/>` 是旧协议的显式纯文字选择：隐藏，记诊断，但不阻止后面真的选图。
- 【开心】、<挥手> 这类角色动画标注不算"可见正文"：标注之后的图仍算图在前。
"""
from __future__ import annotations
import re
from .domain import ASSET_ID

ATTR = re.compile(r"""([a-z_]+)\s*=\s*(?:"([^"<>\n]*)"|'([^'<>\n]*)'|([^\s"'<>/]+))""")
TAG = re.compile(r"""<\s*(sticker|meme|expression)\b([^<>\n]*?)/?\s*>\Z""", re.I)
HANDLE = re.compile(r's(?:[1-9]|10)\Z')
# 旧版曾把内部附件回执拼进 assistant 正文；只剥离这个精确前缀，不把任意方括号当图片。
HISTORY_ANNOTATIONS = ('[角色已发送表情：', '[角色已发送表情:')
STARTS = ('<sticker', '<meme', '<expression', '<task>')+HISTORY_ANNOTATIONS
# 角色动画标注：给桌宠脸和动作用，屏幕上会被 CueStream 去掉，不算对方能看到的正文。
CUE = re.compile(r'【[^】\n]{1,12}】|<[^<>\n/]{1,12}>')


class ExpressionStream:
    def __init__(self, candidate_ids=(), legacy=None, handles=None, meanings=None):
        self.allowed = frozenset(candidate_ids); self.legacy = dict(legacy or {})
        self.handles = dict(handles or {})       # 's3' -> asset_id
        self.meanings = dict(meanings or {})     # 含义 -> asset_id（历史写法照抄时用）
        self.pending = ''; self.selection = None; self.reserved = False; self.diagnostics = []
        self.markers = 0; self.visible = ''; self.has_text = False; self.cancelled = False; self.finished = False
        self.fence = 0; self.inline = False; self.ticks = ''; self.tick_start = False; self.line_start = True
        self.quote_line = False; self.json_depth = 0; self.json_string = False; self.escape = False
        self.task = ''; self.drop_line = False; self.drop_annotation = False
        self.after_text_seen = False

    def diagnose(self, code):
        if code not in self.diagnostics and len(self.diagnostics) < 16: self.diagnostics.append(code)

    def _emit(self, text, out):
        if self.selection and self.selection[1] == 'after_text' and CUE.sub('', text).strip():
            self.after_text_seen = True
        if not self.has_text and text:
            # 标注可能被拆在几个 token 里（"<挥" + "手>"），所以累积起来再判断；超过 200 字肯定已经有正文了
            self.visible += text
            if len(self.visible) > 200 or CUE.sub('', self.visible).strip() and not self._maybe_cue_tail():
                self.has_text = True
        out.append(text)
        for ch in text:
            if ch == '\n': self.line_start = True; self.quote_line = False
            elif not ch.isspace(): self.line_start = False

    def _maybe_cue_tail(self):
        """末尾是不是一个还没闭合的标注（【开 / <挥），是的话先不算正文。"""
        body = CUE.sub('', self.visible)
        tail = re.search(r'(【[^】\n]{0,12}|<[^<>\n/]{0,12})\Z', body)
        return bool(tail) and not body[:tail.start()].strip()

    def _visible_text(self):
        return self.has_text or bool(CUE.sub('', self.visible).strip())

    def _resolve(self, value):
        value = (value or '').strip()
        if HANDLE.fullmatch(value.lower()): return self.handles.get(value.lower())
        if ASSET_ID.fullmatch(value) and value in self.allowed: return value
        return None

    def _marker(self, raw):
        self.markers += 1
        match = TAG.match(raw.strip())
        if not match:
            self.diagnose('invalid_marker'); return
        kind = match[1].lower()
        attrs = {m[1].lower(): (m[2] if m[2] is not None else m[3] if m[3] is not None else m[4]) for m in ATTR.finditer(match[2])}
        if kind == 'expression':
            self.diagnose('explicit_text_only' if attrs.get('mode') == 'text_only' else 'invalid_marker'); return
        asset = position = None
        if kind == 'meme':
            asset = self.legacy.get(attrs.get('name', ''))
        elif 'sent' in attrs and not ({'id', 'asset_id'} & attrs.keys()):
            asset = self.meanings.get(attrs['sent'].strip())
            if asset is None:
                self.diagnose('history_echo'); return
            self.diagnose('history_echo_matched')
        else:
            value = attrs.get('id', attrs.get('asset_id'))
            if value is None:
                bare = match[2].strip().strip('"\'')   # <sticker s3/> 这种省略属性名的写法
                value = bare if HANDLE.fullmatch(bare.lower()) else None
            asset = self._resolve(value)
            position = attrs.get('position')
        if not isinstance(asset, str) or asset not in self.allowed:
            self.diagnose('invalid_asset'); return
        if self.reserved:
            self.diagnose('multiple_markers'); return
        self.has_text = self._visible_text()
        actual = 'after_text' if self.has_text else 'before_text'
        if position in ('before_text', 'after_text') and position != actual:
            # 写了"图在前"但前面已有正文：仍然发，只是放到文字后面
            self.diagnose('position_adjusted' if position == 'before_text' else 'position_adjusted_after')
            if position == 'after_text': actual = 'after_text'
        self.reserved = True; self.selection = (asset, actual)

    def _char(self, ch, out):
        if self.drop_annotation:
            if ch == ']': self.drop_annotation = False
            elif ch == '\n': self.drop_annotation = False; self._emit(ch, out)
            return
        if self.drop_line:
            if ch == '>': self.drop_line = False
            elif ch == '\n': self.drop_line = False; self._emit(ch, out)
            return
        if self.task:
            self.task += ch
            if self.task.endswith('</task>'):
                # task 是独立控制域，不在这里执行其中的图片写法。
                self._emit(self.task, out); self.task = ''
            elif len(self.task) > 1530:
                safe = re.sub(r'<(?:sticker|meme|expression)\b[^>\n]*>?', '', self.task)
                self._emit(safe, out); self.task = ''
            return
        if self.pending:
            self.pending += ch
            low = self.pending.lower()
            annotation = any(self.pending.startswith(prefix) for prefix in HISTORY_ANNOTATIONS)
            if annotation:
                if ch == ']' or ch == '\n' or len(self.pending) > 512:
                    self.pending = ''; self.diagnose('internal_annotation_echo')
                    if ch == '\n': self._emit(ch, out)
                    elif ch != ']': self.drop_annotation = True
                return
            reserved = low.startswith(('<sticker', '<meme', '<expression'))
            if self.pending == '<task>':
                self.task = self.pending; self.pending = ''; return
            if reserved and (ch == '>' or ch == '\n' or len(self.pending) > 512):
                raw = self.pending; self.pending = ''
                if ch == '>': self._marker(raw)
                else:
                    self.markers += 1; self.diagnose('invalid_marker')
                    if ch == '\n': self._emit(ch, out)
                    else: self.drop_line = True
                return
            if reserved or any(s.startswith(low) for s in STARTS): return
            raw = self.pending; self.pending = ''
            if raw.startswith('['):
                # 只有已确认不是内部说明的方括号才进入普通 JSON 数组保护。
                self.json_depth = 1; self._emit('[', out)
                for part in raw[1:]: self._char(part, out)
            else: self._emit(raw, out)
            return
        if self.ticks:
            if ch == self.ticks[0]: self.ticks += ch; return
            run = self.ticks; self.ticks = ''
            if self.tick_start and len(run) >= 3:
                self.fence = 0 if self.fence else len(run)
            elif not self.fence and run[0] == '`': self.inline = not self.inline
            self._emit(run, out)
        if ch in ('`', '~') and not self.json_depth:
            self.ticks = ch; self.tick_start = self.line_start; return
        if self.fence or self.inline or self.quote_line:
            self._emit(ch, out); return
        if self.json_depth:
            if self.escape: self.escape = False
            elif ch == '\\' and self.json_string: self.escape = True
            elif ch == '"': self.json_string = not self.json_string
            elif not self.json_string:
                if ch in '{[': self.json_depth += 1
                elif ch in '}]': self.json_depth -= 1
            self._emit(ch, out); return
        if ch == '>' and self.line_start: self.quote_line = True
        if ch == '{': self.json_depth = 1
        if ch in '<[': self.pending = ch
        else: self._emit(ch, out)

    def feed(self, chunk):
        if self.cancelled or self.finished: return ''
        out = []
        for ch in str(chunk): self._char(ch, out)
        return ''.join(out)

    def finish(self):
        if self.finished or self.cancelled: return ''
        out = []
        if self.pending:
            if any(self.pending.startswith(prefix) for prefix in HISTORY_ANNOTATIONS): self.diagnose('internal_annotation_echo')
            elif not self.pending.lower().startswith(('<sticker', '<meme', '<expression')): self._emit(self.pending, out)
            else: self.markers += 1; self.diagnose('unfinished_marker')
        if self.task:
            self._emit(re.sub(r'<(?:sticker|meme|expression)\b[^>\n]*>?', '', self.task), out)
        if self.ticks: self._emit(self.ticks, out)
        if self.selection and self.after_text_seen: self.diagnose('mid_text_marker')
        self.pending = self.task = self.ticks = ''; self.finished = True
        return ''.join(out)

    def cancel(self):
        self.cancelled = True; self.selection = None; self.pending = self.task = self.ticks = ''


def clean_expression_history(text):
    """仅清理发给模型的副本，不重写旧聊天；代码 / JSON 中的字面示例仍保留。"""
    parser = ExpressionStream()
    return parser.feed(text)+parser.finish()
