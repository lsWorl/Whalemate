"""语气词：语音不再把整段回复念出来，只发一声"嘿嘿""诶？"这样的反应，文字留在气泡里。

挑哪一声：先看回复里已经出现的表情标注，再看第一句的标点，最后看当前心情。不依赖 Qt。
"""
from __future__ import annotations

import json
from pathlib import Path
import random

STYLES = ('interjection', 'brief', 'full')   # 只说语气词 / 语气词 + 第一句短话 / 完整朗读
BRIEF_LIMIT = 30

DEFAULT = {
    'neutral': ['嗯嗯', '好哒', '嗯', '这样呀'],
    'happy': ['嘿嘿', '好耶', '哈哈', '嘻嘻'],
    'love': ['呜哇', '最喜欢了'],
    'surprised': ['诶？', '哇', '欸——'],
    'question': ['嗯？', '诶？'],
    'excited': ['哇！', '好耶！'],
    'shy': ['唔……', '诶嘿'],
    'smug': ['哼哼', '那当然'],
    'sleep': ['呼啊……', '嗯……'],
    'sad': ['呜……', '唉'],
    'angry': ['哼', '真是的'],
    'worried': ['诶诶', '呜哇'],
    'agree': ['好呀', '收到', '交给我'],
}
EXPRESSION_TO_KIND = {'happy': 'happy', 'love': 'love', 'surprised': 'surprised', 'shy': 'shy', 'smug': 'smug',
                      'sleep': 'sleep', 'drag': 'worried', 'dizzy': 'worried', 'neutral': 'neutral'}
MOOD_TO_KIND = {'happy': 'happy', 'sad': 'sad', 'angry': 'angry', 'shy': 'shy', 'lonely': 'sad', 'calm': 'neutral'}


def load(pack_dir: Path | None) -> dict:
    table = {k: list(v) for k, v in DEFAULT.items()}
    if pack_dir and (Path(pack_dir)/'interjections.json').exists():
        try:
            data = json.loads((Path(pack_dir)/'interjections.json').read_text(encoding='utf-8'))
            for k, v in data.items():
                if isinstance(v, list) and v: table[k] = [str(x) for x in v]
        except (OSError, ValueError):
            pass
    return table


def kind_for(first_sentence: str, expressions: list[str], mood: str = 'calm') -> str:
    for name in expressions:
        if name in EXPRESSION_TO_KIND: return EXPRESSION_TO_KIND[name]
    s = (first_sentence or '').strip()
    if s.endswith(('？', '?')) or s.startswith(('诶', '咦', '欸')): return 'question'
    if any(w in s[:12] for w in ('好的', '好呀', '没问题', '可以', '收到', '交给我')): return 'agree'
    if s.endswith(('！', '!')): return 'excited'
    return MOOD_TO_KIND.get(mood, 'neutral')


def pick(table: dict, kind: str, rng: random.Random | None = None, avoid: str = '') -> str:
    rng = rng or random
    options = table.get(kind) or table.get('neutral') or ['嗯']
    if len(options) > 1 and avoid in options: options = [o for o in options if o != avoid]
    return rng.choice(options)


def brief_text(sentence: str) -> str:
    """"语气词 + 第一句"模式下第一句太长就不念。"""
    s = (sentence or '').strip()
    return s if 0 < len(s) <= BRIEF_LIMIT else ''
