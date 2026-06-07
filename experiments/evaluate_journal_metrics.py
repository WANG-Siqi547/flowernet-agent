#!/usr/bin/env python3
"""Journal-style external metrics for FlowerNet experiments.

This script is deliberately reference-driven. It refuses to pretend that
ROUGE/BERTScore are meaningful without external references.

Input:
  --outputs results/week2/week2_benchmark_outputs_full.json
  --references references/week2_reference_sets.json

Reference JSON schema:
{
  "fw24_001": {
    "reference_texts": ["...", "..."],
    "source_note": "Wikipedia pages: AI agent, Tool use ..."
  }
}
"""

from __future__ import annotations

import argparse
import json
import csv
from pathlib import Path
from typing import Any, Dict, List

from rouge_score import rouge_scorer


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def row_text(row: Dict[str, Any]) -> str:
    for key in ("final_text", "text", "output", "document", "content", "markdown"):
        val = row.get(key)
        if isinstance(val, str) and val.strip():
            return val
    parts: List[str] = []
    for sub in row.get("subsections") or []:
        if isinstance(sub, dict):
            for key in ("text", "content", "draft"):
                val = sub.get(key)
                if isinstance(val, str) and val.strip():
                    parts.append(val)
                    break
    return "\n\n".join(parts)


def best_rouge(candidate: str, refs: List[str]) -> Dict[str, float]:
    scorer = rouge_scorer.RougeScorer(["rouge1", "rouge2", "rougeL"], use_stemmer=True)
    best = {"rouge1": 0.0, "rouge2": 0.0, "rougeL": 0.0}
    for ref in refs:
        scores = scorer.score(ref, candidate)
        for key in best:
            best[key] = max(best[key], float(scores[key].fmeasure))
    return best


