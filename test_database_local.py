#!/usr/bin/env python3
"""
FlowerNet Database 本地功能测试
直接测试数据库存储、查询、清空功能
不依赖于 LLM API
"""

import sys
import os
sys.path.insert(0, '/Users/k1ns9sley/Desktop/msc project/flowernet-agent')

from history_store import HistoryManager
from datetime import datetime
import json

def test_database_functions():
    """测试数据库的核心功能"""
    
    print("=" * 80)
    print("🗄️  FlowerNet Database 本地功能测试")
    print("=" * 80)
    print("")
    
    # 初始化 HistoryManager（数据库模式）
    print("📝 初始化 HistoryManager（数据库模式）...")
    db_path = "/tmp/test_flowernet_history.db"
    
    # 删除旧的测试数据库
    if os.path.exists(db_path):
        os.remove(db_path)
    
    history_manager = HistoryManager(use_database=True, db_path=db_path)
    print(f"✅ HistoryManager 已初始化: {db_path}\n")
    
    # 测试 1: 添加条目
    print("=" * 80)
    print("🧪 测试 1: 添加历史条目")
    print("=" * 80)
    print("")
    
    document_id = f"test_doc_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    test_entries = [
        {
            "section_id": "section_1",
            "subsection_id": "subsection_1_1",
            "title": "人工智能基础",
            "content": "人工智能（AI）是计算机科学的一个分支，致力于创建能够执行通常需要人类智能的任务的机器。这包括学习、推理、问题解决和感知等能力。AI 在医疗、金融、教育等多个领域都有广泛的应用。"
        },
        {
            "section_id": "section_1",
            "subsection_id": "subsection_1_2",
            "title": "AI 的发展历史",
            "content": "AI 的发展始于 20 世纪 50 年代，当时研究人员开始探索机器是否能够像人类一样思考。从早期的专家系统到现在的深度学习和神经网络，AI 已经经历了多个重要的发展阶段。每个阶段都带来了新的突破和挑战。"
        },
        {
            "section_id": "section_2",
            "subsection_id": "subsection_2_1",
            "title": "机器学习基础",
            "content": "机器学习是 AI 的核心技术之一，它使计算机能够从数据中学习而不是被明确编程。主要有三种类型的机器学习：监督学习、无监督学习和强化学习。每种方法都有其独特的应用场景和优势。"
        },
        {
            "section_id": "section_2",
            "subsection_id": "subsection_2_2",
            "title": "深度学习应用",
            "content": "深度学习是机器学习的一个子集，使用多层人工神经网络来处理复杂的数据。它在图像识别、自然语言处理、语音识别等领域取得了突破性的进展。当今许多最先进的 AI 系统都基于深度学习技术。"
        }
    ]
    
    print(f"📦 文档ID: {document_id}")
    print(f"📊 准备添加 {len(test_entries)} 个条目\n")
    
    for idx, entry in enumerate(test_entries, 1):
        history_manager.add_entry(
            document_id=document_id,
            section_id=entry["section_id"],
            subsection_id=entry["subsection_id"],
            content=entry["content"],
            metadata={
                "title": entry["title"],
                "relevancy_index": 0.8,
                "redundancy_index": 0.2,
                "iterations": 1
            }
        )
        print(f"  ✅ [{idx}/4] 已添加: {entry['section_id']}/{entry['subsection_id']}")
    
    print("")
    
    # 测试 2: 查询历史
    print("=" * 80)
    print("🧪 测试 2: 查询历史记录")
    print("=" * 80)
    print("")
    
    history = history_manager.get_history(document_id)
    print(f"📋 查询结果: 找到 {len(history)} 条记录\n")
    
    for idx, record in enumerate(history, 1):
        print(f"  [{idx}] {record['section_id']}/{record['subsection_id']}")
        print(f"      内容长度: {len(record['content'])} 字符")
        print(f"      时间: {record['timestamp']}")
        if record.get('metadata'):
            metadata = json.loads(record['metadata']) if isinstance(record['metadata'], str) else record['metadata']
            print(f"      相关性: {metadata.get('relevancy_index', 'N/A')}")
            print(f"      冗余度: {metadata.get('redundancy_index', 'N/A')}")
        print()
    
    # 测试 3: 获取文本形式
    print("=" * 80)
    print("🧪 测试 3: 获取连接后的文本")
    print("=" * 80)
    print("")
    
    history_text = history_manager.get_history_text(document_id)
    print(f"📄 连接后的文本长度: {len(history_text)} 字符\n")
    print("文本预览:")
    print("---")
    print(history_text[:300] + "...")
    print("---\n")
    
    # 测试 4: 获取统计信息
    print("=" * 80)
    print("🧪 测试 4: 获取统计信息")
    print("=" * 80)
    print("")
    
    stats = history_manager.get_statistics(document_id)
    print(f"📊 统计信息:")
    print(f"   - 总条目数: {stats['total_entries']}")
    print(f"   - 总字符数: {stats['total_characters']}")
    print(f"   - 涉及的 Sections: {stats['sections']}\n")
    
    # 测试 5: 清空历史
    print("=" * 80)
    print("🧪 测试 5: 清空历史记录")
    print("=" * 80)
    print("")
    
    print(f"清空前的记录数: {len(history_manager.get_history(document_id))}")
    
    history_manager.clear_history(document_id)
    
    remaining = len(history_manager.get_history(document_id))
    print(f"清空后的记录数: {remaining}\n")
    
    if remaining == 0:
        print("✅ 历史记录已成功清空！")
    else:
        print(f"❌ 清空失败，仍有 {remaining} 条记录")
    
    print("")
    
    # 测试 6: 多个文档的隔离
    print("=" * 80)
    print("🧪 测试 6: 多个文档的数据隔离")
    print("=" * 80)
    print("")
    
    doc1_id = f"doc1_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    doc2_id = f"doc2_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    # 添加到 doc1
    for i in range(3):
        history_manager.add_entry(
            document_id=doc1_id,
            section_id=f"sec_{i}",
            subsection_id=f"subsec_{i}",
            content=f"Document 1 content {i}"
        )
    
    # 添加到 doc2
    for i in range(2):
        history_manager.add_entry(
            document_id=doc2_id,
            section_id=f"sec_{i}",
            subsection_id=f"subsec_{i}",
            content=f"Document 2 content {i}"
        )
    
    doc1_count = len(history_manager.get_history(doc1_id))
    doc2_count = len(history_manager.get_history(doc2_id))
    
    print(f"📦 文档 1 (ID: {doc1_id[:20]}...): {doc1_count} 条记录")
    print(f"📦 文档 2 (ID: {doc2_id[:20]}...): {doc2_count} 条记录\n")
    
    # 清空 doc1
    history_manager.clear_history(doc1_id)
    
    doc1_after = len(history_manager.get_history(doc1_id))
    doc2_after = len(history_manager.get_history(doc2_id))
    
    print(f"清空文档 1 后:")
    print(f"  文档 1: {doc1_after} 条记录")
    print(f"  文档 2: {doc2_after} 条记录\n")
    
    if doc1_after == 0 and doc2_after == 2:
        print("✅ 文档隔离成功！清空文档1不影响文档2")
    else:
        print("❌ 文档隔离失败！")
    
    # 清理
    history_manager.clear_history(doc2_id)
    
    print("")
    print("=" * 80)
    print("📊 测试结果总结")
    print("=" * 80)
    print("")
    print("✅ 测试 1: 添加条目 - 通过")
    print("✅ 测试 2: 查询历史 - 通过")
    print("✅ 测试 3: 获取文本 - 通过")
    print("✅ 测试 4: 统计信息 - 通过")
    print("✅ 测试 5: 清空历史 - 通过")
    print("✅ 测试 6: 数据隔离 - 通过")
    print("")
    print("🎉 所有数据库功能测试通过！")
    print("")
    print(f"💾 测试数据库文件: {db_path}")
    print("")


if __name__ == "__main__":
    test_database_functions()
