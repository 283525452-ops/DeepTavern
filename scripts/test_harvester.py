# scripts/test_harvester.py
import sys
import os
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.harvester.crawler import WebCrawler
from core.harvester.cleaner import LocalCleaner

def test_batch_pipeline():
    print("\n" + "="*50)
    print("🧪 测试: 多源聚合抓取流程")
    print("="*50)
    
    keyword = "黑神话悟空 第三章 剧情解析"
    crawler = WebCrawler()
    cleaner = LocalCleaner()
    
    # 1. 抓取
    print(f"1️⃣ 正在搜索并抓取 3 个网页: {keyword} ...")
    results = crawler.search_and_fetch(keyword, max_results=3)
    
    if not results:
        print("❌ 抓取失败")
        return

    print(f"📦 成功抓取 {len(results)} 个网页。")
    
    # 2. 构造数据
    batch_data = [{'source': r['domain'], 'text': r['content']} for r in results]
    
    # 3. 聚合
    print(f"2️⃣ 正在发送给 LLM 进行聚合总结 (输入总长: {sum(len(x['text']) for x in batch_data)} 字符)...")
    start = time.time()
    summary = cleaner.clean_batch(batch_data, keyword)
    end = time.time()
    
    print(f"\n⏱️ LLM 耗时: {end - start:.2f} 秒")
    
    if summary:
        print("\n✅ [深度百科条目]:")
        print("-" * 40)
        print(summary)
        print("-" * 40)
    else:
        print("❌ 聚合失败")

if __name__ == "__main__":
    test_batch_pipeline()
