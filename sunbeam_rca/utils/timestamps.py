"""Timestamp parsing and normalisation helpers."""

from __future__ import annotations

import re
from datetime import datetime, timezone

_ISO_NANO_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d+)Z"
)

_ISO_OFFSET_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d+)([+-]\d{2}:\d{2})"
)


def parse_github_actions_ts(raw: str) -> datetime | None:
    """Parse ``2026-02-11T09:12:42.4300801Z`` into a UTC datetime."""
    m = _ISO_NANO_RE.match(raw)
    if not m:
        return None
    base, frac = m.group(1), m.group(2)
    frac_us = frac[:6].ljust(6, "0")
    return datetime.fromisoformat(f"{base}.{frac_us}+00:00")


def parse_syslog_ts(raw: str) -> datetime | None:
    """Parse ``2026-02-11T09:34:19.166559+00:00`` into a UTC datetime."""
    m = _ISO_OFFSET_RE.match(raw)
    if not m:
        try:
            return datetime.fromisoformat(raw).astimezone(timezone.utc)
        except (ValueError, TypeError):
            return None
    base, frac, offset = m.group(1), m.group(2), m.group(3)
    frac_us = frac[:6].ljust(6, "0")
    dt = datetime.fromisoformat(f"{base}.{frac_us}{offset}")
    return dt.astimezone(timezone.utc)


def parse_juju_ts(raw: str) -> datetime | None:
    """Parse ``2026-02-11 09:45:05`` (assumed UTC) into a UTC datetime."""
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, TypeError):
        return None


def ensure_utc(dt: datetime) -> datetime:
    """Ensure *dt* is timezone-aware and in UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
