"""Tests for the pattern matcher."""

from __future__ import annotations

from datetime import datetime, timezone

from sunbeam_rca.analysis.pattern_matcher import load_patterns, match_patterns
from sunbeam_rca.models import LogEvent, LogLevel, SourceType


def _make_event(
    message: str,
    source_type: SourceType = SourceType.PIPELINE,
    level: LogLevel = LogLevel.ERROR,
    line_number: int = 1,
    ts_offset_secs: int = 0,
) -> LogEvent:
    return LogEvent(
        timestamp=datetime(2026, 2, 11, 11, 0, 0, tzinfo=timezone.utc),
        source_file="test.log",
        line_number=line_number,
        level=level,
        message=message,
        source_type=source_type,
    )


class TestLoadPatterns:
    def test_loads_default_patterns(self):
        patterns = load_patterns()
        assert len(patterns) >= 23
        ids = {p.id for p in patterns}
        assert "OOM_KILL" in ids
        assert "PIPELINE_ERROR_ANNOTATION" in ids
        assert "JUJU_CHARM_DOWNLOAD_FAIL" in ids
        assert "MICROCEPH_DB_UNINITIALIZED" in ids
        assert "SUNBEAM_WAIT_TIMEOUT" in ids
        assert "K8S_DNS_RESOLUTION_FAIL" in ids
        assert "CILIUM_CNI_MISSING" in ids

    def test_all_patterns_valid(self):
        patterns = load_patterns()
        for p in patterns:
            assert 1 <= p.severity <= 10
            assert p.source_types


class TestMatchPatterns:
    def test_matches_oom(self):
        events = [
            _make_event("Out of memory: Killed process 1234", SourceType.SYSLOG),
        ]
        matches = match_patterns(events)
        assert any(m.pattern_id == "OOM_KILL" for m in matches)

    def test_matches_pipeline_error(self):
        events = [
            _make_event("##[error]Process completed with exit code 1."),
        ]
        matches = match_patterns(events)
        assert any(m.pattern_id == "PIPELINE_ERROR_ANNOTATION" for m in matches)

    def test_matches_juju_charm_fail(self):
        events = [
            _make_event(
                'failed to download charm "ch:amd64/sunbeam-machine-129"',
                SourceType.JUJU,
            ),
        ]
        matches = match_patterns(events)
        assert any(m.pattern_id == "JUJU_CHARM_DOWNLOAD_FAIL" for m in matches)

    def test_matches_disk_full(self):
        events = [
            _make_event("No space left on device", SourceType.SYSLOG),
        ]
        matches = match_patterns(events)
        assert any(m.pattern_id == "DISK_FULL" for m in matches)

    def test_no_false_positives_on_clean_log(self):
        events = [
            _make_event("Everything is fine", SourceType.SYSLOG, LogLevel.INFO),
        ]
        matches = match_patterns(events)
        assert len(matches) == 0

    def test_source_type_filtering(self):
        events = [
            _make_event("Out of memory: Killed process", SourceType.PIPELINE),
        ]
        matches = match_patterns(events)
        oom_matches = [m for m in matches if m.pattern_id == "OOM_KILL"]
        assert len(oom_matches) == 0

    def test_matches_microceph_db_uninitialized(self):
        events = [
            _make_event(
                "Failed executing cmd: ['microceph', 'status'], error: Error: failed listing disks: Database is not yet initialized",
                SourceType.JUJU,
            ),
        ]
        matches = match_patterns(events)
        assert any(m.pattern_id == "MICROCEPH_DB_UNINITIALIZED" for m in matches)

    def test_matches_sunbeam_wait_timeout(self):
        events = [
            _make_event("wait timed out after 1799.999999149s", SourceType.PIPELINE),
        ]
        matches = match_patterns(events)
        assert any(m.pattern_id == "SUNBEAM_WAIT_TIMEOUT" for m in matches)

    def test_matches_k8s_dns_resolution_fail(self):
        events = [
            _make_event(
                'cannot resolve "controller-service.controller-sunbeam-controller.svc.cluster.local": '
                "lookup ... i/o timeout",
                SourceType.JUJU,
            ),
        ]
        matches = match_patterns(events)
        assert any(m.pattern_id == "K8S_DNS_RESOLUTION_FAIL" for m in matches)

    def test_matches_cilium_cni_missing(self):
        events = [
            _make_event(
                "Machine 2 (server-04) is missing cilium_host network interface",
                SourceType.JUJU,
            ),
        ]
        matches = match_patterns(events)
        assert any(m.pattern_id == "CILIUM_CNI_MISSING" for m in matches)

    def test_network_timeout_no_false_positive_on_config(self):
        events = [
            _make_event("  timeout-seconds: 86400", SourceType.PIPELINE),
        ]
        matches = match_patterns(events)
        timeout_matches = [m for m in matches if m.pattern_id == "NETWORK_TIMEOUT"]
        assert len(timeout_matches) == 0

    def test_matches_curl_connect_timeout(self):
        events = [
            _make_event(
                "curl: (28) Failed to connect to 10.152.183.114 port 17070 after 20002 ms: Timeout was reached",
                SourceType.CLOUD_INIT,
            ),
        ]
        matches = match_patterns(events)
        assert any(m.pattern_id == "CURL_CONNECT_TIMEOUT" for m in matches)
