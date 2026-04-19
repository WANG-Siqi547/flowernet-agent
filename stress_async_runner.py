#!/usr/bin/env python3
import json
import time
from datetime import datetime
import requests

BASE_WEB = "https://flowernet-web.onrender.com"
REL = 0.72
RED = 0.60
POLL_INTERVAL = 20
CASE_TIMEOUT = 5400

session = requests.Session()
session.trust_env = False


def submit_case(chapters, subs):
    payload = {
        "query": f"机器人：从概念到应用的全面探索（{chapters}x{subs} 压测）",
        "chapter_count": chapters,
        "subsection_count": subs,
        "user_background": "普通读者，想系统了解机器人",
        "extra_requirements": "每节给出清晰定义、技术要点和实际案例，中文，避免空话。",
        "rel_threshold": REL,
        "red_threshold": RED,
        "async_mode": True,
        "timeout_seconds": CASE_TIMEOUT,
    }
    r = session.post(f"{BASE_WEB}/api/poffices/generate", json=payload, timeout=120)
    r.raise_for_status()
    body = r.json()
    if not body.get("success"):
        raise RuntimeError(f"submit failed: {body}")
    return body.get("task_id")


def poll_task(task_id):
    started = time.time()
    while True:
        r = session.post(
            f"{BASE_WEB}/api/poffices/task-status",
            json={"task_id": task_id},
            timeout=120,
        )
        r.raise_for_status()
        body = r.json()
        status = body.get("status") or body.get("task_status")

        elapsed = int(time.time() - started)
        print(f"  - task={task_id[:10]}... status={status} elapsed={elapsed}s", flush=True)

        if status == "completed" or body.get("task_status") == "completed":
            return {"ok": True, "elapsed": elapsed, "body": body}
        if status == "failed":
            return {"ok": False, "elapsed": elapsed, "body": body}

        if elapsed > CASE_TIMEOUT + 120:
            return {"ok": False, "elapsed": elapsed, "body": {"error": "poll timeout"}}
        time.sleep(POLL_INTERVAL)


def analyze(case_name, expected_subsections, result):
    out = {
        "case": case_name,
        "expected_subsections": expected_subsections,
        "ok": result["ok"],
        "elapsed_seconds": result["elapsed"],
        "error": None,
    }
    body = result.get("body") or {}
    if not result["ok"]:
        out["error"] = body.get("error") or body.get("message") or str(body)
        return out

    stats = body.get("stats") if isinstance(body.get("stats"), dict) else {}
    out.update(
        {
            "passed_subsections": int(stats.get("passed_subsections", 0) or 0),
            "failed_subsections": int(stats.get("failed_subsections", 0) or 0),
            "forced_subsections": int(stats.get("forced_subsections", 0) or 0),
            "total_iterations": int(stats.get("total_iterations", 0) or 0),
            "controller_calls_total": int(stats.get("controller_calls_total", 0) or 0),
            "controller_triggered_subsections": int(stats.get("controller_triggered_subsections", 0) or 0),
            "controller_success_total": int(stats.get("controller_success_total", 0) or 0),
            "controller_error_total": int(stats.get("controller_error_total", 0) or 0),
            "controller_unavailable_total": int(stats.get("controller_unavailable_total", 0) or 0),
            "controller_exhausted_total": int(stats.get("controller_exhausted_total", 0) or 0),
            "content_length": len((body.get("content") or "").strip()),
        }
    )

    calls = out["controller_calls_total"]
    triggered_sections = out["controller_triggered_subsections"]
    out["controller_triggered"] = calls > 0
    out["trigger_rate_subsections"] = round(triggered_sections / expected_subsections, 4) if expected_subsections else 0.0
    out["call_rate_per_subsection"] = round(calls / expected_subsections, 4) if expected_subsections else 0.0
    out["controller_not_overtriggered"] = calls <= expected_subsections * 3
    out["all_passed"] = out["failed_subsections"] == 0 and out["passed_subsections"] >= expected_subsections
    return out


def main():
    print(f"async stress start @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"thresholds: rel={REL}, red={RED}")

    cases = [(2, 2), (3, 2)]
    results = []
    for ch, sub in cases:
        case_name = f"{ch}x{sub}"
        expected = ch * sub
        print(f"\n=== submit case {case_name} ===", flush=True)
        task_id = submit_case(ch, sub)
        print(f"task_id={task_id}", flush=True)
        polled = poll_task(task_id)
        analyzed = analyze(case_name, expected, polled)
        results.append(analyzed)

    overall_ok = all(r.get("ok") and r.get("all_passed") and r.get("controller_not_overtriggered") for r in results)
    total_expected = sum(r.get("expected_subsections", 0) for r in results)
    total_triggered_subsections = sum(r.get("controller_triggered_subsections", 0) for r in results)
    overall_trigger_rate = round(total_triggered_subsections / total_expected, 4) if total_expected else 0.0

    report = {
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "mode": "async_poffices_stress",
        "thresholds": {"rel": REL, "red": RED},
        "overall_ok": overall_ok,
        "overall_trigger_rate_subsections": overall_trigger_rate,
        "target_trigger_rate_range": [0.30, 0.50],
        "results": results,
    }

    out_file = f"stress_async_2x2_3x2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n=== FINAL REPORT ===")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"report_file={out_file}")


if __name__ == "__main__":
    main()
