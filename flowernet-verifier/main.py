from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import uvicorn
import jieba
import os
import re
import json
import time
from collections import deque
from urllib.parse import urlparse, urlunparse
from rank_bm25 import BM25Okapi
from rouge_score import rouge_scorer
import requests

try:
    from sentence_transformers import SentenceTransformer, util as st_util
    _HAS_ST = True
except Exception:
    SentenceTransformer = None
    st_util = None
    _HAS_ST = False

from history_store import HistoryManager

# 英文停用词表：过滤高频功能词，只保留实义词参与计算
_EN_STOPWORDS = {
    'the','a','an','is','are','was','were','be','been','being',
    'have','has','had','do','does','did','will','would','could',
    'should','may','might','shall','can','need','dare','ought',
    'used','to','of','in','on','at','by','for','with','about',
    'against','between','into','through','during','before','after',
    'above','below','from','up','down','out','off','over','under',
    'again','further','then','once','and','but','or','nor','so',
    'yet','both','either','neither','not','only','same','than',
    'too','very','just','because','as','until','while','that',
    'this','these','those','it','its','i','you','he','she','we',
    'they','what','which','who','whom','when','where','why','how',
    'all','each','every','more','most','other','some','such','no',
    'any','if','my','your','his','her','our','their','there','also',
    'its','use','also','since','however','therefore','thus','hence',
}


