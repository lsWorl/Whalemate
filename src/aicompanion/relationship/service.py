"""T15 人物关系：识别不同的人，记住她和每个人的好感 / 熟悉 / 信任，以及相处中真实发生过的事。

职责分工（见 docs/tasks/T15-relationship.md）：
- 模型只负责"理解发生了什么"（relationship/analyzer.py 产出事件、置信度、依据）；
- 这里用确定性规则把事件变成分数：每种事件固定的变化量 × 置信度，低置信度丢弃，同一依据只算一次，
  每人每天每个维度有涨跌上限，单次负面且把握不大时减半，手动调整过的维度 72 小时内不自动改。
- 关系只影响"她怎么相处、愿不愿意"，不授予任何权限（主人身份、群白名单、工具审批都在别处强制）。

人物身份 = platform + bot_account_id + platform_user_id。群号是场景，不进身份；昵称只作展示。
主人：桌面身份 desktop/local/owner；QQ 上已绑定的主人号（bridge_owner:qq）经现有绑定（明确授权）关联到同一个人物。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
import re
import time

STRANGER = {'affinity': 30.0, 'familiarity': 0.0, 'trust': 20.0}
OWNER_SEED = {'affinity': 80.0, 'familiarity': 75.0, 'trust': 75.0}
DIMENSIONS = ('affinity', 'familiarity', 'trust')
MIN_CONFIDENCE = 0.6
DAILY_UP = 4.0
DAILY_DOWN = 6.0
MANUAL_HOLD = 72*3600
FAMILIAR_PER_DAY = 0.6        # 每天和她说过话，熟悉度 +0.6（按天不按条，刷屏不涨）

# 每种事件对（好感, 熟悉, 信任）的变化量，置信度 1.0 时的值。中性事件不动好感和信任。
EVENTS = {
    # 正向
    'care': (2.0, 1.0, 1.0),               # 真诚关心她
    'friendly': (1.0, 0.5, 0.0),            # 友好交流、开心地聊
    'help_her': (2.0, 1.0, 2.0),            # 帮了她 / 替她着想
    'respect_boundary': (1.0, 0.0, 2.0),    # 她拒绝或不想时，对方尊重
    'shared_achievement': (2.0, 2.0, 1.0),  # 一起做成了有意义的事
    'apology': (1.0, 0.0, 1.0),             # 真诚道歉
    'personal_share': (0.5, 1.5, 0.5),      # 跟她分享自己的事
    # 负向（恶意，不是负面情绪或不同意见）
    'insult': (-3.0, 0.0, -1.0),
    'harassment': (-3.0, 0.0, -2.0),
    'deception': (-1.0, 0.0, -4.0),
    'attack': (-4.0, 0.0, -3.0),
    'boundary_violation': (-2.0, 0.0, -2.0),
    # 中性：只让她更了解对方一点
    'chat': (0.0, 0.2, 0.0), 'question': (0.0, 0.2, 0.0), 'disagreement': (0.0, 0.3, 0.0),
    'news': (0.0, 0.2, 0.0), 'emotion': (0.0, 0.5, 0.0),
    # 对方请她做事（她自己的 <mind/> 标记或分析得出）：只用来统计"多常把事丢给她"，不改分
    'request': (0.0, 0.0, 0.0),
}
NEGATIVE = {k for k, v in EVENTS.items() if v[0] < 0 or v[2] < 0}


@dataclass(frozen=True)
class PersonRef:
    platform: str
    bot_account_id: str
    platform_user_id: str
    nickname: str = ''

    @property
    def key(self): return f'{self.platform}:{self.bot_account_id}:{self.platform_user_id}'


OWNER_DESKTOP = PersonRef('desktop', 'local', 'owner', '')


def clean_id(value, pattern=r'[\w@.:-]{1,64}') -> str:
    value = str(value or '').strip()
    return value if re.fullmatch(pattern, value) else 'unknown'


def scope_of(channel: str) -> str:
    """事件 / 经历的作用范围：群里的只在那个群用，私聊和桌面各自一份。用不可逆短哈希，看不出群号。"""
    if channel.startswith('qq:group:'):
        return 'group:'+hashlib.sha256(channel.encode()).hexdigest()[:12]
    if channel.startswith('qq:user:'): return 'qq_private'
    return 'private'   # 桌面和爹的 QQ 私聊是同一段相处（desktop 频道）


def level(value: float, words) -> str:
    edges = (20, 40, 60, 80)
    return words[sum(value >= e for e in edges)]


AFFINITY_WORDS = ('有点烦他', '说不上喜不喜欢', '还行', '挺喜欢他', '很喜欢他')
FAMILIAR_WORDS = ('几乎不认识', '有点印象', '算是认识', '挺熟', '非常熟')
TRUST_WORDS = ('不太信得过', '还谈不上信任', '一般', '比较信任', '很信任')


class RelationshipService:
    def __init__(self, database, clock=time.time, today=date.today):
        self.db, self.clock, self.today = database, clock, today

    # ---- 身份 ------------------------------------------------------------------------------------
    def owner_person(self, connection=None) -> int:
        """主人的人物档案（第一次用到时创建，并关联桌面身份）。"""
        def work(c):
            row = c.execute("SELECT person_id FROM platform_identities WHERE platform='desktop' AND bot_account_id='local' AND platform_user_id='owner'").fetchone()
            if row: return row['person_id']
            now = self.clock()
            pid = c.execute("INSERT INTO people(display_name,created_at,last_seen) VALUES ('主人',?,?)", (now, now)).lastrowid
            c.execute("INSERT INTO platform_identities VALUES ('desktop','local','owner',?,'owner',?)", (pid, now))
            return pid
        return work(connection) if connection is not None else self.db.locked(work)

    def resolve(self, ref: PersonRef, character_id: str, *, count=True) -> int:
        """平台身份 → 人物；没见过就建档（陌生人初始值）。QQ 上已绑定的主人号走现有绑定，关联到主人档案。
        count=True 时记一次互动（更新最近时间、互动次数、按天的熟悉度）。"""
        ref = PersonRef(clean_id(ref.platform, r'[a-z0-9_-]{1,20}'), clean_id(ref.bot_account_id), clean_id(ref.platform_user_id),
                        re.sub(r'[\r\n]+', ' ', str(ref.nickname or ''))[:30])
        owner_qq = str(self.db.get_setting('bridge_owner:qq', '') or '').strip()
        now = self.clock()

        def work(c):
            row = c.execute('SELECT person_id,link_state FROM platform_identities WHERE platform=? AND bot_account_id=? AND platform_user_id=?',
                            (ref.platform, ref.bot_account_id, ref.platform_user_id)).fetchone()
            is_owner_binding = ref == OWNER_DESKTOP or (ref.platform == 'qq' and owner_qq and ref.platform_user_id == owner_qq)
            if row and row['link_state'] == 'owner' and not is_owner_binding:
                # 主人绑定已解除：这个 QQ 号不再算主人，另建自己的档案（不带走主人的关系）
                c.execute('DELETE FROM platform_identities WHERE platform=? AND bot_account_id=? AND platform_user_id=?',
                          (ref.platform, ref.bot_account_id, ref.platform_user_id)); row = None
            if row is None:
                if is_owner_binding:
                    pid = self.owner_person(c)
                    if ref != OWNER_DESKTOP:
                        c.execute('INSERT OR REPLACE INTO platform_identities VALUES (?,?,?,?,?,?)',
                                  (ref.platform, ref.bot_account_id, ref.platform_user_id, pid, 'owner', now))
                else:
                    pid = c.execute('INSERT INTO people(display_name,created_at,last_seen) VALUES (?,?,?)', (ref.nickname, now, now)).lastrowid
                    c.execute('INSERT INTO platform_identities VALUES (?,?,?,?,?,?)', (ref.platform, ref.bot_account_id, ref.platform_user_id, pid, 'observed', now))
            else:
                pid = row['person_id']
            owner = bool(is_owner_binding)
            person = c.execute('SELECT nicknames,display_name FROM people WHERE id=?', (pid,)).fetchone()
            nicknames = json.loads(person['nicknames'] or '[]')
            if ref.nickname and ref.nickname not in nicknames:
                nicknames = (nicknames+[ref.nickname])[-10:]
            c.execute('UPDATE people SET nicknames=?, display_name=?, last_seen=? WHERE id=?',
                      (json.dumps(nicknames, ensure_ascii=False), ('主人' if owner else (ref.nickname or person['display_name'])), now, pid))
            self._ensure_profile(c, character_id, pid, owner)
            if count:
                day = self.today().isoformat()
                profile = c.execute('SELECT * FROM relationship_profiles WHERE character_id=? AND person_id=?', (character_id, pid)).fetchone()
                familiar = profile['familiarity']
                if profile['last_familiar_day'] != day and not self._held(profile, 'familiarity'):
                    familiar = min(100.0, familiar+FAMILIAR_PER_DAY)
                c.execute('UPDATE relationship_profiles SET interactions=interactions+1,last_seen=?,familiarity=?,last_familiar_day=? WHERE character_id=? AND person_id=?',
                          (now, familiar, day, character_id, pid))
            return pid
        return self.db.locked(work)

    def _ensure_profile(self, c, character_id, pid, owner):
        exists = c.execute('SELECT 1 FROM relationship_profiles WHERE character_id=? AND person_id=?', (character_id, pid)).fetchone()
        if exists: return
        start = OWNER_SEED if owner else STRANGER
        now = self.clock()
        c.execute('INSERT INTO relationship_profiles(character_id,person_id,affinity,familiarity,trust,is_owner,first_met,last_seen,source) VALUES (?,?,?,?,?,?,?,?,?)',
                  (character_id, pid, start['affinity'], start['familiarity'], start['trust'], int(owner), now, now, 'seed:owner' if owner else 'stranger'))

    # ---- 读取 ------------------------------------------------------------------------------------
    def profile(self, character_id: str, person_id: int) -> dict | None:
        with self.db.transaction() as c:
            row = c.execute('''SELECT p.*, h.display_name, h.nicknames FROM relationship_profiles p JOIN people h ON h.id=p.person_id
                               WHERE p.character_id=? AND p.person_id=?''', (character_id, person_id)).fetchone()
            if row is None: return None
            ids = c.execute('SELECT platform,bot_account_id,platform_user_id,link_state FROM platform_identities WHERE person_id=?', (person_id,)).fetchall()
        value = dict(row)
        for key in ('manual', 'interests', 'experiences', 'nicknames'): value[key] = json.loads(value[key] or ('{}' if key == 'manual' else '[]'))
        value['identities'] = [dict(r) for r in ids]
        return value

    def people(self, character_id: str) -> list[dict]:
        with self.db.transaction() as c:
            rows = c.execute('SELECT person_id FROM relationship_profiles WHERE character_id=? ORDER BY is_owner DESC, last_seen DESC', (character_id,)).fetchall()
        return [p for p in (self.profile(character_id, r['person_id']) for r in rows) if p]

    def events(self, character_id: str, person_id: int, limit=20) -> list[dict]:
        with self.db.transaction() as c:
            rows = c.execute('SELECT * FROM relationship_events WHERE character_id=? AND person_id=? ORDER BY id DESC LIMIT ?',
                             (character_id, person_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def recent_requests(self, character_id: str, person_id: int, hours=24) -> int:
        with self.db.transaction() as c:
            return c.execute("SELECT COUNT(*) FROM relationship_events WHERE character_id=? AND person_id=? AND kind='request' AND created_at>=?",
                             (character_id, person_id, self.clock()-hours*3600)).fetchone()[0]

    # ---- 事件 → 分数（确定性） --------------------------------------------------------------------------
    def _held(self, profile, dimension) -> bool:
        manual = json.loads(profile['manual'] or '{}') if not isinstance(profile['manual'], dict) else profile['manual']
        return self.clock()-float(manual.get(dimension, 0) or 0) < MANUAL_HOLD

    def record(self, character_id: str, person_id: int, kind: str, confidence: float, *, scope='', evidence='', key='',
               interest='', experience='') -> dict:
        """记一条关系事件并按规则打分。返回 {applied, deltas, note}。kind 不认识、置信度低、重复的都不改分。"""
        kind = str(kind or '').strip()
        try: confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError): confidence = 0.0
        evidence = re.sub(r'\s+', ' ', str(evidence or ''))[:80]
        key = key or hashlib.sha256(f'{person_id}:{kind}:{evidence}:{round(self.clock()/600)}'.encode()).hexdigest()[:32]
        day = self.today().isoformat(); now = self.clock()

        def work(c):
            profile = c.execute('SELECT * FROM relationship_profiles WHERE character_id=? AND person_id=?', (character_id, person_id)).fetchone()
            if profile is None or kind not in EVENTS: return {'applied': False, 'note': 'unknown'}
            if c.execute('SELECT 1 FROM relationship_events WHERE character_id=? AND dedupe_key=?', (character_id, key)).fetchone():
                return {'applied': False, 'note': 'duplicate'}
            deltas = {d: 0.0 for d in DIMENSIONS}; note = ''
            if confidence < MIN_CONFIDENCE: note = 'low_confidence'
            else:
                base = dict(zip(DIMENSIONS, EVENTS[kind]))
                scale = confidence
                if kind in NEGATIVE:
                    # 单次负面且把握不大：减半。最近 7 天已有负面事件才按全量算（长期行为综合判断）
                    earlier = c.execute(f"SELECT COUNT(*) FROM relationship_events WHERE character_id=? AND person_id=? AND applied=1 AND created_at>=? AND kind IN ({','.join('?'*len(NEGATIVE))})",
                                        (character_id, person_id, now-7*86400, *sorted(NEGATIVE))).fetchone()[0]
                    if confidence < 0.85 and not earlier: scale *= 0.5; note = 'first_negative_halved'
                for d in DIMENSIONS:
                    value = base[d]*scale
                    if not value: continue
                    if self._held(profile, d): note = note or 'manual_hold'; continue
                    used = c.execute(f'SELECT COALESCE(SUM(CASE WHEN d_{d}>0 THEN d_{d} ELSE 0 END),0) AS up, COALESCE(SUM(CASE WHEN d_{d}<0 THEN d_{d} ELSE 0 END),0) AS down '
                                     'FROM relationship_events WHERE character_id=? AND person_id=? AND day=? AND applied=1', (character_id, person_id, day)).fetchone()
                    if value > 0: value = max(0.0, min(value, DAILY_UP-used['up']))
                    else: value = min(0.0, max(value, -DAILY_DOWN-used['down']))
                    if value == 0 and base[d]: note = note or 'daily_cap'
                    deltas[d] = round(value, 2)
            applied = any(deltas.values())
            if applied:
                c.execute('UPDATE relationship_profiles SET affinity=MIN(100,MAX(0,affinity+?)),familiarity=MIN(100,MAX(0,familiarity+?)),trust=MIN(100,MAX(0,trust+?)) WHERE character_id=? AND person_id=?',
                          (deltas['affinity'], deltas['familiarity'], deltas['trust'], character_id, person_id))
            # 兴趣、共同经历：只记模型确认过的，带作用范围，各保留最近 12 条
            for field, text in (('interests', interest), ('experiences', experience)):
                text = re.sub(r'\s+', ' ', str(text or '')).strip()[:40]
                if text and confidence >= MIN_CONFIDENCE:
                    items = json.loads(profile[field] or '[]')
                    if not any(i.get('text') == text and i.get('scope') == scope for i in items):
                        items = (items+[{'text': text, 'scope': scope, 'at': round(now)}])[-12:]
                        c.execute(f'UPDATE relationship_profiles SET {field}=? WHERE character_id=? AND person_id=?',
                                  (json.dumps(items, ensure_ascii=False), character_id, person_id))
            c.execute('''INSERT INTO relationship_events(character_id,person_id,kind,confidence,scope,evidence,d_affinity,d_familiarity,d_trust,applied,note,dedupe_key,created_at,day)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                      (character_id, person_id, kind, confidence, scope, evidence, deltas['affinity'], deltas['familiarity'], deltas['trust'],
                       int(applied), note, key, now, day))
            return {'applied': applied, 'deltas': deltas, 'note': note}
        return self.db.locked(work)

    # ---- 手动管理 ---------------------------------------------------------------------------------
    def adjust(self, character_id: str, person_id: int, **values) -> None:
        """界面手动调整：改过的维度记下时间，72 小时内自动打分不动它，之后从手动值继续。"""
        def work(c):
            profile = c.execute('SELECT manual FROM relationship_profiles WHERE character_id=? AND person_id=?', (character_id, person_id)).fetchone()
            if profile is None: raise ValueError('档案不存在')
            manual = json.loads(profile['manual'] or '{}')
            for d in DIMENSIONS:
                if d in values and values[d] is not None:
                    v = max(0.0, min(100.0, float(values[d])))
                    c.execute(f'UPDATE relationship_profiles SET {d}=? WHERE character_id=? AND person_id=?', (v, character_id, person_id))
                    manual[d] = self.clock()
            c.execute("UPDATE relationship_profiles SET manual=?, source='manual' WHERE character_id=? AND person_id=?",
                      (json.dumps(manual), character_id, person_id))
        self.db.locked(work)

    def reset(self, character_id: str, person_id: int) -> None:
        """重置：分数回到初始值，清空事件、兴趣、经历；人物和平台身份保留。"""
        def work(c):
            row = c.execute('SELECT is_owner FROM relationship_profiles WHERE character_id=? AND person_id=?', (character_id, person_id)).fetchone()
            if row is None: return
            start = OWNER_SEED if row['is_owner'] else STRANGER
            c.execute("""UPDATE relationship_profiles SET affinity=?,familiarity=?,trust=?,manual='{}',interests='[]',experiences='[]',
                         interactions=0,source='reset' WHERE character_id=? AND person_id=?""",
                      (start['affinity'], start['familiarity'], start['trust'], character_id, person_id))
            c.execute('DELETE FROM relationship_events WHERE character_id=? AND person_id=?', (character_id, person_id))
        self.db.locked(work)

    def delete(self, person_id: int) -> None:
        """删除档案：人物、平台身份、所有角色对他的关系和事件一起删。主人档案不能删（只能重置）。"""
        def work(c):
            if c.execute("SELECT 1 FROM platform_identities WHERE person_id=? AND link_state='owner'", (person_id,)).fetchone():
                raise ValueError('主人的档案不能删除，可以重置')
            for table, column in (('relationship_events', 'person_id'), ('relationship_profiles', 'person_id'),
                                  ('platform_identities', 'person_id'), ('people', 'id')):
                c.execute(f'DELETE FROM {table} WHERE {column}=?', (person_id,))
        self.db.locked(work)

    # ---- 进入统一聊天上下文 -----------------------------------------------------------------------------
    def closeness(self, character_id: str, person_id: int | None) -> float:
        p = self.profile(character_id, person_id) if person_id else None
        return round((p['affinity']*0.5+p['familiarity']*0.5)/100, 2) if p else 0.0

    def prompt(self, character_id: str, person_id: int | None, scope: str, *, mood='', energy=None, public=False) -> str:
        """给原聊天大脑的"她此刻的处境"：和对方的关系（定性，不报数字）、心情精力、对方最近多常找她做事、
        她知道的对方兴趣和共同经历（只取这个作用范围的），以及"自己的意愿"。不规定回应方式，只给依据。"""
        p = self.profile(character_id, person_id) if person_id else None
        if p is None: return ''
        days = max(0, int((self.clock()-p['first_met'])//86400))
        lines = []
        who = '他'
        if p['is_owner']:
            lines.append('对方就是和你最亲近、天天相处的那个人。')
        else:
            lines.append(f"你和{who}：{level(p['familiarity'], FAMILIAR_WORDS)}（认识 {days} 天，说过 {p['interactions']} 次话）；"
                         f"{level(p['affinity'], AFFINITY_WORDS)}；{level(p['trust'], TRUST_WORDS)}。")
        if p['is_owner']:
            lines.append(f"你对他：{level(p['affinity'], AFFINITY_WORDS)}；{level(p['trust'], TRUST_WORDS)}。")
        interests = [i['text'] for i in p['interests'] if i.get('scope') == scope][-4:]
        experiences = [i['text'] for i in p['experiences'] if i.get('scope') == scope][-3:]
        if interests: lines.append('你知道他感兴趣的：'+'、'.join(interests)+'。')
        if experiences: lines.append('你们一起经历过：'+'；'.join(experiences)+'。（只提这些真实发生过的，别编新的。）')
        requests = self.recent_requests(character_id, person_id)
        if requests >= 2: lines.append(f'这一天他已经找你做过 {requests} 次事了。')
        state = []
        if mood: state.append(f'心情 {mood}')
        if energy is not None: state.append('有点累' if energy < 35 else ('精神不错' if energy > 70 else '精力一般'))
        if state: lines.append('你现在：'+'，'.join(state)+'。')
        return ('\n【你和这个人】'+''.join(lines)+'这些是你心里的感觉，别说出数值或"关系等级"，也别每句都提。'
                + AUTONOMY)


# 负责人 2026-11-09：任务里举的回应方式只是参考，不能写死，要她根据上下文自己推动。
AUTONOMY = ('\n【你自己的意愿】你不是有求必应的工具。对方要你做事时，先按你自己的感觉判断：跟他熟不熟、喜不喜欢他、'
            '你现在累不累、心情怎样、这事有没有意思、难不难、是不是他自己随手就能做、他是不是老把事丢给你、现在这个场合合不合适。'
            '可能乐意做，可能勉强答应，可能开玩笑推回去，可能直说不想，可能说等会儿再说，也可能对事情本身来了兴趣、反过来聊你的想法——'
            '这些只是可能性，不是固定说法，照此刻的你自然决定。关系好也可以拒绝，关系一般也可以因为有意思而参与。'
            '拒绝时别讲大道理、别解释规则，也别威胁、冷战或让人愧疚，拒绝完照常聊。真正涉及安全和权限的事由系统把关，不归你的意愿决定。'
            '\n如果对方这条是在请你做事，回复最开头写一个对方看不到的 <mind intent="你的态度" willing="0到1"/>（intent 用几个字描述，例如 乐意、勉强、推回去、不想、晚点、来兴趣了），'
            '然后照常说话；没请你做事就不写。')
