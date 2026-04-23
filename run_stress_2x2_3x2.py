#!/usr/bin/env python3
import json
import os
import time
from datetime import datetime
from typing import Dict, Any, List

import requests

BASE_WEB = os.getenv("WEB_URL", "http://localhost:8010")
BASE_OUT = os.getenv("OUT_URL", "http://localhost:8003")
REL_THRESHOLD = float(os.getenv("FLOWERNET_REL_THRESHOLD", "0.72"))
RED_THRESHOLD = float(os.getenv("FLOWERNET_RED_THRESHOLD", "0.60"))
SESSION = requests.Session()
SESSION.trust_env = False


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_post(url: str, payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    st = time.time()
    try:
        resp = SESSION.post(url, json=payload, timeout=timeout)
        out: Dict[str, Any] = {
            "ok": resp.status_code == 200,
            "status": resp.status_code,
            "elapsed": round(time.time() - st, 2),
        }
        try:
            out["json"] = resp.json()
        except Exception:
            out["text"] = (resp.text or "")[:800]
        return out
    except Exception as e:
        return {
            "ok": False,
            "status": None,
            "elapsed": round(time.time() - st, 2),
            "error": f"{type(e).__name__}: {e}",
        }


def get_progress_events(document_id: str) -> List[Dict[str, Any]]:
    res = safe_post(
        f"{BASE_OUT}/history/progress",
        {"document_id": document_id, "after_id": 0, "limit": 5000},
        timeout=120,
    )
    if not res.get("ok"):
        return []
    body = res.get("json")
    if not isinstance(body, dict):
        return []
    events = body.get("events")
    if not isinstance(events, list):
        return []
    return events


def analyze_events(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    counters: Dict[str, int] = {}
    max_iteration = 0

    for ev in events:
        stage = str(ev.get("stage") or "")
        counters[stage] = counters.get(stage, 0) + 1
        meta = ev.get("metadata")
        if isinstance(meta, dict):
            it = meta.get("iteration")
            if isinstance(it, int):
                max_iteration = max(max_iteration, it)

    return {
        "stage_counters": counters,
        "max_iteration": max_iteration,
        "controller_triggered": counters.get("controller_start", 0) > 0,
        "controller_unavailable": counters.get("controller_unavailable", 0),
        "controller_error": counters.get("controller_error", 0),
        "controller_success": counters.get("controller_success", 0),
        "verifier_failed": counters.get("verifier_failed", 0),
    }


def backend_metrics_from_stats(stats: Dict[str, Any]) -> Dict[str, int]:
    return {
        "controller_calls_total": int(stats.get("controller_calls_total", 0) or 0),
        "controller_triggered_subsections": int(stats.get("controller_triggered_subsections", 0) or 0),
        "controller_success_total": int(stats.get("controller_success_total", 0) or 0),
        "controller_error_total": int(stats.get("controller_error_total", 0) or 0),
        "controller_unavailable_total": int(stats.get("controller_unavailable_total", 0) or 0),
        "controller_ineffective_total": int(stats.get("controller_ineffective_total", 0) or 0),
        "controller_fallback_outline_total": int(stats.get("controller_fallback_outline_total", 0) or 0),
        "controller_exhausted_total": int(stats.get("controller_exhausted_total", 0) or 0),
        "verifier_failed_total": int(stats.get("verifier_failed_total", 0) or 0),
    }


def run_case(chapter_count: int, subsection_count: int, timeout_seconds: int) -> Dict[str, Any]:
    payload = {
        "topic": f"机器人：从概念到应用的全面探索（{chapter_count}x{subsection_count} 压测）",
        "chapter_count": chapter_count,
        "subsection_count": subsection_count,
        "user_background": "普通读者，想系统了解机器人",
        "extra_requirements": "每节给出清晰定义、技术要点和实际案例，中文，避免空话。",
        "rel_threshold": REL_THRESHOLD,
        "red_threshold": RED_THRESHOLD,
        "timeout_seconds": timeout_seconds,
    }

    print(f"\n=== Running case {chapter_count}x{subsection_count} ===")
    res = safe_post(f"{BASE_WEB}/api/generate", payload, timeout=max(timeout_seconds + 300, 7500))

    case_result: Dict[str, Any] = {
        "case": f"{chapter_count}x{subsection_count}",
        "request_ok": res.get("ok"),
        "http_status": res.get("status"),
        "elapsed": res.get("elapsed"),
        "success": False,
        "document_id": "",
        "title": "",
        "content_length": 0,
        "expected_subsections": chapter_count * subsection_count,
        "passed_subsections": 0,
        "failed_subsections": 0,
        "forced_subsections": 0,
        "total_iterations": 0,
        "events": {},
        "backend_metrics": {},
        "validation": {
            "controller_triggered": False,
            "max_iteration_ok": False,
            "no_dead_loop": False,
            "complete_doc": False,
            "all_passed": False,
        },
        "error": res.get("error") or res.get("text"),
    }

    body = res.get("json")
    if isinstance(body, dict):
        stats = body.get("stats") if isinstance(body.get("stats"), dict) else {}
        doc_id = str(body.get("document_id") or "")
        content = str(body.get("content") or "")

        case_result.update({
            "success": body.get("success") is True,
            "document_id": doc_id,
            "title": str(body.get("title") or ""),
            "content_length": len(content),
            "passed_subsections": int(stats.get("passed_subsections", 0) or 0),
            "failed_subsections": int(stats.get("failed_subsections", 0) or 0),
            "forced_subsections": int(stats.get("forced_subsections", 0) or 0),
            "total_iterations": int(stats.get("total_iterations", 0) or 0),
            "error": body.get("error") or body.get("message") or case_result["error"],
        })

        if doc_id:
            backend_metrics = backend_metrics_from_stats(stats)
            case_result["backend_metrics"] = backend_metrics
            events = get_progress_events(doc_id)
            ev_summary = analyze_events(events)
            case_result["events"] = ev_summary

            controller_triggered = backend_metrics["controller_calls_total"] > 0
            controller_not_overtriggered = backend_metrics["controller_calls_total"] <= (case_result["expected_subsections"] * 3)
            max_iter_proxy_ok = case_result["total_iterations"] <= (case_result["expected_subsections"] * 5)
            max_iteration_ok = (ev_summary["max_iteration"] <= 8 if ev_summary["max_iteration"] > 0 else max_iter_proxy_ok)
            no_dead_loop = (
                backend_metrics["controller_exhausted_total"] <= 1
                and backend_metrics["controller_unavailable_total"] <= 2
                and backend_metrics["controller_error_total"] <= 6
            )
            complete_doc = len(content.strip()) >= (1600 if chapter_count == 2 else 2600)
            all_passed = (
                case_result["success"]
                and case_result["passed_subsections"] >= case_result["expected_subsections"]
                and case_result["failed_subsections"] == 0
            )

            case_result["validation"] = {
                "controller_triggered": controller_triggered,
                "controller_not_overtriggered": controller_not_overtriggered,
                "max_iteration_ok": max_iteration_ok,
                "no_dead_loop": no_dead_loop,
                "complete_doc": complete_doc,
                "all_passed": all_passed,
            }

    return case_result


def main() -> None:
    print(f"FlowerNet stress regression @ {now()}")

    results = [
        run_case(chapter_count=2, subsection_count=2, timeout_seconds=7200),
        run_case(chapter_count=3, subsection_count=2, timeout_seconds=7200),
    ]

    overall_ok = all(
        r["validation"].get("controller_triggered")
        and r["validation"].get("controller_not_overtriggered")
        and r["validation"].get("max_iteration_ok")
        and r["validation"].get("no_dead_loop")
        and r["validation"].get("complete_doc")
        and r["validation"].get("all_passed")
        for r in results
    )

    report = {
        "timestamp": now(),
        "overall_ok": overall_ok,
        "results": results,
    }

    out_file = f"stress_regression_2x2_3x2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"report_file={out_file}")

    if not overall_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
