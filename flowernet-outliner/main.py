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
import json
from datetime import datetime

from outliner import FlowerNetOutliner
import sys
import os.path
# Add root directory to path to import shared history_store
if '..' not in sys.path:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from history_store import HistoryManager


# ============ Pydantic Models ============

class OutlineRequest(BaseModel):
    """生成大纲的请求"""
    user_background: str = Field(..., description="用户背景信息")
    user_requirements: str = Field(..., description="用户需求描述")
    max_sections: int = Field(default=5, ge=1, le=10, description="最大 Section 数量")
    max_subsections_per_section: int = Field(default=4, ge=1, le=8, description="每个 Section 最大 Subsection 数量")


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


class ProgressQuery(BaseModel):
    """查询流程事件的请求"""
    document_id: str = Field(..., description="文档 ID")
    after_id: int = Field(default=0, ge=0, description="仅返回该 ID 之后的事件")
    limit: int = Field(default=100, ge=1, le=500, description="单次返回上限")


class SaveOutlineRequest(BaseModel):
    """保存大纲的请求"""
    document_id: str = Field(..., description="文档 ID")
    outline_content: str = Field(..., description="大纲内容（可以是 JSON 或纯文本）")
    outline_type: str = Field(default="document", description="大纲类型: document/section/subsection")
    section_id: Optional[str] = Field(default=None, description="Section ID（section/subsection 级别需要）")
    subsection_id: Optional[str] = Field(default=None, description="Subsection ID（subsection 级别需要）")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="额外元数据")


class GetOutlineRequest(BaseModel):
    """获取大纲的请求"""
    document_id: str = Field(..., description="文档 ID")
    outline_type: str = Field(default="document", description="大纲类型: document/section/subsection")
    section_id: Optional[str] = Field(default=None, description="Section ID")
    subsection_id: Optional[str] = Field(default=None, description="Subsection ID")


class GenerateAndSaveOutlineRequest(BaseModel):
    """生成大纲并保存的请求"""
    document_id: str = Field(..., description="文档 ID")
    user_background: str = Field(..., description="用户背景信息")
    user_requirements: str = Field(..., description="用户需求描述")
    max_sections: int = Field(default=5, ge=1, le=10)
    max_subsections_per_section: int = Field(default=4, ge=1, le=8)


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
    provider = os.getenv('OUTLINER_PROVIDER', 'azure,ollama')
    model = os.getenv('OUTLINER_MODEL', 'gpt-4o-mini')

    api_key = os.getenv('GOOGLE_API_KEY', '')
    outliner = FlowerNetOutliner(api_key=api_key, model=model, provider=provider)
    
    # 初始化 History Manager（默认使用数据库模式）
    use_db = os.getenv('USE_DATABASE', 'true').lower() == 'true'
    raw_db_path = os.getenv('DATABASE_PATH', 'flowernet_history.db')
    if os.path.isabs(raw_db_path):
        db_path = raw_db_path
    else:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        db_path = os.path.join(project_root, raw_db_path)
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


