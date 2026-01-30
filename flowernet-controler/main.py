from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from controler import FlowerNetController
import os

app = FastAPI(title="FlowerNet Controller API")

# 初始化 Controller
controller = FlowerNetController()

# ============ API 数据模型 ============

class InitialPromptRequest(BaseModel):
    outline: str
    history: List[str] = []

class RefinePromptRequest(BaseModel):
    old_prompt: str
    failed_draft: str
    feedback: Dict[str, Any]  # Verifier 返回的完整反馈
    outline: str
    history: List[str] = []

# ============ API 端点 ============

@app.get("/")
def read_root():
    return {
        "status": "online", 
        "message": "FlowerNet Controller is ready.", 
        "public_url": controller.public_url,
        "endpoints": {
            "/initial_prompt": "生成初始 prompt",
            "/refine_prompt": "根据反馈修改 prompt"
        }
    }

@app.post("/initial_prompt")
async def create_initial_prompt(req: InitialPromptRequest):
    """
    生成初始 Prompt
    输入：outline（大纲）+ history（历史内容）
    输出：初始 prompt
    """
    try:
        prompt = controller.build_initial_prompt(
            outline=req.outline,
            history=req.history
        )
        return {
            "success": True,
            "prompt": prompt
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@app.post("/refine_prompt")
async def refine_prompt(req: RefinePromptRequest):
    """
    根据 Verifier 反馈修改 Prompt
    输入：old_prompt + failed_draft + feedback + outline + history
    输出：新的 prompt
    """
    try:
        new_prompt = controller.refine_prompt(
            old_prompt=req.old_prompt,
            failed_draft=req.failed_draft,
            feedback=req.feedback,
            outline=req.outline,
            history=req.history
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)