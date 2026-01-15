# config/settings.py
import json
import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_JSON_PATH = os.path.join(BASE_DIR, "config.json")

# --- 默认值 ---
DEFAULT_HISTORY_LIMIT = 20
SYSTEM_MAX_HISTORY_CHARS = 30000
LOCAL_MODEL_PATH = "" 
MODEL_CONFIG = {}

# API 占位符
EMBEDDING_MODEL = "BAAI/bge-m3"
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
VECTOR_API_KEY = ""
VECTOR_BASE_URL = ""

# --- Redis 配置 (热数据库) ---
# 如果没有安装 Redis，请将 USE_REDIS 设为 False，代码会自动降级为纯 SQLite 模式
USE_REDIS = True
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DB = 0
REDIS_PASSWORD = None
REDIS_TTL = 3600  # 缓存过期时间 (秒)

def clean_prompt_content(text):
    if not text: return ""
    match = re.search(r'"""(.*?)"""', text, re.DOTALL)
    if match: return match.group(1).strip()
    return text.strip()

if os.path.exists(CONFIG_JSON_PATH):
    try:
        with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            
            providers = data.get("providers", {})
            vec_conf = data.get("vector", {})
            vec_provider_key = vec_conf.get("provider", "silicon")
            vec_provider = providers.get(vec_provider_key, {})
            
            EMBEDDING_MODEL = vec_conf.get("embedding_model", EMBEDDING_MODEL)
            RERANK_MODEL = vec_conf.get("rerank_model", RERANK_MODEL)
            VECTOR_API_KEY = vec_provider.get("api_key", "")
            VECTOR_BASE_URL = vec_provider.get("base_url", "")

            # 注入全局变量
            for key, value in data.items():
                if key.isupper():
                    globals()[key] = value

            raw_roles = data.get("roles", [])
            for role in raw_roles:
                key = role.get("key")
                provider_key = role.get("provider")
                provider_info = providers.get(provider_key, {})
                raw_prompt = role.get("prompt", "")
                
                MODEL_CONFIG[key] = {
                    "model": role.get("model"),
                    "api_key": provider_info.get("api_key"),
                    "base_url": provider_info.get("base_url"),
                    "temperature": role.get("temperature", 0.7),
                    "max_tokens": role.get("max_tokens", 8192),
                    "prompt": clean_prompt_content(raw_prompt),
                    "n_ctx": role.get("n_ctx", 32768),       # 默认给大点
                    "n_gpu_layers": role.get("n_gpu_layers", -1) # 默认全GPU
                }

    except Exception as e:
        print(f"[Config] 加载配置文件失败: {e}")
else:
    print(f"[Config] 警告: 找不到 {CONFIG_JSON_PATH}")
