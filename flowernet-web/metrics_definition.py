"""
FlowerNet 文档生成质量指标定义
体现 FlowerNet 在学术文档生成中的多维质量保证能力
"""

from typing import Dict, List, Any

# 所有需要检测的指标定义
FLOWERNET_METRICS = {
    # ===== 第一层：内容相关性检测（Relevancy Check）=====
    "relevancy_index": {
        "name": "相关性指数",
        "description": "衡量生成内容与小节主题的相关程度",
        "threshold": 0.75,
        "threshold_description": "默认阈值 ≥ 0.75，确保内容紧密围绕主题展开",
        "category": "内容质量",
        "icon": "🎯",
        "feature": "基于语义和概念相似度的多层验证，避免偏题或泛化叙述",
        "pass_criteria": "relevancy_index >= 0.75",
    },

    # ===== 第二层：冗余度检测（Redundancy Check）=====
    "redundancy_index": {
        "name": "冗余度指数",
        "description": "衡量生成内容与文档已有内容的重复程度",
        "threshold": 0.40,
        "threshold_description": "默认阈值 ≤ 0.40，确保信息增量和创新性",
        "category": "内容创新",
        "icon": "✨",
        "feature": "自动检测与前文重复的表达和观点，强制补充新视角和案例",
        "pass_criteria": "redundancy_index <= 0.40",
    },

    # ===== 第三层：多维质量检测（Quality Dimensions）=====
    "topic_alignment": {
        "name": "主题对齐度",
        "description": "内容与小节主题的对齐程度",
        "threshold": 0.70,
        "threshold_description": "确保内容紧紧围绕主题核心展开",
        "category": "多维质量",
        "icon": "🔗",
        "feature": "分析关键概念覆盖、定义准确性和相关性",
        "pass_criteria": "topic_alignment >= 0.70",
    },

    "coverage_completeness": {
        "name": "覆盖完整性",
        "description": "内容对小节应覆盖要点的完整度",
        "threshold": 0.65,
        "threshold_description": "确保小节包含所有关键子点、步骤或维度",
        "category": "多维质量",
        "icon": "📋",
        "feature": "检测是否遗漏重要定义、步骤、对比或边界条件",
        "pass_criteria": "coverage_completeness >= 0.65",
    },

    "logical_coherence": {
        "name": "逻辑连贯性",
        "description": "内容的逻辑推进和论证连贯程度",
        "threshold": 0.25,
        "threshold_description": "通过逻辑缺陷检测确保推理流畅",
        "category": "多维质量",
        "icon": "🔀",
        "feature": "验证因果关系、递进逻辑和论证完整性，避免逻辑跳跃",
        "pass_criteria": "logical_coherence_gaps <= 0.25",
    },

    "evidence_grounding": {
        "name": "证据接地性",
        "description": "内容的事实依据和可验证性程度",
        "threshold": 0.30,
        "threshold_description": "要求充分的事实、数据、引用或示例支撑",
        "category": "多维质量",
        "icon": "📊",
        "feature": "检测空话比例，要求关键论点有数据、案例或权威引用支撑",
        "pass_criteria": "evidence_grounding >= 0.30",
    },

    "novelty": {
        "name": "新颖性",
        "description": "内容相对于前文的信息增量",
        "threshold": 0.40,
        "threshold_description": "避免简单重复，提供新的角度或信息",
        "category": "多维质量",
        "icon": "💡",
        "feature": "分析内容相对前文的增量信息量，要求新观点、新数据或新反例",
        "pass_criteria": "novelty >= 0.40",
    },

    "structure_clarity": {
        "name": "结构清晰度",
        "description": "内容组织结构的清晰程度",
        "threshold": 0.60,
        "threshold_description": "确保层次清晰、易于理解",
        "category": "多维质量",
        "icon": "📐",
        "feature": "检测标题使用、分点组织和步骤清晰度",
        "pass_criteria": "structure_clarity >= 0.60",
    },

    # ===== 第四层：引用质量检测（Citation Quality）=====
    "unique_citations": {
        "name": "唯一引用数",
        "description": "文档中不重复的引用来源数量",
        "threshold": 5,  # 最少要有5个唯一来源
        "threshold_description": "每个文档至少需要5个不同来源的引用",
        "category": "引用质量",
        "icon": "📚",
        "feature": "确保多源引证，避免单一来源依赖",
        "pass_criteria": "unique_citations >= 5",
    },

    "low_quality_citation_ratio": {
        "name": "低质量引用比例",
        "description": "低质量域名引用占总引用的比例",
        "threshold": 0.50,
        "threshold_description": "低质量引用不应超过总引用数的50%",
        "category": "引用质量",
        "icon": "🔍",
        "feature": "自动分类域名质量（学术库、权威新闻、官方网站vs非权威博客）",
        "pass_criteria": "low_quality_ratio <= 0.50",
    },

    "high_quality_per_section": {
        "name": "每小节高质量引用",
        "description": "每个小节至少要有高质量引用",
        "threshold": 1,
        "threshold_description": "每个小节至少包含1个高质量来源（评分≥0.70）",
        "category": "引用质量",
        "icon": "⭐",
        "feature": "确保每个小节的证据接地性和学术严谨性",
        "pass_criteria": "high_quality_urls_per_section >= 1",
    },

    # ===== 第五层：领域相关性过滤（Domain Filter）=====
    "domain_similarity": {
        "name": "领域相关性",
        "description": "引用摘要与文档关键词的语义相似度",
        "threshold": 0.35,
        "threshold_description": "相似度≥0.35时保留引用，避免跨领域污染",
        "category": "引用过滤",
        "icon": "🧲",
        "feature": "基于论文Abstract与文档Index Terms的多层相似度算法，自动过滤不相关论文",
        "pass_criteria": "domain_similarity >= 0.35",
    },

    # ===== 第六层：文档整体质量汇总=====
    "quality_score_avg": {
        "name": "平均质量评分",
        "description": "所有小节质量评分的平均值（0-1）",
        "threshold": 0.70,
        "threshold_description": "文档整体质量≥70分（百分制）",
        "category": "文档质量",
        "icon": "📊",
        "feature": "综合评估整个文档的学术质量和完整性",
        "pass_criteria": "quality_score_avg >= 0.70",
    },

    "quality_uncertainty_avg": {
        "name": "平均不确定度",
        "description": "质量评估的不确定程度平均值",
        "threshold": 0.15,
        "threshold_description": "不确定度≤0.15，表示评估高度可信",
        "category": "文档质量",
        "icon": "🎯",
        "feature": "使用不确定性量化来表示评估的置信度",
        "pass_criteria": "uncertainty_avg <= 0.15",
    },

    # ===== 第七层：生成过程指标=====
    "pass_rate": {
        "name": "通过率",
        "description": "通过验证的小节占总小节数的比例",
        "threshold": 0.95,  # 至少95%的小节要通过
        "threshold_description": "至少95%的小节需要首次或迭代后通过验证",
        "category": "生成过程",
        "icon": "✅",
        "feature": "自动迭代和改进机制，确保高通过率的同时避免过度修订",
        "pass_criteria": "pass_rate >= 0.95",
    },

    "iteration_efficiency": {
        "name": "迭代效率",
        "description": "平均每个小节的迭代次数",
        "threshold": 2.5,  # 平均不超过2.5次迭代
        "threshold_description": "低于2.5次平均迭代，表示生成和验证高效",
        "category": "生成过程",
        "icon": "🔄",
        "feature": "通过智能反馈和渐进式修订，快速达到质量标准",
        "pass_criteria": "avg_iterations <= 2.5",
    },

    "source_refs_coverage": {
        "name": "引用覆盖率",
        "description": "有源引用的小节占总小节数的比例",
        "threshold": 0.90,  # 至少90%的小节有来源引用
        "threshold_description": "至少90%的小节需要有对应的源引用",
        "category": "生成过程",
        "icon": "📖",
        "feature": "确保RAG系统的有效运作和源可溯性",
        "pass_criteria": "source_refs_coverage >= 0.90",
    },
}

