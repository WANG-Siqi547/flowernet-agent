"""
FlowerNet Database Integration 测试脚本
演示如何使用新的层级结构和自动数据库存储功能
"""

import http.client
import json
from datetime import datetime

# 服务 URL 配置
OUTLINER_URL = "http://localhost:8003"
GENERATOR_URL = "http://localhost:8002"


def http_post(host: str, port: int, path: str, data: dict, timeout: int = 120):
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        conn.request("POST", path, body, {"Content-Type": "application/json; charset=utf-8"})
        response = conn.getresponse()
        result_data = response.read().decode()

        if response.status != 200:
            raise Exception(f"HTTP {response.status}: {result_data[:200]}")

        if not result_data.strip():
            raise Exception("Empty response")

        return json.loads(result_data)
    finally:
        conn.close()

def test_full_workflow():
    """测试完整的文档生成工作流程"""
    
    print("=" * 80)
    print("🌸 FlowerNet 完整工作流程测试（带数据库集成）")
    print("=" * 80)
    
    # ========== 阶段 1: 生成文档大纲 ==========
    print("\n📋 阶段 1: 生成文档大纲...\n")
    
    outline_request = {
        "user_background": "我是一名计算机科学研究生，需要撰写关于人工智能的论文。",
        "user_requirements": "需要一篇介绍人工智能的文章，包括历史发展、核心技术和应用场景。",
        "max_sections": 3,
        "max_subsections_per_section": 2
    }
    
    outline_result = http_post("localhost", 8003, "/generate-outline", outline_request, timeout=120)
    
    if not outline_result.get("success"):
        print(f"❌ 大纲生成失败: {outline_result.get('error')}")
        return
    
    print(f"✅ 文档标题: {outline_result['document_title']}")
    print(f"📊 总共 {outline_result['total_subsections']} 个 subsections 需要生成\n")
    
    # ========== 阶段 2: 构建层级结构 ==========
    print("🏗️  阶段 2: 构建层级化大纲结构...\n")
    
    document_id = f"doc_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    structure = outline_result["structure"]
    
    # 将 Outliner 的输出转换为 Generator 需要的格式
    formatted_outline = []
    
    for section in structure["sections"]:
        section_data = {
            "section_id": section["id"],
            "section_title": section["title"],
            "subsections": [
                {
                    "subsection_id": subsection["id"],
                    "title": subsection["title"],
                    "outline": subsection["description"]
                }
                for subsection in section["subsections"]
            ]
        }
        formatted_outline.append(section_data)
    
    print(f"📦 文档ID: {document_id}")
    print(f"📚 Sections: {len(formatted_outline)}")
    for idx, section in enumerate(formatted_outline, 1):
        print(f"   Section {idx}: {section['section_title']}")
        for sub_idx, subsection in enumerate(section['subsections'], 1):
            print(f"      {idx}.{sub_idx} {subsection['title']}")
    
    # ========== 阶段 3: 生成完整文档 ==========
    print(f"\n📝 阶段 3: 生成完整文档（带数据库自动存储）...\n")
    
    document_request = {
        "document_id": document_id,
        "title": outline_result["document_title"],
        "outline_list": formatted_outline,
        "system_prompt": "请使用专业、学术的语言风格。",
        "rel_threshold": 0.5,
        "red_threshold": 0.7
    }
    
    print("⏳ 开始生成文档（这可能需要几分钟）...\n")
    
    document_result = http_post("localhost", 8002, "/generate_document", document_request, timeout=600)
    
    # ========== 阶段 4: 展示结果 ==========
    print("\n" + "=" * 80)
    print("📊 文档生成结果")
    print("=" * 80)
    
    print(f"\n📄 文档标题: {document_result['title']}")
    print(f"🆔 文档ID: {document_result['document_id']}")
    print(f"✅ 成功生成: {document_result['success_count']}/{document_result['total_subsections']} subsections")
    print(f"❌ 失败: {len(document_result['failed_subsections'])} subsections")
    print(f"🔄 总迭代次数: {document_result['total_iterations']}")
    print(f"🗑️  历史记录已清空: {document_result.get('history_cleared', False)}")
    
    # 打印每个 section 的详情
    print(f"\n📚 文档结构:")
    for section in document_result["sections"]:
        print(f"\n  📖 {section['section_title']}")
        print(f"     Section ID: {section['section_id']}")
        print(f"     成功: {section['success_count']}/{len(section['subsections']) + section['failed_count']} subsections")
        
        for subsection in section["subsections"]:
            print(f"\n     ✅ {subsection['subsection_title']}")
            print(f"        Subsection ID: {subsection['subsection_id']}")
            print(f"        迭代次数: {subsection['iterations']}")
            print(f"        相关性: {subsection['verification']['relevancy_index']:.4f}")
            print(f"        冗余度: {subsection['verification']['redundancy_index']:.4f}")
            print(f"        已存入数据库: {subsection['stored_in_db']}")
            print(f"        内容片段: {subsection['content'][:100]}...")
    
    # ========== 阶段 5: 验证数据库清空 ==========
    print(f"\n🔍 阶段 5: 验证数据库历史已清空...\n")
    
    history_query = {"document_id": document_id}
    history_data = http_post("localhost", 8003, "/history/get", history_query, timeout=10)
    print(f"历史记录数量: {len(history_data.get('history', []))}")
    
    if len(history_data.get('history', [])) == 0:
        print("✅ 确认：文档完成后历史记录已成功清空")
    else:
        print("⚠️  警告：历史记录未清空")
    
    print("\n" + "=" * 80)
    print("🎉 测试完成！")
    print("=" * 80)
    
    # 保存结果到文件
    output_file = f"document_{document_id}.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(document_result, f, ensure_ascii=False, indent=2)
    
    print(f"\n💾 完整结果已保存到: {output_file}")


