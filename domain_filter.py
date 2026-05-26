"""
Domain Filter - 基于Abstract-to-IndexTerms相似度的文献过滤
================================================

Purpose:
- 在文献被加入Reference之前，进行领域相关性检查
- 比对文献的Abstract与整篇文档的Index Terms
- 过滤掉与主题无关的引用（相似度 < 阈值）
- 防止"引证漂移"（Cross-domain Drift）

Architecture:
1. Index Terms Extraction: 从文档标题、大纲、摘要中提取关键词
2. Citation Abstract Retrieval: 获取文献的摘要信息
3. Semantic Similarity: 使用sentence-transformers计算相似度
4. Domain Filter: 根据阈值过滤引用
"""

import re
import logging
from typing import List, Dict, Any, Optional, Set, Tuple
import os

try:
    from sentence_transformers import SentenceTransformer, util
    HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    HAS_SENTENCE_TRANSFORMERS = False

# Try to import additional domain keyword maps and red-flag lists
try:
    from citation_drift_prevention import DOMAIN_KEYWORD_MAP, CROSS_DOMAIN_RED_FLAGS
except Exception:
    DOMAIN_KEYWORD_MAP = {}
    CROSS_DOMAIN_RED_FLAGS = {}

# Optional Chinese tokenizer to improve index-term extraction
try:
    import jieba
    HAS_JIEBA = True
except Exception:
    HAS_JIEBA = False
logger = logging.getLogger(__name__)

# Configuration
DOMAIN_FILTER_ENABLED = os.getenv("DOMAIN_FILTER_ENABLED", "true").lower() == "true"
DOMAIN_FILTER_SIMILARITY_THRESHOLD = float(os.getenv("DOMAIN_FILTER_SIMILARITY_THRESHOLD", "0.30"))
DOMAIN_FILTER_MIN_INDEX_TERMS = int(os.getenv("DOMAIN_FILTER_MIN_INDEX_TERMS", "3"))
DOMAIN_FILTER_MODEL = os.getenv("DOMAIN_FILTER_MODEL", "sentence-transformers/paraphrase-MiniLM-L6-v2")
DOMAIN_FILTER_SEMANTIC_ENABLED = os.getenv("DOMAIN_FILTER_SEMANTIC_ENABLED", "false").lower() == "true"
DOMAIN_FILTER_SEMANTIC_ALLOW_NETWORK = os.getenv("DOMAIN_FILTER_SEMANTIC_ALLOW_NETWORK", "false").lower() == "true"


