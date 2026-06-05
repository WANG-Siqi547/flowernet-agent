#!/usr/bin/env python3
"""Run LongWriter through an OpenAI-compatible remote vLLM endpoint."""

from __future__ import annotations

import argparse
import os
import time
from typing import Any, Dict, List

from openai import OpenAI

from common import load_topics, now_iso, text_metrics, write_json


def build_prompt(topic: Dict[str, Any]) -> str:
    return (
        f"{topic.get('prompt')}\n\n"
        "结构要求：生成 2 个一级章节，每个一级章节包含 2 个二级小节。"
        "写作要求：研究报告风格，包含摘要、关键发现、可验证事实线索、局限与结论；"
        "尽可能加入一个简洁对比表格；不要输出模板占位符。"
    )


def run(args: argparse.Namespace) -> List[Dict[str, Any]]:
    topics = load_topics(args.topics, limit=args.limit, topic_id=args.topic_id)
    client = OpenAI(base_url=args.base_url.rstrip("/") + "/v1", api_key=args.api_key)
    rows: List[Dict[str, Any]] = []
    for topic in topics:
        prompt = build_prompt(topic)
        started = time.time()
        try:
            response = client.chat.completions.create(
                model=args.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                timeout=args.timeout_seconds,
            )
            text = (response.choices[0].message.content or "").strip()
            status = "ok" if text else "failed_empty_output"
            error = ""
        except Exception as exc:
            text = ""
            status = "failed"
            error = str(exc)
        rows.append(
            {
                "baseline": "longwriter_remote_vllm",
                "topic_id": topic.get("id"),
                "topic": topic.get("topic"),
                "status": status,
                "created_at": now_iso(),
                "elapsed_seconds": round(time.time() - started, 2),
                "model": args.model,
                "endpoint": args.base_url,
                "final_text": text,
                "error": error,
                "metrics": text_metrics(text),
            }
        )
    write_json(args.output, rows)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topics", default="experiments/topics_week1.json")
    parser.add_argument("--topic-id", default="fw24_007")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", default="results/week1/longwriter_remote_outputs.json")
    parser.add_argument("--base-url", default=os.getenv("LONGWRITER_BASE_URL", "http://127.0.0.1:8088"))
    parser.add_argument("--api-key", default=os.getenv("LONGWRITER_API_KEY", "EMPTY"))
    parser.add_argument("--model", default=os.getenv("LONGWRITER_MODEL", "longwriter"))
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    args = parser.parse_args()
    outputs = run(args)
    print(f"wrote {len(outputs)} remote LongWriter outputs to {args.output}")


if __name__ == "__main__":
    main()
