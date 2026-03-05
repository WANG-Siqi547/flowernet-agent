#!/usr/bin/env python3
"""
最小化测试 - 验证Orchestrator本地调用修复
"""
import requests
import time

print("=" * 60)
print("🔧 测试Orchestrator本地Generator调用修复")
print("=" * 60)

# 测试payload
payload = {
    "topic": "猫咪饮食指南", 
    "chapter_count": 2,
    "subsection_count": 2,
    "user_background": "宠物主人",
    "extra_requirements": "简单实用"
}

print(f"\n📤 发送请求: {payload['topic']}")
print(f"   章节: {payload['chapter_count']}, 小节/章: {payload['subsection_count']}")

start = time.time()
try:
    resp = requests.post(
        "http://localhost:8010/api/generate",
        json=payload,
        timeout=900  # 15分钟
    )
    elapsed = time.time() - start
    
    print(f"\n⏱  耗时: {elapsed:.1f}秒")
    print(f"📊 状态码: {resp.status_code}")
    
    if resp.status_code == 200:
        data = resp.json()
        success = data.get('success', False)
        passed = data.get('stats', {}).get('passed_subsections', 0)
        total_iter = data.get('stats', {}).get('total_iterations', 0)
        content = data.get('content', '')
        
        print(f"\n✅ 生成成功: {success}")
        print(f"📝 通过的小节: {passed} / 4")
        print(f"🔄 总迭代次数: {total_iter}")
        print(f"📄 内容长度: {len(content)} 字符")
        
        if passed > 0:
            print(f"\n🎉 修复成功！至少有{passed}个小节通过验证")
            print(f"\n内容预览 (前200字符):")
            print("-" * 60)
            print(content[:200])
            print("-" * 60)
            exit(0)
        else:
            print(f"\n⚠️  警告: 没有小节通过验证")
            print(f"响应: {resp.text[:500]}")
            exit(1)
    else:
        print(f"\n❌ 请求失败")
        print(f"响应: {resp.text[:500]}")
        exit(1)
        
except Exception as e:
    print(f"\n❌ 异常: {e}")
    import traceback
    traceback.print_exc()
    exit(1)
