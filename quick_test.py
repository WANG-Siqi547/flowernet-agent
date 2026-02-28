#!/usr/bin/env python3
"""快速测试 Outliner generate-outline 端点"""
import requests
import json

try:
    print("测试 Outliner /generate-outline 端点...")
    payload = {
        "user_background": "AI researcher",
        "user_requirements": "test workflow"
    }
    
    print(f"发送: {json.dumps(payload)}")
    resp = requests.post(
        "http://localhost:8003/generate-outline",
        json=payload,
        timeout=30
    )
    
    print(f"状态: HTTP {resp.status_code}")
    print(f"响应长度: {len(resp.text)} bytes")
    
    if resp.status_code == 200:
        data = resp.json()
        print(f"✅ 成功")
        print(f"   文档标题: {data.get('document_title')}")
        print(f"   章节数: {len(data['structure']['sections'])}")
        for i, sec in enumerate(data['structure']['sections']):
            print(f"      [{i+1}] {sec['title']}")
    else:
        print(f"❌ 失败: {resp.text[:200]}")
        
except Exception as e:
    print(f"❌ 错误: {e}")
    import traceback
    traceback.print_exc()
