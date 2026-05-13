"""
Citation Verifier Agent
========================
Semantic relevance validation and reranking of citations to fix cross-domain drift.

Purpose:
- Filter out irrelevant references (Physics papers when topic is "Business Negotiation")
- Rerank citations by domain alignment with document topic and section content
- Enforce "relevant citation pools" to prevent RAG noise pollution
- Integrate with Verifier feedback loop for citation quality checking

Key Components:
1. DomainClassifier: Maps document topic to academic domains
2. CitationSemanticsScore: Measures semantic alignment (topic, section, reference title)
3. CitationVerifier: Filters, reranks, and validates entire reference list
4. Integration: Plugs into Verifier's check_sources() and web post-processing
"""

import re
import json
import logging
from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass
from urllib.parse import urlparse
import os

try:
    from sentence_transformers import SentenceTransformer, util
    HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    HAS_SENTENCE_TRANSFORMERS = False

# ============ Configuration ============
CITATION_VERIFIER_ENABLED = os.getenv("CITATION_VERIFIER_ENABLED", "true").lower() == "true"
CITATION_SEMANTIC_THRESHOLD = float(os.getenv("CITATION_SEMANTIC_THRESHOLD", "0.35"))
CITATION_STRICT_MODE = os.getenv("CITATION_STRICT_MODE", "false").lower() == "true"
CITATION_MIN_REFERENCES = int(os.getenv("CITATION_MIN_REFERENCES", "4"))
SEMANTIC_MODEL = os.getenv("CITATION_SEMANTIC_MODEL", "sentence-transformers/paraphrase-MiniLM-L6-v2")

# Domain-to-keywords mapping for cross-domain drift detection
DOMAIN_KEYWORDS = {
    "business": {"谈判", "商业", "市场", "销售", "管理", "企业", "合同", "战略", "经济", "竞争", "投资", "品牌"},
    "physics": {"物理", "量子", "粒子", "能量", "光子", "原子", "分子", "波", "震动", "力学", "热力学"},
    "medicine": {"医学", "病", "治疗", "药物", "诊断", "临床", "症状", "健康", "手术", "疾病"},
    "psychology": {"心理", "行为", "认知", "情感", "压力", "学习", "记忆", "大脑"},
    "computer_science": {"算法", "编程", "数据", "计算", "软件", "网络", "系统", "代码"},
    "engineering": {"工程", "设计", "结构", "材料", "机械", "电气", "建筑", "施工"},
    "biology": {"生物", "基因", "细胞", "进化", "生命", "蛋白质", "DNA", "微生物"},
    "chemistry": {"化学", "分子", "反应", "化合物", "元素", "催化", "氧化", "还原"},
}

# Merge external mapping if available (from citation_drift_prevention)
try:
    from citation_drift_prevention import DOMAIN_KEYWORD_MAP as _EXTERNAL_DOMAIN_KEYWORD_MAP, CROSS_DOMAIN_RED_FLAGS as _EXTERNAL_CROSS_DOMAIN_RED_FLAGS
except Exception:
    _EXTERNAL_DOMAIN_KEYWORD_MAP = {}
    _EXTERNAL_CROSS_DOMAIN_RED_FLAGS = {}

for _d, _meta in (_EXTERNAL_DOMAIN_KEYWORD_MAP or {}).items():
    try:
        kws = set(_meta.get("keywords", [])) if isinstance(_meta, dict) else set(_meta)
        if _d in DOMAIN_KEYWORDS:
            DOMAIN_KEYWORDS[_d].update(kws)
        else:
            DOMAIN_KEYWORDS[_d] = set(kws)
    except Exception:
        continue

# Normalize cross-domain red flags into a set for quick checks
CROSS_DOMAIN_RED_FLAGS = set()
if isinstance(_EXTERNAL_CROSS_DOMAIN_RED_FLAGS, dict):
    for v in _EXTERNAL_CROSS_DOMAIN_RED_FLAGS.values():
        if isinstance(v, (list, set)):
            CROSS_DOMAIN_RED_FLAGS.update(str(x).lower() for x in v if x)
