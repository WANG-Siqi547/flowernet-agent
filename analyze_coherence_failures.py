#!/usr/bin/env python3
"""
Analyze logical coherence failures from saved FlowerNet regression outputs.

Usage:
  python3 analyze_coherence_failures.py
  python3 analyze_coherence_failures.py --glob "full_regression_result_*.json" --top 30
"""

import argparse
import glob
import json
import os
import re
from collections import Counter
from statistics import mean
from typing import Dict, List, Any, Tuple


TRANSITION_MARKERS = [
    "therefore", "thus", "however", "moreover", "in addition", "consequently", "nevertheless",
    "因此", "然而", "此外", "总之", "由此可见", "进一步"
]

EVIDENCE_MARKERS = [
    "according to", "study", "data", "evidence", "et al", "doi", "http", "[", "]",
    "研究", "数据显示", "文献", "证据", "来源"
]

CLAIM_MARKERS = [
    "we argue", "this suggests", "it is clear", "must", "proves",
    "我们认为", "这表明", "可以看出", "必须", "证明"
]


def split_sentences(text: str) -> List[str]:
    parts = re.split(r"[。！？!?\.]+", text)
    return [p.strip() for p in parts if p.strip()]


def detect_patterns(text: str) -> Dict[str, Any]:
    lower = text.lower()
    sents = split_sentences(text)
    words = re.findall(r"\w+", text, flags=re.UNICODE)
    avg_sent_len = (len(words) / max(len(sents), 1)) if words else 0.0

    has_transition = any(m in lower for m in TRANSITION_MARKERS)
    has_evidence = any(m in lower for m in EVIDENCE_MARKERS)
    has_claim = any(m in lower for m in CLAIM_MARKERS)

    patterns = []

    if len(words) < 120:
        patterns.append("too_short_or_underdeveloped")

    if avg_sent_len < 12:
        patterns.append("short_sentences_weak_reasoning_chain")

    if not has_transition:
        patterns.append("missing_transition_markers")

    if has_claim and not has_evidence:
        patterns.append("unsupported_claims_no_evidence_markers")

    return {
        "word_count": len(words),
        "sentence_count": len(sents),
        "avg_sentence_len": round(avg_sent_len, 2),
        "has_transition": has_transition,
        "has_evidence": has_evidence,
        "has_claim": has_claim,
        "patterns": patterns,
    }


def collect_failure_items(obj: Any) -> List[Dict[str, Any]]:
    """
    Best-effort extraction of failed subsection records from heterogeneous saved outputs.
    """
    failures = []

    def walk(node: Any):
        if isinstance(node, dict):
            # Common keys observed in project outputs
            if "failed_subsections" in node and isinstance(node["failed_subsections"], list):
                for item in node["failed_subsections"]:
                    if isinstance(item, dict):
                        failures.append(item)
            # Generic verifier-like records
            if node.get("logical_coherence") is False:
                failures.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for it in node:
                walk(it)

    walk(obj)
    return failures


def _get_nested(d: Dict[str, Any], keys: List[str], default=None):
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def split_subsections_from_markdown(content: str) -> List[Tuple[str, str]]:
    """
    Split markdown content into subsection blocks by lines starting with '### '.
    Returns list of (title, block_text).
    """
    lines = content.splitlines()
    blocks: List[Tuple[str, List[str]]] = []
    current_title = "document"
    current_lines: List[str] = []
    for line in lines:
        if line.strip().startswith("### "):
            if current_lines:
                blocks.append((current_title, current_lines))
            current_title = line.strip()[4:].strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        blocks.append((current_title, current_lines))

    return [(title, "\n".join(txt).strip()) for title, txt in blocks if "\n".join(txt).strip()]


def extract_text(item: Dict[str, Any]) -> str:
    for k in ["content", "draft", "text", "failed_draft", "output"]:
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--glob", default="full_regression_result_*.json", help="Glob pattern for result files")
    parser.add_argument("--top", type=int, default=20, help="Top N failure samples to analyze")
    parser.add_argument("--out", default="coherence_failure_report.json", help="Output report file")
    parser.add_argument("--coherence-threshold", type=float, default=0.40, help="Logical coherence threshold for low-coherence runs")
    args = parser.parse_args()

    files = sorted(glob.glob(args.glob))
    if not files:
        print(f"No files matched: {args.glob}")
        return

    all_failures: List[Dict[str, Any]] = []
    low_coherence_runs: List[Dict[str, Any]] = []
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            items = collect_failure_items(data)
            for it in items:
                it_copy = dict(it)
                it_copy["_source_file"] = os.path.basename(path)
                all_failures.append(it_copy)

            logical_coherence = _get_nested(data, ["complete_result", "stats", "quality_dimension_avgs", "logical_coherence"])
            content = _get_nested(data, ["complete_result", "content"], "")
            if isinstance(logical_coherence, (int, float)) and logical_coherence < args.coherence_threshold and isinstance(content, str) and content.strip():
                low_coherence_runs.append({
                    "source": os.path.basename(path),
                    "logical_coherence": float(logical_coherence),
                    "content": content,
                })
        except Exception as e:
            print(f"Skip unreadable file {path}: {e}")

    # If explicit failure records are absent, fall back to low-coherence run analysis.
    if not all_failures and not low_coherence_runs:
        print("No failure items or low-coherence runs found.")
        return

    sampled = all_failures[: args.top]

    pattern_counter = Counter()
    metric_rows = []
    detailed = []

    for item in sampled:
        text = extract_text(item)
        if not text:
            continue
        p = detect_patterns(text)
        pattern_counter.update(p["patterns"])
        metric_rows.append(p)
        detailed.append({
            "source": item.get("_source_file"),
            "section_id": item.get("section_id"),
            "subsection_id": item.get("subsection_id"),
            "subsection_title": item.get("subsection_title") or item.get("title"),
            "metrics": p,
        })

    # Low-coherence subsection heuristics from full markdown content
    low_run_details = []
    low_run_pattern_counter = Counter()
    for run in low_coherence_runs[: args.top]:
        subsection_blocks = split_subsections_from_markdown(run["content"])
        subsection_metrics = []
        for title, text in subsection_blocks:
            m = detect_patterns(text)
            subsection_metrics.append({"title": title, "metrics": m})
            low_run_pattern_counter.update(m["patterns"])
        low_run_details.append({
            "source": run["source"],
            "logical_coherence": run["logical_coherence"],
            "subsections": subsection_metrics,
        })

    summary = {
        "files_scanned": len(files),
        "failure_items_found": len(all_failures),
        "low_coherence_runs_found": len(low_coherence_runs),
        "samples_analyzed": len(detailed),
        "pattern_counts": dict(pattern_counter.most_common()),
        "low_coherence_pattern_counts": dict(low_run_pattern_counter.most_common()),
        "avg_word_count": round(mean([m["word_count"] for m in metric_rows]), 2) if metric_rows else 0.0,
        "avg_sentence_len": round(mean([m["avg_sentence_len"] for m in metric_rows]), 2) if metric_rows else 0.0,
        "recommendations": [
            "In generator prompt, enforce subsection structure: Claim -> Evidence -> Reasoning -> Transition -> Implication.",
            "Require at least one explicit transition marker per subsection.",
            "If strong claim markers exist, require at least one evidence/citation marker.",
            "Increase minimum target length for coherence-critical subsections to >= 180 words.",
        ],
    }

    report = {
        "summary": summary,
        "samples": detailed,
        "low_coherence_runs": low_run_details,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nWrote report: {args.out}")


if __name__ == "__main__":
    main()
