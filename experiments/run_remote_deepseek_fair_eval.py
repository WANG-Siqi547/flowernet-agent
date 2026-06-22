#!/usr/bin/env python3
"""Remote DeepSeek-only fair evaluation.

Baselines call the deployed FlowerNet generator service, which is configured
as DeepSeek-only. FlowerNet full calls the deployed web service, which runs the
full outliner/generator/verifier/controller document pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "baselines") not in sys.path:
    sys.path.insert(0, str(ROOT / "baselines"))

from common import extract_text_from_result, load_topics, now_iso, text_metrics, write_json  # type: ignore


GENERATOR_URL = "https://flowernet-generator.onrender.com"
WEB_URL = "https://flowernet-web.onrender.com"


def post_json(url: str, payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    session = requests.Session()
    session.trust_env = False
    response = session.post(url, json=payload, timeout=timeout)
    try:
        data = response.json()
    except Exception:
        data = {"success": False, "error": response.text}
    if response.status_code >= 400:
        data.setdefault("success", False)
        data.setdefault("error", f"HTTP {response.status_code}: {response.text[:300]}")
    return data


def remote_generate(prompt: str, max_tokens: int, timeout: int = 300) -> Dict[str, Any]:
    return post_json(f"{GENERATOR_URL}/generate", {"prompt": prompt, "max_tokens": max_tokens}, timeout=timeout)


def target_instruction(min_chars: int, max_chars: int) -> str:
    return (
        f"\n\nFair length target: write {min_chars}-{max_chars} Chinese characters or equivalent long-form English length. "
        "Do not pad with repetition. Expand only through topic-specific coverage, evidence, examples, evaluation metrics, risks, and future directions. "
        "Use exactly 2 top-level sections and 2 subsections per section."
    )


def run_vanilla(topic: Dict[str, Any], max_tokens: int, min_chars: int, max_chars: int) -> Dict[str, Any]:
    prompt = (
        f"{topic.get('prompt') or topic.get('topic')}\n\n"
        "Generate a complete academic document in one pass. Include title, abstract, sections, subsections, citations, one compact table, limitations, and references."
        + target_instruction(min_chars, max_chars)
    )
    started = time.time()
    raw = remote_generate(prompt, max_tokens=max_tokens)
    text = extract_text_from_result(raw)
    return row("vanilla_length_controlled_remote", topic, text, raw, started, llm_calls=1)


def run_self_refine(topic: Dict[str, Any], max_tokens: int, min_chars: int, max_chars: int) -> Dict[str, Any]:
    base = topic.get("prompt") or topic.get("topic") or ""
    started = time.time()
    draft_raw = remote_generate(
        f"{base}\n\nWrite a complete first draft." + target_instruction(min_chars, max_chars),
        max_tokens=max_tokens,
    )
    draft = extract_text_from_result(draft_raw)
    critique_raw = remote_generate(
        "Critique the draft for topic coverage, evidence grounding, structure, redundancy, missing references, and length control. "
        "Return concise actionable issues only.\n\n"
        f"Topic: {base}\n\nDraft:\n{draft[:12000]}",
        max_tokens=max(700, max_tokens // 3),
    )
    critique = extract_text_from_result(critique_raw)
    rewrite_raw = remote_generate(
        "Rewrite the draft using the critique. Preserve useful content, remove repetition, and expand with topic-specific evidence-grounded analysis.\n\n"
        f"Topic: {base}\n\nCritique:\n{critique}\n\nDraft:\n{draft[:12000]}"
        + target_instruction(min_chars, max_chars),
        max_tokens=max_tokens,
    )
    text = extract_text_from_result(rewrite_raw)
    errors = " | ".join(str(x.get("error", "")) for x in [draft_raw, critique_raw, rewrite_raw] if isinstance(x, dict) and x.get("error"))
    raw = {"success": bool(text), "error": errors}
    return row("self_refine_length_controlled_remote", topic, text, raw, started, llm_calls=3)


def run_flowernet_full(topic: Dict[str, Any], timeout: int = 1800) -> Dict[str, Any]:
    started = time.time()
    topic_text = topic.get("topic") or topic.get("prompt") or ""
    payload = {
        "topic": topic_text,
        "query": topic_text,
        "chapter_count": 2,
        "subsection_count": 2,
        "user_background": "",
        "extra_requirements": (
            str(topic.get("prompt") or "")
            + "\n\nFair evaluation: produce a complete long-form academic document with topic-specific coverage, evidence grounding, citations, one compact table, limitations, and references. Target total length 4500-5000 Chinese characters or equivalent English length."
        ),
        "timeout_seconds": timeout,
    }
    raw = post_json(f"{WEB_URL}/api/generate", payload, timeout=timeout + 60)
    text = (
        str(raw.get("markdown") or raw.get("content") or raw.get("document") or raw.get("text") or "")
        or str((raw.get("result") or {}).get("markdown") if isinstance(raw.get("result"), dict) else "")
    ).strip()
    if not text and isinstance(raw.get("result"), dict):
        result = raw["result"]
        text = str(result.get("content") or result.get("document") or result.get("text") or "").strip()
    out = row("flowernet_full_remote", topic, text, raw, started, llm_calls=int(raw.get("llm_calls", 0) or 0))
    out["remote_metadata"] = {
        "document_id": raw.get("document_id"),
        "title": raw.get("title") or raw.get("document_title"),
        "passed_subsections": raw.get("passed_subsections"),
        "rag_used_subsections": raw.get("rag_used_subsections"),
        "controller_effective_subsections": raw.get("controller_effective_subsections"),
    }
    return out


def row(system: str, topic: Dict[str, Any], text: str, raw: Dict[str, Any], started: float, llm_calls: int) -> Dict[str, Any]:
    return {
        "system": system,
        "topic_id": topic.get("id"),
        "topic": topic.get("topic"),
        "status": "ok" if text and raw.get("success", bool(text)) else "failed",
        "elapsed_seconds": round(time.time() - started, 2),
        "final_text": text,
        "error": raw.get("error", "") if isinstance(raw, dict) else "",
        "llm_calls": llm_calls,
        "controller_calls": int(raw.get("controller_calls_total", 0) or raw.get("controller_calls", 0) or 0) if isinstance(raw, dict) else 0,
        "verified_subsections": int(raw.get("passed_subsections", 0) or 0) if isinstance(raw, dict) else 0,
        "forced_pass_subsections": int(raw.get("forced_subsections", 0) or 0) if isinstance(raw, dict) and isinstance(raw.get("forced_subsections"), int) else 0,
        "metrics": text_metrics(text),
        "created_at": now_iso(),
        "fair_eval_note": "Remote DeepSeek-only fair evaluation.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topics", default="experiments/topics_week1.json")
    parser.add_argument("--output", default="results/week2/fair_eval/remote_deepseek_fair_outputs.json")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--topic-id")
    parser.add_argument("--systems", default="vanilla,self_refine,flowernet_full")
    parser.add_argument("--min-chars", type=int, default=4500)
    parser.add_argument("--max-chars", type=int, default=5000)
    parser.add_argument("--max-tokens", type=int, default=2400)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    topics = load_topics(args.topics, limit=args.limit, topic_id=args.topic_id)
    systems = [s.strip() for s in args.systems.split(",") if s.strip()]
    out_path = ROOT / args.output
    rows: List[Dict[str, Any]] = []
    done = set()
    if args.resume and out_path.exists():
        rows = json.loads(out_path.read_text(encoding="utf-8"))
        done = {(r.get("system"), r.get("topic_id")) for r in rows}

    for topic in topics:
        for system in systems:
            key_name = {
                "vanilla": "vanilla_length_controlled_remote",
                "self_refine": "self_refine_length_controlled_remote",
                "flowernet_full": "flowernet_full_remote",
            }.get(system, system)
            if (key_name, topic.get("id")) in done:
                continue
            print(f"[remote-fair] {system} :: {topic.get('id')} {topic.get('topic')}", flush=True)
            try:
                if system == "vanilla":
                    result = run_vanilla(topic, args.max_tokens, args.min_chars, args.max_chars)
                elif system == "self_refine":
                    result = run_self_refine(topic, args.max_tokens, args.min_chars, args.max_chars)
                elif system == "flowernet_full":
                    result = run_flowernet_full(topic)
                else:
                    raise ValueError(f"unknown system {system}")
            except Exception as exc:
                result = row(key_name, topic, "", {"success": False, "error": str(exc)}, time.time(), 0)
            result["length_control_target"] = {"min_chars": args.min_chars, "max_chars": args.max_chars}
            rows.append(result)
            write_json(out_path, rows)

    write_json(out_path, rows)
    print(f"wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
