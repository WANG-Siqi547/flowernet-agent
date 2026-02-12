"""
FlowerNet Outliner - 文档大纲生成与内容提示词管理
根据用户需求生成文档结构，并为每个段落生成专用的 Content Prompt
"""

import os
import json
from typing import Optional, Dict, Any, List
from google import genai
from google.genai import types


class FlowerNetOutliner:
    """
    文档大纲生成器
    - 第一阶段：使用 Document Structure Prompt 生成层级大纲
    - 第二阶段：为每个 subsection 生成 Content Prompt
    """
    
    def __init__(self, api_key: Optional[str] = None, model: str = "models/gemini-2.5-flash"):
        """
        初始化 Outliner
        
        Args:
            api_key: Google API Key（默认从环境变量读取）
            model: 使用的 Gemini 模型
        """
        self.api_key = api_key or os.getenv('GOOGLE_API_KEY', '')
        if not self.api_key:
            raise ValueError("请设置 GOOGLE_API_KEY 环境变量或传入 api_key 参数")
        
        self.model = model
        self.client = genai.Client(api_key=self.api_key)
        
        print(f"✅ Outliner 初始化成功:")
        print(f"  - Model: {self.model}")
        print(f"  - Provider: Google Gemini")
    
    def generate_document_structure(
        self,
        user_background: str,
        user_requirements: str,
        max_sections: int = 5,
        max_subsections_per_section: int = 4
    ) -> Dict[str, Any]:
        """
        阶段 1: 生成文档结构大纲
        
        Args:
            user_background: 用户提供的背景信息
            user_requirements: 用户需求描述
            max_sections: 最大 section 数量
            max_subsections_per_section: 每个 section 最大 subsection 数量
            
        Returns:
            {
                "title": "文档标题",
                "sections": [
                    {
                        "id": "section_1",
                        "title": "Section 标题",
                        "subsections": [
                            {"id": "subsection_1_1", "title": "子标题", "description": "简述"},
                            ...
                        ]
                    },
                    ...
                ]
            }
        """
        # 构建 Document Structure Prompt
        structure_prompt = f"""
你是一个专业的文档结构设计专家。根据用户提供的背景和需求，生成一个详细的文档大纲。

**用户背景**:
{user_background}

**用户需求**:
{user_requirements}

**任务要求**:
1. 生成一个清晰的文档标题
2. 将文档分为 {max_sections} 个左右的主要章节（Section）
3. 每个章节包含 {max_subsections_per_section} 个左右的子章节（Subsection）
4. 每个子章节需要有标题和简短描述（1-2句话说明该段应该写什么）

**输出格式**（严格按照 JSON 格式）:
{{
  "title": "文档总标题",
  "sections": [
    {{
      "id": "section_1",
      "title": "第一章标题",
      "subsections": [
        {{
          "id": "subsection_1_1",
          "title": "第一节标题",
          "description": "该节应该介绍...（1-2句话）"
        }},
        {{
          "id": "subsection_1_2",
          "title": "第二节标题",
          "description": "该节应该讨论...（1-2句话）"
        }}
      ]
    }},
    {{
      "id": "section_2",
      "title": "第二章标题",
      "subsections": [...]
    }}
  ]
}}

请直接输出 JSON，不要添加任何额外说明或代码块标记。
"""
        
        try:
            # 调用 LLM 生成大纲
            response = self.client.models.generate_content(
                model=self.model,
                contents=structure_prompt,
                config=types.GenerateContentConfig(
                    temperature=0.7,
                    max_output_tokens=4000
                )
            )
            
            # 解析 JSON
            structure_text = response.text.strip()
            
            # 移除可能的代码块标记
            if structure_text.startswith("```json"):
                structure_text = structure_text[7:]
            if structure_text.startswith("```"):
                structure_text = structure_text[3:]
            if structure_text.endswith("```"):
                structure_text = structure_text[:-3]
            
            structure = json.loads(structure_text.strip())
            
            print(f"✅ 文档大纲生成成功:")
            print(f"  - 标题: {structure.get('title', 'N/A')}")
            print(f"  - Sections: {len(structure.get('sections', []))}")
            
            return {
                "success": True,
                "structure": structure,
                "metadata": {
                    "model": self.model,
                    "prompt_tokens": response.usage_metadata.prompt_token_count if hasattr(response, 'usage_metadata') else 0,
                    "output_tokens": response.usage_metadata.candidates_token_count if hasattr(response, 'usage_metadata') else 0,
                }
            }
            
        except json.JSONDecodeError as e:
            print(f"❌ JSON 解析失败: {e}")
            print(f"原始输出: {response.text[:500]}")
            return {
                "success": False,
                "error": f"JSON 解析失败: {str(e)}",
                "raw_output": response.text
            }
        except Exception as e:
            print(f"❌ 大纲生成失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def generate_content_prompts(
        self,
        structure: Dict[str, Any],
        user_background: str,
        user_requirements: str
    ) -> List[Dict[str, Any]]:
        """
        阶段 2: 为每个 subsection 生成 Content Prompt
        
        Args:
            structure: 阶段 1 生成的文档结构
            user_background: 用户背景
            user_requirements: 用户需求
            
        Returns:
            [
                {
                    "section_id": "section_1",
                    "subsection_id": "subsection_1_1",
                    "section_title": "章节标题",
                    "subsection_title": "子章节标题",
                    "content_prompt": "生成该段的详细提示词",
                    "order": 1  # 生成顺序
                },
                ...
            ]
        """
        content_prompts = []
        order = 1
        
        for section in structure.get("sections", []):
            section_id = section.get("id", "")
            section_title = section.get("title", "")
            
            for subsection in section.get("subsections", []):
                subsection_id = subsection.get("id", "")
                subsection_title = subsection.get("title", "")
                subsection_desc = subsection.get("description", "")
                
                # 为该 subsection 生成 Content Prompt
                content_prompt = self._build_content_prompt(
                    document_title=structure.get("title", ""),
                    section_title=section_title,
                    subsection_title=subsection_title,
                    subsection_description=subsection_desc,
                    user_background=user_background,
                    user_requirements=user_requirements
                )
                
                content_prompts.append({
                    "section_id": section_id,
                    "subsection_id": subsection_id,
                    "section_title": section_title,
                    "subsection_title": subsection_title,
                    "subsection_description": subsection_desc,
                    "content_prompt": content_prompt,
                    "order": order
                })
                
                order += 1
        
        print(f"✅ 生成了 {len(content_prompts)} 个 Content Prompts")
        return content_prompts
    
    def _build_content_prompt(
        self,
        document_title: str,
        section_title: str,
        subsection_title: str,
        subsection_description: str,
        user_background: str,
        user_requirements: str
    ) -> str:
        """
        构建单个 subsection 的 Content Prompt
        """
        prompt = f"""
你正在撰写一篇关于"{document_title}"的文档。

**整体背景**:
{user_background}

**整体需求**:
{user_requirements}

**当前章节**: {section_title}
**当前小节**: {subsection_title}

**该小节要求**:
{subsection_description}

**写作要求**:
1. 紧扣"{subsection_title}"这个主题，确保内容相关性
2. 详细展开，字数控制在 500-800 字
3. 使用清晰的逻辑结构，可以包含小标题
4. 语言专业、准确，避免空洞内容
5. 如果涉及技术概念，需要提供具体例子或解释
6. 注意与前面已生成内容的衔接，避免重复

请直接输出该小节的正文内容，不要添加"该小节内容如下"等引导语。
"""
        return prompt.strip()
    
    def generate_full_outline(
        self,
        user_background: str,
        user_requirements: str,
        max_sections: int = 5,
        max_subsections_per_section: int = 4
    ) -> Dict[str, Any]:
        """
        完整流程: 生成文档结构 + 为每段生成 Content Prompt
        
        Returns:
            {
                "success": True,
                "document_title": "...",
                "structure": {...},
                "content_prompts": [...],
                "metadata": {...}
            }
        """
        # 阶段 1: 生成大纲
        structure_result = self.generate_document_structure(
            user_background=user_background,
            user_requirements=user_requirements,
            max_sections=max_sections,
            max_subsections_per_section=max_subsections_per_section
        )
        
        if not structure_result.get("success"):
            return structure_result
        
        structure = structure_result["structure"]
        
        # 阶段 2: 生成所有 Content Prompts
        content_prompts = self.generate_content_prompts(
            structure=structure,
            user_background=user_background,
            user_requirements=user_requirements
        )
        
        return {
            "success": True,
            "document_title": structure.get("title", ""),
            "structure": structure,
            "content_prompts": content_prompts,
            "total_subsections": len(content_prompts),
            "metadata": structure_result.get("metadata", {})
        }


# ============ 测试代码 ============

if __name__ == "__main__":
    # 测试用例
    outliner = FlowerNetOutliner()
    
    result = outliner.generate_full_outline(
        user_background="我是一名计算机科学学生，需要撰写关于人工智能的学术论文。",
        user_requirements="需要一篇全面介绍人工智能的文章，包括历史、核心技术、应用场景和未来展望。要求内容专业、逻辑清晰。",
        max_sections=4,
        max_subsections_per_section=3
    )
    
    if result["success"]:
        print(f"\n📄 文档标题: {result['document_title']}")
        print(f"📊 总共 {result['total_subsections']} 个段落需要生成\n")
        
        # 显示前 3 个 Content Prompt
        for prompt_info in result["content_prompts"][:3]:
            print(f"--- {prompt_info['section_title']} > {prompt_info['subsection_title']} ---")
            print(f"Prompt 长度: {len(prompt_info['content_prompt'])} 字符\n")
    else:
        print(f"❌ 失败: {result.get('error')}")
