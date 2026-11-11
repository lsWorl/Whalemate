"""陪伴日期（计划书 v4 功能九，阶段二的基础版）：认识第几天、其中聊过几天、纪念日，到了那天自然地提一句。

这些日子属于你们的关系，不属于人格或模型：认识那天取主人关系档案里的 first_met（换人格、换模型、换渠道都不变，长期约束 1～3），
用户也可以在“关系与心情 → 我们的日子”里手动改（以前在别处养过她）。

哪些算特别的日子：
- 程序算出来的（不落表，现算）：认识第 100、200、300、500、520、1000、1314 天及以后每满 1000 天；每一周年；
  她的“生日”默认就是认识那天；第一次一起办成一件事的周年。节日：公历的元旦、情人节、劳动节、国庆、圣诞，
  农历的除夕、春节、元宵、端午、七夕、中秋（用 lunar_python 换算，负责人 2026-11-11 同意引入），可以整体关掉。
- 用户自己加的：你的生日、她的生日（改默认）、考试和纪念日这类重要日子；可以每年重复，也可以只有一次；可以按农历记（农历生日）。

怎么用：只在和主人私下聊天时（桌面、主人的 QQ 私聊）带进提示词——平时只是一句背景（认识第几天），
当天有特别的日子才提醒她“今天可以自然提一句”；这一轮成功说完后记下“今天已经提过”，同一个日子一天只提一次，重启也不重复。
群聊、陌生人那边一律不带（生日这类是主人的隐私，硬底线 3）。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

MILESTONES = (100, 200, 300, 500, 520, 1000, 1314)
HOLIDAYS = {(1, 1): '元旦', (2, 14): '情人节', (5, 1): '劳动节', (10, 1): '国庆节', (12, 25): '圣诞节'}
LUNAR_HOLIDAYS = {(1, 1): '春节', (1, 15): '元宵节', (5, 5): '端午节', (7, 7): '七夕', (8, 15): '中秋节'}
KINDS = {'birthday': '你的生日', 'her_birthday': '她的生日', 'custom': '纪念日'}
MAX_TITLE = 30
CLAIM_TTL = 600   # 一轮生成占住提醒最多这么久（秒）：进程里出了意外没走到确认 / 释放，过期后别的轮次还能提

# 同一个日子同一天只提一次（Codex PR #43 R4）：桌面和主人 QQ 私聊可能同时在生成，各自都没 mark 之前会都拿到提醒。
# 拼提示词时先在这里按（数据库、角色、key、日期）占住，占住的轮次才带提醒；成功说完写进库，失败 / 取消就放掉。
# 只在内存里：单进程结构（AGENTS.md），重启时没有进行中的轮次，没说完的自然作废。
_CLAIMS_LOCK = threading.Lock()
_CLAIMS: dict[tuple[str, str, str, str], tuple[str, float]] = {}


@dataclass(frozen=True)
class Day:
    key: str          # 去重用：同一个 key 同一天只提一次
    title: str        # 给人看、给她看的说法
    kind: str         # milestone / anniversary / her_birthday / birthday / custom / holiday / first
    private: bool = True


def _local(ts: float) -> date:
    return datetime.fromtimestamp(ts).date()


def _on(year: int, month: int, day: int) -> date:
    """每年重复的日子落到某一年：2 月 29 日在平年按 2 月 28 日算。"""
    try: return date(year, month, day)
    except ValueError: return date(year, month, 28)


def lunar_to_solar(year: int, month: int, day: int) -> date | None:
    """农历 year 年 month 月 day 日（不含闰月）对应的公历日期。这个月没有 30 日时按 29 日算；换算不了返回 None。"""
    try:
        from lunar_python import Lunar, LunarMonth
    except ImportError:   # 依赖没装好：农历的日子先不提，公历照常
        return None
    try:
        days = LunarMonth.fromYm(year, month).getDayCount()
        solar = Lunar.fromYmd(year, month, min(day, days)).getSolar()
        return date(solar.getYear(), solar.getMonth(), solar.getDay())
    except Exception:
        return None


def solar_to_lunar(when: date) -> tuple[int, int, int] | None:
    """公历 → 农历（年、月、日；闰月按同名月算）。"""
    try:
        from lunar_python import Solar
        lunar = Solar.fromYmd(when.year, when.month, when.day).getLunar()
        return lunar.getYear(), abs(lunar.getMonth()), lunar.getDay()
    except Exception:
        return None


def _lunar_hits(on: date, month: int, day: int) -> int | None:
    """农历 month 月 day 日每年重复：今天是不是这一天（今天所在的农历年和上一个农历年都看，跨公历年的也对得上）。
    是的话返回落在哪个农历年，用来和起始年份比（Codex PR #43 R5）；不是返回 None。"""
    for year in (on.year, on.year - 1):
        if lunar_to_solar(year, month, day) == on: return year
    return None


