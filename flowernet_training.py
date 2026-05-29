#!/usr/bin/env python3
"""Train lightweight FlowerNet controller and verifier/reward models.

The produced models are JSON files consumed by the Controller and Generator at
runtime. This makes the training loop useful in production without introducing
heavy ML dependencies into the services.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sqlite3
import time
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from flowernet_trained_models import (
    CONTROLLER_ARMS,
    QUALITY_DIMENSION_KEYS,
    build_reward_features,
    clip01,
    project_root,
    sigmoid,
)


def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def _linear_predict(weights: Sequence[float], bias: float, features: Sequence[float]) -> float:
    return float(bias) + sum(float(w) * float(x) for w, x in zip(weights, features))


def _fit_linear_regression_sgd(
    rows: Sequence[Tuple[List[float], float]],
    *,
    epochs: int = 240,
    lr: float = 0.035,
    l2: float = 0.001,
) -> Dict[str, Any]:
    if not rows:
        return {"weights": [], "bias": 0.0, "mse": 0.0, "n": 0}
    dim = len(rows[0][0])
    weights = [0.0] * dim
    bias = sum(y for _, y in rows) / max(1, len(rows))
    train_rows = list(rows)
    for _ in range(max(1, epochs)):
        random.shuffle(train_rows)
        for features, target in train_rows:
            pred = _linear_predict(weights, bias, features)
            err = pred - float(target)
            bias -= lr * err
            for idx, value in enumerate(features):
                weights[idx] -= lr * (err * float(value) + l2 * weights[idx])
    mse = sum((_linear_predict(weights, bias, x) - y) ** 2 for x, y in rows) / max(1, len(rows))
    return {
        "weights": [round(w, 6) for w in weights],
        "bias": round(bias, 6),
        "mse": round(mse, 6),
        "n": len(rows),
    }


def _fit_logistic_sgd(
    rows: Sequence[Tuple[List[float], float]],
    *,
    epochs: int = 320,
    lr: float = 0.04,
    l2: float = 0.001,
) -> Dict[str, Any]:
    if not rows:
        return {"weights": [], "bias": 0.0, "log_loss": 0.0, "accuracy": 0.0, "n": 0}
    dim = len(rows[0][0])
    positive_rate = sum(y for _, y in rows) / max(1, len(rows))
    positive_rate = min(0.98, max(0.02, positive_rate))
    bias = math.log(positive_rate / (1.0 - positive_rate))
    weights = [0.0] * dim
    train_rows = list(rows)
    for _ in range(max(1, epochs)):
        random.shuffle(train_rows)
        for features, target in train_rows:
            pred = sigmoid(_linear_predict(weights, bias, features))
            err = pred - float(target)
            bias -= lr * err
            for idx, value in enumerate(features):
                weights[idx] -= lr * (err * float(value) + l2 * weights[idx])
    losses: List[float] = []
    correct = 0
    for features, target in rows:
        pred = min(0.999999, max(0.000001, sigmoid(_linear_predict(weights, bias, features))))
        losses.append(-(target * math.log(pred) + (1.0 - target) * math.log(1.0 - pred)))
        correct += int((pred >= 0.5) == bool(target >= 0.5))
    return {
        "weights": [round(w, 6) for w in weights],
        "bias": round(bias, 6),
        "log_loss": round(sum(losses) / max(1, len(losses)), 6),
        "accuracy": round(correct / max(1, len(rows)), 4),
        "n": len(rows),
    }


def train_controller_policy(events_path: str, output_path: str) -> Dict[str, Any]:
    events = []
    for obj in _read_jsonl(events_path):
        arm = str(obj.get("chosen_arm") or "")
        features = obj.get("feature_vector")
        if arm not in CONTROLLER_ARMS or not isinstance(features, list):
            continue
        try:
            x = [clip01(v) for v in features]
            reward = clip01(obj.get("reward", 0.0))
        except Exception:
            continue
        if len(x) < 3:
            continue
        events.append((arm, x, reward, bool(obj.get("effective", False))))

    if not events:
        raise RuntimeError(f"No usable controller events found in {events_path}")

    feature_dim = len(events[0][1])
    by_arm: Dict[str, List[Tuple[List[float], float]]] = defaultdict(list)
    counts = Counter()
    rewards = defaultdict(float)
    effective_counts = Counter()
    for arm, x, reward, effective in events:
        if len(x) != feature_dim:
            continue
        by_arm[arm].append((x, reward))
        counts[arm] += 1
        rewards[arm] += reward
        effective_counts[arm] += int(effective)

    arm_models: Dict[str, Any] = {}
    for arm in CONTROLLER_ARMS:
        rows = by_arm.get(arm, [])
        if len(rows) >= 3:
            fitted = _fit_linear_regression_sgd(rows)
        else:
            avg = rewards[arm] / max(1, counts[arm])
            fitted = {"weights": [0.0] * feature_dim, "bias": round(avg, 6), "mse": 0.0, "n": len(rows)}
        fitted["avg_reward"] = round(rewards[arm] / max(1, counts[arm]), 6)
        fitted["effective_rate"] = round(effective_counts[arm] / max(1, counts[arm]), 4)
        arm_models[arm] = fitted

    model = {
        "kind": "controller_policy",
        "version": f"controller_policy_{int(time.time())}",
        "created_at": int(time.time()),
        "feature_dim": feature_dim,
        "training_events": sum(counts.values()),
        "arm_counts": dict(counts),
        "arms": arm_models,
        "notes": "Per-arm linear reward priors. Runtime blends these with online bandit scores.",
    }
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, indent=2)
    return model


def _load_verifier_events(db_path: str) -> List[Tuple[List[float], float, Dict[str, Any]]]:
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT metadata
            FROM progress_events
            WHERE stage = 'verifier_result'
            ORDER BY id ASC
            """
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    examples: List[Tuple[List[float], float, Dict[str, Any]]] = []
    for (meta_text,) in rows:
        try:
            meta = json.loads(meta_text or "{}")
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        features = build_reward_features(meta, iteration=int(meta.get("iteration", 1) or 1))
        label = 1.0 if meta.get("is_passed") else 0.0
        examples.append((features, label, meta))
    return examples


