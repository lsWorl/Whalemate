"""主动发到 QQ 私聊的投递记录（Codex PR #46 第三轮）。

以前服务端只知道“这条从队列里交出去了”，就拿它去推测发没发成：插件可能根本没读到心跳响应、可能只发出一半、
回执可能丢、程序可能中途重启。现在把一条投递分成几个可核实的事实，存进 qq_outbox（重启也在）：

    generating 正在生成（回复可能已经写进聊天记录，还没放进发件箱；退出重启时按它把回复拿掉）
    queued    生成好了，在发件箱里等插件心跳来取
    dequeued  服务端在心跳响应里交出去了（插件读没读到不知道）
    claimed   插件确认收到、马上开始发（插件先领取再发送，领取不到就不发）
    sent      插件回报全部发出，带第一段实际发出的时间
    partial   只发出了前几段（后面失败）
    failed    确定没发出去：插件回报失败、过期没被取走、交出去了插件却没来领取
    unknown   插件领取了，但一直没有回执，或插件回报发送超时 / 断连：可能发了也可能没发

各个事实分别管不同的事：
- 防重：交出去以后（dequeued 起）就不再为同一个话题重新生成；
- 回应和每天次数：按第一段实际发出的时间算（插件回报），不按取件时间；
- 纪念日算不算提过：只有 sent 才算；partial、failed、unknown 都放回去，宁可再提一次，不能没提却当作提过；
- 聊天记录：确定没发出去的回复从记录里拿掉。

回执可以重发（插件会重试）：已经有结果的再收到同一条回执直接认可，不重复处理；unknown 之后迟到的回执照样补记。
结果和它连带的写入（纪念日记为已提、拿掉没发出的回复）在同一个事务里提交：中途退出不会出现“记了发出、却没记提过”（Codex PR #46 第四轮）。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

QUEUE_TTL = 600     # 生成好了 10 分钟还没被取走：插件掉线，作废
CLAIM_TTL = 120     # 交出去 2 分钟插件还没来领取：插件没读到这次心跳响应，确定没发
ACK_TTL = 600       # 领取后 10 分钟还没有回执：结果未知
GENERATING_TTL = 1800   # 生成半小时还没进发件箱：生成线程早就该结束了，按没发出处理
FINAL = ('sent', 'partial', 'failed', 'unknown')


@dataclass(frozen=True)
class Delivery:
    """一条主动消息的投递结果。first_at：第一段实际发出的时间（只有 sent / partial 有）。"""
    status: str
    first_at: float | None = None

    @property
    def reached(self) -> bool:
        """他至少收到了一部分。"""
        return self.status in ('sent', 'partial')

    def __bool__(self) -> bool:   # 兼容以前的 on_done(sent: bool)
        return self.reached


class OutboxStore:
    def __init__(self, database, clock=time.time):
        self.db, self.clock = database, clock

    def begin(self, turn: str, character_id: str, guard: str = 'direct') -> None:
        """开始生成前先记一条：生成出来的回复写进聊天记录后、放进发件箱前程序退出，启动时能认出这条回复没发出去。"""
        with self.db.transaction() as c:
            c.execute('INSERT INTO qq_outbox(turn_id, character_id, status, created_at, guard) VALUES (?,?,?,?,?)',
                      (turn, character_id, 'generating', self.clock(), guard))

    def add(self, turn: str, character_id: str, date_keys=(), day: str = '') -> bool:
        """生成完成后入队。清理器可能已判定这一轮过期，旧线程恢复时不能覆盖结果。"""
        with self.db.transaction() as c:
            return c.execute("""INSERT INTO qq_outbox(turn_id, character_id, status, date_keys, date_day, created_at, guard)
                                VALUES (?,?,'queued',?,?,?,'direct')
                                ON CONFLICT(turn_id) DO UPDATE SET status='queued', date_keys=excluded.date_keys,
                                    date_day=excluded.date_day, created_at=excluded.created_at
                                WHERE qq_outbox.status='generating' AND qq_outbox.character_id=excluded.character_id""",
                             (turn, character_id, json.dumps(list(date_keys), ensure_ascii=False), day, self.clock())).rowcount == 1

    def get(self, turn: str):
        with self.db.transaction() as c:
            return c.execute('SELECT * FROM qq_outbox WHERE turn_id=?', (turn,)).fetchone()

    def dequeued(self, turn: str) -> None:
        with self.db.transaction() as c:
            c.execute("UPDATE qq_outbox SET status='dequeued', dequeued_at=? WHERE turn_id=? AND status='queued'", (self.clock(), turn))

    def claim(self, turn: str, valid=None) -> bool:
        """插件领取：只有刚交出去、还没过期的才能领。领取成功插件才发送；失败（已作废、已有结果）插件就不发。"""
        def claim_in(c):
            if valid is not None and not valid(c): return False
            return c.execute("UPDATE qq_outbox SET status='claimed', claimed_at=? WHERE turn_id=? AND status='dequeued' AND dequeued_at>=?",
                             (self.clock(), turn, self.clock()-CLAIM_TTL)).rowcount == 1
        return self.db.locked(claim_in)

    def settle(self, turn: str, status: str, first_at: float | None = None, *, expected: str | None = None) -> Delivery | None:
        """记下结果并处理连带的事（纪念日、聊天记录）。已经有确定结果的不再改（回执重发时幂等）；返回这次新定下的结果。"""
        allowed = {'sent': ('claimed', 'unknown'), 'partial': ('claimed', 'unknown'),
                   'failed': ('generating', 'queued', 'dequeued', 'claimed', 'unknown'), 'unknown': ('claimed',)}[status]
        with self.db.transaction() as c:
            row = c.execute('SELECT * FROM qq_outbox WHERE turn_id=?', (turn,)).fetchone()
            if row is None or row['status'] not in allowed or (expected and row['status'] != expected): return None
            if not self.settle_in(c, row, status, first_at): return None
        try:                                               # 这一轮在内存里的纪念日占位（重启后已经没有了）
            from aicompanion.services.companion_dates import CompanionDates
            CompanionDates(self.db, clock=self.clock).release(row['character_id'], turn)
        except Exception:
            pass
        return Delivery(status, first_at)

    def settle_in(self, c, row, status, first_at=None):
        if not c.execute("UPDATE qq_outbox SET status=?, first_sent_at=?, settled_at=? WHERE turn_id=? AND status=?",
                         (status, first_at, self.clock(), row['turn_id'], row['status'])).rowcount:
            return False
        self._after(c, row, status)
        return True

    def _after(self, c, row, status: str) -> None:
        cid, turn = row['character_id'], row['turn_id']
        try: keys = json.loads(row['date_keys'] or '[]')
        except ValueError: keys = []
        if status == 'sent' and keys and row['date_day']:
            for key in keys:                               # 按生成时记下的日子和 key 记“提过了”，重启后的迟到回执也能补上
                c.execute('INSERT OR IGNORE INTO companion_date_mentions(character_id, key, day, at) VALUES (?,?,?,?)',
                          (cid, key, row['date_day'], self.clock()))
        if status == 'failed':                             # 没发出去：不算她说过
            c.execute("DELETE FROM chat_messages WHERE turn_id=? AND character_id=? AND role='assistant'", (turn, cid))

    def sweep(self) -> list[tuple[str, str, Delivery]]:
        """按时间把卡住的投递定下来。返回（轮次, 角色, 结果）。调度器和桥接都会调用，重启后的旧记录也在这里收尾。"""
        now = self.clock()
        with self.db.transaction() as c:
            rows = c.execute("SELECT turn_id, character_id, status FROM qq_outbox WHERE "
                             "(status='generating' AND created_at<?) OR "
                             "(status='queued' AND created_at<?) OR (status='dequeued' AND dequeued_at<?) OR (status='claimed' AND claimed_at<?)",
                             (now-GENERATING_TTL, now-QUEUE_TTL, now-CLAIM_TTL, now-ACK_TTL)).fetchall()
        done = []
        for row in rows:
            result = self.settle(row['turn_id'], 'unknown' if row['status'] == 'claimed' else 'failed', expected=row['status'])
            if result: done.append((row['turn_id'], row['character_id'], result))
        return done
