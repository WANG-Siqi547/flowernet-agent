#!/usr/bin/env python3
"""
FlowerNet 完整本地测试脚本
使用 Ollama qwen2.5 模型进行 Controller 触发率测试
"""

import requests
import json
import time
import sys
from typing import Dict, List, Any
from collections import defaultdict
import statistics

WEB_URL = "http://localhost:8010"
TIMEOUT = 600

def test_service_connectivity():
    """测试服务连接"""
    print("\n" + "="*60)
    print("🔍 步骤 1: 服务连接性测试")
    print("="*60)
    
    services = {
        "Web": "http://localhost:8010/",
        "Verifier": "http://localhost:8000/",
        "Controller": "http://localhost:8001/",
        "Generator": "http://localhost:8002/",
        "Outliner": "http://localhost:8003/",
    }
    
    all_ok = True
    for name, url in services.items():
        try:
            resp = requests.get(url, timeout=5)
            status = "✅" if resp.status_code == 200 else "⚠️"
            print(f"  {status} {name}: HTTP {resp.status_code}")
        except Exception as e:
            print(f"  ❌ {name}: {str(e)[:50]}")
            all_ok = False
    
    return all_ok


def test_ollama_model():
    """测试 Ollama 模型可用性"""
    print("\n" + "="*60)
    print("🔍 步骤 2: Ollama 模型检查")
    print("="*60)
    
    try:
        result = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "qwen2.5:1.5b",
                "prompt": "What is AI?",
                "stream": False,
            },
            timeout=30
        )
        
        if result.status_code == 200:
            print("  ✅ qwen2.5:1.5b 模型可用")
            return True
        else:
            print(f"  ❌ 模型异常: HTTP {result.status_code}")
            return False
    except Exception as e:
        print(f"  ❌ Ollama 连接失败: {str(e)}")
        return False


def run_verifier_test():
    """测试 Verifier 的 relevancy 和 redundancy 计算"""
    print("\n" + "="*60)
    print("🔍 步骤 3: Verifier 阈值计算测试")
    print("="*60)
    
    test_cases = [
        {
            "name": "高相关性，低冗余度",
            "draft": "AI技术在医学影像分析中的应用，通过深度学习算法可以快速识别病灶。",
            "outline": "AI在医疗诊断中的应用",
            "history": ["电子病历是现代医疗的基础设施。"],
            "expect_pass": True
        },
        {
            "name": "低相关性，高冗余度",
            "draft": "电子病历是现代医疗的基础设施。现代医疗需要电子化记录。",
            "outline": "AI在医疗诊断中的应用",
            "history": ["电子病历是现代医疗的基础设施。"],
            "expect_pass": False
        },
    ]
    
    results = []
    
    for test in test_cases:
        try:
            resp = requests.post(
                f"http://localhost:8000/verify",
                json={
                    "draft": test["draft"],
                    "outline": test["outline"],
                    "history": test["history"],
                    "rel_threshold": 0.80,
                    "red_threshold": 0.50,
                },
                timeout=30
            )
            
            if resp.status_code == 200:
                data = resp.json()
                passed = data.get("is_passed", False)
                status = "✅" if passed == test["expect_pass"] else "⚠️"
                
                print(f"\n  {status} {test['name']}")
                print(f"     相关性: {data.get('relevancy_index', 0):.4f}")
                print(f"     冗余度: {data.get('redundancy_index', 0):.4f}")
                print(f"     通过: {passed}")
                
                results.append({
                    "test": test["name"],
                    "rel": data.get('relevancy_index', 0),
                    "red": data.get('redundancy_index', 0),
                    "passed": passed,
                    "expected": test["expect_pass"]
                })
        except Exception as e:
            print(f"  ❌ {test['name']}: {str(e)}")
    
    return results


