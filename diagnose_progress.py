#!/usr/bin/env python3
"""
诊断为什么进度显示 0% 及生成反复失败
"""
import sqlite3
import json
import os
from pathlib import Path

def diagnose_progress_issue():
    """诊断进度显示不更新的问题"""
    print("=" * 70)
    print("🔍 进度显示 0% 诊断")
    print("=" * 70)
    
    print("\n问题分析:")
    print("  1. 前端进度计算方式:")
    print("     progress = len(history) / total_subsections * 100")
    print("  2. history 来自 Outliner: /history/get?document_id=xx")
    print("  3. 进度 0% 意味着 history 列表为空")
    print()
    
    # 检查数据库
    db_path = "/Users/k1ns9sley/Desktop/msc project/flowernet-agent/flowernet_history.db"
    if not os.path.exists(db_path):
        print(f"❌ 数据库不存在: {db_path}")
        return
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 查看表结构
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        print(f"✅ 数据库表: {tables}")
        
        if "history" not in tables:
            print("❌ 找不到 'history' 表")
            return
        
        # 查看最新的记录
        cursor.execute("""
            SELECT document_id, COUNT(*) as count 
            FROM history 
            GROUP BY document_id 
            ORDER BY document_id DESC 
            LIMIT 5
        """)
        
        print("\n📊 最新文档的历史记录数:")
        recent_docs = cursor.fetchall()
        if not recent_docs:
            print("  ❌ 没有任何历史记录！这是问题原因 1")
        else:
            for doc_id, count in recent_docs:
                print(f"  - {doc_id}: {count} 条记录")
        
        # 检查最新的完整记录
        if recent_docs:
            latest_doc_id = recent_docs[0][0]
            cursor.execute("""
                SELECT id, document_id, section_id, subsection_id, 
                       LENGTH(content) as content_length, created_at 
                FROM history 
                WHERE document_id = ? 
                ORDER BY id DESC 
                LIMIT 3
            """, (latest_doc_id,))
            
            print(f"\n📋 文档 {latest_doc_id[:40]}... 的最新记录:")
            records = cursor.fetchall()
            for row in records:
                rec_id, doc, sec, subsec, content_len, created = row
                print(f"  [{rec_id}] {sec}/{subsec} - {content_len} 字节 - {created}")
        
        conn.close()
        
    except Exception as e:
        print(f"❌ 数据库查询错误: {e}")

def diagnose_verifier_timeout():
    """诊断 Verifier 超时的原因"""
    print("\n" + "=" * 70)
    print("🔍 Verifier 反复失败诊断")
    print("=" * 70)
    
    print("\n根本原因分析:")
    print("  症状：【检验异常】Verifier 调用失败，5s 后重试")
    print()
    print("  可能原因及排序:")
    print("  1. ⭐ Render Free Plan 冷启动超时 (30-60s)")
    print("     - 症状：首次请求超时，后续请求正常")
    print("     - 修复：等待 60s 后重试，或升级到 Starter plan")
    print()
    print("  2. ⭐ Verifier /verify 端点处理缓慢")
    print("     - 症状：consistent timeouts on all attempts")
    print("     - 可能原因：jieba 分词缓慢、ROUGE-L 计算耗时")
    print("     - 修复：优化 Verifier 的计算逻辑")
    print()
    print("  3. Verifier 依赖缺失或初始化失败")
    print("     - 检查 Render Dashboard → Verifier → Logs")
    print()
    print("  4. 网络问题 or Render 基础设施问题")
    print("     - Render 新加坡地区偶发问题")
    print()
    
    print("📊 当前超时配置:")
    print("  - Generator 调用 Verifier 超时: 90 秒")
    print("  - Verifier 内部重试: 3 次 (每次间隔 5s)")
    print("  - 总容忍时间: 90 + 3*5 = 105 秒")
    print()
    
    print("💡 立即可采取的行动:")
    print("  1. 检查 Render Logs 中是否有错误信息")
    print("  2. 设置更长的超时时间（目前 90s 可能不够）")
    print("  3. 增加 Verifier 重试次数")
    print("  4. 优化 Verifier 的 jieba 和 ROUGE 计算")

def diagnose_connection_interrupts():
    """诊断连接中断的原因"""
    print("\n" + "=" * 70)
    print("🔍 连接中断诊断")
    print("=" * 70)
    
    print("\n现象描述:")
    print("  本来一直失败 → 突然好起来 → 又突然连接中断")
    print()
    
    print("根本原因分析:")
    print()
    print("时间线：")
    print("  T0: Verifier Render 冷启动失败")
    print("     ├─ Generator → Verifier 调用超时")
    print("     └─ 导致整个生成卡住")
    print()
    print("  T1: Render 服务恢复（约 30-60s）")
    print("     ├─ /history/get 开始返回数据")
    print("     └─ Web 前端进度条开始更新（0% → N%）")
    print()
    print("  T2: 生成过程中 Verifier 再次超时")
    print("     ├─ Verifier 负载过高 or Render 资源卡顿")
    print("     └─ Web → Generator 的流式连接断开")
    print("        原因：HTTP 连接保活失败 or Render 503 响应")
    print()
    
    print("💡 解决方案：")
    print("  1. 增加 Verifier 的超时容限")
    print("     当前：90s，建议改为 180s")
    print()
    print("  2. 加入连接保活机制")
    print("     Web → 前端：每 30s 发一个心跳 SSE 消息")
    print("     Generator → Verifier：增加 retry 次数")
    print()
    print("  3. 降低 Verifier 计算复杂度")
    print("     - 缓存 jieba 分词结果")
    print("     - 优化 ROUGE-L 计算")
    print("     - 使用更快的 BM25 实现")
    print()
    print("  4. 考虑分阶段生成")
    print("     - 生成 → 验证（Render）")
    print("     - 改进 -> Controller（本地快速）")
    print("     - 这样 Verifier 压力小，超时风险降低")

if __name__ == "__main__":
    diagnose_progress_issue()
    diagnose_verifier_timeout()
    diagnose_connection_interrupts()
    
    print("\n" + "=" * 70)
    print("📝 建议的修复优先级")
    print("=" * 70)
    print("""
优先级 1（立即）：
  - 增加 Generator → Verifier 超时到 180s
  - 增加 Verifier 重试次数从 3 → 5

优先级 2（本周）：
  - 优化 Verifier jieba + ROUGE-L 性能
  - Web 前端增加心跳消息防断连

优先级 3（下周）：
  - 升级 Render Verifier 到 Starter plan
  - 考虑本地 Verifier sidecar 与 Render Verifier 动态切换
""")
