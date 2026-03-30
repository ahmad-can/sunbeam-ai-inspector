"""report_node — Generate JSON and markdown reports."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from sunbeam_rca.analysis.prompts import REPORT_SYSTEM, REPORT_USER
from sunbeam_rca.config import get_llm
from sunbeam_rca.state import RCAState
from sunbeam_rca.utils.sanitizer import sanitize

logger = logging.getLogger(__name__)

_PIPELINE_SOURCE_TYPES = {"pipeline", ""}

_SOS_PREFIX_RE = re.compile(
    r".*/sosreport-[^/]+/"
)
_PIPELINE_PREFIX_RE = re.compile(
    r".*/sunbeam_pipeline_[^/]+/"
)


def _extract_relative_path(source_file: str) -> str:
    """Strip temp extraction prefixes to produce a human-readable path.

    ``/tmp/sunbeam_sos_xxx/sosreport-host-.../var/log/syslog``
    becomes ``var/log/syslog``.

    ``/tmp/sunbeam_pipeline_xxx/1_Run the pipeline.txt``
    becomes ``pipeline/1_Run the pipeline.txt``.
    """
    if not source_file:
        return ""
    m = _SOS_PREFIX_RE.match(source_file)
    if m:
        return source_file[m.end():]
    m = _PIPELINE_PREFIX_RE.match(source_file)
    if m:
        return "pipeline/" + source_file[m.end():]
    return source_file


def _relativize_evidence(candidates: list[dict]) -> list[dict]:
    """Return a deep copy of candidates with paths relativized and
    sosreport evidence sorted before pipeline evidence."""
    result = []
    for c in candidates:
        c_copy = dict(c)
        new_evidence = []
        for e in c_copy.get("evidence", []):
            e_copy = dict(e)
            e_copy["source_file"] = _extract_relative_path(e_copy.get("source_file", ""))
            new_evidence.append(e_copy)
        new_evidence.sort(
            key=lambda e: (
                1 if e.get("source_file", "").startswith("pipeline/") else 0
            )
        )
        c_copy["evidence"] = new_evidence
        result.append(c_copy)
    return result


def _enrich_with_sosreport_evidence(
    candidates: list[dict], all_events: list[dict]
) -> list[dict]:
    """For candidates whose evidence is pipeline-only, add nearby sosreport
    ERROR/WARNING events so the candidate cards show actual system log paths."""
    from datetime import datetime, timezone

    sos_errors: list[dict] = []
    for ev in all_events:
        if ev.get("source_type") in _PIPELINE_SOURCE_TYPES:
            continue
        if ev.get("level") not in ("ERROR", "WARNING"):
            continue
        sos_errors.append(ev)

    if not sos_errors:
        return candidates

    result = []
    for c in candidates:
        evidence = list(c.get("evidence", []))
        has_sos = any(
            e.get("source_type") not in _PIPELINE_SOURCE_TYPES
            for e in evidence
        )
        if has_sos or not evidence:
            result.append(c)
            continue

        primary_ts_str = evidence[0].get("timestamp", "")
        try:
            primary_ts = datetime.fromisoformat(primary_ts_str).astimezone(
                timezone.utc
            )
        except (ValueError, TypeError):
            result.append(c)
            continue

        nearby: list[tuple[float, dict]] = []
        for ev in sos_errors:
            try:
                ev_ts = datetime.fromisoformat(
                    ev.get("timestamp", "")
                ).astimezone(timezone.utc)
                delta = abs((ev_ts - primary_ts).total_seconds())
                if delta <= 120:
                    nearby.append((delta, ev))
            except (ValueError, TypeError):
                continue

        nearby.sort(key=lambda t: t[0])

        c_copy = dict(c)
        enriched_evidence = list(evidence)
        seen = {
            f"{e.get('source_file', '')}:{e.get('line_number', 0)}"
            for e in evidence
        }
        for _, ev in nearby[:3]:
            key = f"{ev.get('source_file', '')}:{ev.get('line_number', 0)}"
            if key in seen:
                continue
            seen.add(key)
            enriched_evidence.append({
                "source_file": ev.get("source_file", ""),
                "line_number": ev.get("line_number", 0),
                "timestamp": ev.get("timestamp", ""),
                "message": ev.get("message", "")[:500],
                "source_type": ev.get("source_type", ""),
            })
        c_copy["evidence"] = enriched_evidence
        result.append(c_copy)
    return result


def _pick_primary_evidence(evidence: list[dict]) -> dict:
    """Return the matched event (evidence[0]), which is always the regex hit."""
    return evidence[0] if evidence else {}


def _build_root_cause_log(candidates: list[dict]) -> dict | None:
    """Build a root-cause verdict dict from the top-ranked candidate."""
    if not candidates:
        return None
    top = candidates[0]
    primary = _pick_primary_evidence(top.get("evidence", []))
    return {
        "pattern_id": top.get("pattern_id", ""),
        "description": top.get("description", ""),
        "category": top.get("category", ""),
        "confidence": top.get("confidence", 0),
        "log_file": primary.get("source_file", ""),
        "line_number": primary.get("line_number", 0),
        "log_line": primary.get("message", ""),
        "timestamp": primary.get("timestamp", ""),
        "source_type": primary.get("source_type", ""),
    }


def report_node(state: RCAState) -> dict:
    """Produce a JSON report and a markdown summary.

    The markdown summary is generated by the LLM when available,
    with a deterministic template fallback.
    """
    candidates = state.get("ranked_candidates", [])
    all_events = state.get("events", [])
    failure_ts = state.get("failure_timestamp", "unknown")
    timeline = state.get("timeline_summary", "")
    llm_analysis = state.get("llm_analysis", "")

    infra_state = _format_infrastructure_state(state)

    correlated = state.get("correlated_findings", [])
    causal_diagram = _build_causal_chain_diagram(correlated, candidates)

    enriched = _enrich_with_sosreport_evidence(candidates, all_events)
    rel_candidates = _relativize_evidence(enriched)

    json_report = _build_json_report(state, causal_diagram, rel_candidates)

    llm = get_llm()
    if llm:
        markdown_report = _llm_markdown_report(
            llm, rel_candidates, timeline, failure_ts, infra_state
        )
    else:
        markdown_report = _template_markdown_report(
            rel_candidates, failure_ts
        )

    if causal_diagram:
        markdown_report = _inject_causal_diagram(markdown_report, causal_diagram)

    output_dir = state.get("output_dir", "./output")
    _write_outputs(output_dir, json_report, markdown_report)

    return {
        "json_report": json_report,
        "markdown_report": markdown_report,
    }


def _build_json_report(
    state: RCAState,
    causal_diagram: str = "",
    rel_candidates: list[dict] | None = None,
) -> str:
    domain_findings = state.get("domain_findings", [])
    domain_summary = [
        {
            "domain": df.get("domain", "?"),
            "status": df.get("status", "?"),
            "summary": df.get("summary", ""),
            "match_count": df.get("match_count", 0),
            "hypotheses": df.get("hypotheses", []),
        }
        for df in domain_findings
    ]

    candidates = rel_candidates or _relativize_evidence(
        state.get("ranked_candidates", [])
    )

    report = {
        "failure_timestamp": state.get("failure_timestamp", ""),
        "causal_chain_diagram": causal_diagram,
        "root_cause_log": _build_root_cause_log(candidates),
        "candidates": candidates,
        "domain_findings": domain_summary,
        "pattern_match_count": len(state.get("pattern_matches", [])),
        "total_events_parsed": len(state.get("events", [])),
        "correlated_findings": state.get("correlated_findings", []),
        "juju_status_summary": state.get("juju_status_summary", {}),
        "machine_map": state.get("machine_map", {}),
        "model_topology": state.get("model_topology", []),
    }
    return json.dumps(report, indent=2, default=str)


def _llm_markdown_report(
    llm,
    candidates: list[dict],
    timeline: str,
    failure_ts: str,
    infrastructure_state: str = "",
) -> str:
    candidates_text = ""
    for c in candidates[:10]:
        evidence_lines = "\n".join(
            f"  - `{e['source_file']}:{e['line_number']}` — {e['message']}"
            for e in c.get("evidence", [])[:3]
        )
        candidates_text += (
            f"### Rank {c['rank']}: {c['pattern_id']} "
            f"(confidence={c['confidence']:.2f}, category={c['category']})\n"
            f"{c['description']}\n"
            f"Explanation: {c.get('explanation', '')}\n"
            f"Evidence:\n{evidence_lines}\n\n"
        )

    user_msg = REPORT_USER.format(
        candidates_text=sanitize(candidates_text),
        infrastructure_state=sanitize(infrastructure_state),
        timeline_text=sanitize(timeline[:3000]),
        failure_timestamp=failure_ts,
    )

    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        resp = llm.invoke([
            SystemMessage(content=REPORT_SYSTEM),
            HumanMessage(content=user_msg),
        ])
        return resp.content if hasattr(resp, "content") else str(resp)
    except Exception:
        logger.exception("LLM report generation failed, using template")
        return _template_markdown_report(candidates, failure_ts)


def _template_markdown_report(
    candidates: list[dict],
    failure_ts: str,
) -> str:
    lines = ["# Sunbeam CI Failure — Root Cause Analysis\n"]
    lines.append(f"**Failure timestamp:** {failure_ts}\n")

    if candidates:
        top = candidates[0]
        lines.append("## Root Cause\n")
        lines.append(
            f"**{top['pattern_id']}** ({top['category']}) "
            f"— confidence {top['confidence']:.2f}\n"
        )
        lines.append(f"> {top['description']}\n")
        primary = _pick_primary_evidence(top.get("evidence", []))
        if primary:
            lines.append(f"**Log file:** `{primary['source_file']}` line {primary['line_number']}\n")
            lines.append(f"```\n{primary['message']}\n```\n")

        lines.append("## Root Cause Reasoning\n")
        lines.append(_build_narrative(top, candidates, failure_ts))
    else:
        lines.append("## Root Cause\n")
        lines.append(
            "No high-confidence root cause was identified by pattern matching.\n"
        )

    lines.append("## Ranked Candidates\n")
    for c in candidates[:10]:
        lines.append(
            f"### {c['rank']}. {c['pattern_id']} — {c['category']} "
            f"(confidence: {c['confidence']:.2f})\n"
        )
        lines.append(f"{c['description']}\n")
        for e in c.get("evidence", [])[:3]:
            lines.append(
                f"- `{e['source_file']}:{e['line_number']}` — "
                f"{e['message']}\n"
            )
        lines.append("")

    return "\n".join(lines)


def _build_narrative(
    top: dict,
    candidates: list[dict],
    failure_ts: str,
) -> str:
    """Build a human-readable narrative explaining why the top candidate
    is the root cause, referencing actual sosreport file paths."""
    parts: list[str] = []
    pid = top.get("pattern_id", "")
    category = top.get("category", "")
    description = top.get("description", "")
    explanation = top.get("explanation", "")
    evidence = top.get("evidence", [])
    llm_reasoning = top.get("llm_reasoning", "")

    parts.append(
        f"**{pid}** ({category}) was identified as the most likely root cause "
        f"of this pipeline failure.\n"
    )
    parts.append(f"*{description}*\n")

    if llm_reasoning:
        parts.append(f"{llm_reasoning}\n")

    primary = _pick_primary_evidence(evidence)
    if primary:
        parts.append("**Primary evidence:**\n")
        parts.append(
            f"- `{primary['source_file']}:{primary['line_number']}` — "
            f"{primary['message']}\n"
        )

    sos_evidence = _collect_sosreport_evidence(candidates)
    if sos_evidence:
        parts.append("**Supporting evidence from system logs:**\n")
        for se in sos_evidence[:5]:
            parts.append(
                f"- **{se['pattern_id']}** ({se['category']}): "
                f"`{se['source_file']}:{se['line_number']}` — "
                f"{se['message']}"
            )
        parts.append("")

    if explanation:
        parts.append(f"**{explanation}**\n")

    eliminated = _build_eliminated_reasons(candidates)
    if eliminated:
        parts.append("**Eliminated alternatives:**\n")
        for e in eliminated[:4]:
            parts.append(f"- {e}")
        parts.append("")

    return "\n".join(parts)


def _collect_sosreport_evidence(candidates: list[dict]) -> list[dict]:
    """Collect evidence from sosreport (non-pipeline) sources across candidates.

    Skips evidence from the root cause pattern (rank 1) to avoid repeating
    the primary evidence, and focuses on corroborating entries from other
    patterns.
    """
    sos_evidence: list[dict] = []
    seen_keys: set[str] = set()
    root_pid = candidates[0].get("pattern_id", "") if candidates else ""

    for c in candidates[:10]:
        pid = c.get("pattern_id", "")
        if pid == root_pid:
            continue
        cat = c.get("category", "")
        for e in c.get("evidence", []):
            sf = e.get("source_file", "")
            st = e.get("source_type", "")
            if st in _PIPELINE_SOURCE_TYPES or sf.startswith("pipeline/"):
                continue
            if not sf:
                continue
            key = f"{sf}:{e.get('line_number', 0)}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            sos_evidence.append({
                "pattern_id": pid,
                "category": cat,
                "source_file": sf,
                "line_number": e.get("line_number", 0),
                "message": e.get("message", ""),
                "source_type": st,
            })
    return sos_evidence


def _build_eliminated_reasons(candidates: list[dict]) -> list[str]:
    """Produce short explanations for why lower-ranked candidates are less likely."""
    eliminated: list[str] = []
    for c in candidates[1:8]:
        explanation = c.get("explanation", "")
        pid = c.get("pattern_id", "")
        if "resolved_error" in explanation:
            eliminated.append(
                f"**{pid}** — Occurred during bootstrap; resolved before failure."
            )
        elif "post_failure" in explanation:
            eliminated.append(
                f"**{pid}** — Occurred after the pipeline failure (symptom, not cause)."
            )
        elif "noise_penalty" in explanation:
            eliminated.append(
                f"**{pid}** — Classified as transient noise; not causal."
            )
        elif "state_snapshot" in explanation:
            eliminated.append(
                f"**{pid}** — Static state observation, not an active error."
            )
    return eliminated


def _format_infrastructure_state(state: RCAState) -> str:
    """Build a human-readable infrastructure state section from Juju status."""
    lines = []

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

    unhealthy_apps = summary.get("unhealthy_apps", [])
    if unhealthy_apps:
        lines.append(f"### Unhealthy Applications ({len(unhealthy_apps)} total)")
        for a in unhealthy_apps[:10]:
            lines.append(
                f"- [{a.get('model', '?')}] **{a['application']}**: "
                f"status={a['status']}, message=\"{a.get('message', '')}\""
            )
        lines.append("")

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

    missing_cni = summary.get("machines_missing_cni", [])
    if missing_cni:
        lines.append(f"### Machines Missing Cilium CNI ({len(missing_cni)} total)")
        for m in missing_cni:
            lines.append(
                f"- Machine {m['machine']} ({m.get('hostname', '?')}): "
                f"interfaces={m.get('interfaces', [])}"
            )
        lines.append("")

    saas = summary.get("saas_issues", [])
    if saas:
        lines.append(f"### Cross-Model (SAAS) Issues ({len(saas)} total)")
        for s in saas[:10]:
            lines.append(
                f"- [{s.get('model', '?')}] **{s['saas_name']}** "
                f"(from {s.get('offer_url', '?')}): "
                f"status={s['status']}, message=\"{s.get('message', '')}\""
            )
        lines.append("")

    lines.append(
        f"Total machines: {summary.get('machine_count', '?')}, "
        f"applications: {summary.get('application_count', '?')}"
    )

    # Domain findings summary
    domain_findings = state.get("domain_findings", [])
    if domain_findings:
        lines.append("")
        lines.append("### Domain Agent Summaries")
        for df in domain_findings:
            domain = df.get("domain", "?")
            status = df.get("status", "?")
            summary_text = df.get("summary", "")
            lines.append(f"- **{domain}** [{status}]: {summary_text}")

    return "\n".join(lines)


def _build_causal_chain_diagram(
    correlated: list[dict],
    candidates: list[dict],
) -> str:
    """Build an ASCII causal chain from the top-ranked candidate downward.

    Uses the deterministic causal graph to walk from the #1 scored root
    cause through its matched downstream effects, producing a tree that
    is consistent with the scoring.  Falls back to LLM correlated
    findings only if the causal graph has no chain.
    """
    if not candidates:
        return ""

    from sunbeam_rca.analysis.causal_chains import CAUSAL_GRAPH

    matched_ids = {c.get("pattern_id", "") for c in candidates}
    matched_ids.discard("")

    desc_map: dict[str, str] = {}
    cat_map: dict[str, str] = {}
    for c in candidates:
        pid = c.get("pattern_id", "")
        desc_map[pid] = c.get("description", "")
        cat_map[pid] = c.get("category", "")

    root_pid = candidates[0].get("pattern_id", "")
    if not root_pid:
        return ""

    chain: list[tuple[str, int]] = []
    visited: set[str] = set()

    def _walk(pid: str, depth: int) -> None:
        if pid in visited:
            return
        visited.add(pid)
        chain.append((pid, depth))
        for child in CAUSAL_GRAPH.get(pid, []):
            if child in matched_ids:
                _walk(child, depth + 1)

    _walk(root_pid, 0)

    if len(chain) < 2:
        _ROLE_LABELS = {
            "root_cause": "infrastructure root cause",
            "symptom": "direct consequence",
            "consequence": "downstream effect",
        }
        if len(correlated) >= 2:
            lines: list[str] = []
            for i, finding in enumerate(correlated):
                pid = finding.get("pattern_id", "?")
                role = finding.get("role", "")
                label = _ROLE_LABELS.get(role, role)
                desc = desc_map.get(pid, finding.get("reasoning", "")[:80])
                indent = "  " * i
                arrow = "\u2192 " if i > 0 else ""
                lines.append(f"{indent}{arrow}{pid} ({label})")
                lines.append(f"{indent}{'  ' if i > 0 else ''}  {desc}")
            return "\n".join(lines)
        return ""

    lines = []
    for pid, depth in chain:
        desc = desc_map.get(pid, "")
        if depth == 0:
            label = f"{cat_map.get(pid, '')} root cause"
        else:
            label = "downstream effect"
        indent = "  " * depth
        arrow = "\u2192 " if depth > 0 else ""
        lines.append(f"{indent}{arrow}{pid} ({label})")
        lines.append(f"{indent}{'  ' if depth > 0 else ''}  {desc}")

    return "\n".join(lines)


def _inject_causal_diagram(markdown: str, diagram: str) -> str:
    """Append the deterministic causal chain diagram to the report.

    Skips injection if the LLM already included a Failure Cascade section.
    """
    if "## Failure Cascade" in markdown:
        return markdown

    section = (
        "\n## Failure Cascade\n\n"
        "```\n"
        f"{diagram}\n"
        "```\n"
    )
    return markdown + section


def _write_outputs(output_dir: str, json_report: str, markdown_report: str) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    json_path = out / "report.json"
    json_path.write_text(json_report)
    logger.info("JSON report written to %s", json_path)

    md_path = out / "report.md"
    md_path.write_text(markdown_report)
    logger.info("Markdown report written to %s", md_path)
