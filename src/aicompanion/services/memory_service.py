from __future__ import annotations

import json
import re
from aicompanion.domain.cancellation import CancellationToken
from aicompanion.llm.base import ChatTurn
from aicompanion.storage.database import Database


class MemoryService:
    """自动记忆后台提取；删除过记忆的角色暂停历史重分析，避免同义改写复活。"""

    def __init__(self, database: Database) -> None:
        self.database = database

    def build_memory_context(self, query="", character_id="emilia") -> str:
        memories = self.database.recent_memories(character_id=character_id)
        grams = {query[i:i+2] for i in range(len(query)-1)}
        def score(row):
            matches = sum(gram in row['content'] for gram in grams)
            return matches * 4 + row['importance'] + row['confidence']
        selected = sorted(memories, key=score, reverse=True)[:8]
        return "\n".join(f"- {row['content']}" for row in selected) or "暂无已确认的长期记忆。"

    def maybe_extract_memory(self, user_message: str, character_id="emilia", source_id=None) -> None:
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

    def analyze(self, character_id, client, cancellation: CancellationToken) -> None:
        if self.database.has_deleted_memories(character_id):
            return
        rows = self.database.recent_messages(40, character_id)
        if not rows:
            return
        last_id = rows[-1]['id']
        marker = f"memory_through:{character_id}"
        if last_id <= self.database.get_setting(marker, 0):
            return
        users = [r for r in rows if r['role'] == 'user']
        existing = [dict(r) for r in self.database.recent_memories(character_id=character_id)]
        instruction = (
            '仅提取用户明确表达的稳定事实、偏好、重要经历和关系事件。不要将假设、否定或角色猜测当事实。'
            '输出 JSON {"memories":[{"content":"摘要","fact_key":"稳定语义键",'
            '"category":"identity|preference|event|relationship","importance":2,"confidence":0.9,"source_ids":[消息ID]}]}。'
            '使用已有相同事实键更新更正后的事实；无新事实返回空数组。不要输出 Markdown。'
        )
        context = json.dumps({"messages": [{"id":r['id'],"content":r['content']} for r in users],
                              "existing": [{"content":r['content'],"fact_key":r['fact_key']} for r in existing]}, ensure_ascii=False)
        # 使用可取消流式调用，即使这里仅需要完整结构化结果。
        raw = ''.join(client.generate_reply_stream([ChatTurn("user", context)], instruction, cancellation))
        cancellation.check()
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
        if not isinstance(data,dict) or not isinstance(data.get('memories'),list):
            raise ValueError("记忆分析格式无效")
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
            # 删除与分析并发时再次检查，防止生成了新事实键绕过 tombstone。
            if self.database.has_deleted_memories(character_id):
                return
            importance, confidence = item.get('importance',2), item.get('confidence',0.8)
            if not isinstance(importance,int) or not isinstance(confidence,(float,int)):
                continue
            self.database.upsert_memory(content,character_id,key,category,importance,confidence,sources)
        self.database.set_setting(marker,last_id)

