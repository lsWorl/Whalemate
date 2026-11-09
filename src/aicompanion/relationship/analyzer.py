"""关系事件理解：后台批量读一个会话里新的聊天，用现有模型判断"谁对她做了什么"，交给 RelationshipService 确定性打分。

- 不是每条消息都调一次模型：一个会话攒够 TRIGGER 条新的用户消息、额度允许时才跑一次，后台线程，不耽误回复。
- 模型只输出事件类型、置信度、简短依据（和已确认的兴趣 / 共同经历），不输出分数。
- 说话人用本轮临时编号（P1、P2…）交给模型，不把号码交出去；编号 → 人物在本地对应。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading

from aicompanion.domain.cancellation import CancellationToken
from aicompanion.llm.base import ChatTurn
from aicompanion.relationship.service import EVENTS, OWNER_DESKTOP, RelationshipService, scope_of

TRIGGER = 6
WINDOW = 30
SENDERS_KEY = 'relationship_senders:'   # 聊天记录里的说话人（qq:号）→ 人物 id（桥接识别身份时记下）
RUNNING = set(); RUNNING_LOCK = threading.Lock()

INSTRUCTION = (
    '你在帮一个虚拟角色"她"整理人际关系。下面是一段聊天，每条用户消息前是说话人编号（P1、P2…），"她："开头的是她自己说的。'
    '只找这些人对"她"做了什么、或者让她更了解对方的事，输出 JSON：{"events":[{"who":"P1","type":"类型","confidence":0到1,'
    '"evidence":"不超过20字的依据","interest":"对方明确表达的稳定兴趣，没有就空","experience":"她和对方一起经历的、值得记住的事，没有就空"}]}。'
    '类型只能是：care（真诚关心她）、friendly（友好愉快地聊）、help_her（帮了她或替她着想）、respect_boundary（她不想做时对方尊重）、'
    'shared_achievement（一起做成了有意义的事）、apology（真诚道歉）、personal_share（跟她分享自己的事）、'
    'insult（恶意辱骂她）、harassment（持续骚扰）、deception（故意骗她）、attack（明确的恶意攻击）、boundary_violation（她拒绝后反复强迫）、'
    'chat、question、disagreement、news、emotion（这几种是中性：普通聊天、提问、正常意见分歧、分享新闻、表达自己的情绪）、'
    'request（请她做事）。'
    '注意：对方心情不好、发牢骚、和她意见不同、拒绝她的建议、开玩笑互损，都不是恶意；拿不准就用中性类型或不输出。'
    '每人每种类型最多一条，最多 8 条；没有就输出 {"events":[]}。不要输出 Markdown。'
)


def remember_sender(database, character_id, sender, person_id):
    """桥接识别出说话人后记下"聊天记录里的 sender → 人物"，后台分析时用来对上号。"""
    if not sender: return
    def write(c):
        table = database.setting_in(c, SENDERS_KEY+character_id, {}) or {}
        if table.get(sender) == person_id: return
        table[sender] = person_id
        if len(table) > 2000: table = dict(list(table.items())[-1500:])
        database.set_setting_in(c, SENDERS_KEY+character_id, table)
    database.locked(write)


class RelationshipAnalyzer:
    def __init__(self, database, relationships: RelationshipService | None = None):
        self.db = database; self.relationships = relationships or RelationshipService(database)

    def marker(self, character_id, channel): return f'relationship_through:{character_id}:{channel}'

    def pending(self, character_id, channel) -> int:
        rows = self.db.recent_messages(WINDOW, character_id, channel=channel)
        done = int(self.db.get_setting(self.marker(character_id, channel), 0) or 0)
        return sum(1 for r in rows if r['role'] == 'user' and r['id'] > done)

    def maybe_run(self, character_id, channel, client, background=True) -> bool:
        """够条数、额度允许、这个会话没在分析时，启动一次后台分析。"""
        if not self.db.get_setting('relationship_learning', True): return False
        if self.pending(character_id, channel) < int(self.db.get_setting('relationship_trigger', TRIGGER) or TRIGGER): return False
        try:
            from aicompanion.usage.meter import Quota
            if Quota(self.db).check_silent('memory') == 'stop': return False
        except Exception:
            pass
        key = (character_id, channel)
        with RUNNING_LOCK:
            if key in RUNNING: return False
            RUNNING.add(key)

        def work():
            try: self.analyze(character_id, channel, client, CancellationToken())
            except Exception: pass   # 分析失败不影响聊天；下次够条数再试
            finally:
                with RUNNING_LOCK: RUNNING.discard(key)
        if background: threading.Thread(target=work, daemon=True, name='aicompanion-relationship').start()
        else: work()
        return True

    def analyze(self, character_id, channel, client, cancellation) -> int:
        rows = self.db.recent_messages(WINDOW, character_id, channel=channel)
        done = int(self.db.get_setting(self.marker(character_id, channel), 0) or 0)
        new = [r for r in rows if r['id'] > done]
        if not any(r['role'] == 'user' for r in new): return 0
        senders = self.db.get_setting(SENDERS_KEY+character_id, {}) or {}
        owner = self.relationships.owner_person() if channel == 'desktop' else None
        aliases, people, lines = {}, {}, []
        for r in rows:
            if r['role'] != 'user':
                lines.append('她：'+str(r['content'])[:200]); continue
            person = owner if channel == 'desktop' else senders.get(r['sender'] or '')
            if person is None: continue
            alias = aliases.setdefault(person, f'P{len(aliases)+1}'); people[alias] = person
            text = re.sub(r'^(?:【爹】)?[^：\n]{0,30}#\d+：', '', str(r['content']))   # 群聊记录前面的"昵称#编号："
            lines.append(('（新）' if r['id'] > done else '')+f'{alias}：'+text[:300])
        if not people: self._advance(character_id, channel, rows); return 0
        from aicompanion.usage.meter import using
        with using('memory', character_id):
            raw = ''.join(client.generate_reply_stream([ChatTurn('user', '\n'.join(lines[-WINDOW:]))],
                                                       INSTRUCTION+'只判断标着（新）的消息里发生的事，前面的只是上下文。', cancellation))
        cancellation.check()
        count = self.apply(character_id, channel, raw, people, last_id=new[-1]['id'])
        self._advance(character_id, channel, rows)
        return count

    def apply(self, character_id, channel, raw, people, last_id) -> int:
        start, end = raw.find('{'), raw.rfind('}')
        try: data = json.loads(raw[start:end+1]) if start >= 0 else {}
        except ValueError: return 0
        events = data.get('events') if isinstance(data, dict) else None
        if not isinstance(events, list): return 0
        scope = scope_of(channel); applied = 0; seen = set()
        for event in events[:8]:
            if not isinstance(event, dict): continue
            person = people.get(str(event.get('who') or ''))
            kind = str(event.get('type') or '')
            if person is None or kind not in EVENTS or (person, kind) in seen: continue
            seen.add((person, kind))
            key = hashlib.sha256(f'{channel}:{last_id}:{person}:{kind}'.encode()).hexdigest()[:32]
            result = self.relationships.record(character_id, person, kind, event.get('confidence', 0), scope=scope,
                                               evidence=str(event.get('evidence') or ''), key=key,
                                               interest=str(event.get('interest') or ''), experience=str(event.get('experience') or ''))
            applied += int(result.get('applied', False))
        return applied

    def _advance(self, character_id, channel, rows):
        if rows: self.db.set_setting(self.marker(character_id, channel), rows[-1]['id'])
