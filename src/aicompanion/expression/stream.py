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
from aicompanion.pet.script import CONTROL_TAGS, EXPRESSION_TAGS, SAY_OPEN, MarkdownLiterals, SayBlock
from .domain import ASSET_ID

ATTR = re.compile(r"""([a-z_]+)\s*=\s*(?:"([^"<>\n]*)"|'([^'<>\n]*)'|([^\s"'<>/]+))""")
TAG = re.compile(r"""<\s*(sticker|meme|expression)\b([^<>\n]*?)/?\s*>\Z""", re.I)
HANDLE = re.compile(r's(?:[1-9]|10)\Z')
# 旧版曾把内部附件回执拼进 assistant 正文；只剥离这个精确前缀，不把任意方括号当图片。
HISTORY_ANNOTATIONS = ('[角色已发送表情：', '[角色已发送表情:')
# <img …>：第八轮验收里模型写过 `<img src="">` 想"贴图"，原样露在正文里；当作无效的图片标记隐藏（代码块里的照常保留）。
# 标签名后面可以是任意空白（空格、制表符、换行、CRLF）、/ 或 >，属性可以跨行（Codex 审计）：进入单独的 img 状态一直吃到 >。
STARTS = CONTROL_TAGS + HISTORY_ANNOTATIONS
RESERVED = EXPRESSION_TAGS
# <mind intent="…" willing="0~1"/>：T15 她对"对方请她做事"的态度，对方看不到；只记录给关系统计和观测，不决定执行与否
IMG_LIMIT = 1024
LEAVE_LIMIT = 128
LEAVE = re.compile(r"""<leave[ \t]+reason[ \t]*=[ \t]*(["'])([^<>"'\x00-\x1f]{1,64})\1[ \t]*/>""", re.I)
# 角色动画标注：给桌宠脸和动作用，屏幕上会被 CueStream 去掉，不算对方能看到的正文。
CUE = re.compile(r'【[^】\n]{1,12}】|<[^<>\n/]{1,12}>')


PLACEHOLDERS = {'编号', '编号标记', '数字', '序号', 'id', 'sn', 'sx', 'n', 'x', 'number'}


def rejected_shape(value):
    """无效编号只归成几类形态，用于观测；任何原文都不保留。"""
    value = (value or '').strip()
    if not value: return 'empty'
    if value.lower() in PLACEHOLDERS: return 'placeholder'
    if re.fullmatch(r's\d+', value, re.I): return 'handle_out_of_range'
    if ASSET_ID.fullmatch(value): return 'asset_id_not_offered'
    if re.fullmatch(r'[A-Za-z0-9_-]{1,40}', value): return 'other_code'
    return 'free_text'


