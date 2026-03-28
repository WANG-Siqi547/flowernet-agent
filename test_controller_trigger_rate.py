#!/usr/bin/env python3
"""
完整的 FlowerNet 测试脚本 - 追踪 Controller 触发率
测试目标：控制 Controller 触发率在 30%-50% 之间
通过调整 Verifier 的 rel_threshold 和 red_threshold 来达成目标
"""

import requests
import json
import time
import sys
from typing import Dict, List, Any
from collections import defaultdict
from datetime import datetime

# 测试配置
WEB_URL = "http://localhost:8010"
HEALTH_CHECK_TIMEOUT = 10
GENERATION_TIMEOUT = 600

# 测试用例：不同主题和复杂度的文档
TEST_CASES = [
    {
        "topic": "人工智能在医疗中的应用",
        "subtopics": [
            "AI诊断系统",
            "医学影像分析",
            "个性化治疗方案",
            "药物发现与开发",
            "健康监测与预防"
        ],
        "doc_id": "doc_ai_healthcare"
    },
    {
        "topic": "区块链技术基础",
        "subtopics": [
            "分布式账本概念",
            "共识机制",
            "智能合约",
            "加密货币应用",
            "企业级实现"
        ],
        "doc_id": "doc_blockchain"
    },
    {
        "topic": "云计算架构",
        "subtopics": [
            "基础设施即服务",
            "平台即服务",
            "软件即服务",
            "混合云部署",
            "成本优化策略"
        ],
        "doc_id": "doc_cloud"
    }
]

