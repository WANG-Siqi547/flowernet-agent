#!/usr/bin/env python3
"""One-shot LLM baseline over the unified FlowerNet week-1 topics."""

from __future__ import annotations

import argparse
import time
from typing import Any, Dict, List

from common import baseline_prompt, extract_text_from_result, get_flowernet_generator, load_topics, now_iso, text_metrics, write_json


def run(args: argparse.Namespace) -> List[Dict[str, Any]]:
    topics = load_topics(args.topics, limit=args.limit, topic_id=args.topic_id)
    generator = None if args.dry_run else get_flowernet_generator()
    outputs: List[Dict[str, Any]] = []
    for topic in topics:
        prompt = baseline_prompt(topic, style="vanilla")
        started = time.time()
        if args.dry_run:
            text = f"# {topic['topic']}\n\n[DRY RUN] Vanilla LLM prompt prepared.\n\n{prompt[:500]}"
            raw = {"success": True, "draft": text, "metadata": {"dry_run": True}}
        else:
            raw = generator.generate_draft(prompt, max_tokens=args.max_tokens, allow_compact_fallback=False)
            text = extract_text_from_result(raw)
        elapsed = time.time() - started
        outputs.append(
            {
                "baseline": "vanilla_llm",
                "topic_id": topic.get("id"),
                "topic": topic.get("topic"),
                "status": "ok" if raw.get("success") and text else "failed",
                "created_at": now_iso(),
                "elapsed_seconds": round(elapsed, 2),
                "prompt": prompt,
                "final_text": text,
                "raw_metadata": raw.get("metadata", {}),
                "error": raw.get("error", ""),
                "metrics": text_metrics(text),
            }
        )
    write_json(args.output, outputs)
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topics", default="experiments/topics_week1.json")
    parser.add_argument("--output", default="results/week1/vanilla_outputs.json")
    parser.add_argument("--topic-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-tokens", type=int, default=2400)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    outputs = run(args)
    print(f"wrote {len(outputs)} vanilla outputs to {args.output}")


if __name__ == "__main__":
    main()
