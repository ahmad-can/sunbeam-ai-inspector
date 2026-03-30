"""Regex-based pattern scanner for log events."""

from __future__ import annotations

import bisect
import logging
import re
from datetime import timedelta
from pathlib import Path

import yaml

from sunbeam_rca.models import FailurePattern, LogEvent, PatternMatch

logger = logging.getLogger(__name__)

_DEFAULT_PATTERNS_FILE = Path(__file__).parent / "patterns.yaml"

MAX_CONTEXT_EVENTS = 10


def load_patterns(path: str | Path | None = None) -> list[FailurePattern]:
    """Load failure patterns from a YAML file."""
    p = Path(path) if path else _DEFAULT_PATTERNS_FILE
    raw = yaml.safe_load(p.read_text())
    return [FailurePattern(**entry) for entry in raw]


MAX_HITS_PER_PATTERN = 50


def match_patterns(
    events: list[LogEvent],
    patterns: list[FailurePattern] | None = None,
) -> list[PatternMatch]:
    """Scan *events* against *patterns* and return all matches.

    For each match, a context window of surrounding events (by timestamp)
    is attached, preferring events from the same source file / source type.

    To avoid O(n^2) performance on large syslogs with repetitive errors,
    each pattern is capped at MAX_HITS_PER_PATTERN raw matches. The
    score_node deduplicates further (keeping only the first match per
    pattern_id), so collecting all duplicates is unnecessary.
    """
    if patterns is None:
        patterns = load_patterns()

    compiled: list[tuple[FailurePattern, re.Pattern]] = [
        (pat, re.compile(pat.regex, re.IGNORECASE))
        for pat in patterns
    ]

    matches: list[PatternMatch] = []
    hit_counts: dict[str, int] = {}
    ts_cache: dict = {}

    for event in events:
        for pat, regex in compiled:
            if hit_counts.get(pat.id, 0) >= MAX_HITS_PER_PATTERN:
                continue
            if event.source_type.value not in pat.source_types:
                continue
            if regex.search(event.message):
                hit_counts[pat.id] = hit_counts.get(pat.id, 0) + 1
                ctx = _gather_context(
                    events, event, timedelta(seconds=pat.context_window_secs),
                    _ts_cache=ts_cache,
                )
                matches.append(
                    PatternMatch(
                        pattern_id=pat.id,
                        category=pat.category,
                        description=pat.description,
                        severity=pat.severity,
                        matched_event=event,
                        context_events=ctx,
                    )
                )

    logger.info("Pattern matching: %d matches from %d events", len(matches), len(events))
    return matches


def _gather_context(
    events: list[LogEvent],
    anchor: LogEvent,
    window: timedelta,
    _ts_cache: dict | None = None,
) -> list[LogEvent]:
    """Return events within *window* of *anchor*, excluding the anchor itself.

    Prioritises events from the same source file, then same source_type,
    then other sources.  Only ERROR/WARNING events are included.

    Uses binary search on the pre-sorted events list for O(log n) window
    lookup instead of O(n) full scan.
    """
    t_start = anchor.timestamp - window
    t_end = anchor.timestamp + window

    if _ts_cache is not None and "timestamps" in _ts_cache:
        timestamps = _ts_cache["timestamps"]
    else:
        timestamps = [e.timestamp for e in events]
        if _ts_cache is not None:
            _ts_cache["timestamps"] = timestamps

    lo = bisect.bisect_left(timestamps, t_start)
    hi = bisect.bisect_right(timestamps, t_end)

    same_file: list[LogEvent] = []
    same_type: list[LogEvent] = []
    other: list[LogEvent] = []
    total = 0

    for i in range(lo, hi):
        e = events[i]
        if e is anchor:
            continue
        if e.level.value not in ("ERROR", "WARNING"):
            continue

        if e.source_file == anchor.source_file:
            same_file.append(e)
        elif e.source_type == anchor.source_type:
            same_type.append(e)
        else:
            other.append(e)

        total += 1
        if total >= MAX_CONTEXT_EVENTS * 3:
            break

    result = same_file + same_type + other
    return result[:MAX_CONTEXT_EVENTS]
