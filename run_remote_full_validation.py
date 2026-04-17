#!/usr/bin/env python3
import json
import os
import time
from datetime import datetime
import requests

BASE_WEB = os.getenv("WEB_URL", "https://flowernet-web.onrender.com")
BASE_GEN = os.getenv("GEN_URL", "https://flowernet-generator.onrender.com")
BASE_OUT = os.getenv("OUT_URL", "https://flowernet-outliner.onrender.com")
BASE_VER = os.getenv("VER_URL", "https://flowernet-verifier.onrender.com")
BASE_CTRL = os.getenv("CTRL_URL", "https://flowernet-controller.onrender.com")
REL_THRESHOLD = float(os.getenv("FLOWERNET_REL_THRESHOLD", "0.70"))
RED_THRESHOLD = float(os.getenv("FLOWERNET_RED_THRESHOLD", "0.62"))

SESSION = requests.Session()
SESSION.trust_env = False


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_get(url, timeout=30):
    st = time.time()
    try:
        r = SESSION.get(url, timeout=timeout)
        return {
            "ok": r.status_code == 200,
            "status": r.status_code,
            "elapsed": round(time.time() - st, 2),
            "body": (r.text or "")[:300],
        }
    except Exception as e:
        return {
            "ok": False,
            "status": None,
            "elapsed": round(time.time() - st, 2),
            "error": f"{type(e).__name__}: {e}",
        }


def safe_post(url, payload, timeout=180):
    st = time.time()
    try:
        r = SESSION.post(url, json=payload, timeout=timeout)
        out = {
            "ok": r.status_code == 200,
            "status": r.status_code,
            "elapsed": round(time.time() - st, 2),
        }
        try:
            out["json"] = r.json()
        except Exception:
            out["text"] = (r.text or "")[:800]
        return out
    except Exception as e:
        return {
            "ok": False,
            "status": None,
            "elapsed": round(time.time() - st, 2),
            "error": f"{type(e).__name__}: {e}",
        }


def health_checks():
    checks = {
        "web_health": safe_get(f"{BASE_WEB}/health"),
        "generator_health": safe_get(f"{BASE_GEN}/health"),
        "generator_root": safe_get(f"{BASE_GEN}/"),
        "outliner_root": safe_get(f"{BASE_OUT}/"),
        "verifier_root": safe_get(f"{BASE_VER}/"),
        "controller_root": safe_get(f"{BASE_CTRL}/"),
    }
    return checks


def module_smoke():
    results = {}

    results["generator_generate"] = safe_post(
        f"{BASE_GEN}/generate",
        {"prompt": "请用中文输出一句完整建议，主题是时间管理。", "max_tokens": 120},
        timeout=180,
    )

    results["outliner_generate_structure"] = safe_post(
        f"{BASE_OUT}/generate-structure",
        {
            "user_background": "大学新生",
            "user_requirements": "1章1节，主题时间管理，务实",
            "max_sections": 1,
            "max_subsections_per_section": 1,
        },
        timeout=300,
    )

    results["verifier_verify"] = safe_post(
        f"{BASE_VER}/verify",
        {
            "draft": "时间管理的核心是明确优先级，并把每天最重要的一件事固定在精力最好的时段完成。",
            "outline": "时间管理核心方法",
            "history": [],
            "rel_threshold": 0.2,
            "red_threshold": 0.9,
        },
        timeout=120,
    )

    results["controller_improve_outline"] = safe_post(
        f"{BASE_CTRL}/improve-outline",
        {
            "original_outline": "时间管理方法与执行",
            "current_outline": "时间管理概述",
            "failed_draft": "内容泛泛而谈，缺少执行步骤。",
            "feedback": {
                "relevancy_index": 0.62,
                "redundancy_index": 0.15,
                "feedback": "相关性不足，请聚焦可执行步骤"
            },
            "history": ["上一节已介绍番茄钟原理"],
            "iteration": 1,
            "rel_threshold": 0.75,
            "red_threshold": 0.5,
        },
        timeout=240,
    )

    return results


def e2e_generate_complete():
    payload = {
        "topic": "大学新生时间管理实战指南",
        "chapter_count": 1,
        "subsection_count": 1,
        "user_background": "大一新生",
        "extra_requirements": "给出可执行步骤，中文",
        "rel_threshold": REL_THRESHOLD,
        "red_threshold": RED_THRESHOLD,
        "timeout_seconds": 1800,
    }
    return safe_post(f"{BASE_WEB}/api/generate", payload, timeout=1900)


def main():
    report = {
        "timestamp": now(),
        "health": {},
        "modules": {},
        "e2e": {},
        "summary": {},
    }

    report["health"] = health_checks()
    report["modules"] = module_smoke()
    report["e2e"] = e2e_generate_complete()

    # Evaluate
    health_ok = all(v.get("ok") for v in report["health"].values())

    gen_ok = False
    gen_body = report["modules"]["generator_generate"].get("json")
    if isinstance(gen_body, dict):
        gen_ok = report["modules"]["generator_generate"].get("ok") and bool((gen_body.get("draft") or "").strip())

    out_ok = False
    out_body = report["modules"]["outliner_generate_structure"].get("json")
    if isinstance(out_body, dict):
        out_ok = report["modules"]["outliner_generate_structure"].get("ok") and out_body.get("success") is True

    ver_ok = False
    ver_body = report["modules"]["verifier_verify"].get("json")
    if isinstance(ver_body, dict):
        ver_ok = report["modules"]["verifier_verify"].get("ok") and ("is_passed" in ver_body)

    ctrl_ok = False
    ctrl_body = report["modules"]["controller_improve_outline"].get("json")
    if isinstance(ctrl_body, dict):
        ctrl_ok = report["modules"]["controller_improve_outline"].get("ok") and ("improved_outline" in ctrl_body)

    e2e_ok = False
    e2e_body = report["e2e"].get("json")
    if isinstance(e2e_body, dict):
        stats = e2e_body.get("stats") or {}
        content = e2e_body.get("content") or ""
        e2e_ok = (
            report["e2e"].get("ok")
            and e2e_body.get("success") is True
            and bool((e2e_body.get("title") or "").strip())
            and len(content.strip()) >= 500
            and int(stats.get("passed_subsections", 0) or 0) >= 1
            and int(stats.get("failed_subsections", 0) or 0) == 0
        )

    report["summary"] = {
        "health_ok": health_ok,
        "generator_ok": gen_ok,
        "outliner_ok": out_ok,
        "verifier_ok": ver_ok,
        "controller_ok": ctrl_ok,
        "e2e_complete_ok": e2e_ok,
        "overall_ok": health_ok and gen_ok and out_ok and ver_ok and ctrl_ok and e2e_ok,
    }

    out_file = f"remote_full_validation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"report_file={out_file}")

    if not report["summary"]["overall_ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
