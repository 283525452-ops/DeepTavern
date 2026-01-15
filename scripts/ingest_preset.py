# scripts/ingest_preset.py
import json
import sqlite3
import re
import os
import sys
import uuid

# 自动定位项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

from core.database.vector_store import VectorStore
from core.llm.api_client import APILLM

RULES_DB_PATH = os.path.join(BASE_DIR, "data", "rules_preset.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS rule_fragments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    raw_content TEXT,
    category TEXT,
    scope_type TEXT DEFAULT 'GLOBAL',
    scope_value TEXT,
    required_tags TEXT,
    summary TEXT,
    source_preset TEXT,
    is_active BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_rules_scope ON rule_fragments(scope_type, scope_value);
"""

PROMPT_DEEP_ANALYSIS = """
You are an expert Prompt Engineer and Data Architect.
Analyze the following raw preset fragment from "SillyTavern".

[Raw Text]
{raw_text}

[Tasks]
1. **Clean & Rewrite**: Remove all `{{...}}` variables, XML tags, and irrelevant glue text. Rewrite the core logic into a **clear, professional System Instruction** for an LLM.
2. **Categorize**: Choose ONE: STYLE, LOGIC, NSFW, SYSTEM, FORMAT, CONSTRAINT, OTHER.
3. **Tagging**: Extract 3-5 semantic tags.
4. **Scope**: When should this trigger? (e.g., GLOBAL, COMBAT, H_SCENE).
5. **Summary**: A one-sentence summary in Chinese.

[Output Format]
Strictly output valid JSON only:
{{
    "optimized_content": "Rewritten instruction...",
    "category": "STYLE",
    "tags": ["Tag1", "Tag2"],
    "scope": "GLOBAL",
    "summary": "中文摘要..."
}}
"""

class PresetIngester:
    def __init__(self, llm_config, log_callback=print):
        """
        :param llm_config: dict {"api_key": "...", "base_url": "...", "model": "..."}
        :param log_callback: function to handle log strings
        """
        self.log = log_callback
        self.conn = self._init_db()
        self.cursor = self.conn.cursor()
        self.vec_store = VectorStore(collection_name="rules_memory")
        
        # 初始化 LLM
        self.log(f"正在初始化清洗模型: {llm_config.get('model')}...")
        self.llm = APILLM(llm_config)

    def _init_db(self):
        db_dir = os.path.dirname(RULES_DB_PATH)
        if not os.path.exists(db_dir): os.makedirs(db_dir)
        conn = sqlite3.connect(RULES_DB_PATH, check_same_thread=False)
        # 注意：这里我们不再每次都 Drop Table，以免误删数据，改为由用户决定是否清空
        # 如果需要强制清空，可以在外部手动执行 SQL
        conn.cursor().executescript(SCHEMA_SQL)
        conn.commit()
        return conn

    def extract_raw_content(self, text):
        matches = re.findall(r"\{\{setvar::.*?::([\s\S]*?)\}\}", text)
        if matches:
            return "\n".join(matches).strip()
        return text.strip()

    def _parse_json_response(self, response):
        try:
            return json.loads(response)
        except:
            match = re.search(r"```json(.*?)```", response, re.DOTALL)
            if match:
                try: return json.loads(match.group(1).strip())
                except: pass
            match = re.search(r"\{.*\}", response, re.DOTALL)
            if match:
                try: return json.loads(match.group(0).strip())
                except: pass
        return None

    def process_item(self, name, raw_text, source_name):
        content_to_analyze = self.extract_raw_content(raw_text)
        
        if len(content_to_analyze) < 5 or "初始化变量" in name or "过渡" in name:
            self.log(f"⏭️ 跳过 (内容过短或无关): {name}")
            return

        self.log(f"💎 正在分析: {name} ...")
        
        try:
            prompt = PROMPT_DEEP_ANALYSIS.format(raw_text=content_to_analyze[:10000])
            response = self.llm.generate([{"role": "user", "content": prompt}])
            analysis = self._parse_json_response(response)
            
            if not analysis:
                self.log("❌ JSON 解析失败，使用原始内容降级处理。")
                analysis = {
                    "optimized_content": content_to_analyze,
                    "category": "OTHER",
                    "tags": [],
                    "scope": "GLOBAL",
                    "summary": name
                }
            else:
                self.log("✅ 分析完成。")

        except Exception as e:
            self.log(f"❌ API 请求错误: {e}")
            return

        # 入库
        opt_content = analysis.get("optimized_content", content_to_analyze)
        category = analysis.get("category", "OTHER")
        tags = analysis.get("tags", [])
        scope = analysis.get("scope", "GLOBAL")
        summary = analysis.get("summary", name)
        
        scope_value = name
        if "-" in name: scope_value = name.split("-")[-1].strip()
        
        is_active = 1 if category in ["SYSTEM", "CONSTRAINT"] else 0

        self.cursor.execute(
            """INSERT INTO rule_fragments 
               (content, raw_content, category, scope_type, scope_value, required_tags, summary, source_preset, is_active) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (opt_content, raw_text, category, scope, scope_value, json.dumps(tags, ensure_ascii=False), summary, source_name, is_active)
        )
        rule_id = self.cursor.lastrowid

        vec_id = f"rule_{rule_id}_{uuid.uuid4().hex[:6]}"
        meta = {
            "category": category,
            "tags": ",".join(tags),
            "scope": scope,
            "source": source_name,
            "summary": summary
        }
        vector_text = f"[{category}] {summary}\nTags: {', '.join(tags)}\n{opt_content}"
        
        self.vec_store.add_memory(vector_text, meta, vec_id)

    def ingest(self, json_path):
        self.log(f"📂 加载文件: {json_path}")
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            self.log(f"❌ JSON 读取错误: {e}")
            return

        prompts = data.get('prompts', [])
        self.log(f"🔍 发现 {len(prompts)} 条预设。开始清洗...")

        for p in prompts:
            if not p.get('enabled', False): continue
            name = p.get('name', 'Unknown')
            content = p.get('content', '')
            
            self.process_item(name, content, os.path.basename(json_path))
            
        self.conn.commit()
        self.log("🎉 清洗入库完成！")
