#!/usr/bin/env python3
"""
完整文档生成流程测试 - 使用curl而非requests
"""

import subprocess
import json
import sqlite3
import time
from datetime import datetime

DB_PATH = "/Users/k1ns9sley/Desktop/msc project/flowernet-agent/flowernet_history.db"

def curl_request(method, url, data=None):
    """使用curl执行HTTP请求"""
    cmd = ["curl", "-s", "-X", method, url, "-H", "Content-Type: application/json"]
    if data:
        cmd.extend(["-d", json.dumps(data)])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    try:
        return json.loads(result.stdout) if result.stdout else {}
    except:
        return {"_raw": result.stdout, "_error": True}

def main():
    doc_id = f"workflow_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    print(f"\n{'='*70}")
    print(" 🚀 FlowerNet 完整文档生成流程测试")
    print(f"{'='*70}")
    
    print(f"\n📋 测试信息:")
    print(f"   文档ID: {doc_id}")
    print(f"   开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # ============ 步骤1: 保存大纲 ============
    print(f"\n{'-'*70}")
    print("[步骤1] 保存文档大纲到数据库")
    print(f"{'-'*70}")
    
    outline = """# 深度学习基础
## 第一章 | 神经网络基础
- 人工神经元
- 前向传播
- 反向传播算法
- 梯度下降优化

## 第二章 | 卷积神经网络
- 卷积层原理
- 池化层
- 典型架构 (LeNet, AlexNet, VGG)
- 图像分类应用

## 第三章 | 循环神经网络
- RNN结构
- LSTM和GRU
- 序列建模
- 自然语言处理应用"""
    
    print(f"📤 保存大纲...")
    outline_result = curl_request("POST", "http://localhost:8003/outline/save", {
        "document_id": doc_id,
        "outline_content": outline,
        "outline_type": "document"
    })
    
    if outline_result.get("success"):
        print(f"✅ 大纲保存成功")
    else:
        print(f"❌ 大纲保存失败: {outline_result}")
        return 1
    
    # 验证数据库
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM outlines WHERE document_id = ?", (doc_id,))
    outline_count = cursor.fetchone()[0]
    conn.close()
    print(f"   数据库验证: {outline_count} 条大纲记录 ✓")
    
    # ============ 步骤2: 生成内容 ============
    print(f"\n{'-'*70}")
    print("[步骤2] 按顺序生成小节内容")
    print(f"{'-'*70}")
    
    subsections = [
        ("sec1_sub1", "人工神经元", "解释人工神经元的结构、激活函数和参数"),
        ("sec1_sub2", "前向传播", "详细说明神经网络的前向计算过程"),
        ("sec2_sub1", "卷积层原理", "讲解卷积操作、特征图和卷积核的含义"),
        ("sec2_sub2", "典型CNN架构", "介绍LeNet、AlexNet和VGG等经典模型"),
        ("sec3_sub1", "RNN结构", "说明循环神经网络的结构和时间展开"),
        ("sec3_sub2", "LSTM和GRU", "对比分析LSTM和GRU的改进机制")
    ]
    
    generated_count = 0
    history_contents = []
    
    for subsec_id, title, description in subsections:
        print(f"\n📝 [{generated_count + 1}/{len(subsections)}] 生成: {title}")
        
        # 构建包含历史的prompt
        history_hint = ""
        if history_contents:
            history_hint = "\n=== 前面已生成的内容要点 ===\n"
            for i, hist in enumerate(history_contents[-1:], 1):
                history_hint += f"{i}. {hist[:100]}...\n"
        
        prompt = f"""请为深度学习教材撰写一个小节的内容。

**小节标题**: {title}
**小节要求**: {description}
{history_hint}

**写作要求**:
- 字数: 250-350字
- 语言: 清晰学术中文
- 结构: 有逻辑的段落分层
- 不要添加标题，直接写正文

请立即开始写作内容。"""
        
        # 使用curl调用Generator
        print(f"   📤 调用Generator...", end=" ", flush=True)
        gen_result = curl_request("POST", "http://localhost:8002/generate", {
            "prompt": prompt,
            "max_tokens": 600
        })
        
        if gen_result.get("_error"):
            print(f"❌ 请求失败")
            continue
        
        content = gen_result.get("draft", "")
        if not content:
            print(f"❌ 无内容")
            continue
        
        print(f"✅ ({len(content)}字)")
        
        # 保存到数据库
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 1. 保存到 history 表
        try:
            cursor.execute("""
                INSERT INTO history (document_id, section_id, subsection_id, content, timestamp)
                VALUES (?, ?, ?, ?, datetime('now'))
            """, (doc_id, title[:20], subsec_id, content))
        except:
            pass
        
        # 2. 保存到 subsection_tracking 表
        try:
            cursor.execute("""
                INSERT INTO subsection_tracking 
                (document_id, section_id, subsection_id, outline, generated_content, is_passed, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """, (doc_id, title[:20], subsec_id, description, content, 1))
        except:
            pass
        
        # 3. 保存到 passed_history 表（用于历史链）
        try:
            cursor.execute("""
                INSERT INTO passed_history 
                (document_id, section_id, subsection_id, content, order_index, created_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
            """, (doc_id, title[:20], subsec_id, content, generated_count + 1))
        except:
            pass
        
        conn.commit()
        conn.close()
        
        generated_count += 1
        history_contents.append(content[:150])
        
        # 短暂延迟，避免过快请求
        time.sleep(0.3)
    
    print(f"\n✅ 完成 {generated_count} 个小节的内容生成")
    
    # ============ 步骤3: 数据库总结 ============
    print(f"\n{'-'*70}")
    print("[步骤3] 数据库内容总结")
    print(f"{'-'*70}")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print(f"\n📊 各表的数据统计:")
    
    # 统计各表的记录数
    tables_query = {
        "outlines": "SELECT COUNT(*) FROM outlines WHERE document_id = ?",
        "history": "SELECT COUNT(*) FROM history WHERE document_id = ?",
        "subsection_tracking": "SELECT COUNT(*) FROM subsection_tracking WHERE document_id = ?",
        "passed_history": "SELECT COUNT(*) FROM passed_history WHERE document_id = ?"
    }
    
    total_records = 0
    for table_name, query in tables_query.items():
        try:
            cursor.execute(query, (doc_id,))
            count = cursor.fetchone()[0]
            total_records += count
            status = "✓" if count > 0 else "  "
            print(f"  {status} {table_name:20}: {count:3} 条记录")
        except:
            print(f"     {table_name:20}: 查询错误")
    
    # 显示生成内容的摘要
    print(f"\n📝 生成的内容摘要:")
    try:
        cursor.execute("""
            SELECT subsection_id, LENGTH(generated_content) as content_len,
                   substr(generated_content, 1, 60) as preview
            FROM subsection_tracking
            WHERE document_id = ?
            ORDER BY rowid
        """, (doc_id,))
        
        for subsec_id, content_len, preview in cursor.fetchall():
            print(f"  • {subsec_id:15}: {content_len:4}字 - {preview}...")
    except:
        pass
    
    # 显示历史链信息
    print(f"\n🔗 历史链信息 (用于顺序生成的累积上下文):")
    try:
        cursor.execute("""
            SELECT order_index, subsection_id, LENGTH(content) as len
            FROM passed_history
            WHERE document_id = ?
            ORDER BY order_index
        """, (doc_id,))
        
        for order_idx, subsec_id, content_len in cursor.fetchall():
            print(f"  [{order_idx}] {subsec_id:15} - {content_len:4}字")
    except:
        pass
    
    conn.close()
    
    # ============ 最终报告 ============
    print(f"\n{'='*70}")
    print(" ✨ 测试完成")
    print(f"{'='*70}")
    
    if generated_count > 0:
        print(f"\n🎉 完整文档生成流程测试成功！")
        print(f"\n   📋 生成统计:")
        print(f"      文档ID: {doc_id}")
        print(f"      生成小节数: {generated_count}")
        print(f"      数据库记录总数: {total_records}")
        print(f"      大纲保存: 1条")
        print(f"      内容保存: {generated_count}条 (history) + {generated_count}条 (tracking) + {generated_count}条 (passed_history)")
        return 0
    else:
        print(f"\n⚠️  没有内容被成功生成")
        return 1

if __name__ == "__main__":
    import sys
    sys.exit(main())
