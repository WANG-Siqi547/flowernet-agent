"""
FlowerNet 指标展示 API 端点
提供所有质量检测指标的详细定义和说明
"""

from fastapi import APIRouter, Header, HTTPException
from typing import Dict, Any, List
import os
import requests

try:
    from metrics_definition import (
        FLOWERNET_METRICS,
        METRICS_CATEGORIES,
        FLOWERNET_FEATURES,
        get_all_metrics,
        get_all_categories,
        get_metrics_by_category,
    )
    HAS_METRICS_DEFINITION = True
except ImportError:
    HAS_METRICS_DEFINITION = False
    FLOWERNET_METRICS = {}
    METRICS_CATEGORIES = {}
    FLOWERNET_FEATURES = {}

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


def _generator_url() -> str:
    return os.getenv("GENERATOR_URL", "http://localhost:8002").rstrip("/")


def _safe_generator_get(path: str) -> Dict[str, Any]:
    try:
        resp = requests.get(f"{_generator_url()}{path}", timeout=8)
        if resp.ok:
            return resp.json()
        return {"success": False, "error": f"HTTP {resp.status_code}"}
    except Exception as exc:
        return {"success": False, "error": str(exc)[:240]}


@router.get("/all")
def get_all_metrics_endpoint() -> Dict[str, Any]:
    """
    获取所有检测指标的完整定义
    
    返回 FlowerNet 系统中所有的质量检测指标，包括：
    - 内容相关性检测（Relevancy）
    - 冗余度检测（Redundancy）
    - 多维质量检测（Quality Dimensions）
    - 引用质量检测（Citation Quality）
    - 领域相关性过滤（Domain Filter）
    - 文档整体质量（Document Quality）
    - 生成效率指标（Generation Efficiency）
    """
    if not HAS_METRICS_DEFINITION:
        raise HTTPException(status_code=503, detail="Metrics Definition not available")
    
    return {
        "success": True,
        "metrics_count": len(FLOWERNET_METRICS),
        "metrics": FLOWERNET_METRICS,
    }


@router.get("/categories")
def get_categories_endpoint() -> Dict[str, Any]:
    """
    获取指标分类信息
    
    返回按功能分类的指标组织结构，每个分类包括：
    - 分类名称和描述
    - 该分类下的所有指标
    - 分类的图标标识
    """
    if not HAS_METRICS_DEFINITION:
        raise HTTPException(status_code=503, detail="Metrics Definition not available")
    
    return {
        "success": True,
        "categories_count": len(METRICS_CATEGORIES),
        "categories": METRICS_CATEGORIES,
    }


@router.get("/category/{category_name}")
def get_category_metrics_endpoint(category_name: str) -> Dict[str, Any]:
    """
    获取指定分类下的所有指标
    
    参数:
        category_name: 分类名称（如 "内容质量", "引用质量" 等）
    
    返回该分类的详细信息和所有关联的指标
    """
    if not HAS_METRICS_DEFINITION:
        raise HTTPException(status_code=503, detail="Metrics Definition not available")
    
    category_info = get_all_categories().get(category_name)
    if not category_info:
        raise HTTPException(status_code=404, detail=f"Category '{category_name}' not found")
    
    metric_keys = category_info.get("metrics", [])
    category_metrics = {
        key: FLOWERNET_METRICS.get(key) 
        for key in metric_keys 
        if key in FLOWERNET_METRICS
    }
    
    return {
        "success": True,
        "category_name": category_name,
        "category_info": category_info,
        "metrics_count": len(category_metrics),
        "metrics": category_metrics,
    }


@router.get("/metric/{metric_key}")
def get_metric_detail_endpoint(metric_key: str) -> Dict[str, Any]:
    """
    获取单个指标的详细定义
    
    参数:
        metric_key: 指标的唯一标识（如 "relevancy_index", "domain_similarity" 等）
    
    返回该指标的完整说明、阈值、评分标准等信息
    """
    if not HAS_METRICS_DEFINITION:
        raise HTTPException(status_code=503, detail="Metrics Definition not available")
    
    metric = FLOWERNET_METRICS.get(metric_key)
    if not metric:
        raise HTTPException(status_code=404, detail=f"Metric '{metric_key}' not found")
    
    return {
        "success": True,
        "metric_key": metric_key,
        "metric": metric,
    }


@router.get("/features")
def get_features_endpoint() -> Dict[str, Any]:
    """
    获取 FlowerNet 的核心特点说明
    
    返回系统的六大核心能力：
    1. 多维质量保证 - 从6个维度全面评估
    2. 领域感知引用过滤 - 防止跨领域污染
    3. 冗余度自动检测 - 确保信息增量
    4. 迭代自我完善 - 高效收敛到优质文档
    5. 多源交叉验证 - 提高评估可靠性
    6. 不确定性量化 - 体现评估置信度
    """
    if not HAS_METRICS_DEFINITION:
        raise HTTPException(status_code=503, detail="Metrics Definition not available")
    
    return {
        "success": True,
        "features_count": len(FLOWERNET_FEATURES),
        "features": FLOWERNET_FEATURES,
    }


