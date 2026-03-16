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
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
import time
import os
import random


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
        history_manager: Optional[Any] = None,
        history_window_size: int = 5,  # 历史窗口大小：只使用最近N个小节
        max_forced_iterations: int = 15  # 兼容旧参数：不再用于强制通过
    ):
        """初始化编排器"""
        self.generator_url = generator_url
        self.verifier_url = verifier_url
        self.controller_url = controller_url
        self.outliner_url = outliner_url
        self.max_iterations = max_iterations
        self.history_manager = history_manager
        self.history_window_size = history_window_size
        self.max_forced_iterations = max_forced_iterations
        self.retry_base_delay = float(os.getenv("DOC_RETRY_BASE_DELAY", "2.0"))
        self.retry_max_delay = float(os.getenv("DOC_RETRY_MAX_DELAY", "90.0"))
        self.retry_jitter = float(os.getenv("DOC_RETRY_JITTER", "0.5"))
        self.subsection_retry_forever = os.getenv("SUBSECTION_RETRY_FOREVER", "true").lower() == "true"
        self.max_subsection_attempts = int(os.getenv("MAX_SUBSECTION_ATTEMPTS", "0"))
        self.session = requests.Session()
        self.session.trust_env = False
        
        # 用于本地 HTTP 调用优化
        self._local_generator = None
        self._local_verifier = None
        self._local_controller = None

    def _compute_retry_delay(self, attempt: int) -> float:
        base = self.retry_base_delay * (2 ** max(0, min(attempt - 1, 6)))
        delay = min(base, self.retry_max_delay)
        delay += random.uniform(0.0, self.retry_jitter)
        return min(delay, self.retry_max_delay)

    def _compute_effective_thresholds(self, iteration: int, rel_threshold: float, red_threshold: float) -> Tuple[float, float]:
        """前3轮使用严格阈值；第4轮起每轮放宽 0.02，最多放宽 0.10，保证最终收敛。"""
        relax_steps = max(0, iteration - 3)
        effective_rel = max(rel_threshold - min(0.10, 0.02 * relax_steps), rel_threshold - 0.10)
        effective_red = min(red_threshold + min(0.10, 0.02 * relax_steps), red_threshold + 0.10)
        return round(effective_rel, 4), round(effective_red, 4)

    def set_local_generator(self, generator):
        """设置本地Generator实例，避免HTTP自调用"""
        self._local_generator = generator
        print("✅ Orchestrator已绑定本地Generator实例")

    def _emit_progress_event(
        self,
        document_id: str,
        stage: str,
        message: str,
        section_id: Optional[str] = None,
        subsection_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """写入流程事件（用于前端可视化详细过程）。"""
        if not self.history_manager:
            return
        try:
            self.history_manager.add_progress_event(
                document_id=document_id,
                section_id=section_id,
                subsection_id=subsection_id,
                stage=stage,
                message=message,
                metadata=metadata or {},
            )
        except Exception as e:
            print(f"⚠️  写入流程事件失败: {e}")

    def _resolve_subsection_outline(
        self,
        document_id: str,
        section_id: str,
        subsection_id: str,
        fallback_outline: str,
    ) -> str:
        """优先从数据库读取 subsection 大纲，确保生成逻辑以已存储大纲为准。"""
        if not self.history_manager:
            return fallback_outline

        try:
            tracking = self.history_manager.get_subsection_tracking(document_id, section_id, subsection_id)
            if tracking and tracking.get("outline"):
                return str(tracking["outline"]).strip()
        except Exception as e:
            print(f"⚠️  读取 subsection tracking 失败: {e}")

        try:
            outline = self.history_manager.get_outline(
                document_id=document_id,
                outline_type="subsection",
                section_id=section_id,
                subsection_id=subsection_id,
            )
            if outline:
                return str(outline).strip()
        except Exception as e:
            print(f"⚠️  读取 subsection outline 失败: {e}")

        return fallback_outline

    def _load_passed_history(self, document_id: str) -> List[Dict[str, str]]:
        """每次进入新 subsection 前从数据库重新拉取已通过历史。"""
        if not self.history_manager:
            return []
        try:
            history = self.history_manager.get_passed_history(document_id)
            if isinstance(history, list):
                return history
        except Exception as e:
            print(f"⚠️  读取 passed history 失败: {e}")
        return []
    
    def generate_document(
        self,
        document_id: str,
        title: str,
        structure: Dict[str, Any],  # 从 Outliner 返回的结构
        content_prompts: List[Dict[str, Any]],  # 从 Outliner 返回的 content_prompts
        user_background: str,
        user_requirements: str,
        rel_threshold: float = 0.80,
        red_threshold: float = 0.40
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
        self._emit_progress_event(
            document_id=document_id,
            stage="document_start",
            message=f"文档生成已启动，目标小节数: {len(content_prompts)}",
        )
        
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
            content_prompt_map = {
                f"{cp['section_id']}::{cp['subsection_id']}": cp
                for cp in content_prompts
                if cp.get("section_id") and cp.get("subsection_id")
            }

            # 为每个 subsection 创建追踪记录，并以数据库中的正式大纲作为初始值
            for section in structure.get("sections", []):
                section_id = section["id"]
                for subsection in section.get("subsections", []):
                    subsection_id = subsection["id"]
                    prompt_info = content_prompt_map.get(f"{section_id}::{subsection_id}", {})
                    initial_outline = self._resolve_subsection_outline(
                        document_id=document_id,
                        section_id=section_id,
                        subsection_id=subsection_id,
                        fallback_outline=str(
                            prompt_info.get("subsection_outline")
                            or subsection.get("outline")
                            or prompt_info.get("subsection_description")
                            or subsection.get("description")
                            or subsection.get("title", "")
                        ).strip(),
                    )

                    if self.history_manager:
                        self.history_manager.create_subsection_tracking(
                            document_id=document_id,
                            section_id=section_id,
                            subsection_id=subsection_id,
                            outline=initial_outline,
                        )
            
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
                    prompt_info = content_prompt_map.get(f"{section_id}::{subsection_id}", {})
                    subsection_outline = self._resolve_subsection_outline(
                        document_id=document_id,
                        section_id=section_id,
                        subsection_id=subsection_id,
                        fallback_outline=str(
                            prompt_info.get("subsection_outline")
                            or subsection.get("outline")
                            or prompt_info.get("subsection_description")
                            or subsection.get("description")
                            or subsection_title
                        ).strip(),
                    )
                    content_prompt = str(prompt_info.get("content_prompt") or "").strip()
                    if not content_prompt:
                        content_prompt = f"请你作为专家，写作关于\"{subsection_title}\"的内容。\n\n要求：{subsection_outline}"
                    
                    print(f"\n📖 生成 Section: {section_title} > Subsection: {subsection_title}")
                    print(f"   (顺序: {subsection_index + 1}/{len(subsection_list)})")
                    self._emit_progress_event(
                        document_id=document_id,
                        section_id=section_id,
                        subsection_id=subsection_id,
                        stage="subsection_start",
                        message=f"开始处理小节: {section_title} > {subsection_title}",
                        metadata={
                            "section_title": section_title,
                            "subsection_title": subsection_title,
                            "subsection_order": subsection_index + 1,
                            "section_subsection_total": len(subsection_list),
                        },
                    )
                    
                    try:
                        passed_history = self._load_passed_history(document_id)
                        subsection_gen_result = self._generate_and_verify_subsection(
                            document_id=document_id,
                            section_id=section_id,
                            subsection_id=subsection_id,
                            outline=subsection_outline,
                            initial_prompt=content_prompt,
                            passed_history=passed_history,
                            rel_threshold=rel_threshold,
                            red_threshold=red_threshold
                        )
                        
                        document_result["total_iterations"] += subsection_gen_result.get("iterations", 0)
                        
                        if subsection_gen_result.get("success"):
                            generated_content = subsection_gen_result.get("draft", "")
                            verification = subsection_gen_result.get("verification", {})
                            history_order = len(passed_history)
                            
                            if self.history_manager:
                                self.history_manager.add_entry(
                                    document_id=document_id,
                                    section_id=section_id,
                                    subsection_id=subsection_id,
                                    content=generated_content,
                                    metadata={
                                        "iterations": subsection_gen_result.get("iterations", 0),
                                        "verification": verification,
                                        "outline": subsection_gen_result.get("final_outline", subsection_outline),
                                    }
                                )
                                self.history_manager.add_passed_history(
                                    document_id=document_id,
                                    section_id=section_id,
                                    subsection_id=subsection_id,
                                    content=generated_content,
                                    order_index=history_order
                                )
                            
                            document_result["passed_subsections"] += 1
                            self._emit_progress_event(
                                document_id=document_id,
                                section_id=section_id,
                                subsection_id=subsection_id,
                                stage="subsection_passed",
                                message=f"小节通过验证: {section_title} > {subsection_title}",
                                metadata={
                                    "iterations": subsection_gen_result.get("iterations", 0),
                                    "verification": verification,
                                },
                            )
                            
                            section_result["subsections"].append({
                                "subsection_id": subsection_id,
                                "subsection_title": subsection_title,
                                "content": generated_content,
                                "outline": subsection_gen_result.get("final_outline", subsection_outline),
                                "success": True,
                                "iterations": subsection_gen_result.get("iterations", 0),
                                "verification": verification,
                                "length": len(generated_content)
                            })
                            
                        else:
                            err = subsection_gen_result.get("error", "Unknown error")
                            print(f"❌ 当前小节生成失败且未通过闭环: {err}")
                            document_result["failed_subsections"].append({
                                "section_id": section_id,
                                "subsection_id": subsection_id,
                                "error": err
                            })
                            document_result["success"] = False
                            document_result["error"] = f"小节未通过且无法完成闭环: {section_title} > {subsection_title}"
                            document_result["sections"].append(section_result)
                            self._emit_progress_event(
                                document_id=document_id,
                                section_id=section_id,
                                subsection_id=subsection_id,
                                stage="subsection_failed",
                                message=f"小节未能完成闭环: {section_title} > {subsection_title}",
                                metadata={"error": err},
                            )
                            return document_result
                    
                    except Exception as e:
                        print(f"❌ 小节生成异常，中断文档流程: {e}")
                        error_str = str(e)[:200]
                        document_result["failed_subsections"].append({
                            "section_id": section_id,
                            "subsection_id": subsection_id,
                            "error": f"异常: {error_str}"
                        })
                        document_result["success"] = False
                        document_result["error"] = f"小节异常中断: {section_title} > {subsection_title}"
                        document_result["sections"].append(section_result)
                        self._emit_progress_event(
                            document_id=document_id,
                            section_id=section_id,
                            subsection_id=subsection_id,
                            stage="subsection_exception",
                            message=f"小节异常中断: {section_title} > {subsection_title}",
                            metadata={"error": error_str},
                        )
                        return document_result
                
                document_result["sections"].append(section_result)
            
            elapsed = (datetime.now() - start_time).total_seconds()
            document_result["generation_time"] = f"{elapsed:.2f}s"
            
            # 计算总小节数
            total_subsections_expected = sum(
                len(section.get("subsections", []))
                for section in structure.get("sections", [])
            )
            total_subsections_generated = (
                document_result["passed_subsections"] + 
                len(document_result["failed_subsections"])
            )

            # 严格判定：必须达到预期小节数，且所有小节都已有结果（正常通过或强制补齐）
            if document_result["passed_subsections"] < total_subsections_expected:
                document_result["success"] = False
                document_result["error"] = (
                    f"生成未达到目标小节数: 通过 {document_result['passed_subsections']}/{total_subsections_expected}, "
                    f"失败 {len(document_result['failed_subsections'])}"
                )
            
            print(f"\n{'='*70}")
            print(f"{'✅' if document_result['success'] else '❌'} 文档生成完成！")
            print(f"   - 预期小节数: {total_subsections_expected}")
            print(f"   - 实际生成: {total_subsections_generated}")
            print(f"   - 通过: {document_result['passed_subsections']}")
            print(f"   - 失败: {len(document_result['failed_subsections'])}")
            print(f"   - 总迭代: {document_result['total_iterations']} 次")
            print(f"   - 耗时: {document_result['generation_time']}")
            if not document_result["success"]:
                print(f"   - 错误: {document_result.get('error', '生成未满足严格要求')}")
            print(f"{'='*70}")
            self._emit_progress_event(
                document_id=document_id,
                stage="document_complete",
                message=(
                    f"文档流程结束：通过 {document_result['passed_subsections']}，"
                    f"失败 {len(document_result['failed_subsections'])}"
                ),
                metadata={
                    "success": document_result["success"],
                    "passed_subsections": document_result["passed_subsections"],
                    "failed_subsections": len(document_result["failed_subsections"]),
                    "total_iterations": document_result["total_iterations"],
                },
            )
            
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
        rel_threshold: float = 0.80,
        red_threshold: float = 0.40
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
        
        # 应用历史窗口：只使用最近N个小节（避免历史过长导致冗余度计算失真）
        windowed_history = passed_history[-self.history_window_size:] if passed_history else []
        
        # 构建历史文本
        if windowed_history:
            history_text = "\n\n---\n\n".join([h["content"] for h in windowed_history])
        else:
            history_text = ""
        
        total_history_count = len(passed_history)
        windowed_count = len(windowed_history)
        print(f"   📜 已通过的前置内容数: {total_history_count} (使用最近 {windowed_count} 个小节)")
        
        while True:
            iterations += 1
            if (not self.subsection_retry_forever) and self.max_subsection_attempts > 0 and iterations > self.max_subsection_attempts:
                self._emit_progress_event(
                    document_id=document_id,
                    section_id=section_id,
                    subsection_id=subsection_id,
                    stage="subsection_exhausted",
                    message=f"达到最大尝试次数 {self.max_subsection_attempts}，小节失败",
                    metadata={"iteration": iterations - 1},
                )
                return {
                    "success": False,
                    "error": f"小节未通过且达到最大尝试次数: {self.max_subsection_attempts}",
                    "final_outline": current_outline,
                    "iterations": iterations - 1,
                    "all_drafts": all_drafts,
                }

            if iterations <= self.max_iterations:
                print(f"\n      尝试 {iterations}/{self.max_iterations}")
            else:
                print(f"\n      尝试 {iterations}（超过配置迭代上限，继续严格闭环直到通过）")
            
            print(f"         🎯 调用 Generator...")
            self._emit_progress_event(
                document_id=document_id,
                section_id=section_id,
                subsection_id=subsection_id,
                stage="generator_start",
                message=f"第 {iterations} 轮：进入 Generator 生成",
                metadata={"iteration": iterations},
            )
            
            effective_rel_threshold, effective_red_threshold = self._compute_effective_thresholds(
                iteration=iterations,
                rel_threshold=rel_threshold,
                red_threshold=red_threshold,
            )

            enhanced_prompt = self._build_enhanced_prompt(
                original_prompt=current_prompt,
                outline=current_outline,
                history_text=history_text,
                rel_threshold=effective_rel_threshold,
                red_threshold=effective_red_threshold,
            )
            
            gen_result = self._call_generator(enhanced_prompt)
            
            if not gen_result.get("success"):
                print(f"         ⚠️ Generator 错误，继续重试当前小节: {gen_result.get('error')}")
                self._emit_progress_event(
                    document_id=document_id,
                    section_id=section_id,
                    subsection_id=subsection_id,
                    stage="generator_error",
                    message=f"第 {iterations} 轮：Generator 失败，准备重试",
                    metadata={"iteration": iterations, "error": gen_result.get("error", "unknown")},
                )
                time.sleep(self._compute_retry_delay(iterations))
                continue
            
            draft = gen_result.get("draft", "")
            all_drafts.append(draft)
            print(f"         ✅ 生成 {len(draft)} 字符")
            
            print(f"         🔍 调用 Verifier...")
            self._emit_progress_event(
                document_id=document_id,
                section_id=section_id,
                subsection_id=subsection_id,
                stage="verifier_start",
                message=f"第 {iterations} 轮：进入 Verifier 检测",
                metadata={"iteration": iterations},
            )
            verify_result = self._call_verifier(
                draft=draft,
                outline=current_outline,
                history=[h["content"] for h in windowed_history],
                rel_threshold=effective_rel_threshold,
                red_threshold=effective_red_threshold
            )
            
            if not verify_result.get("success"):
                print(f"         ⚠️ Verifier 错误，继续重试当前小节")
                self._emit_progress_event(
                    document_id=document_id,
                    section_id=section_id,
                    subsection_id=subsection_id,
                    stage="verifier_error",
                    message=f"第 {iterations} 轮：Verifier 调用失败，准备重试",
                    metadata={"iteration": iterations},
                )
                time.sleep(self._compute_retry_delay(iterations))
                continue
            
            is_passed = verify_result.get("is_passed", False)
            rel_score = verify_result.get("relevancy_index", 0)
            red_score = verify_result.get("redundancy_index", 0)
            feedback = verify_result.get("feedback", "")
            
            print(
                f"         相关性: {rel_score:.4f} (阈值: {effective_rel_threshold:.2f}), "
                f"冗余度: {red_score:.4f} (阈值: {effective_red_threshold:.2f})"
            )
            
            if is_passed:
                print(f"         ✨ 验证通过!")
                self._emit_progress_event(
                    document_id=document_id,
                    section_id=section_id,
                    subsection_id=subsection_id,
                    stage="verifier_passed",
                    message=f"第 {iterations} 轮：Verifier 判定通过",
                    metadata={
                        "iteration": iterations,
                        "relevancy_index": rel_score,
                        "redundancy_index": red_score,
                    },
                )
                
                if self.history_manager:
                    self.history_manager.update_subsection_content(
                        document_id=document_id,
                        section_id=section_id,
                        subsection_id=subsection_id,
                        generated_content=draft,
                        outline=current_outline,
                        relevancy_index=rel_score,
                        redundancy_index=red_score,
                        is_passed=True,
                        iteration_count=iterations
                    )
                
                return {
                    "success": True,
                    "draft": draft,
                    "final_outline": current_outline,
                    "iterations": iterations,
                    "verification": {
                        "relevancy_index": rel_score,
                        "redundancy_index": red_score,
                        "feedback": feedback
                    },
                    "all_drafts": all_drafts
                }
            
            print(f"         🔧 调用 Controller...")
            self._emit_progress_event(
                document_id=document_id,
                section_id=section_id,
                subsection_id=subsection_id,
                stage="verifier_failed",
                message=f"第 {iterations} 轮：Verifier 判定不通过，进入 Controller",
                metadata={
                    "iteration": iterations,
                    "relevancy_index": rel_score,
                    "redundancy_index": red_score,
                },
            )
            controller_retry = 0
            while True:
                controller_retry += 1
                self._emit_progress_event(
                    document_id=document_id,
                    section_id=section_id,
                    subsection_id=subsection_id,
                    stage="controller_start",
                    message=f"第 {iterations} 轮：Controller 第 {controller_retry} 次尝试改纲",
                    metadata={"iteration": iterations, "controller_retry": controller_retry},
                )
                controller_result = self._call_controller(
                    old_outline=self._resolve_subsection_outline(
                        document_id=document_id,
                        section_id=section_id,
                        subsection_id=subsection_id,
                        fallback_outline=current_outline,
                    ),
                    failed_draft=draft,
                    feedback=verify_result,
                    outline=outline,
                    history=[h["content"] for h in windowed_history],
                    iteration=iterations,
                    rel_threshold=effective_rel_threshold,
                    red_threshold=effective_red_threshold,
                    document_id=document_id,
                    section_id=section_id,
                    subsection_id=subsection_id,
                )

                improved_outline = str(controller_result.get("improved_outline", "")).strip()
                if controller_result.get("success") and improved_outline:
                    current_outline = improved_outline
                    # 回写 controller 改进的大纲到数据库
                    if self.history_manager:
                        try:
                            self.history_manager.update_subsection_content(
                                document_id=document_id,
                                section_id=section_id,
                                subsection_id=subsection_id,
                                outline=current_outline,
                                iteration_count=iterations,
                            )
                        except Exception as _e:
                            print(f"⚠️  回写改进大纲失败: {_e}")
                    print(f"         ✅ 大纲已改进（controller重试 {controller_retry} 次）")
                    self._emit_progress_event(
                        document_id=document_id,
                        section_id=section_id,
                        subsection_id=subsection_id,
                        stage="controller_success",
                        message=f"第 {iterations} 轮：Controller 改纲成功，返回 Generator",
                        metadata={"iteration": iterations, "controller_retry": controller_retry},
                    )
                    break

                print(f"         ⚠️  Controller 失败，继续重试（第 {controller_retry} 次）")
                self._emit_progress_event(
                    document_id=document_id,
                    section_id=section_id,
                    subsection_id=subsection_id,
                    stage="controller_error",
                    message=f"第 {iterations} 轮：Controller 改纲失败，继续重试",
                    metadata={"iteration": iterations, "controller_retry": controller_retry},
                )
                time.sleep(self._compute_retry_delay(controller_retry))
    
    def _build_enhanced_prompt(
        self,
        original_prompt: str,
        outline: str,
        history_text: str,
        rel_threshold: float,
        red_threshold: float,
    ) -> str:
        """
        构建增强的生成提示，按照正确流程:
        - 大纲（已此前存储在数据库的 subsection outline）
        - history（已通过验证的前置小节）
        一起发送给 LLM，提示生成高相关性、低冗余度的内容。
        """
        enhanced = f"""你正在撰写一篇文档的某个小节。

【当前小节的详细大纲（必须严格按照它展开，这是内容的完整范围和边界）】
{outline}

"""

        if history_text:
            enhanced += f"""【前面已通过验证的小节内容（作为已生成内容的参考）】
{history_text}

【生成要求】
- 目标指标：relevancy_index >= {rel_threshold:.2f}，redundancy_index <= {red_threshold:.2f}
- 「必须满足」：内容必须与当前小节大纲高度匹配，每个段落都要服从大纲中的具体要求
- 「必须遵免」：不得重复、改写或拼套上面《已通过小节内容》中已有的信息；若内容馆相似会指文重写
- 与前面小节保持逻辑连贯，但展开全新的视角和信息
- 字数控制在 500～800 字

"""
        else:
            enhanced += f"""【生成要求】
- 目标指标：relevancy_index >= {rel_threshold:.2f}，redundancy_index <= {red_threshold:.2f}
- 「必须满足」：内容必须与当前小节大纲高度匹配，不写大纲之外的内容
- 字数控制在 500～800 字

"""

        enhanced += f"""【原始生成指令】
{original_prompt}

请直接输出该小节的正文内容，不要添加任何前言或后语。
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
            print(f"      [Generator] 发起HTTP请求...")
            response = self.session.post(
                f"{self.generator_url}/generate",
                json={"prompt": prompt, "max_tokens": 800},
                timeout=120
            )
            
            print(f"      [Generator] 收到响应 (status={response.status_code}, size={len(response.text)})")
            if response.status_code == 200:
                result = response.json()
                print(f"      [Generator] 解析成功: success={result.get('success')}")
                return result
            else:
                return {
                    "success": False,
                    "error": f"HTTP {response.status_code}: {response.text[:200]}"
                }
        except requests.Timeout:
            return {
                "success": False,
                "error": "Generator 响应超时 (120秒)"
            }
        except Exception as e:
            print(f"      [Generator] 异常: {type(e).__name__}: {str(e)[:100]}")
            return {
                "success": False,
                "error": f"{type(e).__name__}: {str(e)[:100]}"
            }
    
    def _call_verifier(
        self,
        draft: str,
        outline: str,
        history: List[str],
        rel_threshold: float,
        red_threshold: float
    ) -> Dict[str, Any]:
        """调用 Verifier API，内部最多重试3次（应对 Render 冷启动），避免浪费生成轮次。"""
        last_error = "unknown"
        for attempt in range(1, 4):
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
                    timeout=90
                )
                if response.status_code == 200:
                    result = response.json()
                    result["success"] = True
                    return result
                else:
                    last_error = f"HTTP {response.status_code}"
            except Exception as e:
                last_error = str(e)
            if attempt < 3:
                print(f"         ⚠️ Verifier 第{attempt}次调用失败 ({last_error[:80]})，5s 后重试...")
                time.sleep(5)
        return {"success": False, "error": last_error}
    
    def _call_controller(
        self,
        old_outline: str,
        failed_draft: str,
        feedback: Dict[str, Any],
        outline: str,
        history: Optional[List[str]] = None,
        iteration: int = 1,
        rel_threshold: float = 0.80,
        red_threshold: float = 0.40,
        document_id: Optional[str] = None,
        section_id: Optional[str] = None,
        subsection_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """调用 Controller API 改进大纲"""
        try:
            payload: Dict[str, Any] = {
                "original_outline": outline,
                "current_outline": old_outline,
                "failed_draft": failed_draft,
                "feedback": feedback,
                "history": history or [],
                "iteration": iteration,
                "rel_threshold": rel_threshold,
                "red_threshold": red_threshold,
            }
            if document_id:
                payload["document_id"] = document_id
            if section_id:
                payload["section_id"] = section_id
            if subsection_id:
                payload["subsection_id"] = subsection_id
            response = self.session.post(
                f"{self.controller_url}/improve-outline",
                json=payload,
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

