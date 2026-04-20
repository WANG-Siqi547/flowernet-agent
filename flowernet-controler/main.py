from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Dict, Any, Optional, Tuple
from controler import FlowerNetController
import os
import requests
import re
import time
from collections import Counter
import json
import random
import threading
import math

app = FastAPI(title="FlowerNet Controller API")

# 初始化 Controller
controller = FlowerNetController()

outliner_url = None
BANDIT_LOCK = threading.Lock()


def _project_root_path(*parts: str) -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, *parts)


def _bandit_state_path() -> str:
    raw = os.getenv("CONTROLLER_BANDIT_STATE_PATH", "controller_bandit_state.json").strip()
    if os.path.isabs(raw):
        return raw
    return _project_root_path(raw)


def _default_bandit_state(feature_dim: int) -> Dict[str, Any]:
    return {
        "version": 2,
        "feature_dim": feature_dim,
        "total_rounds": 0,
        "drift": {
            "method": "page_hinkley",
            "mean": 0.0,
            "cum_sum": 0.0,
            "min_cum_sum": 0.0,
            "count": 0,
            "drift_events": 0,
            "last_drift_round": 0,
        },
        "constraints": {
            "lambda_latency": 0.0,
            "lambda_cost": 0.0,
            "avg_latency": 0.0,
            "avg_cost": 0.0,
        },
        "arms": {
            "llm": {"count": 0, "weights": [0.0] * feature_dim, "bias": 0.0, "avg_latency": 0.0, "avg_cost": 0.0},
            "rule": {"count": 0, "weights": [0.0] * feature_dim, "bias": 0.0, "avg_latency": 0.0, "avg_cost": 0.0},
            "rule_structured": {"count": 0, "weights": [0.0] * feature_dim, "bias": 0.0, "avg_latency": 0.0, "avg_cost": 0.0},
            "defect_topic": {"count": 0, "weights": [0.0] * feature_dim, "bias": 0.0, "avg_latency": 0.0, "avg_cost": 0.0},
            "defect_evidence": {"count": 0, "weights": [0.0] * feature_dim, "bias": 0.0, "avg_latency": 0.0, "avg_cost": 0.0},
            "defect_structure": {"count": 0, "weights": [0.0] * feature_dim, "bias": 0.0, "avg_latency": 0.0, "avg_cost": 0.0},
        },
    }


def _load_bandit_state(feature_dim: int) -> Dict[str, Any]:
    path = _bandit_state_path()
    default_state = _default_bandit_state(feature_dim)
    try:
        if not os.path.exists(path):
            return default_state
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return default_state
        if int(data.get("feature_dim", 0)) != feature_dim:
            return default_state
        arms = data.get("arms") if isinstance(data.get("arms"), dict) else {}
        for arm_name in default_state["arms"].keys():
            arm_data = arms.get(arm_name, {})
            weights = arm_data.get("weights") if isinstance(arm_data, dict) else None
            if not isinstance(weights, list) or len(weights) != feature_dim:
                return default_state

        # Forward compatibility with evolving state schema.
        merged = default_state
        merged["total_rounds"] = int(data.get("total_rounds", 0))
        if isinstance(data.get("drift"), dict):
            merged["drift"].update(data["drift"])
        if isinstance(data.get("constraints"), dict):
            merged["constraints"].update(data["constraints"])
        for arm_name, arm_default in default_state["arms"].items():
            arm_data = arms.get(arm_name, {}) if isinstance(arms, dict) else {}
            if isinstance(arm_data, dict):
                arm_default.update(arm_data)
        return merged
    except Exception:
        return default_state


def _save_bandit_state(state: Dict[str, Any]) -> None:
    path = _bandit_state_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️  保存 bandit state 失败: {e}")


def _dot(weights: List[float], features: List[float]) -> float:
    return sum(float(w) * float(x) for w, x in zip(weights, features))


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _extract_numeric_suffix(value: Optional[str]) -> float:
    text = str(value or "")
    m = re.search(r"(\d+)(?!.*\d)", text)
    if not m:
        return 0.0
    return min(1.0, int(m.group(1)) / 20.0)


def _ope_events_path() -> str:
    raw = os.getenv("CONTROLLER_BANDIT_EVENTS_PATH", "controller_bandit_events.jsonl").strip()
    if os.path.isabs(raw):
        return raw
    return _project_root_path(raw)


def _append_ope_event(event: Dict[str, Any]) -> None:
    try:
        path = _ope_events_path()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"⚠️  写入 OPE 事件失败: {e}")


def _extract_quality_dims(feedback: Dict[str, Any]) -> Dict[str, float]:
    dims_raw = feedback.get("quality_dimensions") if isinstance(feedback.get("quality_dimensions"), dict) else {}
    return {k: _clip01(float(v)) for k, v in dims_raw.items() if isinstance(v, (int, float))}


def _extract_uncertainty_dims(feedback: Dict[str, Any]) -> Dict[str, float]:
    unc_raw = feedback.get("quality_dimensions_uncertainty") if isinstance(feedback.get("quality_dimensions_uncertainty"), dict) else {}
    return {k: _clip01(float(v)) for k, v in unc_raw.items() if isinstance(v, (int, float))}


def _build_defect_graph(feedback: Dict[str, Any], rel_score: float, red_score: float, rel_threshold: float, red_threshold: float) -> Dict[str, float]:
    dims = _extract_quality_dims(feedback)
    unc = _extract_uncertainty_dims(feedback)

    topic_defect = _clip01(max(0.0, rel_threshold - rel_score) + (1.0 - dims.get("topic_alignment", 0.0)) * 0.6)
    coverage_defect = _clip01(1.0 - dims.get("coverage_completeness", 0.0))
    evidence_defect = _clip01(1.0 - dims.get("evidence_grounding", 0.0))
    structure_defect = _clip01(1.0 - dims.get("structure_clarity", 0.0))
    coherence_defect = _clip01(1.0 - dims.get("logical_coherence", 0.0))
    redundancy_defect = _clip01(max(0.0, red_score - red_threshold))

    # Uncertainty boosts risk-sensitive repair priority.
    uncertainty_pressure = _clip01(sum(unc.values()) / max(1, len(unc))) if unc else 0.0

    return {
        "topic": round(topic_defect, 4),
        "coverage": round(coverage_defect, 4),
        "evidence": round(evidence_defect, 4),
        "structure": round(structure_defect, 4),
        "coherence": round(coherence_defect, 4),
        "redundancy": round(redundancy_defect, 4),
        "uncertainty_pressure": round(uncertainty_pressure, 4),
    }


