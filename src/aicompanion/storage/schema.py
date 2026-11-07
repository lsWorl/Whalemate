SCHEMA_VERSION = 4

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
    turn_id TEXT, status TEXT NOT NULL DEFAULT 'complete', source TEXT NOT NULL DEFAULT 'text',
    channel TEXT NOT NULL DEFAULT 'desktop', sender TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL,
    importance INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    character_id TEXT NOT NULL DEFAULT 'emilia' REFERENCES characters(id),
    category TEXT NOT NULL DEFAULT 'legacy', fact_key TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 1, source_ids TEXT NOT NULL DEFAULT '[]',
    clarity REAL NOT NULL DEFAULT 1, recalled_at TEXT, scope TEXT NOT NULL DEFAULT 'pet'
);
CREATE TABLE IF NOT EXISTS memory_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT, character_id TEXT NOT NULL REFERENCES characters(id),
    content TEXT NOT NULL, category TEXT NOT NULL, fact_key TEXT NOT NULL, importance INTEGER NOT NULL DEFAULT 1,
    scope TEXT NOT NULL DEFAULT 'pet', source_ids TEXT NOT NULL DEFAULT '[]', reason TEXT NOT NULL DEFAULT 'created',
    archived_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS memory_inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT, character_id TEXT NOT NULL REFERENCES characters(id),
    kind TEXT NOT NULL, content TEXT NOT NULL DEFAULT '', scope TEXT NOT NULL DEFAULT 'pet',
    processed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS purposes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, character_id TEXT NOT NULL REFERENCES characters(id),
    kind TEXT NOT NULL, content TEXT NOT NULL, opener TEXT NOT NULL DEFAULT '', memory_ids TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending', due_at TEXT NOT NULL, delivered_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
CREATE TABLE IF NOT EXISTS relationship_state (
    character_id TEXT PRIMARY KEY REFERENCES characters(id),trust INTEGER NOT NULL DEFAULT 50,
    last_positive_day TEXT NOT NULL DEFAULT ''
);
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
CREATE INDEX IF NOT EXISTS ix_messages_channel ON chat_messages(character_id, channel, id);
CREATE INDEX IF NOT EXISTS ix_memories_character ON memories(character_id, importance);
CREATE INDEX IF NOT EXISTS ix_archive_character ON memory_archive(character_id, fact_key);
CREATE INDEX IF NOT EXISTS ix_inbox_character ON memory_inbox(character_id, processed, id);
CREATE INDEX IF NOT EXISTS ix_purposes_character ON purposes(character_id, status, due_at);
CREATE TABLE IF NOT EXISTS token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT, character_id TEXT NOT NULL DEFAULT '', purpose TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '', prompt_tokens INTEGER NOT NULL DEFAULT 0, completion_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0, cost REAL NOT NULL DEFAULT 0, estimated INTEGER NOT NULL DEFAULT 0,
    ref TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_token_usage_time ON token_usage(created_at, purpose);
CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, character_id TEXT NOT NULL, goal TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running', started_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    ended_at TEXT, steps INTEGER NOT NULL DEFAULT 0, tokens INTEGER NOT NULL DEFAULT 0, cost REAL NOT NULL DEFAULT 0,
    result TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS agent_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL REFERENCES agent_runs(id), seq INTEGER NOT NULL,
    tool TEXT NOT NULL, args_json TEXT NOT NULL, risk TEXT NOT NULL, approval TEXT NOT NULL,
    result_summary TEXT NOT NULL DEFAULT '', undo_json TEXT NOT NULL DEFAULT '', undone INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL DEFAULT (datetime('now','localtime')), ended_at TEXT, error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_agent_steps_run ON agent_steps(run_id, seq);
"""
