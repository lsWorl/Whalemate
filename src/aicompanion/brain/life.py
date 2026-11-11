"""她自己的生活（计划书 v4 功能四）：空闲时按兴趣去看点真实的东西，形成自己的看法；值得说的交给统一调度器，
找合适的时候来分享，说完等你回应。

一次活动（这一版只有“读”：搜一个和兴趣有关的话题，读搜索结果的摘要）：
1. 选兴趣：人格包里的兴趣；人格没写时用默认的几样。最近三天看过的兴趣往后排。
2. 去看：用这个角色的联网搜索（community/search.py，和查梗共用每天的搜索次数上限）。搜不到、关掉联网、超额 → 这次算没做成（failed），不编。
3. 形成看法：一次小的模型调用，只根据搜到的标题和摘要说两句自己的看法、值不值得跟你说（worth 0～1）、想怎么开口；
   没配模型时不调用，直接按规则记一句“看到了某某”，值得程度给低分（大多不会拿来说）。
4. 记下来源：标题、链接、站点、摘录都存表（activities），分享时只能说这里写的内容。
5. 分享：worth ≥ SHARE_AT、24 小时内、还没说过的，交给调度器（来源 share，排在心事之后、问候之前）；说出去后标记 shared。
6. 等回应：调度器发现你回了 → 这条记为 answered，她心情好一点；没回按调度器的规则追一句或算了。

节制：每天最多做 DAILY 件（设置 life_daily_activities，默认 3），两件之间至少隔 GAP 小时；安静时段、专注模式不做；设置 life_enabled 可整体关掉。
"""
from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime

from aicompanion.brain.focus import in_quiet_hours, is_focused

DEFAULT_INTERESTS = ('深海和鲸鱼的冷知识', 'AI 和大模型的新鲜事', '最近好玩的游戏和动画', '网上新出的梗', '好吃的和做饭小技巧')
DAILY = 3
GAP_HOURS = 2.0
SHARE_AT = 0.55
SHARE_WITHIN = 24*3600
RECENT_INTEREST_DAYS = 3


