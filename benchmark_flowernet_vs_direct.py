#!/usr/bin/env python3
import argparse
import json
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Dict, List, Tuple

import requests


FLOWERNET_URL = "http://localhost:8010/api/poffices/generate"
DIRECT_URL = "http://localhost:8002/generate"

CHAPTER_COUNT = 2
SUBSECTION_COUNT = 2
TIMEOUT_SECONDS = 900

CASES = [
    {
        "topic": "面向大学新生的时间管理方法",
        "keywords": ["时间管理", "优先级", "番茄钟", "计划", "复盘", "效率"],
    },
    {
        "topic": "中小企业如何落地数据分析",
        "keywords": ["指标", "数据源", "仪表盘", "成本", "决策", "ROI"],
    },
    {
        "topic": "健康饮食与运动协同减脂",
        "keywords": ["热量", "蛋白质", "有氧", "力量训练", "睡眠", "坚持"],
    },
    {
        "topic": "生成式AI在教育中的应用边界",
        "keywords": ["个性化", "评估", "偏差", "隐私", "教师", "伦理"],
    },
]


def parse_args():
    parser = argparse.ArgumentParser(description="A/B benchmark: FlowerNet vs Direct LLM")
    parser.add_argument("--max-cases", type=int, default=len(CASES), help="How many topics to run")
    parser.add_argument("--chapter-count", type=int, default=CHAPTER_COUNT)
    parser.add_argument("--subsection-count", type=int, default=SUBSECTION_COUNT)
    parser.add_argument("--prefix", type=str, default="ab_test_results")
    return parser.parse_args()


@dataclass
class MetricRow:
    system: str
    topic: str
    success: bool
    latency_s: float
    char_count: int
    sentence_count: int
    avg_sentence_len: float
    chapter_headings: int
    subsection_headings: int
    structure_score: float
    keyword_coverage: float
    lexical_diversity: float
    duplicate_sentence_ratio: float
    error: str


def make_session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    return s


def extract_text_from_flowernet(resp_json: Dict[str, Any]) -> str:
    for key in ["content", "document", "text", "result"]:
        val = resp_json.get(key)
        if isinstance(val, str):
            return val
    return ""


def split_sentences(text: str) -> List[str]:
    parts = re.split(r"[。！？!?\n]+", text)
    return [p.strip() for p in parts if p and p.strip()]


def tokenize(text: str) -> List[str]:
    return re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text)


def compute_metrics(
    system: str,
    topic: str,
    keywords: List[str],
    text: str,
    success: bool,
    latency_s: float,
    chapter_count: int,
    subsection_count: int,
    error: str = "",
) -> MetricRow:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    chapter_headings = sum(1 for line in lines if re.match(r"^##\s+", line))
    subsection_headings = sum(1 for line in lines if re.match(r"^###\s+", line))

    chapter_score = min(chapter_headings / max(chapter_count, 1), 1.0)
    subsection_score = min(subsection_headings / max(chapter_count * subsection_count, 1), 1.0)
    structure_score = round(0.45 * chapter_score + 0.55 * subsection_score, 4)

    low = text.lower()
    hit = sum(1 for k in keywords if k.lower() in low)
    keyword_coverage = round(hit / max(len(keywords), 1), 4)

    sentences = split_sentences(text)
    clean_text = re.sub(r"[#*`>-]", "", text)
    avg_sentence_len = round(len(clean_text) / max(len(sentences), 1), 2)

    tokens = tokenize(clean_text)
    lexical_diversity = round(len(set(tokens)) / max(len(tokens), 1), 4)

    if sentences:
        uniq = len(set(sentences))
        duplicate_sentence_ratio = round(1 - uniq / len(sentences), 4)
    else:
        duplicate_sentence_ratio = 0.0

    return MetricRow(
        system=system,
        topic=topic,
        success=success,
        latency_s=round(latency_s, 2),
        char_count=len(text),
        sentence_count=len(sentences),
        avg_sentence_len=avg_sentence_len,
        chapter_headings=chapter_headings,
        subsection_headings=subsection_headings,
        structure_score=structure_score,
        keyword_coverage=keyword_coverage,
        lexical_diversity=lexical_diversity,
        duplicate_sentence_ratio=duplicate_sentence_ratio,
        error=error[:400],
    )


def run_flowernet(session: requests.Session, topic: str, chapter_count: int, subsection_count: int) -> Tuple[bool, str, float, str]:
    payload = {
        "query": topic,
        "chapter_count": chapter_count,
        "subsection_count": subsection_count,
        "async_mode": False,
        "timeout_seconds": TIMEOUT_SECONDS,
    }
    start = time.time()
    try:
        r = session.post(FLOWERNET_URL, json=payload, timeout=TIMEOUT_SECONDS + 60)
        elapsed = time.time() - start
        data = r.json()
        ok = bool(data.get("success")) and str(data.get("task_status", "")).lower() == "completed"
        text = extract_text_from_flowernet(data)
        err = str(data.get("error", "")) if not ok else ""
        return ok, text, elapsed, err
    except Exception as e:
        return False, "", time.time() - start, str(e)