class IndexTermsExtractor:
    """从文档中提取关键词(Index Terms)"""

    def __init__(self):
        self.stopwords = {
            # 中文通用停用词
            "的", "一", "是", "在", "不", "了", "有", "和", "人", "这", "中", "大",
            "为", "上", "个", "国", "我", "以", "要", "他", "时", "来", "用", "们",
            "生", "到", "作", "地", "于", "出", "就", "分", "对", "成", "会", "可",
            "主", "发", "年", "动", "同", "工", "也", "能", "下", "过", "民", "而",
            "发", "后", "效", "制", "造", "去", "法", "子", "自", "式", "第", "又",
            "间", "因", "定", "帮", "多", "少", "又", "样", "把", "她", "给", "你",
            # 英文通用停用词
            "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
            "of", "with", "by", "from", "up", "about", "into", "is", "are", "be",
            "been", "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "must", "can", "shall",
        }

    def extract(
        self,
        title: str = "",
        outline: str = "",
        abstract: str = "",
        content_sample: str = "",
    ) -> Set[str]:
        """
        从文档各部分提取关键词
        
        Args:
            title: 文档标题
            outline: 文档大纲
            abstract: 文档摘要
            content_sample: 文档内容样本
            
        Returns:
            Set of normalized keywords (Chinese and English)
        """
        combined_text = f"{title} {outline} {abstract} {content_sample}".lower()
        
        # 中文关键词提取 - 基于词长和词频
        chinese_terms = self._extract_chinese_terms(combined_text)

        # 英文关键词提取
        english_terms = self._extract_english_terms(combined_text)

        # jieba-based Chinese tokenization (adds finer-grained tokens)
        jieba_terms = []
        if HAS_JIEBA:
            try:
                jieba_tokens = [t.strip() for t in jieba.cut(combined_text) if t.strip()]
                # filter stopwords and short tokens
                for t in jieba_tokens:
                    if t not in self.stopwords and len(t) >= 2:
                        jieba_terms.append(t)
            except Exception:
                jieba_terms = []

        # bigrams from English token extractor to capture short phrases
        bigrams = self._extract_bigrams(combined_text)

        # Include synonyms only for domains that are actually signaled by the
        # document. Adding every domain map pollutes broad topics such as
        # education with unrelated terms like "机器学习" or "供应链".
        synonym_terms = []
        try:
            combined_lower = combined_text.lower()
            weak_domain_signals = {
                "管理", "学习", "研究", "分析", "模型", "数据", "系统", "应用", "方法",
                "management", "study", "research", "analysis", "model", "data", "system",
            }
            for domain_name, v in (DOMAIN_KEYWORD_MAP or {}).items():
                kws = v.get("keywords") if isinstance(v, dict) else v
                if not kws:
                    continue
                kw_list = [str(kw).lower() for kw in kws if isinstance(kw, str) and kw]
                strong_hits = [
                    kw for kw in kw_list
                    if kw not in weak_domain_signals
                    and (len(kw) >= 4 or re.search(r"[\u4e00-\u9fff]{3,}", kw))
                    and kw in combined_lower
                ]
                domain_hit = str(domain_name or "").lower() in combined_lower or len(strong_hits) >= 1
                if not domain_hit:
                    continue
                synonym_terms.extend(kw_list)
        except Exception:
            synonym_terms = []

        # 合并并去重：保留有限数量以避免噪声
        all_terms = set(chinese_terms) | set(english_terms) | set(jieba_terms) | set(bigrams) | set(synonym_terms)
        # cap total terms to a reasonable size
        if len(all_terms) > 60:
            # prefer longer phrases first
            sorted_terms = sorted(all_terms, key=lambda s: (-len(s), s))
            all_terms = set(sorted_terms[:60])
        
        logger.info(f"🔍 Extracted {len(all_terms)} index terms: {list(all_terms)[:10]}")
        return all_terms

    def _extract_chinese_terms(self, text: str) -> List[str]:
        """提取中文关键词"""
        # 提取中文词汇（2-4个字的连续中文字符）
        chinese_pattern = r"[\u4e00-\u9fff]{2,8}"
        matches = re.findall(chinese_pattern, text)
        
        # 过滤停用词并计算频率
        term_freq: Dict[str, int] = {}
        for term in matches:
            if term not in self.stopwords and len(term) >= 2:
                term_freq[term] = term_freq.get(term, 0) + 1
        
        # 返回出现频率 >= 2 或字长 >= 3 的词
        filtered = [
            term for term, freq in term_freq.items()
            if freq >= 2 or len(term) >= 3
        ]
        
        return filtered

    def _extract_english_terms(self, text: str) -> List[str]:
        """提取英文关键词"""
        # 提取英文单词和短语
        words = re.findall(r"\b[a-z][a-z0-9\-]{1,30}\b", text)
        
        # 过滤停用词和太短的词
        filtered = [
            w for w in words
            if w not in self.stopwords and len(w) >= 3
        ]
        
        # 计算频率，返回高频词
        term_freq: Dict[str, int] = {}
        for term in filtered:
            term_freq[term] = term_freq.get(term, 0) + 1
        
        # 返回出现 >= 2 次的词
        high_freq = [term for term, freq in term_freq.items() if freq >= 2]
        
        return high_freq[:30]  # 限制返回数量

    def _extract_bigrams(self, text: str) -> List[str]:
        """提取二词短语"""
        words = re.findall(r"\b[a-z0-9]{2,}\b", text)
        bigrams = [
            f"{words[i]} {words[i+1]}"
            for i in range(len(words) - 1)
            if len(words[i]) >= 2 and len(words[i+1]) >= 2
        ]
        # Also add adjacent Chinese character bigrams as phrase hints
        try:
            chinese_chars = re.findall(r"[\u4e00-\u9fff]{2,}", text)
            for chunk in chinese_chars:
                for i in range(len(chunk) - 1):
                    big = chunk[i:i+2]
                    if len(big.strip()) >= 2:
                        bigrams.append(big)
        except Exception:
            pass

        return list(dict.fromkeys(bigrams))[:30]


