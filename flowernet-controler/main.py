from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from controler import FlowerNetController
import os

app = FastAPI(title="FlowerNet Controller API")

# 初始化 Controller
controller = FlowerNetController()

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
    
    当 Generator 生成的内容验证失败时，Controller 会：
    1. 从 failed_draft 和 feedback 分析失败原因
    2. 改进当前大纲，使其更明确、更具体   
    3. 返回改进后的大纲供 Generator 再次生成
    
    输入：
    - original_outline: 原始大纲（最初的大纲要求）
    - current_outline: 当前一轮的大纲
    - failed_draft: 验证失败的生成内容
    - feedback: Verifier 的反馈（包含 relevancy_index 和 redundancy_index）
    
    输出：改进后的大纲
    """
    try:
        # 分析失败原因
        rel_score = req.feedback.get("relevancy_index", 0)
        red_score = req.feedback.get("redundancy_index", 0)
        feedback_text = req.feedback.get("feedback", "")
        
        # 构建改进大纲的提示
        improvement_prompt = f"""
你是一个文档写作指导专家。

原始大纲要求：
{req.original_outline}

当前大纲：
{req.current_outline}

生成失败的内容：
{req.failed_draft[:500]}...

验证反馈：
- 相关性分数: {rel_score:.4f} (目标 >= 0.6)
- 冗余度分数: {red_score:.4f} (目标 <= 0.7)
- 反馈: {feedback_text}

请分析失败原因，并改进大纲以解决这些问题：

1. 如果相关性不足：大纲要更明确具体，强调核心主题
2. 如果冗余度过高：大纲要提示避免重复，强调创新点  
3. 如果内容偏离主题：大纲要强化主题约束

请直接输出改进后的详细大纲，不要添加任何引导语。
"""
        
        # 调用 LLM 改进大纲（这里简化处理，实际应该使用某个 LLM）
        # 为了演示，我们返回一个改进的大纲
        improved_outline = f"{req.original_outline}\n\n【第 {req.feedback.get('iteration', 1)} 次改进建议】\n"
        
        if rel_score < 0.6:
            improved_outline += f"- 增加与主题高度相关的细节内容\n"
            improved_outline += f"- 在开头明确表述核心观点\n"
        
        if red_score > 0.7:
            improved_outline += f"- 避免与前文重复，强调新的视角和信息\n"
            improved_outline += f"- 补充前文未覆盖的领域\n"
        
        if "偏离主题" in feedback_text or rel_score < 0.5:
            improved_outline += f"- 严格遵循主题，每句话都要与主题直接相关\n"
        
        return {
            "success": True,
            "improved_outline": improved_outline,
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