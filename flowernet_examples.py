#!/usr/bin/env python3
"""
FlowerNet 快速示例脚本
展示如何使用 FlowerNet 系统的各种功能
"""

from flowernet_client import FlowerNetClient, FlowerNetDocumentGenerator
import time


def example_simple_generation():
    """例子 1: 简单生成（不验证）"""
    print("\n" + "="*60)
    print("例子 1: 简单生成")
    print("="*60)
    
    client = FlowerNetClient(verbose=True)
    
    result = client.generate(
        prompt="请简要介绍深度学习的基本原理。",
        max_tokens=500
    )
    
    if result.get("success"):
        print(f"\n✅ 生成成功")
        print(f"内容:\n{result['draft']}")
    else:
        print(f"\n❌ 生成失败: {result.get('error')}")


def example_verify():
    """例子 2: 验证内容"""
    print("\n" + "="*60)
    print("例子 2: 验证内容")
    print("="*60)
    
    client = FlowerNetClient(verbose=True)
    
    draft = "深度学习是机器学习的一个子领域，它使用具有多层的神经网络（称为深度神经网络）来学习数据的表示。"
    outline = "介绍深度学习的基本原理"
    history = ["机器学习是人工智能的重要分支。"]
    
    result = client.verify(
        draft=draft,
        outline=outline,
        history=history,
        rel_threshold=0.5,
        red_threshold=0.7
    )
    
    print(f"\n📊 验证结果:")
    print(f"  相关性: {result.get('relevancy_index', 0):.4f}")
    print(f"  冗余度: {result.get('redundancy_index', 0):.4f}")
    print(f"  验证: {'✅ 通过' if result.get('is_passed') else '❌ 未通过'}")
    print(f"  反馈: {result.get('feedback', '')}")


def example_full_loop():
    """例子 3: 完整循环（生成 -> 验证 -> 修改）"""
    print("\n" + "="*60)
    print("例子 3: 完整循环 - 生成一个段落")
    print("="*60)
    
    client = FlowerNetClient(verbose=True)
    
    result = client.generate_with_loop(
        outline="介绍神经网络的基本结构",
        initial_prompt="""
请详细介绍神经网络的基本结构，包括：
1. 神经元和连接
2. 层的概念
3. 前向传播和反向传播
4. 激活函数的作用

要求：
- 长度 300-500 字
- 逻辑清晰
- 包含具体例子
""",
        history=[],
        max_iterations=3,
        rel_threshold=0.5,
        red_threshold=0.7
    )
    
    if result.get("success"):
        print(f"\n✅ 生成成功")
        print(f"迭代次数: {result['iterations']}")
        print(f"相关性: {result['verification']['relevancy']:.4f}")
        print(f"冗余度: {result['verification']['redundancy']:.4f}")
        print(f"\n生成的内容:\n{result['draft']}")
    else:
        print(f"\n❌ 生成失败: {result.get('error')}")


def example_document_generation():
    """例子 4: 生成完整文档"""
    print("\n" + "="*60)
    print("例子 4: 生成完整文档（多个段落）")
    print("="*60)
    
    client = FlowerNetClient(verbose=True)
    doc_gen = FlowerNetDocumentGenerator(client)
    
    document = doc_gen.generate_document(
        title="人工智能基础教程",
        outlines=[
            "人工智能的定义和发展历史",
            "机器学习的基本概念",
            "深度学习和神经网络",
            "自然语言处理应用",
            "计算机视觉和目标检测"
        ],
        system_prompt="使用学术但容易理解的语言，面向大学生读者",
        rel_threshold=0.5,
        red_threshold=0.7,
        max_iterations=2
    )
    
    print(f"\n📊 文档生成统计:")
    print(f"  标题: {document['title']}")
    print(f"  总段落数: {len(document['sections'])}")
    print(f"  成功段落: {document['success_count']}")
    print(f"  总迭代次数: {document['total_iterations']}")
    
    # 显示第一个段落
    if document['sections']:
        first_section = document['sections'][0]
        print(f"\n📝 第一段落示例:")
        print(f"  主题: {first_section['outline']}")
        print(f"  迭代数: {first_section['iterations']}")
        print(f"  内容: {first_section['content'][:100]}...")


def example_health_check():
    """例子 5: 检查服务健康状态"""
    print("\n" + "="*60)
    print("例子 5: 检查服务健康状态")
    print("="*60)
    
    client = FlowerNetClient(verbose=False)
    
    print("\n🔍 检查服务状态...\n")
    status = client.health_check()
    
    for service, online in status.items():
        status_str = "✅ 在线" if online else "❌ 离线"
        print(f"  {service:15} {status_str}")
    
    all_online = all(status.values())
    if all_online:
        print("\n✅ 所有服务正常！")
    else:
        print("\n❌ 部分服务离线，请启动它们")
        print("\n快速启动所有服务:")
        print("  ./start-flowernet.sh")


