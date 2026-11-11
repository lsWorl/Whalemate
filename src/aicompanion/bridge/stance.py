"""T22 她对一个外人此刻的态度：热络 / 一般 / 冷淡 / 不理。让好感度看得见地影响她怎么回、回不回。

负责人 2026-11-10 18:44 看真机聊天记录后的反馈：
- "明显有些时候说不会再回话了还是会回复"——她说了"不奉陪了""懒得搭理你了"，下一条照样回；
- "好感度系统没有清晰体现作用"——被人连着骂了半小时，她还是每条都认真回两三句。

之前这些只写在提示里，由模型自己把握，结果是嘴上说不理、实际每条都回。这里改成确定性的：
- 说到做到：她自己说了要走（LEAVE），这句发出去以后一段时间内，这个人的消息只记录、不回、不调模型；
  一天内再犯，时间翻倍（10 → 20 → 40 … 最多 120 分钟）。对方来服软（APOLOGY）可以提前结束，原不原谅仍由她定。
- 复读刷屏：同一个人两分钟内把同一句话发第三遍，直接不理 5 分钟。
- 冷淡（好感 < 25，或今天已经冲她来了 3 次）：只回一句短的；冲她来的次数越多越可能已读不回；
  今天冲她来 5 次以上，不打招呼直接不理 15 分钟。
- 一般（不熟）：最多两三条。热络：不限制。

只管外人：爹不走这里。状态存在设置里（重启不丢），"人物关系"页能看到，也能手动解除。
"""
from __future__ import annotations
import re
import time

LEAVE = re.compile(r'不奉陪|懒得(?:理|搭理|回|跟你)|不(?:回|理|接)(?:你)?了|不(?:跟|和)你(?:说|聊|吵|掰扯|玩)了|你自己(?:玩|刷|循环|演|数着)|'
                   r'(?:自己|你)慢慢(?:玩|刷|循环|演)|爱刷刷吧|我不接|拉黑|别再(?:找|@|艾特)我|不想理你|懒得理了')
APOLOGY = re.compile(r'对不起|抱歉|我错了|不闹了|不骂了|别生气|别气了|道歉|不好意思|是我不对|sorry', re.I)
COLD_AFFINITY = 25      # 和 T15 "没好感"的线一致
HOSTILE_COLD = 3        # 今天冲她来了这么多次：按冷淡对待
HOSTILE_OUT = 5         # 今天冲她来了这么多次：不打招呼直接不理
SAID, SPAM, FED_UP = 'said', 'spam', 'fed_up'
REASONS = {SAID: '她说了不理他', SPAM: '他在复读刷屏', FED_UP: '他今天一直冲她来'}
CAPS = {'cold': 1, 'neutral': 3}     # 一次最多发几条；热络和爹不限制。只截零碎话，有内容的回答和吵架还嘴不截（bridge/service.py、segments.fit_cap）


def tier(profile, hostility: int = 0, upset: bool = False) -> str:
    """热络 warm / 一般 neutral / 冷淡 cold。没有档案（没聊过）按一般。"""
    if not profile: return 'neutral'
    if profile.get('is_owner'): return 'warm'
    if profile['affinity'] < COLD_AFFINITY or hostility >= HOSTILE_COLD: return 'cold'
    from aicompanion.relationship.service import rapport
    return 'warm' if rapport(profile) >= 65 and not upset else 'neutral'


