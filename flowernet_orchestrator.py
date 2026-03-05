"""
FlowerNet 完整编排器 - 按照你的完整需求实现

流程说明：
第一步（Outliner）：
  1. 调用LLM生成整篇文章的大纲
  2. 根据大纲生成每个section和subsection的详细大纲
  3. 所有大纲存储到数据库

第二步（Generator）：
  1. 根据大纲生成第一个subsection
  2. 内容传给Verifier检测
  3. 如果通过，存储到数据库供下一个subsection使用
  4. 如果不通过，进入第三步

第三步（Controller循环）：
  1. Controller从数据库提取未通过的subsection大纲
  2. 修改大纲传给Generator
  3. Generator再次生成
  4. 传给Verifier检测
  5. 循环直到通过

关键点：
- subsection和section一个一个生成
- 上一个subsection合格才能生成下一个
- history在下一个subsection生成时被提取出来
- history也在Verifier验证时使用
"""

import requests
import json
from typing import Optional, Dict, Any, List
from datetime import datetime


class DocumentGenerationOrchestrator:
    """
    文档生成编排器 - 完整流程控制
    """
    
    def __init__(
        self,
        generator_url: str = "http://localhost:8002",
        verifier_url: str = "http://localhost:8000",
        controller_url: str = "http://localhost:8001",
        outliner_url: str = "http://localhost:8003",
        max_iterations: int = 5,
        history_manager: Optional[Any] = None
    ):
        """初始化编排器"""
        self.generator_url = generator_url
        self.verifier_url = verifier_url
        self.controller_url = controller_url
        self.outliner_url = outliner_url
        self.max_iterations = max_iterations
        self.history_manager = history_manager
        self.session = requests.Session()
        self.session.trust_env = False
        
        # 用于本地 HTTP 调用优化
        self._local_generator = None
        self._local_verifier = None
        self._local_controller = None

    def set_local_generator(self, generator):
        """设置本地Generator实例，避免HTTP自调用"""
        self._local_generator = generator
        print("✅ Orchestrator已绑定本地Generator实例")
    
    def generate_document(
        self,
        document_id: str,
        title: str,
        structure: Dict[str, Any],  # 从 Outliner 返回的结构
        content_prompts: List[Dict[str, Any]],  # 从 Outliner 返回的 content_prompts
        user_background: str,
        user_requirements: str,
        rel_threshold: float = 0.6,
        red_threshold: float = 0.7
    ) -> Dict[str, Any]:
        """
        完整文档生成流程
        
        按照结构，逐个 section/subsection 生成，每个通过才能生成下一个
        """
        print(f"\n{'='*70}")
        print(f"📚 开始生成文档: {title}")
        print(f"{'='*70}")
        print(f"Document ID: {document_id}")
        print(f"Section 数: {len(structure.get('sections', []))}")
        print(f"总 Subsection 数: {len(content_prompts)}")
        
        document_result = {
            "success": True,
            "document_id": document_id,
            "title": title,
            "sections": [],
            "passed_subsections": 0,
            "failed_subsections": [],
            "total_iterations": 0,
            "generation_time": None
        }
        
        start_time = datetime.now()
        
        try:
            # 为每个 subsection 创建追踪记录并加载其大纲
            for content_prompt_info in content_prompts:
                section_id = content_prompt_info["section_id"]
                subsection_id = content_prompt_info["subsection_id"]
                section_title = content_prompt_info["section_title"]
                subsection_title = content_prompt_info["subsection_title"]
                subsection_desc = content_prompt_info.get("subsection_description", "")
                
                # 创建 subsection 追踪记录
                if self.history_manager:
                    self.history_manager.create_subsection_tracking(
                        document_id=document_id,
                        section_id=section_id,
                        subsection_id=subsection_id,
                        outline=subsection_desc
                    )
            
            # 逐个生成 subsection
            passed_history = []  # 用于累积已通过的 subsection
            
            for section in structure.get("sections", []):
                section_id = section["id"]
                section_title = section["title"]
                
                section_result = {
                    "section_id": section_id,
                    "section_title": section_title,
                    "subsections": []
                }
                
                subsection_list = section.get("subsections", [])
                
                for subsection_index, subsection in enumerate(subsection_list):
                    subsection_id = subsection["id"]
                    subsection_title = subsection["title"]
                    subsection_desc = subsection.get("description", "")
                    
                    # 获取这个 subsection 的 content prompt
                    content_prompt = None
                    for cp in content_prompts:
                        if cp["section_id"] == section_id and cp["subsection_id"] == subsection_id:
                            content_prompt = cp.get("content_prompt", "")
                            break
                    
                    print(f"\n📖 生成 Section: {section_title} > Subsection: {subsection_title}")
                    print(f"   (顺序: {subsection_index + 1}/{len(subsection_list)})")
                    
                    # 调用生成和验证循环
                    subsection_gen_result = self._generate_and_verify_subsection(
                        document_id=document_id,
                        section_id=section_id,
                        subsection_id=subsection_id,
                        outline=subsection_desc,
                        initial_prompt=content_prompt or f"请你作为专家，写作关于\"{subsection_title}\"的内容。\n\n要求：{subsection_desc}",
                        passed_history=passed_history,  # 传递已通过的历史
                        rel_threshold=rel_threshold,
                        red_threshold=red_threshold
                    )
                    
                    document_result["total_iterations"] += subsection_gen_result.get("iterations", 0)
                    
                    if subsection_gen_result.get("success"):
                        # 这个 subsection 通过了
                        generated_content = subsection_gen_result.get("draft", "")
                        
                        # 添加到已通过历史
                        history_order = len(passed_history)
                        passed_history.append({
                            "section_id": section_id,
                            "subsection_id": subsection_id,
                            "content": generated_content
                        })
                        
                        # 保存到数据库的已通过历史
                        if self.history_manager:
                            self.history_manager.add_passed_history(
                                document_id=document_id,
                                section_id=section_id,
                                subsection_id=subsection_id,
                                content=generated_content,
                                order_index=history_order
                            )
                        
                        document_result["passed_subsections"] += 1
                        
                        section_result["subsections"].append({
                            "subsection_id": subsection_id,
                            "subsection_title": subsection_title,
                            "success": True,
                            "iterations": subsection_gen_result.get("iterations", 0),
                            "verification": subsection_gen_result.get("verification", {}),
                            "length": len(generated_content)
                        })
                        
                    else:
                        # 这个 subsection 失败了
                        document_result["failed_subsections"].append({
                            "section_id": section_id,
                            "subsection_id": subsection_id,
                            "error": subsection_gen_result.get("error", "Unknown error")
                        })
                        
                        section_result["subsections"].append({
                            "subsection_id": subsection_id,
                            "subsection_title": subsection_title,
                            "success": False,
                            "error": subsection_gen_result.get("error", "Unknown error")
                        })
                
                document_result["sections"].append(section_result)
            
            # 清空已通过历史（文档完成）
            if self.history_manager:
                self.history_manager.clear_passed_history(document_id)
            
            elapsed = (datetime.now() - start_time).total_seconds()
            document_result["generation_time"] = f"{elapsed:.2f}s"
            
            print(f"\n{'='*70}")
            print(f"✅ 文档生成完成！")
            print(f"   - 通过: {document_result['passed_subsections']}")
            print(f"   - 失败: {len(document_result['failed_subsections'])}")
            print(f"   - 总迭代: {document_result['total_iterations']} 次")
            print(f"   - 耗时: {document_result['generation_time']}")
            print(f"{'='*70}")
            
            return document_result
            
        except Exception as e:
            print(f"❌ 文档生成失败: {e}")
            import traceback
            traceback.print_exc()
            
            return {
                "success": False,
                "error": str(e),
                "document_id": document_id
            }
    
    def _generate_and_verify_subsection(
        self,
        document_id: str,
        section_id: str,
        subsection_id: str,
        outline: str,
        initial_prompt: str,
        passed_history: List[Dict[str, str]],
        rel_threshold: float = 0.6,
        red_threshold: float = 0.7
    ) -> Dict[str, Any]:
        """
        生成单个 subsection 的完整循环（第二步和第三步）
        
        流程：
        1. Generator 根据大纲和已通过历史生成内容
        2. Verifier 验证
        3. 如果不通过，Controller 修改大纲
        4. 循环回步骤1
        """
        
        current_prompt = initial_prompt
        current_outline = outline
        iterations = 0
        all_drafts = []
        
        # 构建历史文本
        if passed_history:
            history_text = "\n\n---\n\n".join([h["content"] for h in passed_history])
        else:
            history_text = ""
        
        print(f"   📜 已通过的前置内容数: {len(passed_history)}")
        
        while iterations < self.max_iterations:
            iterations += 1
            print(f"\n      尝试 {iterations}/{self.max_iterations}")
            
            # 第二步：Generator 生成内容
            print(f"         🎯 调用 Generator...")
            
            # 增强 prompt 以利用大纲和历史
            enhanced_prompt = self._build_enhanced_prompt(
                original_prompt=current_prompt,
                outline=current_outline,
                history_text=history_text
            )
            
            gen_result = self._call_generator(enhanced_prompt)
            
            if not gen_result.get("success"):
                return {
                    "success": False,
                    "error": f"Generator 错误: {gen_result.get('error')}",
                    "iterations": iterations
                }
            
            draft = gen_result.get("draft", "")
            all_drafts.append(draft)
            print(f"         ✅ 生成 {len(draft)} 字符")
            
            # Verifier 验证（使用已通过历史）
            print(f"         🔍 调用 Verifier...")
            verify_result = self._call_verifier(
                draft=draft,
                outline=current_outline,
                history=[h["content"] for h in passed_history],
                rel_threshold=rel_threshold,
                red_threshold=red_threshold
            )
            
            if not verify_result.get("success"):
                return {
                    "success": False,
                    "error": f"Verifier 错误",
                    "iterations": iterations
                }
            
            is_passed = verify_result.get("is_passed", False)
            rel_score = verify_result.get("relevancy_index", 0)
            red_score = verify_result.get("redundancy_index", 0)
            feedback = verify_result.get("feedback", "")
            
            print(f"         相关性: {rel_score:.4f}, 冗余度: {red_score:.4f}")
            
            # 如果通过，返回成功
            if is_passed:
                print(f"         ✨ 验证通过!")
                
                # 更新数据库的 subsection 追踪
                if self.history_manager:
                    self.history_manager.update_subsection_content(
                        document_id=document_id,
                        section_id=section_id,
                        subsection_id=subsection_id,
                        generated_content=draft,
                        relevancy_index=rel_score,
                        redundancy_index=red_score,
                        is_passed=True,
                        iteration_count=iterations
                    )
                
                return {
                    "success": True,
                    "draft": draft,
                    "iterations": iterations,
                    "verification": {
                        "relevancy_index": rel_score,
                        "redundancy_index": red_score,
                        "feedback": feedback
                    },
                    "all_drafts": all_drafts
                }
            
            # 如果没有通过，调用 Controller 修改大纲
            print(f"         🔧 调用 Controller...")
            controller_result = self._call_controller(
                old_outline=current_outline,
                failed_draft=draft,
                feedback=verify_result,
                outline=outline  # 原始大纲
            )
            
            if controller_result.get("success"):
                current_outline = controller_result.get("improved_outline", current_outline)
                print(f"         ✅ 大纲已改进")
            else:
                print(f"         ⚠️  Controller 返回失败")
        
        # 达到最大迭代次数
        print(f"         ⚠️  达到最大迭代次数")
        
        if all_drafts:
            # 返回最后一个 draft 作为降级方案
            return {
                "success": True,
                "draft": all_drafts[-1],
                "iterations": iterations,
                "warning": "达到最大迭代次数，内容可能不完全符合要求"
            }
        
        return {
            "success": False,
            "error": "无法生成满足要求的内容",
            "iterations": iterations
        }
    
    def _build_enhanced_prompt(
        self,
        original_prompt: str,
        outline: str,
        history_text: str
    ) -> str:
        """
        构建增强的生成提示，包含大纲和历史context
        """
        enhanced = f"""
你正在编写一篇文档的特定部分。

【当前部分的大纲和要求】
{outline}

"""
        
        if history_text:
            enhanced += f"""【前面已生成的内容（作为参考，避免重复）】
{history_text}

【生成要求】
- 基于上述大纲，生成新的内容
- 与前面的内容保持逻辑连贯
- 避免与前面内容重复或冗余
- 确保内容与大纲高度相关
- 字数控制在 300-500 字

"""
        
        enhanced += f"""【原始生成指令】
{original_prompt}

请直接输出所需的内容，不要添加任何前言或后言。
"""
        
        return enhanced.strip()
    
    def _call_generator(self, prompt: str) -> Dict[str, Any]:
        """调用 Generator API（优先使用本地实例）"""
        if self._local_generator is not None:
            try:
                return self._local_generator.generate_draft(prompt=prompt, max_tokens=800)
            except Exception as e:
                print(f"⚠️ 本地Generator调用失败: {e}，回退到HTTP调用")

        try:
            response = self.session.post(
                f"{self.generator_url}/generate",
                json={"prompt": prompt, "max_tokens": 800},
                timeout=120
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                return {
                    "success": False,
                    "error": f"HTTP {response.status_code}"
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def _call_verifier(
        self,
        draft: str,
        outline: str,
        history: List[str],
        rel_threshold: float,
        red_threshold: float
    ) -> Dict[str, Any]:
        """调用 Verifier API"""
        try:
            response = self.session.post(
                f"{self.verifier_url}/verify",
                json={
                    "draft": draft,
                    "outline": outline,
                    "history": history,
                    "rel_threshold": rel_threshold,
                    "red_threshold": red_threshold
                },
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                result["success"] = True
                return result
            else:
                return {
                    "success": False,
                    "error": f"HTTP {response.status_code}"
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def _call_controller(
        self,
        old_outline: str,
        failed_draft: str,
        feedback: Dict[str, Any],
        outline: str
    ) -> Dict[str, Any]:
        """调用 Controller API 改进大纲"""
        try:
            response = self.session.post(
                f"{self.controller_url}/improve-outline",
                json={
                    "original_outline": outline,
                    "current_outline": old_outline,
                    "failed_draft": failed_draft,
                    "feedback": feedback
                },
                timeout=60
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                return {
                    "success": False,
                    "error": f"HTTP {response.status_code}"
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