class DomainSimilarityScorer:
    """计算引用与文档领域的相似度"""

    def __init__(self):
        self.model = None
        self.extractor = IndexTermsExtractor()
        if HAS_SENTENCE_TRANSFORMERS and DOMAIN_FILTER_SEMANTIC_ENABLED:
            if not DOMAIN_FILTER_SEMANTIC_ALLOW_NETWORK and not os.path.isdir(DOMAIN_FILTER_MODEL):
                logger.warning(
                    "⚠️  Domain semantic model enabled but model is not a local directory; "
                    "skipping load to avoid blocking final assembly on network/model download. "
                    "Set DOMAIN_FILTER_MODEL to a local path or DOMAIN_FILTER_SEMANTIC_ALLOW_NETWORK=true."
                )
                return
            try:
                self.model = SentenceTransformer(DOMAIN_FILTER_MODEL)
                logger.info(f"✅ DomainSimilarityScorer loaded model: {DOMAIN_FILTER_MODEL}")
            except Exception as e:
                logger.warning(f"⚠️  Failed to load semantic model: {e}")
        elif HAS_SENTENCE_TRANSFORMERS:
            logger.info("ℹ️ DomainSimilarityScorer semantic model disabled; using keyword matching")

    def compute_similarity(
        self,
        citation_abstract: str,
        index_terms: Set[str],
        debug: bool = False,
    ) -> float:
        """
        计算引用与文档领域的相似度
        
        Args:
            citation_abstract: 引用的摘要/描述
            index_terms: 文档的关键词集合
            debug: 是否打印调试信息
            
        Returns:
            Similarity score (0.0 - 1.0)
        """
        if not index_terms or not citation_abstract:
            return 0.0

        # 如果没有模型，进行关键词匹配
        if not self.model:
            return self._keyword_match_score(citation_abstract, index_terms)

        # 使用语义相似度
        try:
            return self._semantic_similarity(citation_abstract, index_terms, debug=debug)
        except Exception as e:
            logger.warning(f"⚠️  Semantic similarity failed: {e}, falling back to keyword matching")
            return self._keyword_match_score(citation_abstract, index_terms)

    def _keyword_match_score(self, text: str, keywords: Set[str]) -> float:
        """基于关键词匹配的相似度"""
        if not keywords:
            return 0.0

        text_lower = str(text).lower()
        matched_keywords = [str(kw) for kw in keywords if str(kw).lower() in text_lower]
        matched = len(matched_keywords)

        # 补偿：当没有 SBERT 时，尝试基于常见英文词到中文词的小映射增强匹配（轻量启发式）
        # 这可以帮助 "Sales Negotiation" 类的英文标题配上中文 index terms
        english_to_chinese = {
            "negotiation": "谈判",
            "sales": "销售",
            "strategy": "策略",
            "strategies": "策略",
            "communication": "沟通",
            "management": "管理",
            "student": "学生",
            "students": "学生",
            "college": "大学",
            "university": "大学",
            "learning": "学习",
            "study": "学习",
            "academic": "学业",
            "procrastination": "拖延",
            "self-regulated": "自我调节",
            "price": "价格",
            "marketing": "市场",
            "psychology": "心理",
        }
        try:
            import re as _re
            tokens = _re.findall(r"[a-zA-Z]+", str(text or "").lower())
            for t in set(tokens):
                mapped = english_to_chinese.get(t)
                if mapped and mapped in keywords:
                    # 如果映射的中文关键词尚未计入，则计数补偿
                    if not any(str(kw).lower() == mapped for kw in keywords):
                        # mapped not literally in keywords set (unlikely), skip
                        pass
                    else:
                        # 增加一个计数（但不超过关键词总量）
                        matched += 1
        except Exception:
            pass
        # 如果配置了额外的 domain keyword map，尝试根据当前文献文本检测跨域红旗
        try:
            text_lower = str(text or "").lower()
            # CROSS_DOMAIN_RED_FLAGS may be a dict of lists or a set
            red_flags = set()
            if isinstance(CROSS_DOMAIN_RED_FLAGS, dict):
                for v in CROSS_DOMAIN_RED_FLAGS.values():
                    if isinstance(v, (list, set)):
                        red_flags.update(set([str(x).lower() for x in v if x]))
            elif isinstance(CROSS_DOMAIN_RED_FLAGS, (list, set)):
                red_flags.update(set([str(x).lower() for x in CROSS_DOMAIN_RED_FLAGS if x]))

            for rf in red_flags:
                if rf and rf in text_lower:
                    # strong negative signal -> force very low score
                    return 0.0
        except Exception:
            pass

        # Use a bounded denominator. Full index-term sets include many local
        # Chinese fragments; requiring overlap with all of them unfairly rejects
        # relevant English academic records with sparse abstracts.
        score = matched / max(1, min(len(keywords), 8))

        if matched_keywords:
            try:
                logger.info(
                    f"[DomainFilter] keyword-overlap matched={matched}/{len(keywords)} "
                    f"sample={matched_keywords[:8]}"
                )
            except Exception:
                pass
        
        return min(1.0, score)

    def _semantic_similarity(
        self,
        citation_abstract: str,
        index_terms: Set[str],
        debug: bool = False,
    ) -> float:
        """使用sentence-transformers计算语义相似度
        
        Multi-layer approach:
        1. Semantic similarity via SBERT encoding
        2. Keyword presence check
        3. Composite scoring
        """
        if not self.model:
            return 0.0

        try:
            # 编码引用摘要
            abstract_embedding = self.model.encode(
                str(citation_abstract)[:500],
                convert_to_tensor=True
            )

            keywords_list = list(index_terms)[:15]
            
            if not keywords_list:
                return 0.0

            # 计算与关键词的相似度
            terms_embeddings = self.model.encode(keywords_list, convert_to_tensor=True)
            similarities = util.cos_sim(abstract_embedding, terms_embeddings)[0]
            similarities_list = similarities.cpu().numpy().tolist()

            # ================== LAYER 1: Semantic Similarity ==================
            # 使用平均相似度而不是最大值
            mean_sim = sum(similarities_list) / len(similarities_list)
            
            # ================== LAYER 2: Keyword Presence Check ==================
            # 计算有多少关键词在abstract中直接出现（子串匹配）
            abstract_lower = str(citation_abstract).lower()
            keywords_present = 0
            for kw in keywords_list:
                kw_lower = str(kw).lower()
                # 只在abstract中也出现的关键词计数
                if len(kw_lower) >= 2 and kw_lower in abstract_lower:
                    keywords_present += 1
            
            presence_ratio = keywords_present / len(keywords_list) if keywords_list else 0
            
            # ================== LAYER 3: High-Quality Match Count ==================
            # 统计高相似度的匹配（>= 0.55）
            high_quality_matches = sum(1 for s in similarities_list if s >= 0.55)
            high_quality_ratio = high_quality_matches / len(keywords_list)
            
            # ================== COMPOSITE SCORING ==================
            # 综合三个因素：
            # - semantic_similarity: 50% weight - 编码-level语义对齐
            # - keyword_presence: 30% weight - 直观的词汇重叠
            # - high_quality_match: 20% weight - 强相似度信号
            composite_score = (
                mean_sim * 0.50 +           # Semantic similarity
                presence_ratio * 0.30 +     # Keyword presence  
                high_quality_ratio * 0.20   # High-quality matches
            )

            if debug:
                logger.debug(
                    f"  Abstract: {citation_abstract[:60]}...\n"
                    f"  Terms: {keywords_list[:5]}\n"
                    f"  Mean Similarity: {mean_sim:.3f}\n"
                    f"  Presence Ratio: {presence_ratio:.3f}\n"
                    f"  High-Quality Ratio: {high_quality_ratio:.3f}\n"
                    f"  Composite Score: {composite_score:.3f}"
                )

            return composite_score

        except Exception as e:
            logger.warning(f"⚠️  Semantic similarity computation failed: {e}")
            return 0.0


