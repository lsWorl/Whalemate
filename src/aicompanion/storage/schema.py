SCHEMA_VERSION = 9

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
    character_id TEXT NOT NULL DEFAULT 'deepseek-whale' REFERENCES characters(id),
    turn_id TEXT, status TEXT NOT NULL DEFAULT 'complete', source TEXT NOT NULL DEFAULT 'text',
    channel TEXT NOT NULL DEFAULT 'desktop', sender TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL,
    importance INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    character_id TEXT NOT NULL DEFAULT 'deepseek-whale' REFERENCES characters(id),
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
    character_id TEXT NOT NULL DEFAULT 'deepseek-whale' REFERENCES characters(id)
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

CREATE TABLE IF NOT EXISTS expression_assets (
 asset_id TEXT PRIMARY KEY, revision TEXT NOT NULL, root TEXT NOT NULL, relative_path TEXT NOT NULL,
 sha256 TEXT NOT NULL, metadata TEXT NOT NULL, thumbnail BLOB, review_status TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 0, imported_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS expression_asset_revisions (
 asset_id TEXT NOT NULL REFERENCES expression_assets(asset_id), revision TEXT NOT NULL,
 root TEXT NOT NULL, relative_path TEXT NOT NULL, sha256 TEXT NOT NULL,
 PRIMARY KEY(asset_id,revision)
);
CREATE TABLE IF NOT EXISTS expression_deliveries (
 delivery_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL, character_id TEXT NOT NULL,
 scope TEXT NOT NULL, channel TEXT NOT NULL, asset_id TEXT NOT NULL REFERENCES expression_assets(asset_id),
 revision TEXT NOT NULL, placement TEXT NOT NULL, alt_text TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_expression_scope ON expression_deliveries(character_id,scope,created_at);
CREATE INDEX IF NOT EXISTS ix_expression_turn ON expression_deliveries(turn_id,character_id);

-- v7（T15 人物关系）：人物 / 平台身份 / 角色对人物的关系 / 关系事件。原 relationship_state（主人信任度）保留不动。
-- 平台身份 = platform + bot_account_id + platform_user_id；群号只是场景，不进身份；昵称只作展示。
CREATE TABLE IF NOT EXISTS people (
 id INTEGER PRIMARY KEY AUTOINCREMENT, display_name TEXT NOT NULL DEFAULT '', nicknames TEXT NOT NULL DEFAULT '[]',
 created_at REAL NOT NULL, last_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS platform_identities (
 platform TEXT NOT NULL, bot_account_id TEXT NOT NULL, platform_user_id TEXT NOT NULL,
 person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE, link_state TEXT NOT NULL DEFAULT 'observed',
 created_at REAL NOT NULL, prior_person_id INTEGER, PRIMARY KEY(platform, bot_account_id, platform_user_id)
);
CREATE TABLE IF NOT EXISTS relationship_profiles (
 character_id TEXT NOT NULL, person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
 affinity REAL NOT NULL, familiarity REAL NOT NULL, trust REAL NOT NULL, is_owner INTEGER NOT NULL DEFAULT 0,
 interactions INTEGER NOT NULL DEFAULT 0, first_met REAL NOT NULL, last_seen REAL NOT NULL, last_familiar_day TEXT NOT NULL DEFAULT '',
 manual TEXT NOT NULL DEFAULT '{}',
 source TEXT NOT NULL DEFAULT 'auto', PRIMARY KEY(character_id, person_id)
);
CREATE TABLE IF NOT EXISTS relationship_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, character_id TEXT NOT NULL, person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
 kind TEXT NOT NULL, confidence REAL NOT NULL, scope TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '',
 d_affinity REAL NOT NULL DEFAULT 0, d_familiarity REAL NOT NULL DEFAULT 0, d_trust REAL NOT NULL DEFAULT 0,
 applied INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '', dedupe_key TEXT NOT NULL, created_at REAL NOT NULL, day TEXT NOT NULL,
 UNIQUE(character_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS ix_relationship_events_person ON relationship_events(character_id, person_id, created_at);
CREATE TABLE IF NOT EXISTS person_notes (
 id INTEGER PRIMARY KEY AUTOINCREMENT, character_id TEXT NOT NULL, person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
 category TEXT NOT NULL, text TEXT NOT NULL, weight REAL NOT NULL, mentions INTEGER NOT NULL DEFAULT 1, origin TEXT NOT NULL DEFAULT '',
 first_at REAL NOT NULL, last_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_person_notes_person ON person_notes(character_id, person_id);
-- T18 社区知识：词 / 梗、词义（Graphiti 式双时间线：valid_from / valid_to 是词义有效期，learned_at / expired_at 是她知道 / 不再采用的时间）
CREATE TABLE IF NOT EXISTS lexicon_terms (
 id INTEGER PRIMARY KEY AUTOINCREMENT, term TEXT NOT NULL UNIQUE, circle TEXT NOT NULL DEFAULT '', is_jargon INTEGER NOT NULL DEFAULT 1,
 inferred_at_count INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS lexicon_senses (
 id INTEGER PRIMARY KEY AUTOINCREMENT, term_id INTEGER NOT NULL REFERENCES lexicon_terms(id) ON DELETE CASCADE,
 meaning TEXT NOT NULL, usage TEXT NOT NULL DEFAULT '', tone TEXT NOT NULL DEFAULT '', scope TEXT NOT NULL DEFAULT '',
 origin TEXT NOT NULL, source_name TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL DEFAULT 0.5,
 valid_from REAL NOT NULL, valid_to REAL, learned_at REAL NOT NULL, expired_at REAL, superseded_by INTEGER, reinforced INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_lexicon_senses_term ON lexicon_senses(term_id, valid_to);
CREATE TABLE IF NOT EXISTS lexicon_sightings (
 term_id INTEGER NOT NULL REFERENCES lexicon_terms(id) ON DELETE CASCADE, channel TEXT NOT NULL, speakers TEXT NOT NULL DEFAULT '[]',
 count INTEGER NOT NULL DEFAULT 0, contexts TEXT NOT NULL DEFAULT '[]', first_at REAL NOT NULL, last_at REAL NOT NULL,
 PRIMARY KEY(term_id, channel)
);
-- T18 群里的共同经历 / 内部梗 / 叫法（跟群走）
CREATE TABLE IF NOT EXISTS group_lore (
 id INTEGER PRIMARY KEY AUTOINCREMENT, character_id TEXT NOT NULL, channel TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
 weight REAL NOT NULL, mentions INTEGER NOT NULL DEFAULT 1, first_at REAL NOT NULL, last_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_group_lore_channel ON group_lore(character_id, channel);
-- v9 计划书 v4 功能二 人格包：同一个角色可以有多个人格，切换不丢记忆；每次保存留一个版本可回退
CREATE TABLE IF NOT EXISTS personas (
 id TEXT PRIMARY KEY, character_id TEXT NOT NULL, title TEXT NOT NULL, data TEXT NOT NULL,
 builtin INTEGER NOT NULL DEFAULT 0, pack_version INTEGER NOT NULL DEFAULT 0, user_modified INTEGER NOT NULL DEFAULT 0,
 created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_personas_character ON personas(character_id);
CREATE TABLE IF NOT EXISTS persona_versions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, persona_id TEXT NOT NULL, data TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', saved_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_persona_versions ON persona_versions(persona_id, id);
"""
