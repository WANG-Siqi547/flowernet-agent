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
from xml.sax.saxutils import escape as xml_escape

import requests
from docx import Document
from docx.shared import Pt, Inches
from docx.oxml.ns import qn
from docx.enum.text import WD_ALIGN_PARAGRAPH
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, inch
from reportlab.lib.fonts import addMapping
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Preformatted
from pydantic import BaseModel, Field

# 导入 Citation Verifier 用于引证质量控制
try:
    from citation_verifier import CitationVerifier, verify_references
    HAS_CITATION_VERIFIER = True
except ImportError:
    HAS_CITATION_VERIFIER = False
    print("⚠️ Citation Verifier 未安装，跳过引证验证")

# 导入 Domain Filter 用于基于领域相关性的过滤
try:
    from domain_filter import get_domain_filter
    HAS_DOMAIN_FILTER = True
except ImportError:
    HAS_DOMAIN_FILTER = False
    print("⚠️ Domain Filter 未安装，跳过领域过滤")

# 导入指标定义
try:
    from metrics_definition import (
        FLOWERNET_METRICS,
        METRICS_CATEGORIES,
        FLOWERNET_FEATURES,
        get_all_metrics,
        get_all_categories,
        get_metrics_by_category,
    )
    HAS_METRICS_DEFINITION = True
except ImportError:
    HAS_METRICS_DEFINITION = False
    print("⚠️ Metrics Definition 未安装，跳过指标展示")
    FLOWERNET_METRICS = {}
    METRICS_CATEGORIES = {}
    FLOWERNET_FEATURES = {}