@app.post("/history/progress")
async def get_progress_events(query: ProgressQuery):
    """
    获取文档流程事件（增量），用于前端实时展示生成细节。
    """
    try:
        events = history_manager.get_progress_events(
            document_id=query.document_id,
            after_id=query.after_id,
            limit=query.limit,
        )
        last_id = events[-1]["id"] if events else query.after_id

        return {
            "success": True,
            "document_id": query.document_id,
            "events": events,
            "last_id": last_id,
            "count": len(events),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ 新增：大纲管理接口 ============

@app.post("/outline/save")
async def save_outline(request: SaveOutlineRequest):
    """
    保存大纲到数据库
    
    Args:
        request: 包含大纲内容和类型
        
    Returns:
        {"success": True, "message": "大纲已保存"}
    """
    try:
        history_manager.save_outline(
            document_id=request.document_id,
            outline_content=request.outline_content,
            outline_type=request.outline_type,
            section_id=request.section_id,
            subsection_id=request.subsection_id,
            metadata=request.metadata
        )
        
        return {
            "success": True,
            "message": f"大纲已保存: {request.outline_type} level"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/outline/get")
async def get_outline(request: GetOutlineRequest):
    """
    获取已保存的大纲
    
    Args:
        request: 包含查询条件
        
    Returns:
        {"success": True, "outline": "...", "outline_type": "document"}
    """
    try:
        outline = history_manager.get_outline(
            document_id=request.document_id,
            outline_type=request.outline_type,
            section_id=request.section_id,
            subsection_id=request.subsection_id
        )
        
        return {
            "success": True,
            "document_id": request.document_id,
            "outline_type": request.outline_type,
            "outline": outline
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/outline/generate-and-save")
async def generate_and_save_outline(request: GenerateAndSaveOutlineRequest):
    """
    生成大纲并自动保存到数据库，同时为每个 section/subsection 的大纲也保存
    
    这实现了你的第一步需求：
    1. 调用一次LLM生成整篇文章的大纲
    2. 再调用一次LLM根据大纲生成每个section和subsection的大纲
    3. 这些大纲全都存储到数据库
    
    Args:
        request: 包含 document_id 和生成参数
        
    Returns:
        {
            "success": True,
            "document_title": "...",
            "document_id": "...",
            "outline_saved": True,
            "structure_outline_saved": True,
            "subsection_outlines_count": 12,
            "structure": {...}
        }
    """
    try:
        # 第一步：生成整篇文章的大纲
        print(f"\n📝 第一步：生成整篇文章的大纲...")
        result = outliner.generate_full_outline(
            user_background=request.user_background,
            user_requirements=request.user_requirements,
            max_sections=request.max_sections,
            max_subsections_per_section=request.max_subsections_per_section
        )
        
        if not result.get("success"):
            return result
        
        structure = result["structure"]
        document_title = result["document_title"]
        content_prompts = result["content_prompts"]
        
        # 保存整篇文章的大纲
        print(f"💾 保存整篇文章的大纲...")
        history_manager.save_outline(
            document_id=request.document_id,
            outline_content=f"# {document_title}\n\n" + json.dumps(structure, ensure_ascii=False, indent=2),
            outline_type="document",
            metadata={"title": document_title, "input": {
                "user_background": request.user_background,
                "user_requirements": request.user_requirements
            }}
        )
        
        # 第二步：为每个 section 和 subsection 保存其大纲
        print(f"💾 保存每个 section 和 subsection 的大纲...")
        subsection_outline_count = 0
        
        for section in structure.get("sections", []):
            section_id = section.get("id", "")
            section_title = section.get("title", "")
            section_description = f"该章节包含以下小节:\n"
            
            for subsection in section.get("subsections", []):
                subsection_id = subsection.get("id", "")
                subsection_title = subsection.get("title", "")
                subsection_desc = subsection.get("description", "")
                
                # 保存 subsection 的大纲
                outlined_content_prompt = None
                for cp in content_prompts:
                    if cp["section_id"] == section_id and cp["subsection_id"] == subsection_id:
                        outlined_content_prompt = cp.get("content_prompt", "")
                        break
                
                history_manager.save_outline(
                    document_id=request.document_id,
                    outline_content=f"## {subsection_title}\n\n{subsection_desc}\n\n### 生成提示\n{outlined_content_prompt or 'N/A'}",
                    outline_type="subsection",
                    section_id=section_id,
                    subsection_id=subsection_id,
                    metadata={"title": subsection_title}
                )
                
                subsection_outline_count += 1
                section_description += f"\n- {subsection_title}: {subsection_desc}"
            
            # 保存 section 的大纲
            history_manager.save_outline(
                document_id=request.document_id,
                outline_content=f"# {section_title}\n\n{section_description}",
                outline_type="section",
                section_id=section_id,
                metadata={"title": section_title}
            )
        
        print(f"✅ 大纲生成并保存完成!")
        print(f"   - 文档大纲: 已保存")
        print(f"   - Section 大纲: {len(structure.get('sections', []))} 个")
        print(f"   - Subsection 大纲: {subsection_outline_count} 个")
        
        return {
            "success": True,
            "document_title": document_title,
            "document_id": request.document_id,
            "outline_saved": True,
            "structure_outline_saved": True,
            "section_count": len(structure.get("sections", [])),
            "subsection_outlines_count": subsection_outline_count,
            "structure": structure,
            "content_prompts": content_prompts
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ============ Main ============

if __name__ == "__main__":
    port = int(os.getenv('PORT', 8003))
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=False
    )
