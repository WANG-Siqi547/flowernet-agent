#!/usr/bin/env python3
"""
简化的数据库集成测试
"""

import http.client
import json
import time
from datetime import datetime

# 颜色输出
class bcolors:
    OKGREEN = '\033[92m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'

def print_success(msg):
    print(f"{bcolors.OKGREEN}✅ {msg}{bcolors.ENDC}")

def print_error(msg):
    print(f"{bcolors.FAIL}❌ {msg}{bcolors.ENDC}")

def http_post(host, port, path, data, timeout=120):
    """发送 HTTP POST 请求"""
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        body = json.dumps(data)
        conn.request("POST", path, body, {"Content-Type": "application/json"})
        response = conn.getresponse()
        result_data = response.read().decode()
        
        if response.status != 200:
            raise Exception(f"HTTP {response.status}")
        
        if not result_data.strip():
            raise Exception("Empty response")
        
        return json.loads(result_data)
    finally:
        conn.close()

print("\n🌸 FlowerNet 数据库集成 - 简化测试\n")

# 测试 1: 生成大纲
print("=" * 60)
print("测试 1: 生成文档大纲")
print("=" * 60)

try:
    outliner_resp = http_post("localhost", 8003, "/generate-outline", {
        "user_background": "我需要一篇关于AI的介绍",
        "user_requirements": "简要介绍人工智能的定义和应用",
        "max_sections": 2,
        "max_subsections_per_section": 2
    }, timeout=60)
    
    print_success(f"大纲生成成功")
    print(f"  标题: {outliner_resp['document_title']}")
    print(f"  Section 数量: {len(outliner_resp['structure']['sections'])}")
    
except Exception as e:
    print_error(f"大纲使生成失败: {e}")
    import traceback
    traceback.print_exc()

# 测试 2: 生成单个 subsection
print("\n" + "=" * 60)
print("测试 2: 生成单个 Subsection")
print("=" * 60)

try:
    doc_id = f"test_{datetime.now().strftime('%s')}"
    
    gen_resp = http_post("localhost", 8002, "/generate_section", {
        "outline": "介绍人工智能的定义",
        "initial_prompt": "请写一段 200 字的文字介绍人工智能的定义",
        "document_id": doc_id,
        "section_id": "section_1",
        "subsection_id": "subsection_1_1",
        "history": [],
        "rel_threshold": 0.4,
        "red_threshold": 0.7
    }, timeout=120)
    
    if gen_resp.get("success"):
        print_success(f"生成成功 (耗时 {gen_resp.get('iterations', 0)} 迭代)")
        print(f"  内容长度: {len(gen_resp.get('draft', ''))} 字符")
        print(f"  相关性: {gen_resp.get('verification', {}).get('relevancy_index', 'N/A')}")
        print(f"  冗余度: {gen_resp.get('verification', {}).get('redundancy_index', 'N/A')}")
        print(f"  已存入数据库: {gen_resp.get('stored_in_db', False)}")
    else:
        print_error(f"生成失败: {gen_resp.get('error')}")
        
except Exception as e:
    print_error(f"测试失败: {e}")
    import traceback
    traceback.print_exc()

# 测试 3: 验证历史数据库
print("\n" + "=" * 60)
print("测试 3: 查询历史数据库")
print("=" * 60)

try:
    history_resp = http_post("localhost", 8003, "/history/get", {
        "document_id": doc_id
    }, timeout=10)
    
    history_count = len(history_resp.get('history', []))
    print_success(f"数据库查询成功")
    print(f"  找到 {history_count} 条记录") 
    
    if history_count > 0:
        for entry in history_resp['history'][:3]:  # 只显示前 3 条
            print(f"    - {entry.get('section_id')}/{entry.get('subsection_id')}: {len(entry.get('content', ''))} 字符")
    
except Exception as e:
    print_error(f"查询失败: {e}")

print("\n🎉 测试完成!\n")
