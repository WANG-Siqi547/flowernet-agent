"""
FlowerNet Outliner - 快速测试脚本
测试完整流程：生成大纲 → 生成 Content Prompts → History 管理
"""

import os
from outliner import FlowerNetOutliner
from database import HistoryManager


def test_outliner():
    """测试 Outliner 核心功能"""
    print("=" * 60)
    print("🧪 测试 FlowerNet Outliner")
    print("=" * 60)
    
    # 检查 API Key
    api_key = os.getenv('GOOGLE_API_KEY', '')
    if not api_key:
        print("❌ 错误: 请设置 GOOGLE_API_KEY 环境变量")
        print("   export GOOGLE_API_KEY='your_api_key_here'")
        return
    
    # 初始化
    outliner = FlowerNetOutliner(api_key=api_key)
    
    # 测试数据
    user_background = """
我是一名计算机科学硕士生，正在研究深度学习在图像识别中的应用。
我需要撰写一篇技术综述论文，面向有一定机器学习基础的读者。
    """.strip()
    
    user_requirements = """
需要一篇全面介绍卷积神经网络（CNN）的文章，包括：
1. CNN 的基本原理和发展历史
2. 核心组件（卷积层、池化层、全连接层）
3. 经典架构（LeNet、AlexNet、VGG、ResNet）
4. 实际应用案例
5. 未来发展趋势

要求内容专业、逻辑清晰、有具体例子。
    """.strip()
    
    # 生成完整大纲
    print("\n🔄 正在生成文档大纲...\n")
    result = outliner.generate_full_outline(
        user_background=user_background,
        user_requirements=user_requirements,
        max_sections=4,
        max_subsections_per_section=3
    )
    
    if not result["success"]:
        print(f"❌ 生成失败: {result.get('error')}")
        return
    
    # 显示结果
    print("\n" + "=" * 60)
    print(f"📄 文档标题: {result['document_title']}")
    print("=" * 60)
    
    print(f"\n📊 统计信息:")
    print(f"  - Sections: {len(result['structure']['sections'])}")
    print(f"  - 总段落数: {result['total_subsections']}")
    print(f"  - Prompt Tokens: {result['metadata'].get('prompt_tokens', 'N/A')}")
    print(f"  - Output Tokens: {result['metadata'].get('output_tokens', 'N/A')}")
    
    # 显示文档结构
    print(f"\n📝 文档结构:")
    for section in result['structure']['sections']:
        print(f"\n  {section['id']}: {section['title']}")
        for subsection in section['subsections']:
            print(f"    └─ {subsection['id']}: {subsection['title']}")
            print(f"       描述: {subsection['description'][:60]}...")
    
    # 显示 Content Prompts（前 2 个）
    print(f"\n📋 Content Prompts 示例（前 2 个）:")
    for i, prompt_info in enumerate(result['content_prompts'][:2], 1):
        print(f"\n  [{i}] {prompt_info['section_title']} > {prompt_info['subsection_title']}")
        print(f"      顺序: {prompt_info['order']}")
        print(f"      Prompt 长度: {len(prompt_info['content_prompt'])} 字符")
        print(f"      Prompt 预览:\n")
        print("      " + prompt_info['content_prompt'][:300].replace("\n", "\n      ") + "...")
    
    print("\n" + "=" * 60)
    print("✅ Outliner 测试完成！")
    print("=" * 60)


def test_history_manager():
    """测试 History Manager"""
    print("\n" + "=" * 60)
    print("🧪 测试 History Manager")
    print("=" * 60)
    
    # 内存模式
    print("\n📝 测试内存模式:")
    manager = HistoryManager(use_database=False)
    
    # 添加测试数据
    manager.add_entry(
        document_id="doc_test_001",
        section_id="section_1",
        subsection_id="subsection_1_1",
        content="这是第一段内容，介绍了 CNN 的历史背景。卷积神经网络起源于 1980 年代...",
        metadata={"tokens": 150, "passed": True}
    )
    
    manager.add_entry(
        document_id="doc_test_001",
        section_id="section_1",
        subsection_id="subsection_1_2",
        content="这是第二段内容，讲解了卷积层的工作原理。卷积操作通过滤波器提取特征...",
        metadata={"tokens": 200, "passed": True}
    )
    
    # 查询
    history = manager.get_history("doc_test_001")
    print(f"  - 获取到 {len(history)} 条 history")
    
    # 统计
    stats = manager.get_statistics("doc_test_001")
    print(f"  - 统计信息: {stats}")
    
    # 文本形式
    text = manager.get_history_text("doc_test_001")
    print(f"  - History 文本长度: {len(text)} 字符")
    
    # 清空
    manager.clear_history("doc_test_001")
    print(f"  - 清空后剩余: {len(manager.get_history('doc_test_001'))} 条")
    
    print("\n✅ History Manager 测试完成！")


if __name__ == "__main__":
    # 测试 Outliner
    test_outliner()
    
    # 测试 History Manager
    test_history_manager()
    
    print("\n🎉 所有测试完成！")
    print("\n提示: 如需启动 API 服务，运行:")
    print("  python main.py")
