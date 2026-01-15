# core/workflow/backend_manager.py
"""
DeepTavern 后台任务管理器 v4.5
- 适配扩展状态系统
- 状态引擎解析更丰富的状态变更
"""

import json
import threading
import re
import time
import uuid
from typing import Dict, Any, Optional

from core.llm.api_client import APILLM
from core.llm.local_direct import LocalDirectLLM
from core.database.sqlite_manager import SQLiteManager
from core.database.vector_store import VectorStore
from core.database.graph_manager import GraphManager
from core.harvester.scheduler import KnowledgeHarvester
from core.workflow.prompts import get_prompt, PROMPT_GRAPH_EXTRACTOR
from config.settings import MODEL_CONFIG
from core.utils.logger import logger


class BackendManager:
    """
    后台任务管理器
    负责：状态更新、记忆压缩、图谱提取、知识爬取
    """

    def __init__(self):
        logger.info("⚙️ [后台] 初始化后台工作流管理器...")
        
        self.db = SQLiteManager()
        self.vec = VectorStore()
        self.graph = GraphManager()

        # 加载各个后台 LLM
        def load_llm(role_key):
            conf = MODEL_CONFIG.get(role_key, {})
            if not conf:
                return APILLM({"model": "mock", "api_key": "none", "base_url": ""})
            model_path = str(conf.get("model", "")).lower()
            if model_path.endswith(".gguf"):
                return LocalDirectLLM(config=conf)
            else:
                return APILLM(conf)

        self.status_bot = load_llm("status")
        self.left_brain = load_llm("left_brain")
        self.right_brain = load_llm("critic")
        self.historian = load_llm("historian")
        self.sociologist = load_llm("sociologist")
        self.graph_extractor = load_llm("sociologist")  # 复用

        # 知识爬虫
        self.harvester = KnowledgeHarvester()
        self.harvester.start()

        logger.info("✅ [后台] 后台服务就绪")

    def _clean_json(self, text: str) -> Optional[Dict]:
        """从 LLM 输出中提取 JSON"""
        if not text:
            return None
        
        try:
            # 尝试直接解析
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        
        # 尝试提取 markdown 代码块
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass
        
        # 尝试提取裸 JSON
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        
        return None

    def _deep_merge_state(self, base: Dict, update: Dict) -> Dict:
        """
        深度合并状态
        update 中的字段会覆盖/更新 base 中的对应字段
        """
        result = json.loads(json.dumps(base))  # 深拷贝
        
        for key, value in update.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                # 递归合并字典
                result[key] = self._deep_merge_state(result[key], value)
            elif key in result and isinstance(result[key], list) and isinstance(value, list):
                # 列表直接替换（或者可以选择合并）
                result[key] = value
            else:
                result[key] = value
        
        return result

    # ==========================================
    # 状态更新任务
    # ==========================================

    def _task_status_update(self, user_input: str, narr_output: str) -> str:
        """
        状态更新任务
        解析对话，更新完整的游戏状态
        """
        current_state = self.db.get_current_state()
        
        # 确保状态结构完整
        current_state = self._ensure_state_structure(current_state)
        
        prompt = get_prompt("status").format(
            current_state=json.dumps(current_state, ensure_ascii=False, indent=2),
            user_input=user_input,
            narrator_output=narr_output
        )
        
        logger.info("⏳ [后台] Status 模型正在分析状态变化...")
        
        try:
            raw = self.status_bot.generate([{"role": "user", "content": prompt}])
            data = self._clean_json(raw)
            
            if not data:
                logger.warning("⚠️ [状态更新] JSON 解析失败，使用默认时间推进")
                # 默认推进 10 分钟
                return self._advance_time_default(current_state)
            
            timeline_tag = data.get("timeline_tag", "Unknown")
            state_update = data.get("state", {})
            
            if state_update:
                # 深度合并状态
                new_state = self._deep_merge_state(current_state, state_update)
                
                # 同步 world_time 和 timeline_tag
                if "world_time" in state_update:
                    wt = state_update["world_time"]
                    if isinstance(wt, dict):
                        timeline_tag = f"Day {wt.get('day', 1)}, {wt.get('hour', 8):02d}:{wt.get('minute', 0):02d}"
                
                # 保存状态
                self.db.save_state(new_state, diff_summary=f"Time: {timeline_tag}")
                
                # 日志记录重要变化
                self._log_state_changes(current_state, new_state)
                
                logger.info(f"🕒 [状态更新] 时间推进至: {timeline_tag}")
            else:
                timeline_tag = self._advance_time_default(current_state)
            
            return timeline_tag
            
        except Exception as e:
            logger.error(f"❌ [状态更新] 错误: {e}")
            return self._advance_time_default(current_state)

    def _advance_time_default(self, current_state: Dict) -> str:
        """默认时间推进（10分钟）"""
        world_time = current_state.get("world_time", {})
        
        if isinstance(world_time, dict):
            day = world_time.get("day", 1)
            hour = world_time.get("hour", 8)
            minute = world_time.get("minute", 0)
            
            minute += 10
            if minute >= 60:
                minute -= 60
                hour += 1
            if hour >= 24:
                hour -= 24
                day += 1
            
            world_time["day"] = day
            world_time["hour"] = hour
            world_time["minute"] = minute
            
            current_state["world_time"] = world_time
            
            # 更新 time_of_day
            if "scene" in current_state:
                current_state["scene"]["time_of_day"] = self._get_time_of_day(hour)
            
            self.db.save_state(current_state, diff_summary="Auto time advance")
            
            return f"Day {day}, {hour:02d}:{minute:02d}"
        else:
            return "Day 1, 08:00"

    def _get_time_of_day(self, hour: int) -> str:
        """根据小时判断时段"""
        if 5 <= hour < 7:
            return "dawn"
        elif 7 <= hour < 12:
            return "morning"
        elif 12 <= hour < 17:
            return "afternoon"
        elif 17 <= hour < 20:
            return "evening"
        else:
            return "night"

    def _log_state_changes(self, old_state: Dict, new_state: Dict):
        """记录重要的状态变化"""
        changes = []
        
        # HP 变化
        old_hp = old_state.get("player", {}).get("hp", 100)
        new_hp = new_state.get("player", {}).get("hp", 100)
        if old_hp != new_hp:
            diff = new_hp - old_hp
            changes.append(f"HP: {old_hp} → {new_hp} ({'+' if diff > 0 else ''}{diff})")
        
        # 关系变化
        old_rels = old_state.get("relationships", {})
        new_rels = new_state.get("relationships", {})
        for name in new_rels:
            if name not in old_rels:
                changes.append(f"新关系: {name}")
            elif new_rels[name] != old_rels.get(name):
                changes.append(f"关系更新: {name}")
        
        # 物品变化
        old_inv = old_state.get("inventory", {})
        new_inv = new_state.get("inventory", {})
        for item in new_inv:
            if item not in old_inv:
                changes.append(f"获得物品: {item}")
        for item in old_inv:
            if item not in new_inv:
                changes.append(f"失去物品: {item}")
        
        # 技能变化
        old_skills = old_state.get("skills", {})
        new_skills = new_state.get("skills", {})
        for skill in new_skills:
            if skill not in old_skills:
                changes.append(f"习得技能: {skill}")
            elif isinstance(new_skills[skill], dict) and isinstance(old_skills.get(skill), dict):
                old_lvl = old_skills[skill].get("level", 1)
                new_lvl = new_skills[skill].get("level", 1)
                if new_lvl > old_lvl:
                    changes.append(f"技能升级: {skill} Lv.{old_lvl} → Lv.{new_lvl}")
        
        # 氛围变化
        old_atm = old_state.get("scene", {}).get("atmosphere", "")
        new_atm = new_state.get("scene", {}).get("atmosphere", "")
        if old_atm != new_atm and new_atm:
            changes.append(f"氛围变化: {old_atm} → {new_atm}")
        
        if changes:
            logger.info(f"📊 [状态变化] {' | '.join(changes)}")

    def _ensure_state_structure(self, state: Dict) -> Dict:
        """确保状态结构完整"""
        default_state = {
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
        
        # 合并缺失的字段
        for key, value in default_state.items():
            if key not in state:
                state[key] = value
            elif isinstance(value, dict) and isinstance(state.get(key), dict):
                for sub_key, sub_value in value.items():
                    if sub_key not in state[key]:
                        state[key][sub_key] = sub_value
        
        # 兼容旧格式
        if "hp" in state and "player" not in state:
            state["player"] = {"hp": state.pop("hp"), "max_hp": 100}
        if "location" in state and "scene" not in state:
            state["scene"] = {"location": state.pop("location")}
        if isinstance(state.get("inventory"), list):
            old_inv = state["inventory"]
            state["inventory"] = {item: {"type": "item", "count": 1} for item in old_inv}
        if isinstance(state.get("world_time"), str):
            state["world_time"] = {"day": 1, "hour": 8, "minute": 0}
        
        return state

    # ==========================================
    # 记忆压缩任务
    # ==========================================

    def _task_recursive_summary(self, timeline_tag: str, session_id: str):
        """递归摘要任务"""
        msgs = self.db.get_unsummarized_messages(limit=5)
        if len(msgs) < 5:
            return

        logger.info(f"📝 [后台] 触发递归总结 (处理 5 条消息)...")
        raw_text = "\n".join([f"{m['role']}: {m['content']}" for m in msgs])

        # 世界观拓展检测
        try:
            expansion_prompt = (
                f"Analyze the following dialogue:\n{raw_text[:2000]}\n\n"
                "Identify ONE specific proper noun, event, or concept that needs external knowledge. "
                "Return ONLY the keyword. If nothing needs research, return 'NONE'."
            )
            
            keyword_raw = self.left_brain.generate([{"role": "user", "content": expansion_prompt}])
            keyword = keyword_raw.strip().replace('"', '').replace("'", "").split('\n')[0]
            
            if keyword and "NONE" not in keyword.upper() and len(keyword) < 30:
                logger.info(f"🌍 [世界观拓展] 触发爬虫: '{keyword}'")
                self.harvester.add_task(keyword, priority=5)
                
        except Exception as e:
            logger.error(f"❌ [世界观拓展] 失败: {e}")

        # 左脑压缩
        left_prompt = get_prompt("left_brain").format(text=raw_text, time=timeline_tag)
        draft = self.left_brain.generate([{"role": "user", "content": left_prompt}])

        # 右脑审核
        right_prompt = get_prompt("critic").format(draft=draft, original=raw_text)
        final_micro = self.right_brain.generate([{"role": "user", "content": right_prompt}])

        # 保存微观记忆
        self.db.add_memory_node(final_micro, "MICRO", timeline_tag)
        self.db.mark_messages_summarized([m['id'] for m in msgs])

        # 向量化
        vec_id = f"micro_{int(time.time())}_{uuid.uuid4().hex[:4]}"
        self.vec.add_memory(
            text=final_micro,
            metadata={
                "type": "episodic",
                "level": "MICRO",
                "timeline": timeline_tag,
                "session_id": session_id
            },
            memory_id=vec_id
        )
        logger.info(f"💾 [记忆存储] 微观总结已保存 | 预览: {final_micro[:50]}...")

        # 检查是否需要宏观总结
        micros = self.db.get_unmerged_micro_nodes(limit=10)
        if len(micros) >= 10:
            logger.info(f"📚 [后台] 触发宏观总结 (合并 10 条微观记忆)...")
            micro_text = "\n".join([f"[{m['timeline_tag']}] {m['summary_text']}" for m in micros])
            
            merge_prompt = get_prompt("right_brain_merge", 
                f"请将以下微观记忆合并成一段连贯的宏观叙述:\n{micro_text}")
            macro_summary = self.right_brain.generate([{"role": "user", "content": merge_prompt}])

            self.db.add_memory_node(macro_summary, "MACRO", micros[0]['timeline_tag'])
            self.db.mark_nodes_merged([m['id'] for m in micros])

            vec_id_macro = f"macro_{int(time.time())}_{uuid.uuid4().hex[:4]}"
            self.vec.add_memory(
                text=macro_summary,
                metadata={
                    "type": "episodic",
                    "level": "MACRO",
                    "session_id": session_id
                },
                memory_id=vec_id_macro
            )
            logger.info(f"📜 [记忆存储] 宏观总结已生成 | 预览: {macro_summary[:50]}...")

            # 史官记录
            self._task_historian(macro_summary)

    def _task_historian(self, macro_summary: str):
        """史官撰写章节"""
        logger.info("🖋️ [后台] 史官正在撰写章节...")
        
        historian_prompt = get_prompt("historian").format(macro_content=macro_summary)
        saga = self.historian.generate([{"role": "user", "content": historian_prompt}])
        
        self.db.save_saga_entry(saga)
        logger.info("✅ [史官] 章节已归档")

    # ==========================================
    # 社会学分析任务
    # ==========================================

    def _task_sociologist(self, user_input: str, narr_output: str):
        """社会学分析"""
        if len(narr_output) < 50:
            return
        
        try:
            prompt = get_prompt("sociologist").format(
                current_graph="{}",
                interaction=f"User: {user_input}\nAI: {narr_output}"
            )
            self.sociologist.generate([{"role": "user", "content": prompt}])
        except Exception as e:
            logger.debug(f"[社会学分析] {e}")

    # ==========================================
    # 图谱更新任务
    # ==========================================

    def _task_update_graph(self, user_input: str, narr_output: str):
        """图谱三元组提取"""
        text = f"User: {user_input}\nNarrator: {narr_output}"
        if len(text) < 100:
            return

        prompt = PROMPT_GRAPH_EXTRACTOR.format(text=text)

        try:
            raw = self.graph_extractor.generate([{"role": "user", "content": prompt}])
            data = self._clean_json(raw)
            
            if not data:
                return
            
            triplets = data.get("triplets", [])
            
            count = 0
            preview_rels = []
            
            for t in triplets:
                src = t.get("source")
                rel = t.get("relation")
                tgt = t.get("target")
                desc = t.get("desc", "")
                
                if src and rel and tgt:
                    self.graph.add_triplet(src, rel, tgt, desc)
                    count += 1
                    if len(preview_rels) < 3:
                        preview_rels.append(f"({src}--{rel}-->{tgt})")

            if count > 0:
                logger.info(f"🕸️ [图谱更新] 新增 {count} 条关系: {', '.join(preview_rels)}")
                
        except Exception as e:
            logger.error(f"❌ [图谱更新] 失败: {e}")

    # ==========================================
    # 主入口
    # ==========================================

    def run_background_tasks(self, user_input: str, narr_output: str, 
                             search_query: str, session_id: str):
        """
        运行所有后台任务
        """
        # 1. 状态更新（同步执行，获取时间标签）
        timeline_tag = self._task_status_update(user_input, narr_output)

        # 2. 并行执行其他任务
        tasks = [
            threading.Thread(
                target=self._task_recursive_summary,
                args=(timeline_tag, session_id),
                daemon=True
            ),
            threading.Thread(
                target=self._task_sociologist,
                args=(user_input, narr_output),
                daemon=True
            ),
            threading.Thread(
                target=self._task_update_graph,
                args=(user_input, narr_output),
                daemon=True
            )
        ]

        for t in tasks:
            t.start()
