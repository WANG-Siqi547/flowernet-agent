#!/usr/bin/env python3
import json
import os
import time
from datetime import datetime

import requests

BASE_WEB = os.getenv("WEB_URL", "http://localhost:8010")
BASE_GEN = os.getenv("GEN_URL", "http://localhost:8002")
BASE_OUT = os.getenv("OUT_URL", "http://localhost:8003")
BASE_VER = os.getenv("VER_URL", "http://localhost:8000")
BASE_CTRL = os.getenv("CTRL_URL", "http://localhost:8001")

SESSION = requests.Session()
SESSION.trust_env = False
REL_THRESHOLD = float(os.getenv("FLOWERNET_REL_THRESHOLD", "0.70"))
RED_THRESHOLD = float(os.getenv("FLOWERNET_RED_THRESHOLD", "0.62"))


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_get(url: str, timeout: int = 15):
    started = time.time()
    try:
        resp = SESSION.get(url, timeout=timeout)
        return {
            "ok": resp.status_code == 200,
            "status": resp.status_code,
            "elapsed": round(time.time() - started, 3),
            "body": (resp.text or "")[:200],
        }
    except Exception as e:
        return {
            "ok": False,
            "status": None,
            "elapsed": round(time.time() - started, 3),
            "error": f"{type(e).__name__}: {e}",
        }


def safe_post(url: str, payload: dict, timeout: int = 60):
    started = time.time()
    try:
        resp = SESSION.post(url, json=payload, timeout=timeout)
        info = {
            "ok": resp.status_code == 200,
            "status": resp.status_code,
            "elapsed": round(time.time() - started, 3),
        }
        try:
            info["json"] = resp.json()
        except Exception:
            info["text"] = (resp.text or "")[:500]
        return info
    except Exception as e:
        return {
            "ok": False,
            "status": None,
            "elapsed": round(time.time() - started, 3),
            "error": f"{type(e).__name__}: {e}",
        }


def health_checks():
    print("\n=== [1] Health checks ===")
    checks = {
        "web_root": safe_get(f"{BASE_WEB}/"),
        "web_health": safe_get(f"{BASE_WEB}/health"),
        "generator_root": safe_get(f"{BASE_GEN}/"),
        "generator_health": safe_get(f"{BASE_GEN}/health"),
        "outliner_root": safe_get(f"{BASE_OUT}/"),
    }
    for name, result in checks.items():
        print(f"- {name}: ok={result.get('ok')} status={result.get('status')} elapsed={result.get('elapsed')}s")
        if not result.get("ok"):
            print(f"  detail: {result.get('error') or result.get('body')}")
    all_ok = all(v.get("ok") for v in checks.values())
    return all_ok, checks


def stability_probe(rounds: int = 10):
    print("\n=== [2] Stability probe (repeated calls) ===")
    failures = []
    latencies = []

    for i in range(rounds):
        res = safe_get(f"{BASE_GEN}/", timeout=10)
        latencies.append(res.get("elapsed", 0))
        if not res.get("ok"):
            failures.append(("generator_root", i + 1, res))
        time.sleep(0.2)

    for i in range(rounds):
        payload = {
            "prompt": f"稳定性测试第{i+1}次：请仅输出OK",
            "max_tokens": 64,
        }
        res = safe_post(f"{BASE_GEN}/generate", payload, timeout=120)
        latencies.append(res.get("elapsed", 0))
        body = res.get("json", {}) if isinstance(res.get("json"), dict) else {}
        logical_ok = res.get("ok") and body.get("success") is True and bool((body.get("draft") or "").strip())
        if not logical_ok:
            failures.append(("generator_generate", i + 1, res))
        time.sleep(0.4)

    fail_count = len(failures)
    avg_latency = round(sum(latencies) / max(1, len(latencies)), 3)
    p95 = sorted(latencies)[int(0.95 * (len(latencies) - 1))] if latencies else 0

    print(f"- rounds={rounds * 2}, failures={fail_count}, avg_latency={avg_latency}s, p95={round(p95,3)}s")
    if failures:
        print("- sample failures:")
        for item in failures[:5]:
            name, idx, detail = item
            print(f"  * {name} #{idx}: status={detail.get('status')} err={detail.get('error') or detail.get('text') or detail.get('json')}")

    stable = fail_count == 0
    return stable, {
        "rounds": rounds * 2,
        "failures": fail_count,
        "avg_latency": avg_latency,
        "p95_latency": round(p95, 3),
        "samples": failures[:5],
    }


