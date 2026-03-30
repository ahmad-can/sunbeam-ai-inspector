"""Tests for the Juju status JSON parser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sunbeam_rca.models import LogLevel, SourceType
from sunbeam_rca.parsers.juju_status_parser import parse_juju_status


@pytest.fixture
def juju_status_json(tmp_path: Path) -> Path:
    """Minimal Juju status JSON with one stuck unit and one machine missing CNI."""
    data = {
        "controller": {"timestamp": "11:25:09Z"},
        "machines": {
            "0": {
                "hostname": "server-01",
                "dns-name": "10.0.0.1",
                "network-interfaces": {
                    "eth0": {"ip-addresses": ["10.0.0.1"]},
                    "cilium_host": {"ip-addresses": ["10.1.0.1"]},
                },
            },
            "1": {
                "hostname": "server-02",
                "dns-name": "10.0.0.2",
                "network-interfaces": {
                    "eth0": {"ip-addresses": ["10.0.0.2"]},
                },
            },
        },
        "applications": {
            "microceph": {
                "application-status": {"current": "waiting"},
                "units": {
                    "microceph/0": {
                        "workload-status": {
                            "current": "active",
                            "message": "",
                            "since": "11 Feb 2026 10:10:00Z",
                        },
                        "machine": "0",
                    },
                    "microceph/1": {
                        "workload-status": {
                            "current": "waiting",
                            "message": "waiting to join cluster",
                            "since": "11 Feb 2026 10:18:40Z",
                        },
                        "machine": "1",
                        "subordinates": {},
                    },
                },
            },
        },
    }
    p = tmp_path / "juju_status_json"
    p.write_text(json.dumps(data))
    return p


def test_detects_missing_cni(juju_status_json: Path):
    events, summary, machine_map = parse_juju_status(str(juju_status_json))
    cni_events = [e for e in events if "cilium_host" in e.message]
    assert len(cni_events) == 1
    assert cni_events[0].level == LogLevel.ERROR
    assert "server-02" in cni_events[0].message
    assert summary["machines_missing_cni"][0]["machine"] == "1"


def test_detects_stuck_unit(juju_status_json: Path):
    events, summary, machine_map = parse_juju_status(str(juju_status_json))
    stuck = summary["stuck_units"]
    assert len(stuck) == 1
    assert stuck[0]["unit"] == "microceph/1"
    assert stuck[0]["status"] == "waiting"
    assert "waiting to join cluster" in stuck[0]["message"]


def test_machine_map(juju_status_json: Path):
    _, _, machine_map = parse_juju_status(str(juju_status_json))
    assert machine_map["0"] == "server-01"
    assert machine_map["1"] == "server-02"


def test_events_have_correct_source_type(juju_status_json: Path):
    events, _, _ = parse_juju_status(str(juju_status_json))
    assert all(e.source_type == SourceType.JUJU for e in events)
    assert all(e.metadata.get("synthetic") is True for e in events)


def test_healthy_cluster_no_alerts(tmp_path: Path):
    data = {
        "controller": {"timestamp": "11:00:00Z"},
        "machines": {
            "0": {
                "hostname": "server-01",
                "dns-name": "10.0.0.1",
                "network-interfaces": {
                    "eth0": {"ip-addresses": ["10.0.0.1"]},
                    "cilium_host": {"ip-addresses": ["10.1.0.1"]},
                },
            },
        },
        "applications": {
            "app": {
                "application-status": {"current": "active"},
                "units": {
                    "app/0": {
                        "workload-status": {
                            "current": "active",
                            "message": "ready",
                            "since": "11 Feb 2026 10:00:00Z",
                        },
                        "machine": "0",
                    },
                },
            },
        },
    }
    p = tmp_path / "healthy_status_json"
    p.write_text(json.dumps(data))
    events, summary, _ = parse_juju_status(str(p))
    assert len(events) == 0
    assert len(summary["stuck_units"]) == 0
    assert len(summary["machines_missing_cni"]) == 0


def test_detects_unhealthy_app(juju_status_json: Path):
    """microceph has app-level status=waiting, should be detected."""
    events, summary, _ = parse_juju_status(str(juju_status_json))
    unhealthy = summary.get("unhealthy_apps", [])
    assert len(unhealthy) == 1
    assert unhealthy[0]["application"] == "microceph"
    assert unhealthy[0]["status"] == "waiting"


def test_detects_saas_issue(tmp_path: Path):
    data = {
        "controller": {"timestamp": "11:00:00Z"},
        "model": {"name": "openstack"},
        "machines": {},
        "applications": {},
        "remote-applications": {
            "microceph": {
                "offer-url": "admin/openstack-machines.microceph",
                "application-status": {
                    "current": "waiting",
                    "message": "(workload) waiting to join cluster",
                },
            },
        },
    }
    p = tmp_path / "juju_status_saas_json"
    p.write_text(json.dumps(data))
    events, summary, _ = parse_juju_status(str(p))
    saas = summary.get("saas_issues", [])
    assert len(saas) == 1
    assert saas[0]["saas_name"] == "microceph"
    assert saas[0]["offer_url"] == "admin/openstack-machines.microceph"
    saas_events = [e for e in events if "SAAS" in e.message]
    assert len(saas_events) == 1


def test_detects_offer_issue(tmp_path: Path):
    data = {
        "controller": {"timestamp": "11:00:00Z"},
        "model": {"name": "openstack"},
        "machines": {},
        "applications": {},
        "application-endpoints": {
            "microceph": {
                "url": "admin/openstack-machines.microceph",
                "application-status": {
                    "current": "waiting",
                    "message": "(workload) waiting to join cluster",
                },
            },
        },
    }
    p = tmp_path / "juju_status_offer_json"
    p.write_text(json.dumps(data))
    events, summary, _ = parse_juju_status(str(p))
    offers = summary.get("offer_issues", [])
    assert len(offers) == 1
    assert offers[0]["offer_name"] == "microceph"


def test_model_name_extraction(tmp_path: Path):
    data = {
        "controller": {"timestamp": "11:00:00Z"},
        "model": {"name": "openstack-machines"},
        "machines": {},
        "applications": {
            "k8s": {
                "application-status": {"current": "blocked", "message": "test", "since": ""},
                "units": {},
            },
        },
    }
    p = tmp_path / "juju_status_json"
    p.write_text(json.dumps(data))
    _, summary, _ = parse_juju_status(str(p))
    assert summary["model_name"] == "openstack-machines"
    assert summary["unhealthy_apps"][0]["model"] == "openstack-machines"
