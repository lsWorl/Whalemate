"""记忆召回基础（计划书 v4 功能六，阶段二）：记忆卡之外，相关时还能想起——

1. **存档里的旧事**：自然遗忘（清晰度太低或记忆卡满了被挤出）的记忆，聊到相关的事时带上，并注明“是旧事、记不太清”。
   用户在记忆页删掉的不会出现：删除时存档里同一事实一起删掉，墓碑挡住同一个事实键。
2. **更早的聊天**：最近 30 条之外的聊天，按关键词在全文索引里找（storage/fts.py），只找同一个频道（群里的话不会出现在桌面，
   反过来也一样），也不越过“删除记忆”时设下的水位线。
3. **按关联直接查**：提到以前让她办过的事，带上那次办事的目标和结果。只在私下（桌面 / 主人私聊）用。

关键词靠中文二字 / 三字片段的重合度，不需要分词库，也不调模型（不额外花钱、不增加延迟）。同义词和省略说法要等语义检索（阶段四）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from aicompanion.brain.memory_card import visible_in
from aicompanion.storage import fts

# 太常见、几乎每句话都有的二字片段：不算“相关”
STOP = set('我们 你们 他们 今天 明天 昨天 现在 什么 怎么 这个 那个 一下 一个 没有 不是 就是 还是 可以 知道 觉得 时候 自己 '
           '真的 好像 感觉 因为 所以 但是 然后 如果 已经 还有 一点 有点 这么 那么 为什么 哈哈 嘿嘿 呜呜 你的 我的 他的 她的 '
           '的人 了吗 了吧 是不 不要 要不 吗？ 呢？'.split())
ARCHIVE_LIMIT, CHAT_LIMIT, TASK_LIMIT = 3, 3, 2
CHAT_SNIPPET = 120
SCAN_ARCHIVE = 2000
RARE_HITS = 5      # 一个二字词在更早的聊天里只出现这么几次，就当作专有的词（名字、地名），单独命中也算相关


def grams(text: str, n: int) -> set[str]:
    out = set()
    for segment in fts.segments(text.lower()):
        out |= {segment[i:i+n] for i in range(len(segment)-n+1)}
    return out - STOP if n == 2 else out


def score(query: str, text: str) -> int:
    """相关度：二字片段重合数 + 三字片段重合数×2。0 表示不相关。"""
    q2, q3 = grams(query, 2), grams(query, 3)
    text = text.lower()
    return sum(1 for g in q2 if g in text) + 2*sum(1 for g in q3 if g in text)


@dataclass
class Recalled:
    archive: list = field(default_factory=list)    # [(row, score)]
    chats: list = field(default_factory=list)      # [(row, score)]
    tasks: list = field(default_factory=list)      # [(row, score)]

    def render(self, now: datetime | None = None, limit_chars: int = 1200) -> str:
        """写进提示词的一段；超过 limit_chars 时从相关度低的开始丢。"""
        now = now or datetime.now()
        lines = []
        for row, _ in self.archive:
            lines.append(f"- （旧事，记不太清了）{row['content']}")
        for row, _ in self.tasks:
            state = {'done': '做完了', 'failed': '没做成', 'cancelled': '中途停了', 'interrupted': '被打断了'}.get(row['status'], row['status'])
            lines.append(f"- （{ago(row['started_at'], now)}帮对方办过事，{state}）{row['goal'][:80]}")
        for row, _ in self.chats:
            who = '对方' if row['role'] == 'user' else '你'
            text = ' '.join(row['content'].split())
            text = text[:CHAT_SNIPPET]+('…' if len(text) > CHAT_SNIPPET else '')
            lines.append(f"- （{ago(row['created_at'], now)}聊过）{who}：{text}")
        while lines and sum(len(l) for l in lines) > limit_chars: lines.pop()
        return '\n'.join(lines)

    def __bool__(self):
        return bool(self.archive or self.chats or self.tasks)


def ago(stamp: str, now: datetime) -> str:
    try: then = datetime.fromisoformat(str(stamp).replace('Z', '').replace('T', ' ')[:19])
    except ValueError: return '以前'
    days = (now.date()-then.date()).days
    if days <= 0: return '今天早些时候'
    if days == 1: return '昨天'
    if days < 7: return f'{days} 天前'
    if days < 60: return f'{days//7} 周前'
    return f'{then.year} 年 {then.month} 月'


class RecallService:
    def __init__(self, database):
        self.db = database

    def recall(self, character_id: str, query: str, scene: str = 'pet', channel: str = 'desktop',
               before_id: int | None = None, known=()) -> Recalled:
        """query：这一轮要回应的话（可含最近一两句）；before_id：当前历史窗口里最早一条消息的 id，比它早的才算“更早的聊天”；
        known：已经在记忆卡里带上的内容，召回时去重。"""
        result = Recalled()
        query = (query or '').strip()
        if len(query) < 2: return result
        from aicompanion.services.memory_service import similar
        with self.db.transaction() as c:
            # 1. 存档里的旧事：当前记忆卡里没有这个事实键、没被删除（删除会连同存档一起删，墓碑另外再挡一次）
            current = {r['fact_key'] for r in c.execute('SELECT fact_key FROM memories WHERE character_id=?', (character_id,))}
            tombs = {r['fact_key'] for r in c.execute('SELECT fact_key FROM memory_tombstones WHERE character_id=?', (character_id,))}
            seen, picked = set(), []
            for row in c.execute("SELECT * FROM memory_archive WHERE character_id=? AND reason IN ('forgotten','trimmed') "
                                 "ORDER BY id DESC LIMIT ?", (character_id, SCAN_ARCHIVE)):
                key = row['fact_key']
                if key in current or key in tombs or key in seen or not visible_in(row['scope'], scene): continue
                seen.add(key)
                s = score(query, row['content'])
                if s >= 2: picked.append((row, s))
            picked.sort(key=lambda item: (item[1], item[0]['importance']), reverse=True)
            for row, s in picked:
                if len(result.archive) >= ARCHIVE_LIMIT: break
                if any(similar(row['content'], k) for k in known): continue
                result.archive.append((row, s))
            # 2. 更早的聊天：同一频道、删除记忆的水位线之后、当前历史窗口之前。
            #    三字片段走全文索引；两个字的词（“团子”“青岛”）trigram 搜不到，用 LIKE 逐个查，命中少的算“专有的词”加分
            if before_id:
                watermark = int(self.db.get_setting(f'context_after:{character_id}', 0) or 0)
                candidates = {r['id']: r for r in fts.search(c, sorted(grams(query, 3))[:24], character_id, channel, before_id, watermark, 30)}
                rare = set()
                for gram in sorted(grams(query, 2))[:10]:
                    rows = c.execute("SELECT id, role, content, created_at FROM chat_messages WHERE character_id=? AND channel=? AND id<? AND id>? "
                                     "AND status='complete' AND content LIKE ? ORDER BY id DESC LIMIT ?",
                                     (character_id, channel, before_id, watermark, '%'+gram+'%', RARE_HITS+1)).fetchall()
                    if 0 < len(rows) <= RARE_HITS: rare.add(gram)
                    for r in rows: candidates.setdefault(r['id'], r)
                scored = []
                for row in candidates.values():
                    text = row['content'].lower()
                    scored.append((row, score(query, row['content'])+2*sum(1 for g in rare if g in text)))
                scored.sort(key=lambda item: (item[1], item[0]['id']), reverse=True)
                for row, s in scored:
                    if s < 3 or len(result.chats) >= CHAT_LIMIT: continue
                    if any(similar(row['content'], k) for k in known) or any(similar(row['content'], r['content']) for r, _ in result.chats): continue
                    result.chats.append((row, s))
            # 3. 按关联直接查：以前办过的事（只在私下用）
            if scene == 'pet':
                runs = c.execute("SELECT id, goal, status, started_at, result FROM agent_runs WHERE character_id=? AND status!='running' "
                                 "ORDER BY id DESC LIMIT 200", (character_id,)).fetchall()
                scored = sorted(((r, score(query, r['goal'])) for r in runs), key=lambda item: item[1], reverse=True)
                result.tasks = [(r, s) for r, s in scored if s >= 3][:TASK_LIMIT]
        return result
