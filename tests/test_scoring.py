"""Tests for the scoring node improvements."""

from __future__ import annotations

from datetime import datetime, timezone

from sunbeam_rca.nodes.score import _frequency_bonus, score_node


class TestFrequencyBonus:
    def test_single_occurrence(self):
        assert _frequency_bonus(1) == 0.0

    def test_few_occurrences(self):
        assert _frequency_bonus(3) == 0.05
        assert _frequency_bonus(9) == 0.05

    def test_moderate_occurrences(self):
        assert _frequency_bonus(10) == 0.10
        assert _frequency_bonus(29) == 0.10

    def test_many_occurrences(self):
        assert _frequency_bonus(30) == 0.15
        assert _frequency_bonus(100) == 0.15


class TestScoringIntegration:
    """Test that the scorer correctly combines signals."""

    def _make_match(
        self, pattern_id: str, severity: int, message: str, ts: str
    ) -> dict:
        return {
            "pattern_id": pattern_id,
            "category": "test",
            "description": f"Test {pattern_id}",
            "severity": severity,
            "matched_event": {
                "timestamp": ts,
                "source_file": "test.log",
                "line_number": 1,
                "level": "ERROR",
                "message": message,
                "source_type": "juju",
            },
            "context_events": [],
        }

    def test_noise_penalty_lowers_confidence(self):
        """Transient error should score lower than a real error."""
        ts = "2026-02-11T11:16:21+00:00"
        noisy_match = self._make_match(
            "JUJU_WORKER_UNEXPECTED_ERROR",
            8,
            '"lxd-container-provisioner" manifold worker returned unexpected error: container types not yet available',
            "2026-02-11T10:16:20+00:00",
        )
        real_match = self._make_match(
            "MICROCEPH_DB_UNINITIALIZED",
            9,
            "failed listing disks: Database is not yet initialized",
            "2026-02-11T10:19:44+00:00",
        )

        state = {
            "pattern_matches": [noisy_match, real_match],
            "failure_timestamp": ts,
            "correlated_findings": [],
        }

        result = score_node(state)
        candidates = result["ranked_candidates"]
        assert len(candidates) == 2

        noisy_cand = next(
            c for c in candidates if c["pattern_id"] == "JUJU_WORKER_UNEXPECTED_ERROR"
        )
        real_cand = next(
            c for c in candidates if c["pattern_id"] == "MICROCEPH_DB_UNINITIALIZED"
        )
        assert real_cand["confidence"] > noisy_cand["confidence"]

    def test_causal_chain_boosts_upstream(self):
        """Upstream cause should score higher than its downstream symptom."""
        ts = "2026-02-11T11:16:21+00:00"
        upstream = self._make_match(
            "MICROCEPH_DB_UNINITIALIZED",
            9,
            "failed listing disks: Database is not yet initialized",
            "2026-02-11T10:19:44+00:00",
        )
        downstream = self._make_match(
            "SUNBEAM_WAIT_TIMEOUT",
            9,
            "wait timed out after 1799.999s",
            "2026-02-11T10:47:15+00:00",
        )

        state = {
            "pattern_matches": [upstream, downstream],
            "failure_timestamp": ts,
            "correlated_findings": [],
        }

        result = score_node(state)
        candidates = result["ranked_candidates"]
        up_cand = next(
            c for c in candidates if c["pattern_id"] == "MICROCEPH_DB_UNINITIALIZED"
        )
        down_cand = next(
            c for c in candidates if c["pattern_id"] == "SUNBEAM_WAIT_TIMEOUT"
        )
        assert up_cand["confidence"] > down_cand["confidence"]

    def test_frequency_appears_in_explanation(self):
        ts = "2026-02-11T11:16:21+00:00"
        match = self._make_match(
            "MICROCEPH_DB_UNINITIALIZED",
            9,
            "failed listing disks: Database is not yet initialized",
            "2026-02-11T10:19:44+00:00",
        )
        # Simulate 30 matches of same pattern
        state = {
            "pattern_matches": [match] * 30,
            "failure_timestamp": ts,
            "correlated_findings": [],
        }
        result = score_node(state)
        candidates = result["ranked_candidates"]
        assert len(candidates) == 1
        assert "frequency(30x)" in candidates[0]["explanation"]
