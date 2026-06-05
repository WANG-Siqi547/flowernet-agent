#!/usr/bin/env python3
"""Run epistemic FlowerNet baselines on the same 2x2 long-document task.

The comparison is intentionally small but real: it contrasts a one-shot LLM,
Self-Refine, and the current FlowerNet self-audit output with the same topic
and the same 2 chapter x 2 subsection target.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

THIS_FILE = Path(__file__).resolve()
ROOT = THIS_FILE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "baselines") not in sys.path:
    sys.path.insert(0, str(ROOT / "baselines"))

from baselines.common import get_flowernet_generator, load_dotenv, text_metrics, write_json


DEFAULT_TOPIC = "AI智能体在科学发现中的可证伪性与风险审计框架"
DEFAULT_BACKGROUND = "AI researcher"
DEFAULT_REQUIREMENTS = (
    "请生成中文研究报告风格，必须包含可证伪性、金融风险审计、机器人学闭环控制、"
    "反例意识、证据线索和多智能体审稿压力。结构为2章，每章2小节。"
)


def _extract_text(result: Dict[str, Any]) -> str:
    return str(result.get("draft") or result.get("content") or result.get("text") or "").strip()


def _safe_json_load(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def build_vanilla_prompt(topic: str, background: str, requirements: str) -> str:
    return (
        f"主题：{topic}\n"
        f"读者背景：{background}\n"
        f"要求：{requirements}\n\n"
        "请一次性生成完整长文档。必须包含摘要、2个一级章节、每章2个二级小节、结论、参考线索。"
        "不要输出过程解释，不要使用占位符。"
    )


def build_self_refine_prompts(topic: str, background: str, requirements: str, draft: str = "", critique: str = "") -> Dict[str, str]:
    base = f"主题：{topic}\n读者背景：{background}\n要求：{requirements}"
    return {
        "draft": (
            f"{base}\n\n请生成完整中文研究报告。结构必须为2章、每章2小节；包含可证伪性、风险审计、证据线索和结论。"
        ),
        "critique": (
            "你是严格的科学写作审稿人。只提出可执行修改意见，覆盖事实证据、可证伪性、结构完整、"
            "风险暴露、逻辑连贯、引用漂移和冗余。\n\n"
            f"{base}\n\n初稿：\n{draft}"
        ),
        "rewrite": (
            "请根据审稿意见重写最终稿。只输出最终文档，不输出审稿过程；不要写死模板兜底内容。\n\n"
            f"{base}\n\n初稿：\n{draft}\n\n审稿意见：\n{critique}"
        ),
    }


def epistemic_text_features(text: str) -> Dict[str, Any]:
    text = text or ""
    lower = text.lower()
    patterns = {
        "falsifiability_hits": r"可证伪|证伪|falsifi",
        "risk_hits": r"风险|risk|drawdown|portfolio|不确定性",
        "evidence_hits": r"证据|引用|参考|source|evidence|\[\d+\]",
        "review_hits": r"审稿|reviewer|peer review|质疑|反例",
        "control_hits": r"闭环|controller|控制|反馈|修正|迭代",
        "ledger_hits": r"ledger|账本|audit|审计|Self-Audit Ledger",
    }
    features = {name: len(re.findall(pattern, text, flags=re.I)) for name, pattern in patterns.items()}
    features["has_self_audit_ledger"] = "Self-Audit Ledger" in text
    features["chapter_audit_table_count"] = text.count("Chapter epistemic audit") + text.count("Chapter Audit Table")
    features["reference_count"] = len(re.findall(r"^\[\d+\]\s+.+", text, flags=re.M))
    return features


def quality_score(row: Dict[str, Any]) -> float:
    metrics = row.get("metrics", {})
    feats = row.get("epistemic_features", {})
    if row.get("status") != "ok":
        return 0.0
    length = min(1.0, metrics.get("chars", 0) / 12000)
    structure = min(1.0, (metrics.get("heading_count", 0) + metrics.get("paragraphs", 0) / 5) / 12)
    evidence = min(1.0, (feats.get("evidence_hits", 0) + feats.get("reference_count", 0) * 2) / 18)
    audit = min(
        1.0,
        (
            feats.get("falsifiability_hits", 0)
            + feats.get("risk_hits", 0)
            + feats.get("review_hits", 0)
            + feats.get("control_hits", 0)
            + feats.get("ledger_hits", 0)
        )
        / 28,
    )
    table = min(1.0, metrics.get("table_marker_count", 0) / 40)
    repeat_penalty = min(0.25, metrics.get("repeat_3gram_ratio", 0))
    return round(max(0.0, 0.25 * length + 0.22 * structure + 0.22 * evidence + 0.24 * audit + 0.07 * table - repeat_penalty), 3)


def make_row(name: str, text: str, elapsed: float, status: str = "ok", notes: str = "") -> Dict[str, Any]:
    row = {
        "baseline": name,
        "status": status if text else "failed",
        "elapsed_seconds": round(elapsed, 2),
        "final_text": text,
        "metrics": text_metrics(text),
        "epistemic_features": epistemic_text_features(text),
        "notes": notes,
    }
    row["epistemic_quality_score"] = quality_score(row)
    return row


def run_llm_baselines(args: argparse.Namespace) -> List[Dict[str, Any]]:
    load_dotenv()
    generator = get_flowernet_generator()
    rows: List[Dict[str, Any]] = []

    started = time.time()
    vanilla_raw = generator.generate_draft(
        build_vanilla_prompt(args.topic, args.background, args.requirements),
        max_tokens=args.max_tokens,
        allow_compact_fallback=False,
    )
    rows.append(
        make_row(
            "vanilla_llm",
            _extract_text(vanilla_raw),
            time.time() - started,
            "ok" if vanilla_raw.get("success") else "failed",
            str(vanilla_raw.get("error", "")),
        )
    )

    started = time.time()
    prompts = build_self_refine_prompts(args.topic, args.background, args.requirements)
    draft_raw = generator.generate_draft(prompts["draft"], max_tokens=args.max_tokens, allow_compact_fallback=False)
    draft = _extract_text(draft_raw)
    critique_prompt = build_self_refine_prompts(args.topic, args.background, args.requirements, draft=draft)["critique"]
    critique_raw = generator.generate_draft(
        critique_prompt,
        max_tokens=max(700, args.max_tokens // 3),
        allow_compact_fallback=False,
    )
    critique = _extract_text(critique_raw)
    rewrite_prompt = build_self_refine_prompts(args.topic, args.background, args.requirements, draft=draft, critique=critique)["rewrite"]
    rewrite_raw = generator.generate_draft(rewrite_prompt, max_tokens=args.max_tokens, allow_compact_fallback=False)
    rows.append(
        make_row(
            "self_refine",
            _extract_text(rewrite_raw),
            time.time() - started,
            "ok" if rewrite_raw.get("success") else "failed",
            " | ".join(str(x.get("error", "")) for x in [draft_raw, critique_raw, rewrite_raw] if x.get("error")),
        )
    )
    return rows


def load_flowernet_row(path: Path) -> Dict[str, Any]:
    payload = _safe_json_load(path)
    if not payload:
        return make_row("flowernet_self_audit", "", 0.0, "missing", f"missing {path}")
    response = payload.get("response", payload)
    summary = payload.get("summary", {})
    text = str(response.get("content") or response.get("document") or response.get("markdown") or "")
    row = make_row(
        "flowernet_self_audit",
        text,
        float(summary.get("elapsed_seconds", response.get("elapsed_seconds", 0)) or 0),
        "ok" if response.get("success") else "failed",
        str(summary.get("error") or response.get("error") or response.get("detail") or ""),
    )
    row["flowernet_stats"] = response.get("stats", {})
    return row


def markdown_report(rows: List[Dict[str, Any]]) -> str:
    lines = [
        "# FlowerNet Epistemic Baseline Comparison",
        "",
        "同一任务：2章 x 2小节，主题为 AI 智能体在科学发现中的可证伪性与风险审计框架。",
        "",
        "| Baseline | Status | Score | Chars | Headings | Tables | Citations | Audit hits | Time(s) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in sorted(rows, key=lambda r: r.get("epistemic_quality_score", 0), reverse=True):
        metrics = row.get("metrics", {})
        feats = row.get("epistemic_features", {})
        audit_hits = sum(
            int(feats.get(k, 0))
            for k in ["falsifiability_hits", "risk_hits", "review_hits", "control_hits", "ledger_hits"]
        )
        lines.append(
            f"| {row['baseline']} | {row['status']} | {row.get('epistemic_quality_score', 0)} | "
            f"{metrics.get('chars', 0)} | {metrics.get('heading_count', 0)} | {metrics.get('table_marker_count', 0)} | "
            f"{feats.get('reference_count', 0)} | {audit_hits} | {row.get('elapsed_seconds', 0)} |"
        )
    winner = max(rows, key=lambda r: r.get("epistemic_quality_score", 0)) if rows else {}
    lines.extend(
        [
            "",
            "## 结论",
            "",
            f"当前最高分 baseline 是 `{winner.get('baseline', '-')}`，score={winner.get('epistemic_quality_score', 0)}。",
            "这个分数是轻量可复现实验指标，重点衡量长文档完整度、结构、证据线索、自审计信号、表格/账本和重复惩罚；它不是论文最终指标。",
            "",
            "## 指标说明",
            "",
            "- `Audit hits`: 可证伪性、风险、审稿、控制、账本相关信号的出现次数。",
            "- `Citations`: Markdown References 中的编号参考条目数量。",
            "- `Tables`: 表格分隔符数量，FlowerNet 的章级审计表和 Self-Audit Ledger 会提高该项。",
            "- `Score`: length + structure + evidence + epistemic audit + table - repetition penalty。",
            "",
            "## 产物",
            "",
            "- JSON: `results/epistemic_baseline/baseline_comparison.json`",
            "- Report: `reports/epistemic_baseline/baseline_comparison.md`",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--background", default=DEFAULT_BACKGROUND)
    parser.add_argument("--requirements", default=DEFAULT_REQUIREMENTS)
    parser.add_argument("--max-tokens", type=int, default=2600)
    parser.add_argument("--flowernet-result", default="results/epistemic_smoke_2x2.json")
    parser.add_argument("--skip-llm", action="store_true", help="Only score existing FlowerNet output.")
    parser.add_argument("--output", default="results/epistemic_baseline/baseline_comparison.json")
    parser.add_argument("--report", default="reports/epistemic_baseline/baseline_comparison.md")
    args = parser.parse_args()

    rows: List[Dict[str, Any]] = []
    if not args.skip_llm:
        rows.extend(run_llm_baselines(args))
    rows.append(load_flowernet_row(ROOT / args.flowernet_result))

    output_payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "topic": args.topic,
        "background": args.background,
        "requirements": args.requirements,
        "rows": rows,
    }
    write_json(ROOT / args.output, output_payload)

    report_path = ROOT / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(markdown_report(rows), encoding="utf-8")
    print(f"wrote {ROOT / args.output}")
    print(f"wrote {report_path}")


if __name__ == "__main__":
    main()
