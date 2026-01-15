-- core/database/schema.sql

-- 1. 会话表
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE NOT NULL,
    character_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_state_json TEXT
);

-- 2. 消息表 (无损记录)
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_summarized BOOLEAN DEFAULT 0, -- 是否已被 Left Brain 处理
    meta_tags TEXT, -- 用于存储 "Flashback" 等标记
    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);

-- 3. 记忆节点表 (递归摘要核心)
CREATE TABLE IF NOT EXISTS memory_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER,
    summary_text TEXT,
    level TEXT DEFAULT 'MICRO',        -- 'MICRO' (5轮) | 'MACRO' (50轮)
    timeline_tag TEXT,                 -- 时间锚点 (Day 1, 14:00)
    is_merged BOOLEAN DEFAULT 0,       -- 是否已被合并进更高层级
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    vector_id TEXT,                    -- 关联向量库ID
    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);

-- 4. 关系图谱表
CREATE TABLE IF NOT EXISTS relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER,
    source_entity TEXT,
    target_entity TEXT,
    value INTEGER,
    tags TEXT,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);

-- 5. 史诗章节表
CREATE TABLE IF NOT EXISTS saga_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER,
    chapter_title TEXT,
    content TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);

-- 6. 世界设定表 (Lore)
CREATE TABLE IF NOT EXISTS lore_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER,
    keyword TEXT UNIQUE,
    content TEXT,
    source TEXT, -- 'AI_Generated' or 'Internet'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);

-- 7. 交互日志 (黑匣子 - 调试用)
CREATE TABLE IF NOT EXISTS interaction_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER,
    message_id INTEGER,
    full_prompt TEXT,
    rag_context TEXT,
    model_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);

-- 8. 世界状态快照 (回滚用)
CREATE TABLE IF NOT EXISTS world_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER,
    message_id INTEGER, -- 关联到具体的某条消息ID，用于回滚定位
    state_json TEXT,
    diff_summary TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);
