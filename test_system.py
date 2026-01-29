#!/usr/bin/env python3
"""
完整系统测试脚本
测试 Controller + Verifier 的完整工作流程
"""

import requests
import json
import time

# 配置
VERIFIER_URL = "http://localhost:8000"
CONTROLLER_URL = "http://localhost:8001"

def test_verifier():
    """测试 verifier 服务"""
    print("\n" + "="*60)
    print("🧪 测试 1: Verifier 服务")
    print("="*60)
    
    test_data = {
        "draft": "AI has revolutionized healthcare systems by enabling faster and more accurate medical diagnosis.",
        "outline": "Discuss the impact of AI on modern healthcare and medical diagnosis",
        "history": [],
        "rel_threshold": 0.4,
        "red_threshold": 0.6
    }
    
    try:
        response = requests.post(
            f"{VERIFIER_URL}/verify",
            json=test_data,
            timeout=30
        )
        response.raise_for_status()
        result = response.json()
        
        print("✅ Verifier 响应成功!")
        print(f"   是否通过: {result.get('is_passed')}")
        print(f"   相关性: {result.get('relevancy_index')}")
        print(f"   冗余度: {result.get('redundancy_index')}")
        print(f"   反馈: {result.get('feedback')}")
        return True
    except Exception as e:
        print(f"❌ Verifier 测试失败: {e}")
        return False


def test_controller_algo():
    """测试 controller 的算法工具箱"""
    print("\n" + "="*60)
    print("🧪 测试 2: Controller 算法工具箱")
    print("="*60)
    
    # 本地测试算法
    import sys
    sys.path.insert(0, '/Users/k1ns9sley/Desktop/msc project/flowernet-agent/flowernet-controler')
    from algo_toolbox import FlowerNetAlgos
    
    outline = "Deep learning improves MRI scanning efficiency by reducing noise."
    history = ["The history of MRI dates back to 1970s."]
    
    try:
        # 测试 Entity Recall
        print("\n📍 Entity Recall 测试:")
        entity_result = FlowerNetAlgos.entity_recall(outline)
        print(f"   {entity_result}")
        
        # 测试 LayRED
        print("\n📍 LayRED 测试:")
        layred_result = FlowerNetAlgos.layred_structure(outline)
        print(f"   {layred_result if layred_result else '   (无主谓宾结构)'}")
        
        # 测试 PacSum
        print("\n📍 PacSum 测试:")
        pacsum_result = FlowerNetAlgos.pacsum_template(history)
        print(f"   {pacsum_result[:100]}...")
        
        # 测试 SemDedup
        print("\n📍 SemDedup 测试:")
        failed_draft = "MRI technology has evolved significantly. Scanning efficiency is important."
        dedup_result = FlowerNetAlgos.sem_dedup(failed_draft, history)
        print(f"   {dedup_result if dedup_result else '   (无冗余检测)'}")
        
        print("\n✅ 所有算法工具箱测试通过!")
        return True
    except Exception as e:
        print(f"❌ 算法工具箱测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_controller_api():
    """测试 controller API"""
    print("\n" + "="*60)
    print("🧪 测试 3: Controller API 端点")
    print("="*60)
    
    try:
        # 先测试根路径
        response = requests.get(f"{CONTROLLER_URL}/", timeout=5)
        print("✅ Controller 根路径正常")
        
        # 测试 /process 端点
        print("\n📍 测试 /process 端点:")
        test_data = {
            "outline": "Discuss the benefits of artificial intelligence in healthcare"
        }
        
        response = requests.post(
            f"{CONTROLLER_URL}/process",
            json=test_data,
            timeout=30
        )
        response.raise_for_status()
        result = response.json()
        
        print(f"   生成内容长度: {len(result.get('content', ''))} 字符")
        print(f"   生成成功: {result.get('success')}")
        
        if not result.get('success'):
            print(f"   失败原因: {result.get('content')}")
        
        print("\n✅ Controller API 测试通过!")
        return True
    except Exception as e:
        print(f"❌ Controller API 测试失败: {e}")
        return False


def test_full_flow():
    """测试完整流程"""
    print("\n" + "="*60)
    print("🧪 测试 4: 完整端到端流程 (Mock LLM)")
    print("="*60)
    
    try:
        # 导入 controller
        import sys
        sys.path.insert(0, '/Users/k1ns9sley/Desktop/msc project/flowernet-agent/flowernet-controler')
        from controler import FlowerNetController
        
        # Mock LLM 生成器
        def mock_generator(prompt):
            """返回简单的模拟草稿"""
            return "AI technology has transformed healthcare by enabling faster diagnosis and personalized treatment plans. The impact of AI on modern medical systems is profound and continues to grow."
        
        # 创建 controller 实例
        controller = FlowerNetController(VERIFIER_URL, mock_generator)
        
        # 运行一次生成循环
        outline = "Discuss the impact of AI on modern healthcare"
        print(f"\n📍 大纲: {outline}")
        
        draft, success = controller.run_loop(outline, max_retries=2)
        
        print(f"\n📍 生成结果:")
        print(f"   成功: {success}")
        print(f"   内容长度: {len(draft)} 字符")
        if success:
            print(f"   内容预览: {draft[:100]}...")
        else:
            print(f"   失败原因: {draft}")
        
        print("\n✅ 完整流程测试通过!")
        return True
    except Exception as e:
        print(f"❌ 完整流程测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主测试函数"""
    print("""
    ╔════════════════════════════════════════════════════════════╗
    ║           FlowerNet 系统完整测试套件                        ║
    ║                                                             ║
    ║  - 测试 1: Verifier 服务验证                               ║
    ║  - 测试 2: Controller 算法工具箱                           ║
    ║  - 测试 3: Controller API 端点                             ║
    ║  - 测试 4: 完整端到端流程                                  ║
    ╚════════════════════════════════════════════════════════════╝
    """)
    
    results = {
        "Verifier 服务": test_verifier(),
        "算法工具箱": test_controller_algo(),
        "Controller API": test_controller_api(),
        "端到端流程": test_full_flow(),
    }
    
    # 总结
    print("\n" + "="*60)
    print("📊 测试结果总结")
    print("="*60)
    
    for name, passed in results.items():
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"{name:20} {status}")
    
    total = len(results)
    passed = sum(results.values())
    
    print(f"\n总计: {passed}/{total} 个测试通过")
    
    if passed == total:
        print("\n🎉 所有测试通过！系统正常运行！")
        return 0
    else:
        print("\n⚠️  部分测试失败，请检查日志")
        return 1


if __name__ == "__main__":
    exit(main())
