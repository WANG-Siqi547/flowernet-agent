#!/usr/bin/env python3
"""Build external reference sets for Week-2 journal-style metrics.

FreshWiki-style topics use Wikipedia article extracts. Education topics use
published review/survey metadata and abstracts returned by Semantic Scholar,
falling back to Crossref when needed.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import quote

import requests


FRESHWIKI_PAGES: Dict[str, List[str]] = {
    "fw24_001": ["Intelligent agent", "Software agent", "Tool use"],
    "fw24_002": ["Multimodal learning", "Large language model", "Generative artificial intelligence"],
    "fw24_003": ["Retrieval-augmented generation", "Information retrieval", "Citation"],
    "fw24_004": ["AI safety", "Evaluation of machine learning models", "Artificial general intelligence"],
    "fw24_005": ["Open-source software", "Large language model", "Software deployment"],
    "fw24_006": ["Synthetic data", "Data augmentation", "Machine learning"],
    "fw24_007": ["Transformer (deep learning architecture)", "Large language model", "Natural language generation"],
    "fw24_008": ["Artificial intelligence in science", "Literature review", "Scientific literature"],
    "fw24_009": ["Knowledge management", "Virtual assistant", "Workflow"],
    "fw24_010": ["Natural language generation", "Automatic summarization", "Evaluation of machine translation"],
}


EDUCATION_QUERIES: Dict[str, List[str]] = {
    "edu_001": [
        "artificial intelligence personalized learning systematic review",
        "personalized learning artificial intelligence review education",
    ],
    "edu_002": [
        "artificial intelligence higher education systematic literature review",
        "intelligent tutoring systems higher education systematic review",
    ],
    "edu_003": [
        "automated assessment feedback schools systematic review",
        "automatic feedback educational technology systematic review",
    ],
    "edu_004": [
        "AI literacy curriculum systematic review",
        "artificial intelligence literacy education review",
    ],
    "edu_005": [
        "learning analytics student support systematic review",
        "learning analytics early warning student support review",
    ],
}


def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def wikipedia_extract(session: requests.Session, title: str) -> str:
    url = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "prop": "extracts",
        "explaintext": "1",
        "redirects": "1",
        "titles": title,
        "format": "json",
    }
    last_exc: Exception | None = None
    last_status = 0
    for attempt in range(1, 5):
        try:
            resp = session.get(url, params=params, timeout=20)
            last_status = resp.status_code
            resp.raise_for_status()
            break
        except requests.HTTPError as exc:
            last_exc = exc
            if getattr(exc.response, "status_code", None) == 429 and attempt == 4:
                return wikipedia_summary(session, title)
            if getattr(exc.response, "status_code", None) != 429:
                raise
            time.sleep(2.5 * attempt)
        except Exception as exc:
            last_exc = exc
            if attempt == 4:
                raise
            time.sleep(1.5 * attempt)
    else:
        raise last_exc or RuntimeError("wikipedia request failed")
    pages = resp.json().get("query", {}).get("pages", {})
    for page in pages.values():
        extract = clean_text(page.get("extract", ""))
        if extract:
            return extract[:12000]
    if last_status == 429:
        return wikipedia_summary(session, title)
    return ""


def wikipedia_summary(session: requests.Session, title: str) -> str:
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title.replace(' ', '_'), safe='')}"
    resp = session.get(url, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    extract = clean_text(str(data.get("extract") or ""))
    if not extract:
        return ""
    return extract[:4000]


def semantic_scholar_search(session: requests.Session, query: str, limit: int = 5) -> List[Dict[str, Any]]:
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {
        "query": query,
        "limit": limit,
        "fields": "title,abstract,year,venue,url,publicationTypes,externalIds",
    }
    resp = session.get(url, params=params, timeout=25)
    resp.raise_for_status()
    return resp.json().get("data", []) or []


def crossref_search(session: requests.Session, query: str, limit: int = 3) -> List[Dict[str, Any]]:
    url = "https://api.crossref.org/works"
    params = {
        "query.title": query,
        "filter": "type:journal-article",
        "rows": limit,
        "select": "title,abstract,published-print,published-online,DOI,container-title",
    }
    resp = session.get(url, params=params, timeout=25)
    resp.raise_for_status()
    return resp.json().get("message", {}).get("items", []) or []


def is_review_like(record: Dict[str, Any]) -> bool:
    title = " ".join(record.get("title") or []) if isinstance(record.get("title"), list) else str(record.get("title") or "")
    publication_types = " ".join(record.get("publicationTypes") or [])
    blob = f"{title} {publication_types}".lower()
    return any(term in blob for term in ("review", "survey", "systematic", "meta-analysis"))


def pick_education_references(session: requests.Session, queries: Iterable[str]) -> Dict[str, Any]:
    refs: List[str] = []
    notes: List[str] = []
    seen_titles = set()
    for query in queries:
        try:
            papers = semantic_scholar_search(session, query)
        except Exception:
            papers = []
        candidates = sorted(papers, key=lambda item: (not is_review_like(item), -(item.get("year") or 0)))
        for paper in candidates:
            title = clean_text(str(paper.get("title") or ""))
            abstract = clean_text(str(paper.get("abstract") or ""))
            if not title or not abstract or title.lower() in seen_titles:
                continue
            seen_titles.add(title.lower())
            refs.append(f"{title}. {abstract}"[:10000])
            doi = (paper.get("externalIds") or {}).get("DOI", "")
            url = paper.get("url") or (f"https://doi.org/{doi}" if doi else "")
            notes.append(f"Semantic Scholar: {title} ({paper.get('year', 'n.d.')}, {paper.get('venue', '')}) {url}".strip())
            if len(refs) >= 2:
                return {"reference_texts": refs, "source_note": " | ".join(notes)}
        time.sleep(0.5)

    for query in queries:
        try:
            works = crossref_search(session, query)
        except Exception:
            works = []
        candidates = sorted(works, key=lambda item: not is_review_like(item))
        for work in candidates:
            title = clean_text(" ".join(work.get("title") or []))
            abstract = clean_text(str(work.get("abstract") or ""))
            if not title or not abstract or title.lower() in seen_titles:
                continue
            seen_titles.add(title.lower())
            refs.append(f"{title}. {abstract}"[:10000])
            doi = work.get("DOI", "")
            notes.append(f"Crossref: {title} DOI:{doi}")
            if len(refs) >= 2:
                return {"reference_texts": refs, "source_note": " | ".join(notes)}
        time.sleep(0.5)
    return {"reference_texts": refs, "source_note": " | ".join(notes)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="references/week2_reference_sets.json")
    args = parser.parse_args()

    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": "FlowerNet academic metric builder/0.1 (contact: local research use)"})

    out: Dict[str, Dict[str, Any]] = {}
    for topic_id, pages in FRESHWIKI_PAGES.items():
        texts: List[str] = []
        used_pages: List[str] = []
        for page in pages:
            try:
                text = wikipedia_extract(session, page)
            except Exception as exc:
                text = ""
                used_pages.append(f"{page} (fetch failed: {exc})")
            if text:
                texts.append(text)
                used_pages.append(f"{page}: https://en.wikipedia.org/wiki/{quote(page.replace(' ', '_'))}")
            time.sleep(1.0)
        out[topic_id] = {
            "reference_texts": texts,
            "source_note": "Wikipedia extracts: " + " | ".join(used_pages),
        }

    for topic_id, queries in EDUCATION_QUERIES.items():
        out[topic_id] = pick_education_references(session, queries)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    main()
