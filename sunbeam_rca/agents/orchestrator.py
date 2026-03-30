"""Orchestrator agent — correlates findings across all domain agents.

Receives DomainFindings from all six agents and:
1. Cross-domain correlation (network failure -> storage failure)
2. Layered reasoning (lower-layer issues override higher-layer)
3. Causal chain construction
4. Root cause identification
"""

from __future__ import annotations

import json
import logging

from sunbeam_rca.agents.models import (
    ALL_DOMAINS,
    DomainFinding,
)
from sunbeam_rca.agents.prompts import ORCHESTRATOR_SYSTEM
from sunbeam_rca.config import get_llm
from sunbeam_rca.utils.sanitizer import sanitize

logger = logging.getLogger(__name__)

DOMAIN_LAYER_ORDER = [
    "infrastructure",
    "network",
    "kubernetes",
    "juju",
    "storage",
    "pipeline",
]


def orchestrate(
    findings: list[DomainFinding],
    state: dict,
) -> dict:
    """Correlate domain findings and produce the final analysis.

    Returns a dict with keys compatible with the existing RCAState:
    - pattern_matches (from all agents)
    - llm_analysis (orchestrator reasoning)
    - correlated_findings (causal chain)
    - domain_findings (the raw agent findings)
    """
    llm = get_llm()
    if llm:
        llm_analysis, correlated = _llm_orchestrate(llm, findings, state)
    else:
        llm_analysis, correlated = _deterministic_orchestrate(findings, state)

    return {
        "llm_analysis": llm_analysis,
        "correlated_findings": correlated,
    }


def _llm_orchestrate(
    llm,
    findings: list[DomainFinding],
    state: dict,
) -> tuple[str, list[dict]]:
    """Use LLM for cross-domain correlation."""
    user_prompt = _build_orchestrator_prompt(findings, state)

    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        resp = llm.invoke([
            SystemMessage(content=ORCHESTRATOR_SYSTEM),
            HumanMessage(content=user_prompt),
        ])
        analysis = resp.content if hasattr(resp, "content") else str(resp)
        correlated = _parse_orchestrator_response(analysis)
        return analysis, correlated
    except Exception:
        logger.exception("LLM orchestration failed, using deterministic fallback")
        return _deterministic_orchestrate(findings, state)


def _build_orchestrator_prompt(
    findings: list[DomainFinding],
    state: dict,
) -> str:
    """Build the user prompt for the orchestrator LLM."""
    failure_ts = state.get("failure_timestamp", "unknown")
    parts: list[str] = [
        f"## Pipeline failure timestamp\n{failure_ts}\n",
        "## Domain Agent Findings\n",
    ]

    ordered = sorted(
        findings,
        key=lambda f: DOMAIN_LAYER_ORDER.index(f.domain)
        if f.domain in DOMAIN_LAYER_ORDER else 99,
    )

    for f in ordered:
        status_icon = {"healthy": "OK", "degraded": "WARN", "failed": "FAIL"}.get(
            f.status, "?"
        )
        parts.append(
            f"### {f.domain.upper()} [{status_icon}] "
            f"({f.event_count} events, {f.match_count} matches)\n"
            f"**Status**: {f.status}\n"
            f"**Summary**: {sanitize(f.summary)}\n"
        )

        if f.hypotheses:
            parts.append("**Hypotheses:**")
            for h in f.hypotheses[:5]:
                parts.append(
                    f"- [{h.confidence}] {h.pattern_id}: {h.description}\n"
                    f"  Reasoning: {sanitize(h.reasoning)}"
                )
            parts.append("")

        if f.affected_components:
            parts.append(
                f"**Affected components**: {', '.join(f.affected_components[:10])}\n"
            )

        if f.key_evidence:
            parts.append("**Key evidence:**")
            for ev in f.key_evidence[:3]:
                parts.append(
                    f"- {ev.get('pattern_id', '?')} (x{ev.get('frequency', 1)}): "
                    f"{ev.get('source_file', '?')}:{ev.get('line_number', 0)} — "
                    f"{sanitize(str(ev.get('message', ''))[:150])}"
                )
            parts.append("")

    machine_map = state.get("machine_map", {})
    if machine_map:
        parts.append("## Machine-to-Hostname Mapping")
        for mid, hostname in machine_map.items():
            parts.append(f"- Machine {mid}: {hostname}")
        parts.append("")

    parts.append(
        "\nRespond with JSON only:\n"
        "{\n"
        '  "root_cause_pattern_id": "<pattern_id>",\n'
        '  "root_cause_domain": "<domain>",\n'
        '  "causal_chain": [\n'
        "    {\n"
        '      "pattern_id": "...",\n'
        '      "domain": "...",\n'
        '      "role": "root_cause|symptom|consequence",\n'
        '      "reasoning": "..."\n'
        "    }\n"
        "  ],\n"
        '  "cross_domain_reasoning": "how failures propagated across domains",\n'
        '  "confidence": "high|medium|low"\n'
        "}\n"
    )

    return "\n".join(parts)


def _parse_orchestrator_response(text: str) -> list[dict]:
    """Parse orchestrator LLM response into correlated findings."""
    try:
        clean = text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1]
            clean = clean.rsplit("```", 1)[0]
        data = json.loads(clean)
        chain = data.get("causal_chain", [])

        root_pid = data.get("root_cause_pattern_id", "")
        if root_pid and chain:
            has_root = any(
                item.get("role") == "root_cause" for item in chain
            )
            if not has_root:
                chain.insert(0, {
                    "pattern_id": root_pid,
                    "domain": data.get("root_cause_domain", ""),
                    "role": "root_cause",
                    "reasoning": data.get("cross_domain_reasoning", ""),
                })

        return chain
    except (json.JSONDecodeError, AttributeError):
        logger.warning("Could not parse orchestrator LLM response")
        return []


def _deterministic_orchestrate(
    findings: list[DomainFinding],
    state: dict,
) -> tuple[str, list[dict]]:
    """Fallback when LLM is unavailable — uses layer ordering and
    hypothesis severity to build a basic causal chain."""
    failed_domains = [
        f for f in findings if f.status == "failed"
    ]
    degraded_domains = [
        f for f in findings if f.status == "degraded"
    ]
    problem_domains = failed_domains or degraded_domains

    if not problem_domains:
        return "All domains report healthy.", []

    problem_domains.sort(
        key=lambda f: DOMAIN_LAYER_ORDER.index(f.domain)
        if f.domain in DOMAIN_LAYER_ORDER else 99,
    )

    chain: list[dict] = []
    for i, f in enumerate(problem_domains):
        top_hyp = f.hypotheses[0] if f.hypotheses else None
        role = "root_cause" if i == 0 else "consequence"
        chain.append({
            "pattern_id": top_hyp.pattern_id if top_hyp else f.domain,
            "domain": f.domain,
            "role": role,
            "reasoning": (
                top_hyp.reasoning if top_hyp
                else f.summary
            ),
        })

    root = problem_domains[0]
    summary = (
        f"Deepest failing domain: {root.domain} ({root.status}). "
        f"{root.summary}"
    )

    return summary, chain