def _estimate_arm_cost_latency(source: str, prompt_len: int, output_len: int, llm_elapsed: float) -> Tuple[float, float]:
    # A simple, stable proxy used for constrained optimization and reproducible OPE logs.
    if source == "llm":
        token_cost = max(1.0, (prompt_len + output_len) / 4.0)
        latency = max(0.01, float(llm_elapsed))
    elif source in ("rule", "rule_structured"):
        token_cost = max(1.0, output_len / 8.0)
        latency = 0.01
    elif source == "defect_topic":
        token_cost = max(1.0, output_len / 7.0)
        latency = 0.02
    elif source == "defect_evidence":
        token_cost = max(1.0, output_len / 7.0)
        latency = 0.02
    elif source == "defect_structure":
        token_cost = max(1.0, output_len / 7.0)
        latency = 0.02
    else:
        token_cost = max(1.0, output_len / 8.0)
        latency = 0.02
    return float(token_cost), float(latency)


def _safe_sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _selection_probabilities(scores: Dict[str, Dict[str, float]], epsilon: float) -> Dict[str, float]:
    if not scores:
        return {}
    arms = list(scores.keys())
    best_arm = max(arms, key=lambda arm: scores[arm]["total"])
    k = len(arms)
    base = epsilon / max(1, k)
    probs = {arm: base for arm in arms}
    probs[best_arm] = min(1.0, probs[best_arm] + (1.0 - epsilon))
    return probs


def _update_constraints(state: Dict[str, Any], observed_latency: float, observed_cost: float) -> Dict[str, float]:
    constraints = state.get("constraints") if isinstance(state.get("constraints"), dict) else {}
    target_latency = max(0.05, float(os.getenv("CONTROLLER_CONSTRAINT_TARGET_LATENCY", "2.0")))
    target_cost = max(1.0, float(os.getenv("CONTROLLER_CONSTRAINT_TARGET_COST", "600")))
    dual_lr = max(0.0005, min(0.2, float(os.getenv("CONTROLLER_CONSTRAINT_DUAL_LR", "0.02"))))
    ema_alpha = max(0.01, min(0.5, float(os.getenv("CONTROLLER_CONSTRAINT_EMA_ALPHA", "0.08"))))

    avg_latency = float(constraints.get("avg_latency", 0.0))
    avg_cost = float(constraints.get("avg_cost", 0.0))
    if avg_latency <= 0:
        avg_latency = observed_latency
    else:
        avg_latency = (1 - ema_alpha) * avg_latency + ema_alpha * observed_latency

    if avg_cost <= 0:
        avg_cost = observed_cost
    else:
        avg_cost = (1 - ema_alpha) * avg_cost + ema_alpha * observed_cost

    lambda_latency = max(0.0, float(constraints.get("lambda_latency", 0.0)) + dual_lr * (avg_latency - target_latency))
    lambda_cost = max(0.0, float(constraints.get("lambda_cost", 0.0)) + dual_lr * ((avg_cost - target_cost) / target_cost))

    constraints["avg_latency"] = float(avg_latency)
    constraints["avg_cost"] = float(avg_cost)
    constraints["lambda_latency"] = float(lambda_latency)
    constraints["lambda_cost"] = float(lambda_cost)
    state["constraints"] = constraints

    return {
        "target_latency": target_latency,
        "target_cost": target_cost,
        "avg_latency": round(avg_latency, 4),
        "avg_cost": round(avg_cost, 4),
        "lambda_latency": round(lambda_latency, 4),
        "lambda_cost": round(lambda_cost, 4),
    }


def _update_drift_and_maybe_reset(state: Dict[str, Any], reward: float) -> Dict[str, Any]:
    drift = state.get("drift") if isinstance(state.get("drift"), dict) else {}
    alpha = max(0.001, min(0.2, float(os.getenv("CONTROLLER_DRIFT_ALPHA", "0.03"))))
    threshold = max(0.01, float(os.getenv("CONTROLLER_DRIFT_THRESHOLD", "0.2")))
    decay = max(0.0, min(1.0, float(os.getenv("CONTROLLER_DRIFT_DECAY", "0.6"))))

    mean = float(drift.get("mean", 0.0))
    count = int(drift.get("count", 0)) + 1
    mean = (1 - alpha) * mean + alpha * float(reward)
    centered = float(reward) - mean - 0.01
    cum_sum = float(drift.get("cum_sum", 0.0)) + centered
    min_cum = min(float(drift.get("min_cum_sum", 0.0)), cum_sum)
    statistic = cum_sum - min_cum
    triggered = statistic > threshold

    drift["mean"] = mean
    drift["count"] = count
    drift["cum_sum"] = cum_sum
    drift["min_cum_sum"] = min_cum

    if triggered:
        drift["drift_events"] = int(drift.get("drift_events", 0)) + 1
        drift["last_drift_round"] = int(state.get("total_rounds", 0))
        drift["cum_sum"] = 0.0
        drift["min_cum_sum"] = 0.0
        for arm_data in (state.get("arms") or {}).values():
            if not isinstance(arm_data, dict):
                continue
            weights = arm_data.get("weights") if isinstance(arm_data.get("weights"), list) else []
            arm_data["weights"] = [float(w) * decay for w in weights]
            arm_data["bias"] = float(arm_data.get("bias", 0.0)) * decay
            arm_data["count"] = int(float(arm_data.get("count", 0)) * decay)

    state["drift"] = drift
    return {
        "triggered": triggered,
        "statistic": round(statistic, 4),
        "threshold": round(threshold, 4),
        "drift_events": int(drift.get("drift_events", 0)),
        "last_drift_round": int(drift.get("last_drift_round", 0)),
    }


def _build_bandit_context_features(
    rel_score: float,
    red_score: float,
    rel_threshold: float,
    red_threshold: float,
    iteration: int,
    history: List[str],
    feedback: Dict[str, Any],
    defect_graph: Dict[str, float],
    section_id: Optional[str],
    subsection_id: Optional[str],
) -> List[float]:
    dims = _extract_quality_dims(feedback)
    unc = _extract_uncertainty_dims(feedback)
    rel_gap = max(0.0, rel_threshold - float(rel_score))
    red_gap = max(0.0, float(red_score) - red_threshold)
    source_passed = 1.0 if (feedback.get("source_check") or {}).get("passed") else 0.0
    unc_overall = _clip01(float(feedback.get("quality_overall_uncertainty", 0.0)))

    return [
        _clip01(rel_gap),
        _clip01(red_gap),
        _clip01(float(iteration) / 8.0),
        1.0 if history else 0.0,
        source_passed,
        _clip01(float(dims.get("topic_alignment", 0.0))),
        _clip01(float(dims.get("novelty", 0.0))),
        _clip01(float(dims.get("evidence_grounding", 0.0))),
        _clip01(float(dims.get("logical_coherence", 0.0))),
        _clip01(float(dims.get("coverage_completeness", 0.0))),
        _clip01(float(dims.get("structure_clarity", 0.0))),
        _clip01(unc_overall),
        _clip01(sum(unc.values()) / max(1, len(unc))) if unc else 0.0,
        _clip01(float(defect_graph.get("topic", 0.0))),
        _clip01(float(defect_graph.get("evidence", 0.0))),
        _clip01(float(defect_graph.get("structure", 0.0))),
        _clip01(float(defect_graph.get("uncertainty_pressure", 0.0))),
        _extract_numeric_suffix(section_id),
        _extract_numeric_suffix(subsection_id),
    ]