elif isinstance(_EXTERNAL_CROSS_DOMAIN_RED_FLAGS, (list, set)):
    CROSS_DOMAIN_RED_FLAGS.update(str(x).lower() for x in _EXTERNAL_CROSS_DOMAIN_RED_FLAGS if x)

logger = logging.getLogger(__name__)


@dataclass
class CitationMetrics:
    """Citation semantic metrics"""
    title_similarity: float  # Similarity between title and topic/section
    domain_alignment: float  # How well does it fit the document domain
    cross_domain_risk: float  # Risk of cross-domain drift (1.0 = high risk)
    overall_score: float  # Weighted combination of above
    is_relevant: bool  # Whether to keep this citation
    reason: str  # Why it was kept or removed


class DomainClassifier:
    """Maps document topic/section to academic domains"""
    
    def __init__(self):
        self.domain_keywords = DOMAIN_KEYWORDS
        self.detected_domains: Set[str] = set()
        self.topic_embedding = None
        self.model = None
        if HAS_SENTENCE_TRANSFORMERS:
            try:
                self.model = SentenceTransformer(SEMANTIC_MODEL)
            except Exception as e:
                logger.warning(f"Failed to load semantic model: {e}, falling back to keyword matching")
    
    def classify(self, topic: str, section_outline: str = "", full_content: str = "") -> Set[str]:
        """Classify document into academic domains"""
        combined_text = f"{topic} {section_outline} {full_content}".lower()
        detected = set()
        
        # Keyword-based domain detection (supports Chinese substrings and English tokens)
        for domain, keywords in self.domain_keywords.items():
            # count keyword substrings present in combined_text
            count = 0
            for kw in keywords:
                try:
                    if kw.lower() in combined_text:
                        count += 1
                except Exception:
                    continue
            # consider domain detected when at least one keyword appears
            if count >= 1:
                detected.add(domain)
        
        self.detected_domains = detected if detected else {"general"}
        logger.info(f"🎯 Detected domains: {self.detected_domains}")
        return self.detected_domains
    
    def get_domain_keywords(self) -> Set[str]:
        """Get all keywords for detected domains"""
        keywords = set()
        for domain in self.detected_domains:
            if domain in self.domain_keywords:
                keywords.update(self.domain_keywords[domain])
        return keywords