OUTLINER_URL = os.getenv("OUTLINER_URL", "http://localhost:8003")
GENERATOR_URL = os.getenv("GENERATOR_URL", "http://localhost:8002")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "7200"))
DOWNSTREAM_RETRIES = int(os.getenv("DOWNSTREAM_RETRIES", "1"))
DOWNSTREAM_BACKOFF = float(os.getenv("DOWNSTREAM_BACKOFF", "1.0"))
DOWNSTREAM_MAX_BACKOFF = float(os.getenv("DOWNSTREAM_MAX_BACKOFF", "30.0"))
DOWNSTREAM_JITTER = float(os.getenv("DOWNSTREAM_JITTER", "0.35"))
DOWNSTREAM_OUTLINER_MIN_RETRIES = int(os.getenv("DOWNSTREAM_OUTLINER_MIN_RETRIES", "1"))
DOWNSTREAM_OUTLINER_MAX_RETRIES = int(os.getenv("DOWNSTREAM_OUTLINER_MAX_RETRIES", "2"))
DOWNSTREAM_OUTLINER_MIN_DELAY_503 = float(os.getenv("DOWNSTREAM_OUTLINER_MIN_DELAY_503", "8.0"))
DOWNSTREAM_GENERATOR_MIN_RETRIES = int(os.getenv("DOWNSTREAM_GENERATOR_MIN_RETRIES", "1"))
DOWNSTREAM_GENERATOR_MIN_DELAY_429 = float(os.getenv("DOWNSTREAM_GENERATOR_MIN_DELAY_429", "10.0"))
GENERATOR_RESUME_RETRIES = int(os.getenv("GENERATOR_RESUME_RETRIES", "0"))
GENERATOR_DOWNSTREAM_MIN_TIMEOUT = int(os.getenv("GENERATOR_DOWNSTREAM_MIN_TIMEOUT", "600"))
OUTLINER_DOWNSTREAM_MIN_TIMEOUT = int(os.getenv("OUTLINER_DOWNSTREAM_MIN_TIMEOUT", "600"))
DOWNSTREAM_OUTLINER_MIN_DELAY_429 = float(os.getenv("DOWNSTREAM_OUTLINER_MIN_DELAY_429", "35.0"))
DOWNSTREAM_OUTLINER_COOLDOWN_429 = float(os.getenv("DOWNSTREAM_OUTLINER_COOLDOWN_429", "60.0"))
DOWNSTREAM_GENERATOR_COOLDOWN_429 = float(os.getenv("DOWNSTREAM_GENERATOR_COOLDOWN_429", "20.0"))
GENERATOR_RESUME_BACKOFF = float(os.getenv("GENERATOR_RESUME_BACKOFF", "2.0"))
API_AUTH_ENABLED = os.getenv("API_AUTH_ENABLED", "false").lower() == "true"
API_KEY = os.getenv("FLOWERNET_API_KEY", "")
BEARER_TOKEN = os.getenv("FLOWERNET_BEARER_TOKEN", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
WEB_DEFAULT_REL_THRESHOLD = float(os.getenv("WEB_DEFAULT_REL_THRESHOLD", "0.75"))
# Make redundancy detection slightly stricter by default (user requested)
WEB_DEFAULT_RED_THRESHOLD = float(os.getenv("WEB_DEFAULT_RED_THRESHOLD", "0.40"))
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
RATE_LIMIT_LOCK = threading.Lock()
RATE_LIMIT_UNTIL: Dict[str, float] = {}


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

# Add request logging middleware for debugging
class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if "/api/download" in str(request.url):
            print(f"[Middleware] Incoming {request.method} {request.url.path}", flush=True)
        response = await call_next(request)
        if "/api/download" in str(request.url):
            print(f"[Middleware] Response status: {response.status_code}", flush=True)
        return response

app.add_middleware(LoggingMiddleware)


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


def _downstream_rate_limit_key(url: str) -> str:
    try:
        parsed = urlparse(url)
        host = (parsed.netloc or "").strip().lower()
        if "outliner" in host or "/outline/" in parsed.path:
            return "outliner"
        if "generator" in host or "/generate" in parsed.path:
            return "generator"
        return host or "downstream"
    except Exception:
        return "downstream"


def _set_rate_limit_cooldown(rate_key: str, seconds: float) -> None:
    delay = max(0.0, float(seconds or 0.0))
    if delay <= 0:
        return
    with RATE_LIMIT_LOCK:
        now = time.time()
        next_allowed = now + delay
        current = float(RATE_LIMIT_UNTIL.get(rate_key, 0.0) or 0.0)
        RATE_LIMIT_UNTIL[rate_key] = max(current, next_allowed)


def _get_rate_limit_sleep_seconds(rate_key: str) -> float:
    with RATE_LIMIT_LOCK:
        now = time.time()
        next_allowed = float(RATE_LIMIT_UNTIL.get(rate_key, 0.0) or 0.0)
    return max(0.0, next_allowed - now)


def _probe_service_health(base_url: str, timeout: float = 5.0) -> bool:
    root_url = base_url.rstrip("/") + "/"
    health_url = base_url.rstrip("/") + "/health"

    for url in (health_url, root_url):
        try:
            response = DOWNSTREAM_SESSION.get(url, timeout=timeout)
            if 200 <= response.status_code < 500:
                return True
        except requests.RequestException:
            continue
    return False


def _wait_for_service_ready(base_url: str, label: str, max_wait_seconds: float = 45.0) -> bool:
    deadline = time.time() + max(0.0, float(max_wait_seconds or 0.0))
    attempt = 0
    while True:
        attempt += 1
        if _probe_service_health(base_url, timeout=5.0):
            print(f"[Web] ✅ {label} 健康检查通过")
            return True

        remaining = deadline - time.time()
        if remaining <= 0:
            print(f"[Web] ⚠️ {label} 健康检查超时，继续尝试主请求")
            return False

        sleep_seconds = min(5.0 + min(attempt * 1.5, 10.0), remaining)
        print(f"[Web] ⏳ 等待 {label} 就绪（第 {attempt} 次，{sleep_seconds:.1f}s 后重试）")
        time.sleep(max(1.0, sleep_seconds))


def post_json_with_retry(url: str, payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    last_error: str = ""
    is_outliner_url = "outliner" in url or "/outline/" in url
    is_generator_url = "generator" in url or "/generate_document" in url
    rate_key = _downstream_rate_limit_key(url)
    effective_retries = DOWNSTREAM_RETRIES
    if is_outliner_url:
        effective_retries = max(effective_retries, DOWNSTREAM_OUTLINER_MIN_RETRIES)
        effective_retries = min(effective_retries, max(1, DOWNSTREAM_OUTLINER_MAX_RETRIES))
    if is_generator_url:
        effective_retries = max(effective_retries, DOWNSTREAM_GENERATOR_MIN_RETRIES)

    for attempt in range(1, effective_retries + 1):
        cooldown_sleep = _get_rate_limit_sleep_seconds(rate_key)
        if cooldown_sleep > 0:
            time.sleep(cooldown_sleep)

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

            # Outliner already performs internal provider-level retries.
            # Avoid multiplying retries here which can trigger remote 429.
            if is_outliner_url and isinstance(body, dict) and body.get("success") is False:
                return body

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
                        _set_rate_limit_cooldown(
                            rate_key,
                            max(retry_delay, retry_after_seconds or 0.0, DOWNSTREAM_GENERATOR_COOLDOWN_429),
                        )
                    if is_outliner_url:
                        # For Outliner on Render Free: 429 means rate limit exceeded.
                        # Don't retry - break immediately to fail fast and prevent DoS spiral.
                        last_error = f"HTTP 429 from {url}: {response_body} (Render rate limit - no retry)"
                        break
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


def post_json_once(url: str, payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    try:
        response = DOWNSTREAM_SESSION.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        try:
            body = response.json()
        except ValueError:
            content_type = response.headers.get("Content-Type", "")
            response_body = (response.text or "")[:800]
            raise HTTPException(
                status_code=502,
                detail=(
                    f"HTTP {response.status_code} from {url}: 下游返回非JSON响应 "
                    f"(Content-Type={content_type or 'unknown'}): {response_body or '<empty>'}"
                ),
            )

        if isinstance(body, dict):
            return body
        raise HTTPException(status_code=502, detail=f"HTTP {response.status_code} from {url}: 下游返回格式异常")
    except requests.RequestException as exc:
        response = getattr(exc, "response", None)
        if response is not None:
            response_body = _extract_response_error(response)
            raise HTTPException(
                status_code=502,
                detail=f"HTTP {response.status_code} from {url}: {response_body}",
            )
        raise HTTPException(status_code=502, detail=f"下游服务请求失败: {url}, 错误: {exc}")


def call_outliner_generate_and_save(payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    target_url = f"{OUTLINER_URL}/outline/generate-and-save"
    return post_json_once(url=target_url, payload=payload, timeout=max(OUTLINER_DOWNSTREAM_MIN_TIMEOUT, int(timeout)))


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


def _recover_partial_document(document_id: str, attempts: int = 5, timeout_seconds: int = 30) -> Dict[str, Any]:
    attempts = max(1, int(attempts))
    delay_seconds = 1.5
    last_history_items: List[Dict[str, Any]] = []

    for attempt in range(1, attempts + 1):
        history_items = fetch_history_items(document_id=document_id, timeout_seconds=timeout_seconds)
        if history_items:
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
            return {
                "success": True,
                "partial": expected > 0 and passed < expected,
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
                "history_items": history_items,
                "attempts": attempt,
            }

        last_history_items = history_items
        if attempt < attempts:
            time.sleep(min(3.0, delay_seconds))
            delay_seconds = min(5.0, delay_seconds * 1.6)

    return {
        "success": False,
        "document_id": document_id,
        "error": "history_not_ready",
        "message": "后台内容尚未可恢复，请稍后重试。",
        "history_items": last_history_items,
        "attempts": attempts,
    }


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
        # Optional plot bounds provided by orchestrator for frontend chart scaling
        "bandit_plot_y_min": float(gen_resp.get("plot_y_min", 0.0) or 0.0),
        "bandit_plot_y_max": float(gen_resp.get("plot_y_max", 0.0) or 0.0),
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
            # The generator is the slowest hop and has to survive the full subsection loop.
            # Use a larger floor here than the general stream budget so SSE validation does
            # not fail just because the orchestrator needs more time than the adaptive web budget.
            call_timeout = max(GENERATOR_DOWNSTREAM_MIN_TIMEOUT, int(timeout_seconds))
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
    _wait_for_service_ready(OUTLINER_URL, "outliner", max_wait_seconds=min(60.0, max(20.0, timeout_seconds / 10.0)))
    outline_resp = call_outliner_generate_and_save(
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


def _is_outline_like_fallback_content(
    content: str,
    outline: str = "",
    forced_pass: bool = False,
) -> bool:
    text = str(content or "").strip()
    if not text:
        return False
    outline_text = str(outline or "").strip()
    compact_text = " ".join(text.split())
    compact_outline = " ".join(outline_text.split())

    has_system_fallback_prefix = text.startswith("（系统兜底）")
    prompt_markers = ["请你作为", "要求：", "段落主题", "系统指示", "content_prompt"]
    has_prompt_marker = any(marker in text for marker in prompt_markers)

    outline_embedded = False
    if compact_outline and len(compact_outline) >= 16:
        outline_embedded = (
            compact_outline in compact_text
            or compact_text in compact_outline
        )

    # 仅在 forced_pass/系统兜底场景下过滤，避免误伤正常正文
    should_filter = bool((forced_pass or has_system_fallback_prefix) and (outline_embedded or has_prompt_marker))
    if should_filter:
        print(
            f"[Web] filtering outline-like content: forced_pass={forced_pass}, "
            f"system_prefix={has_system_fallback_prefix}, outline_embedded={outline_embedded}, "
            f"has_prompt_marker={has_prompt_marker}, len={len(compact_text)}"
        )
    return should_filter


def _build_content_map_from_history(history: List[Dict[str, Any]]) -> Dict[str, str]:
    """从history中构建content map，优先使用非forced_pass的内容"""
    content_map: Dict[str, str] = {}
    # 第一遍：记录所有内容
    for item in history:
        key = f"{item.get('section_id', '')}::{item.get('subsection_id', '')}"
        content = item.get("content", "")
        metadata = item.get("metadata", {})
        is_forced_pass = metadata.get("forced_pass", False)
        outline = metadata.get("outline", "")

        if _is_outline_like_fallback_content(content=content, outline=outline, forced_pass=bool(is_forced_pass)):
            continue
        
        # 仅当content非空时才添加；如果已有内容且新内容是forced_pass，则不覆盖
        if content:
            if key not in content_map:
                content_map[key] = content
            elif not is_forced_pass:
                # 如果新内容不是forced_pass，优先使用它（更新已存在的content）
                content_map[key] = content
    return content_map


def _build_content_map_from_sections(sections: Optional[List[Dict[str, Any]]]) -> Dict[str, str]:
    content_map: Dict[str, str] = {}
    for section in sections or []:
        section_id = str(section.get("section_id") or section.get("id") or "")
        for subsection in section.get("subsections", []) or []:
            subsection_id = str(subsection.get("subsection_id") or subsection.get("id") or "")
            content = subsection.get("content", "")
            is_forced_pass = bool(subsection.get("forced_pass", False))
            outline = subsection.get("outline", "")
            if _is_outline_like_fallback_content(content=content, outline=outline, forced_pass=is_forced_pass):
                continue
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
        content_map[key] = value

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
        highlighted = "; ".join(section_titles[:3]) if section_titles else "the main section themes"
        return (
            f"This paper presents an IEEE-style structured overview of \"{_normalize_label(title)}\". "
            f"Its content is organized into a hierarchical sequence of sections and subsections to support traceability, modular editing, and consistent citation handling. "
            f"The document emphasizes the core topics of {highlighted} while maintaining a concise academic tone and a formal technical presentation."
        )

    def _build_keywords() -> str:
        section_titles = [
            _normalize_label(section.get("title", ""))
            for section in structure.get("sections", [])
            if str(section.get("title", "")).strip()
        ]
        keywords = [
            _normalize_label(title),
            "IEEE-style formatting",
            "structured document generation",
            "reference management",
            "hierarchical writing",
            "academic presentation",
        ]
        for section_title in section_titles[:3]:
            cleaned = _normalize_label(section_title)
            if cleaned and cleaned not in keywords:
                keywords.append(cleaned)
        return "Index Terms—" + ", ".join(keywords[:6])

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

    def _collect_reference_entries() -> List[str]:
        seen_urls: set[str] = set()
        references: List[str] = []
        candidates: List[Dict[str, Any]] = []  # 存储原始候选对象用于Domain Filter

        def _collect_textual_citations() -> List[str]:
            patterns = [
                re.compile(r"\(([^()]{2,100}?,\s*\d{4}[a-z]?)\)"),
                re.compile(r"([A-Z][A-Za-z\-]+(?:\s+et al\.)?,\s*\d{4}[a-z]?)"),
                re.compile(r"\[来源\d+\]"),
            ]
            seen_citations: set[str] = set()
            textual_refs: List[str] = []

            def scan_text(text: str) -> None:
                sample = str(text or "")
                if not sample:
                    return
                for pattern in patterns:
                    for match in pattern.findall(sample):
                        citation = match if isinstance(match, str) else str(match)
                        citation = " ".join(citation.split()).strip(" .,;，")
                        if len(citation) < 3:
                            continue
                        key = citation.lower()
                        if key in seen_citations:
                            continue
                        seen_citations.add(key)
                        textual_refs.append(citation)

            for section in generated_sections or []:
                for subsection in (section.get("subsections") or []):
                    scan_text(subsection.get("content", ""))

            for item in history or []:
                scan_text(item.get("content", ""))

            return textual_refs[:20]

        def add_candidate(candidate: Dict[str, Any]) -> None:
            if not isinstance(candidate, dict):
                return
            url = str(candidate.get("href") or candidate.get("url") or candidate.get("link") or "").strip()
            title_text = str(candidate.get("title") or candidate.get("source_name") or candidate.get("source_type") or "").strip()
            if not url:
                return
            key = url.lower()
            if key in seen_urls:
                return
            seen_urls.add(key)
            
            # 保存原始候选对象用于Domain Filter
            candidates.append(candidate)
            
            label = title_text or urlparse(url).netloc or "source"
            snippet = str(candidate.get("body") or candidate.get("description") or candidate.get("summary") or "").strip()
            if snippet:
                references.append(f"{label}: {url} — {snippet[:180]}")
            else:
                references.append(f"{label}: {url}")

        for section in generated_sections or []:
            for subsection in (section.get("subsections") or []):
                for item in subsection.get("source_results", []) or []:
                    add_candidate(item if isinstance(item, dict) else {})

        for item in history or []:
            metadata = item.get("metadata", {}) if isinstance(item, dict) else {}
            for candidate in metadata.get("source_results", []) or []:
                add_candidate(candidate if isinstance(candidate, dict) else {})

        # 🔍 应用Domain Filter进行领域相关性检查
        if HAS_DOMAIN_FILTER and candidates:
            try:
                domain_filter = get_domain_filter()
                
                # 从文档中提取Index Terms
                index_terms = domain_filter.extract_document_index_terms(
                    title=title,
                    outline=str(structure.get("outline", "")),
                    abstract=str(structure.get("abstract", "")),
                    content_sample=" ".join([
                        subsection.get("content", "")[:200]
                        for section in (generated_sections or [])
                        for subsection in section.get("subsections", [])
                    ])[:1000],
                )
                
                # 过滤候选引用
                filtered_candidates, filtered_out = domain_filter.filter_citations(
                    citations=candidates,
                    index_terms=index_terms,
                    debug=False,
                )
                
                # 重建references列表（仅保留过滤后的）
                references.clear()
                seen_urls.clear()
                for candidate in filtered_candidates:
                    url = str(candidate.get("href") or candidate.get("url") or candidate.get("link") or "").strip()
                    title_text = str(candidate.get("title") or candidate.get("source_name") or candidate.get("source_type") or "").strip()
                    if not url or url.lower() in seen_urls:
                        continue
                    seen_urls.add(url.lower())
                    label = title_text or urlparse(url).netloc or "source"
                    snippet = str(candidate.get("body") or candidate.get("description") or candidate.get("summary") or "").strip()
                    if snippet:
                        references.append(f"{label}: {url} — {snippet[:180]}")
                    else:
                        references.append(f"{label}: {url}")
                        
            except Exception as e:
                print(f"⚠️ Domain Filter error: {e}, continuing with original references")

        if references:
            return references

        # Fallback 1: extract real URLs directly from generated content/history when
        # source_results metadata is unavailable, so references are still real URLs.
        def add_url_from_text(text: str) -> None:
            for url in _extract_urls(str(text or "")):
                key = url.lower().strip()
                if not key or key in seen_urls:
                    continue
                seen_urls.add(key)
                label = urlparse(url).netloc or "source"
                references.append(f"{label}: {url}")

        for section in generated_sections or []:
            for subsection in (section.get("subsections") or []):
                add_url_from_text(subsection.get("content", ""))

        for item in history or []:
            add_url_from_text(item.get("content", ""))

        if references:
            return references

        # Fallback 2: textual citation extraction when no URLs exist in content.
        return _collect_textual_citations()

    def _inject_inline_citations(text: str, reference_index: Dict[str, int]) -> str:
        """
        Aggressively inject [1], [2], [3] citations into document text.
        GUARANTEED: If references exist, citations WILL appear in output.
        """
        if not text or not reference_index:
            return text

        result = str(text)
        
        # Phase 1: Replace explicit URLs and placeholders
        for url, idx in sorted(reference_index.items(), key=lambda item: len(item[0]), reverse=True):
            if url:
                result = re.sub(re.escape(url), f"[{idx}]", result, flags=re.IGNORECASE)
        
        result = re.sub(r"\[来源(\d+)\]", r"[\1]", result)
        result = re.sub(r"\(来源\s*ID\s*[:：]?\s*(\d+)\)", r"[\1]", result)
        result = re.sub(r"\(来源\s*[:：]?\s*(\d+)\)", r"[\1]", result)
        result = re.sub(r"（来源\s*ID\s*[:：]?\s*(\d+)）", r"[\1]", result)
        result = re.sub(r"（来源\s*[:：]?\s*(\d+)）", r"[\1]", result)
        
        # Phase 2: If NO citations found yet, FORCE inject them
        if not re.search(r'\[\d+\]', result):
            sentences = re.split(r'(?<=[。.!?！？\n])\s*', result.strip())
            sentences = [s for s in sentences if s.strip()]
            
            if len(sentences) > 0:
                # Inject in first substantial sentence
                for i, sent in enumerate(sentences):
                    if len(sent.strip()) > 15:
                        idx = list(reference_index.values())[0] if reference_index else 1
                        sentences[i] = sent + f" [{idx}]"
                        break
                
                result = " ".join(sentences)
        
        return result

    def _append_references(lines: List[str], references: List[str]) -> None:
        lines.append("## References")
        lines.append("")
        if not references:
            lines.extend([
                "[1] A. A. Author, \"Title of article,\" Journal/Conference, vol. x, no. x, pp. xx-xx, Year.",
                "[2] B. B. Author, Book Title, xth ed. City, Country: Publisher, Year.",
                "[3] C. C. Author, \"Title of report or web resource,\" Organization, Year. [Online]. Available: URL.",
                "",
            ])
            return
        for idx, ref in enumerate(references, 1):
            lines.append(_format_ieee_reference_entry(ref, idx))
        lines.append("")

    reference_entries = _collect_reference_entries()
    
    # ============ 引证质量验证与重排 ============
    if HAS_CITATION_VERIFIER and reference_entries:
        try:
            # 构建引用对象列表以供 Citation Verifier 处理
            ref_dicts = []
            for ref_text in reference_entries:
                urls = _extract_urls(ref_text)
                if urls:
                    ref_dicts.append({
                        'title': ref_text.split(':', 1)[0] if ':' in ref_text else ref_text[:100],
                        'url': urls[0],
                        'body': ref_text,
                    })
            
            # 调用 Citation Verifier 进行验证和重排
            if ref_dicts:
                filtered_refs, quality_report = verify_references(
                    references=ref_dicts,
                    topic=title,
                    section_outline=_build_abstract()[:500],
                    full_content="\n".join([
                        s.get("content", "") 
                        for section in (generated_sections or [])
                        for s in section.get("subsections", [])
                    ])[:2000],
                )
                
                # 用过滤和重排后的引用替换原始引用
                if filtered_refs:
                    reference_entries = []
                    for ref_dict in filtered_refs:
                        ref_text = ref_dict.get('body', '')
                        if ref_text:
                            reference_entries.append(ref_text)
                
                # 记录质量报告（用于调试）
                print(f"📋 {quality_report}")
        except Exception as e:
            print(f"⚠️ Citation Verifier 处理失败，继续使用原始引用: {e}")
    
    reference_index: Dict[str, int] = {}
    for idx, ref in enumerate(reference_entries, 1):
        for url in _extract_urls(ref):
            reference_index.setdefault(url.lower().strip(), idx)
    if reference_entries and not reference_index:
        reference_index["__fallback_ref__"] = 1

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
    ]
    lines.append("")
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
            
            # 获取该subsection的内容，如果为空则表示内容仍在恢复或生成失败
            raw_content = content_map.get(key)
            if raw_content:
                subsection_text = _clean_subsection_text(raw_content)
            else:
                # 如果内容为空，显示清晰的恢复中标记，而不是试图补充outline
                subsection_text = "（本小节内容仍在后台生成/恢复中，请稍后刷新或重新下载）"

            subsection_text = _inject_inline_citations(subsection_text, reference_index)

            lines.append(f'<a id="{_anchor_id(section_index, subsection_index)}"></a>')
            lines.append(f"### {alpha}. {_normalize_label(subsection_title)}")
            lines.append("")
            lines.append(subsection_text)
            lines.append("")


    _append_references(lines, reference_entries)

    # After assembling lines, replace any source placeholders like [来源1], (来源 ID:1),
    # (来源:1) or Chinese variants with the actual source name extracted from
    # reference entries so the DOCX body shows human-readable source names.
    doc_text = "\n".join(lines).strip()

    try:
        references = _collect_reference_entries()
        labels: List[str] = []
        for ref in references:
            if not isinstance(ref, str):
                continue
            label = str(ref).split(":", 1)[0].strip()
            labels.append(label)

        def _replace_placeholder(match: re.Match) -> str:
            idx = int(match.group(1)) if match and match.group(1) else None
            if not idx or idx < 1 or idx > len(labels):
                return match.group(0)
            return labels[idx - 1]

        patterns = [
            re.compile(r"\[来源(\d+)\]"),
            re.compile(r"\(来源\s*ID\s*[:：]?\s*(\d+)\)"),
            re.compile(r"\(来源\s*[:：]?\s*(\d+)\)"),
            re.compile(r"（来源\s*ID\s*[:：]?\s*(\d+)）"),
            re.compile(r"（来源\s*[:：]?\s*(\d+)）"),
        ]

        for pat in patterns:
            doc_text = pat.sub(_replace_placeholder, doc_text)
    except Exception:
        # If anything goes wrong, fall back to original assembled text.
        doc_text = "\n".join(lines).strip()

    return doc_text


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


def _normalize_render_text(text: str) -> str:
    """
    Comprehensively clean all markdown, special characters, and formatting artifacts.
    """
    result = str(text or "")
    
    # STAGE 1: Remove markdown syntax comprehensively
    # Remove all forms of heading markers (anywhere in text, not just line start)
    result = re.sub(r'^#+\s+', '', result, flags=re.M)  # Line-start headings
    result = re.sub(r'\s+^#+', '', result, flags=re.M)  # Headings with leading space
    result = re.sub(r'####+', '', result)  # Multiple # anywhere
    
    # Remove bold/italic markers
    result = re.sub(r'\*\*(.*?)\*\*', r'\1', result, flags=re.S)  # **bold**
    result = re.sub(r'__(.*?)__', r'\1', result, flags=re.S)  # __bold__
    result = re.sub(r'\*(.*?)\*', r'\1', result, flags=re.S)  # *italic*
    result = re.sub(r'_(.*?)_', r'\1', result, flags=re.S)  # _italic_
    
    # Remove inline code and special markers
    result = re.sub(r'`([^`]+)`', r'\1', result)  # `code`
    result = re.sub(r'~~(.*?)~~', r'\1', result, flags=re.S)  # ~~strikethrough~~
    result = re.sub(r'\{\{(.*?)\}\}', r'\1', result, flags=re.S)  # {{template}}
    
    # Remove math markers but keep content
    result = re.sub(r'\\\((.*?)\\\)', r'\1', result, flags=re.S)  # \(...\)
    result = re.sub(r'\\\[(.*?)\\\]', r'\1', result, flags=re.S)  # \[...\]
    result = re.sub(r'\$\$(.*?)\$\$', r'\1', result, flags=re.S)  # $$...$$
    result = re.sub(r'\$([^\$]+)\$', r'\1', result, flags=re.S)  # $...$ inline
    
    # Remove markdown links
    result = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', result)  # [text](link)
    result = re.sub(r'<a[^>]*>(.*?)</a>', r'\1', result, flags=re.S)  # <a>...</a>
    
    # STAGE 2: Clean up artifacts and normalize whitespace
    result = result.replace("\u00a0", " ")  # Non-breaking space
    result = result.replace("​", "")  # Zero-width space
    result = re.sub(r'[ \t]+', ' ', result)  # Collapse multiple spaces/tabs
    result = re.sub(r'\n\s*\n', '\n', result)  # Collapse multiple newlines
    
    # STAGE 3: Remove trailing markdown artifacts on lines
    lines = result.split('\n')
    cleaned_lines = []
    for line in lines:
        line = line.rstrip()
        # Remove any remaining ## or #### at the end
        while line.endswith('##') or line.endswith('####'):
            line = line[:-1].rstrip()
        cleaned_lines.append(line)
    result = '\n'.join(cleaned_lines)
    
    return result.strip()


def _format_ieee_reference_entry(reference: str, index: int) -> str:
    """Format reference in IEEE style, with comprehensive cleaning."""
    raw = str(reference or "").strip()
    if not raw:
        return f"[{index}] Unknown reference."
    
    # CRITICAL: Clean all markdown/special chars from reference
    cleaned = _normalize_render_text(raw)
    cleaned = " ".join(cleaned.split()).strip()
    if not cleaned:
        return f"[{index}] Unknown reference."
    
    # Remove any remaining math notation, formulas
    cleaned = re.sub(r'\$[^\$]*\$', '', cleaned)  # Remove inline math
    cleaned = re.sub(r'\\[a-zA-Z]+\{[^}]*\}', '', cleaned)  # Remove LaTeX commands
    cleaned = re.sub(r'\\[a-zA-Z]+', '', cleaned)  # Remove LaTeX keywords
    cleaned = re.sub(r'\[[^\]]*\^[^\]]*\]', '', cleaned)  # Remove power notation in brackets
    cleaned = " ".join(cleaned.split()).strip()
    
    # Extract URL
    url_match = re.search(r"https?://[^\s\]）)>,;]+", cleaned, flags=re.IGNORECASE)
    access_date = datetime.now().strftime("%Y-%m-%d")
    
    if url_match:
        url = url_match.group(0).rstrip(".,;)")
        prefix = cleaned[:url_match.start()].strip(" -—:，,")
        suffix = cleaned[url_match.end():].strip(" -—:，,.")
        title = prefix or urlparse(url).netloc or f"Source {index}"
        if ":" in title and not prefix:
            title = title.split(":", 1)[0].strip() or title
        if suffix and suffix.lower() not in {title.lower(), urlparse(url).netloc.lower()}:
            title = suffix
        return f"[{index}] \"{title}\", [Online]. Available: {url}. Accessed: {access_date}."
    
    textual = cleaned
    textual = re.sub(r"\s*—\s*", ", ", textual)
    textual = re.sub(r"\s*:\s*", ", ", textual, count=1)
    return f"[{index}] {textual}"


def _iter_document_blocks(content: str) -> List[Dict[str, str]]:
    blocks: List[Dict[str, str]] = []
    paragraph_lines: List[str] = []
    code_lines: List[str] = []
    equation_lines: List[str] = []
    in_code = False
    in_equation = False

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        if not paragraph_lines:
            return
        paragraph_text = _normalize_render_text(" ".join(paragraph_lines))
        if paragraph_text:
            blocks.append({"type": "paragraph", "text": paragraph_text})
        paragraph_lines = []

    for raw_line in str(content or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code:
                blocks.append({"type": "code", "text": "\n".join(code_lines).rstrip("\n")})
                code_lines = []
                in_code = False
            else:
                flush_paragraph()
                in_equation = False
                equation_lines = []
                in_code = True
            continue

        if in_code:
            code_lines.append(line)
            continue

        if stripped == "$$":
            if in_equation:
                blocks.append({"type": "equation", "text": _normalize_render_text(" ".join(equation_lines))})
                equation_lines = []
                in_equation = False
            else:
                flush_paragraph()
                in_equation = True
            continue

        if in_equation:
            equation_lines.append(line)
            continue

        if not stripped:
            flush_paragraph()
            continue

        if stripped in {"---", "***"}:
            flush_paragraph()
            blocks.append({"type": "separator", "text": ""})
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading_match:
            flush_paragraph()
            blocks.append({"type": "heading", "level": str(len(heading_match.group(1))), "text": _normalize_render_text(heading_match.group(2))})
            continue

        list_match = re.match(r"^(?:[-*+]\s+|\d+[.)]\s+)(.+)$", stripped)
        if list_match:
            flush_paragraph()
            blocks.append({"type": "list_item", "text": _normalize_render_text(list_match.group(1)), "ordered": "true" if re.match(r"^\d", stripped) else "false"})
            continue

        reference_match = re.match(r"^\[\d+\]\s+.+$", stripped)
        if reference_match:
            flush_paragraph()
            blocks.append({"type": "reference", "text": _normalize_render_text(stripped)})
            continue

        if stripped.startswith("<a id="):
            continue

        paragraph_lines.append(stripped)

    flush_paragraph()

    if code_lines:
        blocks.append({"type": "code", "text": "\n".join(code_lines).rstrip("\n")})
    if equation_lines:
        blocks.append({"type": "equation", "text": _normalize_render_text(" ".join(equation_lines))})

    return blocks


def _configure_docx_fonts(document: Document) -> None:
    section = document.sections[0]
    section.top_margin = Inches(0.7)
    section.bottom_margin = Inches(0.7)
    section.left_margin = Inches(0.7)
    section.right_margin = Inches(0.7)

    title_style = document.styles["Title"]
    title_style.font.name = "Times New Roman"
    title_style._element.rPr.rFonts.set(qn("w:eastAsia"), "SimSun")
    title_style.font.size = Pt(16)
    title_style.font.bold = True

    normal_style = document.styles["Normal"]
    normal_style.font.name = "Times New Roman"
    normal_style._element.rPr.rFonts.set(qn("w:eastAsia"), "SimSun")
    normal_style.font.size = Pt(10.5)
    normal_style.paragraph_format.line_spacing = 1.0
    normal_style.paragraph_format.space_before = Pt(0)
    normal_style.paragraph_format.space_after = Pt(0)

    for heading_name, size in (("Heading 1", 15), ("Heading 2", 13), ("Heading 3", 12), ("Heading 4", 11)):
        if heading_name not in document.styles:
            continue
        heading_style = document.styles[heading_name]
        heading_style.font.name = "Times New Roman"
        heading_style._element.rPr.rFonts.set(qn("w:eastAsia"), "SimSun")
        heading_style.font.size = Pt(max(10, size - 4))
        heading_style.font.bold = True
        heading_style.paragraph_format.space_before = Pt(6)
        heading_style.paragraph_format.space_after = Pt(2)

    if "List Bullet" in document.styles:
        bullet_style = document.styles["List Bullet"]
        bullet_style.font.name = "Times New Roman"
        bullet_style._element.rPr.rFonts.set(qn("w:eastAsia"), "SimSun")
        bullet_style.font.size = Pt(10.5)
        bullet_style.paragraph_format.space_before = Pt(0)
        bullet_style.paragraph_format.space_after = Pt(0)


def _add_docx_paragraph(document: Document, text: str, *, style_name: Optional[str] = None, center: bool = False, monospace: bool = False, bold: bool = False) -> None:
    paragraph = document.add_paragraph(style=style_name)
    if center:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run(text)
    if monospace:
        run.font.name = "Courier New"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "Courier New")
    else:
        run.font.name = "Times New Roman"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "SimSun")
    run.font.size = Pt(11)
    run.bold = bold


def _add_docx_reference_paragraph(document: Document, text: str) -> None:
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.left_indent = Inches(0.25)
    paragraph.paragraph_format.first_line_indent = Inches(-0.25)
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    paragraph.paragraph_format.line_spacing = 1.0
    paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    run = paragraph.add_run(text)
    run.font.name = "Times New Roman"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "SimSun")
    run.font.size = Pt(9.5)


def _render_docx_document(title: str, content: str) -> BytesIO:
    document = Document()
    document.core_properties.title = title
    _configure_docx_fonts(document)

    title_added = False
    front_matter_label: Optional[str] = None
    for block in _iter_document_blocks(content):
        block_type = block.get("type", "")
        text = str(block.get("text", "")).strip()
        if block_type == "heading":
            level = max(1, min(4, int(block.get("level", "2"))))
            heading_text = text
            if not title_added and level == 1:
                _add_docx_paragraph(document, heading_text, style_name="Title", center=True, bold=True)
                title_added = True
            elif heading_text == "Abstract":
                front_matter_label = "Abstract"
            elif heading_text == "Index Terms":
                front_matter_label = "Index Terms"
            elif heading_text == "References":
                _add_docx_paragraph(document, "References", style_name="Heading 1", bold=True)
            else:
                _add_docx_paragraph(document, heading_text, style_name=f"Heading {level}", bold=True)
        elif block_type == "paragraph":
            if front_matter_label:
                paragraph = document.add_paragraph()
                paragraph.paragraph_format.space_before = Pt(0)
                paragraph.paragraph_format.space_after = Pt(4)
                paragraph.paragraph_format.line_spacing = 1.0
                paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                label_run = paragraph.add_run(f"{front_matter_label}— ")
                label_run.font.name = "Times New Roman"
                label_run._element.rPr.rFonts.set(qn("w:eastAsia"), "SimSun")
                label_run.bold = True
                label_run.font.size = Pt(10.5)
                body_run = paragraph.add_run(text)
                body_run.font.name = "Times New Roman"
                body_run._element.rPr.rFonts.set(qn("w:eastAsia"), "SimSun")
                body_run.font.size = Pt(10.5)
                front_matter_label = None
            else:
                _add_docx_paragraph(document, text)
        elif block_type == "list_item":
            paragraph = document.add_paragraph(style="List Bullet")
            run = paragraph.add_run(text)
            run.font.name = "Times New Roman"
            run._element.rPr.rFonts.set(qn("w:eastAsia"), "SimSun")
            run.font.size = Pt(10.5)
        elif block_type == "reference":
            _add_docx_reference_paragraph(document, text)
        elif block_type == "equation":
            paragraph = document.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            paragraph.paragraph_format.space_before = Pt(0)
            paragraph.paragraph_format.space_after = Pt(2)
            run = paragraph.add_run(text)
            run.font.name = "Cambria Math"
            run._element.rPr.rFonts.set(qn("w:eastAsia"), "SimSun")
            run.font.size = Pt(10.5)
        elif block_type == "code":
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.left_indent = Inches(0.2)
            paragraph.paragraph_format.space_before = Pt(0)
            paragraph.paragraph_format.space_after = Pt(2)
            run = paragraph.add_run(text)
            run.font.name = "Courier New"
            run._element.rPr.rFonts.set(qn("w:eastAsia"), "SimSun")
            run.font.size = Pt(9.5)
        elif block_type == "separator":
            document.add_paragraph("")

    for paragraph in document.paragraphs:
        plain = paragraph.text.strip()
        if re.match(r"^\[\d+\]\s+", plain):
            paragraph.paragraph_format.left_indent = Inches(0.25)
            paragraph.paragraph_format.first_line_indent = Inches(-0.25)
            paragraph.paragraph_format.space_before = Pt(0)
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.paragraph_format.line_spacing = 1.0
            paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            for run in paragraph.runs:
                run.font.name = "Times New Roman"
                run._element.rPr.rFonts.set(qn("w:eastAsia"), "SimSun")
                run.font.size = Pt(9.5)

    stream = BytesIO()
    document.save(stream)
    stream.seek(0)
    return stream


def _render_pdf_document(title: str, content: str) -> BytesIO:
    stream = BytesIO()

    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        addMapping("STSong-Light", 0, 0, "STSong-Light")
    except Exception:
        pass

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="DocTitle", fontName="STSong-Light", fontSize=14.5, leading=16, alignment=TA_CENTER, spaceAfter=2))
    styles.add(ParagraphStyle(name="DocMeta", fontName="STSong-Light", fontSize=8.4, leading=10, alignment=TA_CENTER, spaceAfter=1))
    styles.add(ParagraphStyle(name="DocFrontLabel", fontName="STSong-Light", fontSize=8.8, leading=10, spaceBefore=0, spaceAfter=0, alignment=TA_LEFT))
    styles.add(ParagraphStyle(name="DocBody", fontName="STSong-Light", fontSize=9.0, leading=10.8, spaceAfter=1.5, alignment=TA_JUSTIFY))
    styles.add(ParagraphStyle(name="DocHeading1", fontName="STSong-Light", fontSize=9.6, leading=11, spaceBefore=2.5, spaceAfter=0.5, alignment=TA_LEFT))
    styles.add(ParagraphStyle(name="DocHeading2", fontName="STSong-Light", fontSize=9.0, leading=10.5, spaceBefore=1.5, spaceAfter=0, alignment=TA_LEFT))
    styles.add(ParagraphStyle(name="DocHeading3", fontName="STSong-Light", fontSize=8.8, leading=10.2, spaceBefore=1, spaceAfter=0, alignment=TA_LEFT))
    styles.add(ParagraphStyle(name="DocEquation", fontName="STSong-Light", fontSize=8.8, leading=10.2, alignment=TA_CENTER, spaceBefore=0.5, spaceAfter=1))
    styles.add(ParagraphStyle(name="DocCode", fontName="Courier", fontSize=8, leading=9.2, leftIndent=8, spaceBefore=0.5, spaceAfter=1))
    styles.add(ParagraphStyle(name="DocReference", fontName="STSong-Light", fontSize=8.1, leading=9.4, leftIndent=12, firstLineIndent=-12, spaceBefore=0, spaceAfter=0, alignment=TA_JUSTIFY))

    page_width, page_height = A4
    side_margin = 0.62 * inch
    top_margin = 0.55 * inch
    bottom_margin = 0.55 * inch
    column_gap = 0.18 * inch
    header_height = 0.24 * inch
    front_height = 1.85 * inch
    body_top = page_height - top_margin - front_height
    body_bottom = bottom_margin
    body_height = body_top - body_bottom
    body_width = page_width - 2 * side_margin
    column_width = (body_width - column_gap) / 2.0

    def _draw_page(canvas, doc_obj) -> None:
        canvas.saveState()
        canvas.setStrokeColorRGB(0.78, 0.78, 0.78)
        canvas.setLineWidth(0.45)
        canvas.line(side_margin, page_height - top_margin + 0.05 * inch, page_width - side_margin, page_height - top_margin + 0.05 * inch)
        canvas.setFont("Times-Roman", 8)
        canvas.drawString(side_margin, page_height - top_margin + 0.09 * inch, title[:64])
        canvas.drawRightString(page_width - side_margin, page_height - top_margin + 0.09 * inch, str(doc_obj.page))
        canvas.setStrokeColorRGB(0.88, 0.88, 0.88)
        canvas.line(side_margin, bottom_margin - 0.08 * inch, page_width - side_margin, bottom_margin - 0.08 * inch)
        canvas.restoreState()

    front_frame = Frame(
        side_margin,
        body_top,
        body_width,
        front_height - header_height,
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
        showBoundary=0,
        id="front",
    )
    left_frame = Frame(
        side_margin,
        body_bottom,
        column_width,
        body_height,
        leftPadding=0,
        rightPadding=column_gap / 2,
        topPadding=0,
        bottomPadding=0,
        showBoundary=0,
        id="left",
    )
    right_frame = Frame(
        side_margin + column_width + column_gap,
        body_bottom,
        column_width,
        body_height,
        leftPadding=column_gap / 2,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
        showBoundary=0,
        id="right",
    )

    doc = BaseDocTemplate(
        stream,
        pagesize=A4,
        leftMargin=side_margin,
        rightMargin=side_margin,
        topMargin=top_margin,
        bottomMargin=bottom_margin,
        title=title,
        author="FlowerNet",
    )
    doc.addPageTemplates([
        PageTemplate(id="IEEE", frames=[front_frame, left_frame, right_frame], onPage=_draw_page),
    ])

    story = [
        Paragraph(xml_escape(title), styles["DocTitle"]),
        Paragraph("IEEE-style two-column manuscript layout", styles["DocMeta"]),
        Spacer(1, 0.04 * inch),
    ]

    front_matter_label: Optional[str] = None
    in_references = False

    for block in _iter_document_blocks(content):
        block_type = block.get("type", "")
        raw_text = str(block.get("text", "")).strip()
        text = xml_escape(raw_text)
        if not text and block_type != "separator":
            continue
        if block_type == "heading":
            level = max(1, min(3, int(block.get("level", "2"))))
            if text == "Abstract":
                front_matter_label = "Abstract"
            elif text == "Index Terms":
                front_matter_label = "Index Terms"
            elif text == "References":
                in_references = True
                story.append(Paragraph("References", styles["DocHeading1"]))
            else:
                style_name = {1: "DocHeading1", 2: "DocHeading2", 3: "DocHeading3"}[level]
                story.append(Paragraph(text, styles[style_name]))
        elif block_type == "paragraph":
            if front_matter_label:
                story.append(Paragraph(f"<b>{front_matter_label}—</b> {text}", styles["DocFrontLabel"]))
                front_matter_label = None
            elif re.match(r"^\[\d+\]\s+", raw_text):
                story.append(Paragraph(text, styles["DocReference"]))
            else:
                story.append(Paragraph(text, styles["DocBody"]))
        elif block_type == "list_item":
            story.append(Paragraph(f"• {text}", styles["DocBody"]))
        elif block_type == "reference":
            story.append(Paragraph(text, styles["DocReference"]))
        elif block_type == "equation":
            story.append(Paragraph(text, styles["DocEquation"]))
        elif block_type == "code":
            story.append(Preformatted(str(block.get("text", "")), styles["DocCode"]))
        elif block_type == "separator":
            story.append(Spacer(1, 0.03 * inch))

    if front_matter_label:
        story.append(Paragraph(f"<b>{front_matter_label}—</b>", styles["DocFrontLabel"]))

    if not in_references:
        story.append(Paragraph("References", styles["DocHeading1"]))

    try:
        doc.build(story)
        print(f"[PDF] ✅ PDF built successfully: {len(story)} story elements", flush=True)
    except Exception as e:
        print(f"[PDF] ❌ Failed to build PDF: {str(e)}", flush=True)
        raise
    
    stream.seek(0)
    return stream


