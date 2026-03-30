"""score_node -- Rank root-cause candidates with deterministic scoring.

Scoring factors (v3 -- evidence-neutral, temporal-aware, baseline-aware):
- Base severity (0-10 -> 0.0-1.0)
- Failure window multiplier: events within the failure window get a large bonus
- Resolved-error penalty: errors that stopped >30min before failure get penalised
- Temporal proximity to pipeline failure (+0.15 max)
- Cross-source corroboration (+0.15 per extra source)
- Frequency / persistence signal (+0.10 max, capped to avoid domination)
- Causal chain position (upstream +0.20, downstream -0.15)
- Transitive depth penalty (-0.05 per hop from root cause)
- Noise filter penalty (-0.25 max)
- State snapshot penalty (-0.10 for synthetic observation events)
- LLM root-cause boost (+0.1)
- Domain agent confidence boost (+0.15 for patterns in failed domains)
- Cross-domain corroboration bonus (+0.1)
- Direct failure evidence bonus (+0.25 for patterns matching the exact failure)
- LLM discovery bonus (+0.15 for candidates surfaced by deep analysis)
- Baseline novel bonus (+0.30 for patterns NOT in successful-run baseline)
- Baseline noise penalty (-0.25 for patterns also present in successful runs)
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone

from sunbeam_rca.analysis.baseline import compute_baseline_adjustment, load_baseline
from sunbeam_rca.analysis.causal_chains import (
    compute_causal_adjustments,
    compute_causal_depth,
)
from sunbeam_rca.analysis.noise_filter import compute_noise_penalty, load_noise_filters
from sunbeam_rca.models import Evidence, RootCauseCandidate
from sunbeam_rca.state import RCAState

STATE_SNAPSHOT_PENALTY = 0.10
FAILURE_WINDOW_BONUS = 0.25
RESOLVED_ERROR_PENALTY = 0.30
DIRECT_FAILURE_BONUS = 0.25
LLM_DISCOVERY_BONUS = 0.15

logger = logging.getLogger(__name__)


def score_node(state: RCAState) -> dict:
    """Score and rank pattern matches into root-cause candidates."""
    pattern_matches = state.get("pattern_matches", [])
    failure_ts_str = state.get("failure_timestamp", "")
    fw_start_str = state.get("failure_window_start", "")
    fw_end_str = state.get("failure_window_end", "")
    correlated = state.get("correlated_findings", [])
    domain_findings = state.get("domain_findings", [])

    failure_ts = _parse_ts(failure_ts_str)
    fw_start = _parse_ts(fw_start_str)
    fw_end = _parse_ts(fw_end_str)
    llm_root_ids = _extract_llm_root_cause_ids(correlated)
    noise_filters = load_noise_filters()
    baseline = load_baseline()

    domain_info = _extract_domain_info(domain_findings, correlated)

    freq_counts = Counter(m.get("pattern_id", "") for m in pattern_matches)

    all_matched_ids = set(freq_counts.keys())
    all_matched_ids.discard("")

    last_seen = _compute_last_seen(pattern_matches)

    causal_adjustments = compute_causal_adjustments(all_matched_ids)

    best_matches = _select_best_matches(pattern_matches, fw_start, fw_end, failure_ts)

    scored: list[tuple[float, RootCauseCandidate]] = []

    for m in best_matches:
        pid = m.get("pattern_id", "")
        severity = m.get("severity", 5)
        base_score = severity / 10.0

        matched_event = m.get("matched_event", {})
        event_ts = _parse_ts(matched_event.get("timestamp", ""))
        context_events = m.get("context_events", [])
        metadata = matched_event.get("metadata", {})
        is_synthetic = metadata.get("synthetic", False)
        is_llm_discovered = metadata.get("llm_discovered", False)
        is_state_snapshot = metadata.get("observation_type") == "state_snapshot"

        if is_synthetic:
            proximity_bonus = 0.10
            timing_bonus = 0.0
        else:
            proximity_bonus = _temporal_proximity_score(event_ts, failure_ts)
            timing_bonus = _pre_post_failure_score(event_ts, failure_ts)

        snapshot_penalty = STATE_SNAPSHOT_PENALTY if is_state_snapshot else 0.0

        in_window = _is_in_failure_window(event_ts, fw_start, fw_end)
        window_bonus = FAILURE_WINDOW_BONUS if in_window else 0.0

        last_ts = last_seen.get(pid)
        resolved_penalty = _resolved_error_penalty(last_ts, failure_ts)

        is_direct = _is_direct_failure_evidence(metadata)
        direct_bonus = DIRECT_FAILURE_BONUS if is_direct else 0.0

        source_types = {matched_event.get("source_type", "")}
        for ce in context_events:
            if ce.get("level") in ("ERROR", "WARNING"):
                source_types.add(ce.get("source_type", ""))
        source_types.discard("")
        corroboration_bonus = max(0, (len(source_types) - 1)) * 0.15

        freq_bonus = _frequency_bonus(freq_counts.get(pid, 1))

        causal_adj = causal_adjustments.get(pid, 0.0)
        causal_depth = compute_causal_depth(pid, all_matched_ids)

        event_msg = matched_event.get("message", "")
        noise_penalty = compute_noise_penalty(event_msg, noise_filters)

        llm_bonus = 0.1 if pid in llm_root_ids else 0.0
        llm_discovery_bonus = LLM_DISCOVERY_BONUS if is_llm_discovered else 0.0

        bl_bonus, bl_penalty, bl_reason = compute_baseline_adjustment(pid, baseline)

        di = domain_info.get(pid, {})
        domain_confidence_bonus = di.get("domain_confidence", 0.0)
        cross_domain_bonus = di.get("cross_domain", 0.0)

        raw_confidence = (
            base_score
            + proximity_bonus
            + timing_bonus
            + corroboration_bonus
            + freq_bonus
            + causal_adj
            - noise_penalty
            - snapshot_penalty
            + window_bonus
            - resolved_penalty
            + direct_bonus
            + llm_bonus
            + llm_discovery_bonus
            + domain_confidence_bonus
            + cross_domain_bonus
            + bl_bonus
            - bl_penalty
        )
        confidence = min(1.0, max(0.0, raw_confidence))

        evidence_list = _build_evidence(matched_event, context_events)

        llm_reasoning = ""
        for finding in correlated:
            if finding.get("pattern_id") == pid:
                llm_reasoning = finding.get("reasoning", "")
                break

        scored.append((
            raw_confidence,
            RootCauseCandidate(
                pattern_id=pid,
                category=m.get("category", ""),
                description=m.get("description", ""),
                confidence=round(confidence, 3),
                evidence=evidence_list,
                timeline_start=matched_event.get("timestamp", ""),
                timeline_end=matched_event.get("timestamp", ""),
                explanation=_build_explanation(
                    base_score,
                    proximity_bonus,
                    timing_bonus,
                    corroboration_bonus,
                    freq_bonus,
                    freq_counts.get(pid, 1),
                    causal_adj,
                    causal_depth,
                    noise_penalty,
                    snapshot_penalty,
                    window_bonus,
                    resolved_penalty,
                    direct_bonus,
                    llm_bonus,
                    is_synthetic,
                    domain_confidence_bonus,
                    cross_domain_bonus,
                    llm_discovery_bonus,
                    bl_bonus,
                    bl_penalty,
                    bl_reason,
                ),
                llm_reasoning=llm_reasoning,
            ),
        ))

    scored.sort(key=lambda t: t[0], reverse=True)
    for i, (_, c) in enumerate(scored, 1):
        c.rank = i

    candidate_dicts = [c.model_dump(mode="json") for _, c in scored]
    logger.info("Scored %d candidates", len(candidate_dicts))

    return {"ranked_candidates": candidate_dicts}


def _parse_ts(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _temporal_proximity_score(
    event_ts: datetime | None, failure_ts: datetime | None
) -> float:
    if not event_ts or not failure_ts:
        return 0.0
    delta = abs((event_ts - failure_ts).total_seconds())
    if delta <= 60:
        return 0.15
    if delta <= 300:
        return 0.10
    if delta <= 600:
        return 0.05
    return 0.0


def _pre_post_failure_score(
    event_ts: datetime | None, failure_ts: datetime | None
) -> float:
    if not event_ts or not failure_ts:
        return 0.0
    return 0.05 if event_ts <= failure_ts else -0.10


def _is_in_failure_window(
    event_ts: datetime | None,
    fw_start: datetime | None,
    fw_end: datetime | None,
) -> bool:
    """Return True if the event falls within the failure window."""
    if not event_ts or not fw_start or not fw_end:
        return False
    return fw_start <= event_ts <= fw_end


def _select_best_matches(
    pattern_matches: list[dict],
    fw_start: datetime | None,
    fw_end: datetime | None,
    failure_ts: datetime | None,
) -> list[dict]:
    """For each pattern_id, select the single best representative match.

    Prefers matches inside the failure window, then closest to
    the failure timestamp, to avoid early bootstrap noise overshadowing
    the actual failure event.
    """
    buckets: dict[str, list[dict]] = {}
    for m in pattern_matches:
        pid = m.get("pattern_id", "")
        if not pid:
            continue
        buckets.setdefault(pid, []).append(m)

    result: list[dict] = []
    for pid, matches in buckets.items():
        if len(matches) == 1:
            result.append(matches[0])
            continue

        category = matches[0].get("category", "")

        def _score_match(m: dict) -> tuple[int, int, int, float]:
            ev = m.get("matched_event", {})
            ts = _parse_ts(ev.get("timestamp", ""))
            in_window = 1 if _is_in_failure_window(ts, fw_start, fw_end) else 0
            st = ev.get("source_type", "")
            source_match = 1 if (category and st == category) else 0
            prefer_sos = 1 if st != "pipeline" else 0
            proximity = 0.0
            if ts and failure_ts:
                proximity = -abs((ts - failure_ts).total_seconds())
            return (in_window, source_match, prefer_sos, proximity)

        best = max(matches, key=_score_match)
        result.append(best)
    return result


def _compute_last_seen(pattern_matches: list[dict]) -> dict[str, datetime | None]:
    """For each pattern, find the timestamp of its LAST occurrence."""
    last: dict[str, datetime | None] = {}
    for m in pattern_matches:
        pid = m.get("pattern_id", "")
        if not pid:
            continue
        ev = m.get("matched_event", {})
        ts = _parse_ts(ev.get("timestamp", ""))
        if ts:
            existing = last.get(pid)
            if existing is None or ts > existing:
                last[pid] = ts
    return last


def _resolved_error_penalty(
    last_occurrence: datetime | None, failure_ts: datetime | None
) -> float:
    """Penalise patterns whose last occurrence was >30 min before failure.

    This catches bootstrap-time errors (e.g. etcd no-leader during setup)
    that resolved long before the actual test failure.
    """
    if not last_occurrence or not failure_ts:
        return 0.0
    delta = (failure_ts - last_occurrence).total_seconds()
    if delta > 3600:
        return RESOLVED_ERROR_PENALTY
    if delta > 1800:
        return RESOLVED_ERROR_PENALTY * 0.7
    return 0.0


def _is_direct_failure_evidence(metadata: dict) -> bool:
    """Check if this pattern match contains direct failure evidence.

    Direct evidence = the pattern matched the actual error that caused
    the pipeline to fail (e.g., a test assertion, task error, missing metric).
    """
    return bool(
        metadata.get("task_error")
        or metadata.get("assertion")
        or metadata.get("failed_test")
        or metadata.get("task_error_detail")
    )


def _frequency_bonus(count: int) -> float:
    """Capped frequency bonus to prevent high-volume patterns from dominating."""
    if count >= 30:
        return 0.10
    if count >= 10:
        return 0.07
    if count >= 3:
        return 0.04
    return 0.0


def _extract_llm_root_cause_ids(correlated: list[dict]) -> set[str]:
    ids: set[str] = set()
    for finding in correlated:
        if finding.get("role") == "root_cause":
            pid = finding.get("pattern_id", "")
            if pid:
                ids.add(pid)
    return ids


def _build_evidence(
    matched_event: dict, context_events: list[dict]
) -> list[Evidence]:
    items: list[Evidence] = []
    if matched_event:
        items.append(
            Evidence(
                source_file=matched_event.get("source_file", ""),
                line_number=matched_event.get("line_number", 0),
                timestamp=matched_event.get("timestamp", "1970-01-01T00:00:00+00:00"),
                message=matched_event.get("message", "")[:500],
                source_type=matched_event.get("source_type", ""),
            )
        )
    for ce in context_events[:5]:
        if ce.get("level") in ("ERROR", "WARNING"):
            items.append(
                Evidence(
                    source_file=ce.get("source_file", ""),
                    line_number=ce.get("line_number", 0),
                    timestamp=ce.get("timestamp", "1970-01-01T00:00:00+00:00"),
                    message=ce.get("message", "")[:500],
                    source_type=ce.get("source_type", ""),
                )
            )
    return items


def _extract_domain_info(
    domain_findings: list[dict],
    correlated: list[dict],
) -> dict[str, dict[str, float]]:
    """Extract per-pattern scoring adjustments from domain findings.

    Returns {pattern_id: {domain_confidence, cross_domain}}.
    """
    info: dict[str, dict[str, float]] = {}

    failed_domain_patterns: set[str] = set()
    domain_pattern_domains: dict[str, set[str]] = {}

    for df in domain_findings:
        domain = df.get("domain", "")
        status = df.get("status", "healthy")
        hypotheses = df.get("hypotheses", [])

        for h in hypotheses:
            pid = h.get("pattern_id", "")
            if not pid:
                continue

            domain_pattern_domains.setdefault(pid, set()).add(domain)

            if status in ("failed", "degraded"):
                failed_domain_patterns.add(pid)
                conf = h.get("confidence", "medium")
                bonus = 0.15 if conf == "high" else 0.10 if conf == "medium" else 0.05
                existing = info.get(pid, {}).get("domain_confidence", 0.0)
                info.setdefault(pid, {})["domain_confidence"] = max(existing, bonus)

    for pid, domains in domain_pattern_domains.items():
        if len(domains) >= 2:
            info.setdefault(pid, {})["cross_domain"] = 0.10

    return info


def _build_explanation(
    base: float,
    proximity: float,
    timing: float,
    corroboration: float,
    freq: float,
    freq_count: int,
    causal: float,
    causal_depth: int,
    noise: float,
    snapshot_penalty: float,
    window_bonus: float,
    resolved_penalty: float,
    direct_bonus: float,
    llm: float,
    is_synthetic: bool = False,
    domain_confidence: float = 0.0,
    cross_domain: float = 0.0,
    llm_discovery: float = 0.0,
    baseline_bonus: float = 0.0,
    baseline_penalty: float = 0.0,
    baseline_reason: str = "",
) -> str:
    parts = [f"base_severity={base:.2f}"]
    if is_synthetic and not llm_discovery:
        parts.append("synthetic_state=+0.10")
    elif proximity:
        parts.append(f"temporal_proximity=+{proximity:.2f}")
    if not is_synthetic:
        if timing > 0:
            parts.append(f"pre_failure=+{timing:.2f}")
        elif timing < 0:
            parts.append(f"post_failure={timing:.2f}")
    if window_bonus:
        parts.append(f"in_failure_window=+{window_bonus:.2f}")
    if resolved_penalty:
        parts.append(f"resolved_error=-{resolved_penalty:.2f}")
    if direct_bonus:
        parts.append(f"direct_failure_evidence=+{direct_bonus:.2f}")
    if corroboration:
        parts.append(f"cross_source=+{corroboration:.2f}")
    if freq:
        parts.append(f"frequency({freq_count}x)=+{freq:.2f}")
    if causal > 0:
        parts.append(f"causal_upstream=+{causal:.2f}")
    elif causal < 0:
        parts.append(f"causal_downstream={causal:.2f}")
    if causal_depth > 0:
        parts.append(f"causal_depth={causal_depth}_hops_from_root")
    if noise:
        parts.append(f"noise_penalty=-{noise:.2f}")
    if snapshot_penalty:
        parts.append(f"state_snapshot=-{snapshot_penalty:.2f}")
    if llm:
        parts.append(f"llm_root_cause=+{llm:.2f}")
    if llm_discovery:
        parts.append(f"llm_discovered=+{llm_discovery:.2f}")
    if domain_confidence:
        parts.append(f"domain_agent=+{domain_confidence:.2f}")
    if cross_domain:
        parts.append(f"cross_domain=+{cross_domain:.2f}")
    if baseline_bonus:
        parts.append(f"baseline_novel=+{baseline_bonus:.2f}")
    if baseline_penalty:
        reason_tag = f"({baseline_reason})" if baseline_reason else ""
        parts.append(f"baseline_noise=-{baseline_penalty:.2f}{reason_tag}")
    return "Confidence breakdown: " + ", ".join(parts)
