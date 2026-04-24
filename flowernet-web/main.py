from datetime import datetime
from collections import deque
from io import BytesIO
import os
import re
import threading
import time
import random
from typing import Any, Dict, List, Generator, Optional
from urllib.parse import quote, urlparse
import json
from uuid import uuid4
from datetime import timezone
from email.utils import parsedate_to_datetime

import requests
from docx import Document
from docx.shared import Pt
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field


OUTLINER_URL = os.getenv("OUTLINER_URL", "http://localhost:8003")
GENERATOR_URL = os.getenv("GENERATOR_URL", "http://localhost:8002")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "7200"))
DOWNSTREAM_RETRIES = int(os.getenv("DOWNSTREAM_RETRIES", "1"))
DOWNSTREAM_BACKOFF = float(os.getenv("DOWNSTREAM_BACKOFF", "1.0"))
DOWNSTREAM_MAX_BACKOFF = float(os.getenv("DOWNSTREAM_MAX_BACKOFF", "30.0"))
DOWNSTREAM_JITTER = float(os.getenv("DOWNSTREAM_JITTER", "0.35"))
DOWNSTREAM_OUTLINER_MIN_RETRIES = int(os.getenv("DOWNSTREAM_OUTLINER_MIN_RETRIES", "1"))
DOWNSTREAM_OUTLINER_MIN_DELAY_503 = float(os.getenv("DOWNSTREAM_OUTLINER_MIN_DELAY_503", "8.0"))
DOWNSTREAM_GENERATOR_MIN_RETRIES = int(os.getenv("DOWNSTREAM_GENERATOR_MIN_RETRIES", "1"))
DOWNSTREAM_GENERATOR_MIN_DELAY_429 = float(os.getenv("DOWNSTREAM_GENERATOR_MIN_DELAY_429", "10.0"))
GENERATOR_RESUME_RETRIES = int(os.getenv("GENERATOR_RESUME_RETRIES", "0"))
DOWNSTREAM_OUTLINER_MIN_DELAY_429 = float(os.getenv("DOWNSTREAM_OUTLINER_MIN_DELAY_429", "5.0"))
GENERATOR_RESUME_BACKOFF = float(os.getenv("GENERATOR_RESUME_BACKOFF", "2.0"))
API_AUTH_ENABLED = os.getenv("API_AUTH_ENABLED", "false").lower() == "true"
API_KEY = os.getenv("FLOWERNET_API_KEY", "")
BEARER_TOKEN = os.getenv("FLOWERNET_BEARER_TOKEN", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
WEB_DEFAULT_REL_THRESHOLD = float(os.getenv("WEB_DEFAULT_REL_THRESHOLD", "0.55"))
WEB_DEFAULT_RED_THRESHOLD = float(os.getenv("WEB_DEFAULT_RED_THRESHOLD", "0.70"))
ENABLE_CITATION_QA = os.getenv("ENABLE_CITATION_QA", "true").lower() == "true"
CITATION_MIN_SECTION_HIGH_QUALITY = int(os.getenv("CITATION_MIN_SECTION_HIGH_QUALITY", "1"))
CITATION_LOW_QUALITY_MAX_RATIO = float(os.getenv("CITATION_LOW_QUALITY_MAX_RATIO", "0.5"))
TIMEOUT_ADAPTIVE_ENABLED = os.getenv("TIMEOUT_ADAPTIVE_ENABLED", "true").lower() == "true"
TIMEOUT_MIN_SECONDS = int(os.getenv("TIMEOUT_MIN_SECONDS", "60"))
TIMEOUT_MAX_SECONDS = int(os.getenv("TIMEOUT_MAX_SECONDS", "7200"))
TIMEOUT_SAFETY_FACTOR = float(os.getenv("TIMEOUT_SAFETY_FACTOR", "1.35"))
TIMEOUT_FIXED_BUFFER_SECONDS = int(os.getenv("TIMEOUT_FIXED_BUFFER_SECONDS", "20"))
TIMEOUT_BASE_OUTLINE_SECONDS = int(os.getenv("TIMEOUT_BASE_OUTLINE_SECONDS", "25"))
TIMEOUT_BASE_CITATION_SECONDS = int(os.getenv("TIMEOUT_BASE_CITATION_SECONDS", "8"))
TIMEOUT_BASE_ITERATION_SECONDS = float(os.getenv("TIMEOUT_BASE_ITERATION_SECONDS", "55"))
ESTIMATED_ITERATIONS_PER_SUBSECTION = float(os.getenv("ESTIMATED_ITERATIONS_PER_SUBSECTION", "1.8"))

DOWNSTREAM_SESSION = requests.Session()
DOWNSTREAM_SESSION.trust_env = False

POFFICES_TASKS: Dict[str, Dict[str, Any]] = {}
POFFICES_TASKS_LOCK = threading.Lock()
RECENT_PIPELINE_SECONDS = deque(maxlen=20)
RECENT_ITERATION_SECONDS = deque(maxlen=20)
METRICS_LOCK = threading.Lock()


class GenerateDocRequest(BaseModel):
    topic: str = Field(..., min_length=2, description="文档主题")
    chapter_count: int = Field(default=5, ge=1, le=10)
    subsection_count: int = Field(default=3, ge=1, le=8)
    user_background: str = Field(default="普通读者")
    extra_requirements: str = Field(default="")
    rel_threshold: float = Field(default=WEB_DEFAULT_REL_THRESHOLD, ge=0, le=1)
    red_threshold: float = Field(default=WEB_DEFAULT_RED_THRESHOLD, ge=0, le=1)
    timeout_seconds: int = Field(default=7200, ge=60, le=7200, description="同步模式超时秒数")


class DownloadDocxRequest(BaseModel):
    title: str
    content: str


class PofficesGenerateRequest(BaseModel):
    query: str = Field(default="", description="用户输入查询")
    chapter_count: int = Field(default=5, ge=1, le=10)
    subsection_count: int = Field(default=3, ge=1, le=8)
    user_background: str = Field(default="普通读者")
    extra_requirements: str = Field(default="")
    rel_threshold: float = Field(default=WEB_DEFAULT_REL_THRESHOLD, ge=0, le=1)
    red_threshold: float = Field(default=WEB_DEFAULT_RED_THRESHOLD, ge=0, le=1)
    async_mode: bool = Field(default=True, description="true=异步任务，false=同步等待结果")
    timeout_seconds: int = Field(default=7200, ge=60, le=7200, description="同步模式超时秒数")


class PofficesTaskStatusRequest(BaseModel):
    task_id: str


app = FastAPI(title="FlowerNet Web UI", version="1.0.0")


def verify_auth(
    x_api_key: str = Header(default="", alias="X-API-Key"),
    authorization: str = Header(default="", alias="Authorization"),
):
    if not API_AUTH_ENABLED:
        return

    api_ok = bool(API_KEY) and x_api_key == API_KEY
    bearer_ok = bool(BEARER_TOKEN) and authorization == f"Bearer {BEARER_TOKEN}"
    if not (api_ok or bearer_ok):
        raise HTTPException(status_code=401, detail="Unauthorized: invalid API key or bearer token")


def _extract_response_error(response: requests.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            detail = payload.get("detail")
            error = payload.get("error")
            message = payload.get("message")
            if isinstance(detail, list):
                return json.dumps(detail, ensure_ascii=False)
            if detail:
                return str(detail)
            if error:
                return str(error)
            if message:
                return str(message)
        return json.dumps(payload, ensure_ascii=False)[:800]
    except ValueError:
        return response.text[:800]


def _parse_retry_after_seconds(retry_after: str) -> float | None:
    value = (retry_after or "").strip()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        pass
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0.0, (dt - now).total_seconds())
    except Exception:
        return None


def _parse_seconds_value(raw: Any) -> float | None:
    text = str(raw or "").strip().lower()
    if not text:
        return None
    if text.endswith("s"):
        text = text[:-1].strip()
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _estimate_subsection_count(chapter_count: int, subsection_count: int) -> int:
    return max(1, int(chapter_count) * int(subsection_count))


def _quantile(values: List[float], q: float) -> float | None:
    if not values:
        return None
    sorted_vals = sorted(float(v) for v in values)
    if len(sorted_vals) == 1:
        return sorted_vals[0]

    q = max(0.0, min(1.0, float(q)))
    pos = (len(sorted_vals) - 1) * q
    lower = int(pos)
    upper = min(lower + 1, len(sorted_vals) - 1)
    if lower == upper:
        return sorted_vals[lower]
    weight = pos - lower
    return sorted_vals[lower] * (1.0 - weight) + sorted_vals[upper] * weight


def _build_timeout_profile(
    chapter_count: int,
    subsection_count: int,
    requested_timeout: int,
) -> Dict[str, Any]:
    expected_subsections = _estimate_subsection_count(chapter_count, subsection_count)

    with METRICS_LOCK:
        recent_iteration = list(RECENT_ITERATION_SECONDS)
        recent_pipeline = list(RECENT_PIPELINE_SECONDS)

    iteration_p50 = _quantile(recent_iteration, 0.50)
    iteration_p90 = _quantile(recent_iteration, 0.90)
    iteration_p95 = _quantile(recent_iteration, 0.95)
    pipeline_p50 = _quantile(recent_pipeline, 0.50)
    pipeline_p90 = _quantile(recent_pipeline, 0.90)
    pipeline_p95 = _quantile(recent_pipeline, 0.95)

    iteration_seconds_for_budget = (
        float(iteration_p90)
        if iteration_p90 is not None
        else TIMEOUT_BASE_ITERATION_SECONDS
    )

    estimated_iterations = max(1.0, expected_subsections * ESTIMATED_ITERATIONS_PER_SUBSECTION)
    estimated_seconds = (
        TIMEOUT_BASE_OUTLINE_SECONDS
        + TIMEOUT_BASE_CITATION_SECONDS
        + iteration_seconds_for_budget * estimated_iterations
    )
    if pipeline_p95 is not None:
        estimated_seconds = max(estimated_seconds, float(pipeline_p95))

    recommended_timeout = int(estimated_seconds * TIMEOUT_SAFETY_FACTOR + TIMEOUT_FIXED_BUFFER_SECONDS)
    recommended_timeout = max(TIMEOUT_MIN_SECONDS, min(TIMEOUT_MAX_SECONDS, recommended_timeout))

    requested = int(requested_timeout or REQUEST_TIMEOUT)
    requested = max(TIMEOUT_MIN_SECONDS, min(TIMEOUT_MAX_SECONDS, requested))

    effective_timeout = max(requested, recommended_timeout) if TIMEOUT_ADAPTIVE_ENABLED else requested
    effective_timeout = max(TIMEOUT_MIN_SECONDS, min(TIMEOUT_MAX_SECONDS, int(effective_timeout)))

    return {
        "adaptive_enabled": TIMEOUT_ADAPTIVE_ENABLED,
        "quantile_window_tasks": 20,
        "expected_subsections": expected_subsections,
        "estimated_iterations": round(estimated_iterations, 2),
        "iteration_seconds_for_budget": round(float(iteration_seconds_for_budget), 2),
        "iteration_p50_seconds": round(float(iteration_p50), 2) if iteration_p50 is not None else None,
        "iteration_p90_seconds": round(float(iteration_p90), 2) if iteration_p90 is not None else None,
        "iteration_p95_seconds": round(float(iteration_p95), 2) if iteration_p95 is not None else None,
        "pipeline_p50_seconds": round(float(pipeline_p50), 2) if pipeline_p50 is not None else None,
        "pipeline_p90_seconds": round(float(pipeline_p90), 2) if pipeline_p90 is not None else None,
        "pipeline_p95_seconds": round(float(pipeline_p95), 2) if pipeline_p95 is not None else None,
        "requested_timeout_seconds": requested,
        "recommended_timeout_seconds": recommended_timeout,
        "effective_timeout_seconds": effective_timeout,
    }


def _record_timeout_metrics(elapsed_seconds: float, result: Dict[str, Any]) -> None:
    if elapsed_seconds <= 0:
        return

    total_iterations = 0
    generation_time_seconds = None
    if isinstance(result, dict):
        stats = result.get("stats") or {}
        if isinstance(stats, dict):
            total_iterations = int(stats.get("total_iterations", 0) or 0)
            generation_time_seconds = _parse_seconds_value(stats.get("generation_time"))

    if generation_time_seconds is None:
        generation_time_seconds = float(elapsed_seconds)

    with METRICS_LOCK:
        RECENT_PIPELINE_SECONDS.append(float(elapsed_seconds))
        if total_iterations > 0:
            RECENT_ITERATION_SECONDS.append(float(generation_time_seconds) / float(total_iterations))


def _inject_timeout_profile(result: Dict[str, Any], timeout_profile: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result
    stats = result.get("stats")
    if not isinstance(stats, dict):
        stats = {}
        result["stats"] = stats
    stats["timeout_profile"] = timeout_profile
    return result


def _is_transient_downstream_payload(payload: Dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("success") is not False:
        return False
    message = str(payload.get("error") or payload.get("message") or "").lower()
    transient_tokens = [
        "429", "too many requests", "resource_exhausted", "quota", "rate",
        "timeout", "timed out", "temporarily", "503", "502", "504", "retry",
        "已有大纲生成任务正在执行",
    ]
    return any(token in message for token in transient_tokens)


def post_json_with_retry(url: str, payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    last_error: str = ""
    is_outliner_url = "outliner" in url or "/outline/" in url
    is_generator_url = "generator" in url or "/generate_document" in url
    effective_retries = DOWNSTREAM_RETRIES
    if is_outliner_url:
        effective_retries = max(effective_retries, DOWNSTREAM_OUTLINER_MIN_RETRIES)
    if is_generator_url:
        effective_retries = max(effective_retries, DOWNSTREAM_GENERATOR_MIN_RETRIES)

    for attempt in range(1, effective_retries + 1):
        retry_delay = DOWNSTREAM_BACKOFF * (2 ** max(0, attempt - 1))
        retry_delay += random.uniform(0, DOWNSTREAM_JITTER)
        retry_after_seconds = None

        try:
            response = DOWNSTREAM_SESSION.post(url, json=payload, timeout=timeout)
            response.raise_for_status()
            try:
                body = response.json()
            except ValueError:
                content_type = response.headers.get("Content-Type", "")
                response_body = (response.text or "")[:800]
                last_error = (
                    f"HTTP {response.status_code} from {url}: 下游返回非JSON响应 "
                    f"(Content-Type={content_type or 'unknown'}): {response_body or '<empty>'}"
                )
                if attempt < effective_retries:
                    retry_delay = min(retry_delay, DOWNSTREAM_MAX_BACKOFF)
                    time.sleep(retry_delay)
                    continue
                break

            if _is_transient_downstream_payload(body) and attempt < effective_retries:
                last_error = f"下游返回可重试失败: {body}"
                retry_delay = min(retry_delay, DOWNSTREAM_MAX_BACKOFF)
                time.sleep(retry_delay)
                continue

            return body
        except requests.RequestException as exc:
            response = getattr(exc, "response", None)
            if response is not None:
                response_body = _extract_response_error(response)
                last_error = f"HTTP {response.status_code} from {url}: {response_body}"
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After", "")
                    retry_after_seconds = _parse_retry_after_seconds(retry_after)
                    if is_generator_url:
                        retry_delay = max(retry_delay, DOWNSTREAM_GENERATOR_MIN_DELAY_429)
                    if is_outliner_url:
                        retry_delay = max(retry_delay, DOWNSTREAM_OUTLINER_MIN_DELAY_429)
                if is_outliner_url and response.status_code == 503:
                    retry_delay = max(retry_delay, DOWNSTREAM_OUTLINER_MIN_DELAY_503)
            else:
                last_error = str(exc)

            if attempt < effective_retries:
                if retry_after_seconds is not None:
                    retry_delay = max(retry_delay, retry_after_seconds)
                retry_delay = min(retry_delay, DOWNSTREAM_MAX_BACKOFF)
                time.sleep(retry_delay)

    raise HTTPException(
        status_code=502,
        detail=f"下游服务请求失败(重试{effective_retries}次): {url}, 错误: {last_error}",
    )


def post_json(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return post_json_with_retry(url=url, payload=payload, timeout=REQUEST_TIMEOUT)


def fetch_history_items(document_id: str, timeout_seconds: int = 60) -> List[Dict[str, Any]]:
    try:
        history_resp = post_json_with_retry(
            f"{OUTLINER_URL}/history/get",
            {"document_id": document_id},
            timeout_seconds,
        )
        if history_resp.get("success") and isinstance(history_resp.get("history"), list):
            return history_resp.get("history", [])
    except Exception as e:
        print(f"获取历史内容失败: {e}")
    return []


def extract_orchestration_metrics(gen_resp: Dict[str, Any]) -> Dict[str, int]:
    if not isinstance(gen_resp, dict):
        return {
            "controller_triggered_subsections": 0,
            "controller_calls_total": 0,
            "controller_success_total": 0,
            "controller_error_total": 0,
            "controller_unavailable_total": 0,
            "controller_ineffective_total": 0,
            "controller_fallback_outline_total": 0,
            "controller_exhausted_total": 0,
            "verifier_failed_total": 0,
        }

    return {
        "controller_triggered_subsections": int(gen_resp.get("controller_triggered_subsections", 0) or 0),
        "controller_calls_total": int(gen_resp.get("controller_calls_total", 0) or 0),
        "controller_success_total": int(gen_resp.get("controller_success_total", 0) or 0),
        "controller_error_total": int(gen_resp.get("controller_error_total", 0) or 0),
        "controller_unavailable_total": int(gen_resp.get("controller_unavailable_total", 0) or 0),
        "controller_ineffective_total": int(gen_resp.get("controller_ineffective_total", 0) or 0),
        "controller_fallback_outline_total": int(gen_resp.get("controller_fallback_outline_total", 0) or 0),
        "controller_exhausted_total": int(gen_resp.get("controller_exhausted_total", 0) or 0),
        "verifier_failed_total": int(gen_resp.get("verifier_failed_total", 0) or 0),
    }


def extract_document_quality_metrics(gen_resp: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(gen_resp, dict):
        return {
            "quality_score_avg": 0.0,
            "quality_overall_uncertainty_avg": 0.0,
            "quality_dimension_avgs": {},
            "quality_weights": {},
            "unieval_available_subsections": 0,
            "unieval_fallback_subsections": 0,
            "unieval_available_ratio": 0.0,
            "unieval_fallback_ratio": 0.0,
            "bandit_selected_arm_counts": {},
            "bandit_reward_sum": 0.0,
            "bandit_reward_count": 0,
            "bandit_reward_avg": 0.0,
            "bandit_drift_events": 0,
            "bandit_drift_triggered_subsections": 0,
            "bandit_drift_trigger_rate": 0.0,
            "bandit_last_selected_arm": "",
            "bandit_last_selection_mode": "",
            "bandit_last_constraints": {},
        }

    return {
        "quality_score_avg": float(gen_resp.get("quality_score_avg", 0.0) or 0.0),
        "quality_overall_uncertainty_avg": float(gen_resp.get("quality_overall_uncertainty_avg", 0.0) or 0.0),
        "quality_dimension_avgs": gen_resp.get("quality_dimension_avgs", {}) if isinstance(gen_resp.get("quality_dimension_avgs"), dict) else {},
        "quality_weights": gen_resp.get("quality_weights", {}) if isinstance(gen_resp.get("quality_weights"), dict) else {},
        "unieval_available_subsections": int(gen_resp.get("unieval_available_subsections", 0) or 0),
        "unieval_fallback_subsections": int(gen_resp.get("unieval_fallback_subsections", 0) or 0),
        "unieval_available_ratio": float(gen_resp.get("unieval_available_ratio", 0.0) or 0.0),
        "unieval_fallback_ratio": float(gen_resp.get("unieval_fallback_ratio", 0.0) or 0.0),
        "bandit_selected_arm_counts": gen_resp.get("bandit_selected_arm_counts", {}) if isinstance(gen_resp.get("bandit_selected_arm_counts"), dict) else {},
        "bandit_reward_sum": float(gen_resp.get("bandit_reward_sum", 0.0) or 0.0),
        "bandit_reward_count": int(gen_resp.get("bandit_reward_count", 0) or 0),
        "bandit_reward_avg": float(gen_resp.get("bandit_reward_avg", 0.0) or 0.0),
        "bandit_drift_events": int(gen_resp.get("bandit_drift_events", 0) or 0),
        "bandit_drift_triggered_subsections": int(gen_resp.get("bandit_drift_triggered_subsections", 0) or 0),
        "bandit_drift_trigger_rate": float(gen_resp.get("bandit_drift_trigger_rate", 0.0) or 0.0),
        "bandit_last_selected_arm": str(gen_resp.get("bandit_last_selected_arm", "") or ""),
        "bandit_last_selection_mode": str(gen_resp.get("bandit_last_selection_mode", "") or ""),
        "bandit_last_constraints": gen_resp.get("bandit_last_constraints", {}) if isinstance(gen_resp.get("bandit_last_constraints"), dict) else {},
    }


def generate_document_with_recovery(
    document_id: str,
    generate_payload: Dict[str, Any],
    timeout_seconds: int,
) -> Dict[str, Any]:
    total_attempts = 1 + max(0, GENERATOR_RESUME_RETRIES)
    last_error = ""

    for attempt in range(1, total_attempts + 1):
        try:
            # Keep each downstream call bounded so async tasks do not stay running forever.
            call_timeout = max(30, int(timeout_seconds))
            result = post_json_with_retry(
                f"{GENERATOR_URL}/generate_document",
                generate_payload,
                call_timeout,
            )
            if isinstance(result, dict):
                result.setdefault("recovery_attempt", attempt)
                result.setdefault("recovery_attempts", total_attempts)
            return result
        except HTTPException as exc:
            last_error = str(exc.detail)
        except Exception as exc:
            last_error = str(exc)

        if attempt < total_attempts:
            delay = min(
                DOWNSTREAM_MAX_BACKOFF,
                GENERATOR_RESUME_BACKOFF * (2 ** (attempt - 1)) + random.uniform(0, DOWNSTREAM_JITTER),
            )
            print(f"⚠️ generate_document 第{attempt}次失败，{delay:.1f}s 后重试续跑: {last_error[:180]}")
            time.sleep(delay)

    return {
        "success": False,
        "error": f"文档生成在重试续跑后仍失败: {last_error}",
        "interrupted": True,
        "recovery_attempt": total_attempts,
        "recovery_attempts": total_attempts,
    }


def _build_document(req: GenerateDocRequest, timeout_seconds: int) -> Dict[str, Any]:
    start_ts = time.time()

    def _remaining_timeout(min_seconds: int = 30) -> int:
        elapsed = time.time() - start_ts
        remaining = int(timeout_seconds - elapsed)
        return max(min_seconds, remaining)

    document_id = f"web_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    user_requirements = build_requirements_text(req)

    outline_payload = {
        "document_id": document_id,
        "user_background": req.user_background,
        "user_requirements": user_requirements,
        "max_sections": req.chapter_count,
        "max_subsections_per_section": req.subsection_count,
    }
    outline_resp = post_json_with_retry(
        f"{OUTLINER_URL}/outline/generate-and-save",
        outline_payload,
        _remaining_timeout(min_seconds=30),
    )

    if not outline_resp.get("success"):
        raise HTTPException(status_code=500, detail=f"大纲生成失败: {outline_resp}")

    title = outline_resp.get("document_title") or f"{req.topic} 文档"
    structure = outline_resp.get("structure", {})
    content_prompts = outline_resp.get("content_prompts", [])
    if not isinstance(structure, dict) or not isinstance(content_prompts, list):
        raise HTTPException(status_code=500, detail=f"大纲结果格式异常: {outline_resp}")

    structure, content_prompts, source_subsections, normalized_subsections = ensure_exact_structure_and_prompts(
        title=title,
        structure=structure,
        content_prompts=content_prompts,
        chapter_count=req.chapter_count,
        subsection_count=req.subsection_count,
    )

    expected_subsections = req.chapter_count * req.subsection_count
    outlined_subsections = normalized_subsections
    if outlined_subsections <= 0:
        raise HTTPException(status_code=500, detail="大纲生成结果为空，无法开始内容生成")

    generate_payload = {
        "document_id": document_id,
        "title": title,
        "structure": structure,
        "content_prompts": content_prompts,
        "user_background": req.user_background,
        "user_requirements": user_requirements,
        "rel_threshold": req.rel_threshold,
        "red_threshold": req.red_threshold,
    }
    gen_resp = generate_document_with_recovery(
        document_id=document_id,
        generate_payload=generate_payload,
        timeout_seconds=_remaining_timeout(min_seconds=30),
    )

    history_items = fetch_history_items(document_id=document_id, timeout_seconds=60)
    orchestration_metrics = extract_orchestration_metrics(gen_resp if isinstance(gen_resp, dict) else {})
    document_quality_metrics = extract_document_quality_metrics(gen_resp if isinstance(gen_resp, dict) else {})
    if not gen_resp.get("success"):
        if history_items:
            partial_content = build_markdown_document(
                title,
                structure,
                history_items,
                generated_sections=gen_resp.get("sections", []) if isinstance(gen_resp, dict) else [],
            )
            citation_quality = _citation_quality_check(partial_content)
            return {
                "success": True,
                "partial": True,
                "interrupted": True,
                "message": f"生成中断，已返回通过验证的 {len(history_items)} 个小节",
                "document_id": document_id,
                "title": title,
                "content": partial_content,
                "stats": {
                    "expected_subsections": expected_subsections,
                    "outlined_subsections": outlined_subsections,
                    "passed_subsections": len(history_items),
                    "failed_subsections": len(gen_resp.get("failed_subsections", [])) if isinstance(gen_resp, dict) else 0,
                    "forced_subsections": len(gen_resp.get("forced_subsections", [])) if isinstance(gen_resp, dict) else 0,
                    "total_iterations": gen_resp.get("total_iterations", 0) if isinstance(gen_resp, dict) else 0,
                    "generation_time": gen_resp.get("generation_time", "") if isinstance(gen_resp, dict) else "",
                    "citation_quality": citation_quality,
                    **orchestration_metrics,
                    **document_quality_metrics,
                },
            }
        raise HTTPException(status_code=500, detail=f"文档生成失败: {gen_resp}")

    passed = gen_resp.get("passed_subsections", 0)
    failed = len(gen_resp.get("failed_subsections", []))
    forced = len(gen_resp.get("forced_subsections", []))
    if passed < outlined_subsections and history_items:
        markdown_content = build_markdown_document(
            title,
            structure,
            history_items,
            generated_sections=gen_resp.get("sections", []),
        )
        citation_quality = _citation_quality_check(markdown_content)
        return {
            "success": True,
            "partial": True,
            "interrupted": True,
            "message": f"生成中断，已返回通过验证的 {len(history_items)} 个小节",
            "document_id": document_id,
            "title": title,
            "content": markdown_content,
            "stats": {
                "expected_subsections": expected_subsections,
                "outlined_subsections": outlined_subsections,
                "passed_subsections": len(history_items),
                "failed_subsections": failed,
                "forced_subsections": forced,
                "total_iterations": gen_resp.get("total_iterations", 0),
                "generation_time": gen_resp.get("generation_time", ""),
                "citation_quality": citation_quality,
                **orchestration_metrics,
                **document_quality_metrics,
            },
        }
    if passed < outlined_subsections:
        raise HTTPException(
            status_code=500,
            detail=f"文档生成未达到大纲小节数: 通过 {passed}/{outlined_subsections}, 失败 {failed}",
        )

    if not history_items:
        history_items = fetch_history_items(document_id=document_id, timeout_seconds=60)
    markdown_content = build_markdown_document(
        title,
        structure,
        history_items,
        generated_sections=gen_resp.get("sections", []),
    )
    citation_quality = _citation_quality_check(markdown_content)

    if not citation_quality.get("passed", False):
        return {
            "success": True,
            "partial": True,
            "interrupted": True,
            "message": f"文档内容已生成，但引用质量未达标：{citation_quality.get('reason')}",
            "document_id": document_id,
            "title": title,
            "content": markdown_content,
            "stats": {
                "expected_subsections": expected_subsections,
                "outlined_subsections": outlined_subsections,
                "passed_subsections": passed,
                "failed_subsections": failed,
                "forced_subsections": forced,
                "total_iterations": gen_resp.get("total_iterations", 0),
                "generation_time": gen_resp.get("generation_time", ""),
                "citation_quality": citation_quality,
                **orchestration_metrics,
                **document_quality_metrics,
            },
        }

    return {
        "success": True,
        "document_id": document_id,
        "title": title,
        "content": markdown_content,
        "stats": {
            "expected_subsections": expected_subsections,
            "outlined_subsections": outlined_subsections,
            "passed_subsections": passed,
            "failed_subsections": failed,
            "forced_subsections": forced,
            "total_iterations": gen_resp.get("total_iterations", 0),
            "generation_time": gen_resp.get("generation_time", ""),
            "citation_quality": citation_quality,
            **orchestration_metrics,
            **document_quality_metrics,
        },
    }


def _extract_urls(text: str) -> List[str]:
    return re.findall(r"https?://[^\s\]）)>,;]+", text or "", flags=re.IGNORECASE)


def _domain_quality(domain: str) -> float:
    host = (domain or "").lower().strip()
    if not host:
        return 0.0

    high_quality_domains = {
        "nature.com", "science.org", "sciencedirect.com", "springer.com", "ieee.org", "acm.org",
        "arxiv.org", "ncbi.nlm.nih.gov", "who.int", "oecd.org", "un.org", "nist.gov", "nih.gov",
        "gov.cn", "edu.cn", "ruc.edu.cn", "tsinghua.edu.cn", "pku.edu.cn", "cass.cn", "moe.gov.cn",
    }
    low_quality_domains = {
        "baike.baidu.com", "zhidao.baidu.com", "tieba.baidu.com", "jingyan.baidu.com",
        "m.baidu.com", "weibo.com", "t.co", "bit.ly", "tinyurl.com",
    }

    if host in low_quality_domains:
        return 0.15
    if host in high_quality_domains:
        return 1.0
    if host.endswith(".gov") or host.endswith(".edu") or host.endswith(".gov.cn") or host.endswith(".edu.cn"):
        return 0.95
    if "wikipedia.org" in host:
        return 0.55
    if host.endswith(".org"):
        return 0.72
    if host.endswith(".com"):
        return 0.60
    return 0.5


def _citation_quality_check(markdown: str) -> Dict[str, Any]:
    if not ENABLE_CITATION_QA:
        return {"passed": True, "reason": "disabled"}

    subsection_blocks = re.findall(r"^###\s+.*?(?=^###\s+|\Z)", markdown or "", flags=re.MULTILINE | re.DOTALL)
    if not subsection_blocks:
        subsection_blocks = [markdown or ""]

    all_urls = _extract_urls(markdown or "")
    unique_urls = list(dict.fromkeys(all_urls))

    low_quality_count = 0
    section_details: List[Dict[str, Any]] = []
    for block in subsection_blocks:
        urls = list(dict.fromkeys(_extract_urls(block)))
        high_quality_urls = []
        for url in urls:
            domain = (urlparse(url).netloc or "").lower().strip()
            score = _domain_quality(domain)
            if score < 0.35:
                low_quality_count += 1
            if score >= 0.70:
                high_quality_urls.append(url)
        section_details.append({
            "url_count": len(urls),
            "high_quality_url_count": len(high_quality_urls),
        })

    low_quality_ratio = (low_quality_count / max(1, len(unique_urls))) if unique_urls else 1.0
    missing_high_quality_sections = sum(
        1 for sec in section_details if sec["high_quality_url_count"] < CITATION_MIN_SECTION_HIGH_QUALITY
    )

    passed = bool(unique_urls) and missing_high_quality_sections == 0 and low_quality_ratio <= CITATION_LOW_QUALITY_MAX_RATIO
    reason = "ok"
    if not unique_urls:
        reason = "no_citation_urls"
    elif missing_high_quality_sections > 0:
        reason = "section_missing_high_quality_source"
    elif low_quality_ratio > CITATION_LOW_QUALITY_MAX_RATIO:
        reason = "low_quality_domain_ratio_too_high"

    return {
        "passed": passed,
        "reason": reason,
        "total_urls": len(all_urls),
        "unique_urls": len(unique_urls),
        "low_quality_ratio": round(low_quality_ratio, 4),
        "missing_high_quality_sections": missing_high_quality_sections,
        "section_details": section_details,
    }


def _build_download_url(request: Request) -> str:
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL.rstrip('/')}/api/download-docx"
    return str(request.url_for("download_docx")).rstrip("/")


def _build_poffices_result(request: Request, result: Dict[str, Any]) -> Dict[str, Any]:
    download_url = _build_download_url(request)
    return {
        "success": True,
        "task_status": "completed",
        "document_id": result.get("document_id", ""),
        "title": result.get("title", ""),
        "content": result.get("content", ""),
        "stats": result.get("stats", {}),
        "download": {
            "method": "POST",
            "url": download_url,
            "body": {
                "title": result.get("title", ""),
                "content": result.get("content", ""),
            },
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        },
    }


def build_requirements_text(req: GenerateDocRequest) -> str:
    base = (
        f"请帮我生成一篇关于“{req.topic}”的高质量长文档，"
        f"总共需要 {req.chapter_count} 个章节，"
        f"每个章节包含 {req.subsection_count} 个子章节。"
    )
    if req.extra_requirements.strip():
        return f"{base}\n\n附加要求：{req.extra_requirements.strip()}"
    return base


def _build_content_map_from_history(history: List[Dict[str, Any]]) -> Dict[str, str]:
    content_map: Dict[str, str] = {}
    for item in history:
        key = f"{item.get('section_id', '')}::{item.get('subsection_id', '')}"
        content_map[key] = item.get("content", "")
    return content_map


def _build_content_map_from_sections(sections: Optional[List[Dict[str, Any]]]) -> Dict[str, str]:
    content_map: Dict[str, str] = {}
    for section in sections or []:
        section_id = str(section.get("section_id") or section.get("id") or "")
        for subsection in section.get("subsections", []) or []:
            subsection_id = str(subsection.get("subsection_id") or subsection.get("id") or "")
            content = subsection.get("content", "")
            if section_id and subsection_id and content:
                content_map[f"{section_id}::{subsection_id}"] = content
    return content_map


def build_markdown_document(
    title: str,
    structure: Dict[str, Any],
    history: List[Dict[str, Any]],
    generated_sections: Optional[List[Dict[str, Any]]] = None,
) -> str:
    content_map = _build_content_map_from_sections(generated_sections)
    history_map = _build_content_map_from_history(history)
    for key, value in history_map.items():
        content_map.setdefault(key, value)

    def _normalize_label(value: str) -> str:
        text = re.sub(r"^第\d+[章节]", "", str(value or "")).strip()
        text = re.sub(r"^\d+(?:\.\d+)*\s*", "", text).strip()
        return text or str(value or "").strip()

    def _to_roman(num: int) -> str:
        vals = [
            (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
            (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
            (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
        ]
        result = []
        n = max(1, num)
        for v, s in vals:
            while n >= v:
                result.append(s)
                n -= v
        return "".join(result)

    def _to_alpha(num: int) -> str:
        # 1 -> A, 2 -> B, ...
        n = max(1, num)
        chars: List[str] = []
        while n > 0:
            n -= 1
            chars.append(chr(ord("A") + (n % 26)))
            n //= 26
        return "".join(reversed(chars))

    def _build_abstract() -> str:
        section_titles = [
            _normalize_label(section.get("title", ""))
            for section in structure.get("sections", [])
            if str(section.get("title", "")).strip()
        ]
        highlighted = "、".join(section_titles[:3]) if section_titles else "各章节主题"
        return (
            f"This paper presents a structured exposition on \"{title}\". "
            f"It develops the topic through a hierarchical organization of sections and subsections, "
            f"covering key themes such as {highlighted}. "
            "The document follows a formal IEEE-like layout to improve readability, traceability, and scholarly presentation quality."
        )

    def _build_keywords() -> str:
        section_titles = [
            _normalize_label(section.get("title", ""))
            for section in structure.get("sections", [])
            if str(section.get("title", "")).strip()
        ]
        keywords = [_normalize_label(title), "technical writing", "academic style", "structured document"]
        for section_title in section_titles[:3]:
            if section_title and section_title not in keywords:
                keywords.append(section_title)
        return ", ".join(keywords[:6])

    def _anchor_id(section_index: int, subsection_index: Optional[int] = None) -> str:
        if subsection_index is None:
            return f"chapter-{section_index}"
        return f"chapter-{section_index}-{subsection_index}"

    def _clean_subsection_text(text: str) -> str:
        seen_headings: set[tuple[int, str]] = set()
        cleaned_lines: List[str] = []

        for raw_line in text.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                if cleaned_lines and cleaned_lines[-1] != "":
                    cleaned_lines.append("")
                continue

            heading_match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
            if heading_match:
                heading_level = max(len(heading_match.group(1)), 4)
                heading_text = heading_match.group(2).strip()
                heading_key = (heading_level, heading_text)
                if heading_key in seen_headings:
                    continue
                seen_headings.add(heading_key)
                cleaned_lines.append(f"{'#' * heading_level} {heading_text}")
                continue

            cleaned_lines.append(raw_line.rstrip())

        while cleaned_lines and cleaned_lines[-1] == "":
            cleaned_lines.pop()

        return "\n".join(cleaned_lines).strip()

    def _section_link(section_index: int, section_title: str) -> str:
        roman = _to_roman(section_index)
        return f"[{roman}. {section_title}](#{_anchor_id(section_index)})"

    def _subsection_link(section_index: int, subsection_index: int, subsection_title: str) -> str:
        alpha = _to_alpha(subsection_index)
        return f"[{alpha}. {subsection_title}](#{_anchor_id(section_index, subsection_index)})"

    def _append_outline_list(lines: List[str]) -> None:
        lines.append("## Contents")
        lines.append("")
        for section_index, section in enumerate(structure.get("sections", []), 1):
            section_title = section.get("title", f"第{section_index}章")
            lines.append(_section_link(section_index, section_title))
            for subsection_index, subsection in enumerate(section.get("subsections", []), 1):
                subsection_title = subsection.get("title", f"第{subsection_index}节")
                lines.append(f"   - {_subsection_link(section_index, subsection_index, subsection_title)}")
        lines.append("")

    def _append_references_placeholder(lines: List[str]) -> None:
        lines.extend(
            [
                "## References",
                "",
                "[1] A. A. Author, \"Title of article,\" Journal/Conference, vol. x, no. x, pp. xx-xx, Year.",
                "[2] B. B. Author, Book Title, xth ed. City, Country: Publisher, Year.",
                "[3] C. C. Author, \"Title of report or web resource,\" Organization, Year. [Online]. Available: URL",
                "",
            ]
        )

    lines = [
        f"# {title}",
        "",
        "## Abstract",
        "",
        _build_abstract(),
        "",
        "## Index Terms",
        "",
        _build_keywords(),
        "",
        "---",
        "",
    ]
    _append_outline_list(lines)
    lines.extend(["---", ""])
    for section_index, section in enumerate(structure.get("sections", []), 1):
        section_id = section.get("id", "")
        section_title = section.get("title", "未命名章节")
        roman = _to_roman(section_index)
        lines.append(f'<a id="{_anchor_id(section_index)}"></a>')
        lines.append(f"## {roman}. {_normalize_label(section_title).upper()}")
        lines.append("")

        for subsection_index, subsection in enumerate(section.get("subsections", []), 1):
            subsection_id = subsection.get("id", "")
            subsection_title = subsection.get("title", "未命名小节")
            alpha = _to_alpha(subsection_index)
            key = f"{section_id}::{subsection_id}"
            subsection_text = _clean_subsection_text(content_map.get(key, "（该小节未成功生成）"))

            lines.append(f'<a id="{_anchor_id(section_index, subsection_index)}"></a>')
            lines.append(f"### {alpha}. {_normalize_label(subsection_title)}")
            lines.append("")
            lines.append(subsection_text)
            lines.append("")


    _append_references_placeholder(lines)

    return "\n".join(lines).strip()


def _fallback_structure_from_history(title: str, history: List[Dict[str, Any]]) -> Dict[str, Any]:
    sections: Dict[str, Dict[str, Any]] = {}
    for item in history:
        section_id = str(item.get("section_id") or "section")
        subsection_id = str(item.get("subsection_id") or "subsection")
        section = sections.setdefault(
            section_id,
            {
                "id": section_id,
                "title": section_id,
                "subsections": [],
                "_seen": set(),
            },
        )
        if subsection_id not in section["_seen"]:
            section["subsections"].append({
                "id": subsection_id,
                "title": subsection_id,
            })
            section["_seen"].add(subsection_id)

    ordered_sections: List[Dict[str, Any]] = []
    for sec in sections.values():
        sec.pop("_seen", None)
        ordered_sections.append(sec)

    return {
        "title": title,
        "sections": ordered_sections,
    }


def _load_document_structure(document_id: str, history: List[Dict[str, Any]]) -> tuple[str, Dict[str, Any]]:
    title = document_id
    structure: Dict[str, Any] = {}
    try:
        resp = post_json_with_retry(
            f"{OUTLINER_URL}/outline/get",
            {"document_id": document_id, "outline_type": "document"},
            timeout=30,
        )
        raw_outline = str((resp or {}).get("outline") or "")
        first_line = raw_outline.splitlines()[0].strip() if raw_outline else ""
        if first_line.startswith("# "):
            title = first_line[2:].strip() or title

        json_start = raw_outline.find("{")
        if json_start >= 0:
            candidate = raw_outline[json_start:].strip()
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                structure = parsed
                if parsed.get("title"):
                    title = str(parsed.get("title"))
    except Exception:
        pass

    if not isinstance(structure, dict) or not isinstance(structure.get("sections"), list):
        structure = _fallback_structure_from_history(title, history)
    return title, structure


def ensure_exact_structure_and_prompts(
    title: str,
    structure: Dict[str, Any],
    content_prompts: List[Dict[str, Any]],
    chapter_count: int,
    subsection_count: int,
) -> tuple[Dict[str, Any], List[Dict[str, Any]], int, int]:
    """强制将结构和 prompts 对齐到用户要求的精确章节/小节数量。"""
    safe_structure: Dict[str, Any] = dict(structure or {})
    source_sections = safe_structure.get("sections", [])
    if not isinstance(source_sections, list):
        source_sections = []

    source_total = 0
    for src_sec in source_sections:
        if isinstance(src_sec, dict):
            subs = src_sec.get("subsections", [])
            source_total += len(subs) if isinstance(subs, list) else 0

    prompt_map: Dict[str, Dict[str, Any]] = {}
    for cp in content_prompts or []:
        if not isinstance(cp, dict):
            continue
        sec_id = str(cp.get("section_id", "")).strip()
        sub_id = str(cp.get("subsection_id", "")).strip()
        if sec_id and sub_id:
            prompt_map[f"{sec_id}::{sub_id}"] = cp

    normalized_sections: List[Dict[str, Any]] = []
    normalized_prompts: List[Dict[str, Any]] = []

    for sec_idx in range(chapter_count):
        source_section = source_sections[sec_idx] if sec_idx < len(source_sections) and isinstance(source_sections[sec_idx], dict) else {}

        section_id = str(source_section.get("id") or f"sec_{sec_idx + 1}")
        section_title = str(source_section.get("title") or f"第{sec_idx + 1}章")
        section_desc = str(source_section.get("description") or "")

        source_subs = source_section.get("subsections", [])
        if not isinstance(source_subs, list):
            source_subs = []

        normalized_subsections: List[Dict[str, Any]] = []
        for sub_idx in range(subsection_count):
            source_sub = source_subs[sub_idx] if sub_idx < len(source_subs) and isinstance(source_subs[sub_idx], dict) else {}

            subsection_id = str(source_sub.get("id") or f"{section_id}_sub_{sub_idx + 1}")
            subsection_title = str(source_sub.get("title") or f"{section_title} - 第{sub_idx + 1}节")
            subsection_desc = str(source_sub.get("description") or f"围绕主题“{title}”展开：{subsection_title}")

            normalized_subsections.append({
                "id": subsection_id,
                "title": subsection_title,
                "description": subsection_desc,
            })

            prompt_key = f"{section_id}::{subsection_id}"
            source_prompt = prompt_map.get(prompt_key, {})
            content_prompt = str(source_prompt.get("content_prompt") or "").strip()
            if not content_prompt:
                content_prompt = (
                    f"请撰写《{title}》中“{section_title} > {subsection_title}”的小节内容。\n"
                    f"小节说明：{subsection_desc}\n"
                    "要求：内容准确、结构清晰、与前文衔接并避免重复。"
                )

            normalized_prompts.append({
                "section_id": section_id,
                "subsection_id": subsection_id,
                "section_title": section_title,
                "subsection_title": subsection_title,
                "subsection_description": subsection_desc,
                "content_prompt": content_prompt,
            })

        normalized_sections.append({
            "id": section_id,
            "title": section_title,
            "description": section_desc,
            "subsections": normalized_subsections,
        })

    safe_structure["sections"] = normalized_sections
    normalized_total = chapter_count * subsection_count
    return safe_structure, normalized_prompts, source_total, normalized_total


def markdown_to_docx(title: str, content: str) -> BytesIO:
    document = Document()
    document.core_properties.title = title

    normal_style = document.styles["Normal"]
    normal_style.font.name = "Times New Roman"
    normal_style.font.size = Pt(11)
    normal_style.paragraph_format.line_spacing = 1.5
    normal_style.paragraph_format.space_before = Pt(0)
    normal_style.paragraph_format.space_after = Pt(6)

    for heading_name, size in (("Heading 1", 14), ("Heading 2", 12), ("Heading 3", 11)):
        heading_style = document.styles[heading_name]
        heading_style.font.name = "Times New Roman"
        heading_style.font.size = Pt(size)
        heading_style.paragraph_format.space_before = Pt(12)
        heading_style.paragraph_format.space_after = Pt(6)

    def _is_ignored_line(line: str) -> bool:
        stripped = line.strip()
        return not stripped or stripped == "---" or stripped.startswith("<a id=")

    title_added = False

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if _is_ignored_line(line):
            continue

        if line.startswith("# ") and not title_added:
            heading = document.add_heading(line[2:].strip(), level=0)
            heading.alignment = 1
            title_added = True
        elif line.startswith("## Abstract") or line.startswith("## Index Terms") or line.startswith("## References"):
            document.add_heading(line[3:].strip(), level=2)
        elif line.startswith("### "):
            document.add_heading(line[4:].strip(), level=3)
        elif line.startswith("## "):
            document.add_heading(line[3:].strip(), level=2)
        elif re.match(r"^\d+(?:\.\d+)+\s+", line):
            document.add_heading(line, level=3)
        else:
            document.add_paragraph(line)

    stream = BytesIO()
    document.save(stream)
    stream.seek(0)
    return stream


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": "flowernet-web"}


def generate_stream(req: GenerateDocRequest) -> Generator[str, None, None]:
    """流式生成文档，实时推送进度到前端"""
    try:
        timeout_profile = _build_timeout_profile(
            chapter_count=req.chapter_count,
            subsection_count=req.subsection_count,
            requested_timeout=req.timeout_seconds or REQUEST_TIMEOUT,
        )
        stream_timeout = int(timeout_profile.get("effective_timeout_seconds", REQUEST_TIMEOUT))

        # 开始
        msg = json.dumps({'type': 'start', 'message': '开始生成大纲...'})
        yield f"data: {msg}\n\n"
        
        document_id = f"web_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        user_requirements = build_requirements_text(req)

        # 第1步：生成大纲
        outline_payload = {
            "document_id": document_id,
            "user_background": req.user_background,
            "user_requirements": user_requirements,
            "max_sections": req.chapter_count,
            "max_subsections_per_section": req.subsection_count,
        }
        
        outline_resp = None
        outline_error = None

        def build_outline_async():
            nonlocal outline_resp, outline_error
            try:
                outline_resp = post_json_with_retry(
                    f"{OUTLINER_URL}/outline/generate-and-save",
                    outline_payload,
                    stream_timeout,
                )
            except Exception as e:
                outline_error = e

        outline_thread = threading.Thread(target=build_outline_async, daemon=True)
        outline_thread.start()
        outline_deadline = time.time() + stream_timeout
        last_outline_keepalive = time.time()

        while outline_thread.is_alive() and time.time() < outline_deadline:
            if time.time() - last_outline_keepalive > 10:
                heartbeat = json.dumps({'type': 'heartbeat', 'message': '⏳ 正在生成大纲，保持连接中...'})
                yield f"data: {heartbeat}\n\n"
                last_outline_keepalive = time.time()
            time.sleep(0.5)

        outline_thread.join(timeout=1)

        if outline_thread.is_alive():
            msg = json.dumps({'type': 'error', 'message': f'大纲生成超时（>{stream_timeout}s），请稍后重试'})
            yield f"data: {msg}\n\n"
            return

        if outline_error is not None:
            if isinstance(outline_error, HTTPException):
                detail = outline_error.detail if isinstance(outline_error.detail, str) else json.dumps(outline_error.detail, ensure_ascii=False)
                msg = json.dumps({'type': 'error', 'message': f'大纲生成失败: {detail}'})
                yield f"data: {msg}\n\n"
                return
            msg = json.dumps({'type': 'error', 'message': f'大纲生成失败: {str(outline_error)}'})
            yield f"data: {msg}\n\n"
            return

        if not isinstance(outline_resp, dict):
            msg = json.dumps({'type': 'error', 'message': '大纲生成失败: 返回结果格式异常'})
            yield f"data: {msg}\n\n"
            return
        
        # 检查是否是验证错误（422）
        if "detail" in outline_resp and isinstance(outline_resp["detail"], list):
            errors = [e.get("msg", "未知验证错误") for e in outline_resp["detail"]]
            msg = json.dumps({'type': 'error', 'message': f'参数验证失败: {"; ".join(errors)}'})
            yield f"data: {msg}\n\n"
            return
        
        if not outline_resp.get("success"):
            raw_error = str(outline_resp.get("error", "未知错误"))
            if "localhost:11434" in raw_error or "Connection refused" in raw_error:
                user_error = "大纲生成失败：当前服务正在 Render 上运行，但 OLLAMA_URL 指向 localhost:11434（容器内不可用）。请将 OLLAMA_URL 改为可公网访问的 Ollama 地址。"
            else:
                user_error = f"大纲生成失败: {raw_error}"
            msg = json.dumps({'type': 'error', 'message': user_error})
            yield f"data: {msg}\n\n"
            return

        doc_title = outline_resp.get("document_title", req.topic)
        msg = json.dumps({'type': 'outline', 'message': f'✅ 大纲生成完成\n主题: {doc_title}'})
        yield f"data: {msg}\n\n"

        msg = json.dumps({
            'type': 'detail',
            'stage': 'outline_document_ready',
            'message': f'全文大纲已生成并存储到数据库（document_id={document_id}）',
            'metadata': {'document_id': document_id},
        })
        yield f"data: {msg}\n\n"

        title = outline_resp.get("document_title") or f"{req.topic} 文档"
        structure = outline_resp.get("structure", {})
        content_prompts = outline_resp.get("content_prompts", [])
        if not isinstance(structure, dict) or not isinstance(content_prompts, list):
            msg = json.dumps({'type': 'error', 'message': f'大纲结果格式异常: {str(outline_resp)[:200]}'} )
            yield f"data: {msg}\n\n"
            return
        total_subsections = len(content_prompts)
        if total_subsections <= 0:
            msg = json.dumps({'type': 'error', 'message': '大纲结果为空，无法开始内容生成'})
            yield f"data: {msg}\n\n"
            return

        ordered_subsections: List[Dict[str, Any]] = []
        subsection_order = 0
        for section in structure.get("sections", []):
            section_id = str(section.get("id") or "")
            section_title = str(section.get("title") or section_id or "未命名章节")
            msg = json.dumps({
                'type': 'detail',
                'stage': 'section_outline_ready',
                'message': f'章节大纲已生成并存储: {section_title}',
                'metadata': {
                    'section_id': section_id,
                    'section_title': section_title,
                },
            })
            yield f"data: {msg}\n\n"
            for subsection in section.get("subsections", []):
                subsection_id = str(subsection.get("id") or "")
                subsection_title = str(subsection.get("title") or subsection_id or "未命名小节")
                subsection_order += 1
                ordered_subsections.append({
                    "section_id": section_id,
                    "subsection_id": subsection_id,
                    "section_title": section_title,
                    "subsection_title": subsection_title,
                    "subsection_order": subsection_order,
                })
                msg = json.dumps({
                    'type': 'detail',
                    'stage': 'subsection_outline_ready',
                    'message': f'第{subsection_order}小节大纲已生成并存储: {section_title} > {subsection_title}',
                    'metadata': {
                        'section_id': section_id,
                        'subsection_id': subsection_id,
                        'section_title': section_title,
                        'subsection_title': subsection_title,
                        'subsection_order': subsection_order,
                    },
                })
                yield f"data: {msg}\n\n"

        emitted_start_indices = set()
        emitted_passed_indices = set()

        # 第2步：生成文档内容（异步启动）
        msg = json.dumps({'type': 'progress', 'message': f'🚀 开始生成内容（共{total_subsections}个小节）...'})
        yield f"data: {msg}\n\n"

        if ordered_subsections:
            first_item = ordered_subsections[0]
            first_msg = f"开始处理小节: {first_item['section_title']} > {first_item['subsection_title']}"
            msg = json.dumps({
                'type': 'detail',
                'message': first_msg,
                'stage': 'subsection_start',
                'metadata': {
                    'section_id': first_item['section_id'],
                    'subsection_id': first_item['subsection_id'],
                    'section_title': first_item['section_title'],
                    'subsection_title': first_item['subsection_title'],
                    'subsection_order': 1,
                    'section_subsection_total': total_subsections,
                    'synthetic': True,
                },
            })
            yield f"data: {msg}\n\n"
            emitted_start_indices.add(0)

        generate_payload = {
            "document_id": document_id,
            "title": title,
            "structure": structure,
            "content_prompts": content_prompts,
            "user_background": req.user_background,
            "user_requirements": user_requirements,
            "rel_threshold": req.rel_threshold,
            "red_threshold": req.red_threshold,
        }
        
        # 在后台线程中启动生成，同时主线程推送进度
        gen_resp = None
        error_occurred = False
        
        def generate_async():
            nonlocal gen_resp, error_occurred
            try:
                gen_resp = generate_document_with_recovery(
                    document_id=document_id,
                    generate_payload=generate_payload,
                    timeout_seconds=stream_timeout,
                )
                if not isinstance(gen_resp, dict) or not gen_resp.get("success"):
                    error_occurred = True
                    print(f"生成错误: {gen_resp}")
            except Exception as e:
                error_occurred = True
                gen_resp = {"success": False, "error": f"生成服务异常: {e}"}
                print(f"生成错误: {e}")
        
        gen_thread = threading.Thread(target=generate_async, daemon=True)
        gen_thread.start()
        
        # 定期检查生成进度
        last_count = 0
        last_event_id = 0
        timeout = time.time() + stream_timeout
        last_progress_update = time.time()
        last_keepalive = time.time()
        
        while gen_thread.is_alive() and time.time() < timeout:
            try:
                # 查询当前生成的小节数
                history_resp = DOWNSTREAM_SESSION.post(
                    f"{OUTLINER_URL}/history/get",
                    json={"document_id": document_id},
                    timeout=10
                )
                if history_resp.status_code == 200:
                    history = history_resp.json().get("history", [])
                    current_count = len(history)

                    while len(emitted_passed_indices) < current_count and len(emitted_passed_indices) < len(ordered_subsections):
                        next_idx = len(emitted_passed_indices)
                        item = ordered_subsections[next_idx]
                        pass_msg = f"小节通过验证: {item['section_title']} > {item['subsection_title']}"
                        msg = json.dumps({
                            'type': 'detail',
                            'message': pass_msg,
                            'stage': 'subsection_passed',
                            'metadata': {
                                'section_id': item['section_id'],
                                'subsection_id': item['subsection_id'],
                                'section_title': item['section_title'],
                                'subsection_title': item['subsection_title'],
                                'subsection_order': next_idx + 1,
                                'section_subsection_total': total_subsections,
                                'synthetic': True,
                            },
                        })
                        yield f"data: {msg}\n\n"
                        emitted_passed_indices.add(next_idx)

                    next_start_idx = current_count
                    if next_start_idx < len(ordered_subsections) and next_start_idx not in emitted_start_indices:
                        item = ordered_subsections[next_start_idx]
                        start_msg = f"开始处理小节: {item['section_title']} > {item['subsection_title']}"
                        msg = json.dumps({
                            'type': 'detail',
                            'message': start_msg,
                            'stage': 'subsection_start',
                            'metadata': {
                                'section_id': item['section_id'],
                                'subsection_id': item['subsection_id'],
                                'section_title': item['section_title'],
                                'subsection_title': item['subsection_title'],
                                'subsection_order': next_start_idx + 1,
                                'section_subsection_total': total_subsections,
                                'synthetic': True,
                            },
                        })
                        yield f"data: {msg}\n\n"
                        emitted_start_indices.add(next_start_idx)
                    
                    # 每次进度变化或30秒都推送一次进度
                    if current_count > last_count or time.time() - last_progress_update > 30:
                        progress = min(100, int(current_count / total_subsections * 100)) if total_subsections > 0 else 0
                        msg = json.dumps({'type': 'progress', 'message': f'进度: {current_count}/{total_subsections} 小节已完成 ({progress}%)'})
                        yield f"data: {msg}\n\n"
                        last_count = current_count
                        last_progress_update = time.time()

                    if time.time() - last_keepalive > 15:
                        heartbeat = json.dumps({'type': 'heartbeat', 'message': '⏳ 生成中，正在保持连接...'})
                        yield f"data: {heartbeat}\n\n"
                        last_keepalive = time.time()

                # 查询流程细节事件
                events_resp = DOWNSTREAM_SESSION.post(
                    f"{OUTLINER_URL}/history/progress",
                    json={"document_id": document_id, "after_id": last_event_id, "limit": 200},
                    timeout=10,
                )
                if events_resp.status_code == 200:
                    events = events_resp.json().get("events", [])
                    for event_item in events:
                        detail_msg = event_item.get("message", "")
                        event_stage = event_item.get("stage", "")
                        event_meta = event_item.get("metadata", {})
                        msg = json.dumps({
                            'type': 'detail',
                            'message': detail_msg,
                            'stage': event_stage,
                            'metadata': event_meta,
                        })
                        yield f"data: {msg}\n\n"
                        last_event_id = max(last_event_id, int(event_item.get("id", 0)))
            except Exception as e:
                print(f"查询进度异常: {e}")
            
            time.sleep(2)  # 每2秒检查一次
        
        # 等待线程结束（最多等待10秒）
        gen_thread.join(timeout=10)
        
        if error_occurred:
            history_items = fetch_history_items(document_id=document_id, timeout_seconds=30)
            if history_items:
                gen_resp = gen_resp or {}
                gen_resp.update({
                    "success": True,
                    "passed_subsections": len(history_items),
                    "failed_subsections": gen_resp.get("failed_subsections", []),
                    "forced_subsections": gen_resp.get("forced_subsections", []),
                    "interrupted": True,
                    "interrupted_reason": gen_resp.get("error", "生成服务连接失败"),
                })
                msg = json.dumps({'type': 'warning', 'message': f'⚠️ 生成中断，先返回已通过的 {len(history_items)} 个小节'})
                yield f"data: {msg}\n\n"
            else:
                msg = json.dumps({'type': 'error', 'message': '生成服务连接失败'})
                yield f"data: {msg}\n\n"
                return
        
        if gen_resp is None:
            # 线程仍在运行但超时 - 尝试从数据库恢复
            try:
                history_resp = DOWNSTREAM_SESSION.post(
                    f"{OUTLINER_URL}/history/get",
                    json={"document_id": document_id},
                    timeout=10
                )
                if history_resp.status_code == 200:
                    history_items = history_resp.json().get("history", [])
                    if len(history_items) > 0:
                        # 有部分小节生成成功，返回部分结果
                        msg = json.dumps({'type': 'progress', 'message': '⚠️ 生成超时，返回已完成的部分内容...'})
                        yield f"data: {msg}\n\n"
                        gen_resp = {
                            "success": True,
                            "passed_subsections": len(history_items),
                            "failed_subsections": [],
                            "total_iterations": 0,
                            "generation_time": f"{time.time() - (timeout - stream_timeout):.2f}s"
                        }
                    else:
                        msg = json.dumps({'type': 'error', 'message': '生成未能开始，请检查生成服务'})
                        yield f"data: {msg}\n\n"
                        return
            except:
                msg = json.dumps({'type': 'error', 'message': '生成超时且无法恢复'})
                yield f"data: {msg}\n\n"
                return
        
        if not gen_resp.get("success"):
            history_items = fetch_history_items(document_id=document_id, timeout_seconds=30)
            if history_items:
                gen_resp.update({
                    "success": True,
                    "passed_subsections": len(history_items),
                    "failed_subsections": gen_resp.get("failed_subsections", []),
                    "forced_subsections": gen_resp.get("forced_subsections", []),
                    "interrupted": True,
                    "interrupted_reason": gen_resp.get('error', '文档生成失败'),
                })
                msg = json.dumps({'type': 'warning', 'message': f'⚠️ 生成中断，先返回已通过的 {len(history_items)} 个小节'})
                yield f"data: {msg}\n\n"
            else:
                err_msg = gen_resp.get('error', '文档生成失败')
                msg = json.dumps({'type': 'error', 'message': err_msg})
                yield f"data: {msg}\n\n"
                return

        # 再抓取一轮收尾事件，避免线程结束时最后几条细节日志丢失
        try:
            events_resp = DOWNSTREAM_SESSION.post(
                f"{OUTLINER_URL}/history/progress",
                json={"document_id": document_id, "after_id": last_event_id, "limit": 500},
                timeout=10,
            )
            if events_resp.status_code == 200:
                events = events_resp.json().get("events", [])
                for event_item in events:
                    detail_msg = event_item.get("message", "")
                    event_stage = event_item.get("stage", "")
                    event_meta = event_item.get("metadata", {})
                    msg = json.dumps({
                        'type': 'detail',
                        'message': detail_msg,
                        'stage': event_stage,
                        'metadata': event_meta,
                    })
                    yield f"data: {msg}\n\n"
                    last_event_id = max(last_event_id, int(event_item.get("id", 0)))
        except Exception as e:
            print(f"收尾事件查询异常: {e}")

        try:
            history_resp = DOWNSTREAM_SESSION.post(
                f"{OUTLINER_URL}/history/get",
                json={"document_id": document_id},
                timeout=10
            )
            final_history = history_resp.json().get("history", []) if history_resp.status_code == 200 else []
            final_count = len(final_history)
            while len(emitted_passed_indices) < final_count and len(emitted_passed_indices) < len(ordered_subsections):
                next_idx = len(emitted_passed_indices)
                item = ordered_subsections[next_idx]
                pass_msg = f"小节通过验证: {item['section_title']} > {item['subsection_title']}"
                msg = json.dumps({
                    'type': 'detail',
                    'message': pass_msg,
                    'stage': 'subsection_passed',
                    'metadata': {
                        'section_id': item['section_id'],
                        'subsection_id': item['subsection_id'],
                        'section_title': item['section_title'],
                        'subsection_title': item['subsection_title'],
                        'subsection_order': next_idx + 1,
                        'section_subsection_total': total_subsections,
                        'synthetic': True,
                    },
                })
                yield f"data: {msg}\n\n"
                emitted_passed_indices.add(next_idx)
        except Exception as e:
            print(f"收尾补发小节事件异常: {e}")
        
        # 第3步：获取最终内容
        msg = json.dumps({'type': 'progress', 'message': '📦 整合文档内容...'})
        yield f"data: {msg}\n\n"
        
        try:
            history_resp = DOWNSTREAM_SESSION.post(
                f"{OUTLINER_URL}/history/get",
                json={"document_id": document_id},
                timeout=10
            )
            history_items = history_resp.json().get("history", []) if history_resp.status_code == 200 else []
        except:
            history_items = []

        markdown_content = build_markdown_document(
            title,
            structure,
            history_items,
            generated_sections=gen_resp.get("sections", []),
        )
        
        # 计算统计数据
        expected_subsections = req.chapter_count * req.subsection_count
        passed = gen_resp.get("passed_subsections", 0)
        failed = len(gen_resp.get("failed_subsections", []))
        forced = len(gen_resp.get("forced_subsections", []))
        total_generated = passed + failed

        partial_mode = passed < expected_subsections or bool(gen_resp.get("interrupted"))
        if passed <= 0:
            msg = json.dumps({
                'type': 'error',
                'message': f'生成未产出可用内容：通过 {passed}/{expected_subsections}，失败 {failed}。请重试或检查下游服务。'
            })
            yield f"data: {msg}\n\n"
            return
        if partial_mode:
            warn_text = gen_resp.get("interrupted_reason") or f'生成未达到目标小节数：通过 {passed}/{expected_subsections}，失败 {failed}'
            msg = json.dumps({'type': 'warning', 'message': f'⚠️ {warn_text}，先展示已通过内容'})
            yield f"data: {msg}\n\n"
        
        result = {
            "success": True,
            "partial": partial_mode,
            "interrupted": partial_mode,
            "document_id": document_id,
            "title": title,
            "content": markdown_content,
            "stats": {
                "expected_subsections": expected_subsections,
                "passed_subsections": passed,
                "failed_subsections": failed,
                "forced_subsections": forced,
                "total_generated": total_generated,
                "total_iterations": gen_resp.get("total_iterations", 0),
                "generation_time": gen_resp.get("generation_time", ""),
                "controller_effective_subsections": gen_resp.get("controller_effective_subsections", 0),
                "rag_used_subsections": gen_resp.get("rag_used_subsections", 0),
                "rag_search_success_subsections": gen_resp.get("rag_search_success_subsections", 0),
                **extract_document_quality_metrics(gen_resp if isinstance(gen_resp, dict) else {}),
            },
        }
        
        msg = json.dumps({'type': 'complete', 'result': result})
        yield f"data: {msg}\n\n"
        
    except Exception as e:
        msg = json.dumps({'type': 'error', 'message': f'内部错误: {str(e)}'})
        yield f"data: {msg}\n\n"


@app.get("/api/generate-stream")
async def generate_stream_endpoint(
    topic: str,
    chapter_count: int = 2,
    subsection_count: int = 2,
    user_background: str = "普通读者",
    extra_requirements: str = "",
    rel_threshold: float = WEB_DEFAULT_REL_THRESHOLD,
    red_threshold: float = WEB_DEFAULT_RED_THRESHOLD,
    timeout_seconds: int = 7200,
    x_api_key: str = Header(default="", alias="X-API-Key"),
    authorization: str = Header(default="", alias="Authorization"),
):
    """SSE 端点：实时推送文档生成进度"""
    verify_auth(x_api_key=x_api_key, authorization=authorization)
    req = GenerateDocRequest(
        topic=topic,
        chapter_count=chapter_count,
        subsection_count=subsection_count,
        user_background=user_background,
        extra_requirements=extra_requirements,
        rel_threshold=rel_threshold,
        red_threshold=red_threshold,
        timeout_seconds=timeout_seconds,
    )
    
    return StreamingResponse(
        generate_stream(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@app.get("/api/recover-document")
def recover_document(document_id: str) -> Dict[str, Any]:
    history_items = fetch_history_items(document_id=document_id, timeout_seconds=60)
    if not history_items:
        return {
            "success": False,
            "document_id": document_id,
            "error": "history_not_ready",
            "message": "后台内容尚未可恢复，请稍后重试。",
        }

    title, structure = _load_document_structure(document_id=document_id, history=history_items)
    markdown_content = build_markdown_document(
        title=title,
        structure=structure,
        history=history_items,
        generated_sections=None,
    )

    expected = 0
    for sec in (structure.get("sections") or []):
        subs = sec.get("subsections") if isinstance(sec, dict) else []
        if isinstance(subs, list):
            expected += len(subs)

    passed = len(history_items)
    partial = expected > 0 and passed < expected

    return {
        "success": True,
        "partial": partial,
        "document_id": document_id,
        "title": title,
        "content": markdown_content,
        "stats": {
            "expected_subsections": expected,
            "passed_subsections": passed,
            "failed_subsections": 0,
            "forced_subsections": 0,
            "total_generated": passed,
        },
    }


@app.post("/api/generate")
def generate_document(
    req: GenerateDocRequest,
    x_api_key: str = Header(default="", alias="X-API-Key"),
    authorization: str = Header(default="", alias="Authorization"),
) -> Dict[str, Any]:
    verify_auth(x_api_key=x_api_key, authorization=authorization)
    timeout_profile = _build_timeout_profile(
        chapter_count=req.chapter_count,
        subsection_count=req.subsection_count,
        requested_timeout=req.timeout_seconds or REQUEST_TIMEOUT,
    )
    effective_timeout = timeout_profile["effective_timeout_seconds"]
    start_time = time.time()
    try:
        result = _build_document(req=req, timeout_seconds=effective_timeout)
        elapsed = time.time() - start_time
        _record_timeout_metrics(elapsed_seconds=elapsed, result=result)
        _inject_timeout_profile(result=result, timeout_profile=timeout_profile)
        return result
    except HTTPException:
        raise
    except Exception as e:
        print(f"[generate_document] UNEXPECTED ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"文档生成异常: {str(e)[:200]}")


@app.post("/api/download-docx")
def download_docx(
    req: DownloadDocxRequest,
    x_api_key: str = Header(default="", alias="X-API-Key"),
    authorization: str = Header(default="", alias="Authorization"),
):
    verify_auth(x_api_key=x_api_key, authorization=authorization)
    stream = markdown_to_docx(req.title, req.content)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_title = (req.title or "flowernet_document").strip()[:40]
    ascii_fallback = "flowernet_document"
    encoded = quote(f"{safe_title}_{ts}.docx")

    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f"attachment; filename={ascii_fallback}_{ts}.docx; filename*=UTF-8''{encoded}"
        },
    )


def _run_poffices_task(task_id: str, req: PofficesGenerateRequest):
    try:
        with POFFICES_TASKS_LOCK:
            POFFICES_TASKS[task_id]["status"] = "running"
            POFFICES_TASKS[task_id]["message"] = "任务运行中"
            POFFICES_TASKS[task_id]["started_at"] = datetime.now().isoformat()

        timeout_profile = _build_timeout_profile(
            chapter_count=req.chapter_count,
            subsection_count=req.subsection_count,
            requested_timeout=req.timeout_seconds,
        )
        start_time = time.time()

        doc_req = GenerateDocRequest(
            topic=req.query,
            chapter_count=req.chapter_count,
            subsection_count=req.subsection_count,
            user_background=req.user_background,
            extra_requirements=req.extra_requirements,
            rel_threshold=req.rel_threshold,
            red_threshold=req.red_threshold,
        )
        result = _build_document(req=doc_req, timeout_seconds=timeout_profile["effective_timeout_seconds"])

        elapsed = time.time() - start_time
        _record_timeout_metrics(elapsed_seconds=elapsed, result=result)
        _inject_timeout_profile(result=result, timeout_profile=timeout_profile)
        if elapsed > timeout_profile["effective_timeout_seconds"]:
            raise TimeoutError(f"任务超时: {elapsed:.1f}s > {timeout_profile['effective_timeout_seconds']}s")

        with POFFICES_TASKS_LOCK:
            POFFICES_TASKS[task_id]["status"] = "completed"
            POFFICES_TASKS[task_id]["result"] = result
            POFFICES_TASKS[task_id]["message"] = "任务完成"
    except HTTPException as exc:
        with POFFICES_TASKS_LOCK:
            POFFICES_TASKS[task_id]["status"] = "failed"
            POFFICES_TASKS[task_id]["error"] = str(exc.detail)
            POFFICES_TASKS[task_id]["message"] = "任务失败"
    except Exception as exc:
        with POFFICES_TASKS_LOCK:
            POFFICES_TASKS[task_id]["status"] = "failed"
            POFFICES_TASKS[task_id]["error"] = str(exc)
            POFFICES_TASKS[task_id]["message"] = "任务失败"


@app.post("/api/poffices/generate")
def poffices_generate(
    req: PofficesGenerateRequest,
    request: Request,
    x_api_key: str = Header(default="", alias="X-API-Key"),
    authorization: str = Header(default="", alias="Authorization"),
):
    verify_auth(x_api_key=x_api_key, authorization=authorization)

    normalized_query = (req.query or "").strip()
    if len(normalized_query) < 2:
        return {
            "success": False,
            "task_status": "failed",
            "error": "query 不能为空且至少 2 个字符",
            "message": "请求参数无效",
        }

    req = req.model_copy(update={"query": normalized_query})

    if req.async_mode:
        task_id = f"task_{uuid4().hex}"
        with POFFICES_TASKS_LOCK:
            POFFICES_TASKS[task_id] = {
                "status": "queued",
                "message": "任务已入队",
                "created_at": datetime.now().isoformat(),
                "started_at": None,
                "timeout_seconds": req.timeout_seconds,
                "request": req.model_dump(),
            }

        th = threading.Thread(target=_run_poffices_task, args=(task_id, req), daemon=True)
        th.start()
        return {
            "success": True,
            "task_id": task_id,
            "status": "queued",
            "poll_url": str(request.url_for("poffices_task_status")),
            "message": "异步任务已创建，请轮询 task status",
        }

    timeout_profile = _build_timeout_profile(
        chapter_count=req.chapter_count,
        subsection_count=req.subsection_count,
        requested_timeout=req.timeout_seconds,
    )

    doc_req = GenerateDocRequest(
        topic=req.query,
        chapter_count=req.chapter_count,
        subsection_count=req.subsection_count,
        user_background=req.user_background,
        extra_requirements=req.extra_requirements,
        rel_threshold=req.rel_threshold,
        red_threshold=req.red_threshold,
    )
    try:
        start_time = time.time()
        result = _build_document(req=doc_req, timeout_seconds=timeout_profile["effective_timeout_seconds"])
        elapsed = time.time() - start_time
        _record_timeout_metrics(elapsed_seconds=elapsed, result=result)
        _inject_timeout_profile(result=result, timeout_profile=timeout_profile)
        return _build_poffices_result(request=request, result=result)
    except HTTPException as exc:
        return {
            "success": False,
            "task_status": "failed",
            "error": str(exc.detail),
            "message": "文档生成失败",
        }
    except Exception as exc:
        return {
            "success": False,
            "task_status": "failed",
            "error": str(exc),
            "message": "文档生成异常",
        }


@app.post("/api/poffices/task-status", name="poffices_task_status")
def poffices_task_status(
    req: PofficesTaskStatusRequest,
    request: Request,
    x_api_key: str = Header(default="", alias="X-API-Key"),
    authorization: str = Header(default="", alias="Authorization"),
):
    verify_auth(x_api_key=x_api_key, authorization=authorization)

    with POFFICES_TASKS_LOCK:
        task = POFFICES_TASKS.get(req.task_id)

    if not task:
        raise HTTPException(status_code=404, detail="task_id not found")

    status = task.get("status", "unknown")

    if status in {"queued", "running"}:
        started_at = task.get("started_at") or task.get("created_at")
        timeout_seconds = int(task.get("timeout_seconds") or 0)
        if started_at and timeout_seconds > 0:
            try:
                elapsed = (datetime.now() - datetime.fromisoformat(started_at)).total_seconds()
                if elapsed > timeout_seconds + 30:
                    with POFFICES_TASKS_LOCK:
                        if req.task_id in POFFICES_TASKS and POFFICES_TASKS[req.task_id].get("status") in {"queued", "running"}:
                            POFFICES_TASKS[req.task_id]["status"] = "failed"
                            POFFICES_TASKS[req.task_id]["error"] = f"任务超时: {int(elapsed)}s"
                            POFFICES_TASKS[req.task_id]["message"] = "任务超时"
                    status = "failed"
                    task = POFFICES_TASKS.get(req.task_id, task)
            except ValueError:
                pass

    if status == "completed":
        result = task.get("result", {})
        return _build_poffices_result(request=request, result=result)

    if status == "failed":
        return {
            "success": False,
            "task_id": req.task_id,
            "status": "failed",
            "error": task.get("error", "unknown error"),
            "message": task.get("message", "任务失败"),
        }

    return {
        "success": True,
        "task_id": req.task_id,
        "status": status,
        "message": task.get("message", "处理中"),
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8010"))
    uvicorn.run(app, host="0.0.0.0", port=port)