def run_generation_test():
    """运行完整的生成测试"""
    print("\n" + "="*60)
    print("🔍 步骤 4: 文档生成和 Controller 触发率测试")
    print("="*60)
    
    test_cases = [
        {
            "topic": "人工智能在医疗中的应用",
            "subtopics": [
                "AI 诊断系统",
                "医学影像分析",
                "个性化治疗",
                "数据隐私",
                "临床验证"
            ]
        },
        {
            "topic": "云计算架构基础",
            "subtopics": [
                "分布式系统",
                "资源管理",
                "负载均衡",
                "故障转移",
                "成本优化"
            ]
        }
    ]
    
    stats = {
        "total_subsections": 0,
        "controller_triggered": 0,
        "controller_success": 0,
        "controller_failed": 0,
        "rel_scores": [],
        "red_scores": [],
        "subsection_details": []
    }
    
    for i, config in enumerate(test_cases):
        print(f"\n  📄 生成文档 {i+1}: {config['topic']}")
        
        try:
            response = requests.post(
                f"{WEB_URL}/api/generate",
                json={
                    "topic": config["topic"],
                    "subtopics": config["subtopics"],
                    "document_id": f"test_{i}",
                },
                timeout=TIMEOUT
            )
            
            if response.status_code == 200:
                data = response.json()
                
                # 解析响应数据
                for section_id, section_data in data.items():
                    if isinstance(section_data, dict):
                        for subsection_id, subsection_data in section_data.items():
                            if isinstance(subsection_data, dict):
                                stats["total_subsections"] += 1
                                
                                # 检查 Controller 触发
                                if subsection_data.get("controller_triggered"):
                                    stats["controller_triggered"] += 1
                                    if subsection_data.get("success"):
                                        stats["controller_success"] += 1
                                    else:
                                        stats["controller_failed"] += 1
                                
                                # 收集得分
                                verification = subsection_data.get("verification", {})
                                rel = verification.get("relevancy_index")
                                red = verification.get("redundancy_index")
                                
                                if rel is not None:
                                    stats["rel_scores"].append(float(rel))
                                if red is not None:
                                    stats["red_scores"].append(float(red))
                                
                                # 记录详情
                                stats["subsection_details"].append({
                                    "section": section_id,
                                    "subsection": subsection_id,
                                    "controller_triggered": subsection_data.get("controller_triggered", False),
                                    "rel": rel,
                                    "red": red,
                                    "success": subsection_data.get("success", False),
                                    "iterations": subsection_data.get("iteration", 0),
                                })
                
                print(f"     ✅ 完成 ({len(section_data)} 个小节)")
            else:
                print(f"     ❌ HTTP {response.status_code}")
        
        except requests.Timeout:
            print(f"     ❌ 超时")
        except Exception as e:
            print(f"     ❌ 错误: {str(e)[:50]}")
        
        time.sleep(2)
    
    return stats


