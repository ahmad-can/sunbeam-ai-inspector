"""Tests for the OVN log parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from sunbeam_rca.models import LogLevel, SourceType
from sunbeam_rca.parsers.ovn_parser import OvnParser


@pytest.fixture
def ovn_log(tmp_path: Path) -> Path:
    content = (
        "2026-02-11T10:28:18.586Z|00001|vlog|INFO|opened log file /var/snap/ovn/ovn-controller.log\n"
        "2026-02-11T10:30:28.931Z|00009|reconnect|INFO|ssl:10.241.36.131:6642: connecting...\n"
        "2026-02-11T10:30:28.934Z|00010|reconnect|INFO|ssl:10.241.36.131:6642: connected\n"
        "2026-02-11T10:37:18.267Z|00027|stream_ssl|WARN|SSL_read: system error (Connection reset by peer)\n"
        "2026-02-11T10:37:19.500Z|00028|ovsdb-idl|ERR|Unable to open stream to tcp:127.0.0.1:6642\n"
    )
    p = tmp_path / "ovn-controller.log"
    p.write_text(content)
    return p


class TestOvnParser:
    def test_parses_all_lines(self, ovn_log: Path):
        parser = OvnParser()
        events = parser.parse(str(ovn_log))
        assert len(events) == 5

    def test_detects_warning(self, ovn_log: Path):
        parser = OvnParser()
        events = parser.parse(str(ovn_log))
        warnings = [e for e in events if e.level == LogLevel.WARNING]
        assert len(warnings) == 1
        assert "SSL_read" in warnings[0].message

    def test_detects_error(self, ovn_log: Path):
        parser = OvnParser()
        events = parser.parse(str(ovn_log))
        errors = [e for e in events if e.level == LogLevel.ERROR]
        assert len(errors) == 1
        assert "Unable to open stream" in errors[0].message

    def test_metadata(self, ovn_log: Path):
        parser = OvnParser()
        events = parser.parse(str(ovn_log))
        assert events[0].metadata["process"] == "ovn-controller"
        assert events[0].metadata["module"] == "vlog"
