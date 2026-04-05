#!/usr/bin/env python3
"""
远端完整回归测试
在 Render 部署完成后运行此脚本验证所有功能
"""
import json
import time
from datetime import datetime
import requests

BASE_WEB = "https://flowernet-web.onrender.com"
BASE_GEN = "https://flowernet-generator.onrender.com"
BASE_OUT = "https://flowernet-outliner.onrender.com"

SESSION = requests.Session()

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def safe_get(url, timeout=20):
    st = time.time()
    try:
        r = SESSION.get(url, timeout=timeout)
        return {
            "ok": r.status_code == 200,
            "status": r.status_code,
            "elapsed": round(time.time() - st, 2),
            "body": (r.text or "")[:180],
        }
    except Exception as e:
        return {
            "ok": False,
            "status": None,
            "elapsed": round(time.time() - st, 2),
            "error": f"{type(e).__name__}",
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
        except:
            out["text"] = (r.text or "")[:400]
        return out
    except Exception as e:
        return {
            "ok": False,
            "status": None,
            "elapsed": round(time.time() - st, 2),
            "error": f"{type(e).__name__}",
        }

def health_checks():
    print("\n=== [1] Remote Health Checks ===")
    checks = {
        "web": safe_get(f"{BASE_WEB}/health"),
        "generator": safe_get(f"{BASE_GEN}/health"),
        "outliner": safe_get(f"{BASE_OUT}/"),
    }
    for name, result in checks.items():
        print(f"- {name}: ok={result.get('ok')} status={result.get('status')} elapsed={result.get('elapsed')}s")
        if not result.get("ok"):
            print(f"  error: {result.get('error')}")
    all_ok = all(v.get("ok") for v in checks.values())
    return all_ok, checks

def module_smoke():
    print("\n=== [2] Module Smoke Tests ===")
    
    print("- Testing generator /generate...")
    gen_res = safe_post(
        f"{BASE_GEN}/generate",
        {"prompt": "Explain time management in two sentences.", "max_tokens": 100},
        timeout=180,
    )
    gen_ok = False
    if gen_res.get("ok") and isinstance(gen_res.get("json"), dict):
        body = gen_res["json"]
        gen_ok = body.get("success") is True and len((body.get("draft") or "").strip()) > 20
    print(f"  ok={gen_ok} status={gen_res.get('status')} elapsed={gen_res.get('elapsed')}s")
    
    print("- Testing outliner /generate-structure...")
    out_res = safe_post(
        f"{BASE_OUT}/generate-structure",
        {
            "user_background": "college student",
            "user_requirements": "1 chapter 1 subsection on time management",
            "max_sections": 1,
            "max_subsections_per_section": 1,
        },
        timeout=240,
    )
    out_ok = False
    if out_res.get("ok") and isinstance(out_res.get("json"), dict):
        body = out_res["json"]
        sections = ((body.get("structure") or {}).get("sections") or []) if body.get("success") else []
        out_ok = body.get("success") is True and len(sections) >= 1
    print(f"  ok={out_ok} status={out_res.get('status')} elapsed={out_res.get('elapsed')}s")
    
    return gen_ok and out_ok, {"generator_ok": gen_ok, "outliner_ok": out_ok}

def e2e_test():
    print("\n=== [3] End-to-End Document Generation (1x1) ===")
    
    payload = {
        "topic": "Time management for college freshmen",
        "chapter_count": 1,
        "subsection_count": 1,
        "user_background": "first year student",
        "extra_requirements": "practical and concise",
        "timeout_seconds": 1500,
        "rel_threshold": 0.2,
        "red_threshold": 0.8,
    }
    
    print("- Sending request to /api/generate...")
    res = safe_post(f"{BASE_WEB}/api/generate", payload, timeout=1600)
    
    e2e_ok = False
    details = {
        "http_status": res.get("status"),
        "elapsed": res.get("elapsed"),
        "success": False,
    }
    
    if isinstance(res.get("json"), dict):
        body = res["json"]
        title = body.get("title") or ""
        content = body.get("content") or ""
        success = body.get("success") is True
        stats = body.get("stats", {})
        
        details.update({
            "success": success,
            "title": title[:60],
            "content_length": len(content),
            "passed_subsections": stats.get("passed_subsections"),
            "failed_subsections": stats.get("failed_subsections"),
        })
        
        e2e_ok = (
            res.get("ok")
            and success
            and bool(title.strip())
            and len(content.strip()) >= 800
            and stats.get("failed_subsections") in (0, None)
        )
    
    for key, val in details.items():
        print(f"  {key}={val}")
    
    return e2e_ok, details

def main():
    print(f"🌸 FlowerNet Remote Full Regression @ {now()}")
    
    health_ok, health_detail = health_checks()
    
    if not health_ok:
        print("\n❌ Health checks failed. Services may still be starting up.")
        print("Please wait for Render deployment to complete and try again.")
        return False
    
    module_ok, module_detail = module_smoke()
    e2e_ok, e2e_detail = e2e_test()
    
    overall = health_ok and module_ok and e2e_ok
    
    final = {
        "timestamp": now(),
        "overall_pass": overall,
        "health_ok": health_ok,
        "module_smoke_ok": module_ok,
        "e2e_ok": e2e_ok,
        "details": {
            "health": health_detail,
            "module": module_detail,
            "e2e": e2e_detail,
        },
    }
    
    print("\n=== FINAL VERDICT ===")
    print(json.dumps(final, indent=2, ensure_ascii=False))
    
    return overall

if __name__ == "__main__":
    result = main()
    exit(0 if result else 1)
