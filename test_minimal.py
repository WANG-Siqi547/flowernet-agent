#!/usr/bin/env python3
"""
FlowerNet 最小化运行测试 - 使用 Web API 真实流程
"""

import requests
import json
import time
import os

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

def run_minimal_test():
    """最小化的完整工作流测试"""
    
    print("""
╔════════════════════════════════════════════════════════════════╗
║           FlowerNet 最小化测试 - 真实生成流程                  ║
╚════════════════════════════════════════════════════════════════╝
""")
    
    # 测试参数
    topic = "人工智能的未来发展"
    subtopics = [
        "AI的现状",
        "深度学习进展",
        "应用前景"
    ]
    doc_id = f"test_{int(time.time())}"
    
    print(f"\n📝 开始生成")    
    print(f"   主题: {topic}")
    print(f"   小章节数: {len(subtopics)}")
    print(f"   文档 ID: {doc_id}")
    
    try:
        print("\n⏳ 等待生成完成（可能需要 2-5 分钟）...")
        
        start_time = time.time()
        response = requests.post(
            "http://localhost:8010/api/generate",
            json={
                "topic": topic,
                "chapter_count": 1,
                "subsection_count": len(subtopics),
                "user_background": "普通读者",
                "extra_requirements": "请简洁清晰地完成生成",
                "rel_threshold": 0.75,
                "red_threshold": 0.50,
                "timeout_seconds": 1800,
            },
            timeout=2000
        )
        elapsed = time.time() - start_time
        
        print(f"\n✅ 请求完成 (耗时 {elapsed:.1f}s)")
        
        if response.status_code == 200:
            data = response.json()
            print(f"\n✅ 生成成功 (HTTP 200)")
            
            # 解析结果
            total_subsections = 0
            controller_triggered = 0
            controller_success = 0
            
            for section_id, section_data in data.items():
                if isinstance(section_data, dict):
                    for subsection_id, subsection_data in section_data.items():
                        if isinstance(subsection_data, dict):
                            total_subsections += 1
                            if subsection_data.get("controller_triggered"):
                                controller_triggered += 1
                                if subsection_data.get("success"):
                                    controller_success += 1
            
            print(f"\n📊 结果统计:")
            print(f"   总小节数: {total_subsections}")
            print(f"   Controller 触发: {controller_triggered}")
            if total_subsections > 0:
                trigger_rate = controller_triggered / total_subsections * 100
                print(f"   触发率: {trigger_rate:.1f}%")
            
            if controller_triggered > 0:
                print(f"   改纲成功: {controller_success}/{controller_triggered}")
            
            # 保存结果
            with open("test_result_minimal.json", "w") as f:
                json.dump({
                    "success": True,
                    "elapsed": elapsed,
                    "total_subsections": total_subsections,
                    "controller_triggered": controller_triggered,
                    "controller_success": controller_success,
                    "raw_response": data
                }, f, indent=2, ensure_ascii=False)
            
            print(f"\n📁 结果已保存到 test_result_minimal.json")
            return True
            
        else:
            print(f"\n❌ HTTP {response.status_code}")
            print(f"   响应: {response.text[:500]}")
            return False
    
    except requests.Timeout:
        print(f"\n❌ 请求超时 (客户端超时 2000s)")
        return False
    
    except Exception as e:
        print(f"\n❌ 错误: {str(e)}")
        return False


if __name__ == "__main__":
    success = run_minimal_test()
    
    if success:
        print("\n\n✅ 测试完成！")
        print("\n📋 下一步:")
        print("   1. 检查 test_result_minimal.json 中的 Controller 触发率")
        print("   2. 根据触发率调整 Verifier 阈值")
        print("   3. 在 flowernet-generator/flowernet_orchestrator_impl.py 修改:")
        print("      - rel_threshold (第 292 行)")
        print("      - red_threshold (第 293 行)")
    else:
        print("\n\n❌ 测试失败")
        print("\n⚠️  检查:")
        print("   1. docker-compose ps 确保所有容器运行")
        print("   2. docker logs flower-web 查看 Web 服务日志")
        print("   3. docker logs flower-outliner 查看 Outliner 日志")
