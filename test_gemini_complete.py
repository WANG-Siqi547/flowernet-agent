"""
完整测试脚本：使用免费的 Google Gemini API 测试 FlowerNet
测试从生成到验证到修改的完整流程
"""

import sys
import os

# 检查 API Key
def check_api_key():
    """检查 Google API Key 是否设置"""
    api_key = os.getenv('GOOGLE_API_KEY', '')
    
    if not api_key:
        print("\n" + "="*80)
        print("⚠️  未检测到 GOOGLE_API_KEY 环境变量！")
        print("="*80)
        print("\n请按以下步骤获取免费的 Google Gemini API Key：\n")
        print("1. 访问: https://aistudio.google.com/app/apikey")
        print("2. 使用 Google 账号登录")
        print("3. 点击 'Create API Key' 按钮")
        print("4. 复制生成的 API Key\n")
        print("5. 设置环境变量:")
        print("   export GOOGLE_API_KEY=\"你的API密钥\"\n")
        print("详细说明请查看: GEMINI_SETUP_GUIDE.md")
        print("="*80 + "\n")
        return False
    
    print(f"\n✅ GOOGLE_API_KEY 已设置: {api_key[:20]}...")
    return True


def test_complete_workflow():
    """测试完整的生成-验证-修改流程"""
    
    print("\n" + "="*80)
    print("🌸 FlowerNet 完整流程测试 (使用 Google Gemini 免费 API)")
    print("="*80)
    
    # 导入客户端
    try:
        from flowernet_client import FlowerNetClient
    except ImportError:
        print("\n❌ 找不到 flowernet_client.py")
        print("请确保在正确的目录下运行此脚本")
        return False
    
    # 初始化客户端
    print("\n📡 初始化 FlowerNet 客户端...")
    client = FlowerNetClient(
        generator_url="http://localhost:8002",
        verifier_url="http://localhost:8000",
        controller_url="http://localhost:8001"
    )
    
    # 检查服务状态
    print("\n🔍 检查服务状态...")
    
    try:
        health = client.health_check()
        generator_ok = health.get('generator', False)
        verifier_ok = health.get('verifier', False)
        controller_ok = health.get('controller', False)
    except Exception as e:
        print(f"\n❌ 健康检查失败: {e}")
        print("\n手动检查服务...")
        import requests
        
        try:
            requests.get("http://localhost:8002/", timeout=2)
            generator_ok = True
        except:
            generator_ok = False
            
        try:
            requests.get("http://localhost:8000/", timeout=2)
            verifier_ok = True
        except:
            verifier_ok = False
            
        try:
            requests.get("http://localhost:8001/", timeout=2)
            controller_ok = True
        except:
            controller_ok = False
    
    if not all([generator_ok, verifier_ok, controller_ok]):
        print("\n❌ 服务未全部启动！")
        print(f"Generator: {'✅' if generator_ok else '❌'}")
        print(f"Verifier:  {'✅' if verifier_ok else '❌'}")
        print(f"Controller: {'✅' if controller_ok else '❌'}")
        print("\n请先启动所有服务:")
        print("  pkill -f 'main.py'  # 停止旧服务")
        print("  python3 flowernet-verifier/main.py 8000 &")
        print("  python3 flowernet-controler/main.py 8001 &")
        print("  python3 flowernet-generator/main.py 8002 gemini &")
        return False
    
    print("✅ 所有服务在线！")
    
    # 测试案例
    print("\n" + "="*80)
    print("📝 测试案例 1: 生成 AI 基础概念")
    print("="*80)
    
    outline1 = "人工智能的基本概念"
    prompt1 = """请用200字以内介绍人工智能的基本概念。
要求：
1. 简洁明了
2. 涵盖核心定义
3. 提及主要应用领域
"""
    
    print(f"\n📋 大纲: {outline1}")
    print(f"📝 Prompt: {prompt1}")
    print("\n⏳ 开始生成...\n")
    
    result1 = client.generate_with_loop(
        outline=outline1,
        initial_prompt=prompt1,
        history=[],
        max_iterations=3,
        rel_threshold=0.5,  # 较宽松的相关性要求
        red_threshold=0.8   # 较宽松的冗余度要求
    )
    
    if result1['success']:
        print("✅ 生成成功！\n")
        print("-" * 80)
        print("📄 生成内容:")
        print("-" * 80)
        print(result1['draft'])
        print("-" * 80)
        print(f"\n📊 评估指标:")
        print(f"  - 迭代次数: {result1['iterations']}")
        print(f"  - 相关性指数: {result1['relevancy_index']:.3f} (阈值: 0.5)")
        print(f"  - 冗余度指数: {result1['redundancy_index']:.3f} (阈值: 0.8)")
        print(f"  - 验证通过: {'✅' if result1['passed'] else '❌'}")
    else:
        print(f"❌ 生成失败: {result1.get('error', 'Unknown error')}")
        return False
    
    # 测试案例 2: 带历史记录的生成
    print("\n" + "="*80)
    print("📝 测试案例 2: 生成机器学习概念（避免与之前内容重复）")
    print("="*80)
    
    outline2 = "机器学习的核心方法"
    prompt2 = """请用200字以内介绍机器学习的核心方法。
要求：
1. 避免重复前面提到的内容
2. 聚焦于具体的学习方法
3. 提供简单示例
"""
    
    print(f"\n📋 大纲: {outline2}")
    print(f"📝 Prompt: {prompt2}")
    print(f"📚 历史内容: [已有 1 段内容]")
    print("\n⏳ 开始生成...\n")
    
    result2 = client.generate_with_loop(
        outline=outline2,
        initial_prompt=prompt2,
        history=[result1['draft']],  # 传入历史内容
        max_iterations=3,
        rel_threshold=0.5,
        red_threshold=0.7  # 稍微严格一点避免重复
    )
    
    if result2['success']:
        print("✅ 生成成功！\n")
        print("-" * 80)
        print("📄 生成内容:")
        print("-" * 80)
        print(result2['draft'])
        print("-" * 80)
        print(f"\n📊 评估指标:")
        print(f"  - 迭代次数: {result2['iterations']}")
        print(f"  - 相关性指数: {result2['relevancy_index']:.3f} (阈值: 0.5)")
        print(f"  - 冗余度指数: {result2['redundancy_index']:.3f} (阈值: 0.7)")
        print(f"  - 验证通过: {'✅' if result2['passed'] else '❌'}")
    else:
        print(f"❌ 生成失败: {result2.get('error', 'Unknown error')}")
        return False
    
    # 显示完整流程说明
    print("\n" + "="*80)
    print("🎉 测试完成！FlowerNet 工作流程说明")
    print("="*80)
    
    print("""
完整流程:
┌─────────────────────────────────────────────────────────────┐
│  1️⃣  Generator (使用 Google Gemini 免费 API)               │
│      根据 prompt 生成初始 draft                             │
│      ↓                                                      │
│  2️⃣  Verifier (多维度验证)                                 │
│      - 相关性检测 (关键词 + 语义 + 主题一致性)              │
│      - 冗余度检测 (与历史内容的重叠度)                      │
│      ↓                                                      │
│  3️⃣  判定逻辑                                              │
│      ✅ 相关性 ≥ 阈值 且 冗余度 ≤ 阈值 → 通过，返回结果    │
│      ❌ 否则 → 转入 Controller                             │
│      ↓                                                      │
│  4️⃣  Controller (智能 Prompt 优化)                         │
│      - 分析具体问题 (相关性不足 or 冗余度过高)              │
│      - 生成针对性的优化建议                                 │
│      - 修改 Prompt                                          │
│      ↓                                                      │
│  5️⃣  回到 Generator 重新生成                               │
│      循环直到满足要求或达到最大迭代次数                     │
└─────────────────────────────────────────────────────────────┘

优势:
✅ 完全免费 - 使用 Google Gemini API (每天 1500 次请求)
✅ 自动优化 - 不满足要求时自动修改 Prompt 重试
✅ 质量保证 - 多维度验证确保内容质量
✅ 避免重复 - 与历史内容对比，避免冗余
✅ 灵活调整 - 可自定义相关性和冗余度阈值
""")
    
    print("\n💡 参数调优建议:")
    print("  - 快速模式: rel_threshold=0.3, red_threshold=0.9, max_iterations=1")
    print("  - 标准模式: rel_threshold=0.5, red_threshold=0.7, max_iterations=3")
    print("  - 质量模式: rel_threshold=0.7, red_threshold=0.5, max_iterations=5")
    
    print("\n📚 详细文档:")
    print("  - 快速开始: FLOWERNET_GUIDE.md")
    print("  - Gemini 配置: GEMINI_SETUP_GUIDE.md")
    print("  - 完整文档: README_FLOWERNET.md")
    
    return True


if __name__ == "__main__":
    # 检查 API Key
    if not check_api_key():
        print("\n⛔ 测试中止：请先设置 GOOGLE_API_KEY")
        sys.exit(1)
    
    # 运行测试
    success = test_complete_workflow()
    
    if success:
        print("\n" + "="*80)
        print("✅ 所有测试通过！FlowerNet 系统运行正常！")
        print("="*80 + "\n")
        sys.exit(0)
    else:
        print("\n" + "="*80)
        print("❌ 测试失败，请检查错误信息")
        print("="*80 + "\n")
        sys.exit(1)
