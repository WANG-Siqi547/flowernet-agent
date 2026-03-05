from datetime import datetime
from io import BytesIO
import os
from typing import Any, Dict, List

import requests
from docx import Document
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field


OUTLINER_URL = os.getenv("OUTLINER_URL", "http://localhost:8003")
GENERATOR_URL = os.getenv("GENERATOR_URL", "http://localhost:8002")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "300"))


class GenerateDocRequest(BaseModel):
    topic: str = Field(..., min_length=2, description="文档主题")
    chapter_count: int = Field(default=5, ge=1, le=10)
    subsection_count: int = Field(default=3, ge=1, le=8)
    user_background: str = Field(default="普通读者")
    extra_requirements: str = Field(default="")
    rel_threshold: float = Field(default=0.6, ge=0, le=1)
    red_threshold: float = Field(default=0.7, ge=0, le=1)


class DownloadDocxRequest(BaseModel):
    title: str
    content: str


app = FastAPI(title="FlowerNet Web UI", version="1.0.0")


def post_json(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"下游服务请求失败: {url}, 错误: {exc}")


def build_requirements_text(req: GenerateDocRequest) -> str:
    base = (
        f"请帮我生成一篇关于“{req.topic}”的高质量长文档，"
        f"总共需要 {req.chapter_count} 个章节，"
        f"每个章节包含 {req.subsection_count} 个子章节。"
    )
    if req.extra_requirements.strip():
        return f"{base}\n\n附加要求：{req.extra_requirements.strip()}"
    return base


def build_markdown_document(title: str, structure: Dict[str, Any], history: List[Dict[str, Any]]) -> str:
    content_map: Dict[str, str] = {}
    for item in history:
        key = f"{item.get('section_id', '')}::{item.get('subsection_id', '')}"
        content_map[key] = item.get("content", "")

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


@app.post("/api/generate")
def generate_document(req: GenerateDocRequest) -> Dict[str, Any]:
    document_id = f"web_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    user_requirements = build_requirements_text(req)

    outline_payload = {
        "user_background": req.user_background,
        "user_requirements": user_requirements,
        "max_sections": req.chapter_count,
        "max_subsections_per_section": req.subsection_count,
    }
    outline_resp = post_json(f"{OUTLINER_URL}/generate-outline", outline_payload)

    if not outline_resp.get("success"):
        raise HTTPException(status_code=500, detail=f"大纲生成失败: {outline_resp}")

    title = outline_resp.get("document_title") or f"{req.topic} 文档"
    structure = outline_resp.get("structure", {})
    content_prompts = outline_resp.get("content_prompts", [])

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
    gen_resp = post_json(f"{GENERATOR_URL}/generate_document", generate_payload)

    if not gen_resp.get("success"):
        raise HTTPException(status_code=500, detail=f"文档生成失败: {gen_resp}")

    history_resp = post_json(f"{OUTLINER_URL}/history/get", {"document_id": document_id})
    history_items = history_resp.get("history", []) if history_resp.get("success") else []

    markdown_content = build_markdown_document(title, structure, history_items)

    return {
        "success": True,
        "document_id": document_id,
        "title": title,
        "content": markdown_content,
        "stats": {
            "passed_subsections": gen_resp.get("passed_subsections", 0),
            "failed_subsections": len(gen_resp.get("failed_subsections", [])),
            "total_iterations": gen_resp.get("total_iterations", 0),
            "generation_time": gen_resp.get("generation_time", ""),
        },
    }


@app.post("/api/download-docx")
def download_docx(req: DownloadDocxRequest):
    stream = markdown_to_docx(req.title, req.content)
    filename = f"{req.title[:40].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"

    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8010"))
    uvicorn.run(app, host="0.0.0.0", port=port)
