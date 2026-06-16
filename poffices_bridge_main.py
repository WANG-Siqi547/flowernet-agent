from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
import json
import re
import time
import html
import urllib.error
import urllib.request
from typing import Any

app = FastAPI()

FLOWERNET_BASE_URL = "https://flowernet-web.onrender.com"
POLL_WAIT_SECONDS = 25
START_URL = f"{FLOWERNET_BASE_URL}/api/poffices/generate"
STATUS_URL = f"{FLOWERNET_BASE_URL}/api/poffices/task-status"
POLL_URL = f"{FLOWERNET_BASE_URL}/api/poffices/poll-render"
WEB_URL = FLOWERNET_BASE_URL
BRIDGE_VERSION = "2026-06-16-poffices-document-render-v1"

TASK_BY_KEY: dict[str, str] = {}
STALE_TASK_IDS: set[str] = set()

BLOCK_PROMPT_MARKERS = (
    "FlowerNet Input Parser",
    "FlowerNet Start Task Block",
    "FlowerNet Poll Render Block",
    "FlowerNet Final Render Block",
    "FlowerNet Document Display Block",
    "Return exactly this schema",
    "Output strict JSON only",
    "Call the FlowerNet",
    "Primary goal:",
)


def _post_json(url: str, payload: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        if exc.code in (502, 503, 504):
            return {
                "success": True,
                "status": "running",
                "task_status": "running",
                "error": f"HTTP {exc.code}: upstream temporarily unavailable",
                "message": f"FlowerNet upstream returned HTTP {exc.code}; please continue polling.",
                "content": f"FlowerNet upstream returned HTTP {exc.code}; please continue polling.",
                "text": f"FlowerNet upstream returned HTTP {exc.code}; please continue polling.",
                "result": f"FlowerNet upstream returned HTTP {exc.code}; please continue polling.",
                "output": f"FlowerNet upstream returned HTTP {exc.code}; please continue polling.",
            }
        return {"success": False, "status": "failed", "error": f"HTTP {exc.code}: {raw}"}
    except Exception as exc:
        return {"success": False, "status": "failed", "error": str(exc)}


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)
    elif isinstance(value, str):
        text = value.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                yield from _walk(json.loads(text))
            except Exception:
                pass


def _payload_text(payload: Any) -> str:
    parts = []
    for item in _walk(payload):
        if isinstance(item, str) and item.strip():
            parts.append(item.strip())
        elif isinstance(item, (int, float, bool)):
            parts.append(str(item))
    return "\n".join(parts)


def _extract_task_id(payload: Any) -> str:
    for item in _walk(payload):
        if isinstance(item, dict):
            for key in ("task_id", "taskId"):
                val = item.get(key)
                if isinstance(val, str) and val.startswith("task_") and val not in STALE_TASK_IDS:
                    return val.strip()
        if isinstance(item, str):
            match = re.search(r"task_[A-Za-z0-9_:-]{12,}", item)
            if match and match.group(0) not in STALE_TASK_IDS:
                return match.group(0)
    return ""


def _looks_like_block_prompt(text: str) -> bool:
    return any(marker in text for marker in BLOCK_PROMPT_MARKERS)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _first_value(payload: Any, keys: tuple[str, ...]) -> str:
    for item in _walk(payload):
        if isinstance(item, dict):
            for key in keys:
                if key in item:
                    text = _clean_text(item.get(key))
                    if text and not _looks_like_block_prompt(text):
                        return text
    return ""


def _section_after(label: str, text: str) -> str:
    pattern = rf"{re.escape(label)}\s*:\s*(.*?)(?:\n\s*[A-Z][A-Z0-9_ ]{{2,}}\s*:|\n\s*Return\b|\Z)"
    match = re.search(pattern, text, flags=re.S)
    return match.group(1).strip() if match else ""


def _to_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except Exception:
        return default


