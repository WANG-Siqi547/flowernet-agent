#!/usr/bin/env python3
"""
完整流程测试脚本 - 演示所有需求的实现

流程：
1. 调用 Outliner 生成并保存整篇文章的大纲
2. 调用 Outliner 生成每个 section/subsection 的详细大纲并保存
3. 调用 Generator 按照大纲逐个生成 subsection
4. 每个 subsection 生成后传给 Verifier 检测
5. 如果不通过，Controller 改进大纲，重复 3-4
6. 通过后存储到数据库，作为下一个 subsection 的历史
7. 所有 subsection 完成后文档生成完毕
"""

import requests
import json
import time
from typing import Dict, Any
from datetime import datetime

class FlowerNetE2ETest:
    def __init__(self):
        self.outliner_url = "http://localhost:8003"
        self.generator_url = "http://localhost:8002"
        self.verifier_url = "http://localhost:8000"
        self.controller_url = "http://localhost:8001"
        self.session = requests.Session()
    
    def test_complete_flow(self):
        """完整流程测试"""
        print("\n" + "="*80)
        print("🌸 FlowerNet 完整流程端到端测试")
        print("="*80)
        
        document_id = f"e2e_complete_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # 第一步：Outliner 生成并保存大纲
        print("\n【第一步】Outliner 生成并保存大纲")
        print("-" * 80)
        
        outline_result = self._generate_and_save_outlines(document_id)
        if not outline_result.get("success"):
            print(f"❌ 大纲生成失败: {outline_result.get('error')}")
            return False
        
        print(f"✅ 大纲生成完成")
        print(f"   - 文档标题: {outline_result['document_title']}")
        print(f"   - Sections: {outline_result['section_count']}")
        print(f"   - Subsections: {outline_result['subsection_outlines_count']}")
        
        structure = outline_result["structure"]
        content_prompts = outline_result["content_prompts"]
        
        # 第二至第三步：Generator 逐个生成 subsection 并验证
        print("\n【第二、三步】Generator 逐个生成 subsection 并循环验证")
        print("-" * 80)
        
        doc_generation_result = self._generate_document(
            document_id=document_id,
            title=outline_result["document_title"],
            structure=structure,
            content_prompts=content_prompts
        )
        
        if not doc_generation_result.get("success"):
            print(f"❌ 文档生成失败: {doc_generation_result.get('error')}")
            return False
        
        # 展示结果
        print(f"\n✅ 文档生成完成")
        print(f"   - 通过 Subsections: {doc_generation_result['passed_subsections']}")
        print(f"   - 失败 Subsections: {len(doc_generation_result['failed_subsections'])}")
        print(f"   - 总迭代次数: {doc_generation_result['total_iterations']}")
        print(f"   - 耗时: {doc_generation_result.get('generation_time', 'N/A')}")
        
        # 展示详细的 Section 信息
        print(f"\n【生成详情】")
        for section in doc_generation_result.get("sections", []):
            section_title = section["section_title"]
            subsection_count = len(section["subsections"])
            passed_count = sum(1 for s in section["subsections"] if s.get("success", False))
            
            print(f"\n📖 Section: {section_title}")
            print(f"   Subsections: {passed_count}/{subsection_count} 通过")
            
            for subsection in section["subsections"]:
                status = "✅" if subsection.get("success") else "❌"
                title = subsection.get("subsection_title", "Unknown")
                print(f"      {status} {title}")
                
                if subsection.get("success"):
                    rel = subsection.get("verification", {}).get("relevancy_index", 0)
                    red = subsection.get("verification", {}).get("redundancy_index", 0)
                    iterations = subsection.get("iterations", 0)
                    length = subsection.get("length", 0)
                    print(f"         相关性: {rel:.4f}, 冗余度: {red:.4f}, 迭代: {iterations}, 长度: {length}")
                else:
                    error = subsection.get("error", "Unknown")
                    print(f"         错误: {error}")
        
        print("\n" + "="*80)
        print("🎉 完整流程测试完成！")
        print("="*80)
        
        return True
    
    def _generate_and_save_outlines(self, document_id: str) -> Dict[str, Any]:
        """第一步：生成并保存大纲"""
        
        payload = {
            "document_id": document_id,
            "user_background": "我是一名计算机科学学生，需要撰写关于人工智能的学术论文。",
            "user_requirements": "需要一篇全面介绍人工智能的文章，包括定义、核心技术和应用。要求专业、逻辑清晰。",
            "max_sections": 3,
            "max_subsections_per_section": 2
        }
        
        try:
            print(f"📡 调用 Outliner: /outline/generate-and-save")
            response = self.session.post(
                f"{self.outliner_url}/outline/generate-and-save",
                json=payload,
                timeout=300
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                return {
                    "success": False,
                    "error": f"HTTP {response.status_code}: {response.text}"
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def _generate_document(
        self,
        document_id: str,
        title: str,
        structure: Dict[str, Any],
        content_prompts: list
    ) -> Dict[str, Any]:
        """第二、三步：生成文档"""
        
        payload = {
            "document_id": document_id,
            "title": title,
            "structure": structure,
            "content_prompts": content_prompts,
            "user_background": "我是一名计算机科学学生，需要撰写关于人工智能的学术论文。",
            "user_requirements": "需要一篇全面介绍人工智能的文章，包括定义、核心技术和应用。要求专业、逻辑清晰。",
            "rel_threshold": 0.5,
            "red_threshold": 0.7
        }
        
        try:
            print(f"📡 调用 Generator: /generate_document (完整流程版)")
            response = self.session.post(
                f"{self.generator_url}/generate_document",
                json=payload,
                timeout=600  # 可能需要很长时间
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"Response status: {response.status_code}")
                print(f"Response text: {response.text[:500]}")
                return {
                    "success": False,
                    "error": f"HTTP {response.status_code}"
                }
        except Exception as e:
            print(f"Exception: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e)
            }


if __name__ == "__main__":
    # 检查服务
    print("\n🔍 检查服务状态...")
    services = {
        "Outliner (8003)": "http://localhost:8003/",
        "Generator (8002)": "http://localhost:8002/",
        "Verifier (8000)": "http://localhost:8000/",
        "Controller (8001)": "http://localhost:8001/",
    }
    
    all_online = True
    for name, url in services.items():
        try:
            r = requests.get(url, timeout=3)
            if r.status_code == 200:
                print(f"   ✅ {name}")
            else:
                print(f"   ⚠️  {name} (HTTP {r.status_code})")
                all_online = False
        except Exception as e:
            print(f"   ❌ {name} - {e}")
            all_online = False
    
    if not all_online:
        print("\n⚠️  某些服务离线，测试可能失败")
        response = input("是否继续? [y/N]: ")
        if response.lower() != 'y':
            exit(1)
    
    # 运行测试
    tester = FlowerNetE2ETest()
    success = tester.test_complete_flow()
    
    if success:
        print("\n✨ 测试成功！")
        exit(0)
    else:
        print("\n❌ 测试失败！")
        exit(1)

