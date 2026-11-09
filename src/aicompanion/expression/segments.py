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

QQ 另有一套（负责人 2026-11-09 18:05："不要一次性生成这么多回复，不像人在手敲；不要用空格作间断，
如'哟 逆天这次都用上了'，可以搞成两次回复，每次间隔一段时间；没必要每次都回 2～3 句"），参考 MaiBot 的分句：
- "，"照常保留，不换成空格；
- 一行里夹在中文之间的空格当作"这里本来想分两条发"：拆开（英文、数字之间的空格不动）；
- 拆出来的短句按概率合并回去（整段越短越容易合并，用"，"连），所以大多数时候是一条、偶尔两条；
- 最多 QQ_MAX_SEGMENTS 条，多出来的并进最后一条；
- 每条之间的等待时间按打字速度算（typing_delays），插件按这个间隔一条条发。
"""
from __future__ import annotations
import random
import re


MAX_SEGMENTS = 6
QQ_MAX_SEGMENTS = 3
CJK = '\u4e00-\u9fff\u3400-\u4dbf'
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


def _qq_pieces(line: str) -> list[str]:
    """QQ 的一行：引号外的"。"和夹在中文之间的空格处断开，"，"保留，去掉句末句号和逗号。"""
    line = re.sub(r'。{2,}', '…', line.strip())
    parts, current, depth = [], '', 0
    for i, ch in enumerate(line):
        if ch in OPEN and not (ch == '"' and depth):
            depth += 1; current += ch; continue
        if ch in CLOSE and depth:
            depth -= 1; current += ch; continue
        if depth == 0 and ch == '。':
            parts.append(current); current = ''; continue
        if depth == 0 and ch in ' \u3000' and current.strip():
            rest = line[i+1:].lstrip(' \u3000')
            prev = current.rstrip()[-1:]
            if rest and not (re.match(r'[A-Za-z0-9]', prev) or re.match(r'[A-Za-z0-9]', rest[0])):
                parts.append(current); current = ''; continue
        current += ch
    parts.append(current)
    return [re.sub(r'[，,\s]+$', '', p).strip() for p in parts if p.strip()]


def qq_segments(text: str, chance=random.random, limit: int = QQ_MAX_SEGMENTS) -> list[str]:
    """QQ 上分几条发。换行是她明确想分开发的地方，只在超过条数时才并；一行里拆出来的碎句按概率合并。"""
    text = str(text or '').replace('\r\n', '\n')
    if is_long_form(text): return [text.strip()] if text.strip() else []
    total = len(re.sub(r'\s', '', text))
    merge = 0.8 if total < 12 else 0.4 if total < 32 else 0.3      # MaiBot：越短越倾向一条发完
    segments = []
    for line in text.split('\n'):
        pieces = _qq_pieces(line)
        merged = []
        for piece in pieces:
            if merged and chance() < merge: merged[-1] = merged[-1]+'，'+piece
            else: merged.append(piece)
        segments.extend(merged)
    if len(segments) > limit: segments = segments[:limit-1]+['，'.join(segments[limit-1:])]
    return segments


def typing_delays(segments: list[str], chance=random.random) -> list[float]:
    """每条消息发出前等多久（第一条为 0，生成本身就是"想"的时间）。参考 MaiBot calculate_typing_time：
    中文约 0.3 秒一个字、其他 0.15 秒，加 0.3 秒回车，再上下浮动 20%；夹在 1～8 秒。"""
    delays = []
    for index, text in enumerate(segments):
        if index == 0: delays.append(0.0); continue
        cjk = len(re.findall(f'[{CJK}]', text))
        base = 0.3*cjk+0.15*(len(text)-cjk)+0.3
        delays.append(round(min(8.0, max(1.0, base*(0.8+0.4*chance()))), 2))
    return delays
