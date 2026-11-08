"""小型增量解析：图片控制先清理，原 cue/task 留给 CueStream。"""
from __future__ import annotations
import re
from .domain import ASSET_ID

NEW = re.compile(r"""<sticker\s+asset_id=["']([a-z][a-z0-9_-]{7,79})["']\s+position=["'](before_text|after_text)["']\s*/>""")
TEXT_ONLY = re.compile(r"""<expression\s+mode=["']text_only["']\s*/>""")
OLD = re.compile(r"""<meme\s+name=["']([^"'<>]{1,80})["']\s*/>""")
# 旧版曾把内部附件回执拼进 assistant 正文；只剥离这个精确前缀，不把任意方括号当图片。
HISTORY_ANNOTATIONS=('[角色已发送表情：','[角色已发送表情:')
STARTS=('<sticker','<meme','<expression','<task>')+HISTORY_ANNOTATIONS

class ExpressionStream:
    def __init__(self,candidate_ids=(),legacy=None):
        self.allowed=frozenset(candidate_ids);self.legacy=dict(legacy or {})
        self.pending='';self.selection=None;self.reserved=False;self.diagnostics=[]
        self.has_text=False;self.after=False;self.cancelled=False;self.finished=False
        self.fence=0;self.inline=False;self.ticks='';self.tick_start=False;self.line_start=True
        self.quote_line=False;self.json_depth=0;self.json_string=False;self.escape=False
        self.task='';self.drop_line=False;self.drop_annotation=False

    def diagnose(self,code):
        if code not in self.diagnostics and len(self.diagnostics)<16:self.diagnostics.append(code)

    def _emit(self,text,out):
        if self.after and text.strip():
            self.selection=None;self.after=False;self.diagnose('after_has_trailing_text')
        if text.strip():self.has_text=True
        out.append(text)
        for ch in text:
            if ch=='\n':self.line_start=True;self.quote_line=False
            elif not ch.isspace():self.line_start=False

    def _marker(self,raw):
        if self.reserved:
            self.diagnose('multiple_markers');return
        self.reserved=True
        if TEXT_ONLY.fullmatch(raw):self.diagnose('explicit_text_only');return
        match=NEW.fullmatch(raw)
        if match:asset,position=match.groups()
        else:
            match=OLD.fullmatch(raw)
            if not match:
                self.diagnose('invalid_marker');return
            asset=self.legacy.get(match[1]);position='after_text' if self.has_text else 'before_text'
        if not isinstance(asset,str) or not ASSET_ID.fullmatch(asset) or asset not in self.allowed:
            self.diagnose('invalid_asset');return
        if position=='before_text' and self.has_text:
            self.diagnose('late_before');return
        self.selection=(asset,position);self.after=position=='after_text'

    def _char(self,ch,out):
        if self.drop_annotation:
            if ch==']':self.drop_annotation=False
            elif ch=='\n':self.drop_annotation=False;self._emit(ch,out)
            return
        if self.drop_line:
            if ch=='>':self.drop_line=False
            elif ch=='\n':self.drop_line=False;self._emit(ch,out)
            return
        if self.task:
            self.task+=ch
            if self.task.endswith('</task>'):
                # task 是独立控制域，不在这里执行其中的图片写法。
                self._emit(self.task,out);self.task=''
            elif len(self.task)>1530:
                safe=re.sub(r'<(?:sticker|meme|expression)\b[^>\n]*>?','',self.task)
                self._emit(safe,out);self.task=''
            return
        if self.pending:
            self.pending+=ch
            low=self.pending.lower()
            annotation=any(self.pending.startswith(prefix) for prefix in HISTORY_ANNOTATIONS)
            if annotation:
                if ch==']' or ch=='\n' or len(self.pending)>512:
                    self.pending='';self.diagnose('internal_annotation_echo')
                    if ch=='\n':self._emit(ch,out)
                    elif ch!=']':self.drop_annotation=True
                return
            reserved=low.startswith(('<sticker','<meme','<expression'))
            if self.pending=='<task>':
                self.task=self.pending;self.pending='';return
            if reserved and (ch=='>' or ch=='\n' or len(self.pending)>512):
                raw=self.pending;self.pending=''
                if ch=='>':self._marker(raw)
                else:
                    self.reserved=True;self.diagnose('invalid_marker')
                    if ch=='\n':self._emit(ch,out)
                    else:self.drop_line=True
                return
            if reserved or any(s.startswith(low) for s in STARTS):return
            raw=self.pending;self.pending=''
            if raw.startswith('['):
                # 只有已确认不是内部说明的方括号才进入普通 JSON 数组保护。
                self.json_depth=1;self._emit('[',out)
                for part in raw[1:]:self._char(part,out)
            else:self._emit(raw,out)
            return
        if self.ticks:
            if ch==self.ticks[0]:self.ticks+=ch;return
            run=self.ticks;self.ticks=''
            if self.tick_start and len(run)>=3:
                self.fence=0 if self.fence else len(run)
            elif not self.fence and run[0]=='`':self.inline=not self.inline
            self._emit(run,out)
        if ch in ('`','~') and not self.json_depth:
            self.ticks=ch;self.tick_start=self.line_start;return
        if self.fence or self.inline or self.quote_line:
            self._emit(ch,out);return
        if self.json_depth:
            if self.escape:self.escape=False
            elif ch=='\\' and self.json_string:self.escape=True
            elif ch=='"':self.json_string=not self.json_string
            elif not self.json_string:
                if ch in '{[':self.json_depth+=1
                elif ch in '}]':self.json_depth-=1
            self._emit(ch,out);return
        if ch=='>' and self.line_start:self.quote_line=True
        if ch=='{':self.json_depth=1
        if ch in '<[':self.pending=ch
        else:self._emit(ch,out)

    def feed(self,chunk):
        if self.cancelled or self.finished:return ''
        out=[]
        for ch in str(chunk):self._char(ch,out)
        return ''.join(out)

    def finish(self):
        if self.finished or self.cancelled:return ''
        out=[]
        if self.pending:
            if any(self.pending.startswith(prefix) for prefix in HISTORY_ANNOTATIONS):self.diagnose('internal_annotation_echo')
            elif not self.pending.lower().startswith(('<sticker','<meme','<expression')):self._emit(self.pending,out)
            else:self.diagnose('unfinished_marker')
        if self.task:
            self._emit(re.sub(r'<(?:sticker|meme|expression)\b[^>\n]*>?','',self.task),out)
        if self.ticks:self._emit(self.ticks,out)
        self.pending=self.task=self.ticks='';self.finished=True
        return ''.join(out)

    def cancel(self):
        self.cancelled=True;self.selection=None;self.pending=self.task=self.ticks=''


def clean_expression_history(text):
    """仅清理发给模型的副本，不重写旧聊天；代码 / JSON 中的字面示例仍保留。"""
    parser=ExpressionStream()
    return parser.feed(text)+parser.finish()
