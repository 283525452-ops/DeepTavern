# core/workflow/manager.py
"""
DeepTavern 工作流管理器 v4.5
- 扩展状态系统（技能、物品、场景、关系描述）
- 关系使用文字描述而非数值
- 氛围系统
"""

import json
import re
import threading
import time
from typing import Generator, List, Dict, Any, Optional

from core.llm.local_direct import LocalDirectLLM
from core.llm.api_client import APILLM

from core.database.vector_store import VectorStore
from core.database.sqlite_manager import SQLiteManager
from core.database.redis_manager import RedisManager
from core.database.graph_manager import GraphManager
from core.workflow.backend_manager import BackendManager
from core.workflow.prompts import get_prompt
from config.settings import MODEL_CONFIG, DEFAULT_HISTORY_LIMIT, SYSTEM_MAX_HISTORY_CHARS
from core.utils.logger import logger


class WorkflowManager:
    """
    DeepTavern 核心工作流管理器
    """
    
    # 氛围→写作风格映射
    ATMOSPHERE_STYLES = {
        "紧张": "短句为主，动词密集，节奏紧凑，少用形容词",
        "战斗": "动作描写密集，紧张刺激，可有痛感和血腥描写",
        "浪漫": "感官细节丰富，节奏舒缓，注重情感流动和氛围营造",
        "亲密": "大胆露骨，感官描写详尽，不回避任何细节",
        "诡异": "暗示性描写，营造不安和悬疑，多用隐喻",
        "恐怖": "压迫感，未知的恐惧，感官上的不适",
        "悲伤": "内省基调，关注内心感受，节奏放缓",
        "轻松": "自然对话，可以有幽默，节奏轻快",
        "日常": "生活化描写，细节真实，对话自然"
    }

    # 默认初始状态模板
    DEFAULT_STATE = {
        "player": {
            "name": "Player",
            "hp": 100,
            "max_hp": 100,
            "mp": 50,
            "max_mp": 50,
            "status_effects": []
        },
        "skills": {},
        "inventory": {},
        "relationships": {},
        "scene": {
            "location": "未知地点",
            "sub_location": "",
            "atmosphere": "日常",
            "weather": "晴朗",
            "time_of_day": "morning",
            "npcs_present": []
        },
        "world_time": {
            "day": 1,
            "hour": 8,
            "minute": 0
        },
        "narrator_persona": {
            "current_mood": "平静",
            "speech_style": "正常"
        }
    }

    def __init__(self):
        logger.info("=" * 60)
        logger.info("🏰 系统初始化: DeepTavern v4.5 Core")
        logger.info("   (扩展状态系统 & 文字关系描述 & 氛围系统)")
        logger.info("=" * 60)
        
        # 加载 LLM
        def load_llm(role_key, default_name="Unknown"):
            conf = MODEL_CONFIG.get(role_key, {})
            if not conf:
                logger.warning(f"⚠️ [{role_key}] 未找到配置，使用 Mock 模型")
                return APILLM({"model": "mock", "api_key": "none", "base_url": ""})
            
            model_path = str(conf.get("model", "")).lower()
            
            if model_path.endswith(".gguf"):
                logger.info(f"📥 [初始化] 加载本地模型 {default_name} (GGUF)...")
                return LocalDirectLLM(config=conf)
            else:
                logger.info(f"☁️ [初始化] 连接云端模型 {default_name}...")
                return APILLM(conf)

        self.reflex_bot = load_llm("reflex", "Reflex (意图识别)")
        self.director_bot = load_llm("director", "Director (导演)")
        self.narrator_bot = load_llm("narrator", "Narrator (叙事者)")
        
        # 基础设施
        self.memory_vec = VectorStore(collection_name="long_term_memory")
        self.rules_vec = VectorStore(collection_name="rules_memory")
        self.graph = GraphManager()
        
        self.db = SQLiteManager()
        self.redis = RedisManager()
        self.backend = BackendManager()
        
        self.current_session_uuid = None
        self.context_limit = DEFAULT_HISTORY_LIMIT
        self.max_chars = SYSTEM_MAX_HISTORY_CHARS
        
        self.char_name = "AI Character"
        self.char_persona = "A helpful roleplay assistant."
        
        logger.info("=" * 60)
        logger.info("✅ 系统就绪")
        logger.info("=" * 60)

    # ==========================================
    # 会话管理
    # ==========================================

    def start_new_session(self, user_name: str = "Player", char_name: str = None, 
                          char_persona: str = None) -> str:
        """开启新会话"""
        if char_name:
            self.char_name = char_name
        if char_persona:
            self.char_persona = char_persona
        
        # 创建初始状态
        initial_state = self._create_initial_state(user_name)
        
        uuid = self.db.create_conversation(
            character_name=self.char_name,
            initial_state=initial_state
        )
        
        self.current_session_uuid = uuid
        self.graph.switch_session(uuid)
        self.redis.clear_context(uuid)
        self.redis.clear_state(uuid)
        
        logger.info(f"🆕 新会话已创建: {user_name} vs {self.char_name} (UUID: {uuid})")
        return uuid

    def _create_initial_state(self, user_name: str) -> Dict:
        """创建初始状态"""
        state = json.loads(json.dumps(self.DEFAULT_STATE))  # 深拷贝
        state["player"]["name"] = user_name
        return state

    def load_session(self, uuid: str) -> bool:
        """加载会话"""
        if self.db.load_conversation(uuid):
            self.current_session_uuid = uuid
            self.char_name = self.db.get_current_character_name()
            self.graph.switch_session(uuid)
            self._get_history_list()
            self._get_current_state()
            logger.info(f"📂 存档已加载: {uuid} (角色: {self.char_name})")
            return True
        logger.error(f"❌ 加载存档失败: {uuid}")
        return False

    def list_all_sessions(self) -> List[Dict]:
        """列出所有会话"""
        return self.db.list_conversations()

    def delete_session(self, uuid: str) -> bool:
        """删除会话"""
        logger.warning(f"🗑️ 正在销毁会话: {uuid}")
        
        db_success = self.db.delete_session(uuid)
        if not db_success:
            return False
        
        self.memory_vec.delete_session_memories(uuid)
        self.graph.delete_graph(uuid)
        self.redis.clear_context(uuid)
        self.redis.clear_state(uuid)
        
        if self.current_session_uuid == uuid:
            self.current_session_uuid = None
        
        logger.info("✅ 会话销毁完成")
        return True

    # ==========================================
    # 状态格式化方法
    # ==========================================

    def _format_player_status(self, state: Dict) -> str:
        """格式化玩家状态"""
        player = state.get("player", {})
        
        hp = player.get("hp", 100)
        max_hp = player.get("max_hp", 100)
        mp = player.get("mp", 0)
        max_mp = player.get("max_mp", 0)
        effects = player.get("status_effects", [])
        
        lines = [f"HP: {hp}/{max_hp}"]
        
        if max_mp > 0:
            lines.append(f"MP: {mp}/{max_mp}")
        
        if effects:
            lines.append(f"状态: {', '.join(effects)}")
        
        return " | ".join(lines)

    def _format_relationships(self, state: Dict) -> str:
        """格式化人物关系（文字描述）"""
        relationships = state.get("relationships", {})
        
        if not relationships:
            return "暂无已建立的人物关系"
        
        lines = []
        for name, info in relationships.items():
            if isinstance(info, dict):
                relation = info.get("关系", "未知")
                events = info.get("近期事件", [])
                personality = info.get("性格备注", "")
                
                line = f"【{name}】{relation}"
                if events:
                    line += f"\n  近期: {'; '.join(events[-3:])}"  # 最近3件事
                if personality:
                    line += f"\n  备注: {personality}"
                lines.append(line)
            else:
                # 兼容旧格式（纯数值）
                lines.append(f"【{name}】关系值: {info}")
        
        return "\n".join(lines)

    def _format_skills(self, state: Dict) -> str:
        """格式化技能"""
        skills = state.get("skills", {})
        
        if not skills:
            return "暂无技能"
        
        lines = []
        for name, info in skills.items():
            if isinstance(info, dict):
                level = info.get("level", 1)
                exp = info.get("exp", 0)
                desc = info.get("description", "")
                line = f"- {name} Lv.{level} (经验: {exp}/100)"
                if desc:
                    line += f" - {desc}"
                lines.append(line)
            else:
                lines.append(f"- {name}: {info}")
        
        return "\n".join(lines)

    def _format_inventory(self, state: Dict) -> str:
        """格式化物品"""
        inventory = state.get("inventory", {})
        
        if not inventory:
            return "背包为空"
        
        equipped = []
        items = []
        
        for name, info in inventory.items():
            if isinstance(info, dict):
                count = info.get("count", 1)
                item_type = info.get("type", "")
                is_equipped = info.get("equipped", False)
                desc = info.get("description", "")
                
                if count > 1:
                    item_str = f"{name} x{count}"
                else:
                    item_str = name
                
                if desc:
                    item_str += f" ({desc})"
                
                if is_equipped:
                    equipped.append(f"[装备中] {item_str}")
                else:
                    items.append(f"- {item_str}")
            else:
                items.append(f"- {name}")
        
        result = []
        if equipped:
            result.extend(equipped)
        if items:
            result.extend(items)
        
        return "\n".join(result) if result else "背包为空"

    def _format_skills_and_items(self, state: Dict) -> str:
        """组合技能和物品信息"""
        skills_text = self._format_skills(state)
        items_text = self._format_inventory(state)
        
        return f"【技能】\n{skills_text}\n\n【物品】\n{items_text}"

    def _format_scene(self, state: Dict) -> Dict[str, str]:
        """提取场景信息"""
        scene = state.get("scene", {})
        world_time = state.get("world_time", {})
        
        location = scene.get("location", "未知")
        sub_loc = scene.get("sub_location", "")
        if sub_loc:
            location = f"{location} - {sub_loc}"
        
        atmosphere = scene.get("atmosphere", "日常")
        weather = scene.get("weather", "")
        time_of_day = scene.get("time_of_day", "")
        npcs = scene.get("npcs_present", [])
        
        return {
            "location": location,
            "atmosphere": atmosphere,
            "weather": weather,
            "time_of_day": time_of_day,
            "npcs_present": ", ".join(npcs) if npcs else "无"
        }

    def _get_atmosphere_style(self, atmosphere: str) -> str:
        """获取氛围对应的写作风格指导"""
        return self.ATMOSPHERE_STYLES.get(atmosphere, "正常叙事风格")

    def _format_timeline_tag(self, state: Dict) -> str:
        """格式化时间标签"""
        world_time = state.get("world_time", {})
        day = world_time.get("day", 1)
        hour = world_time.get("hour", 8)
        minute = world_time.get("minute", 0)
        return f"Day {day}, {hour:02d}:{minute:02d}"

    def _format_persona_voice(self, state: Dict) -> str:
        """格式化叙事者人格状态"""
        persona = state.get("narrator_persona", {})
        mood = persona.get("current_mood", "平静")
        style = persona.get("speech_style", "正常")
        
        return f"当前心情: {mood}\n说话风格: {style}"

    # ==========================================
    # 规则解析辅助
    # ==========================================

    def _parse_rule_selection(self, selection_res: str, max_options: int) -> List[int]:
        """解析规则选择结果"""
        if not selection_res or "NONE" in selection_res.upper():
            return []
        
        selected = []
        numbers = re.findall(r'\d+', selection_res)
        
        for num_str in numbers:
            try:
                num = int(num_str)
                if 1 <= num <= max_options and num not in selected:
                    selected.append(num)
            except ValueError:
                continue
        
        return selected

    # ==========================================
    # 核心对话循环
    # ==========================================

    def chat(self, user_input: str, deep_mode: bool = False, 
             lite_mode: bool = False) -> Generator[str, None, None]:
        """核心对话方法"""
        
        if not self.current_session_uuid:
            yield "[系统错误]: 未加载任何会话。"
            return

        start_time = time.time()
        
        history_list = self._get_history_list()
        current_turn = (len(history_list) // 2) + 1
        current_state = self._get_current_state()
        
        logger.info(f"\n{'='*60}")
        logger.info(f"🏁 [第 {current_turn} 轮对话开始]")
        logger.info(f"   深度模式: {deep_mode}, 轻量模式: {lite_mode}")
        logger.info(f"{'='*60}")
        logger.info(f"👤 [用户输入]: {user_input}")
        
        # 格式化状态信息
        scene_info = self._format_scene(current_state)
        timeline_tag = self._format_timeline_tag(current_state)
        player_status = self._format_player_status(current_state)
        relationships_text = self._format_relationships(current_state)
        skills_and_items = self._format_skills_and_items(current_state)
        persona_voice = self._format_persona_voice(current_state)
        atmosphere = scene_info.get("atmosphere", "日常")
        atmosphere_style = self._get_atmosphere_style(atmosphere)
        
        # 初始化
        logic_verdict = "（轻量模式跳过）"
        weighted_memory_text = ""
        weighted_rules_text = ""
        search_query = user_input

        # === 阶段 A: 感知与思考 ===
        if not lite_mode:
            # 1. Reflex (意图识别)
            logger.info("🔍 [Reflex] 意图识别中...")
            
            reflex_limit = 5
            short_history = history_list[-reflex_limit:] if len(history_list) > reflex_limit else history_list
            short_history_text = self._format_history_text(short_history)
            
            reflex_prompt = get_prompt("reflex").format(
                history=short_history_text,
                user_input=user_input
            )
            reflex_response = self.reflex_bot.generate([{"role": "user", "content": reflex_prompt}])
            
            if "Error" in reflex_response or "exceed" in reflex_response:
                logger.error(f"❌ [Reflex] 错误: {reflex_response}")
                search_query = user_input
            else:
                search_query = reflex_response.strip().replace('"', '').replace("Search Query:", "").strip()
                logger.info(f"✅ [Reflex] 搜索关键词: '{search_query}'")
            
            if "BLOCK" in reflex_response.upper() and "BLOCK" not in user_input.upper():
                logger.warning("🛡️ [安全拦截]")
                yield "系统拦截：输入包含不安全内容。"
                return

            # 2. Rules RAG
            logger.info("📜 [Rules RAG] 检索规则库...")
            
            rule_candidates = self.rules_vec.search(search_query, n_results=5)
            active_rules = self.db.get_active_rules()
            
            if rule_candidates:
                options_text = ""
                for i, r in enumerate(rule_candidates):
                    preview = r['content'][:100].replace('\n', ' ')
                    options_text += f"Option {i+1}: {preview}...\n"
                
                selection_prompt = (
                    f"User Input: {user_input}\n"
                    f"Candidates:\n{options_text}\n"
                    f"Task: Which rules apply? Output numbers (e.g. 1,3) or NONE."
                )
                selection_res = self.reflex_bot.generate([{"role": "user", "content": selection_prompt}])
                
                selected_indices = self._parse_rule_selection(selection_res, len(rule_candidates))
                
                for idx in selected_indices:
                    r = rule_candidates[idx - 1]
                    full_content = r.get('metadata', {}).get('full_content', r['content'])
                    active_rules.append(full_content)
                
                logger.info(f"✅ [Rules RAG] 激活 {len(selected_indices)} 条规则")

            weighted_rules_text = "\n\n".join(active_rules) if active_rules else ""

            # 3. Memory RAG
            n_results = 100 if deep_mode else 20
            logger.info(f"🧠 [Memory RAG] 检索记忆 (目标: {n_results})...")
            
            filter_condition = {
                "$or": [
                    {"session_id": self.current_session_uuid},
                    {"type": "INTERNET_LORE"}
                ]
            }
            
            memories = self.memory_vec.search(
                search_query,
                n_results=n_results,
                filter_dict=filter_condition
            )
            
            if memories:
                memory_parts = [f"- {m['content']}" for m in memories if m.get('score', 0) > 0.2]
                weighted_memory_text = "\n".join(memory_parts) if memory_parts else "无相关记忆"
                logger.info(f"✅ [Memory RAG] 召回 {len(memory_parts)} 条")
            else:
                weighted_memory_text = "无相关记忆"

            # 4. GraphRAG
            logger.info("🕸️ [GraphRAG] 检索知识图谱...")
            keywords = search_query.split()
            graph_context = self.graph.search_subgraph(search_query, top_k=5, depth=1)
            
            if graph_context:
                weighted_memory_text += f"\n\n【知识图谱】\n{graph_context}"
                logger.info("✅ [GraphRAG] 发现关联")

            # 5. Director
            logger.info("🎬 [Director] 编排剧情...")
            
            memory_spine = self.db.get_memory_spine()
            
            director_history_limit = 10
            recent_msgs = history_list[-director_history_limit:] if len(history_list) > director_history_limit else history_list
            recent_history_text = self._format_history_text(recent_msgs)
            if not recent_history_text:
                recent_history_text = "(对话刚开始)"

            director_prompt = get_prompt("director").format(
                timeline_tag=timeline_tag,
                location=scene_info["location"],
                atmosphere=atmosphere,
                weather=scene_info["weather"],
                npcs_present=scene_info["npcs_present"],
                player_status=player_status,
                relationships_text=relationships_text,
                skills_and_items=skills_and_items,
                state=json.dumps(current_state, ensure_ascii=False, indent=2),
                dynamic_rules=weighted_rules_text,
                spine=memory_spine,
                rag_details=weighted_memory_text,
                user_input=user_input
            )
            
            logic_verdict = self.director_bot.generate([{"role": "user", "content": director_prompt}])
            
            logger.info(f"🎬 [Director 指令]:\n{'-'*40}\n{logic_verdict[:500]}...\n{'-'*40}")
            
            yield f"\n[导演]: {logic_verdict[:80]}...\n\n"

        # === 阶段 B: Narrator ===
        logger.info("🗣️ [Narrator] 生成回复...")
        
        narrator_system_prompt = get_prompt("narrator").format(
            atmosphere=atmosphere,
            persona_voice=persona_voice,
            scene_info=scene_info["location"],
            npcs_present=scene_info["npcs_present"],
            director_note=logic_verdict,
            dynamic_rules=weighted_rules_text,
            persona=self.char_persona,
            user_input=user_input
        )
        
        safe_history_limit = 20
        messages = [{"role": "system", "content": narrator_system_prompt}]
        recent_history = history_list[-safe_history_limit:] if len(history_list) > safe_history_limit else history_list
        
        for msg in recent_history:
            messages.append({"role": msg["role"], "content": msg["content"]})
        
        messages.append({"role": "user", "content": user_input})
        
        full_response = ""
        
        try:
            for chunk in self.narrator_bot.generate_stream(messages):
                full_response += chunk
                yield chunk
        except Exception as e:
            logger.error(f"❌ [Narrator] 生成中断: {e}")
            if not full_response:
                full_response = "(叙事者故障，请重试)"
                yield full_response

        logger.info(f"🗣️ [Narrator] 输出 {len(full_response)} 字")

        # === 阶段 C: 后台任务 ===
        logger.info("⚙️ [后台] 触发异步任务...")
        
        self.db.add_message("user", user_input)
        ai_msg_id = self.db.add_message("assistant", full_response)
        
        full_prompt_log = json.dumps(messages, ensure_ascii=False)
        self.db.log_interaction(ai_msg_id, full_prompt_log, weighted_memory_text, 
                               getattr(self.narrator_bot, 'model_name', 'unknown'))
        
        new_history = history_list + [
            {"role": "user", "content": user_input},
            {"role": "assistant", "content": full_response}
        ]
        if len(new_history) > self.context_limit:
            new_history = new_history[-self.context_limit:]
        self.redis.cache_context(self.current_session_uuid, new_history)
        
        threading.Thread(
            target=self.backend.run_background_tasks,
            args=(user_input, full_response, search_query, self.current_session_uuid),
            daemon=True
        ).start()
        
        elapsed = time.time() - start_time
        logger.info(f"{'='*60}")
        logger.info(f"🏁 [第 {current_turn} 轮结束] 耗时: {elapsed:.2f}s")
        logger.info(f"{'='*60}")

    # ==========================================
    # 高级功能接口
    # ==========================================

    def rollback(self, target_message_id: int) -> bool:
        """回滚到指定消息"""
        if not self.current_session_uuid:
            return False
        
        logger.warning(f"⏪ [回滚] 至消息 ID {target_message_id}")
        new_state = self.db.rollback_to_message(target_message_id)
        
        if new_state:
            self.redis.clear_context(self.current_session_uuid)
            self.redis.clear_state(self.current_session_uuid)
            self.redis.cache_state(self.current_session_uuid, new_state)
            logger.info("✅ [回滚] 成功")
            return True
        
        logger.error("❌ [回滚] 失败")
        return False

    def get_full_history(self, page: int = 1, page_size: int = 50) -> List[Dict]:
        """获取完整历史"""
        if not self.current_session_uuid:
            return []
        return self.db.get_full_history(page, page_size)

    def get_archived_memories(self) -> List[Dict]:
        """获取归档记忆"""
        if not self.current_session_uuid:
            return []
        return self.db.get_memories()

    # ==========================================
    # 内部辅助方法
    # ==========================================

    def _get_history_list(self) -> List[Dict]:
        """获取历史消息列表"""
        if not self.current_session_uuid:
            return []
        
        cached = self.redis.get_context(self.current_session_uuid)
        if cached:
            return cached
        
        history = self.db.get_recent_messages(limit=self.context_limit)
        self.redis.cache_context(self.current_session_uuid, history)
        return history

    def _get_current_state(self) -> Dict:
        """获取当前状态"""
        if not self.current_session_uuid:
            return {}
        
        cached = self.redis.get_state(self.current_session_uuid)
        if cached:
            return cached
        
        state = self.db.get_current_state()
        
        # 确保状态有所有必需字段
        state = self._ensure_state_structure(state)
        
        self.redis.cache_state(self.current_session_uuid, state)
        return state

    def _ensure_state_structure(self, state: Dict) -> Dict:
        """确保状态结构完整（兼容旧存档）"""
        default = self.DEFAULT_STATE
        
        # 合并缺失的字段
        for key, value in default.items():
            if key not in state:
                state[key] = value
            elif isinstance(value, dict) and isinstance(state.get(key), dict):
                for sub_key, sub_value in value.items():
                    if sub_key not in state[key]:
                        state[key][sub_key] = sub_value
        
        # 兼容旧的扁平结构
        if "hp" in state and "player" in state:
            state["player"]["hp"] = state.pop("hp", 100)
        if "inventory" in state and isinstance(state["inventory"], list):
            # 旧格式是列表，转换为字典
            old_inv = state["inventory"]
            state["inventory"] = {item: {"type": "item", "count": 1} for item in old_inv}
        if "location" in state and "scene" in state:
            state["scene"]["location"] = state.pop("location", "未知")
        if "world_time" in state and isinstance(state["world_time"], str):
            # 旧格式是字符串 "Day 1, 08:00"
            state["world_time"] = {"day": 1, "hour": 8, "minute": 0}
        
        return state

    def _format_history_text(self, history_list: List[Dict]) -> str:
        """格式化历史消息"""
        buffer = []
        for msg in history_list:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            
            if role == "user":
                buffer.append(f"Player: {content}")
            elif role == "assistant":
                buffer.append(f"{self.char_name}: {content}")
            else:
                buffer.append(f"[{role}]: {content}")
        
        return "\n".join(buffer)