# 指标分组
METRICS_CATEGORIES = {
    "内容质量": {
        "description": "衡量生成内容是否准确、相关且完整",
        "metrics": ["relevancy_index", "redundancy_index", "topic_alignment", "coverage_completeness"],
        "icon": "📝",
    },
    "逻辑与证据": {
        "description": "验证内容的逻辑推理和事实依据",
        "metrics": ["logical_coherence", "evidence_grounding", "novelty"],
        "icon": "🔗",
    },
    "结构与表达": {
        "description": "评估内容的组织结构和表达清晰度",
        "metrics": ["structure_clarity"],
        "icon": "📐",
    },
    "引用质量": {
        "description": "保证引用来源的质量、多样性和相关性",
        "metrics": ["unique_citations", "low_quality_citation_ratio", "high_quality_per_section", "domain_similarity"],
        "icon": "📚",
    },
    "文档质量": {
        "description": "文档整体的质量汇总评分",
        "metrics": ["quality_score_avg", "quality_uncertainty_avg"],
        "icon": "⭐",
    },
    "生成效率": {
        "description": "生成过程的效率和成功率指标",
        "metrics": ["pass_rate", "iteration_efficiency", "source_refs_coverage"],
        "icon": "⚡",
    },
}

# FlowerNet 的核心特点说明
FLOWERNET_FEATURES = {
    "multi_dimensional_quality": {
        "title": "🎯 多维质量保证",
        "description": "不仅检查相关性，更从6个维度全面评估内容质量：主题对齐、覆盖完整性、逻辑连贯性、证据接地性、新颖性、结构清晰度",
        "advantage": "避免单一维度评估的盲点，确保学术严谨性"
    },
    "domain_aware_filtering": {
        "title": "🧲 领域感知引用过滤",
        "description": "基于论文摘要与文档关键词的多层语义相似度算法，自动过滤不相关引用",
        "advantage": "防止跨领域污染，确保引用的学术相关性"
    },
    "redundancy_detection": {
        "title": "✨ 冗余度自动检测",
        "description": "检测生成内容与前文的重复程度，强制补充新视角和案例",
        "advantage": "确保信息增量，避免重复讲述"
    },
    "iterative_improvement": {
        "title": "🔄 迭代自我完善",
        "description": "生成-验证-反馈-改进的循环，平均2.5次迭代达到质量标准",
        "advantage": "高质量通过率（95%+），快速收敛到优质文档"
    },
    "multi_source_verification": {
        "title": "📊 多源交叉验证",
        "description": "结合语义验证、维度检查、引用质量评估，采用多个AI评估器",
        "advantage": "提高评估的可靠性和鲁棒性"
    },
    "uncertainty_quantification": {
        "title": "🎲 不确定性量化",
        "description": "每个评估结果都附带置信度指标，表示评估的可信程度",
        "advantage": "用户可了解评估结果的可信度，做出更好决策"
    },
}

def get_metric_definition(metric_key: str) -> Dict[str, Any]:
    """获取单个指标的完整定义"""
    return FLOWERNET_METRICS.get(metric_key, {})

def get_all_metrics() -> Dict[str, Any]:
    """获取所有指标定义"""
    return FLOWERNET_METRICS

def get_metrics_by_category(category: str) -> List[str]:
    """按分类获取指标列表"""
    return METRICS_CATEGORIES.get(category, {}).get("metrics", [])

def get_category_info(category: str) -> Dict[str, Any]:
    """获取分类信息"""
    return METRICS_CATEGORIES.get(category, {})

def get_all_categories() -> Dict[str, Any]:
    """获取所有分类"""
    return METRICS_CATEGORIES
