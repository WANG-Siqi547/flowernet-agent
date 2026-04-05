"""
FlowerNet Generator API
提供 HTTP 接口给其他模块调用生成功能
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import uvicorn
import os
import sys
import threading
import importlib.util
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root in sys.path:
    sys.path.remove(project_root)
sys.path.insert(0, project_root)

from generator import FlowerNetGenerator, FlowerNetOrchestrator

_local_orchestrator_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flowernet_orchestrator_impl.py")
_local_orchestrator_spec = importlib.util.spec_from_file_location("_flowernet_orchestrator_impl_local", _local_orchestrator_path)
_local_orchestrator_module = importlib.util.module_from_spec(_local_orchestrator_spec)
_local_orchestrator_spec.loader.exec_module(_local_orchestrator_module)
DocumentGenerationOrchestrator = _local_orchestrator_module.DocumentGenerationOrchestrator

# 导入 HistoryManager
try:
    from history_store import HistoryManager
    from remote_history_client import RemoteHistoryManager
    HISTORY_AVAILABLE = True
except ImportError:
    print("⚠️  警告: 无法导入 HistoryManager，数据库存储功能将不可用")
    HISTORY_AVAILABLE = False

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
    """生成一个subsection（带验证循环）的请求"""
    outline: str
    initial_prompt: str
    document_id: Optional[str] = None
    section_id: Optional[str] = None
    subsection_id: Optional[str] = None
    history: List[str] = []
    rel_threshold: float = 0.90
    red_threshold: float = 0.42


class GenerateDocumentRequest(BaseModel):
    """生成完整文档的请求（采用新的完整流程）"""
    document_id: str
    title: str
    structure: Dict[str, Any]  # 从 Outliner 返回的结构
    content_prompts: List[Dict[str, Any]]  # 从 Outliner 返回的 content_prompts
    user_background: str
    user_requirements: str
    rel_threshold: float = 0.90
    red_threshold: float = 0.42


# ============ 全局对象 ============

app = FastAPI(title="FlowerNet Generator API")

# 初始化生成器
generator = None
_provider = "ollama"
_model = None
_init_error = None

def init_generator(provider: str = "azure", model: str = None):
    """初始化生成器（支持链式降级，如 Azure -> Ollama）"""
    global generator, _provider, _model, _init_error
    
    try:
        _provider = provider
        _model = model
        
        generator = FlowerNetGenerator(provider=provider, model=model)
        
        _init_error = None
        print(f"✅ Generator 已初始化 ({provider})")
        return generator
    except Exception as e:
        _init_error = str(e)
        print(f"❌ Generator 初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return None

# 初始化编排器（用于调用其他服务）
orchestrator = None
document_orchestrator = None
history_manager = None
document_generation_lock = threading.Lock()
generator_init_lock = threading.Lock()


def ensure_generator_initialized():
    """按需初始化 Generator，避免启动阶段阻塞健康检查。"""
    global generator
    if generator is not None:
        return generator

    with generator_init_lock:
        if generator is not None:
            return generator
        return init_generator(
            provider=os.getenv("GENERATOR_PROVIDER", "azure"),
            model=os.getenv("GENERATOR_MODEL", None),
        )


def get_history_manager(outliner_url: str):
    """优先通过 outliner 服务访问共享数据库，避免多服务各自持有本地 SQLite。"""
    global history_manager

    if history_manager is not None:
        return history_manager

    if not HISTORY_AVAILABLE:
        return None

    use_remote_history = os.getenv('USE_REMOTE_HISTORY', 'true').lower() == 'true'
    if use_remote_history and outliner_url:
        history_manager = RemoteHistoryManager(
            base_url=outliner_url,
            timeout=int(os.getenv('HISTORY_HTTP_TIMEOUT', '60')),
        )
        print(f"✅ HistoryManager 已初始化 (Remote via {outliner_url})")
        return history_manager

    use_db = os.getenv('USE_DATABASE', 'true').lower() == 'true'
    raw_db_path = os.getenv('DATABASE_PATH', 'flowernet_history.db')
    if os.path.isabs(raw_db_path):
        db_path = raw_db_path
    else:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        db_path = os.path.join(project_root, raw_db_path)
    history_manager = HistoryManager(use_database=use_db, db_path=db_path)
    print(f"✅ HistoryManager 已初始化 ({'数据库模式' if use_db else '内存模式'})")
    return history_manager


def get_orchestrator():
    """获取或初始化编排器（带共享 HistoryManager）"""
    global orchestrator, history_manager
    if orchestrator is None:
        generator_url = os.getenv('GENERATOR_URL', 'http://localhost:8002')
        verifier_url = os.getenv('VERIFIER_URL', 'http://localhost:8000')
        controller_url = os.getenv('CONTROLLER_URL', 'http://localhost:8001')
        outliner_url = os.getenv('OUTLINER_URL', 'http://localhost:8003')
        max_iterations = int(os.getenv('MAX_ITERATIONS', '5'))
        history_manager = get_history_manager(outliner_url)
        
        orchestrator = FlowerNetOrchestrator(
            generator_url=generator_url,
            verifier_url=verifier_url,
            controller_url=controller_url,
            max_iterations=max_iterations,
            history_manager=history_manager
        )
        
        # 为 orchestrator 注入本地 generator 实例，避免 HTTP 递归调用
        # 使用与主 Generator 相同的 provider chain 和模型配置
        provider = os.getenv('GENERATOR_PROVIDER', 'azure')
        model = os.getenv('GENERATOR_MODEL', None)
        local_gen = FlowerNetGenerator(provider=provider, model=model)
        orchestrator._local_generator = local_gen
        print(f"✅ Orchestrator 已配置本地 Generator（provider={provider}, model={model}）")
        
    return orchestrator


def get_document_generation_orchestrator():
    """获取或初始化新的文档生成编排器"""
    global document_orchestrator, history_manager
    
    if document_orchestrator is None:
        generator_url = os.getenv('GENERATOR_URL', 'http://localhost:8002')
        verifier_url = os.getenv('VERIFIER_URL', 'http://localhost:8000')
        controller_url = os.getenv('CONTROLLER_URL', 'http://localhost:8001')
        outliner_url = os.getenv('OUTLINER_URL', 'http://localhost:8003')
        max_iterations = int(os.getenv('MAX_ITERATIONS', '5'))
        history_manager = get_history_manager(outliner_url)
        
        document_orchestrator = DocumentGenerationOrchestrator(
            generator_url=generator_url,
            verifier_url=verifier_url,
            controller_url=controller_url,
            outliner_url=outliner_url,
            max_iterations=max_iterations,
            history_manager=history_manager
        )
        
        # 绑定本地Generator实例，避免HTTP自调用死锁
        if generator is not None:
            document_orchestrator.set_local_generator(generator)
        
        print(f"✅ DocumentGenerationOrchestrator 已初始化")
    
    return document_orchestrator


# 启动事件：在应用启动时初始化 Generator
@app.on_event("startup")
async def startup_event():
    """应用启动时初始化 Generator"""
    provider = os.getenv('GENERATOR_PROVIDER', 'azure')
    model = os.getenv('GENERATOR_MODEL', None)
    ollama_url = os.getenv('OLLAMA_URL', 'http://localhost:11434')
    preload_on_startup = os.getenv('GENERATOR_PRELOAD_ON_STARTUP', 'false').lower() == 'true'
    
    msg = f"\n⚡ 启动事件触发\n"
    msg += f"📦 环境变量:\n"
    msg += f"   - GENERATOR_PROVIDER: {provider}\n"
    msg += f"   - GENERATOR_MODEL: {model}\n"
    msg += f"   - OLLAMA_URL: {ollama_url}\n"
    msg += f"   - GENERATOR_PRELOAD_ON_STARTUP: {preload_on_startup}\n"
    
    print(msg)
    sys.stdout.flush()
    
    if preload_on_startup:
        result = ensure_generator_initialized()
        if result is None:
            print("⚠️  警告: Generator 预加载失败，后续会在请求时重试初始化")
        else:
            print(f"✅ Generator 预加载成功: {type(result).__name__}")
    else:
        print("ℹ️  启用惰性初始化：启动阶段不阻塞，首次请求时再初始化 Generator")
    
    sys.stdout.flush()


# 调试端点：检查 Generator 状态
@app.get("/debug")
async def debug():
    """调试信息"""
    return {
        "status": "Generator initialized" if generator else "Generator NOT initialized",
        "generator": {
            "is_none": generator is None,
            "type": str(type(generator)) if generator else "None"
        },
        "init_error": _init_error,
        "environment": {
            "GENERATOR_PROVIDER": os.getenv('GENERATOR_PROVIDER', 'NOT SET'),
            "GENERATOR_MODEL": os.getenv('GENERATOR_MODEL', 'NOT SET'),
            "OLLAMA_URL": os.getenv('OLLAMA_URL', 'NOT SET'),
            "azure_key_present": bool(os.getenv('GENERATOR_AZURE_API_KEY') or os.getenv('AZURE_OPENAI_API_KEY')),
            "azure_api_base_present": bool(os.getenv('GENERATOR_AZURE_API_BASE') or os.getenv('AZURE_OPENAI_API_BASE')),
            "azure_deployment_present": bool(os.getenv('GENERATOR_AZURE_DEPLOYMENT_NAME') or os.getenv('AZURE_OPENAI_DEPLOYMENT_NAME'))
        }
    }


# 健康检查端点
@app.get("/health")
async def health():
    """服务健康检查"""
    return {
        "status": "healthy" if generator else "degraded",
        "generator_initialized": generator is not None
    }


@app.head("/health")
async def health_head():
    """Render 可能使用 HEAD 做健康检查，显式返回 200。"""
    return


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


@app.head("/")
def read_root_head():
    """Render 内部探针兼容：HEAD / 返回 200。"""
    return


@app.post("/generate")
def generate(request: GenerateRequest):
    """
    简单生成：只根据 prompt 生成 draft，不进行验证
    """
    if generator is None:
        ensure_generator_initialized()
    if generator is None:
        raise HTTPException(status_code=500, detail=f"Generator not initialized: {_init_error or 'unknown error'}")
    
    try:
        result = generator.generate_draft(
            prompt=request.prompt,
            max_tokens=request.max_tokens
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate_with_context")
def generate_with_context(request: GenerateWithContextRequest):
    """
    带上下文的生成：考虑大纲和历史内容
    """
    if generator is None:
        ensure_generator_initialized()
    if generator is None:
        raise HTTPException(status_code=500, detail=f"Generator not initialized: {_init_error or 'unknown error'}")
    
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
def generate_section(request: GenerateSectionRequest):
    """
    生成一个subsection（完整的生成-验证-修改循环）
    验证通过后自动存入 History Database
    
    流程：
    1. 调用 Generator 生成 draft
    2. 调用 Verifier 验证
    3. 如果验证失败，调用 Controller 修改 prompt
    4. 重复直到通过或达到最大迭代次数
    5. 验证通过后存入数据库（如果提供了ID信息）
    """
    print(f"\n🔹 [HTTP] /generate_section 请求接收到")
    
    if generator is None:
        ensure_generator_initialized()
    if generator is None:
        raise HTTPException(status_code=500, detail=f"Generator not initialized: {_init_error or 'unknown error'}")
    
    try:
        print(f"📍 调用 get_orchestrator()...")
        orch = get_orchestrator()
        print(f"📍 获得 Orchestrator，_local_generator: {hasattr(orch, '_local_generator')}")
        
        result = orch.generate_section(
            outline=request.outline,
            initial_prompt=request.initial_prompt,
            document_id=request.document_id,
            section_id=request.section_id,
            subsection_id=request.subsection_id,
            history=request.history,
            rel_threshold=request.rel_threshold,
            red_threshold=request.red_threshold
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate_document")
def generate_document(request: GenerateDocumentRequest):
    """
    生成完整文档 - 新版本（完整流程）
    
    完整流程实现了所有需求：
    第一步（Outliner）：
      1. 调用LLM生成整篇文章的大纲
      2. 根据大纲生成每个section和subsection的详细大纲
      3. 所有大纲存储到数据库
    
    第二步（Generator）：
      1. 根据大纲生成第一个subsection
      2. 内容传给Verifier检测
      3. 如果通过，存储到数据库供下一个subsection使用
      4. 如果不通过，进入第三步
    
    第三步（Controller循环）：
      1. Controller从数据库提取未通过的subsection大纲
      2. 修改大纲传给Generator
      3. Generator再次生成
      4. 传给Verifier检测
      5. 循环直到通过
    
    关键特性：
    - subsection和section一个一个生成
    - 上一个subsection合格才能生成下一个
    - history在下一个subsection生成时被提取出来
    - history也在Verifier验证时使用
    - 可选全局串行：同一时刻只允许一个文档任务执行（默认开启）
    """
    try:
        if generator is None:
            ensure_generator_initialized()

        orchestrator = get_document_generation_orchestrator()
        if generator is not None:
            orchestrator.set_local_generator(generator)

        serialize_tasks = os.getenv("SERIALIZE_DOCUMENT_TASKS", "true").lower() == "true"
        lock_wait_timeout = float(os.getenv("SERIALIZE_DOCUMENT_WAIT_TIMEOUT", "30"))

        acquired = True
        if serialize_tasks:
            if lock_wait_timeout > 0:
                acquired = document_generation_lock.acquire(timeout=lock_wait_timeout)
            else:
                document_generation_lock.acquire()

            if not acquired:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"已有文档生成任务正在运行，请稍后重试（等待上限 {lock_wait_timeout:.0f}s）"
                    ),
                )

        try:
            result = orchestrator.generate_document(
                document_id=request.document_id,
                title=request.title,
                structure=request.structure,
                content_prompts=request.content_prompts,
                user_background=request.user_background,
                user_requirements=request.user_requirements,
                rel_threshold=request.rel_threshold,
                red_threshold=request.red_threshold
            )
            return result
        finally:
            if serialize_tasks and acquired:
                document_generation_lock.release()
        
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ============ 本地测试 ============

if __name__ == "__main__":
    # 优先使用环境变量 PORT（Render 会自动设置），否则使用命令行参数
    port = int(os.getenv("PORT", sys.argv[1] if len(sys.argv) > 1 else 8002))
    provider = os.getenv("GENERATOR_PROVIDER", sys.argv[2] if len(sys.argv) > 2 else "azure")
    model = os.getenv("GENERATOR_MODEL", sys.argv[3] if len(sys.argv) > 3 else None)
    
    # 初始化生成器
    init_generator(provider=provider, model=model)
    
    print(f"\n🚀 FlowerNet Generator 启动在 http://0.0.0.0:{port}")
    print(f"📖 API 文档: http://0.0.0.0:{port}/docs")
    print(f"🤖 使用 LLM: {provider} ({model or 'default'})")
    
    uvicorn.run(app, host="0.0.0.0", port=port)
