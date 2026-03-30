"""LangGraph state definition for the RCA workflow."""

from __future__ import annotations

import operator
from typing import Annotated

from typing_extensions import TypedDict


class RCAState(TypedDict, total=False):
    """Typed state that flows through every node in the RCA graph.

    Fields are populated incrementally by each node. Using ``total=False``
    so nodes only need to return the keys they update.
    """

    # -- Inputs (set by CLI before graph invocation) --
    pipeline_zip_path: str
    sosreport_path: str

    # -- After collect_node --
    pipeline_log_files: list[str]
    sosreport_manifest: dict

    # -- After parse_node --
    events: Annotated[list[dict], operator.add]
    timeline_summary: str
    failure_timestamp: str
    failure_window_start: str
    failure_window_end: str
    juju_status_summary: dict
    machine_map: dict
    model_topology: list[dict]

    # -- After analyze_node / multi-agent pipeline --
    pattern_matches: Annotated[list[dict], operator.add]
    llm_analysis: str
    correlated_findings: list[dict]
    domain_findings: Annotated[list[dict], operator.add]

    # -- After score_node --
    ranked_candidates: list[dict]

    # -- After report_node --
    json_report: str
    markdown_report: str
    output_dir: str