PROMPTS = {
    'cold': ('\n【你现在对他很冷淡】这次最多回一句，十个字以内：不解释、不讲道理、不长篇反击，像真人敷衍一句或者呛一句就完'
             '（"哦""？""随你""急了"这类意思，只是示意，别照抄）。懒得回就只回复【不说】——已读不回比说一大段更像你现在的态度。'
             '例外：他是认真来问事、请教东西，你又愿意答，就把答案说完整；跟他吵起来要还嘴，也可以多发几条短的把话说完。'),
    'neutral': ('\n【你跟他不熟】一般就回一句短的，最多两三条；不用热情，也不用每句都接。'
                '但他认真问你问题、请教东西，或者你们在吵、你要把话说清楚时，可以多发几条把话说完整，别说一半。'),
    'warm': '\n【你跟他挺熟】可以多聊几句、开玩笑、主动接话。',
}
KEEP_WORD = ('\n说到做到：你要是说了"不理你了""不奉陪了""不回了"这类话，这句发出去以后一段时间内你就真的收不到他的消息了。'
             '所以没打算走就别说；真烦了，就说一句短的然后走，或者干脆只回复【不说】。别一边说不回、一边接着回。')
MADE_UP = '\n你之前说了不理他，他现在像是来服软的：原不原谅、怎么回，你自己定（可以还冷着他）。'


class Stance:
    REPEAT_WINDOW = 120     # 秒
    REPEAT_TIMES = 3

    def __init__(self, database, clock=time.time):
        self.db, self.clock = database, clock
        self.recent = {}        # 说话人 → 最近几条（时间，归一化的文字）；只在内存里，用来认复读

    @staticmethod
    def key(character_id, person_id) -> str: return f'bridge_ignore:{character_id}:{person_id}'

    def state(self, character_id, person_id) -> dict:
        value = self.db.get_setting(self.key(character_id, person_id), None) if person_id else None
        return value if isinstance(value, dict) else {}

    def ignoring(self, character_id, person_id) -> float:
        """还要不理他多少秒；0 表示没有不理。"""
        return max(0.0, float(self.state(character_id, person_id).get('until') or 0)-self.clock())

    def walk_away(self, character_id, person_id, reason: str, minutes: float | None = None) -> float:
        """开始不理这个人，返回分钟数。她自己说要走（said）的，一天内再犯时间翻倍。"""
        if not person_id: return 0
        now = self.clock(); old = self.state(character_id, person_id)
        strikes = int(old.get('strikes') or 0) if now-float(old.get('since') or 0) < 86400 else 0
        if minutes is None: minutes = min(120, 10*(2**strikes))
        self.db.set_setting(self.key(character_id, person_id), {'until': now+minutes*60, 'since': now, 'reason': reason,
                                                                'strikes': strikes+(1 if reason == SAID else 0), 'minutes': minutes})
        return minutes

    def clear(self, character_id, person_id) -> None:
        old = self.state(character_id, person_id)
        if old: self.db.set_setting(self.key(character_id, person_id), dict(old, until=0))

    def repeated(self, speaker: str, text: str) -> bool:
        """同一个人在两分钟内把同一句话发到第三遍（去掉空白和标点后比较）。"""
        now = self.clock(); norm = re.sub(r'[\W_]+', '', str(text or '')).lower()[:40]
        items = [(at, t) for at, t in self.recent.get(speaker, []) if now-at < self.REPEAT_WINDOW][-8:]
        items.append((now, norm)); self.recent[speaker] = items
        if len(self.recent) > 500: self.recent.pop(next(iter(self.recent)))
        return bool(norm) and sum(1 for _at, t in items if t == norm) >= self.REPEAT_TIMES

    def describe(self, character_id, person_id, profile, hostility: int = 0, upset: bool = False) -> str:
        """给"人物关系"页看的一句话。"""
        left = self.ignoring(character_id, person_id)
        if left:
            state = self.state(character_id, person_id)
            return f"不理他（{REASONS.get(state.get('reason'), '不想理')}），还剩约 {int(left//60)+1} 分钟"
        if profile and profile.get('is_owner'): return '爹：不受这些限制'
        name = {'warm': '热络：可以多聊、开玩笑', 'neutral': '一般：回得短，最多两三条',
                'cold': '冷淡：只回一句短的，常常已读不回'}[tier(profile, hostility, upset)]
        return name + (f'；他今天已经冲她来了 {hostility} 次' if hostility else '')
