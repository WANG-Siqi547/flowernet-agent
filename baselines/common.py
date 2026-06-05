#!/usr/bin/env python3
"""Shared helpers for FlowerNet week-1 baselines."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List


ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def load_topics(path: str | Path, limit: int | None = None, topic_id: str | None = None) -> List[Dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    topics = data.get("topics", data if isinstance(data, list) else [])
    if topic_id:
        topics = [t for t in topics if t.get("id") == topic_id]
    if limit is not None:
        topics = topics[:limit]
    return topics


def write_json(path: str | Path, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def text_metrics(text: str) -> Dict[str, Any]:
    text = text or ""
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_words = len(re.findall(r"\b[A-Za-z][A-Za-z0-9_-]*\b", text))
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    sentences = [s.strip() for s in re.split(r"(?<=[。！？.!?])\s*", text) if s.strip()]
    grams = []
    # Repetition should measure prose repetition, not Markdown table syntax.
    # FlowerNet intentionally emits audit/evidence tables; counting repeated
    # table separators as textual 3-grams unfairly penalizes useful structure.
    prose_for_repeat = re.sub(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?\s*$", " ", text, flags=re.M)
    prose_for_repeat = re.sub(r"^\s*\|.*\|\s*$", " ", prose_for_repeat, flags=re.M)
    prose_for_repeat = prose_for_repeat.replace("|", " ")
    tokens = re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+", prose_for_repeat.lower())
    for i in range(max(0, len(tokens) - 2)):
        grams.append(tuple(tokens[i : i + 3]))
    repeat_ratio = 0.0
    if grams:
        repeat_ratio = 1.0 - (len(set(grams)) / len(grams))
    heading_count = len(re.findall(r"(^|\n)\s{0,3}#{1,4}\s+", text))
    table_markers = text.count("|") + len(re.findall(r"<table\b|</table>", text, flags=re.I))
    citation_markers = len(re.findall(r"\[[0-9,\s-]+\]|\((?:19|20)\d{2}\)|https?://", text))
    return {
        "chars": len(text),
        "chinese_chars": chinese_chars,
        "latin_words": latin_words,
        "paragraphs": len(paragraphs),
        "sentences": len(sentences),
        "heading_count": heading_count,
        "table_marker_count": table_markers,
        "citation_marker_count": citation_markers,
        "repeat_3gram_ratio": round(repeat_ratio, 4),
    }


def get_flowernet_generator():
    load_dotenv()
    generator_dir = ROOT / "flowernet-generator"
    if str(generator_dir) not in sys.path:
        sys.path.insert(0, str(generator_dir))
    from generator import FlowerNetGenerator  # type: ignore

    return FlowerNetGenerator()


def baseline_prompt(topic: Dict[str, Any], *, style: str = "vanilla") -> str:
    base = topic.get("prompt") or topic.get("topic") or ""
    if style == "vanilla":
        return (
            f"{base}\n\n"
            "结构要求：生成 2 个一级章节，每个一级章节必须包含 2 个二级小节；"
            "要求：直接一次性生成完整长文档；包含清晰标题、摘要、分节结构、关键论点、局限与结论；"
            "尽量给出可验证的事实和引用线索；不要输出过程解释。"
        )
    return str(base)


def extract_text_from_result(result: Dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return ""
    return str(result.get("draft") or result.get("content") or result.get("text") or "").strip()
