"""统一的主动行为调度（计划书 v4 功能四“统一的主动行为调度”，阶段三第一步）。

以前问候、心事、各自定时，彼此不知道对方说过没有：可能刚问完早安又冒出一件心事，他没回也照样接着发。
现在所有“她主动开口”都先过这里：调度器决定要不要开口、说哪一件，模型决定说什么。

规则（数值是初值，按体感调，都能在设置里改）：
- 总开关、安静时段、专注模式照旧；他 3 分钟内刚说过话不插嘴。
- 每天主动开口有上限（proactive_daily_limit，默认 5 次，追问也算）。
- 两次主动开口至少隔一段时间（proactive_gap_minutes，默认 90 分钟）。这是下限：关系还生疏时节奏放慢、隔得更久，
  再亲近也不会比用户设的更短（Codex PR #46 第二轮）。
- 上一句他还没回：不开新话题。过一会儿（45～90 分钟）他在电脑前却没理，可能撒娇追一句，也可能自己算了；
  一个话题最多追一次；3 小时还没回就当他没空，记一点点委屈（持续情绪里的“被冷落”，力度很小），之后可以聊别的。
- 他回了（主动消息之后他说了话）就算接上了。
- 先后：今天是特别的日子而他还没来 → 心里惦记的事 → 早晚问候 / 闲聊。

记录在 outreach_log：每次主动开口一条，渠道（桌面 / QQ）、状态：
- pending：选中了、还没送出去（桌面在生成；QQ 在生成、排队或刚交给插件）；
- delivering：QQ 插件已经领取、正在发；
- unknown：插件领取了却一直没回执，发没发到不知道。算今天一次、挡住重复，但不追问、不当作他没理她；
- sent / answered / followed / let_go / ignored：说出来以后的去向。
sent_at 记的是对方最早能看到的时刻（桌面：开始说；QQ：插件回报的第一段实际发出时间），用来判断“他是不是回了这一句”和算每天次数。
QQ 那边每条投递的实际结果记在 qq_outbox（bridge/outbox.py），这里每次决定前按它对账：程序重启、回调丢了也能补齐。
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
PENDING_STALE = 30*60            # 占位半小时还没交出去（程序中途退出之类）：当作没发出去；已交给插件的按发出去算
DATE_FROM_HOUR = 9


@dataclass
class Outreach:
    source: str                  # date / purpose / greeting / follow
    key: str
    intent: str
    payload: dict = field(default_factory=dict)


class ProactiveScheduler:
    def __init__(self, database, purposes=None, greetings=None, clock=time.time, rng=random.random):
        self.db, self.purposes, self.greetings = database, purposes, greetings
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
        # 用户填的是“至少”：关系节奏只能把间隔拉长（还不熟时少打扰），不能缩短到设置以下（Codex PR #46 第二轮）
        return minutes*60*max(1.0, pace)

    # ---- 记录 --------------------------------------------------------------------------------
    def reserve(self, character_id: str, outreach: Outreach, channel: str = 'desktop') -> int:
        """选中要说的那一刻先占一条 pending：发送中（尤其是走 QQ、要等插件回执的）也算“今天说过一次”、也挡住下一次开口，
        不会在回执到之前又选中同一句追问（Codex PR #46 第 1 项）。说成了 confirm，没说成 release。"""
        now = self.clock()
        def work(c):
            return c.execute('INSERT INTO outreach_log(character_id, source, key, channel, status, parent_id, sent_at) VALUES (?,?,?,?,?,?,?)',
                             (character_id, outreach.source, outreach.key, channel, 'pending', outreach.payload.get('parent_id'), now)).lastrowid
        return self.db.locked(work)

    def attach(self, outreach_id: int, turn_id: str, channel: str | None = None) -> None:
        """记下这次主动开口对应的聊天轮次（追问时按它找回原话；QQ 按它对账投递结果）和渠道。"""
        if not turn_id: return
        with self.db.transaction() as c:
            c.execute('UPDATE outreach_log SET turn_id=?, channel=COALESCE(?, channel) WHERE id=?', (turn_id, channel, outreach_id))

    def handed(self, outreach_id: int, turn_id: str | None = None) -> None:
        """QQ 插件领取了这条、开始发送：之后不再当作没发过，等它回报结果。"""
        now = self.clock()
        with self.db.transaction() as c:
            c.execute("UPDATE outreach_log SET status='delivering', channel='qq', handed_at=?, turn_id=COALESCE(?, turn_id) "
                      "WHERE id=? AND status='pending'", (now, turn_id, outreach_id))

    def confirm(self, character_id: str, outreach_id: int, outreach: Outreach, channel: str = 'desktop', at: float | None = None) -> None:
        """真说出来了：→ sent；追问还会把它追的那条标成 followed。
        at：对方最早能看到的时刻。QQ 用插件回报的第一段实际发出时间（取件、领取都只是发送尝试的开始，Codex PR #46 第三轮）；
        桌面不传，用开始说的时间。"""
        now = self.clock()
        def work(c):
            parent = outreach.payload.get('parent_id')
            if parent: c.execute("UPDATE outreach_log SET status='followed' WHERE id=? AND status='sent'", (parent,))
            updated = c.execute("UPDATE outreach_log SET status='sent', channel=?, sent_at=COALESCE(?, sent_at), settled_at=NULL "
                                "WHERE id=? AND status IN ('pending','delivering','unknown')", (channel, at, outreach_id)).rowcount
            if not updated and not c.execute('SELECT 1 FROM outreach_log WHERE id=?', (outreach_id,)).fetchone():
                # 结果来得太晚，占位已经当作没发出去撤掉了：照实补记一条
                c.execute('INSERT INTO outreach_log(character_id, source, key, channel, status, parent_id, sent_at) VALUES (?,?,?,?,?,?,?)',
                          (character_id, outreach.source, outreach.key, channel, 'sent', parent, at or now))
        self.db.locked(work)

    def unknown(self, outreach_id: int) -> None:
        """插件领取了却一直没回执：发没发到不知道。留着防重、算今天一次，但不追问（不能拿不确定的事说他没理她）。"""
        with self.db.transaction() as c:
            c.execute("UPDATE outreach_log SET status='unknown', settled_at=? WHERE id=? AND status IN ('pending','delivering')",
                      (self.clock(), outreach_id))

    def release(self, outreach_id: int) -> None:
        """没说成（生成失败、被打断、QQ 确定没发出去）：撤掉占位，不算说过。"""
        with self.db.transaction() as c:
            c.execute("DELETE FROM outreach_log WHERE id=? AND status IN ('pending','delivering','unknown')", (outreach_id,))

    def discard(self, outreach_id: int) -> None:
        """确定没发出去：不管这条后来被记成什么（结果未知期间他说了话、被记成 answered 之类），都撤掉，
        今天的次数也跟着减回去（Codex PR #46 第四轮：迟到的明确失败回执要能纠正已经变化的状态）。"""
        with self.db.transaction() as c:
            c.execute("DELETE FROM outreach_log WHERE id=?", (outreach_id,))

    def settle_delivery(self, character_id: str, outreach_id: int, outreach: Outreach, result, channel: str = 'qq') -> None:
        """按投递结果收尾这条主动开口，以及它在心事 / 问候 / 纪念日那边的占位。
        result：bridge.outbox.Delivery（QQ），或 True / False（桌面说完没有）。
        - 发出（含只发出一部分）：记成说过，时间按第一段实际发出；心事算送到；
        - 确定没发出：撤掉，心事和问候 / 纪念日的占位放回，下次还能说；
        - 结果未知：不主动重发（心事算送到、问候 / 纪念日的占位留着），但纪念日“提没提过”不确认（bridge/outbox.py），
          他来聊天时还可能自然提起——宁可再提一次，也不能没提却当作提过。"""
        status = getattr(result, 'status', 'sent' if result else 'failed')
        # 先做连带的写入（心事送到 / 放回、问候和纪念日占位放回，都可以重复做），最后才改调度记录：
        # 中途退出时调度记录还停在原来的状态，重启后按 qq_outbox 对账会把整套重放一遍，不会出现“记成说过、心事却还是待说”
        # 这样的半截状态（Codex PR #46 第四轮）
        purpose = outreach.key.split(':', 1)[1] if outreach.source == 'purpose' and outreach.key.startswith('purpose:') else None
        try:
            if purpose and self.purposes is not None:
                if status == 'failed': self.purposes.release(int(purpose))
                else: self.purposes.delivered(int(purpose))
            if status == 'failed' and outreach.source in ('date', 'greeting'): self.db.release_proactive(character_id, outreach.key)
        except Exception:
            pass
        if status in ('sent', 'partial'): self.confirm(character_id, outreach_id, outreach, channel, getattr(result, 'first_at', None))
        elif status == 'unknown':
            parent = outreach.payload.get('parent_id')
            if parent:   # 追过了（到没到不知道）：这个话题不再追
                with self.db.transaction() as c: c.execute("UPDATE outreach_log SET status='followed' WHERE id=? AND status='sent'", (parent,))
            self.unknown(outreach_id)
        else: self.discard(outreach_id)

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
        """最近一条已经有结果的（发送中的另算）。"""
        with self.db.transaction() as c:
            return c.execute("SELECT * FROM outreach_log WHERE character_id=? AND status NOT IN ('pending','delivering') ORDER BY id DESC LIMIT 1",
                             (character_id,)).fetchone()

    def reconcile(self, character_id: str) -> None:
        """按 qq_outbox 对账走 QQ 的主动开口：回调丢了（程序重启、回执晚到）也把调度记录、渠道、心事和占位补齐。"""
        try:
            from aicompanion.bridge.outbox import Delivery, OutboxStore
            OutboxStore(self.db, self.clock).sweep()
            with self.db.transaction() as c:
                # 要对账的：还没结算的；投递确定失败的（不管调度记录后来变成了什么）；
                # 迟到的发出回执、首达时间和记录不一致的（结果未知期间他说了话被记成 answered 等，也要按实际首达时间校正）
                rows = c.execute("SELECT o.*, q.status AS delivery, q.first_sent_at FROM outreach_log o JOIN qq_outbox q ON q.turn_id=o.turn_id "
                                 "WHERE o.character_id=? AND q.status IN ('sent','partial','failed','unknown') AND ("
                                 " o.status IN ('pending','delivering') OR (o.status='unknown' AND q.status!='unknown') OR q.status='failed'"
                                 " OR (q.status IN ('sent','partial') AND q.first_sent_at IS NOT NULL AND (o.sent_at!=q.first_sent_at OR o.channel!='qq')))",
                                 (character_id,)).fetchall()
        except Exception:
            return
        for row in rows:
            outreach = Outreach(row['source'], row['key'], '', {'parent_id': row['parent_id']} if row['parent_id'] else {})
            delivery = Delivery(row['delivery'], row['first_sent_at'])
            if row['status'] in ('pending', 'delivering', 'unknown') or row['delivery'] == 'failed':
                self.settle_delivery(character_id, row['id'], outreach, delivery)
            else:   # 已经有了对话上的去向（answered / let_go / ignored …）：只校正首达时间和渠道，去向不动；心事照样补记送到
                try:
                    if row['source'] == 'purpose' and self.purposes is not None and str(row['key']).startswith('purpose:'):
                        self.purposes.delivered(int(str(row['key']).split(':', 1)[1]))
                except Exception:
                    pass
                with self.db.transaction() as c:
                    c.execute("UPDATE outreach_log SET sent_at=?, channel='qq' WHERE id=?", (row['first_sent_at'], row['id']))

    def pending(self, character_id: str) -> bool:
        """有还在发送中的主动消息。先按 QQ 投递记录对账；再把超过 PENDING_STALE 还没送出去、也没进 QQ 发件箱的
        （程序在生成时退出之类）撤掉，问候 / 纪念日 / 心事的占位一起放回，下次还能说。"""
        self.reconcile(character_id)
        now = self.clock()
        with self.db.transaction() as c:
            # 跑过本 PR 早期版本（e38ed29）的库：插件取走了、回执前退出，留下没有发件箱记录的 delivering，对账永远对不上。
            # 取走超过半小时还没结果的当作“结果未知”：算今天一次、不重发、不追问，三小时后悄悄放下（Codex PR #46 第四轮）
            c.execute("UPDATE outreach_log SET status='unknown', settled_at=? WHERE character_id=? AND status='delivering' "
                      "AND COALESCE(handed_at, sent_at)<? AND NOT EXISTS (SELECT 1 FROM qq_outbox q WHERE q.turn_id=outreach_log.turn_id)",
                      (now, character_id, now-PENDING_STALE))
            stale = c.execute("SELECT * FROM outreach_log o WHERE character_id=? AND status='pending' AND sent_at<? "
                              "AND NOT EXISTS (SELECT 1 FROM qq_outbox q WHERE q.turn_id=o.turn_id)", (character_id, now-PENDING_STALE)).fetchall()
            for row in stale: c.execute("DELETE FROM outreach_log WHERE id=? AND status='pending'", (row['id'],))
            busy = c.execute("SELECT 1 FROM outreach_log WHERE character_id=? AND status IN ('pending','delivering') LIMIT 1",
                             (character_id,)).fetchone() is not None
        for row in stale: self._give_back(character_id, row['source'], row['key'])
        return busy

    def _give_back(self, character_id: str, source: str, key: str) -> None:
        try:
            if source in ('date', 'greeting'): self.db.release_proactive(character_id, key)
            elif source == 'purpose' and self.purposes is not None and key.startswith('purpose:'):
                self.purposes.release(int(key.split(':', 1)[1]))
        except Exception:
            pass

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
            waiting = c.execute("SELECT * FROM outreach_log WHERE character_id=? AND status IN ('sent','followed','unknown') ORDER BY id",
                                (character_id,)).fetchall()
        for row in waiting:
            if self._user_spoke_since(character_id, row['sent_at']):
                self._set(row['id'], 'answered', now)
            elif row['status'] == 'unknown':
                # 发没发到都不知道：太久没动静就悄悄放下，不记委屈（不能拿不确定的事说他没理她）
                if now-row['sent_at'] >= GIVE_UP_SECONDS: self._set(row['id'], 'let_go', now)
            elif now-row['sent_at'] >= GIVE_UP_SECONDS:
                self._set(row['id'], 'ignored', now)
                # 一件事只记一次小委屈：记在最早那条上（追问那句没回不再另记）
                if row['parent_id']: continue
                try:
                    from aicompanion.services.mood_service import MoodService
                    MoodService(self.db, clock=self.clock).event(character_id, 'ignored', reason='主动找他说话没回', scale=0.4)
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
        if last is not None and last['status'] == 'unknown': return None        # 结果未知：不追问，也不开新话题，等他说话或悄悄放下
        if last is not None:
            # “算了”、没等到回应的那条：从放下它的时候起算间隔，不会刚说完“算了”就换个话题又来
            since = last['settled_at'] if last['status'] in ('let_go', 'ignored') and last['settled_at'] else last['sent_at']
            if now_ts-since < self.gap_seconds(character_id): return None
        return (self._date(character_id, now) or self._purpose(character_id, system_idle, seconds_since_user)
                or self._greeting(character_id, seconds_since_user))

    def _follow_up(self, character_id, last, now_ts, system_idle) -> Outreach | None:
        if last['source'] == 'follow' or last['parent_id']: return None             # 追问过了就不再追
        with self.db.transaction() as c:                                                 # 同一个话题已经有追问（含发送中、结果未知的）
            if c.execute('SELECT 1 FROM outreach_log WHERE parent_id=? LIMIT 1', (last['id'],)).fetchone(): return None
        if system_idle >= PRESENT_SECONDS: return None                               # 他不在电脑前，不算没理她
        rng = random.Random(f"{character_id}:{last['id']}")                           # 每条消息的“多久后追”固定，重启也一样
        # 追问也是一次主动开口，设置里的“两次之间至少隔 N 分钟”同样适用（Codex PR #46 第 3 项）；
        # 间隔设得比“没回就放下”的 3 小时还长时，就不会追问
        if now_ts-last['sent_at'] < max(rng.uniform(*FOLLOW_AFTER), self.gap_seconds(character_id)): return None
        if self.rng() >= FOLLOW_CHANCE:
            self._set(last['id'], 'let_go', now_ts); return None                       # 自己算了
        said = self._said(character_id, last)
        gist = f'（你说的是：“{said[:60]}”）' if said else ''
        return Outreach('follow', f"follow:{last['id']}",
                        f'你刚才主动找他说了话{gist}，过了好一会儿他还没回，人好像就在电脑前。'
                        '可以撒个娇追一句，或者装作不在意地嘀咕一句；只说一句，别质问，别重复刚才的话，也别说是系统让你来的。',
                        {'parent_id': last['id']})

    def _said(self, character_id, last) -> str:
        """上一句主动消息的原话：按记下的轮次取；旧记录没有轮次时按时间就近找。
        QQ 是先生成、插件心跳时才取件，取件时间可能比生成晚十几秒，不能只按时间往前看几秒（Codex PR #46 第二轮 P3）。"""
        with self.db.transaction() as c:
            row = None
            if last['turn_id']:
                row = c.execute("SELECT content FROM chat_messages WHERE character_id=? AND turn_id=? AND role='assistant' LIMIT 1",
                                (character_id, last['turn_id'])).fetchone()
            if row is None:
                stamp = datetime.fromtimestamp(last['sent_at']-5, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
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

    def _greeting(self, character_id, seconds_since_user) -> Outreach | None:
        if self.greetings is None: return None
        due = self.greetings.due(character_id, seconds_since_user)
        if not due: return None
        key, intent = due
        return Outreach('greeting', key, intent)
