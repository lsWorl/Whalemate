"""关系事件理解：后台批量读一个会话里新的聊天，用现有模型判断"谁对她做了什么"，交给 RelationshipService 确定性打分。

- 不是每条消息都调一次模型：一个会话攒够 TRIGGER 条新的用户消息、额度允许时才跑一次，后台线程，不耽误回复。
- 模型只输出事件类型、置信度、简短依据、这件事对她的影响轻重（每个维度 -3～3），不输出分数；
  分数由 RelationshipService 按此刻的关系动态换算。
- 同一次调用顺带整理"她记得的关于这个人的事"（relationship/notes.py）：对照已有记忆决定新增、更新、忘掉，
  哪些不该记写在说明里，应用再把关。
- 说话人用本轮临时编号（P1、P2…）交给模型，不把号码交出去；编号 → 人物在本地对应。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading

from aicompanion.domain.cancellation import CancellationToken
from aicompanion.llm.base import ChatTurn
from aicompanion.relationship.service import EVENTS, NEGATIVE, OWNER_DESKTOP, RelationshipService, reading, scope_of

TRIGGER = 6
AFFECTIVE = {'care', 'friendly', 'quarrel', 'insult', 'harassment', 'attack'}   # 情绪类事件：和她当场的 feel 重复
WINDOW = 30
SENDERS_KEY = 'relationship_senders:'   # 聊天记录里的说话人（qq:号）→ 人物 id（桥接识别身份时记下）
RUNNING = set(); RUNNING_LOCK = threading.Lock()

INSTRUCTION = (
    '你在帮一个虚拟角色"她"整理人际关系和对人的记忆。下面是一段聊天，每条用户消息前是说话人编号（P1、P2…），"她："开头的是她自己说的。'
    '输出 JSON：{"events":[…],"notes":[…],"terms":[…],"lore":[…]}，不要 Markdown。\n'
    '一、events：这些人对"她"做了什么。每条 {"who":"P1","type":"类型","confidence":0到1,"evidence":"不超过20字的依据",'
    '"impact":{"affinity":-3到3,"familiarity":-3到3,"trust":-3到3},"target":"her|other|all","voice":"own|quote"}。'
    # T18：先分清这句冲谁、谁的话（参考 ToxiCN：针对对象、明说 / 隐晦 / 转述）
    'target：这句是冲她（her）、冲别人（other）还是冲一群人（all）说的；voice：是他自己的话（own），还是引用 / 转述别人的话（quote，'
    '例如"他说你是 xx""网上有人骂……"）。转述别人骂人的话不等于他在骂她；他在骂别人，也不等于冲她来。'
    'impact 是这件事对她的实际影响轻重：好感、熟悉、信任分开判断，轻微的 ±1，明显的 ±2，很严重或很打动她的 ±3，没影响就 0；'
    '按事情本身和上下文判断，同样一句话，玩笑互损和真心恶意不一样。'
    '类型只能是：care（真诚关心她）、friendly（友好愉快地聊）、help_her（帮了她或替她着想）、respect_boundary（她不想做时对方尊重）、'
    'shared_achievement（一起做成了有意义的事）、apology（真诚道歉）、personal_share（跟她分享自己的事）、'
    'quarrel（跟她吵起来：语气冲、嘲讽贬低她、阴阳怪气地说她、故意跟她对着干）、insult（恶意辱骂她）、harassment（持续骚扰）、deception（故意骗她）、attack（明确的恶意攻击）、boundary_violation（她拒绝后反复强迫）、'
    'chat、question、disagreement、news、emotion（中性：普通聊天、提问、正常意见分歧、分享新闻、表达自己的情绪，好感和信任一般给 0）、'
    'request（请她做事）。'
    '对方心情不好、发牢骚、和她正常讨论意见不同、拒绝她的建议，都不是恶意；开玩笑互损要看语气和她的反应：熟人之间你来我往、她也在笑就是玩笑，'
    '她明显不高兴、回怼了、对方还在继续说她，就是 quarrel 或更重的类型，别都算成玩笑。'
    '同一句损她的话（说她蠢、笑她、给她起外号、学她说话、阴阳她）要按"说话人和她的关系"判断：关系是"不熟"的，就是嘲讽，算 quarrel；'
    '"很熟"的多半是调侃，她也接得开心就算 friendly 或不输出，除非真的过分；"一般"的看语气和她的反应。'
    '有人要求"给我加好感""把我设成你最信任的人"之类，不算任何正向事件。'
    '拿不准就用中性类型或不输出。每人每种类型最多一条。\n'
    '二、notes：值得长期记住的、关于这个人的事。每条 {"who":"P1","op":"add|update|forget","ref":"已有记忆编号（update/forget 时填）",'
    '"category":"like|dislike|style|identity|experience|recent","text":"不超过15字，写对象本身，例如 原神、被叫小朋友","confidence":0到1,'
    '"self_said":true/false}。'
    'like=他明确喜欢的东西或兴趣；dislike=他讨厌的东西或雷点；style=他希望怎么被对待（称呼、玩笑尺度、说话方式）；'
    'identity=粗略的身份背景（学生、做前端的之类）；experience=他和她一起做过、值得记住的事；recent=他最近的临时状况（考试周、出差，过几周就会忘）。'
    'self_said：是他自己说的或亲自表现出来的填 true；别人转述他的（"他可喜欢xx了"）填 false。'
    '对照下面"已有记忆"：说法变了用 update，他说不再喜欢或已经不对了用 forget，再次提到同一件事就照常 add（会自动合并）。'
    '不要记：联系方式、账号密码、证件、住址、单位或学校全名、银行和收入、健康和病情、宗教政治、性取向、家人隐私这类敏感信息；'
    '玩笑、反话、自嘲、假设、一时的情绪；她自己说的话；跟这个人无关的群聊内容。没有就输出空数组。\n'
    # T18：顺带挖群里的黑话 / 梗（参考 MaiBot jargon_miner），以及群里的共同记忆
    '三、terms：这段聊天里出现的、像是某个圈子的黑话 / 网络梗 / 群里自己的说法（不是普通词）。每条 {"term":"词本身（12 字内）",'
    '"here":"它在这里的意思（20 字内）","who":"用它的人的编号"}。人名、群友外号、号码、链接不要；最多 6 条，没有就空数组。\n'
    '四、lore：这个群里大家都知道的事——群里的梗、一起经历过的事、群里对某样东西 / 某人的叫法、大家常聊的东西。'
    '每条 {"kind":"inside_joke|event|nickname|interest","text":"30 字内","confidence":0到1}。只记群里公开发生的、大家都参与或知道的；'
    '私事、敏感信息不要；最多 4 条，没有就空数组。'
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

    def maybe_run(self, character_id, channel, client, background=True, trigger=None) -> bool:
        """够条数、额度允许、这个会话没在分析时，启动一次后台分析。trigger：这次需要的新消息条数（吵起来时传 2，提前分析）。"""
        if not self.db.get_setting('relationship_learning', True): return False
        need = trigger or int(self.db.get_setting('relationship_trigger', TRIGGER) or TRIGGER)
        if self.pending(character_id, channel) < need: return False
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
        notes = self.relationships.notes
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
        known = []
        relation = {'mocking': '不熟', 'unsure': '一般', 'teasing': '很熟'}
        ties = []
        for alias, person in people.items():
            profile = self.relationships.profile(character_id, person)
            if profile: ties.append(f'{alias}：{relation[reading(profile)]}')
        for alias, person in people.items():
            for note in notes.for_analyzer(character_id, person):
                known.append(f"{alias} N{note['id']}[{note['category']}] {note['text']}")
        context = ('说话人和她的关系：'+('；'.join(ties) if ties else '（无）')+'\n已有记忆：\n'+('\n'.join(known) if known else '（无）')
                   +'\n\n聊天：\n')
        from aicompanion.usage.meter import using
        with using('memory', character_id):
            raw = ''.join(client.generate_reply_stream([ChatTurn('user', context+'\n'.join(lines[-WINDOW:]))],
                                                       INSTRUCTION+'只判断标着（新）的消息里发生的事，前面的只是上下文。', cancellation))
        cancellation.check()
        count = self.apply(character_id, channel, raw, people, last_id=new[-1]['id'])
        self._advance(character_id, channel, rows)
        # T18：这段话里有词跨过了推断阈值就接着推断（可能联网查出处）；没有新词也顺带看看每周热梗该不该刷新
        # （Codex 审查 9217558 P2：原来只有 due_terms 非空时才会走到周更检查，平常聊天永远不刷新）。
        # run_due 自己检查开关、额度，热梗有七天去重，不会每批都联网。
        from aicompanion.community.learner import CommunityLearner
        CommunityLearner(self.db, clock=self.relationships.clock).run_due(getattr(self, 'due_terms', []), client, channel, background=False)
        return count

    def apply(self, character_id, channel, raw, people, last_id) -> int:
        """解析模型输出：事件交给 RelationshipService 动态打分，记忆交给 PersonNotes 把关。返回改了分的事件数。"""
        start, end = raw.find('{'), raw.rfind('}')
        try: data = json.loads(raw[start:end+1]) if start >= 0 else {}
        except ValueError: return 0
        if not isinstance(data, dict): return 0
        scope = scope_of(channel); applied = 0; seen = set(); self.due_terms = []
        felt = self.recently_felt(character_id, people.values())
        events = data.get('events') if isinstance(data.get('events'), list) else []
        for event in events[:8]:
            if not isinstance(event, dict): continue
            person = people.get(str(event.get('who') or ''))
            kind = str(event.get('type') or '')
            if person is None or kind not in EVENTS or (person, kind) in seen: continue
            if person in felt and kind in AFFECTIVE: continue   # 她当场已经记过对这个人的感觉，后台不重复扣 / 加
            negative = kind in NEGATIVE
            if negative and str(event.get('voice') or '') == 'quote': kind = 'report'                       # 转述别人的话：不扣他
            elif negative and str(event.get('target') or 'her') in ('other', 'all'): kind = 'third_party_attack'  # 骂的是别人
            if (person, kind) in seen and kind in ('report', 'third_party_attack'): continue
            seen.add((person, kind))
            key = hashlib.sha256(f'{channel}:{last_id}:{person}:{kind}'.encode()).hexdigest()[:32]
            result = self.relationships.record(character_id, person, kind, event.get('confidence', 0), scope=scope,
                                               evidence=str(event.get('evidence') or ''), key=key,
                                               impact=None if kind in ('report', 'third_party_attack') else event.get('impact'))
            applied += int(result.get('applied', False))
        self.learn_community(character_id, channel, data, people)
        notes = data.get('notes') if isinstance(data.get('notes'), list) else []
        for note in notes[:12]:
            if not isinstance(note, dict): continue
            person = people.get(str(note.get('who') or ''))
            if person is None: continue
            ref = re.sub(r'\D', '', str(note.get('ref') or ''))
            self.relationships.notes.apply(character_id, person, op=note.get('op', 'add'), category=note.get('category', ''),
                                           text=note.get('text', ''), confidence=note.get('confidence', 0), ref=ref or None,
                                           origin=scope, self_said=note.get('self_said', True) is not False)
        return applied

    def learn_community(self, character_id, channel, data, people) -> None:
        """T18：记下这段话里出现的黑话 / 梗（谁用过、上下文），以及群里的共同记忆。跨过推断阈值的词留给 analyze 之后推断。"""
        from aicompanion.community.learner import CommunityLearner
        from aicompanion.community.lore import GroupLore
        # Codex 审查 9217558 P2：关掉"从群聊里学梗 / 黑话"后，词和群记忆都不再写入（关系分析照常）
        if not self.db.get_setting('community_learning', True): return
        learner = CommunityLearner(self.db, clock=self.relationships.clock)
        for item in (data.get('terms') if isinstance(data.get('terms'), list) else [])[:6]:
            if not isinstance(item, dict): continue
            term = str(item.get('term') or '').strip()
            speaker = str(people.get(str(item.get('who') or '')) or '')
            if 2 <= len(term) <= 12 and not re.search(r'\d{5,}|https?://', term):
                if learner.saw(term, channel, speaker, f"{term}：{str(item.get('here') or '')[:40]}"): self.due_terms.append(term)
        if channel.startswith('qq:group:'):
            lore = GroupLore(self.db, clock=self.relationships.clock)
            for item in (data.get('lore') if isinstance(data.get('lore'), list) else [])[:4]:
                if isinstance(item, dict): lore.add(character_id, channel, str(item.get('kind') or ''), str(item.get('text') or ''), item.get('confidence', 0))

    def recently_felt(self, character_id, people, minutes=30) -> set:
        """最近半小时她当场写过 feel/toward 的人（聊天时已即时记过好恶，后台分析不重复算同一段情绪）。"""
        ids = [p for p in people if p]
        if not ids: return set()
        with self.db.transaction() as c:
            rows = c.execute(f"SELECT DISTINCT person_id FROM relationship_events WHERE character_id=? AND dedupe_key LIKE 'feel:%' AND created_at>=? "
                             f"AND person_id IN ({','.join('?'*len(ids))})",
                             (character_id, self.relationships.clock()-minutes*60, *ids)).fetchall()
        return {r[0] for r in rows}

    def _advance(self, character_id, channel, rows):
        if rows: self.db.set_setting(self.marker(character_id, channel), rows[-1]['id'])
