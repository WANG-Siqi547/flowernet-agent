from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Dict, Any, Optional, Tuple
import os
import sys

_SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_SERVICE_DIR)
for _path in (_SERVICE_DIR, _ROOT_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from controler import FlowerNetController
import requests
import re
import time
from collections import Counter
import json
import random
import threading
import math

try:
    from flowernet_trained_models import (
        load_json_model,
        predict_controller_arm_prior,
        resolve_model_path,
    )
except Exception:
    load_json_model = None  # type: ignore
    predict_controller_arm_prior = None  # type: ignore
    resolve_model_path = None  # type: ignore

app = FastAPI(title="FlowerNet Controller API")

# 初始化 Controller
controller = FlowerNetController()

outliner_url = None
BANDIT_LOCK = threading.Lock()
TRAINED_POLICY_CACHE: Dict[str, Any] = {"path": "", "mtime": 0.0, "model": None}


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
            "llm": {"count": 0, "weights": [0.0] * feature_dim, "bias": 0.0, "avg_latency": 0.0, "avg_cost": 0.0, "ineffective_streak": 0},
            "rule": {"count": 0, "weights": [0.0] * feature_dim, "bias": 0.0, "avg_latency": 0.0, "avg_cost": 0.0, "ineffective_streak": 0},
            "rule_structured": {"count": 0, "weights": [0.0] * feature_dim, "bias": 0.0, "avg_latency": 0.0, "avg_cost": 0.0, "ineffective_streak": 0},
            "defect_topic": {"count": 0, "weights": [0.0] * feature_dim, "bias": 0.0, "avg_latency": 0.0, "avg_cost": 0.0, "ineffective_streak": 0},
            "defect_evidence": {"count": 0, "weights": [0.0] * feature_dim, "bias": 0.0, "avg_latency": 0.0, "avg_cost": 0.0, "ineffective_streak": 0},
            "defect_structure": {"count": 0, "weights": [0.0] * feature_dim, "bias": 0.0, "avg_latency": 0.0, "avg_cost": 0.0, "ineffective_streak": 0},
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


def _trained_controller_policy_path() -> str:
    raw = os.getenv("CONTROLLER_TRAINED_POLICY_PATH", os.path.join("models", "controller_policy.json"))
    if resolve_model_path is not None:
        return resolve_model_path(raw, "controller_policy.json")
    return raw if os.path.isabs(raw) else _project_root_path(raw)


def _load_trained_controller_policy() -> Optional[Dict[str, Any]]:
    if os.getenv("CONTROLLER_TRAINED_POLICY_ENABLED", "true").lower() != "true":
        return None
    if load_json_model is None:
        return None
    path = _trained_controller_policy_path()
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        return None
    if str(TRAINED_POLICY_CACHE.get("path") or "") == path and abs(float(TRAINED_POLICY_CACHE.get("mtime") or 0.0) - mtime) < 1e-9:
        cached = TRAINED_POLICY_CACHE.get("model")
        return cached if isinstance(cached, dict) else None
    model = load_json_model(path, "controller_policy")
    TRAINED_POLICY_CACHE.update({"path": path, "mtime": mtime, "model": model})
    if model:
        print(f"✅ Loaded trained controller policy: {path}")
    return model


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


def _build_dimension_guidance(feedback: Dict[str, Any]) -> List[str]:
    """把 Verifier 的失败维度转换成可直接写入改纲 prompt 的约束文本。"""
    if not isinstance(feedback, dict):
        return []

    failed_dims = feedback.get("quality_dimensions_failed")
    if not isinstance(failed_dims, list):
        failed_dims = []

    checks = feedback.get("quality_dimensions_check") if isinstance(feedback.get("quality_dimensions_check"), dict) else {}
    thresholds = feedback.get("dimension_thresholds") if isinstance(feedback.get("dimension_thresholds"), dict) else {}
    dims = feedback.get("quality_dimensions") if isinstance(feedback.get("quality_dimensions"), dict) else {}

    dimension_messages = {
        "topic_alignment": "主题对齐不足：重新强化该小节的中心论点、关键定义和必须回答的问题，避免泛化叙述。",
        "coverage_completeness": "覆盖不完整：补全大纲中缺失的关键子点、流程步骤、约束条件或对比维度。",
        "logical_coherence": "逻辑连贯性不足：按因果/递进/问题-解决结构重排小节层级，并显式要求 Claim→Evidence→Reasoning→Transition→Implication。",
        "evidence_grounding": "证据接地性不足：明确要求加入可验证事实、引用、示例或数据支撑，避免空泛结论。",
        "novelty": "新颖性不足：要求引入新的角度、反例、比较对象或未覆盖的信息，避免重复前文。",
        "structure_clarity": "结构清晰度不足：要求使用清晰的小标题、分点或步骤式结构，增强可读性和条理性。",
    }

    guidance: List[str] = []
    for dim in failed_dims:
        dim_name = str(dim)
        value = dims.get(dim_name)
        threshold = thresholds.get(dim_name)
        check = checks.get(dim_name) if isinstance(checks, dict) else {}
        if not isinstance(check, dict):
            check = {}
        if dim_name in dimension_messages:
            if isinstance(value, (int, float)) and isinstance(threshold, (int, float)):
                guidance.append(
                    f"- {dim_name}: {dimension_messages[dim_name]} 当前值={float(value):.4f}，阈值={float(threshold):.4f}，margin={float(check.get('margin', float(value) - float(threshold))):.4f}。"
                )
            else:
                guidance.append(f"- {dim_name}: {dimension_messages[dim_name]}")

    # Persona consistency guidance (if verifier provides it)
    persona_check = feedback.get("persona_check") if isinstance(feedback.get("persona_check"), dict) else {}
    persona_passed = bool(feedback.get("persona_passed", True))
    persona_threshold = float(feedback.get("persona_threshold", 0.0) or 0.0)
    persona_similarity = float(persona_check.get("similarity", 0.0) or 0.0) if persona_check else 0.0
    if persona_check and not persona_passed:
        guidance.append(
            f"- persona_consistency: 风格一致性不足，要求生成文本严格贴合指定 persona 语气、术语和叙述方式。当前={persona_similarity:.4f}，阈值={persona_threshold:.4f}。"
        )

    # Stronger coherence repairs when this dimension fails.
    if "logical_coherence" in failed_dims:
        guidance.append("- coherence_template: 每段需包含『主张句 + 证据句 + 推理句』最小结构，段尾添加过渡句连接下一段。")
        guidance.append("- transition_requirement: 每个小节至少使用 1 个显式过渡词（例如：因此/然而/此外/总之）。")
        guidance.append("- unsupported_claim_guard: 出现强结论词（必须/证明/it is clear）时，必须绑定可验证事实或引用。")

    coverage_diag = feedback.get("coverage_diagnostics") if isinstance(feedback.get("coverage_diagnostics"), dict) else {}
    if coverage_diag:
        missing_terms = [str(x) for x in coverage_diag.get("missing_terms", []) if str(x).strip()][:10]
        missing_aspects = [str(x) for x in coverage_diag.get("missing_aspects", []) if str(x).strip()][:6]
        if missing_terms:
            guidance.append("- targeted_coverage_terms: 下一版必须自然覆盖这些缺失主题词，并把它们写成具体论点而不是词表：" + "、".join(missing_terms) + "。")
        if missing_aspects:
            guidance.append("- targeted_coverage_aspects: 下一版必须补齐这些内容面向：" + "、".join(missing_aspects) + "。")

    evidence_diag = feedback.get("evidence_diagnostics") if isinstance(feedback.get("evidence_diagnostics"), dict) else {}
    if evidence_diag:
        missing_types = [str(x) for x in evidence_diag.get("missing_evidence_types", []) if str(x).strip()][:6]
        source_terms = [str(x) for x in evidence_diag.get("source_topic_terms", []) if str(x).strip()][:8]
        failures = [str(x) for x in evidence_diag.get("source_failures", []) if str(x).strip()][:5]
        if missing_types:
            guidance.append("- targeted_evidence_types: 下一版必须补齐这些证据类型：" + "、".join(missing_types) + "。")
        if source_terms:
            guidance.append("- source_grounding_terms: 优先围绕检索来源中的这些主题词构造可验证论点：" + "、".join(source_terms) + "。")
        if failures:
            guidance.append("- source_failure_repair: 修复这些来源问题：" + "、".join(failures) + "；不要编造来源或继续引用跨域来源。")

    return guidance


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
    epsilon = max(0.0, min(0.5, float(os.getenv("CONTROLLER_BANDIT_EPSILON", "0.15"))))
    explore_c = max(0.0, float(os.getenv("CONTROLLER_BANDIT_EXPLORE_C", "0.12")))
    cooldown_streak = max(1, int(os.getenv("CONTROLLER_ARM_COOLDOWN_STREAK", "2")))
    min_alt_ratio = max(0.0, min(1.0, float(os.getenv("CONTROLLER_COOLDOWN_MIN_ALT_SCORE_RATIO", "0.55"))))
    constraints = state.get("constraints") if isinstance(state.get("constraints"), dict) else {}
    lambda_latency = float(constraints.get("lambda_latency", 0.0))
    lambda_cost = float(constraints.get("lambda_cost", 0.0))
    trained_policy = _load_trained_controller_policy()
    trained_blend = max(0.0, min(1.0, float(os.getenv("CONTROLLER_TRAINED_POLICY_BLEND", "0.35"))))

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
        trained_prior = None
        trained_bonus = 0.0
        if trained_policy and predict_controller_arm_prior is not None:
            trained_prior = predict_controller_arm_prior(trained_policy, arm, features)
            if trained_prior is not None:
                trained_bonus = trained_blend * float(trained_prior)
        constrained_total = total - latency_penalty - cost_penalty + alignment_bonus + trained_bonus

        scores[arm] = {
            "total": round(constrained_total, 4),
            "base_total": round(total, 4),
            "exploit": round(exploit, 4),
            "explore": round(explore, 4),
            "latency_penalty": round(latency_penalty, 4),
            "cost_penalty": round(cost_penalty, 4),
            "alignment_bonus": round(alignment_bonus, 4),
            "trained_prior": round(float(trained_prior), 4) if trained_prior is not None else None,
            "trained_bonus": round(trained_bonus, 4),
            "avg_latency": round(avg_latency, 4),
            "avg_cost": round(avg_cost, 4),
            "count": int(arm_state.get("count", 0)),
        }

    if not scores:
        return available_arms[0], {"mode": "fallback", "scores": {}}

    drift = state.get("drift") if isinstance(state.get("drift"), dict) else {}
    recent_drift = int(state.get("total_rounds", 0) or 0) - int(drift.get("last_drift_round", 0) or 0) <= 3
    effective_epsilon = min(0.5, epsilon * (2.0 if recent_drift else 1.0))

    if random.random() < effective_epsilon:
        chosen_arm = random.choice(list(scores.keys()))
        mode = "drift_recover" if recent_drift else "epsilon_explore"
    else:
        ranked = sorted(scores.keys(), key=lambda a: scores[a]["total"], reverse=True)
        chosen_arm = ranked[0]
        mode = "score_exploit"
        chosen_state = state.get("arms", {}).get(chosen_arm, {}) if isinstance(state.get("arms"), dict) else {}
        ineffective_streak = int(chosen_state.get("ineffective_streak", 0) or 0) if isinstance(chosen_state, dict) else 0
        if ineffective_streak >= cooldown_streak and len(ranked) > 1:
            best_total = max(1e-6, float(scores[chosen_arm]["total"]))
            for alt in ranked[1:]:
                alt_state = state.get("arms", {}).get(alt, {}) if isinstance(state.get("arms"), dict) else {}
                alt_streak = int(alt_state.get("ineffective_streak", 0) or 0) if isinstance(alt_state, dict) else 0
                if alt_streak < cooldown_streak and float(scores[alt]["total"]) >= best_total * min_alt_ratio:
                    chosen_arm = alt
                    mode = "cooldown_shift"
                    break

    probs = _selection_probabilities(scores=scores, epsilon=effective_epsilon)
    return chosen_arm, {
        "mode": mode,
        "scores": scores,
        "propensity": probs,
        "epsilon": round(effective_epsilon, 4),
        "cooldown_streak": cooldown_streak,
        "trained_policy_used": bool(trained_policy),
        "trained_policy_blend": trained_blend if trained_policy else 0.0,
        "trained_policy_version": trained_policy.get("version", "") if trained_policy else "",
    }


def _bandit_update(
    state: Dict[str, Any],
    arm: str,
    features: List[float],
    reward: float,
    predicted_exploit: float,
    observed_latency: float,
    observed_cost: float,
    uncertainty_pressure: float,
    effective: bool = True,
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
    weak_reward = reward < float(os.getenv("CONTROLLER_ARM_WEAK_REWARD_THRESHOLD", "0.025"))
    arm_state["ineffective_streak"] = 0 if effective and not weak_reward else int(arm_state.get("ineffective_streak", 0) or 0) + 1


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


def _normalize_outline_for_compare(text: str) -> str:
    """Normalize outline text for semantic-equivalent comparison."""
    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    return normalized.lower()


def _coherence_outline_signal(outline: str) -> float:
    """Estimate whether an outline is likely to produce coherent prose.

    Signals:
    - has explicit argument chain markers (claim/evidence/reasoning/transition)
    - has transition connectors
    - has stepwise structure
    """
    text = str(outline or "")
    lower = text.lower()

    claim_hit = 1.0 if re.search(r"主张|核心结论|结论|claim|thesis", lower) else 0.0
    evidence_hit = 1.0 if re.search(r"证据|数据|案例|引用|evidence|citation|data|example", lower) else 0.0
    reasoning_hit = 1.0 if re.search(r"推理|原因|机制|because|reasoning|rationale", lower) else 0.0
    implication_hit = 1.0 if re.search(r"小结|启示|结论延伸|implication|takeaway", lower) else 0.0

    transition_count = len(re.findall(r"因此|然而|此外|总之|同时|所以|therefore|however|moreover|in conclusion|meanwhile", lower))
    transition_score = min(1.0, transition_count / 4.0)

    step_markers = len(re.findall(r"(^|\n)\s*(\d+[\.|\)]|[①②③④⑤]|first|second|third|finally)", lower))
    step_score = min(1.0, step_markers / 3.0)

    chain_score = (claim_hit + evidence_hit + reasoning_hit + implication_hit) / 4.0
    return _clip01(0.45 * chain_score + 0.30 * transition_score + 0.25 * step_score)


def _evidence_outline_signal(outline: str) -> float:
    """Estimate whether an outline can realistically repair weak grounding.

    A useful evidence repair outline should not merely say "add evidence"; it
    should specify claim slots, acceptable evidence types, citation placement,
    and anti-hallucination constraints. These features are cheap to score and
    make the bandit reward reflect the actual generator-facing repair quality.
    """
    text = str(outline or "")
    lower = text.lower()

    claim_slot = 1.0 if re.search(r"主张|核心论点|claim|thesis|待验证命题", lower) else 0.0
    evidence_slot = 1.0 if re.search(r"证据槽|证据计划|evidence slot|source slot|来源槽", lower) else 0.0
    citation_slot = 1.0 if re.search(r"引用位置|引用标记|citation|\\[source\\]|\\[ref\\]|\\[来源\\]", lower) else 0.0
    source_type = 1.0 if re.search(r"论文|报告|数据集|基准|实验|统计|案例|可靠来源|retrieved source|source_results", lower) else 0.0
    guard = 1.0 if re.search(r"不得编造|不可虚构|缺少来源|无来源则|hallucination|unsupported", lower) else 0.0

    numbered_slots = len(re.findall(r"证据槽\s*[A-Z0-9一二三四五六七八九十]*|evidence slot", lower))
    slot_score = min(1.0, numbered_slots / 3.0)

    return _clip01(
        0.18 * claim_slot
        + 0.22 * evidence_slot
        + 0.18 * citation_slot
        + 0.16 * source_type
        + 0.16 * guard
        + 0.10 * slot_score
    )


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
    feedback: Optional[Dict[str, Any]] = None,
    defect_graph: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    anchors = _extract_anchor_terms(original_outline)
    relevance_anchor = _keyword_coverage(candidate_outline, anchors)
    similarity_to_working = 1.0 - _token_overlap_ratio(candidate_outline, working_outline)

    history_text = "\n".join(history[-3:]) if history else ""
    overlap_history = _token_overlap_ratio(candidate_outline, history_text) if history_text else 0.0
    overlap_failed = _token_overlap_ratio(candidate_outline, failed_draft)
    novelty = 1.0 - min(1.0, 0.65 * overlap_history + 0.35 * overlap_failed)

    structure = _structure_score(candidate_outline)
    coherence_signal = _coherence_outline_signal(candidate_outline)
    evidence_signal = _evidence_outline_signal(candidate_outline)

    rel_gap = max(0.0, rel_threshold - rel_score)
    red_gap = max(0.0, red_score - red_threshold)

    defect_graph = defect_graph or {}
    coherence_need = _clip01(float(defect_graph.get("coherence", 0.0)))
    topic_need = _clip01(max(rel_gap, float(defect_graph.get("topic", 0.0))))
    novelty_need = _clip01(max(red_gap, float(defect_graph.get("redundancy", 0.0))))
    structure_need = _clip01(float(defect_graph.get("structure", 0.0)))
    evidence_need = _clip01(float(defect_graph.get("evidence", 0.0)))

    # Dynamic weights to align Controller objective with Verifier failures.
    rel_weight = 0.32 + 0.20 * topic_need
    novelty_weight = 0.14 + 0.18 * novelty_need
    structure_weight = 0.12 + 0.16 * structure_need
    coherence_weight = 0.18 + 0.28 * coherence_need
    evidence_weight = 0.12 + 0.30 * evidence_need
    delta_weight = 0.06

    # If verifier explicitly failed logical_coherence, push more weight to coherence optimization.
    failed_dims = feedback.get("quality_dimensions_failed") if isinstance(feedback, dict) and isinstance(feedback.get("quality_dimensions_failed"), list) else []
    if "logical_coherence" in failed_dims:
        coherence_weight += 0.10
        rel_weight = max(0.20, rel_weight - 0.05)
        novelty_weight = max(0.10, novelty_weight - 0.03)
    if "evidence_grounding" in failed_dims:
        evidence_weight += 0.12
        structure_weight += 0.03
    if topic_need > evidence_need + 0.15:
        # When topic drift is the dominant defect, do not let evidence planning
        # overwhelm the repair objective. Evidence still matters, but topic
        # recovery must come first.
        evidence_weight *= 0.35
        rel_weight += 0.18
        coherence_weight += 0.04

    total_weight = max(1e-6, rel_weight + novelty_weight + structure_weight + coherence_weight + evidence_weight + delta_weight)
    rel_weight /= total_weight
    novelty_weight /= total_weight
    structure_weight /= total_weight
    coherence_weight /= total_weight
    evidence_weight /= total_weight
    delta_weight /= total_weight

    total = (
        rel_weight * relevance_anchor
        + novelty_weight * novelty
        + structure_weight * structure
        + coherence_weight * coherence_signal
        + evidence_weight * evidence_signal
        + delta_weight * similarity_to_working
    )

    return {
        "total": round(total, 4),
        "relevance_anchor": round(relevance_anchor, 4),
        "novelty": round(novelty, 4),
        "structure": round(structure, 4),
        "coherence_signal": round(coherence_signal, 4),
        "evidence_signal": round(evidence_signal, 4),
        "delta_from_working": round(similarity_to_working, 4),
        "weights": {
            "relevance_anchor": round(rel_weight, 4),
            "novelty": round(novelty_weight, 4),
            "structure": round(structure_weight, 4),
            "coherence_signal": round(coherence_weight, 4),
            "evidence_signal": round(evidence_weight, 4),
            "delta_from_working": round(delta_weight, 4),
        },
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


@app.get("/health")
@app.get("/health/live")
def health_check():
    """Lightweight health endpoint for Render and upstream service probes."""
    return {"status": "ok", "service": "flowernet-controller"}


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

        dimension_guidance = _build_dimension_guidance(req.feedback)
        dimension_guidance_text = "\n".join(dimension_guidance) if dimension_guidance else "- 暂无明确失败维度，按笼统反馈进行最小修改。"
        coverage_diag = req.feedback.get("coverage_diagnostics") if isinstance(req.feedback.get("coverage_diagnostics"), dict) else {}
        evidence_diag = req.feedback.get("evidence_diagnostics") if isinstance(req.feedback.get("evidence_diagnostics"), dict) else {}
        missing_terms = [str(x) for x in coverage_diag.get("missing_terms", []) if str(x).strip()][:10] if coverage_diag else []
        missing_aspects = [str(x) for x in coverage_diag.get("missing_aspects", []) if str(x).strip()][:6] if coverage_diag else []
        source_terms = [str(x) for x in evidence_diag.get("source_topic_terms", []) if str(x).strip()][:8] if evidence_diag else []
        missing_evidence_types = [str(x) for x in evidence_diag.get("missing_evidence_types", []) if str(x).strip()][:6] if evidence_diag else []
        targeted_gap_text = "\n".join(
            line for line in [
                ("- 缺失主题词：" + "、".join(missing_terms)) if missing_terms else "",
                ("- 缺失内容面向：" + "、".join(missing_aspects)) if missing_aspects else "",
                ("- 检索来源主题锚点：" + "、".join(source_terms)) if source_terms else "",
                ("- 缺失证据类型：" + "、".join(missing_evidence_types)) if missing_evidence_types else "",
            ]
            if line
        ) or "- 暂无结构化缺口。"

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

【失败维度定向修复建议】
{dimension_guidance_text}

【结构化缺口（必须转化为正文扩写计划，不要原样堆词）】
{targeted_gap_text}

{history_context}
【改进要求】
{"1. 相关性不足（" + str(round(rel_score,4)) + " < " + str(rel_threshold) + "）：大纲要更明确、具体，强调该小节的核心主题，列出必须涵盖的关键点，确保每个写作要点都与主题直接相关。" if rel_score < rel_threshold else "1. 相关性已满足，保持当前主题聚焦度。"}
{"2. 冗余度过高（" + str(round(red_score,4)) + " > " + str(red_threshold) + "）：大纲中必须明确指出哪些角度/信息已被前文覆盖，要求写全新的视角，可以列出具体的禁止重复方向。" if red_score > red_threshold else "2. 冗余度已满足，保持现有差异化要求。"}
3. 你不是格式修补器，而是专业编辑：优先补内容覆盖、证据支撑、论证深度和主题专属性；只有必要时才调整格式。
4. 输出的大纲必须包含 Targeted Expansion Plan：列出 2-3 个最关键的新增论点，每个论点绑定一个证据槽和一个避免重复的说明；不要把所有缺口机械扩写成超长章节。

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
        if missing_terms:
            fallback_lines.append("Targeted Expansion Plan：必须把这些缺失主题词转化为具体论点：" + "、".join(missing_terms) + "。")
        if missing_aspects:
            fallback_lines.append("Targeted Expansion Plan：必须补齐这些内容面向：" + "、".join(missing_aspects) + "。")
        if missing_evidence_types:
            fallback_lines.append("Evidence Plan：必须补齐这些证据类型：" + "、".join(missing_evidence_types) + "。")
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
        if missing_terms:
            structured_blocks.append("【缺失主题词扩写】" + "、".join(missing_terms))
        if missing_aspects:
            structured_blocks.append("【必须补齐的内容面向】" + "、".join(missing_aspects))
        if repeated_terms:
            structured_blocks.append("【禁止重复词】" + "、".join(repeated_terms))
        if red_score > red_threshold:
            structured_blocks.append("【差异化要求】每个要点至少包含一个新的事实、案例、数据或机制说明。")
        structured_blocks.append("【专业编辑要求】不要只增加格式；每一处修改都必须带来新的主题信息、证据或推理。")
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
                    "",
                    "【主题恢复 / Topic Recovery】优先修复 topic_alignment 与 coverage_completeness。",
                    "【原始主题锁定】" + (original_outline[:260] if original_outline else "保持当前小节主题"),
                    "- 核心论点：第一段必须直接回答原始小节标题，不得改写成泛泛背景介绍。",
                    "- 主题锚点：每个段落至少包含 1 个原始主题关键词，并围绕该关键词给出结论句。",
                    "- 覆盖清单：按“定义/机制 -> 代表方法 -> 应用场景 -> 评价指标 -> 风险边界 -> 未来方向”补齐缺失点。",
                    "- 缺失主题词：" + ("、".join(missing_terms) if missing_terms else "从原始大纲中提取至少 5 个具体术语"),
                    "- 缺失内容面向：" + ("、".join(missing_aspects) if missing_aspects else "根据大纲自行判断缺失的内容面向"),
                    "- 证据约束：只有在主题句完成后才插入证据，不允许证据主题替代本小节主题。",
                    "- 反漂移约束：禁止转向与原始小节无关的金融、心理、软件可靠性泛化案例。",
                ]
            )
        )
        evidence_anchor_tokens: List[str] = []
        for _token in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_-]{2,}", (original_outline + "\n" + working_outline)):
            if _token not in evidence_anchor_tokens:
                evidence_anchor_tokens.append(_token)
            if len(evidence_anchor_tokens) >= 10:
                break
        defect_evidence_outline = _sanitize_outline_text(
            "\n".join(
                [
                    working_outline or original_outline,
                    "",
                    "【主题锁定】本轮证据修复不得改变原小节主题：" + (original_outline[:220] if original_outline else "保持当前小节主题"),
                    "【证据计划 / Evidence Plan】优先修复 evidence_grounding，不得只写抽象观点。",
                    "- 主张槽 Claim A：用 1 句话写出本小节最核心、可被证据支持的论点，并绑定至少 2 个主题锚点。",
                    "- 证据槽 Evidence Slot 1：从已检索/可用来源中寻找论文、报告、数据集、基准实验或可靠案例；在正文对应句后插入引用位置 [Source]。",
                    "- 证据槽 Evidence Slot 2：补充一个机制性证据或工程案例，说明该主张如何发生、在哪些条件下成立。",
                    "- 证据槽 Evidence Slot 3：加入一个限制/反例/边界条件，避免把局部证据扩大成普遍结论。",
                    "- 推理槽 Reasoning：每个证据后必须写 1 句“证据如何支撑主张”的解释，而不是只堆引用。",
                    "- 缺失证据类型：" + ("、".join(missing_evidence_types) if missing_evidence_types else "至少包含方法/模型、实证或基准、应用案例、风险边界中的两类"),
                    "- 检索来源主题锚点：" + ("、".join(source_terms) if source_terms else "使用当前来源中与主题最相关的术语"),
                    "- 防幻觉约束：不得编造作者、年份、DOI、数值或不存在的论文；缺少来源时必须改写为谨慎表述并标注需要进一步验证。",
                    "- 引用位置：每个关键段落至少预留 1 个引用标记位置，不把所有引用集中到段末。",
                    "- 主题锚点：" + ("、".join(evidence_anchor_tokens) if evidence_anchor_tokens else "保持原主题关键词"),
                ]
            )
        )
        baseline_outline = working_outline or original_outline
        topic_anchor_tokens: List[str] = []
        for _token in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_-]{2,}", baseline_outline or original_outline or ""):
            if _token not in topic_anchor_tokens:
                topic_anchor_tokens.append(_token)
            if len(topic_anchor_tokens) >= 8:
                break
        defect_structure_outline = _sanitize_outline_text(
            "\n".join(
                [
                    baseline_outline,
                    "",
                    "【结构化修复约束】",
                    "- 保留并强化上方原小节主题，不得改写为通用模板。",
                    "- 第一段给出本小节的核心论点，并直接回应原大纲标题。",
                    "- 中间段按“概念/机制 -> 证据或案例 -> 分析推理 -> 局限或边界”展开。",
                    "- 每个段落至少包含一个与原主题强相关的关键词、事实或可验证论据。",
                    "- 结尾只做本小节范围内的小结，并说明与前后小节的信息增量。",
                    "- 主题锚点：" + ("、".join(topic_anchor_tokens) if topic_anchor_tokens else "保持原主题关键词"),
                ]
            )
        )
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
            feedback=req.feedback,
            defect_graph=defect_graph,
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
                feedback=req.feedback,
                defect_graph=defect_graph,
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
        min_coherence_gain = float(os.getenv("CONTROLLER_MIN_COHERENCE_GAIN", "0.03"))
        min_evidence_gain = float(os.getenv("CONTROLLER_MIN_EVIDENCE_GAIN", "0.05"))

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
            best_candidate_by_source: Dict[str, Dict[str, Any]] = {}
            for cand in dedup_candidates:
                src = cand["source"]
                if src not in best_candidate_by_source or cand["score"]["total"] > best_candidate_by_source[src]["score"]["total"]:
                    best_candidate_by_source[src] = cand
            if bandit_enabled:
                available_arms = [
                    arm
                    for arm in ["llm", "rule", "rule_structured", "defect_topic", "defect_evidence", "defect_structure"]
                    if arm in best_candidate_by_source
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
                    chosen = best_candidate_by_source[selected_arm]
                    chosen_source = selected_arm
                    bandit_debug["selection"] = selection_debug
                    bandit_debug["selected_arm"] = selected_arm
                else:
                    chosen = max(dedup_candidates, key=lambda x: x["score"]["total"])
                    chosen_source = chosen["source"]
            else:
                chosen = max(dedup_candidates, key=lambda x: x["score"]["total"])
                chosen_source = chosen["source"]

            # Risk-sensitive override: when evidence grounding is the dominant
            # failure mode, do not let historical bandit inertia pick a weaker
            # generic structure repair over a substantially better evidence plan.
            failed_dims_for_override = req.feedback.get("quality_dimensions_failed") if isinstance(req.feedback.get("quality_dimensions_failed"), list) else []
            evidence_override_margin = float(os.getenv("CONTROLLER_EVIDENCE_OVERRIDE_MARGIN", "0.08"))
            evidence_candidate = best_candidate_by_source.get("defect_evidence")
            chosen_total = float((chosen or {}).get("score", {}).get("total", 0.0)) if chosen else 0.0
            if (
                evidence_candidate
                and ("evidence_grounding" in failed_dims_for_override or float(defect_graph.get("evidence", 0.0)) >= 0.55)
                and (
                    float(defect_graph.get("evidence", 0.0)) >= float(defect_graph.get("topic", 0.0)) + 0.05
                    or rel_score >= rel_threshold * 0.85
                )
                and float(evidence_candidate["score"].get("total", 0.0)) >= chosen_total + evidence_override_margin
            ):
                chosen = evidence_candidate
                chosen_source = "defect_evidence"
                bandit_debug["override"] = {
                    "reason": "dominant_evidence_defect",
                    "previous_arm": bandit_debug.get("selected_arm", ""),
                    "previous_total": round(chosen_total, 4),
                    "override_total": evidence_candidate["score"].get("total", 0.0),
                    "margin": evidence_override_margin,
                }
                bandit_debug["selected_arm"] = "defect_evidence"

            topic_candidate = best_candidate_by_source.get("defect_topic")
            topic_override_margin = float(os.getenv("CONTROLLER_TOPIC_OVERRIDE_MARGIN", "0.10"))
            chosen_total = float((chosen or {}).get("score", {}).get("total", 0.0)) if chosen else 0.0
            topic_dominant = bool(
                topic_candidate
                and (
                    "topic_alignment" in failed_dims_for_override
                    or rel_score < rel_threshold * 0.82
                    or float(defect_graph.get("topic", 0.0)) >= float(defect_graph.get("evidence", 0.0)) + 0.10
                )
            )
            force_topic_recovery = bool(
                topic_candidate
                and (
                    rel_score < rel_threshold * 0.70
                    or "topic_alignment" in failed_dims_for_override
                    or float(defect_graph.get("topic", 0.0)) >= float(defect_graph.get("evidence", 0.0)) + 0.05
                )
            )
            if (
                topic_candidate
                and topic_dominant
                and (
                    force_topic_recovery
                    or float(topic_candidate["score"].get("total", 0.0)) >= chosen_total - topic_override_margin
                )
            ):
                chosen = topic_candidate
                chosen_source = "defect_topic"
                bandit_debug["override"] = {
                    "reason": "dominant_topic_defect",
                    "previous_arm": bandit_debug.get("selected_arm", ""),
                    "previous_total": round(chosen_total, 4),
                    "override_total": topic_candidate["score"].get("total", 0.0),
                    "margin": topic_override_margin,
                }
                bandit_debug["selected_arm"] = "defect_topic"

            # Final guard: overrides can still keep selecting an arm that has
            # just failed repeatedly. Shift to the best viable alternative so
            # controller repairs do not spiral into the same low-yield outline.
            if bandit_enabled and chosen and best_candidate_by_source:
                try:
                    with BANDIT_LOCK:
                        cooldown_state = _load_bandit_state(len(feature_vector))
                    cooldown_streak = max(1, int(os.getenv("CONTROLLER_ARM_COOLDOWN_STREAK", "2")))
                    min_alt_ratio = max(0.0, min(1.0, float(os.getenv("CONTROLLER_COOLDOWN_MIN_ALT_SCORE_RATIO", "0.55"))))
                    arm_state = (cooldown_state.get("arms") or {}).get(chosen_source, {})
                    ineffective_streak = int(arm_state.get("ineffective_streak", 0) or 0) if isinstance(arm_state, dict) else 0
                    if ineffective_streak >= cooldown_streak and len(best_candidate_by_source) > 1:
                        current_total = max(1e-6, float((chosen or {}).get("score", {}).get("total", 0.0)))
                        alternatives = sorted(
                            [
                                cand for arm, cand in best_candidate_by_source.items()
                                if arm != chosen_source
                            ],
                            key=lambda cand: float(cand.get("score", {}).get("total", 0.0)),
                            reverse=True,
                        )
                        for alt in alternatives:
                            alt_source = str(alt.get("source", ""))
                            alt_state = (cooldown_state.get("arms") or {}).get(alt_source, {})
                            alt_streak = int(alt_state.get("ineffective_streak", 0) or 0) if isinstance(alt_state, dict) else 0
                            if alt_streak < cooldown_streak and float(alt.get("score", {}).get("total", 0.0)) >= current_total * min_alt_ratio:
                                bandit_debug["cooldown_override"] = {
                                    "reason": "selected_arm_ineffective_streak",
                                    "previous_arm": chosen_source,
                                    "previous_ineffective_streak": ineffective_streak,
                                    "new_arm": alt_source,
                                    "new_total": alt.get("score", {}).get("total", 0.0),
                                }
                                chosen = alt
                                chosen_source = alt_source
                                bandit_debug["selected_arm"] = alt_source
                                bandit_debug.setdefault("selection", {})["mode"] = "cooldown_shift"
                                break
                except Exception as _cooldown_error:
                    bandit_debug["cooldown_error"] = str(_cooldown_error)[:180]

        changed = False
        score_gain = (chosen["score"]["total"] - baseline_score["total"]) if chosen else 0.0
        rel_anchor_gain = (chosen["score"].get("relevance_anchor", 0.0) - baseline_score.get("relevance_anchor", 0.0)) if chosen else 0.0
        novelty_gain = (chosen["score"].get("novelty", 0.0) - baseline_score.get("novelty", 0.0)) if chosen else 0.0
        structure_gain = (chosen["score"].get("structure", 0.0) - baseline_score.get("structure", 0.0)) if chosen else 0.0
        coherence_gain = (chosen["score"].get("coherence_signal", 0.0) - baseline_score.get("coherence_signal", 0.0)) if chosen else 0.0
        evidence_gain = (chosen["score"].get("evidence_signal", 0.0) - baseline_score.get("evidence_signal", 0.0)) if chosen else 0.0

        rel_needed = rel_score < rel_threshold
        red_needed = red_score > red_threshold
        coherence_needed = bool(defect_graph.get("coherence", 0.0) >= 0.25 or "logical_coherence" in (req.feedback.get("quality_dimensions_failed") if isinstance(req.feedback.get("quality_dimensions_failed"), list) else []))
        evidence_needed = bool(defect_graph.get("evidence", 0.0) >= 0.25 or "evidence_grounding" in (req.feedback.get("quality_dimensions_failed") if isinstance(req.feedback.get("quality_dimensions_failed"), list) else []))
        rel_gain_ok = (not rel_needed) or (rel_anchor_gain >= min_rel_anchor_gain) or (structure_gain >= min_structure_gain)
        novelty_gain_ok = (not red_needed) or (novelty_gain >= min_novelty_gain)
        coherence_gain_ok = (not coherence_needed) or (coherence_gain >= min_coherence_gain) or (chosen_source in ("rule_structured", "defect_structure"))
        evidence_gain_ok = (not evidence_needed) or (evidence_gain >= min_evidence_gain) or (chosen_source == "defect_evidence" and chosen and chosen["score"].get("evidence_signal", 0.0) >= 0.75)

        candidate_changed = bool(chosen and _normalize_outline_for_compare(chosen["outline"]) != _normalize_outline_for_compare(baseline_outline))
        if chosen and candidate_changed:
            improved_outline = chosen["outline"]
            chosen_source = chosen["source"]
            changed = True
        else:
            improved_outline = baseline_outline
        if chosen_source and not bandit_debug.get("selected_arm"):
            bandit_debug["selected_arm"] = chosen_source

        # 线上稳定性优先：只要输出确实发生变化，且不是明显退化，就允许进入下一轮。
        # 这样避免 Controller 因阈值过严而反复返回原纲，导致 Generator/Controller 死循环。
        effective = bool(
            chosen
            and changed
            and (
                (
                    score_gain >= min_gain
                    and rel_gain_ok
                    and novelty_gain_ok
                    and coherence_gain_ok
                    and evidence_gain_ok
                )
                or (chosen_source == "rule_structured" and rel_gain_ok and coherence_gain_ok)
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
                + max(0.0, evidence_gain) * 0.18
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
                    effective=effective,
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
                "selected_arm": chosen_source,
                "changed": False,
                "effective": False,
                "baseline_score": baseline_score,
                "selected_score": (chosen["score"] if chosen else baseline_score),
                "selection_min_gain": min_gain,
                "selection_rel_anchor_gain": round(rel_anchor_gain, 4),
                "selection_novelty_gain": round(novelty_gain, 4),
                "selection_structure_gain": round(structure_gain, 4),
                "selection_coherence_gain": round(coherence_gain, 4),
                "selection_evidence_gain": round(evidence_gain, 4),
                "selection_rel_gain_ok": rel_gain_ok,
                "selection_novelty_gain_ok": novelty_gain_ok,
                "selection_coherence_gain_ok": coherence_gain_ok,
                "selection_evidence_gain_ok": evidence_gain_ok,
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
                        "coherence_signal": c["score"].get("coherence_signal", 0.0),
                        "evidence_signal": c["score"].get("evidence_signal", 0.0),
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
            "selected_arm": chosen_source,
            "changed": changed,
            "effective": True,
            "baseline_score": baseline_score,
            "selected_score": (chosen["score"] if chosen else baseline_score),
            "selection_min_gain": min_gain,
            "selection_rel_anchor_gain": round(rel_anchor_gain, 4),
            "selection_novelty_gain": round(novelty_gain, 4),
            "selection_structure_gain": round(structure_gain, 4),
            "selection_coherence_gain": round(coherence_gain, 4),
            "selection_evidence_gain": round(evidence_gain, 4),
            "selection_rel_gain_ok": rel_gain_ok,
            "selection_novelty_gain_ok": novelty_gain_ok,
            "selection_coherence_gain_ok": coherence_gain_ok,
            "selection_evidence_gain_ok": evidence_gain_ok,
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
                    "coherence_signal": c["score"].get("coherence_signal", 0.0),
                    "evidence_signal": c["score"].get("evidence_signal", 0.0),
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
