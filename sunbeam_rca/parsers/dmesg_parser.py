"""Parser for dmesg / kern.log files.

dmesg lines typically look like::

    [    3.456789] Some kernel message
    [12345.678901] error: something failed

Or in syslog format (kern.log)::

    2026-02-11T09:34:19.166559+00:00 hostname kernel: [    3.456] message
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from sunbeam_rca.models import LogEvent, LogLevel, SourceType
from sunbeam_rca.parsers.base import BaseParser
from sunbeam_rca.utils.timestamps import parse_syslog_ts

_DMESG_RE = re.compile(
    r"^\[\s*(\d+\.\d+)\]\s+(.*)$"
)

_KERN_LOG_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+[+-]\d{2}:\d{2})\s+"
    r"(\S+)\s+"
    r"kernel:\s+"
    r"(?:\[\s*\d+\.\d+\]\s+)?"
    r"(.*)$"
)


def _detect_level(message: str) -> LogLevel:
    lower = message.lower()
    if any(kw in lower for kw in (
        "error", "fail", "panic", "bug", "oom", "out of memory",
        "killed process", "segfault", "oops", "call trace",
    )):
        return LogLevel.ERROR
    if any(kw in lower for kw in ("warn", "deprecated")):
        return LogLevel.WARNING
    return LogLevel.INFO


def _estimate_boot_time(file_path: str, max_uptime: float) -> datetime:
    """Estimate boot time from the file's mtime and the largest uptime offset.

    boot_time ≈ file_mtime - max_uptime, since the dmesg dump was
    captured at roughly file_mtime and the highest uptime reflects how
    long the system had been running.
    """
    try:
        mtime = os.path.getmtime(file_path)
        return datetime.fromtimestamp(mtime - max_uptime, tz=timezone.utc)
    except (OSError, ValueError):
        return datetime(2026, 1, 1, tzinfo=timezone.utc)


class DmesgParser(BaseParser):
    """Parse dmesg or kern.log files into structured events."""

    def parse(self, file_path: str) -> list[LogEvent]:
        lines = self._read_lines(file_path)
        events: list[LogEvent] = []

        for line_num, raw_line in enumerate(lines, start=1):
            event = self._try_kern_log(raw_line, file_path, line_num)
            if not event:
                event = self._try_dmesg(raw_line, file_path, line_num)
            if event:
                events.append(event)

        self._fixup_dmesg_timestamps(events, file_path)
        return events

    def _try_kern_log(
        self, line: str, file_path: str, line_num: int
    ) -> LogEvent | None:
        m = _KERN_LOG_RE.match(line)
        if not m:
            return None

        ts = parse_syslog_ts(m.group(1))
        if ts is None:
            return None

        message = m.group(3)
        return LogEvent(
            timestamp=ts,
            source_file=file_path,
            line_number=line_num,
            level=_detect_level(message),
            message=message,
            source_type=SourceType.DMESG,
            metadata={"hostname": m.group(2)},
        )

    def _try_dmesg(
        self, line: str, file_path: str, line_num: int
    ) -> LogEvent | None:
        m = _DMESG_RE.match(line)
        if not m:
            return None

        uptime_secs = float(m.group(1))
        message = m.group(2)

        ts = datetime.fromtimestamp(uptime_secs, tz=timezone.utc)

        return LogEvent(
            timestamp=ts,
            source_file=file_path,
            line_number=line_num,
            level=_detect_level(message),
            message=message,
            source_type=SourceType.DMESG,
            metadata={"uptime_secs": uptime_secs},
        )

    @staticmethod
    def _fixup_dmesg_timestamps(
        events: list[LogEvent], file_path: str
    ) -> None:
        """Convert raw-dmesg uptime-based timestamps to real wall-clock times.

        Events parsed from kern.log already have real timestamps and are
        left untouched.
        """
        uptime_events = [
            e for e in events if "uptime_secs" in e.metadata
        ]
        if not uptime_events:
            return

        max_uptime = max(e.metadata["uptime_secs"] for e in uptime_events)
        boot_time = _estimate_boot_time(file_path, max_uptime)

        from datetime import timedelta

        for ev in uptime_events:
            ev.timestamp = boot_time + timedelta(
                seconds=ev.metadata["uptime_secs"]
            )