class ControllerTriggerAnalyzer:
    """Controller 触发率分析工具"""
    
    def __init__(self):
        self.stats = {
            "total_subsections": 0,
            "controller_triggered": 0,
            "controller_success": 0,
            "controller_failed": 0,
            "details": defaultdict(list),
            "relevancy_scores": [],
            "redundancy_scores": [],
            "trigger_iterations": defaultdict(list),
        }
        self.start_time = datetime.now()
    
    def check_health(self) -> bool:
        """检查所有服务是否就绪"""
        services = [
            ("Web", f"{WEB_URL}/"),
            ("Generator", "http://localhost:8002/"),
            ("Outliner", "http://localhost:8003/"),
            ("Verifier", "http://localhost:8000/"),
            ("Controller", "http://localhost:8001/"),
        ]
        
        print("\n🔍 健康检查...")
        all_healthy = True
        for name, url in services:
            try:
                resp = requests.get(url, timeout=5)
                status = "✅" if resp.status_code == 200 else "⚠️"
                print(f"  {status} {name}: {resp.status_code}")
            except Exception as e:
                print(f"  ❌ {name}: {str(e)[:50]}")
                all_healthy = False
        
        return all_healthy
    
    def generate_document(self, topic: str, doc_id: str, subtopics: List[str]) -> Dict[str, Any]:
        """生成一个文档并收集指标"""
        print(f"\n📄 开始生成文档: {doc_id}")
        print(f"   主题: {topic}")
        print(f"   小章节数: {len(subtopics)}")
        
        start_time = time.time()
        
        try:
            response = requests.post(
                f"{WEB_URL}/api/generate",
                json={
                    "topic": topic,
                    "subtopics": subtopics,
                    "document_id": doc_id,
                },
                timeout=GENERATION_TIMEOUT
            )
            
            elapsed = time.time() - start_time
            
            if response.status_code != 200:
                print(f"   ❌ 生成失败: HTTP {response.status_code}")
                return {
                    "success": False,
                    "doc_id": doc_id,
                    "status_code": response.status_code,
                    "elapsed": elapsed,
                    "error": response.text[:200]
                }
            
            result = response.json()
            print(f"   ✅ 生成完成 ({elapsed:.1f}s)")
            return {
                "success": True,
                "doc_id": doc_id,
                "elapsed": elapsed,
                "data": result
            }
            
        except requests.Timeout:
            print(f"   ❌ 超时 (>{GENERATION_TIMEOUT}s)")
            return {
                "success": False,
                "doc_id": doc_id,
                "error": "timeout",
                "elapsed": time.time() - start_time
            }
        except Exception as e:
            print(f"   ❌ 错误: {str(e)}")
            return {
                "success": False,
                "doc_id": doc_id,
                "error": str(e),
                "elapsed": time.time() - start_time
            }
    
    def parse_metrics_from_response(self, response_data: Dict[str, Any]) -> None:
        """从响应中解析 Controller 触发和得分信息"""
        if not isinstance(response_data, dict):
            return
        
        # 遍历响应中的各个章节和小节
        for section_id, section_data in response_data.items():
            if not isinstance(section_data, dict):
                continue
                
            for subsection_id, subsection_data in section_data.items():
                if not isinstance(subsection_data, dict):
                    continue
                
                self.stats["total_subsections"] += 1
                
                # 检查是否触发了 controller
                if subsection_data.get("controller_triggered"):
                    self.stats["controller_triggered"] += 1
                    iterations = subsection_data.get("controller_retry_count", 0)
                    self.stats["trigger_iterations"][subsection_id] = iterations
                    
                    # 检查改纲是否最终成功
                    if subsection_data.get("success"):
                        self.stats["controller_success"] += 1
                    else:
                        self.stats["controller_failed"] += 1
                
                # 收集得分
                verification = subsection_data.get("verification", {})
                if "relevancy_index" in verification:
                    rel_score = verification["relevancy_index"]
                    self.stats["relevancy_scores"].append(rel_score)
                
                if "redundancy_index" in verification:
                    red_score = verification["redundancy_index"]
                    self.stats["redundancy_scores"].append(red_score)
                
                # 记录详情
                self.stats["details"][subsection_id].append({
                    "triggered": subsection_data.get("controller_triggered", False),
                    "relevancy": verification.get("relevancy_index", None),
                    "redundancy": verification.get("redundancy_index", None),
                    "success": subsection_data.get("success", False),
                    "iterations": subsection_data.get("iteration", 0),
                })
    
    def generate_report(self) -> str:
        """生成测试报告"""
        elapsed = (datetime.now() - self.start_time).total_seconds()
        
        if self.stats["total_subsections"] == 0:
            return "❌ 没有生成任何小节，无法生成报告"
        
        trigger_rate = (
            self.stats["controller_triggered"] / self.stats["total_subsections"] * 100
        )
        success_rate = (
            self.stats["controller_success"] / max(1, self.stats["controller_triggered"]) * 100
            if self.stats["controller_triggered"] > 0 else 0
        )
        
        # 计算得分统计
        import statistics
        rel_stats = {}
        red_stats = {}
        
        if self.stats["relevancy_scores"]:
            rel_stats = {
                "min": min(self.stats["relevancy_scores"]),
                "max": max(self.stats["relevancy_scores"]),
                "avg": statistics.mean(self.stats["relevancy_scores"]),
                "stdev": statistics.stdev(self.stats["relevancy_scores"]) if len(self.stats["relevancy_scores"]) > 1 else 0,
            }
        
        if self.stats["redundancy_scores"]:
            red_stats = {
                "min": min(self.stats["redundancy_scores"]),
                "max": max(self.stats["redundancy_scores"]),
                "avg": statistics.mean(self.stats["redundancy_scores"]),
                "stdev": statistics.stdev(self.stats["redundancy_scores"]) if len(self.stats["redundancy_scores"]) > 1 else 0,
            }
        
        report = f"""
╔═══════════════════════════════════════════════════════════════╗
║           FlowerNet Controller 触发率分析报告                  ║
╚═══════════════════════════════════════════════════════════════╝

⏱️  测试耗时: {elapsed:.1f} 秒

📊 总体指标:
   • 总小节数: {self.stats['total_subsections']}
   • Controller 触发次数: {self.stats['controller_triggered']}
   • 触发率: {trigger_rate:.1f}% {'✅ 在目标范围内 (30%-50%)' if 30 <= trigger_rate <= 50 else '⚠️  需要调整阈值'}
   • Controller 改纲成功: {self.stats['controller_success']}/{self.stats['controller_triggered']} ({success_rate:.1f}%)

📈 相关性 (Relevancy) 统计:
   • 平均值: {rel_stats.get('avg', 'N/A'):.4f}
   • 范围: {rel_stats.get('min', 'N/A'):.4f} - {rel_stats.get('max', 'N/A'):.4f}
   • 标准差: {rel_stats.get('stdev', 'N/A'):.4f}

📉 冗余度 (Redundancy) 统计:
   • 平均值: {red_stats.get('avg', 'N/A'):.4f}
   • 范围: {red_stats.get('min', 'N/A'):.4f} - {red_stats.get('max', 'N/A'):.4f}
   • 标准差: {red_stats.get('stdev', 'N/A'):.4f}

💡 调整建议:
"""
        
        if trigger_rate < 30:
            report += """   当前触发率过低 (<30%)，说明阈值过宽松
   建议调整方案:
   1. 增加 rel_threshold (现在 0.83)，目标 0.85-0.87
   2. 降低 red_threshold (现在 0.50)，目标 0.45-0.48
   3. 或组合调整两个阈值
"""
        elif trigger_rate > 50:
            report += """   当前触发率过高 (>50%)，说明阈值过严格
   建议调整方案:
   1. 降低 rel_threshold (现在 0.83)，目标 0.80-0.82
   2. 增加 red_threshold (现在 0.50)，目标 0.52-0.55
   3. 或组合调整两个阈值
"""
        else:
            report += f"""   当前触发率在目标范围内! ({trigger_rate:.1f}%)
   ✅ 配置已优化
"""
        
        if self.stats["controller_failed"] > 0:
            report += f"""
⚠️  改纲失败警告:
   • 触发了 Controller 但改纲失败的小节数: {self.stats['controller_failed']}
   • 失败率: {self.stats['controller_failed'] / max(1, self.stats['controller_triggered']) * 100:.1f}%
   
   建议检查:
   - Controller 的大纲改进算法是否有效
   - LLM 生成能力是否充足
   - 是否需要增加 controller 的重试次数
"""
        
        return report
    
    def save_detailed_results(self, filename: str = "test_results.json") -> None:
        """保存详细结果到文件"""
        results = {
            "timestamp": self.start_time.isoformat(),
            "summary": {
                "total_subsections": self.stats["total_subsections"],
                "controller_triggered": self.stats["controller_triggered"],
                "trigger_rate_percent": (
                    self.stats["controller_triggered"] / max(1, self.stats["total_subsections"]) * 100
                ),
                "controller_success": self.stats["controller_success"],
                "controller_failed": self.stats["controller_failed"],
            },
            "score_distributions": {
                "relevancy_scores": self.stats["relevancy_scores"],
                "redundancy_scores": self.stats["redundancy_scores"],
            },
            "detailed_metrics": dict(self.stats["details"]),
        }
        
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        
        print(f"\n📁 详细结果已保存: {filename}")


