# core/database/redis_manager.py
import json
import redis
from config.settings import REDIS_HOST, REDIS_PORT, REDIS_DB, REDIS_PASSWORD, USE_REDIS, REDIS_TTL
from core.utils.logger import logger

class RedisManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(RedisManager, cls).__new__(cls)
            cls._instance._init_redis()
        return cls._instance

    def _init_redis(self):
        self.enabled = USE_REDIS
        self.client = None
        if self.enabled:
            try:
                self.client = redis.Redis(
                    host=REDIS_HOST,
                    port=REDIS_PORT,
                    db=REDIS_DB,
                    password=REDIS_PASSWORD,
                    decode_responses=True, # 自动将 bytes 解码为 str
                    socket_connect_timeout=2
                )
                self.client.ping() # 测试连接
                logger.info(f"Redis (Hot DB) Connected [DB:{REDIS_DB}]")
            except Exception as e:
                logger.warning(f"Redis Connection Failed. Downgrading to SQLite only: {e}")
                self.enabled = False

    # ==========================
    # 1. 上下文缓存 (Context Window)
    # ==========================
    
    def cache_context(self, session_uuid, messages):
        """
        缓存最近的对话历史 (Context Window)
        :param messages: list of dict [{'role': 'user', 'content': '...'}]
        """
        if not self.enabled: return
        key = f"session:{session_uuid}:context"
        try:
            # 存为 JSON 字符串
            self.client.setex(key, REDIS_TTL, json.dumps(messages, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Redis Write Context Failed: {e}")

    def get_context(self, session_uuid):
        """
        获取缓存的上下文
        :return: list or None (None 表示 Cache Miss)
        """
        if not self.enabled: return None
        key = f"session:{session_uuid}:context"
        try:
            data = self.client.get(key)
            if data:
                return json.loads(data)
            return None 
        except Exception as e:
            logger.error(f"Redis Read Context Failed: {e}")
            return None

    def clear_context(self, session_uuid):
        """清除上下文缓存"""
        if not self.enabled: return
        try:
            self.client.delete(f"session:{session_uuid}:context")
        except: pass

    # ==========================
    # 2. 状态缓存 (State Cache)
    # ==========================

    def cache_state(self, session_uuid, state_dict):
        """缓存最新的 RPG 状态"""
        if not self.enabled: return
        key = f"session:{session_uuid}:state"
        try:
            self.client.setex(key, REDIS_TTL, json.dumps(state_dict, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Redis Write State Failed: {e}")

    def get_state(self, session_uuid):
        """获取缓存的状态"""
        if not self.enabled: return None
        key = f"session:{session_uuid}:state"
        try:
            data = self.client.get(key)
            if data:
                return json.loads(data)
            return None
        except:
            return None
            
    def clear_state(self, session_uuid):
        """清除状态缓存"""
        if not self.enabled: return
        try:
            self.client.delete(f"session:{session_uuid}:state")
        except: pass
