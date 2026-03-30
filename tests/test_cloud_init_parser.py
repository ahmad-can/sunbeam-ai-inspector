"""Tests for the cloud-init parsers."""

from __future__ import annotations

from pathlib import Path

import pytest

from sunbeam_rca.models import LogLevel, SourceType
from sunbeam_rca.parsers.cloud_init_parser import CloudInitOutputParser, CloudInitParser


@pytest.fixture
def cloud_init_log(tmp_path: Path) -> Path:
    content = (
        "2026-02-11 09:32:35,308 - main.py[INFO]: PID [1] started cloud-init.\n"
        "2026-02-11 09:34:40,100 - reporting.py[WARNING]: Failed posting event: "
        "HTTPConnectionPool Max retries exceeded Network is unreachable\n"
        "2026-02-11 09:35:00,200 - main.py[DEBUG]: Closing stdin\n"
    )
    p = tmp_path / "cloud-init.log"
    p.write_text(content)
    return p


@pytest.fixture
def cloud_init_output_log(tmp_path: Path) -> Path:
    content = (
        "Running apt-get update\n"
        "Hit:1 http://archive.ubuntu.com/ubuntu noble InRelease\n"
        "curl: (28) Failed to connect to 10.152.183.114 port 17070 after 20002 ms: Timeout was reached\n"
        "curl: (28) Resolving timed out after 20000 milliseconds\n"
        "Agent binaries downloaded successfully.\n"
    )
    p = tmp_path / "cloud-init-output.log"
    p.write_text(content)
    return p


class TestCloudInitParser:
    def test_parses_structured_log(self, cloud_init_log: Path):
        parser = CloudInitParser()
        events = parser.parse(str(cloud_init_log))
        assert len(events) == 3
        assert all(e.source_type == SourceType.CLOUD_INIT for e in events)

    def test_detects_warning_level(self, cloud_init_log: Path):
        parser = CloudInitParser()
        events = parser.parse(str(cloud_init_log))
        warnings = [e for e in events if e.level == LogLevel.WARNING]
        assert len(warnings) == 1
        assert "Failed posting event" in warnings[0].message


class TestCloudInitOutputParser:
    def test_extracts_curl_errors(self, cloud_init_output_log: Path):
        parser = CloudInitOutputParser()
        events = parser.parse(str(cloud_init_output_log))
        assert len(events) == 2
        assert all(e.level == LogLevel.ERROR for e in events)
        assert all(e.source_type == SourceType.CLOUD_INIT for e in events)

    def test_ignores_normal_lines(self, cloud_init_output_log: Path):
        parser = CloudInitOutputParser()
        events = parser.parse(str(cloud_init_output_log))
        messages = " ".join(e.message for e in events)
        assert "apt-get" not in messages
        assert "Agent binaries downloaded" not in messages

    def test_curl_timeout_message(self, cloud_init_output_log: Path):
        parser = CloudInitOutputParser()
        events = parser.parse(str(cloud_init_output_log))
        assert any("Timeout was reached" in e.message for e in events)
        assert any("Resolving timed out" in e.message for e in events)
