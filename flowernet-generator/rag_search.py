from typing import Dict, Any, List, Tuple
import re
import time
import html
import os
from urllib.parse import unquote, urlparse, parse_qs

import requests


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
            "小节", "章节", "大纲", "写作", "说明", "包括", "以及", "关于"
        }
        self.include_social_cn = os.getenv("RAG_INCLUDE_SOCIAL_CN", "true").lower() == "true"
        self.include_social_global = os.getenv("RAG_INCLUDE_SOCIAL_GLOBAL", "true").lower() == "true"
        self.include_academic_sources = os.getenv("RAG_INCLUDE_ACADEMIC_SOURCES", "true").lower() == "true"
        self.high_quality_domains = {
            "nature.com", "science.org", "sciencedirect.com", "springer.com", "ieee.org",
            "acm.org", "arxiv.org", "ncbi.nlm.nih.gov", "who.int", "oecd.org", "un.org",
            "nist.gov", "nih.gov", "gov.cn", "edu.cn", "ruc.edu.cn", "tsinghua.edu.cn",
            "pku.edu.cn", "cass.cn", "moe.gov.cn", "researchgate.net", "doi.org",
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
                    if ranked_academic:
                        return {
                            "success": True,
                            "query": query,
                            "effective_query": query,
                            "results": ranked_academic,
                            "search_time": round(time.time() - started_at, 3),
                            "error": None,
                            "source_type": "academic",
                        }

            for query_candidate in query_candidates:
                raw_html, fetch_error = self._fetch_duckduckgo_html(query_candidate)
                if raw_html:
                    results = self._parse_results(raw_html, self.max_results)
                    if results:
                        ranked_results = self._rank_results(query_candidate, results)
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
        query_text = " ".join(str(query or "").split())[:180]

        for item in self._search_arxiv(query_text):
            href = str(item.get("href", ""))
            if href and href not in seen:
                seen.add(href)
                results.append(item)
                if len(results) >= self.max_results:
                    return results

        for item in self._search_site_targeted(query_text, "ssrn.com"):
            href = str(item.get("href", ""))
            if href and href not in seen:
                seen.add(href)
                results.append(item)
                if len(results) >= self.max_results:
                    return results

        for item in self._search_site_targeted(query_text, "scholar.google.com"):
            href = str(item.get("href", ""))
            if href and href not in seen:
                seen.add(href)
                results.append(item)
                if len(results) >= self.max_results:
                    return results

        return results

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

    def _rank_results(self, query: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ranked: List[Dict[str, Any]] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            domain = self._extract_domain(str(item.get("href", "")))
            semantic_score = self._semantic_score(query, item)
            domain_score = self._domain_score(domain)
            quality_score = round((semantic_score * 0.65) + (domain_score * 0.35), 4)

            enriched = dict(item)
            enriched["source"] = enriched.get("source") or domain
            enriched["domain_score"] = round(domain_score, 4)
            enriched["semantic_score"] = round(semantic_score, 4)
            enriched["quality_score"] = quality_score
            ranked.append(enriched)

        ranked.sort(
            key=lambda x: (float(x.get("quality_score", 0.0)), float(x.get("semantic_score", 0.0))),
            reverse=True,
        )
        return ranked[: self.max_results]

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
