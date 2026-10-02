SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS characters (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
    speaking_style TEXT NOT NULL, model_path TEXT NOT NULL DEFAULT '', voice TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS conversation_turns (
    id TEXT PRIMARY KEY, character_id TEXT NOT NULL REFERENCES characters(id),
    status TEXT NOT NULL DEFAULT 'generating', error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    character_id TEXT NOT NULL DEFAULT 'emilia' REFERENCES characters(id),
    turn_id TEXT, status TEXT NOT NULL DEFAULT 'complete', source TEXT NOT NULL DEFAULT 'text'
);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL,
    importance INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    character_id TEXT NOT NULL DEFAULT 'emilia' REFERENCES characters(id),
    category TEXT NOT NULL DEFAULT 'legacy', fact_key TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 1, source_ids TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS memory_tombstones (
    character_id TEXT NOT NULL REFERENCES characters(id), fact_key TEXT NOT NULL,
    deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(character_id, fact_key)
);
CREATE TABLE IF NOT EXISTS conversation_summaries (
    character_id TEXT PRIMARY KEY REFERENCES characters(id), summary TEXT NOT NULL,
    through_id INTEGER NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS emotion_state (
    character_id TEXT PRIMARY KEY REFERENCES characters(id), mood TEXT NOT NULL DEFAULT 'calm',
    affection INTEGER NOT NULL DEFAULT 50, loneliness INTEGER NOT NULL DEFAULT 15,
    energy INTEGER NOT NULL DEFAULT 80, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS proactive_events (
    character_id TEXT NOT NULL REFERENCES characters(id), event_key TEXT NOT NULL,
    sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(character_id, event_key)
);
CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY, character_id TEXT NOT NULL, kind TEXT NOT NULL,
    amount INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, due_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    character_id TEXT NOT NULL DEFAULT 'emilia' REFERENCES characters(id)
);
CREATE INDEX IF NOT EXISTS ix_messages_character ON chat_messages(character_id, id);
CREATE INDEX IF NOT EXISTS ix_memories_character ON memories(character_id, importance);
"""
