"""Tests for the noise filter module."""

from __future__ import annotations

from sunbeam_rca.analysis.noise_filter import compute_noise_penalty, load_noise_filters


class TestLoadNoiseFilters:
    def test_loads_default_filters(self):
        filters = load_noise_filters()
        assert len(filters) >= 5
        ids = {f.id for f in filters}
        assert "JUJU_CONTAINER_TYPES_NOT_AVAILABLE" in ids
        assert "JUJU_PEER_RELATION_MISSING" in ids

    def test_all_filters_have_penalty(self):
        filters = load_noise_filters()
        for f in filters:
            assert 0 < f.penalty <= 1.0


class TestComputeNoisePenalty:
    def test_transient_container_types(self):
        penalty = compute_noise_penalty(
            "container types not yet available"
        )
        assert penalty >= 0.20

    def test_peer_relation_missing(self):
        penalty = compute_noise_penalty(
            "Cannot check leader ready as peer relation missing"
        )
        assert penalty >= 0.15

    def test_no_penalty_for_real_error(self):
        penalty = compute_noise_penalty(
            "failed listing disks: Database is not yet initialized"
        )
        assert penalty == 0.0

    def test_max_penalty_when_multiple_match(self):
        msg = (
            "container types not yet available and also "
            "Cannot check leader ready as peer relation missing"
        )
        penalty = compute_noise_penalty(msg)
        # Should return the higher of the two penalties
        assert penalty >= 0.20
