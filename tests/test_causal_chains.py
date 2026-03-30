"""Tests for causal chain logic."""

from __future__ import annotations

from sunbeam_rca.analysis.causal_chains import (
    DOWNSTREAM_PENALTY,
    UPSTREAM_BONUS,
    compute_causal_adjustments,
    get_downstream_ids,
    get_upstream_ids,
)


class TestCausalGraph:
    def test_cilium_causes_dns_failure(self):
        downstream = get_downstream_ids("CILIUM_CNI_MISSING")
        assert "K8S_DNS_RESOLUTION_FAIL" in downstream
        assert "CURL_CONNECT_TIMEOUT" in downstream

    def test_dns_failure_caused_by_cilium(self):
        upstream = get_upstream_ids("K8S_DNS_RESOLUTION_FAIL")
        assert "CILIUM_CNI_MISSING" in upstream

    def test_microceph_chain(self):
        downstream = get_downstream_ids("MICROCEPH_DB_UNINITIALIZED")
        assert "SUNBEAM_WAIT_TIMEOUT" in downstream

        upstream = get_upstream_ids("MICROCEPH_DB_UNINITIALIZED")
        assert "K8S_DNS_RESOLUTION_FAIL" in upstream
        assert "MICROCEPH_RADOS_ERROR" in upstream

    def test_unknown_pattern_returns_empty(self):
        assert get_downstream_ids("NONEXISTENT") == set()
        assert get_upstream_ids("NONEXISTENT") == set()


class TestCausalAdjustments:
    def test_upstream_boosted(self):
        matched = {"CILIUM_CNI_MISSING", "K8S_DNS_RESOLUTION_FAIL"}
        adj = compute_causal_adjustments(matched)
        assert adj["CILIUM_CNI_MISSING"] == UPSTREAM_BONUS
        assert adj["K8S_DNS_RESOLUTION_FAIL"] == -DOWNSTREAM_PENALTY

    def test_full_chain_adjustments(self):
        matched = {
            "CILIUM_CNI_MISSING",
            "K8S_DNS_RESOLUTION_FAIL",
            "MICROCEPH_DB_UNINITIALIZED",
            "SUNBEAM_WAIT_TIMEOUT",
            "PIPELINE_SUBPROCESS_ERROR",
        }
        adj = compute_causal_adjustments(matched)
        # Root cause gets boost, deepest symptom gets penalty
        assert adj.get("CILIUM_CNI_MISSING", 0) > 0
        assert adj.get("PIPELINE_SUBPROCESS_ERROR", 0) < 0

    def test_isolated_pattern_no_adjustment(self):
        matched = {"OOM_KILL"}
        adj = compute_causal_adjustments(matched)
        assert "OOM_KILL" not in adj

    def test_both_upstream_and_downstream(self):
        """A middle node should get both upstream boost and downstream penalty."""
        matched = {
            "CILIUM_CNI_MISSING",
            "K8S_DNS_RESOLUTION_FAIL",
            "MICROCEPH_RADOS_ERROR",
        }
        adj = compute_causal_adjustments(matched)
        dns_adj = adj.get("K8S_DNS_RESOLUTION_FAIL", 0)
        assert dns_adj == UPSTREAM_BONUS - DOWNSTREAM_PENALTY
