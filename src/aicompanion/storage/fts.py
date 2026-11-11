"""全文检索（计划书 v4 功能六“记忆召回基础”）：聊天记录建 SQLite FTS5 trigram 索引，旧聊天也能按关键词找回。

为什么用 trigram：中文没有空格分词，trigram 按三个字切，不需要额外的分词库。代价是两个字的词（“奶茶”）单独搜不到——
召回时先用三字片段在索引里粗筛，再按二字片段重合度精排；只有两个字的询问不查旧聊天（记忆和存档仍按二字片段比对）。
SQLite 没编进 FTS5 或 trigram 时（极少见），索引不建，召回退回只看记忆和存档，不影响聊天。
"""
from __future__ import annotations

import re
import sqlite3

TABLE = 'message_fts'
SCAN = 400          # 索引里先取这么多条最相关的，再按角色和频道过滤
_TRIGGERS = (
    f"CREATE TRIGGER IF NOT EXISTS {TABLE}_ai AFTER INSERT ON chat_messages BEGIN "
    f"INSERT INTO {TABLE}(rowid, content) VALUES (new.id, new.content); END",
    f"CREATE TRIGGER IF NOT EXISTS {TABLE}_ad AFTER DELETE ON chat_messages BEGIN "
    f"INSERT INTO {TABLE}({TABLE}, rowid, content) VALUES ('delete', old.id, old.content); END",
    f"CREATE TRIGGER IF NOT EXISTS {TABLE}_au AFTER UPDATE OF content ON chat_messages BEGIN "
    f"INSERT INTO {TABLE}({TABLE}, rowid, content) VALUES ('delete', old.id, old.content); "
    f"INSERT INTO {TABLE}(rowid, content) VALUES (new.id, new.content); END",
)


def ensure(connection, rebuild: bool = False) -> bool:
    """建好聊天记录的全文索引和同步触发器（可重复调用）。rebuild：老库升级时把已有聊天一次性补进索引。返回是否可用。"""
    try:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)).fetchone()
        connection.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {TABLE} USING fts5(content, content='chat_messages', "
                           "content_rowid='id', tokenize='trigram')")
        for statement in _TRIGGERS: connection.execute(statement)
        if rebuild or not exists: connection.execute(f"INSERT INTO {TABLE}({TABLE}) VALUES ('rebuild')")
        return True
    except sqlite3.OperationalError:
        return False


def available(connection) -> bool:
    return bool(connection.execute("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)).fetchone())


def trigram_query(grams) -> str:
    """把三字片段拼成 FTS5 的 OR 查询；每个片段加引号，里面的双引号转义，避免被当成语法。"""
    return ' OR '.join('"'+g.replace('"', '""')+'"' for g in grams)


def search(connection, grams, character_id: str, channel: str, before_id: int, after_id: int = 0, limit: int = 30):
    """在这个角色、这个频道、(after_id, before_id) 范围内找含这些三字片段的消息，按相关度排序。"""
    if not grams or not available(connection): return []
    try:
        # 先在索引里取最相关的一批（子查询，让 SQLite 先走全文索引），再按角色、频道、范围过滤；
        # 直接 JOIN 时规划器可能先扫聊天表、再逐行做 MATCH，几万条记录会慢到秒级（实测）
        return connection.execute(
            f"SELECT m.id, m.role, m.content, m.created_at FROM (SELECT rowid, rank FROM {TABLE} WHERE {TABLE} MATCH ? "
            "AND rowid<? AND rowid>? ORDER BY rank LIMIT ?) f JOIN chat_messages m ON m.id=f.rowid "
            "WHERE m.character_id=? AND m.channel=? AND m.status='complete' ORDER BY f.rank LIMIT ?",
            (trigram_query(grams), before_id, after_id, SCAN, character_id, channel, limit)).fetchall()
    except sqlite3.OperationalError:
        return []


_SPLIT = re.compile(r'[\s，。！？、；：,.!?;:“”"\'（）()【】\[\]《》<>…~～—\-]+')


def segments(text: str) -> list[str]:
    return [s for s in _SPLIT.split(text or '') if s]