def _extract_generation_request(payload: Any) -> dict:
    text = _payload_text(payload)
    audit = payload.get("flowernet_audit") if isinstance(payload, dict) and isinstance(payload.get("flowernet_audit"), dict) else {}

    topic = (
        _first_value(payload, ("query", "topic", "REAL_USER_REQUEST", "real_user_request", "user_request", "prompt"))
        or _first_value(audit, ("query", "request_key", "topic"))
        or _section_after("REAL_USER_REQUEST", text)
    )
    if not topic:
        topic = _first_value(payload, ("request", "input"))
    if not topic:
        topic = "General professional long-form report"

    user_background = _first_value(payload, ("user_background", "background", "audience"))
    extra = _first_value(payload, ("extra_requirements", "requirements", "source_context_summary", "file_context"))
    file_context = _section_after("UPLOADED_FILE_CONTEXT", text)
    if file_context:
        extra = (extra + "\n\nUploaded file context:\n" + file_context).strip()

    chapter_count = _first_value(payload, ("chapter_count", "chapters", "chapterCount")) or audit.get("chapter_count")
    subsection_count = _first_value(payload, ("subsection_count", "subsections", "subsection", "subsectionCount")) or audit.get("subsection_count")

    return {
        "query": topic.strip(),
        "chapter_count": _to_int(chapter_count, 2, 1, 10),
        "subsection_count": _to_int(subsection_count, 2, 1, 8),
        "user_background": user_background or "",
        "extra_requirements": extra or "",
        "async_mode": True,
        "timeout_seconds": 7200,
    }


def _task_key(gen_req: dict) -> str:
    return re.sub(r"\s+", " ", (gen_req.get("query") or "").strip().lower())[:220]


def _contains_task_not_found(data: Any) -> bool:
    return "task_id not found" in _payload_text(data).lower()