def _bandit_predict(arm_state: Dict[str, Any], features: List[float], explore_c: float) -> Tuple[float, float, float]:
    weights = arm_state.get("weights", [])
    bias = float(arm_state.get("bias", 0.0))
    count = int(arm_state.get("count", 0))
    exploit = bias + _dot(weights, features)
    explore = float(explore_c) / ((count + 1) ** 0.5)
    total = exploit + explore
    return total, exploit, explore


def _bandit_choose_arm(
    state: Dict[str, Any],
    available_arms: List[str],
    features: List[float],
    defect_graph: Dict[str, float],
) -> Tuple[str, Dict[str, Any]]:
    epsilon = max(0.0, min(0.5, float(os.getenv("CONTROLLER_BANDIT_EPSILON", "0.08"))))
    explore_c = max(0.0, float(os.getenv("CONTROLLER_BANDIT_EXPLORE_C", "0.12")))
    constraints = state.get("constraints") if isinstance(state.get("constraints"), dict) else {}
    lambda_latency = float(constraints.get("lambda_latency", 0.0))
    lambda_cost = float(constraints.get("lambda_cost", 0.0))

    # Defect-aware preference map for explainable arm alignment.
    arm_alignment = {
        "llm": 0.35 * defect_graph.get("topic", 0.0) + 0.25 * defect_graph.get("coverage", 0.0) + 0.20 * defect_graph.get("coherence", 0.0),
        "rule": 0.25 * defect_graph.get("redundancy", 0.0) + 0.25 * defect_graph.get("topic", 0.0),
        "rule_structured": 0.5 * defect_graph.get("structure", 0.0) + 0.25 * defect_graph.get("coherence", 0.0),
        "defect_topic": 0.8 * defect_graph.get("topic", 0.0) + 0.2 * defect_graph.get("coverage", 0.0),
        "defect_evidence": 0.8 * defect_graph.get("evidence", 0.0) + 0.2 * defect_graph.get("coverage", 0.0),
        "defect_structure": 0.8 * defect_graph.get("structure", 0.0) + 0.2 * defect_graph.get("coherence", 0.0),
    }

    scores: Dict[str, Dict[str, float]] = {}
    for arm in available_arms:
        arm_state = state.get("arms", {}).get(arm)
        if not isinstance(arm_state, dict):
            continue
        total, exploit, explore = _bandit_predict(arm_state, features, explore_c)
        avg_latency = max(0.0, float(arm_state.get("avg_latency", 0.0)))
        avg_cost = max(0.0, float(arm_state.get("avg_cost", 0.0)))
        latency_penalty = lambda_latency * min(2.0, avg_latency / max(0.1, float(os.getenv("CONTROLLER_CONSTRAINT_TARGET_LATENCY", "2.0"))))
        cost_penalty = lambda_cost * min(2.0, avg_cost / max(1.0, float(os.getenv("CONTROLLER_CONSTRAINT_TARGET_COST", "600"))))

        # Risk-aware confidence shaping: high uncertainty pressure favors defect-specific arms.
        alignment_bonus = 0.15 * _clip01(float(arm_alignment.get(arm, 0.0)))
        constrained_total = total - latency_penalty - cost_penalty + alignment_bonus

        scores[arm] = {
            "total": round(constrained_total, 4),
            "base_total": round(total, 4),
            "exploit": round(exploit, 4),
            "explore": round(explore, 4),
            "latency_penalty": round(latency_penalty, 4),
            "cost_penalty": round(cost_penalty, 4),
            "alignment_bonus": round(alignment_bonus, 4),
            "avg_latency": round(avg_latency, 4),
            "avg_cost": round(avg_cost, 4),
            "count": int(arm_state.get("count", 0)),
        }

    if not scores:
        return available_arms[0], {"mode": "fallback", "scores": {}}

    if random.random() < epsilon:
        chosen_arm = random.choice(list(scores.keys()))
        mode = "epsilon_explore"
    else:
        chosen_arm = max(scores.keys(), key=lambda a: scores[a]["total"])
        mode = "score_exploit"

    probs = _selection_probabilities(scores=scores, epsilon=epsilon)
    return chosen_arm, {"mode": mode, "scores": scores, "propensity": probs}


def _bandit_update(
    state: Dict[str, Any],
    arm: str,
    features: List[float],
    reward: float,
    predicted_exploit: float,
    observed_latency: float,
    observed_cost: float,
    uncertainty_pressure: float,
) -> None:
    base_lr = max(0.001, min(0.2, float(os.getenv("CONTROLLER_BANDIT_LR", "0.05"))))
    # Higher uncertainty encourages faster adaptation under non-stationarity.
    lr = min(0.25, base_lr * (1.0 + 0.8 * _clip01(uncertainty_pressure)))
    reward = _clip01(reward)
    arm_state = state.get("arms", {}).get(arm)
    if not isinstance(arm_state, dict):
        return

    weights = arm_state.get("weights") or []
    if len(weights) != len(features):
        return
    bias = float(arm_state.get("bias", 0.0))
    error = reward - float(predicted_exploit)

    new_weights = []
    for w, x in zip(weights, features):
        new_weights.append(float(w) + lr * error * float(x))

    arm_state["weights"] = new_weights
    arm_state["bias"] = bias + lr * error
    arm_state["count"] = int(arm_state.get("count", 0)) + 1

    ema_alpha = max(0.01, min(0.5, float(os.getenv("CONTROLLER_CONSTRAINT_EMA_ALPHA", "0.08"))))
    prev_latency = float(arm_state.get("avg_latency", 0.0))
    prev_cost = float(arm_state.get("avg_cost", 0.0))
    arm_state["avg_latency"] = float(observed_latency if prev_latency <= 0 else (1 - ema_alpha) * prev_latency + ema_alpha * observed_latency)
    arm_state["avg_cost"] = float(observed_cost if prev_cost <= 0 else (1 - ema_alpha) * prev_cost + ema_alpha * observed_cost)


def _get_outliner_session():
    s = requests.Session()
    s.trust_env = False
    return s


