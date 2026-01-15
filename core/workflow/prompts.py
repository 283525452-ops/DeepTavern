# core/workflow/prompts.py
from config.settings import MODEL_CONFIG

def get_prompt(role_key, default=""):
    """
    安全获取 Prompt。
    优先从 config.json (MODEL_CONFIG) 中读取。
    如果配置中没有，则返回 default。
    """
    return MODEL_CONFIG.get(role_key, {}).get("prompt", default)

# --- 默认 Prompt 模板 (当 config.json 中缺失时使用) ---

DEFAULT_GRAPH_EXTRACTOR = """[System: Knowledge Graph Extractor]
Analyze the narrative and extract Entities and Relationships.

[Input Text]
{text}

[Instructions]
1. Identify key entities (Characters, Locations, Items, Factions).
2. Identify relationships between them (e.g., hates, loves, owns, located_in, member_of).
3. Output strictly in JSON format:
{{
  "triplets": [
    {{"source": "Alice", "relation": "owns", "target": "Rusty Sword", "desc": "Alice found it in the cave"}},
    {{"source": "Alice", "relation": "located_in", "target": "Dark Cave", "desc": ""}}
  ]
}}
4. If no significant relationship changes, return empty list.
"""

# --- 导出变量 ---

# 1. 意图识别 & 查询重写
PROMPT_REWRITER = get_prompt("reflex")
PROMPT_GATEKEEPER = get_prompt("gatekeeper", PROMPT_REWRITER)

# 2. 导演
PROMPT_DIRECTOR = get_prompt("director")

# 3. 主叙事
PROMPT_NARRATOR = get_prompt("narrator")

# 4. 状态引擎
PROMPT_STATUS_UPDATE = get_prompt("status")

# 5. 记忆清洗
PROMPT_CLEANER = get_prompt("cleaner")

# 6. 情感分析
PROMPT_EMOTION = get_prompt("empath")

# 7. 舞台监督
PROMPT_STAGE = get_prompt("stage")

# 8. 世界模拟器 (原 gossip)
PROMPT_WORLD_SIM = get_prompt("world_sim")

# 9. 知识图谱提取 (GraphRAG)
# 尝试从配置中读取 key="graph_extractor" 的 prompt，如果没有则使用默认值
PROMPT_GRAPH_EXTRACTOR = get_prompt("graph_extractor", DEFAULT_GRAPH_EXTRACTOR)
