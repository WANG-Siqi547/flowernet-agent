#!/usr/bin/env python3
"""Run local quantized LongWriter through Ollama on Apple Silicon."""

from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List

from common import load_topics, now_iso, text_metrics, write_json


ROOT = Path(__file__).resolve().parents[1]
MODEL_NAME = "longwriter-llama3.1-8b-q4"


def build_prompt(topic: Dict[str, Any]) -> str:
    return (
        f"{topic.get('prompt')}\n\n"
        "结构要求：生成 2 个一级章节，每个一级章节包含 2 个二级小节。"
        "写作要求：研究报告风格，包含摘要、关键发现、可验证事实线索、局限与结论；"
        "尽可能加入一个简洁对比表格；不要输出模板占位符。"
    )


def ensure_model() -> None:
    model_path = ROOT / "models" / "longwriter-llama3.1-8b-gguf" / "LongWriter-llama3.1-8b-Q4_K_M.gguf"
    if not model_path.exists():
        raise FileNotFoundError(f"GGUF weight not found: {model_path}")
    existing = subprocess.run(["ollama", "list"], text=True, capture_output=True, check=False).stdout
    if MODEL_NAME in existing:
        return
    subprocess.run(
        ["ollama", "create", MODEL_NAME, "-f", str(ROOT / "baselines" / "longwriter_ollama.Modelfile")],
        cwd=str(ROOT),
        check=True,
    )


def run(args: argparse.Namespace) -> List[Dict[str, Any]]:
    topics = load_topics(args.topics, limit=args.limit, topic_id=args.topic_id)
    ensure_model()
    outputs: List[Dict[str, Any]] = []
    for topic in topics:
        prompt = build_prompt(topic)
        started = time.time()
        proc = subprocess.run(
            ["ollama", "run", MODEL_NAME, prompt],
            text=True,
            capture_output=True,
            timeout=args.timeout_seconds,
            check=False,
        )
        elapsed = time.time() - started
        text = proc.stdout.strip()
        error = proc.stderr.strip()
        outputs.append(
            {
                "baseline": "longwriter_ollama_q4",
                "topic_id": topic.get("id"),
                "topic": topic.get("topic"),
                "status": "ok" if proc.returncode == 0 and text else "failed",
                "created_at": now_iso(),
                "elapsed_seconds": round(elapsed, 2),
                "model": MODEL_NAME,
                "weight": "bartowski/LongWriter-llama3.1-8b-GGUF:Q4_K_M",
                "runtime": "Ollama GGUF on Apple Silicon / Metal",
                "prompt": prompt,
                "final_text": text,
                "error": error,
                "metrics": text_metrics(text),
            }
        )
    write_json(args.output, outputs)
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topics", default="experiments/topics_week1.json")
    parser.add_argument("--output", default="results/week1/longwriter_ollama_outputs.json")
    parser.add_argument("--topic-id", default="fw24_007")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    args = parser.parse_args()
    outputs = run(args)
    print(f"wrote {len(outputs)} LongWriter Ollama outputs to {args.output}")


if __name__ == "__main__":
    main()