class LifeService:
    def __init__(self, database, clock=time.time, rng=None):
        self.db, self.clock = database, clock
        self.rng = rng or random.Random()

    # ---- 设置与兴趣 ------------------------------------------------------------------------------
    def enabled(self) -> bool:
        return bool(self.db.get_setting('life_enabled', True))

    def daily(self) -> int:
        try: return max(0, int(self.db.get_setting('life_daily_activities', DAILY)))
        except (TypeError, ValueError): return DAILY

    def interests(self, character_id: str) -> list[str]:
        try:
            from aicompanion.services.persona_service import PersonaService
            persona = PersonaService(self.db).active(character_id)
            own = [t for t in (persona.interests if persona else ()) if t.strip()]
        except Exception:
            own = []
        return own or list(DEFAULT_INTERESTS)

    def pick_interest(self, character_id: str) -> str:
        """最近三天看过的兴趣往后排，剩下的随机挑一个。"""
        since = self.clock()-RECENT_INTEREST_DAYS*86400
        with self.db.transaction() as c:
            recent = {r[0] for r in c.execute('SELECT interest FROM activities WHERE character_id=? AND started_at>=?', (character_id, since))}
        options = self.interests(character_id)
        fresh = [i for i in options if i not in recent]
        return self.rng.choice(fresh or options)

    # ---- 什么时候去做 -----------------------------------------------------------------------------
    def should_start(self, character_id: str) -> bool:
        if not self.enabled(): return False
        now = self.clock(); local = datetime.fromtimestamp(now)
        if is_focused(self.db, local) or in_quiet_hours(self.db, local.hour): return False
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        with self.db.transaction() as c:
            if c.execute("SELECT 1 FROM activities WHERE character_id=? AND status='doing' AND started_at>=?", (character_id, now-1800)).fetchone():
                return False                                                     # 上一件还没做完
            today = c.execute("SELECT COUNT(*) FROM activities WHERE character_id=? AND started_at>=? AND status!='failed'",
                              (character_id, midnight)).fetchone()[0]
            last = c.execute('SELECT MAX(started_at) FROM activities WHERE character_id=?', (character_id,)).fetchone()[0]
        if today >= self.daily(): return False
        return last is None or now-float(last) >= GAP_HOURS*3600

    # ---- 做一件事 --------------------------------------------------------------------------------
    def run(self, character_id: str, search, client=None, cancellation=None, character_name: str = '') -> int | None:
        """做一件事。search：WebSearch（或测试替身，需要 search(query) → [Hit]）；client：聊天客户端，None 时按规则。返回活动 id。"""
        interest = self.pick_interest(character_id)
        query = interest
        now = self.clock()
        with self.db.transaction() as c:
            activity = c.execute('INSERT INTO activities(character_id, kind, interest, query, status, started_at) VALUES (?,?,?,?,?,?)',
                                 (character_id, 'read', interest, query, 'doing', now)).lastrowid
        try:
            hit = self._pick_hit(character_id, search.search(query, count=5) if search else [])
            if hit is None:
                self._finish(activity, 'failed'); return activity                 # 没看到东西：不算做过，也不编
            view = self._view(character_id, character_name, interest, hit, client, cancellation)
            with self.db.transaction() as c:
                c.execute("UPDATE activities SET status='done', title=?, url=?, site=?, excerpt=?, thought=?, opener=?, worth=?, finished_at=? WHERE id=?",
                          (hit.title[:80], hit.url[:500], (hit.site or '')[:30], hit.summary[:900], view['thought'], view['opener'],
                           view['worth'], self.clock(), activity))
            return activity
        except Exception:
            self._finish(activity, 'failed')
            raise

    def _finish(self, activity: int, status: str) -> None:
        with self.db.transaction() as c:
            c.execute('UPDATE activities SET status=?, finished_at=? WHERE id=?', (status, self.clock(), activity))

    def _pick_hit(self, character_id, hits):
        """挑一条 30 天内没看过、有正文摘要的结果。"""
        since = self.clock()-30*86400
        with self.db.transaction() as c:
            seen = {r[0] for r in c.execute("SELECT url FROM activities WHERE character_id=? AND started_at>=? AND url!=''", (character_id, since))}
        for hit in hits or []:
            if getattr(hit, 'url', '') and hit.url not in seen and len(getattr(hit, 'summary', '') or '') >= 30:
                return hit
        return None

    def _view(self, character_id, name, interest, hit, client, cancellation) -> dict:
        if client is None:
            return {'thought': f'看到一篇《{hit.title[:40]}》', 'opener': '', 'worth': 0.3}
        from aicompanion.llm.base import ChatTurn
        instruction = (
            f'你是{name or "陪伴角色"}的“内心”。你刚按自己的兴趣（{interest}）在网上看到下面这条内容。'
            '只根据给出的标题和摘要（不要补充摘要里没有的事实），用第一人称写你的看法（thought，一两句，不超过 60 字，口语，可以有情绪），'
            '判断值不值得跟用户分享（worth，0 到 1：有趣、新鲜、和你们聊过的东西有关就高；平淡、广告、敏感的就低），'
            '再写一句想怎么跟他开口（opener，不超过 30 字）。只输出 JSON：{"thought":"","worth":0.5,"opener":""}')
        context = json.dumps({'title': hit.title, 'site': hit.site, 'summary': hit.summary[:900]}, ensure_ascii=False)
        from aicompanion.usage.meter import using
        with using('life', character_id):
            raw = ''.join(client.generate_reply_stream([ChatTurn('user', context)], instruction, cancellation))
        raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.S).strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
        try:
            data = json.loads(raw)
        except ValueError:
            match = re.search(r'\{.*\}', raw, re.S)
            data = json.loads(match.group(0)) if match else {}
        try: worth = max(0.0, min(1.0, float(data.get('worth', 0))))
        except (TypeError, ValueError): worth = 0.0
        return {'thought': ' '.join(str(data.get('thought') or '').split())[:80], 'opener': ' '.join(str(data.get('opener') or '').split())[:40],
                'worth': worth}

    # ---- 分享 --------------------------------------------------------------------------------
    def shareable(self, character_id: str):
        with self.db.transaction() as c:
            return c.execute("SELECT * FROM activities WHERE character_id=? AND status='done' AND worth>=? AND finished_at>=? "
                             'ORDER BY worth DESC, finished_at DESC LIMIT 1', (character_id, SHARE_AT, self.clock()-SHARE_WITHIN)).fetchone()

    @staticmethod
    def intent(row) -> str:
        where = f'在{row["site"]}上' if row['site'] else '在网上'
        opener = f'可以这样开口：{row["opener"]}。' if row['opener'] else ''
        return (f'你刚才{where}看到一篇《{row["title"]}》，内容大概是：{row["excerpt"][:300]}。你的看法：{row["thought"]}。'
                f'现在主动找他分享这件事。{opener}像发消息那样自然地说，提一句是在哪看到的；'
                '只说上面写到的内容，不要编造细节，也不要说是系统让你来的。说完可以问问他怎么看。')

    def shared(self, activity_id: int) -> None:
        with self.db.transaction() as c:
            c.execute("UPDATE activities SET status='shared', shared_at=? WHERE id=? AND status='done'", (self.clock(), activity_id))

    def answered(self, activity_id: int, response: str = 'answered') -> None:
        with self.db.transaction() as c:
            c.execute("UPDATE activities SET response=? WHERE id=?", (response, activity_id))

    def recent(self, character_id: str, limit: int = 10) -> list[dict]:
        with self.db.transaction() as c:
            return [dict(r) for r in c.execute('SELECT * FROM activities WHERE character_id=? ORDER BY id DESC LIMIT ?', (character_id, limit))]
