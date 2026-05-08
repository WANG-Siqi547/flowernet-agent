from typing import Dict, Any, List, Tuple
import re
import time
import html
import os
from urllib.parse import unquote, urlparse, parse_qs

import requests
import os as _os


class RAGSearchEngine:
    def __init__(self, max_results: int = 5, timeout: int = 10):
        self.max_results = max_results
        self.timeout = timeout
        self.available = True
        self.session = requests.Session()
        self.session.trust_env = False
        self._user_agent = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
        self._blocked_hosts = {"duckduckgo.com", "html.duckduckgo.com"}
        self._query_stopwords = {
            "section", "subsection", "outline", "prompt", "write", "writing",
            "chapter", "part", "introduction", "conclusion", "要求", "生成", "内容",
            "小节", "章节", "大纲", "写作", "说明", "包括", "以及", "关于",
            "指南", "方法", "体系", "策略", "研究", "分析", "综述", "实践"
        }
        self.include_social_cn = os.getenv("RAG_INCLUDE_SOCIAL_CN", "false").lower() == "true"
        self.include_social_global = os.getenv("RAG_INCLUDE_SOCIAL_GLOBAL", "false").lower() == "true"
        self.include_academic_sources = os.getenv("RAG_INCLUDE_ACADEMIC_SOURCES", "true").lower() == "true"
        self.min_topic_alignment = float(os.getenv("RAG_MIN_TOPIC_ALIGNMENT", "0.18"))
        self.safe_min_results = max(1, int(os.getenv("RAG_SAFE_MIN_RESULTS", "1")))
        self.safe_backfill_enabled = os.getenv("RAG_SAFE_BACKFILL_ENABLED", "true").lower() == "true"
        self.high_quality_domains = {
            "nature.com", "science.org", "sciencedirect.com", "springer.com", "ieee.org",
            "acm.org", "arxiv.org", "ncbi.nlm.nih.gov", "who.int", "oecd.org", "un.org",
            "nist.gov", "nih.gov", "gov.cn", "edu.cn", "ruc.edu.cn", "tsinghua.edu.cn",
            "pku.edu.cn", "cass.cn", "moe.gov.cn", "researchgate.net", "doi.org",
            "eric.ed.gov", "apa.org", "psycnet.apa.org", "tandfonline.com", "sagepub.com",
            "frontiersin.org", "mdpi.com", "cambridge.org", "oxfordacademic.com",
        }
        self.low_quality_domains = {
            "baike.baidu.com", "zhidao.baidu.com", "tieba.baidu.com", "jingyan.baidu.com",
            "m.baidu.com", "t.co", "bit.ly", "tinyurl.com",
        }
        self.social_quality_domains = {
            "zhihu.com", "bilibili.com", "weibo.com", "xiaohongshu.com", "douyin.com",
            "x.com", "twitter.com", "reddit.com", "linkedin.com", "substack.com",
            "medium.com", "youtube.com", "facebook.com", "instagram.com",
        }
        self._domain_profiles = {
            "education": {
                "signals": [
                    "大学", "学生", "新生", "学习", "学习习惯", "时间管理", "自我调节", "自主学习",
                    "复习", "拖延", "课堂", "高校", "教育", "student", "students", "college",
                    "university", "learning", "study habits", "time management", "self-regulated",
                    "procrastination",
                ],
                "expansions": [
                    "college student time management",
                    "university students study habits",
                    "self-regulated learning university students",
                    "academic procrastination college students",
                    "learning strategies higher education",
                ],
                "required_any": [
                    "student", "students", "college", "university", "learning", "study", "academic",
                    "education", "time management", "self-regulated", "procrastination",
                    "学生", "大学", "高校", "学习", "时间管理", "拖延",
                ],
                "reject": [
                    "construction", "engineering", "thermal power", "building", "algorithm",
                    "sequence-to-sequence", "spectral sequence", "mining frequent sequence",
                    "fixed point", "cyclic group", "施工", "工程", "建筑", "热电", "算法",
                    "机器学习", "供应链",
                ],
                "preferred_domains": [
                    "eric.ed.gov", "apa.org", "psycnet.apa.org", "springer.com", "sciencedirect.com",
                    "tandfonline.com", "sagepub.com", "frontiersin.org", "doi.org",
                ],
                "min_alignment": 0.35,
            },
            "business": {
                "signals": ["谈判", "商务", "商业", "企业", "供应链", "采购", "negotiation", "business", "procurement"],
                "expansions": ["business negotiation", "negotiation strategy", "commercial negotiation"],
                "required_any": ["negotiation", "business", "commercial", "procurement", "谈判", "商务", "商业"],
                "reject": ["quantum", "protein", "gene", "spectral sequence", "量子", "基因", "蛋白质"],
                "preferred_domains": ["hbr.org", "ssrn.com", "sciencedirect.com", "springer.com", "doi.org"],
                "min_alignment": 0.28,
            },
            "technology": {
                "signals": ["算法", "编程", "软件", "机器学习", "ai", "algorithm", "software", "machine learning"],
                "expansions": ["computer science", "software engineering", "machine learning"],
                "required_any": ["algorithm", "software", "computer", "machine learning", "算法", "软件", "机器学习"],
                "reject": ["student time management", "商业谈判"],
                "preferred_domains": ["arxiv.org", "acm.org", "ieee.org", "springer.com"],
                "min_alignment": 0.25,
            },
            "medicine": {
                "signals": ["医学", "临床", "疾病", "治疗", "诊断", "患者", "护理", "药物", "medical", "clinical", "patient", "disease", "treatment"],
                "expansions": ["clinical study", "systematic review", "medical evidence", "patient outcomes"],
                "required_any": ["clinical", "medical", "patient", "disease", "treatment", "diagnosis", "医学", "临床", "患者", "治疗", "诊断"],
                "reject": ["business negotiation", "software architecture", "spectral sequence", "商业谈判", "施工", "供应链"],
                "preferred_domains": ["pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov", "nih.gov", "who.int", "thelancet.com", "bmj.com", "nejm.org", "cochranelibrary.com", "doi.org"],
                "min_alignment": 0.32,
            },
            "law": {
                "signals": ["法律", "法规", "合同", "司法", "法院", "判例", "合规", "law", "legal", "regulation", "court", "contract"],
                "expansions": ["legal analysis", "law review", "regulatory framework", "case law"],
                "required_any": ["law", "legal", "regulation", "court", "contract", "法律", "法规", "司法", "法院", "合同"],
                "reject": ["clinical trial", "machine learning", "quantum", "临床", "算法", "量子"],
                "preferred_domains": ["law.cornell.edu", "supreme.justia.com", "ssrn.com", "heinonline.org", "doi.org", "gov.cn", "edu.cn"],
                "min_alignment": 0.30,
            },
            "finance": {
                "signals": ["金融", "投资", "股票", "银行", "风险", "资产", "财务", "finance", "financial", "investment", "bank", "risk", "asset"],
                "expansions": ["financial risk", "investment management", "corporate finance", "banking regulation"],
                "required_any": ["finance", "financial", "investment", "bank", "risk", "asset", "金融", "投资", "银行", "风险", "资产", "财务"],
                "reject": ["clinical", "construction", "spectral sequence", "临床", "施工", "量子"],
                "preferred_domains": ["imf.org", "worldbank.org", "bis.org", "nber.org", "ssrn.com", "sciencedirect.com", "doi.org"],
                "min_alignment": 0.30,
            },
            "environment": {
                "signals": ["环境", "气候", "碳", "生态", "污染", "能源", "可持续", "climate", "carbon", "environment", "sustainability", "pollution"],
                "expansions": ["climate change", "environmental sustainability", "carbon emissions", "pollution control"],
                "required_any": ["climate", "carbon", "environment", "sustainability", "pollution", "energy", "环境", "气候", "碳", "生态", "污染", "能源"],
                "reject": ["student affairs", "business negotiation", "spectral sequence", "学生事务", "商业谈判", "谱序列"],
                "preferred_domains": ["ipcc.ch", "un.org", "oecd.org", "nature.com", "science.org", "sciencedirect.com", "springer.com", "doi.org"],
                "min_alignment": 0.30,
            },
            "social_science": {
                "signals": ["社会", "文化", "政策", "治理", "人口", "社区", "传播", "social", "policy", "governance", "culture", "community"],
                "expansions": ["social science research", "public policy", "governance study", "community development"],
                "required_any": ["social", "policy", "governance", "culture", "community", "社会", "政策", "治理", "文化", "社区"],
                "reject": ["clinical trial", "quantum", "algorithm", "临床", "量子", "算法"],
                "preferred_domains": ["oecd.org", "un.org", "worldbank.org", "sagepub.com", "tandfonline.com", "springer.com", "doi.org"],
                "min_alignment": 0.30,
            },
            "humanities": {
                "signals": ["历史", "文学", "艺术", "文物", "考古", "瓷器", "纹样", "明代", "清代", "history", "literature", "art", "archaeology", "porcelain", "ceramic"],
                "expansions": ["art history", "cultural history", "archaeology study", "material culture", "porcelain ceramics decorative patterns"],
                "required_any": ["history", "art", "archaeology", "culture", "porcelain", "ceramic", "历史", "艺术", "考古", "文化", "瓷器", "纹样", "明代", "清代"],
                "reject": ["clinical trial", "machine learning", "financial risk", "construction", "临床", "机器学习", "金融风险", "施工"],
                "preferred_domains": ["jstor.org", "cambridge.org", "oxfordacademic.com", "tandfonline.com", "springer.com", "doi.org"],
                "min_alignment": 0.30,
            },
        }

    def search(self, query: str) -> Dict[str, Any]:
        try:
            started_at = time.time()
            query_candidates = self._build_query_candidates(query)
            results: List[Dict[str, Any]] = []
            last_error = "no_results_parsed"

            if self.include_academic_sources:
                academic_results = self._search_academic_sources(query)
                if academic_results:
                    ranked_academic = self._rank_results(query, academic_results)
                    if len(ranked_academic) >= self.safe_min_results:
                        return {
                            "success": True,
                            "query": query,
                            "effective_query": query,
                            "results": ranked_academic,
                            "search_time": round(time.time() - started_at, 3),
                            "error": None,
                            "source_type": "academic",
                        }
                if self.safe_backfill_enabled:
                    safe_ranked = self._safe_backfill_results(query, academic_results)
                    if safe_ranked:
                        return {
                            "success": True,
                            "query": query,
                            "effective_query": query,
                            "results": safe_ranked,
                            "search_time": round(time.time() - started_at, 3),
                            "error": None,
                            "source_type": "academic_safe_backfill",
                        }

            for query_candidate in query_candidates:
                raw_html, fetch_error = self._fetch_duckduckgo_html(query_candidate)
                if raw_html:
                    results = self._parse_results(raw_html, self.max_results)
                    if results:
                        ranked_results = self._rank_results(query_candidate, results)
                        if ranked_results:
                            return {
                                "success": True,
                                "query": query,
                                "effective_query": query_candidate,
                                "results": ranked_results,
                                "search_time": round(time.time() - started_at, 3),
                                "error": None,
                                "source_type": "web",
                            }
                if fetch_error:
                    last_error = fetch_error

            fallback_candidates = list(query_candidates)
            if query:
                extra_short = self._compact_query(str(query)[:80])
                if extra_short and extra_short not in fallback_candidates:
                    fallback_candidates.append(extra_short)

            for fallback_query in fallback_candidates:
                fallback_results = self._search_wikipedia(fallback_query)
                if fallback_results:
                    ranked_fallback = self._rank_results(fallback_query, fallback_results)
                    if ranked_fallback:
                        return {
                            "success": True,
                            "query": query,
                            "effective_query": fallback_query,
                            "results": ranked_fallback,
                            "search_time": round(time.time() - started_at, 3),
                            "error": "fallback_wikipedia",
                            "source_type": "wiki",
                        }

            return {
                "success": False,
                "query": query,
                "results": [],
                "search_time": round(time.time() - started_at, 3),
                "error": last_error,
            }
        except Exception as exc:
            return {
                "success": False,
                "query": query,
                "results": [],
                "error": str(exc),
            }

    def _build_query_candidates(self, query: str) -> List[str]:
        query_text = " ".join(str(query or "").split())[:300].strip()
        if not query_text:
            return []

        compact = self._compact_query(query_text)
        semantic = self._semantic_query(query_text)
        first_sentence = re.split(r"[。！？.!?]\s*", query_text)[0].strip()[:140]

        candidates: List[str] = [query_text, compact, semantic, first_sentence]
        if self.include_social_cn and semantic:
            candidates.extend([
                f"{semantic} site:xiaohongshu.com",
                f"{semantic} site:douyin.com",
                f"{semantic} site:weibo.com",
                f"{semantic} site:zhihu.com",
                f"{semantic} site:bilibili.com",
                f"{semantic} 小红书",
                f"{semantic} 抖音",
                f"{semantic} 微博",
                f"{semantic} 知乎",
                f"{semantic} B站",
            ])
        if self.include_social_global and semantic:
            candidates.extend([
                f"{semantic} site:x.com",
                f"{semantic} site:twitter.com",
                f"{semantic} site:reddit.com",
                f"{semantic} site:linkedin.com",
                f"{semantic} site:substack.com",
            ])
        if self.include_academic_sources and semantic:
            candidates.extend([
                f"{semantic} site:arxiv.org",
                f"{semantic} site:ssrn.com",
                f"{semantic} site:scholar.google.com",
                f"{semantic} site:springer.com",
                f"{semantic} site:ieee.org",
            ])
        dedup: List[str] = []
        seen = set()
        for candidate in candidates:
            cleaned = " ".join(str(candidate or "").split()).strip()
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            dedup.append(cleaned)

        return dedup[:8]

    def _search_academic_sources(self, query: str) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        seen: set[str] = set()
        raw_limit = max(self.max_results * 4, 12)
        query_text = " ".join(str(query or "").split())[:180]
        semantic_query = self._semantic_query(query_text)
        profile_name, profile = self._infer_domain_profile(query_text)
        academic_queries = self._academic_queries(query_text, semantic_query, profile)

        for crossref_query in academic_queries:
            for item in self._search_crossref(crossref_query, max_items=raw_limit):
                href = str(item.get("href", ""))
                if href and href not in seen:
                    seen.add(href)
                    results.append(item)
                    if len(results) >= raw_limit:
                        return results

        # arXiv is valuable for technical topics but a frequent drift source for
        # education/business writing, so keep it profile-gated.
        if profile_name == "technology":
            arxiv_candidates = [query_text]
            if semantic_query and semantic_query not in arxiv_candidates:
                arxiv_candidates.append(semantic_query)
            for arxiv_query in arxiv_candidates:
                for item in self._search_arxiv(arxiv_query):
                    href = str(item.get("href", ""))
                    if href and href not in seen:
                        seen.add(href)
                        results.append(item)
                        if len(results) >= raw_limit:
                            return results

        targeted_domains = ["ssrn.com", "scholar.google.com", "springer.com", "sciencedirect.com"]
        if profile_name == "education":
            targeted_domains = ["eric.ed.gov", "apa.org", "tandfonline.com", "sagepub.com", "springer.com"]
        elif profile_name == "medicine":
            targeted_domains = ["pubmed.ncbi.nlm.nih.gov", "who.int", "cochranelibrary.com", "bmj.com"]
        elif profile_name == "law":
            targeted_domains = ["law.cornell.edu", "ssrn.com", "justia.com", "gov.cn"]
        elif profile_name == "finance":
            targeted_domains = ["imf.org", "worldbank.org", "bis.org", "nber.org", "ssrn.com"]
        elif profile_name == "environment":
            targeted_domains = ["ipcc.ch", "un.org", "oecd.org", "nature.com", "sciencedirect.com"]
        elif profile_name == "social_science":
            targeted_domains = ["oecd.org", "un.org", "worldbank.org", "sagepub.com", "tandfonline.com"]
        elif profile_name == "humanities":
            targeted_domains = ["jstor.org", "cambridge.org", "oxfordacademic.com", "tandfonline.com", "springer.com"]

        for domain in targeted_domains:
            for item in self._search_site_targeted(academic_queries[0] if academic_queries else query_text, domain):
                href = str(item.get("href", ""))
                if href and href not in seen:
                    seen.add(href)
                    results.append(item)
                    if len(results) >= raw_limit:
                        return results

        return results

    def _safe_backfill_results(self, query: str, existing: List[Dict[str, Any]] | None = None) -> List[Dict[str, Any]]:
        query_text = " ".join(str(query or "").split())[:180]
        semantic_query = self._semantic_query(query_text)
        profile_name, profile = self._infer_domain_profile(query_text)
        candidates: List[Dict[str, Any]] = []
        seen: set[str] = set()

        for item in existing or []:
            href = str((item or {}).get("href") or "")
            if href and href not in seen:
                seen.add(href)
                candidates.append(item)

        backfill_queries = self._safe_backfill_queries(query_text, semantic_query, profile)
        for backfill_query in backfill_queries:
            for item in self._search_crossref(backfill_query, max_items=max(10, self.max_results * 4)):
                href = str(item.get("href", ""))
                if href and href not in seen:
                    seen.add(href)
                    candidates.append(item)

            ranked = self._rank_results(query, candidates)
            if len(ranked) >= self.safe_min_results:
                return ranked

        targeted_domains = list(profile.get("preferred_domains", []) or [])[:4]
        for domain in targeted_domains:
            if domain == "doi.org":
                continue
            for backfill_query in backfill_queries[:2]:
                for item in self._search_site_targeted(backfill_query, domain):
                    href = str(item.get("href", ""))
                    if href and href not in seen:
                        seen.add(href)
                        candidates.append(item)
                ranked = self._rank_results(query, candidates)
                if len(ranked) >= self.safe_min_results:
                    return ranked

        return self._rank_results(query, candidates)

    def _safe_backfill_queries(self, query_text: str, semantic_query: str, profile: Dict[str, Any]) -> List[str]:
        queries: List[str] = []
        for expansion in profile.get("expansions", [])[:5]:
            expansion = str(expansion or "").strip()
            if expansion:
                queries.extend([
                    expansion,
                    f"{expansion} review",
                    f"{expansion} empirical study",
                ])

        required_terms = [str(x).strip() for x in profile.get("required_any", []) if str(x).strip()]
        if required_terms:
            queries.append(" ".join(required_terms[:4]))
            queries.append(" ".join(required_terms[:6]))

        if semantic_query:
            queries.append(semantic_query)
            queries.append(f"{semantic_query} review")
        if query_text:
            queries.append(query_text)

        dedup: List[str] = []
        seen: set[str] = set()
        for query in queries:
            cleaned = " ".join(str(query or "").split()).strip()
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            dedup.append(cleaned[:180])
        return dedup[:10]

    def _infer_domain_profile(self, query: str) -> Tuple[str, Dict[str, Any]]:
        text = str(query or "").lower()
        best_name = ""
        best_profile: Dict[str, Any] = {}
        best_hits = 0
        for name, profile in self._domain_profiles.items():
            hits = sum(1 for signal in profile.get("signals", []) if str(signal).lower() in text)
            if hits > best_hits:
                best_name = name
                best_profile = profile
                best_hits = hits
        if best_profile:
            return best_name, best_profile
        return "generic", self._build_generic_profile(query)

    def _build_generic_profile(self, query: str) -> Dict[str, Any]:
        anchors = self._extract_anchor_terms(query, limit=8)
        expansions = []
        if anchors:
            anchor_query = " ".join(anchors[:4])
            expansions = [
                f"{anchor_query} research",
                f"{anchor_query} systematic review",
                f"{anchor_query} empirical study",
            ]
        return {
            "signals": anchors,
            "expansions": expansions,
            "required_any": anchors,
            "reject": self._generic_cross_domain_reject_terms(anchors),
            "preferred_domains": [
                "doi.org", "springer.com", "sciencedirect.com", "tandfonline.com", "sagepub.com",
                "cambridge.org", "oxfordacademic.com", "nature.com", "science.org", "jstor.org",
                "ssrn.com", "oecd.org", "un.org", "worldbank.org",
            ],
            "min_alignment": 0.34 if anchors else 0.45,
            "generic": True,
        }

    def _extract_anchor_terms(self, text: str, limit: int = 8) -> List[str]:
        raw_tokens = re.findall(r"[A-Za-z\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff\-_]{1,30}", text or "")
        anchors: List[str] = []

        def add_anchor(value: str) -> None:
            normalized_value = value.strip().lower()
            if not normalized_value or normalized_value in self._query_stopwords or normalized_value.isdigit():
                return
            if len(normalized_value) <= 1 or len(normalized_value) > 28:
                return
            if re.fullmatch(r"[\u4e00-\u9fff]{1}", normalized_value):
                return
            if normalized_value not in anchors:
                anchors.append(normalized_value)

        for token in raw_tokens:
            normalized = token.strip().lower()
            if re.fullmatch(r"[\u4e00-\u9fff]{4,}", normalized):
                add_anchor(normalized)
                for n in (4, 3, 2):
                    for i in range(0, max(0, len(normalized) - n + 1)):
                        add_anchor(normalized[i:i + n])
                        if len(anchors) >= limit:
                            break
                    if len(anchors) >= limit:
                        break
            else:
                add_anchor(normalized)
            if len(anchors) >= limit:
                break
        return anchors

    def _generic_cross_domain_reject_terms(self, anchors: List[str]) -> List[str]:
        reject_pool = {
            "physics": ["quantum", "particle", "superconduct", "spectral sequence", "量子", "粒子", "超导"],
            "biology": ["gene", "protein", "cell", "dna", "基因", "蛋白质", "细胞"],
            "engineering": ["construction", "concrete", "thermal power", "施工", "混凝土", "热电"],
            "cs": ["sequence-to-sequence", "neural network", "algorithm", "算法", "神经网络"],
            "medicine": ["clinical trial", "patient", "disease", "临床", "患者", "疾病"],
            "business": ["business negotiation", "procurement", "supply chain", "商业谈判", "采购", "供应链"],
        }
        anchor_text = " ".join(anchors).lower()
        rejects: List[str] = []
        for terms in reject_pool.values():
            if any(str(term).lower() in anchor_text for term in terms):
                continue
            rejects.extend(terms)
        return rejects[:80]

    def _academic_queries(self, query_text: str, semantic_query: str, profile: Dict[str, Any]) -> List[str]:
        candidates: List[str] = []
        for expansion in profile.get("expansions", [])[:5]:
            candidates.append(str(expansion))
        if semantic_query:
            candidates.append(semantic_query)
        if query_text:
            candidates.append(query_text)
        dedup: List[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            cleaned = " ".join(str(candidate or "").split()).strip()
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            dedup.append(cleaned[:180])
        return dedup[:6]

    def _search_crossref(self, query: str, max_items: int | None = None) -> List[Dict[str, Any]]:
        if not query:
            return []
        try:
            rows = max(2, int(max_items or self.max_results))
            response = self.session.get(
                "https://api.crossref.org/works",
                params={
                    "query": query,
                    "rows": rows,
                    "sort": "relevance",
                    "order": "desc",
                },
                timeout=self.timeout,
                headers={"User-Agent": self._user_agent},
            )
            if response.status_code != 200:
                return []

            payload = response.json() if response.content else {}
            items = ((payload or {}).get("message") or {}).get("items") or []
            results: List[Dict[str, Any]] = []
            for item in items:
                title_list = item.get("title") or []
                title = self._strip_html(str(title_list[0] if title_list else "")).strip()
                if not title:
                    continue

                doi = str(item.get("DOI") or "").strip()
                url = str(item.get("URL") or "").strip()
                href = url or (f"https://doi.org/{doi}" if doi else "")
                if not href:
                    continue

                authors = item.get("author") or []
                author_names: List[str] = []
                for author in authors[:3]:
                    given = str((author or {}).get("given") or "").strip()
                    family = str((author or {}).get("family") or "").strip()
                    full_name = " ".join(x for x in [given, family] if x).strip()
                    if full_name:
                        author_names.append(full_name)
                year_parts = ((item.get("issued") or {}).get("date-parts") or [])
                year = ""
                if year_parts and isinstance(year_parts[0], list) and year_parts[0]:
                    year = str(year_parts[0][0])

                source = str(item.get("container-title", [""])[0] if item.get("container-title") else "").strip()
                summary_bits = []
                if author_names:
                    summary_bits.append(", ".join(author_names))
                if year:
                    summary_bits.append(year)
                if source:
                    summary_bits.append(source)

                results.append(
                    {
                        "title": title,
                        "body": " | ".join(summary_bits)[:400],
                        "href": href,
                        "source": "crossref.org",
                    }
                )
                if len(results) >= rows:
                    break
            return results
        except Exception:
            return []

    def _search_arxiv(self, query: str) -> List[Dict[str, Any]]:
        if not query:
            return []
        try:
            api_url = "http://export.arxiv.org/api/query"
            response = self.session.get(
                api_url,
                params={
                    "search_query": f"all:{query}",
                    "start": 0,
                    "max_results": self.max_results,
                    "sortBy": "relevance",
                    "sortOrder": "descending",
                },
                timeout=self.timeout,
                headers={"User-Agent": self._user_agent},
            )
            if response.status_code != 200 or not response.text:
                return []

            import xml.etree.ElementTree as ET

            ns = {
                "atom": "http://www.w3.org/2005/Atom",
                "arxiv": "http://arxiv.org/schemas/atom",
            }
            root = ET.fromstring(response.text)
            results: List[Dict[str, Any]] = []
            for entry in root.findall("atom:entry", ns):
                title = self._strip_html(" ".join(entry.findtext("atom:title", default="", namespaces=ns).split()))
                summary = self._strip_html(" ".join(entry.findtext("atom:summary", default="", namespaces=ns).split()))
                href = ""
                for link in entry.findall("atom:link", ns):
                    if link.attrib.get("rel") == "alternate" and link.attrib.get("href"):
                        href = link.attrib["href"]
                        break
                if not href:
                    id_text = entry.findtext("atom:id", default="", namespaces=ns)
                    href = id_text.strip()
                if not title or not href:
                    continue
                results.append(
                    {
                        "title": title,
                        "body": summary[:400],
                        "href": href,
                        "source": "arxiv.org",
                    }
                )
                if len(results) >= self.max_results:
                    break
            return results
        except Exception:
            return []

    def _search_site_targeted(self, query: str, domain: str) -> List[Dict[str, Any]]:
        if not query or not domain:
            return []
        site_query = f"{query} site:{domain}"
        raw_html, _ = self._fetch_duckduckgo_html(site_query)
        if not raw_html:
            return []
        items = self._parse_results(raw_html, self.max_results)
        filtered: List[Dict[str, Any]] = []
        for item in items:
            href = str(item.get("href", ""))
            if domain in self._extract_domain(href):
                filtered.append(item)
        return filtered[: self.max_results]

    def _tokenize_query(self, text: str) -> List[str]:
        tokens = re.findall(r"[A-Za-z\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff\-_]{1,30}", text or "")
        out: List[str] = []
        for token in tokens:
            t = token.strip().lower()
            if not t or t in self._query_stopwords:
                continue
            if len(t) <= 1 or t.isdigit():
                continue
            if t not in out:
                out.append(t)
        return out

    def _domain_score(self, domain: str) -> float:
        host = (domain or "").lower().strip()
        if not host:
            return 0.0

        if host in self.low_quality_domains:
            return 0.15
        if host in self.high_quality_domains:
            return 1.0
        if host in self.social_quality_domains:
            if host in {"zhihu.com", "bilibili.com", "reddit.com", "substack.com", "medium.com"}:
                return 0.72
            if host in {"x.com", "twitter.com", "linkedin.com", "youtube.com", "facebook.com", "instagram.com"}:
                return 0.68
            return 0.66
        if host.endswith(".gov") or host.endswith(".edu") or host.endswith(".gov.cn") or host.endswith(".edu.cn"):
            return 0.95
        if "wikipedia.org" in host:
            return 0.70
        if host.endswith(".org"):
            return 0.78
        if host.endswith(".com"):
            return 0.62
        return 0.5

    def _semantic_score(self, query: str, item: Dict[str, Any]) -> float:
        query_tokens = self._tokenize_query(query)
        if not query_tokens:
            return 0.0
        text = f"{item.get('title', '')} {item.get('body', '')}".lower()
        hit_count = 0
        for token in query_tokens:
            if token in text:
                hit_count += 1
        coverage = hit_count / max(1, len(query_tokens))

        # Penalize obvious noisy titles like pure numeric pages (e.g. "1", "1.1.1.1")
        title = str(item.get("title", "")).strip().lower()
        if re.fullmatch(r"[\d\.\-:_\s]{1,40}", title):
            coverage *= 0.35
        return max(0.0, min(1.0, coverage))

    def _topic_alignment_score(self, query: str, item: Dict[str, Any]) -> Tuple[float, bool, str]:
        profile_name, profile = self._infer_domain_profile(query)
        if not profile:
            return self._semantic_score(query, item), False, ""

        text = f"{item.get('title', '')} {item.get('body', '')} {item.get('source', '')} {item.get('href', '')}".lower()
        for reject in profile.get("reject", []):
            if str(reject).lower() in text:
                return 0.0, True, f"reject:{reject}"

        required = [str(x).lower() for x in profile.get("required_any", []) if str(x).strip()]
        if not required:
            return self._semantic_score(query, item), False, profile_name

        hits = [term for term in required if term in text]
        score = len(hits) / max(1, min(len(required), 8))

        # DOI/Crossref records often have sparse abstracts. A title hit on a
        # high-precision phrase should be enough to keep the item in play.
        title = str(item.get("title", "")).lower()
        phrase_hits = [term for term in hits if " " in term and term in title]
        if phrase_hits:
            score = max(score, 0.45)

        return max(0.0, min(1.0, score)), False, profile_name

    def _rank_results(self, query: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ranked: List[Dict[str, Any]] = []
        profile_name, profile = self._infer_domain_profile(query)
        for item in items or []:
            if not isinstance(item, dict):
                continue
            domain = self._extract_domain(str(item.get("href", "")))
            semantic_score = self._semantic_score(query, item)
            topic_alignment, rejected, rejection_reason = self._topic_alignment_score(query, item)
            if rejected:
                continue
            min_alignment = float(profile.get("min_alignment", self.min_topic_alignment) if profile else self.min_topic_alignment)
            if profile_name and topic_alignment < min_alignment:
                continue
            domain_score = self._domain_score(domain)
            preferred_domains = [str(d).lower() for d in profile.get("preferred_domains", [])] if profile else []
            authority_bonus = 0.12 if any(d in domain for d in preferred_domains) else 0.0
            quality_score = round((topic_alignment * 0.50) + (semantic_score * 0.25) + (domain_score * 0.25) + authority_bonus, 4)

            enriched = dict(item)
            enriched["source"] = enriched.get("source") or domain
            enriched["domain_score"] = round(domain_score, 4)
            enriched["semantic_score"] = round(semantic_score, 4)
            enriched["topic_alignment_score"] = round(topic_alignment, 4)
            enriched["domain_profile"] = profile_name
            if rejection_reason:
                enriched["alignment_note"] = rejection_reason
            enriched["quality_score"] = quality_score
            ranked.append(enriched)

        ranked.sort(
            key=lambda x: (
                float(x.get("quality_score", 0.0)),
                float(x.get("topic_alignment_score", 0.0)),
                float(x.get("semantic_score", 0.0)),
            ),
            reverse=True,
        )
        # Apply optional semantic re-ranking using SBERT if enabled
        try:
            use_rerank = _os.getenv("RAG_USE_SEMANTIC_RERANKER", "false").lower() == "true"
        except Exception:
            use_rerank = False

        if use_rerank and ranked:
            try:
                ranked = self._semantic_rerank(query, ranked, top_k=self.max_results)
            except Exception:
                # Fall back to existing ranking on any reranker error
                pass

        return ranked[: self.max_results]

    def _semantic_rerank(self, query: str, items: List[Dict[str, Any]], top_k: int | None = None) -> List[Dict[str, Any]]:
        """Lightweight SBERT reranker. Uses SentenceTransformer if available; falls back silently.

        Returns a re-ordered list of items (descending by similarity), enriched with `sbert_score`.
        Controlled by env `RAG_USE_SEMANTIC_RERANKER=true` and optional `RERANKER_MODEL`.
        """
        if not items:
            return items
        try:
            from sentence_transformers import SentenceTransformer, util
        except Exception:
            return items

        try:
            model_name = _os.getenv("RERANKER_MODEL", "sentence-transformers/paraphrase-MiniLM-L6-v2")
            model = getattr(self, "_sbert_model", None)
            if model is None:
                model = SentenceTransformer(model_name)
                setattr(self, "_sbert_model", model)

            texts = [f"{it.get('title','')} {it.get('body','')}" for it in items]
            q_emb = model.encode(str(query or ""), convert_to_tensor=True)
            docs_emb = model.encode(texts, convert_to_tensor=True)
            sims = util.cos_sim(q_emb, docs_emb)[0].cpu().numpy().tolist()
            for it, sim in zip(items, sims):
                it["sbert_score"] = float(sim)
            items.sort(key=lambda x: (float(x.get("sbert_score", 0.0)), float(x.get("quality_score", 0.0))), reverse=True)
            return items[:top_k] if top_k else items
        except Exception:
            return items

    def _compact_query(self, query: str) -> str:
        tokens = re.findall(r"[A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff\-_]{0,30}", query)
        if not tokens:
            return query[:140]
        return " ".join(tokens[:10])[:140]

    def _semantic_query(self, query: str) -> str:
        tokens = re.findall(r"[A-Za-z\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff\-_]{1,30}", query or "")
        refined: List[str] = []
        for token in tokens:
            normalized = token.strip().lower()
            if not normalized or normalized in self._query_stopwords:
                continue
            if normalized.isdigit():
                continue
            if len(normalized) <= 1:
                continue
            if normalized not in refined:
                refined.append(normalized)
            if len(refined) >= 8:
                break

        if not refined:
            return self._compact_query(query)
        return " ".join(refined)[:120]

    def _fetch_duckduckgo_html(self, query: str) -> Tuple[str, str]:
        headers = {"User-Agent": self._user_agent}
        endpoints = [
            "https://duckduckgo.com/html/",
            "https://html.duckduckgo.com/html/",
            "https://lite.duckduckgo.com/lite/",
        ]

        last_error = "unknown"
        for endpoint in endpoints:
            for attempt in range(1, 3):
                try:
                    response = self.session.get(
                        endpoint,
                        params={"q": query},
                        timeout=self.timeout,
                        headers=headers,
                    )
                    if response.status_code == 200 and response.text:
                        return response.text, ""
                    last_error = f"http_{response.status_code}"
                except Exception as exc:
                    last_error = str(exc)

                if attempt == 1:
                    time.sleep(0.35)

        return "", f"duckduckgo_html_fetch_failed: {last_error}"

    def _search_wikipedia(self, query: str) -> List[Dict[str, Any]]:
        if not query:
            return []

        endpoints = [
            "https://en.wikipedia.org/w/api.php",
            "https://zh.wikipedia.org/w/api.php",
        ]
        headers = {"User-Agent": self._user_agent}
        normalized_query = " ".join(str(query).split())[:120]

        seen: set[str] = set()
        results: List[Dict[str, Any]] = []

        for endpoint in endpoints:
            try:
                response = self.session.get(
                    endpoint,
                    params={
                        "action": "query",
                        "list": "search",
                        "srsearch": normalized_query,
                        "srlimit": max(2, self.max_results),
                        "format": "json",
                        "utf8": 1,
                    },
                    timeout=self.timeout,
                    headers=headers,
                )
                if response.status_code != 200:
                    continue

                payload = response.json()
                entries = ((payload or {}).get("query") or {}).get("search") or []
                wiki_host = urlparse(endpoint).netloc

                for entry in entries:
                    title = self._strip_html(str(entry.get("title", "")))
                    if not title:
                        continue
                    href = f"https://{wiki_host}/wiki/{title.replace(' ', '_')}"
                    if href in seen:
                        continue
                    seen.add(href)

                    snippet = self._strip_html(str(entry.get("snippet", "")))[:400]
                    results.append(
                        {
                            "title": title,
                            "body": snippet,
                            "href": href,
                            "source": wiki_host,
                        }
                    )
                    if len(results) >= self.max_results:
                        return results
            except Exception:
                continue

        return results

    def _parse_results(self, html_text: str, max_items: int) -> List[Dict[str, Any]]:
        link_patterns = [
            re.compile(
                r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
                flags=re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r'<a[^>]*class="result-link"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
                flags=re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r'<a[^>]*href="(?P<href>/l/\?[^\"]+|https?://[^\"]+)"[^>]*>(?P<title>.*?)</a>',
                flags=re.IGNORECASE | re.DOTALL,
            ),
        ]
        snippet_patterns = [
            re.compile(
                r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>.*?</a>.*?'
                r'<a[^>]*class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
                flags=re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r'<a[^>]*class="result-link"[^>]*href="(?P<href>[^"]+)"[^>]*>.*?</a>.*?'
                r'<td[^>]*class="result-snippet"[^>]*>(?P<snippet>.*?)</td>',
                flags=re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r'<a[^>]*href="(?P<href>/l/\?[^\"]+|https?://[^\"]+)"[^>]*>.*?</a>.*?'
                r'<(?:a|td|div)[^>]*class="(?:result__snippet|result-snippet|snippet)"[^>]*>(?P<snippet>.*?)</(?:a|td|div)>',
                flags=re.IGNORECASE | re.DOTALL,
            ),
        ]

        snippets_by_href: Dict[str, str] = {}
        for snippet_pattern in snippet_patterns:
            for snippet_match in snippet_pattern.finditer(html_text or ""):
                href = self._clean_href(snippet_match.group("href"))
                if not href:
                    continue
                snippet_html = snippet_match.group("snippet") or ""
                snippet_value = self._strip_html(snippet_html)[:400]
                if snippet_value and href not in snippets_by_href:
                    snippets_by_href[href] = snippet_value

        parsed: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for link_pattern in link_patterns:
            for match in link_pattern.finditer(html_text or ""):
                href = self._clean_href(match.group("href"))
                if not href or href in seen:
                    continue
                seen.add(href)

                title_html = match.group("title") or ""
                title = self._strip_html(title_html)
                if not title:
                    continue

                snippet = snippets_by_href.get(href, "")
                parsed.append(
                    {
                        "title": title,
                        "body": snippet,
                        "href": href,
                        "source": self._extract_domain(href),
                    }
                )
                if len(parsed) >= max_items:
                    return parsed

        return parsed

    def _strip_html(self, content: str) -> str:
        without_tags = re.sub(r"<[^>]+>", " ", content or "")
        normalized = " ".join(html.unescape(without_tags).split())
        return normalized.strip()

    def _clean_href(self, href: str) -> str:
        href = (href or "").strip()
        if not href:
            return ""

        if href.startswith("//"):
            href = "https:" + href

        if href.startswith("/"):
            href = "https://duckduckgo.com" + href

        decoded = unquote(href)
        resolved = self._resolve_ddg_redirect(decoded)

        parsed = urlparse(resolved)
        if parsed.scheme not in ("http", "https"):
            return ""
        host = (parsed.netloc or "").lower()
        if host in self._blocked_hosts:
            return ""
        return resolved

    def _resolve_ddg_redirect(self, href: str) -> str:
        parsed = urlparse(href)
        if parsed.netloc.lower().endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
            query_map = parse_qs(parsed.query)
            uddg_values = query_map.get("uddg")
            if uddg_values:
                return unquote(uddg_values[0]).strip()
        return href

    def _extract_domain(self, url: str) -> str:
        if not url:
            return ""
        try:
            domain = (urlparse(url).netloc or "").strip().lower()
            if domain.startswith("www."):
                return domain[4:]
            return domain
        except Exception:
            return url

    def format_search_context(self, search_result: Dict[str, Any], max_items: int = 3) -> str:
        if not search_result.get("success"):
            return ""
        items = list(search_result.get("results") or [])[:max_items]
        if not items:
            return ""

        context = "【参考资料（可引用来源）】\n"
        for index, item in enumerate(items, start=1):
            context += (
                f"\n来源ID: {index}\n"
                f"站点: {item.get('source', '')}\n"
                f"标题: {item.get('title', '')}\n"
                f"摘要: {item.get('body', '')}\n"
                f"文章链接: {item.get('href', '')}\n"
            )
        return context

    def extract_source_numbers(self, text: str) -> List[int]:
        matches = re.findall(r"\[来源(\d+)\]", text or "")
        unique_numbers = sorted({int(value) for value in matches})
        return unique_numbers


class SourceVerifier:
    def verify(
        self,
        text: str,
        source_results: List[Dict[str, Any]],
        require_citations: bool = True,
        min_citations: int = 1,
        topic: str = "",
        min_semantic_score: float = 0.35,
    ) -> Dict[str, Any]:
        refs = sorted({int(value) for value in re.findall(r"\[来源(\d+)\]", text or "")})
        url_pattern = re.compile(r"https?://[^\s\]）)>,;]+", flags=re.IGNORECASE)
        found_urls = sorted(set(url_pattern.findall(text or "")))
        total_sources = len(source_results or [])
        invalid_refs = [ref for ref in refs if ref < 1 or ref > total_sources]

        source_urls = {
            str((item or {}).get("href") or "").strip()
            for item in (source_results or [])
            if str((item or {}).get("href") or "").strip()
        }
        matched_urls = [url for url in found_urls if url in source_urls]
        invalid_urls = [url for url in found_urls if url not in source_urls]

        matched_semantic_scores: Dict[str, float] = {}
        topic_tokens = set(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,40}", str(topic or "").lower()))
        for url in matched_urls:
            source_item = next((it for it in (source_results or []) if str((it or {}).get("href") or "").strip() == url), {})
            source_text = f"{source_item.get('title', '')} {source_item.get('body', '')}".lower()
            if not topic_tokens:
                semantic = float(source_item.get("semantic_score", 0.0) or 0.0)
            else:
                hit = sum(1 for tk in topic_tokens if tk in source_text)
                semantic = hit / max(1, len(topic_tokens))
            matched_semantic_scores[url] = round(float(semantic), 4)

        low_semantic_urls = [url for url, score in matched_semantic_scores.items() if score < float(min_semantic_score)]

        citation_count = max(len(refs), len(matched_urls))

        if require_citations:
            citation_ok = citation_count >= max(1, int(min_citations))
        else:
            citation_ok = True

        valid = citation_ok and len(invalid_refs) == 0 and len(invalid_urls) == 0 and len(low_semantic_urls) == 0
        return {
            "valid": valid,
            "found_references": citation_count,
            "references": refs,
            "invalid_references": invalid_refs,
            "found_urls": found_urls,
            "matched_urls": matched_urls,
            "invalid_urls": invalid_urls,
            "matched_semantic_scores": matched_semantic_scores,
            "low_semantic_urls": low_semantic_urls,
            "total_sources": total_sources,
            "required": require_citations,
            "min_citations": max(1, int(min_citations)),
            "min_semantic_score": float(min_semantic_score),
        }
