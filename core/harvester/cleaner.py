# core/harvester/cleaner.py
from core.llm.local_direct import LocalDirectLLM
from core.llm.api_client import APILLM
from config.settings import MODEL_CONFIG
from core.utils.logger import logger

# [新增] 聚合总结的 Prompt
PROMPT_BATCH_SUMMARY = """你是一个专业的知识库编辑。
你需要根据以下 {count} 篇关于"{keyword}"的网页内容，撰写一份详尽的“深度百科条目”。

【来源列表】
{context_str}

【任务要求】
1. **综合统合**：将不同来源的信息拼凑在一起，去除重复内容，解决冲突。
2. **深度挖掘**：保留所有细节（如具体数值、步骤、剧情转折、评价）。
3. **结构清晰**：使用 Markdown 格式，包含一级标题、二级标题和列表。
4. **客观中立**：像维基百科一样写作。
5. **篇幅不限**：内容越长越好，越详细越好，目标字数 1500+ 字。
6. 请先在脑海中梳理所有线索，然后一步步构建这篇报告。
【深度百科条目】
"""

class LocalCleaner:
    def __init__(self):
        seeker_conf = MODEL_CONFIG.get("seeker", {})
        if not seeker_conf:
            seeker_conf = {"model": "qwen2.5:7b", "provider": "local"}
        
        logger.info(f"[Cleaner] Initializing Seeker LLM: {seeker_conf.get('model')}")
        
        model_path = str(seeker_conf.get("model", "")).lower()
        if model_path.endswith(".gguf"):
            self.llm = LocalDirectLLM(config=seeker_conf)
        else:
            self.llm = APILLM(seeker_conf)

    def clean_batch(self, contents_list, keyword):
        """
        [新增] 批量清洗聚合方法
        :param contents_list: list of dict [{'source': 'url', 'text': '...'}, ...]
        """
        if not contents_list:
            return None

        # 1. 拼接上下文
        context_parts = []
        total_chars = 0
        
        for i, item in enumerate(contents_list):
            # 每个来源截取前 6000 字，防止单个网页太长撑爆显存
            text_segment = item['text'][:6000]
            source_tag = f"=== 来源 {i+1}: {item['source']} ==="
            context_parts.append(f"{source_tag}\n{text_segment}\n")
            total_chars += len(text_segment)

        full_context = "\n".join(context_parts)
        
        # 安全截断：如果总长超过 25000 字（约 20k tokens），强行截断，防止 OOM
        # Qwen2.5 支持 32k，留 7k 给输出和 Prompt
        if len(full_context) > 250000:
            full_context = full_context[:250000] + "\n...(截断)..."

        logger.info(f"[Cleaner] Aggregating {len(contents_list)} sources (Total {len(full_context)} chars)...")

        prompt = PROMPT_BATCH_SUMMARY.format(
            count=len(contents_list),
            keyword=keyword,
            context_str=full_context
        )

        try:
            result = self.llm.generate([{"role": "user", "content": prompt}])
            if not result or "NULL" in result:
                return None
            return result
        except Exception as e:
            logger.error(f"[Cleaner] Batch LLM Error: {e}")
            return None

    # 保留旧的单条清洗方法，以备不时之需
    def clean(self, raw_text, keyword):
        # ... (保持原样，或者直接删掉也行)
        pass
