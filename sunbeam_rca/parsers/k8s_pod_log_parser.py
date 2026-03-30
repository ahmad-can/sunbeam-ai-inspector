"""Parser for Kubernetes pod logs from sosreport cluster-info dumps.

For Juju models of type kubernetes, unit logs are Kubernetes pod logs.
These can appear in:
- sos_commands/kubernetes/cluster-info/  (kubectl cluster-info dump)
- var/log/pods/                          (direct container log files)

Pod log lines are typically free-form but many follow common patterns:
- Go-style: ``2026-02-11T10:17:33.088Z level=ERROR msg="..."``
- Python-style: ``2026-02-11 10:17:33,088 - module - ERROR - message``
- JSON structured: ``{"ts":"...","level":"error","msg":"..."}``

This parser handles all three, plus plain lines with error keywords.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from sunbeam_rca.models import LogEvent, LogLevel, SourceType
from sunbeam_rca.parsers.base import BaseParser

_GO_TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)"
)

_PYTHON_TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[,\.]\d+)"
)

_LEVEL_KEYWORDS = {
    "error": LogLevel.ERROR,
    "fatal": LogLevel.ERROR,
    "panic": LogLevel.ERROR,
    "critical": LogLevel.ERROR,
    "warn": LogLevel.WARNING,
    "warning": LogLevel.WARNING,
    "info": LogLevel.INFO,
    "debug": LogLevel.DEBUG,
}

_ERROR_KEYWORDS = re.compile(
    r"error|fail|panic|fatal|crash|timeout|refused|denied|not found|exception",
    re.IGNORECASE,
)


class K8sPodLogParser(BaseParser):
    """Parse Kubernetes pod / container log files."""

    def parse(self, file_path: str) -> list[LogEvent]:
        lines = self._read_lines(file_path)
        events: list[LogEvent] = []

        pod_name = _extract_pod_name(file_path)

        for line_num, raw_line in enumerate(lines, start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue

            ts, level, message = _parse_line(stripped)
            if ts is None:
                if not _ERROR_KEYWORDS.search(stripped):
                    continue
                ts = _file_mtime(file_path)
                level = LogLevel.ERROR
                message = stripped

            events.append(
                LogEvent(
                    timestamp=ts,
                    source_file=file_path,
                    line_number=line_num,
                    level=level,
                    message=message,
                    source_type=SourceType.KUBERNETES,
                    metadata={"pod": pod_name} if pod_name else {},
                )
            )

        return events


def _parse_line(line: str) -> tuple[datetime | None, LogLevel, str]:
    """Try to extract timestamp, level, and message from a log line."""
    if line.startswith("{"):
        return _parse_json_line(line)

    m = _GO_TS_RE.match(line)
    if m:
        ts = _parse_go_ts(m.group(1))
        rest = line[m.end():].strip()
        level = _detect_level(rest)
        return ts, level, rest

    m = _PYTHON_TS_RE.match(line)
    if m:
        ts = _parse_python_ts(m.group(1))
        rest = line[m.end():].strip().lstrip("- ")
        level = _detect_level(rest)
        return ts, level, rest

    return None, LogLevel.UNKNOWN, line


def _parse_json_line(line: str) -> tuple[datetime | None, LogLevel, str]:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None, LogLevel.UNKNOWN, line
    ts_str = data.get("ts") or data.get("timestamp") or data.get("time") or ""
    ts = _parse_go_ts(ts_str) if ts_str else None
    level_str = (data.get("level") or data.get("severity") or "").lower()
    level = _LEVEL_KEYWORDS.get(level_str, LogLevel.UNKNOWN)
    msg = data.get("msg") or data.get("message") or line
    return ts, level, msg


def _detect_level(text: str) -> LogLevel:
    lower = text.lower()
    for kw, level in _LEVEL_KEYWORDS.items():
        if kw in lower[:50]:
            return level
    if _ERROR_KEYWORDS.search(lower):
        return LogLevel.ERROR
    return LogLevel.INFO


def _parse_go_ts(raw: str) -> datetime | None:
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_python_ts(raw: str) -> datetime | None:
    raw = raw.replace(",", ".")
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S.%f").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _extract_pod_name(file_path: str) -> str:
    """Try to derive a pod/container name from the file path."""
    p = Path(file_path)
    parts = p.parts
    for i, part in enumerate(parts):
        if part in ("pods", "cluster-info"):
            remaining = parts[i + 1 :]
            if remaining:
                return remaining[0]
    return p.stem


def _file_mtime(file_path: str) -> datetime:
    try:
        mtime = Path(file_path).stat().st_mtime
        return datetime.fromtimestamp(mtime, tz=timezone.utc)
    except OSError:
        return datetime.now(timezone.utc)