def print_analysis_report(gen_stats: Dict, verifier_results: List):
    """打印详细分析报告"""
    
    print("\n" + "="*60)
    print("📊 步骤 5: 详细分析报告")
    print("="*60)
    
    print("\n【Verifier 阈值测试结果】")
    for result in verifier_results:
        status = "✅" if result["passed"] == result["expected"] else "❌"
        print(f"  {status} {result['test']}")
        print(f"     相关性: {result['rel']:.4f} | 冗余度: {result['red']:.4f}")
    
    if gen_stats["total_subsections"] == 0:
        print("\n❌ 生成测试失败，无可用数据")
        return False
    
    trigger_rate = gen_stats["controller_triggered"] / gen_stats["total_subsections"] * 100
    
    print(f"\n【文档生成测试结果】")
    print(f"  总小节数: {gen_stats['total_subsections']}")
    print(f"  Controller 触发: {gen_stats['controller_triggered']}")
    print(f"  触发率: {trigger_rate:.1f}%")
    
    if gen_stats["controller_triggered"] > 0:
        success_rate = gen_stats["controller_success"] / gen_stats["controller_triggered"] * 100
        print(f"  改纲成功: {gen_stats['controller_success']}/{gen_stats['controller_triggered']} ({success_rate:.1f}%)")
        
        if gen_stats["controller_failed"] > 0:
            print(f"  改纲失败: {gen_stats['controller_failed']}")
    
    # 得分统计
    print(f"\n【得分统计】")
    if gen_stats["rel_scores"]:
        rel_avg = statistics.mean(gen_stats["rel_scores"])
        rel_min = min(gen_stats["rel_scores"])
        rel_max = max(gen_stats["rel_scores"])
        print(f"  相关性 (Relevancy):")
        print(f"    平均: {rel_avg:.4f} | 最小: {rel_min:.4f} | 最大: {rel_max:.4f}")
    
    if gen_stats["red_scores"]:
        red_avg = statistics.mean(gen_stats["red_scores"])
        red_min = min(gen_stats["red_scores"])
        red_max = max(gen_stats["red_scores"])
        print(f"  冗余度 (Redundancy):")
        print(f"    平均: {red_avg:.4f} | 最小: {red_min:.4f} | 最大: {red_max:.4f}")
    
    # 诊断建议
    print(f"\n【诊断和建议】")
    
    print(f"\n1️⃣  Controller 触发率诊断:")
    if trigger_rate < 30:
        print(f"   ⚠️  触发率过低 ({trigger_rate:.1f}% < 30%)")
        print(f"   原因: Verifier 阈值过于宽松")
        print(f"   建议: ")
        print(f"     • 增加 rel_threshold (0.83→0.85)")
        print(f"     • 降低 red_threshold (0.50→0.48)")
    elif trigger_rate > 50:
        print(f"   ⚠️  触发率过高 ({trigger_rate:.1f}% > 50%)")
        print(f"   原因: Verifier 阈值过于严格")
        print(f"   建议:")
        print(f"     • 降低 rel_threshold (0.83→0.82)")
        print(f"     • 增加 red_threshold (0.50→0.52)")
    else:
        print(f"   ✅ 触发率处于目标范围 ({trigger_rate:.1f}%)")
    
    print(f"\n2️⃣  Controller 改纲成功率诊断:")
    if gen_stats["controller_triggered"] > 0:
        success_rate = gen_stats["controller_success"] / gen_stats["controller_triggered"] * 100
        if success_rate < 80:
            print(f"   ⚠️  成功率低 ({success_rate:.1f}% < 80%)")
            print(f"   原因: Controller 的改纲算法可能不够强大")
            print(f"   建议:")
            print(f"     • 检查 Controller 使用的 LLM 能力")
            print(f"     • 优化 Controller 的改纲 Prompt")
            print(f"     • 增加重试次数")
        else:
            print(f"   ✅ 成功率良好 ({success_rate:.1f}%)")
    
    # 保存详细结果
    results_file = "test_results_comprehensive.json"
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(gen_stats, f, indent=2, ensure_ascii=False)
    print(f"\n📁 详细结果已保存: {results_file}")
    
    return True


def main():
    """主测试流程"""
    
    print("""
╔════════════════════════════════════════════════════════════════╗
║        FlowerNet 完整本地测试 - 使用 Ollama qwen2.5           ║
╚════════════════════════════════════════════════════════════════╝
""")
    
    # 步骤 1: 服务连接性
    if not test_service_connectivity():
        print("\n❌ 服务连接失败，请先启动容器: docker-compose up -d")
        return False
    
    # 步骤 2: Ollama 模型检查
    if not test_ollama_model():
        print("\n❌ Ollama 模型不可用，请拉取: docker exec flower-ollama ollama pull qwen2.5:1.5b")
        return False
    
    # 步骤 3: Verifier 测试
    verifier_results = run_verifier_test()
    
    # 步骤 4: 生成和 Controller 测试
    gen_stats = run_generation_test()
    
    # 步骤 5: 详细分析
    success = print_analysis_report(gen_stats, verifier_results)
    
    if success:
        print(f"\n✅ 测试完成！")
    
    return success


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
