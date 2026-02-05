"""
FlowerNet Generator API
提供 HTTP 接口给其他模块调用生成功能
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import uvicorn
import os

from generator import FlowerNetGenerator, FlowerNetOrchestrator

# ============ 数据模型 ============

class GenerateRequest(BaseModel):
    """生成单个 draft 的请求"""
    prompt: str
    max_tokens: int = 2000


class GenerateWithContextRequest(BaseModel):
    """带上下文生成的请求"""
    prompt: str
    outline: str
    history: List[str] = []
    max_tokens: int = 2000


class GenerateSectionRequest(BaseModel):
    """生成一个段落（带验证循环）的请求"""
    outline: str
    initial_prompt: str
    history: List[str] = []
    rel_threshold: float = 0.6
    red_threshold: float = 0.7


class GenerateDocumentRequest(BaseModel):
    """生成完整文档的请求"""
    title: str
    outline_list: List[str]
    system_prompt: str = ""
    rel_threshold: float = 0.6
    red_threshold: float = 0.7


# ============ 全局对象 ============

app = FastAPI(title="FlowerNet Generator API")

# 初始化生成器
generator = None

def init_generator(provider: str = "gemini", model: str = None):
    """初始化生成器（支持 Gemini 和 Claude）"""
    global generator
    
    try:
        if provider == "gemini":
            model = model or "models/gemini-2.5-flash"
            generator = FlowerNetGenerator(provider="gemini", model=model)
        elif provider == "claude":
            model = model or "claude-3-5-sonnet-20241022"
            generator = FlowerNetGenerator(provider="claude", model=model)
        else:
            raise ValueError(f"不支持的提供商: {provider}")
        
        print(f"✅ Generator 已初始化 ({provider})")
        return generator
    except Exception as e:
        print(f"❌ Generator 初始化失败: {e}")
        return None

# 初始化编排器（用于调用其他服务）
orchestrator = None

def get_orchestrator():
    """获取或初始化编排器"""
    global orchestrator
    if orchestrator is None:
        generator_url = os.getenv('GENERATOR_URL', 'http://localhost:8002')
        verifier_url = os.getenv('VERIFIER_URL', 'http://localhost:8000')
        controller_url = os.getenv('CONTROLLER_URL', 'http://localhost:8001')
        max_iterations = int(os.getenv('MAX_ITERATIONS', '5'))
        
        orchestrator = FlowerNetOrchestrator(
            generator_url=generator_url,
            verifier_url=verifier_url,
            controller_url=controller_url,
            max_iterations=max_iterations
        )
    return orchestrator


# ============ API 端点 ============

@app.get("/")
def read_root():
    """根端点 - 检查服务状态"""
    return {
        "status": "online",
        "message": "FlowerNet Generator API is ready.",
        "endpoints": {
            "/generate": "Simple draft generation",
            "/generate_with_context": "Draft generation with context",
            "/generate_section": "Generate section with verification loop",
            "/generate_document": "Generate complete document"
        }
    }


@app.post("/generate")
async def generate(request: GenerateRequest):
    """
    简单生成：只根据 prompt 生成 draft，不进行验证
    """
    if generator is None:
        raise HTTPException(status_code=500, detail="Generator not initialized")
    
    try:
        result = generator.generate_draft(
            prompt=request.prompt,
            max_tokens=request.max_tokens
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate_with_context")
async def generate_with_context(request: GenerateWithContextRequest):
    """
    带上下文的生成：考虑大纲和历史内容
    """
    if generator is None:
        raise HTTPException(status_code=500, detail="Generator not initialized")
    
    try:
        result = generator.generate_with_context(
            prompt=request.prompt,
            outline=request.outline,
            history=request.history,
            max_tokens=request.max_tokens
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate_section")
async def generate_section(request: GenerateSectionRequest):
    """
    生成一个段落（完整的生成-验证-修改循环）
    
    流程：
    1. 调用 Generator 生成 draft
    2. 调用 Verifier 验证
    3. 如果验证失败，调用 Controller 修改 prompt
    4. 重复直到通过或达到最大迭代次数
    """
    if generator is None:
        raise HTTPException(status_code=500, detail="Generator not initialized")
    
    try:
        orch = get_orchestrator()
        result = orch.generate_section(
            outline=request.outline,
            initial_prompt=request.initial_prompt,
            history=request.history,
            rel_threshold=request.rel_threshold,
            red_threshold=request.red_threshold
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate_document")
async def generate_document(request: GenerateDocumentRequest):
    """
    生成完整文档（多个段落）
    
    每个段落都会经过生成-验证-修改循环
    """
    if generator is None:
        raise HTTPException(status_code=500, detail="Generator not initialized")
    
    try:
        orch = get_orchestrator()
        result = orch.generate_document(
            title=request.title,
            outline_list=request.outline_list,
            system_prompt=request.system_prompt,
            rel_threshold=request.rel_threshold,
            red_threshold=request.red_threshold
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ 本地测试 ============

if __name__ == "__main__":
    import sys
    
    # 可以通过命令行参数指定端口和提供商
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8002
    provider = sys.argv[2] if len(sys.argv) > 2 else "gemini"  # 默认使用 Gemini
    model = sys.argv[3] if len(sys.argv) > 3 else None
    
    # 初始化生成器
    init_generator(provider=provider, model=model)
    
    print(f"\n🚀 FlowerNet Generator 启动在 http://0.0.0.0:{port}")
    print(f"📖 API 文档: http://localhost:{port}/docs")
    print(f"🤖 使用 LLM: {provider} ({model or 'default'})")
    
    uvicorn.run(app, host="0.0.0.0", port=port)
