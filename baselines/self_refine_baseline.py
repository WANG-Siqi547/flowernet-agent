#!/usr/bin/env python3
"""Self-Refine baseline: draft, critique, then rewrite with no structured controller."""

from __future__ import annotations

import argparse
import time
from typing import Any, Dict, List

from common import extract_text_from_result, get_flowernet_generator, load_topics, now_iso, text_metrics, write_json


def build_prompts(topic: Dict[str, Any], draft: str = "", critique: str = "") -> Dict[str, str]:
    user_request = topic.get("prompt") or topic.get("topic") or ""
    return {
        "draft": (
            f"{user_request}\n\n"
            "请生成一篇完整、可信、结构化的长篇研究报告。结构必须是 2 个一级章节，每个一级章节包含 2 个二级小节。"
            "要求包含摘要、目录式章节、核心论点、"
            "可验证事实线索、局限、结论。不要使用模板化占位符。"
        ),
        "critique": (
            "你是严格的长文档审稿人。请只针对下面初稿提出可执行修改意见，覆盖：主题覆盖、结构完整性、"
            "事实与证据、冗余、逻辑连贯、表格/对比是否必要。不要重写全文。\n\n"
            f"用户要求：\n{user_request}\n\n初稿：\n{draft}"
        ),
        "rewrite": (
            "请根据审稿意见重写为最终长篇研究报告。必须保留真实内容，不要输出审稿过程；"
            "优先修复覆盖不足、结构断裂、引用漂移和重复问题。\n\n"
            f"用户要求：\n{user_request}\n\n初稿：\n{draft}\n\n审稿意见：\n{critique}"
        ),
    }


def run(args: argparse.Namespace) -> List[Dict[str, Any]]:
    topics = load_topics(args.topics, limit=args.limit, topic_id=args.topic_id)
    generator = None if args.dry_run else get_flowernet_generator()
    outputs: List[Dict[str, Any]] = []
    for topic in topics:
        started = time.time()
        prompts = build_prompts(topic)
        stage_results: Dict[str, Any] = {}
        if args.dry_run:
            draft = f"# {topic['topic']}\n\n[DRY RUN] Self-Refine draft prepared."
            critique = "DRY RUN critique prepared."
            final_text = draft + "\n\n## Refined\n\nDRY RUN rewrite prepared."
            stage_results = {"draft": {}, "critique": {}, "rewrite": {}}
        else:
            draft_raw = generator.generate_draft(prompts["draft"], max_tokens=args.max_tokens, allow_compact_fallback=False)
            draft = extract_text_from_result(draft_raw)
            critique_prompt = build_prompts(topic, draft=draft)["critique"]
            critique_raw = generator.generate_draft(critique_prompt, max_tokens=max(600, args.max_tokens // 3), allow_compact_fallback=False)
            critique = extract_text_from_result(critique_raw)
            rewrite_prompt = build_prompts(topic, draft=draft, critique=critique)["rewrite"]
            rewrite_raw = generator.generate_draft(rewrite_prompt, max_tokens=args.max_tokens, allow_compact_fallback=False)
            final_text = extract_text_from_result(rewrite_raw)
            stage_results = {
                "draft": draft_raw,
                "critique": critique_raw,
                "rewrite": rewrite_raw,
            }
        elapsed = time.time() - started
        ok = bool(final_text.strip())
        outputs.append(
            {
                "baseline": "self_refine",
                "topic_id": topic.get("id"),
                "topic": topic.get("topic"),
                "status": "ok" if ok else "failed",
                "created_at": now_iso(),
                "elapsed_seconds": round(elapsed, 2),
                "rounds": [
                    {"name": "draft", "text": draft, "metrics": text_metrics(draft)},
                    {"name": "critique", "text": critique, "metrics": text_metrics(critique)},
                    {"name": "rewrite", "text": final_text, "metrics": text_metrics(final_text)},
                ],
                "final_text": final_text,
                "raw_metadata": {k: v.get("metadata", {}) for k, v in stage_results.items() if isinstance(v, dict)},
                "error": " | ".join(str(v.get("error", "")) for v in stage_results.values() if isinstance(v, dict) and v.get("error")),
                "metrics": text_metrics(final_text),
            }
        )
    write_json(args.output, outputs)
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topics", default="experiments/topics_week1.json")
    parser.add_argument("--output", default="results/week1/self_refine_outputs.json")
    parser.add_argument("--topic-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-tokens", type=int, default=2400)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    outputs = run(args)
    print(f"wrote {len(outputs)} self-refine outputs to {args.output}")


if __name__ == "__main__":
    main()
