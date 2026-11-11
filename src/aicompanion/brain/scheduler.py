"""统一的主动行为调度（计划书 v4 功能四“统一的主动行为调度”，阶段三第一步）。

以前问候、心事、各自定时，彼此不知道对方说过没有：可能刚问完早安又冒出一件心事，他没回也照样接着发。
现在所有“她主动开口”都先过这里：调度器决定要不要开口、说哪一件，模型决定说什么。

规则（数值是初值，按体感调，都能在设置里改）：
- 总开关、安静时段、专注模式照旧；他 3 分钟内刚说过话不插嘴。
- 每天主动开口有上限（proactive_daily_limit，默认 5 次，追问也算）。
- 两次主动开口至少隔一段时间（proactive_gap_minutes，默认 90 分钟，再乘关系节奏：越亲近越常来）。
- 上一句他还没回：不开新话题。过一会儿（45～90 分钟）他在电脑前却没理，可能撒娇追一句，也可能自己算了；
  一个话题最多追一次；3 小时还没回就当他没空，记一点点委屈（持续情绪里的“被冷落”，力度很小），之后可以聊别的。
- 他回了（主动消息之后他说了话）就算接上了。
- 先后：今天是特别的日子而他还没来 → 心里惦记的事 → 她自己看到的有意思的事（功能四，brain/life.py）→ 早晚问候 / 闲聊。

记录在 outreach_log：每次主动开口一条，渠道（桌面 / QQ）、状态（sent / answered / followed / let_go / ignored）。
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from aicompanion.brain.focus import in_quiet_hours, is_focused

DAILY_LIMIT = 5
GAP_MINUTES = 90
RECENT_TALK_SECONDS = 180
PRESENT_SECONDS = 600            # 电脑闲置不到 10 分钟：他在电脑前
FOLLOW_AFTER = (45*60, 90*60)    # 没回多久以后考虑追一句
FOLLOW_CHANCE = 0.6              # 到时候追问的概率；其余就“算了”
GIVE_UP_SECONDS = 3*3600
PENDING_STALE = 30*60            # 发送中超过半小时还没确认：当作没发出去
DATE_FROM_HOUR = 9


@dataclass
class Outreach:
    source: str                  # date / purpose / greeting / follow
    key: str
    intent: str
    payload: dict = field(default_factory=dict)


class ProactiveScheduler:
    def __init__(self, database, purposes=None, greetings=None, clock=time.time, rng=random.random, life=None):
        self.db, self.purposes, self.greetings, self.life = database, purposes, greetings, life
        self.clock, self.rng = clock, rng

    # ---- 设置 --------------------------------------------------------------------------------
    def limit(self) -> int:
        try: return max(0, int(self.db.get_setting('proactive_daily_limit', DAILY_LIMIT)))
        except (TypeError, ValueError): return DAILY_LIMIT

    def gap_seconds(self, character_id: str) -> float:
        try: minutes = max(10.0, float(self.db.get_setting('proactive_gap_minutes', GAP_MINUTES)))
        except (TypeError, ValueError): minutes = GAP_MINUTES
        pace = 1.0
        if self.greetings is not None:
            try: pace = float(self.greetings.pace_of(character_id))
            except Exception: pace = 1.0
        return minutes*60*max(0.3, pace)

    # ---- 记录 --------------------------------------------------------------------------------
    def reserve(self, character_id: str, outreach: Outreach, channel: str = 'desktop') -> int:
        """选中要说的那一刻先占一条 pending：发送中（尤其是走 QQ、要等插件回执的）也算“今天说过一次”、也挡住下一次开口，
        不会在回执到之前又选中同一句追问（Codex PR #46 第 1 项）。说成了 confirm，没说成 release。"""
        now = self.clock()
        def work(c):
            return c.execute('INSERT INTO outreach_log(character_id, source, key, channel, status, parent_id, sent_at) VALUES (?,?,?,?,?,?,?)',
                             (character_id, outreach.source, outreach.key, channel, 'pending', outreach.payload.get('parent_id'), now)).lastrowid
        return self.db.locked(work)

    def confirm(self, character_id: str, outreach_id: int, outreach: Outreach, channel: str = 'desktop') -> None:
        """真说出来了：pending → sent（时间按说出来的时候算）；追问还会把它追的那条标成 followed；分享的活动标成 shared。"""
        now = self.clock()
        def work(c):
            parent = outreach.payload.get('parent_id')
            if parent: c.execute("UPDATE outreach_log SET status='followed' WHERE id=? AND status='sent'", (parent,))
            updated = c.execute("UPDATE outreach_log SET status='sent', channel=?, sent_at=? WHERE id=? AND status='pending'",
                                (channel, now, outreach_id)).rowcount
            if not updated:   # 回执来得太晚，占位已经当作没发出去撤掉了：照实补记一条
                c.execute('INSERT INTO outreach_log(character_id, source, key, channel, status, parent_id, sent_at) VALUES (?,?,?,?,?,?,?)',
                          (character_id, outreach.source, outreach.key, channel, 'sent', parent, now))
        self.db.locked(work)
        if outreach.source == 'share' and self.life is not None: self.life.shared(outreach.payload['activity']['id'])

    def release(self, outreach_id: int) -> None:
        """没说成（生成失败、被打断、QQ 没发出去）：撤掉占位，不算说过。"""
        with self.db.transaction() as c:
            c.execute("DELETE FROM outreach_log WHERE id=? AND status='pending'", (outreach_id,))

    def record(self, character_id: str, outreach: Outreach, channel: str = 'desktop') -> int:
        """占位并立刻确认（已经说出来时用）。"""
        outreach_id = self.reserve(character_id, outreach, channel)
        self.confirm(character_id, outreach_id, outreach, channel)
        return outreach_id

    def today_count(self, character_id: str) -> int:
        start = datetime.fromtimestamp(self.clock()).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        with self.db.transaction() as c:
            return c.execute('SELECT COUNT(*) FROM outreach_log WHERE character_id=? AND sent_at>=?', (character_id, start)).fetchone()[0]

    def last(self, character_id: str):
        """最近一条已经说出来的（pending 另算）。"""
        with self.db.transaction() as c:
            return c.execute("SELECT * FROM outreach_log WHERE character_id=? AND status!='pending' ORDER BY id DESC LIMIT 1", (character_id,)).fetchone()

    def pending(self, character_id: str) -> bool:
        """有还在发送中的主动消息。超过 PENDING_STALE 还没确认的当作没发出去（程序中途退出之类），撤掉。"""
        now = self.clock()
        with self.db.transaction() as c:
            c.execute("DELETE FROM outreach_log WHERE character_id=? AND status='pending' AND sent_at<?", (character_id, now-PENDING_STALE))
            return c.execute("SELECT 1 FROM outreach_log WHERE character_id=? AND status='pending' LIMIT 1", (character_id,)).fetchone() is not None

    def seconds_since_user(self, character_id: str) -> float:
        """他最近一次说话距今多久：桌面和主人的 QQ 私聊都算（两边都记在 desktop 频道；Codex PR #46 第 2 项）。"""
        with self.db.transaction() as c:
            row = c.execute("SELECT MAX(created_at) FROM chat_messages WHERE character_id=? AND channel='desktop' AND role='user'",
                            (character_id,)).fetchone()
        if not row or not row[0]: return float('inf')
        try: when = datetime.fromisoformat(str(row[0])).replace(tzinfo=timezone.utc).timestamp()
        except ValueError: return float('inf')
        return max(0.0, self.clock()-when)

    def _user_spoke_since(self, character_id: str, since: float) -> bool:
        stamp = datetime.fromtimestamp(since, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')   # chat_messages.created_at 是 UTC 文本
        with self.db.transaction() as c:
            return c.execute("SELECT 1 FROM chat_messages WHERE character_id=? AND channel='desktop' AND role='user' AND created_at>=? LIMIT 1",
                             (character_id, stamp)).fetchone() is not None

    def settle(self, character_id: str) -> None:
        """更新还在等回应的主动消息：他回了 → answered；太久没回 → ignored（记一点点委屈）。"""
        now = self.clock()
        with self.db.transaction() as c:
            waiting = c.execute("SELECT * FROM outreach_log WHERE character_id=? AND status IN ('sent','followed') ORDER BY id", (character_id,)).fetchall()
        for row in waiting:
            if self._user_spoke_since(character_id, row['sent_at']):
                self._set(row['id'], 'answered', now)
                self._shared_reply(character_id, row, 'answered')
            elif now-row['sent_at'] >= GIVE_UP_SECONDS:
                self._set(row['id'], 'ignored', now)
                # 一件事只记一次小委屈：记在最早那条上（追问那句没回不再另记）
                if row['parent_id']: continue
                self._shared_reply(character_id, row, 'ignored')
                try:
                    from aicompanion.services.mood_service import MoodService
                    MoodService(self.db, clock=self.clock).event(character_id, 'ignored', reason='主动找他说话没回', scale=0.4)
                except Exception:
                    pass

    def _shared_reply(self, character_id, row, response) -> None:
        """分享的那件事有没有被接住：记到活动上；接住了她开心一点（功能四：你回了以后更新她的心情和这段话题的状态）。"""
        if row['source'] != 'share' or self.life is None: return
        try:
            self.life.answered(int(str(row['key']).split(':', 1)[1]), response)
            if response == 'answered':
                from aicompanion.services.mood_service import MoodService
                MoodService(self.db, clock=self.clock).event(character_id, 'praised', reason='分享的事他接住了', scale=0.4)
        except Exception:
            pass

    def _set(self, outreach_id: int, status: str, now: float) -> None:
        with self.db.transaction() as c:
            c.execute('UPDATE outreach_log SET status=?, settled_at=? WHERE id=?', (status, now, outreach_id))

    # ---- 决定 --------------------------------------------------------------------------------
    def next(self, character_id: str, system_idle: float, seconds_since_user: float) -> Outreach | None:
        if not self.db.get_setting('proactive_enabled', True): return None
        now_ts = self.clock(); now = datetime.fromtimestamp(now_ts)
        if is_focused(self.db, now) or in_quiet_hours(self.db, now.hour): return None
        seconds_since_user = min(seconds_since_user, self.seconds_since_user(character_id))
        if seconds_since_user < RECENT_TALK_SECONDS: return None
        if self.pending(character_id): return None                              # 上一条还在发送中
        self.settle(character_id)
        if self.today_count(character_id) >= self.limit(): return None          # pending 也计入今天的次数
        last = self.last(character_id)
        if last is not None and last['status'] == 'sent':
            return self._follow_up(character_id, last, now_ts, system_idle)    # 上一句还没回：不开新话题
        if last is not None:
            # “算了”、没等到回应的那条：从放下它的时候起算间隔，不会刚说完“算了”就换个话题又来
            since = last['settled_at'] if last['status'] in ('let_go', 'ignored') and last['settled_at'] else last['sent_at']
            if now_ts-since < self.gap_seconds(character_id): return None
        return (self._date(character_id, now) or self._purpose(character_id, system_idle, seconds_since_user)
                or self._share(character_id, system_idle) or self._greeting(character_id, seconds_since_user))

    def _follow_up(self, character_id, last, now_ts, system_idle) -> Outreach | None:
        if last['source'] == 'follow' or last['parent_id']: return None             # 追问过了就不再追
        if system_idle >= PRESENT_SECONDS: return None                               # 他不在电脑前，不算没理她
        rng = random.Random(f"{character_id}:{last['id']}")                           # 每条消息的“多久后追”固定，重启也一样
        # 追问也是一次主动开口，设置里的“两次之间至少隔 N 分钟”同样适用（Codex PR #46 第 3 项）；
        # 间隔设得比“没回就放下”的 3 小时还长时，就不会追问
        if now_ts-last['sent_at'] < max(rng.uniform(*FOLLOW_AFTER), self.gap_seconds(character_id)): return None
        if self.rng() >= FOLLOW_CHANCE:
            self._set(last['id'], 'let_go', now_ts); return None                       # 自己算了
        said = self._said_after(character_id, last['sent_at'])
        gist = f'（你说的是：“{said[:60]}”）' if said else ''
        return Outreach('follow', f"follow:{last['id']}",
                        f'你刚才主动找他说了话{gist}，过了好一会儿他还没回，人好像就在电脑前。'
                        '可以撒个娇追一句，或者装作不在意地嘀咕一句；只说一句，别质问，别重复刚才的话，也别说是系统让你来的。',
                        {'parent_id': last['id']})

    def _said_after(self, character_id, since) -> str:
        stamp = datetime.fromtimestamp(since-5, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        with self.db.transaction() as c:
            row = c.execute("SELECT content FROM chat_messages WHERE character_id=? AND channel='desktop' AND role='assistant' AND created_at>=? "
                            "ORDER BY id LIMIT 1", (character_id, stamp)).fetchone()
        return ' '.join(str(row['content']).split()) if row else ''

    def _date(self, character_id, now: datetime) -> Outreach | None:
        """今天是特别的日子、还没提过、他今天也还没来找她：主动去说（具体提哪几个日子由聊天上下文带上，说完记为已提）。"""
        if now.hour < DATE_FROM_HOUR: return None
        try:
            from aicompanion.services.companion_dates import CompanionDates
            dates = CompanionDates(self.db, clock=self.clock)
            fresh = [d for d in dates.events_on(character_id) if not dates.mentioned(character_id, d.key)]
        except Exception:
            return None
        if not fresh: return None
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        if self._user_spoke_since(character_id, midnight): return None             # 他今天来过了，聊天时自然会提
        key = f'date:{now.date().isoformat()}'
        if not self.db.claim_proactive(character_id, key): return None
        titles = '、'.join(d.title for d in fresh)
        return Outreach('date', key, f'今天是{titles}，他今天还没来找你。主动发消息找他，自然地提一句今天这个日子，像真人那样，一两句就好。')

    def _purpose(self, character_id, system_idle, seconds_since_user) -> Outreach | None:
        if self.purposes is None: return None
        row = self.purposes.due(character_id, system_idle, seconds_since_user)
        if not row: return None
        return Outreach('purpose', f"purpose:{row['id']}", self.purposes.intent(row), {'purpose': row})

    def _share(self, character_id, system_idle) -> Outreach | None:
        """她自己看到的有意思的事（功能四）。他在电脑前时才来说，不然说了也是对着空屋子。"""
        if self.life is None or system_idle >= PRESENT_SECONDS or not self.life.enabled(): return None   # 总开关也管分享（Codex PR #48）
        row = self.life.shareable(character_id)
        if row is None: return None
        return Outreach('share', f"share:{row['id']}", self.life.intent(row), {'activity': dict(row)})

    def _greeting(self, character_id, seconds_since_user) -> Outreach | None:
        if self.greetings is None: return None
        due = self.greetings.due(character_id, seconds_since_user)
        if not due: return None
        key, intent = due
        return Outreach('greeting', key, intent)
