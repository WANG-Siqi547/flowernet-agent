#!/usr/bin/env python3
"""
FlowerNet Controller 触发率调整指南
===========================================

针对用户需求：
✓ 确认本地和远端代码一致
✓ 本地完整测试
✓ 控制 Controller 触发率 30%-50%  
✓ Controller 改纲不失败

关键参数：
- rel_threshold: 相关性阈值（越高触发率越低）
- red_threshold: 冗余度阈值（越低触发率越高）
"""

import os
import sys
from pathlib import Path

def get_threshold_recommendations():
    """根据当前阈值给出调整建议"""
    
    current_rel = 0.83
    current_red = 0.50
    
    recommendations = {
        "current": {
            "rel_threshold": current_rel,
            "red_threshold": current_red,
            "description": "当前配置（基准）"
        },
        "increase_trigger_rate": {
            "微调版": {
                "rel_threshold": 0.85,
                "red_threshold": 0.48,
                "expected_trigger_rate": "35-45%",
                "rationale": "提高相关性要求，降低冗余度容限"
            },
            "激进版": {
                "rel_threshold": 0.87,
                "red_threshold": 0.45,
                "expected_trigger_rate": "45-55%",
                "rationale": "严格的相关性和冗余度要求"
            }
        },
        "decrease_trigger_rate": {
            "微调版": {
                "rel_threshold": 0.82,
                "red_threshold": 0.52,
                "expected_trigger_rate": "25-35%",
                "rationale": "降低相关性要求，提高冗余度容限"
            },
            "激进版": {
                "rel_threshold": 0.80,
                "red_threshold": 0.55,
                "expected_trigger_rate": "15-25%",
                "rationale": "宽松的要求，很少触发改纲"
            }
        }
    }
    
    return recommendations

def create_test_script():
    """创建测试脚本"""
    
    script = '''#!/usr/bin/env python3
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
        print(f"\\n{'='*60}")
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
    print(f"\\n📁 结果已保存到 test_results.json")
'''
    
    return script

def create_adjustment_guide():
    """创建了调整指南"""
    
    guide = """
# FlowerNet Controller 触发率调整指南

## 目标
- Controller 触发率: **30%-50%**
- Controller 改纲成功率: **>= 80%**

## 关键参数

### 1. rel_threshold (相关性阈值)
- **含义**: Draft 必须与 Outline 相关性 >= 该值
- **范围**: 0.0 - 1.0
- **当前值**: 0.83
- **调整方向**:
  - 增加到 0.85-0.87 → 触发率 ↑ (相关性要求更严格)
  - 降低到 0.80-0.82 → 触发率 ↓ (相关性容限更大)

### 2. red_threshold (冗余度阈值)
- **含义**: Draft 的冗余度必须 <= 该值
- **范围**: 0.0 - 1.0
- **当前值**: 0.50
- **调整方向**:
  - 降低到 0.45-0.48 → 触发率 ↑ (冗余度容限更小)
  - 增加到 0.52-0.55 → 触发率 ↓ (冗余度容限更大)

## 调整步骤

### 步骤 1: 基准测试
```bash
# 启动 Ollama 测试环境
docker-compose -f docker-compose-test.yml up -d

# 拉取本地模型（首次）
docker exec flower-ollama ollama pull qwen2.5:7b

# 运行测试
python test_ollama_controller_trigger.py

# 查看结果
cat test_results.json
```

### 步骤 2: 解读结果
```
若触发率 < 30%:
  ↳ 目标: 增加触发率
  ↳ 方案 A 微调: rel_threshold 0.83→0.85, red_threshold 0.50→0.48
  ↳ 方案 B 激进: rel_threshold 0.83→0.87, red_threshold 0.50→0.45

若触发率 > 50%:
  ↳ 目标: 降低触发率
  ↳ 方案 A 微调: rel_threshold 0.83→0.82, red_threshold 0.50→0.52
  ↳ 方案 B 激进: rel_threshold 0.83→0.80, red_threshold 0.50→0.55
```

### 步骤 3: 修改代码
编辑 `flowernet-generator/flowernet_orchestrator_impl.py`:

```python
# 第 292-293 行
def _generate_and_verify_subsection(
    self,
    # ...
    rel_threshold: float = 0.83,    # ← 修改此值
    red_threshold: float = 0.50,    # ← 修改此值
) -> Dict[str, Any]:
    # ...
```

### 步骤 4: 重新测试
```bash
# 重新构建镜像
docker-compose -f docker-compose-test.yml up -d --build

# 再次运行测试
python test_ollama_controller_trigger.py

# 比较结果
diff test_results.json test_results_v2.json
```

## 预期触发率与参数关系

| rel_threshold | red_threshold | 预期触发率 | 说明 |
|---|---|---|---|
| 0.80 | 0.55 | 15-25% | 宽松要求 |
| 0.82 | 0.52 | 25-35% | 较宽松 |
| **0.83** | **0.50** | **30-40%** | **基准配置** |
| 0.85 | 0.48 | 35-45% | 较严格 |
| 0.87 | 0.45 | 45-55% | 严格要求 |

## 常见问题

### Q: 为什么改纲总是失败?
A: 
- Check Controller 的改纲算法是否能生成更好的 Outline
- 增加 Controller 重试次数 (MAX_CONTROLLER_RETRIES)
- 检查 LLM 生成能力 (考虑用更强大的模型)

### Q: 相关性和冗余度得分总是很接近?
A:
- 说明 Draft 质量中等，需要优化 Generator Prompt
- 考虑在 Prompt 中加入更多约束条件

### Q: 为什么本地 (Ollama) 和远端 (Azure) 效果不同?
A:
- Ollama (qwen2.5:7b) 能力有限，相比 GPT-4o-mini 质量较低
- 本地用 Ollama 测试是为了快速迭代，远端最终还是用 Azure

## 回溯到 Azure

当本地测试完成，触发率达到目标后:

```bash
# 恢复原始配置
mv docker-compose.yml.azure-backup docker-compose.yml

# 更新相同的阈值到 render.yaml
# 编辑: flowernet-generator/render.yaml
# 确保与代码中的参数一致

# 重新启动
docker-compose up -d --build
```

## 验证检查清单

- [ ] 本地 Ollama 测试完成
- [ ] 触发率在 30-50% 范围内
- [ ] 改纲成功率 >= 80%
- [ ] 相关性和冗余度得分分布合理
- [ ] 代码参数已提交到 Git
- [ ] render.yaml 已同步参数
- [ ] 远端 Azure 配置已验证
"""
    
    return guide