class CitationSemanticScorer:
    """Scores semantic relevance of citations"""
    
    def __init__(self, domain_classifier: DomainClassifier):
        self.classifier = domain_classifier
        self.model = domain_classifier.model if HAS_SENTENCE_TRANSFORMERS else None
        self.title_cache = {}
    
    def score_citation(
        self,
        ref_title: str,
        ref_url: str,
        topic: str,
        section_content: str,
        domain_keywords: Optional[Set[str]] = None,
        context_text: str = "",
    ) -> CitationMetrics:
        """
        Compute semantic relevance score for a single citation.
        
        Args:
            ref_title: Reference title or metadata
            ref_url: URL (used for domain extraction)
            topic: Document main topic
            section_content: Section text where citation is used
            domain_keywords: Keywords of detected domains
        
        Returns:
            CitationMetrics with relevance assessment
        """
        domain_keywords = domain_keywords or self.classifier.get_domain_keywords()
        context_text = str(context_text or "").lower()
        
        # Extract citation metadata
        title_lower = ref_title.lower()
        title_tokens = set(re.findall(r"\w+", title_lower))
        domain_tokens = domain_keywords or set()
        context_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", context_text))
        
        # 1. Title-to-domain keyword overlap
        keyword_overlap = title_tokens & domain_tokens
        title_similarity = len(keyword_overlap) / max(1, len(domain_tokens))

        # 1b. User context alignment: keep citations close to the user's
        # background and extra requirements, not only the section outline.
        context_overlap = title_tokens & context_tokens
        context_alignment = len(context_overlap) / max(1, len(context_tokens))
        
        # 2. Cross-domain drift detection
        cross_domain_keywords = {"physics", "quantum", "particle", "laser", "plasma", "superconductor", "材料", "物理"}
        if title_lower in ["LaFeAsO", "激光等离子体", "超导"]:
            cross_domain_risk = 1.0
        else:
            cross_domain_drift_tokens = title_tokens & cross_domain_keywords
            cross_domain_risk = min(1.0, len(cross_domain_drift_tokens) / max(1, len(title_tokens)))

        # If external CROSS_DOMAIN_RED_FLAGS present, check title and context for them
        try:
            if CROSS_DOMAIN_RED_FLAGS:
                # Red flags must be tied to the citation itself. Checking the
                # whole generated document here can incorrectly mark every
                # reference as cross-domain when the body mentions unrelated
                # contrast examples or prior filtered terms.
                text_for_check = f"{title_lower} {str(ref_url or '').lower()}"
                for rf in CROSS_DOMAIN_RED_FLAGS:
                    if rf and rf in text_for_check:
                        cross_domain_risk = 1.0
                        break
        except Exception:
            pass
        
        # 3. Semantic similarity (if model available)
        domain_alignment = 0.0
        if self.model and topic:
            try:
                topic_embedding = self.model.encode(topic, convert_to_tensor=True)
                title_embedding = self.model.encode(ref_title, convert_to_tensor=True)
                cosine_score = util.cos_sim(topic_embedding, title_embedding).item()
                domain_alignment = max(0.0, cosine_score)
            except Exception as e:
                logger.debug(f"Semantic similarity computation failed: {e}")
                domain_alignment = title_similarity
        else:
            domain_alignment = title_similarity
        
        # 4. Overall score (weighted combination)
        # Heavily penalize cross-domain drift
        overall_score = (
            0.4 * domain_alignment
            + 0.25 * title_similarity
            + 0.2 * context_alignment
            - 0.15 * cross_domain_risk
        )
        overall_score = max(0.0, min(1.0, overall_score))
        
        # 5. Decision
        is_relevant = overall_score >= CITATION_SEMANTIC_THRESHOLD and cross_domain_risk < 0.7
        
        reason = ""
        if cross_domain_risk >= 0.7:
            reason = f"cross-domain risk too high ({cross_domain_risk:.2f})"
        elif overall_score < CITATION_SEMANTIC_THRESHOLD:
            reason = f"semantic score too low ({overall_score:.2f} < {CITATION_SEMANTIC_THRESHOLD})"
        else:
            reason = "passes domain alignment"
        
        return CitationMetrics(
            title_similarity=title_similarity,
            domain_alignment=domain_alignment,
            cross_domain_risk=cross_domain_risk,
            overall_score=overall_score,
            is_relevant=is_relevant,
            reason=reason,
        )


