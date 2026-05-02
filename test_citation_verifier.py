#!/usr/bin/env python3
"""
Test Citation Verifier with problematic document
=====================================================
Tests the Citation Verifier Agent on the "谈判策略" document that showed citation drift.
"""

import sys
import json
import logging

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

# Import Citation Verifier
try:
    from citation_verifier import CitationVerifier, verify_references
    print("✅ Citation Verifier imported successfully")
except ImportError as e:
    print(f"❌ Failed to import Citation Verifier: {e}")
    print("Install sentence-transformers: pip install sentence-transformers")
    sys.exit(1)

# Test case: The problematic "谈判策略" document
PROBLEMATIC_REFERENCES = [
    {
        "title": "谈判策略: 双赢框架与实战指南",
        "url": "https://example.com/negotiation-guide",
        "body": "A comprehensive guide on negotiation strategies..."
    },
    {
        "title": "LaFeAsO超导体的物理性质与应用",
        "url": "https://arxiv.org/abs/2001.11111",
        "body": "Study on LaFeAsO superconductor..."  # ❌ IRRELEVANT - Physics paper
    },
    {
        "title": "商业谈判中的心理学原理",
        "url": "https://example.com/psychology-negotiation",
        "body": "Psychological principles in business negotiation..."
    },
    {
        "title": "激光与等离子体互作的最新研究",
        "url": "https://arxiv.org/abs/2001.22222",
        "body": "Latest research on laser-plasma interaction..."  # ❌ IRRELEVANT - Physics paper
    },
    {
        "title": "企业谈判战略: 案例分析与最佳实践",
        "url": "https://hbr.org/negotiation-strategies",
        "body": "Case studies and best practices in corporate negotiation..."
    },
]

DOCUMENT_TOPIC = "谈判策略"
DOCUMENT_SECTION = """
    谈判是商业活动中的关键技能。有效的谈判可以帮助企业获得更好的合同条款、
    建立长期合作关系，并创造互利的商业机会。本节介绍商业谈判的核心原则、
    谈判技巧和实战案例...
"""

def test_citation_verifier():
    """Test the Citation Verifier on problematic references"""
    print("\n" + "="*70)
    print("Citation Verifier Test - Detecting Citation Drift")
    print("="*70)
    
    print(f"\n📄 Document Topic: {DOCUMENT_TOPIC}")
    print(f"📊 Total References (before filtering): {len(PROBLEMATIC_REFERENCES)}")
    
    # Run verification
    verifier = CitationVerifier()
    result = verifier.verify_and_rerank(
        references=PROBLEMATIC_REFERENCES,
        topic=DOCUMENT_TOPIC,
        section_outline="谈判策略",
        full_content=DOCUMENT_SECTION,
    )
    
    # Display results
    print("\n" + "="*70)
    print("VERIFICATION RESULTS")
    print("="*70)
    
    print(f"\n✅ KEPT REFERENCES: {len(result['filtered'])}")
    for i, ref in enumerate(result['filtered'], 1):
        title = ref.get('title', '')[:60]
        url = ref.get('url', '')
        metrics_key = url or f"ref_{i}"
        metric = result['metrics'].get(metrics_key, {})
        print(f"  [{i}] {title}... (score: {metric.get('overall_score', 'N/A'):.2f})")
    
    print(f"\n❌ REMOVED REFERENCES: {len(result['removed'])}")
    for i, ref in enumerate(result['removed'], 1):
        title = ref.get('title', '')[:60]
        reason = ref.get('removal_reason', 'low relevance')
        print(f"  [{i}] {title}... ({reason})")
    
    print("\n" + "="*70)
    print("QUALITY REPORT")
    print("="*70)
    print(result['quality_report'])
    
    # Verification assertions
    print("\n" + "="*70)
    print("VERIFICATION ASSERTIONS")
    print("="*70)
    
    # Should remove physics papers
    removed_titles = [r.get('title', '') for r in result['removed']]
    assert any('LaFeAsO' in t or 'physics' in t.lower() for t in removed_titles), \
        "❌ Failed: Physics paper (LaFeAsO) should be removed"
    print("✅ Physics papers correctly identified and removed")
    
    # Should remove laser-plasma papers
    assert any('激光' in t or 'laser' in t.lower() for t in removed_titles), \
        "❌ Failed: Laser-plasma paper should be removed"
    print("✅ Laser-plasma paper correctly identified and removed")
    
    # Should keep negotiation-related papers
    kept_titles = [r.get('title', '') for r in result['filtered']]
    assert any('谈判' in t or 'negotiation' in t.lower() for t in kept_titles), \
        "❌ Failed: Negotiation paper should be kept"
    print("✅ Negotiation papers correctly kept")
    
    print(f"\n✅ All assertions passed! Citation drift effectively prevented.")
    print(f"   Filtered from {len(PROBLEMATIC_REFERENCES)} → {len(result['filtered'])} relevant references")
    
    return result


def test_domain_classification():
    """Test domain classification"""
    print("\n" + "="*70)
    print("Domain Classification Test")
    print("="*70)
    
    verifier = CitationVerifier()
    domains = verifier.domain_classifier.classify(
        topic=DOCUMENT_TOPIC,
        section_outline="谈判策略与商业应用",
        full_content=DOCUMENT_SECTION,
    )
    
    print(f"🎯 Detected domains for '{DOCUMENT_TOPIC}': {domains}")
    assert "business" in domains, "Should detect business domain"
    print("✅ Domain classification working correctly")


if __name__ == "__main__":
    try:
        # Test domain classification
        test_domain_classification()
        
        # Test citation verification
        test_citation_verifier()
        
        print("\n" + "="*70)
        print("🎉 ALL TESTS PASSED!")
        print("="*70)
        print("\nThe Citation Verifier Agent successfully:")
        print("  1. ✅ Classified document domain as 'business'")
        print("  2. ✅ Removed irrelevant cross-domain papers (physics)")
        print("  3. ✅ Kept relevant negotiation papers")
        print("  4. ✅ Prevented citation drift from RAG noise pollution")
        print("\nNext steps:")
        print("  - Run end-to-end test with full document generation")
        print("  - Monitor Citation Verifier performance in production")
        print("  - Collect metrics on drift prevention effectiveness")
        
    except AssertionError as e:
        print(f"\n❌ Assertion failed: {e}")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Test failed with exception: {e}")
        sys.exit(1)
