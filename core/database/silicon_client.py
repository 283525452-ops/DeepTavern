# core/database/silicon_client.py
import requests
from typing import List
from chromadb import Documents, EmbeddingFunction, Embeddings
# 引入新的通用变量名
from config.settings import VECTOR_API_KEY, VECTOR_BASE_URL, EMBEDDING_MODEL, RERANK_MODEL
from core.utils.logger import logger

class SiliconFlowEmbedding(EmbeddingFunction):
    def __init__(self):
        pass
        
    def name(self):
        return "SiliconFlowEmbedding"

    # ================= [新增修复] =================
    def get_config(self):
        """修复 ChromaDB 的 DeprecationWarning"""
        return {
            "model": EMBEDDING_MODEL,
            "base_url": VECTOR_BASE_URL
        }
    # =============================================

    def __call__(self, input: Documents) -> Embeddings:
        # 使用 settings.py 解析出来的向量专用配置
        url = f"{VECTOR_BASE_URL}/embeddings"
        headers = {
            "Authorization": f"Bearer {VECTOR_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": EMBEDDING_MODEL,
            "input": input,
            "encoding_format": "float"
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            embeddings = [item['embedding'] for item in data['data']]
            return embeddings
        except Exception as e:
            logger.error(f"Embedding API 调用失败: {e}")
            raise e

def rerank_documents(query: str, documents: List[str]) -> List[dict]:
    if not documents:
        return []

    url = f"{VECTOR_BASE_URL}/rerank"
    headers = {
        "Authorization": f"Bearer {VECTOR_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": RERANK_MODEL,
        "query": query,
        "documents": documents,
        "top_n": len(documents),
        "return_documents": False
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data['results']
    except Exception as e:
        logger.error(f"Rerank API 调用失败: {e}")
        return []
