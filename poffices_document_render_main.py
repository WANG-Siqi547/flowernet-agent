from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import html
import json
import re
from typing import Any

app = FastAPI()

RENDER_VERSION = "2026-06-16-flowernet-render-v1"


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


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


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
    candidates: list[tuple[int, str, str]] = []
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
                        candidates.append((len(nested_doc), str(item.get("title") or ""), nested_doc))
                    elif nested_status == "failed":
                        return "", nested_status, nested_msg
                    continue
                if isinstance(decoded, str):
                    text = decoded.strip()
                    if _looks_like_completed_document(text):
                        candidates.append((len(text), str(item.get("title") or ""), text))
        elif isinstance(item, str):
            decoded = _decode_text_maybe_json(item)
            if isinstance(decoded, dict):
                nested_doc, nested_status, nested_msg = _extract_completed_document(decoded)
                if nested_doc:
                    candidates.append((len(nested_doc), "", nested_doc))
                elif nested_status == "failed":
                    return "", nested_status, nested_msg
            elif _looks_like_completed_document(item):
                candidates.append((len(item), "", item.strip()))

    if candidates:
        candidates.sort(key=lambda row: row[0], reverse=True)
        _, title, doc = candidates[0]
        return doc, "completed", title.strip()
    if status_messages:
        return "", "running", status_messages[-1]
    return "", "missing", "FlowerNet completed document was not found in the previous block output."


def _clean_markdown_document(markdown: str) -> str:
    text = str(markdown or "").replace("\\n", "\n").replace("\\t", "\t")
    text = re.sub(r"<a\s+id=[\"'][^\"']+[\"']\s*>\s*</a>\s*", "", text, flags=re.I)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    def normalize_heading_text(value: str) -> str:
        value = re.sub(r"^\s*(?:[IVXLCDM]+\.|\d+\.|[A-Z]\.)\s+", "", value.strip(), flags=re.I)
        return re.sub(r"\s+", " ", value).strip().lower()

    cleaned: list[str] = []
    last_heading_text = ""
    skip_next_blank = False
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading:
            last_heading_text = normalize_heading_text(heading.group(2))
            cleaned.append(line)
            skip_next_blank = False
            continue
        plain = re.sub(r"[*_`]+", "", line).strip()
        if last_heading_text and normalize_heading_text(plain) == last_heading_text:
            skip_next_blank = True
            continue
        if skip_next_blank and not line.strip():
            skip_next_blank = False
            continue
        skip_next_blank = False
        cleaned.append(line)

    text = "\n".join(cleaned)
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
            formatted.append("")
            if in_table:
                in_table = False
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
            formatted.append(f'<p style="font-size:14px; line-height:1.55; margin:6px 0;">{html.escape(stripped)}</p>')
        else:
            in_table = False
            formatted.append(f'<p style="font-size:15.5px; line-height:1.72; text-align:justify; margin:0 0 12px 0;">{html.escape(stripped)}</p>')

    return re.sub(r"\n{3,}", "\n\n", "\n".join(formatted)).strip()


@app.post("/execute")
async def execute(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    document, status, info = _extract_completed_document(payload)
    if status == "completed" and document:
        rendered = _format_document_for_poffices(document, title_hint=info)
        return PlainTextResponse(rendered, media_type="text/markdown; charset=utf-8")
    if status == "failed":
        return PlainTextResponse(info or "FlowerNet generation failed.", media_type="text/plain; charset=utf-8")
    if status == "running":
        return PlainTextResponse("FlowerNet generation is still running. Please continue polling.", media_type="text/plain; charset=utf-8")
    return PlainTextResponse(info, media_type="text/plain; charset=utf-8")


@app.get("/")
def root():
    return {"ok": True, "service": "flowernet-poffices-document-render", "version": RENDER_VERSION}
