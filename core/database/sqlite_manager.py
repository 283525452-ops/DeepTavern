# core/database/sqlite_manager.py
"""
DeepTavern SQLite 数据库管理器 v4.5
- 适配扩展状态系统
- 支持新的状态结构
"""

import sqlite3
import json
import os
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any

from core.utils.logger import logger

# 路径配置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "chat_core.db")
RULES_DB_PATH = os.path.join(PROJECT_ROOT, "data", "rules_preset.db")

SCHEMA_PATH = os.path.join(BASE_DIR, 'schema.sql')
RULES_SCHEMA_PATH = os.path.join(BASE_DIR, 'schema_rules.sql')


class SQLiteManager:
    """
    SQLite 数据库管理器（单例模式）
    """
    
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(SQLiteManager, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        
        # 确保数据目录存在
        data_dir = os.path.dirname(DB_PATH)
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
        
        self.db_lock = threading.Lock()
        
        # 主数据库连接
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        
        # 规则数据库连接
        self.conn_rules = sqlite3.connect(RULES_DB_PATH, check_same_thread=False)
        self.conn_rules.row_factory = sqlite3.Row
        self.cursor_rules = self.conn_rules.cursor()
        
        self.current_conversation_id = None
        self._init_schemas()
        self._initialized = True

    def _init_schemas(self):
        """初始化数据库表结构"""
        with self.db_lock:
            if os.path.exists(SCHEMA_PATH):
                try:
                    with open(SCHEMA_PATH, 'r', encoding='utf-8') as f:
                        self.cursor.executescript(f.read())
                    self.conn.commit()
                except Exception as e:
                    logger.error(f"主数据库初始化错误: {e}")
            
            # 规则库
            try:
                self.cursor_rules.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='rule_fragments'"
                )
                if not self.cursor_rules.fetchone():
                    if os.path.exists(RULES_SCHEMA_PATH):
                        with open(RULES_SCHEMA_PATH, 'r', encoding='utf-8') as f:
                            self.cursor_rules.executescript(f.read())
                        self.conn_rules.commit()
            except Exception as e:
                logger.error(f"规则数据库初始化错误: {e}")

    def _check_session(self):
        """检查是否有活跃会话"""
        if self.current_conversation_id is None:
            pass  # 允许无会话时的只读操作

    # ==========================================
    # 规则库查询
    # ==========================================

    def get_rule_by_keyword(self, keyword: str) -> Optional[str]:
        """根据关键词查询规则"""
        with self.db_lock:
            self.cursor_rules.execute(
                "SELECT content FROM rule_fragments WHERE scope_value LIKE ? LIMIT 1",
                (f"%{keyword}%",)
            )
            row = self.cursor_rules.fetchone()
            if row:
                return row['content']
            
            self.cursor_rules.execute(
                "SELECT content FROM rule_fragments WHERE summary LIKE ? LIMIT 1",
                (f"%{keyword}%",)
            )
            row = self.cursor_rules.fetchone()
            return row['content'] if row else None

    def get_random_rule(self, category: str) -> Optional[str]:
        """获取随机规则"""
        with self.db_lock:
            self.cursor_rules.execute(
                "SELECT content FROM rule_fragments WHERE category = ? ORDER BY RANDOM() LIMIT 1",
                (category,)
            )
            row = self.cursor_rules.fetchone()
            return row['content'] if row else None

    def get_active_rules(self) -> List[str]:
        """获取所有默认启用的规则"""
        with self.db_lock:
            self.cursor_rules.execute(
                "SELECT content FROM rule_fragments WHERE is_active = 1"
            )
            return [row['content'] for row in self.cursor_rules.fetchall()]

    def get_all_keywords(self) -> List[str]:
        """获取所有规则关键词"""
        with self.db_lock:
            self.cursor_rules.execute(
                "SELECT scope_value FROM rule_fragments WHERE scope_value IS NOT NULL AND scope_value != ''"
            )
            return list(set([row['scope_value'] for row in self.cursor_rules.fetchall()]))

    def get_context_rules(self, location: str, hp: int, tags_list: List[str]) -> List[str]:
        """根据上下文获取规则"""
        rules = []
        with self.db_lock:
            if location:
                self.cursor_rules.execute(
                    "SELECT content FROM rule_fragments WHERE scope_type='LOCATION' AND scope_value = ?",
                    (location,)
                )
                rules.extend([r['content'] for r in self.cursor_rules.fetchall()])
            
            if hp < 20:
                self.cursor_rules.execute(
                    "SELECT content FROM rule_fragments WHERE scope_type='STATE' AND scope_value = 'LOW_HP'"
                )
                rules.extend([r['content'] for r in self.cursor_rules.fetchall()])
            
            if tags_list:
                for tag in tags_list:
                    self.cursor_rules.execute(
                        "SELECT content FROM rule_fragments WHERE required_tags LIKE ?",
                        (f"%{tag}%",)
                    )
                    rules.extend([r['content'] for r in self.cursor_rules.fetchall()])
        
        return rules

    # ==========================================
    # 会话管理
    # ==========================================

    def create_conversation(self, character_name: str, initial_state: Dict) -> str:
        """创建新会话"""
        session_uuid = str(uuid.uuid4())
        json_str = json.dumps(initial_state, ensure_ascii=False)
        
        with self.db_lock:
            self.cursor.execute(
                "INSERT INTO conversations (uuid, character_name, last_state_json) VALUES (?, ?, ?)",
                (session_uuid, character_name, json_str)
            )
            self.current_conversation_id = self.cursor.lastrowid
            self.conn.commit()
        
        return session_uuid

    def load_conversation(self, uuid: str) -> bool:
        """加载会话"""
        with self.db_lock:
            self.cursor.execute(
                "SELECT id, character_name FROM conversations WHERE uuid = ?",
                (uuid,)
            )
            row = self.cursor.fetchone()
            if row:
                self.current_conversation_id = row['id']
                return True
            return False

    def list_conversations(self) -> List[Dict]:
        """列出所有会话"""
        with self.db_lock:
            self.cursor.execute(
                "SELECT uuid, character_name, created_at FROM conversations ORDER BY id DESC"
            )
            return [dict(row) for row in self.cursor.fetchall()]

    def get_current_character_name(self) -> str:
        """获取当前角色名"""
        self._check_session()
        with self.db_lock:
            self.cursor.execute(
                "SELECT character_name FROM conversations WHERE id = ?",
                (self.current_conversation_id,)
            )
            row = self.cursor.fetchone()
            return row['character_name'] if row else "Unknown"

    def delete_session(self, uuid: str) -> bool:
        """删除会话"""
        with self.db_lock:
            self.cursor.execute("SELECT id FROM conversations WHERE uuid = ?", (uuid,))
            row = self.cursor.fetchone()
            if not row:
                return False
            
            conv_id = row['id']
            
            # 级联删除
            tables = [
                "messages", "memory_nodes", "relationships",
                "saga_entries", "lore_entries", "interaction_logs", "world_states"
            ]
            for table in tables:
                self.cursor.execute(f"DELETE FROM {table} WHERE conversation_id = ?", (conv_id,))
            
            self.cursor.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
            self.conn.commit()
            
            if self.current_conversation_id == conv_id:
                self.current_conversation_id = None
            
            return True

    # ==========================================
    # 消息管理
    # ==========================================

    def add_message(self, role: str, content: str) -> int:
        """添加消息"""
        self._check_session()
        with self.db_lock:
            self.cursor.execute(
                "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
                (self.current_conversation_id, role, content)
            )
            self.conn.commit()
            return self.cursor.lastrowid

    def get_recent_messages(self, limit: int = 20) -> List[Dict]:
        """获取最近消息"""
        self._check_session()
        with self.db_lock:
            self.cursor.execute(
                "SELECT id, role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
                (self.current_conversation_id, limit)
            )
            return [dict(row) for row in reversed(self.cursor.fetchall())]

    def get_unsummarized_messages(self, limit: int = 5) -> List[Dict]:
        """获取未摘要的消息"""
        self._check_session()
        with self.db_lock:
            self.cursor.execute(
                "SELECT id, role, content FROM messages WHERE conversation_id = ? AND is_summarized = 0 ORDER BY id ASC LIMIT ?",
                (self.current_conversation_id, limit)
            )
            return [dict(row) for row in self.cursor.fetchall()]

    def mark_messages_summarized(self, ids: List[int]):
        """标记消息为已摘要"""
        if not ids:
            return
        with self.db_lock:
            placeholders = ','.join(['?' for _ in ids])
            self.cursor.execute(
                f"UPDATE messages SET is_summarized=1 WHERE id IN ({placeholders})",
                ids
            )
            self.conn.commit()

    def get_full_history(self, page: int = 1, page_size: int = 50) -> List[Dict]:
        """获取完整历史（分页）"""
        self._check_session()
        offset = (page - 1) * page_size
        with self.db_lock:
            self.cursor.execute(
                "SELECT id, role, content, timestamp FROM messages WHERE conversation_id = ? ORDER BY id ASC LIMIT ? OFFSET ?",
                (self.current_conversation_id, page_size, offset)
            )
            return [dict(row) for row in self.cursor.fetchall()]

    # ==========================================
    # 状态管理
    # ==========================================

    def get_current_state(self) -> Dict:
        """获取当前状态"""
        self._check_session()
        with self.db_lock:
            self.cursor.execute(
                "SELECT last_state_json FROM conversations WHERE id = ?",
                (self.current_conversation_id,)
            )
            row = self.cursor.fetchone()
            if row and row['last_state_json']:
                try:
                    return json.loads(row['last_state_json'])
                except json.JSONDecodeError:
                    return {}
            return {}

    def save_state(self, state: Dict, diff_summary: str = "", message_id: int = None):
        """保存状态"""
        self._check_session()
        json_str = json.dumps(state, ensure_ascii=False)
        
        with self.db_lock:
            # 更新当前状态
            self.cursor.execute(
                "UPDATE conversations SET last_state_json = ? WHERE id = ?",
                (json_str, self.current_conversation_id)
            )
            
            # 保存状态快照（用于回滚）
            self.cursor.execute(
                "INSERT INTO world_states (conversation_id, message_id, state_json, diff_summary) VALUES (?, ?, ?, ?)",
                (self.current_conversation_id, message_id, json_str, diff_summary)
            )
            self.conn.commit()

    def rollback_to_message(self, target_message_id: int) -> Optional[Dict]:
        """回滚到指定消息"""
        self._check_session()
        with self.db_lock:
            # 查找对应的状态快照
            self.cursor.execute(
                "SELECT state_json FROM world_states WHERE conversation_id = ? AND message_id <= ? ORDER BY message_id DESC LIMIT 1",
                (self.current_conversation_id, target_message_id)
            )
            row = self.cursor.fetchone()
            
            if row:
                state_data = json.loads(row['state_json'])
                json_str = json.dumps(state_data, ensure_ascii=False)
                
                # 恢复状态
                self.cursor.execute(
                    "UPDATE conversations SET last_state_json = ? WHERE id = ?",
                    (json_str, self.current_conversation_id)
                )
                
                # 删除后续消息
                self.cursor.execute(
                    "DELETE FROM messages WHERE conversation_id = ? AND id > ?",
                    (self.current_conversation_id, target_message_id)
                )
                
                # 删除后续状态快照
                self.cursor.execute(
                    "DELETE FROM world_states WHERE conversation_id = ? AND message_id > ?",
                    (self.current_conversation_id, target_message_id)
                )
                
                self.conn.commit()
                return state_data
            
            return None

    # ==========================================
    # 记忆节点管理
    # ==========================================

    def get_memory_spine(self) -> str:
        """获取记忆脊柱"""
        self._check_session()
        spine_text = ""
        
        with self.db_lock:
            # 宏观记忆
            self.cursor.execute(
                "SELECT timeline_tag, summary_text FROM memory_nodes WHERE conversation_id = ? AND level = 'MACRO' ORDER BY id ASC",
                (self.current_conversation_id,)
            )
            for r in self.cursor.fetchall():
                spine_text += f"[Macro|{r['timeline_tag']}] {r['summary_text']}\n"
            
            # 未合并的微观记忆
            self.cursor.execute(
                "SELECT timeline_tag, summary_text FROM memory_nodes WHERE conversation_id = ? AND level = 'MICRO' AND is_merged = 0 ORDER BY id ASC",
                (self.current_conversation_id,)
            )
            for r in self.cursor.fetchall():
                spine_text += f"[Micro|{r['timeline_tag']}] {r['summary_text']}\n"
        
        return spine_text if spine_text else "No history yet."

    def add_memory_node(self, text: str, level: str, timeline_tag: str, vector_id: str = ""):
        """添加记忆节点"""
        self._check_session()
        with self.db_lock:
            self.cursor.execute(
                "INSERT INTO memory_nodes (conversation_id, summary_text, level, timeline_tag, vector_id) VALUES (?, ?, ?, ?, ?)",
                (self.current_conversation_id, text, level, timeline_tag, vector_id)
            )
            self.conn.commit()

    def get_unmerged_micro_nodes(self, limit: int = 10) -> List[Dict]:
        """获取未合并的微观节点"""
        self._check_session()
        with self.db_lock:
            self.cursor.execute(
                "SELECT id, summary_text, timeline_tag FROM memory_nodes WHERE conversation_id = ? AND level = 'MICRO' AND is_merged = 0 ORDER BY id ASC LIMIT ?",
                (self.current_conversation_id, limit)
            )
            return [dict(row) for row in self.cursor.fetchall()]

    def mark_nodes_merged(self, ids: List[int]):
        """标记节点为已合并"""
        if not ids:
            return
        with self.db_lock:
            placeholders = ','.join(['?' for _ in ids])
            self.cursor.execute(
                f"UPDATE memory_nodes SET is_merged=1 WHERE id IN ({placeholders})",
                ids
            )
            self.conn.commit()

    # ==========================================
    # 其他功能
    # ==========================================

    def save_saga_entry(self, content: str):
        """保存史诗章节"""
        self._check_session()
        with self.db_lock:
            self.cursor.execute(
                "INSERT INTO saga_entries (conversation_id, content) VALUES (?, ?)",
                (self.current_conversation_id, content)
            )
            self.conn.commit()

    def log_interaction(self, assistant_msg_id: int, full_prompt: str, 
                        rag_context: str, model_name: str):
        """记录交互日志"""
        self._check_session()
        with self.db_lock:
            self.cursor.execute(
                "INSERT INTO interaction_logs (conversation_id, message_id, full_prompt, rag_context, model_name) VALUES (?, ?, ?, ?, ?)",
                (self.current_conversation_id, assistant_msg_id, full_prompt, rag_context, model_name)
            )
            self.conn.commit()

    def get_memories(self, limit: int = 50) -> List[Dict]:
        """获取记忆列表"""
        self._check_session()
        with self.db_lock:
            self.cursor.execute(
                "SELECT id, summary_text, level, timeline_tag, created_at FROM memory_nodes WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
                (self.current_conversation_id, limit)
            )
            return [dict(row) for row in self.cursor.fetchall()]

    def get_latest_rumor(self) -> str:
        """获取最新传闻（兼容接口）"""
        return ""
