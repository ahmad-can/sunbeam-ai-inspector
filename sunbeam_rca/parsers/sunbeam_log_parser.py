"""Parser for Sunbeam application logs.

Found at ``home/ubuntu/snap/openstack/common/logs/sunbeam-*.log`` inside
sosreports.  Log format::

    HH:MM:SS,mmm module.name LEVEL message

The date is extracted from the filename (``sunbeam-YYYYMMDD-HHMMSS.*.log``).
Multi-line tracebacks are folded into the preceding event.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from sunbeam_rca.models import LogEvent, LogLevel, SourceType
from sunbeam_rca.parsers.base import BaseParser

_LINE_RE = re.compile(
    r"^(\d{2}:\d{2}:\d{2}),(\d{3})\s+"
    r"(\S+)\s+"
    r"(ERROR|WARNING|INFO|DEBUG|CRITICAL)\s+"
    r"(.*)$"
)

_FILENAME_DATE_RE = re.compile(r"sunbeam-(\d{8})-")

_LEVEL_MAP: dict[str, LogLevel] = {
    "CRITICAL": LogLevel.ERROR,
    "ERROR": LogLevel.ERROR,
    "WARNING": LogLevel.WARNING,
    "INFO": LogLevel.INFO,
    "DEBUG": LogLevel.DEBUG,
}


class SunbeamLogParser(BaseParser):
    """Parse a sunbeam application log into structured events."""

    def parse(self, file_path: str) -> list[LogEvent]:
        lines = self._read_lines(file_path)
        date_prefix = self._extract_date(file_path)
        events: list[LogEvent] = []
        current_event: LogEvent | None = None
        traceback_lines: list[str] = []

        for line_num, raw_line in enumerate(lines, start=1):
            m = _LINE_RE.match(raw_line)
            if m:
                if current_event and traceback_lines:
                    current_event.message += "\n" + "\n".join(traceback_lines[-5:])
                    traceback_lines.clear()

                time_str = m.group(1)
                millis = m.group(2)
                module = m.group(3)
                level_str = m.group(4)
                message = m.group(5)

                ts = self._make_timestamp(date_prefix, time_str, millis)
                if ts is None:
                    continue

                current_event = LogEvent(
                    timestamp=ts,
                    source_file=file_path,
                    line_number=line_num,
                    level=_LEVEL_MAP.get(level_str, LogLevel.UNKNOWN),
                    message=message,
                    source_type=SourceType.SUNBEAM,
                    metadata={"module": module},
                )
                events.append(current_event)
            else:
                stripped = raw_line.strip()
                if stripped and current_event is not None:
                    traceback_lines.append(stripped)

        if current_event and traceback_lines:
            current_event.message += "\n" + "\n".join(traceback_lines[-5:])

        return events

    @staticmethod
    def _extract_date(file_path: str) -> str:
        """Extract YYYYMMDD from filename like ``sunbeam-20260225-212526.*.log``."""
        basename = os.path.basename(file_path)
        m = _FILENAME_DATE_RE.search(basename)
        if m:
            return m.group(1)
        return ""

    @staticmethod
    def _make_timestamp(date_prefix: str, time_str: str, millis: str) -> datetime | None:
        """Combine date from filename with time from log line."""
        if not date_prefix or len(date_prefix) != 8:
            return None
        try:
            combined = f"{date_prefix} {time_str}"
            dt = datetime.strptime(combined, "%Y%m%d %H:%M:%S")
            dt = dt.replace(
                microsecond=int(millis) * 1000,
                tzinfo=timezone.utc,
            )
            return dt
        except (ValueError, TypeError):
            return None
