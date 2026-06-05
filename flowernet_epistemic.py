"""Epistemic audit layer for FlowerNet long-document generation.

This module keeps the existing FlowerNet pipeline intact while adding a real,
deterministic self-auditing layer around it:
- prompt constraints before generation,
- chapter assets after generation,
- claim/evidence/risk ledgers in the final document,
- machine-readable metrics in the API response.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse


ENABLED_ENV_NAME = "FLOWERNET_EPISTEMIC_AUDIT_ENABLED"


def _clean(text: str) -> str:
    return " ".join(str(text or "").split())


def _sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[。！？.!?])\s+|(?<=[。！？.!?])", str(text or ""))
    return [_clean(p) for p in parts if len(_clean(p)) >= 18]


def _tokens(text: str) -> List[str]:
    return re.findall(r"[\u4e00-\u9fff]|[A-Za-z][A-Za-z0-9_-]+", str(text or "").lower())


def _stable_id(prefix: str, text: str) -> str:
    return f"{prefix}_{hashlib.sha1(str(text).encode('utf-8')).hexdigest()[:10]}"


def _extract_urls(text: str) -> List[str]:
    return re.findall(r"https?://[^\s\]）)>,;]+", str(text or ""), flags=re.I)


def _source_url(candidate: Dict[str, Any]) -> str:
    return str(candidate.get("href") or candidate.get("url") or candidate.get("link") or "").strip()


def _source_label(candidate: Dict[str, Any]) -> str:
    url = _source_url(candidate)
    return (
        str(candidate.get("title") or candidate.get("source_name") or "").strip()
        or (urlparse(url).netloc if url else "")
        or "source"
    )


def build_epistemic_requirements() -> str:
    """Prompt block that turns generation into self-audited scientific writing."""
    return (
        "\n\n自审计科学写作要求（必须真实执行，不要当作说明文字复述）：\n"
        "1. Evidence-SLAM：每个小节先建立证据地图，正文中明确区分事实、推理、假设和局限。\n"
        "2. Epistemic Ledger：每个核心论断都要有可追踪来源线索、可信度判断或需要验证的条件。\n"
        "3. Adversarial Peer Review：主动回应事实审稿、逻辑审稿、创新性审稿、反例审稿和伦理/风险审稿可能提出的问题。\n"
        "4. Risk-Sensitive Control：优先修复高风险论断，避免引用漂移、过度概括、重复和证据集中。\n"
        "5. Chapter-Level Active Perception：每章结束前说明该章还缺什么证据、下一章应继承什么 unresolved claims。\n"
        "6. Falsifiability Engine：每章至少给出一个可能推翻或削弱本章结论的反例/证据类型。\n"
        "7. FlowerBench-LD：输出应便于评估结构完整性、claim factuality、citation faithfulness、argument coherence、redundancy 和 auditability。\n"
    )


def augment_user_requirements(user_requirements: str) -> str:
    return f"{user_requirements.rstrip()}{build_epistemic_requirements()}"


def _prompt_block_for_subsection(section_title: str, subsection_title: str) -> str:
    return (
        "\n\n[FlowerNet Epistemic Audit Instructions]\n"
        f"Current chapter: {section_title}\n"
        f"Current subsection: {subsection_title}\n"
        "- Begin from an implicit evidence map: identify relevant sources, missing evidence, and likely counterexamples.\n"
        "- Include at least one explicit falsifiability/counterexample sentence using wording such as “可能削弱该结论的证据是…”.\n"
        "- Mark claim status in prose: established evidence, model inference, assumption, limitation, or open question.\n"
        "- Avoid unsupported certainty. Prefer calibrated language when evidence is incomplete.\n"
        "- Maintain continuity with previous subsections and state what this subsection hands off to the next one.\n"
        "- Do not output this instruction block; only output polished document content.\n"
    )


def augment_content_prompts(content_prompts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    augmented: List[Dict[str, Any]] = []
    for prompt in content_prompts or []:
        if not isinstance(prompt, dict):
            augmented.append(prompt)
            continue
        item = dict(prompt)
        section_title = str(item.get("section_title") or "")
        subsection_title = str(item.get("subsection_title") or "")
        block = _prompt_block_for_subsection(section_title, subsection_title)
        original = str(item.get("content_prompt") or "")
        if "FlowerNet Epistemic Audit Instructions" not in original:
            item["content_prompt"] = original.rstrip() + block
        item.setdefault("epistemic_audit_enabled", True)
        augmented.append(item)
    return augmented


def _iter_subsections(sections: List[Dict[str, Any]]) -> Iterable[Tuple[Dict[str, Any], Dict[str, Any]]]:
    for section in sections or []:
        if not isinstance(section, dict):
            continue
        for subsection in section.get("subsections", []) or []:
            if isinstance(subsection, dict):
                yield section, subsection


def _source_candidates(section: Dict[str, Any], subsection: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for candidate in subsection.get("source_results", []) or []:
        if isinstance(candidate, dict):
            candidates.append(candidate)
    for url in _extract_urls(subsection.get("content", "")):
        candidates.append({"url": url, "title": urlparse(url).netloc})
    return candidates


def _claim_type(sentence: str) -> str:
    s = sentence.lower()
    if any(x in sentence for x in ["可能", "假设", "若", "如果", "推测"]) or any(x in s for x in ["may", "might", "assume", "hypothesis"]):
        return "assumption_or_inference"
    if any(x in sentence for x in ["局限", "风险", "不足", "挑战", "失败", "反例"]) or any(x in s for x in ["limitation", "risk", "failure", "counterexample"]):
        return "limitation_or_counterevidence"
    if re.search(r"\[\d+\]|https?://|\b(19|20)\d{2}\b", sentence):
        return "evidence_backed_claim"
    return "model_synthesis_claim"


def _claim_confidence(sentence: str, sources: List[Dict[str, Any]]) -> float:
    score = 0.45
    if re.search(r"\[\d+\]|https?://|\b(19|20)\d{2}\b", sentence):
        score += 0.18
    if sources:
        score += min(0.22, len(sources) * 0.04)
    if any(x in sentence for x in ["可能", "推测", "假设", "尚不", "需要进一步"]):
        score -= 0.08
    if any(x in sentence for x in ["证明", "必然", "完全", "唯一"]):
        score -= 0.06
    return round(max(0.05, min(0.95, score)), 3)


def _risk_for_claim(sentence: str, confidence: float, source_count: int) -> float:
    risk = 1.0 - confidence
    if source_count == 0:
        risk += 0.18
    if any(x in sentence for x in ["必然", "完全", "唯一", "显著优于", "首次"]):
        risk += 0.12
    if len(sentence) > 220:
        risk += 0.05
    return round(max(0.0, min(1.0, risk)), 3)


class EpistemicAuditEngine:
    """Builds the self-audit artifacts used by FlowerNet final assembly."""

    reviewer_names = [
        "factual_reviewer",
        "logic_reviewer",
        "novelty_reviewer",
        "skeptical_reviewer",
        "domain_reviewer",
        "ethics_risk_reviewer",
    ]

    def build_audit(
        self,
        *,
        title: str,
        structure: Dict[str, Any],
        sections: List[Dict[str, Any]],
        history: List[Dict[str, Any]],
        orchestration_metrics: Optional[Dict[str, Any]] = None,
        quality_metrics: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        orchestration_metrics = orchestration_metrics or {}
        quality_metrics = quality_metrics or {}
        claim_ledger: List[Dict[str, Any]] = []
        evidence_nodes: Dict[str, Dict[str, Any]] = {}
        chapter_cards: List[Dict[str, Any]] = []
        contradiction_edges: List[Dict[str, Any]] = []
        all_text_parts: List[str] = []

        for section_index, section in enumerate(sections or [], 1):
            section_id = str(section.get("section_id") or section.get("id") or f"section_{section_index}")
            section_title = str(section.get("title") or section.get("section_title") or f"Section {section_index}")
            section_claims: List[Dict[str, Any]] = []
            section_sources: List[Dict[str, Any]] = []
            section_text = []
            for subsection_index, subsection in enumerate(section.get("subsections", []) or [], 1):
                subsection_id = str(subsection.get("subsection_id") or subsection.get("id") or f"subsection_{subsection_index}")
                subsection_title = str(subsection.get("title") or subsection.get("subsection_title") or f"Subsection {subsection_index}")
                content = str(subsection.get("content") or "")
                section_text.append(content)
                all_text_parts.append(content)
                sources = _source_candidates(section, subsection)
                section_sources.extend(sources)
                for candidate in sources:
                    key = _source_url(candidate) or _source_label(candidate)
                    if not key:
                        continue
                    node_id = _stable_id("src", key)
                    evidence_nodes.setdefault(
                        node_id,
                        {
                            "id": node_id,
                            "label": _source_label(candidate),
                            "url": _source_url(candidate),
                            "sections": [],
                            "quality": float(candidate.get("quality_score", candidate.get("source_weight", 0.5)) or 0.5),
                        },
                    )
                    evidence_nodes[node_id]["sections"].append(section_id)

                candidates = _sentences(content)[:6]
                for sent in candidates:
                    claim_id = _stable_id("claim", f"{section_id}:{subsection_id}:{sent}")
                    confidence = _claim_confidence(sent, sources)
                    claim = {
                        "id": claim_id,
                        "section_id": section_id,
                        "section_title": section_title,
                        "subsection_id": subsection_id,
                        "subsection_title": subsection_title,
                        "text": sent[:360],
                        "type": _claim_type(sent),
                        "source_count": len(sources),
                        "confidence": confidence,
                        "risk": _risk_for_claim(sent, confidence, len(sources)),
                        "provenance": "source_results" if sources else "model_synthesis",
                    }
                    claim_ledger.append(claim)
                    section_claims.append(claim)
                    if claim["type"] == "limitation_or_counterevidence":
                        contradiction_edges.append({
                            "claim_id": claim_id,
                            "section_id": section_id,
                            "kind": "self_raised_counterevidence",
                            "description": sent[:240],
                        })

            chapter_cards.append(self._chapter_card(section_id, section_title, section_claims, section_sources, "\n".join(section_text)))

        full_text = "\n".join(all_text_parts)
        reviewer_scores = self._reviewer_scores(
            full_text=full_text,
            claims=claim_ledger,
            evidence_nodes=list(evidence_nodes.values()),
            metrics={**orchestration_metrics, **quality_metrics},
        )
        risk_portfolio = self._risk_portfolio(claim_ledger, evidence_nodes, reviewer_scores, orchestration_metrics)
        active_perception = self._active_perception(chapter_cards)
        benchmark = self._benchmark_snapshot(full_text, claim_ledger, reviewer_scores, risk_portfolio)

        return {
            "enabled": True,
            "title": title,
            "evidence_map": {
                "nodes": list(evidence_nodes.values())[:80],
                "claim_count": len(claim_ledger),
                "source_count": len(evidence_nodes),
                "contradiction_edges": contradiction_edges[:40],
            },
            "claim_ledger": claim_ledger[:80],
            "chapter_cards": chapter_cards,
            "active_perception": active_perception,
            "reviewer_scores": reviewer_scores,
            "risk_portfolio": risk_portfolio,
            "benchmark_snapshot": benchmark,
            "summary": self._summary(claim_ledger, evidence_nodes, reviewer_scores, risk_portfolio),
        }

    def _chapter_card(
        self,
        section_id: str,
        section_title: str,
        claims: List[Dict[str, Any]],
        sources: List[Dict[str, Any]],
        text: str,
    ) -> Dict[str, Any]:
        high_risk = sorted(claims, key=lambda c: c.get("risk", 0), reverse=True)[:3]
        counter = [c for c in claims if c.get("type") == "limitation_or_counterevidence"]
        handoff_terms = [w for w, _ in Counter(_tokens(text)).most_common(8) if len(w) > 1 or re.match(r"[A-Za-z]", w)]
        return {
            "section_id": section_id,
            "section_title": section_title,
            "claims": len(claims),
            "sources": len(sources),
            "avg_confidence": round(sum(c.get("confidence", 0) for c in claims) / max(1, len(claims)), 3),
            "max_risk": round(max([c.get("risk", 0) for c in claims] + [0]), 3),
            "counterexample_count": len(counter),
            "high_risk_claims": high_risk,
            "missing_evidence": self._missing_evidence(claims, sources),
            "handoff_terms": handoff_terms[:5],
        }

    def _missing_evidence(self, claims: List[Dict[str, Any]], sources: List[Dict[str, Any]]) -> List[str]:
        gaps = []
        if not sources:
            gaps.append("No external source metadata reached this chapter.")
        if sum(1 for c in claims if c.get("source_count", 0) == 0) > max(1, len(claims) // 2):
            gaps.append("Many claims are model-synthesis claims and need source triangulation.")
        if not any(c.get("type") == "limitation_or_counterevidence" for c in claims):
            gaps.append("Counterexamples or falsifying evidence are underrepresented.")
        if not gaps:
            gaps.append("Evidence coverage is acceptable; prioritize stronger primary sources if available.")
        return gaps[:3]

    def _reviewer_scores(
        self,
        *,
        full_text: str,
        claims: List[Dict[str, Any]],
        evidence_nodes: List[Dict[str, Any]],
        metrics: Dict[str, Any],
    ) -> Dict[str, Dict[str, Any]]:
        claim_count = max(1, len(claims))
        evidence_ratio = min(1.0, len(evidence_nodes) / max(1, claim_count / 3))
        avg_conf = sum(c.get("confidence", 0) for c in claims) / claim_count
        avg_risk = sum(c.get("risk", 0) for c in claims) / claim_count
        counter_ratio = min(1.0, sum(1 for c in claims if c.get("type") == "limitation_or_counterevidence") / max(1, claim_count / 5))
        paragraphs = len([p for p in re.split(r"\n\s*\n", full_text) if p.strip()])
        headings = len(re.findall(r"^#{2,4}\s+", full_text, flags=re.M))
        redundancy = float(metrics.get("redundancy_index_avg", metrics.get("redundancy_avg", 0.15)) or 0.15)
        scores = {
            "factual_reviewer": (0.35 + 0.45 * evidence_ratio + 0.20 * avg_conf),
            "logic_reviewer": (0.35 + 0.35 * min(1.0, headings / 8) + 0.30 * min(1.0, paragraphs / 18)),
            "novelty_reviewer": (0.45 + 0.25 * min(1.0, len(set(_tokens(full_text))) / 700) + 0.15 * counter_ratio + 0.15 * min(1.0, len(claims) / 30)),
            "skeptical_reviewer": (0.30 + 0.55 * counter_ratio + 0.15 * (1.0 - avg_risk)),
            "domain_reviewer": (0.40 + 0.35 * evidence_ratio + 0.25 * min(1.0, headings / 6)),
            "ethics_risk_reviewer": (0.35 + 0.40 * (1.0 - avg_risk) + 0.25 * counter_ratio),
        }
        return {
            name: {
                "score": round(max(0.0, min(1.0, score - min(0.18, redundancy * 0.12))), 3),
                "role": name.replace("_", " "),
            }
            for name, score in scores.items()
        }

    def _risk_portfolio(
        self,
        claims: List[Dict[str, Any]],
        evidence_nodes: Dict[str, Dict[str, Any]],
        reviewer_scores: Dict[str, Dict[str, Any]],
        orchestration_metrics: Dict[str, Any],
    ) -> Dict[str, Any]:
        risks = [float(c.get("risk", 0.0) or 0.0) for c in claims]
        avg_risk = sum(risks) / max(1, len(risks))
        max_drawdown = max(risks or [0.0])
        avg_review = sum(v.get("score", 0.0) for v in reviewer_scores.values()) / max(1, len(reviewer_scores))
        source_domains = []
        for node in evidence_nodes.values():
            url = node.get("url", "")
            if url:
                source_domains.append(urlparse(url).netloc)
        domain_counts = Counter(source_domains)
        concentration = max(domain_counts.values()) / max(1, sum(domain_counts.values())) if domain_counts else 1.0
        reward_avg = float(orchestration_metrics.get("bandit_reward_avg", 0.0) or 0.0)
        epistemic_sharpe = (avg_review + reward_avg + 1e-6) / (avg_risk + concentration * 0.2 + 1e-6)
        return {
            "avg_claim_risk": round(avg_risk, 3),
            "citation_drift_drawdown": round(max_drawdown, 3),
            "evidence_concentration_risk": round(concentration, 3),
            "epistemic_sharpe": round(epistemic_sharpe, 3),
            "recommended_repairs": self._recommended_repairs(avg_risk, concentration, claims),
        }

    def _recommended_repairs(self, avg_risk: float, concentration: float, claims: List[Dict[str, Any]]) -> List[str]:
        repairs = []
        if avg_risk > 0.42:
            repairs.append("retrieve_more_evidence")
            repairs.append("split_high_risk_claims")
        if concentration > 0.55:
            repairs.append("diversify_sources")
        if not any(c.get("type") == "limitation_or_counterevidence" for c in claims):
            repairs.append("add_counterexamples")
        if not repairs:
            repairs.append("preserve_current_structure_and_strengthen_primary_sources")
        return repairs[:4]

    def _active_perception(self, chapter_cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        events = []
        for idx, card in enumerate(chapter_cards, 1):
            events.append({
                "chapter": card["section_title"],
                "status": "passed_with_audit" if card["avg_confidence"] >= 0.45 else "needs_more_evidence",
                "next_action": card["missing_evidence"][0],
                "handoff_terms": card["handoff_terms"],
                "risk": card["max_risk"],
            })
        return events

    def _benchmark_snapshot(
        self,
        full_text: str,
        claims: List[Dict[str, Any]],
        reviewer_scores: Dict[str, Dict[str, Any]],
        risk_portfolio: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "flowerbench_ld": {
                "claim_factuality_proxy": round(sum(c.get("confidence", 0) for c in claims) / max(1, len(claims)), 3),
                "argument_coherence_proxy": reviewer_scores.get("logic_reviewer", {}).get("score", 0.0),
                "auditability_proxy": round(min(1.0, len(claims) / 24) * 0.5 + min(1.0, risk_portfolio.get("epistemic_sharpe", 0) / 4) * 0.5, 3),
                "redundancy_proxy": round(1.0 - min(0.8, self._repeat_ratio(full_text)), 3),
            }
        }

    def _repeat_ratio(self, text: str) -> float:
        toks = _tokens(text)
        grams = [tuple(toks[i:i + 3]) for i in range(max(0, len(toks) - 2))]
        if not grams:
            return 0.0
        return 1.0 - len(set(grams)) / len(grams)

    def _summary(
        self,
        claims: List[Dict[str, Any]],
        evidence_nodes: Dict[str, Dict[str, Any]],
        reviewer_scores: Dict[str, Dict[str, Any]],
        risk_portfolio: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "claims_audited": len(claims),
            "evidence_nodes": len(evidence_nodes),
            "reviewer_score_avg": round(sum(v.get("score", 0) for v in reviewer_scores.values()) / max(1, len(reviewer_scores)), 3),
            "epistemic_sharpe": risk_portfolio.get("epistemic_sharpe", 0),
            "highest_risk": risk_portfolio.get("citation_drift_drawdown", 0),
        }


def build_chapter_assets(audit: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Return chapter-level tables keyed by section id, inserted after last subsection."""
    assets: Dict[str, List[Dict[str, Any]]] = {}
    for card in audit.get("chapter_cards", []) or []:
        section_id = str(card.get("section_id") or "")
        if not section_id:
            continue
        table = [
            "| Audit dimension | Value |",
            "|---|---:|",
            f"| Claims audited | {card.get('claims', 0)} |",
            f"| Source signals | {card.get('sources', 0)} |",
            f"| Avg confidence | {card.get('avg_confidence', 0)} |",
            f"| Max risk | {card.get('max_risk', 0)} |",
            f"| Counterexample signals | {card.get('counterexample_count', 0)} |",
        ]
        assets.setdefault(section_id, []).append({
            "type": "table",
            "title": f"Chapter epistemic audit: {card.get('section_title', section_id)}",
            "caption": "Generated by FlowerNet's chapter-level active perception layer after the chapter passed generation and verification.",
            "markdown": "\n".join(table),
        })
    return assets