def test_single_subsection():
    """测试单个 subsection 的生成（带数据库存储）"""
    
    print("\n" + "=" * 80)
    print("🧪 单个 Subsection 测试")
    print("=" * 80 + "\n")
    
    document_id = f"test_doc_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    request_data = {
        "outline": "介绍人工智能的历史发展",
        "initial_prompt": "请撰写一段关于人工智能历史发展的内容，字数约300字。",
        "document_id": document_id,
        "section_id": "section_1",
        "subsection_id": "subsection_1_1",
        "history": [],
        "rel_threshold": 0.5,
        "red_threshold": 0.7
    }
    
    print(f"📦 文档ID: {document_id}")
    print(f"📝 开始生成...\n")
    
    result = http_post("localhost", 8002, "/generate_section", request_data, timeout=120)
    
    if result.get("success"):
        print("✅ 生成成功！")
        print(f"🔄 迭代次数: {result['iterations']}")
        print(f"📊 相关性: {result['verification']['relevancy_index']:.4f}")
        print(f"📊 冗余度: {result['verification']['redundancy_index']:.4f}")
        print(f"💾 已存入数据库: {result.get('stored_in_db', False)}")
        print(f"\n📄 内容预览:")
        print(result['draft'][:300] + "...")
        
        # 验证数据库中的记录
        print(f"\n🔍 验证数据库记录...")
        history_data = http_post("localhost", 8003, "/history/get", {"document_id": document_id}, timeout=10)
        print(f"数据库中的记录数: {len(history_data.get('history', []))}")
        
    else:
        print(f"❌ 生成失败: {result.get('error')}")


if __name__ == "__main__":
    import sys
    
    print("\n🌸 FlowerNet Database Integration 测试工具")
    print("\n请选择测试模式:")
    print("1. 完整文档生成测试（推荐）")
    print("2. 单个 Subsection 测试")
    print("3. 退出\n")
    
    choice = input("请输入选项 (1-3): ").strip()
    
    if choice == "1":
        test_full_workflow()
    elif choice == "2":
        test_single_subsection()
    else:
        print("👋 再见！")