def module_smoke():
    print("\n=== [3] Module smoke checks ===")

    gen_res = safe_post(
        f"{BASE_GEN}/generate",
        {"prompt": "请用一句话解释时间管理的重要性", "max_tokens": 200},
        timeout=180,
    )
    gen_ok = False
    if gen_res.get("ok") and isinstance(gen_res.get("json"), dict):
        body = gen_res["json"]
        gen_ok = body.get("success") is True and len((body.get("draft") or "").strip()) > 20

    out_res = safe_post(
        f"{BASE_OUT}/generate-structure",
        {
            "user_background": "大一新生",
            "user_requirements": "2章每章2节，主题是时间管理",
            "max_sections": 2,
            "max_subsections_per_section": 2,
        },
        timeout=300,
    )
    out_ok = False
    if out_res.get("ok") and isinstance(out_res.get("json"), dict):
        body = out_res["json"]
        sections = ((body.get("structure") or {}).get("sections") or []) if body.get("success") else []
        out_ok = body.get("success") is True and len(sections) >= 1

    print(f"- generator /generate ok={gen_ok} status={gen_res.get('status')} elapsed={gen_res.get('elapsed')}s")
    print(f"- outliner /generate_structure ok={out_ok} status={out_res.get('status')} elapsed={out_res.get('elapsed')}s")

    return gen_ok and out_ok, {
        "generator": gen_res,
        "outliner": out_res,
        "generator_ok": gen_ok,
        "outliner_ok": out_ok,
    }


def e2e_document_test():
    print("\n=== [4] End-to-end document generation ===")

    payload = {
        "topic": "大学新生时间管理与学习习惯指南",
        "chapter_count": 2,
        "subsection_count": 2,
        "user_background": "大一新生，想建立学习和作息体系",
        "extra_requirements": "内容要可执行，包含具体方法与示例",
        "rel_threshold": REL_THRESHOLD,
        "red_threshold": RED_THRESHOLD,
        "timeout_seconds": 4800,
    }

    started = time.time()
    res = safe_post(f"{BASE_WEB}/api/generate", payload, timeout=5200)
    elapsed = round(time.time() - started, 2)

    e2e_ok = False
    details = {
        "http_status": res.get("status"),
        "elapsed_seconds": elapsed,
        "success": False,
        "partial": None,
        "title": "",
        "content_length": 0,
        "expected_subsections": None,
        "passed_subsections": None,
        "forced_subsections": None,
        "failed_subsections": None,
        "error": res.get("error"),
    }

    if isinstance(res.get("json"), dict):
        body = res["json"]
        title = body.get("title") or ""
        content = body.get("content") or ""
        success = body.get("success") is True
        partial = body.get("partial")

        stats = body.get("stats") if isinstance(body.get("stats"), dict) else body.get("metadata", {})
        expected = stats.get("expected_subsections")
        passed = stats.get("passed_subsections")
        forced = stats.get("forced_subsections")
        failed = stats.get("failed_subsections")

        details.update({
            "success": success,
            "partial": partial,
            "title": title,
            "content_length": len(content),
            "expected_subsections": expected,
            "passed_subsections": passed,
            "forced_subsections": forced,
            "failed_subsections": failed,
            "error": body.get("error") or body.get("message") or body.get("detail"),
        })

        e2e_ok = (
            res.get("ok")
            and success
            and bool(title.strip())
            and len(content.strip()) >= 1200
            and (failed in (0, None))
        )
    elif res.get("text"):
        details["error"] = res.get("text")

    print(f"- HTTP status={details['http_status']} elapsed={details['elapsed_seconds']}s")
    print(f"- success={details['success']} partial={details['partial']} title={details['title'][:40]}")
    print(f"- content_length={details['content_length']} expected={details['expected_subsections']} passed={details['passed_subsections']} forced={details['forced_subsections']} failed={details['failed_subsections']}")
    if details.get("error"):
        print(f"- error={details['error']}")

    return e2e_ok, details


def main():
    print(f"FlowerNet Full Regression @ {now()}")

    health_ok, health_detail = health_checks()
    stable_ok, stable_detail = stability_probe(rounds=10)
    module_ok, module_detail = module_smoke()
    e2e_ok, e2e_detail = e2e_document_test()

    overall = health_ok and stable_ok and module_ok and e2e_ok

    final = {
        "timestamp": now(),
        "overall_pass": overall,
        "all_health_ok": health_ok,
        "stability_ok": stable_ok,
        "module_smoke_ok": module_ok,
        "e2e_complete_ok": e2e_ok,
        "health": health_detail,
        "stability": stable_detail,
        "module": {
            "generator_ok": module_detail.get("generator_ok"),
            "outliner_ok": module_detail.get("outliner_ok"),
        },
        "e2e": e2e_detail,
    }

    print("\n=== [5] Final verdict ===")
    print(json.dumps(final, ensure_ascii=False, indent=2))

    out_path = f"full_regression_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)
    print(f"\nSaved report: {out_path}")

    raise SystemExit(0 if overall else 1)


if __name__ == "__main__":
    main()
