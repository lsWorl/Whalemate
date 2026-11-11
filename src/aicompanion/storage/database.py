from __future__ import annotations

from contextlib import contextmanager, closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from aicompanion.domain.character import CharacterProfile, DEFAULT_CHARACTER
from aicompanion.domain.emotion import EmotionState
from aicompanion.storage.schema import SCHEMA_SQL, SCHEMA_VERSION


class Database:
    """角色隔离、事务和迁移集中于存储层，连接绝不跨线程共享。"""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)

    def connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @contextmanager
    def transaction(self):
        # Connection 的 with 负责事务，closing 才负责释放文件句柄。
        with closing(self.connect()) as connection:
            with connection:
                yield connection

    def initialize(self) -> None:
        with closing(self.connect()) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError("数据库来自更新版本，请升级应用后再打开。")
            tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if version < SCHEMA_VERSION and "chat_messages" in tables:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                backup = self.database_path.with_suffix(f".backup-{stamp}.sqlite3")
                with closing(sqlite3.connect(backup)) as destination:
                    connection.backup(destination)
            connection.execute("BEGIN IMMEDIATE")
            try:
                if version == 0:
                    additions = {
                        "chat_messages": [("character_id", "TEXT NOT NULL DEFAULT 'deepseek-whale'"), ("turn_id", "TEXT"), ("status", "TEXT NOT NULL DEFAULT 'complete'"), ("source", "TEXT NOT NULL DEFAULT 'text'")],
                        "memories": [("character_id", "TEXT NOT NULL DEFAULT 'deepseek-whale'"), ("category", "TEXT NOT NULL DEFAULT 'legacy'"), ("fact_key", "TEXT NOT NULL DEFAULT ''"), ("confidence", "REAL NOT NULL DEFAULT 1"), ("source_ids", "TEXT NOT NULL DEFAULT '[]'")],
                        "reminders": [("character_id", "TEXT NOT NULL DEFAULT 'deepseek-whale'")],
                    }
                    for table, columns in additions.items():
                        if table in tables:
                            existing = {r[1] for r in connection.execute(f"PRAGMA table_info({table})")}
                            for name, definition in columns:
                                if name not in existing:
                                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
                    if "emotion_state" in tables:
                        connection.execute("ALTER TABLE emotion_state RENAME TO legacy_emotion")
                if version < 5 and "chat_messages" in tables:
                    # v5（T9 修订版；试用过 T9 初版的库是"v4、有 channel、没有 sender"，同样按缺哪列补哪列）。
                    # 聊天记录按"频道"隔离。desktop = 桌面和已绑定主人的私聊（同一个她、同一段相处）；
                    # qq:group:<群号> / qq:user:<号> = 公开场合，聊天记录互不可见（记忆另行共享，见 bridge/service.py）。
                    # sender：聊天平台上说话人的稳定 ID（平台:号），昵称会重名，不能只靠昵称区分群友。
                    existing = {r[1] for r in connection.execute("PRAGMA table_info(chat_messages)")}
                    for name, definition in (("channel", "TEXT NOT NULL DEFAULT 'desktop'"), ("sender", "TEXT NOT NULL DEFAULT ''")):
                        if name not in existing:
                            connection.execute(f"ALTER TABLE chat_messages ADD COLUMN {name} {definition}")
                if version < 2 and "memories" in tables:
                    # v2：记忆卡增加清晰度、上次想起时间、作用范围
                    existing = {r[1] for r in connection.execute("PRAGMA table_info(memories)")}
                    for name, definition in (("clarity", "REAL NOT NULL DEFAULT 1"), ("recalled_at", "TEXT"), ("scope", "TEXT NOT NULL DEFAULT 'pet'")):
                        if name not in existing:
                            connection.execute(f"ALTER TABLE memories ADD COLUMN {name} {definition}")
                if "outreach_log" in tables:
                    # v13 的主动开口记录在 PR #46 审查中补了两列（插件取件时间、对应轮次）；先跑过这个分支的库按缺哪列补哪列
                    existing = {r[1] for r in connection.execute("PRAGMA table_info(outreach_log)")}
                    for name, definition in (("turn_id", "TEXT"), ("handed_at", "REAL")):
                        if name not in existing:
                            connection.execute(f"ALTER TABLE outreach_log ADD COLUMN {name} {definition}")
                # executescript 隐式提交，固定 schema 逐条执行才保证迁移原子性。
                for statement in SCHEMA_SQL.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                self._insert_character(connection, DEFAULT_CHARACTER)
                if version == 0 and "emotion_state" in tables:
                    connection.execute("INSERT OR REPLACE INTO emotion_state(character_id,mood,affection,loneliness,energy,updated_at) SELECT 'deepseek-whale',mood,affection,loneliness,energy,updated_at FROM legacy_emotion WHERE id=1")
                    connection.execute("DROP TABLE legacy_emotion")
                for row in connection.execute("SELECT id, content FROM memories WHERE fact_key=''").fetchall():
                    key = hashlib.sha256(row['content'].strip().encode()).hexdigest()
                    connection.execute("UPDATE memories SET fact_key=? WHERE id=?", (key, row['id']))
                if version < 2:
                    # 现有记忆整体写入全量存档（只追加）
                    connection.execute("INSERT INTO memory_archive(character_id,content,category,fact_key,importance,scope,source_ids,reason) "
                                       "SELECT character_id,content,category,fact_key,importance,scope,source_ids,'migrated' FROM memories")
                connection.execute("UPDATE conversation_turns SET status='interrupted' WHERE status='generating'")
                connection.execute("UPDATE purposes SET status='pending' WHERE status='delivering'")
                # 上次退出时还在发件箱里、没被插件领取的 QQ 主动消息：内存里的待发内容已经没了，确定发不出去。
                # 标成失败并把那条回复从聊天记录里拿掉（插件之后再来领取也会被拒绝，不会发）。领取过的等回执或超时
                stuck = [r[0] for r in connection.execute("SELECT turn_id FROM qq_outbox WHERE status IN ('queued','dequeued')")]
                for turn in stuck:
                    connection.execute("UPDATE qq_outbox SET status='failed', settled_at=? WHERE turn_id=?", (time.time(), turn))
                    connection.execute("DELETE FROM chat_messages WHERE turn_id=? AND role='assistant'", (turn,))
                connection.execute("UPDATE agent_runs SET status='interrupted',ended_at=datetime('now','localtime') WHERE status='running'")   # 上次退出时没说完的心事放回去
                # 重启后未确认的外部发送不能自动重发；尚未开始的待发结果作废。
                connection.execute("UPDATE expression_deliveries SET status='unknown' WHERE status='sending'")
                connection.execute("UPDATE expression_deliveries SET status='cancelled' WHERE status='pending'")
                # 计划书 v4 功能六：聊天记录全文索引（触发器里有分号，不能放进按分号切开执行的 SCHEMA_SQL）
                from aicompanion.storage import fts
                fts.ensure(connection, rebuild=version < 10 and "chat_messages" in tables)
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _insert_character(connection, character: CharacterProfile) -> None:
        connection.execute("INSERT OR IGNORE INTO characters VALUES (?,?,?,?,?,?)", (character.id, character.name, character.description, character.speaking_style, character.model_path, character.voice))
        connection.execute("INSERT OR IGNORE INTO emotion_state(character_id) VALUES (?)", (character.id,))
        connection.execute("INSERT OR IGNORE INTO relationship_state(character_id) VALUES (?)", (character.id,))

    def add_character(self, character: CharacterProfile) -> None:
        with self.transaction() as connection:
            self._insert_character(connection, character)

    def characters(self) -> list[CharacterProfile]:
        with self.transaction() as connection:
            return [CharacterProfile(**dict(r)) for r in connection.execute("SELECT * FROM characters ORDER BY rowid")]

    def update_character(self, character: CharacterProfile) -> None:
        with self.transaction() as connection:
            connection.execute("UPDATE characters SET name=?,description=?,speaking_style=?,model_path=?,voice=? WHERE id=?", (character.name, character.description, character.speaking_style, character.model_path, character.voice, character.id))

    def add_message(self, role: str, content: str, character_id="deepseek-whale", turn_id=None, status="complete", source="text", channel="desktop", sender="") -> int:
        with self.transaction() as connection:
            cursor = connection.execute("INSERT INTO chat_messages(role,content,character_id,turn_id,status,source,channel,sender) VALUES (?,?,?,?,?,?,?,?)", (role, content, character_id, turn_id, status, source, channel, sender))
            return cursor.lastrowid

    def drop_reply(self, turn_id, character_id) -> None:
        """群里插话时模型选择不说：把这一轮的回复从记录里拿掉（没发出去的话不算她说过）。"""
        with self.transaction() as connection:
            connection.execute("DELETE FROM chat_messages WHERE turn_id=? AND character_id=? AND role='assistant'", (turn_id, character_id))

    def locked(self, fn):
        """在一个 BEGIN IMMEDIATE 写事务里执行 fn(connection)：fn 读到的设置和它的写入之间不会插进别的写（例如设置页撤销权限）。
        fn 抛异常时整个事务回滚。"""
        with self.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return fn(connection)

    @staticmethod
    def setting_in(connection, key, default=None):
        row = connection.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def set_setting_in(connection, key, value) -> None:
        connection.execute("INSERT INTO app_settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value, ensure_ascii=False)))

    def reserve_daily(self, key: str, limit: int) -> bool:
        """原子地"判断没到上限就加一"：同一事务里读和写，多个线程 / 多个服务实例同时来也不会超过 limit。"""
        return self.reserve_daily_all([(key, limit)])

    def reserve_daily_all(self, budgets) -> bool:
        """几个每日计数一起预留：全部没到上限才同时各加一，有一个到了就都不动（不会白白占掉另一份额度）。"""
        def reserve(connection):
            used = [int(self.setting_in(connection, key, 0) or 0) for key, _ in budgets]
            if any(u >= int(limit) for u, (_, limit) in zip(used, budgets)): return False
            for u, (key, _) in zip(used, budgets): self.set_setting_in(connection, key, u+1)
            return True
        return self.locked(reserve)

    def recent_messages(self, limit=30, character_id="deepseek-whale", before_id=None, channel="desktop") -> list[sqlite3.Row]:
        """只返回同一频道的记录：桌面（含已绑定主人的私聊）看不到群聊和陌生人，公开场合也看不到桌面。"""
        with self.transaction() as connection:
            rows = connection.execute("SELECT * FROM chat_messages WHERE character_id=? AND channel=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?", (character_id, channel, before_id, before_id, limit)).fetchall()
            return list(reversed(rows))

    def start_turn(self, turn_id, character_id) -> None:
        with self.transaction() as connection:
            connection.execute("INSERT INTO conversation_turns(id,character_id) VALUES (?,?)", (turn_id, character_id))

    def finish_turn(self, turn_id, character_id, content, status, error="", channel="desktop", source="text") -> None:
        with self.transaction() as connection:
            has_attachment=connection.execute("SELECT 1 FROM expression_deliveries WHERE turn_id=? AND character_id=? AND status IN ('pending','sending','displayed','accepted') LIMIT 1",(turn_id,character_id)).fetchone()
            if content.strip() or has_attachment:
                row=connection.execute("SELECT id FROM chat_messages WHERE turn_id=? AND character_id=? AND role='assistant'",(turn_id,character_id)).fetchone()
                if row:
                    connection.execute("UPDATE chat_messages SET status=? WHERE id=?",(status,row['id']))
                else:
                    connection.execute("INSERT INTO chat_messages(role,content,character_id,turn_id,status,source,channel) VALUES ('assistant',?,?,?,?,?,?)", (content, character_id, turn_id, status, source, channel))
            connection.execute("UPDATE conversation_turns SET status=?,error=? WHERE id=? AND character_id=?", (status, error, turn_id, character_id))

    def set_delivered_reply(self,turn_id,character_id,content,status):
        """上下文只保存用户已看见或已听见的文本，生成全文不等于已送达。"""
        with self.transaction() as connection:
            row=connection.execute("SELECT id FROM chat_messages WHERE turn_id=? AND character_id=? AND role='assistant'", (turn_id,character_id)).fetchone()
            if row:
                connection.execute("UPDATE chat_messages SET content=?,status=? WHERE id=?", (content,status,row['id']))
            elif content:
                connection.execute("INSERT INTO chat_messages(role,content,character_id,turn_id,status) VALUES ('assistant',?,?,?,?)", (content,character_id,turn_id,status))

    def load_summary(self,character_id):
        with self.transaction() as connection:
            row=connection.execute('SELECT summary FROM conversation_summaries WHERE character_id=?',(character_id,)).fetchone()
            return row[0] if row else ''

    def save_summary(self,character_id,summary,through_id):
        with self.transaction() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if connection.execute('SELECT 1 FROM memory_tombstones WHERE character_id=?',(character_id,)).fetchone(): return
            connection.execute('INSERT INTO conversation_summaries(character_id,summary,through_id) VALUES (?,?,?) ON CONFLICT(character_id) DO UPDATE SET summary=excluded.summary,through_id=excluded.through_id,updated_at=CURRENT_TIMESTAMP',(character_id,summary[:3000],through_id))

    def upsert_memory(self, content, character_id="deepseek-whale", fact_key="", category="preference", importance=2, confidence=1, source_ids=(), scope="pet", guard=None) -> bool:
        """guard(connection, key) → bool：在同一个写事务里、写入之前做的检查（公开来源的记忆用它按此刻的 private 状态把关，
        和设置页的 set_memory_scope 互斥，没有"检查完、写之前"的空隙）。"""
        key = fact_key or hashlib.sha256(content.strip().encode()).hexdigest()
        with self.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM memory_tombstones WHERE character_id=? AND fact_key=?", (character_id, key)).fetchone():
                return False
            if guard is not None and not guard(connection, key):
                return False
            row = connection.execute("SELECT id,content FROM memories WHERE character_id=? AND fact_key=?", (character_id, key)).fetchone()
            values = (content[:1000], category, max(1,min(5,importance)), max(0,min(1,confidence)), json.dumps(list(source_ids)))
            if row:
                # 再次提到 = 重新记清楚
                connection.execute("UPDATE memories SET content=?,category=?,importance=?,confidence=?,source_ids=?,updated_at=CURRENT_TIMESTAMP,clarity=1,recalled_at=CURRENT_TIMESTAMP WHERE id=?", (*values, row['id']))
            else:
                connection.execute("INSERT INTO memories(content,category,importance,confidence,source_ids,character_id,fact_key,scope) VALUES (?,?,?,?,?,?,?,?)", (*values, character_id, key, scope))
            if not row or row['content'] != content[:1000]:
                connection.execute("INSERT INTO memory_archive(character_id,content,category,fact_key,importance,scope,source_ids,reason) VALUES (?,?,?,?,?,?,?,?)",
                                   (character_id, content[:1000], category, key, values[2], scope, values[4], 'updated' if row else 'created'))
            return True

    def set_memory_scope(self, ids, character_id, scope) -> None:
        """记忆页里"只在私下用 / 共享"的切换。"""
        with self.transaction() as connection:
            connection.executemany("UPDATE memories SET scope=? WHERE id=? AND character_id=?", [(scope, i, character_id) for i in ids])

    def recent_memories(self, limit=100, character_id="deepseek-whale"):
        with self.transaction() as connection:
            return connection.execute("SELECT * FROM memories WHERE character_id=? ORDER BY importance DESC,updated_at DESC,id DESC LIMIT ?", (character_id, limit)).fetchall()

    def delete_memory(self, memory_id, character_id) -> None:
        with self.transaction() as connection:
            row = connection.execute("SELECT fact_key FROM memories WHERE id=? AND character_id=?", (memory_id,character_id)).fetchone()
            if row:
                connection.execute("INSERT OR IGNORE INTO memory_tombstones(character_id,fact_key) VALUES (?,?)", (character_id,row['fact_key']))
                # 你主动删除 = 真的忘掉：全量存档里同一事实也一起删除
                connection.execute("DELETE FROM memory_archive WHERE character_id=? AND fact_key=?", (character_id,row['fact_key']))
                connection.execute("DELETE FROM memories WHERE id=? AND character_id=?", (memory_id,character_id))
                connection.execute("DELETE FROM conversation_summaries WHERE character_id=?", (character_id,))

    # ---- v2：记忆卡、存档、收件箱、目的 ----
    def memory_card(self, character_id):
        with self.transaction() as connection:
            return connection.execute("SELECT * FROM memories WHERE character_id=? ORDER BY id", (character_id,)).fetchall()

    def set_clarity(self, updates) -> None:
        with self.transaction() as connection:
            connection.executemany("UPDATE memories SET clarity=? WHERE id=?", [(max(0.0, min(1.0, c)), i) for i, c in updates])

    def touch_memories(self, ids, boost=0.25) -> None:
        """被想起（用在了对话里）：清晰度回升一些。"""
        with self.transaction() as connection:
            connection.executemany("UPDATE memories SET clarity=MIN(1,clarity+?),recalled_at=CURRENT_TIMESTAMP WHERE id=?", [(boost, i) for i in ids])

    def forget_memories(self, ids, reason='forgotten') -> None:
        """自然遗忘：从记忆卡移除（不是墓碑，以后再提到还能重新记住），存档里留一条记录。"""
        with self.transaction() as connection:
            for memory_id in ids:
                row = connection.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
                if not row: continue
                connection.execute("INSERT INTO memory_archive(character_id,content,category,fact_key,importance,scope,source_ids,reason) VALUES (?,?,?,?,?,?,?,?)",
                                   (row['character_id'], row['content'], row['category'], row['fact_key'], row['importance'], row['scope'], row['source_ids'], reason))
                connection.execute("DELETE FROM memories WHERE id=?", (memory_id,))

    def memory_archive(self, character_id, limit=500):
        with self.transaction() as connection:
            return connection.execute("SELECT * FROM memory_archive WHERE character_id=? ORDER BY id DESC LIMIT ?", (character_id, limit)).fetchall()

    def add_inbox(self, character_id, kind, content='', scope='pet') -> None:
        with self.transaction() as connection:
            connection.execute("INSERT INTO memory_inbox(character_id,kind,content,scope) VALUES (?,?,?,?)", (character_id, kind, content[:500], scope))

    def inbox(self, character_id, unprocessed=True, limit=500):
        with self.transaction() as connection:
            where = " AND processed=0" if unprocessed else ""
            return connection.execute(f"SELECT * FROM memory_inbox WHERE character_id=?{where} ORDER BY id LIMIT ?", (character_id, limit)).fetchall()

    def mark_inbox(self, ids) -> None:
        with self.transaction() as connection:
            connection.executemany("UPDATE memory_inbox SET processed=1 WHERE id=?", [(i,) for i in ids])

    def add_purpose(self, character_id, kind, content, opener, memory_ids, due_at) -> int:
        with self.transaction() as connection:
            return connection.execute("INSERT INTO purposes(character_id,kind,content,opener,memory_ids,due_at) VALUES (?,?,?,?,?,?)",
                                      (character_id, kind, content[:200], opener[:200], json.dumps(list(memory_ids)), due_at)).lastrowid

    def purposes(self, character_id, status=None, limit=100):
        with self.transaction() as connection:
            if status:
                return connection.execute("SELECT * FROM purposes WHERE character_id=? AND status=? ORDER BY due_at,id LIMIT ?", (character_id, status, limit)).fetchall()
            return connection.execute("SELECT * FROM purposes WHERE character_id=? ORDER BY id DESC LIMIT ?", (character_id, limit)).fetchall()

    def update_purpose(self, purpose_id, status, delivered_at=None) -> None:
        with self.transaction() as connection:
            connection.execute("UPDATE purposes SET status=?,delivered_at=COALESCE(?,delivered_at) WHERE id=?", (status, delivered_at, purpose_id))

    def purposes_delivered_since(self, character_id, since_iso) -> int:
        with self.transaction() as connection:
            return connection.execute("SELECT COUNT(*) FROM purposes WHERE character_id=? AND status='delivered' AND delivered_at>=?", (character_id, since_iso)).fetchone()[0]

    def has_deleted_memories(self, character_id) -> bool:
        with self.transaction() as connection:
            return bool(connection.execute("SELECT 1 FROM memory_tombstones WHERE character_id=? LIMIT 1", (character_id,)).fetchone())

    def load_emotion(self, character_id="deepseek-whale") -> EmotionState:
        with self.transaction() as connection:
            row = connection.execute("SELECT mood,affection,loneliness,energy FROM emotion_state WHERE character_id=?", (character_id,)).fetchone()
            return EmotionState(**dict(row)) if row else EmotionState()

    def save_emotion(self, emotion, character_id="deepseek-whale") -> None:
        with self.transaction() as connection:
            connection.execute("UPDATE emotion_state SET mood=?,affection=?,loneliness=?,energy=?,updated_at=CURRENT_TIMESTAMP WHERE character_id=?", (emotion.mood,emotion.affection,emotion.loneliness,emotion.energy,character_id))

    def get_setting(self, key, default=None):
        with self.transaction() as connection:
            row = connection.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_setting(self, key, value) -> None:
        with self.transaction() as connection:
            connection.execute("INSERT INTO app_settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key,json.dumps(value,ensure_ascii=False)))

    def claim_proactive(self, character_id, key) -> bool:
        with self.transaction() as connection:
            return connection.execute("INSERT OR IGNORE INTO proactive_events(character_id,event_key) VALUES (?,?)", (character_id,key)).rowcount == 1

    def release_proactive(self, character_id, key) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM proactive_events WHERE character_id=? AND event_key=?", (character_id,key))

    def record_usage(self, character_id, kind, amount) -> None:
        with self.transaction() as connection:
            connection.execute("INSERT INTO usage_events(character_id,kind,amount) VALUES (?,?,?)", (character_id,kind,int(amount)))

    # ---- P5：词元用量 ----------------------------------------------------------
    def add_token_usage(self, purpose, model, prompt_tokens, completion_tokens, cached_tokens=0, cost=0.0,
                        estimated=False, character_id='', ref=''):
        with self.transaction() as connection:
            connection.execute("INSERT INTO token_usage(character_id,purpose,model,prompt_tokens,completion_tokens,cached_tokens,cost,estimated,ref) "
                               "VALUES (?,?,?,?,?,?,?,?,?)", (character_id, purpose, model, int(prompt_tokens), int(completion_tokens),
                                                              int(cached_tokens), float(cost), int(bool(estimated)), ref))

    def token_usage_by_purpose(self, since):
        """since 为本地时间字符串，如 '2026-11-06 00:00:00'。"""
        with self.transaction() as connection:
            return connection.execute("SELECT purpose, SUM(prompt_tokens) AS prompt, SUM(completion_tokens) AS completion, "
                                      "SUM(cached_tokens) AS cached, SUM(cost) AS cost, COUNT(*) AS calls, SUM(estimated) AS estimated "
                                      "FROM token_usage WHERE created_at>=? GROUP BY purpose ORDER BY cost DESC", (since,)).fetchall()

    def token_usage_daily(self, days=7):
        with self.transaction() as connection:
            return connection.execute("SELECT substr(created_at,1,10) AS day, SUM(prompt_tokens+completion_tokens) AS tokens, SUM(cost) AS cost "
                                      "FROM token_usage WHERE created_at>=date('now','localtime',?) GROUP BY day ORDER BY day",
                                      (f'-{int(days)-1} days',)).fetchall()

    def token_usage_top(self, since, limit=5):
        with self.transaction() as connection:
            return connection.execute("SELECT * FROM token_usage WHERE created_at>=? ORDER BY cost DESC, prompt_tokens+completion_tokens DESC LIMIT ?",
                                      (since, limit)).fetchall()

    # ---- P5：agent 审计 --------------------------------------------------------
    def add_agent_run(self, character_id, goal):
        with self.transaction() as connection:
            return connection.execute("INSERT INTO agent_runs(character_id,goal) VALUES (?,?)", (character_id, goal)).lastrowid

    def finish_agent_run(self, run_id, status, result='', error='', steps=0, tokens=0, cost=0.0):
        with self.transaction() as connection:
            connection.execute("UPDATE agent_runs SET status=?,result=?,error=?,steps=?,tokens=?,cost=?,ended_at=datetime('now','localtime') WHERE id=?",
                               (status, result, error, steps, tokens, cost, run_id))

    def add_agent_step(self, run_id, seq, tool, args_json, risk, approval):
        with self.transaction() as connection:
            return connection.execute("INSERT INTO agent_steps(run_id,seq,tool,args_json,risk,approval) VALUES (?,?,?,?,?,?)",
                                      (run_id, seq, tool, args_json, risk, approval)).lastrowid

    def finish_agent_step(self, step_id, result_summary='', error='', undo_json=''):
        with self.transaction() as connection:
            connection.execute("UPDATE agent_steps SET result_summary=?,error=?,undo_json=?,ended_at=datetime('now','localtime') WHERE id=?",
                               (result_summary, error, undo_json, step_id))

    def agent_runs(self, limit=50):
        with self.transaction() as connection:
            return connection.execute("SELECT * FROM agent_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def agent_steps(self, run_id):
        with self.transaction() as connection:
            return connection.execute("SELECT * FROM agent_steps WHERE run_id=? ORDER BY seq", (run_id,)).fetchall()

    def usage_summary(self):
        with self.transaction() as connection:
            return connection.execute("SELECT kind,SUM(amount) AS amount FROM usage_events WHERE created_at>=datetime('now','start of month') GROUP BY kind").fetchall()

    def relationship(self,character_id):
        with self.transaction() as connection:
            return dict(connection.execute('SELECT * FROM relationship_state WHERE character_id=?',(character_id,)).fetchone())

    def record_relationship_event(self,character_id):
        with self.transaction() as connection:
            connection.execute("UPDATE relationship_state SET trust=MIN(100,trust+1),last_positive_day=date('now') WHERE character_id=? AND last_positive_day!=date('now')",(character_id,))

    def reminders(self,character_id):
        with self.transaction() as connection:
            return connection.execute('SELECT * FROM reminders WHERE character_id=? ORDER BY due_at,id',(character_id,)).fetchall()

    def save_reminder(self,character_id,title,due_at,reminder_id=None):
        with self.transaction() as connection:
            if reminder_id:
                connection.execute("UPDATE reminders SET title=?,due_at=?,status='pending' WHERE id=? AND character_id=?",(title,due_at,reminder_id,character_id))
            else:
                connection.execute('INSERT INTO reminders(character_id,title,due_at) VALUES (?,?,?)',(character_id,title,due_at))

    def delete_reminder(self,character_id,reminder_id):
        with self.transaction() as connection:
            connection.execute('DELETE FROM reminders WHERE character_id=? AND id=?',(character_id,reminder_id))

    def due_reminders(self,character_id):
        with self.transaction() as connection:
            connection.execute('BEGIN IMMEDIATE')
            rows=connection.execute("SELECT * FROM reminders WHERE character_id=? AND status='pending' AND due_at<=datetime('now')",(character_id,)).fetchall()
            for row in rows: connection.execute("UPDATE reminders SET status='done' WHERE id=?",(row['id'],))
            return rows