def markdown_to_docx(title: str, content: str) -> BytesIO:
    """Convert markdown to DOCX with comprehensive error handling"""
    try:
        result = _render_docx_document(title, content)
        if result is None or (hasattr(result, 'getbuffer') and result.getbuffer().nbytes == 0):
            print(f"[DOCX] ⚠️ Generated DOCX is empty or None", flush=True)
            raise ValueError("DOCX generation produced empty or None result")
        return result
    except Exception as e:
        print(f"[DOCX] ❌ Error rendering DOCX: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
        raise


def markdown_to_pdf(title: str, content: str) -> BytesIO:
    """Convert markdown to PDF with comprehensive error handling and logging"""
    try:
        result = _render_pdf_document(title, content)
        if result is None or (hasattr(result, 'getbuffer') and result.getbuffer().nbytes == 0):
            print(f"[PDF] ⚠️ Generated PDF is empty or None", flush=True)
            raise ValueError("PDF generation produced empty or None result")
        result.seek(0)  # Ensure BytesIO is at the start
        return result
    except Exception as e:
        print(f"[PDF] ❌ Error rendering PDF: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
        # Re-raise the error so it's properly handled by the endpoint
        raise
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

        document_id = f"web_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"

        # 开始
        msg = json.dumps({
            'type': 'start',
            'message': '开始生成大纲...',
            'metadata': {
                'document_id': document_id,
            },
        })
        yield f"data: {msg}\n\n"

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
                print(f"[Web] 🌐 发起异步 Outliner 请求: {OUTLINER_URL}/outline/generate-and-save")
                print(f"[Web]    document_id: {outline_payload['document_id']}")
                print(f"[Web]    user_requirements: {outline_payload['user_requirements'][:80]}...")
                outline_resp = call_outliner_generate_and_save(
                    outline_payload,
                    stream_timeout,
                )
                print(f"[Web] ✅ Outliner 请求成功: {outline_resp.get('success')}")
            except Exception as e:
                print(f"[Web] ❌ Outliner 请求失败: {str(e)}")
                outline_error = e

        outline_thread = threading.Thread(target=build_outline_async, daemon=True)
        outline_thread.start()
        print(f"[Web] 🚀 启动异步 Outliner 线程（timeout={stream_timeout}s）")
        outline_deadline = time.time() + stream_timeout
        last_outline_keepalive = time.time()

        while outline_thread.is_alive() and time.time() < outline_deadline:
            if time.time() - last_outline_keepalive > 10:
                elapsed = int(time.time() - (outline_deadline - stream_timeout))
                heartbeat = json.dumps({'type': 'heartbeat', 'message': f'⏳ 正在生成大纲（{elapsed}s）...'})
                print(f"[Web] 💓 发送心跳 (已等待{elapsed}s)")
                yield f"data: {heartbeat}\n\n"
                last_outline_keepalive = time.time()
            time.sleep(0.5)

        outline_thread.join(timeout=1)

        if outline_thread.is_alive():
            print(f"[Web] ⏱️ Outliner 生成超时（>{stream_timeout}s）")
            msg = json.dumps({'type': 'error', 'message': f'大纲生成超时（>{stream_timeout}s），请稍后重试'})
            yield f"data: {msg}\n\n"
            return

        if outline_error is not None:
            print(f"[Web] ❌ Outliner 返回错误: {str(outline_error)}")
            if isinstance(outline_error, HTTPException):
                detail = outline_error.detail if isinstance(outline_error.detail, str) else json.dumps(outline_error.detail, ensure_ascii=False)
                msg = json.dumps({'type': 'error', 'message': f'大纲生成失败: {detail}'})
                yield f"data: {msg}\n\n"
                return
            msg = json.dumps({'type': 'error', 'message': f'大纲生成失败: {str(outline_error)}'})
            yield f"data: {msg}\n\n"
            return

        if not isinstance(outline_resp, dict):
            print(f"[Web] ❌ Outliner 返回格式异常: {type(outline_resp)}")
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
                    
                    # 注意：不要在生成过程中基于 history 的增量来发送 subsection_passed 事件！
                    # 因为这会导致前端错误地认为所有小节都已完成，即使生成还在进行中。
                    # 只有当生成器真正返回最终结果后，才发送这些事件。
                    # （参考下面的"最后一轮小节完成补发"部分）

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
                    
                    # 每次进度变化或30秒都推送一次进度（但不要做implicit的完成判断）
                    if current_count > last_count or time.time() - last_progress_update > 30:
                        # 注意：这里只是报告已添加到 history 的项目数，不应该用来判断是否完成
                        # 因为 forced_pass 的小节也会立即被添加到 history，
                        # 但可能还没有经过真正的验证（verifier），所以指标可能还是0
                        progress = min(100, int(current_count / total_subsections * 100)) if total_subsections > 0 else 0
                        msg = json.dumps({
                            'type': 'progress', 
                            'message': f'进度: {current_count}/{total_subsections} 小节已处理 ({progress}%)',
                            'note': '进度反映已保存的内容，部分可能是强制通过的临时结果',
                        })
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
                        if event_stage == "controller_result":
                            print(f"🎯 [Web SSE Forward] controller_result event: arm={event_meta.get('selected_arm')}, reward={event_meta.get('reward')}")
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
            
            # 仅在生成器完全返回后，才补发最后的 subsection_passed 事件
            # 注意：检查每个 history 项的 forced_pass 标记，只发送真正通过验证的小节
            # （forced_pass 的小节不发送"通过验证"事件，避免前端误导）
            for idx in range(len(emitted_passed_indices), final_count):
                if idx >= len(ordered_subsections):
                    break
                    
                item = ordered_subsections[idx]
                history_item = final_history[idx] if idx < len(final_history) else {}
                is_forced_pass = history_item.get("metadata", {}).get("forced_pass", False)
                
                # 只发送非 forced_pass 的小节为"通过验证"
                if not is_forced_pass:
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
                            'subsection_order': idx + 1,
                            'section_subsection_total': total_subsections,
                            'verification_passed': True,
                        },
                    })
                    yield f"data: {msg}\n\n"
                else:
                    # forced_pass 的小节显示为"内容已生成"而不是"通过验证"
                    pass_msg = f"小节生成完成（强制通过）: {item['section_title']} > {item['subsection_title']}"
                    msg = json.dumps({
                        'type': 'detail',
                        'message': pass_msg,
                        'stage': 'subsection_generated',
                        'metadata': {
                            'section_id': item['section_id'],
                            'subsection_id': item['subsection_id'],
                            'section_title': item['section_title'],
                            'subsection_title': item['subsection_title'],
                            'subsection_order': idx + 1,
                            'section_subsection_total': total_subsections,
                            'forced_pass': True,
                            'force_reason': history_item.get("metadata", {}).get("force_reason", "unknown"),
                        },
                    })
                    yield f"data: {msg}\n\n"
                
                emitted_passed_indices.add(idx)
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
    return _recover_partial_document(document_id=document_id, attempts=6, timeout_seconds=45)


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
    print(f"[DOCX] ✅ ENDPOINT CALLED", flush=True)
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


