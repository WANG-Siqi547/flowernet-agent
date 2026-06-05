#!/usr/bin/env python3
"""Readiness and optional execution wrapper for THUDM LongWriter."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from common import load_topics, now_iso, write_json


ROOT = Path(__file__).resolve().parents[1]
LONGWRITER_DIR = ROOT / "baselines" / "external" / "LongWriter"


def git_head(path: Path) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def readiness(model: str) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "repo_path": str(LONGWRITER_DIR),
        "repo_exists": LONGWRITER_DIR.exists(),
        "git_head": git_head(LONGWRITER_DIR) if LONGWRITER_DIR.exists() else "",
        "model": model,
        "python": sys.version.split()[0],
    }
    try:
        import torch  # type: ignore

        info["torch_available"] = True
        info["cuda_available"] = bool(torch.cuda.is_available())
        info["cuda_device_count"] = int(torch.cuda.device_count()) if torch.cuda.is_available() else 0
        if torch.cuda.is_available():
            info["cuda_device_name"] = torch.cuda.get_device_name(0)
            total = torch.cuda.get_device_properties(0).total_memory
            info["cuda_total_gb"] = round(total / (1024**3), 2)
    except Exception as exc:
        info["torch_available"] = False
        info["torch_error"] = str(exc)
    for package in ["transformers", "vllm"]:
        try:
            __import__(package)
            info[f"{package}_available"] = True
        except Exception as exc:
            info[f"{package}_available"] = False
            info[f"{package}_error"] = str(exc)
    return info


def run(args: argparse.Namespace) -> Dict[str, Any]:
    topics = load_topics(args.topics, limit=args.limit, topic_id=args.topic_id)
    info = readiness(args.model)
    prompts = [
        {
            "topic_id": t.get("id"),
            "topic": t.get("topic"),
            "prompt": t.get("prompt"),
            "max_new_tokens": args.max_new_tokens,
        }
        for t in topics
    ]
    status = "prepared_not_executed"
    reason = "dry_run requested"
    outputs: List[Dict[str, Any]] = []
    if args.execute:
        if not info.get("cuda_available"):
            status = "blocked"
            reason = "LongWriter model execution requires a suitable CUDA GPU/checkpoint; this machine did not report CUDA availability."
        elif not info.get("transformers_available"):
            status = "blocked"
            reason = "transformers is not installed in the active environment."
        else:
            status = "execution_not_implemented_in_wrapper"
            reason = "Use LongWriter README HF/vLLM command with the prepared prompts; wrapper intentionally avoids downloading an 8B/9B checkpoint without explicit model cache."
    payload = {
        "baseline": "longwriter",
        "status": status,
        "reason": reason,
        "created_at": now_iso(),
        "readiness": info,
        "source": {
            "repo": "https://github.com/THUDM/LongWriter",
            "paper": "https://arxiv.org/abs/2408.07055",
            "models": ["THUDM/LongWriter-glm4-9b", "THUDM/LongWriter-llama3.1-8b"],
        },
        "prompts": prompts,
        "outputs": outputs,
    }
    write_json(args.output, payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topics", default="experiments/topics_week1.json")
    parser.add_argument("--output", default="results/week1/longwriter_status.json")
    parser.add_argument("--topic-id")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--model", default=os.getenv("LONGWRITER_MODEL", "THUDM/LongWriter-glm4-9b"))
    parser.add_argument("--max-new-tokens", type=int, default=8192)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    payload = run(args)
    print(f"LongWriter {payload['status']}: {payload['reason']}")


if __name__ == "__main__":
    main()
