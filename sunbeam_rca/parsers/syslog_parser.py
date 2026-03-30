"""Parser for syslog files.

Expected line format::

    2026-02-11T09:34:19.166559+00:00 hostname process[pid]: message
"""

from __future__ import annotations

import re

from sunbeam_rca.models import LogEvent, LogLevel, SourceType
from sunbeam_rca.parsers.base import BaseParser
from sunbeam_rca.utils.timestamps import parse_syslog_ts

_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+[+-]\d{2}:\d{2})\s+"
    r"(\S+)\s+"
    r"(\S+?)(?:\[(\d+)\])?:\s+"
    r"(.*)$"
)


def _detect_level(message: str, process: str) -> LogLevel:
    lower = message.lower()
    if any(kw in lower for kw in (
        "error", "fail", "panic", "critical",
        "oom", "out of memory", "killed process", "segfault",
    )):
        return LogLevel.ERROR
    if any(kw in lower for kw in ("warn",)):
        return LogLevel.WARNING
    if "debug" in lower:
        return LogLevel.DEBUG
    return LogLevel.INFO


class SyslogParser(BaseParser):
    """Parse a syslog file into structured events."""

    def parse(self, file_path: str) -> list[LogEvent]:
        lines = self._read_lines(file_path)
        events: list[LogEvent] = []

        for line_num, raw_line in enumerate(lines, start=1):
            m = _LINE_RE.match(raw_line)
            if not m:
                continue

            ts_raw = m.group(1)
            hostname = m.group(2)
            process = m.group(3)
            pid = m.group(4)
            message = m.group(5)

            ts = parse_syslog_ts(ts_raw)
            if ts is None:
                continue

            metadata: dict = {"hostname": hostname, "process": process}
            if pid:
                metadata["pid"] = int(pid)

            events.append(
                LogEvent(
                    timestamp=ts,
                    source_file=file_path,
                    line_number=line_num,
                    level=_detect_level(message, process),
                    message=message,
                    source_type=SourceType.SYSLOG,
                    metadata=metadata,
                )
            )

        return events
