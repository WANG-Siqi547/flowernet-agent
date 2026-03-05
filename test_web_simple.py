#!/usr/bin/env python3
"""
简化的Web UI端到端测试
使用更小的文档结构以加快测试速度
"""
import requests
import time
import os

BASE_URL = "http://localhost:8010"

def test_simple_generation():
    """测试简单文档生成（1章节2小节）"""
    print("=" * 60)
    print("📝 测试 Web UI 文档生成流程")
    print("=" * 60)
    
    # 测试用例：简化的猫咪指南
    payload = {
        "topic": "家猫饲养基础知识",
        "chapter_count": 2,  # 生成2个章节（Outliner要求至少2个）
        "subsection_count": 2,  # 每章节2个小节
        "user_background": "新手养猫者",
        "extra_requirements": "简单实用"
    }
    
    print(f"\n📤 发送生成请求:")
    print(f"   主题: {payload['topic']}")
    print(f"   章节数: {payload['chapter_count']}")
    print(f"   小节数: {payload['subsection_count']}")
    print(f"   用户背景: {payload['user_background']}")
    
    # 步骤1: 调用生成接口
    print(f"\n[1] POST {BASE_URL}/api/generate")
    start_time = time.time()
    
    try:
        response = requests.post(
            f"{BASE_URL}/api/generate",
            json=payload,
            timeout=600  # 10分钟超时，因为Ollama生成较慢
        )
        elapsed = time.time() - start_time
        
        print(f"   状态码: {response.status_code}")
        print(f"   耗时: {elapsed:.2f}秒")
        
        if response.status_code != 200:
            print(f"\n❌ 生成失败:")
            print(f"   响应: {response.text[:500]}")
            return False
        
        data = response.json()
        
        if not data.get('success'):
            print(f"\n❌ 生成失败:")
            print(f"   错误: {data.get('error', 'Unknown error')}")
            return False
        
        # 验证返回数据
        document_id = data.get('document_id')
        title = data.get('title')
        content = data.get('content', '')
        stats = data.get('stats', {})
        
        print(f"\n✅ 生成成功!")
        print(f"   文档ID: {document_id}")
        print(f"   标题: {title}")
        print(f"   内容长度: {len(content)} 字符")
        print(f"   通过的小节: {stats.get('passed_subsections', 0)}")
        print(f"   总迭代次数: {stats.get('total_iterations', 0)}")
        
        if len(content) < 100:
            print(f"\n⚠️  警告: 内容太短 ({len(content)} 字符)")
            return False
        
        # 步骤2: 测试DOCX下载
        print(f"\n[2] POST {BASE_URL}/api/download-docx")
        docx_response = requests.post(
            f"{BASE_URL}/api/download-docx",
            json={"title": title, "content": content},
            timeout=30
        )
        
        print(f"   状态码: {docx_response.status_code}")
        
        if docx_response.status_code != 200:
            print(f"\n❌ DOCX下载失败:")
            print(f"   响应: {docx_response.text[:200]}")
            return False
        
        # 保存DOCX文件
        output_path = "/tmp/flowernet_test_simple.docx"
        with open(output_path, 'wb') as f:
            f.write(docx_response.content)
        
        file_size = os.path.getsize(output_path)
        print(f"   DOCX大小: {file_size:,} 字节")
        print(f"   保存路径: {output_path}")
        
        if file_size < 1000:
            print(f"\n⚠️  警告: DOCX文件太小 ({file_size} 字节)")
            return False
        
        print(f"\n{'=' * 60}")
        print(f"🎉 完整测试通过!")
        print(f"{'=' * 60}")
        print(f"总耗时: {elapsed:.2f}秒")
        print(f"文档内容: {len(content)} 字符")
        print(f"DOCX文件: {file_size:,} 字节")
        
        # 显示部分内容
        print(f"\n📄 文档预览 (前500字符):")
        print("-" * 60)
        print(content[:500])
        print("-" * 60)
        
        return True
        
    except requests.Timeout:
        elapsed = time.time() - start_time
        print(f"\n❌ 请求超时 ({elapsed:.2f}秒)")
        print("   Ollama生成可能需要更长时间，请检查服务日志")
        return False
        
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_simple_generation()
    exit(0 if success else 1)
