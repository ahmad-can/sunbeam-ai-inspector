"""Parser for cloud-init logs.

Handles two file formats:
- ``cloud-init.log``: structured ``YYYY-MM-DD HH:MM:SS,mmm - module[LEVEL]: msg``
- ``cloud-init-output.log``: free-form bootstrap output (curl errors, apt, etc.)
"""

from __future__ import annotations

import re

from sunbeam_rca.models import LogEvent, LogLevel, SourceType
from sunbeam_rca.parsers.base import BaseParser
from sunbeam_rca.utils.timestamps import parse_juju_ts

_CLOUD_INIT_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+-\s+"
    r"(\S+)\[(\w+)\]:\s+"
    r"(.*)$"
)

_LEVEL_MAP = {
    "ERROR": LogLevel.ERROR,
    "WARNING": LogLevel.WARNING,
    "WARN": LogLevel.WARNING,
    "INFO": LogLevel.INFO,
    "DEBUG": LogLevel.DEBUG,
}

_ERROR_KEYWORDS = re.compile(
    r"curl.*Failed|curl.*[Tt]imeout|"
    r"Failed to connect|"
    r"Resolving timed out|"
    r"Network is unreachable|"
    r"Failed posting event|"
    r"Max retries exceeded|"
    r"failed because the control process|"
    r"FAILURE|"
    r"Failed with result",
    re.IGNORECASE,
)


class CloudInitParser(BaseParser):
    """Parse cloud-init.log into structured events."""

    def parse(self, file_path: str) -> list[LogEvent]:
        lines = self._read_lines(file_path)
        events: list[LogEvent] = []

        for line_num, raw_line in enumerate(lines, start=1):
            m = _CLOUD_INIT_RE.match(raw_line)
            if not m:
                continue

            ts_raw = m.group(1)
            module = m.group(2)
            level_str = m.group(3).upper()
            message = m.group(4)

            ts = parse_juju_ts(ts_raw)
            if ts is None:
                continue

            level = _LEVEL_MAP.get(level_str, LogLevel.UNKNOWN)
            if level == LogLevel.UNKNOWN and _ERROR_KEYWORDS.search(message):
                level = LogLevel.ERROR

            events.append(
                LogEvent(
                    timestamp=ts,
                    source_file=file_path,
                    line_number=line_num,
                    level=level,
                    message=message,
                    source_type=SourceType.CLOUD_INIT,
                    metadata={"module": module},
                )
            )

        return events


class CloudInitOutputParser(BaseParser):
    """Parse cloud-init-output.log (free-form bootstrap output).

    Only extracts lines that contain error-like keywords since this file
    has no structured timestamp per line.  Uses the file modification time
    as a fallback timestamp.
    """

    def parse(self, file_path: str) -> list[LogEvent]:
        lines = self._read_lines(file_path)
        events: list[LogEvent] = []

        from datetime import datetime, timezone
        from pathlib import Path

        try:
            mtime = Path(file_path).stat().st_mtime
            file_ts = datetime.fromtimestamp(mtime, tz=timezone.utc)
        except OSError:
            file_ts = datetime.now(timezone.utc)

        for line_num, raw_line in enumerate(lines, start=1):
            if not _ERROR_KEYWORDS.search(raw_line):
                continue

            events.append(
                LogEvent(
                    timestamp=file_ts,
                    source_file=file_path,
                    line_number=line_num,
                    level=LogLevel.ERROR,
                    message=raw_line.strip(),
                    source_type=SourceType.CLOUD_INIT,
                    metadata={"format": "output_log"},
                )
            )

        return events
