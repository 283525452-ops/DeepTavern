# core/harvester/scheduler.py
import threading
import queue
import time
from core.harvester.crawler import WebCrawler
from core.harvester.cleaner import LocalCleaner
from core.database.vector_store import VectorStore
from core.utils.logger import logger

class KnowledgeHarvester(threading.Thread):
    def __init__(self):
        super().__init__()
        self.name = "HarvesterThread"
        self.daemon = True
        self.queue = queue.PriorityQueue()
        self.running = True
        
        self.crawler = WebCrawler()
        self.cleaner = LocalCleaner()
        self.vec = VectorStore(collection_name="long_term_memory")

        # 白名单/黑名单保持不变...
        self.whitelist = ["wikipedia.org", "baike.baidu.com", "zhihu.com", "gamersky.com", "ali213.net"]
        self.blacklist = ["csdn.net", "baidu.com/link", "weibo.com", "bilibili.com"]

    def add_task(self, keyword, priority=10):
        if keyword:
            logger.info(f"[Harvester] 📥 Added task: {keyword}")
            self.queue.put((priority, time.time(), keyword))

    def run(self):
        logger.info("[Harvester] Service Started (Batch Aggregation Mode).")
        while self.running:
            try:
                priority, _, keyword = self.queue.get(timeout=5)
                self._process_task_batch(keyword) # 改用 Batch 方法
                self.queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"[Harvester] Loop Error: {e}")
                time.sleep(5)

    def _process_task_batch(self, keyword):
        # 1. 爬取多条 (比如一次抓 4 个网页)
        raw_results = self.crawler.search_and_fetch(
            keyword, 
            whitelist=self.whitelist, 
            blacklist=self.blacklist,
            max_results=6  # 增加数量，喂饱 LLM
        )
        
        if not raw_results:
            return

        # 2. 准备数据
        contents_to_merge = []
        for res in raw_results:
            # 简单过滤太短的垃圾
            if len(res['content']) > 200:
                contents_to_merge.append({
                    'source': res['domain'],
                    'text': res['content']
                })

        if not contents_to_merge:
            logger.warning("[Harvester] No valid content to merge.")
            return

        # 3. 聚合清洗 (One Pass)
        logger.info(f"[Harvester] 🧠 Synthesizing {len(contents_to_merge)} pages for '{keyword}'...")
        final_summary = self.cleaner.clean_batch(contents_to_merge, keyword)

        if final_summary:
            # 4. 存入向量库 (只存这一条高质量的)
            mem_id = f"lore_{int(time.time())}_{hash(keyword) % 10000}"
            
            # 构造元数据，记录所有来源
            sources_str = ", ".join([c['source'] for c in contents_to_merge])
            
            self.vec.add_memory(
                text=final_summary, 
                metadata={
                    "type": "INTERNET_LORE", 
                    "keyword": keyword,
                    "sources": sources_str,
                    "timestamp": str(int(time.time())),
                    "quality": "high_batch" # 标记为高质量聚合
                }, 
                memory_id=mem_id
            )
            logger.info(f"[Harvester] ✅ Saved Deep Lore for '{keyword}' (Length: {len(final_summary)})")
        else:
            logger.warning("[Harvester] Batch summary failed.")