class ExpressionStream:
    def __init__(self, candidate_ids=(), legacy=None, handles=None, meanings=None, cue_words=None):
        self.allowed = frozenset(candidate_ids); self.legacy = dict(legacy or {})
        self.handles = dict(handles or {})       # 's3' -> asset_id
        self.meanings = dict(meanings or {})     # 含义 -> asset_id（历史写法照抄时用）
        self.pending = ''; self.selection = None; self.reserved = False; self.diagnostics = []
        self.mind = None
        self.leave_reason = ''
        self.leave_tag = ''
        self.leave_quote = ''
        self.say = SayBlock()
        self.markers = 0; self.visible = ''; self.has_text = False; self.cancelled = False; self.finished = False
        self.markdown = MarkdownLiterals()
        self.json_depth = 0; self.json_string = False; self.escape = False
        self.task = ''; self.drop_line = False; self.drop_annotation = False
        self.after_text_seen = False
        # 角色动画词表：传入时，词表外的短情绪方括号（【委屈】【心虚】…）不会被动画执行、只会原样露在正文里，
        # 这里直接隐藏并记诊断（第一轮真实验收里她常这样写，而那正是该发表情包的时候）。【不说】是群里的静默标记，保留。
        self.cue_words = frozenset(cue_words or ()) | {'不说'} if cue_words else None
        self.bracket = ''; self.rejected = []; self.img = 0; self.img_quote = ''; self.img_quoted = 0

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
        # 模型偶尔把图的含义当编号写（id="困到闭眼"）：和本轮恰好一张候选的含义对得上才算
        if len(value) >= 2:
            hits = {asset for meaning, asset in self.meanings.items() if value in meaning}
            if len(hits) == 1: self.diagnose('meaning_as_id'); return hits.pop()
        return None

    def _mind(self, raw):
        """<mind intent="乐意" willing="0.8" feel="生气" toward="-2"/>：只取有限长度的态度词、0～1 的意愿、
        感觉词和 -3～3 的好恶变化，其余忽略；不计入表情标记数。"""
        attrs = {m[1].lower(): (m[2] if m[2] is not None else m[3] if m[3] is not None else m[4]) for m in ATTR.finditer(raw)}
        try: willing = max(0.0, min(1.0, float(attrs.get('willing', ''))))
        except ValueError: willing = None
        intent = re.sub(r'[^\w\u4e00-\u9fff]', '', str(attrs.get('intent', '')))[:8]
        # 负责人 2026-11-09：她要有自己的脾气。feel / toward 是这条消息让她对对方的感觉变好还是变差（-3～3），即时记进关系
        feel = re.sub(r'[^\w\u4e00-\u9fff]', '', str(attrs.get('feel', '')))[:6]
        try: toward = max(-3.0, min(3.0, float(attrs.get('toward', ''))))
        except ValueError: toward = None
        # T18：这句冲谁、谁的话、什么语气（她自己的判断，由关系系统确定性记分）
        pick = lambda key, allowed: (str(attrs.get(key, '')).strip().lower() if str(attrs.get(key, '')).strip().lower() in allowed else '')
        at = pick('at', ('me', 'other', 'all', 'none'))
        voice = pick('voice', ('own', 'quote'))
        tone = pick('tone', ('joke', 'tease', 'sarcasm', 'attack', 'neutral'))
        # T20：她想当场查一个没见过的梗（只留词本身：去掉引号、控制字符，最多 16 字）
        lookup = re.sub(r'[\x00-\x1f<>]', '', str(attrs.get('lookup', ''))).strip().strip('"“”「」『』《》')[:16]
        if self.mind is None: self.mind = {'intent': intent, 'willing': willing, 'feel': feel, 'toward': toward, 'at': at, 'voice': voice, 'tone': tone,
                                           'lookup': lookup}
        self.diagnose('mind')

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
            self.diagnose('invalid_asset')
            value = attrs.get('id', attrs.get('asset_id', attrs.get('name', attrs.get('sent', ''))))
            # 只记"写错的形态"，不记原值：模型可能把聊天内容写进标记（Codex 审计 P2）
            if len(self.rejected) < 3: self.rejected.append(rejected_shape(value))
            return
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
        if self.leave_tag:
            self.leave_tag += ch
            if ch in '\r\n' or len(self.leave_tag) > LEAVE_LIMIT:
                # 普通正文也可能提到 <leave；到边界立即放回原文，后面的文字继续解析。
                self._emit(self.leave_tag, out)
                self.leave_tag = self.leave_quote = ''
            elif self.leave_quote:
                if ch == self.leave_quote:
                    self.leave_quote = ''
            elif ch in "\"'":
                self.leave_quote = ch
            elif ch == '>':
                match = LEAVE.fullmatch(self.leave_tag)
                if match:
                    if not self.leave_reason:
                        self.leave_reason = match[2].strip()
                else:
                    self._emit(self.leave_tag, out)
                self.leave_tag = ''
            return
        if self.drop_annotation:
            if ch == ']': self.drop_annotation = False
            elif ch == '\n': self.drop_annotation = False; self._emit(ch, out)
            return
        if self.drop_line:
            if ch == '>': self.drop_line = False
            elif ch == '\n': self.drop_line = False; self._emit(ch, out)
            return
        if self.say:
            self._emit(self.say.feed(ch), out)
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
        if self.img:
            # <img 标签内部：一旦认出是 img 标签就只丢弃、不再还回正文（Codex 审计 e28c1ac：超长 src 曾整段回吐到正文 / TTS / 历史）。
            # 只计数不存内容，内存有界；跨行一直丢到引号外的 >。引号里的 > 不算结束，但一对引号超过 IMG_LIMIT 还没闭合
            # 就不再认这个引号，遇到下一个 > 即结束，避免一个落单的引号吞掉后面所有正常文字。
            self.img += 1
            if self.img_quote:
                self.img_quoted += 1
                if ch == self.img_quote: self.img_quote = ''; return
                if self.img_quoted <= IMG_LIMIT: return
                # 这个标签里之后的引号都不再认（否则超长值自己的收尾引号会重新开一对引号，把后文吞掉）
                self.img_quote = ''; self.img_quoted = -1; self.diagnose('html_img_overlong')
            if ch in '"\'' and self.img_quoted >= 0: self.img_quote = ch; self.img_quoted = 0
            elif ch == '>':
                self.img = 0; self.markers += 1; self.diagnose('html_img_tag')
            return
        if self.bracket:
            self.bracket += ch
            if ch == '】' or ch == '\n' or len(self.bracket) > 10:
                raw = self.bracket; self.bracket = ''
                word = raw[1:-1] if ch == '】' else None
                if word is not None and word not in self.cue_words and re.fullmatch(r'[\u4e00-\u9fff]{1,4}', word):
                    self.diagnose('stray_emotion_tag')
                else: self._emit(raw, out)
            return
        if self.pending:
            self.pending += ch
            low = self.pending.lower()
            if low[:-1] == '<leave' and (ch.isspace() or ch in '/>'):
                self.leave_tag, self.pending = self.pending[:-1], ''
                self._char(ch, out)
                return
            annotation = any(self.pending.startswith(prefix) for prefix in HISTORY_ANNOTATIONS)
            if annotation:
                if ch == ']' or ch == '\n' or len(self.pending) > 512:
                    self.pending = ''; self.diagnose('internal_annotation_echo')
                    if ch == '\n': self._emit(ch, out)
                    elif ch != ']': self.drop_annotation = True
                return
            if low[:4] == '<img' and len(low) == 5:
                if ch.isspace() or ch in '/>':
                    self.pending = ''
                    if ch == '>': self.markers += 1; self.diagnose('html_img_tag')
                    else: self.img = 5; self.img_quote = ''; self.img_quoted = 0
                    return
            if low[:5] == '<mind' and len(low) == 6 and not (ch.isspace() or ch in '/>'):
                raw = self.pending; self.pending = ''; self._emit(raw, out); return   # <minds> 之类是普通文字
            reserved = low.startswith(RESERVED)
            if self.pending == SAY_OPEN:
                self.say.buffer = self.pending; self.pending = ''; return
            if self.pending == '<task>':
                self.task = self.pending; self.pending = ''; return
            if reserved and (ch == '>' or ch == '\n' or len(self.pending) > 512):
                raw = self.pending; self.pending = ''
                if low.startswith('<mind'):
                    if ch == '>': self._mind(raw)
                    else: self.diagnose('invalid_mind'); self.drop_line = ch != '\n'
                    if ch == '\n': self._emit(ch, out)
                    return
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
        if self.json_depth:
            if self.escape: self.escape = False
            elif ch == '\\' and self.json_string: self.escape = True
            elif ch == '"': self.json_string = not self.json_string
            elif not self.json_string:
                if ch in '{[': self.json_depth += 1
                elif ch in '}]': self.json_depth -= 1
            self._emit(ch, out); return
        if ch == '{': self.json_depth = 1
        if ch in '<[': self.pending = ch
        elif ch == '【' and self.cue_words is not None: self.bracket = ch
        else: self._emit(ch, out)

    def _markdown_enabled(self):
        in_tag = self.pending.lower().startswith(RESERVED + ('<img', '['))
        return not (in_tag or self.leave_tag or self.say or self.task or self.json_depth or self.img or self.drop_annotation or self.drop_line)

    def _read(self, chunk, out, *, final=False):
        for ch, literal in self.markdown.feed(chunk, self._markdown_enabled, final=final):
            if literal:
                if self.pending or self.bracket:
                    self._emit(self.pending + self.bracket, out)
                    self.pending = self.bracket = ''
                self._emit(ch, out)
            else:
                self._char(ch, out)

    def feed(self, chunk):
        if self.cancelled or self.finished: return ''
        out = []
        self._read(str(chunk), out)
        return ''.join(out)

    def finish(self):
        if self.finished or self.cancelled: return ''
        out = []
        self._read('', out, final=True)
        if self.leave_tag:
            self._emit(self.leave_tag, out)
            self.leave_tag = self.leave_quote = ''
        if self.pending:
            if any(self.pending.startswith(prefix) for prefix in HISTORY_ANNOTATIONS): self.diagnose('internal_annotation_echo')
            elif not self.pending.lower().startswith(RESERVED): self._emit(self.pending, out)
            else: self.markers += 1; self.diagnose('unfinished_marker')
        if self.task:
            self._emit(re.sub(r'<(?:sticker|meme|expression)\b[^>\n]*>?', '', self.task), out)
        self._emit(self.say.finish(), out)
        if self.bracket: self._emit(self.bracket, out)
        if self.img: self.markers += 1; self.diagnose('html_img_tag')   # 没闭合的 <img 标签：整段不显示
        if self.selection and self.after_text_seen: self.diagnose('mid_text_marker')
        self.pending = self.task = self.bracket = ''; self.img = 0; self.img_quote = ''; self.finished = True
        return ''.join(out)

    def cancel(self):
        self.leave_tag = self.leave_quote = ''
        self.leave_reason = ''
        self.markdown = MarkdownLiterals()
        self.say = SayBlock()
        self.cancelled = True; self.selection = None; self.pending = self.task = self.bracket = ''; self.img = 0; self.img_quote = ''


def clean_expression_history(text):
    """仅清理发给模型的副本，不重写旧聊天；代码 / JSON 中的字面示例仍保留。"""
    parser = ExpressionStream()
    return parser.feed(text)+parser.finish()