class DomainFilter:
    """主过滤器 - 集成Index Terms提取和相似度计算"""

    def __init__(self):
        self.extractor = IndexTermsExtractor()
        self.scorer = DomainSimilarityScorer()
        self.enabled = DOMAIN_FILTER_ENABLED

    def extract_document_index_terms(
        self,
        title: str = "",
        outline: str = "",
        abstract: str = "",
        content_sample: str = "",
    ) -> Set[str]:
        """
        从文档提取Index Terms
        """
        terms = self.extractor.extract(
            title=title,
            outline=outline,
            abstract=abstract,
            content_sample=content_sample,
        )

        # 确保最少关键词数
        if len(terms) < DOMAIN_FILTER_MIN_INDEX_TERMS:
            logger.warning(
                f"⚠️  Too few index terms ({len(terms)}), "
                f"falling back to title-only extraction"
            )
            fallback_terms = set(re.findall(r"\b[a-z0-9]{3,}\b", title.lower()))
            terms = terms | fallback_terms

        return terms

    def filter_citations(
        self,
        citations: List[Dict[str, Any]],
        index_terms: Set[str],
        threshold: float = DOMAIN_FILTER_SIMILARITY_THRESHOLD,
        debug: bool = False,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        过滤引用列表，仅保留相关的文献
        
        Args:
            citations: 引用列表 [{"title": ..., "body": ..., "abstract": ..., "href": ...}, ...]
            index_terms: 文档关键词集合
            threshold: 相似度阈值 (默认 0.3)
            debug: 打印调试信息
            
        Returns:
            (filtered_citations, filtered_out_citations)
        """
        if not self.enabled or not citations or not index_terms:
            return citations, []

        filtered = []
        filtered_out = []

        for citation in citations:
            # 获取Abstract（优先顺序：abstract -> body -> title），但在无模型回退时
            # 同时包含 title 以便英文标题中的关键词能被启发式映射检测到
            title_text = str(citation.get("title") or "")
            abstract = (
                citation.get("abstract") or
                citation.get("body") or
                ""
            )
            scoring_text = f"{title_text} {abstract}".strip()

            if not abstract:
                filtered_out.append(citation)
                continue

            if debug:
                try:
                    print(
                        f"📎 [DomainFilter] scoring title={title_text[:80]} abstract={str(abstract)[:120]} terms={sorted(list(index_terms))[:12]}"
                    )
                except Exception:
                    pass

            # 计算相似度
            similarity = self.scorer.compute_similarity(scoring_text, index_terms, debug=debug)

            # Detailed per-candidate logging for debugging and tuning
            try:
                if debug:
                    matched_terms = [kw for kw in index_terms if str(kw).lower() in scoring_text.lower()]
                    print(
                        f"📎 [DomainFilter] candidate={title_text[:80]} | url={citation.get('href') or citation.get('url') or citation.get('link')} | "
                        f"similarity={similarity:.3f} | threshold={threshold:.3f} | matched_terms={matched_terms[:10]}"
                    )
                else:
                    logger.info(
                        f"[DomainFilter] Candidate: {title_text[:80]} | url={citation.get('href') or citation.get('url') or citation.get('link')} | similarity={similarity:.3f} | threshold={threshold:.3f}"
                    )
            except Exception:
                pass

            if debug:
                logger.debug(
                    f"  Citation: {citation.get('title', '')[:60]}\n"
                    f"  Similarity: {similarity:.3f} | Threshold: {threshold}"
                )

            # 过滤决策
            scored = dict(citation)
            scored["domain_similarity"] = similarity

            if similarity >= threshold:
                filtered.append(scored)
            else:
                filtered_out.append(scored)

        if debug or True:  # 总是输出统计信息
            if debug:
                print(
                    f"📊 Domain Filter Results: Total={len(citations)} | Kept={len(filtered)} | FilteredOut={len(filtered_out)}"
                )
            else:
                logger.info(
                    f"📊 Domain Filter Results:\n"
                    f"  Total: {len(citations)} | "
                    f"Kept: {len(filtered)} | "
                    f"Filtered Out: {len(filtered_out)}"
                )
            if filtered_out:
                if debug:
                    print(
                        "📊 [DomainFilter] filtered citations:\n" +
                        "\n".join([
                            f"    - {c.get('title', '')[:50]} | similarity={float(c.get('domain_similarity', 0.0)):.3f}"
                            for c in filtered_out[:5]
                        ])
                    )
                else:
                    logger.info(
                        f"  Filtered citations:\n" +
                        "\n".join([
                            f"    - {c.get('title', '')[:50]}"
                            for c in filtered_out[:5]
                        ])
                    )

        return filtered, filtered_out


# Singleton instance
_domain_filter_instance = None


def get_domain_filter() -> DomainFilter:
    """获取Domain Filter单例"""
    global _domain_filter_instance
    if _domain_filter_instance is None:
        _domain_filter_instance = DomainFilter()
    return _domain_filter_instance
