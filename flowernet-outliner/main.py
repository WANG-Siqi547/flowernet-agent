"""
FlowerNet Outliner - FastAPI Service
提供 RESTful API 接口
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
import uvicorn
import os
from datetime import datetime

from outliner import FlowerNetOutliner
from database import HistoryManager


# ============ Pydantic Models ============

class OutlineRequest(BaseModel):
    """生成大纲的请求"""
    user_background: str = Field(..., description="用户背景信息")
    user_requirements: str = Field(..., description="用户需求描述")
    max_sections: int = Field(default=5, ge=2, le=10, description="最大 Section 数量")
    max_subsections_per_section: int = Field(default=4, ge=2, le=8, description="每个 Section 最大 Subsection 数量")


class HistoryEntry(BaseModel):
    """添加 History 的请求"""
    document_id: str = Field(..., description="文档 ID")
    section_id: str = Field(..., description="Section ID")
    subsection_id: str = Field(..., description="Subsection ID")
    content: str = Field(..., description="生成的内容")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="额外元数据")


class HistoryQuery(BaseModel):
    """查询 History 的请求"""
    document_id: str = Field(..., description="文档 ID")


# ============ FastAPI App ============

app = FastAPI(
    title="FlowerNet Outliner",
    description="文档大纲生成与 Content Prompt 管理服务",
    version="1.0.0"
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局实例
outliner = None
history_manager = None


@app.on_event("startup")
async def startup_event():
    """启动时初始化"""
    global outliner, history_manager
    
    # 初始化 Outliner
    api_key = os.getenv('GOOGLE_API_KEY', '')
    if not api_key:
        print("❌ 警告: 未设置 GOOGLE_API_KEY 环境变量")
    
    model = os.getenv('OUTLINER_MODEL', 'models/gemini-2.5-flash')
    outliner = FlowerNetOutliner(api_key=api_key, model=model)
    
    # 初始化 History Manager（默认内存模式）
    use_db = os.getenv('USE_DATABASE', 'false').lower() == 'true'
    db_path = os.getenv('DATABASE_PATH', 'flowernet_history.db')
    history_manager = HistoryManager(use_database=use_db, db_path=db_path)
    
    print("=" * 50)
    print("🚀 FlowerNet Outliner 启动成功")
    print("=" * 50)


# ============ API Endpoints ============

@app.get("/")
async def root():
    """健康检查"""
    return {
        "service": "FlowerNet Outliner",
        "status": "running",
        "version": "1.0.0",
        "timestamp": datetime.now().isoformat()
    }


@app.post("/generate-outline")
async def generate_outline(request: OutlineRequest):
    """
    生成完整的文档大纲和 Content Prompts
    
    Returns:
        {
            "success": True,
            "document_title": "...",
            "structure": {...},
            "content_prompts": [...],
            "total_subsections": 12
        }
    """
    try:
        result = outliner.generate_full_outline(
            user_background=request.user_background,
            user_requirements=request.user_requirements,
            max_sections=request.max_sections,
            max_subsections_per_section=request.max_subsections_per_section
        )
        
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate-structure")
async def generate_structure(request: OutlineRequest):
    """
    仅生成文档结构（不生成 Content Prompts）
    
    Returns:
        {
            "success": True,
            "structure": {...},
            "metadata": {...}
        }
    """
    try:
        result = outliner.generate_document_structure(
            user_background=request.user_background,
            user_requirements=request.user_requirements,
            max_sections=request.max_sections,
            max_subsections_per_section=request.max_subsections_per_section
        )
        
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/history/add")
async def add_history(entry: HistoryEntry):
    """
    添加一条 History 记录
    
    Args:
        entry: History 数据
        
    Returns:
        {"success": True, "message": "已添加"}
    """
    try:
        history_manager.add_entry(
            document_id=entry.document_id,
            section_id=entry.section_id,
            subsection_id=entry.subsection_id,
            content=entry.content,
            metadata=entry.metadata
        )
        
        return {
            "success": True,
            "message": f"已添加 history: {entry.subsection_id}"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/history/get")
async def get_history(query: HistoryQuery):
    """
    获取某个文档的所有 History
    
    Args:
        query: 包含 document_id
        
    Returns:
        {
            "success": True,
            "document_id": "...",
            "history": [...],
            "total": 5
        }
    """
    try:
        history = history_manager.get_history(query.document_id)
        
        return {
            "success": True,
            "document_id": query.document_id,
            "history": history,
            "total": len(history)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/history/get-text")
async def get_history_text(query: HistoryQuery):
    """
    获取某个文档的 History 纯文本（用于传给 Verifier）
    
    Args:
        query: 包含 document_id
        
    Returns:
        {
            "success": True,
            "document_id": "...",
            "history_text": "...",
            "total_characters": 5000
        }
    """
    try:
        text = history_manager.get_history_text(query.document_id)
        
        return {
            "success": True,
            "document_id": query.document_id,
            "history_text": text,
            "total_characters": len(text)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/history/clear")
async def clear_history(query: HistoryQuery):
    """
    清空某个文档的 History（文档完成后调用）
    
    Args:
        query: 包含 document_id
        
    Returns:
        {"success": True, "message": "已清空"}
    """
    try:
        history_manager.clear_history(query.document_id)
        
        return {
            "success": True,
            "message": f"已清空文档 {query.document_id} 的 history"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/history/statistics")
async def get_statistics(query: HistoryQuery):
    """
    获取文档的统计信息
    
    Args:
        query: 包含 document_id
        
    Returns:
        {
            "success": True,
            "statistics": {
                "total_entries": 10,
                "total_characters": 5000,
                "sections": [...]
            }
        }
    """
    try:
        stats = history_manager.get_statistics(query.document_id)
        
        return {
            "success": True,
            "statistics": stats
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ Main ============

if __name__ == "__main__":
    port = int(os.getenv('PORT', 8003))
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=True
    )
