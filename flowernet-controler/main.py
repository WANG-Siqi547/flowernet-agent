from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from controler import FlowerNetController
import os
import requests

app = FastAPI(title="FlowerNet Controller API")

# 初始化 Controller
controller = FlowerNetController()

outliner_url = None


def _get_outliner_session():
    s = requests.Session()
    s.trust_env = False
    return s


def _outliner_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    base = (os.getenv("OUTLINER_URL", "http://localhost:8003")).rstrip("/")
    timeout = int(os.getenv("OUTLINER_HTTP_TIMEOUT", "30"))
    resp = _get_outliner_session().post(f"{base}{path}", json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _fetch_subsection_outline_from_db(
    document_id: Optional[str],
    section_id: Optional[str],
    subsection_id: Optional[str],
) -> Optional[str]:
    """通过 outliner 服务从数据库中取 subsection 当前大纲。"""
    if not (document_id and section_id and subsection_id):
        return None
    try:
        body = _outliner_post(
            "/subsection-tracking/get",
            {"document_id": document_id, "section_id": section_id, "subsection_id": subsection_id},
        )
        tracking = body.get("tracking") or {}
        outline = (tracking.get("outline") or "").strip()
        if outline:
            return outline
    except Exception as e:
        print(f"⚠️  从 DB 读取 subsection outline 失败: {e}")
    try:
        body = _outliner_post(
            "/outline/get",
            {
                "document_id": document_id,
                "outline_type": "subsection",
                "section_id": section_id,
                "subsection_id": subsection_id,
            },
        )
        outline = (body.get("outline") or "").strip()
        if outline:
            return outline
    except Exception as e:
        print(f"⚠️  从 DB 读取 subsection outline（outline表）失败: {e}")
    return None


def _save_improved_outline_to_db(
    document_id: Optional[str],
    section_id: Optional[str],
    subsection_id: Optional[str],
    improved_outline: str,
    iteration_count: Optional[int] = None,
):
    """把改进后的大纲回写到 subsection_tracking 表。"""
    if not (document_id and section_id and subsection_id):
        return
    try:
        payload: Dict[str, Any] = {
            "document_id": document_id,
            "section_id": section_id,
            "subsection_id": subsection_id,
            "outline": improved_outline,
        }
        if iteration_count is not None:
            payload["iteration_count"] = iteration_count
        _outliner_post("/subsection-tracking/update", payload)
        print(f"✅ 改进大纲已写入 DB: {subsection_id}")
    except Exception as e:
        print(f"⚠️  回写改进大纲失败: {e}")


# ============ API 数据模型 ============

class RefinePromptRequest(BaseModel):
    """Prompt 修改请求"""
    old_prompt: str
    failed_draft: str
    feedback: Dict[str, Any]  # Verifier 返回的完整反馈
    outline: str
    history: List[str] = []
    iteration: int = 1


class AnalyzeFailureRequest(BaseModel):
    """失败模式分析请求"""
    failed_drafts: List[str]
    feedback_list: List[Dict[str, Any]]


class ImproveOutlineRequest(BaseModel):
    """改进大纲的请求（用于第三步）"""
    original_outline: str  # 原始大纲
    current_outline: str  # 当前大纲（可能已被改进过）
    failed_draft: str  # 验证失败的内容
    feedback: Dict[str, Any]  # Verifier 的反馈
    # 可选：如果传了这三个 ID，controller 会直接从数据库读取最新大纲并在成功后回写
    document_id: Optional[str] = None
    section_id: Optional[str] = None
    subsection_id: Optional[str] = None


# ============ API 端点 ============

@app.get("/")
def read_root():
    """根端点 - 检查服务状态"""
    return {
        "status": "online", 
        "message": "FlowerNet Controller is ready.", 
        "public_url": controller.public_url,
        "endpoints": {
            "/refine_prompt": "根据 Verifier 反馈修改 prompt",
            "/analyze_failures": "分析失败模式并给出建议"
        }
    }


@app.post("/refine_prompt")
async def refine_prompt(req: RefinePromptRequest):
    """
    根据 Verifier 反馈修改 Prompt
    
    输入：
    - old_prompt: 原始 prompt
    - failed_draft: 验证失败的 draft
    - feedback: Verifier 的验证反馈（包含 relevancy_index 和 redundancy_index）
    - outline: 段落大纲
    - history: 历史内容列表
    - iteration: 当前迭代次数
    
    输出：优化后的新 prompt
    """
    try:
        new_prompt = controller.refine_prompt(
            old_prompt=req.old_prompt,
            failed_draft=req.failed_draft,
            feedback=req.feedback,
            outline=req.outline,
            history=req.history,
            iteration=req.iteration
        )
        return {
            "success": True,
            "prompt": new_prompt
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/analyze_failures")
async def analyze_failures(req: AnalyzeFailureRequest):
    """
    分析多次失败的模式
    
    输入：
    - failed_drafts: 所有失败的 draft 列表
    - feedback_list: 对应的验证反馈列表
    
    输出：失败模式分析结果
    """
    try:
        analysis = controller.analyze_failure_patterns(
            failed_drafts=req.failed_drafts,
            feedback_list=req.feedback_list
        )
        return {
            "success": True,
            "analysis": analysis
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/improve-outline")
async def improve_outline(req: ImproveOutlineRequest):
    """
    根据验证反馈改进大纲（第三步中使用）

    如果传入了 document_id / section_id / subsection_id，
    则从数据库读取当前最新大纲作为改进起点，改进后写回数据库。
    """
    try:
        rel_score = req.feedback.get("relevancy_index", 0)
        red_score = req.feedback.get("redundancy_index", 0)
        feedback_text = req.feedback.get("feedback", "")
        iteration = req.feedback.get("iteration", 1)

        # 优先从 DB 里取最新大纲，这样 controller 能感知之前已经改过的版本
        db_outline = _fetch_subsection_outline_from_db(
            document_id=req.document_id,
            section_id=req.section_id,
            subsection_id=req.subsection_id,
        )
        working_outline = db_outline if db_outline else req.current_outline
        original_outline = req.original_outline or working_outline

        improvement_prompt = f"""
你是一个文档写作指导专家。

原始大纲要求：
{original_outline}

当前大纲：
{working_outline}

生成失败的内容（前500字）：
{req.failed_draft[:500]}

验证反馈：
- 相关性分数: {rel_score:.4f} (目标 >= 0.6)
- 冗余度分数: {red_score:.4f} (目标 <= 0.7)
- 反馈: {feedback_text}

请根据以上信息改进大纲，使生成的内容能通过验证。改进建议：
1. 如果相关性不足（{rel_score:.4f} < 0.6）：大纲要更明确具体，强调核心主题，列出必须涵盖的关键点
2. 如果冗余度过高（{red_score:.4f} > 0.7）：大纲要提示写全新的角度和信息，明确指出禁止重复的内容类型
3. 如果两者都需要改进：综合上述两点

请直接输出改进后的详细大纲文本，不要添加任何前言或标签。
"""

        import requests as _req
        outliner_base = (os.getenv("OUTLINER_URL", "http://localhost:8003")).rstrip("/")
        generator_base = (os.getenv("GENERATOR_URL", "http://localhost:8002")).rstrip("/")

        improved_outline = None

        # 尝试通过 generator /generate 接口让 LLM 改进大纲
        try:
            sess = _req.Session()
            sess.trust_env = False
            resp = sess.post(
                f"{generator_base}/generate",
                json={"prompt": improvement_prompt, "max_tokens": 1000},
                timeout=120,
            )
            if resp.status_code == 200:
                body = resp.json()
                if body.get("success") and body.get("draft"):
                    improved_outline = str(body["draft"]).strip()
        except Exception as e:
            print(f"⚠️  LLM 改进大纲失败（会使用规则降级）: {e}")

        if not improved_outline:
            improved_outline = f"{original_outline}\n\n【第 {iteration} 次改进建议】\n"
            if rel_score < 0.6:
                improved_outline += "- 增加与主题高度相关的细节内容\n"
                improved_outline += "- 在开头明确表述核心观点\n"
            if red_score > 0.7:
                improved_outline += "- 避免与前文重复，强调新的视角和信息\n"
                improved_outline += "- 补充前文未覆盖的领域\n"
            if "偏离主题" in feedback_text or rel_score < 0.5:
                improved_outline += "- 严格遵循主题，每句话都要与主题直接相关\n"

        # 改进成功后写回数据库
        _save_improved_outline_to_db(
            document_id=req.document_id,
            section_id=req.section_id,
            subsection_id=req.subsection_id,
            improved_outline=improved_outline,
            iteration_count=iteration,
        )

        return {
            "success": True,
            "improved_outline": improved_outline,
            "source": "llm" if improved_outline and "\n【第" not in improved_outline else "rule",
            "recommendations": [
                f"相关性分数: {rel_score:.4f}",
                f"冗余度分数: {red_score:.4f}",
                f"反馈: {feedback_text}"
            ]
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": str(e)
        }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv('PORT', 8001))
    print(f"\n🚀 FlowerNet Controller 启动在 http://0.0.0.0:{port}")
    print(f"📖 API 文档: http://localhost:{port}/docs")
    uvicorn.run(app, host="0.0.0.0", port=port)