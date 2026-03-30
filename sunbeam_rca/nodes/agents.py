"""LangGraph nodes for multi-agent domain analysis.

Provides:
- route_node: fans out to domain agents via Send
- domain_agent_node: runs a single domain agent
- orchestrator_node: collects findings and runs cross-domain correlation
"""

from __future__ import annotations

import logging
from collections import Counter

from langgraph.types import Send

from sunbeam_rca.agents.infra_agent import InfraAgent
from sunbeam_rca.agents.juju_agent import JujuAgent
from sunbeam_rca.agents.k8s_agent import K8sAgent
from sunbeam_rca.agents.models import ALL_DOMAINS, DomainFinding
from sunbeam_rca.agents.network_agent import NetworkAgent
from sunbeam_rca.agents.observability_agent import ObservabilityAgent
from sunbeam_rca.agents.orchestrator import orchestrate
from sunbeam_rca.agents.pipeline_agent import PipelineAgent
from sunbeam_rca.agents.storage_agent import StorageAgent
from sunbeam_rca.analysis.pattern_matcher import load_patterns, match_patterns
from sunbeam_rca.models import LogEvent
from sunbeam_rca.state import RCAState

logger = logging.getLogger(__name__)

_AGENT_CLASSES = {
    "infrastructure": InfraAgent,
    "network": NetworkAgent,
    "kubernetes": K8sAgent,
    "juju": JujuAgent,
    "storage": StorageAgent,
    "observability": ObservabilityAgent,
    "pipeline": PipelineAgent,
}


def pattern_match_node(state: RCAState) -> dict:
    """Run deterministic pattern matching across all events.

    This is the same as the first half of the old analyze_node — pattern
    matching runs once, then results are shared with all domain agents.
    """
    raw_events = state.get("events", [])
    events = [LogEvent(**e) for e in raw_events]

    patterns = load_patterns()
    matches = match_patterns(events, patterns)
    match_dicts = [m.model_dump(mode="json") for m in matches]

    logger.info(
        "Pattern matching: %d matches from %d events", len(matches), len(events)
    )
    return {"pattern_matches": match_dicts}


def route_to_agents(state: RCAState) -> list[Send]:
    """Routing function for conditional edges — fans out to all domain agents."""
    sends = []
    for domain in ALL_DOMAINS:
        sends.append(Send(
            f"{domain}_agent",
            state,
        ))
    return sends


def _make_agent_node(domain: str):
    """Factory: create a LangGraph node function for a specific domain agent."""
    agent_cls = _AGENT_CLASSES[domain]

    def agent_node(state: RCAState) -> dict:
        agent = agent_cls()
        patterns = load_patterns()
        events = state.get("events", [])

        finding = agent.analyze(events, patterns, state)

        logger.info(
            "%s agent: status=%s, matches=%d, hypotheses=%d",
            domain,
            finding.status,
            finding.match_count,
            len(finding.hypotheses),
        )

        return {"domain_findings": [finding.model_dump(mode="json")]}

    agent_node.__name__ = f"{domain}_agent"
    agent_node.__qualname__ = f"{domain}_agent"
    return agent_node


infra_agent_node = _make_agent_node("infrastructure")
network_agent_node = _make_agent_node("network")
k8s_agent_node = _make_agent_node("kubernetes")
juju_agent_node = _make_agent_node("juju")
storage_agent_node = _make_agent_node("storage")
observability_agent_node = _make_agent_node("observability")
pipeline_agent_node = _make_agent_node("pipeline")

AGENT_NODES = {
    "infrastructure_agent": infra_agent_node,
    "network_agent": network_agent_node,
    "kubernetes_agent": k8s_agent_node,
    "juju_agent": juju_agent_node,
    "storage_agent": storage_agent_node,
    "observability_agent": observability_agent_node,
    "pipeline_agent": pipeline_agent_node,
}


def orchestrator_node(state: RCAState) -> dict:
    """Collect domain findings and run cross-domain correlation."""
    raw_findings = state.get("domain_findings", [])
    findings = [DomainFinding(**f) for f in raw_findings]

    logger.info(
        "Orchestrator: received %d domain findings — %s",
        len(findings),
        ", ".join(f"{f.domain}={f.status}" for f in findings),
    )

    result = orchestrate(findings, state)

    return {
        "llm_analysis": result["llm_analysis"],
        "correlated_findings": result["correlated_findings"],
    }