@app.post("/api/download-pdf")
def download_pdf(
    req: DownloadDocxRequest,
    x_api_key: str = Header(default="", alias="X-API-Key"),
    authorization: str = Header(default="", alias="Authorization"),
):
    print(f"[PDF] ✅ ENDPOINT CALLED", flush=True)
    verify_auth(x_api_key=x_api_key, authorization=authorization)
    try:
        print(f"[PDF] 🔄 Building PDF (title={req.title[:30] if req.title else 'N/A'}..., content_len={len(req.content) if req.content else 0})", flush=True)
        stream = markdown_to_pdf(req.title, req.content)
        if stream.getbuffer().nbytes == 0:
            print(f"[PDF] ❌ Generated PDF is empty!", flush=True)
            raise ValueError("PDF generation produced empty file")
        print(f"[PDF] ✅ PDF ready: {stream.getbuffer().nbytes} bytes", flush=True)
    except Exception as e:
        print(f"[PDF] ❌ PDF download failed: {str(e)}", flush=True)
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {str(e)}")
    
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_title = (req.title or "flowernet_document").strip()[:40]
    ascii_fallback = "flowernet_document"
    encoded = quote(f"{safe_title}_{ts}.pdf")

    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename={ascii_fallback}_{ts}.pdf; filename*=UTF-8''{encoded}"
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


# ==================== 导入指标展示 API ====================
try:
    from metrics_api import router as metrics_router
    app.include_router(metrics_router)
    print("✅ 指标展示 API 已加载")
except ImportError:
    print("⚠️  指标展示 API 未加载")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8010"))
    uvicorn.run(app, host="0.0.0.0", port=port)
