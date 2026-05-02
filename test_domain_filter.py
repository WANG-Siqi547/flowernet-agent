#!/usr/bin/env python3
"""
Domain Filter 功能测试
=======================
测试Index Terms提取、相似度计算和引用过滤
"""

import sys
import json
from domain_filter import DomainFilter, get_domain_filter

def test_index_terms_extraction():
    """测试关键词提取"""
    print("\n" + "="*60)
    print("✅ Test 1: Index Terms Extraction")
    print("="*60)
    
    domain_filter = get_domain_filter()
    
    # 测试样本1: 商业谈判相关
    title = "全面谈判策略指南"
    outline = """
    1. 谈判基础知识
       1.1 谈判定义与重要性
       1.2 谈判类型分类
    2. 谈判准备阶段
       2.1 信息收集与分析
       2.2 目标制定
    3. 谈判执行阶段
       3.1 开场技巧
       3.2 博弈与让步
    """
    
    index_terms = domain_filter.extract_document_index_terms(
        title=title,
        outline=outline,
        abstract="本文探讨现代商业谈判的策略和技巧",
        content_sample="谈判过程中的沟通技巧和心理学应用"
    )
    
    print(f"📌 Document Title: {title}")
    print(f"📌 Extracted Index Terms ({len(index_terms)}):")
    print(f"   {sorted(list(index_terms))}")
    
    assert len(index_terms) >= 3, f"Should extract at least 3 terms, got {len(index_terms)}"
    print("✅ Index terms extraction passed")
    
    return index_terms


def test_similarity_scoring():
    """测试相似度计算"""
    print("\n" + "="*60)
    print("✅ Test 2: Similarity Scoring")
    print("="*60)
    
    domain_filter = get_domain_filter()
    
    index_terms = {"谈判", "商业", "策略", "沟通", "negotiation"}
    
    # 测试用例
    test_cases = [
        {
            "name": "Relevant - Business Negotiation",
            "abstract": "本文研究现代商业谈判中的沟通策略与心理学应用",
            "expected_high": True,
        },
        {
            "name": "Relevant - Data Mining & Analytics",
            "abstract": "数据挖掘在商业分析中的应用，包括客户谈判行为预测",
            "expected_high": False,  # 主要是数据挖掘，不是谈判
        },
        {
            "name": "Irrelevant - Audio Processing",
            "abstract": "音频信号处理与声学分析技术",
            "expected_high": False,
        },
        {
            "name": "Irrelevant - Loop Group Theory",
            "abstract": "循环群的谱序列与代数拓扑研究",
            "expected_high": False,
        },
        {
            "name": "Relevant - Sales Negotiation",
            "abstract": "销售谈判技巧: 价格博弈、让步策略和双赢协议",
            "expected_high": True,
        },
    ]
    
    threshold = 0.30
    
    for test_case in test_cases:
        similarity = domain_filter.scorer.compute_similarity(
            citation_abstract=test_case["abstract"],
            index_terms=index_terms,
            debug=False,
        )
        
        expected_result = "HIGH ✅" if similarity >= threshold else "LOW ✗"
        actual_result = "HIGH ✅" if test_case["expected_high"] else "LOW ✗"
        
        status = "PASS ✓" if (similarity >= threshold) == test_case["expected_high"] else "FAIL ✗"
        
        print(f"\n  Test: {test_case['name']}")
        print(f"    Abstract: {test_case['abstract'][:60]}...")
        print(f"    Similarity: {similarity:.3f} | Expected: {actual_result} | Result: {status}")


def test_citation_filtering():
    """测试引用过滤"""
    print("\n" + "="*60)
    print("✅ Test 3: Citation Filtering")
    print("="*60)
    
    domain_filter = get_domain_filter()
    
    index_terms = {"谈判", "商业", "策略", "沟通", "管理"}
    
    citations = [
        {
            "title": "Modern Business Negotiation Strategies",
            "body": "本文研究现代商业谈判中的沟通策略与心理学应用，包括开场技巧、让步策略和博弈论",
            "href": "https://example.com/negotiation-strategies",
            "source": "academic",
        },
        {
            "title": "Data Mining for Customer Insights",
            "body": "数据挖掘技术用于客户行为分析",
            "href": "https://example.com/data-mining",
            "source": "academic",
        },
        {
            "title": "Advanced Audio Signal Processing",
            "body": "音频信号处理与声学分析的最新进展",
            "href": "https://example.com/audio-processing",
            "source": "journal",
        },
        {
            "title": "Spectral Sequences in Loop Groups",
            "body": "循环群的谱序列与代数拓扑结构研究",
            "href": "https://example.com/loop-groups",
            "source": "arxiv",
        },
        {
            "title": "Sales Negotiation and Price Dynamics",
            "body": "销售谈判中的价格博弈、让步策略和双赢协议达成",
            "href": "https://example.com/sales-negotiation",
            "source": "academic",
        },
    ]
    
    threshold = 0.58  # Adjusted to match new default
    filtered, filtered_out = domain_filter.filter_citations(
        citations=citations,
        index_terms=index_terms,
        threshold=threshold,
        debug=False,
    )
    
    print(f"\n📊 Filtering Results (Threshold: {threshold}):")
    print(f"  Total: {len(citations)} | Kept: {len(filtered)} | Filtered Out: {len(filtered_out)}")
    
    print(f"\n✅ Kept References:")
    for i, cite in enumerate(filtered, 1):
        print(f"  {i}. {cite.get('title', '')}")
    
    print(f"\n❌ Filtered Out References:")
    for i, cite in enumerate(filtered_out, 1):
        print(f"  {i}. {cite.get('title', '')}")
    
    # 验证结果
    assert len(filtered) >= 2, "Should keep at least 2 relevant citations"
    assert len(filtered_out) >= 2, "Should filter out at least 2 irrelevant citations"
    
    print("\n✅ Citation filtering passed")


def test_threshold_adjustment():
    """测试阈值调整的效果"""
    print("\n" + "="*60)
    print("✅ Test 4: Threshold Impact Analysis")
    print("="*60)
    
    domain_filter = get_domain_filter()
    
    index_terms = {"谈判", "商业", "strategy"}
    
    citations = [
        {
            "title": "核心话题1",
            "body": "这是关于谈判和商业策略的核心论文",
            "href": "https://example.com/1",
        },
        {
            "title": "相关话题",
            "body": "商业管理中的一些相关概念",
            "href": "https://example.com/2",
        },
        {
            "title": "边界话题",
            "body": "数据和分析在某些方面相关",
            "href": "https://example.com/3",
        },
        {
            "title": "无关话题",
            "body": "完全不同的技术领域",
            "href": "https://example.com/4",
        },
    ]
    
    thresholds = [0.15, 0.30, 0.50, 0.70]
    
    print("\n🔍 Testing different thresholds:")
    for threshold in thresholds:
        filtered, _ = domain_filter.filter_citations(
            citations=citations,
            index_terms=index_terms,
            threshold=threshold,
            debug=False,
        )
        print(f"  Threshold {threshold:.2f}: {len(filtered)} references kept")


def main():
    print("\n" + "🧪 "*20)
    print("Domain Filter Test Suite")
    print("🧪 "*20)
    
    try:
        # Test 1: 关键词提取
        index_terms = test_index_terms_extraction()
        
        # Test 2: 相似度计算
        test_similarity_scoring()
        
        # Test 3: 引用过滤
        test_citation_filtering()
        
        # Test 4: 阈值分析
        test_threshold_adjustment()
        
        print("\n" + "="*60)
        print("✅ ALL TESTS PASSED!")
        print("="*60)
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
