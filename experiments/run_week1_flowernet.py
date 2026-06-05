#!/usr/bin/env python3
"""Run the Week 1 FlowerNet 2x2 smoke test against the local web API."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Dict

import requests


ROOT = Path(__file__).resolve().parents[1]


def load_topic(topic_id: str) -> Dict[str, Any]:
    topics_path = ROOT / "experiments" / "topics_week1.json"
    payload = json.loads(topics_path.read_text(encoding="utf-8"))
    for topic in payload.get("topics", []):
        if topic.get("id") == topic_id:
            return topic
    raise ValueError(f"Topic id not found: {topic_id}")


def main() -> None:
    # Local smoke tests must talk to localhost directly. Some macOS/browser
    # setups export HTTP proxy variables, which can route 127.0.0.1 through an
    # external proxy and make an otherwise healthy local service look broken.
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost,::1")
    os.environ.setdefault("no_proxy", "127.0.0.1,localhost,::1")

    parser = argparse.ArgumentParser()
    parser.add_argument("--topic-id", default="fw24_007")
    parser.add_argument("--url", default="http://127.0.0.1:8010/api/generate")
    parser.add_argument("--output", default="results/week1/flowernet_smoke_output.json")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--chapter-count", type=int, default=2)
    parser.add_argument("--subsection-count", type=int, default=2)
    args = parser.parse_args()

    topic = load_topic(args.topic_id)
    topic_prompt = topic.get("prompt") or topic.get("topic")
    payload = {
        "topic": topic_prompt,
        "chapter_count": args.chapter_count,
        "subsection_count": args.subsection_count,
        "user_background": "AI researcher",
        "extra_requirements": (
            f"{topic_prompt}\n"
            "请严格按照2章、每章2小节生成；所有一级章节标题和二级小节标题必须使用中文；"
            "保留证据线索、引用标记、局限性、比较表格和结论。"
        ),
        "timeout_seconds": args.timeout,
    }
    started = time.time()
    session = requests.Session()
    session.trust_env = False
    response = session.post(args.url, json=payload, timeout=args.timeout + 300)
    elapsed = time.time() - started
    try:
        data = response.json()
    except Exception:
        data = {"success": False, "error": response.text}
    data["_elapsed_seconds"] = round(elapsed, 2)
    data["_experiment_scale"] = f"{args.chapter_count}x{args.subsection_count}"
    data["_topic_id"] = args.topic_id
    data["_http_status"] = response.status_code
    data["_request_payload"] = payload

    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"FlowerNet HTTP {response.status_code}; success={data.get('success')}; elapsed={elapsed:.2f}s")
    print(output)


if __name__ == "__main__":
    main()
