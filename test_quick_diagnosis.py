#!/usr/bin/env python3
"""
快速诊断脚本 - 逐步检测和修复问题
"""

import requests
import json
import time

def test_verifier_calculation():
    """测试 Verifier 的相关性计算"""
    print("\n🔍 Verifier 相关性计算诊断")
    print("="*60)
    
    # 测试用例：完全相同的文本应该有高相关性
    test_cases = [
        {
            "name": "完全相同的文本",
            "draft": "AI在医疗中的应用非常重要",
            "outline": "AI在医疗中的应用",
            "expect_rel_high": True
        },
        {
            "name": "关键词完全匹配",
            "draft": "AI和医疗应用的结合",
            "outline": "AI在医疗中的应用",
            "expect_rel_high": True
        },
        {
            "name": "完全不相关",
            "draft": "今天天气很好",
            "outline": "AI在医疗中的应用",
            "expect_rel_high": False
        }
    ]
    
    for test in test_cases:
        try:
            resp = requests.post(
                "http://localhost:8000/verify",
                json={
                    "draft": test["draft"],
                    "outline": test["outline"],
                    "history": [],
                    "rel_threshold": 0.50,  # 较低阈值便于观察
                    "red_threshold": 0.50,
                },
                timeout=10
            )
            
            if resp.status_code == 200:
                data = resp.json()
                rel = data.get("relevancy_index", 0)
                status = "✅" if (rel > 0.6) == test["expect_rel_high"] else "⚠️"
                
                print(f"\n{status} {test['name']}")
                print(f"   Draft: {test['draft']}")
                print(f"   Outline: {test['outline']}")
                print(f"   Relevancy: {rel:.4f} (expect_high: {test['expect_rel_high']})")
            else:
                print(f"\n❌ {test['name']}: HTTP {resp.status_code}")
        except Exception as e:
            print(f"\n❌ {test['name']}: {str(e)}")


def test_outliner_simple():
    """测试 Outliner 的最简单功能"""
    print("\n\n🔍 Outliner 简单生成测试")
    print("="*60)
    
    try:
        resp = requests.post(
            "http://localhost:8003/generate-outline",
            json={
                "topic": "AI",
                "num_sections": 2,
                "num_subsections": 1,
                "context": "Brief"
            },
            timeout=60
        )
        
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                print("\n✅ Outliner 可以生成大纲")
                print(f"   结构: {json.dumps(data.get('structure'), ensure_ascii=False)[:200]}...")
                return True
            else:
                print(f"\n❌ Outliner 生成失败: {data.get('error', 'unknown')[:100]}")
                return False
        else:
            print(f"\n❌ HTTP {resp.status_code}")
            return False
    except Exception as e:
        print(f"\n❌ 连接失败: {str(e)}")
        return False


def test_generator_simple():
    """测试 Generator 的最简单功能"""
    print("\n\n🔍 Generator 简单生成测试")
    print("="*60)
    
    try:
        resp = requests.post(
            "http://localhost:8002/generate",
            json={
                "prompt": "Write a short paragraph about AI in healthcare.",
            },
            timeout=60
        )
        
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                draft = data.get("draft", "")
                print("\n✅ Generator 可以生成内容")
                print(f"   内容: {draft[:100]}..." if len(draft) > 100 else f"   内容: {draft}")
                return True
            else:
                print(f"\n❌ Generator 生成失败: {data.get('error', 'unknown')[:100]}")
                return False
        else:
            print(f"\n❌ HTTP {resp.status_code}")
            print(f"   Response: {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"\n❌ 连接失败: {str(e)}")
        return False


def main():
    print("""
╔════════════════════════════════════════════════════════════════╗
║           FlowerNet 快速问题诊断                              ║
╚════════════════════════════════════════════════════════════════╝
""")
    
    # 1. Verifier 诊断
    test_verifier_calculation()
    
    # 2. Outliner 诊断
    outliner_ok = test_outliner_simple()
    
    # 3. Generator 诊断
    generator_ok = test_generator_simple()
    
    # 总结
    print("\n\n" + "="*60)
    print("📋 诊断总结")
    print("="*60)
    
    if outliner_ok and generator_ok:
        print("\n✅ 所有基础功能正常")
        print("\n📝 下一步:")
        print("   1. 修复 Verifier 的相关性计算阈值")
        print("   2. 运行完整的生成流程测试")
    else:
        if not outliner_ok:
            print("\n❌ Outliner 有问题，需要检查日志")
        if not generator_ok:
            print("\n❌ Generator 有问题，需要检查日志")


if __name__ == "__main__":
    main()
