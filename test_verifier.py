#!/usr/bin/env python3
"""
诊断 Verifier 服务状态
"""
import requests
import json
from datetime import datetime

# 测试 Verifier 端点
VERIFIER_URLS = [
    "https://flowernet-verifier.onrender.com",  # Render 生产环境
    "http://localhost:8000",  # 本地调试
]

def test_verifier_health():
    """测试 Verifier 健康检查"""
    print("=" * 60)
    print("🔍 Verifier 服务诊断")
    print("=" * 60)
    
    for url in VERIFIER_URLS:
        print(f"\n测试: {url}")
        print("-" * 60)
        
        # 1. 测试根端点 (健康检查)
        try:
            print(f"  [1] 健康检查 GET {url}/")
            response = requests.get(f"{url}/", timeout=10)
            print(f"      ✅ HTTP {response.status_code}")
            print(f"      响应: {response.json()}")
        except Exception as e:
            print(f"      ❌ 失败: {type(e).__name__}: {e}")
            continue
        
        # 2. 测试 /verify 端点
        try:
            print(f"\n  [2] 验证接口 POST {url}/verify")
            payload = {
                "draft": "这是一个关于奇美兰的简介。奇美兰是一个兰花品种，具有美丽的花朵和独特的香气。",
                "outline": "奇美兰简介\n- 品种介绍\n- 生长环境\n- 养护方法",
                "history": [],
                "rel_threshold": 0.4,
                "red_threshold": 0.6,
                "require_source_citations": False,
                "min_source_citations": 1,
            }
            
            response = requests.post(
                f"{url}/verify",
                json=payload,
                timeout=120  # 给 Verifier 足够的时间计算
            )
            print(f"      ✅ HTTP {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                print(f"      相关性得分: {result.get('rel_score', 'N/A')}")
                print(f"      冗余度得分: {result.get('red_score', 'N/A')}")
                print(f"      验证结果: {'通过' if result.get('is_passed') else '未通过'}")
                if not result.get('is_passed'):
                    print(f"      建议: {result.get('advice', 'N/A')}")
            else:
                print(f"      错误: {response.text[:200]}")
                
        except requests.Timeout:
            print(f"      ❌ 超时 (90s+): 服务可能无响应或处理缓慢")
        except requests.ConnectionError as e:
            print(f"      ❌ 连接失败: {e}")
        except Exception as e:
            print(f"      ❌ 错误: {type(e).__name__}: {e}")

def test_render_deployment():
    """检查 Render 部署状态"""
    print("\n" + "=" * 60)
    print("📋 Render 部署检查")
    print("=" * 60)
    print("\n⚠️  Verifier 在 Render 上的常见问题:")
    print("  1. 服务冷启动 (Free plan 可能需要 30-60s)")
    print("  2. 依赖缺失 (requirements.txt 不完整)")
    print("  3. 环境变量配置错误")
    print("  4. Dockerfile 配置问题")
    print("\n✅ 检查项:")
    print("  - 确认 Render Dashboard 中 flowernet-verifier 服务已 Deploy")
    print("  - 查看 Logs 是否有启动错误")
    print("  - 确认 healthCheckPath: / 返回 200")
    
if __name__ == "__main__":
    test_verifier_health()
    test_render_deployment()
