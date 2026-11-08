"""目的机制：从近期记忆里提炼"心里惦记的事"，按时机主动开口（参考 surfcore 的 Purpose Outreach）。

四种目的：想弄明白（understand）、想做（do）、想分享（share）、想向你要（ask）。
- 生成：每 2–5 小时一次，挂起中的目的不超过 2 个；每个目的必须引用来源记忆。
- 送达：到点、非安静时段、非专注、你在电脑前（系统闲置 < 10 分钟）、你 3 分钟内没说话、今天没超过次数上限。
- 超过 24 小时没送达的目的过期。
- 载体目前只有桌宠（气泡 + 语音）；以后 QQ 接入时再做"桌宠不在线就走 QQ"的互斥回落。
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta

from aicompanion.brain.focus import in_quiet_hours, is_focused
from aicompanion.brain.memory_card import CLEAR, today_start

KINDS = {'understand': '想弄明白', 'do': '想做', 'share': '想分享', 'ask': '想向你要'}
DAILY_LIMIT = 3
MAX_PENDING = 2
AWAY_SECONDS = 600
RECENT_TALK_SECONDS = 180
EXPIRE_HOURS = 24


class PurposeEngine:
    def __init__(self, database, clock=datetime.now, rng=None):
        self.database, self.clock = database, clock
        self.rng = rng or random.Random()

    # ---- 生成 ----
    def should_generate(self, character_id: str) -> bool:
        if not self.database.get_setting('purpose_enabled', True): return False
        now = self.clock()
        next_at = self.database.get_setting(f'purpose_next_gen:{character_id}')
        if next_at and datetime.fromisoformat(next_at) > now: return False
        return len(self.database.purposes(character_id, 'pending')) < MAX_PENDING

    def _schedule_next(self, character_id: str) -> None:
        later = self.clock()+timedelta(hours=self.rng.uniform(2, 5))
        self.database.set_setting(f'purpose_next_gen:{character_id}', later.isoformat(timespec='seconds'))

    def _due_time(self) -> str:
        """20–150 分钟后，避开安静时段（顺延到醒来后半小时）。"""
        due = self.clock()+timedelta(minutes=self.rng.uniform(20, 150))
        for _ in range(24):
            if not in_quiet_hours(self.database, due.hour): break
            due = (due+timedelta(hours=1)).replace(minute=30, second=0, microsecond=0)
        return due.isoformat(timespec='seconds')

    def candidates(self, character_id: str):
        rows = [r for r in self.database.memory_card(character_id) if float(r['clarity']) >= CLEAR and r['scope'] == 'pet']
        return sorted(rows, key=lambda r: (r['importance']*float(r['clarity']), r['id']), reverse=True)[:20]

    def generate(self, character_id: str, client=None, cancellation=None) -> list[int]:
        """client 为 None（离线）时按规则生成；否则让模型提炼。返回新目的的 id。"""
        self._schedule_next(character_id)
        memories = self.candidates(character_id)
        if not memories: return []
        items = self._from_model(character_id, memories, client, cancellation) if client is not None else self._from_rules(memories)
        ids = []
        known = {r['id'] for r in memories}
        for item in items[:MAX_PENDING]:
            kind, content, opener = item.get('kind'), item.get('content'), item.get('opener', '')
            sources = [i for i in item.get('memory_ids', []) if isinstance(i, int) and i in known]
            if kind not in KINDS or not isinstance(content, str) or not content.strip() or len(content) > 120 or not sources: continue
            if not isinstance(opener, str): opener = ''
            ids.append(self.database.add_purpose(character_id, kind, content.strip(), opener.strip()[:120], sources, self._due_time()))
        return ids

    def _from_rules(self, memories):
        row = self.rng.choice(memories[:5])
        return [{'kind': 'understand', 'content': f'想知道"{row["content"]}"这件事最近怎么样了',
                 'opener': '', 'memory_ids': [row['id']]}]

    def _from_model(self, character_id, memories, client, cancellation):
        from aicompanion.llm.base import ChatTurn
        instruction = (
            '你是陪伴角色的"内心"。根据下面这些你记得的关于用户的事，想出 0 到 2 件你心里惦记、想主动找用户聊的事。'
            'kind 只能是 understand（想弄明白）、do（想一起做）、share（想分享）、ask（想向用户要点什么，比如一张照片、一个推荐）。'
            'content 用第一人称写你的念头（一句话，不超过 40 字）；opener 写见面时自然开口的第一句（口语，不超过 30 字）；'
            'memory_ids 必须是下面记忆的 id。不要编造记忆里没有的经历；没有值得说的就返回空数组。'
            '只输出 JSON：{"purposes":[{"kind":"","content":"","opener":"","memory_ids":[1]}]}'
        )
        context = json.dumps({'memories': [{'id': r['id'], 'content': r['content'], 'category': r['category']} for r in memories],
                              'existing_purposes': [p['content'] for p in self.database.purposes(character_id, 'pending')],
                              'now': self.clock().isoformat(timespec='minutes')}, ensure_ascii=False)
        from aicompanion.usage.meter import using
        with using('purpose', character_id):
            raw = ''.join(client.generate_reply_stream([ChatTurn('user', context)], instruction, cancellation))
        raw = raw.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
        data = json.loads(raw)
        items = data.get('purposes') if isinstance(data, dict) else None
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    # ---- 送达 ----
    def expire_old(self, character_id: str) -> None:
        cutoff = (self.clock()-timedelta(hours=EXPIRE_HOURS)).isoformat(timespec='seconds')
        for row in self.database.purposes(character_id, 'pending'):
            if row['due_at'] < cutoff: self.database.update_purpose(row['id'], 'expired')

    def due(self, character_id: str, system_idle_seconds: float, seconds_since_user: float):
        """返回现在该说的目的（数据库行），或 None。"""
        if not self.database.get_setting('purpose_enabled', True): return None
        now = self.clock()
        self.expire_old(character_id)
        if is_focused(self.database, now) or in_quiet_hours(self.database, now.hour): return None
        if system_idle_seconds >= AWAY_SECONDS or seconds_since_user < RECENT_TALK_SECONDS: return None
        limit = int(self.database.get_setting('purpose_daily_limit', DAILY_LIMIT))
        if self.database.purposes_delivered_since(character_id, today_start(now)) >= limit: return None
        stamp = now.isoformat(timespec='seconds')
        for row in self.database.purposes(character_id, 'pending'):
            if row['due_at'] <= stamp: return row
        return None

    def intent(self, row) -> str:
        """交给对话生成的主动意图。"""
        hint = f'可以参考这样开口：{row["opener"]}。' if row['opener'] else ''
        return (f'你心里一直惦记着一件事（{KINDS.get(row["kind"], "想聊")}）：{row["content"]}。'
                f'现在自然地主动提起，像发消息那样说一句或连着几句都行。{hint}不要说是系统或提醒让你来的，也不要编造记忆里没有的细节。')

    def delivered(self, purpose_id: int) -> None:
        self.database.update_purpose(purpose_id, 'delivered', self.clock().isoformat(timespec='seconds'))

    def release(self, purpose_id: int) -> None:
        """没说成（被打断或失败）：放回待送达，稍后再试。"""
        self.database.update_purpose(purpose_id, 'pending')
