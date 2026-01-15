import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "rules_preset.db")

def check():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. 统计各分类数量
    print("=== 规则分类统计 ===")
    cursor.execute("SELECT category, COUNT(*) FROM rule_fragments GROUP BY category")
    for cat, count in cursor.fetchall():
        print(f"[{cat}]: {count} 条")
        
    # 2. 查看几条刚刚导入的内容 (检查清洗效果)
    print("\n=== 内容抽查 (前3条) ===")
    cursor.execute("SELECT category, scope_value, content FROM rule_fragments ORDER BY id DESC LIMIT 3")
    for row in cursor.fetchall():
        print(f"--- [{row[0]}] {row[1]} ---")
        # 只显示前100个字，看看有没有乱码或 {{setvar}}
        print(row[2][:100].replace("\n", " ") + "...") 
        
    conn.close()

if __name__ == "__main__":
    check()
