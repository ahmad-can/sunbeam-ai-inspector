"""Parser for GitHub Actions job log files.

Line format::

    2026-02-11T09:12:42.4300801Z <message>

Special markers:
- ``##[error]``   — error annotation
- ``##[group]``   — collapsible section start
- ``##[endgroup]`` — collapsible section end

This parser also performs a second pass to extract structured information
about the *exact* failure point: which test/command failed, the assertion
message, and test-suite result summaries (passed/failed/skipped counts).
"""

from __future__ import annotations

import re

from sunbeam_rca.models import LogEvent, LogLevel, SourceType
from sunbeam_rca.parsers.base import BaseParser
from sunbeam_rca.utils.timestamps import parse_github_actions_ts

_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)\s(.*)$"
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

_TEST_RESULT_RE = re.compile(
    r"(\d+)\s+failed.*?(\d+)\s+passed", re.IGNORECASE,
)
_PYTEST_SUMMARY_RE = re.compile(
    r"=+\s+(\d+)\s+failed.*?(\d+)\s+passed.*?=+", re.IGNORECASE,
)
_VALIDATION_TOTALS_RE = re.compile(
    r"Ran:\s+(\d+)\s+tests.*?Passed:\s+(\d+).*?Failed:\s+(\d+)",
    re.IGNORECASE | re.DOTALL,
)
_ASSERTION_RE = re.compile(
    r"(Assertion(?:Error)?:\s*.+)", re.IGNORECASE,
)
_PYTEST_FAILED_RE = re.compile(
    r"FAILED\s+(\S+::\S+)", re.IGNORECASE,
)
_TASK_ERROR_RE = re.compile(
    r"task error:\s*Task\s+\d+.*?status\s+'failed'", re.IGNORECASE,
)


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _detect_level(message: str) -> LogLevel:
    lower = message.lower()
    if "##[error]" in lower:
        return LogLevel.ERROR
    if "##[warning]" in lower:
        return LogLevel.WARNING
    if "error" in lower or "traceback" in lower or "exception" in lower:
        return LogLevel.ERROR
    if "warning" in lower or "warn" in lower:
        return LogLevel.WARNING
    return LogLevel.INFO


class PipelineParser(BaseParser):
    """Parse a single GitHub Actions job log file."""

    def parse(self, file_path: str) -> list[LogEvent]:
        lines = self._read_lines(file_path)
        events: list[LogEvent] = []
        current_group: str = ""

        for line_num, raw_line in enumerate(lines, start=1):
            m = _LINE_RE.match(raw_line)
            if not m:
                continue

            ts_raw, body = m.group(1), m.group(2)
            ts = parse_github_actions_ts(ts_raw)
            if ts is None:
                continue

            body = _strip_ansi(body)

            if body.startswith("##[group]"):
                current_group = body[len("##[group]"):]
                continue
            if body.startswith("##[endgroup]"):
                current_group = ""
                continue

            level = _detect_level(body)
            metadata: dict = {}
            if current_group:
                metadata["group"] = current_group

            am = _ASSERTION_RE.search(body)
            if am:
                metadata["assertion"] = am.group(1)[:300]

            fm = _PYTEST_FAILED_RE.search(body)
            if fm:
                metadata["failed_test"] = fm.group(1)

            if _TASK_ERROR_RE.search(body):
                metadata["task_error"] = True

            events.append(
                LogEvent(
                    timestamp=ts,
                    source_file=file_path,
                    line_number=line_num,
                    level=level,
                    message=body,
                    source_type=SourceType.PIPELINE,
                    metadata=metadata,
                )
            )

        self._inject_failure_context(events)
        return events

    @staticmethod
    def _inject_failure_context(events: list[LogEvent]) -> None:
        """Walk backwards from the first ##[error] to enrich the event with
        information about exactly which test or command failed and why."""
        error_idx = None
        for i, ev in enumerate(events):
            if "##[error]" in ev.message:
                error_idx = i
                break

        if error_idx is None:
            return

        window = events[max(0, error_idx - 80): error_idx + 1]

        failed_test = ""
        assertion_msg = ""
        test_summary = ""
        task_error = ""

        for ev in reversed(window):
            if not failed_test and ev.metadata.get("failed_test"):
                failed_test = ev.metadata["failed_test"]
            if not assertion_msg and ev.metadata.get("assertion"):
                assertion_msg = ev.metadata["assertion"]
            if not task_error and ev.metadata.get("task_error"):
                task_error = ev.message[:300]

            vm = _VALIDATION_TOTALS_RE.search(ev.message)
            if vm and not test_summary:
                test_summary = (
                    f"Ran {vm.group(1)} tests: "
                    f"{vm.group(2)} passed, {vm.group(3)} failed"
                )

        error_ev = events[error_idx]
        if failed_test:
            error_ev.metadata["failed_test"] = failed_test
        if assertion_msg:
            error_ev.metadata["assertion"] = assertion_msg
        if test_summary:
            error_ev.metadata["test_summary"] = test_summary
        if task_error:
            error_ev.metadata["task_error_detail"] = task_error