def _outliner_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    base = (os.getenv("OUTLINER_URL", "http://localhost:8003")).rstrip("/")
    timeout = int(os.getenv("OUTLINER_HTTP_TIMEOUT", "30"))
    resp = _get_outliner_session().post(f"{base}{path}", json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _get_controller_llm_session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    return s


def _parse_llm_content_from_response(data: Any) -> str:
    container = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
    choice = ((container.get("choices") or [{}])[0] or {}) if isinstance(container, dict) else {}
    msg = choice.get("message") if isinstance(choice, dict) else None
    content = ""
    if isinstance(msg, str):
        content = msg
    elif isinstance(msg, dict):
        content = msg.get("content", "")
    if isinstance(content, list):
        parts = [str(item.get("text", "")) for item in content if isinstance(item, dict)]
        content = "".join(parts)
    return str(content or "").strip()


def _generate_outline_with_sensenova(prompt: str, max_tokens: int, timeout: int, retries: int) -> Tuple[Optional[str], str]:
    api_key = os.getenv("CONTROLLER_SENSENOVA_API_KEY", os.getenv("SENSENOVA_API_KEY", "")).strip()
    if not api_key:
        return None, "CONTROLLER_SENSENOVA_API_KEY not set"

    api_url = os.getenv(
        "CONTROLLER_SENSENOVA_API_URL",
        os.getenv("SENSENOVA_API_URL", "https://api.sensenova.cn/v1/llm/chat-completions")
    ).rstrip("/")
    model = os.getenv(
        "CONTROLLER_SENSENOVA_MODEL",
        os.getenv("SENSENOVA_MODEL", "SenseNova-V6-5-Turbo")
    )

    payload_variants = [
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "stream": False,
            "max_tokens": max_tokens,
        },
        {
            "model": model,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
            "temperature": 0.3,
            "stream": False,
            "max_tokens": max_tokens,
        },
    ]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_error = "unknown"
    session = _get_controller_llm_session()
    for attempt in range(1, retries + 1):
        for payload in payload_variants:
            try:
                resp = session.post(api_url, json=payload, headers=headers, timeout=timeout)
                if resp.status_code >= 400:
                    text = (resp.text or "").strip()
                    last_error = f"SenseNova HTTP {resp.status_code}: {text[:300]}"
                    # 4xx 可能是 content 格式差异，允许切换 payload 继续尝试
                    continue
                content = _parse_llm_content_from_response(resp.json())
                if content:
                    return content, ""
                last_error = "SenseNova empty response"
            except Exception as e:
                last_error = f"SenseNova request failed: {str(e)}"

        if attempt < retries:
            time.sleep(min(6, 1.5 * attempt))

    return None, last_error


def _generate_outline_via_generator(prompt: str, max_tokens: int, timeout: int, retries: int) -> Tuple[Optional[str], str]:
    generator_base = (os.getenv("GENERATOR_URL", "http://localhost:8002")).rstrip("/")
    last_error = "generator_unavailable"
    for attempt in range(1, retries + 1):
        try:
            sess = _get_controller_llm_session()
            resp = sess.post(
                f"{generator_base}/generate",
                json={"prompt": prompt, "max_tokens": max_tokens},
                timeout=timeout,
            )
            if resp.status_code == 200:
                body = resp.json()
                if body.get("success") and body.get("draft"):
                    return _sanitize_outline_text(str(body["draft"]).strip()), ""
                last_error = str(body.get("error") or "llm_generate_unsuccessful")
            else:
                last_error = f"HTTP {resp.status_code}: {(resp.text or '')[:200]}"
        except Exception as e:
            last_error = str(e)

        if attempt < retries:
            time.sleep(min(4, 1.2 * attempt))

    return None, last_error


def _fetch_subsection_outline_from_db(
    document_id: Optional[str],
    section_id: Optional[str],
    subsection_id: Optional[str],
) -> Optional[str]:
    """通过 outliner 服务从数据库中取 subsection 当前大纲。"""
    if not (document_id and section_id and subsection_id):
        return None
    try:
        body = _outliner_post(
            "/subsection-tracking/get",
            {"document_id": document_id, "section_id": section_id, "subsection_id": subsection_id},
        )
        tracking = body.get("tracking") or {}
        outline = (tracking.get("outline") or "").strip()
        if outline:
            return outline
    except Exception as e:
        print(f"⚠️  从 DB 读取 subsection outline 失败: {e}")
    try:
        body = _outliner_post(
            "/outline/get",
            {
                "document_id": document_id,
                "outline_type": "subsection",
                "section_id": section_id,
                "subsection_id": subsection_id,
            },
        )
        outline = (body.get("outline") or "").strip()
        if outline:
            return outline
    except Exception as e:
        print(f"⚠️  从 DB 读取 subsection outline（outline表）失败: {e}")
    return None


def _save_improved_outline_to_db(
    document_id: Optional[str],
    section_id: Optional[str],
    subsection_id: Optional[str],
    improved_outline: str,
    iteration_count: Optional[int] = None,
):
    """把改进后的大纲回写到 subsection_tracking 表。"""
    if not (document_id and section_id and subsection_id):
        return
    try:
        payload: Dict[str, Any] = {
            "document_id": document_id,
            "section_id": section_id,
            "subsection_id": subsection_id,
            "outline": improved_outline,
        }
        if iteration_count is not None:
            payload["iteration_count"] = iteration_count
        _outliner_post("/subsection-tracking/update", payload)
        print(f"✅ 改进大纲已写入 DB: {subsection_id}")
    except Exception as e:
        print(f"⚠️  回写改进大纲失败: {e}")


def _sanitize_outline_text(text: Optional[str]) -> str:
    """清洗被规则降级文本污染的大纲，避免相关性计算被元信息拉低。"""
    outline = (text or "").strip()
    if not outline:
        return ""

    # 去掉历史叠加的规则降级片段
    marker = "【第 "
    idx = outline.find(marker)
    if idx > 0:
        outline = outline[:idx].strip()

    # 去掉常见前缀标签
    for prefix in ("改进后大纲：", "改进后的大纲：", "大纲："):
        if outline.startswith(prefix):
            outline = outline[len(prefix):].strip()

    return outline


def _tokenize_text(text: str) -> List[str]:
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_-]{2,}", text or "")
    return [token.lower() for token in tokens if token.strip()]


def _extract_anchor_terms(text: str, max_terms: int = 16) -> List[str]:
    counter = Counter(_tokenize_text(text))
    return [term for term, _ in counter.most_common(max_terms)]


def _keyword_coverage(candidate: str, anchors: List[str]) -> float:
    if not anchors:
        return 0.0
    normalized = (candidate or "").lower()
    hit = sum(1 for term in anchors if term and term in normalized)
    return hit / max(1, len(anchors))