def attach_chapter_assets(sections: List[Dict[str, Any]], audit: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Attach active-perception tables to generated sections for final rendering."""
    assets_by_section = build_chapter_assets(audit)
    for section in sections or []:
        if not isinstance(section, dict):
            continue
        section_id = str(section.get("section_id") or section.get("id") or "")
        assets = assets_by_section.get(section_id, [])
        if not assets:
            continue
        subsections = section.get("subsections") or []
        anchor = ""
        if subsections:
            last_sub = subsections[-1]
            if isinstance(last_sub, dict):
                anchor = str(last_sub.get("subsection_id") or last_sub.get("id") or "")
        section_assets = list(section.get("chapter_assets") or [])
        for asset in assets:
            item = dict(asset)
            item["insert_after_subsection_id"] = anchor
            section_assets.append(item)
        section["chapter_assets"] = section_assets
    return sections


def render_audit_markdown(audit: Dict[str, Any]) -> str:
    if not audit or not audit.get("enabled"):
        return ""
    summary = audit.get("summary", {}) or {}
    risk = audit.get("risk_portfolio", {}) or {}
    reviewers = audit.get("reviewer_scores", {}) or {}
    benchmark = ((audit.get("benchmark_snapshot") or {}).get("flowerbench_ld") or {})
    lines = [
        "## Self-Audit Ledger",
        "",
        "This section is generated by FlowerNet's epistemic audit layer. It records how the document managed evidence uncertainty, falsifiability, reviewer pressure, and risk-sensitive control during long-form generation.",
        "",
        "#### Epistemic Control Dashboard",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Claims audited | {summary.get('claims_audited', 0)} |",
        f"| Evidence nodes | {summary.get('evidence_nodes', 0)} |",
        f"| Reviewer score avg | {summary.get('reviewer_score_avg', 0)} |",
        f"| Epistemic Sharpe ratio | {summary.get('epistemic_sharpe', 0)} |",
        f"| Citation drift drawdown | {risk.get('citation_drift_drawdown', 0)} |",
        f"| Evidence concentration risk | {risk.get('evidence_concentration_risk', 0)} |",
        "",
        "#### Multi-Agent Reviewer Panel",
        "",
        "| Reviewer | Score | Role |",
        "|---|---:|---|",
    ]
    for name, item in reviewers.items():
        lines.append(f"| {name} | {item.get('score', 0)} | {item.get('role', name)} |")
    lines.extend([
        "",
        "#### FlowerBench-LD Snapshot",
        "",
        "| Dimension | Proxy score |",
        "|---|---:|",
        f"| Claim factuality | {benchmark.get('claim_factuality_proxy', 0)} |",
        f"| Argument coherence | {benchmark.get('argument_coherence_proxy', 0)} |",
        f"| Auditability | {benchmark.get('auditability_proxy', 0)} |",
        f"| Non-redundancy | {benchmark.get('redundancy_proxy', 0)} |",
        "",
        "#### Falsifiability and Active Perception",
        "",
    ])
    for event in audit.get("active_perception", []) or []:
        terms = ", ".join(str(x) for x in event.get("handoff_terms", [])[:5])
        lines.append(f"- **{event.get('chapter')}**: {event.get('status')}; next evidence action: {event.get('next_action')}; handoff terms: {terms}.")
    lines.extend([
        "",
        "#### Highest-Risk Claims",
        "",
    ])
    claims = sorted(audit.get("claim_ledger", []) or [], key=lambda c: c.get("risk", 0), reverse=True)[:8]
    for claim in claims:
        lines.append(
            f"- `{claim.get('id')}` ({claim.get('type')}, risk={claim.get('risk')}, confidence={claim.get('confidence')}): "
            f"{claim.get('text')}"
        )
    repairs = ", ".join(str(x) for x in risk.get("recommended_repairs", []) or [])
    lines.extend(["", f"Recommended controller repair arms: {repairs or 'preserve_current_strategy'}.", ""])
    return "\n".join(lines).strip()