def run_direct(session: requests.Session, topic: str, chapter_count: int, subsection_count: int) -> Tuple[bool, str, float, str]:
    prompt = (
        f"请围绕主题《{topic}》为普通读者写一份结构化文档。"
        f"要求：共{chapter_count}章，每章{subsection_count}个小节。"
        "严格使用 Markdown 标题：文档标题用 #，章标题用 ##，小节标题用 ###。"
        "内容需包含定义、方法、案例、风险与建议，避免空话，语言清晰。"
    )
    payload = {"prompt": prompt, "max_tokens": 3000}
    start = time.time()
    try:
        r = session.post(DIRECT_URL, json=payload, timeout=TIMEOUT_SECONDS)
        elapsed = time.time() - start
        data = r.json()
        ok = bool(data.get("success")) and isinstance(data.get("draft"), str) and len(data.get("draft")) > 0
        text = data.get("draft", "") if ok else ""
        err = "" if ok else str(data)
        return ok, text, elapsed, err
    except Exception as e:
        return False, "", time.time() - start, str(e)


def aggregate(rows: List[MetricRow], system: str) -> Dict[str, Any]:
    srows = [r for r in rows if r.system == system]
    succ = [r for r in srows if r.success]

    def m(vals: List[float]) -> float:
        return round(mean(vals), 4) if vals else 0.0

    def sd(vals: List[float]) -> float:
        return round(pstdev(vals), 4) if len(vals) > 1 else 0.0

    return {
        "system": system,
        "cases": len(srows),
        "success_count": len(succ),
        "success_rate": round(len(succ) / len(srows), 4) if srows else 0.0,
        "latency_mean_s": m([r.latency_s for r in succ]),
        "latency_std_s": sd([r.latency_s for r in succ]),
        "char_count_mean": m([r.char_count for r in succ]),
        "structure_score_mean": m([r.structure_score for r in succ]),
        "keyword_coverage_mean": m([r.keyword_coverage for r in succ]),
        "lexical_diversity_mean": m([r.lexical_diversity for r in succ]),
        "duplicate_sentence_ratio_mean": m([r.duplicate_sentence_ratio for r in succ]),
        "avg_sentence_len_mean": m([r.avg_sentence_len for r in succ]),
    }


def save_snapshot(rows: List[MetricRow], out_path: Path, chapter_count: int, subsection_count: int, case_count: int):
    summary = {
        "timestamp": datetime.now().isoformat(),
        "config": {
            "chapter_count": chapter_count,
            "subsection_count": subsection_count,
            "case_count": case_count,
            "flowernet_url": FLOWERNET_URL,
            "direct_url": DIRECT_URL,
        },
        "aggregate": [
            aggregate(rows, "flowernet"),
            aggregate(rows, "direct_llm"),
        ],
        "rows": [asdict(r) for r in rows],
    }
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    args = parse_args()
    selected_cases = CASES[: max(1, min(args.max_cases, len(CASES)))]
    session = make_session()
    rows: List[MetricRow] = []
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path.cwd() / f"{args.prefix}_{ts}.json"

    for idx, case in enumerate(selected_cases, start=1):
        topic = case["topic"]
        keywords = case["keywords"]
        print(f"\n[{idx}/{len(selected_cases)}] Topic: {topic}")

        ok_f, text_f, t_f, e_f = run_flowernet(session, topic, args.chapter_count, args.subsection_count)
        row_f = compute_metrics(
            "flowernet", topic, keywords, text_f, ok_f, t_f,
            chapter_count=args.chapter_count,
            subsection_count=args.subsection_count,
            error=e_f,
        )
        rows.append(row_f)
        print(f"  FlowerNet -> success={ok_f}, latency={t_f:.2f}s, chars={len(text_f)}, err={e_f[:80]}")
        save_snapshot(rows, out_path, args.chapter_count, args.subsection_count, len(selected_cases))

        ok_d, text_d, t_d, e_d = run_direct(session, topic, args.chapter_count, args.subsection_count)
        row_d = compute_metrics(
            "direct_llm", topic, keywords, text_d, ok_d, t_d,
            chapter_count=args.chapter_count,
            subsection_count=args.subsection_count,
            error=e_d,
        )
        rows.append(row_d)
        print(f"  DirectLLM -> success={ok_d}, latency={t_d:.2f}s, chars={len(text_d)}, err={e_d[:80]}")
        save_snapshot(rows, out_path, args.chapter_count, args.subsection_count, len(selected_cases))

    summary = json.loads(out_path.read_text(encoding="utf-8"))

    print("\n=== Aggregate ===")
    for a in summary["aggregate"]:
        print(json.dumps(a, ensure_ascii=False))

    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
