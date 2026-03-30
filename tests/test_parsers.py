"""Tests for log parsers."""

from __future__ import annotations

from pathlib import Path

from sunbeam_rca.models import LogLevel, SourceType
from sunbeam_rca.parsers.juju_parser import JujuParser
from sunbeam_rca.parsers.pipeline_parser import PipelineParser
from sunbeam_rca.parsers.syslog_parser import SyslogParser


class TestPipelineParser:
    def test_parses_timestamps(self, sample_pipeline_log: Path):
        parser = PipelineParser()
        events = parser.parse(str(sample_pipeline_log))
        assert len(events) > 0
        assert all(e.source_type == SourceType.PIPELINE for e in events)

    def test_detects_error_annotation(self, sample_pipeline_log: Path):
        parser = PipelineParser()
        events = parser.parse(str(sample_pipeline_log))
        errors = [e for e in events if e.level == LogLevel.ERROR]
        assert len(errors) >= 1
        error_msgs = " ".join(e.message for e in errors)
        assert "##[error]" in error_msgs or "Traceback" in error_msgs

    def test_strips_ansi(self, tmp_dir: Path):
        p = tmp_dir / "ansi.txt"
        p.write_text(
            "2026-02-11T09:00:00.0000000Z \x1b[36;1mhello\x1b[0m world\n"
        )
        parser = PipelineParser()
        events = parser.parse(str(p))
        assert len(events) == 1
        assert "\x1b" not in events[0].message
        assert "hello" in events[0].message

    def test_groups_tracked(self, sample_pipeline_log: Path):
        parser = PipelineParser()
        events = parser.parse(str(sample_pipeline_log))
        grouped = [e for e in events if e.metadata.get("group")]
        assert len(grouped) >= 1


class TestSyslogParser:
    def test_parses_basic_syslog(self, sample_syslog: Path):
        parser = SyslogParser()
        events = parser.parse(str(sample_syslog))
        assert len(events) == 4
        assert all(e.source_type == SourceType.SYSLOG for e in events)

    def test_detects_oom(self, sample_syslog: Path):
        parser = SyslogParser()
        events = parser.parse(str(sample_syslog))
        oom_events = [e for e in events if "Out of memory" in e.message]
        assert len(oom_events) == 1
        assert oom_events[0].level == LogLevel.ERROR

    def test_extracts_metadata(self, sample_syslog: Path):
        parser = SyslogParser()
        events = parser.parse(str(sample_syslog))
        assert events[0].metadata["hostname"] == "server01"
        assert events[0].metadata["process"] == "systemd"
        assert events[0].metadata["pid"] == 1


class TestJujuParser:
    def test_parses_juju_log(self, sample_juju_log: Path):
        parser = JujuParser()
        events = parser.parse(str(sample_juju_log))
        assert len(events) == 3
        assert all(e.source_type == SourceType.JUJU for e in events)

    def test_detects_error_level(self, sample_juju_log: Path):
        parser = JujuParser()
        events = parser.parse(str(sample_juju_log))
        errors = [e for e in events if e.level == LogLevel.ERROR]
        assert len(errors) == 1
        assert "failed to download charm" in errors[0].message

    def test_extracts_unit_name(self, sample_juju_log: Path):
        parser = JujuParser()
        events = parser.parse(str(sample_juju_log))
        assert events[0].metadata["unit"] == "sunbeam-machine/0"

    def test_unit_name_extraction(self):
        assert JujuParser._extract_unit_name("unit-sunbeam-machine-0.log") == "sunbeam-machine/0"
        assert JujuParser._extract_unit_name("unit-k8s-0.log") == "k8s/0"
        assert JujuParser._extract_unit_name("machine-0.log") == "machine-0"
        assert JujuParser._extract_unit_name("other.log") == ""
