# core/llm/local_direct.py
from core.llm.base import BaseLLM
from core.utils.logger import logger
import os
import threading

try:
    from llama_cpp import Llama
except ImportError:
    Llama = None

class LocalDirectLLM(BaseLLM):
    # === 类变量：用于存储已加载的模型实例 ===
    _loaded_instances = {} 
    _instance_lock = threading.Lock() # 这里的锁用于保护加载过程
    _generate_lock = threading.Lock() # 这里的锁用于保护推理过程（防止多线程同时调用同一个模型）

    def __init__(self, config):
        if Llama is None:
            raise ImportError("Please install llama-cpp-python to use local GGUF models!")
            
        self.model_path = config.get("model")
        # 修复 model_name 报错
        self.model_name = os.path.basename(self.model_path) if self.model_path else "Local-GGUF"
        
        self.context_window = config.get("n_ctx", 4096)
        # 如果是 CPU 推理，建议设为 0 或 -1；如果是 GPU，设为 -1
        self.n_gpu_layers = config.get("n_gpu_layers", -1) 

        # === 核心修改：单例模式加载 ===
        with LocalDirectLLM._instance_lock:
            if self.model_path in LocalDirectLLM._loaded_instances:
                logger.info(f"[Local] Reusing loaded model instance: {self.model_name}")
                self.llm = LocalDirectLLM._loaded_instances[self.model_path]
            else:
                logger.info(f"[Local] Loading NEW model instance: {self.model_path}")
                try:
                    # 第一次加载
                    self.llm = Llama(
                        model_path=self.model_path,
                        n_ctx=self.context_window,
                        n_gpu_layers=self.n_gpu_layers,
                        verbose=False # 关闭底层啰嗦的日志
                    )
                    LocalDirectLLM._loaded_instances[self.model_path] = self.llm
                except Exception as e:
                    logger.error(f"[Local] Init failed: {e}")
                    self.llm = None

    def generate(self, messages, temperature=0.7):
        if not self.llm: return "Error: Model not loaded."
        
        # === 核心修改：推理加锁 ===
        # 本地 GGUF 模型通常不支持多线程并发推理，必须排队
        with LocalDirectLLM._generate_lock:
            try:
                response = self.llm.create_chat_completion(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=2048
                )
                return response['choices'][0]['message']['content']
            except Exception as e:
                logger.error(f"[Local] Generate error: {e}")
                return f"Error: {str(e)}"

    def generate_stream(self, messages, temperature=0.7):
        if not self.llm: 
            yield "Error: Model not loaded."
            return

        # === 核心修改：推理加锁 ===
        with LocalDirectLLM._generate_lock:
            try:
                stream = self.llm.create_chat_completion(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=2048,
                    stream=True
                )
                for chunk in stream:
                    delta = chunk['choices'][0]['delta']
                    if 'content' in delta:
                        yield delta['content']
            except Exception as e:
                logger.error(f"[Local] Stream error: {e}")
                yield f"Error: {str(e)}"