def trim_for_bertscore(text: str, max_chars: int) -> str:
    text = " ".join((text or "").split())
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    head = text[: max_chars // 2]
    tail = text[-max_chars // 2 :]
    return f"{head} ... {tail}"


def compute_bertscore_rows(
    candidates: List[str],
    references: List[str],
    model_type: str,
    batch_size: int,
) -> List[Dict[str, float]]:
    try:
        from bert_score import score as bert_score
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "BERTScore is not installed. Install with `python3 -m pip install bert-score`."
        ) from exc

    precision, recall, f1 = bert_score(
        candidates,
        references,
        model_type=model_type,
        batch_size=batch_size,
        verbose=False,
        rescale_with_baseline=False,
        device="cpu",
    )
    return [
        {
            "precision": float(p),
            "recall": float(r),
            "f1": float(f),
        }
        for p, r, f in zip(precision, recall, f1)
    ]


def mean(values: List[float]) -> float:
    values = [v for v in values if isinstance(v, (int, float))]
    return float(sum(values) / len(values)) if values else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", required=True)
    parser.add_argument("--references", required=True)
    parser.add_argument("--out", default="results/week2/journal_metrics.json")
    parser.add_argument("--csv-out", default="")
    parser.add_argument("--skip-bertscore", action="store_true")
    parser.add_argument("--bert-model", default="xlm-roberta-base")
    parser.add_argument("--bert-batch-size", type=int, default=4)
    parser.add_argument(
        "--max-bertscore-chars",
        type=int,
        default=6000,
        help="BERTScore is token-limited and CPU-heavy; trim long docs head+tail for a reproducible external proxy.",
    )
    args = parser.parse_args()

    outputs = load_json(Path(args.outputs))
    refs = load_json(Path(args.references))
    rows = []
    bert_candidates: List[str] = []
    bert_refs: List[str] = []
    bert_row_indices: List[int] = []
    missing = []
    for row in outputs:
        topic_id = str(row.get("topic_id", ""))
        ref_pack = refs.get(topic_id) or {}
        ref_texts = [x for x in ref_pack.get("reference_texts", []) if isinstance(x, str) and x.strip()]
        if not ref_texts:
            missing.append(topic_id)
            continue
        text = row_text(row)
        scores = best_rouge(text, ref_texts)
        reference_for_bert = "\n\n".join(ref_texts)
        rows.append(
            {
                "system": row.get("system"),
                "topic_id": topic_id,
                "topic": row.get("topic", ""),
                "status": row.get("status", ""),
                "rouge": scores,
                "bertscore": None,
                "reference_source_note": ref_pack.get("source_note", ""),
                "candidate_chars": len(text),
                "reference_chars": len(reference_for_bert),
            }
        )
        if text.strip() and reference_for_bert.strip() and not args.skip_bertscore:
            bert_row_indices.append(len(rows) - 1)
            bert_candidates.append(trim_for_bertscore(text, args.max_bertscore_chars))
            bert_refs.append(trim_for_bertscore(reference_for_bert, args.max_bertscore_chars))

    if bert_candidates and not args.skip_bertscore:
        bert_rows = compute_bertscore_rows(
            bert_candidates,
            bert_refs,
            model_type=args.bert_model,
            batch_size=args.bert_batch_size,
        )
        for row_idx, bert in zip(bert_row_indices, bert_rows):
            rows[row_idx]["bertscore"] = bert

    summary: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        system = str(row.get("system") or "unknown")
        bucket = summary.setdefault(system, {"n": 0, "rouge1": [], "rouge2": [], "rougeL": [], "bertscore_f1": []})
        bucket["n"] += 1
        for key in ("rouge1", "rouge2", "rougeL"):
            bucket[key].append(float((row.get("rouge") or {}).get(key, 0.0)))
        bert = row.get("bertscore") or {}
        if isinstance(bert, dict):
            bucket["bertscore_f1"].append(float(bert.get("f1", 0.0)))

    summary_rows = []
    for system, vals in summary.items():
        summary_rows.append(
            {
                "system": system,
                "n": vals["n"],
                "rouge1": mean(vals["rouge1"]),
                "rouge2": mean(vals["rouge2"]),
                "rougeL": mean(vals["rougeL"]),
                "bertscore_f1": mean(vals["bertscore_f1"]),
            }
        )
    summary_rows.sort(
        key=lambda item: (
            item.get("bertscore_f1", 0.0),
            item.get("rougeL", 0.0),
            item.get("rouge2", 0.0),
        ),
        reverse=True,
    )

    out = {
        "status": "ok" if rows else "no_reference_scored",
        "metric_note": (
            "ROUGE is computed against explicit external references. BERTScore uses a locked "
            f"{args.bert_model} encoder on CPU; long candidates/references are trimmed head+tail "
            f"to {args.max_bertscore_chars} chars for reproducibility."
        ),
        "bertscore_model": None if args.skip_bertscore else args.bert_model,
        "summary_by_system": summary_rows,
        "scored_rows": rows,
        "missing_reference_topic_ids": sorted(set(missing)),
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_out = Path(args.csv_out) if args.csv_out else out_path.with_suffix(".csv")
    with csv_out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "system",
                "topic_id",
                "status",
                "candidate_chars",
                "reference_chars",
                "rouge1",
                "rouge2",
                "rougeL",
                "bertscore_precision",
                "bertscore_recall",
                "bertscore_f1",
            ],
        )
        writer.writeheader()
        for row in rows:
            rouge = row.get("rouge") or {}
            bert = row.get("bertscore") or {}
            writer.writerow(
                {
                    "system": row.get("system"),
                    "topic_id": row.get("topic_id"),
                    "status": row.get("status"),
                    "candidate_chars": row.get("candidate_chars"),
                    "reference_chars": row.get("reference_chars"),
                    "rouge1": rouge.get("rouge1", 0.0),
                    "rouge2": rouge.get("rouge2", 0.0),
                    "rougeL": rouge.get("rougeL", 0.0),
                    "bertscore_precision": bert.get("precision", ""),
                    "bertscore_recall": bert.get("recall", ""),
                    "bertscore_f1": bert.get("f1", ""),
                }
            )
    print(out_path)


if __name__ == "__main__":
    main()
