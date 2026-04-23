#!/usr/bin/env python3
import json
import time
from datetime import datetime
from pathlib import Path

import requests


def safe_get(dct, key, default=None):
    if isinstance(dct, dict):
        return dct.get(key, default)
    return default


def main() -> int:
    payload = {
        "topic": "大学新生时间管理与学习习惯指南",
        "chapter_count": 2,
        "subsection_count": 2,
        "user_background": "大一新生，想建立学习和作息体系",
        "extra_requirements": "内容要可执行，包含具体方法与示例",
        "rel_threshold": 0.72,
        "red_threshold": 0.60,
        "timeout_seconds": 7200,
    }

    url = "http://localhost:8010/api/generate"
    baseline_seconds = 995.60

    print("=" * 80)
    print("FlowerNet 2x2 Full Stats Regression")
    print("=" * 80)
    print(f"Start: {datetime.now().isoformat(timespec='seconds')}")
    print(f"URL: {url}")
    print(f"Payload: chapter_count={payload['chapter_count']}, subsection_count={payload['subsection_count']}")
    print(f"Quality thresholds: rel={payload['rel_threshold']}, red={payload['red_threshold']}")
    print()

    started = time.time()
    response = requests.post(url, json=payload, timeout=7500)
    elapsed = time.time() - started

    status_code = response.status_code
    try:
        body = response.json()
    except Exception:
        body = {"_raw_text": response.text}

    stats = safe_get(body, "stats", {}) or {}

    improvement_pct = ((baseline_seconds - elapsed) / baseline_seconds) * 100.0
    content = safe_get(body, "content", "") or ""
    content_len = len(content)

    # Common KPI fields expected from FlowerNet stats.
    kpi = {
        "passed_subsections": safe_get(stats, "passed_subsections"),
        "failed_subsections": safe_get(stats, "failed_subsections"),
        "forced_subsections": safe_get(stats, "forced_subsections"),
        "controller_calls_total": safe_get(stats, "controller_calls_total"),
        "rag_used_subsections": safe_get(stats, "rag_used_subsections"),
        "rag_search_success_subsections": safe_get(stats, "rag_search_success_subsections"),
        "controller_effective_subsections": safe_get(stats, "controller_effective_subsections"),
        "avg_relevancy": safe_get(stats, "avg_relevancy"),
        "avg_redundancy": safe_get(stats, "avg_redundancy"),
        "avg_quality": safe_get(stats, "avg_quality"),
        "pass_rate": safe_get(stats, "pass_rate"),
    }

    out = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "request": {
            "url": url,
            "payload": payload,
        },
        "performance": {
            "status_code": status_code,
            "elapsed_seconds": elapsed,
            "elapsed_minutes": elapsed / 60.0,
            "baseline_seconds": baseline_seconds,
            "improvement_pct_vs_995_6s": improvement_pct,
            "success": safe_get(body, "success"),
            "title": safe_get(body, "title"),
            "content_len": content_len,
        },
        "quality_summary": kpi,
        "stats_keys": sorted(list(stats.keys())) if isinstance(stats, dict) else [],
        "stats": stats,
        "raw_response": body,
    }

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(f"full_stats_2x2_{ts}.json")
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("HTTP status:", status_code)
    print(f"Elapsed: {elapsed:.2f}s ({elapsed/60.0:.2f} min)")
    print(f"Baseline: {baseline_seconds:.2f}s")
    print(f"Improvement: {improvement_pct:+.2f}%")
    print("success:", safe_get(body, "success"))
    print("title:", safe_get(body, "title"))
    print("content_len:", content_len)
    print()

    print("Quality KPIs:")
    for k, v in kpi.items():
        print(f"  - {k}: {v}")

    print()
    print(f"stats keys ({len(out['stats_keys'])}):")
    for k in out["stats_keys"]:
        print("  -", k)

    print()
    print("Saved:", out_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
