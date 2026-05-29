"""Lightweight trained model helpers for FlowerNet runtime.

The runtime intentionally uses JSON-only linear models. They are cheap to load
in every microservice and avoid adding training-time dependencies to Render.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, Iterable, List, Optional


CONTROLLER_ARMS = [
    "llm",
    "rule",
    "rule_structured",
    "defect_topic",
    "defect_evidence",
    "defect_structure",
]

QUALITY_DIMENSION_KEYS = [
    "topic_alignment",
    "novelty",
    "evidence_grounding",
    "logical_coherence",
    "coverage_completeness",
    "structure_clarity",
]


def project_root(*parts: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), *parts)


def resolve_model_path(raw: str, default_name: str) -> str:
    value = (raw or "").strip() or os.path.join("models", default_name)
    if os.path.isabs(value):
        return value
    cwd_candidate = os.path.abspath(value)
    if os.path.exists(cwd_candidate):
        return cwd_candidate
    return project_root(value)


def clip01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


def sigmoid(value: float) -> float:
    if value >= 30:
        return 1.0
    if value <= -30:
        return 0.0
    return 1.0 / (1.0 + math.exp(-value))


def dot(weights: Iterable[Any], features: Iterable[Any]) -> float:
    total = 0.0
    for w, x in zip(weights, features):
        try:
            total += float(w) * float(x)
        except Exception:
            continue
    return total


def load_json_model(path: str, expected_kind: str) -> Optional[Dict[str, Any]]:
    try:
        if not path or not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or data.get("kind") != expected_kind:
            return None
        return data
    except Exception:
        return None


def predict_linear_model(model: Dict[str, Any], features: List[float], *, logistic: bool = False) -> float:
    weights = model.get("weights") if isinstance(model.get("weights"), list) else []
    bias = float(model.get("bias", 0.0) or 0.0)
    raw = bias + dot(weights, features)
    return clip01(sigmoid(raw) if logistic else raw)


def build_reward_features(verification: Dict[str, Any], iteration: int = 1) -> List[float]:
    """Build stable verifier/reward features from a verifier_result payload."""
    verification = verification if isinstance(verification, dict) else {}
    dims = verification.get("quality_dimensions") if isinstance(verification.get("quality_dimensions"), dict) else {}
    source_check = verification.get("source_check") if isinstance(verification.get("source_check"), dict) else {}
    rel = clip01(verification.get("relevancy_index", 0.0))
    red = clip01(verification.get("redundancy_index", 1.0))
    quality = clip01(verification.get("quality_score", 0.0))
    rel_threshold = clip01(verification.get("rel_threshold", 0.7))
    red_threshold = clip01(verification.get("red_threshold", 0.45))
    quality_threshold = clip01(verification.get("quality_threshold", 0.6))
    source_refs = min(1.0, float(source_check.get("reference_count", 0) or 0) / 5.0)
    return [
        rel,
        1.0 - red,
        quality,
        clip01(rel - rel_threshold + 0.5),
        clip01(red_threshold - red + 0.5),
        clip01(quality - quality_threshold + 0.5),
        1.0 if source_check.get("passed") else 0.0,
        source_refs,
        clip01(float(iteration or 1) / 8.0),
        *[clip01(dims.get(key, 0.0)) for key in QUALITY_DIMENSION_KEYS],
    ]


def predict_reward_model(model: Optional[Dict[str, Any]], verification: Dict[str, Any], iteration: int = 1) -> Dict[str, Any]:
    if not model:
        return {"used": False, "score": 0.0, "features": []}
    features = build_reward_features(verification, iteration=iteration)
    expected_dim = int(model.get("feature_dim", 0) or 0)
    if expected_dim and expected_dim != len(features):
        return {"used": False, "score": 0.0, "features": features, "reason": "feature_dim_mismatch"}
    score = predict_linear_model(model, features, logistic=True)
    return {
        "used": True,
        "score": round(score, 4),
        "features": [round(x, 4) for x in features],
        "model_version": model.get("version", ""),
    }


def predict_controller_arm_prior(policy: Optional[Dict[str, Any]], arm: str, features: List[float]) -> Optional[float]:
    if not policy:
        return None
    arms = policy.get("arms") if isinstance(policy.get("arms"), dict) else {}
    arm_model = arms.get(arm) if isinstance(arms.get(arm), dict) else None
    if not arm_model:
        return None
    expected_dim = int(policy.get("feature_dim", 0) or 0)
    if expected_dim and expected_dim != len(features):
        return None
    return predict_linear_model(arm_model, features, logistic=False)
