# core/utils/config_loader.py
import json
import os
from core.utils.logger import logger

CONFIG_PATH = "config.json"

class ConfigLoader:
    _instance = None
    _config = {}
    
    # 仪表盘所需的缓存数据
    _models = {}
    _prompts = {}
    _global = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigLoader, cls).__new__(cls)
            cls._instance.load()
        return cls._instance

    @classmethod
    def load(cls):
        if not os.path.exists(CONFIG_PATH):
            logger.error("找不到 config.json！请先运行 config_editor.py 生成配置。")
            cls._config = {}
            return
            
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                cls._config = json.load(f)
            logger.info("配置已加载")
            cls._parse_for_dashboard()
        except Exception as e:
            logger.error(f"配置文件解析失败: {e}")
            cls._config = {}

    @classmethod
    def _parse_for_dashboard(cls):
        """将原始 config.json 结构解析为 Dashboard 易读的格式"""
        cls._models = {}
        cls._prompts = {}
        cls._global = {}

        # 1. 解析 Global (Vector)
        vec = cls._config.get("vector", {})
        provs = cls._config.get("providers", {})
        vec_prov_key = vec.get("provider", "silicon")
        vec_prov_data = provs.get(vec_prov_key, {})
        
        cls._global = {
            "embedding_model": vec.get("embedding_model", ""),
            "rerank_model": vec.get("rerank_model", ""),
            "vector_api_key": vec_prov_data.get("api_key", ""),
            "vector_base_url": vec_prov_data.get("base_url", "")
        }

        # 2. 解析 Models & Prompts
        roles = cls._config.get("roles", [])
        for role in roles:
            key = role.get("key")
            if not key: continue
            
            prov_key = role.get("provider", "silicon")
            prov_data = provs.get(prov_key, {})
            
            # Model Data
            cls._models[key] = {
                "name": role.get("name", key),
                "provider": prov_key,
                "model": role.get("model", ""),
                "api_key": prov_data.get("api_key", ""),
                "base_url": prov_data.get("base_url", ""),
                "temperature": role.get("temperature", 0.7)
            }
            
            # Prompt Data
            cls._prompts[key] = {
                "description": f"{role.get('name', key)} 的系统提示词",
                "content": role.get("prompt", "")
            }

    @classmethod
    def load_configs(cls):
        """Dashboard 调用的别名"""
        cls.load()

    @classmethod
    def save_models(cls, models_data):
        """从 Dashboard 保存模型配置回 config.json"""
        cls._models = models_data
        cls._sync_to_config()

    @classmethod
    def save_prompts(cls, prompts_data):
        """从 Dashboard 保存提示词回 config.json"""
        cls._prompts = prompts_data
        cls._sync_to_config()

    @classmethod
    def save_global(cls, global_data):
        """从 Dashboard 保存全局配置回 config.json"""
        cls._global = global_data
        # 更新 vector 部分
        if "vector" not in cls._config: cls._config["vector"] = {}
        cls._config["vector"]["embedding_model"] = global_data.get("embedding_model")
        cls._config["vector"]["rerank_model"] = global_data.get("rerank_model")
        # 注意：这里简化处理，不反向更新 provider 的 key，因为 provider 结构比较复杂
        cls._save_file()

    @classmethod
    def _sync_to_config(cls):
        """将 _models 和 _prompts 同步回 cls._config['roles']"""
        new_roles = []
        
        # 遍历现有的 models
        for key, m_data in cls._models.items():
            p_data = cls._prompts.get(key, {})
            
            role_entry = {
                "key": key,
                "name": m_data.get("name"),
                "provider": m_data.get("provider"),
                "model": m_data.get("model"),
                "temperature": m_data.get("temperature"),
                "max_tokens": 8192, # 默认值
                "prompt": p_data.get("content", "")
            }
            new_roles.append(role_entry)
            
            # 同时尝试更新 providers (如果 API Key 变了)
            prov_key = m_data.get("provider")
            if prov_key and "providers" in cls._config:
                if prov_key not in cls._config["providers"]:
                    cls._config["providers"][prov_key] = {}
                
                # 仅当 dashboard 提供了非空值时更新
                if m_data.get("api_key"):
                    cls._config["providers"][prov_key]["api_key"] = m_data.get("api_key")
                if m_data.get("base_url"):
                    cls._config["providers"][prov_key]["base_url"] = m_data.get("base_url")

        cls._config["roles"] = new_roles
        cls._save_file()

    @classmethod
    def _save_file(cls):
        try:
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(cls._config, f, indent=4, ensure_ascii=False)
            logger.info("配置已保存至 config.json")
        except Exception as e:
            logger.error(f"保存配置失败: {e}")

    def get_provider_config(self, provider_key):
        return self._config.get("providers", {}).get(provider_key, {})

    def get_vector_config(self):
        return self._config.get("vector", {})

    def get_role_config(self, role_key):
        """根据 key (如 'narrator') 获取完整的模型配置和 prompt"""
        roles = self._config.get("roles", [])
        for role in roles:
            if role["key"] == role_key:
                # 组合数据：把 provider 的 url/key 拼进去
                provider_key = role.get("provider", "silicon")
                provider_info = self.get_provider_config(provider_key)
                
                return {
                    "model": role["model"],
                    "temperature": role.get("temperature", 0.7),
                    "prompt": role.get("prompt", ""),
                    "api_key": provider_info.get("api_key"),
                    "base_url": provider_info.get("base_url")
                }
        logger.warning(f"未找到角色配置: {role_key}")
        return {}
