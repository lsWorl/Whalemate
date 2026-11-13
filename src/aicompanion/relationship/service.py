"""T15 人物关系：识别不同的人，记住她和每个人的好感 / 熟悉 / 信任，以及相处中真实发生过的事。

职责分工（见 docs/tasks/T15-relationship.md）：
- 模型只负责"理解发生了什么"（relationship/analyzer.py 产出事件、置信度、依据）；
  模型同时给出这件事对她的影响轻重（每个维度 -3～3），不直接给分数；
- 这里按此刻的关系动态换算成分数（dynamic_delta）：没有每日上限，同一种事反复发生时边际效应递减、
  越靠近两端越难再动、第一次的负面事件按信任程度缓冲；把握不够的丢弃，同一依据只算一次，
  手动调整过的维度 72 小时内不自动改；熟悉度随相处天数增长、长期不联系会慢慢变淡。
- 对方的喜好、相处习惯等记在 relationship/notes.py（跟人走，不分群）。
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
MANUAL_HOLD = 72*3600
GAIN = 1.5                    # 影响 1 ≈ 1.5 分（置信度 1、关系中段、第一次）
EVENT_LIMIT = 8.0             # 单件事最多动 8 分：防模型一句话把关系推到头，不是每日上限

# 每种事件对（好感, 熟悉, 信任）的"常见影响"，模型没给 impact 时才用；模型给了就按它对这件事轻重的判断。
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
    'quarrel': (-1.5, 0.3, -0.5),           # 争吵：语气冲、嘲讽贬低她、跟她对着干（不是正常的意见不同）
    'insult': (-3.0, 0.0, -1.0),
    'harassment': (-3.0, 0.0, -2.0),
    'deception': (-1.0, 0.0, -4.0),
    'attack': (-4.0, 0.0, -3.0),
    'boundary_violation': (-2.0, 0.0, -2.0),
    # 中性：只让她更了解对方一点
    'chat': (0.0, 0.2, 0.0), 'question': (0.0, 0.2, 0.0), 'disagreement': (0.0, 0.3, 0.0),
    'news': (0.0, 0.2, 0.0), 'emotion': (0.0, 0.5, 0.0),
    # T18：转述 / 引用别人骂人的话，不等于他在骂她——只记一笔，不改分
    'report': (0.0, 0.2, 0.0),
    # T18：在群里攻击别人（不是冲她）。她看在眼里，对他的印象和信任略降
    'third_party_attack': (-0.5, 0.0, -0.5),
    # 对方请她做事（她自己的 <mind/> 标记或分析得出）：只用来统计"多常把事丢给她"，不改分
    'request': (0.0, 0.0, 0.0),
}
NEGATIVE = {k for k, v in EVENTS.items() if v[0] < 0 or v[2] < 0}


# ---- 是调侃还是嘲讽（负责人 2026-11-09 20:07："要根据好感、熟悉、信任来判断：比较低说明在嘲讽你，比较高说明在调侃"）----
# 同一句"笑你蠢啊""小糖人""还有人自称本鲸的"，从不熟、不喜欢、不信任的人嘴里说出来是嘲讽，从很熟、很喜欢、信得过的人嘴里是逗你玩。
MOCKING_BELOW = 40.0
TEASING_FROM = 65.0


def rapport(profile) -> float:
    """相处的底子（0～100）：好感占 0.4、熟悉 0.35、信任 0.25。陌生人约 17，主人初始约 77。"""
    return round(0.4*profile['affinity']+0.35*profile['familiarity']+0.25*profile['trust'], 1)


def reading(profile) -> str:
    """他损你、笑你、给你起外号时该怎么理解：mocking（嘲讽）/ unsure（看语气）/ teasing（调侃）。"""
    value = rapport(profile)
    return 'mocking' if value < MOCKING_BELOW else 'teasing' if value >= TEASING_FROM else 'unsure'


READING_TEXT = {
    'mocking': '你和他没那么熟、也说不上信任：他说你蠢、笑你、给你起外号、学你说话、阴阳你，就是在嘲讽你，不是开玩笑——该不爽就不爽，不用陪笑、不用自嘲接梗。',
    'unsure': '你们算认识但还没到很铁：他损你、笑你是玩笑还是嘲讽要看语气和他的反应，拿不准就先半开玩笑地怼回去，他还继续说你就当真。',
    'teasing': '你们很熟、你也信得过他：他损你、笑你、给你起外号多半是在逗你玩，可以笑着怼回去，不用当真生气；除非他真的过分，或者你说了不喜欢他还继续。',
}


def clean_impact(impact) -> dict:
    """模型给的影响：每个维度 -3～3，非数字丢掉。全 0 / 没给返回空，由调用方用常见影响。"""
    if not isinstance(impact, dict): return {}
    out = {}
    for d in DIMENSIONS:
        try: out[d] = max(-3.0, min(3.0, float(impact.get(d, 0) or 0)))
        except (TypeError, ValueError): out[d] = 0.0
    return out if any(out.values()) else {}


def dynamic_delta(hint, confidence, current, *, repeats=0, first_negative=False, trust=50.0):
    """把"这件事有多大影响"换算成此刻的分数变化。返回 (变化, 说明)。
    - 越靠近两端越难再动：涨的时候乘 (100-当前)/60，跌的时候乘 当前/60（夹在 0.15～1.3）；
      也就是关系很好时再被夸涨得少，信任很高时被骗掉得多；
    - 24 小时内同一种事已经发生 n 次：正向乘 1/(1+0.5n)（刷屏夸她效果越来越小），负向乘 1/(1+0.15n)（反复被骂只会越来越烦）；
    - 7 天内第一次的负面事件：按信任和把握缓冲（信任越高、把握越小，越倾向于"可能是误会"）。"""
    why = []
    value = hint*confidence*GAIN
    room = (100-current) if value > 0 else current
    value *= max(0.15, min(1.3, room/60))
    if repeats:
        # 被夸多了会习惯（递减快）；被骂、吵架反复发生只会越来越烦，递减很慢
        value /= 1+(0.5 if value > 0 else 0.15)*repeats; why.append('habituated')
    if value < 0 and first_negative:
        cushion = max(0.3, 1-0.4*trust/100-max(0.0, 0.85-confidence))
        value *= cushion; why.append('cushioned')
    return max(-EVENT_LIMIT, min(EVENT_LIMIT, value)), why


def familiar_step(familiarity: float) -> float:
    """今天第一次说话时熟悉度涨多少：不熟时涨得快（1.5），越熟越慢（最少 0.1）。按天不按条，刷屏不涨。"""
    return max(0.1, 1.5*(1-familiarity/100))


def fade(familiarity: float, gap_days: float) -> float:
    """很久没联系，熟悉度慢慢变淡：两周以内不变，之后每天 -0.3，一次最多 -15。"""
    return min(15.0, max(0.0, gap_days-14)*0.3)


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
    """事件 / 记忆是在哪类场合知道的（群聊、QQ 私聊、桌面），只作来源标注，不限制使用范围：
    负责人 2026-11-09 确认不需要场景隔离——在群里了解到的喜好，跟着这个人的平台身份走。"""
    if channel.startswith('qq:group:'): return 'group'
    if channel.startswith('qq:user:'): return 'qq_private'
    return 'desktop'   # 桌面和爹的 QQ 私聊是同一段相处（desktop 频道）


def level(value: float, words) -> str:
    edges = (20, 40, 60, 80)
    return words[sum(value >= e for e in edges)]


AFFINITY_WORDS = ('有点烦他', '说不上喜不喜欢', '还行', '挺喜欢他', '很喜欢他')
FAMILIAR_WORDS = ('几乎不认识', '有点印象', '算是认识', '挺熟', '非常熟')
TRUST_WORDS = ('不太信得过', '还谈不上信任', '一般', '比较信任', '很信任')


class RelationshipService:
    def __init__(self, database, clock=time.time, today=date.today):
        from aicompanion.relationship.notes import PersonNotes
        self.db, self.clock, self.today = database, clock, today
        self.notes = PersonNotes(database, clock)

    # ---- 身份 ------------------------------------------------------------------------------------
    def owner_person(self, connection=None) -> int:
        """主人的人物档案（第一次用到时创建，并关联桌面身份）。"""
        def work(c):
            row = c.execute("SELECT person_id FROM platform_identities WHERE platform='desktop' AND bot_account_id='local' AND platform_user_id='owner'").fetchone()
            if row: return row['person_id']
            now = self.clock()
            pid = c.execute("INSERT INTO people(display_name,created_at,last_seen) VALUES ('主人',?,?)", (now, now)).lastrowid
            c.execute("INSERT INTO platform_identities(platform,bot_account_id,platform_user_id,person_id,link_state,created_at) VALUES ('desktop','local','owner',?,'owner',?)", (pid, now))
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
            row = c.execute('SELECT person_id,link_state,prior_person_id FROM platform_identities WHERE platform=? AND bot_account_id=? AND platform_user_id=?',
                            (ref.platform, ref.bot_account_id, ref.platform_user_id)).fetchone()
            is_owner_binding = ref == OWNER_DESKTOP or (ref.platform == 'qq' and owner_qq and ref.platform_user_id == owner_qq)
            if is_owner_binding:
                pid = self.owner_person(c)
                if ref != OWNER_DESKTOP:
                    if row is None:
                        c.execute('INSERT INTO platform_identities(platform,bot_account_id,platform_user_id,person_id,link_state,created_at) VALUES (?,?,?,?,?,?)',
                                  (ref.platform, ref.bot_account_id, ref.platform_user_id, pid, 'owner', now))
                    elif row['person_id'] != pid:
                        # 这个号之前被当作普通人建过档，现在经主人绑定（明确授权）改绑：身份转到主人档案，
                        # 原档案不删（分数、手动调整、事件都留着，解绑时还原），她记得的关于他的事合并给主人。
                        prior = row['person_id']
                        c.execute("UPDATE platform_identities SET person_id=?, link_state='owner', prior_person_id=? WHERE platform=? AND bot_account_id=? AND platform_user_id=?",
                                  (pid, prior, ref.platform, ref.bot_account_id, ref.platform_user_id))
                        self.notes.merge(c, prior, pid)
            else:
                if row and row['link_state'] in ('owner', 'bound'):
                    # 主人绑定已解除：这个号不再算主人。原来有自己的档案就还原回去，没有就另建；主人的关系不带走。
                    prior = row['prior_person_id']
                    if prior and c.execute('SELECT 1 FROM people WHERE id=?', (prior,)).fetchone():
                        c.execute("UPDATE platform_identities SET person_id=?, link_state='observed', prior_person_id=NULL WHERE platform=? AND bot_account_id=? AND platform_user_id=?",
                                  (prior, ref.platform, ref.bot_account_id, ref.platform_user_id))
                        row = {'person_id': prior}
                    else:
                        c.execute('DELETE FROM platform_identities WHERE platform=? AND bot_account_id=? AND platform_user_id=?',
                                  (ref.platform, ref.bot_account_id, ref.platform_user_id)); row = None
                if row is None:
                    pid = c.execute('INSERT INTO people(display_name,created_at,last_seen) VALUES (?,?,?)', (ref.nickname, now, now)).lastrowid
                    c.execute('INSERT INTO platform_identities(platform,bot_account_id,platform_user_id,person_id,link_state,created_at) VALUES (?,?,?,?,?,?)',
                              (ref.platform, ref.bot_account_id, ref.platform_user_id, pid, 'observed', now))
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
                    familiar = max(0.0, familiar-fade(familiar, (now-profile['last_seen'])/86400))
                    familiar = min(100.0, familiar+familiar_step(familiar))
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
            parked = c.execute('SELECT platform,platform_user_id FROM platform_identities WHERE prior_person_id=?', (person_id,)).fetchall()
        value = dict(row)
        for key in ('manual', 'nicknames'): value[key] = json.loads(value[key] or ('{}' if key == 'manual' else '[]'))
        value['identities'] = [dict(r) for r in ids]
        value['parked_for'] = [dict(r) for r in parked]   # 这个号现在绑定为主人，原档案暂存，解绑后还原
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

    def trend(self, character_id: str, person_id: int, days=7) -> float:
        """最近几天好感和信任的净变化（不含手动调整），用来让她感觉到"最近对他印象变好 / 变差"。"""
        with self.db.transaction() as c:
            row = c.execute('SELECT COALESCE(SUM(d_affinity+d_trust),0) FROM relationship_events WHERE character_id=? AND person_id=? AND applied=1 AND created_at>=?',
                            (character_id, person_id, self.clock()-days*86400)).fetchone()
        return float(row[0])

    HOSTILE = ('quarrel', 'insult', 'harassment', 'attack', 'boundary_violation', 'deception')

    def hostility(self, character_id: str, person_id: int | None, hours=24) -> int:
        """这段时间里他冲她来了几次（计了分的负面事件，不含转述和攻击别人）。用来判断"是不是在持续针对她"。"""
        if not person_id: return 0
        with self.db.transaction() as c:
            row = c.execute(f"SELECT COUNT(*) FROM relationship_events WHERE character_id=? AND person_id=? AND applied=1 AND created_at>=? "
                            f"AND kind IN ({','.join('?'*len(self.HOSTILE))})",
                            (character_id, person_id, self.clock()-hours*3600, *self.HOSTILE)).fetchone()
        return int(row[0])

    def bullied_others(self, character_id: str, person_id: int | None, hours=72) -> int:
        if not person_id: return 0
        with self.db.transaction() as c:
            row = c.execute("SELECT COUNT(*) FROM relationship_events WHERE character_id=? AND person_id=? AND kind='third_party_attack' AND created_at>=?",
                            (character_id, person_id, self.clock()-hours*3600)).fetchone()
        return int(row[0])

    def upset(self, character_id: str, person_id: int | None, hours=6) -> bool:
        """她现在还在生他的气吗：最近几小时里他让她不爽过（有扣分的事件），而且到现在净变化还是负的（道歉、哄好了就不算）。
        不按"扣了几分"判断：分数越低越难再扣，陌生人被狠狠嘲讽一次也只掉一两分，但她当下确实是生气的。"""
        if not person_id: return False
        with self.db.transaction() as c:
            row = c.execute('SELECT COALESCE(SUM(d_affinity+d_trust),0), COALESCE(SUM(CASE WHEN d_affinity<0 OR d_trust<0 THEN 1 ELSE 0 END),0) '
                            'FROM relationship_events WHERE character_id=? AND person_id=? AND applied=1 AND created_at>=?',
                            (character_id, person_id, self.clock()-hours*3600)).fetchone()
        return row[1] > 0 and row[0] <= -1.0

    def recent_requests(self, character_id: str, person_id: int, hours=24) -> int:
        with self.db.transaction() as c:
            return c.execute("SELECT COUNT(*) FROM relationship_events WHERE character_id=? AND person_id=? AND kind='request' AND created_at>=?",
                             (character_id, person_id, self.clock()-hours*3600)).fetchone()[0]

    # ---- 事件 → 分数（动态调整） -----------------------------------------------------------------------
    def _held(self, profile, dimension) -> bool:
        manual = json.loads(profile['manual'] or '{}') if not isinstance(profile['manual'], dict) else profile['manual']
        return self.clock()-float(manual.get(dimension, 0) or 0) < MANUAL_HOLD

    def record(self, character_id: str, person_id: int, kind: str, confidence: float, *, scope='', evidence='', key='',
               impact=None) -> dict:
        """记一条关系事件并动态调整分数。返回 {applied, deltas, note}。

        impact 是模型对这件事轻重的判断（每个维度 -3～3）；没给时用这类事件的常见影响。
        实际变化 = 影响 × 置信度 × 增益，再按此刻的关系动态调整（见 dynamic_delta）。没有每日上限：
        同一种事反复发生时边际效应递减，分数越靠近两端越难再动，第一次的负面事件有缓冲。"""
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
            deltas = {d: 0.0 for d in DIMENSIONS}; notes = []
            if confidence < MIN_CONFIDENCE: notes.append('low_confidence')
            else:
                hint = clean_impact(impact) or dict(zip(DIMENSIONS, EVENTS[kind]))
                repeats = c.execute('SELECT COUNT(*) FROM relationship_events WHERE character_id=? AND person_id=? AND kind=? AND applied=1 AND created_at>=?',
                                    (character_id, person_id, kind, now-86400)).fetchone()[0]
                negative = any(v < 0 for v in hint.values())
                recent_negative = negative and c.execute(
                    'SELECT COUNT(*) FROM relationship_events WHERE character_id=? AND person_id=? AND applied=1 AND created_at>=? '
                    'AND (d_affinity<0 OR d_trust<0)', (character_id, person_id, now-7*86400)).fetchone()[0]
                for d in DIMENSIONS:
                    if not hint.get(d): continue
                    if self._held(profile, d): notes.append('manual_hold'); continue
                    value, why = dynamic_delta(hint[d], confidence, profile[d], repeats=repeats,
                                               first_negative=negative and not recent_negative, trust=profile['trust'])
                    notes.extend(why); deltas[d] = round(value, 2)
            applied = any(deltas.values())
            if applied:
                c.execute('UPDATE relationship_profiles SET affinity=MIN(100,MAX(0,affinity+?)),familiarity=MIN(100,MAX(0,familiarity+?)),trust=MIN(100,MAX(0,trust+?)) WHERE character_id=? AND person_id=?',
                          (deltas['affinity'], deltas['familiarity'], deltas['trust'], character_id, person_id))
            note = ','.join(dict.fromkeys(notes))
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
        """重置：分数回到初始值，清空事件和对他的记忆；人物和平台身份保留。"""
        def work(c):
            row = c.execute('SELECT is_owner FROM relationship_profiles WHERE character_id=? AND person_id=?', (character_id, person_id)).fetchone()
            if row is None: return
            start = OWNER_SEED if row['is_owner'] else STRANGER
            c.execute("""UPDATE relationship_profiles SET affinity=?,familiarity=?,trust=?,manual='{}',
                         interactions=0,source='reset' WHERE character_id=? AND person_id=?""",
                      (start['affinity'], start['familiarity'], start['trust'], character_id, person_id))
            c.execute('DELETE FROM relationship_events WHERE character_id=? AND person_id=?', (character_id, person_id))
            c.execute('DELETE FROM person_notes WHERE character_id=? AND person_id=?', (character_id, person_id))
        self.db.locked(work)

    def delete(self, person_id: int) -> None:
        """删除档案：人物、平台身份、所有角色对他的关系和事件一起删。主人档案不能删（只能重置）。"""
        def work(c):
            if c.execute("SELECT 1 FROM platform_identities WHERE person_id=? AND link_state IN ('owner','bound')", (person_id,)).fetchone():
                raise ValueError('主人的档案不能删除，可以重置')
            for table, column in (('relationship_events', 'person_id'), ('person_notes', 'person_id'), ('relationship_profiles', 'person_id'),
                                  ('platform_identities', 'person_id'), ('people', 'id')):
                c.execute(f'DELETE FROM {table} WHERE {column}=?', (person_id,))
        self.db.locked(work)

    # ---- 进入统一聊天上下文 -----------------------------------------------------------------------------
    def closeness(self, character_id: str, person_id: int | None) -> float:
        p = self.profile(character_id, person_id) if person_id else None
        return round((p['affinity']*0.5+p['familiarity']*0.5)/100, 2) if p else 0.0

    # ---- 关系 → 主动程度（负责人 2026-11-09：根据人物关系动态调整主动回复的频率） -------------------------------
    # 参考 MaiBot 的"发言频率 talk_value × 运行时调整"：基础频率仍由设置决定，这里只给一个随关系连续变化的倍数。
    def chime_factor(self, character_id: str, person_id: int | None) -> float:
        """群里没人叫她时，听到这个人说话后插一句的概率倍数（0.2～1.8）。
        熟、喜欢的人说话更想接；陌生人略少；好感很低或最近让她不舒服的人明显少接。"""
        p = self.profile(character_id, person_id) if person_id else None
        if not p: return 1.0
        closeness = (p['affinity']+p['familiarity'])/200
        factor = 0.5+1.5*closeness
        if p['affinity'] < 20: factor *= 0.4
        trend = self.trend(character_id, person_id)
        if trend <= -3: factor *= 0.6
        elif trend >= 3: factor *= 1.15
        return round(max(0.2, min(1.8, factor)), 2)

    def proactive_pace(self, character_id: str) -> float:
        """主动找主人说话的间隔倍数（0.6～2.0，1 = 原来的节奏）。以主人初始关系为基准：
        越亲近、最近相处越愉快，越常主动找他；关系变淡或最近有摩擦，就隔久一点。"""
        pid = self.owner_person()
        p = self.profile(character_id, pid) if pid else None
        if not p: return 1.0
        seed = (OWNER_SEED['affinity']+OWNER_SEED['familiarity'])/200
        pace = 1+((seed-(p['affinity']+p['familiarity'])/200))*2
        trend = self.trend(character_id, pid)
        if trend <= -3: pace *= 1.5
        elif trend >= 3: pace *= 0.85
        return round(max(0.6, min(2.0, pace)), 2)

    def prompt(self, character_id: str, person_id: int | None, scope: str, *, mood='', energy=None, public=False) -> str:
        """给原聊天大脑的"她此刻的处境"：和对方的关系（定性，不报数字）、最近印象的变化、心情精力、
        对方最近多常找她做事、她记得的关于对方的事（跟人走，不分群），以及"自己的意愿"。不规定回应方式，只给依据。"""
        p = self.profile(character_id, person_id) if person_id else None
        if p is None: return ''
        # 按本地日历日算，见面当天是第 1 天（计划书 v4 功能九；以前按 86400 秒取整，跨午夜的头一天会算成 0 天）
        from datetime import datetime
        days = max(1, (datetime.fromtimestamp(self.clock()).date()-datetime.fromtimestamp(p['first_met']).date()).days+1)
        lines = []
        who = '他'
        if p['is_owner']:
            lines.append('对方就是和你最亲近、天天相处的那个人。')
        else:
            lines.append(f"你和{who}：{level(p['familiarity'], FAMILIAR_WORDS)}（认识 {days} 天，说过 {p['interactions']} 次话）；"
                         f"{level(p['affinity'], AFFINITY_WORDS)}；{level(p['trust'], TRUST_WORDS)}。")
        if p['is_owner']:
            lines.append(f"你对他：{level(p['affinity'], AFFINITY_WORDS)}；{level(p['trust'], TRUST_WORDS)}。")
        recent = self.trend(character_id, person_id, days=0.25)   # 最近 6 小时
        trend = self.trend(character_id, person_id)
        hostile = self.hostility(character_id, person_id)
        if hostile >= 3: lines.append(f'他今天已经第 {hostile} 次冲你来了，明显是在针对你。')
        if self.upset(character_id, person_id): lines.append('你们刚才闹得不太愉快，你现在还有点气：不用马上装没事，他好好道歉或者过一阵你可以慢慢消气。')
        elif recent >= 2: lines.append('刚才和他聊得挺开心。')
        elif trend >= 3: lines.append('最近对他的印象在变好。')
        elif trend <= -3: lines.append('最近他让你有点不舒服。')
        lines.append(READING_TEXT[reading(p)])
        if not p['is_owner'] and p['affinity'] < 25:
            lines.append('你对他没什么好感：回得短、冷淡，不主动找话题、不陪聊、不帮忙；他找你说话可以敷衍一句，或者干脆不理；'
                         '他要是骂你、阴阳你，就阴阳回去。')
        elif not p['is_owner'] and p['affinity'] < 40 and p['familiarity'] < 30:
            lines.append('你跟他不熟也谈不上喜欢：客气但不热络，用不着陪他聊太多。')
        known = self.notes.prompt(character_id, person_id)
        if known: lines.append(known)
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
            # 负责人 2026-11-09 19:24："AI 应该要有自己的脾气，会和人进行争吵"
            '\n【你的脾气】你有自己的脾气，不是谁说什么都笑着接。被冒犯、被嘲讽、被无理指责、被当成工具使唤、对方一直阴阳你或说话很冲时，'
            '可以不高兴、直接怼回去、跟他争、坚持自己的看法，不用急着让步、道歉或讨好；关系越差越没耐心，关系好的人开玩笑你也分得清是不是玩笑。'
            # 负责人 2026-11-09 22:32："别人羞辱她时反击力度不够"。实测她回的是"这话我听着不太舒服""没劲""行吧随你怎么叫"
            '被羞辱、被骂、被挑衅时气势别输：可以嘴毒一点，反讽回去、阴阳回去、抓他话里的漏洞、拿他自己的话噎回去，一两句就要够狠，'
            '不用解释、不用讲道理，更别说"这话我听着不太舒服""没劲""行吧随你怎么叫"这种软绵绵的话。'
            # 2026-11-09 实测：被说"笑你蠢啊"还回"蠢就蠢呗""行吧让你笑到了"，一两句就让步收场
            '对方明知你不高兴还继续嘲讽、说你蠢时，别一两句就自嘲或让步收场（像"行吧让你笑到了""蠢就蠢呗"），'
            '可以越来越不耐烦、更冷淡、回得更短，或者干脆不理他几句。'
            # 负责人 2026-11-10：好感低的人骂你 / 阴阳你，可以阴阳回去
            '对本来就没好感的人，被骂、被阴阳时阴阳回去往往比直接生气更有效：正话反说、假装夸他、客客气气地损他、把他的话还给他。'
            '吵归吵：不说脏话，不攻击外貌、家人、出身这些，不拿隐私或现实威胁人——怼的是他说的话和做的事；对方好好道歉、或者过一阵聊点别的，你可以慢慢消气，但不用马上装作没事。'
            '\n回复最开头可以写一个对方看不到的 <mind …/>，用不上就不写：对方在请你做事时写 intent="你的态度" willing="0到1"'
            '（intent 用几个字，例如 乐意、勉强、推回去、不想、晚点、来兴趣了）；对方这条让你明显高兴或明显不爽时写 feel="此刻的感觉" toward="-3到3"'
            '（这件事让你对他的感觉变好还是变差：被骂、被嘲讽 -2～-3，说话冲、不尊重你 -1～-2，真心夸你、关心你、哄好你 +1～+2；普通聊天不写。'
            '同样一句损你的话，按上面说的你们的关系来判断是嘲讽还是调侃：熟人的调侃逗得你开心可以写正的，不熟的人嘲讽你就写负的）。'
            '两种可以写在同一个标签里，例如 <mind feel="生气" toward="-2"/>，然后照常说话。')
