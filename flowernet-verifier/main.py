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
        print("✅ 验证层就绪")

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
    ) -> Dict[str, float]:
        draft = draft or ""
        outline = outline or ""
        history_list = history_list or []

        keyword_coverage = self._safe_float((rel.get("details") or {}).get("keyword_coverage"), rel.get("score", 0.0))
        rouge_l = self._safe_float((rel.get("details") or {}).get("rouge_l"), rel.get("score", 0.0))
        bm25_score = self._safe_float((rel.get("details") or {}).get("bm25_score"), rel.get("score", 0.0))

        # 1) Topic alignment: 关注是否紧扣小节主题
        topic_alignment = self._clip01(0.6 * keyword_coverage + 0.25 * rouge_l + 0.15 * bm25_score)

        # 2) Novelty: 与历史去重互补，越高越新
        novelty = self._clip01(1.0 - self._safe_float(red.get("score"), 0.0))

        # 3) Coverage completeness: 是否覆盖大纲关键信息
        coverage_completeness = self._clip01(0.7 * keyword_coverage + 0.3 * rouge_l)

        # 4) Logical coherence: 使用句长稳定性 + 连接词密度近似估计
        sentence_units = [s.strip() for s in re.split(r"[。！？!?；;\n]", draft) if s.strip()]
        if sentence_units:
            lengths = [len(s) for s in sentence_units]
            avg_len = sum(lengths) / len(lengths)
            len_penalty = abs(avg_len - 24.0) / 24.0
            connector_count = len(re.findall(r"因此|所以|然而|同时|此外|首先|其次|最后|because|however|therefore|moreover|first|second|finally", draft.lower()))
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
        evidence_grounding = self._clip01(0.65 * avg_semantic_match + 0.35 * citation_signal)

        # 6) Structure clarity: 小节组织形态质量
        line_count = len([ln for ln in draft.splitlines() if ln.strip()])
        heading_count = len(re.findall(r"^#{1,4}\s+", draft, flags=re.MULTILINE))
        bullet_count = len(re.findall(r"^[\-\*\d]+[\.\)]?\s+", draft, flags=re.MULTILINE))
        structure_clarity = self._clip01(min(1.0, line_count / 10.0) * 0.5 + min(1.0, (heading_count + bullet_count) / 4.0) * 0.5)

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
        default_thresholds = {
            "topic_alignment": 0.48,
            "coverage_completeness": 0.46,
            "logical_coherence": 0.45,
            "evidence_grounding": 0.45,
            "novelty": 0.36,
            "structure_clarity": 0.42,
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
                # Two-estimator fusion: mean with estimator disagreement uncertainty.
                mean_val = (float(h) + float(u)) / 2.0
                unc = self._clip01(abs(float(h) - float(u)))
                low = self._clip01(mean_val - 0.5 * unc)
                high = self._clip01(mean_val + 0.5 * unc)
                sources[key] = {"heuristic": round(float(h), 4), "unieval": round(float(u), 4)}
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


    def check_sources(
        self,
        draft: str,
        outline: str = "",
        source_results: Optional[List[Dict[str, Any]]] = None,
        require_source_citations: bool = False,
        min_source_citations: int = 1,
        min_semantic_source_score: float = 0.35,
    ) -> Dict[str, Any]:
        source_results = source_results or []
        refs = sorted({int(item) for item in re.findall(r"\[来源(\d+)\]", draft or "")})
        url_pattern = re.compile(r"https?://[^\s\]）)>,;]+", flags=re.IGNORECASE)
        found_urls = sorted(set(url_pattern.findall(draft or "")))

        source_urls = {
            str((item or {}).get("href") or "").strip()
            for item in source_results
            if str((item or {}).get("href") or "").strip()
        }
        matched_urls = [url for url in found_urls if url in source_urls]
        invalid_urls = [url for url in found_urls if url not in source_urls]

        outline_tokens = set(self._content_tokens(outline or ""))
        matched_semantic_scores: Dict[str, float] = {}
        for url in matched_urls:
            source_item = next(
                (item for item in source_results if str((item or {}).get("href") or "").strip() == url),
                {},
            )
            source_text = f"{source_item.get('title', '')} {source_item.get('body', '')}"
            source_tokens = set(self._content_tokens(source_text))
            if not outline_tokens or not source_tokens:
                score = float(source_item.get("semantic_score", 0.0) or 0.0)
            else:
                score = len(outline_tokens & source_tokens) / max(1, len(outline_tokens))
            matched_semantic_scores[url] = float(round(score, 4))

        low_semantic_urls = [
            url for url, score in matched_semantic_scores.items()
            if score < float(min_semantic_source_score)
        ]

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
        }

    def calculate_relevancy(self, draft, outline):
        """
        计算草稿与大纲的相关性。
        算法：实义关键词覆盖率(0.4) + ROUGE-L F1(0.4) + BM25(0.2)
        - 关键词覆盖率：大纲中的实义词有多少出现在草稿中
        - ROUGE-L：最长公共子序列匹配，捕捉词序和短语一致性
        - BM25：用大纲自比得分作上限归一化，衡量草稿对大纲的词频相关程度
        """
        outline_tokens = self._content_tokens(outline)
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
            rouge_l = self.scorer.score(outline, draft)['rougeL'].recall
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

        relevancy_score = (keyword_coverage * 0.4) + (rouge_l * 0.4) + (bm25_score * 0.2)

        return {
            "score": float(round(relevancy_score, 4)),
            "details": {
                "keyword_coverage": float(round(keyword_coverage, 4)),
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
        rel_threshold=0.4,
        red_threshold=0.6,
        source_results: Optional[List[Dict[str, Any]]] = None,
        require_source_citations: bool = False,
        min_source_citations: int = 1,
    ):
        """一键验证逻辑"""
        rel = self.calculate_relevancy(draft, outline)
        red = self.calculate_redundancy(draft, history_list)
        source_check = self.check_sources(
            draft=draft,
            outline=outline,
            source_results=source_results,
            require_source_citations=require_source_citations,
            min_source_citations=min_source_citations,
            min_semantic_source_score=float(os.getenv("MIN_SEMANTIC_SOURCE_SCORE", "0.35")),
        )

        heuristic_dimensions = self._compute_semantic_dimensions(
            draft=draft,
            outline=outline,
            history_list=history_list,
            rel=rel,
            red=red,
            source_check=source_check,
        )
        
        require_multidim_env = os.getenv("REQUIRE_MULTIDIM_QUALITY", "true").lower() == "true"
        unieval_endpoint = os.getenv("UNIEVAL_ENDPOINT", "").strip()
        # If UniEval endpoint is absent, degrade to heuristic-only quality checks
        # instead of failing every verify request with HTTP 500.
        require_multidim = require_multidim_env and bool(unieval_endpoint)
        
        # 尝试调用 UniEval，如果启用了多维检查且 UniEval 必须可用
        unieval_dimensions = self._try_unieval_dimensions(
            draft=draft,
            outline=outline,
            history_list=history_list,
            require_available=require_multidim,
        )
        
        fusion = self._fuse_dimensions_with_uncertainty(
            heuristic_dims=heuristic_dimensions,
            unieval_dims=unieval_dimensions,
        )
        semantic_dimensions = fusion["fused"]
        unieval_available = bool(unieval_dimensions)
        
        # 用于兼容旧的总分逻辑，但主要用维度级阈值
        quality_score = self._composite_quality_score(semantic_dimensions)
        quality_threshold = self._safe_float(os.getenv("QUALITY_SCORE_THRESHOLD", "0.58"), 0.58)
        
        # 维度级阈值检查（新的主要判定逻辑）
        dimension_check = self._check_dimension_thresholds(semantic_dimensions)
        quality_passed = dimension_check["all_passed"]

        is_passed = (
            (rel['score'] >= rel_threshold)
            and (red['score'] <= red_threshold)
            and source_check["passed"]
            and (quality_passed if require_multidim else True)
        )

        advice = "Content looks good."
        if rel['score'] < rel_threshold:
            advice = "Content is deviating from the outline. Add more focus on the section mission."
        if red['score'] > red_threshold:
            advice = "Content is redundant with previous sections. Provide new information."
        if not source_check["passed"]:
            advice = "Source citation check failed. Use semantically relevant, verifiable source URLs for the current subsection outline."
        if require_multidim and not quality_passed:
            failed_dims = ", ".join(dimension_check["failed_dimensions"])
            advice = f"Multi-dimensional semantic quality failed on: {failed_dims}. Improve these dimensions."

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
            "source_check": source_check,
            "raw_data": {
                "relevancy": rel['details'],
                "redundancy": red['details'],
                "source_check": source_check,
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
    rel_threshold: Optional[float] = 0.55  # 可选：自定义相关性阈值
    red_threshold: Optional[float] = 0.70  # 可选：自定义冗余度阈值
    source_results: List[Dict[str, Any]] = []
    require_source_citations: bool = False
    min_source_citations: int = 1

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

