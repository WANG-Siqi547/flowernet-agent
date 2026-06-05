#!/usr/bin/env python3
"""CogWriter adapter for FlowerNet's unified topic set and DeepSeek/OpenAI-compatible backend."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from common import load_dotenv, load_topics, now_iso, text_metrics, write_json


ROOT = Path(__file__).resolve().parents[1]
COGWRITER_DIR = ROOT / "baselines" / "external" / "CogWriter"


def git_head(path: Path) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def to_cogwriter_examples(topics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    examples: List[Dict[str, Any]] = []
    for topic in topics:
        examples.append(
            {
                "id": topic.get("id"),
                "type": "Block",
                "prompt": (
                    f"{topic.get('prompt')}\n\n"
                    "Please produce a structured long-form report with exactly 2 top-level chapters and exactly 2 subsections in each chapter. "
                    "Use '#*#' only if the original CogWriter generator needs block separators."
                ),
                "source_topic": topic,
            }
        )
    return examples


async def call_deepseek(prompt: str, max_tokens: int) -> str:
    from openai import AsyncOpenAI  # type: ignore

    load_dotenv()
    api_key = os.getenv("GENERATOR_DEEPSEEK_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")
    base_url = (os.getenv("GENERATOR_DEEPSEEK_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
    model = os.getenv("GENERATOR_DEEPSEEK_MODEL") or os.getenv("DEEPSEEK_MODEL") or "deepseek-v4-flash"
    client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=max_tokens,
    )
    return (response.choices[0].message.content or "").strip()


async def run_baseline_cogwriter_style(examples: List[Dict[str, Any]], max_tokens: int, dry_run: bool) -> List[Dict[str, Any]]:
    outputs = []
    for example in examples:
        started = time.time()
        if dry_run:
            text = f"# {example['source_topic']['topic']}\n\n[DRY RUN] CogWriter-compatible prompt prepared.\n\n{example['prompt'][:500]}"
        else:
            text = await call_deepseek(example["prompt"], max_tokens=max_tokens)
        outputs.append(
            {
                "baseline": "cogwriter_adapter",
                "topic_id": example.get("id"),
                "topic": example["source_topic"].get("topic"),
                "status": "ok" if text else "failed",
                "elapsed_seconds": round(time.time() - started, 2),
                "final_text": text,
                "metrics": text_metrics(text),
            }
        )
    return outputs


def run(args: argparse.Namespace) -> Dict[str, Any]:
    topics = load_topics(args.topics, limit=args.limit, topic_id=args.topic_id)
    examples = to_cogwriter_examples(topics)
    write_json(args.dataset_output, examples)
    readiness = {
        "repo_path": str(COGWRITER_DIR),
        "repo_exists": COGWRITER_DIR.exists(),
        "git_head": git_head(COGWRITER_DIR) if COGWRITER_DIR.exists() else "",
        "python": sys.version.split()[0],
        "adapter_backend": "DeepSeek/OpenAI-compatible",
        "original_entrypoint": "baselines/external/CogWriter/main.py",
    }
    outputs = asyncio.run(run_baseline_cogwriter_style(examples, args.max_tokens, args.dry_run))
    payload = {
        "baseline": "cogwriter",
        "status": "ok" if all(o["status"] == "ok" for o in outputs) else "partial",
        "created_at": now_iso(),
        "readiness": readiness,
        "source": {
            "repo": "https://github.com/KaiyangWan/CogWriter",
            "paper_or_project": "A training-free cognitive writing framework for constrained long-form text generation",
        },
        "dataset": str(Path(args.dataset_output).resolve()),
        "outputs": outputs,
    }
    write_json(args.output, payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topics", default="experiments/topics_week1.json")
    parser.add_argument("--output", default="results/week1/cogwriter_outputs.json")
    parser.add_argument("--dataset-output", default="results/week1/cogwriter_topics.json")
    parser.add_argument("--topic-id")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--max-tokens", type=int, default=2400)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    payload = run(args)
    print(f"CogWriter adapter {payload['status']}; wrote {args.output}")


if __name__ == "__main__":
    main()
