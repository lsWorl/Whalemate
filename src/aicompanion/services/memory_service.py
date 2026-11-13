from __future__ import annotations

import json
import re
from aicompanion.domain.cancellation import CancellationToken
from aicompanion.llm.base import ChatTurn
from aicompanion.storage.database import Database


def similar(a: str, b: str, threshold: float = 0.5) -> bool:
    """两段中文是否说的是同一件事：字符二元组的重合比例（相对较短的一段），或者一段完整包含另一段里不短的片段。"""
    def grams(text):
        text = re.sub(r'\W+', '', text.lower())
        return {text[i:i+2] for i in range(len(text)-1)} or {text}
    x, y = grams(a), grams(b)
    if not x or not y: return False
    overlap = len(x & y)/min(len(x), len(y))
    tokens = re.findall(r'[A-Za-z0-9-]{6,}', b)
    return overlap >= threshold or any(t in a for t in tokens)


class MemoryService:
    """自动记忆后台提取；删除过记忆的角色暂停历史重分析，避免同义改写复活。"""

    def __init__(self, database: Database) -> None:
        self.database = database

    def build_memory_context(self, query="", character_id="deepseek-whale", scene="pet", channel="desktop") -> str:
        """按相关性、重要度和清晰度挑记忆；模糊的会标出来；和这句话相关而被用上的记忆清晰度回升。"""
        from aicompanion.brain.memory_card import render, select_for_prompt
        from aicompanion.services.semantic import shared_here
        rows = self.database.memory_card(character_id)
        if channel != 'desktop':
            with self.database.transaction() as c:
                rows = [r for r in rows if shared_here(c, character_id, r, channel)]
        selected = select_for_prompt(rows, query, 8, scene)
        recalled = [row['id'] for row, _, matched in selected if matched]
        if recalled: self.database.touch_memories(recalled)
        return render(selected)

    def set_scope(self, ids, character_id, scope) -> list[int]:
        """记忆页的"只在私下用 / 共享"。标成私下时，内容和它高度重合的其他记忆（例如之前从群里整理出的副本）一起标成私下，
        在同一个写事务里完成。返回实际改动的记忆 id。"""
        ids = list(ids); changed = list(ids)
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if scope == 'private' and ids:
                marks = connection.execute(f"SELECT content FROM memories WHERE character_id=? AND id IN ({','.join('?'*len(ids))})", (character_id, *ids)).fetchall()
                for row in connection.execute("SELECT id,content FROM memories WHERE character_id=? AND scope!='private'", (character_id,)).fetchall():
                    if row['id'] not in ids and any(similar(row['content'], m['content']) or similar(m['content'], row['content']) for m in marks):
                        changed.append(row['id'])
            connection.executemany("UPDATE memories SET scope=? WHERE id=? AND character_id=?", [(scope, i, character_id) for i in changed])
        return changed

    @staticmethod
    def public_guard(content, character_id, channel=None):
        """公开来源能写的记忆（在写事务里按此刻的数据检查）：不能落在已有的非公开事实键上（桌面 / 私下记住的只由桌面那边更新），
        也不能和"只在私下用"的记忆内容高度重合（防止换个键把私下的事复制成共享；整理期间才被标成私下的也算）。"""
        def guard(connection, key):
            from aicompanion.services.semantic import shared_here
            row = connection.execute("SELECT scope,source_ids FROM memories WHERE character_id=? AND fact_key=?", (character_id, key)).fetchone()
            if row is not None and (row['scope'] not in ('qq', 'bili', 'public') or (channel is not None and not shared_here(connection, character_id, row, channel))):
                return False
            private = connection.execute("SELECT content FROM memories WHERE character_id=? AND scope='private'", (character_id,)).fetchall()
            return not any(similar(content, r['content']) for r in private)
        return guard

    def maybe_extract_memory(self, user_message: str, character_id="deepseek-whale", source_id=None) -> None:
        # 离线规则仅识别明确稳定信息，否定、假设和过去偏好交给模型分析。
        patterns = [("identity", "name", r"(?:我的名字是|我叫)([^，。！？\n]{1,20})"),
                    ("preference", "like", r"^我喜欢([^，。！？\n]{1,40})[。！]?$"),
                    ("preference", "dislike", r"^我讨厌([^，。！？\n]{1,40})[。！]?$"),
                    ("event", "remember", r"^记住[：:，, ]?(.{1,100})$")]
        if self.database.has_deleted_memories(character_id):
            return
        for category, key, pattern in patterns:
            match = re.search(pattern, user_message)
            if match:
                value = match.group(1).strip()
                fact_key = key if key == "name" else f"{key}:{value}"
                summary = {"name": "用户的名字是", "like": "用户喜欢", "dislike": "用户不喜欢", "remember": "用户明确希望记住："}[key] + value
                self.database.upsert_memory(summary, character_id, fact_key, category, source_ids=[source_id] if source_id else [])

    def pending(self, character_id, channel="desktop") -> int:
        """这个频道还没整理进记忆的用户消息条数。"""
        rows = self.database.recent_messages(40, character_id, channel=channel)
        done = self.database.get_setting(self.marker(character_id, channel), 0)
        return sum(1 for r in rows if r['role'] == 'user' and r['id'] > done)

    @staticmethod
    def marker(character_id, channel="desktop") -> str:
        return f"memory_through:{character_id}" if channel == "desktop" else f"memory_through:{character_id}:{channel}"

    def analyze(self, character_id, client, cancellation: CancellationToken, channel="desktop") -> None:
        """把一段聊天整理成记忆。channel 不是 desktop 时是群聊 / 陌生人私聊：说话的不只是爹，记忆写清楚是谁的，
        记成 QQ 来源，阶段摘要只用于产生原话的频道。"""
        from aicompanion.services import stages
        from aicompanion.domain.cancellation import ReplyCancelled
        from aicompanion.services.circle_topics import CircleTopics, allowed, cutoff
        with self.database.transaction() as c:
            group_topics = allowed(c, channel)
            if channel.startswith('qq:group:') and not group_topics:
                return
            floor = cutoff(c, character_id, channel) if group_topics else 0
        topics = CircleTopics(self.database)
        try:
            if client is not None:
                stages.compact(self.database, character_id, channel, client, cancellation)
        except ReplyCancelled:
            raise
        except Exception:
            pass  # 摘要服务的失败独立于事实提取。
        forgotten = self.database.has_deleted_memories(character_id)
        if forgotten and not group_topics:
            return
        public = channel != "desktop"
        rows = self.database.recent_messages(40, character_id, channel=channel)
        if group_topics:
            rows = [r for r in rows if r['id'] > floor]
        if not rows:
            return
        last_id = rows[-1]['id']
        marker = self.marker(character_id, channel)
        if last_id <= self.database.get_setting(marker, 0):
            return
        users = [r for r in rows if r['role'] == 'user']
        if client is None or forgotten:
            if group_topics:
                cancellation.check()
                topics.collect(character_id, channel, users)
                self.database.set_setting(marker, last_id)
            return
        existing = [dict(r) for r in self.database.recent_memories(character_id=character_id)]
        if public:
            # 公开来源的整理看不到"只在私下用"的记忆；写入时再在同一事务里按最新状态把关（public_guard）
            from aicompanion.services.semantic import shared_here, source_channel
            with self.database.transaction() as c:
                if group_topics:
                    existing = [r for r in existing if r['scope'] == 'qq' and source_channel(c, character_id, r) == channel]
                else:
                    existing = [r for r in existing if r['scope'] != 'private' and shared_here(c, character_id, r, channel)]
        instruction = (
            '仅提取用户明确表达的稳定事实、偏好、重要经历和关系事件。不要将假设、否定或角色猜测当事实。'
            '输出 JSON {"memories":[{"content":"摘要","fact_key":"稳定语义键",'
            '"category":"identity|preference|event|relationship","importance":2,"confidence":0.9,"source_ids":[消息ID]}]}。'
            '使用已有相同事实键更新更正后的事实；无新事实返回空数组。不要输出 Markdown。'
        )
        if public:
            instruction += ('\n这是聊天平台上的群聊或陌生人私聊记录，说话人不是同一个"用户"：每条用户消息开头是"昵称#编号"（行首带【】标记的是用户本人，例如旧记录里的【爹】）。'
                            # 记忆里统一写“用户”，不写“爹”“男朋友”这类关系称呼：换人格、换关系设定后记忆照样成立（长期约束 2）
                            '记忆必须写清楚是谁的事，例如"群友大壮#2 是程序员"、"用户在群里说周末去爬山"；不要把群友的事记成用户的；'
                            'fact_key 里带上说话人标记（如 "qq#2:job"）。群里的玩笑、刷屏、对她的调侃不用记。')
        if group_topics:
            instruction += ('\n同一个 JSON 另加 topics 数组，只整理本群反复讨论的具体话题，每项包含 '
                            'name（可沿用原名的短语）、summary（一句话）、source_ids（支持它的本群用户消息编号）。'
                            '从本批消息取证，无合适话题用空数组。话题不要再写入 memories。')
        context = {"messages": [{"id":r['id'],"role":r['role'],"content":r['content']} for r in rows],
                              "existing": [{"content":r['content'],"fact_key":r['fact_key']} for r in existing]}
        if group_topics:
            # 同一话题跨批整理时优先沿用名字；只提供本群已登记的名称。
            context['topic_names'] = [row['name'] for row in topics.rows(character_id, channel)[:12]]
        context = json.dumps(context, ensure_ascii=False)
        # 使用可取消流式调用，即使这里仅需要完整结构化结果。
        from aicompanion.usage.meter import using
        try:
            with using('memory', character_id):
                raw = ''.join(client.generate_reply_stream([ChatTurn("user", context)], instruction, cancellation))
            cancellation.check()
            raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            data = json.loads(raw)
            if not isinstance(data,dict) or not isinstance(data.get('memories'),list):
                raise ValueError("记忆分析格式无效")
        except ReplyCancelled:
            raise
        except Exception:
            if group_topics:
                cancellation.check()
                topics.collect(character_id, channel, users)
            raise
        group_summaries = []
        if group_topics:
            group_summaries = topics.collect(character_id, channel, users, data.get('topics'), analyzed_after=floor)
        source_ids = {r['id'] for r in users}
        for item in data['memories'][:12]:
            cancellation.check()
            if not isinstance(item,dict):
                continue
            content, key = item.get('content'), item.get('fact_key')
            category = item.get('category')
            sources = item.get('source_ids', [])
            if not isinstance(content,str) or not isinstance(key,str) or not key or len(key)>120:
                continue
            if category not in {'identity','preference','event','relationship'} or not isinstance(sources,list):
                continue
            sources = [s for s in sources if isinstance(s,int) and s in source_ids]
            if not sources:
                continue
            # 模型可能把同一段话同时放进两个字段。群话题摘要不能再经共享事实进入桌面。
            if any(ids.intersection(sources) and similar(content, summary) for summary, ids in group_summaries):
                continue
            # 删除与分析并发时再次检查，防止生成了新事实键绕过 tombstone。
            if self.database.has_deleted_memories(character_id):
                return
            importance, confidence = item.get('importance',2), item.get('confidence',0.8)
            if not isinstance(importance,int) or not isinstance(confidence,(float,int)):
                continue
            self.database.upsert_memory(content,character_id,key,category,importance,confidence,sources,scope='qq' if public else 'pet',
                                        guard=self.public_guard(content,character_id,channel) if public else None)
        self.database.set_setting(marker,last_id)