def _content_from_result(data: Any) -> str:
    if not isinstance(data, dict):
        return str(data or "")
    for key in ("markdown", "document", "content", "text", "result", "output", "message"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    nested = data.get("result")
    if isinstance(nested, dict):
        return _content_from_result(nested)
    return json.dumps(data, ensure_ascii=False, indent=2)


def _decode_text_maybe_json(text: str) -> Any:
    text = str(text or "").strip()
    if not text:
        return ""
    if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
        try:
            return json.loads(text)
        except Exception:
            pass
    return text


def _looks_like_completed_document(text: str) -> bool:
    text = str(text or "").strip()
    if len(text) < 200:
        return False
    return bool(re.search(r"(?m)^#\s+\S+", text) and re.search(r"(?m)^##\s+", text))


def _extract_completed_document(payload: Any) -> tuple[str, str, str]:
    candidates: list[tuple[int, str, str, str]] = []
    status_messages: list[str] = []

    for item in _walk(payload):
        if isinstance(item, dict):
            status = str(item.get("status") or item.get("task_status") or "").lower()
            if status in {"queued", "running"}:
                msg = _clean_text(item.get("message") or item.get("content") or item.get("text"))
                if msg:
                    status_messages.append(msg)
            if status in {"failed", "error"}:
                msg = _clean_text(item.get("error") or item.get("message") or item.get("content"))
                if msg:
                    return "", "failed", msg
            for key in ("content", "markdown", "document", "text", "result", "output"):
                value = item.get(key)
                decoded = _decode_text_maybe_json(value) if isinstance(value, str) else value
                if isinstance(decoded, dict):
                    nested_doc, nested_status, nested_msg = _extract_completed_document(decoded)
                    if nested_doc:
                        candidates.append((len(nested_doc), key, item.get("title", ""), nested_doc))
                    elif nested_status == "failed":
                        return "", nested_status, nested_msg
                    continue
                if isinstance(decoded, str):
                    text = decoded.strip()
                    if _looks_like_completed_document(text):
                        candidates.append((len(text), key, item.get("title", ""), text))
        elif isinstance(item, str):
            decoded = _decode_text_maybe_json(item)
            if isinstance(decoded, dict):
                nested_doc, nested_status, nested_msg = _extract_completed_document(decoded)
                if nested_doc:
                    candidates.append((len(nested_doc), "string-json", "", nested_doc))
                elif nested_status == "failed":
                    return "", nested_status, nested_msg
            elif _looks_like_completed_document(item):
                candidates.append((len(item), "string", "", item.strip()))

    if candidates:
        candidates.sort(key=lambda row: row[0], reverse=True)
        _, _, title, doc = candidates[0]
        return doc, "completed", str(title or "").strip()
    if status_messages:
        return "", "running", status_messages[-1]
    return "", "missing", "FlowerNet completed document was not found in the previous block output."


def _clean_markdown_document(markdown: str) -> str:
    text = str(markdown or "").replace("\\n", "\n").replace("\\t", "\t")
    text = re.sub(r"<a\s+id=[\"'][^\"']+[\"']\s*>\s*</a>\s*", "", text, flags=re.I)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    lines = text.splitlines()
    cleaned: list[str] = []
    last_heading_text = ""

    def _normalize_heading_text(value: str) -> str:
        value = re.sub(r"^\s*(?:[IVXLCDM]+\.|\d+\.|[A-Z]\.)\s+", "", value.strip(), flags=re.I)
        return re.sub(r"\s+", " ", value).strip().lower()

    skip_next_blank = False
    for raw_line in lines:
        line = raw_line.rstrip()
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading:
            last_heading_text = _normalize_heading_text(heading.group(2))
            cleaned.append(line)
            skip_next_blank = False
            continue
        plain = re.sub(r"[*_`]+", "", line).strip()
        normalized_plain = _normalize_heading_text(plain)
        if last_heading_text and normalized_plain == last_heading_text:
            skip_next_blank = True
            continue
        if skip_next_blank and not line.strip():
            skip_next_blank = False
            continue
        skip_next_blank = False
        cleaned.append(line)

    text = "\n".join(cleaned)
    # Collapse accidental full-document duplication if a block concatenated the same
    # completed document twice.
    midpoint = len(text) // 2
    if len(text) > 1000:
        left = text[:midpoint].strip()
        right = text[midpoint:].strip()
        if left and right and left == right:
            text = left
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _format_document_for_poffices(markdown: str, title_hint: str = "") -> str:
    text = _clean_markdown_document(markdown)
    lines = text.splitlines()
    if not lines:
        return ""

    title = title_hint.strip()
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip() or title
        lines = lines[1:]

    formatted: list[str] = []
    if title:
        formatted.extend(
            [
                f'<h1 style="text-align:center; font-size:30px; font-weight:800; line-height:1.25; margin:0 0 24px 0;">{html.escape(title)}</h1>',
                "",
            ]
        )

    in_table = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_table:
                formatted.append("")
                in_table = False
            else:
                formatted.append("")
            continue
        if stripped.startswith("## "):
            in_table = False
            formatted.append(f'<h2 style="font-size:22px; font-weight:750; line-height:1.35; margin:28px 0 12px 0;">{html.escape(stripped[3:].strip())}</h2>')
        elif stripped.startswith("### "):
            in_table = False
            formatted.append(f'<h3 style="font-size:18px; font-weight:700; line-height:1.35; margin:22px 0 10px 0;">{html.escape(stripped[4:].strip())}</h3>')
        elif stripped.startswith("#### "):
            in_table = False
            formatted.append(f'<h4 style="font-size:16px; font-weight:700; line-height:1.35; margin:18px 0 8px 0;">{html.escape(stripped[5:].strip())}</h4>')
        elif stripped.startswith("|") and stripped.endswith("|"):
            in_table = True
            formatted.append(stripped)
        elif re.match(r"^\[\d+\]\s+", stripped):
            in_table = False
            formatted.append(f'<p style="font-size:14px; line-height:1.55; margin:6px 0 6px 0;">{html.escape(stripped)}</p>')
        else:
            in_table = False
            formatted.append(f'<p style="font-size:15.5px; line-height:1.72; text-align:justify; margin:0 0 12px 0;">{html.escape(stripped)}</p>')

    rendered = "\n".join(formatted)
    rendered = re.sub(r"\n{3,}", "\n\n", rendered).strip()
    return rendered


def _render_final_document(payload: Any) -> PlainTextResponse:
    document, status, info = _extract_completed_document(payload)
    if status == "completed" and document:
        rendered = _format_document_for_poffices(document, title_hint=info)
        return PlainTextResponse(rendered, media_type="text/markdown; charset=utf-8")
    if status == "failed":
        return PlainTextResponse(info or "FlowerNet generation failed.", media_type="text/plain; charset=utf-8")
    if status == "running":
        return PlainTextResponse("FlowerNet generation is still running. Please continue polling.", media_type="text/plain; charset=utf-8")
    return PlainTextResponse(info, media_type="text/plain; charset=utf-8")


def _normalize(data: Any, fallback_status: str = "running") -> dict:
    if not isinstance(data, dict):
        data = {"success": True, "content": str(data or "")}
    status = data.get("status") or data.get("task_status") or fallback_status
    content = _content_from_result(data)
    success = bool(data.get("success", status not in {"failed", "error"}))
    body = dict(data)
    body.update({
        "success": success,
        "status": status,
        "task_status": status,
        "content": content,
        "text": content,
        "result": content,
        "output": content,
        "markdown": content,
        "document": content,
        "web_url": WEB_URL,
        "bridge_version": BRIDGE_VERSION,
    })
    return body


def _start_task(gen_req: dict) -> dict:
    data = _post_json(START_URL, gen_req, timeout=45)
    task_id = _extract_task_id(data)
    if task_id:
        TASK_BY_KEY[_task_key(gen_req)] = task_id
    return data


def _poll_task(task_id: str, payload: dict, wait_seconds: int = POLL_WAIT_SECONDS) -> dict:
    poll_payload = dict(payload or {})
    poll_payload.update({"task_id": task_id, "wait": True, "wait_seconds": wait_seconds})
    return _post_json(POLL_URL, poll_payload, timeout=max(30, wait_seconds + 10))


def _start_or_recover(gen_req: dict, old_task_id: str = "") -> dict:
    if old_task_id:
        STALE_TASK_IDS.add(old_task_id)
    data = _start_task(gen_req)
    if old_task_id and isinstance(data, dict):
        data.setdefault("warning", f"stale_or_unknown_task_id_recovered: {old_task_id}")
    return data


@app.post("/execute")
async def execute(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    text = _payload_text(payload)
    gen_req = _extract_generation_request(payload)
    key = _task_key(gen_req)
    task_id = _extract_task_id(payload)

    # Downstream block prompts often mention "FlowerNet Input Parser" while
    # describing their input contract, so the more specific blocks must win.
    if "FlowerNet Final Render Block" in text or "FlowerNet Document Display Block" in text:
        return _render_final_document(payload)

    if "FlowerNet Start Task Block" in text:
        return _normalize(_start_task(gen_req), fallback_status="queued")

    if "FlowerNet Input Parser" in text and "FlowerNet Poll Render Block" not in text:
        return _normalize(gen_req, fallback_status="completed")

    if not task_id and key in TASK_BY_KEY and TASK_BY_KEY[key] not in STALE_TASK_IDS:
        task_id = TASK_BY_KEY[key]

    if task_id:
        data = _poll_task(task_id, payload, wait_seconds=POLL_WAIT_SECONDS)
        if _contains_task_not_found(data):
            TASK_BY_KEY.pop(key, None)
            data = _start_or_recover(gen_req, old_task_id=task_id)
        elif isinstance(data, dict) and data.get("status") in {"completed", "success"}:
            TASK_BY_KEY.pop(key, None)
        return JSONResponse(_normalize(data, fallback_status=data.get("status", "running") if isinstance(data, dict) else "running"))

    data = _start_task(gen_req)
    return JSONResponse(_normalize(data, fallback_status=data.get("status", "queued") if isinstance(data, dict) else "queued"))


@app.get("/")
def root():
    return {"ok": True, "service": "flowernet-poffices-bridge", "mode": "recoverable", "bridge_version": BRIDGE_VERSION}
