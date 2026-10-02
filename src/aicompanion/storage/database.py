from __future__ import annotations

from contextlib import contextmanager, closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

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
            if version == 0 and "chat_messages" in tables:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                backup = self.database_path.with_suffix(f".backup-{stamp}.sqlite3")
                with closing(sqlite3.connect(backup)) as destination:
                    connection.backup(destination)
            connection.execute("BEGIN IMMEDIATE")
            try:
                if version == 0:
                    additions = {
                        "chat_messages": [("character_id", "TEXT NOT NULL DEFAULT 'emilia'"), ("turn_id", "TEXT"), ("status", "TEXT NOT NULL DEFAULT 'complete'"), ("source", "TEXT NOT NULL DEFAULT 'text'")],
                        "memories": [("character_id", "TEXT NOT NULL DEFAULT 'emilia'"), ("category", "TEXT NOT NULL DEFAULT 'legacy'"), ("fact_key", "TEXT NOT NULL DEFAULT ''"), ("confidence", "REAL NOT NULL DEFAULT 1"), ("source_ids", "TEXT NOT NULL DEFAULT '[]'")],
                        "reminders": [("character_id", "TEXT NOT NULL DEFAULT 'emilia'")],
                    }
                    for table, columns in additions.items():
                        if table in tables:
                            existing = {r[1] for r in connection.execute(f"PRAGMA table_info({table})")}
                            for name, definition in columns:
                                if name not in existing:
                                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
                    if "emotion_state" in tables:
                        connection.execute("ALTER TABLE emotion_state RENAME TO legacy_emotion")
                # executescript 隐式提交，固定 schema 逐条执行才保证迁移原子性。
                for statement in SCHEMA_SQL.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                self._insert_character(connection, DEFAULT_CHARACTER)
                if version == 0 and "emotion_state" in tables:
                    connection.execute("INSERT OR REPLACE INTO emotion_state(character_id,mood,affection,loneliness,energy,updated_at) SELECT 'emilia',mood,affection,loneliness,energy,updated_at FROM legacy_emotion WHERE id=1")
                    connection.execute("DROP TABLE legacy_emotion")
                for row in connection.execute("SELECT id, content FROM memories WHERE fact_key=''").fetchall():
                    key = hashlib.sha256(row['content'].strip().encode()).hexdigest()
                    connection.execute("UPDATE memories SET fact_key=? WHERE id=?", (key, row['id']))
                connection.execute("UPDATE conversation_turns SET status='interrupted' WHERE status='generating'")
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _insert_character(connection, character: CharacterProfile) -> None:
        connection.execute("INSERT OR IGNORE INTO characters VALUES (?,?,?,?,?,?)", (character.id, character.name, character.description, character.speaking_style, character.model_path, character.voice))
        connection.execute("INSERT OR IGNORE INTO emotion_state(character_id) VALUES (?)", (character.id,))

    def add_character(self, character: CharacterProfile) -> None:
        with self.transaction() as connection:
            self._insert_character(connection, character)

    def characters(self) -> list[CharacterProfile]:
        with self.transaction() as connection:
            return [CharacterProfile(**dict(r)) for r in connection.execute("SELECT * FROM characters ORDER BY rowid")]

    def update_character(self, character: CharacterProfile) -> None:
        with self.transaction() as connection:
            connection.execute("UPDATE characters SET name=?,description=?,speaking_style=?,model_path=?,voice=? WHERE id=?", (character.name, character.description, character.speaking_style, character.model_path, character.voice, character.id))

    def add_message(self, role: str, content: str, character_id="emilia", turn_id=None, status="complete", source="text") -> int:
        with self.transaction() as connection:
            cursor = connection.execute("INSERT INTO chat_messages(role,content,character_id,turn_id,status,source) VALUES (?,?,?,?,?,?)", (role, content, character_id, turn_id, status, source))
            return cursor.lastrowid

    def recent_messages(self, limit=30, character_id="emilia", before_id=None) -> list[sqlite3.Row]:
        with self.transaction() as connection:
            rows = connection.execute("SELECT * FROM chat_messages WHERE character_id=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?", (character_id, before_id, before_id, limit)).fetchall()
            return list(reversed(rows))

    def start_turn(self, turn_id, character_id) -> None:
        with self.transaction() as connection:
            connection.execute("INSERT INTO conversation_turns(id,character_id) VALUES (?,?)", (turn_id, character_id))

    def finish_turn(self, turn_id, character_id, content, status, error="") -> None:
        with self.transaction() as connection:
            if content.strip():
                connection.execute("INSERT INTO chat_messages(role,content,character_id,turn_id,status) VALUES ('assistant',?,?,?,?)", (content, character_id, turn_id, status))
            connection.execute("UPDATE conversation_turns SET status=?,error=? WHERE id=? AND character_id=?", (status, error, turn_id, character_id))

    def upsert_memory(self, content, character_id="emilia", fact_key="", category="preference", importance=2, confidence=1, source_ids=()) -> bool:
        key = fact_key or hashlib.sha256(content.strip().encode()).hexdigest()
        with self.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM memory_tombstones WHERE character_id=? AND fact_key=?", (character_id, key)).fetchone():
                return False
            row = connection.execute("SELECT id FROM memories WHERE character_id=? AND fact_key=?", (character_id, key)).fetchone()
            values = (content[:1000], category, max(1,min(5,importance)), max(0,min(1,confidence)), json.dumps(list(source_ids)))
            if row:
                connection.execute("UPDATE memories SET content=?,category=?,importance=?,confidence=?,source_ids=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (*values, row['id']))
            else:
                connection.execute("INSERT INTO memories(content,category,importance,confidence,source_ids,character_id,fact_key) VALUES (?,?,?,?,?,?,?)", (*values, character_id, key))
            return True

    def add_memory(self, content, importance=1, character_id="emilia") -> None:
        self.upsert_memory(content, character_id, importance=importance)

    def recent_memories(self, limit=100, character_id="emilia"):
        with self.transaction() as connection:
            return connection.execute("SELECT * FROM memories WHERE character_id=? ORDER BY importance DESC,updated_at DESC,id DESC LIMIT ?", (character_id, limit)).fetchall()

    def delete_memory(self, memory_id, character_id) -> None:
        with self.transaction() as connection:
            row = connection.execute("SELECT fact_key FROM memories WHERE id=? AND character_id=?", (memory_id,character_id)).fetchone()
            if row:
                connection.execute("INSERT OR IGNORE INTO memory_tombstones(character_id,fact_key) VALUES (?,?)", (character_id,row['fact_key']))
                connection.execute("DELETE FROM memories WHERE id=? AND character_id=?", (memory_id,character_id))
                connection.execute("DELETE FROM conversation_summaries WHERE character_id=?", (character_id,))

    def has_deleted_memories(self, character_id) -> bool:
        with self.transaction() as connection:
            return bool(connection.execute("SELECT 1 FROM memory_tombstones WHERE character_id=? LIMIT 1", (character_id,)).fetchone())

    def load_emotion(self, character_id="emilia") -> EmotionState:
        with self.transaction() as connection:
            row = connection.execute("SELECT mood,affection,loneliness,energy FROM emotion_state WHERE character_id=?", (character_id,)).fetchone()
            return EmotionState(**dict(row)) if row else EmotionState()

    def save_emotion(self, emotion, character_id="emilia") -> None:
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

    def usage_summary(self):
        with self.transaction() as connection:
            return connection.execute("SELECT kind,SUM(amount) AS amount FROM usage_events WHERE created_at>=datetime('now','start of month') GROUP BY kind").fetchall()