class CompanionDates:
    def __init__(self, database, clock=time.time):
        self.db, self.clock = database, clock

    # ---- 认识那天、天数 ---------------------------------------------------------------------------
    def today(self) -> date:
        return _local(self.clock())

    def met_on(self, character_id: str) -> date | None:
        """认识那天：用户改过的优先；否则主人关系档案的 first_met；都没有就看最早的一条桌面聊天。"""
        with self.db.transaction() as c:
            row = c.execute("SELECT year, month, day FROM companion_dates WHERE character_id=? AND kind='met' AND enabled=1 ORDER BY id DESC LIMIT 1",
                            (character_id,)).fetchone()
            if row and row['year']: return date(row['year'], row['month'], row['day'])
            row = c.execute('SELECT MIN(first_met) FROM relationship_profiles WHERE character_id=? AND is_owner=1', (character_id,)).fetchone()
            if row and row[0]: return _local(float(row[0]))
            row = c.execute("SELECT MIN(created_at) FROM chat_messages WHERE character_id=? AND channel='desktop'", (character_id,)).fetchone()
        if row and row[0]:
            try: return _local(datetime.fromisoformat(str(row[0])).replace(tzinfo=timezone.utc).timestamp())   # 聊天记录时间是 UTC 文本
            except ValueError: return None
        return None

    def set_met_on(self, character_id: str, when: date) -> None:
        if when > self.today(): raise ValueError('认识那天不能在今天之后')
        def work(c):
            c.execute("DELETE FROM companion_dates WHERE character_id=? AND kind='met'", (character_id,))
            c.execute("INSERT INTO companion_dates(character_id, kind, title, year, month, day, yearly, source, created_at) VALUES (?,?,?,?,?,?,0,'user',?)",
                      (character_id, 'met', '认识那天', when.year, when.month, when.day, self.clock()))
        self.db.locked(work)

    def day_number(self, character_id: str, on: date | None = None) -> int | None:
        """认识第几天：按本地日历日算，见面当天是第 1 天（不是按 86400 秒取整）。"""
        met = self.met_on(character_id)
        if met is None: return None
        return max(1, ((on or self.today()) - met).days + 1)

    def talked_days(self, character_id: str) -> int:
        """其中有多少天说过话（桌面和主人的 QQ 私聊，按本地日期）。只用来体现感受和展示，不做签到。"""
        with self.db.transaction() as c:
            row = c.execute("SELECT COUNT(DISTINCT date(created_at, 'localtime')) FROM chat_messages WHERE character_id=? AND channel='desktop' AND role='user'",
                            (character_id,)).fetchone()
        return int(row[0] or 0)

    # ---- 哪些日子 ------------------------------------------------------------------------------
    def user_dates(self, character_id: str) -> list[dict]:
        with self.db.transaction() as c:
            rows = c.execute("SELECT * FROM companion_dates WHERE character_id=? AND kind!='met' ORDER BY month, day, id", (character_id,)).fetchall()
        return [dict(r) for r in rows]

    def add(self, character_id: str, kind: str, title: str, when: date, yearly: bool = True, lunar: bool = False) -> int:
        """when 是公历日期；lunar=True 时按它对应的农历月日每年重复（农历生日：在日历上选出生那天的公历日期就行）。"""
        if kind not in KINDS: raise ValueError('未知的日子类型')
        title = ' '.join(str(title or '').split())[:MAX_TITLE] or KINDS[kind]
        calendar, (year, month, day) = 'solar', (when.year, when.month, when.day)
        # 只一次的日子就是选中的那一天，按公历存最准：换成农历月日会丢掉闰月，落到普通月的同一天（Codex PR #43 R2）。
        # 农历只影响“每年按哪天重复”；每年重复的闰月日子按同名的普通月过，这是民间常见的做法。
        if lunar and yearly:
            converted = solar_to_lunar(when)
            if converted is None: raise ValueError('农历换算不可用（缺少 lunar_python），先按公历记')
            calendar, (year, month, day) = 'lunar', converted
        def work(c):
            if kind == 'her_birthday':   # 她的生日只有一个：改默认就是替换
                c.execute("DELETE FROM companion_dates WHERE character_id=? AND kind='her_birthday'", (character_id,))
            return c.execute("INSERT INTO companion_dates(character_id, kind, title, calendar, year, month, day, yearly, source, created_at) VALUES (?,?,?,?,?,?,?,?,'user',?)",
                             (character_id, kind, title, calendar, year, month, day, int(bool(yearly)), self.clock())).lastrowid
        return self.db.locked(work)

    def remove(self, character_id: str, date_id: int) -> None:
        with self.db.transaction() as c:
            c.execute("DELETE FROM companion_dates WHERE character_id=? AND id=? AND kind!='met'", (character_id, date_id))

    def options(self, character_id: str) -> dict:
        value = self.db.get_setting(f'date_options:{character_id}', {}) or {}
        return {'holidays': bool(value.get('holidays', True)), 'milestones': bool(value.get('milestones', True))}

    def set_options(self, character_id: str, **changes) -> None:
        self.db.set_setting(f'date_options:{character_id}', dict(self.options(character_id), **{k: bool(v) for k, v in changes.items()}))

    def first_achievement(self, character_id: str) -> date | None:
        """第一次一起办成一件事：办成的那一刻（完成时间）最早的一条。按开始时间算的话，跨午夜的任务会把周年记到开工那天，
        并发时也可能挑错（Codex PR #43 R3）。旧记录没有完成时间时退回开始时间。"""
        try:
            with self.db.transaction() as c:
                row = c.execute("SELECT MIN(COALESCE(NULLIF(ended_at, ''), started_at)) FROM agent_runs "
                                "WHERE character_id=? AND status IN ('complete','completed','done','succeeded')",
                                (character_id,)).fetchone()
            return datetime.fromisoformat(str(row[0])).date() if row and row[0] else None
        except Exception:
            return None

    def events_on(self, character_id: str, on: date | None = None) -> list[Day]:
        on = on or self.today()
        out, options = [], self.options(character_id)
        met = self.met_on(character_id)
        rows = self.user_dates(character_id)
        own_birthday = next((r for r in rows if r['kind'] == 'her_birthday' and r['enabled']), None)
        if met is not None and on >= met:
            n = (on - met).days + 1
            if options['milestones'] and (n in MILESTONES or (n > 1000 and n % 1000 == 0)):
                out.append(Day(f'milestone:{n}', f'认识第 {n} 天', 'milestone'))
            years = on.year - met.year
            # 周年和下面“第一次办成事”的周年都属于“纪念日”这一类，跟着同一个开关（Codex PR #43 R1）。
            # 默认的“她的生日”就是认识周年，关掉后一起不提；用户自己加的她的生日不受影响
            if options['milestones'] and years >= 1 and on == _on(on.year, met.month, met.day):
                extra = '，也是她的“生日”' if own_birthday is None else ''
                out.append(Day(f'anniversary:{years}', f'认识 {years} 周年{extra}', 'anniversary'))
        first = self.first_achievement(character_id)
        if options['milestones'] and first and on.year > first.year and on == _on(on.year, first.month, first.day):
            out.append(Day(f'first-done:{on.year - first.year}', f'第一次一起办成事 {on.year - first.year} 周年', 'first'))
        for r in rows:
            if not r['enabled']: continue
            if r['calendar'] == 'lunar':
                year = _lunar_hits(on, r['month'], r['day']) if r['yearly'] else None
                hit = (year is not None and (not r['year'] or year >= r['year'])) or \
                      (not r['yearly'] and r['year'] and lunar_to_solar(r['year'], r['month'], r['day']) == on)
            else:
                hit = (r['yearly'] and on == _on(on.year, r['month'], r['day']) and (not r['year'] or on.year >= r['year'])) or \
                      (not r['yearly'] and r['year'] and on == date(r['year'], r['month'], r['day']))
            if hit:
                title = r['title'] if r['kind'] == 'custom' else KINDS[r['kind']]
                if r['kind'] == 'birthday': title = '你的生日'
                out.append(Day(f"user:{r['id']}:{on.year}", title, r['kind']))
        if options['holidays']:
            if (on.month, on.day) in HOLIDAYS:
                out.append(Day(f'holiday:{on.month}-{on.day}', HOLIDAYS[(on.month, on.day)], 'holiday', private=False))
            lunar = solar_to_lunar(on)
            if lunar and (lunar[1], lunar[2]) in LUNAR_HOLIDAYS and lunar_to_solar(lunar[0], lunar[1], lunar[2]) == on:   # 闰月那天不算
                out.append(Day(f'lunar:{lunar[1]}-{lunar[2]}', LUNAR_HOLIDAYS[(lunar[1], lunar[2])], 'holiday', private=False))
            tomorrow = solar_to_lunar(on + timedelta(days=1))
            if tomorrow and (tomorrow[1], tomorrow[2]) == (1, 1):
                out.append(Day('lunar:eve', '除夕', 'holiday', private=False))
        return out

    def upcoming(self, character_id: str, days: int = 60) -> list[tuple[date, list[Day]]]:
        start = self.today()
        found = []
        for i in range(days + 1):
            day = start + timedelta(days=i)
            events = self.events_on(character_id, day)
            if events: found.append((day, events))
        return found

    # ---- 提起 ------------------------------------------------------------------------------
    def mentioned(self, character_id: str, key: str, on: date | None = None) -> bool:
        with self.db.transaction() as c:
            return c.execute('SELECT 1 FROM companion_date_mentions WHERE character_id=? AND key=? AND day=?',
                             (character_id, key, (on or self.today()).isoformat())).fetchone() is not None

    def mark(self, character_id: str, keys, on: date | None = None) -> None:
        day = (on or self.today()).isoformat()
        with self.db.transaction() as c:
            for key in keys:
                c.execute('INSERT OR IGNORE INTO companion_date_mentions(character_id, key, day, at) VALUES (?,?,?,?)',
                          (character_id, key, day, self.clock()))

    def background(self, character_id: str) -> str:
        """平时那一句背景：认识第几天、其中聊过几天。"""
        n = self.day_number(character_id)
        if n is None: return ''
        return f'\n你们认识第 {n} 天了，其中 {self.talked_days(character_id)} 天聊过天（这是背景：被问起时照实说，平时不用报数字，也不拿来怪他没来）。'

    def prompt(self, character_id: str, claim: str | None = None) -> tuple[str, tuple]:
        """给主人私下聊天的提示词：一句背景（认识第几天），当天有特别的日子且今天还没提过时再加一句提醒。
        返回（文字，这次提醒到的日子 key）。
        给了 claim（这一轮的 id）时，提醒到的日子会先替这一轮占住，别的轮次同时生成也不会再拿到；说完调 confirm(claim)，
        失败 / 取消调 release(claim)。不给 claim 时由调用方自己 mark(key)。"""
        text = self.background(character_id)
        if not text: return '', ()
        events = self.events_on(character_id)
        if claim is None:
            fresh = [d for d in events if not self.mentioned(character_id, d.key)]
        else:
            fresh = self._claim(character_id, events, claim)
        if not fresh: return text, ()
        text += ('\n今天是特别的日子：' + '、'.join(d.title for d in fresh) +
                 '。这一轮可以自然地提一句（像真人那样，一句就好，可以配个表情或语气），今天提过就不用再提。')
        return text, tuple(d.key for d in fresh)

    def _slot(self, character_id: str, key: str, day: date) -> tuple[str, str, str, str]:
        return (str(getattr(self.db, 'database_path', id(self.db))), character_id, key, day.isoformat())

    def _claim(self, character_id: str, events, claim: str) -> list[Day]:
        """在锁里看“今天提过没有、别的轮次占了没有”，没有就替这一轮占住。查库和占位在同一把锁里，两轮不会都拿到。"""
        day, now = self.today(), time.monotonic()
        taken = []
        with _CLAIMS_LOCK:
            for slot, (_, at) in list(_CLAIMS.items()):
                if now - at > CLAIM_TTL: del _CLAIMS[slot]
            for d in events:
                slot = self._slot(character_id, d.key, day)
                owner = _CLAIMS.get(slot)
                if (owner and owner[0] != claim) or self.mentioned(character_id, d.key, day): continue
                _CLAIMS[slot] = (claim, now); taken.append(d)
        return taken

    def _owned(self, character_id: str, claim: str) -> list[tuple[str, str, str, str]]:
        base = str(getattr(self.db, 'database_path', id(self.db)))
        return [slot for slot, (owner, _) in _CLAIMS.items() if owner == claim and slot[0] == base and slot[1] == character_id]

    def confirm(self, character_id: str, claim: str) -> None:
        """这一轮成功说完：把它占住的日子记成“今天提过了”。先写库再放锁，中间别的轮次不会钻空子。"""
        with _CLAIMS_LOCK:
            slots = self._owned(character_id, claim)
            if not slots: return
            with self.db.transaction() as c:
                for _, cid, key, day in slots:
                    c.execute('INSERT OR IGNORE INTO companion_date_mentions(character_id, key, day, at) VALUES (?,?,?,?)',
                              (cid, key, day, self.clock()))
            for slot in slots: _CLAIMS.pop(slot, None)

    def release(self, character_id: str, claim: str) -> None:
        """这一轮失败、被取消或回复被拦下：放掉占住的日子，下一轮还能提。"""
        with _CLAIMS_LOCK:
            for slot in self._owned(character_id, claim): _CLAIMS.pop(slot, None)
