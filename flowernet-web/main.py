from datetime import datetime
from io import BytesIO
import os
import threading
import time
import random
from typing import Any, Dict, List, Generator, Optional
from urllib.parse import quote
import json
from uuid import uuid4
from datetime import timezone
from email.utils import parsedate_to_datetime

import requests
from docx import Document
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field


OUTLINER_URL = os.getenv("OUTLINER_URL", "http://localhost:8003")
GENERATOR_URL = os.getenv("GENERATOR_URL", "http://localhost:8002")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "3600"))  # 1小时超时，适配Ollama耗时生成
DOWNSTREAM_RETRIES = int(os.getenv("DOWNSTREAM_RETRIES", "3"))
DOWNSTREAM_BACKOFF = float(os.getenv("DOWNSTREAM_BACKOFF", "1.0"))
DOWNSTREAM_MAX_BACKOFF = float(os.getenv("DOWNSTREAM_MAX_BACKOFF", "30.0"))
DOWNSTREAM_JITTER = float(os.getenv("DOWNSTREAM_JITTER", "0.35"))
API_AUTH_ENABLED = os.getenv("API_AUTH_ENABLED", "false").lower() == "true"
API_KEY = os.getenv("FLOWERNET_API_KEY", "")
BEARER_TOKEN = os.getenv("FLOWERNET_BEARER_TOKEN", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")

POFFICES_TASKS: Dict[str, Dict[str, Any]] = {}
POFFICES_TASKS_LOCK = threading.Lock()


class GenerateDocRequest(BaseModel):
    topic: str = Field(..., min_length=2, description="文档主题")
    chapter_count: int = Field(default=5, ge=1, le=10)
    subsection_count: int = Field(default=3, ge=1, le=8)
    user_background: str = Field(default="普通读者")
    extra_requirements: str = Field(default="")
    rel_threshold: float = Field(default=0.95, ge=0, le=1)
    red_threshold: float = Field(default=0.10, ge=0, le=1)


class DownloadDocxRequest(BaseModel):
    title: str
    content: str


class PofficesGenerateRequest(BaseModel):
    query: str = Field(default="", description="用户输入查询")
    chapter_count: int = Field(default=5, ge=1, le=10)
    subsection_count: int = Field(default=3, ge=1, le=8)
    user_background: str = Field(default="普通读者")
    extra_requirements: str = Field(default="")
    rel_threshold: float = Field(default=0.95, ge=0, le=1)
    red_threshold: float = Field(default=0.10, ge=0, le=1)
    async_mode: bool = Field(default=True, description="true=异步任务，false=同步等待结果")
    timeout_seconds: int = Field(default=600, ge=60, le=7200, description="同步模式超时秒数")


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


def _is_transient_downstream_payload(payload: Dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("success") is not False:
        return False
    message = str(payload.get("error") or payload.get("message") or "").lower()
    transient_tokens = [
        "429", "too many requests", "resource_exhausted", "quota", "rate",
        "timeout", "timed out", "temporarily", "503", "502", "504", "retry",
    ]
    return any(token in message for token in transient_tokens)


def post_json_with_retry(url: str, payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    last_error: str = ""
    for attempt in range(1, DOWNSTREAM_RETRIES + 1):
        retry_delay = DOWNSTREAM_BACKOFF * (2 ** max(0, attempt - 1))
        retry_delay += random.uniform(0, DOWNSTREAM_JITTER)
        retry_after_seconds = None

        try:
            response = requests.post(url, json=payload, timeout=timeout)
            response.raise_for_status()
            body = response.json()

            if _is_transient_downstream_payload(body) and attempt < DOWNSTREAM_RETRIES:
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
            else:
                last_error = str(exc)

            if attempt < DOWNSTREAM_RETRIES:
                if retry_after_seconds is not None:
                    retry_delay = max(retry_delay, retry_after_seconds)
                retry_delay = min(retry_delay, DOWNSTREAM_MAX_BACKOFF)
                time.sleep(retry_delay)

    raise HTTPException(
        status_code=502,
        detail=f"下游服务请求失败(重试{DOWNSTREAM_RETRIES}次): {url}, 错误: {last_error}",
    )


def post_json(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return post_json_with_retry(url=url, payload=payload, timeout=REQUEST_TIMEOUT)


def _build_document(req: GenerateDocRequest, timeout_seconds: int) -> Dict[str, Any]:
    document_id = f"web_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    user_requirements = build_requirements_text(req)

    outline_payload = {
        "document_id": document_id,
        "user_background": req.user_background,
        "user_requirements": user_requirements,
        "max_sections": req.chapter_count,
        "max_subsections_per_section": req.subsection_count,
    }
    outline_resp = post_json_with_retry(f"{OUTLINER_URL}/outline/generate-and-save", outline_payload, timeout_seconds)

    if not outline_resp.get("success"):
        raise HTTPException(status_code=500, detail=f"大纲生成失败: {outline_resp}")

    title = outline_resp.get("document_title") or f"{req.topic} 文档"
    structure = outline_resp.get("structure", {})
    content_prompts = outline_resp.get("content_prompts", [])
    if not isinstance(structure, dict) or not isinstance(content_prompts, list):
        raise HTTPException(status_code=500, detail=f"大纲结果格式异常: {outline_resp}")

    expected_subsections = req.chapter_count * req.subsection_count
    outlined_subsections = len(content_prompts)
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
    gen_resp = post_json_with_retry(f"{GENERATOR_URL}/generate_document", generate_payload, timeout_seconds)

    if not gen_resp.get("success"):
        raise HTTPException(status_code=500, detail=f"文档生成失败: {gen_resp}")

    passed = gen_resp.get("passed_subsections", 0)
    failed = len(gen_resp.get("failed_subsections", []))
    forced = len(gen_resp.get("forced_subsections", []))
    if passed < outlined_subsections:
        raise HTTPException(
            status_code=500,
            detail=f"文档生成未达到大纲小节数: 通过 {passed}/{outlined_subsections}, 失败 {failed}",
        )

    history_resp = post_json_with_retry(f"{OUTLINER_URL}/history/get", {"document_id": document_id}, 60)
    history_items = history_resp.get("history", []) if history_resp.get("success") else []
    markdown_content = build_markdown_document(
        title,
        structure,
        history_items,
        generated_sections=gen_resp.get("sections", []),
    )

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
        },
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

    lines = [f"# {title}", ""]
    for section in structure.get("sections", []):
        section_id = section.get("id", "")
        section_title = section.get("title", "未命名章节")
        lines.append(f"## {section_title}")
        lines.append("")

        for subsection in section.get("subsections", []):
            subsection_id = subsection.get("id", "")
            subsection_title = subsection.get("title", "未命名小节")
            key = f"{section_id}::{subsection_id}"
            subsection_text = content_map.get(key, "（该小节未成功生成）")

            lines.append(f"### {subsection_title}")
            lines.append("")
            lines.append(subsection_text)
            lines.append("")

    return "\n".join(lines).strip()


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

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("### "):
            document.add_heading(line[4:].strip(), level=3)
        elif line.startswith("## "):
            document.add_heading(line[3:].strip(), level=2)
        elif line.startswith("# "):
            document.add_heading(line[2:].strip(), level=1)
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
        
        try:
            outline_http_resp = requests.post(
                f"{OUTLINER_URL}/outline/generate-and-save",
                json=outline_payload,
                timeout=REQUEST_TIMEOUT
            )
            outline_resp = outline_http_resp.json()
        except Exception as e:
            msg = json.dumps({'type': 'error', 'message': f'大纲生成失败: {str(e)}'})
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
                resp = requests.post(
                    f"{GENERATOR_URL}/generate_document",
                    json=generate_payload,
                    timeout=REQUEST_TIMEOUT
                )
                gen_resp = resp.json() if resp.status_code == 200 else {"success": False, "error": f"HTTP {resp.status_code}"}
            except Exception as e:
                error_occurred = True
                print(f"生成错误: {e}")
        
        gen_thread = threading.Thread(target=generate_async, daemon=True)
        gen_thread.start()
        
        # 定期检查生成进度
        last_count = 0
        last_event_id = 0
        timeout = time.time() + REQUEST_TIMEOUT
        last_progress_update = time.time()
        
        while gen_thread.is_alive() and time.time() < timeout:
            try:
                # 查询当前生成的小节数
                history_resp = requests.post(
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

                # 查询流程细节事件
                events_resp = requests.post(
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
            msg = json.dumps({'type': 'error', 'message': '生成服务连接失败'})
            yield f"data: {msg}\n\n"
            return
        
        if gen_resp is None:
            # 线程仍在运行但超时 - 尝试从数据库恢复
            try:
                history_resp = requests.post(
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
                            "generation_time": f"{time.time() - (timeout - REQUEST_TIMEOUT):.2f}s"
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
            err_msg = gen_resp.get('error', '文档生成失败')
            msg = json.dumps({'type': 'error', 'message': err_msg})
            yield f"data: {msg}\n\n"
            return

        # 再抓取一轮收尾事件，避免线程结束时最后几条细节日志丢失
        try:
            events_resp = requests.post(
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
            history_resp = requests.post(
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
            history_resp = requests.post(
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

        if passed < expected_subsections:
            msg = json.dumps({
                'type': 'error',
                'message': f'生成未达到目标小节数：通过 {passed}/{expected_subsections}，失败 {failed}。请重试或检查下游服务。'
            })
            yield f"data: {msg}\n\n"
            return
        
        result = {
            "success": True,
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
    rel_threshold: float = 0.95,
    red_threshold: float = 0.10,
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
    )
    
    return StreamingResponse(
        generate_stream(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@app.post("/api/generate")
def generate_document(
    req: GenerateDocRequest,
    x_api_key: str = Header(default="", alias="X-API-Key"),
    authorization: str = Header(default="", alias="Authorization"),
) -> Dict[str, Any]:
    verify_auth(x_api_key=x_api_key, authorization=authorization)
    return _build_document(req=req, timeout_seconds=REQUEST_TIMEOUT)


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
        result = _build_document(req=doc_req, timeout_seconds=req.timeout_seconds)

        elapsed = time.time() - start_time
        if elapsed > req.timeout_seconds:
            raise TimeoutError(f"任务超时: {elapsed:.1f}s > {req.timeout_seconds}s")

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
        result = _build_document(req=doc_req, timeout_seconds=req.timeout_seconds)
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
