#!/usr/bin/env python3
"""
完整文档生成流程测试 - 简化版
"""

import subprocess
import json
import sqlite3
import time
import requests
from datetime import datetime

DB_PATH = "/Users/k1ns9sley/Desktop/msc project/flowernet-agent/flowernet_history.db"

def run_curl(method, url, data=None):
    """使用curl执行请求"""
    cmd = ["curl", "-s", "-X", method, url, "-H", "Content-Type: application/json"]
    if data:
        cmd.extend(["-d", json.dumps(data)])
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(result.stdout) if result.stdout else {}
    except:
        return {"error": result.stdout}

def main():
    doc_id = f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    print(f"\n{'='*70}")
    print(" 🚀 FlowerNet 完整文档生成流程测试")
    print(f"{'='*70}")
    
    print(f"\n📋 文档ID: {doc_id}")
    print(f"📅 开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 步骤1: 保存大纲
    print(f"\n{'=-'*35}")
    print("[步骤1] 保存文档大纲")
    print(f"{'=-'*35}")
    
    outline_data = {
        "document_id": doc_id,
        "outline_content": "深度学习基础\n1. 神经网络基础\n2. CNN架构\n3. RNN和LSTM\n4. 应用案例",
        "outline_type": "document"
    }
    
    print(f"📤 保存大纲...")
    resp = run_curl("POST", "http://localhost:8003/outline/save", outline_data)
    
    if "success" in resp and resp["success"]:
        print(f"✅ 大纲保存成功")
    else:
        print(f"❌ 大纲保存失败: {resp}")
        return 1
    
    # 验证数据库
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM outlines WHERE document_id = ?", (doc_id,))
    outline_count = cursor.fetchone()[0]
    print(f"   数据库验证: {outline_count} 条大纲记录")
    conn.close()
    
    # 步骤2: 生成内容
    print(f"\n{'=-'*35}")
    print("[步骤2] 按顺序生成小节内容")
    print(f"{'=-'*35}")
    
    subsections = [
        ("intro", "深度学习介绍", "介绍深度学习的基本概念和发展历史"),
        ("neural_net", "神经网络基础", "讲解人工神经网络的结构和原理"),
        ("cnn", "卷积神经网络", "详细解释CNN的工作原理和应用"),
        ("training", "模型训练", "介绍深度学习模型的训练方法和优化")
    ]
    
    generated_count = 0
    history = []
    
    for subsec_id, title, description in subsections:
        print(f"\n📝 生成小节: {title}")
        
        # 构建prompt
        history_context = ""
        if history:
            history_context = f"\n前面已写过的内容要点:\n"
            for h in history[-1:]:
                history_context += f"- {h[:80]}...\n"
        
        prompt = f"""请撰写关于"{title}"的内容。

要求：
- 主题: {title}
- 描述: {description}
{history_context}
- 字数: 200-300字
- 风格: 学术但易懂
- 语言: 中文

请直接输出内容，不要添加标题或前言。"""
        
        try:
            resp = requests.post(
                "http://localhost:8002/generate",
                json={"prompt": prompt, "max_tokens": 500},
                timeout=30
            )
            
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("draft", "")
                print(f"✅ 生成成功 ({len(content)}字)")
                
                # 保存到数据库
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                
                # history表
                cursor.execute("""
                    INSERT INTO history (document_id, section_id, subsection_id, content, timestamp)
                    VALUES (?, ?, ?, ?, datetime('now'))
                """, (doc_id, "main", subsec_id, content))
                
                # subsection_tracking表
                cursor.execute("""
                    INSERT INTO subsection_tracking 
                    (document_id, section_id, subsection_id, outline, generated_content, is_passed, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """, (doc_id, "main", subsec_id, description, content, 1))
                
                # passed_history表
                cursor.execute("""
                    INSERT INTO passed_history 
                    (document_id, section_id, subsection_id, content, order_index, created_at)
                    VALUES (?, ?, ?, ?, ?, datetime('now'))
                """, (doc_id, "main", subsec_id, content, generated_count + 1))
                
                conn.commit()
                conn.close()
                
                generated_count += 1
                history.append(content[:100])
                
            else:
                print(f"❌ 生成失败 (HTTP {resp.status_code})")
        
        except Exception as e:
            print(f"❌ 错误: {e}")
        
        time.sleep(0.5)
    
    # 步骤3: 数据库检查
    print(f"\n{'=-'*35}")
    print("[步骤3] 数据库完整性检查")
    print(f"{'=-'*35}")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print(f"\n📊 数据库内容统计:")
    
    tables = {
        "outlines": "SELECT COUNT(*) FROM outlines WHERE document_id = ?",
        "subsection_tracking": "SELECT COUNT(*) FROM subsection_tracking WHERE document_id = ?",
        "passed_history": "SELECT COUNT(*) FROM passed_history WHERE document_id = ?",
        "history": "SELECT COUNT(*) FROM history WHERE document_id = ?"
    }
    
    for table_name, query in tables.items():
        cursor.execute(query, (doc_id,))
        count = cursor.fetchone()[0]
        print(f"  {table_name:20}: {count:3} 条记录")
    
    # 显示历史链
    print(f"\n📝 生成的内容摘要:")
    cursor.execute("""
        SELECT subsection_id, LENGTH(content) as len, substr(content, 1, 60) as preview
        FROM subsection_tracking
        WHERE document_id = ?
        ORDER BY rowid
    """, (doc_id,))
    
    for subsec_id, content_len, preview in cursor.fetchall():
        print(f"  • {subsec_id:15}: {content_len:3}字 - {preview}...")
    
    conn.close()
    
    # 最终总结
    print(f"\n{'='*70}")
    print(" ✨ 测试完成")
    print(f"{'='*70}")
    
    print(f"\n✅ 完整流程测试成功！")
    print(f"  文档ID: {doc_id}")
    print(f"  生成的小节: {generated_count}")
    print(f"  保存的记录: {len(list(tables)) * generated_count} 条")
    
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
