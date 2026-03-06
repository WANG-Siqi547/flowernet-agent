from datetime import datetime
from io import BytesIO
import os
import threading
import time
from typing import Any, Dict, List, Generator
from urllib.parse import quote
import json

import requests
from docx import Document
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field


OUTLINER_URL = os.getenv("OUTLINER_URL", "http://localhost:8003")
GENERATOR_URL = os.getenv("GENERATOR_URL", "http://localhost:8002")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "3600"))  # 1小时超时，适配Ollama耗时生成


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
        
        document_id = f"web_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        user_requirements = build_requirements_text(req)

        # 第1步：生成大纲
        outline_payload = {
            "user_background": req.user_background,
            "user_requirements": user_requirements,
            "max_sections": req.chapter_count,
            "max_subsections_per_section": req.subsection_count,
        }
        
        try:
            outline_http_resp = requests.post(
                f"{OUTLINER_URL}/generate-outline",
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

        title = outline_resp.get("document_title") or f"{req.topic} 文档"
        structure, content_prompts, source_total, total_subsections = ensure_exact_structure_and_prompts(
            title=title,
            structure=outline_resp.get("structure", {}),
            content_prompts=outline_resp.get("content_prompts", []),
            chapter_count=req.chapter_count,
            subsection_count=req.subsection_count,
        )

        if source_total != total_subsections:
            msg = json.dumps({
                'type': 'progress',
                'message': f'⚠️ 大纲小节数为 {source_total}，已自动修正为 {total_subsections}（严格按用户设置）'
            })
            yield f"data: {msg}\n\n"

        # 第2步：生成文档内容（异步启动）
        msg = json.dumps({'type': 'progress', 'message': f'🚀 开始生成内容（共{total_subsections}个小节）...'})
        yield f"data: {msg}\n\n"

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

        markdown_content = build_markdown_document(title, structure, history_items)
        
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
    rel_threshold: float = 0.6,
    red_threshold: float = 0.7,
):
    """SSE 端点：实时推送文档生成进度"""
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
    structure, content_prompts, _, _ = ensure_exact_structure_and_prompts(
        title=title,
        structure=outline_resp.get("structure", {}),
        content_prompts=outline_resp.get("content_prompts", []),
        chapter_count=req.chapter_count,
        subsection_count=req.subsection_count,
    )

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

    expected_subsections = req.chapter_count * req.subsection_count
    passed = gen_resp.get("passed_subsections", 0)
    failed = len(gen_resp.get("failed_subsections", []))
    forced = len(gen_resp.get("forced_subsections", []))
    if passed < expected_subsections:
        raise HTTPException(
            status_code=500,
            detail=f"文档生成未达到目标小节数: 通过 {passed}/{expected_subsections}, 失败 {failed}"
        )

    history_resp = post_json(f"{OUTLINER_URL}/history/get", {"document_id": document_id})
    history_items = history_resp.get("history", []) if history_resp.get("success") else []

    markdown_content = build_markdown_document(title, structure, history_items)

    return {
        "success": True,
        "document_id": document_id,
        "title": title,
        "content": markdown_content,
        "stats": {
            "expected_subsections": expected_subsections,
            "passed_subsections": gen_resp.get("passed_subsections", 0),
            "failed_subsections": len(gen_resp.get("failed_subsections", [])),
            "forced_subsections": forced,
            "total_iterations": gen_resp.get("total_iterations", 0),
            "generation_time": gen_resp.get("generation_time", ""),
        },
    }


@app.post("/api/download-docx")
def download_docx(req: DownloadDocxRequest):
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


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8010"))
    uvicorn.run(app, host="0.0.0.0", port=port)
