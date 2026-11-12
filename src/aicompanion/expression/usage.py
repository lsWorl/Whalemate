"""向群友学发表情包的习惯（用户 2026-11-09 决定：从她所在的 QQ 群和私聊里学）。

每当别人发了一张表情包，记下一条"用法"：发图前大家在说什么（情境，截短到 60 字）→ 发的是哪张图。
她自己回复时，拿眼前的对话和这些情境比：
- 和眼前情境像的用法，对应的图排进候选前面（不再只靠写死的情绪分组和词面相关度）；
- 大家常发的图稍微靠前；
- 在同一个群里学到的用法，会把当时的情境写进候选说明（"群友在「…」时发过"），让她照着大家的习惯用；
  别处学到的只写"群友常用"，不把一个群的聊天内容带到别的群或桌面。

只存在本机设置里，环形保留最近 LIMIT 条；不存发图人身份、不上传。"""
from __future__ import annotations

import math
import re
import time

KEY = 'sticker_usage:'
LIMIT = 800
CUE_CHARS = 60
PLACEHOLDER = re.compile(r'\[(?:图片|收到图片|发了表情包[^\]]*|对方发了表情包[^\]]*)\]')
SPEAKER = re.compile(r'^(?:【[^】\n]{1,6}】)?[^：\n]{0,30}#\d+：')


def clean_cue(text: str) -> str:
    """去掉群聊记录前面的"昵称#编号："、图片占位和多余空白。"""
    text = SPEAKER.sub('', str(text or ''))
    text = PLACEHOLDER.sub(' ', text)
    text = re.sub(r'\[图片描述[\s\S]*$', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()[:CUE_CHARS]


def grams(text: str) -> set[str]:
    text = re.sub(r'[\s，。！？、,.!?~～…：:；;“”"\'（）()]+', '', str(text or '').lower())
    if len(text) < 2: return {text} if text else set()
    return {text[i:i+2] for i in range(len(text)-1)}


def record(database, character_id: str, asset_id: str, cue: str, scope_tag: str, clock=time.time) -> None:
    cue = clean_cue(cue)
    if not asset_id: return
    def write(c):
        ring = database.setting_in(c, KEY+character_id, []) or []
        ring = [e for e in ring if isinstance(e, dict)][-(LIMIT-1):]
        ring.append({'a': asset_id, 'c': cue, 's': scope_tag, 't': round(clock())})
        database.set_setting_in(c, KEY+character_id, ring)
    database.locked(write)


def learned(database, character_id: str, text: str, scope_tag: str = '') -> dict[str, tuple[float, str]]:
    """asset_id → (加分, 候选说明)。加分 = 情境相似度 ×8 + 常用程度（最多 +2）。"""
    ring = [e for e in database.get_setting(KEY+character_id, []) or [] if isinstance(e, dict) and e.get('a')]
    if not ring: return {}
    now = grams(text)
    best: dict[str, tuple[float, str, str]] = {}
    counts: dict[str, int] = {}
    for entry in ring:
        asset = entry['a']; counts[asset] = counts.get(asset, 0)+1
        cue_grams = grams(entry.get('c', ''))
        sim = len(now & cue_grams)/max(1, min(len(now), len(cue_grams))) if now and cue_grams else 0.0
        if sim > best.get(asset, (0.0, '', ''))[0]: best[asset] = (sim, entry.get('c', ''), entry.get('s', ''))
    result = {}
    for asset, count in counts.items():
        sim, cue, scope = best.get(asset, (0.0, '', ''))
        score = sim*8 + min(2.0, math.log2(1+count))
        if sim >= 0.34 and cue and scope and scope == scope_tag: note = f'群友在「{cue}」时发过'
        elif count >= 2: note = f'群友常用（{count} 次）'
        elif sim >= 0.34: note = '群友在类似的时候发过'
        else: note = ''
        result[asset] = (round(score, 2), note)
    return result
