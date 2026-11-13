SCHEMA_VERSION = 19  # v10：聊天记录全文索引（storage/fts.py 建，不在下面的 SQL 里）；v11：持续情绪；v12：陪伴日期；v13：主动开口记录；v14：她自己的活动；v15：用户日期提醒时机；v16：约定；v17：办事附件清单；v18：主人 QQ 交办与审批规则；v19：向量索引与阶段摘要

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
CREATE TABLE IF NOT EXISTS memory_vectors (
    character_id TEXT NOT NULL REFERENCES characters(id), fact_key TEXT NOT NULL,
    provider TEXT NOT NULL, endpoint TEXT NOT NULL, model TEXT NOT NULL, dimension INTEGER NOT NULL,
    content_hash TEXT NOT NULL, vector BLOB NOT NULL, PRIMARY KEY(character_id, fact_key)
);
CREATE TABLE IF NOT EXISTS stage_summaries (
    id INTEGER PRIMARY KEY, character_id TEXT NOT NULL REFERENCES characters(id), channel TEXT NOT NULL,
    first_id INTEGER NOT NULL, last_id INTEGER NOT NULL, source_ids TEXT NOT NULL, summary TEXT NOT NULL,
    UNIQUE(character_id,channel,first_id)
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
CREATE TABLE IF NOT EXISTS agent_rules (
    project TEXT NOT NULL, category TEXT NOT NULL, pattern TEXT NOT NULL, effect TEXT NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(project, category, pattern, effect)
);
CREATE TABLE IF NOT EXISTS qq_task_turns (
    bot TEXT NOT NULL, owner TEXT NOT NULL, message TEXT NOT NULL, PRIMARY KEY(bot,owner,message)
);
CREATE TABLE IF NOT EXISTS agent_qq_runs (
    run_id INTEGER PRIMARY KEY REFERENCES agent_runs(id), bot TEXT NOT NULL, owner TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_attachments (
    run_id INTEGER NOT NULL REFERENCES agent_runs(id), original TEXT NOT NULL, copy TEXT NOT NULL UNIQUE,
    size INTEGER NOT NULL, sha256 TEXT NOT NULL DEFAULT '', caption TEXT NOT NULL DEFAULT '',
    owned INTEGER NOT NULL DEFAULT 0, vision_started INTEGER NOT NULL DEFAULT 0, recycled TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(run_id, copy)
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
-- v11 计划书 v4 功能三 持续情绪：各情绪强度（按半衰期回落）和最近的情绪事件（界面里看“为什么”）
CREATE TABLE IF NOT EXISTS mood_state (character_id TEXT PRIMARY KEY, data TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS mood_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, character_id TEXT NOT NULL, kind TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
 scale REAL NOT NULL DEFAULT 1, channel TEXT NOT NULL DEFAULT 'desktop', at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_mood_events ON mood_events(character_id, id);
-- v12 计划书 v4 功能九 陪伴日期：用户添加 / 修改的日子（认识那天、生日、自定义）；程序算出来的纪念日不落表，现算
CREATE TABLE IF NOT EXISTS companion_dates (
 id INTEGER PRIMARY KEY AUTOINCREMENT, character_id TEXT NOT NULL, person_id INTEGER, kind TEXT NOT NULL, title TEXT NOT NULL,
 calendar TEXT NOT NULL DEFAULT 'solar', year INTEGER, month INTEGER NOT NULL, day INTEGER NOT NULL, yearly INTEGER NOT NULL DEFAULT 1,
 reminder TEXT NOT NULL DEFAULT 'day', source TEXT NOT NULL DEFAULT 'user', scope TEXT NOT NULL DEFAULT 'private', enabled INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_companion_dates ON companion_dates(character_id, kind);
-- 哪个日子哪天已经提起过（同一个日子一天只提一次；她退出再启动也不会再庆祝一遍）
CREATE TABLE IF NOT EXISTS companion_date_mentions (
 character_id TEXT NOT NULL, key TEXT NOT NULL, day TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY(character_id, key, day)
);
-- v13 计划书 v4 统一主动调度：她每次主动开口记一条（问候、心事、纪念日、追问……都算），
-- 用来管“今天说了几次”“上一句他回了没有”“追问过几次”，几种主动消息不再各管各的、同时冒出来
CREATE TABLE IF NOT EXISTS outreach_log (
 id INTEGER PRIMARY KEY AUTOINCREMENT, character_id TEXT NOT NULL, source TEXT NOT NULL, key TEXT NOT NULL,
 channel TEXT NOT NULL DEFAULT 'desktop', status TEXT NOT NULL DEFAULT 'sent', parent_id INTEGER,
 sent_at REAL NOT NULL, settled_at REAL, turn_id TEXT, handed_at REAL, share_delivery TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_outreach_parent ON outreach_log(parent_id);
-- 主动发到 QQ 私聊的每一条投递（bridge/outbox.py）：排队 → 交出 → 插件领取 → 发出 / 部分 / 失败 / 未知。
-- 存进库里是为了重启以后还能对上账：迟到的回执、没被取走的回复、这一轮带的纪念日（date_keys、date_day）
CREATE TABLE IF NOT EXISTS qq_outbox (
 turn_id TEXT PRIMARY KEY, character_id TEXT NOT NULL, status TEXT NOT NULL, date_keys TEXT NOT NULL DEFAULT '[]',
 date_day TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, dequeued_at REAL, claimed_at REAL, first_sent_at REAL, settled_at REAL,
 guard TEXT NOT NULL DEFAULT 'scheduled', target TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_outreach_log ON outreach_log(character_id, sent_at);
-- v16 聊天约定及其原始人物、会话和投递版本
CREATE TABLE IF NOT EXISTS promises (
 id INTEGER PRIMARY KEY AUTOINCREMENT, character_id TEXT NOT NULL REFERENCES characters(id),
 person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE, scope TEXT NOT NULL, source TEXT NOT NULL, turn_id TEXT NOT NULL,
 title TEXT NOT NULL, due_at REAL, condition TEXT NOT NULL DEFAULT '', expires_at REAL,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','done','cancelled','expired')),
 revision INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, UNIQUE(character_id,turn_id)
);
CREATE INDEX IF NOT EXISTS ix_promises_due ON promises(character_id,status,due_at);
CREATE UNIQUE INDEX IF NOT EXISTS ix_promise_delivery ON outreach_log(character_id,key) WHERE source='promise';
-- v14 她自己看过的活动和来源
CREATE TABLE IF NOT EXISTS activities (
 id INTEGER PRIMARY KEY AUTOINCREMENT, character_id TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'read', interest TEXT NOT NULL,
 query TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'doing', title TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '',
 site TEXT NOT NULL DEFAULT '', excerpt TEXT NOT NULL DEFAULT '', thought TEXT NOT NULL DEFAULT '', opener TEXT NOT NULL DEFAULT '',
 worth REAL NOT NULL DEFAULT 0, started_at REAL NOT NULL, finished_at REAL, shared_at REAL, response TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_activities ON activities(character_id, status, finished_at);
"""

