"""Parser for Juju unit/machine log files.

Expected line format::

    2026-02-11 09:45:05 INFO juju.worker.dependency engine.go:695 "uniter" manifold ...
    2026-02-11 09:45:05 ERROR juju.worker.dependency engine.go:695 message ...

The format is: ``YYYY-MM-DD HH:MM:SS LEVEL package source:line message``
"""

from __future__ import annotations

import re

from sunbeam_rca.models import LogEvent, LogLevel, SourceType
from sunbeam_rca.parsers.base import BaseParser
from sunbeam_rca.utils.timestamps import parse_juju_ts

_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"(ERROR|WARNING|INFO|DEBUG|TRACE)\s+"
    r"(\S+)\s+"
    r"(\S+:\d+)\s+"
    r"(.*)$"
)

_LEVEL_MAP: dict[str, LogLevel] = {
    "ERROR": LogLevel.ERROR,
    "WARNING": LogLevel.WARNING,
    "INFO": LogLevel.INFO,
    "DEBUG": LogLevel.DEBUG,
    "TRACE": LogLevel.DEBUG,
}


class JujuParser(BaseParser):
    """Parse a Juju log file into structured events."""

    def parse(self, file_path: str) -> list[LogEvent]:
        lines = self._read_lines(file_path)
        events: list[LogEvent] = []

        unit_name = self._extract_unit_name(file_path)

        for line_num, raw_line in enumerate(lines, start=1):
            m = _LINE_RE.match(raw_line)
            if not m:
                continue

            ts_raw = m.group(1)
            level_str = m.group(2)
            package = m.group(3)
            source_loc = m.group(4)
            message = m.group(5)

            ts = parse_juju_ts(ts_raw)
            if ts is None:
                continue

            metadata: dict = {
                "package": package,
                "source_location": source_loc,
            }
            if unit_name:
                metadata["unit"] = unit_name

            events.append(
                LogEvent(
                    timestamp=ts,
                    source_file=file_path,
                    line_number=line_num,
                    level=_LEVEL_MAP.get(level_str, LogLevel.UNKNOWN),
                    message=message,
                    source_type=SourceType.JUJU,
                    metadata=metadata,
                )
            )

        return events

    @staticmethod
    def _extract_unit_name(file_path: str) -> str:
        """Derive Juju unit name from the log filename.

        Example: ``unit-sunbeam-machine-0.log`` -> ``sunbeam-machine/0``
        """
        import os

        basename = os.path.basename(file_path)
        if basename.startswith("unit-") and basename.endswith(".log"):
            name = basename[len("unit-") : -len(".log")]
            parts = name.rsplit("-", 1)
            if len(parts) == 2 and parts[1].isdigit():
                return f"{parts[0]}/{parts[1]}"
        if basename.startswith("machine-") and basename.endswith(".log"):
            name = basename[len("machine-") : -len(".log")]
            return f"machine-{name}"
        return ""
