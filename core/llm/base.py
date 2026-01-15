# core/llm/base.py
from abc import ABC, abstractmethod
from typing import Generator, List, Dict

class BaseLLM(ABC):
    """所有 LLM 模型的基类"""
    
    def __init__(self, model_name: str, **kwargs):
        self.model_name = model_name
        self.config = kwargs

    @abstractmethod
    def generate(self, messages: List[Dict[str, str]]) -> str:
        """
        同步生成文本（用于后台任务，如总结、状态分析）
        :param messages: [{"role": "user", "content": "..."}]
        :return: 完整的回复字符串
        """
        pass

    @abstractmethod
    def generate_stream(self, messages: List[Dict[str, str]]) -> Generator[str, None, None]:
        """
        流式生成文本（用于主叙事，前端实时显示）
        :return: 生成器，逐字返回
        """
        pass