@router.get("/dashboard-summary")
def get_dashboard_summary_endpoint() -> Dict[str, Any]:
    """
    获取仪表板概览数据
    
    返回一个适合在前端仪表板展示的完整指标体系概览，包括：
    - 总体指标数量
    - 分类统计
    - 核心特性列表
    - 快速指标查询
    """
    if not HAS_METRICS_DEFINITION:
        raise HTTPException(status_code=503, detail="Metrics Definition not available")
    
    categories = get_all_categories()
    
    return {
        "success": True,
        "summary": {
            "total_metrics": len(FLOWERNET_METRICS),
            "total_categories": len(categories),
            "total_features": len(FLOWERNET_FEATURES),
            "categories": {
                name: {
                    "name": name,
                    "description": info.get("description", ""),
                    "metrics_count": len(info.get("metrics", [])),
                    "metrics": info.get("metrics", []),
                }
                for name, info in categories.items()
            },
            "features": [
                {
                    "title": feature.get("title", ""),
                    "description": feature.get("description", ""),
                    "advantage": feature.get("advantage", ""),
                }
                for feature in FLOWERNET_FEATURES.values()
            ],
        },
        "quick_access": {
            "all_metrics_count": len(FLOWERNET_METRICS),
            "categories_list": list(categories.keys()),
            "top_features": list(FLOWERNET_FEATURES.keys())[:3],
        },
    }


@router.get("/agent-stack")
def get_agent_stack_endpoint() -> Dict[str, Any]:
    """
    获取 FlowerNet Agent 工程化能力概览：
    - LangGraph-style 编排
    - Vector DB/RAG/reranker
    - MCP/tool-use 工具目录
    - Redis/task checkpoint 降级状态
    - LLM evaluation 自动评测摘要
    """
    data = _safe_generator_get("/agent/capabilities")
    return {
        "success": bool(data.get("success", False)),
        "generator_url": _generator_url(),
        "capabilities": data.get("capabilities", {}),
        "error": data.get("error", ""),
    }


@router.get("/evaluation-dashboard")
def get_evaluation_dashboard_endpoint() -> Dict[str, Any]:
    """LLM evaluation dashboard summary backed by generator evaluation store."""
    data = _safe_generator_get("/evaluation/summary")
    return {
        "success": bool(data.get("success", False)),
        "generator_url": _generator_url(),
        "summary": data.get("summary", {}),
        "error": data.get("error", ""),
    }


@router.get("/comparison")
def get_metrics_comparison_endpoint() -> Dict[str, Any]:
    """
    获取指标的对比分析数据
    
    用于展示不同指标的阈值、权重、重要性等对比信息
    帮助用户理解各指标之间的关系和相对重要性
    """
    if not HAS_METRICS_DEFINITION:
        raise HTTPException(status_code=503, detail="Metrics Definition not available")
    
    # 按类型分组指标
    by_category = {}
    for metric_key, metric_data in FLOWERNET_METRICS.items():
        category = metric_data.get("category", "其他")
        if category not in by_category:
            by_category[category] = []
        by_category[category].append({
            "key": metric_key,
            "name": metric_data.get("name", ""),
            "threshold": metric_data.get("threshold", "N/A"),
            "feature": metric_data.get("feature", ""),
        })
    
    return {
        "success": True,
        "comparison": {
            "metrics_by_category": by_category,
            "total_categories": len(by_category),
        },
        "statistics": {
            "total_metrics": len(FLOWERNET_METRICS),
            "metrics_with_thresholds": len([m for m in FLOWERNET_METRICS.values() if m.get("threshold")]),
            "categories_covered": list(by_category.keys()),
        },
    }


@router.get("/documentation")
def get_documentation_endpoint() -> Dict[str, Any]:
    """
    获取完整的指标文档
    
    包括指标体系的完整说明、使用指南、解释和最佳实践
    """
    if not HAS_METRICS_DEFINITION:
        raise HTTPException(status_code=503, detail="Metrics Definition not available")
    
    return {
        "success": True,
        "documentation": {
            "system_overview": {
                "title": "FlowerNet 质量检测体系",
                "description": "FlowerNet 采用多层次、多维度的质量检测体系，确保生成文档的学术严谨性和内容质量",
                "layers": [
                    "第一层：内容相关性检测",
                    "第二层：冗余度检测", 
                    "第三层：多维质量检测",
                    "第四层：引用质量检测",
                    "第五层：领域相关性过滤",
                    "第六层：文档整体质量汇总",
                    "第七层：生成过程指标",
                ]
            },
            "metrics": FLOWERNET_METRICS,
            "categories": METRICS_CATEGORIES,
            "features": FLOWERNET_FEATURES,
            "usage_guide": {
                "understanding_thresholds": "每个指标都有对应的阈值或标准，生成文档需要通过这些检测才被认为质量达标",
                "interpreting_scores": "指标评分通常在 0-1 之间，越接近 1 表示质量越好",
                "pass_criteria": "Pass criteria 列出了该指标通过的标准",
                "improvement_tips": "如果某个指标未达标，可以根据指标的说明进行改进",
            }
        },
    }
