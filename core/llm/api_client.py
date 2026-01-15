# core/llm/api_client.py
import requests
import json
import time
from typing import List, Dict, Generator
from core.llm.base import BaseLLM
from core.utils.logger import logger
from config.settings import CONFIG_JSON_PATH

class APILLM(BaseLLM):
    """通用的 API LLM 客户端，支持自动备选 (Fallback) 机制"""
    
    def __init__(self, role_config: dict):
        self.role_config = role_config # 保存完整配置以便读取 fallback 信息
        self.model_name = role_config["model"]
        self.api_key = role_config["api_key"]
        self.base_url = role_config["base_url"]
        self.default_temp = role_config.get("temperature", 0.7)
        self.max_tokens = role_config.get("max_tokens", 8192)
        
        # 加载全局配置，以便查找 fallback provider 的具体 URL/Key
        self.global_providers = {}
        try:
            with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.global_providers = data.get("providers", {})
        except:
            pass

    def _get_endpoint(self, base_url):
        base = base_url.rstrip('/')
        return f"{base}/chat/completions"

    def _try_request(self, model, api_key, base_url, messages, temperature, stream=False):
        """执行单次请求逻辑"""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self.max_tokens,
            "stream": stream
        }
        
        endpoint = self._get_endpoint(base_url)
        
        if stream:
            return requests.post(endpoint, json=payload, headers=headers, stream=True, timeout=300)
        else:
            return requests.post(endpoint, json=payload, headers=headers, timeout=300)

    def generate(self, messages: List[Dict[str, str]], temperature: float = None) -> str:
        """同步生成 (带 Fallback 机制)"""
        temp = temperature if temperature is not None else self.default_temp
        
        # 1. 尝试主模型
        result = self._generate_with_retry(
            self.model_name, self.api_key, self.base_url, messages, temp, "Primary"
        )
        if result: return result

        # 2. 尝试备选模型 (Fallback)
        fallback_provider_key = self.role_config.get("fallback_provider")
        fallback_model = self.role_config.get("fallback_model")
        
        if fallback_provider_key and fallback_model and fallback_provider_key in self.global_providers:
            fb_config = self.global_providers[fallback_provider_key]
            logger.warning(f"[{self.model_name}] 主线路失败，切换备选线路: {fb_config.get('name')} ({fallback_model})")
            
            result = self._generate_with_retry(
                fallback_model, fb_config["api_key"], fb_config["base_url"], messages, temp, "Fallback"
            )
            if result: return result

        return "Error: All providers failed."

    def _generate_with_retry(self, model, key, url, messages, temp, tag):
        """内部重试逻辑"""
        max_retries = 2
        for attempt in range(max_retries):
            try:
                response = self._try_request(model, key, url, messages, temp, stream=False)
                if response.status_code == 200:
                    return response.json()['choices'][0]['message']['content']
                elif response.status_code in [500, 502, 503, 504, 429]:
                    logger.warning(f"[{tag}:{model}] API 繁忙 ({response.status_code})，重试 {attempt+1}/{max_retries}...")
                    time.sleep(2)
                else:
                    logger.error(f"[{tag}:{model}] API 错误: {response.text}")
                    break # 4xx 错误通常重试无效
            except Exception as e:
                logger.error(f"[{tag}:{model}] 连接异常: {e}")
                time.sleep(1)
        return None

    def generate_stream(self, messages: List[Dict[str, str]], temperature: float = None) -> Generator[str, None, None]:
        """流式生成 (暂不支持 Fallback 切换，因为流式通常用于 Narrator，而 Narrator 是本地模型)"""
        temp = temperature if temperature is not None else self.default_temp
        
        try:
            response = self._try_request(self.model_name, self.api_key, self.base_url, messages, temp, stream=True)
            response.raise_for_status()
            for line in response.iter_lines():
                if line:
                    line = line.decode('utf-8')
                    if line.startswith("data: "):
                        if line == "data: [DONE]": break
                        try:
                            json_str = line[6:]
                            data = json.loads(json_str)
                            content = data['choices'][0]['delta'].get('content', '')
                            if content: yield content
                        except: pass
        except Exception as e:
            logger.error(f"[{self.model_name}] 流式失败: {e}")
            yield f"[System Error: {e}]"
