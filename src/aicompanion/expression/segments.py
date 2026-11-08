"""把一条回复拆成几条消息：她想连发几句时，桌面显示成几个气泡、QQ 分几条发出去，而不是一条里塞满空行。

拆分规则（桌面和 QQ 共用）：
- 按空行分段（人设和提示要求"连发几条消息时每条之间空一行"）；单个换行留在同一条里。
- 代码块（``` / ~~~）里的空行不拆，整个代码块和它所在的段落保持在一条消息里。
- 最多 MAX_SEGMENTS 条，多出来的并进最后一条，避免刷屏。
- 像文档一样的回复（代码块、列表、标题、引用，或者很长的讲解）不拆：一份请假说明、一段技术解释拆成五个气泡反而难读。
- 只影响展示 / 发送；数据库里仍是一轮一条完整回复，模型看到的历史也不变。
"""
from __future__ import annotations
import re

MAX_SEGMENTS = 5
LONG_FORM = 600
FENCE = re.compile(r'^\s*(```|~~~)', re.M)
STRUCTURE = re.compile(r'^\s*(?:[-*+] |\d+[.)、] |#{1,6} |> |\|)', re.M)


def is_long_form(text: str) -> bool:
    return len(text) > LONG_FORM or bool(FENCE.search(text) or STRUCTURE.search(text))


def split_segments(text: str, limit: int = MAX_SEGMENTS) -> list[str]:
    text = str(text or '').replace('\r\n', '\n')
    if is_long_form(text): return [text.strip()] if text.strip() else []
    segments, current, fence, blank = [], [], None, False
    for line in text.split('\n'):
        mark = FENCE.match(line)
        if fence:
            current.append(line)
            if mark and mark.group(1) == fence: fence = None
            continue
        if mark:
            if blank and current: segments.append(current); current = []
            blank = False; fence = mark.group(1); current.append(line); continue
        if not line.strip():
            blank = True; continue
        if blank and current: segments.append(current); current = []
        blank = False; current.append(line)
    if current: segments.append(current)
    parts = ['\n'.join(lines).strip() for lines in segments]
    parts = [p for p in parts if p]
    if len(parts) > limit: parts = parts[:limit-1]+['\n\n'.join(parts[limit-1:])]
    return parts
