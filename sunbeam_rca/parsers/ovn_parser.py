"""Parser for OVN controller and OVS logs from openstack-hypervisor snap.

OVN log format::

    2026-02-11T10:28:18.586Z|00001|vlog|INFO|opened log file ...
    2026-02-11T10:30:28.931Z|00009|reconnect|INFO|ssl:10.241.36.131:6642: connecting...
    2026-02-11T10:37:18.267Z|00027|stream_ssl|WARN|SSL_read: system error (Connection reset)

Fields: timestamp | sequence | module | level | message
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sunbeam_rca.models import LogEvent, LogLevel, SourceType
from sunbeam_rca.parsers.base import BaseParser

_OVN_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)"
    r"\|(\d+)"
    r"\|(\S+)"
    r"\|(\w+)"
    r"\|(.*)$"
)

_OVN_LEVEL_MAP = {
    "EMER": LogLevel.ERROR,
    "ERR": LogLevel.ERROR,
    "WARN": LogLevel.WARNING,
    "INFO": LogLevel.INFO,
    "DBG": LogLevel.DEBUG,
}


class OvnParser(BaseParser):
    """Parse OVN/OVS log files into structured events."""

    def parse(self, file_path: str) -> list[LogEvent]:
        lines = self._read_lines(file_path)
        events: list[LogEvent] = []

        for line_num, raw_line in enumerate(lines, start=1):
            m = _OVN_LINE_RE.match(raw_line)
            if not m:
                continue

            ts_raw = m.group(1)
            seq = m.group(2)
            module = m.group(3)
            level_str = m.group(4)
            message = m.group(5)

            ts = _parse_ovn_ts(ts_raw)
            if ts is None:
                continue

            level = _OVN_LEVEL_MAP.get(level_str, LogLevel.INFO)

            events.append(
                LogEvent(
                    timestamp=ts,
                    source_file=file_path,
                    line_number=line_num,
                    level=level,
                    message=message,
                    source_type=SourceType.SYSLOG,
                    metadata={
                        "process": "ovn-controller",
                        "module": module,
                        "ovn_seq": seq,
                    },
                )
            )

        return events


def _parse_ovn_ts(raw: str) -> datetime | None:
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None
