import sys
import os
import uuid

# 定位项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

from core.database.vector_store import VectorStore

def fix_chroma():
    print("正在修复向量数据库索引...")
    
    # 1. 修复剧情记忆库 (Long Term Memory)
    try:
        print("检查剧情记忆库 (long_term_memory)...")
        mem_store = VectorStore(collection_name="long_term_memory")
        count = mem_store.collection.count()
        print(f"当前剧情记忆数量: {count}")
        
        if count == 0:
            print("⚠️ 发现空库，正在写入初始化锚点...")
            mem_store.add_memory(
                text="[System] Memory Initialized.",
                metadata={"type": "init", "timestamp": "0"},
                memory_id="init_anchor_001"
            )
            print("✅ 剧情库初始化完成。")
        else:
            print("✅ 剧情库正常。")
            
    except Exception as e:
        print(f"❌ 剧情库修复失败: {e}")

    # 2. 检查规则库 (Rules Memory)
    try:
        print("\n检查规则库 (rules_memory)...")
        rule_store = VectorStore(collection_name="rules_memory")
        count = rule_store.collection.count()
        print(f"当前规则数量: {count}")
        
        if count == 0:
            print("⚠️ 警告：规则库是空的！你之前的导入可能没成功保存？")
        else:
            print(f"✅ 规则库正常 (包含 {count} 条规则)。")
            
    except Exception as e:
        print(f"❌ 规则库检查失败: {e}")

    print("\n修复完成！请重启 DeepTavern。")

if __name__ == "__main__":
    fix_chroma()
