#!/usr/bin/env python3
"""Offline policy evaluation for FlowerNet controller bandit logs.

Supports IPS / SNIPS / Doubly Robust with bootstrap confidence intervals.
"""

import argparse
import json
import math
import random
from collections import defaultdict
from typing import Any, Dict, List, Tuple


def load_events(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            arm = str(obj.get("chosen_arm", ""))
            prop = float(obj.get("propensity", 0.0) or 0.0)
            rew = float(obj.get("reward", 0.0) or 0.0)
            if not arm or prop <= 0:
                continue
            obj["chosen_arm"] = arm
            obj["propensity"] = max(1e-6, min(1.0, prop))
            obj["reward"] = max(0.0, min(1.0, rew))
            rows.append(obj)
    return rows


def target_policy_prob(event: Dict[str, Any], temperature: float) -> Dict[str, float]:
    scores = ((event.get("policy_scores") or {}) if isinstance(event.get("policy_scores"), dict) else {})
    if not scores:
        arm = event["chosen_arm"]
        return {arm: 1.0}

    logits: Dict[str, float] = {}
    for arm, detail in scores.items():
        if not isinstance(detail, dict):
            continue
        logits[str(arm)] = float(detail.get("total", 0.0) or 0.0)

    if not logits:
        arm = event["chosen_arm"]
        return {arm: 1.0}

    t = max(1e-6, float(temperature))
    max_logit = max(logits.values())
    exps = {a: math.exp((v - max_logit) / t) for a, v in logits.items()}
    denom = sum(exps.values()) or 1.0
    return {a: e / denom for a, e in exps.items()}


def fit_direct_model(events: List[Dict[str, Any]]) -> Dict[str, float]:
    by_arm: Dict[str, List[float]] = defaultdict(list)
    for ev in events:
        by_arm[ev["chosen_arm"]].append(float(ev["reward"]))
    return {arm: sum(vals) / max(1, len(vals)) for arm, vals in by_arm.items()}


def evaluate(events: List[Dict[str, Any]], temperature: float) -> Dict[str, float]:
    if not events:
        return {"ips": 0.0, "snips": 0.0, "dr": 0.0, "n": 0}

    q_hat = fit_direct_model(events)

    ips_sum = 0.0
    snips_num = 0.0
    snips_den = 0.0
    dr_sum = 0.0

    for ev in events:
        arm = ev["chosen_arm"]
        mu = float(ev["propensity"])
        r = float(ev["reward"])
        pi = target_policy_prob(ev, temperature).get(arm, 0.0)
        w = pi / max(1e-6, mu)

        ips_sum += w * r
        snips_num += w * r
        snips_den += w

        # DR estimator.
        expected_q = 0.0
        pi_all = target_policy_prob(ev, temperature)
        for a, p in pi_all.items():
            expected_q += p * float(q_hat.get(a, 0.0))
        dr = expected_q + w * (r - float(q_hat.get(arm, 0.0)))
        dr_sum += dr

    n = len(events)
    return {
        "ips": ips_sum / n,
        "snips": (snips_num / max(1e-9, snips_den)),
        "dr": dr_sum / n,
        "n": n,
    }


def bootstrap_ci(events: List[Dict[str, Any]], temperature: float, rounds: int = 500, alpha: float = 0.05) -> Dict[str, Tuple[float, float]]:
    if not events:
        return {"ips": (0.0, 0.0), "snips": (0.0, 0.0), "dr": (0.0, 0.0)}

    vals = {"ips": [], "snips": [], "dr": []}
    n = len(events)
    for _ in range(max(50, rounds)):
        sample = [events[random.randint(0, n - 1)] for _ in range(n)]
        out = evaluate(sample, temperature)
        vals["ips"].append(out["ips"])
        vals["snips"].append(out["snips"])
        vals["dr"].append(out["dr"])

    def quantile(xs: List[float], q: float) -> float:
        ys = sorted(xs)
        idx = min(len(ys) - 1, max(0, int(q * (len(ys) - 1))))
        return ys[idx]

    lo_q = alpha / 2.0
    hi_q = 1.0 - alpha / 2.0
    return {
        "ips": (quantile(vals["ips"], lo_q), quantile(vals["ips"], hi_q)),
        "snips": (quantile(vals["snips"], lo_q), quantile(vals["snips"], hi_q)),
        "dr": (quantile(vals["dr"], lo_q), quantile(vals["dr"], hi_q)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="FlowerNet Controller OPE evaluator")
    parser.add_argument("--events", default="controller_bandit_events.jsonl", help="Path to events jsonl")
    parser.add_argument("--temperature", type=float, default=0.3, help="Softmax temperature for target policy")
    parser.add_argument("--bootstrap", type=int, default=400, help="Bootstrap rounds")
    parser.add_argument("--alpha", type=float, default=0.05, help="CI alpha")
    args = parser.parse_args()

    events = load_events(args.events)
    out = evaluate(events, args.temperature)
    ci = bootstrap_ci(events, args.temperature, rounds=args.bootstrap, alpha=args.alpha)

    result = {
        "events": out["n"],
        "temperature": args.temperature,
        "ips": round(out["ips"], 6),
        "ips_ci": [round(ci["ips"][0], 6), round(ci["ips"][1], 6)],
        "snips": round(out["snips"], 6),
        "snips_ci": [round(ci["snips"][0], 6), round(ci["snips"][1], 6)],
        "dr": round(out["dr"], 6),
        "dr_ci": [round(ci["dr"][0], 6), round(ci["dr"][1], 6)],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
