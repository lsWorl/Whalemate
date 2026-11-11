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
如'哟 逆天这次都用上了'，可以搞成两次回复，每次间隔一段时间"；18:27 补充："不是限制最多几条，要有不固定的频率，
根据情况和上下文而定"）。参考 MaiBot 的分句合并和真实聊天记录（约六成消息一句，其余两三句）：
- 发几条首先由她自己决定：换行就是另一条，这里不砍、不并（负责人 2026-11-11 15:46：不用防刷屏，按打字速度慢慢发就不会刷屏；
  QQ_SEGMENTS_MAX 只是插件收消息时的格式上限，正常回复到不了）；
- "，"照常保留，不换成空格；
- 一行里夹在中文之间的空格、"。"：当作"这里可能想分两条发"。拆不拆按此刻的聊天节奏（chat_burst）：
  情绪激动、关系亲近、内容长 → 更容易拆开（对方连发不算：连发的几条会先合并成一次回复）；随口应一句、关系一般 → 多半合成一条（用"，"连）；
- 每条之间的等待时间按打字速度算（typing_delays），聊得越快越短，偶尔会像想了一下再补一句那样停久一点。
"""
from __future__ import annotations
import random
import re


MAX_SEGMENTS = 6
QQ_SEGMENTS_MAX = 64    # 和插件 MAX_SEGMENTS 一致：插件只接受这么多段（防异常输入），不是"最多几条"的设计
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


# 列举一行（"上半：玛薇卡 茜特拉莉 希诺宁 班尼特"、"雷神 八重 久岐忍 瑶瑶"）里的空格是分隔符，不是"想分两条发"。
# 2026-11-11 群聊真机记录：配队这类回答按空格拆成一个名字一条，再被"不熟最多三条"截掉，对方只看到"行""换一套…""上半：玛薇卡"。
LABELED = re.compile(r'^[^\s：:]{1,8}[：:]\s*\S')
LIST_WORD_MAX = 6


def is_listing(line: str) -> bool:
    words = line.split()
    return bool(LABELED.match(line)) or (len(words) >= 4 and all(len(w) <= LIST_WORD_MAX for w in words))


def _qq_pieces(line: str, casual: bool = False) -> list[tuple[str, str]]:
    """QQ 的一行：引号外的"。"和夹在中文之间的空格处断开，"，"保留，去掉句末句号和逗号。
    返回（碎句，它前面的断点）。casual（和外人聊）时"，"也算一个可能的断点，句尾的顿号、分号、单个英文句点也去掉。
    列举的一行（is_listing）整行一条，不拆。"""
    line = re.sub(r'。{2,}', '…', line.strip())
    if is_listing(line):
        tail = r'[，,、；;。\s]+$' if casual else r'[，,\s]+$'
        return [(re.sub(tail, '', line), '')] if re.sub(tail, '', line) else []
    parts, current, depth, mark = [], '', 0, ''
    def cut(ch):
        nonlocal current, mark
        parts.append((current, mark)); current = ''; mark = ch
    for i, ch in enumerate(line):
        if ch in OPEN and not (ch == '"' and depth):
            depth += 1; current += ch; continue
        if ch in CLOSE and depth:
            depth -= 1; current += ch; continue
        if depth == 0 and ch == '。':
            cut('。'); continue
        if casual and depth == 0 and current.strip():
            # 和外人聊不用逗号、分号：这些地方都是可能另起一条的断点。英文逗号只在前面是中文时才算（"1,000""a, b" 不拆）
            if ch in '，；;' or (ch == ',' and re.match('[%s]' % CJK, current.rstrip()[-1:])):
                cut('，'); continue
            # 问号、感叹号后面还有话：标点留在前半句，后半句另算一段
            if ch in '？！?!' and line[i+1:i+2] and line[i+1] not in '？！?!…~～"”」』）)':
                current += ch; cut('？'); continue
        if depth == 0 and ch in ' \u3000' and current.strip():
            rest = line[i+1:].lstrip(' \u3000')
            prev = current.rstrip()[-1:]
            if rest and not (re.match(r'[A-Za-z0-9]', prev) or re.match(r'[A-Za-z0-9]', rest[0])):
                cut(' '); continue
        current += ch
    parts.append((current, mark))
    tail = r'(?:[，,、；;\s]|(?<=[%s])\.(?!\.))+$' % CJK if casual else r'[，,\s]+$'
    return [(re.sub(tail, '', p).strip(), m) for p, m in parts if re.sub(tail, '', p).strip()]


CASUAL_GLUE = 10    # 和外人聊：合在一条里的两小段加起来不超过这么多字，就直接连着写、不加逗号（"嗯知道了"）


def qq_segments(text: str, chance=random.random, burst: float = 0.5, limit: int = QQ_SEGMENTS_MAX, casual: bool = False) -> list[str]:
    """QQ 上分几条发。换行处一定分开（她自己想分开发）；一行里拆出来的碎句按聊天节奏决定拆还是合。

    casual（负责人 2026-11-10 13:51 / 18:44："尽量不要用标点符号，切成多次对话回复"，实测留着逗号仍然显得标点多）：
    和外人聊完全不用逗号、句号、分号——这些地方要么另起一条，要么两小段很短时直接连着写（"嗯知道了"）。
    问号、感叹号、省略号、"？？？"这类带情绪的照留；顿号留着（"苹果、香蕉"拆开没法读）。
    条数由调用方按她对这个人的态度截（只截零碎话，见 fit_cap）；这里不限条数，只在超过插件的格式上限时把多出来的并进最后一条。
    像文档的长回复（讲解、代码、列表）不动，标点照常。"""
    text = str(text or '').replace('\r\n', '\n')
    if is_long_form(text): return [text.strip()] if text.strip() else []
    keep_apart = max(0.1, min(0.9, burst))
    segments = []
    for line in text.split('\n'):
        merged = []
        for piece, mark in _qq_pieces(line, casual):
            if casual:
                # 不留逗号：两小段很短（合起来不超过 CASUAL_GLUE 个字）才可能直接连着写，否则就是另一条消息。
                # 逗号处更愿意连着写（概率减半），句号 / 问号处按聊天节奏
                short = bool(merged) and len(merged[-1])+len(piece) <= CASUAL_GLUE and not re.search(r'[A-Za-z0-9？！?!…]$', merged[-1])
                apart = keep_apart*0.5 if mark == '，' else keep_apart
                if short and chance() >= apart: merged[-1] += piece
                else: merged.append(piece)
            elif merged and chance() >= keep_apart: merged[-1] = merged[-1]+'，'+piece
            else: merged.append(piece)
        segments.extend(merged)
    # 超过插件的格式上限（正常回复到不了）：多出来的并进最后一条，用换行隔开，不用逗号硬连
    if len(segments) > limit: segments = segments[:limit-1]+['\n'.join(segments[limit-1:])]
    return segments


def chat_burst(reply: str, *, peer_burst: int = 1, closeness: float = 0.4) -> float:
    """此刻的聊天节奏（0.1～0.9）：越高越像"一句话拆成几条连发"，也打得越快。
    依据：对方刚才连发了几条（跟着对方的节奏）、她这条话的情绪（感叹、问号连发、哈哈哈、笑死）、内容长短、和对方多亲近。"""
    text = str(reply or '')
    value = 0.35
    if len(re.sub(r'\s', '', text)) > 30: value += 0.15
    if re.search(r'[！!]|[？?]{2,}|哈{3,}|笑死|草{2,}|卧槽|救命|啊{2,}', text): value += 0.2
    # 2026-11-09 实测：对方连发时她也跟着连发，越聊越刷屏。对方连发不再让她多拆（连发的几条已在桥接里合并成一次回复）
    value += 0.25*(closeness-0.4)
    return round(max(0.1, min(0.9, value)), 2)


def typing_delays(segments: list[str], chance=random.random, burst: float = 0.5) -> list[float]:
    """每条消息发出前等多久（第一条为 0，生成本身就是"想"的时间）。参考 MaiBot calculate_typing_time：
    中文 0.2～0.35 秒一个字（聊得越快越快）、其他字符减半，加 0.3 秒回车，上下浮动 20%；
    偶尔（约 15%）像想了一下再补一句，多停 2～5 秒。夹在 0.6～12 秒。"""
    delays = []
    per_char = 0.35-0.15*max(0.0, min(1.0, burst))
    for index, text in enumerate(segments):
        if index == 0: delays.append(0.0); continue
        cjk = len(re.findall(f'[{CJK}]', text))
        value = (per_char*cjk+per_char/2*(len(text)-cjk)+0.3)*(0.8+0.4*chance())
        if chance() < 0.15: value += 2+3*chance()
        delays.append(round(min(12.0, max(0.6, value)), 2))
    return delays


# 能明确认定“没有实质内容”的语气碎片：叹词、标点，以及几句固定的敷衍话。只有超出条数的部分全是这种碎片才截掉
FILLER = re.compile(r'^(?:[哦噢喔嗯唔啊呀哈呵嘿嘻诶欸哎唉呃哼额嘛吧呢啦咯么～~…。.!！?？\s]|行吧|好吧|随便|随你|算了|无所谓|就这样|没了|懒得说)+$')


def fit_cap(text: str, segments: list[str], cap: int | None) -> tuple[list[str], bool]:
    """按态度限制条数（冷淡一句、不熟最多三条）。返回（要发的几条，有没有丢掉内容）。

    态度主要靠提示词影响她写多少；这里只做保守的收尾：超出条数的部分全是能明确认定没有实质内容的语气碎片（FILLER）才截掉，
    拿不准就全发。名单（"雷神""八重神子"）、短步骤（"再断电""最后清洁"）字数再短也是正文，不能按字数删
    （Codex PR #40 第二轮第 1 项；原问题见 2026-11-11 群聊："没说完呢""怎么不说话了"）。
    text 暂不使用，保留参数便于以后按整段内容判断。"""
    if not cap or len(segments) <= cap: return segments, False
    if all(FILLER.match(piece.strip()) for piece in segments[cap:]): return segments[:cap], True
    return segments, False