def main():
    """主测试流程"""
    analyzer = ControllerTriggerAnalyzer()
    
    # 检查服务健康
    if not analyzer.check_health():
        print("\n❌ 部分服务不可用，请先启动所有服务")
        print("   运行: docker-compose up -d")
        sys.exit(1)
    
    print(f"\n{'='*60}")
    print("🚀 开始 Controller 触发率测试")
    print(f"{'='*60}")
    
    # 运行测试
    for test_case in TEST_CASES:
        result = analyzer.generate_document(
            topic=test_case["topic"],
            doc_id=test_case["doc_id"],
            subtopics=test_case["subtopics"]
        )
        
        if result["success"] and "data" in result:
            analyzer.parse_metrics_from_response(result["data"])
        
        # 控制请求间隔
        time.sleep(2)
    
    # 生成报告
    print(analyzer.generate_report())
    
    # 保存详细结果
    analyzer.save_detailed_results()
    
    return analyzer


if __name__ == "__main__":
    analyzer = main()
    
    # 输出最后的阈值调整建议
    if analyzer.stats["total_subsections"] > 0:
        trigger_rate = (
            analyzer.stats["controller_triggered"] / analyzer.stats["total_subsections"] * 100
        )
        
        if trigger_rate < 30:
            print(f"\n📝 下一步: 修改 flowernet-generator/flowernet_orchestrator_impl.py")
            print(f"   降低 rel_threshold 或增加 red_threshold")
            print(f"   然后重新启动: docker-compose up -d --build")
        elif trigger_rate > 50:
            print(f"\n📝 下一步: 修改 flowernet-generator/flowernet_orchestrator_impl.py")
            print(f"   增加 rel_threshold 或降低 red_threshold")
            print(f"   然后重新启动: docker-compose up -d --build")