class CitationVerifier:
    """Main Citation Verifier Agent"""
    
    def __init__(self):
        self.domain_classifier = DomainClassifier()
        self.semantic_scorer = CitationSemanticScorer(self.domain_classifier)
        self.enabled = CITATION_VERIFIER_ENABLED
    
    def verify_and_rerank(
        self,
        references: List[Dict[str, Any]],
        topic: str,
        section_outline: str = "",
        full_content: str = "",
        context_text: str = "",
    ) -> Dict[str, Any]:
        """
        Verify and rerank citations by domain relevance.
        
        Args:
            references: List of reference dicts with keys: title, url, body (optional)
            topic: Document main topic
            section_outline: Section outline text
            full_content: Full section/document content
        
        Returns:
            {
                'filtered': List[Dict],  # Kept references in reranked order
                'removed': List[Dict],   # Removed references with reasons
                'metrics': Dict[str, CitationMetrics],  # Per-reference metrics
                'quality_report': str,   # Human-readable report
            }
        """
        if not self.enabled or not references:
            return {
                'filtered': references,
                'removed': [],
                'metrics': {},
                'quality_report': 'Citation verifier disabled or no references provided',
            }
        
        # Classify document domain
        self.domain_classifier.classify(
            topic=topic,
            section_outline=section_outline,
            full_content=full_content
        )
        domain_keywords = self.domain_classifier.get_domain_keywords()
        
        # Score each citation
        metrics = {}
        filtered = []
        removed = []
        
        for idx, ref in enumerate(references):
            title = ref.get('title', ref.get('body', '')[:100])
            url = ref.get('url', ref.get('href', ''))
            
            metric = self.semantic_scorer.score_citation(
                ref_title=title,
                ref_url=url,
                topic=topic,
                section_content=full_content,
                domain_keywords=domain_keywords,
                context_text=context_text or f"{topic}\n{section_outline}\n{full_content}",
            )
            
            metrics[url or f"ref_{idx}"] = metric
            
            if metric.is_relevant:
                filtered.append((metric.overall_score, ref))
            else:
                removed.append({**ref, 'removal_reason': metric.reason})
        
        # Rerank filtered references by overall score (descending)
        filtered.sort(key=lambda x: x[0], reverse=True)
        filtered = [ref for _, ref in filtered]
        
        # Ensure minimum reference count
        if len(filtered) < CITATION_MIN_REFERENCES and removed:
            logger.warning(f"⚠️ Only {len(filtered)} references after filtering, restoring top removed ones")
            shortage = CITATION_MIN_REFERENCES - len(filtered)
            removed_sorted = sorted(
                removed,
                key=lambda x: metrics.get(x.get('url', x.get('href', '')), CitationMetrics(0, 0, 1, 0, False, '')).overall_score,
                reverse=True
            )
            # Restore top removed entries, removing them safely from the `removed` list
            to_restore = removed_sorted[:min(shortage, len(removed_sorted))]
            filtered.extend(to_restore)
            # Remove restored items from `removed` by identity of url/href
            restored_urls = {str(x.get('url') or x.get('href') or '') for x in to_restore}
            removed = [r for r in removed if str(r.get('url') or r.get('href') or '') not in restored_urls]
        
        # Generate quality report
        quality_report = self._generate_quality_report(
            topic=topic,
            filtered=filtered,
            removed=removed,
            metrics=metrics,
        )
        
        return {
            'filtered': filtered,
            'removed': removed,
            'metrics': {k: v.__dict__ for k, v in metrics.items()},
            'quality_report': quality_report,
        }
    
    def _generate_quality_report(
        self,
        topic: str,
        filtered: List[Dict],
        removed: List[Dict],
        metrics: Dict,
    ) -> str:
        """Generate human-readable quality report"""
        lines = [
            f"📋 Citation Verification Report",
            f"━" * 50,
            f"Topic: {topic}",
            f"Detected domains: {', '.join(self.domain_classifier.detected_domains)}",
            f"",
            f"✅ Kept: {len(filtered)} references",
            f"❌ Removed: {len(removed)} references",
            f"",
        ]
        
        if removed:
            lines.append("Removed references:")
            for ref in removed[:5]:  # Show top 5 removed
                title = ref.get('title', '')[:60]
                reason = ref.get('removal_reason', 'low relevance')
                lines.append(f"  - {title}... ({reason})")
            if len(removed) > 5:
                lines.append(f"  ... and {len(removed) - 5} more")
        
        return "\n".join(lines)


# ============ Integration Functions ============

def verify_references(
    references: List[Dict[str, Any]],
    topic: str,
    section_outline: str = "",
    full_content: str = "",
    context_text: str = "",
) -> Tuple[List[Dict[str, Any]], str]:
    """
    Standalone function to verify references.
    
    Returns:
        (filtered_references, quality_report)
    """
    verifier = CitationVerifier()
    result = verifier.verify_and_rerank(
        references=references,
        topic=topic,
        section_outline=section_outline,
        full_content=full_content,
        context_text=context_text,
    )
    return result['filtered'], result['quality_report']


if __name__ == "__main__":
    # Quick test
    logging.basicConfig(level=logging.DEBUG)
    
    test_refs = [
        {"title": "谈判策略: 双赢框架", "url": "https://arxiv.org/abs/2001.00000"},
        {"title": "LaFeAsO超导体的物理性质", "url": "https://arxiv.org/abs/2001.11111"},
        {"title": "商业谈判中的心理学", "url": "https://example.com/psych"},
        {"title": "激光与等离子体互作", "url": "https://arxiv.org/abs/2001.22222"},
    ]
    
    verifier = CitationVerifier()
    result = verifier.verify_and_rerank(
        references=test_refs,
        topic="谈判策略",
        full_content="本文介绍商业谈判的基本原则和实战技巧...",
    )
    
    print(result['quality_report'])
    print(f"\nFiltered: {[r['title'] for r in result['filtered']]}")
    print(f"Removed: {[r['title'] for r in result['removed']]}")
