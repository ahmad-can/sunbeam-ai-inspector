"""deep_analyze_node — LLM-driven discovery of unknown errors.

The deep_analyze_node always runs (not conditionally) so the LLM can
surface unknown errors that no regex pattern covers.  Its findings are
injected as synthetic PatternMatch objects into the scoring pipeline.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sunbeam_rca.analysis.baseline import get_baseline_noise_summary
from sunbeam_rca.analysis.prompts import (
    DEEP_ANALYSIS_SYSTEM,
    DEEP_ANALYSIS_USER,
)
from sunbeam_rca.config import get_llm
from sunbeam_rca.models import LogEvent, LogLevel
from sunbeam_rca.state import RCAState
from sunbeam_rca.utils.sanitizer import sanitize

logger = logging.getLogger(__name__)

_MAX_FAILURE_WINDOW_EVENTS = 150
_MAX_ERROR_EVENTS = 100
_MAX_SUNBEAM_EVENTS = 80


def deep_analyze_node(state: RCAState) -> dict:
    """LLM-driven discovery of unknown errors — always runs.

    Sends failure-window events + sunbeam app logs + error events to the LLM
    and asks it to identify root causes that may not match any regex pattern.
    Findings are converted to synthetic PatternMatch objects and merged into
    the pattern_matches list so the scorer can rank them.
    """
    llm = get_llm()
    if not llm:
        logger.info("No LLM available for deep analysis — skipping")
        return {}

    raw_events = state.get("events", [])
    existing_pattern_ids = {
        m.get("pattern_id", "") for m in state.get("pattern_matches", [])
    }

    events = [LogEvent(**e) for e in raw_events]
    selected = _select_deep_analysis_events(events, state)

    if not selected:
        logger.info("No events selected for deep analysis")
        return {}

    events_text = "\n".join(
        sanitize(e.to_context_str(max_msg_len=400)) for e in selected
    )

    infra_state = _format_infrastructure_state(state)

    user_msg = DEEP_ANALYSIS_USER.format(
        failure_timestamp=state.get("failure_timestamp", "unknown"),
        infrastructure_state=infra_state,
        event_count=len(selected),
        events_text=events_text,
    )

    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        baseline_section = get_baseline_noise_summary()
        system_prompt = DEEP_ANALYSIS_SYSTEM
        if baseline_section:
            system_prompt += "\n\n" + baseline_section

        resp = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_msg),
        ])
        analysis_text = resp.content if hasattr(resp, "content") else str(resp)

        findings = _parse_deep_analysis(analysis_text)
        synthetic_matches = _findings_to_pattern_matches(
            findings, events, existing_pattern_ids
        )

        logger.info(
            "Deep analysis: %d findings → %d synthetic candidates",
            len(findings), len(synthetic_matches),
        )

        return {"pattern_matches": synthetic_matches}
    except Exception:
        logger.exception("Deep LLM analysis failed")
        return {}


def _select_deep_analysis_events(
    events: list[LogEvent], state: dict
) -> list[LogEvent]:
    """Select the most relevant events for LLM deep analysis.

    Priority order:
    1. ALL events within the failure window (any severity)
    2. Sunbeam app log events (any severity — they contain root causes at DEBUG)
    3. ERROR events outside the failure window
    """
    fw_start_str = state.get("failure_window_start", "")
    fw_end_str = state.get("failure_window_end", "")

    try:
        fw_start = datetime.fromisoformat(fw_start_str).astimezone(timezone.utc) if fw_start_str else None
        fw_end = datetime.fromisoformat(fw_end_str).astimezone(timezone.utc) if fw_end_str else None
    except (ValueError, TypeError):
        fw_start = fw_end = None

    window_events: list[LogEvent] = []
    sunbeam_events: list[LogEvent] = []
    error_events: list[LogEvent] = []

    for ev in events:
        in_window = (
            fw_start and fw_end
            and fw_start <= ev.timestamp <= fw_end
        )
        is_sunbeam = ev.source_type.value == "sunbeam"

        if in_window:
            window_events.append(ev)
        elif is_sunbeam:
            sunbeam_events.append(ev)
        elif ev.level in (LogLevel.ERROR, LogLevel.WARNING):
            error_events.append(ev)

    selected = (
        window_events[:_MAX_FAILURE_WINDOW_EVENTS]
        + sunbeam_events[:_MAX_SUNBEAM_EVENTS]
        + error_events[:_MAX_ERROR_EVENTS]
    )
    selected.sort(key=lambda e: e.timestamp)
    return selected


def _findings_to_pattern_matches(
    findings: list[dict],
    events: list[LogEvent],
    existing_pattern_ids: set[str],
) -> list[dict]:
    """Convert LLM deep-analysis findings into synthetic PatternMatch dicts."""
    event_index: dict[str, LogEvent] = {}
    for ev in events:
        key = f"{ev.source_file}:{ev.line_number}"
        event_index[key] = ev

    synthetic: list[dict] = []
    for i, finding in enumerate(findings):
        cause = finding.get("likely_root_cause", "")
        category = finding.get("category", "unknown")
        confidence = finding.get("confidence", "medium")
        reasoning = finding.get("reasoning", "")
        evidence_list = finding.get("evidence", [])

        pattern_id = f"LLM_DISCOVERED_{i + 1}"

        severity_map = {"high": 9, "medium": 7, "low": 5}
        severity = severity_map.get(confidence, 7)

        matched_event_dict: dict = {}
        context_events: list[dict] = []

        for ev_data in evidence_list:
            src = ev_data.get("source_file", "")
            ln = ev_data.get("line_number", 0)
            key = f"{src}:{ln}"
            real_event = event_index.get(key)

            if real_event:
                ev_dict = real_event.model_dump(mode="json")
            else:
                ev_dict = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "source_file": src,
                    "line_number": ln,
                    "level": "ERROR",
                    "message": ev_data.get("message", cause),
                    "source_type": "syslog",
                    "metadata": {"synthetic": True, "llm_discovered": True},
                }

            if not matched_event_dict:
                ev_dict.setdefault("metadata", {})
                ev_dict["metadata"]["synthetic"] = True
                ev_dict["metadata"]["llm_discovered"] = True
                matched_event_dict = ev_dict
            else:
                context_events.append(ev_dict)

        if not matched_event_dict:
            matched_event_dict = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source_file": "",
                "line_number": 0,
                "level": "ERROR",
                "message": cause,
                "source_type": "syslog",
                "metadata": {"synthetic": True, "llm_discovered": True},
            }

        synthetic.append({
            "pattern_id": pattern_id,
            "category": category,
            "description": cause,
            "severity": severity,
            "matched_event": matched_event_dict,
            "context_events": context_events,
        })

        logger.info(
            "Synthetic candidate: %s — %s (confidence=%s)",
            pattern_id, cause[:80], confidence,
        )

    return synthetic


def _format_infrastructure_state(state: RCAState) -> str:
    """Build a human-readable infrastructure state section from Juju status."""
    lines = []

    # Model topology
    topology = state.get("model_topology", [])
    if topology:
        lines.append("### Juju Model Topology")
        for m in topology:
            kind = "K8s" if m.get("model_type") == "caas" else "MAAS"
            ctrl = " (controller)" if m.get("is_controller") else ""
            lines.append(
                f"- **{m.get('short_name', '?')}** [{kind}] "
                f"cloud={m.get('cloud', '?')}/{m.get('region', '?')} "
                f"status={m.get('status', '?')}{ctrl}"
            )
        lines.append("")

    summary = state.get("juju_status_summary", {})
    if not summary:
        lines.append("No Juju status data available.")
        return "\n".join(lines)

    # Unhealthy applications
    unhealthy_apps = summary.get("unhealthy_apps", [])
    if unhealthy_apps:
        lines.append(f"### Unhealthy Applications ({len(unhealthy_apps)} total)")
        for a in unhealthy_apps[:10]:
            lines.append(
                f"- [{a.get('model', '?')}] **{a['application']}**: "
                f"status={a['status']}, message=\"{a.get('message', '')}\""
            )
        lines.append("")

    # Stuck units
    stuck = summary.get("stuck_units", [])
    if stuck:
        lines.append(f"### Unhealthy Units ({len(stuck)} total)")
        for u in stuck[:15]:
            model_prefix = f"[{u.get('model', '?')}] " if u.get("model") else ""
            lines.append(
                f"- {model_prefix}**{u['unit']}**: status={u['status']}, "
                f"message=\"{u.get('message', '')}\", since={u.get('since', '?')}"
            )
        lines.append("")

    if not unhealthy_apps and not stuck:
        lines.append("### All applications and units healthy")
        lines.append("")

    # Missing CNI
    missing_cni = summary.get("machines_missing_cni", [])
    if missing_cni:
        lines.append(f"### Machines Missing Cilium CNI ({len(missing_cni)} total)")
        for m in missing_cni:
            lines.append(
                f"- Machine {m['machine']} ({m.get('hostname', '?')}): "
                f"interfaces={m.get('interfaces', [])}"
            )
        lines.append("")

    # SAAS issues
    saas = summary.get("saas_issues", [])
    if saas:
        lines.append(f"### Cross-Model (SAAS) Integration Issues ({len(saas)} total)")
        for s in saas[:10]:
            lines.append(
                f"- [{s.get('model', '?')}] **{s['saas_name']}** "
                f"(from {s.get('offer_url', '?')}): "
                f"status={s['status']}, message=\"{s.get('message', '')}\""
            )
        lines.append("")

    # Offer issues
    offers = summary.get("offer_issues", [])
    if offers:
        lines.append(f"### Cross-Model Offer Issues ({len(offers)} total)")
        for o in offers[:10]:
            lines.append(
                f"- [{o.get('model', '?')}] **{o['offer_name']}** "
                f"({o.get('url', '?')}): "
                f"status={o['status']}, message=\"{o.get('message', '')}\""
            )
        lines.append("")

    lines.append(
        f"Total machines: {summary.get('machine_count', '?')}, "
        f"applications: {summary.get('application_count', '?')}, "
        f"SAAS relations: {summary.get('saas_count', '?')}, "
        f"offers: {summary.get('offer_count', '?')}"
    )
    return "\n".join(lines)


def _parse_deep_analysis(text: str) -> list[dict]:
    """Extract findings from deep analysis LLM response."""
    try:
        clean = text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1]
            clean = clean.rsplit("```", 1)[0]
        data = json.loads(clean)
        return [data] if isinstance(data, dict) else data
    except (json.JSONDecodeError, AttributeError):
        logger.warning("Could not parse deep analysis response as JSON")
        return []