# ============ FlowerNetVerifier 轻量级版本 ============
class FlowerNetVerifier:
    def __init__(self):
        print("🌸 FlowerNet 验证层启动（优化版）")
        self.public_url = os.getenv('VERIFIER_PUBLIC_URL', 'http://localhost:8000')
        print(f"  - Verifier Public URL: {self.public_url}")
        self.scorer = rouge_scorer.RougeScorer(['rougeL'], use_stemmer=True)
        self._unieval_latency_samples = deque(maxlen=20)
        self._unieval_timeout_floor = self._safe_float(os.getenv("UNIEVAL_TIMEOUT_MIN", "12"), 12.0)
        self._unieval_timeout_ceiling = self._safe_float(os.getenv("UNIEVAL_TIMEOUT_MAX", "120"), 120.0)
        self._unieval_timeout_base = self._safe_float(os.getenv("UNIEVAL_TIMEOUT_BASE", "20"), 20.0)
        self._unieval_timeout_buffer = self._safe_float(os.getenv("UNIEVAL_TIMEOUT_BUFFER", "8"), 8.0)
        self._unieval_timeout_token_factor = self._safe_float(os.getenv("UNIEVAL_TIMEOUT_TOKEN_FACTOR", "0.006"), 0.006)
        self._unieval_timeout_latency_factor = self._safe_float(os.getenv("UNIEVAL_TIMEOUT_LATENCY_FACTOR", "1.6"), 1.6)
        self._unieval_health_timeout = max(2.0, self._safe_float(os.getenv("UNIEVAL_HEALTH_TIMEOUT", "4"), 4.0))
        # Persona consistency configuration
        self.persona_sim_threshold = self._safe_float(os.getenv("PERSONA_SIM_THRESHOLD", "0.65"), 0.65)
        self.persona_model_name = os.getenv("PERSONA_SEMANTIC_MODEL", "sentence-transformers/paraphrase-MiniLM-L6-v2").strip()
        self._persona_model = None
        # 引用黑名单/白名单配置（可通过环境变量覆盖）
        try:
            raw = os.getenv("REFERENCE_BLACKLIST_JSON", "").strip()
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    self.reference_blacklist = parsed
                else:
                    self.reference_blacklist = {}
            else:
                self.reference_blacklist = {}
        except Exception:
            self.reference_blacklist = {}

        # 备选默认敏感词集合（用于快速过滤明显跨学科来源）
        self._math_terms = {
            "随机变量", "随机过程", "多维", "multivariate", "probability", "expectation", "variance",
            "stochastic", "measure theory", "lemma", "theorem",
        }
        self._ling_terms = {
            "第二语言", "语言习得", "汉语作为第二语言", "phonetics", "phonology", "syntax", "morphology",
            "second language acquisition", "l2",
        }
        self._negotiation_terms = {
            "谈判", "谈判策略", "博弈", "博弈论", "negotiation", "bargaining", "商业", "商务", "谈判技巧",
        }
        print("✅ 验证层就绪")

    def _get_persona_model(self):
        if not _HAS_ST:
            return None
        if self._persona_model is None:
            try:
                self._persona_model = SentenceTransformer(self.persona_model_name)
            except Exception as e:
                print(f"⚠️ Persona semantic model load failed: {e}")
                self._persona_model = None
        return self._persona_model

    def _calculate_persona_alignment(self, draft: str, persona_prompt: str) -> Dict[str, Any]:
        """Compute persona/style alignment from semantic similarity + lightweight heuristics."""
        text = str(draft or "")
        persona = str(persona_prompt or "")

        semantic_similarity = 0.0
        model = self._get_persona_model()
        if model is not None and text.strip() and persona.strip():
            try:
                emb_draft = model.encode(text, convert_to_tensor=True)
                emb_persona = model.encode(persona, convert_to_tensor=True)
                semantic_similarity = float(st_util.cos_sim(emb_draft, emb_persona).item())
            except Exception as e:
                print(f"⚠️ Persona semantic scoring failed: {e}")

        lower = text.lower()
        words = re.findall(r"[A-Za-z\u4e00-\u9fff]+", text)
        contractions = sum(lower.count(c) for c in ["n't", "'re", "'ve", "'ll", "'m", "'s"])
        first_person = sum(lower.count(p) for p in [" i ", " we ", " my ", " our "])
        transition_count = len(re.findall(r"因此|然而|此外|总之|therefore|however|moreover|in conclusion", lower))
        avg_word_len = (sum(len(w) for w in words) / max(1, len(words))) if words else 0.0

        heuristic_formality = self._clip01(1.0 - min(1.0, contractions / 10.0))
        heuristic_objectivity = self._clip01(1.0 - min(1.0, first_person / 12.0))
        heuristic_structure = self._clip01(min(1.0, transition_count / 6.0))
        heuristic_lexical = self._clip01(min(1.0, avg_word_len / 6.0))
        heuristic_score = self._clip01(
            0.35 * heuristic_formality + 0.25 * heuristic_objectivity + 0.25 * heuristic_structure + 0.15 * heuristic_lexical
        )

        # If semantic is unavailable, rely on heuristic; otherwise fuse.
        if semantic_similarity <= 0.0:
            final_similarity = heuristic_score
        else:
            final_similarity = self._clip01(0.75 * semantic_similarity + 0.25 * heuristic_score)

        return {
            "similarity": round(final_similarity, 4),
            "semantic_similarity": round(float(max(0.0, semantic_similarity)), 4),
            "heuristic_score": round(heuristic_score, 4),
            "heuristics": {
                "contractions": int(contractions),
                "first_person_count": int(first_person),
                "transition_count": int(transition_count),
                "avg_word_len": round(float(avg_word_len), 2),
            },
        }

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    def _clip01(self, value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    def _coerce_probability(self, value: Any, field_name: str) -> float:
        if isinstance(value, bool):
            value = float(value)
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"UniEval field '{field_name}' is not numeric: {value!r}") from exc
        if not 0.0 <= result <= 1.0:
            raise ValueError(f"UniEval field '{field_name}' out of range [0, 1]: {result}")
        return result

    def _unieval_base_url(self, endpoint: str) -> str:
        parsed = urlparse(endpoint)
        if not parsed.scheme or not parsed.netloc:
            return endpoint.rstrip("/")
        return urlunparse((parsed.scheme, parsed.netloc, "", "", "", "")).rstrip("/")

    def _unieval_ready_url(self, endpoint: str) -> str:
        return self._unieval_base_url(endpoint) + "/health/ready"

    def _wait_for_unieval_ready(self, endpoint: str, timeout_budget: float) -> None:
        ready_url = self._unieval_ready_url(endpoint)
        deadline = time.monotonic() + max(1.0, timeout_budget)
        sleep_seconds = 0.8
        last_error = ""

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                resp = requests.get(ready_url, timeout=min(self._unieval_health_timeout, max(1.0, remaining)))
                if resp.status_code == 200:
                    return
                last_error = f"health status={resp.status_code}"
            except Exception as exc:
                last_error = str(exc)

            time.sleep(min(sleep_seconds, max(0.2, deadline - time.monotonic())))
            sleep_seconds = min(5.0, sleep_seconds * 1.5)

        raise RuntimeError(f"UniEval not ready before timeout budget exhausted: {last_error}")

    def _estimate_unieval_timeout(self, draft: str, outline: str, history_list: List[str]) -> float:
        payload_chars = len(draft or "") + len(outline or "") + sum(len(item or "") for item in (history_list or []))
        size_component = min(40.0, (payload_chars / 1000.0) * self._unieval_timeout_token_factor * 1000.0)

        if self._unieval_latency_samples:
            sorted_samples = sorted(self._unieval_latency_samples)
            percentile_index = max(0, int(round(0.95 * (len(sorted_samples) - 1))))
            observed_p95 = float(sorted_samples[percentile_index])
        else:
            observed_p95 = self._unieval_timeout_base

        timeout = observed_p95 * self._unieval_timeout_latency_factor + size_component + self._unieval_timeout_buffer
        timeout = max(self._unieval_timeout_floor, min(timeout, self._unieval_timeout_ceiling))
        return round(timeout, 2)

    def _compute_semantic_dimensions(
        self,
        draft: str,
        outline: str,
        history_list: List[str],
        rel: Dict[str, Any],
        red: Dict[str, Any],
        source_check: Dict[str, Any],
        context_text: str = "",
        source_results: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, float]:
        draft = draft or ""
        outline = outline or ""
        history_list = history_list or []
        coverage_diag = self._coverage_diagnostics(
            draft=draft,
            outline=outline,
            context_text=context_text,
        )
        evidence_diag = self._evidence_diagnostics(
            draft=draft,
            outline=outline,
            context_text=context_text,
            source_results=source_results or [],
            source_check=source_check,
        )
        coverage_diag["source_topic_coverage"] = self._safe_float(evidence_diag.get("source_topic_coverage"), 0.0)

        keyword_coverage = max(
            self._safe_float((rel.get("details") or {}).get("keyword_coverage"), rel.get("score", 0.0)),
            self._safe_float((rel.get("details") or {}).get("anchor_coverage"), 0.0),
            self._safe_float(coverage_diag.get("target_term_coverage"), 0.0),
        )
        rouge_l = self._safe_float((rel.get("details") or {}).get("rouge_l"), rel.get("score", 0.0))
        bm25_score = self._safe_float((rel.get("details") or {}).get("bm25_score"), rel.get("score", 0.0))

        # 1) Topic alignment: 关注是否紧扣小节主题
        topic_alignment = self._clip01(0.6 * keyword_coverage + 0.25 * rouge_l + 0.15 * bm25_score)

        # 2) Novelty: 与历史去重互补，越高越新
        novelty = self._clip01(1.0 - self._safe_float(red.get("score"), 0.0))

        # 3) Coverage completeness: 是否覆盖大纲关键信息。
        # 旧逻辑只看关键词/ROUGE，容易把“提到主题”误判成“充分覆盖”。
        # 新逻辑加入 target terms、内容面向(aspects)和来源覆盖，逼迫生成器写出
        # 方法、应用、评价、风险、未来方向等 topic-specific 信息。
        coverage_completeness = self._clip01(
            0.36 * keyword_coverage
            + 0.18 * rouge_l
            + 0.26 * self._safe_float(coverage_diag.get("aspect_coverage"), 0.0)
            + 0.12 * self._safe_float(coverage_diag.get("source_topic_coverage"), 0.0)
            + 0.08 * self._safe_float(coverage_diag.get("specificity_score"), 0.0)
        )

        chinese_ratio = (
            len(re.findall(r"[\u4e00-\u9fff]", draft))
            / max(1, len(re.sub(r"\s+", "", draft)))
        )

        # 4) Logical coherence: 使用句长稳定性 + 连接词密度近似估计。
        # 中文学术段落天然比英文短句更长，不能按短句写作目标惩罚正式论文段落。
        sentence_units = [s.strip() for s in re.split(r"[。！？!?；;\n]", draft) if s.strip()]
        if sentence_units:
            lengths = [len(s) for s in sentence_units]
            avg_len = sum(lengths) / len(lengths)
            target_len = 42.0 if chinese_ratio >= 0.35 else 24.0
            len_penalty = abs(avg_len - target_len) / target_len
            connector_count = len(re.findall(
                r"因此|所以|然而|同时|此外|首先|其次|最后|由此|综上|相比|例如|because|however|therefore|moreover|first|second|finally",
                draft.lower(),
            ))
            connector_density = connector_count / max(1, len(sentence_units))
            logical_coherence = self._clip01((1.0 - min(1.0, len_penalty)) * 0.65 + min(1.0, connector_density) * 0.35)
        else:
            logical_coherence = 0.0

        # 5) Evidence grounding: 来源匹配与语义质量
        matched_scores = source_check.get("matched_semantic_scores") or {}
        if matched_scores:
            avg_semantic_match = sum(self._safe_float(v) for v in matched_scores.values()) / len(matched_scores)
        else:
            avg_semantic_match = 0.0
        citation_signal = 1.0 if source_check.get("passed") else 0.35
        evidence_grounding = self._clip01(
            0.36 * avg_semantic_match
            + 0.22 * citation_signal
            + 0.22 * self._safe_float(evidence_diag.get("claim_evidence_alignment"), 0.0)
            + 0.12 * self._safe_float(evidence_diag.get("evidence_type_coverage"), 0.0)
            + 0.08 * self._safe_float(evidence_diag.get("source_usage_coverage"), 0.0)
        )

        # 6) Structure clarity: 小节组织形态质量。
        # 发表型正文通常是多段论证，而不是小节内部继续堆标题/项目符号。
        paragraphs = [
            p.strip()
            for p in re.split(r"\n\s*\n", draft)
            if len(p.strip()) >= 30 and not re.match(r"^#{1,6}\s+", p.strip())
        ]
        heading_count = len(re.findall(r"^#{1,4}\s+", draft, flags=re.MULTILINE))
        bullet_count = len(re.findall(r"^[\-\*\d]+[\.\)]?\s+", draft, flags=re.MULTILINE))
        connector_count = len(re.findall(
            r"因此|所以|然而|同时|此外|首先|其次|最后|由此|综上|相比|例如|because|however|therefore|moreover|first|second|finally",
            draft.lower(),
        ))
        paragraph_signal = min(1.0, len(paragraphs) / 3.0)
        sentence_signal = min(1.0, len(sentence_units) / 8.0)
        transition_signal = min(1.0, connector_count / 5.0)
        outline_signal = min(1.0, (heading_count + bullet_count) / 4.0)
        structure_clarity = self._clip01(
            0.45 * paragraph_signal
            + 0.25 * sentence_signal
            + 0.20 * transition_signal
            + 0.10 * outline_signal
        )

        return {
            "topic_alignment": round(topic_alignment, 4),
            "novelty": round(novelty, 4),
            "coverage_completeness": round(coverage_completeness, 4),
            "logical_coherence": round(logical_coherence, 4),
            "evidence_grounding": round(evidence_grounding, 4),
            "structure_clarity": round(structure_clarity, 4),
        }

    def _try_unieval_dimensions(
        self,
        draft: str,
        outline: str,
        history_list: List[str],
        require_available: bool = False,
    ) -> Dict[str, float]:
        endpoint = os.getenv("UNIEVAL_ENDPOINT", "").strip()
        if not endpoint:
            if require_available:
                raise RuntimeError(
                    "UNIEVAL_ENDPOINT is not configured but REQUIRE_MULTIDIM_QUALITY=true. "
                    "Please set UNIEVAL_ENDPOINT or disable REQUIRE_MULTIDIM_QUALITY."
                )
            return {}

        timeout = max(self._estimate_unieval_timeout(draft, outline, history_list), self._safe_float(os.getenv("UNIEVAL_TIMEOUT", "20"), 20.0))
        max_retries = max(1, int(os.getenv("UNIEVAL_VERIFY_RETRIES", "2")))

        payload = {
            "draft": draft,
            "outline": outline,
            "history": history_list or [],
        }

        if require_available:
            self._wait_for_unieval_ready(endpoint, timeout_budget=timeout)

        last_error = None
        for attempt in range(max_retries):
            start_time = time.monotonic()
            try:
                sess = requests.Session()
                sess.trust_env = False
                resp = sess.post(endpoint, json=payload, timeout=timeout)
                resp.raise_for_status()
                body = resp.json() if resp.content else {}
                if not isinstance(body, dict):
                    last_error = f"Invalid response type: {type(body)}"
                    if attempt < max_retries - 1:
                        continue
                    raise ValueError(last_error)

                scores = body.get("scores") if isinstance(body.get("scores"), dict) else body
                if not isinstance(scores, dict):
                    last_error = f"Scores field is not a dict: {type(scores)}"
                    if attempt < max_retries - 1:
                        continue
                    raise ValueError(last_error)

                result = {
                    "topic_alignment": round(self._coerce_probability(scores.get("consistency", scores.get("relevance", 0.0)), "topic_alignment"), 4),
                    "coverage_completeness": round(self._coerce_probability(scores.get("coherence", scores.get("coverage", 0.0)), "coverage_completeness"), 4),
                    "logical_coherence": round(self._coerce_probability(scores.get("coherence", 0.0), "logical_coherence"), 4),
                    "evidence_grounding": round(self._coerce_probability(scores.get("factuality", scores.get("groundedness", 0.0)), "evidence_grounding"), 4),
                    "structure_clarity": round(self._coerce_probability(scores.get("fluency", scores.get("clarity", 0.0)), "structure_clarity"), 4),
                }

                if any(key not in result for key in ("topic_alignment", "coverage_completeness", "logical_coherence", "evidence_grounding", "structure_clarity")) and require_available:
                    last_error = "UniEval response missing expected dimensions"
                    if attempt < max_retries - 1:
                        continue
                    raise ValueError(last_error)

                elapsed = time.monotonic() - start_time
                self._unieval_latency_samples.append(elapsed)
                return result
            except Exception as e:
                last_error = str(e)
                retryable = isinstance(e, (requests.Timeout, requests.ConnectionError, ValueError, RuntimeError))
                if require_available and attempt < max_retries - 1 and retryable:
                    time.sleep(min(2.0, 0.75 + attempt * 0.5))
                    continue
                if require_available:
                    raise RuntimeError(
                        f"UniEval call failed after {max_retries} retries: {last_error}. "
                        "Endpoint must be available when REQUIRE_MULTIDIM_QUALITY=true."
                    )
                return {}
        
        # 不应该到达这里，但保险起见返回空
        return {}

    def _composite_quality_score(self, dimensions: Dict[str, float]) -> float:
        default_weights = {
            "topic_alignment": 0.26,
            "coverage_completeness": 0.18,
            "logical_coherence": 0.16,
            "evidence_grounding": 0.20,
            "novelty": 0.12,
            "structure_clarity": 0.08,
        }
        weights_raw = os.getenv("QUALITY_DIMENSION_WEIGHTS_JSON", "").strip()
        if weights_raw:
            try:
                parsed = json.loads(weights_raw)
                if isinstance(parsed, dict):
                    for key, value in parsed.items():
                        if key in default_weights:
                            default_weights[key] = self._clip01(self._safe_float(value, default_weights[key]))
            except Exception:
                pass

        total_weight = sum(max(0.0, w) for w in default_weights.values())
        if total_weight <= 0:
            total_weight = 1.0

        score = 0.0
        for key, weight in default_weights.items():
            score += max(0.0, weight) * self._safe_float(dimensions.get(key), 0.0)
        return round(self._clip01(score / total_weight), 4)

    def _quality_dimension_thresholds(self) -> Dict[str, float]:
        """获取每个维度的阈值（可通过 JSON 覆盖，或使用默认值）"""
        # 默认阈值保持适度严格，避免内容过早通过而没有进入 controller 修复环节。
        # 小幅上调即可触发边缘小节修复，避免把正常小节也拖进多轮重写。
        default_thresholds = {
            "topic_alignment": 0.555,
            "coverage_completeness": 0.555,
            "logical_coherence": 0.075,
            "evidence_grounding": 0.355,
            "novelty": 0.685,
            "structure_clarity": 0.535,
        }
        
        thresholds_raw = os.getenv("QUALITY_DIMENSION_THRESHOLDS_JSON", "").strip()
        if thresholds_raw:
            try:
                parsed = json.loads(thresholds_raw)
                if isinstance(parsed, dict):
                    for key, value in parsed.items():
                        if key in default_thresholds:
                            default_thresholds[key] = self._clip01(self._safe_float(value, default_thresholds[key]))
            except Exception:
                pass
        return default_thresholds

    def _check_dimension_thresholds(self, dimensions: Dict[str, float]) -> Dict[str, Any]:
        """检查是否所有维度都通过各自的阈值"""
        thresholds = self._quality_dimension_thresholds()
        passed_per_dimension = {}
        failed_dimensions = []
        
        for dim_name, threshold in thresholds.items():
            dim_value = self._safe_float(dimensions.get(dim_name), 0.0)
            passed = dim_value >= threshold
            passed_per_dimension[dim_name] = {
                "value": dim_value,
                "threshold": threshold,
                "passed": passed,
                "margin": round(dim_value - threshold, 4),
            }
            if not passed:
                failed_dimensions.append(dim_name)
        
        all_passed = len(failed_dimensions) == 0
        return {
            "all_passed": all_passed,
            "per_dimension": passed_per_dimension,
            "failed_dimensions": failed_dimensions,
        }

    def _quality_soft_pass(
        self,
        *,
        quality_score: float,
        quality_threshold: float,
        dimension_check: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Allow high-quality drafts with one tiny dimension miss to pass."""
        if os.getenv("QUALITY_SOFT_PASS_ENABLED", "true").lower() != "true":
            return {"passed": False, "reason": "disabled"}

        failed = list(dimension_check.get("failed_dimensions", []) or [])
        per_dim = dimension_check.get("per_dimension", {}) if isinstance(dimension_check.get("per_dimension"), dict) else {}
        max_failed = max(0, int(os.getenv("QUALITY_SOFT_PASS_MAX_FAILED_DIMS", "1")))
        margin = max(0.0, self._safe_float(os.getenv("QUALITY_SOFT_PASS_DIM_MARGIN", "0.035"), 0.035))
        min_quality = self._safe_float(
            os.getenv("QUALITY_SOFT_PASS_MIN_SCORE", str(max(0.0, quality_threshold - 0.015))),
            max(0.0, quality_threshold - 0.015),
        )
        allowed_dims_raw = os.getenv(
            "QUALITY_SOFT_PASS_ALLOWED_DIMS",
            "logical_coherence,coverage_completeness,structure_clarity,evidence_grounding",
        )
        allowed_dims = {x.strip() for x in allowed_dims_raw.split(",") if x.strip()}

        if not failed or len(failed) > max_failed or quality_score < min_quality:
            return {"passed": False, "reason": "failed_count_or_score"}

        for dim in failed:
            item = per_dim.get(dim, {}) if isinstance(per_dim.get(dim), dict) else {}
            dim_margin = self._safe_float(item.get("margin"), -1.0)
            if dim not in allowed_dims or dim_margin < -margin:
                return {"passed": False, "reason": f"hard_dimension:{dim}"}

        return {
            "passed": True,
            "reason": "minor_dimension_miss",
            "failed_dimensions": failed,
            "min_quality": round(min_quality, 4),
            "margin": round(margin, 4),
        }

    def _quality_weights(self) -> Dict[str, float]:
        weights = {
            "topic_alignment": 0.26,
            "coverage_completeness": 0.18,
            "logical_coherence": 0.16,
            "evidence_grounding": 0.20,
            "novelty": 0.12,
            "structure_clarity": 0.08,
        }
        weights_raw = os.getenv("QUALITY_DIMENSION_WEIGHTS_JSON", "").strip()
        if weights_raw:
            try:
                parsed = json.loads(weights_raw)
                if isinstance(parsed, dict):
                    for key, value in parsed.items():
                        if key in weights:
                            weights[key] = self._clip01(self._safe_float(value, weights[key]))
            except Exception:
                pass
        return weights

    def _fuse_dimensions_with_uncertainty(
        self,
        heuristic_dims: Dict[str, float],
        unieval_dims: Dict[str, float],
    ) -> Dict[str, Any]:
        keys = sorted(set(heuristic_dims.keys()) | set(unieval_dims.keys()))
        fused: Dict[str, float] = {}
        uncertainty: Dict[str, float] = {}
        ci: Dict[str, Dict[str, float]] = {}
        sources: Dict[str, Dict[str, float]] = {}

        # Conservative prior uncertainty for dimensions with single estimator.
        single_source_unc = self._clip01(self._safe_float(os.getenv("QUALITY_SINGLE_SOURCE_UNCERTAINTY", "0.25"), 0.25))

        for key in keys:
            h = heuristic_dims.get(key)
            u = unieval_dims.get(key)
            entries = [float(v) for v in [h, u] if isinstance(v, (int, float))]
            if not entries:
                continue

            if h is not None and u is not None:
                # Two-estimator fusion. UniEval is useful as a second opinion,
                # but the deployed evaluator can be systematically harsh on
                # Chinese long-form paragraphs. Keep heuristic/source/relevance
                # as the primary signal and downweight UniEval when estimators
                # strongly disagree, so one low-confidence evaluator cannot
                # destabilize the controller loop.
                base_weight = self._clip01(self._safe_float(os.getenv("QUALITY_UNIEVAL_WEIGHT", "0.25"), 0.25))
                min_weight = self._clip01(self._safe_float(os.getenv("QUALITY_UNIEVAL_DIVERGENCE_MIN_WEIGHT", "0.08"), 0.08))
                divergence_threshold = self._clip01(self._safe_float(os.getenv("QUALITY_UNIEVAL_DIVERGENCE_THRESHOLD", "0.35"), 0.35))
                disagreement = abs(float(h) - float(u))
                unieval_weight = base_weight
                if disagreement >= divergence_threshold and float(u) < float(h):
                    unieval_weight = min_weight
                mean_val = (1.0 - unieval_weight) * float(h) + unieval_weight * float(u)
                unc = self._clip01(disagreement)
                low = self._clip01(mean_val - 0.5 * unc)
                high = self._clip01(mean_val + 0.5 * unc)
                sources[key] = {
                    "heuristic": round(float(h), 4),
                    "unieval": round(float(u), 4),
                    "unieval_weight": round(unieval_weight, 4),
                }
            else:
                mean_val = float(entries[0])
                unc = single_source_unc
                low = self._clip01(mean_val - 0.5 * unc)
                high = self._clip01(mean_val + 0.5 * unc)
                only_name = "heuristic" if h is not None else "unieval"
                sources[key] = {only_name: round(mean_val, 4)}

            fused[key] = round(self._clip01(mean_val), 4)
            uncertainty[key] = round(unc, 4)
            ci[key] = {"low": round(low, 4), "high": round(high, 4)}

        # System-level uncertainty: mean dimension uncertainty.
        if uncertainty:
            overall_uncertainty = round(sum(uncertainty.values()) / len(uncertainty), 4)
        else:
            overall_uncertainty = 1.0

        return {
            "fused": fused,
            "uncertainty": uncertainty,
            "confidence_interval": ci,
            "sources": sources,
            "overall_uncertainty": overall_uncertainty,
        }

    def _tokenize(self, text):
        """全量分词（含停用词）"""
        tokens = [t.strip().lower() for t in jieba.cut(text)]
        return [t for t in tokens if t]

    def _content_tokens(self, text):
        """实义词分词：过滤英文停用词和单字符 token，用于相似度计算"""
        tokens = [t.strip().lower() for t in jieba.cut(text)]
        return [t for t in tokens if len(t) > 1 and t not in _EN_STOPWORDS]

    def _cjk_bigrams(self, text: str, max_items: int = 120) -> List[str]:
        """Extract stable Chinese character bigrams for topic recall."""
        generic = {
            "本小", "小节", "当前", "大纲", "内容", "生成", "写作", "文档",
            "主题", "要求", "分析", "讨论", "说明", "包括", "以及", "需要",
            "避免", "重复", "章节", "标题", "原始", "任务", "质量",
        }
        items: List[str] = []
        for chunk in re.findall(r"[\u4e00-\u9fff]{3,}", str(text or "")):
            for i in range(0, max(0, len(chunk) - 1)):
                gram = chunk[i:i + 2]
                if gram in generic:
                    continue
                if gram not in items:
                    items.append(gram)
                if len(items) >= max_items:
                    return items
        return items

    def _outline_title_anchor(self, outline: str) -> str:
        text = str(outline or "").strip()
        if not text:
            return ""
        quoted = re.search(r"[“\"]([^”\"]{4,80})[”\"]", text)
        if quoted:
            return quoted.group(1).strip()
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        first_line = re.sub(r"^本小节聚焦", "", first_line).strip()
        first_line = re.split(r"，|。|；|;|\n", first_line)[0].strip()
        return first_line[:100]

    def _content_outline_for_relevance(self, outline: str) -> str:
        """Keep only the subsection's content mission for relevance scoring.

        Generated outlines also carry format constraints, self-audit protocol
        text, and whole-document instructions. Those are important for writing,
        but using all of them as relevance targets unfairly lowers the score of
        an otherwise on-topic subsection.
        """
        text = str(outline or "").strip()
        if not text:
            return ""
        parts: List[str] = []
        title = self._outline_title_anchor(text)
        if title:
            parts.append(title)
        for pattern in [
            r"需要覆盖的基础说明[:：](.*?)(?:避免重复|同时遵守|自审计|$)",
            r"该节系统梳理(.*?)(?:避免重复|同时遵守|自审计|$)",
            r"先界定其在(.*?)(?:写作时|需要覆盖|避免重复|同时遵守|$)",
        ]:
            match = re.search(pattern, text, flags=re.S)
            if match:
                parts.append(match.group(1).strip())
        if not parts:
            first = re.split(r"同时遵守|自审计|附加约束|Extra requirements", text, maxsplit=1)[0]
            parts.append(first.strip())
        scoped = "。".join(part for part in parts if part)
        return scoped or text

    def _topic_anchor_coverage(self, draft: str, outline: str) -> float:
        """Robust topic coverage for Chinese technical prose."""
        draft_lower = str(draft or "").lower()
        topic_terms = [
            term for term in self._topic_terms(outline=outline, context_text="", draft="")
            if len(str(term).strip()) >= 2
        ]
        if topic_terms:
            term_hits = sum(1 for term in topic_terms if str(term).lower() in draft_lower)
            term_coverage = term_hits / max(1, len(topic_terms))
        else:
            term_coverage = 0.0

        grams = self._cjk_bigrams(outline)
        if grams:
            gram_hits = sum(1 for gram in grams if gram in draft_lower)
            gram_coverage = gram_hits / max(1, len(grams))
        else:
            gram_coverage = 0.0

        title_anchor = self._outline_title_anchor(outline)
        title_grams = self._cjk_bigrams(title_anchor, max_items=40)
        if title_grams:
            title_hits = sum(1 for gram in title_grams if gram in draft_lower)
            title_coverage = title_hits / max(1, len(title_grams))
        else:
            title_coverage = 0.0

        broad_coverage = 0.58 * term_coverage + 0.42 * gram_coverage
        title_locked_coverage = 0.82 * title_coverage + 0.18 * gram_coverage
        return self._clip01(max(broad_coverage, title_locked_coverage))

    def _topic_terms(self, outline: str, context_text: str, draft: str = "") -> List[str]:
        """Extract stable topic terms from the subsection task, falling back to draft only when needed."""
        topic_source = f"{outline or ''} {context_text or ''}".strip()
        if not topic_source:
            topic_source = str(draft or "")

        generic_tokens = {
            "section", "subsection", "outline", "prompt", "chapter", "content",
            "write", "writing", "draft", "article", "paper", "document",
            "要求", "生成", "内容", "小节", "章节", "大纲", "写作", "文档",
            "介绍", "分析", "讨论", "说明", "包括", "以及", "关于",
            "标准", "分钟", "休息", "周期", "如何", "应对", "记录", "内部",
            "外部", "模板", "状态", "次数", "任务", "步骤", "流程", "示例",
            "当前", "需要", "覆盖", "必须", "本轮", "下一版", "写作", "要求",
        }
        zh_en_expansions = {
            "时间": ["time"],
            "管理": ["management"],
            "大学": ["college", "university"],
            "新生": ["freshman", "freshmen", "student"],
            "学生": ["student", "students"],
            "学习": ["learning", "study", "academic"],
            "习惯": ["habit", "habits"],
            "自我": ["self", "self-regulated"],
            "调节": ["regulated", "regulation"],
            "动机": ["motivation"],
            "博弈": ["game", "games"],
            "策略": ["strategy", "strategic"],
            "均衡": ["equilibrium"],
            "存在": ["existentialism", "existence"],
            "主义": ["philosophy"],
            "人工智能": ["artificial", "intelligence", "ai"],
            "机器学习": ["machine", "learning"],
            "深度学习": ["deep", "learning"],
            "谈判": ["negotiation", "bargaining"],
            "商业": ["business", "commercial"],
            "商务": ["business"],
            "供应链": ["supply", "chain"],
        }
        terms: List[str] = []
        for token in self._content_tokens(topic_source):
            token = token.strip().lower()
            if not token or token in generic_tokens or token.isdigit():
                continue
            if token not in terms:
                terms.append(token)
            for zh, expansions in zh_en_expansions.items():
                if zh in token or zh in topic_source:
                    for expanded in expansions:
                        if expanded not in terms:
                            terms.append(expanded)
            if len(terms) >= 36:
                break
        return terms

    def _source_topic_terms(self, source_results: List[Dict[str, Any]], max_terms: int = 24) -> List[str]:
        """Extract topic-bearing terms from retrieved source titles/snippets."""
        terms: List[str] = []
        for item in source_results or []:
            source_text = " ".join([
                str((item or {}).get("title") or ""),
                str((item or {}).get("body") or ""),
                str((item or {}).get("description") or ""),
                str((item or {}).get("summary") or ""),
                str((item or {}).get("abstract") or ""),
            ])
            for token in self._content_tokens(source_text):
                if token not in terms:
                    terms.append(token)
                if len(terms) >= max_terms:
                    return terms
        return terms

    def _coverage_aspects(self) -> Dict[str, List[str]]:
        return {
            "definition_scope": ["定义", "概念", "边界", "scope", "definition", "concept"],
            "mechanism_method": ["机制", "方法", "模型", "算法", "框架", "method", "model", "mechanism", "framework"],
            "application_case": ["应用", "场景", "案例", "实践", "application", "case", "practice", "deployment"],
            "evaluation_metric": ["评估", "指标", "基准", "实验", "metric", "benchmark", "evaluation", "experiment"],
            "risk_limitation": ["风险", "局限", "限制", "挑战", "risk", "limitation", "challenge", "failure"],
            "future_direction": ["未来", "趋势", "方向", "开放问题", "future", "direction", "open question"],
        }

    def _coverage_diagnostics(self, draft: str, outline: str, context_text: str = "") -> Dict[str, Any]:
        draft_lower = str(draft or "").lower()
        target_terms = self._topic_terms(outline=outline, context_text=context_text, draft=draft)
        target_terms = [term for term in target_terms if len(str(term).strip()) >= 2][:24]
        matched_terms = [term for term in target_terms if str(term).lower() in draft_lower]
        missing_terms = [term for term in target_terms if term not in matched_terms]
        target_term_coverage = len(matched_terms) / max(1, len(target_terms)) if target_terms else 0.0

        aspect_hits: Dict[str, bool] = {}
        for aspect, markers in self._coverage_aspects().items():
            aspect_hits[aspect] = any(marker.lower() in draft_lower for marker in markers)
        aspect_coverage = sum(1 for ok in aspect_hits.values() if ok) / max(1, len(aspect_hits))

        # Domain specificity: reward named methods/entities and citation-bearing analytic sentences.
        named_terms = re.findall(r"[A-Z][A-Za-z0-9+._-]{2,}|[\u4e00-\u9fff]{3,12}", draft or "")
        generic_noise = {"本文", "因此", "然而", "此外", "同时", "可以", "需要", "通过", "分析", "研究", "方法"}
        specific_count = sum(1 for item in named_terms if item not in generic_noise)
        citation_sentences = len(re.findall(r"[^。.!?\n]{12,}\[\d+\]", draft or ""))
        specificity_score = self._clip01(0.55 * min(1.0, specific_count / 28.0) + 0.45 * min(1.0, citation_sentences / 4.0))

        return {
            "target_terms": target_terms,
            "matched_terms": matched_terms[:18],
            "missing_terms": missing_terms[:12],
            "target_term_coverage": round(float(target_term_coverage), 4),
            "aspect_hits": aspect_hits,
            "missing_aspects": [k for k, ok in aspect_hits.items() if not ok],
            "aspect_coverage": round(float(aspect_coverage), 4),
            "specificity_score": round(float(specificity_score), 4),
            "source_topic_coverage": 0.0,
        }

    def _evidence_diagnostics(
        self,
        draft: str,
        outline: str,
        context_text: str,
        source_results: List[Dict[str, Any]],
        source_check: Dict[str, Any],
    ) -> Dict[str, Any]:
        text = str(draft or "")
        refs = [int(x) for x in re.findall(r"\[(\d+)\]", text)]
        unique_refs = sorted(set(refs))
        source_count = len(source_results or [])
        source_usage_coverage = len([r for r in unique_refs if 1 <= r <= source_count]) / max(1, min(source_count, 4))

        evidence_type_markers = {
            "method_or_model": ["方法", "模型", "算法", "framework", "method", "model"],
            "empirical_or_benchmark": ["实验", "数据", "基准", "评估", "benchmark", "experiment", "empirical", "metric"],
            "application_or_case": ["案例", "应用", "场景", "case", "application", "deployment"],
            "limitation_or_risk": ["局限", "风险", "限制", "challenge", "risk", "limitation"],
        }
        lower = text.lower()
        evidence_type_hits = {
            key: any(marker.lower() in lower for marker in markers)
            for key, markers in evidence_type_markers.items()
        }
        evidence_type_coverage = sum(1 for ok in evidence_type_hits.values() if ok) / max(1, len(evidence_type_hits))

        claim_sentences = [
            s.strip()
            for s in re.split(r"[。.!?\n]", text)
            if len(s.strip()) >= 18 and re.search(r"说明|表明|意味着|导致|支持|显示|indicate|suggest|show|support", s, flags=re.I)
        ]
        cited_claims = [s for s in claim_sentences if re.search(r"\[\d+\]", s)]
        claim_evidence_alignment = len(cited_claims) / max(1, min(5, len(claim_sentences))) if claim_sentences else (0.6 if unique_refs else 0.0)

        source_terms = self._source_topic_terms(source_results, max_terms=24)
        draft_lower = lower
        source_term_hits = [term for term in source_terms if term and term.lower() in draft_lower]
        source_topic_coverage = len(source_term_hits) / max(1, min(12, len(source_terms))) if source_terms else 0.0

        source_failures: List[str] = []
        reason = str(source_check.get("reason") or "")
        if reason and reason != "ok":
            source_failures.append(reason)
        if source_check.get("low_semantic_urls"):
            source_failures.append("low_semantic_source_quality")
        if source_check.get("blacklist_matches"):
            source_failures.append("blacklist_or_domain_mismatch")

        return {
            "unique_reference_ids": unique_refs,
            "source_usage_coverage": round(float(self._clip01(source_usage_coverage)), 4),
            "evidence_type_hits": evidence_type_hits,
            "missing_evidence_types": [k for k, ok in evidence_type_hits.items() if not ok],
            "evidence_type_coverage": round(float(evidence_type_coverage), 4),
            "claim_sentence_count": len(claim_sentences),
            "cited_claim_sentence_count": len(cited_claims),
            "claim_evidence_alignment": round(float(self._clip01(claim_evidence_alignment)), 4),
            "source_topic_terms": source_terms[:12],
            "source_topic_hits": source_term_hits[:12],
            "source_topic_coverage": round(float(self._clip01(source_topic_coverage)), 4),
            "source_failures": sorted(set(source_failures)),
        }

    def _source_relevance_score(
        self,
        source_item: Dict[str, Any],
        outline_tokens: set,
        topic_term_set: set,
    ) -> float:
        """Score a source against both narrow outline terms and broader topic anchors."""
        source_text = " ".join([
            str(source_item.get("title", "") or ""),
            str(source_item.get("body", "") or ""),
            str(source_item.get("description", "") or ""),
            str(source_item.get("summary", "") or ""),
            str(source_item.get("abstract", "") or ""),
        ])
        source_tokens = set(self._content_tokens(source_text))
        semantic_score = float(source_item.get("semantic_score", 0.0) or 0.0)
        outline_score = (
            len(outline_tokens & source_tokens) / max(1, len(outline_tokens))
            if outline_tokens and source_tokens
            else 0.0
        )
        topic_score = (
            len(topic_term_set & source_tokens) / max(1, len(topic_term_set))
            if topic_term_set and source_tokens
            else 0.0
        )
        href = str(source_item.get("href") or source_item.get("url") or "").lower()
        scholarly_bonus = 0.0
        if "doi.org/" in href or re.search(r"\b10\.\d{4,9}/", href):
            scholarly_bonus = 0.08
        return float(round(max(outline_score, topic_score, semantic_score, scholarly_bonus), 4))

    def _configured_blacklist_entries(self) -> List[Dict[str, str]]:
        entries: List[Dict[str, str]] = []
        if not isinstance(self.reference_blacklist, dict):
            return entries

        raw_items = self.reference_blacklist.get("terms") or self.reference_blacklist.get("items") or []
        if isinstance(raw_items, list):
            for item in raw_items:
                if isinstance(item, str):
                    entries.append({"keyword": item, "type": "configured"})
                elif isinstance(item, dict):
                    keyword = str(item.get("keyword") or item.get("term") or "").strip()
                    if keyword:
                        entries.append({
                            "keyword": keyword,
                            "type": str(item.get("type") or "configured"),
                        })
        return entries

    def _referenced_source_items(
        self,
        refs: List[int],
        matched_urls: List[str],
        source_results: List[Dict[str, Any]],
    ) -> List[tuple[int, Dict[str, Any]]]:
        """Return source candidates that are actually cited by [n] markers or explicit URLs."""
        selected: Dict[int, Dict[str, Any]] = {}
        for ref in refs:
            idx = int(ref) - 1
            if 0 <= idx < len(source_results):
                selected[idx] = source_results[idx]

        matched_url_set = set(matched_urls or [])
        if matched_url_set:
            for idx, item in enumerate(source_results):
                href = str((item or {}).get("href") or "").strip()
                if href in matched_url_set:
                    selected[idx] = item

        return [(idx + 1, item) for idx, item in sorted(selected.items()) if isinstance(item, dict)]

    def check_sources(
        self,
        draft: str,
        outline: str = "",
        context_text: str = "",
        source_results: Optional[List[Dict[str, Any]]] = None,
        require_source_citations: bool = False,
        min_source_citations: int = 1,
        min_semantic_source_score: float = 0.35,
    ) -> Dict[str, Any]:
        source_results = source_results or []
        refs_from_cn = {int(item) for item in re.findall(r"\[来源(\d+)\]", draft or "")}
        refs_from_ieee = {int(item) for item in re.findall(r"\[(\d+)\]", draft or "")}
        refs = sorted(refs_from_cn | refs_from_ieee)
        url_pattern = re.compile(r"https?://[^\s\]）)>,;]+", flags=re.IGNORECASE)
        found_urls = sorted(set(url_pattern.findall(draft or "")))

        source_urls = {
            str((item or {}).get("href") or "").strip()
            for item in source_results
            if str((item or {}).get("href") or "").strip()
        }
        matched_urls = [url for url in found_urls if url in source_urls]
        invalid_urls = [url for url in found_urls if url not in source_urls]

        topic_terms = self._topic_terms(outline=outline, context_text=context_text, draft=draft)
        topic_term_set = set(topic_terms)
        outline_tokens = set(self._content_tokens(outline or "")) | topic_term_set
        matched_semantic_scores: Dict[str, float] = {}
        for url in matched_urls:
            source_item = next(
                (item for item in source_results if str((item or {}).get("href") or "").strip() == url),
                {},
            )
            score = self._source_relevance_score(source_item, outline_tokens, topic_term_set)
            matched_semantic_scores[url] = float(round(score, 4))

        # ========== 黑名单扫描：检测明显的跨学科/无关引用 ==========
        blacklist_matches: List[Dict[str, Any]] = []
        referenced_sources = self._referenced_source_items(
            refs=refs,
            matched_urls=matched_urls,
            source_results=source_results,
        )

        for idx, item in referenced_sources:
            ref_key = f"ref:{idx}"
            if ref_key not in matched_semantic_scores:
                matched_semantic_scores[ref_key] = self._source_relevance_score(
                    item,
                    outline_tokens,
                    topic_term_set,
                )

        low_semantic_urls = [
            key for key, score in matched_semantic_scores.items()
            if score < float(min_semantic_source_score) and not str(key).startswith("ref:")
        ]

        configured_blacklist = self._configured_blacklist_entries()
        domain_mismatch_threshold = float(os.getenv("REFERENCE_DOMAIN_MISMATCH_THRESHOLD", "0.08"))
        min_topic_terms_for_mismatch = max(2, int(os.getenv("REFERENCE_DOMAIN_MIN_TOPIC_TERMS", "3")))

        for idx, item in referenced_sources:
            title = str(item.get("title", "") or "")
            body = str(item.get("body", "") or item.get("description", "") or item.get("summary", "") or "")
            text = f"{title}\n{body}"
            text_lower = text.lower()

            for entry in configured_blacklist:
                keyword = str(entry.get("keyword", "")).lower()
                if keyword and keyword in text_lower:
                    blacklist_matches.append({
                        "index": idx,
                        "href": item.get("href", ""),
                        "title": item.get("title", ""),
                        "match": entry.get("keyword", ""),
                        "type": entry.get("type", "configured"),
                    })
                    break

            if len(topic_term_set) < min_topic_terms_for_mismatch:
                continue

            domain_score = self._source_relevance_score(item, outline_tokens, topic_term_set)
            href = str(item.get("href", "") or "").lower()
            source_name = str(item.get("source", "") or "").lower()
            semantic_source_score = self._safe_float(item.get("semantic_score"), 0.0)
            topic_source_score = self._safe_float(item.get("topic_alignment_score"), 0.0)
            trusted_technical_source = (
                bool(item.get("curated_seed"))
                or semantic_source_score >= 0.18
                or topic_source_score >= 0.18
                or any(
                    domain in href or domain in source_name
                    for domain in (
                        "arxiv.org", "acm.org", "ieee.org", "aclweb.org",
                        "openreview.net", "proceedings.neurips.cc", "proceedings.mlr.press",
                        "doi.org",
                    )
                )
            )
            if trusted_technical_source:
                continue
            if domain_score < domain_mismatch_threshold:
                blacklist_matches.append({
                    "index": idx,
                    "href": item.get("href", ""),
                    "title": item.get("title", ""),
                    "match": "low_topic_source_overlap",
                    "type": "domain_mismatch",
                    "domain_score": round(domain_score, 4),
                    "threshold": round(domain_mismatch_threshold, 4),
                    "topic_terms": topic_terms[:12],
                })

        total_available_sources = len(source_results)
        invalid_refs = [ref for ref in refs if ref < 1 or ref > total_available_sources]
        required_count = max(1, int(min_source_citations))
        citation_count = max(len(refs), len(matched_urls))

        if require_source_citations:
            citation_count_ok = citation_count >= required_count
        else:
            citation_count_ok = True

        if require_source_citations and total_available_sources == 0:
            passed = False
            reason = "source_results_empty"
        elif len(invalid_refs) > 0:
            passed = False
            reason = "invalid_source_reference"
        elif len(invalid_urls) > 0:
            passed = False
            reason = "invalid_source_url"
        elif len(low_semantic_urls) > 0:
            passed = False
            reason = "low_semantic_source_quality"
        # 如果发现黑名单匹配，则判定为失败并标注
        elif blacklist_matches:
            passed = False
            reason = "blacklist_detected"
        elif citation_count_ok:
            passed = True
            reason = "ok"
        else:
            passed = False
            reason = "insufficient_citations"

        return {
            "passed": passed,
            "reason": reason,
            "reference_count": citation_count,
            "references": refs,
            "invalid_references": invalid_refs,
            "found_urls": found_urls,
            "matched_urls": matched_urls,
            "invalid_urls": invalid_urls,
            "matched_semantic_scores": matched_semantic_scores,
            "low_semantic_urls": low_semantic_urls,
            "min_semantic_source_score": float(min_semantic_source_score),
            "total_available_sources": total_available_sources,
            "require_source_citations": require_source_citations,
            "min_source_citations": required_count,
            "blacklist_matches": blacklist_matches,
            "trigger_controller": bool(blacklist_matches),
        }

    def calculate_relevancy(self, draft, outline):
        """
        计算草稿与大纲的相关性。
        算法：实义关键词覆盖率(0.4) + ROUGE-L F1(0.4) + BM25(0.2)
        - 关键词覆盖率：大纲中的实义词有多少出现在草稿中
        - ROUGE-L：最长公共子序列匹配，捕捉词序和短语一致性
        - BM25：用大纲自比得分作上限归一化，衡量草稿对大纲的词频相关程度
        """
        relevance_outline = self._content_outline_for_relevance(outline)
        outline_tokens = self._content_tokens(relevance_outline)
        draft_tokens = self._content_tokens(draft)

        # 1. 关键词覆盖率（实义词）
        if outline_tokens:
            outline_kw = set(outline_tokens)
            draft_kw = set(draft_tokens)
            keyword_coverage = len(outline_kw & draft_kw) / len(outline_kw)
        else:
            keyword_coverage = 0.0

        # 2. ROUGE-L Recall：衡量大纲内容被草稿覆盖了多少。
        # 用 recall 而非 F1：outline 通常远短于 draft，F1 会因 precision 被长文本压缩至趋近0，造成假阴性
        # recall = LCS / len(outline_tokens) 不受 draft 长度影响
        try:
            rouge_l = self.scorer.score(relevance_outline, draft)['rougeL'].recall
        except Exception:
            rouge_l = 0.0

        # 3. BM25（以大纲自比得分为上限归一化，修复原来用 draft 自比导致分母过大的 bug）
        try:
            if outline_tokens and draft_tokens:
                bm25 = BM25Okapi([outline_tokens])
                raw_score = bm25.get_scores(draft_tokens)[0]
                # 大纲自比得分作为满分参考上限
                self_ref = bm25.get_scores(outline_tokens)[0]
                bm25_score = float(min(raw_score / (self_ref + 1e-9), 1.0))
                bm25_score = max(bm25_score, 0.0)
            else:
                bm25_score = 0.0
        except Exception:
            bm25_score = 0.0

        anchor_coverage = self._topic_anchor_coverage(draft=draft, outline=relevance_outline)
        lexical_score = (keyword_coverage * 0.4) + (rouge_l * 0.4) + (bm25_score * 0.2)
        anchor_score = (anchor_coverage * 0.72) + (keyword_coverage * 0.18) + (bm25_score * 0.10)
        relevancy_score = max(lexical_score, anchor_score)

        return {
            "score": float(round(relevancy_score, 4)),
            "details": {
                "keyword_coverage": float(round(keyword_coverage, 4)),
                "anchor_coverage": float(round(anchor_coverage, 4)),
                "rouge_l": float(round(rouge_l, 4)),
                "bm25_score": float(round(bm25_score, 4)),
            },
        }

    def calculate_redundancy(self, draft, history_list):
        """
        检测草稿与已生成历史的冗余度。
        算法：对每条历史分别计算，取最大值（修复原来全部拼接导致越来越长、停用词污染的 bug）。
        每条历史得分 = 实义词 token 重叠(0.5) + 实义词 bigram 重叠(0.3) + ROUGE-L(0.2)
        - 使用实义词过滤停用词，避免 the/of/and 等词拉高误报
        - 逐条取最大值而非平均：只要有一条历史高度相似就应报警
        """
        if not history_list:
            return {"score": 0.0, "details": "No history yet"}

        draft_tokens = self._content_tokens(draft)
        draft_tokens_set = set(draft_tokens)
        draft_bigrams = set(zip(draft_tokens, draft_tokens[1:]))

        max_redundancy = 0.0
        per_entry_scores = []

        for hist in history_list:
            hist_tokens = self._content_tokens(hist)
            hist_tokens_set = set(hist_tokens)
            hist_bigrams = set(zip(hist_tokens, hist_tokens[1:]))

            # 实义词 unigram 重叠率
            if draft_tokens_set:
                token_overlap = len(draft_tokens_set & hist_tokens_set) / len(draft_tokens_set)
            else:
                token_overlap = 0.0

            # 实义词 bigram 重叠率（捕捉短语级重复）
            if draft_bigrams:
                bigram_overlap = len(draft_bigrams & hist_bigrams) / len(draft_bigrams)
            else:
                bigram_overlap = 0.0

            # ROUGE-L Recall：衡量历史内容被草稿重复了多少
            # recall = LCS / len(hist_tokens)：hist 是 reference，表示历史中有多少内容被 draft 再现
            try:
                rouge_l = self.scorer.score(hist, draft)['rougeL'].recall
            except Exception:
                rouge_l = 0.0

            entry_score = (token_overlap * 0.5) + (bigram_overlap * 0.3) + (rouge_l * 0.2)
            per_entry_scores.append(float(round(entry_score, 4)))
            if entry_score > max_redundancy:
                max_redundancy = entry_score

        return {
            "score": float(round(max_redundancy, 4)),
            "details": {
                "per_history_scores": per_entry_scores,
                "max_redundancy": float(round(max_redundancy, 4)),
            },
        }

    def verify(
        self,
        draft,
        outline,
        history_list,
        rel_threshold=0.765,
        red_threshold=0.265,
        context_text: str = "",
        source_results: Optional[List[Dict[str, Any]]] = None,
        require_source_citations: bool = False,
        min_source_citations: int = 1,
    ):
        """一键验证逻辑"""
        rel = self.calculate_relevancy(draft, outline)
        red = self.calculate_redundancy(draft, history_list)
        persona_prompt = os.getenv("PERSONA_PROMPT", "").strip()
        persona_result: Optional[Dict[str, Any]] = None
        persona_ok = True
        if persona_prompt:
            persona_result = self._calculate_persona_alignment(draft=draft, persona_prompt=persona_prompt)
            persona_ok = self._safe_float(persona_result.get("similarity"), 0.0) >= self.persona_sim_threshold
        source_check = self.check_sources(
            draft=draft,
            outline=outline,
            context_text=context_text,
            source_results=source_results,
            require_source_citations=require_source_citations,
            min_source_citations=min_source_citations,
            min_semantic_source_score=float(os.getenv("MIN_SEMANTIC_SOURCE_SCORE", "0.35")),
        )
        coverage_diagnostics = self._coverage_diagnostics(
            draft=draft,
            outline=outline,
            context_text=context_text,
        )
        evidence_diagnostics = self._evidence_diagnostics(
            draft=draft,
            outline=outline,
            context_text=context_text,
            source_results=source_results or [],
            source_check=source_check,
        )
        coverage_diagnostics["source_topic_coverage"] = evidence_diagnostics.get("source_topic_coverage", 0.0)

        heuristic_dimensions = self._compute_semantic_dimensions(
            draft=draft,
            outline=outline,
            history_list=history_list,
            rel=rel,
            red=red,
            source_check=source_check,
            context_text=context_text,
            source_results=source_results or [],
        )
        
        require_multidim_env = os.getenv("REQUIRE_MULTIDIM_QUALITY", "true").lower() == "true"
        unieval_strict_required = os.getenv("UNIEVAL_STRICT_REQUIRED", "false").lower() == "true"
        unieval_endpoint = os.getenv("UNIEVAL_ENDPOINT", "").strip()
        # If UniEval endpoint is absent, degrade to heuristic-only quality checks
        # instead of failing every verify request with HTTP 500.
        require_multidim = require_multidim_env
        
        # 尝试调用 UniEval，如果启用了多维检查且 UniEval 必须可用
        unieval_dimensions = self._try_unieval_dimensions(
            draft=draft,
            outline=outline,
            history_list=history_list,
            require_available=require_multidim and unieval_strict_required,
        )
        
        fusion = self._fuse_dimensions_with_uncertainty(
            heuristic_dims=heuristic_dimensions,
            unieval_dims=unieval_dimensions,
        )
        semantic_dimensions = fusion["fused"]
        unieval_available = bool(unieval_dimensions)
        
        # 用于兼容旧的总分逻辑，但主要用维度级阈值
        quality_score = self._composite_quality_score(semantic_dimensions)
        # 略微提高总体质量分数阈值，避免弱质量内容过早判定通过
        quality_threshold = self._safe_float(os.getenv("QUALITY_SCORE_THRESHOLD", "0.620"), 0.620)
        
        # 维度级阈值检查（新的主要判定逻辑）
        dimension_check = self._check_dimension_thresholds(semantic_dimensions)
        quality_soft_pass = self._quality_soft_pass(
            quality_score=quality_score,
            quality_threshold=quality_threshold,
            dimension_check=dimension_check,
        )
        quality_passed = dimension_check["all_passed"] or bool(quality_soft_pass.get("passed", False))

        is_passed = (
            (rel['score'] >= rel_threshold)
            and (red['score'] <= red_threshold)
            and persona_ok
            and source_check["passed"]
            and (quality_score >= quality_threshold)
            and (quality_passed if require_multidim else True)
        )

        if not is_passed:
            print(
                f"[Verifier] fail summary: rel={rel['score']:.3f}/{rel_threshold}, "
                f"red={red['score']:.3f}/{red_threshold}, "
                f"quality_score={quality_score:.3f}/{quality_threshold}, "
                f"unieval_available={unieval_available}, "
                f"failed_dimensions={dimension_check['failed_dimensions']}"
            )

        advice = "Content looks good."
        if rel['score'] < rel_threshold:
            advice = "Content is deviating from the outline. Add more focus on the section mission."
        if red['score'] > red_threshold:
            advice = "Content is redundant with previous sections. Provide new information."
        if not source_check["passed"]:
            advice = "Source citation check failed. Use semantically relevant, verifiable source URLs for the current subsection outline."
        if persona_prompt and not persona_ok:
            advice = "Persona consistency check failed. Align tone, terminology and narrative style with PERSONA_PROMPT."
        if require_multidim and not quality_passed:
            failed_dims = ", ".join(dimension_check["failed_dimensions"])
            advice = f"Multi-dimensional semantic quality failed on: {failed_dims}. Improve these dimensions."
        if "coverage_completeness" in dimension_check["failed_dimensions"]:
            missing_terms = ", ".join(coverage_diagnostics.get("missing_terms", [])[:6])
            missing_aspects = ", ".join(coverage_diagnostics.get("missing_aspects", [])[:4])
            advice = (
                "Coverage check failed. Expand the subsection with missing topic-specific content"
                + (f" terms: {missing_terms}." if missing_terms else ".")
                + (f" Missing aspects: {missing_aspects}." if missing_aspects else "")
            )
        if "evidence_grounding" in dimension_check["failed_dimensions"] or not source_check["passed"]:
            missing_evidence = ", ".join(evidence_diagnostics.get("missing_evidence_types", [])[:4])
            advice = (
                "Evidence grounding check failed. Bind key claims to retrieved or verifiable sources"
                + (f" and add missing evidence types: {missing_evidence}." if missing_evidence else ".")
            )

        return {
            "is_passed": is_passed,
            "relevancy_index": rel['score'],
            "redundancy_index": red['score'],
            # 总分（保留兼容性）
            "quality_score": quality_score,
            "quality_threshold": quality_threshold,
            "quality_score_passed": quality_score >= quality_threshold,
            # 维度级检查（新主逻辑）
            "quality_dimensions": semantic_dimensions,
            "quality_dimensions_passed": quality_passed,
            "quality_dimensions_hard_passed": dimension_check["all_passed"],
            "quality_soft_pass": quality_soft_pass,
            "quality_dimensions_check": dimension_check["per_dimension"],
            "quality_dimensions_failed": dimension_check["failed_dimensions"],
            "dimension_thresholds": self._quality_dimension_thresholds(),
            # 不确定性信息
            "quality_dimensions_uncertainty": fusion["uncertainty"],
            "quality_dimensions_confidence_interval": fusion["confidence_interval"],
            "quality_overall_uncertainty": fusion["overall_uncertainty"],
            "unieval_available": unieval_available,
            # 源信息（启发式 vs UniEval）
            "quality_dimensions_source": {
                "heuristic": heuristic_dimensions,
                "unieval": unieval_dimensions,
                "per_dimension": fusion["sources"],
            },
            "quality_weights": self._quality_weights(),
            "feedback": advice,
            "persona_check": persona_result,
            "persona_passed": bool(persona_ok),
            "persona_threshold": float(self.persona_sim_threshold),
            "source_check": source_check,
            "coverage_diagnostics": coverage_diagnostics,
            "evidence_diagnostics": evidence_diagnostics,
            "raw_data": {
                "relevancy": rel['details'],
                "redundancy": red['details'],
                "persona": persona_result,
                "source_check": source_check,
                "coverage_diagnostics": coverage_diagnostics,
                "evidence_diagnostics": evidence_diagnostics,
                "semantic_dimensions": semantic_dimensions,
                "semantic_uncertainty": fusion["uncertainty"],
            }
        }


# ============ FastAPI 应用 ============

# 1. 定义数据格式 (Pydantic 模型)
# 这样 FastAPI 会自动帮你检查收到的数据对不对
class VerifyRequest(BaseModel):
    draft: str                  # 当前生成的草稿
    outline: str                # 对应的大纲/任务要求
    history: List[str] = []     # 之前已经生成的章节内容列表（用于查重）
    document_id: Optional[str] = None  # 如果不传 history，可用 document_id 从数据库读取
    rel_threshold: Optional[float] = 0.765  # 可选：自定义相关性阈值
    red_threshold: Optional[float] = 0.265  # 可选：自定义冗余度阈值
    context_text: str = ""
    source_results: List[Dict[str, Any]] = []
    require_source_citations: bool = False
    min_source_citations: int = 3

# 2. 初始化应用
app = FastAPI(title="FlowerNet Verifying Layer API")

# 全局 verifier 对象（延迟初始化）
_verifier = None
_history_manager = None

def get_verifier():
    """延迟初始化 verifier（首次使用时才创建）"""
    global _verifier
    if _verifier is None:
        print("⏳ 首次初始化 Verifier...")
        _verifier = FlowerNetVerifier()
        print("✅ Verifier 已初始化")
    return _verifier

def get_history_manager():
    """延迟初始化 HistoryManager（用于从数据库读取历史内容）"""
    global _history_manager
    if _history_manager is None:
        use_db = os.getenv('USE_DATABASE', 'true').lower() == 'true'
        raw_db_path = os.getenv('DATABASE_PATH', 'flowernet_history.db')
        if os.path.isabs(raw_db_path):
            db_path = raw_db_path
        else:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_path = os.path.join(project_root, raw_db_path)
        _history_manager = HistoryManager(use_database=use_db, db_path=db_path)
    return _history_manager

print("🚀 FlowerNet API 启动（Verifier 将按需初始化）...")

# 3. 定义根目录（用于检查服务是否存活）
@app.get("/")
def read_root():
    return {"status": "online", "message": "FlowerNet Verifying Layer is ready."}


@app.get("/health")
@app.get("/health/live")
def health_check():
    """Lightweight liveness endpoint for Render and upstream probes."""
    return {"status": "ok", "service": "flowernet-verifier"}

# 4. 定义核心验证接口
@app.post("/verify")
async def perform_verification(request: VerifyRequest):
    try:
        # 获取或创建 verifier（延迟初始化）
        verifier = get_verifier()
        history_list = request.history
        if (not history_list) and request.document_id:
            history_manager = get_history_manager()
            history_records = history_manager.get_history(request.document_id)
            history_list = [entry["content"] for entry in history_records]
        # 调用 verifier.py 中的 verify 方法
        result = verifier.verify(
            draft=request.draft,
            outline=request.outline,
            history_list=history_list,
            rel_threshold=request.rel_threshold,
            red_threshold=request.red_threshold,
            context_text=request.context_text,
            source_results=request.source_results,
            require_source_citations=request.require_source_citations,
            min_source_citations=request.min_source_citations,
        )
        return result
    except Exception as e:
        # 如果代码出错，返回 500 错误
        raise HTTPException(status_code=500, detail=str(e))

# 5. 本地直接运行脚本的快捷入口
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