def _token_overlap_ratio(text_a: str, text_b: str) -> float:
    set_a = set(_tokenize_text(text_a))
    set_b = set(_tokenize_text(text_b))
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def _structure_score(outline: str) -> float:
    lines = [line.strip() for line in (outline or "").splitlines() if line.strip()]
    if not lines:
        return 0.0
    bullet_like = sum(1 for line in lines if line.startswith(("-", "*", "1.", "2.", "3.", "①", "②", "③")))
    line_count_score = min(1.0, len(lines) / 4.0)
    bullet_score = min(1.0, bullet_like / max(1, len(lines)))
    return 0.6 * line_count_score + 0.4 * bullet_score


def _score_outline_candidate(
    candidate_outline: str,
    original_outline: str,
    working_outline: str,
    failed_draft: str,
    history: List[str],
    rel_score: float,
    red_score: float,
    rel_threshold: float,
    red_threshold: float,
) -> Dict[str, float]:
    anchors = _extract_anchor_terms(original_outline)
    relevance_anchor = _keyword_coverage(candidate_outline, anchors)
    similarity_to_working = 1.0 - _token_overlap_ratio(candidate_outline, working_outline)

    history_text = "\n".join(history[-3:]) if history else ""
    overlap_history = _token_overlap_ratio(candidate_outline, history_text) if history_text else 0.0
    overlap_failed = _token_overlap_ratio(candidate_outline, failed_draft)
    novelty = 1.0 - min(1.0, 0.65 * overlap_history + 0.35 * overlap_failed)

    structure = _structure_score(candidate_outline)

    rel_gap = max(0.0, rel_threshold - rel_score)
    red_gap = max(0.0, red_score - red_threshold)

    rel_weight = 0.55 + min(0.25, rel_gap)
    red_weight = 0.20 + min(0.25, red_gap)
    structure_weight = max(0.10, 1.0 - rel_weight - red_weight)

    total = (
        rel_weight * relevance_anchor
        + red_weight * novelty
        + structure_weight * structure
        + 0.05 * similarity_to_working
    )

    return {
        "total": round(total, 4),
        "relevance_anchor": round(relevance_anchor, 4),
        "novelty": round(novelty, 4),
        "structure": round(structure, 4),
        "delta_from_working": round(similarity_to_working, 4),
    }


# ============ API 数据模型 ============

class RefinePromptRequest(BaseModel):
    """Prompt 修改请求"""
    old_prompt: str
    failed_draft: str
    feedback: Dict[str, Any]  # Verifier 返回的完整反馈
    outline: str
    history: List[str] = []
    iteration: int = 1


class AnalyzeFailureRequest(BaseModel):
    """失败模式分析请求"""
    failed_drafts: List[str]
    feedback_list: List[Dict[str, Any]]


class ImproveOutlineRequest(BaseModel):
    """改进大纲的请求（用于第三步）"""
    original_outline: str  # 原始大纲
    current_outline: str  # 当前大纲（可能已被改进过）
    failed_draft: str  # 验证失败的内容
    feedback: Dict[str, Any]  # Verifier 的反馈
    history: List[str] = []  # 已通过小节的历史内容（用于去重指导）
    iteration: int = 1  # 当前迭代轮次（第几次改纲）
    rel_threshold: float = 0.80  # 实际生效的相关性阈值
    red_threshold: float = 0.40  # 实际生效的冗余度阈值
    # 可选：如果传了这三个 ID，controller 会直接从数据库读取最新大纲并在成功后回写
    document_id: Optional[str] = None
    section_id: Optional[str] = None
    subsection_id: Optional[str] = None


# ============ API 端点 ============

@app.get("/")
def read_root():
    """根端点 - 检查服务状态"""
    return {
        "status": "online", 
        "message": "FlowerNet Controller is ready.", 
        "public_url": controller.public_url,
        "endpoints": {
            "/refine_prompt": "根据 Verifier 反馈修改 prompt",
            "/analyze_failures": "分析失败模式并给出建议"
        }
    }


