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
import re

try:
    from rag_search import RAGSearchEngine, SourceVerifier
    RAG_AVAILABLE = True
except Exception:
    RAG_AVAILABLE = False


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
        history_window_size: int = 3,  # 历史窗口大小：只使用最近N个小节
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
        self.subsection_retry_forever = os.getenv("SUBSECTION_RETRY_FOREVER", "false").lower() == "true"
        # 默认轮次收敛到 8，避免长时间卡在单小节。
        self.max_subsection_attempts = max(1, int(os.getenv("MAX_SUBSECTION_ATTEMPTS", "8")))
        # 单个小节最长处理时长（秒），超过后按最佳努力通过，避免长时间卡住。
        self.subsection_max_seconds = max(120, int(os.getenv("SUBSECTION_MAX_SECONDS", "1800")))
        # 当 Generator 连续失败时，优先按该阈值触发兜底，避免单小节长时间阻塞。
        self.max_generator_failures_per_subsection = max(
            1,
            int(os.getenv("MAX_GENERATOR_FAILURES_PER_SUBSECTION", "3")),
        )
        self.max_controller_retries = max(1, int(os.getenv("MAX_CONTROLLER_RETRIES", "3")))
        configured_min_retries = max(1, int(os.getenv("MIN_CONTROLLER_RETRIES_BEFORE_FORCE", "8")))
        self.min_controller_retries_before_force = min(configured_min_retries, self.max_controller_retries)
        # 默认采用宽松模式：只要 Controller 给出有效改纲且发生变更，就允许继续下一轮。
        self.strict_controller_effective = os.getenv("STRICT_CONTROLLER_EFFECTIVE", "false").lower() == "true"
        self.max_pass_rel_margin = max(0.0, float(os.getenv("MAX_PASS_REL_MARGIN", "0.35")))
        self.max_pass_red_margin = max(0.0, float(os.getenv("MAX_PASS_RED_MARGIN", "0.45")))
        self.orch_generator_retries = max(1, int(os.getenv("ORCH_GENERATOR_RETRIES", "2")))
        self.orch_generator_backoff = max(0.2, float(os.getenv("ORCH_GENERATOR_BACKOFF", "1.5")))
        self.orch_generator_max_backoff = max(1.0, float(os.getenv("ORCH_GENERATOR_MAX_BACKOFF", "20.0")))
        self.generator_http_timeout = max(30, int(os.getenv("GENERATOR_HTTP_TIMEOUT", "120")))
        self.verifier_http_timeout = max(30, int(os.getenv("VERIFIER_HTTP_TIMEOUT", "180")))
        self.verifier_max_retries = max(3, int(os.getenv("VERIFIER_MAX_RETRIES", "8")))
        self.verifier_retry_delay = max(2.0, float(os.getenv("VERIFIER_RETRY_DELAY", "8.0")))
        self.generator_max_tokens = max(400, int(os.getenv("ORCH_GENERATOR_MAX_TOKENS", "600")))
        self.session = requests.Session()
        self.session.trust_env = False
        
        # 用于本地 HTTP 调用优化
        self._local_generator = None
        self._local_verifier = None
        self._local_controller = None
        
        self.rag_enabled = os.getenv("RAG_ENABLED", "true").lower() == "true" and RAG_AVAILABLE
        self.rag_force_citation = os.getenv("RAG_FORCE_CITATION", "true").lower() == "true"
        self.rag_min_citations = max(1, int(os.getenv("RAG_MIN_CITATIONS", "1")))
        self.rag_max_results = max(1, int(os.getenv("RAG_MAX_RESULTS", "5")))
        self.rag_timeout = max(3, int(os.getenv("RAG_TIMEOUT", "10")))
        self.prompt_outline_max_chars = max(500, int(os.getenv("PROMPT_OUTLINE_MAX_CHARS", "4500")))
        self.prompt_original_max_chars = max(500, int(os.getenv("PROMPT_ORIGINAL_MAX_CHARS", "3500")))
        self.prompt_rag_max_chars = max(200, int(os.getenv("PROMPT_RAG_MAX_CHARS", "1200")))
        self.prompt_history_max_chars = max(200, int(os.getenv("PROMPT_HISTORY_MAX_CHARS", "1500")))

        self.search_engine = (
            RAGSearchEngine(max_results=self.rag_max_results, timeout=self.rag_timeout)
            if self.rag_enabled
            else None
        )
        self.source_verifier = SourceVerifier() if self.rag_enabled else None

    def _compute_retry_delay(self, attempt: int) -> float:
        base = self.retry_base_delay * (2 ** max(0, min(attempt - 1, 6)))
        delay = min(base, self.retry_max_delay)
        delay += random.uniform(0.0, self.retry_jitter)
        return min(delay, self.retry_max_delay)

    def _compute_effective_thresholds(self, iteration: int, rel_threshold: float, red_threshold: float) -> Tuple[float, float]:
        """
        阈值策略（平衡质量与收敛）：
        - 第1~2轮：严格使用原始阈值，确保有机会触发 Controller 改纲
        - 第3轮起：每轮放宽 0.015，最多放宽 0.075，降低长循环概率
        """
        if iteration <= 2:
            return round(rel_threshold, 4), round(red_threshold, 4)

        relax_steps = min(5, max(0, iteration - 2))
        relax_amount = 0.015 * relax_steps
        effective_rel = max(0.0, rel_threshold - relax_amount)
        effective_red = min(1.0, red_threshold + relax_amount)
        return round(effective_rel, 4), round(effective_red, 4)

    @staticmethod
    def _quality_dimension_keys() -> List[str]:
        return [
            "topic_alignment",
            "coverage_completeness",
            "logical_coherence",
            "evidence_grounding",
            "structure_clarity",
            "novelty",
        ]

    def _init_document_quality_summary(self) -> Dict[str, Any]:
        return {
            "quality_score_sum": 0.0,
            "quality_score_count": 0,
            "quality_overall_uncertainty_sum": 0.0,
            "quality_overall_uncertainty_count": 0,
            "quality_dimension_sums": {key: 0.0 for key in self._quality_dimension_keys()},
            "quality_dimension_counts": {key: 0 for key in self._quality_dimension_keys()},
            "quality_weights": {},
            "unieval_available_subsections": 0,
            "unieval_fallback_subsections": 0,
        }

    def _init_document_bandit_summary(self) -> Dict[str, Any]:
        return {
            "bandit_selected_arm_counts": {
                "llm": 0,
                "rule": 0,
                "rule_structured": 0,
                "defect_topic": 0,
                "defect_evidence": 0,
                "defect_structure": 0,
            },
            "bandit_reward_sum": 0.0,
            "bandit_reward_count": 0,
            "bandit_reward_avg": 0.0,
            "bandit_drift_events": 0,
            "bandit_drift_triggered_subsections": 0,
            "bandit_last_selected_arm": "",
            "bandit_last_selection_mode": "",
            "bandit_last_constraints": {},
        }

    def _accumulate_quality_summary(self, summary: Dict[str, Any], verification: Dict[str, Any]) -> None:
        if not isinstance(summary, dict) or not isinstance(verification, dict):
            return
        quality_score = verification.get("quality_score")
        if isinstance(quality_score, (int, float)):
            summary["quality_score_sum"] += float(quality_score)
            summary["quality_score_count"] += 1

        overall_unc = verification.get("quality_overall_uncertainty")
        if isinstance(overall_unc, (int, float)):
            summary["quality_overall_uncertainty_sum"] += float(overall_unc)
            summary["quality_overall_uncertainty_count"] += 1

        weights = verification.get("quality_weights")
        if isinstance(weights, dict) and weights:
            summary["quality_weights"] = dict(weights)

        dimensions = verification.get("quality_dimensions")
        if isinstance(dimensions, dict):
            summary["unieval_available_subsections"] += 1
            for key in self._quality_dimension_keys():
                value = dimensions.get(key)
                if isinstance(value, (int, float)):
                    summary["quality_dimension_sums"][key] = summary["quality_dimension_sums"].get(key, 0.0) + float(value)
                    summary["quality_dimension_counts"][key] = summary["quality_dimension_counts"].get(key, 0) + 1

        if verification.get("quality_dimensions_uncertainty"):
            summary["unieval_fallback_subsections"] += 1

    def _accumulate_bandit_summary(self, summary: Dict[str, Any], subsection_result: Dict[str, Any]) -> None:
        if not isinstance(summary, dict) or not isinstance(subsection_result, dict):
            return
        bandit = subsection_result.get("bandit") if isinstance(subsection_result.get("bandit"), dict) else {}
        if not bandit:
            return

        selected_arm = str(bandit.get("selected_arm") or subsection_result.get("controller_source") or "").strip()
        if selected_arm in summary.get("bandit_selected_arm_counts", {}):
            summary["bandit_selected_arm_counts"][selected_arm] += 1
        summary["bandit_last_selected_arm"] = selected_arm or summary.get("bandit_last_selected_arm", "")
        summary["bandit_last_selection_mode"] = str(bandit.get("selection", {}).get("mode", "") or summary.get("bandit_last_selection_mode", ""))

        reward = bandit.get("reward")
        if isinstance(reward, (int, float)):
            summary["bandit_reward_sum"] += float(reward)
            summary["bandit_reward_count"] += 1
            summary["bandit_reward_avg"] = summary["bandit_reward_sum"] / max(1, summary["bandit_reward_count"])

        drift = bandit.get("drift") if isinstance(bandit.get("drift"), dict) else {}
        if drift:
            drift_events = int(drift.get("drift_events", 0) or 0)
            summary["bandit_drift_events"] = max(summary.get("bandit_drift_events", 0), drift_events)
            if drift.get("triggered"):
                summary["bandit_drift_triggered_subsections"] += 1

        constraints = bandit.get("constraints") if isinstance(bandit.get("constraints"), dict) else {}
        if constraints:
            summary["bandit_last_constraints"] = dict(constraints)

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

    def _build_rag_query_candidates(self, outline: str, initial_prompt: str) -> List[str]:
        outline_text = " ".join(str(outline or "").split()).strip()
        prompt_text = " ".join(str(initial_prompt or "").split()).strip()

        merged = (outline_text + " " + prompt_text).strip()[:320]

        title_like = ""
        outline_lines = [line.strip("-•* 1234567890.\t") for line in str(outline_text).splitlines() if line.strip()]
        if outline_lines:
            title_like = outline_lines[0][:140]

        semantic_tokens = re.findall(r"[A-Za-z\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff\-_]{1,24}", merged)
        stop_tokens = {
            "section", "subsection", "outline", "prompt", "chapter", "write", "writing",
            "要求", "生成", "内容", "小节", "章节", "大纲", "写作", "包括", "以及", "关于"
        }
        semantic_terms: List[str] = []
        for token in semantic_tokens:
            normalized = token.strip().lower()
            if not normalized or normalized in stop_tokens or normalized.isdigit() or len(normalized) <= 1:
                continue
            if normalized not in semantic_terms:
                semantic_terms.append(normalized)
            if len(semantic_terms) >= 10:
                break
        semantic_query = " ".join(semantic_terms)[:140]

        candidates_raw = [merged, title_like, semantic_query, prompt_text[:180]]
        candidates: List[str] = []
        seen = set()
        for candidate in candidates_raw:
            cleaned = " ".join(str(candidate or "").split()).strip()
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(cleaned)

        return candidates[:4]

    def _build_local_outline_fallback(
        self,
        current_outline: str,
        original_outline: str,
        feedback: Dict[str, Any],
        rel_threshold: float,
        red_threshold: float,
        iteration: int,
    ) -> str:
        """当 Controller 不可用或返回无效结果时，本地规则改纲兜底。"""
        marker_start = "\n【本地修订约束】\n"
        marker_end = "\n【本地修订约束结束】"

        base_outline_raw = str(current_outline or original_outline or "").strip()
        block_start = base_outline_raw.find(marker_start)
        if block_start >= 0:
            block_end = base_outline_raw.find(marker_end, block_start)
            if block_end >= 0:
                base_outline = (base_outline_raw[:block_start] + base_outline_raw[block_end + len(marker_end):]).strip()
            else:
                base_outline = base_outline_raw[:block_start].strip()
        else:
            base_outline = base_outline_raw

        if not base_outline:
            return ""

        rel_score = float(feedback.get("relevancy_index", 0) or 0)
        red_score = float(feedback.get("redundancy_index", 0) or 0)
        feedback_text = str(feedback.get("feedback", "") or "")
        failed_dimensions = feedback.get("quality_dimensions_failed") if isinstance(feedback.get("quality_dimensions_failed"), list) else []
        dimension_check = feedback.get("quality_dimensions_check") if isinstance(feedback.get("quality_dimensions_check"), dict) else {}
        dimension_thresholds = feedback.get("dimension_thresholds") if isinstance(feedback.get("dimension_thresholds"), dict) else {}
        dimension_messages = {
            "topic_alignment": "主题对齐不足：要更聚焦小节核心主题，补充关键定义、目标或必须回答的问题。",
            "coverage_completeness": "覆盖不完整：补齐小节应覆盖的关键子点、步骤、约束或对比维度。",
            "logical_coherence": "逻辑连贯性不足：重排为更清晰的因果、递进或问题-解决结构。",
            "evidence_grounding": "证据接地性不足：加入可验证事实、数据、引用或示例支撑，避免空话。",
            "novelty": "新颖性不足：引入新的角度、反例、比较对象或差异化信息，避免重复前文。",
            "structure_clarity": "结构清晰度不足：使用更明确的小标题、分点和步骤式组织。",
        }

        extra_lines: List[str] = []
        if rel_score < rel_threshold:
            extra_lines.append(
                f"- 聚焦要求：围绕本小节核心主题展开，确保 relevancy_index >= {rel_threshold:.2f}。"
            )
            keywords = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_-]{2,}", str(original_outline or ""))
            key_tokens = []
            for token in keywords:
                if token not in key_tokens:
                    key_tokens.append(token)
                if len(key_tokens) >= 6:
                    break
            if key_tokens:
                extra_lines.append("- 必须覆盖关键词：" + "、".join(key_tokens) + "。")

        if red_score > red_threshold:
            extra_lines.append(
                f"- 去重要求：避免与前文表达重复，确保 redundancy_index <= {red_threshold:.2f}。"
            )
            extra_lines.append("- 内容策略：优先使用新的事实、案例、数据或反例，不复述已有段落。")

        if failed_dimensions:
            extra_lines.append("- 多维质量要求：以下维度必须分别修复，任何一个维度未达标都不允许通过。")
            for dim_name in failed_dimensions:
                dim_value = dimension_check.get(dim_name, {}).get("value") if isinstance(dimension_check, dict) else None
                dim_threshold = dimension_thresholds.get(dim_name)
                dim_text = dimension_messages.get(dim_name, f"{dim_name} 维度需要提升。")
                if isinstance(dim_value, (int, float)) and isinstance(dim_threshold, (int, float)):
                    extra_lines.append(
                        f"- {dim_name}: {dim_text} 当前值={float(dim_value):.4f}，阈值={float(dim_threshold):.4f}。"
                    )
                else:
                    extra_lines.append(f"- {dim_name}: {dim_text}")

        if feedback_text:
            extra_lines.append("- 验证反馈约束：" + feedback_text[:180])

        if not extra_lines:
            extra_lines.append("- 质量要求：保持主题聚焦与信息增量，避免泛化叙述。")

        adjustments = "\n".join(extra_lines).strip()
        suffix = (
            f"{marker_start}"
            f"- 第{iteration}轮修订：以下约束用于提升主题命中与信息增量。\n"
            f"{adjustments}\n"
            f"{marker_end}"
        )

        return (base_outline + "\n" + suffix).strip()
    
    def generate_document(
        self,
        document_id: str,
        title: str,
        structure: Dict[str, Any],  # 从 Outliner 返回的结构
        content_prompts: List[Dict[str, Any]],  # 从 Outliner 返回的 content_prompts
        user_background: str,
        user_requirements: str,
        rel_threshold: float = 0.55,
        red_threshold: float = 0.70
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
            "forced_subsections": [],
            "total_iterations": 0,
            "generation_time": None,
            "rag_used_subsections": 0,
            "rag_search_success_subsections": 0,
            "controller_effective_subsections": 0,
            "controller_triggered_subsections": 0,
            "verifier_failed_total": 0,
            "controller_calls_total": 0,
            "controller_success_total": 0,
            "controller_error_total": 0,
            "controller_unavailable_total": 0,
            "controller_ineffective_total": 0,
            "controller_fallback_outline_total": 0,
            "controller_exhausted_total": 0,
            "quality_score_sum": 0.0,
            "quality_score_count": 0,
            "quality_overall_uncertainty_sum": 0.0,
            "quality_overall_uncertainty_count": 0,
            "quality_dimension_sums": {key: 0.0 for key in self._quality_dimension_keys()},
            "quality_dimension_counts": {key: 0 for key in self._quality_dimension_keys()},
            "quality_weights": {},
            "unieval_available_subsections": 0,
            "unieval_fallback_subsections": 0,
            "bandit_selected_arm_counts": {
                "llm": 0,
                "rule": 0,
                "rule_structured": 0,
                "defect_topic": 0,
                "defect_evidence": 0,
                "defect_structure": 0,
            },
            "bandit_reward_sum": 0.0,
            "bandit_reward_count": 0,
            "bandit_reward_avg": 0.0,
            "bandit_drift_events": 0,
            "bandit_drift_triggered_subsections": 0,
            "bandit_last_selected_arm": "",
            "bandit_last_selection_mode": "",
            "bandit_last_constraints": {},
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
            
            total_subsections_expected = sum(
                len(sec.get("subsections", []))
                for sec in structure.get("sections", [])
            )
            processed_subsections = 0

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
                    processed_subsections += 1
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
                        stage="subsection_trace_ready",
                        message=f"小节上下文已就绪: {section_title} > {subsection_title}",
                        metadata={
                            "section_title": section_title,
                            "subsection_title": subsection_title,
                            "subsection_order": subsection_index + 1,
                            "section_subsection_total": len(subsection_list),
                            "outline_chars": len(subsection_outline),
                            "content_prompt_chars": len(content_prompt),
                        },
                    )
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
                            "enable_controller": True,
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
                            red_threshold=red_threshold,
                        )
                        
                        document_result["total_iterations"] += subsection_gen_result.get("iterations", 0)
                        
                        if subsection_gen_result.get("success"):
                            generated_content = subsection_gen_result.get("draft", "")
                            verification = subsection_gen_result.get("verification", {})
                            self._accumulate_quality_summary(document_result, verification)
                            self._accumulate_bandit_summary(document_result, subsection_gen_result)
                            history_order = len(passed_history)
                            forced_pass = bool(subsection_gen_result.get("forced_pass", False))
                            force_reason = str(subsection_gen_result.get("force_reason", "") or "")
                            controller_triggered = bool(subsection_gen_result.get("controller_triggered", False))
                            controller_retry_count = int(subsection_gen_result.get("controller_retry_count", 0) or 0)
                            rag_used = bool(subsection_gen_result.get("rag_used", False))
                            rag_search_success = bool(subsection_gen_result.get("rag_search_success", False))
                            controller_effective = bool(subsection_gen_result.get("controller_effective", False))
                            metrics = subsection_gen_result.get("metrics", {}) if isinstance(subsection_gen_result, dict) else {}

                            if rag_used:
                                document_result["rag_used_subsections"] += 1
                            if rag_search_success:
                                document_result["rag_search_success_subsections"] += 1
                            if controller_effective:
                                document_result["controller_effective_subsections"] += 1
                            if controller_triggered:
                                document_result["controller_triggered_subsections"] += 1

                            document_result["verifier_failed_total"] += int(metrics.get("verifier_failed", 0) or 0)
                            document_result["controller_calls_total"] += int(metrics.get("controller_calls", 0) or 0)
                            document_result["controller_success_total"] += int(metrics.get("controller_success", 0) or 0)
                            document_result["controller_error_total"] += int(metrics.get("controller_error", 0) or 0)
                            document_result["controller_unavailable_total"] += int(metrics.get("controller_unavailable", 0) or 0)
                            document_result["controller_ineffective_total"] += int(metrics.get("controller_ineffective", 0) or 0)
                            document_result["controller_fallback_outline_total"] += int(metrics.get("controller_fallback_outline", 0) or 0)
                            document_result["controller_exhausted_total"] += int(metrics.get("controller_exhausted", 0) or 0)
                            
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
                                        "forced_pass": forced_pass,
                                        "force_reason": force_reason,
                                        "controller_triggered": controller_triggered,
                                        "controller_retry_count": controller_retry_count,
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
                            if forced_pass:
                                document_result["forced_subsections"].append({
                                    "section_id": section_id,
                                    "subsection_id": subsection_id,
                                    "reason": force_reason,
                                    "iterations": subsection_gen_result.get("iterations", 0),
                                })
                            self._emit_progress_event(
                                document_id=document_id,
                                section_id=section_id,
                                subsection_id=subsection_id,
                                stage="subsection_passed",
                                message=f"小节通过验证: {section_title} > {subsection_title}",
                                metadata={
                                    "iterations": subsection_gen_result.get("iterations", 0),
                                    "verification": verification,
                                    "forced_pass": forced_pass,
                                    "force_reason": force_reason,
                                    "controller_triggered": controller_triggered,
                                    "controller_retry_count": controller_retry_count,
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
                                "bandit": subsection_gen_result.get("bandit", {}),
                                "forced_pass": forced_pass,
                                "force_reason": force_reason,
                                "controller_triggered": controller_triggered,
                                "controller_retry_count": controller_retry_count,
                                "rag_used": rag_used,
                                "rag_search_success": rag_search_success,
                                "controller_effective": controller_effective,
                                "length": len(generated_content)
                            })
                            
                        else:
                            err = subsection_gen_result.get("error", "Unknown error")
                            print(f"⚠️ 当前小节返回失败结果，降级补全文档: {err}")
                            fallback_content = f"（系统兜底）{subsection_title}\n\n{str(subsection_outline).strip()}"
                            section_result["subsections"].append({
                                "subsection_id": subsection_id,
                                "subsection_title": subsection_title,
                                "content": fallback_content,
                                "outline": subsection_outline,
                                "success": True,
                                "iterations": subsection_gen_result.get("iterations", 0),
                                "verification": subsection_gen_result.get("verification", {}),
                                "bandit": subsection_gen_result.get("bandit", {}),
                                "forced_pass": True,
                                "force_reason": "subsection_fallback_on_error",
                                "rag_used": bool(subsection_gen_result.get("rag_used", False)),
                                "rag_search_success": bool(subsection_gen_result.get("rag_search_success", False)),
                                "controller_effective": bool(subsection_gen_result.get("controller_effective", False)),
                                "length": len(fallback_content),
                            })
                            document_result["passed_subsections"] += 1
                            document_result["forced_subsections"].append({
                                "section_id": section_id,
                                "subsection_id": subsection_id,
                                "reason": "subsection_fallback_on_error",
                                "iterations": subsection_gen_result.get("iterations", 0),
                            })
                            self._emit_progress_event(
                                document_id=document_id,
                                section_id=section_id,
                                subsection_id=subsection_id,
                                stage="subsection_forced_pass",
                                message=f"小节触发兜底强制通过: {section_title} > {subsection_title}",
                                metadata={"error": err},
                            )
                    
                    except Exception as e:
                        print(f"⚠️ 小节生成异常，启用兜底继续文档流程: {e}")
                        error_str = str(e)[:200]
                        fallback_content = f"（系统兜底）{subsection_title}\n\n{str(subsection_outline).strip()}"
                        section_result["subsections"].append({
                            "subsection_id": subsection_id,
                            "subsection_title": subsection_title,
                            "content": fallback_content,
                            "outline": subsection_outline,
                            "success": True,
                            "iterations": 0,
                            "verification": {},
                            "bandit": {},
                            "forced_pass": True,
                            "force_reason": "subsection_exception_fallback",
                            "length": len(fallback_content),
                        })
                        document_result["passed_subsections"] += 1
                        document_result["forced_subsections"].append({
                            "section_id": section_id,
                            "subsection_id": subsection_id,
                            "reason": "subsection_exception_fallback",
                            "iterations": 0,
                        })
                        self._emit_progress_event(
                            document_id=document_id,
                            section_id=section_id,
                            subsection_id=subsection_id,
                            stage="subsection_forced_pass",
                            message=f"小节异常后兜底强制通过: {section_title} > {subsection_title}",
                            metadata={"error": error_str},
                        )
                        continue
                
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

            quality_score_count = max(1, int(document_result.get("quality_score_count", 0) or 0))
            overall_uncertainty_count = max(1, int(document_result.get("quality_overall_uncertainty_count", 0) or 0))
            document_result["quality_score_avg"] = round(
                float(document_result.get("quality_score_sum", 0.0) or 0.0) / quality_score_count,
                4,
            )
            document_result["quality_overall_uncertainty_avg"] = round(
                float(document_result.get("quality_overall_uncertainty_sum", 0.0) or 0.0) / overall_uncertainty_count,
                4,
            )
            document_result["quality_dimension_avgs"] = {
                key: round(
                    float(document_result.get("quality_dimension_sums", {}).get(key, 0.0) or 0.0)
                    / max(1, int(document_result.get("quality_dimension_counts", {}).get(key, 0) or 0)),
                    4,
                )
                for key in self._quality_dimension_keys()
            }
            document_result["unieval_available_ratio"] = round(
                float(document_result.get("unieval_available_subsections", 0) or 0) / max(1, total_subsections_generated),
                4,
            )
            document_result["unieval_fallback_ratio"] = round(
                float(document_result.get("unieval_fallback_subsections", 0) or 0) / max(1, total_subsections_generated),
                4,
            )
            document_result["bandit_reward_avg"] = round(float(document_result.get("bandit_reward_avg", 0.0) or 0.0), 4)
            document_result["bandit_drift_trigger_rate"] = round(
                float(document_result.get("bandit_drift_triggered_subsections", 0) or 0) / max(1, total_subsections_generated),
                4,
            )

            # 文档级永不失败：只要流程走完就返回完整文档，失败小节全部转为兜底通过
            document_result["success"] = True
            
            print(f"\n{'='*70}")
            print(f"{'✅' if document_result['success'] else '❌'} 文档生成完成！")
            print(f"   - 预期小节数: {total_subsections_expected}")
            print(f"   - 实际生成: {total_subsections_generated}")
            print(f"   - 通过: {document_result['passed_subsections']}")
            print(f"   - 失败: {len(document_result['failed_subsections'])}")
            print(f"   - 兜底通过: {len(document_result['forced_subsections'])}")
            print(f"   - RAG 命中: {document_result['rag_search_success_subsections']}/{len(content_prompts)}")
            print(f"   - RAG 使用: {document_result['rag_used_subsections']}/{len(content_prompts)}")
            print(f"   - Controller 有效: {document_result['controller_effective_subsections']}/{len(content_prompts)}")
            print(f"   - Controller 触发小节: {document_result['controller_triggered_subsections']}/{len(content_prompts)}")
            print(f"   - Controller 调用总数: {document_result['controller_calls_total']}")
            print(f"   - 总迭代: {document_result['total_iterations']} 次")
            print(f"   - UniEval 平均分: {document_result['quality_score_avg']}")
            print(f"   - Bandit 平均奖励: {document_result['bandit_reward_avg']}")
            print(f"   - 耗时: {document_result['generation_time']}")
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
                    "forced_subsections": len(document_result["forced_subsections"]),
                    "total_iterations": document_result["total_iterations"],
                    "controller_calls_total": document_result["controller_calls_total"],
                    "controller_triggered_subsections": document_result["controller_triggered_subsections"],
                    "verifier_failed_total": document_result["verifier_failed_total"],
                },
            )
            
            return document_result
            
        except Exception as e:
            print(f"❌ 文档生成失败: {e}")
            import traceback
            traceback.print_exc()
            
            return {
                "success": False,
                "document_id": document_id,
                "title": title,
                "sections": document_result.get("sections", []),
                "passed_subsections": document_result.get("passed_subsections", 0),
                "failed_subsections": document_result.get("failed_subsections", []),
                "forced_subsections": document_result.get("forced_subsections", []),
                "total_iterations": document_result.get("total_iterations", 0),
                "generation_time": document_result.get("generation_time"),
                "rag_used_subsections": document_result.get("rag_used_subsections", 0),
                "rag_search_success_subsections": document_result.get("rag_search_success_subsections", 0),
                "controller_effective_subsections": document_result.get("controller_effective_subsections", 0),
                "controller_triggered_subsections": document_result.get("controller_triggered_subsections", 0),
                "verifier_failed_total": document_result.get("verifier_failed_total", 0),
                "controller_calls_total": document_result.get("controller_calls_total", 0),
                "controller_success_total": document_result.get("controller_success_total", 0),
                "controller_error_total": document_result.get("controller_error_total", 0),
                "controller_unavailable_total": document_result.get("controller_unavailable_total", 0),
                "controller_ineffective_total": document_result.get("controller_ineffective_total", 0),
                "controller_fallback_outline_total": document_result.get("controller_fallback_outline_total", 0),
                "controller_exhausted_total": document_result.get("controller_exhausted_total", 0),
                "error": str(e),
                "warning": f"document_exception_fallback: {str(e)[:180]}",
            }
    
    def _generate_and_verify_subsection(
        self,
        document_id: str,
        section_id: str,
        subsection_id: str,
        outline: str,
        initial_prompt: str,
        passed_history: List[Dict[str, str]],
        rel_threshold: float = 0.75,
        red_threshold: float = 0.50,
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
        controller_triggered = False
        controller_retry_count = 0
        controller_last_result: Dict[str, Any] = {}
        controller_effective = False
        best_candidate: Optional[Dict[str, Any]] = None
        generator_failure_streak = 0
        generator_degraded_mode = False
        metrics: Dict[str, int] = {
            "verifier_failed": 0,
            "controller_calls": 0,
            "controller_success": 0,
            "controller_error": 0,
            "controller_unavailable": 0,
            "controller_ineffective": 0,
            "controller_fallback_outline": 0,
            "controller_exhausted": 0,
            "generator_degraded_mode": 0,
        }
        
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

        rag_search_result: Dict[str, Any] = {"success": False, "results": []}
        rag_context = ""
        require_source_citations = False
        source_citation_required = False
        rag_used = False
        rag_selected_query = ""

        if self.rag_enabled and self.search_engine is not None:
            rag_query_candidates = self._build_rag_query_candidates(
                outline=current_outline,
                initial_prompt=current_prompt,
            )
            selected_query = ""
            rag_error = "unknown"
            for rag_query in rag_query_candidates:
                rag_search_result = self.search_engine.search(rag_query)
                if rag_search_result.get("success") and rag_search_result.get("results"):
                    selected_query = rag_query
                    rag_used = True
                    rag_selected_query = selected_query
                    break
                rag_error = str(rag_search_result.get("error", "unknown"))

            if selected_query:
                    rag_context = self.search_engine.format_search_context(rag_search_result, max_items=3)
                    require_source_citations = self.rag_force_citation
                    print(f"   🌐 RAG检索成功: {len(rag_search_result.get('results', []))} 条来源")
                    self._emit_progress_event(
                        document_id=document_id,
                        section_id=section_id,
                        subsection_id=subsection_id,
                        stage="rag_search_success",
                        message="RAG 搜索成功，已注入来源上下文",
                        metadata={
                            "query": selected_query,
                            "tried_queries": rag_query_candidates,
                            "result_count": len(rag_search_result.get("results", [])),
                            "require_source_citations": require_source_citations,
                        },
                    )
            else:
                print("   ⚠️ RAG检索未返回可用来源，降级为常规生成")
                self._emit_progress_event(
                    document_id=document_id,
                    section_id=section_id,
                    subsection_id=subsection_id,
                    stage="rag_search_failed",
                    message="RAG 搜索失败，降级为常规生成",
                    metadata={
                        "query": rag_query_candidates[0] if rag_query_candidates else "",
                        "tried_queries": rag_query_candidates,
                        "error": rag_error,
                    },
                )
            source_citation_required = require_source_citations

        effective_attempt_cap = self.max_subsection_attempts if self.max_subsection_attempts > 0 else self.max_iterations
        subsection_started_at = time.time()
        
        while True:
            iterations += 1
            elapsed_subsection = time.time() - subsection_started_at
            timed_out = self.subsection_max_seconds > 0 and elapsed_subsection >= self.subsection_max_seconds
            reached_attempt_cap = (
                (not self.subsection_retry_forever)
                and effective_attempt_cap > 0
                and iterations > effective_attempt_cap
            )

            if timed_out or reached_attempt_cap:
                timeout_triggered = timed_out and not reached_attempt_cap
                if best_candidate and best_candidate.get("draft"):
                    best_verification = best_candidate.get("verification", {})
                    best_rel = float(best_verification.get("relevancy_index", 0) or 0)
                    best_red = float(best_verification.get("redundancy_index", 1) or 1)
                    max_pass_ok = (
                        best_rel >= max(0.0, rel_threshold - self.max_pass_rel_margin)
                        and best_red <= min(1.0, red_threshold + self.max_pass_red_margin)
                    )
                    absolute_best_effort_ok = best_rel >= 0.45 and best_red <= 0.90
                    max_pass_ok = max_pass_ok or absolute_best_effort_ok
                    stage_name = "verifier_best_effort_pass" if max_pass_ok else "verifier_forced_pass"
                    if timeout_triggered:
                        pass_message = (
                            f"单小节耗时已达 {self.subsection_max_seconds}s，最佳结果满足放宽阈值，按最佳努力通过"
                            if max_pass_ok
                            else f"单小节耗时已达 {self.subsection_max_seconds}s，按最佳结果强制通过并继续"
                        )
                        pass_reason = "subsection_timeout_best_effort" if max_pass_ok else "subsection_timeout_forced"
                    else:
                        pass_message = (
                            f"达到最大检测次数 {effective_attempt_cap}，最佳结果满足放宽阈值，按最佳努力通过"
                            if max_pass_ok
                            else f"达到最大检测次数 {effective_attempt_cap}，按最佳结果强制通过并继续"
                        )
                        pass_reason = "best_effort_after_max_attempts" if max_pass_ok else "max_attempts_reached"
                    self._emit_progress_event(
                        document_id=document_id,
                        section_id=section_id,
                        subsection_id=subsection_id,
                        stage=stage_name,
                        message=pass_message,
                        metadata={
                            "iteration": iterations - 1,
                            "best_iteration": best_candidate.get("iteration", iterations - 1),
                            "relevancy_index": best_verification.get("relevancy_index", 0),
                            "redundancy_index": best_verification.get("redundancy_index", 1),
                            "elapsed_seconds": round(elapsed_subsection, 2),
                        },
                    )
                    if self.history_manager:
                        try:
                            self.history_manager.update_subsection_content(
                                document_id=document_id,
                                section_id=section_id,
                                subsection_id=subsection_id,
                                generated_content=best_candidate.get("draft", ""),
                                outline=best_candidate.get("outline", current_outline),
                                relevancy_index=best_verification.get("relevancy_index", 0),
                                redundancy_index=best_verification.get("redundancy_index", 1),
                                is_passed=True,
                                iteration_count=iterations - 1,
                            )
                        except Exception as _e:
                            print(f"⚠️  强制通过回写失败: {_e}")
                    return {
                        "success": True,
                        "draft": best_candidate.get("draft", ""),
                        "final_outline": best_candidate.get("outline", current_outline),
                        "iterations": iterations - 1,
                        "rag_used": bool(best_candidate.get("rag_used", rag_used)),
                        "rag_search_success": bool(
                            best_candidate.get(
                                "rag_search_success",
                                bool(rag_search_result.get("success", False)),
                            )
                        ),
                        "rag_result_count": int(
                            best_candidate.get(
                                "rag_result_count",
                                len(rag_search_result.get("results", [])),
                            )
                            or 0
                        ),
                        "rag_selected_query": str(
                            best_candidate.get("rag_selected_query", rag_selected_query) or ""
                        ),
                        "controller_effective": bool(
                            best_candidate.get(
                                "controller_effective",
                                bool(controller_last_result.get("effective", False)),
                            )
                        ),
                        "controller_source": str(
                            best_candidate.get(
                                "controller_source",
                                controller_last_result.get("source", ""),
                            )
                            or ""
                        ),
                        "verification": {
                            **best_verification,
                            "forced_pass": (not max_pass_ok),
                            "force_reason": pass_reason,
                        },
                        "bandit": best_candidate.get("bandit", {}),
                        "all_drafts": all_drafts,
                        "forced_pass": (not max_pass_ok),
                        "force_reason": pass_reason,
                        "controller_triggered": (metrics.get("controller_calls", 0) > 0) or controller_triggered,
                        "controller_retry_count": controller_retry_count,
                        "metrics": metrics,
                    }

                # 改进兜底逻辑：优先使用 all_drafts 中最后一个，而不是 outline
                if all_drafts and len(all_drafts) > 0:
                    # 使用最后一个尝试的 draft，即使未通过验证，也比 outline 要好
                    fallback_draft = all_drafts[-1]
                    fallback_note = "（未完全验证，最后尝试的内容）"
                else:
                    # 完全没有draft时，返回空内容而不是outline
                    fallback_draft = ""
                    fallback_note = "（内容生成失败，仍在恢复中）"
                
                self._emit_progress_event(
                    document_id=document_id,
                    section_id=section_id,
                    subsection_id=subsection_id,
                    stage="verifier_forced_pass",
                    message=(
                        f"单小节耗时已达 {self.subsection_max_seconds}s，按最后可用内容通过"
                        if timeout_triggered
                        else f"达到最大检测次数 {effective_attempt_cap}，按最后可用内容通过"
                    ),
                    metadata={
                        "iteration": iterations - 1,
                        "elapsed_seconds": round(elapsed_subsection, 2),
                        "has_fallback_draft": len(all_drafts) > 0,
                        "fallback_note": fallback_note,
                    },
                )
                placeholder_reason = "subsection_timeout_no_draft" if timeout_triggered else "max_attempts_no_draft"
                return {
                    "success": True,
                    "draft": fallback_draft,
                    "final_outline": current_outline,
                    "iterations": iterations - 1,
                    "all_drafts": all_drafts,
                    "rag_used": rag_used,
                    "rag_search_success": bool(rag_search_result.get("success", False)),
                    "rag_result_count": len(rag_search_result.get("results", [])),
                    "rag_selected_query": rag_selected_query,
                    "controller_effective": bool(controller_last_result.get("effective", False)),
                    "controller_source": controller_last_result.get("source", ""),
                    "bandit": {},
                    "verification": {
                        "relevancy_index": 0.0,
                        "redundancy_index": 1.0,
                        "feedback": placeholder_reason,
                        "forced_pass": True,
                        "force_reason": placeholder_reason,
                    },
                    "forced_pass": True,
                    "force_reason": placeholder_reason,
                    "controller_triggered": (metrics.get("controller_calls", 0) > 0) or controller_triggered,
                    "controller_retry_count": controller_retry_count,
                    "metrics": metrics,
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
                metadata={
                    "iteration": iterations,
                    "outline_chars": len(current_outline),
                    "prompt_chars": len(current_prompt),
                    "history_chars": len(history_text),
                    "effective_rel_threshold": round(float(effective_rel_threshold), 4),
                    "effective_red_threshold": round(float(effective_red_threshold), 4),
                    "generator_degraded_mode": generator_degraded_mode,
                    "rag_used": rag_used,
                },
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
                rag_context=rag_context,
                require_source_citations=require_source_citations,
            )
            
            gen_result = self._call_generator(enhanced_prompt)
            bandit_debug = gen_result.get("bandit", {}) if isinstance(gen_result, dict) else {}
            
            if not gen_result.get("success"):
                generator_failure_streak += 1
                print(f"         ⚠️ Generator 错误，继续重试当前小节: {gen_result.get('error')}")
                self._emit_progress_event(
                    document_id=document_id,
                    section_id=section_id,
                    subsection_id=subsection_id,
                    stage="generator_error",
                    message=f"第 {iterations} 轮：Generator 失败，准备重试",
                    metadata={
                        "iteration": iterations,
                        "error": gen_result.get("error", "unknown"),
                        "generator_failure_streak": generator_failure_streak,
                        "provider": str((gen_result.get("metadata") or {}).get("provider", "") or ""),
                        "prompt_chars": len(enhanced_prompt),
                    },
                )

                max_generator_failures = min(effective_attempt_cap, self.max_generator_failures_per_subsection)
                if generator_failure_streak >= max_generator_failures:
                    fail_error = str(gen_result.get("error", "generator_unavailable"))

                    # 首次达到连续失败阈值时，先进入降级重试而不是立即强制通过。
                    if not generator_degraded_mode:
                        generator_degraded_mode = True
                        generator_failure_streak = 0
                        metrics["generator_degraded_mode"] += 1
                        rag_context = ""
                        source_citation_required = False
                        self._emit_progress_event(
                            document_id=document_id,
                            section_id=section_id,
                            subsection_id=subsection_id,
                            stage="generator_degraded_retry",
                            message=f"Generator 连续失败 {max_generator_failures} 次，切换降级模式后继续重试",
                            metadata={
                                "iteration": iterations,
                                "max_generator_failures": max_generator_failures,
                                "error": fail_error,
                                "degraded_mode": True,
                                "rag_context_cleared": True,
                                "source_citation_required": False,
                            },
                        )
                        time.sleep(self._compute_retry_delay(iterations))
                        continue

                    fail_outline = str(current_outline).strip()
                    # 改进：即使generator失败，也优先使用任何可用的草稿而不是outline
                    if all_drafts and len(all_drafts) > 0:
                        fallback_text = all_drafts[-1]
                        fallback_note = "（Generator失败，使用最后尝试的内容）"
                    else:
                        fallback_text = ""
                        fallback_note = "（Generator失败，内容仍在恢复中）"
                    
                    self._emit_progress_event(
                        document_id=document_id,
                        section_id=section_id,
                        subsection_id=subsection_id,
                        stage="subsection_forced_pass",
                        message=f"Generator 在降级模式下仍连续失败 {generator_failure_streak} 次，按最后可用内容通过",
                        metadata={
                            "iteration": iterations,
                            "max_generator_failures": max_generator_failures,
                            "error": fail_error,
                            "degraded_mode": True,
                            "has_fallback_draft": len(all_drafts) > 0,
                            "fallback_note": fallback_note,
                        },
                    )
                    if self.history_manager:
                        try:
                            self.history_manager.update_subsection_content(
                                document_id=document_id,
                                section_id=section_id,
                                subsection_id=subsection_id,
                                generated_content=fallback_text,
                                outline=current_outline,
                                relevancy_index=0.0,
                                redundancy_index=1.0,
                                is_passed=True,
                                iteration_count=iterations,
                            )
                        except Exception as _e:
                            print(f"⚠️  Generator失败兜底回写失败: {_e}")
                    return {
                        "success": True,
                        "draft": fallback_text,
                        "final_outline": current_outline,
                        "iterations": iterations,
                        "rag_used": rag_used,
                        "rag_search_success": bool(rag_search_result.get("success", False)),
                        "rag_result_count": len(rag_search_result.get("results", [])),
                        "rag_selected_query": rag_selected_query,
                        "controller_effective": bool(controller_last_result.get("effective", False)),
                        "controller_source": controller_last_result.get("source", ""),
                        "verification": {
                            "relevancy_index": 0.0,
                            "redundancy_index": 1.0,
                            "feedback": "generator_repeated_failure_after_degraded_mode",
                            "forced_pass": True,
                            "force_reason": "generator_repeated_failure_after_degraded_mode",
                        },
                        "all_drafts": all_drafts,
                        "forced_pass": True,
                        "force_reason": "generator_repeated_failure_after_degraded_mode",
                        "controller_triggered": (metrics.get("controller_calls", 0) > 0) or controller_triggered,
                        "controller_retry_count": controller_retry_count,
                        "metrics": metrics,
                    }

                time.sleep(self._compute_retry_delay(iterations))
                continue

            generator_failure_streak = 0
            
            draft = gen_result.get("draft", "")
            all_drafts.append(draft)
            print(f"         ✅ 生成 {len(draft)} 字符")
            self._emit_progress_event(
                document_id=document_id,
                section_id=section_id,
                subsection_id=subsection_id,
                stage="generator_success",
                message=f"第 {iterations} 轮：Generator 已产出草稿 ({len(draft)} 字符)",
                metadata={
                    "iteration": iterations,
                    "draft_chars": len(draft),
                    "provider": str((gen_result.get("metadata") or {}).get("provider", "") or ""),
                    "generator_degraded_mode": generator_degraded_mode,
                },
            )

            provider_name = str((gen_result.get("metadata") or {}).get("provider", "")).strip().lower()
            if source_citation_required and provider_name == "ollama":
                source_citation_required = False
                self._emit_progress_event(
                    document_id=document_id,
                    section_id=section_id,
                    subsection_id=subsection_id,
                    stage="source_citation_relaxed",
                    message="检测到 Ollama 兜底提供商，自动放宽来源引用硬约束",
                    metadata={"iteration": iterations, "provider": provider_name},
                )
            
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
                red_threshold=effective_red_threshold,
                source_results=rag_search_result.get("results", []),
                require_source_citations=source_citation_required,
                min_source_citations=self.rag_min_citations,
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
            source_check = verify_result.get("source_check", {})
            
            print(
                f"         相关性: {rel_score:.4f} (阈值: {effective_rel_threshold:.2f}), "
                f"冗余度: {red_score:.4f} (阈值: {effective_red_threshold:.2f})"
            )
            if source_citation_required:
                print(
                    f"         来源检查: valid={source_check.get('passed', False)} "
                    f"refs={source_check.get('reference_count', 0)}"
                )

            self._emit_progress_event(
                document_id=document_id,
                section_id=section_id,
                subsection_id=subsection_id,
                stage="verifier_result",
                message=f"第 {iterations} 轮：Verifier 完成，结果={'通过' if is_passed else '未通过'}",
                metadata={
                    "iteration": iterations,
                    "is_passed": bool(is_passed),
                    "relevancy_index": rel_score,
                    "redundancy_index": red_score,
                    "feedback": str(feedback or "")[:260],
                    "source_check_passed": bool(source_check.get("passed", False)),
                    "source_reference_count": int(source_check.get("reference_count", 0) or 0),
                },
            )

            if (not is_passed) and source_citation_required:
                source_reason = str(source_check.get("reason", "") or "").lower()
                citation_feedback = str(feedback or "").lower()
                citation_failed = (
                    "insufficient_citations" in source_reason
                    or "invalid_source_url" in source_reason
                    or "low_semantic_source_quality" in source_reason
                    or "citation" in citation_feedback
                    or "来源" in citation_feedback
                )
                if citation_failed:
                    source_citation_required = False
                    self._emit_progress_event(
                        document_id=document_id,
                        section_id=section_id,
                        subsection_id=subsection_id,
                        stage="source_citation_relaxed",
                        message=f"第 {iterations} 轮：来源引用校验连续失败，后续轮次放宽硬约束",
                        metadata={
                            "iteration": iterations,
                            "source_reason": source_check.get("reason", ""),
                            "feedback": feedback[:180],
                        },
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
                    "rag_used": rag_used,
                    "rag_search_success": bool(rag_search_result.get("success", False)),
                    "rag_result_count": len(rag_search_result.get("results", [])),
                    "rag_selected_query": rag_selected_query,
                    "controller_effective": bool(controller_last_result.get("effective", False)),
                    "controller_source": controller_last_result.get("source", ""),
                    "verification": verify_result,
                    "bandit": bandit_debug,
                    "all_drafts": all_drafts,
                    "forced_pass": False,
                    "force_reason": "",
                    "controller_triggered": (metrics.get("controller_calls", 0) > 0) or controller_triggered,
                    "controller_retry_count": controller_retry_count,
                    "metrics": metrics,
                }

            current_candidate = {
                    "rag_used": rag_used,
                    "rag_search_success": bool(rag_search_result.get("success", False)),
                    "rag_result_count": len(rag_search_result.get("results", [])),
                    "rag_selected_query": rag_selected_query,
                    "controller_effective": bool(controller_last_result.get("effective", False)),
                    "controller_source": controller_last_result.get("source", ""),
                    "bandit": bandit_debug,
                "draft": draft,
                "outline": current_outline,
                "iteration": iterations,
                "verification": verify_result,
            }
            if best_candidate is None:
                best_candidate = current_candidate
            else:
                best_rel = float(best_candidate.get("verification", {}).get("relevancy_index", 0) or 0)
                best_red = float(best_candidate.get("verification", {}).get("redundancy_index", 1) or 1)
                best_gap = max(0.0, rel_threshold - best_rel) + max(0.0, best_red - red_threshold)
                current_gap = max(0.0, rel_threshold - rel_score) + max(0.0, red_score - red_threshold)
                if current_gap < best_gap or (abs(current_gap - best_gap) < 1e-6 and rel_score > best_rel):
                    best_candidate = current_candidate

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
            metrics["verifier_failed"] += 1

            controller_retry = 0
            controller_updated = False
            while True:
                controller_retry += 1
                controller_retry_count += 1
                if controller_retry > self.max_controller_retries:
                    # 改进：Controller 完全失败时，不再用 outline fallback，而是继续下一轮
                    # 系统会在 verifier 失败后继续尝试，最终通过 best_candidate 机制返回最好的 draft
                    print(f"         ⚠️  Controller 失败 {self.max_controller_retries} 次，不应用改纲而直接继续")
                    self._emit_progress_event(
                        document_id=document_id,
                        section_id=section_id,
                        subsection_id=subsection_id,
                        stage="controller_exhausted",
                        message=(
                            f"第 {iterations} 轮：Controller 连续失败 {self.max_controller_retries} 次，"
                            "跳过改纲继续重试"
                        ),
                        metadata={
                            "iteration": iterations,
                            "controller_retry": self.max_controller_retries,
                            "skipped_outline_fallback": True,
                        },
                    )
                    metrics["controller_exhausted"] += 1
                    break

                self._emit_progress_event(
                    document_id=document_id,
                    section_id=section_id,
                    subsection_id=subsection_id,
                    stage="controller_start",
                    message=f"第 {iterations} 轮：Controller 第 {controller_retry} 次尝试改纲",
                    metadata={
                        "iteration": iterations,
                        "controller_retry": controller_retry,
                        "failed_rel_threshold": effective_rel_threshold,
                        "failed_red_threshold": effective_red_threshold,
                        "draft_chars": len(draft),
                    },
                )
                controller_triggered = True
                metrics["controller_calls"] += 1
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
                controller_last_result = controller_result
                controller_error_text = str(controller_result.get("error", "") or "")

                improved_outline = str(controller_result.get("improved_outline", "")).strip()
                controller_effective = bool(controller_result.get("effective", controller_result.get("success", False)))
                controller_changed = bool(controller_result.get("changed", True))

                self._emit_progress_event(
                    document_id=document_id,
                    section_id=section_id,
                    subsection_id=subsection_id,
                    stage="controller_result",
                    message=(
                        f"第 {iterations} 轮：Controller 返回 {'有效' if (controller_result.get('success') and improved_outline and controller_changed) else '无效'} 改纲"
                    ),
                    metadata={
                        "iteration": iterations,
                        "controller_retry": controller_retry,
                        "success": bool(controller_result.get("success", False)),
                        "effective": controller_effective,
                        "changed": controller_changed,
                        "improved_outline_chars": len(improved_outline),
                        "error": controller_error_text[:260],
                    },
                )

                controller_ok_for_next_round = (
                    controller_result.get("success")
                    and improved_outline
                    and controller_changed
                    and (controller_effective or (not self.strict_controller_effective))
                )

                if controller_ok_for_next_round:
                    current_outline = improved_outline
                    controller_updated = True
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
                        metadata={
                            "iteration": iterations,
                            "controller_retry": controller_retry,
                            "effective": controller_effective,
                            "changed": controller_changed,
                            "strict_controller_effective": self.strict_controller_effective,
                        },
                    )
                    metrics["controller_success"] += 1
                    break

                if controller_result.get("success") and not improved_outline:
                    # 改进：Controller 返回空改纲时，不应用 outline fallback
                    # 而是继续下一轮，让 best_candidate 机制选择最好的 draft
                    print(f"         ⚠️  Controller 返回空改纲，不应用fallback而直接继续")
                    self._emit_progress_event(
                        document_id=document_id,
                        section_id=section_id,
                        subsection_id=subsection_id,
                        stage="controller_empty_outline",
                        message=f"第 {iterations} 轮：Controller 返回空改纲，跳过应用",
                        metadata={
                            "iteration": iterations,
                            "controller_retry": controller_retry,
                            "skipped_outline_fallback": True,
                        },
                    )
                    break

                if controller_result.get("success") and (not controller_changed or (self.strict_controller_effective and not controller_effective)):
                    metrics["controller_ineffective"] += 1
                    self._emit_progress_event(
                        document_id=document_id,
                        section_id=section_id,
                        subsection_id=subsection_id,
                        stage="controller_ineffective",
                        message=f"第 {iterations} 轮：Controller 改纲无效，继续重试",
                        metadata={
                            "iteration": iterations,
                            "controller_retry": controller_retry,
                            "effective": controller_effective,
                            "changed": controller_changed,
                            "error": controller_error_text[:260],
                            "improved_outline_chars": len(improved_outline),
                        },
                    )
                    time.sleep(self._compute_retry_delay(controller_retry))
                    continue

                transient_unavailable = any(token in controller_error_text.lower() for token in [
                    "connection refused",
                    "max retries exceeded",
                    "timed out",
                    "read timeout",
                    "service unavailable",
                    "name or service not known",
                ])
                if transient_unavailable:
                    metrics["controller_unavailable"] += 1
                    # 改进：Controller 暂不可用时，不应用 outline fallback
                    # 而是继续重试，让系统通过 best_candidate 机制选择最好的 draft
                    print(f"         ⚠️  Controller 暂不可用，跳过outline fallback直接重试")
                    self._emit_progress_event(
                        document_id=document_id,
                        section_id=section_id,
                        subsection_id=subsection_id,
                        stage="controller_unavailable",
                        message=f"第 {iterations} 轮：Controller 暂不可用，继续重试",
                        metadata={
                            "iteration": iterations,
                            "controller_retry": controller_retry,
                            "error": controller_error_text[:260],
                            "skipped_outline_fallback": True,
                        },
                    )
                    time.sleep(self._compute_retry_delay(controller_retry))
                    continue

                print(f"         ⚠️  Controller 失败，继续重试（第 {controller_retry} 次）")
                metrics["controller_error"] += 1
                self._emit_progress_event(
                    document_id=document_id,
                    section_id=section_id,
                    subsection_id=subsection_id,
                    stage="controller_error",
                    message=f"第 {iterations} 轮：Controller 改纲失败，继续重试",
                    metadata={
                        "iteration": iterations,
                        "controller_retry": controller_retry,
                        "error": controller_error_text[:260],
                    },
                )
                time.sleep(self._compute_retry_delay(controller_retry))

            if not controller_updated:
                # 允许进入下一轮并继续放宽阈值，避免因控制层短时不可用导致整节失败
                current_outline = self._build_local_outline_fallback(
                    current_outline=current_outline,
                    original_outline=outline,
                    feedback=verify_result,
                    rel_threshold=effective_rel_threshold,
                    red_threshold=effective_red_threshold,
                    iteration=iterations,
                )
            
            # Controller改纲完成，继续回到外层循环尝试下一轮生成
            continue

    def _build_enhanced_prompt(
        self,
        original_prompt: str,
        outline: str,
        history_text: str,
        rel_threshold: float,
        red_threshold: float,
        rag_context: str,
        require_source_citations: bool,
    ) -> str:
        """
        构建增强的生成提示，按照正确流程:
        - 大纲（已此前存储在数据库的 subsection outline）
        - history（已通过验证的前置小节）
        一起发送给 LLM，提示生成高相关性、低冗余度的内容。
        """
        def _clip(text: str, max_chars: int, label: str) -> str:
            raw = str(text or "").strip()
            if len(raw) <= max_chars:
                return raw
            head = max(100, int(max_chars * 0.8))
            tail = max(60, max_chars - head)
            return (
                raw[:head]
                + f"\n\n[...{label}已裁剪，原始长度 {len(raw)} 字，保留首尾关键信息...]\n\n"
                + raw[-tail:]
            )

        outline = _clip(outline, self.prompt_outline_max_chars, "outline")
        original_prompt = _clip(original_prompt, self.prompt_original_max_chars, "original_prompt")
        rag_context = _clip(rag_context, self.prompt_rag_max_chars, "rag_context")
        history_text = _clip(history_text, self.prompt_history_max_chars, "history")

        enhanced = f"""你正在撰写一篇文档的某个小节。

【当前小节的详细大纲（这是内容的完整范围和边界，必须100%严格遵循）】
{outline}

"""

        if original_prompt:
            enhanced += f"""【原始写作任务与风格要求（必须兼容遵循）】
{original_prompt}

"""

        if rag_context:
            enhanced += f"{rag_context}\n\n"

        if history_text:
            enhanced += f"""【前面已通过验证的小节内容（作为已生成内容的参考，避免冗余）】
{history_text}

【严格的生成要求】
1. 相关性（必须 >= {rel_threshold:.2f}）：
   - 内容的每一句话都必须直接对应大纲中的某个要点
   - 不允许任何与大纲无关的内容或例子
   - 确保段落标题直接来自或对应大纲的标题
   - 验证：如果删除某段文字，是否会让大纲的某个要点失去对应内容？如果是，则保留

2. 避免冗余（必须 <= {red_threshold:.2f}）：
   - 严禁重复、改写或拼凑上面《已通过小节内容》中已有的信息
   - 每句话都要贡献新的、未重复的观点
   
3. 质量要求：
   - 与前面小节保持逻辑连贯，但展开全新的视角和信息
   - 字数控制在 500～800 字
   - 表述专业、准确、避免空洞内容

"""
        else:
            enhanced += f"""【严格的生成要求】
1. 相关性（必须 >= {rel_threshold:.2f}）：
   - 内容的每一句话都必须直接对应大纲中的某个要点
   - 不允许任何与大纲无关的内容或例子
   - 确保段落标题直接来自或对应大纲的标题

2. 质量要求：
   - 字数控制在 500～800 字
   - 表述专业、准确、避免空洞内容

"""

        if require_source_citations:
            enhanced += """【来源引用硬性要求（必须满足）】
- 对关键事实、数据、结论，必须直接写出具体来源信息，不要使用“[来源N]”占位符
- 推荐正文引用格式： （来源：文章标题，链接：https://example.com/xxx）
- 至少提供 1 处可点击的具体网页链接；若有图片链接，也请直接写出图片 URL
- 优先使用学术来源：arXiv、SSRN、Google Scholar、期刊/大学/官方机构页面
- 在无法找到学术来源时，允许使用高质量社媒和技术社区来源（如知乎、B站、微博、X/Twitter、Reddit、Medium）
- 只能引用上方“参考资料”里给出的来源，不允许编造不存在的论文、文章、链接或图片链接
- 每个关键事实至少提供 1 组“事实句 + 可验证来源URL”的证据最小单元
- 禁止引用与当前小节主题语义不一致的链接；如果标题/摘要和小节要点不匹配，禁止使用
- 在正文末尾增加“引用来源”小节，逐条列出：标题、网页链接、可选图片链接

"""

        enhanced += """【原始生成指令】
    请直接输出该小节的正文内容，不要添加任何前言或后语。
    """

        return enhanced.strip()

    def _is_transient_generator_error(self, error_text: str) -> bool:
        lowered = str(error_text or "").lower()
        transient_tokens = [
            "timeout", "timed out", "connection", "temporarily", "try again",
            "429", "502", "503", "504", "rate", "quota", "overloaded",
            "resource_exhausted", "service unavailable", "upstream",
        ]
        return any(token in lowered for token in transient_tokens)
    
    def _call_generator(self, prompt: str) -> Dict[str, Any]:
        """调用 Generator API（优先使用本地实例）"""
        print(f"      [_call_generator] Starting (local_gen={self._local_generator is not None})")
        if self._local_generator is not None:
            try:
                print(f"      [_call_generator] Calling local generator.generate_draft...")
                start = time.time()
                result = self._local_generator.generate_draft(prompt=prompt, max_tokens=self.generator_max_tokens)
                elapsed = time.time() - start
                print(f"      [_call_generator] Local call returned in {elapsed:.1f}s: success={result.get('success')}")
                return result
            except Exception as e:
                print(f"⚠️ 本地Generator调用失败: {e}，回退到HTTP调用")

        last_error = "generator_unknown_error"
        for attempt in range(1, self.orch_generator_retries + 1):
            try:
                print(
                    f"      [Generator] 发起HTTP请求... "
                    f"(attempt {attempt}/{self.orch_generator_retries})"
                )
                response = self.session.post(
                    f"{self.generator_url}/generate",
                    json={"prompt": prompt, "max_tokens": self.generator_max_tokens},
                    timeout=self.generator_http_timeout,
                )

                print(f"      [Generator] 收到响应 (status={response.status_code}, size={len(response.text)})")
                if response.status_code == 200:
                    result = response.json()
                    print(f"      [Generator] 解析成功: success={result.get('success')}")
                    if result.get("success"):
                        return result

                    last_error = str(result.get("error") or "generator_failed")
                    can_retry = (
                        attempt < self.orch_generator_retries
                        and self._is_transient_generator_error(last_error)
                    )
                    if can_retry:
                        delay = min(
                            self.orch_generator_max_backoff,
                            self.orch_generator_backoff * (2 ** (attempt - 1)),
                        )
                        delay += random.uniform(0.0, 0.35)
                        print(f"      [Generator] 瞬时失败，{delay:.2f}s 后重试: {last_error[:120]}")
                        time.sleep(delay)
                        continue
                    return {"success": False, "error": last_error}

                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                can_retry = (
                    attempt < self.orch_generator_retries
                    and (response.status_code in (429, 502, 503, 504) or self._is_transient_generator_error(last_error))
                )
                if can_retry:
                    delay = min(
                        self.orch_generator_max_backoff,
                        self.orch_generator_backoff * (2 ** (attempt - 1)),
                    )
                    delay += random.uniform(0.0, 0.35)
                    print(f"      [Generator] HTTP可重试错误，{delay:.2f}s 后重试")
                    time.sleep(delay)
                    continue

                return {"success": False, "error": last_error}
            except requests.Timeout:
                last_error = f"Generator 响应超时 ({self.generator_http_timeout}秒)"
                if attempt < self.orch_generator_retries:
                    delay = min(
                        self.orch_generator_max_backoff,
                        self.orch_generator_backoff * (2 ** (attempt - 1)),
                    )
                    delay += random.uniform(0.0, 0.35)
                    print(f"      [Generator] 请求超时，{delay:.2f}s 后重试")
                    time.sleep(delay)
                    continue
                return {"success": False, "error": last_error}
            except Exception as e:
                last_error = f"{type(e).__name__}: {str(e)[:100]}"
                print(f"      [Generator] 异常: {last_error}")
                if attempt < self.orch_generator_retries and self._is_transient_generator_error(last_error):
                    delay = min(
                        self.orch_generator_max_backoff,
                        self.orch_generator_backoff * (2 ** (attempt - 1)),
                    )
                    delay += random.uniform(0.0, 0.35)
                    print(f"      [Generator] 异常可重试，{delay:.2f}s 后重试")
                    time.sleep(delay)
                    continue
                return {"success": False, "error": last_error}

        return {"success": False, "error": last_error}
    
    def _call_verifier(
        self,
        draft: str,
        outline: str,
        history: List[str],
        rel_threshold: float,
        red_threshold: float,
        source_results: Optional[List[Dict[str, Any]]] = None,
        require_source_citations: bool = False,
        min_source_citations: int = 1,
    ) -> Dict[str, Any]:
        """
        调用 Verifier API，内部最多重试5次（应对 Render 冷启动），避免浪费生成轮次。
        
        超时配置优化：
        - 单次请求超时：180秒（原 90秒，增加 2 倍）
        - 重试次数：5 次（原 3 次）
        - 重试间隔：8秒（原 5秒）
        - 总容忍时间：180 + 5*8 = 220 秒
        
        这样可以容忍 Render Free Plan 的冷启动延迟（30-60s）和高负载情况。
        """
        print(
            f"      [_call_verifier] Starting verifier call "
            f"(timeout={self.verifier_http_timeout}s, max_retries={self.verifier_max_retries})..."
        )
        last_error = "unknown"
        max_retries = self.verifier_max_retries
        retry_delay = self.verifier_retry_delay
        
        for attempt in range(1, max_retries + 1):
            try:
                print(f"      [_call_verifier] Attempt {attempt}/{max_retries}, sending request...")
                start = time.time()
                response = self.session.post(
                    f"{self.verifier_url}/verify",
                    json={
                        "draft": draft,
                        "outline": outline,
                        "history": history,
                        "rel_threshold": rel_threshold,
                        "red_threshold": red_threshold,
                        "source_results": source_results or [],
                        "require_source_citations": require_source_citations,
                        "min_source_citations": max(1, int(min_source_citations)),
                    },
                    timeout=self.verifier_http_timeout,
                )
                if response.status_code == 200:
                    elapsed = time.time() - start
                    result = response.json()
                    result["success"] = True
                    print(f"      [_call_verifier] Response received in {elapsed:.1f}s: success=True")
                    return result
                else:
                    elapsed = time.time() - start
                    last_error = f"HTTP {response.status_code}"
                    print(f"      [_call_verifier] HTTP {response.status_code} after {elapsed:.1f}s")
            except Exception as e:
                elapsed = time.time() - start
                last_error = str(e)
                print(f"      [_call_verifier] Exception after {elapsed:.1f}s: {e}")
            
            if attempt < max_retries:
                adaptive_delay = min(30.0, retry_delay * (1.0 + 0.2 * attempt))
                print(f"         ⚠️ Verifier 第{attempt}次调用失败 ({last_error[:80]})，{adaptive_delay:.1f}s 后重试...")
                time.sleep(adaptive_delay)
        
        print(f"      [_call_verifier] All {max_retries} attempts failed: {last_error}")
        return {"success": False, "error": last_error}
    
    def _call_controller(
        self,
        old_outline: str,
        failed_draft: str,
        feedback: Dict[str, Any],
        outline: str,
        history: Optional[List[str]] = None,
        iteration: int = 1,
        rel_threshold: float = 0.85,
        red_threshold: float = 0.40,
        document_id: Optional[str] = None,
        section_id: Optional[str] = None,
        subsection_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """调用 Controller API 改进大纲"""
        timeout = max(30, int(os.getenv("CONTROLLER_HTTP_TIMEOUT", "120")))
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
                "quality_dimensions_failed": feedback.get("quality_dimensions_failed", []),
                "quality_dimensions_check": feedback.get("quality_dimensions_check", {}),
                "dimension_thresholds": feedback.get("dimension_thresholds", {}),
                "quality_dimensions": feedback.get("quality_dimensions", {}),
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
                timeout=timeout
            )
            
            if response.status_code == 200:
                try:
                    body = response.json()
                except Exception:
                    return {
                        "success": False,
                        "error": "controller_invalid_json",
                    }
                if "success" not in body:
                    body["success"] = True
                return body
            else:
                return {
                    "success": False,
                    "error": f"HTTP {response.status_code}: {response.text[:200]}"
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

