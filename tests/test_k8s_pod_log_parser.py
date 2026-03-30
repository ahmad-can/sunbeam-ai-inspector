"""Tests for the Kubernetes pod log parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from sunbeam_rca.models import LogLevel, SourceType
from sunbeam_rca.parsers.k8s_pod_log_parser import K8sPodLogParser


@pytest.fixture
def go_style_log(tmp_path: Path) -> Path:
    content = (
        "2026-02-11T10:17:33.088Z level=INFO msg=\"Starting controller\"\n"
        "2026-02-11T10:17:34.100Z level=ERROR msg=\"Failed to connect to database: connection refused\"\n"
        "2026-02-11T10:17:35.200Z level=WARNING msg=\"Retrying operation\"\n"
    )
    p = tmp_path / "controller-pod.log"
    p.write_text(content)
    return p


@pytest.fixture
def json_style_log(tmp_path: Path) -> Path:
    content = (
        '{"ts":"2026-02-11T10:00:00.000Z","level":"info","msg":"Server started"}\n'
        '{"ts":"2026-02-11T10:01:00.000Z","level":"error","msg":"Unhandled exception: timeout"}\n'
    )
    p = tmp_path / "app-pod.log"
    p.write_text(content)
    return p


class TestK8sPodLogParser:
    def test_parses_go_style(self, go_style_log: Path):
        parser = K8sPodLogParser()
        events = parser.parse(str(go_style_log))
        assert len(events) == 3
        assert all(e.source_type == SourceType.KUBERNETES for e in events)

    def test_detects_levels(self, go_style_log: Path):
        parser = K8sPodLogParser()
        events = parser.parse(str(go_style_log))
        levels = [e.level for e in events]
        assert LogLevel.INFO in levels
        assert LogLevel.ERROR in levels

    def test_parses_json_style(self, json_style_log: Path):
        parser = K8sPodLogParser()
        events = parser.parse(str(json_style_log))
        assert len(events) == 2
        assert events[0].level == LogLevel.INFO
        assert events[1].level == LogLevel.ERROR

    def test_extracts_pod_name(self, go_style_log: Path):
        parser = K8sPodLogParser()
        events = parser.parse(str(go_style_log))
        assert events[0].metadata.get("pod") == "controller-pod"
