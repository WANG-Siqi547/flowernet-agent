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

try:
    from citation_drift_prevention import CITATION_DRIFT_PREVENTION_PROMPT
except Exception:
    CITATION_DRIFT_PREVENTION_PROMPT = ""

try:
    from flowernet_agent_stack import get_vector_store
except Exception:
    get_vector_store = None  # type: ignore


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
        self.retry_base_delay = float(os.getenv("DOC_RETRY_BASE_DELAY", "1.0"))
        self.retry_max_delay = float(os.getenv("DOC_RETRY_MAX_DELAY", "10.0"))
        self.retry_jitter = float(os.getenv("DOC_RETRY_JITTER", "0.2"))
        self.subsection_retry_forever = os.getenv("SUBSECTION_RETRY_FOREVER", "false").lower() == "true"
        # 默认轮次收敛到 5，给 Controller 更多改纲次数以提升质量
        self.max_subsection_attempts = max(1, int(os.getenv("MAX_SUBSECTION_ATTEMPTS", "5")))
        # 单个小节最长处理时长（秒），超过后按最佳努力通过，避免长时间卡住。
        self.subsection_max_seconds = max(120, int(os.getenv("SUBSECTION_MAX_SECONDS", "900")))
        # 当 Generator 连续失败时，优先按该阈值触发兜底，避免单小节长时间阻塞。
        self.max_generator_failures_per_subsection = max(
            1,
            int(os.getenv("MAX_GENERATOR_FAILURES_PER_SUBSECTION", "3")),
        )
        self.max_controller_retries = max(1, int(os.getenv("MAX_CONTROLLER_RETRIES", "2")))
        self.allow_forced_pass = os.getenv("ALLOW_FORCED_PASS", "false").lower() == "true"
        configured_min_retries = max(1, int(os.getenv("MIN_CONTROLLER_RETRIES_BEFORE_FORCE", "1")))
        self.min_controller_retries_before_force = min(configured_min_retries, self.max_controller_retries)
        # 默认采用宽松模式：只要 Controller 给出有效改纲且发生变更，就允许继续下一轮。
        self.strict_controller_effective = os.getenv("STRICT_CONTROLLER_EFFECTIVE", "false").lower() == "true"
        self.controller_guard_enabled = os.getenv("CONTROLLER_GUARD_ENABLED", "true").lower() == "true"
        self.controller_min_outline_retention = max(
            0.15,
            min(0.95, float(os.getenv("CONTROLLER_MIN_OUTLINE_RETENTION", "0.45"))),
        )
        self.local_outline_fallback_enabled = os.getenv("LOCAL_OUTLINE_FALLBACK_ENABLED", "false").lower() == "true"
        self.max_pass_rel_margin = max(0.0, float(os.getenv("MAX_PASS_REL_MARGIN", "0.25")))
        self.max_pass_red_margin = max(0.0, float(os.getenv("MAX_PASS_RED_MARGIN", "0.30")))
        self.orch_generator_retries = max(1, int(os.getenv("ORCH_GENERATOR_RETRIES", "1")))
        self.orch_generator_backoff = max(0.2, float(os.getenv("ORCH_GENERATOR_BACKOFF", "1.0")))
        self.orch_generator_max_backoff = max(1.0, float(os.getenv("ORCH_GENERATOR_MAX_BACKOFF", "10.0")))
        self.generator_http_timeout = max(30, int(os.getenv("GENERATOR_HTTP_TIMEOUT", "60")))
        # Compact prompts are only useful as provider-failure fallbacks. Using them on
        # normal controller retries drops outline/evidence detail and can make quality worse.
        self.orch_compact_generation_enabled = os.getenv("ORCH_COMPACT_GENERATION_ENABLED", "false").lower() == "true"
        self.orch_compact_prompt_trigger_chars = max(1200, int(os.getenv("ORCH_COMPACT_PROMPT_TRIGGER_CHARS", "7000")))
        self.orch_compact_max_tokens = max(400, int(os.getenv("ORCH_COMPACT_MAX_TOKENS", "1800")))
        self.verifier_http_timeout = max(30, int(os.getenv("VERIFIER_HTTP_TIMEOUT", "60")))
        self.verifier_max_retries = max(3, int(os.getenv("VERIFIER_MAX_RETRIES", "3")))
        self.verifier_retry_delay = max(2.0, float(os.getenv("VERIFIER_RETRY_DELAY", "3.0")))
        self.verifier_unavailable_best_effort = os.getenv("VERIFIER_UNAVAILABLE_BEST_EFFORT", "true").lower() == "true"
        self.generator_max_tokens = max(400, int(os.getenv("ORCH_GENERATOR_MAX_TOKENS", "2000")))
        self.min_draft_chars = max(200, int(os.getenv("ORCH_MIN_DRAFT_CHARS", "800")))
        self.session = requests.Session()
        self.session.trust_env = False
        
        # 用于本地 HTTP 调用优化
        self._local_generator = None
        self.vector_store = get_vector_store() if get_vector_store is not None else None
        self._local_verifier = None
        self._local_controller = None
        
        self.rag_enabled = os.getenv("RAG_ENABLED", "true").lower() == "true" and RAG_AVAILABLE
        self.rag_force_citation = os.getenv("RAG_FORCE_CITATION", "true").lower() == "true"
        self.source_citation_relaxation_enabled = os.getenv("SOURCE_CITATION_RELAXATION_ENABLED", "false").lower() == "true"
        self.rag_min_citations = max(1, int(os.getenv("RAG_MIN_CITATIONS", "1")))
        self.rag_max_results = max(1, int(os.getenv("RAG_MAX_RESULTS", "5")))
        self.rag_timeout = max(3, int(os.getenv("RAG_TIMEOUT", "10")))
        self.prompt_outline_max_chars = max(500, int(os.getenv("PROMPT_OUTLINE_MAX_CHARS", "4500")))
        self.prompt_original_max_chars = max(500, int(os.getenv("PROMPT_ORIGINAL_MAX_CHARS", "3500")))
        self.prompt_rag_max_chars = max(200, int(os.getenv("PROMPT_RAG_MAX_CHARS", "1200")))
        self.prompt_history_max_chars = max(200, int(os.getenv("PROMPT_HISTORY_MAX_CHARS", "1500")))
        self.near_pass_quality_margin = max(0.0, float(os.getenv("NEAR_PASS_QUALITY_MARGIN", "0.03")))

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

    def _sanitize_subsection_draft(self, draft: str) -> str:
        """Remove model meta-output and local reference blocks before verification/export."""
        text = str(draft or "").strip()
        if not text:
            return ""

        lines = text.splitlines()
        cleaned: List[str] = []
        skip_rest = False
        reference_heading = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*(?:references?|bibliography|参考文献)\s*(?:\*\*)?\s*[:：]?\s*$", re.I)
        meta_heading = re.compile(
            r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*(?:论证链实现说明|结构优化|写作说明|生成说明|质量检查说明|citation\s+notes?)\s*(?:\*\*)?\s*[:：]?\s*$",
            re.I,
        )
        inline_reference = re.compile(r"(?is)\s*(?:\*\*)?\s*(?:references?|bibliography|参考文献)\s*(?:\*\*)?\s*[:：]?\s*(?:\[\d+\].*)$")
        inline_meta = re.compile(r"(?is)\s*(?:---\s*)?(?:\*\*)?\s*(?:论证链实现说明|结构优化|写作说明|生成说明|质量检查说明)\s*(?:\*\*)?\s*[:：]?.*$")

        for raw_line in lines:
            stripped = raw_line.strip()
            if reference_heading.match(stripped) or meta_heading.match(stripped):
                skip_rest = True
                continue
            if skip_rest:
                continue
            raw_line = inline_reference.sub("", raw_line)
            raw_line = inline_meta.sub("", raw_line)
            cleaned.append(raw_line)

        text = "\n".join(cleaned).strip()
        text = re.sub(r"(?is)\n\s*(?:---\s*)?(?:\*\*)?\s*(?:论证链实现说明|结构优化|写作说明|生成说明|质量检查说明)\s*(?:\*\*)?\s*[:：]?.*$", "", text).strip()
        text = re.sub(r"(?is)\n\s*(?:\*\*)?\s*(?:references?|bibliography|参考文献)\s*(?:\*\*)?\s*[:：]?\s*(?:\[\d+\].*)$", "", text).strip()
        return text

    def _verification_near_pass(self, verification: Dict[str, Any], rel_threshold: float, red_threshold: float) -> bool:
        if not isinstance(verification, dict):
            return False
        source_check = verification.get("source_check") if isinstance(verification.get("source_check"), dict) else {}
        if not bool(source_check.get("passed", False)):
            return False
        rel = float(verification.get("relevancy_index", 0) or 0)
        red = float(verification.get("redundancy_index", 1) or 1)
        quality = float(verification.get("quality_score", 0) or 0)
        quality_threshold = float(verification.get("quality_threshold", 0.6) or 0.6)
        failed_dims = verification.get("quality_dimensions_failed", [])
        failed_count = len(failed_dims) if isinstance(failed_dims, list) else 0
        return (
            rel >= rel_threshold
            and red <= red_threshold
            and quality >= max(0.0, quality_threshold - self.near_pass_quality_margin)
            and failed_count <= 1
        )

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
            for key in self._quality_dimension_keys():
                value = dimensions.get(key)
                if isinstance(value, (int, float)):
                    summary["quality_dimension_sums"][key] = summary["quality_dimension_sums"].get(key, 0.0) + float(value)
                    summary["quality_dimension_counts"][key] = summary["quality_dimension_counts"].get(key, 0) + 1

        if bool(verification.get("unieval_available", False)):
            summary["unieval_available_subsections"] += 1
        elif isinstance(dimensions, dict) and dimensions:
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
            if summary["bandit_reward_count"] > 0:
                summary["bandit_reward_avg"] = summary["bandit_reward_sum"] / summary["bandit_reward_count"]

        drift = bandit.get("drift") if isinstance(bandit.get("drift"), dict) else {}
        if drift:
            drift_events = int(drift.get("drift_events", 0) or 0)
            summary["bandit_drift_events"] = max(summary.get("bandit_drift_events", 0), drift_events)
            if drift.get("triggered"):
                summary["bandit_drift_triggered_subsections"] += 1

        constraints = bandit.get("constraints") if isinstance(bandit.get("constraints"), dict) else {}
        if constraints:
            summary["bandit_last_constraints"] = dict(constraints)
        # constraints handled above

    def _load_recent_bandit_stats(self, max_events: int = 200) -> Dict[str, Any]:
        """从文件 `controller_bandit_events.jsonl` 读取最近若干条 bandit 事件并聚合为统计信息。
        返回字典包含与前端契合的字段（counts, sum, count, avg, last_arm, drift_events）。
        """
        stats = {
            "bandit_selected_arm_counts": {},
            "bandit_reward_sum": 0.0,
            "bandit_reward_count": 0,
            "bandit_reward_avg": 0.0,
            "bandit_last_selected_arm": "",
            "bandit_last_selection_mode": "",
            "bandit_drift_events": 0,
        }
        try:
            project_root = os.path.dirname(os.path.abspath(__file__))
            events_path = os.path.join(project_root, "controller_bandit_events.jsonl")
            if not os.path.exists(events_path):
                return stats
            with open(events_path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
            if not lines:
                return stats
            recent = lines[-max_events:]
            rewards: List[float] = []
            for line in recent:
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                arm = str(ev.get("chosen_arm") or "")
                reward = float(ev.get("reward", 0.0) or 0.0)
                if arm:
                    stats["bandit_selected_arm_counts"][arm] = stats["bandit_selected_arm_counts"].get(arm, 0) + 1
                    stats["bandit_last_selected_arm"] = arm
                stats["bandit_reward_sum"] += reward
                rewards.append(reward)
                if reward != 0.0:
                    stats["bandit_reward_count"] += 1
                if ev.get("drift") is not None:
                    stats["bandit_drift_events"] += 1
            if stats["bandit_reward_count"] > 0:
                stats["bandit_reward_avg"] = stats["bandit_reward_sum"] / stats["bandit_reward_count"]

            # Compute plot bounds for frontend visualization (with padding)
            if rewards:
                rmin = min(rewards)
                rmax = max(rewards)
                if abs(rmax - rmin) < 1e-8:
                    pad = max(0.01, abs(rmax) * 0.05)
                    rmin_plot = rmin - pad
                    rmax_plot = rmax + pad
                else:
                    span = rmax - rmin
                    rmin_plot = rmin - 0.12 * span
                    rmax_plot = rmax + 0.12 * span
                stats["plot_y_min"] = float(round(rmin_plot, 6))
                stats["plot_y_max"] = float(round(rmax_plot, 6))
            else:
                stats["plot_y_min"] = 0.0
                stats["plot_y_max"] = 0.1

            return stats
        except Exception:
            return stats

    def _read_last_bandit_event(self) -> Dict[str, Any]:
        """返回 controller_bandit_events.jsonl 中最后一条事件的原始解析结果（或空字典）。"""
        try:
            project_root = os.path.dirname(os.path.abspath(__file__))
            events_path = os.path.join(project_root, "controller_bandit_events.jsonl")
            if not os.path.exists(events_path):
                return {}
            with open(events_path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
            if not lines:
                return {}
            for line in reversed(lines):
                try:
                    ev = json.loads(line)
                    if isinstance(ev, dict):
                        return ev
                except Exception:
                    continue
            return {}
        except Exception:
            return {}

    def _is_outline_like(self, content: str, outline: str = "") -> bool:
        """简单判断 content 是否更像大纲/提示而非正文，供兜底选择时排除大纲型草稿。

        规则：空内容 / 以系统兜底前缀开头 / 包含明显的提示标记 / 与 outline 文本高度相似或互为子串
        """
        text = str(content or "").strip()
        if not text:
            return False
        outline_text = str(outline or "").strip()
        compact_text = " ".join(text.split())
        compact_outline = " ".join(outline_text.split())

        # 系统兜底前缀或兜底标记
        if text.startswith("（系统兜底）") or "（兜底内容）" in text:
            return True

        # 明显的提示/模板痕迹
        prompt_markers = ["请你作为", "要求：", "段落主题", "系统指示", "content_prompt", "subsection"]
        if any(m in text for m in prompt_markers):
            return True

        # 如果 outline 非空且两者互为子串或包含关系，认为可能是大纲
        if compact_outline and len(compact_outline) >= 12:
            if compact_outline in compact_text or compact_text in compact_outline:
                return True

        # 否则认为不是大纲型内容
        return False

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

    def _extract_topic_context(self, outline: str, prompt: str) -> str:
        """
        【优化1.0 - Domain Anchoring】
        从小节大纲和提示中提取核心领域关键词（topic_context）
        用于强制锁定RAG搜索的领域范围，避免跨学科幻觉
        
        规则：
        1. 提取标题中的主要名词（保留领域特定词汇）
        2. 过滤通用停词（避免"请、写作、内容"等污染）
        3. 提取提示中的动作+对象组合
        4. 返回最相关的3-5个核心词
        """
        outline_text = " ".join(str(outline or "").split()).strip()
        prompt_text = " ".join(str(prompt or "").split()).strip()
        combined = (outline_text + " " + prompt_text)[:400]
        
        # 提取候选词汇
        tokens = re.findall(r"[A-Za-z\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff\-_]{1,30}", combined)
        
        # 更严格的停词集合（针对生成/大纲特定词汇）
        generic_stop_tokens = {
            # 通用指令词
            "section", "subsection", "outline", "prompt", "chapter", "part",
            "write", "writing", "draft", "content", "article", "essay",
            "要求", "生成", "内容", "小节", "章节", "大纲", "写作", "草稿",
            # 连接词
            "的", "和", "与", "或", "的", "在", "是", "有", "了", "将",
            "please", "write", "about", "regarding", "concerning",
            # 语言学术词
            "includes", "includes", "describe", "discuss", "explain", "detail"
        }
        
        domain_terms: List[str] = []
        for token in tokens:
            normalized = token.strip().lower()
            
            # 过滤规则
            if not normalized or len(normalized) <= 1 or normalized.isdigit():
                continue
            if normalized in generic_stop_tokens or len(normalized) > 30:
                continue
            if re.fullmatch(r"[\d\.\-:]+", normalized):
                continue
            if normalized not in domain_terms:
                domain_terms.append(normalized)
        
        # 返回最相关的领域词
        result = " ".join(domain_terms[:5])[:80]
        return result

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

        # 【优化1.1】强制领域锚定：在每个候选查询前后都加入 topic_context
        topic_context = self._extract_topic_context(outline_text, prompt_text)
        domain_suffix = f"领域:{topic_context}" if topic_context else ""
        
        candidates_raw = [merged, title_like, semantic_query, prompt_text[:180]]
        candidates: List[str] = []
        seen = set()
        for candidate in candidates_raw:
            cleaned = " ".join(str(candidate or "").split()).strip()
            if not cleaned:
                continue
            
            # 【优化1.2】将topic_context前置到查询，强制领域范围
            if topic_context:
                domain_anchored = f"[{topic_context}] {cleaned}"[:300]
            else:
                domain_anchored = cleaned

            # 【优化1.3】追加领域后缀，避免检索器仅匹配到跨域高频词
            if domain_suffix and domain_suffix.lower() not in domain_anchored.lower():
                domain_anchored = f"{domain_anchored} {domain_suffix}"[:300]
            
            key = domain_anchored.lower()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(domain_anchored)

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
        # When verifier does not provide per-dimension thresholds, enforce stricter defaults
        if not isinstance(dimension_thresholds, dict):
            dimension_thresholds = {}
        dimension_thresholds.setdefault("evidence_grounding", 0.30)
        dimension_thresholds.setdefault("logical_coherence", 0.25)
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
        rel_threshold: float = 0.50,
        red_threshold: float = 0.75
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
            "generator_short_draft_total": 0,
            "verifier_error_total": 0,
            "token_usage": {
                "prompt_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 0,
            },
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
                            generated_content = subsection_gen_result.get("draft", "")
                            has_usable_forced_draft = forced_pass and bool(str(generated_content or "").strip())
                            forced_should_fail = forced_pass and (not self.allow_forced_pass) and (not has_usable_forced_draft)
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
                            document_result["verifier_error_total"] += int(metrics.get("verifier_error", 0) or 0)
                            document_result["controller_calls_total"] += int(metrics.get("controller_calls", 0) or 0)
                            document_result["controller_success_total"] += int(metrics.get("controller_success", 0) or 0)
                            document_result["controller_error_total"] += int(metrics.get("controller_error", 0) or 0)
                            document_result["controller_unavailable_total"] += int(metrics.get("controller_unavailable", 0) or 0)
                            document_result["controller_ineffective_total"] += int(metrics.get("controller_ineffective", 0) or 0)
                            document_result["controller_fallback_outline_total"] += int(metrics.get("controller_fallback_outline", 0) or 0)
                            document_result["controller_exhausted_total"] += int(metrics.get("controller_exhausted", 0) or 0)
                            document_result["generator_short_draft_total"] += int(metrics.get("generator_short_draft", 0) or 0)
                            for token_key in [
                                "prompt_tokens",
                                "output_tokens",
                                "total_tokens",
                                "prompt_cache_hit_tokens",
                                "prompt_cache_miss_tokens",
                            ]:
                                document_result["token_usage"][token_key] += int(metrics.get(token_key, 0) or 0)
                            
                            if self.history_manager and (not forced_should_fail):
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
                                        "source_results": subsection_gen_result.get("source_results", []),
                                    }
                                )
                                self.history_manager.add_passed_history(
                                    document_id=document_id,
                                    section_id=section_id,
                                    subsection_id=subsection_id,
                                    content=generated_content,
                                    order_index=history_order
                                )

                            if forced_should_fail:
                                document_result["failed_subsections"].append({
                                    "section_id": section_id,
                                    "subsection_id": subsection_id,
                                    "reason": force_reason or "forced_pass_disallowed",
                                    "iterations": subsection_gen_result.get("iterations", 0),
                                })
                                self._emit_progress_event(
                                    document_id=document_id,
                                    section_id=section_id,
                                    subsection_id=subsection_id,
                                    stage="subsection_failed",
                                    message=f"小节未通过（禁止强制通过）: {section_title} > {subsection_title}",
                                    metadata={
                                        "iterations": subsection_gen_result.get("iterations", 0),
                                        "verification": verification,
                                        "forced_pass": forced_pass,
                                        "force_reason": force_reason,
                                    },
                                )
                            else:
                                document_result["passed_subsections"] += 1
                            if forced_pass:
                                document_result["forced_subsections"].append({
                                    "section_id": section_id,
                                    "subsection_id": subsection_id,
                                    "reason": force_reason,
                                    "iterations": subsection_gen_result.get("iterations", 0),
                                })
                            if not forced_should_fail:
                                self._emit_progress_event(
                                    document_id=document_id,
                                    section_id=section_id,
                                    subsection_id=subsection_id,
                                    stage="subsection_forced_pass" if forced_pass else "subsection_passed",
                                    message=(
                                        f"小节达到最大修复轮次，保留最佳草稿并继续: {section_title} > {subsection_title}"
                                        if forced_pass
                                        else f"小节通过验证: {section_title} > {subsection_title}"
                                    ),
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
                                "success": not forced_should_fail,
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
                                "source_results": subsection_gen_result.get("source_results", []),
                                "token_usage": {
                                    "prompt_tokens": int(metrics.get("prompt_tokens", 0) or 0),
                                    "output_tokens": int(metrics.get("output_tokens", 0) or 0),
                                    "total_tokens": int(metrics.get("total_tokens", 0) or 0),
                                    "prompt_cache_hit_tokens": int(metrics.get("prompt_cache_hit_tokens", 0) or 0),
                                    "prompt_cache_miss_tokens": int(metrics.get("prompt_cache_miss_tokens", 0) or 0),
                                },
                                "length": len(generated_content)
                            })
                            
                        else:
                            err = subsection_gen_result.get("error", "Unknown error")
                            print(f"⚠️ 当前小节返回失败结果: {err}")
                            failed_draft = str(subsection_gen_result.get("draft", "") or "").strip()
                            if failed_draft:
                                fallback_content = failed_draft
                            else:
                                fallback_content = f"（系统兜底）{subsection_title}\n\n（内容生成失败，仍在恢复中）"
                            section_result["subsections"].append({
                                "subsection_id": subsection_id,
                                "subsection_title": subsection_title,
                                "content": fallback_content,
                                "outline": subsection_outline,
                                "success": False,
                                "iterations": subsection_gen_result.get("iterations", 0),
                                "verification": subsection_gen_result.get("verification", {}),
                                "bandit": subsection_gen_result.get("bandit", {}),
                                "forced_pass": True,
                                "force_reason": "subsection_fallback_on_error",
                                "rag_used": bool(subsection_gen_result.get("rag_used", False)),
                                "rag_search_success": bool(subsection_gen_result.get("rag_search_success", False)),
                                "controller_effective": bool(subsection_gen_result.get("controller_effective", False)),
                                "source_results": subsection_gen_result.get("source_results", []),
                                "length": len(fallback_content),
                            })
                            document_result["failed_subsections"].append({
                                "section_id": section_id,
                                "subsection_id": subsection_id,
                                "reason": str(err),
                                "iterations": subsection_gen_result.get("iterations", 0),
                            })
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
                                stage="subsection_failed",
                                message=f"小节生成失败: {section_title} > {subsection_title}",
                                metadata={"error": err},
                            )
                    
                    except Exception as e:
                        print(f"⚠️ 小节生成异常，启用兜底继续文档流程: {e}")
                        error_str = str(e)[:200]
                        fallback_content = f"（系统兜底）{subsection_title}\n\n（内容生成异常，仍在恢复中）"
                        section_result["subsections"].append({
                            "subsection_id": subsection_id,
                            "subsection_title": subsection_title,
                            "content": fallback_content,
                            "outline": subsection_outline,
                            "success": False,
                            "iterations": 0,
                            "verification": {},
                            "bandit": {},
                            "forced_pass": True,
                            "force_reason": "subsection_exception_fallback",
                            "length": len(fallback_content),
                        })
                        document_result["failed_subsections"].append({
                            "section_id": section_id,
                            "subsection_id": subsection_id,
                            "reason": "subsection_exception_fallback",
                            "iterations": 0,
                        })
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
                            stage="subsection_failed",
                            message=f"小节异常失败: {section_title} > {subsection_title}",
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
            # 如果运行时没有产生 bandit 汇总（常见于本地调试或 Controller 没有直连），
            # 试图从 controller_bandit_events.jsonl 读取最近的 events 进行聚合补齐，供前端展示。
            try:
                recent_stats = self._load_recent_bandit_stats(max_events=300)
                # 仅在当前结果没有真实计数时采用补齐值
                if int(document_result.get("bandit_reward_count", 0) or 0) == 0 and int(recent_stats.get("bandit_reward_count", 0) or 0) > 0:
                    document_result["bandit_selected_arm_counts"] = recent_stats.get("bandit_selected_arm_counts", {})
                    document_result["bandit_reward_sum"] = float(recent_stats.get("bandit_reward_sum", 0.0) or 0.0)
                    document_result["bandit_reward_count"] = int(recent_stats.get("bandit_reward_count", 0) or 0)
                    document_result["bandit_reward_avg"] = float(recent_stats.get("bandit_reward_avg", 0.0) or 0.0)
                    document_result["bandit_last_selected_arm"] = str(recent_stats.get("bandit_last_selected_arm", "") or "")
                    document_result["bandit_last_selection_mode"] = str(recent_stats.get("bandit_last_selection_mode", "") or "")
                    document_result["bandit_drift_events"] = int(recent_stats.get("bandit_drift_events", 0) or 0)
            except Exception:
                pass

            document_result["bandit_reward_avg"] = round(float(document_result.get("bandit_reward_avg", 0.0) or 0.0), 4)
            document_result["bandit_drift_trigger_rate"] = round(
                float(document_result.get("bandit_drift_triggered_subsections", 0) or 0) / max(1, total_subsections_generated),
                4,
            )
            cache_hits = int(document_result.get("token_usage", {}).get("prompt_cache_hit_tokens", 0) or 0)
            cache_misses = int(document_result.get("token_usage", {}).get("prompt_cache_miss_tokens", 0) or 0)
            document_result["prompt_cache_hit_rate"] = round(cache_hits / max(1, cache_hits + cache_misses), 4)

            # 文档级成功判定：存在失败小节则返回 partial/failed，避免掩盖真实质量问题
            document_result["success"] = len(document_result["failed_subsections"]) == 0
            
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
            print(f"   - 短草稿重写: {document_result['generator_short_draft_total']}")
            print(f"   - Token usage: {document_result['token_usage']}")
            print(f"   - Prompt cache hit rate: {document_result['prompt_cache_hit_rate']}")
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
                    "generator_short_draft_total": document_result["generator_short_draft_total"],
                    "token_usage": document_result["token_usage"],
                    "prompt_cache_hit_rate": document_result["prompt_cache_hit_rate"],
                    "controller_triggered_subsections": document_result["controller_triggered_subsections"],
                    "verifier_failed_total": document_result["verifier_failed_total"],
                    "verifier_error_total": document_result["verifier_error_total"],
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
                "generator_short_draft_total": document_result.get("generator_short_draft_total", 0),
                "verifier_error_total": document_result.get("verifier_error_total", 0),
                "token_usage": document_result.get("token_usage", {}),
                "prompt_cache_hit_rate": document_result.get("prompt_cache_hit_rate", 0.0),
                # Include quality metrics fields to avoid zero defaults on frontend
                "quality_score_avg": float(document_result.get("quality_score_avg", 0.0) or 0.0),
                "quality_overall_uncertainty_avg": float(document_result.get("quality_overall_uncertainty_avg", 0.0) or 0.0),
                "quality_dimension_avgs": document_result.get("quality_dimension_avgs", {}),
                "quality_weights": document_result.get("quality_weights", {}),
                "unieval_available_subsections": int(document_result.get("unieval_available_subsections", 0) or 0),
                "unieval_fallback_subsections": int(document_result.get("unieval_fallback_subsections", 0) or 0),
                "unieval_available_ratio": float(document_result.get("unieval_available_ratio", 0.0) or 0.0),
                "unieval_fallback_ratio": float(document_result.get("unieval_fallback_ratio", 0.0) or 0.0),
                "bandit_selected_arm_counts": document_result.get("bandit_selected_arm_counts", {}),
                "bandit_reward_sum": float(document_result.get("bandit_reward_sum", 0.0) or 0.0),
                "bandit_reward_count": int(document_result.get("bandit_reward_count", 0) or 0),
                "bandit_reward_avg": float(document_result.get("bandit_reward_avg", 0.0) or 0.0),
                "bandit_drift_events": int(document_result.get("bandit_drift_events", 0) or 0),
                "bandit_drift_triggered_subsections": int(document_result.get("bandit_drift_triggered_subsections", 0) or 0),
                "bandit_drift_trigger_rate": float(document_result.get("bandit_drift_trigger_rate", 0.0) or 0.0),
                "bandit_last_selected_arm": str(document_result.get("bandit_last_selected_arm", "") or ""),
                "bandit_last_selection_mode": str(document_result.get("bandit_last_selection_mode", "") or ""),
                "bandit_last_constraints": document_result.get("bandit_last_constraints", {}),
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
        rel_threshold: float = 0.50,
        red_threshold: float = 0.75,
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
            "generator_short_draft": 0,
            "verifier_error": 0,
            "prompt_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 0,
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

        subsection_started_at = time.time()
        rag_search_result: Dict[str, Any] = {"success": False, "results": []}
        rag_context = ""
        require_source_citations = False
        source_citation_required = False
        rag_used = False
        rag_selected_query = ""
        last_negative_constraints: Optional[Dict[str, Any]] = None

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
                    try:
                        if self.vector_store is not None:
                            reranked = self.vector_store.reranker.rerank(
                                selected_query,
                                rag_search_result.get("results", []),
                                top_k=max(3, len(rag_search_result.get("results", []))),
                            )
                            if reranked:
                                rag_search_result["results"] = reranked
                                rag_search_result["reranker"] = "flowernet_vector_reranker"
                            indexed = self.vector_store.index_rag_results(
                                selected_query,
                                rag_search_result.get("results", []),
                                namespace=str(document_id or "global"),
                            )
                            rag_search_result["vector_indexed"] = indexed
                            rag_search_result["vector_backend"] = self.vector_store.active_backend
                    except Exception as _e:
                        rag_search_result["vector_index_error"] = str(_e)[:180]
                    rag_context = self.search_engine.format_search_context(
                        rag_search_result,
                        max_items=min(self.rag_max_results, len(rag_search_result.get("results", []) or [])),
                    )
                    # If RAG returned usable sources, citations must be used even when an
                    # old deployment env accidentally left RAG_FORCE_CITATION=false.
                    require_source_citations = True
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
                            "vector_indexed": rag_search_result.get("vector_indexed", 0),
                            "vector_backend": rag_search_result.get("vector_backend", ""),
                        },
                    )
            else:
                vector_hits: List[Dict[str, Any]] = []
                if self.vector_store is not None:
                    try:
                        vector_query = rag_query_candidates[0] if rag_query_candidates else current_outline
                        vector_hits = self.vector_store.query(vector_query, top_k=3, namespace=str(document_id or "global"))
                        if not vector_hits:
                            vector_hits = self.vector_store.query(vector_query, top_k=3)
                    except Exception:
                        vector_hits = []
                if vector_hits:
                    rag_search_result = {
                        "success": True,
                        "query": rag_query_candidates[0] if rag_query_candidates else current_outline,
                        "results": [
                            {
                                "title": hit.get("metadata", {}).get("title") or hit.get("text", "")[:80],
                                "body": hit.get("text", ""),
                                "url": hit.get("metadata", {}).get("url", ""),
                                "quality_score": hit.get("rerank_score", 0.0),
                                "source": "vector_db",
                            }
                            for hit in vector_hits
                        ],
                        "source_type": "vector_db",
                        "vector_backend": getattr(self.vector_store, "active_backend", "memory"),
                    }
                    rag_context = self.search_engine.format_search_context(
                        rag_search_result,
                        max_items=min(self.rag_max_results, len(rag_search_result.get("results", []) or [])),
                    )
                    require_source_citations = True
                    rag_used = True
                    rag_selected_query = str(rag_search_result.get("query") or "")
                    print(f"   🧠 Vector DB RAG 命中: {len(vector_hits)} 条来源")
                    self._emit_progress_event(
                        document_id=document_id,
                        section_id=section_id,
                        subsection_id=subsection_id,
                        stage="rag_vector_success",
                        message="Vector DB RAG 命中，已注入历史来源上下文",
                        metadata={
                            "query": rag_selected_query,
                            "result_count": len(vector_hits),
                            "vector_backend": getattr(self.vector_store, "active_backend", "memory"),
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
                        "source_results": best_candidate.get(
                            "source_results",
                            rag_search_result.get("results", []),
                        ),
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
                    # 优先选择最近一次非大纲型的草稿作为兜底；若都像大纲，则退回最后一次草稿
                    chosen = None
                    for d in reversed(all_drafts):
                        if not self._is_outline_like(d, current_outline):
                            chosen = d
                            break
                    if chosen is None:
                        chosen = all_drafts[-1]
                        fallback_note = "（未完全验证，最后尝试的内容）"
                    else:
                        fallback_note = "（未完全验证，最后尝试的非大纲内容）"
                    fallback_draft = chosen
                else:
                    # 完全没有draft时，返回空内容而不是outline
                    fallback_draft = ""
                    fallback_note = "（内容生成失败，仍在恢复中）"
                fallback_is_meaningful = (
                    bool(str(fallback_draft or "").strip())
                    and len(str(fallback_draft or "").strip()) >= self.min_draft_chars
                    and not self._is_outline_like(fallback_draft, current_outline)
                )
                verifier_unavailable_only = (
                    int(metrics.get("verifier_error", 0) or 0) > 0
                    and int(metrics.get("verifier_failed", 0) or 0) == 0
                )
                best_effort_due_to_verifier = (
                    self.verifier_unavailable_best_effort
                    and verifier_unavailable_only
                    and fallback_is_meaningful
                )
                
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
                # 诊断日志：记录究竟选中了哪个兜底草稿，便于排查 web 侧为何仍展示 outline
                try:
                    print(
                        f"[Orch] verifier_forced_pass for {section_id}::{subsection_id} - "
                        f"chosen_len={len(fallback_draft or '')}, is_outline_like={self._is_outline_like(fallback_draft, current_outline)}, "
                        f"best_candidate_present={bool(best_candidate)}, total_drafts={len(all_drafts)}"
                    )
                except Exception:
                    pass

                if best_effort_due_to_verifier:
                    placeholder_reason = "verifier_unavailable_best_effort"
                else:
                    placeholder_reason = "subsection_timeout_no_draft" if timeout_triggered else "max_attempts_no_draft"
                return {
                    "success": True,
                    "draft": fallback_draft,
                    "final_outline": current_outline,
                    "iterations": iterations - 1,
                    "all_drafts": all_drafts,
                    "source_results": rag_search_result.get("results", []),
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
                        "forced_pass": not best_effort_due_to_verifier,
                        "force_reason": placeholder_reason,
                        "verifier_unavailable": verifier_unavailable_only,
                    },
                    "forced_pass": not best_effort_due_to_verifier,
                    "force_reason": placeholder_reason,
                    "controller_triggered": (metrics.get("controller_calls", 0) > 0) or controller_triggered,
                    "controller_retry_count": controller_retry_count,
                    "metrics": metrics,
                }

            if iterations <= self.max_iterations:
                print(f"\n      尝试 {iterations}/{self.max_iterations}")
            else:
                print(f"\n      尝试 {iterations}（超过配置迭代上限，继续严格闭环直到通过）")
            
            effective_rel_threshold, effective_red_threshold = self._compute_effective_thresholds(
                iteration=iterations,
                rel_threshold=rel_threshold,
                red_threshold=red_threshold,
            )

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

            enhanced_prompt = self._build_enhanced_prompt(
                original_prompt=current_prompt,
                outline=current_outline,
                history_text=history_text,
                rel_threshold=effective_rel_threshold,
                red_threshold=effective_red_threshold,
                rag_context=rag_context,
                require_source_citations=require_source_citations,
                available_source_count=len(rag_search_result.get("results", []) or []),
                negative_constraints=last_negative_constraints,
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
                        chosen = None
                        for d in reversed(all_drafts):
                            if not self._is_outline_like(d, current_outline):
                                chosen = d
                                break
                        if chosen is None:
                            chosen = all_drafts[-1]
                            fallback_note = "（Generator失败，使用最后尝试的内容）"
                        else:
                            fallback_note = "（Generator失败，使用最后尝试的非大纲内容）"
                        fallback_text = chosen
                    else:
                        fallback_text = ""
                        fallback_note = "（Generator失败，内容仍在恢复中）"

                    try:
                        print(
                            f"[Orch] generator_degraded_forced_pass for {section_id}::{subsection_id} - "
                            f"chosen_len={len(fallback_text or '')}, is_outline_like={self._is_outline_like(fallback_text, fail_outline)}, "
                            f"total_drafts={len(all_drafts)}"
                        )
                    except Exception:
                        pass
                    
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
            
            raw_draft = gen_result.get("draft", "")
            draft = self._sanitize_subsection_draft(raw_draft)
            all_drafts.append(draft)
            print(f"         ✅ 生成 {len(draft)} 字符")
            generator_metadata = gen_result.get("metadata") if isinstance(gen_result.get("metadata"), dict) else {}
            prompt_tokens = int(generator_metadata.get("prompt_tokens", 0) or 0)
            output_tokens = int(generator_metadata.get("output_tokens", 0) or generator_metadata.get("completion_tokens", 0) or 0)
            total_tokens = int(generator_metadata.get("total_tokens", 0) or (prompt_tokens + output_tokens))
            prompt_cache_hit_tokens = int(generator_metadata.get("prompt_cache_hit_tokens", 0) or 0)
            prompt_cache_miss_tokens = int(generator_metadata.get("prompt_cache_miss_tokens", 0) or 0)
            metrics["prompt_tokens"] += prompt_tokens
            metrics["output_tokens"] += output_tokens
            metrics["total_tokens"] += total_tokens
            metrics["prompt_cache_hit_tokens"] += prompt_cache_hit_tokens
            metrics["prompt_cache_miss_tokens"] += prompt_cache_miss_tokens
            self._emit_progress_event(
                document_id=document_id,
                section_id=section_id,
                subsection_id=subsection_id,
                stage="generator_success",
                message=f"第 {iterations} 轮：Generator 已产出草稿 ({len(draft)} 字符)",
                metadata={
                    "iteration": iterations,
                    "draft_chars": len(draft),
                    "provider": str(generator_metadata.get("provider", "") or ""),
                    "prompt_tokens": prompt_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": total_tokens,
                    "prompt_cache_hit_tokens": prompt_cache_hit_tokens,
                    "prompt_cache_miss_tokens": prompt_cache_miss_tokens,
                    "generator_degraded_mode": generator_degraded_mode,
                },
            )

            if len(str(draft or "").strip()) < self.min_draft_chars:
                metrics["generator_short_draft"] += 1
                last_negative_constraints = {
                    "feedback": (
                        f"Generator 上轮草稿只有 {len(str(draft or '').strip())} 字符，"
                        f"低于最低要求 {self.min_draft_chars} 字符。必须扩展为完整小节正文。"
                    ),
                    "quality_dimensions_failed": [
                        "coverage_completeness",
                        "evidence_grounding",
                        "logical_coherence",
                    ],
                    "short_draft_chars": len(str(draft or "").strip()),
                    "min_draft_chars": self.min_draft_chars,
                }
                self._emit_progress_event(
                    document_id=document_id,
                    section_id=section_id,
                    subsection_id=subsection_id,
                    stage="generator_short_draft",
                    message=(
                        f"第 {iterations} 轮：草稿过短 ({len(str(draft or '').strip())}/{self.min_draft_chars})，"
                        "跳过 Verifier 并重新生成"
                    ),
                    metadata={
                        "iteration": iterations,
                        "draft_chars": len(str(draft or "").strip()),
                        "min_draft_chars": self.min_draft_chars,
                        "provider": str(generator_metadata.get("provider", "") or ""),
                    },
                )
                time.sleep(self._compute_retry_delay(iterations))
                continue

            provider_name = str(generator_metadata.get("provider", "")).strip().lower()
            if self.source_citation_relaxation_enabled and source_citation_required and provider_name == "ollama":
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
            print(f"🎯 [DEBUG] Calling verifier with thresholds: rel={effective_rel_threshold:.2f}, red={effective_red_threshold:.2f}")
            verify_result = self._call_verifier(
                draft=draft,
                outline=current_outline,
                history=[h["content"] for h in windowed_history],
                rel_threshold=effective_rel_threshold,
                red_threshold=effective_red_threshold,
                context_text=current_prompt,
                source_results=rag_search_result.get("results", []),
                require_source_citations=source_citation_required,
                min_source_citations=self.rag_min_citations,
            )
            
            if not verify_result.get("success"):
                print(f"         ⚠️ Verifier 错误，继续重试当前小节")
                verifier_error = str(verify_result.get("error") or "verifier_unavailable")
                metrics["verifier_error"] += 1
                self._emit_progress_event(
                    document_id=document_id,
                    section_id=section_id,
                    subsection_id=subsection_id,
                    stage="verifier_error",
                    message=f"第 {iterations} 轮：Verifier 调用失败，准备重试",
                    metadata={
                        "iteration": iterations,
                        "error": verifier_error[:500],
                        "draft_chars": len(str(draft or "")),
                    },
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
                f"冗余度: {red_score:.4f} (阈值: {effective_red_threshold:.2f}), "
                f"is_passed={is_passed}"
            )
            if source_citation_required:
                print(
                    f"         来源检查: valid={source_check.get('passed', False)} "
                    f"refs={source_check.get('reference_count', 0)}"
                )

            quality_score = float(verify_result.get("quality_score", 0.0) or 0.0)
            quality_threshold = float(verify_result.get("quality_threshold", 0.0) or 0.0)
            quality_passed = bool(verify_result.get("quality_score_passed", False))
            semantic_dimensions = verify_result.get("quality_dimensions", {}) if isinstance(verify_result.get("quality_dimensions"), dict) else {}
            dimension_check = verify_result.get("quality_dimensions_check", {}) if isinstance(verify_result.get("quality_dimensions_check"), dict) else {}
            failed_dimensions = verify_result.get("quality_dimensions_failed", []) if isinstance(verify_result.get("quality_dimensions_failed"), list) else []
            source_check_full = dict(source_check) if isinstance(source_check, dict) else {}
            available_source_reference_count = len(rag_search_result.get("results", []) or [])
            source_reference_count = max(
                int(source_check_full.get("reference_count", 0) or 0),
                available_source_reference_count,
            )
            source_check_full["reference_count"] = source_reference_count
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
                    "rel_threshold": effective_rel_threshold,
                    "red_threshold": effective_red_threshold,
                    "feedback": str(feedback or "")[:260],
                    "quality_score": quality_score,
                    "quality_score_threshold": quality_threshold,
                    "quality_score_passed": quality_passed,
                    "quality_dimensions": semantic_dimensions,
                    "quality_dimensions_check": dimension_check,
                    "quality_dimensions_failed": failed_dimensions,
                    "quality_dimensions_passed": bool(verify_result.get("quality_dimensions_passed", False)),
                    "dimension_thresholds": verify_result.get("dimension_thresholds", {}),
                    "source_check": source_check_full,
                    "source_check_passed": bool(source_check_full.get("passed", False)),
                    "source_reference_count": source_reference_count,
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
                if self.source_citation_relaxation_enabled and citation_failed:
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
                last_negative_constraints = None
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
                    "source_results": rag_search_result.get("results", []),
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
                    "source_results": rag_search_result.get("results", []),
                "draft": draft,
                "outline": current_outline,
                "iteration": iterations,
                "verification": verify_result,
            }
            last_negative_constraints = verify_result
            if best_candidate is None:
                best_candidate = current_candidate
            else:
                def candidate_gap(candidate: Dict[str, Any]) -> float:
                    verification = candidate.get("verification", {}) if isinstance(candidate, dict) else {}
                    cand_rel = float(verification.get("relevancy_index", 0) or 0)
                    cand_red = float(verification.get("redundancy_index", 1) or 1)
                    cand_quality = float(verification.get("quality_score", 0) or 0)
                    cand_quality_threshold = float(verification.get("quality_threshold", quality_threshold) or quality_threshold or 0)
                    cand_failed_dims = verification.get("quality_dimensions_failed", [])
                    failed_dim_count = len(cand_failed_dims) if isinstance(cand_failed_dims, list) else 0
                    return (
                        max(0.0, rel_threshold - cand_rel) * 1.25
                        + max(0.0, cand_red - red_threshold)
                        + max(0.0, cand_quality_threshold - cand_quality) * 0.85
                        + failed_dim_count * 0.08
                    )

                best_gap = candidate_gap(best_candidate)
                current_gap = candidate_gap(current_candidate)
                best_quality = float(best_candidate.get("verification", {}).get("quality_score", 0) or 0)
                if (
                    current_gap < best_gap
                    or (
                        abs(current_gap - best_gap) < 1e-6
                        and (quality_score, rel_score) > (best_quality, float(best_candidate.get("verification", {}).get("relevancy_index", 0) or 0))
                    )
                ):
                    best_candidate = current_candidate

            if best_candidate and controller_triggered:
                best_verification = best_candidate.get("verification", {})
                current_is_worse_than_best = current_candidate is not best_candidate
                if (
                    current_is_worse_than_best
                    and self._verification_near_pass(best_verification, rel_threshold, red_threshold)
                ):
                    self._emit_progress_event(
                        document_id=document_id,
                        section_id=section_id,
                        subsection_id=subsection_id,
                        stage="verifier_best_effort_pass",
                        message="Controller 后续轮次未优于最佳草稿，保留最佳近通过结果并继续",
                        metadata={
                            "iteration": iterations,
                            "best_iteration": best_candidate.get("iteration", iterations),
                            "relevancy_index": best_verification.get("relevancy_index", 0),
                            "redundancy_index": best_verification.get("redundancy_index", 1),
                            "quality_score": best_verification.get("quality_score", 0),
                            "reason": "prevent_controller_regression",
                        },
                    )
                    return {
                        "success": True,
                        "draft": best_candidate.get("draft", ""),
                        "final_outline": best_candidate.get("outline", current_outline),
                        "iterations": iterations,
                        "source_results": best_candidate.get("source_results", rag_search_result.get("results", [])),
                        "rag_used": bool(best_candidate.get("rag_used", rag_used)),
                        "rag_search_success": bool(best_candidate.get("rag_search_success", bool(rag_search_result.get("success", False)))),
                        "rag_result_count": int(best_candidate.get("rag_result_count", len(rag_search_result.get("results", []))) or 0),
                        "rag_selected_query": str(best_candidate.get("rag_selected_query", rag_selected_query) or ""),
                        "controller_effective": bool(best_candidate.get("controller_effective", bool(controller_last_result.get("effective", False)))),
                        "controller_source": str(best_candidate.get("controller_source", controller_last_result.get("source", "")) or ""),
                        "verification": {
                            **best_verification,
                            "forced_pass": False,
                            "force_reason": "prevent_controller_regression",
                        },
                        "bandit": best_candidate.get("bandit", {}),
                        "all_drafts": all_drafts,
                        "forced_pass": False,
                        "force_reason": "prevent_controller_regression",
                        "controller_triggered": (metrics.get("controller_calls", 0) > 0) or controller_triggered,
                        "controller_retry_count": controller_retry_count,
                        "metrics": metrics,
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
            print(f"🎯 [DEBUG] About to call controller (iteration={iterations}, is_passed={is_passed})")
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
                # 增强 feedback 对象，包含强化改纲策略约束
                enhanced_feedback = dict(verify_result or {})
                enhanced_feedback["controller_strategy_instruction"] = (
                    f"【改纲强化指令】本轮必须通过改进大纲来强化以下方面：\n"
                    f"1. 加强对【{current_outline.split(chr(10))[0][:60]}】的核心概念论述\n"
                    f"2. 确保大纲中的每个要点都能找到相关学术/可靠来源支持\n"
                    f"3. 增加具体数据、案例、图表引用的placeholder\n"
                    f"4. 避免与其他学科跨域（若涉及数学/语言学等边界主题，需明确标注）\n"
                    f"5. 改进逻辑递进：前置概念 → 核心观点 → 证据支撑 → 实际应用\n"
                    f"【最终验证】改纲后的大纲必须能支撑 ≥{effective_rel_threshold:.2f} 相关性 && ≤{effective_red_threshold:.2f} 冗余度"
                )
                controller_result = self._call_controller(
                    old_outline=self._resolve_subsection_outline(
                        document_id=document_id,
                        section_id=section_id,
                        subsection_id=subsection_id,
                        fallback_outline=current_outline,
                    ),
                    failed_draft=draft,
                    feedback=enhanced_feedback,
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
                # Guard against false-positive changed flags from controller fallback responses.
                def _norm_outline(text: str) -> str:
                    return " ".join(str(text or "").strip().split()).lower()
                real_outline_changed = bool(improved_outline) and (_norm_outline(improved_outline) != _norm_outline(current_outline))
                if controller_changed and not real_outline_changed:
                    controller_changed = False

                def _outline_terms(text: str) -> set:
                    raw = str(text or "").lower()
                    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z][a-zA-Z0-9_-]{2,}", raw)
                    stop = {
                        "section", "subsection", "outline", "draft", "content", "please",
                        "generate", "analysis", "example", "examples", "reference",
                        "references", "research", "academic", "chapter",
                        "小节", "章节", "大纲", "内容", "生成", "请帮", "高质量",
                        "长文档", "参考文献", "研究", "分析",
                    }
                    return {tok for tok in tokens if tok not in stop}

                def _outline_guard(old_text: str, new_text: str) -> tuple[bool, str, float]:
                    if not self.controller_guard_enabled:
                        return True, "guard_disabled", 1.0
                    old = str(old_text or "").strip()
                    new = str(new_text or "").strip()
                    if not new:
                        return False, "empty_outline", 0.0
                    if len(old) >= 80 and len(new) < max(80, int(len(old) * 0.55)):
                        return False, "outline_too_short", 0.0
                    prompt_like_patterns = [
                        "请帮我生成", "帮我生成", "please generate", "as an ai",
                        "下面是", "以下是", "高质量长文档", "document topic",
                    ]
                    lowered = new.lower()
                    if any(pat in lowered for pat in prompt_like_patterns):
                        return False, "prompt_like_outline", 0.0
                    old_terms = _outline_terms(old)
                    new_terms = _outline_terms(new)
                    if old_terms:
                        retention = len(old_terms & new_terms) / max(1, len(old_terms))
                        if retention < self.controller_min_outline_retention:
                            return False, f"low_topic_retention:{retention:.2f}", retention
                        return True, "accepted", retention
                    return True, "accepted_no_old_terms", 1.0

                outline_guard_ok, outline_guard_reason, outline_retention = _outline_guard(current_outline, improved_outline)
                if controller_changed and not outline_guard_ok:
                    controller_changed = False
                    controller_effective = False

                # 提取 bandit 信息：优先从 Controller 响应，否则从最新 bandit 事件读取
                _selected_arm = str(controller_result.get("selected_arm", "") or "")
                _reward_val = float(controller_result.get("reward", 0.0) or 0.0)
                _selection_mode = ""
                
                # 尝试从 controller 响应的 bandit.selection.mode 提取 mode
                bandit_obj = controller_result.get("bandit") if isinstance(controller_result.get("bandit"), dict) else {}
                selection_obj = bandit_obj.get("selection") if isinstance(bandit_obj.get("selection"), dict) else {}
                _selection_mode = str(selection_obj.get("mode", "") or "")
                
                # 关键：如果 controller 响应中没有 arm/reward，主动从最近 bandit 事件读取
                # 这确保即使 controller 服务不返回这些字段，我们也能获取真实的 bandit 数据
                if not _selected_arm or _reward_val == 0.0:
                    last_ev = self._read_last_bandit_event()
                    if isinstance(last_ev, dict):
                        _selected_arm = _selected_arm or str(last_ev.get("chosen_arm") or "")
                        if _reward_val == 0.0:  # 如果响应中没有 reward，则用文件中的
                            try:
                                _reward_val = float(last_ev.get("reward", 0.0) or 0.0)
                            except Exception:
                                pass

                print(f"🎯 [Bandit Emit] 发出 controller_result 事件: arm={_selected_arm}, reward={_reward_val}, mode={_selection_mode}")
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
                        "changed_real": real_outline_changed,
                        "outline_guard_ok": outline_guard_ok,
                        "outline_guard_reason": outline_guard_reason,
                        "outline_term_retention": outline_retention,
                        "selected_arm": _selected_arm,
                        "reward": float(_reward_val or 0.0),
                        "selection_mode": _selection_mode,
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
                            "outline_guard_ok": outline_guard_ok,
                            "outline_guard_reason": outline_guard_reason,
                            "outline_term_retention": outline_retention,
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
                            "outline_guard_ok": outline_guard_ok,
                            "outline_guard_reason": outline_guard_reason,
                            "outline_term_retention": outline_retention,
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
                if self.local_outline_fallback_enabled:
                    # Optional fallback only. It is disabled by default because
                    # heuristic outline rewrites can drift from the topic and
                    # make later verifier snapshots worse.
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
        available_source_count: int = 0,
        negative_constraints: Optional[Dict[str, Any]] = None,
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
        if original_prompt and outline:
            compact_original = " ".join(original_prompt.split())
            compact_outline = " ".join(outline.split())
            if compact_outline and compact_outline in compact_original:
                original_prompt = compact_original.replace(compact_outline, "[同当前小节详细大纲，已省略重复文本]")

        enhanced = """你正在撰写一篇文档的某个小节。

【FlowerNet稳定写作协议（跨主题、跨小节复用，用于提高DeepSeek prompt cache 命中）】
以下规则是固定协议。无论主题、章节、大纲、用户背景和参考资料如何变化，都必须优先遵守。

一、写作边界
1. 严格围绕当前小节大纲写作，不复述提示词，不输出生成过程。
2. 当前小节只完成当前大纲要求的内容，不扩写到其他小节，不提前总结全文。
3. 直接输出小节正文，不添加“以下是正文”“下面开始”等前言。
4. 输出必须是完整、可发表长文档的小节正文，不允许只写提纲、摘要、列表标题或任务复述。
5. 原始写作任务中的“附加要求/额外要求/Extra requirements”只作为格式、质量、测试或风格约束；除非其中明确要求作为正文主题，否则不得把测试、复测、修复、引用格式等约束词写成正文内容点。

二、学术质量
1. 采用专业中文学术文体，段落之间逻辑清晰、证据明确、过渡自然。
2. 每个核心段落采用 Claim（主张）→ Evidence（证据）→ Reasoning（推理）→ Transition（过渡）→ Implication（小结）的论证链。
3. 对理论概念、技术机制、实证结果、历史事实、政策判断和强结论给出可验证支撑。
4. 避免空泛套话、泛化结论、重复定义、无来源数据、跨主题案例和不必要的背景铺垫。
5. 避免复制前文，避免换词复述，确保每一段都贡献新的信息或新的分析角度。

三、引用与证据
1. 如果提供了参考资料，必须优先使用与当前小节主题高度匹配、专业且可信的来源。
2. 正文引用必须使用紧凑 IEEE 标记，如 [1][2]；正文标记必须和 References 中的编号一致。
3. 引用标记必须出现在真正被来源支撑的句子旁边，不能只在段末或 References 中堆积。
4. 禁止虚构论文、虚构 DOI、虚构 URL、虚构作者、虚构出版物。
5. 没有 URL 时也可以引用真实书籍、经典论文、权威综述、标准、报告或高可信机构资料。
6. 若某来源与当前小节不属于同一问题域，即使看起来学术，也不得强行引用。

四、引用使用的三步证据对齐工作流
第1步 - 提取摘要：
  读取来源的标题、摘要、关键词和可见内容，提取核心问题、方法、对象和结论。
第2步 - 判定匹配：
  判断该来源是否能直接支撑当前小节大纲中的某个核心要点。
  允许：同领域理论、同问题方法、同对象实证、同主题权威综述。
  禁止：关键词偶然相同但学科/对象/问题不一致，或只能泛泛关联的来源。
第3步 - 条件引用：
  通过匹配后才在正文中使用 [序号]；未通过则跳过该来源。

五、格式与可读性
1. 不要使用 Markdown 标题符号（例如 #、##、####）。
2. 如需分层，用自然段或“1.”“2.”编号句，并保持每个编号单独成段。
3. 公式必须用清楚的线性数学表达或 LaTeX 风格表达，不能输出乱码。
4. 段落长度适中，避免整页单段；术语第一次出现时给出必要解释。
5. 结尾应自然过渡到下一小节或回扣当前小节目标，不做全文结论。
"""

        if CITATION_DRIFT_PREVENTION_PROMPT:
            enhanced += f"""

【引用漂移防护（固定协议补充，必须遵守）】
{CITATION_DRIFT_PREVENTION_PROMPT}
"""

        enhanced += f"""

【当前小节的详细大纲（这是内容的完整范围和边界，必须100%严格遵循）】
{outline}

"""

        if original_prompt:
            enhanced += f"""【原始写作任务与风格要求（必须兼容遵循）】
{original_prompt}

"""

        persona_block = os.getenv("PERSONA_PROMPT", "").strip()
        if persona_block:
            enhanced += f"""【Persona 风格约束（必须遵守）】
{persona_block}

"""

        if rag_context:
            enhanced += f"""{rag_context}

"""

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
   - 字数控制在 900～1300 字；低于 800 字会被系统视为短草稿并要求重写
   - 不要使用 Markdown 标题符号（例如 #、##、####）；如需分层，用自然段或“1.”“2.”编号句，并保持每个编号单独成段
   - 表述专业、准确、避免空洞内容
   - 必须采用论证链结构：Claim（主张）→ Evidence（证据）→ Reasoning（推理）→ Transition（过渡）→ Implication（小结）
   - 至少使用 1 个显式过渡词（例如：因此、然而、此外、总之 / therefore, however, moreover, in conclusion）
   - 若出现强结论（如“必须”“证明了”“it is clear”），必须附带可验证事实或引用

"""
        else:
            enhanced += f"""【严格的生成要求】
1. 相关性（必须 >= {rel_threshold:.2f}）：
   - 内容的每一句话都必须直接对应大纲中的某个要点
   - 不允许任何与大纲无关的内容或例子
   - 确保段落标题直接来自或对应大纲的标题

2. 质量要求：
   - 字数控制在 900～1300 字；低于 800 字会被系统视为短草稿并要求重写
   - 不要使用 Markdown 标题符号（例如 #、##、####）；如需分层，用自然段或“1.”“2.”编号句，并保持每个编号单独成段
   - 表述专业、准确、避免空洞内容
   - 必须采用论证链结构：Claim（主张）→ Evidence（证据）→ Reasoning（推理）→ Transition（过渡）→ Implication（小结）
   - 至少使用 1 个显式过渡词（例如：因此、然而、此外、总之 / therefore, however, moreover, in conclusion）
   - 若出现强结论（如“必须”“证明了”“it is clear”），必须附带可验证事实或引用

"""

        if require_source_citations:
            source_count = max(0, int(available_source_count or 0))
            allowed_ids = "、".join(f"[{idx}]" for idx in range(1, source_count + 1)) or "[1]"
            min_marker_count = 2 if source_count >= 2 else 1
            enhanced += f"""【来源引用硬性要求（CRITICAL - 强制执行）】
✓ 本小节可用引用编号只有：{allowed_ids}
✓ 如果大纲、原始任务或上轮反馈中出现超出上述范围的编号（例如 [6][7][8]），必须忽略并改用上述可用编号，禁止输出不存在的编号
✓ 内联引用标记强制要求：本小节正文至少插入 {min_marker_count} 处专业来源引用，使用紧凑 IEEE 标记如 [1][2]
✓ 关键事实/数据处必须有引用，理论/框架处必须有引用
✓ 不要在本小节末尾输出 References / Bibliography / 参考文献块；整篇文档会在最后统一汇总 References
✓ 禁止虚构论文、编造链接、引用不相关来源；没有 URL 时也必须保留最可信的真实书籍、论文或权威综述来源
✓ 最低标准：正文至少 {min_marker_count} 个内联引用标记，且每个编号必须来自可用编号集合
"""

        if negative_constraints:
            failed_dims = negative_constraints.get("quality_dimensions_failed") if isinstance(negative_constraints.get("quality_dimensions_failed"), list) else []
            source_check = negative_constraints.get("source_check") if isinstance(negative_constraints.get("source_check"), dict) else {}
            blacklist = source_check.get("blacklist_matches") if isinstance(source_check.get("blacklist_matches"), list) else []
            feedback_text = str(negative_constraints.get("feedback", "") or "").strip()

            enhanced += """【负向约束重试（CRITICAL - 本轮必须改进）】
上一轮被 Verifier 判定失败，本轮MUST IMPROVE或系统自动标记为"失败"。需要在以下方面明确加强：
"""
            if failed_dims:
                enhanced += f"\n【失败维度 - 本轮必须改进】：{', '.join(str(x) for x in failed_dims)}\n"
            if feedback_text:
                enhanced += f"【Verifier反馈 - 必须立即纠正】：{feedback_text[:280]}\n"
            if blacklist:
                enhanced += "【禁止再次引用 - 跨领域来源黑名单】：\n"
                for item in blacklist[:6]:
                    title = str(item.get("title", "") or "")[:120]
                    matched = str(item.get("match", "") or item.get("match_keyword", "") or "")[:40]
                    if title or matched:
                        enhanced += f"  ❌ {title} (黑名单关键词: {matched})\n"
            enhanced += f"""
【本轮强制要求】：
- 加强与当前小节【{outline.split(chr(10))[0][:60]}】直接相关的内容、数据、案例
- 删除所有与小节主题不一致的观点和跨领域引用
- 至少提供 2+ 处事实句 + [序号] 内联引用（不是末尾列表！）
- 只能使用当前参考资料中存在的编号；不要为了“增加引用”而输出不存在的 [6][7][8] 等编号
- 改进逻辑连贯性：确保每个段落都能直接回答大纲中的某个要点
- 若上一轮短于 {self.min_draft_chars} 字符，本轮必须扩展为 900～1300 字的完整正文
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
                call_prompt = prompt
                call_tokens = self.generator_max_tokens
                used_compact_prompt = False
                if (
                    self.orch_compact_generation_enabled
                    and len(str(prompt or "")) >= self.orch_compact_prompt_trigger_chars
                    and hasattr(self._local_generator, "_build_compact_generation_prompt")
                ):
                    call_prompt = self._local_generator._build_compact_generation_prompt(prompt)
                    call_tokens = min(self.generator_max_tokens, self.orch_compact_max_tokens)
                    used_compact_prompt = True
                    print(
                        "      [_call_generator] Using compact prompt "
                        f"({len(str(prompt or ''))} -> {len(str(call_prompt or ''))} chars)"
                    )

                result = self._local_generator.generate_draft(prompt=call_prompt, max_tokens=call_tokens)
                elapsed = time.time() - start
                print(f"      [_call_generator] Local call returned in {elapsed:.1f}s: success={result.get('success')}")
                if used_compact_prompt and isinstance(result, dict):
                    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
                    metadata["orchestrator_compact_prompt"] = True
                    metadata["original_prompt_chars"] = len(str(prompt or ""))
                    metadata["compact_prompt_chars"] = len(str(call_prompt or ""))
                    result["metadata"] = metadata
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
        context_text: str = "",
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
                        "context_text": context_text,
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
                    try:
                        response_text = response.text[:500]
                    except Exception:
                        response_text = ""
                    last_error = f"HTTP {response.status_code}: {response_text}".strip()
                    print(f"      [_call_verifier] HTTP {response.status_code} after {elapsed:.1f}s: {response_text[:180]}")
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
