#!/usr/bin/env python3
"""
直接测试文档生成API，打印详细输出
"""
import requests
import json

# 先测试Outliner
print("=" * 60)
print("步骤1: 调用Outliner生成大纲")
print("=" * 60)

outline_payload = {
    "user_background": "新手养猫者",
    "user_requirements": "介绍家猫饲养基础知识，2个章节，每章节2个小节，简单实用",
    "max_sections": 2,
    "max_subsections_per_section": 2
}

outline_resp = requests.post('http://localhost:8003/generate-outline', json=outline_payload, timeout=60)
print(f"状态码: {outline_resp.status_code}")

if outline_resp.status_code == 200:
    outline_data = outline_resp.json()
    print(f"成功: {outline_data.get('success')}")
    print(f"标题: {outline_data.get('document_title')}")
    print(f"结构: {json.dumps(outline_data.get('structure'), indent=2, ensure_ascii=False)[:500]}...")
    print(f"Content Prompts总数: {len(outline_data.get('content_prompts', []))}")
    
    # 步骤2: 测试Generator
    print("\n" + "=" * 60)
    print("步骤2: 调用Generator生成文档")
    print("=" * 60)
    
    gen_payload = {
        "document_id": "test_debug_001",
        "title": outline_data.get('document_title'),
        "structure": outline_data.get('structure'),
        "content_prompts": outline_data.get('content_prompts', []),
        "user_background": "新手养猫者",
        "user_requirements": "简单实用",
        "rel_threshold": 0.6,
        "red_threshold": 0.7
    }
    
    print(f"发送请求到Generator...")
    gen_resp = requests.post('http://localhost:8002/generate_document', json=gen_payload, timeout=600)
    print(f"状态码: {gen_resp.status_code}")
    
    if gen_resp.status_code == 200:
        gen_data = gen_resp.json()
        print(f"\n生成结果:")
        print(f"  成功: {gen_data.get('success')}")
        print(f"  文档ID: {gen_data.get('document_id')}")
        print(f"  通过的小节: {gen_data.get('passed_subsections')}")
        print(f"  失败的小节: {gen_data.get('failed_subsections')}")
        print(f"  总迭代: {gen_data.get('total_iterations')}")
        
        content_items = gen_data.get('content', [])
        print(f"  内容项数: {len(content_items)}")
        
        for idx, item in enumerate(content_items[:2], 1):
            print(f"\n  [{idx}] {item.get('section_id')}::{item.get('subsection_id')}")
            content = item.get('content', '')
            print(f"      长度: {len(content)} 字符")
            print(f"      预览: {content[:150]}...")
    else:
        print(f"错误: {gen_resp.text[:500]}")
else:
    print(f"错误: {outline_resp.text[:500]}")