def example_batch_generation():
    """例子 6: 批量生成多个主题的内容"""
    print("\n" + "="*60)
    print("例子 6: 批量生成")
    print("="*60)
    
    client = FlowerNetClient(verbose=False)
    
    topics = [
        "什么是计算机科学？",
        "编程语言的分类",
        "软件工程的最佳实践"
    ]
    
    print(f"\n🔄 为 {len(topics)} 个主题生成内容...\n")
    
    results = []
    for idx, topic in enumerate(topics, 1):
        print(f"[{idx}/{len(topics)}] 生成: {topic}")
        
        result = client.generate_with_loop(
            outline=topic,
            initial_prompt=f"请写一段关于'{topic}'的内容。",
            history=results,  # 使用前面生成的内容作为历史
            max_iterations=2,
            rel_threshold=0.4,  # 降低阈值以加速生成
            red_threshold=0.8
        )
        
        if result.get("success"):
            results.append(result['draft'])
            print(f"  ✅ 成功 (迭代: {result['iterations']})\n")
        else:
            print(f"  ❌ 失败\n")
    
    print(f"\n✅ 完成！共生成 {len(results)} 篇内容")


def example_custom_parameters():
    """例子 7: 使用自定义参数"""
    print("\n" + "="*60)
    print("例子 7: 自定义参数控制")
    print("="*60)
    
    client = FlowerNetClient(verbose=True)
    
    # 参数说明
    print("\n📋 参数说明:")
    print("  rel_threshold: 相关性阈值 (0-1)")
    print("    - 0.3-0.4: 宽松，快速生成")
    print("    - 0.5-0.6: 标准，推荐")
    print("    - 0.7-0.8: 严格，高质量")
    print()
    print("  red_threshold: 冗余度阈值 (0-1)")
    print("    - 0.5-0.6: 严格去重")
    print("    - 0.7-0.8: 标准，推荐")
    print("    - 0.9+: 宽松，允许重复")
    print()
    print("  max_iterations: 最大迭代次数")
    print("    - 1-2: 快速模式")
    print("    - 3-5: 平衡模式（推荐）")
    print("    - 6+: 高质量模式")
    
    # 执行三种不同的配置
    configs = [
        {
            "name": "快速模式",
            "rel": 0.4,
            "red": 0.8,
            "iter": 1
        },
        {
            "name": "平衡模式",
            "rel": 0.5,
            "red": 0.7,
            "iter": 3
        },
        {
            "name": "高质量模式",
            "rel": 0.7,
            "red": 0.6,
            "iter": 5
        }
    ]
    
    outline = "介绍 Python 编程语言"
    
    for config in configs:
        print(f"\n\n测试 {config['name']}:")
        print(f"  相关性: {config['rel']}")
        print(f"  冗余度: {config['red']}")
        print(f"  最大迭代: {config['iter']}")
        
        start = time.time()
        result = client.generate_with_loop(
            outline=outline,
            initial_prompt=f"请介绍 {outline}。",
            max_iterations=config['iter'],
            rel_threshold=config['rel'],
            red_threshold=config['red']
        )
        elapsed = time.time() - start
        
        if result.get("success"):
            print(f"  ✅ 成功 (耗时: {elapsed:.1f}s, 迭代: {result['iterations']})")
        else:
            print(f"  ❌ 失败 (耗时: {elapsed:.1f}s)")


def main():
    """主函数"""
    print("\n" + "🌸" * 30)
    print("\nFlowerNet 快速示例脚本")
    print("\n" + "🌸" * 30)
    
    examples = [
        ("简单生成", example_simple_generation),
        ("验证内容", example_verify),
        ("完整循环", example_full_loop),
        ("生成文档", example_document_generation),
        ("健康检查", example_health_check),
        ("批量生成", example_batch_generation),
        ("自定义参数", example_custom_parameters),
    ]
    
    print("\n📋 可用的示例:\n")
    for idx, (name, _) in enumerate(examples, 1):
        print(f"  {idx}. {name}")
    
    print("\n🚀 使用方法:")
    print("  python3 flowernet_examples.py         # 运行所有示例")
    print("  python3 flowernet_examples.py 1       # 运行示例 1")
    print("  python3 flowernet_examples.py 1 2 3   # 运行示例 1, 2, 3")
    
    # 解析命令行参数
    import sys
    if len(sys.argv) > 1:
        # 运行指定的示例
        for arg in sys.argv[1:]:
            try:
                idx = int(arg) - 1
                if 0 <= idx < len(examples):
                    name, func = examples[idx]
                    print(f"\n\n执行: {name}\n")
                    func()
            except (ValueError, IndexError):
                print(f"❌ 无效的示例号: {arg}")
    else:
        # 运行所有示例
        for name, func in examples:
            try:
                func()
                time.sleep(1)  # 示例间延迟
            except Exception as e:
                print(f"\n❌ 示例执行出错: {str(e)}")
                import traceback
                traceback.print_exc()
    
    print("\n\n" + "="*60)
    print("✅ 示例执行完成！")
    print("="*60)
    print("\n📖 更多信息，请参考:")
    print("  - README_FLOWERNET.md    # 完整文档")
    print("  - CONFIG_GUIDE.md        # 配置指南")
    print("  - flowernet_client.py    # 客户端源码")


if __name__ == "__main__":
    main()
