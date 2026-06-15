from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import json
import re
import time
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

TASK_BY_KEY: dict[str, str] = {}
STALE_TASK_IDS: set[str] = set()

BLOCK_PROMPT_MARKERS = (
    "FlowerNet Input Parser",
    "FlowerNet Start Task Block",
    "FlowerNet Poll Render Block",
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
    return {"ok": True, "service": "flowernet-poffices-bridge", "mode": "recoverable"}
