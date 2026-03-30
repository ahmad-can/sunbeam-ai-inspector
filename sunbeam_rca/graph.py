"""LangGraph StateGraph definition for the RCA workflow.

The graph connects nodes in a multi-agent pipeline::

    collect_node → parse_node → pattern_match_node
        → [infra|network|k8s|juju|storage|observability|pipeline]_agent (parallel)
            → orchestrator_node → deep_analyze_node → score_node → report_node

deep_analyze_node always runs (not conditionally).  It sends failure-window
events to the LLM for open-ended discovery of unknown errors and injects any
findings as synthetic pattern matches before scoring.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from sunbeam_rca.nodes.agents import (
    AGENT_NODES,
    orchestrator_node,
    pattern_match_node,
    route_to_agents,
)
from sunbeam_rca.nodes.analyze import deep_analyze_node
from sunbeam_rca.nodes.collect import collect_node
from sunbeam_rca.nodes.parse import parse_node
from sunbeam_rca.nodes.report import report_node
from sunbeam_rca.nodes.score import score_node
from sunbeam_rca.state import RCAState


def build_graph() -> StateGraph:
    """Construct and return the compiled RCA workflow graph."""
    graph = StateGraph(RCAState)

    graph.add_node("collect_node", collect_node)
    graph.add_node("parse_node", parse_node)
    graph.add_node("pattern_match_node", pattern_match_node)

    for name, fn in AGENT_NODES.items():
        graph.add_node(name, fn)

    graph.add_node("orchestrator_node", orchestrator_node)
    graph.add_node("deep_analyze_node", deep_analyze_node)
    graph.add_node("score_node", score_node)
    graph.add_node("report_node", report_node)

    graph.add_edge(START, "collect_node")
    graph.add_edge("collect_node", "parse_node")
    graph.add_edge("parse_node", "pattern_match_node")

    graph.add_conditional_edges(
        "pattern_match_node",
        route_to_agents,
        list(AGENT_NODES.keys()),
    )

    for name in AGENT_NODES:
        graph.add_edge(name, "orchestrator_node")

    graph.add_edge("orchestrator_node", "deep_analyze_node")
    graph.add_edge("deep_analyze_node", "score_node")
    graph.add_edge("score_node", "report_node")
    graph.add_edge("report_node", END)

    return graph.compile()
