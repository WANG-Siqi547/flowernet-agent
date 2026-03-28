#!/usr/bin/env python3
"""
本地 Ollama 完整测试脚本
用于验证 Controller 触发率并调整阈值
"""

import requests
import json
import time
from typing import Dict, List, Any

WEB_URL = "http://localhost:8010"
TIMEOUT = 600

def run_generation_test(num_documents: int = 3) -> Dict[str, Any]:
    """运行生成测试并收集统计数据"""
    
    test_configs = [
        {
            "topic": "人工智能在医疗诊断中的应用与挑战",
            "subtopics": [
                "AI 医学影像分析",
                "早期疾病预测",
                "个性化治疗方案",
                "数据隐私与伦理",
                "临床集成与验证"
            ]
        },
        {
            "topic": "云计算基础设施架构",
            "subtopics": [
                "分布式系统设计",
                "资源隔离与管理",
                "负载均衡策略",
                "故障转移机制",
                "成本优化方法"
            ]
        },
        {
            "topic": "区块链共识机制",
            "subtopics": [
                "工作量证明 PoW",
                "权益证明 PoS",
                "拜占庭容错算法",
                "中本聪共识",
                "实际应用与挑战"
            ]
        }
    ]
    
    stats = {
        "total_subsections": 0,
        "controller_triggered": 0,
        "controller_success": 0,
        "relevancy_scores": [],
        "redundancy_scores": [],
    }
    
    for i, config in enumerate(test_configs[:num_documents]):
        print(f"\n{'='*60}")
        print(f"📄 测试 {i+1}: {config['topic']}")
        print(f"{'='*60}")
        
        try:
            response = requests.post(
                f"{WEB_URL}/api/generate",
                json={
                    "topic": config["topic"],
                    "subtopics": config["subtopics"],
                    "document_id": f"test_doc_{i}",
                },
                timeout=TIMEOUT
            )
            
            if response.status_code == 200:
                data = response.json()
                
                # 解析结果
                for section_id, section_data in data.items():
                    if isinstance(section_data, dict):
                        for subsection_id, subsection_data in section_data.items():
                            if isinstance(subsection_data, dict):
                                stats["total_subsections"] += 1
                                
                                if subsection_data.get("controller_triggered"):
                                    stats["controller_triggered"] += 1
                                    if subsection_data.get("success"):
                                        stats["controller_success"] += 1
                                
                                verification = subsection_data.get("verification", {})
                                if "relevancy_index" in verification:
                                    stats["relevancy_scores"].append(
                                        verification["relevancy_index"]
                                    )
                                if "redundancy_index" in verification:
                                    stats["redundancy_scores"].append(
                                        verification["redundancy_index"]
                                    )
                
                print(f"✅ 测试完成")
                
            else:
                print(f"❌ HTTP {response.status_code}")
        
        except Exception as e:
            print(f"❌ 错误: {str(e)}")
        
        time.sleep(2)
    
    # 计算触发率
    if stats["total_subsections"] > 0:
        trigger_rate = stats["controller_triggered"] / stats["total_subsections"] * 100
        stats["trigger_rate"] = trigger_rate
    
    return stats

def print_results(stats: Dict[str, Any]):
    """打印测试结果"""
    
    if stats["total_subsections"] == 0:
        print("❌ 没有生成任何小节")
        return
    
    trigger_rate = stats.get("trigger_rate", 0)
    
    print(f"""
╔════════════════════════════════════════════╗
║     测试结果报告                            ║
╚════════════════════════════════════════════╝

📊 总体数据:
   • 总小节数: {stats['total_subsections']}
   • Controller 触发数: {stats['controller_triggered']}
   • 触发率: {trigger_rate:.1f}%
   • 改纲成功: {stats['controller_success']}/{stats['controller_triggered']}

📈 相关性得分 (Relevancy):
   • 平均值: {sum(stats['relevancy_scores'])/len(stats['relevancy_scores']):.4f}
   • 最小值: {min(stats['relevancy_scores']):.4f}
   • 最大值: {max(stats['relevancy_scores']):.4f}

📉 冗余度得分 (Redundancy):
   • 平均值: {sum(stats['redundancy_scores'])/len(stats['redundancy_scores']):.4f}
   • 最小值: {min(stats['redundancy_scores']):.4f}
   • 最大值: {max(stats['redundancy_scores']):.4f}

💡 诊断:
""")
    
    if trigger_rate < 30:
        print("   ⚠️  触发率过低 (<30%) - Controller 改纲机制未充分利用")
        print("   建议: 增加 rel_threshold 或降低 red_threshold")
    elif trigger_rate > 50:
        print("   ⚠️  触发率过高 (>50%) - Controller 过度改纲")
        print("   建议: 降低 rel_threshold 或增加 red_threshold")
    else:
        print(f"   ✅ 触发率处于目标范围 ({trigger_rate:.1f}%)")
    
    if stats["controller_triggered"] > 0:
        success_rate = stats["controller_success"] / stats["controller_triggered"] * 100
        if success_rate < 80:
            print(f"   ⚠️  改纲成功率低 ({success_rate:.1f}%) - 可能是 Controller 算法问题")
        else:
            print(f"   ✅ 改纲成功率高 ({success_rate:.1f}%)")

if __name__ == "__main__":
    print("🚀 开始 Ollama 本地测试...")
    
    # 首先检查服务健康
    try:
        resp = requests.get(f"{WEB_URL}/", timeout=5)
        print("✅ Web 服务就绪")
    except:
        print("❌ Web 服务不可用")
        sys.exit(1)
    
    # 运行测试
    stats = run_generation_test(num_documents=3)
    print_results(stats)
    
    # 保存结果
    with open("test_results.json", "w") as f:
        json.dump(stats, f, indent=2)
    print(f"\n📁 结果已保存到 test_results.json")