@app.post("/refine_prompt")
async def refine_prompt(req: RefinePromptRequest):
    """
    根据 Verifier 反馈修改 Prompt
    
    输入：
    - old_prompt: 原始 prompt
    - failed_draft: 验证失败的 draft
    - feedback: Verifier 的验证反馈（包含 relevancy_index 和 redundancy_index）
    - outline: 段落大纲
    - history: 历史内容列表
    - iteration: 当前迭代次数
    
    输出：优化后的新 prompt
    """
    try:
        new_prompt = controller.refine_prompt(
            old_prompt=req.old_prompt,
            failed_draft=req.failed_draft,
            feedback=req.feedback,
            outline=req.outline,
            history=req.history,
            iteration=req.iteration
        )
        return {
            "success": True,
            "prompt": new_prompt
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/analyze_failures")
async def analyze_failures(req: AnalyzeFailureRequest):
    """
    分析多次失败的模式
    
    输入：
    - failed_drafts: 所有失败的 draft 列表
    - feedback_list: 对应的验证反馈列表
    
    输出：失败模式分析结果
    """
    try:
        analysis = controller.analyze_failure_patterns(
            failed_drafts=req.failed_drafts,
            feedback_list=req.feedback_list
        )
        return {
            "success": True,
            "analysis": analysis
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/improve-outline")
async def improve_outline(req: ImproveOutlineRequest):
    """
    根据验证反馈改进大纲（第三步中使用）

    如果传入了 document_id / section_id / subsection_id，
    则从数据库读取当前最新大纲作为改进起点，改进后写回数据库。
    """
    try:
        rel_score = req.feedback.get("relevancy_index", 0)
        red_score = req.feedback.get("redundancy_index", 0)
        feedback_text = req.feedback.get("feedback", "")
        # 使用请求中传入的实际值（不再从 feedback 里尝试读取）
        iteration = req.iteration
        rel_threshold = req.rel_threshold
        red_threshold = req.red_threshold

        # 优先从 DB 里取最新大纲，这样 controller 能感知之前已经改过的版本
        db_outline = _fetch_subsection_outline_from_db(
            document_id=req.document_id,
            section_id=req.section_id,
            subsection_id=req.subsection_id,
        )
        working_outline = _sanitize_outline_text(db_outline if db_outline else req.current_outline)
        original_outline = _sanitize_outline_text(req.original_outline) or working_outline

        # 构建历史上下文（已通过小节的摘要，供去重指导）
        history_context = ""
        if req.history:
            recent = req.history[-3:]  # 仅取最近3条避免 prompt 过长
            history_context = "\n\n已通过的前置内容（请避免与这些内容重复）：\n"
            for i, h in enumerate(recent, 1):
                history_context += f"[已通过小节 {i}]（前200字）: {h[:200]}\n"

        improvement_prompt = f"""
你是一个文档写作指导专家。本次你的任务是改进一个小节的详细写作大纲，使得根据该大纲生成的内容能够通过以下验证指标：
- 相关性（relevancy_index）需 >= {rel_threshold:.2f}（当前: {rel_score:.4f}）
- 冗余度（redundancy_index）需 <= {red_threshold:.2f}（当前: {red_score:.4f}）

这是第 {iteration} 次改纲尝试。

【原始大纲（保持原始意图不变）】
{original_outline}

【当前大纲（第 {iteration-1} 轮的版本，本次需要改进）】
{working_outline}

【验证失败的内容（前500字）】
{req.failed_draft[:500]}

【Verifier 反馈】
{feedback_text}
{history_context}
【改进要求】
{"1. 相关性不足（" + str(round(rel_score,4)) + " < " + str(rel_threshold) + "）：大纲要更明确、具体，强调该小节的核心主题，列出必须涵盖的关键点，确保每个写作要点都与主题直接相关。" if rel_score < rel_threshold else "1. 相关性已满足，保持当前主题聚焦度。"}
{"2. 冗余度过高（" + str(round(red_score,4)) + " > " + str(red_threshold) + "）：大纲中必须明确指出哪些角度/信息已被前文覆盖，要求写全新的视角，可以列出具体的禁止重复方向。" if red_score > red_threshold else "2. 冗余度已满足，保持现有差异化要求。"}

请直接输出改进后的详细大纲文本（仍然是大纲，不是正文），不要添加任何前言或解释标签。
"""

        improved_outline = None
        llm_outline = None
        llm_elapsed_sec = 0.0

        llm_error = ""
        use_llm_outline = os.getenv("CONTROLLER_USE_LLM_OUTLINE", "true").lower() == "true"
        require_llm_source = os.getenv("CONTROLLER_REQUIRE_LLM_SOURCE", "false").lower() == "true"
        llm_timeout = max(20, int(os.getenv("CONTROLLER_LLM_TIMEOUT", "90")))
        llm_retries = max(1, int(os.getenv("CONTROLLER_LLM_RETRIES", "3")))
        llm_call_mode = os.getenv("CONTROLLER_LLM_CALL_MODE", "direct").strip().lower()
        llm_fallback_to_generator = os.getenv("CONTROLLER_LLM_FALLBACK_TO_GENERATOR", "true").lower() == "true"

        if use_llm_outline:
            llm_start = time.perf_counter()
            if llm_call_mode == "generator":
                llm_outline, llm_error = _generate_outline_via_generator(
                    prompt=improvement_prompt,
                    max_tokens=700,
                    timeout=llm_timeout,
                    retries=llm_retries,
                )
            else:
                llm_outline, llm_error = _generate_outline_with_sensenova(
                    prompt=improvement_prompt,
                    max_tokens=700,
                    timeout=llm_timeout,
                    retries=llm_retries,
                )
                if not llm_outline and llm_fallback_to_generator:
                    fallback_outline, fallback_error = _generate_outline_via_generator(
                        prompt=improvement_prompt,
                        max_tokens=700,
                        timeout=llm_timeout,
                        retries=max(1, min(2, llm_retries)),
                    )
                    if fallback_outline:
                        llm_outline = fallback_outline
                        llm_error = ""
                    else:
                        llm_error = f"direct={llm_error} | fallback_generator={fallback_error}"
            llm_elapsed_sec = max(0.0, time.perf_counter() - llm_start)
        else:
            llm_error = "llm_outline_disabled"

        if not llm_outline and llm_error:
            print(f"⚠️  LLM 改进大纲失败（会使用规则降级）: {llm_error}")

        anchors = _extract_anchor_terms(original_outline, max_terms=12)
        failed_lower = str(req.failed_draft or "").lower()
        missing_anchor_terms = [term for term in anchors if term and term not in failed_lower][:6]

        history_text = "\n".join(req.history[-3:]) if req.history else ""
        history_terms = set(_extract_anchor_terms(history_text, max_terms=12)) if history_text else set()
        failed_terms = _extract_anchor_terms(req.failed_draft, max_terms=12)
        repeated_terms = [term for term in failed_terms if term in history_terms][:5]

        fallback_lines = [working_outline or original_outline]
        if rel_score < rel_threshold:
            fallback_lines.append(
                "补充要求：开头先定义本小节核心结论，再按要点展开，每段都要与本小节主题直接对应。"
            )
            if missing_anchor_terms:
                fallback_lines.append("补充要求：必须覆盖关键词：" + "、".join(missing_anchor_terms) + "。")
        if red_score > red_threshold:
            fallback_lines.append(
                "补充要求：避免复述前文已有信息，改写为新的事实、案例或角度。"
            )
            if repeated_terms:
                fallback_lines.append("补充要求：避免重复这些已出现词：" + "、".join(repeated_terms) + "。")
        if "偏离主题" in feedback_text or rel_score < 0.5:
            fallback_lines.append(
                "补充要求：删除泛泛背景描述，只保留与当前大纲要点直接相关的内容。"
            )
        rule_outline = _sanitize_outline_text("\n".join([line for line in fallback_lines if line and line.strip()]))

        structured_blocks = [
            "【改纲版本】第{}轮结构化修订".format(iteration),
            "【核心主题】" + (original_outline[:240] if original_outline else "请紧扣当前小节主题"),
            "【写作结构】",
            "1) 先给出本小节的核心定义/结论（1-2句）",
            "2) 再按 3-5 个要点展开，每个要点必须直接支撑主题",
            "3) 结尾用 1 段总结该小节新增信息，不复述前文",
        ]
        if missing_anchor_terms:
            structured_blocks.append("【必写关键词】" + "、".join(missing_anchor_terms))
        if repeated_terms:
            structured_blocks.append("【禁止重复词】" + "、".join(repeated_terms))
        if red_score > red_threshold:
            structured_blocks.append("【差异化要求】每个要点至少包含一个新的事实、案例、数据或机制说明。")
        if feedback_text:
            structured_blocks.append("【Verifier反馈约束】" + feedback_text[:180])
        structured_blocks.append("【质量阈值】relevancy >= {:.2f}, redundancy <= {:.2f}".format(rel_threshold, red_threshold))

        structured_rule_outline = _sanitize_outline_text("\n".join([blk for blk in structured_blocks if blk.strip()]))

        defect_graph = _build_defect_graph(
            feedback=req.feedback,
            rel_score=float(rel_score),
            red_score=float(red_score),
            rel_threshold=float(rel_threshold),
            red_threshold=float(red_threshold),
        )

        defect_topic_outline = _sanitize_outline_text(
            "\n".join(
                [
                    working_outline or original_outline,
                    "补充要求：优先修复主题偏移，所有段落首句必须直接回应本小节目标。",
                    "补充要求：每个要点必须包含与主题锚点对应的明确关键词与结论句。",
                ]
            )
        )
        defect_evidence_outline = _sanitize_outline_text(
            "\n".join(
                [
                    working_outline or original_outline,
                    "补充要求：优先修复证据不足，每个要点必须补充可验证事实或来源线索。",
                    "补充要求：避免抽象表述，改用可落地机制、数据口径或案例片段。",
                ]
            )
        )
        defect_structure_outline = _sanitize_outline_text(
            "\n".join(
                [
                    "【结构化修复】",
                    "1) 核心结论（1-2句）",
                    "2) 要点A：问题界定与边界",
                    "3) 要点B：执行机制与步骤",
                    "4) 要点C：风险与保障",
                    "5) 小结：新增信息与与前文区分",
                    "原始约束：" + (original_outline[:240] if original_outline else "保持主题一致"),
                ]
            )
        )

        baseline_outline = working_outline or original_outline
        baseline_score = _score_outline_candidate(
            candidate_outline=baseline_outline,
            original_outline=original_outline,
            working_outline=baseline_outline,
            failed_draft=req.failed_draft,
            history=req.history,
            rel_score=rel_score,
            red_score=red_score,
            rel_threshold=rel_threshold,
            red_threshold=red_threshold,
        )

        candidates: List[Dict[str, Any]] = []
        if llm_outline:
            candidates.append({"source": "llm", "outline": llm_outline})
        if rule_outline:
            candidates.append({"source": "rule", "outline": rule_outline})
        if structured_rule_outline:
            candidates.append({"source": "rule_structured", "outline": structured_rule_outline})
        if defect_topic_outline:
            candidates.append({"source": "defect_topic", "outline": defect_topic_outline})
        if defect_evidence_outline:
            candidates.append({"source": "defect_evidence", "outline": defect_evidence_outline})
        if defect_structure_outline:
            candidates.append({"source": "defect_structure", "outline": defect_structure_outline})

        seen = set()
        dedup_candidates: List[Dict[str, Any]] = []
        for candidate in candidates:
            outline_text = candidate["outline"].strip()
            if not outline_text:
                continue
            key = outline_text[:2000]
            if key in seen:
                continue
            seen.add(key)
            score_detail = _score_outline_candidate(
                candidate_outline=outline_text,
                original_outline=original_outline,
                working_outline=baseline_outline,
                failed_draft=req.failed_draft,
                history=req.history,
                rel_score=rel_score,
                red_score=red_score,
                rel_threshold=rel_threshold,
                red_threshold=red_threshold,
            )
            dedup_candidates.append(
                {
                    "source": candidate["source"],
                    "outline": outline_text,
                    "score": score_detail,
                }
            )

        min_gain = float(os.getenv("CONTROLLER_MIN_SCORE_GAIN", "0.001"))
        min_rel_anchor_gain = float(os.getenv("CONTROLLER_MIN_REL_ANCHOR_GAIN", "0.005"))
        min_novelty_gain = float(os.getenv("CONTROLLER_MIN_NOVELTY_GAIN", "0.005"))
        min_structure_gain = float(os.getenv("CONTROLLER_MIN_STRUCTURE_GAIN", "0.05"))

        bandit_enabled = os.getenv("CONTROLLER_BANDIT_ENABLED", "true").lower() == "true"
        feature_vector = _build_bandit_context_features(
            rel_score=rel_score,
            red_score=red_score,
            rel_threshold=rel_threshold,
            red_threshold=red_threshold,
            iteration=iteration,
            history=req.history,
            feedback=req.feedback,
            defect_graph=defect_graph,
            section_id=req.section_id,
            subsection_id=req.subsection_id,
        )
        chosen = None
        chosen_source = "baseline"
        bandit_debug: Dict[str, Any] = {
            "enabled": bandit_enabled,
            "feature_vector": [round(v, 4) for v in feature_vector],
            "defect_graph": defect_graph,
            "selection": {},
            "state_path": _bandit_state_path(),
        }

        if dedup_candidates:
            if bandit_enabled:
                best_by_source: Dict[str, Dict[str, Any]] = {}
                for cand in dedup_candidates:
                    src = cand["source"]
                    if src not in best_by_source or cand["score"]["total"] > best_by_source[src]["score"]["total"]:
                        best_by_source[src] = cand

                available_arms = [
                    arm
                    for arm in ["llm", "rule", "rule_structured", "defect_topic", "defect_evidence", "defect_structure"]
                    if arm in best_by_source
                ]
                if available_arms:
                    with BANDIT_LOCK:
                        state = _load_bandit_state(len(feature_vector))
                        selected_arm, selection_debug = _bandit_choose_arm(
                            state=state,
                            available_arms=available_arms,
                            features=feature_vector,
                            defect_graph=defect_graph,
                        )
                    chosen = best_by_source[selected_arm]
                    chosen_source = selected_arm
                    bandit_debug["selection"] = selection_debug
                    bandit_debug["selected_arm"] = selected_arm
                else:
                    chosen = max(dedup_candidates, key=lambda x: x["score"]["total"])
                    chosen_source = chosen["source"]
            else:
                chosen = max(dedup_candidates, key=lambda x: x["score"]["total"])
                chosen_source = chosen["source"]

        changed = False
        score_gain = (chosen["score"]["total"] - baseline_score["total"]) if chosen else 0.0
        rel_anchor_gain = (chosen["score"].get("relevance_anchor", 0.0) - baseline_score.get("relevance_anchor", 0.0)) if chosen else 0.0
        novelty_gain = (chosen["score"].get("novelty", 0.0) - baseline_score.get("novelty", 0.0)) if chosen else 0.0
        structure_gain = (chosen["score"].get("structure", 0.0) - baseline_score.get("structure", 0.0)) if chosen else 0.0

        rel_needed = rel_score < rel_threshold
        red_needed = red_score > red_threshold
        rel_gain_ok = (not rel_needed) or (rel_anchor_gain >= min_rel_anchor_gain) or (structure_gain >= min_structure_gain)
        novelty_gain_ok = (not red_needed) or (novelty_gain >= min_novelty_gain)

        if chosen and chosen["outline"].strip() != baseline_outline.strip():
            improved_outline = chosen["outline"]
            chosen_source = chosen["source"]
            changed = True
        else:
            improved_outline = baseline_outline

        # 线上稳定性优先：只要输出确实发生变化，且不是明显退化，就允许进入下一轮。
        # 这样避免 Controller 因阈值过严而反复返回原纲，导致 Generator/Controller 死循环。
        effective = bool(
            chosen
            and changed
            and (
                score_gain >= min_gain
                or chosen_source == "rule_structured"
                or (rel_gain_ok and novelty_gain_ok)
            )
        )
        if require_llm_source and chosen_source != "llm":
            effective = False

        if bandit_enabled and chosen:
            selection_scores = (bandit_debug.get("selection") or {}).get("scores", {})
            predicted_exploit = float((selection_scores.get(chosen_source) or {}).get("exploit", 0.0))
            reward_quality = _clip01(
                max(0.0, score_gain) * 0.55
                + max(0.0, rel_anchor_gain) * 0.20
                + max(0.0, novelty_gain) * 0.15
                + max(0.0, structure_gain) * 0.10
            )

            observed_cost, observed_latency = _estimate_arm_cost_latency(
                source=chosen_source,
                prompt_len=len(improvement_prompt),
                output_len=len(chosen.get("outline", "")),
                llm_elapsed=llm_elapsed_sec,
            )

            target_latency = max(0.05, float(os.getenv("CONTROLLER_CONSTRAINT_TARGET_LATENCY", "2.0")))
            target_cost = max(1.0, float(os.getenv("CONTROLLER_CONSTRAINT_TARGET_COST", "600")))
            latency_over = max(0.0, (observed_latency - target_latency) / target_latency)
            cost_over = max(0.0, (observed_cost - target_cost) / target_cost)
            penalty = _clip01(0.5 * latency_over + 0.5 * cost_over)

            uncertainty_pressure = float(defect_graph.get("uncertainty_pressure", 0.0))
            risk_bonus = 0.06 * uncertainty_pressure if chosen_source.startswith("defect_") else 0.0
            reward = _clip01(max(0.0, reward_quality - penalty) + risk_bonus)

            if not effective:
                reward *= 0.25

            with BANDIT_LOCK:
                state = _load_bandit_state(len(feature_vector))
                state["total_rounds"] = int(state.get("total_rounds", 0)) + 1
                _bandit_update(
                    state=state,
                    arm=chosen_source,
                    features=feature_vector,
                    reward=reward,
                    predicted_exploit=predicted_exploit,
                    observed_latency=observed_latency,
                    observed_cost=observed_cost,
                    uncertainty_pressure=uncertainty_pressure,
                )
                constraint_debug = _update_constraints(
                    state=state,
                    observed_latency=observed_latency,
                    observed_cost=observed_cost,
                )
                drift_debug = _update_drift_and_maybe_reset(state=state, reward=reward)
                _save_bandit_state(state)

            bandit_debug["reward"] = round(reward, 4)
            bandit_debug["reward_quality"] = round(reward_quality, 4)
            bandit_debug["penalty"] = round(penalty, 4)
            bandit_debug["risk_bonus"] = round(risk_bonus, 4)
            bandit_debug["predicted_exploit"] = round(predicted_exploit, 4)
            bandit_debug["observed_latency"] = round(observed_latency, 4)
            bandit_debug["observed_cost"] = round(observed_cost, 4)
            bandit_debug["constraints"] = constraint_debug
            bandit_debug["drift"] = drift_debug

            propensity_map = (bandit_debug.get("selection") or {}).get("propensity") or {}
            chosen_propensity = float(propensity_map.get(chosen_source, 0.0))
            _append_ope_event(
                {
                    "timestamp": time.time(),
                    "state_path": _bandit_state_path(),
                    "chosen_arm": chosen_source,
                    "propensity": round(chosen_propensity, 6),
                    "reward": round(float(reward), 6),
                    "reward_quality": round(float(reward_quality), 6),
                    "penalty": round(float(penalty), 6),
                    "risk_bonus": round(float(risk_bonus), 6),
                    "effective": bool(effective),
                    "score_gain": round(float(score_gain), 6),
                    "rel_anchor_gain": round(float(rel_anchor_gain), 6),
                    "novelty_gain": round(float(novelty_gain), 6),
                    "structure_gain": round(float(structure_gain), 6),
                    "observed_latency": round(float(observed_latency), 6),
                    "observed_cost": round(float(observed_cost), 6),
                    "feature_vector": [round(float(v), 6) for v in feature_vector],
                    "defect_graph": defect_graph,
                    "policy_scores": selection_scores,
                    "constraint_debug": constraint_debug,
                    "drift_debug": drift_debug,
                }
            )

        if not effective:
            return {
                "success": True,
                "error": "controller_outline_not_effective",
                "improved_outline": baseline_outline,
                "source": chosen_source,
                "changed": changed,
                "effective": False,
                "baseline_score": baseline_score,
                "selected_score": (chosen["score"] if chosen else baseline_score),
                "selection_min_gain": min_gain,
                "selection_rel_anchor_gain": round(rel_anchor_gain, 4),
                "selection_novelty_gain": round(novelty_gain, 4),
                "selection_structure_gain": round(structure_gain, 4),
                "selection_rel_gain_ok": rel_gain_ok,
                "selection_novelty_gain_ok": novelty_gain_ok,
                "llm_error": llm_error,
                "defect_graph": defect_graph,
                "bandit": bandit_debug,
                "candidate_scores": [
                    {
                        "source": c["source"],
                        "total": c["score"]["total"],
                        "relevance_anchor": c["score"]["relevance_anchor"],
                        "novelty": c["score"]["novelty"],
                        "structure": c["score"]["structure"],
                    }
                    for c in dedup_candidates
                ],
            }

        # 改进成功后写回数据库
        _save_improved_outline_to_db(
            document_id=req.document_id,
            section_id=req.section_id,
            subsection_id=req.subsection_id,
            improved_outline=improved_outline,
            iteration_count=iteration,
        )

        return {
            "success": True,
            "improved_outline": improved_outline,
            "source": chosen_source,
            "changed": changed,
            "effective": True,
            "baseline_score": baseline_score,
            "selected_score": (chosen["score"] if chosen else baseline_score),
            "selection_min_gain": min_gain,
            "selection_rel_anchor_gain": round(rel_anchor_gain, 4),
            "selection_novelty_gain": round(novelty_gain, 4),
            "selection_structure_gain": round(structure_gain, 4),
            "selection_rel_gain_ok": rel_gain_ok,
            "selection_novelty_gain_ok": novelty_gain_ok,
            "llm_error": llm_error,
            "defect_graph": defect_graph,
            "bandit": bandit_debug,
            "candidate_scores": [
                {
                    "source": c["source"],
                    "total": c["score"]["total"],
                    "relevance_anchor": c["score"]["relevance_anchor"],
                    "novelty": c["score"]["novelty"],
                    "structure": c["score"]["structure"],
                }
                for c in dedup_candidates
            ],
            "recommendations": [
                f"相关性分数: {rel_score:.4f}",
                f"冗余度分数: {red_score:.4f}",
                f"反馈: {feedback_text}"
            ]
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": str(e)
        }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv('PORT', 8001))
    print(f"\n🚀 FlowerNet Controller 启动在 http://0.0.0.0:{port}")
    print(f"📖 API 文档: http://localhost:{port}/docs")
    uvicorn.run(app, host="0.0.0.0", port=port)