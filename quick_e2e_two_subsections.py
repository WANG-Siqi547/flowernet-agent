#!/usr/bin/env python3
import json
import os
import time
import requests

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")


def main():
    doc_id = f"quick2_{int(time.time())}"
    out_payload = {
        "document_id": doc_id,
        "user_background": "普通读者",
        "user_requirements": "详细、逻辑清晰、避免重复",
        "max_sections": 1,
        "max_subsections_per_section": 2,
    }

    print(f"[1/2] outliner start: {doc_id}")
    out_resp = requests.post(
        "http://localhost:8003/outline/generate-and-save",
        json=out_payload,
        timeout=1200,
    )
    print("outliner status:", out_resp.status_code)
    out_data = out_resp.json()
    if not out_data.get("success"):
        print("outliner failed:", json.dumps(out_data, ensure_ascii=False)[:1000])
        return 1

    prompts = out_data.get("content_prompts", [])
    print("content prompts:", len(prompts))

    gen_payload = {
        "document_id": doc_id,
        "title": out_data.get("document_title"),
        "structure": out_data.get("structure"),
        "content_prompts": prompts,
        "user_background": "普通读者",
        "user_requirements": "详细、逻辑清晰、避免重复",
        "rel_threshold": 0.75,
        "red_threshold": 0.50,
    }

    print("[2/2] generator start")
    t0 = time.time()
    gen_resp = requests.post(
        "http://localhost:8002/generate_document",
        json=gen_payload,
        timeout=2400,
    )
    dt = time.time() - t0
    print("generator status:", gen_resp.status_code, "elapsed:", round(dt, 1), "s")

    data = gen_resp.json()
    with open("quick_e2e_two_subsections_result.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    if not data.get("success"):
        print("generator failed:", json.dumps(data, ensure_ascii=False)[:1000])
        return 2

    sections = data.get("sections", [])
    total_sub = sum(len(s.get("subsections", [])) for s in sections)
    forced = len(data.get("forced_subsections", []))
    print("sections:", len(sections), "subsections:", total_sub, "forced:", forced)
    print("total_iterations:", data.get("total_iterations"), "generation_time:", data.get("generation_time"))
    print("saved: quick_e2e_two_subsections_result.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
