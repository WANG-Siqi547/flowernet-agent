#!/usr/bin/env python3
"""Fair length-controlled benchmark helper.

This script is intentionally separate from the original Week-2 runner.  It
reruns selected systems under a shared length target and then writes outputs
that can be passed to ``evaluate_journal_metrics.py``.  The goal is not to
rewrite old scores; it creates a new, explicitly labeled comparison where
ROUGE/BERTScore gaps are less confounded by output length.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "baselines") not in sys.path:
    sys.path.insert(0, str(ROOT / "baselines"))

from common import extract_text_from_result, get_flowernet_generator, load_dotenv, load_topics, now_iso, text_metrics, write_json  # type: ignore
from run_week2_benchmark import run_flowernet_variant  # type: ignore


def force_deepseek_runtime() -> None:
    """Hard-lock all local benchmark calls to the same DeepSeek backend."""
    os.environ["GENERATOR_PROVIDER"] = "deepseek"
    os.environ["GENERATOR_PROVIDER_CHAIN"] = "deepseek"
    os.environ["GENERATOR_DEEPSEEK_MODEL"] = os.getenv("GENERATOR_DEEPSEEK_MODEL") or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    os.environ["DEEPSEEK_MODEL"] = os.getenv("GENERATOR_DEEPSEEK_MODEL", "deepseek-v4-flash")
    os.environ["PROVIDER_RETRIES"] = os.getenv("PROVIDER_RETRIES", "2")
    os.environ["PROVIDER_HTTP_TIMEOUT"] = os.getenv("PROVIDER_HTTP_TIMEOUT", "90")


def _target_instruction(min_chars: int, max_chars: int) -> str:
    return (
        f"\n\nLength-control requirement for fair evaluation: write {min_chars}-{max_chars} Chinese characters "
        "or an equivalent long-form English length. Maintain exactly 2 top-level sections and 2 subsections per section. "
        "Do not pad with repetition; expand with topic-specific mechanisms, evidence, examples, evaluation metrics, "
        "risks, and future directions."
    )


def _generate(prompt: str, max_tokens: int) -> Dict[str, Any]:
    return get_flowernet_generator().generate_draft(prompt, max_tokens=max_tokens, allow_compact_fallback=False)


def run_vanilla_length_controlled(topic: Dict[str, Any], max_tokens: int, min_chars: int, max_chars: int) -> Dict[str, Any]:
    prompt = (
        f"{topic.get('prompt') or topic.get('topic')}\n"
        "Generate the complete document in one pass. Use a professional academic style with citations, "
        "a compact comparison table, limitations, and references."
        + _target_instruction(min_chars, max_chars)
    )
    started = time.time()
    raw = _generate(prompt, max_tokens=max_tokens)
    text = extract_text_from_result(raw)
    return {
        "system": "vanilla_length_controlled",
        "topic_id": topic.get("id"),
        "topic": topic.get("topic"),
        "status": "ok" if text else "failed",
        "elapsed_seconds": round(time.time() - started, 2),
        "final_text": text,
        "error": raw.get("error", "") if isinstance(raw, dict) else "",
        "llm_calls": 1,
        "controller_calls": 0,
        "verified_subsections": 0,
        "forced_pass_subsections": 0,
        "metrics": text_metrics(text),
        "fair_eval_note": f"Length-controlled baseline target={min_chars}-{max_chars} chars; no verifier/controller.",
    }


def run_self_refine_length_controlled(topic: Dict[str, Any], max_tokens: int, min_chars: int, max_chars: int) -> Dict[str, Any]:
    base = topic.get("prompt") or topic.get("topic") or ""
    started = time.time()
    draft_prompt = (
        f"{base}\n\nWrite a first complete long-form academic draft."
        + _target_instruction(min_chars, max_chars)
    )
    draft_raw = _generate(draft_prompt, max_tokens=max_tokens)
    draft = extract_text_from_result(draft_raw)
    critique_prompt = (
        "Critique this draft for topic-specific coverage, evidence grounding, structure, redundancy, and missing references. "
        "Do not rewrite yet; provide concise actionable issues only.\n\n"
        f"Topic: {base}\n\nDraft:\n{draft[:12000]}"
    )
    critique_raw = _generate(critique_prompt, max_tokens=max(600, max_tokens // 3))
    critique = extract_text_from_result(critique_raw)
    rewrite_prompt = (
        "Rewrite the document using the critique. Keep all useful content, remove repetition, and expand only with "
        "topic-specific coverage and evidence-grounded analysis.\n\n"
        f"Topic: {base}\n\nCritique:\n{critique}\n\nDraft:\n{draft[:12000]}"
        + _target_instruction(min_chars, max_chars)
    )
    rewrite_raw = _generate(rewrite_prompt, max_tokens=max_tokens)
    text = extract_text_from_result(rewrite_raw)
    errors = " | ".join(
        str(item.get("error", ""))
        for item in (draft_raw, critique_raw, rewrite_raw)
        if isinstance(item, dict) and item.get("error")
    )
    return {
        "system": "self_refine_length_controlled",
        "topic_id": topic.get("id"),
        "topic": topic.get("topic"),
        "status": "ok" if text else "failed",
        "elapsed_seconds": round(time.time() - started, 2),
        "final_text": text,
        "error": errors,
        "llm_calls": 3,
        "controller_calls": 0,
        "verified_subsections": 0,
        "forced_pass_subsections": 0,
        "metrics": text_metrics(text),
        "fair_eval_note": f"Length-controlled self-refine target={min_chars}-{max_chars} chars; no FlowerNet verifier/controller.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topics", default="experiments/topics_week1.json")
    parser.add_argument("--output", default="results/week2/fair_length_controlled_outputs.json")
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--topic-id")
    parser.add_argument("--systems", default="vanilla_length_controlled,self_refine_length_controlled,flowernet_full")
    parser.add_argument("--min-chars", type=int, default=4500)
    parser.add_argument("--max-chars", type=int, default=5000)
    parser.add_argument("--max-tokens", type=int, default=2400)
    parser.add_argument("--flowernet-max-attempts", type=int, default=5)
    parser.add_argument("--force-deepseek", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    if args.force_deepseek:
        force_deepseek_runtime()
    topics = load_topics(args.topics, limit=args.limit, topic_id=args.topic_id)
    systems = [s.strip() for s in args.systems.split(",") if s.strip()]
    out_path = ROOT / args.output
    rows: List[Dict[str, Any]] = []
    done = set()
    if args.resume and out_path.exists():
        rows = json.loads(out_path.read_text(encoding="utf-8"))
        done = {(r.get("system"), r.get("topic_id")) for r in rows}

    os.environ.setdefault("ORCH_GENERATOR_MAX_TOKENS", str(args.max_tokens))
    os.environ.setdefault("ORCH_MIN_DRAFT_CHARS", "900")

    for topic in topics:
        for system in systems:
            key = (system, topic.get("id"))
            if key in done:
                continue
            print(f"[fair] {system} :: {topic.get('id')} {topic.get('topic')}", flush=True)
            try:
                if system == "vanilla_length_controlled":
                    row = run_vanilla_length_controlled(topic, args.max_tokens, args.min_chars, args.max_chars)
                elif system == "self_refine_length_controlled":
                    row = run_self_refine_length_controlled(topic, args.max_tokens, args.min_chars, args.max_chars)
                elif system == "flowernet_full":
                    row = run_flowernet_variant(
                        topic=topic,
                        variant="flowernet_full",
                        max_tokens=args.max_tokens,
                        max_attempts=args.flowernet_max_attempts,
                        rel_threshold=0.765,
                        red_threshold=0.265,
                    )
                    row["fair_eval_note"] = f"FlowerNet full rerun under comparable max_tokens={args.max_tokens}; target length assessed post hoc."
                else:
                    raise ValueError(f"unknown system: {system}")
            except Exception as exc:
                row = {
                    "system": system,
                    "topic_id": topic.get("id"),
                    "topic": topic.get("topic"),
                    "status": "failed",
                    "elapsed_seconds": 0,
                    "final_text": "",
                    "error": str(exc),
                    "llm_calls": 0,
                    "controller_calls": 0,
                    "verified_subsections": 0,
                    "forced_pass_subsections": 0,
                    "metrics": text_metrics(""),
                }
            row["created_at"] = now_iso()
            row["length_control_target"] = {"min_chars": args.min_chars, "max_chars": args.max_chars}
            rows.append(row)
            write_json(out_path, rows)

    write_json(out_path, rows)
    print(f"wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
