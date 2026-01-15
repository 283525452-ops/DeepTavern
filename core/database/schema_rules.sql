-- core/database/schema_rules.sql

DROP TABLE IF EXISTS rule_fragments;

CREATE TABLE IF NOT EXISTS rule_fragments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,           -- 深度清洗后的规则
    raw_content TEXT,                -- 原始内容备份
    category TEXT,                   -- 智能分类 (STYLE, LOGIC...)
    scope_type TEXT DEFAULT 'GLOBAL',-- 作用域类型
    scope_value TEXT,                -- 作用域值 (如 'LuXun')
    required_tags TEXT,              -- 智能标签 (JSON)
    summary TEXT,                    -- 智能摘要
    source_preset TEXT,              -- 来源文件
    is_active BOOLEAN DEFAULT 0,     -- 是否默认开启
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 建立索引加速查询
CREATE INDEX IF NOT EXISTS idx_rules_scope ON rule_fragments(scope_type, scope_value);
CREATE INDEX IF NOT EXISTS idx_rules_category ON rule_fragments(category);
