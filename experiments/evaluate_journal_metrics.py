#!/usr/bin/env python3
"""Journal-style external metric scaffold for FlowerNet experiments.

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
from pathlib import Path
from typing import Any, Dict, List

from rouge_score import rouge_scorer


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def row_text(row: Dict[str, Any]) -> str:
    for key in ("text", "output", "document", "content"):
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", required=True)
    parser.add_argument("--references", required=True)
    parser.add_argument("--out", default="results/week2/journal_metrics.json")
    args = parser.parse_args()

    outputs = load_json(Path(args.outputs))
    refs = load_json(Path(args.references))
    rows = []
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
        rows.append(
            {
                "system": row.get("system"),
                "topic_id": topic_id,
                "rouge": scores,
                "reference_source_note": ref_pack.get("source_note", ""),
                "candidate_chars": len(text),
            }
        )

    out = {
        "status": "ok" if rows else "no_reference_scored",
        "metric_note": "ROUGE is computed against explicit external references. BERTScore should be added after installing bert-score and locking model/language settings.",
        "scored_rows": rows,
        "missing_reference_topic_ids": sorted(set(missing)),
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    main()