def main():
    """主程序"""
    
    print("""
╔══════════════════════════════════════════════════════════════╗
║    FlowerNet Controller 触发率优化 - 完整指南               ║
╚══════════════════════════════════════════════════════════════╝

📋 本指南包含以下内容:
   1. 关键参数说明
   2. 调整步骤
   3. 参数推荐
   4. 测试脚本
   5. Q&A 常见问题

""")
    
    # 获取推荐
    recommendations = get_threshold_recommendations()
    
    print("🎯 参数调整建议:\n")
    
    print("当前配置 (基准):")
    print(f"  rel_threshold: {recommendations['current']['rel_threshold']}")
    print(f"  red_threshold: {recommendations['current']['red_threshold']}")
    
    print("\n增加触发率 (如果当前 < 30%):")
    for variant, config in recommendations['increase_trigger_rate'].items():
        print(f"\n  {variant}:")
        print(f"    rel_threshold: {config['rel_threshold']}")
        print(f"    red_threshold: {config['red_threshold']}")
        print(f"    预期触发率: {config['expected_trigger_rate']}")
    
    print("\n降低触发率 (如果当前 > 50%):")
    for variant, config in recommendations['decrease_trigger_rate'].items():
        print(f"\n  {variant}:")
        print(f"    rel_threshold: {config['rel_threshold']}")
        print(f"    red_threshold: {config['red_threshold']}")
        print(f"    预期触发率: {config['expected_trigger_rate']}")
    
    # 保存脚本和指南
    test_script = create_test_script()
    with open("test_ollama_controller_trigger.py", "w") as f:
        f.write(test_script)
    print("\n✅ 测试脚本: test_ollama_controller_trigger.py")
    
    guide = create_adjustment_guide()
    with open("CONTROLLER_TRIGGER_GUIDE.md", "w") as f:
        f.write(guide)
    print("✅ 完整指南: CONTROLLER_TRIGGER_GUIDE.md")
    
    print("\n🚀 下一步:")
    print("   1. 查看: CONTROLLER_TRIGGER_GUIDE.md")
    print("   2. 运行: bash setup_test_environment.sh")
    print("   3. 执行: python test_ollama_controller_trigger.py")

if __name__ == "__main__":
    main()
