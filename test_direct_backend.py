#!/usr/bin/env python3
"""
直接调用后端服务的测试 - 绕过 Web 层
用来诊断是否是 Web 层或后端服务的问题
"""

import requests
import json
import time
import sys
import os

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

def test_outliner_directly():
    """直接测试 Outliner 后端"""
    print("\n=== 测试 Outliner 直接调用 ===\n")
    
    doc_id = f"direct_test_{int(time.time())}"
    
    payload = {
        "document_id": doc_id,
        "user_background": "普通读者",
        "user_requirements": "详细、易懂、实用",
        "max_sections": 1,
        "max_subsections_per_section": 1,
    }
    
    print(f"📤 向 Outliner 发送请求（超时 1800s）...")
    print(f"   请求: POST http://localhost:8003/outline/generate-and-save")
    print(f"   文档 ID: {doc_id}\n")
    
    start_time = time.time()
    try:
        response = requests.post(
            "http://localhost:8003/outline/generate-and-save",
            json=payload,
            timeout=1800  # 30 分钟超时
        )
        elapsed = time.time() - start_time
        
        print(f"✅ 响应收到（耗时 {elapsed:.1f}s）")
        print(f"   状态码: {response.status_code}")
        
        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError:
                print(f"\n❌ Outliner 返回非 JSON")
                print(f"   响应: {response.text[:500]}")
                return None
            if data.get("success"):
                print(f"\n✅ Outliner 成功生成大纲")
                print(f"   标题: {data.get('document_title')}")
                print(f"   内容提示数: {len(data.get('content_prompts', []))}")
                return data
            else:
                print(f"\n❌ Outliner 返回失败")
                print(f"   错误: {data.get('error', 'unknown')}")
        else:
            print(f"\n❌ HTTP {response.status_code}")
            print(f"   响应: {response.text[:500]}")
    
    except requests.Timeout:
        elapsed = time.time() - start_time
        print(f"\n❌ 请求超时 ({elapsed:.0f}s)")
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"\n❌ 错误 ({elapsed:.1f}s): {e}")
    
    return None


def test_generator_directly(outline_data):
    """直接测试 Generator 后端"""
    if not outline_data:
        print("\n⚠️  需要 Outliner 成功的结果才能测试 Generator")
        return
    
    print("\n=== 测试 Generator 直接调用 ===\n")
    
    doc_id = outline_data.get("document_id")
    payload = {
        "document_id": doc_id,
        "title": outline_data.get("document_title"),
        "structure": outline_data.get("structure"),
        "content_prompts": outline_data.get("content_prompts"),
        "user_background": "普通读者",
        "user_requirements": "详细、易懂、实用",
        "rel_threshold": 0.75,  # 使用更宽松的阈值
        "red_threshold": 0.50,
    }
    
    print(f"📤 向 Generator 发送请求（超时 1800s）...")
    print(f"   请求: POST http://localhost:8002/generate_document")
    print(f"   文档 ID: {doc_id}")
    print(f"   内容提示数: {len(payload.get('content_prompts', []))}\n")
    
    start_time = time.time()
    try:
        response = requests.post(
            "http://localhost:8002/generate_document",
            json=payload,
            timeout=1200
        )
        elapsed = time.time() - start_time
        
        print(f"✅ 响应收到（耗时 {elapsed:.1f}s）")
        print(f"   状态码: {response.status_code}")
        
        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError:
                print(f"\n❌ Generator 返回非 JSON")
                print(f"   响应: {response.text[:500]}")
                return None
            if data.get("success"):
                print(f"\n✅ Generator 成功生成文档")
                
                # 解析 Controller 触发率
                total_sections = len(data.get("sections", []))
                total_subsections = 0
                controller_triggered = 0
                controller_success = 0
                
                for section in data.get("sections", []):
                    for subsection in section.get("subsections", []):
                        total_subsections += 1
                        if subsection.get("controller_triggered"):
                            controller_triggered += 1
                            if subsection.get("success"):
                                controller_success += 1
                
                print(f"   生成章节数: {total_sections}")
                print(f"   总小节数: {total_subsections}")
                print(f"   Controller 触发: {controller_triggered}")
                if total_subsections > 0:
                    trigger_rate = controller_triggered / total_subsections * 100
                    print(f"   触发率: {trigger_rate:.1f}%")
                if controller_triggered > 0:
                    success_rate = controller_success / controller_triggered * 100
                    print(f"   改纲成功率: {success_rate:.1f}% ({controller_success}/{controller_triggered})")
                
                return data
            else:
                print(f"\n❌ Generator 返回失败")
                print(f"   错误: {data.get('error', 'unknown')}")
        else:
            print(f"\n❌ HTTP {response.status_code}")
            print(f"   响应: {response.text[:500]}")
    
    except requests.Timeout:
        elapsed = time.time() - start_time
        print(f"\n❌ 请求超时 ({elapsed:.0f}s)")
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"\n❌ 错误 ({elapsed:.1f}s): {e}")
    
    return None


if __name__ == "__main__":
    print("""
╔════════════════════════════════════════════════════════════════╗
║         FlowerNet 直接后端测试 - 诊断超时问题                  ║
╚════════════════════════════════════════════════════════════════╝
""")
    
    # 第一步：测试 Outliner
    outline_data = test_outliner_directly()
    
    # 第二步：测试 Generator
    if outline_data:
        gen_data = test_generator_directly(outline_data)
        
        # 保存结果
        if gen_data:
            with open("test_result_direct_backend.json", "w") as f:
                json.dump({
                    "success": True,
                    "outline": outline_data,
                    "generation": gen_data,
                }, f, indent=2, ensure_ascii=False)
            print(f"\n📁 结果已保存到 test_result_direct_backend.json")
    
    print("\n" + "="*60)
    print("✅ 直接后端测试完成")