def train_reward_model(db_path: str, output_path: str) -> Dict[str, Any]:
    examples = _load_verifier_events(db_path)
    if not examples:
        raise RuntimeError(f"No verifier_result examples found in {db_path}")
    rows = [(x, y) for x, y, _ in examples]
    fitted = _fit_logistic_sgd(rows)
    labels = Counter(int(y) for _, y in rows)
    model = {
        "kind": "reward_model",
        "version": f"reward_model_{int(time.time())}",
        "created_at": int(time.time()),
        "feature_dim": len(rows[0][0]),
        "feature_names": [
            "relevancy_index",
            "inverse_redundancy",
            "quality_score",
            "rel_margin_centered",
            "red_margin_centered",
            "quality_margin_centered",
            "source_check_passed",
            "source_reference_count_norm",
            "iteration_norm",
            *QUALITY_DIMENSION_KEYS,
        ],
        "positive_examples": labels.get(1, 0),
        "negative_examples": labels.get(0, 0),
        **fitted,
        "notes": "Predicts verifier pass probability / draft usefulness for best-candidate selection.",
    }
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, indent=2)
    return model


def main() -> None:
    parser = argparse.ArgumentParser(description="Train FlowerNet lightweight models")
    parser.add_argument("--events", default=project_root("controller_bandit_events.jsonl"))
    parser.add_argument("--db", default=project_root("flowernet_history.db"))
    parser.add_argument("--models-dir", default=project_root("models"))
    parser.add_argument("--controller-output", default="")
    parser.add_argument("--reward-output", default="")
    args = parser.parse_args()

    models_dir = args.models_dir
    controller_output = args.controller_output or os.path.join(models_dir, "controller_policy.json")
    reward_output = args.reward_output or os.path.join(models_dir, "reward_model.json")

    controller_model = train_controller_policy(args.events, controller_output)
    reward_model = train_reward_model(args.db, reward_output)

    summary = {
        "controller_model": controller_output,
        "controller_training_events": controller_model.get("training_events"),
        "controller_arm_counts": controller_model.get("arm_counts"),
        "reward_model": reward_output,
        "reward_examples": int(reward_model.get("n", 0) or 0),
        "reward_positive_examples": reward_model.get("positive_examples"),
        "reward_negative_examples": reward_model.get("negative_examples"),
        "reward_accuracy": reward_model.get("accuracy"),
        "reward_log_loss": reward_model.get("log_loss"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

