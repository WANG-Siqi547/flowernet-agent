#!/usr/bin/env python3
import json
import time
import requests
import os

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")


def main():
    doc_id = f"quick_{int(time.time())}"
    out_payload = {
        "document_id": doc_id,
        "user_background": "普通读者",
        "user_requirements": "简洁清晰",
        "max_sections": 1,
        "max_subsections_per_section": 1,
    }

    print(f"outliner start: {doc_id}")
    out_resp = requests.post(
        "http://localhost:8003/outline/generate-and-save",
        json=out_payload,
        timeout=1200,
    )
    print("outliner status:", out_resp.status_code)

    outline = out_resp.json()
    if not outline.get("success"):
        print("outline failed:", json.dumps(outline, ensure_ascii=False)[:1000])
        return

    gen_payload = {
        "document_id": doc_id,
        "title": outline.get("document_title"),
        "structure": outline.get("structure"),
        "content_prompts": outline.get("content_prompts"),
        "user_background": "普通读者",
        "user_requirements": "简洁清晰",
        "rel_threshold": 0.75,
        "red_threshold": 0.50,
    }

    print("generator start, prompts:", len(gen_payload.get("content_prompts", [])))
    start = time.time()
    gen_resp = requests.post(
        "http://localhost:8002/generate_document",
        json=gen_payload,
        timeout=1800,
    )
    elapsed = time.time() - start

    print("generator status:", gen_resp.status_code, "elapsed:", round(elapsed, 1), "s")
    try:
        data = gen_resp.json()
    except Exception:
        print(gen_resp.text[:2000])
        return

    with open("quick_e2e_small_result.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("saved: quick_e2e_small_result.json")


if __name__ == "__main__":
    main()
