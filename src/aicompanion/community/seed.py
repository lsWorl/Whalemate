"""T21 预置词库：第一次启动时把一批常用梗装进词库，她不用等联网和群聊积累就有底子。

负责人 2026-11-10 16:13 / 16:18：词库是空的，要联网等一段时间才学得到；先预置 B 站、游戏、二次元、抽象圈的常用梗。

- 装成通用义（作用域空），来源标"预置"，可信度 0.55，比联网查到的（0.75）低：
  之后联网学到同一个词的不同说法时，按 T18 的对比流程更新，旧义保留做演变记录。
- 只装"这个预置版本新增的词"：记下已装到第几版（设置 community_seed_version），以后加词只补新的；
  用户在"社区与黑话"页删掉的词不会在下次启动时又冒出来。
- 词库里已经有通用义的词（联网学到的、手动的）不覆盖。
- 不联网、不调模型。
"""
from __future__ import annotations

from aicompanion.community.lexicon import Lexicon
from aicompanion.community.seed_data import SEED, SEED_VERSION

SETTING = 'community_seed_version'
CONFIDENCE = 0.55


def entries(since: int = 0):
    """展开成一个词一条：(词，圈子，语气，含义，用法)。"""
    for names, circle, tone, meaning, usage, version in SEED:
        if version <= since: continue
        for term in names.split('|'):
            if term.strip(): yield term.strip(), circle, tone, meaning, usage


def install(database) -> int:
    """装入还没装过的预置词，返回这次新装了多少个。重复调用没有副作用。"""
    done = int(database.get_setting(SETTING, 0) or 0)
    if done >= SEED_VERSION: return 0
    lexicon = Lexicon(database); added = 0
    for term, circle, tone, meaning, usage in entries(done):
        if any(s['scope'] == '' for s in lexicon.senses(term, include_old=False)): continue   # 已经有通用义：不覆盖
        result = lexicon.learn(term, meaning, origin='seed', usage=usage, tone=tone, circle=circle, source_name='预置词库', confidence=CONFIDENCE)
        if result == 'added':
            lexicon.mark(term, is_jargon=True, inferred_at_count=int((lexicon.info(term) or {}).get('inferred_at_count') or 0)); added += 1
    database.set_setting(SETTING, SEED_VERSION)
    return added
