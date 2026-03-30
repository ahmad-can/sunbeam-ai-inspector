"""Tests for the Juju models topology parser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sunbeam_rca.parsers.juju_models_parser import format_model_topology, parse_juju_models


@pytest.fixture
def juju_models_json(tmp_path: Path) -> Path:
    data = {
        "models": [
            {
                "name": "admin/controller",
                "short-name": "controller",
                "model-type": "caas",
                "cloud": "test-k8s",
                "region": "localhost",
                "is-controller": True,
                "status": {"current": "available"},
                "agent-version": "3.6.14",
                "last-connection": "just now",
            },
            {
                "name": "admin/openstack-machines",
                "short-name": "openstack-machines",
                "model-type": "iaas",
                "cloud": "test-maas",
                "region": "default",
                "is-controller": False,
                "status": {"current": "available"},
                "agent-version": "3.6.14",
                "last-connection": "5 minutes ago",
            },
            {
                "name": "user/openstack",
                "short-name": "openstack",
                "model-type": "caas",
                "cloud": "test-k8s",
                "region": "localhost",
                "is-controller": False,
                "status": {"current": "available"},
                "agent-version": "3.6.14",
                "last-connection": "never connected",
            },
        ]
    }
    p = tmp_path / "juju_models_json"
    p.write_text(json.dumps(data))
    return p


def test_parses_three_models(juju_models_json: Path):
    models = parse_juju_models(str(juju_models_json))
    assert len(models) == 3


def test_model_types(juju_models_json: Path):
    models = parse_juju_models(str(juju_models_json))
    types = {m["short_name"]: m["model_type"] for m in models}
    assert types["controller"] == "caas"
    assert types["openstack-machines"] == "iaas"
    assert types["openstack"] == "caas"


def test_controller_flag(juju_models_json: Path):
    models = parse_juju_models(str(juju_models_json))
    ctrl = [m for m in models if m["is_controller"]]
    assert len(ctrl) == 1
    assert ctrl[0]["short_name"] == "controller"


def test_format_topology(juju_models_json: Path):
    models = parse_juju_models(str(juju_models_json))
    text = format_model_topology(models)
    assert "controller" in text
    assert "openstack-machines" in text
    assert "MAAS" in text
    assert "K8s" in text


def test_empty_file_returns_empty(tmp_path: Path):
    p = tmp_path / "empty"
    p.write_text("{}")
    assert parse_juju_models(str(p)) == []
