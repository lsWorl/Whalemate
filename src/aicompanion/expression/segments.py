"""把一条回复拆成几条消息：她想连发几句时，桌面显示成几个气泡、QQ 分几条发出去，而不是一条里塞满空行。

聊天短句的写法（用户 2026-11-09 要求，桌面和 QQ 共用）：像发微信一样不用"，""。"分句——
- 每一行就是一条消息（单个换行也拆，空行照旧拆）；
- 一行里的"。"当作"这句说完了"，后面的话另起一条；连着的"。。。"当省略号，换成"…"；
- 句子里的"，"换成空格（真人聊天常用空格代替逗号停顿）；
- 问号、感叹号、～、…、emoji 都保留；引号 / 书名号里的内容不拆，避免把别人说的话拆散。

不拆的情况：
- 像文档一样的回复（代码块、列表、标题、引用，或者很长的讲解）整条原样：一份请假说明、一段技术解释拆成一堆气泡反而难读，标点也照常保留。
- 最多 MAX_SEGMENTS 条，多出来的并进最后一条（换行连接），避免刷屏。
- 只影响展示 / 发送；数据库里仍是一轮一条完整回复（模型看到的历史、TTS 朗读的文字不变）。
"""
from __future__ import annotations
import re

MAX_SEGMENTS = 6
LONG_FORM = 600
FENCE = re.compile(r'^\s*(```|~~~)', re.M)
STRUCTURE = re.compile(r'^\s*(?:[-*+] |\d+[.)、] |#{1,6} |> |\|)', re.M)
OPEN, CLOSE = '“「『《（(‘"', '”」』》）)’"'


def is_long_form(text: str) -> bool:
    return len(text) > LONG_FORM or bool(FENCE.search(text) or STRUCTURE.search(text))


def _sentences(line: str) -> list[str]:
    """一行聊天短句：在引号外的"。"处断开，"，"换成空格，去掉句末句号。"""
    line = re.sub(r'。{2,}', '…', line)
    parts, current, depth = [], '', 0
    for ch in line:
        if ch in OPEN and not (ch == '"' and depth):
            depth += 1; current += ch; continue
        if ch in CLOSE and depth:
            depth -= 1; current += ch; continue
        if depth == 0 and ch == '。':
            parts.append(current); current = ''; continue
        if depth == 0 and ch == '，':
            current += ' '; continue
        current += ch
    parts.append(current)
    return [re.sub(r'\s{2,}', ' ', p).strip() for p in parts if p.strip()]


def split_segments(text: str, limit: int = MAX_SEGMENTS) -> list[str]:
    text = str(text or '').replace('\r\n', '\n')
    if is_long_form(text): return [text.strip()] if text.strip() else []
    parts = [sentence for line in text.split('\n') for sentence in _sentences(line)]
    if len(parts) > limit: parts = parts[:limit-1]+['\n'.join(parts[limit-1:])]
    return parts
