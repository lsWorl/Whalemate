"""智能朗读（计划书 v4 功能八）：短回复全念；长回复只念适合听的部分，分成几段先后播放，其余看气泡。

为什么不额外调模型：回复是流式生成的，第一段一出来就要念（首次出声不能变慢）；而且“一个大脑”原则下不为口播单独再问一次。
所以这里按规则取舍：
- 先念开头：累计念到 SHORT 字以前的句子都念（120 字以内的回复因此全念）；
- 之后只念要紧的：问你的问题、需要你做决定或去处理的事、结论；结尾那句短的情绪话留到最后补上；
- 压缩：清单只念条数和第一条；表格念成“我做了张表”；代码已由 SentenceBuffer 念成“一段代码”；
- 跳过：重复的话、长段解释的中间部分；
- 一轮念出来的总长度不超过 TOTAL 字（约 60～75 秒）；有内容省掉时最后补一句短提示，一轮最多一次。
分段：每段 1～3 句、约 40～120 字；第一段只放一句，好让她尽快出声。
数值都是初值，按体感调整。
"""
from __future__ import annotations

import random
import re

from aicompanion.speech.output import spoken_text

SHORT = 120
TOTAL = 300
SEGMENT_MIN = 40
SEGMENT_MAX = 120
ENDING_MAX = 40
LIST_ITEM = re.compile(r'^\s*(?:[-*•·]|\d{1,2}[.、)）]|[（(]\d{1,2}[)）])\s*')
TABLE_ROW = re.compile(r'^\s*\|')
# 长回复里还要念的句子：问你的问题、要你做决定或去处理的事、结论。只认冲着“你”说的和句首的结论词，
# 正文里顺带出现的“确认”“完成了”不算（否则长篇过程描述会把预算占满）
IMPORTANT = re.compile(r'[？?]|需要你|麻烦你|请你|你要|你得|你可以|你来|你看看|记得|别忘|要不要|你确认|帮我确认|你决定|^\s*(?:结论|总之|总的来说|所以|结果|最后|建议)')
TAILS = ('剩下的写在气泡里啦', '细节我都写在气泡里了', '其余的看气泡就好', '后面的你看字吧')


CLAUSE = re.compile(r'(?<=[，,；;、：:])')


def _clauses(text: str) -> list[str]:
    """把一句长话切成不超过 SEGMENT_MAX 字的小句：先按逗号、分号、顿号、冒号，还长就硬切。"""
    out, current = [], ''
    for part in CLAUSE.split(text):
        while len(part) > SEGMENT_MAX:
            if current: out.append(current); current = ''
            out.append(part[:SEGMENT_MAX]); part = part[SEGMENT_MAX:]
        if current and len(current)+len(part) > SEGMENT_MAX // 2:
            out.append(current); current = ''
        current += part
    if current: out.append(current)
    return [c for c in out if c.strip()]


class SmartReader:
    def __init__(self, short: int = SHORT, total: int = TOTAL, rng: random.Random | None = None, tails=TAILS):
        self.short, self.total = short, total
        self.rng, self.tails = rng or random.Random(), tails
        self.spoken = 0                 # 已经安排念的字数
        self.segments = 0               # 已经交出去的段数
        self.buffer: list[str] = []     # 还没凑成一段的句子
        self.items: list[str] = []      # 正在收集的清单项
        self.in_table = False
        self.skipped = False            # 有没有省掉内容（决定要不要补“剩下的看气泡”）
        self.seen: set[str] = set()
        self.last_skipped = ''          # 最近一句被跳过的话；如果它就是最后一句、又短，结尾补上（多半是情绪话）
        self.last_was_skipped = False

    # ---- 输入 --------------------------------------------------------------------------------
    def feed(self, sentence: str) -> list[str]:
        """喂入一句（SentenceBuffer 切出来的原文），返回现在就可以念的段。"""
        raw = (sentence or '').strip()
        if not raw: return []
        out = []
        if LIST_ITEM.match(raw) and not TABLE_ROW.match(raw):
            out += self._close_table()
            item = spoken_text(LIST_ITEM.sub('', raw, count=1))
            if item: self.items.append(item)
            return out
        if TABLE_ROW.match(raw):
            out += self._close_list()
            self.in_table = True
            return out
        out += self._close_list() + self._close_table()
        return out + self._sentence(spoken_text(raw))

    def finish(self) -> list[str]:
        """回复生成完：收尾清单 / 表格，补上结尾的短情绪话，交出最后一段，必要时补一句“剩下的看气泡”。"""
        out = self._close_list() + self._close_table()
        if self.last_was_skipped and self.last_skipped and len(self.last_skipped) <= ENDING_MAX \
                and self.spoken+len(self.last_skipped) <= self.total+ENDING_MAX:
            self._take(self.last_skipped); self.last_was_skipped = False
        out += self._flush()
        if self.skipped and self.tails:
            out.append(self.rng.choice(self.tails)); self.skipped = False
        return out

    # ---- 规则 --------------------------------------------------------------------------------
    def _sentence(self, text: str) -> list[str]:
        if not text: return []
        if len(text) > SEGMENT_MAX:
            # 一句太长（逗号连成一大串、或者办事结果一次交过来）：先按逗号、分号切成小句再逐个取舍，
            # 这样不管正文是一个字一个字流过来还是一次给完，念出来的都一样，也不会整段长句原样念（Codex PR #39 第 3 项）
            out = []
            for clause in _clauses(text): out += self._sentence(clause)
            return out
        key = re.sub(r'\W', '', text)
        if key and key in self.seen:   # 重复的话不念第二遍
            return []
        if self.spoken < self.short:
            return self._accept(text, key)
        if IMPORTANT.search(text) and self.spoken+len(text) <= self.total:
            return self._accept(text, key)
        self.skipped = True; self.last_skipped = text; self.last_was_skipped = True
        return []

    def _accept(self, text, key):
        if key: self.seen.add(key)
        self.last_was_skipped = False
        self._take(text)
        return self._ready()

    def _take(self, text):
        self.buffer.append(text); self.spoken += len(text)

    def _ready(self) -> list[str]:
        """第一段只放一句（尽快出声）；之后凑够 SEGMENT_MIN 字或三句就交出去，单段不超过 SEGMENT_MAX 字。"""
        joined = ''.join(self.buffer)
        if self.segments == 0 or len(joined) >= SEGMENT_MIN or len(self.buffer) >= 3:
            return self._flush()
        return []

    def _flush(self) -> list[str]:
        if not self.buffer: return []
        out, current = [], ''
        for part in self.buffer:
            if current and len(current)+len(part) > SEGMENT_MAX:
                out.append(current); current = ''
            current += part
        if current: out.append(current)
        self.buffer = []; self.segments += len(out)
        return out

    def _close_list(self) -> list[str]:
        if not self.items: return []
        items, self.items = self.items, []
        if len(items) == 1: return self._sentence(items[0])
        self.skipped = True
        first = items[0] if len(items[0]) <= 30 else items[0][:28]+'…'
        return self._summary(f'我列了{len(items)}条，第一条是{first}')

    def _close_table(self) -> list[str]:
        if not self.in_table: return []
        self.in_table = False; self.skipped = True
        return self._summary('我做了张表')

    def _summary(self, text: str) -> list[str]:
        """清单、表格的一句概括也占整轮预算：好几组清单 / 表格时，超出预算的就不再念（看气泡）。"""
        if self.spoken >= self.short and self.spoken+len(text) > self.total: return []
        return self._accept(text, '')
