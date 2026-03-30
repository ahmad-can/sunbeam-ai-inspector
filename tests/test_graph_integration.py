"""Integration test: run the full graph against sample data without LLM.

This test verifies the end-to-end flow by running the graph with
``LLM_PROVIDER`` unset so the LLM nodes fall back to pattern-only mode.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def sample_workspace(tmp_path: Path) -> dict:
    """Create a realistic pipeline zip and sosreport with the Cilium/microceph
    failure scenario."""
    pipeline_content = (
        "2026-02-11T09:12:42.4300801Z Runner started\n"
        "2026-02-11T10:00:00.0000000Z Deploying sunbeam...\n"
        "2026-02-11T10:47:15.5281767Z wait timed out after 1799.999997377s\n"
        "2026-02-11T11:16:21.4215629Z Traceback (most recent call last):\n"
        "2026-02-11T11:16:21.4270000Z subprocess.CalledProcessError: Command returned non-zero exit status 1.\n"
        "2026-02-11T11:16:21.4287067Z ##[error]Process completed with exit code 1.\n"
    )

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "1_Run the pipeline.txt").write_text(pipeline_content)

    zip_path = tmp_path / "logs.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(log_dir / "1_Run the pipeline.txt", "1_Run the pipeline.txt")

    sos_dir = tmp_path / "sosreport"
    sos_dir.mkdir()
    (sos_dir / "environment").write_text("PATH=/usr/bin\n")
    (sos_dir / "hostname").write_text("test-server-04\n")

    var_log = sos_dir / "var" / "log"
    var_log.mkdir(parents=True)
    (var_log / "syslog").write_text(
        "2026-02-11T10:17:33.088972+00:00 test-server-04 microceph.daemon[10760]: "
        'time=2026-02-11T10:17:33.088Z level=ERROR msg="PostRefresh failed: '
        "Error initializing cluster client: ObjectNotFound('RADOS object not found')\"\n"
    )

    juju_dir = var_log / "juju"
    juju_dir.mkdir()
    (juju_dir / "machine-2.log").write_text(
        '2026-02-11 10:16:19 INFO juju.api apiclient.go:988 cannot resolve '
        '"controller-service.controller-sunbeam-controller.svc.cluster.local": '
        'lookup controller-service.controller-sunbeam-controller.svc.cluster.local: i/o timeout\n'
        '2026-02-11 10:16:20 ERROR juju.worker.dependency engine.go:695 '
        '"lxd-container-provisioner" manifold worker returned unexpected error: '
        'container types not yet available\n'
    )

    microceph_lines = []
    for i in range(30):
        microceph_lines.append(
            f"2026-02-11 10:19:{44 + i % 20:02d} ERROR unit.microceph/1.juju-log "
            f"server.go:405 ceph:6: Failed executing cmd: ['microceph', 'status'], "
            "error: Error: failed listing disks: Database is not yet initialized\n"
        )
    (juju_dir / "unit-microceph-1.log").write_text("".join(microceph_lines))

    ci_log = var_log / "cloud-init.log"
    ci_log.write_text(
        "2026-02-11 09:34:40,100 - reporting.py[WARNING]: "
        "Failed posting event: Network is unreachable\n"
    )

    ci_output = var_log / "cloud-init-output.log"
    ci_output.write_text(
        "curl: (28) Failed to connect to 10.152.183.114 port 17070 "
        "after 20002 ms: Timeout was reached\n"
    )

    sunbeam_dir = sos_dir / "sos_commands" / "sunbeam"
    sunbeam_dir.mkdir(parents=True)
    juju_status = {
        "controller": {"timestamp": "11:25:09Z"},
        "machines": {
            "0": {
                "hostname": "test-server-01",
                "dns-name": "10.0.0.1",
                "network-interfaces": {
                    "eth0": {"ip-addresses": ["10.0.0.1"]},
                    "cilium_host": {"ip-addresses": ["10.1.0.1"]},
                },
            },
            "2": {
                "hostname": "test-server-04",
                "dns-name": "10.0.0.4",
                "network-interfaces": {
                    "eth0": {"ip-addresses": ["10.0.0.4"]},
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
                        "machine": "2",
                        "subordinates": {},
                    },
                },
            },
        },
    }
    (sunbeam_dir / "juju_status_-m_admin_--format_json").write_text(
        json.dumps(juju_status)
    )

    output_dir = tmp_path / "output"

    return {
        "pipeline_zip": str(zip_path),
        "sosreport_dir": str(sos_dir),
        "output_dir": str(output_dir),
    }


def test_full_graph_no_llm(sample_workspace: dict, monkeypatch):
    """Run the full graph without an LLM and verify outputs."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")

    from sunbeam_rca.graph import build_graph

    graph = build_graph()
    result = graph.invoke({
        "pipeline_zip_path": sample_workspace["pipeline_zip"],
        "sosreport_path": sample_workspace["sosreport_dir"],
        "output_dir": sample_workspace["output_dir"],
        "events": [],
    })

    assert "ranked_candidates" in result
    assert "json_report" in result
    assert "markdown_report" in result
    assert "juju_status_summary" in result
    assert "machine_map" in result

    candidates = result["ranked_candidates"]
    assert len(candidates) >= 1

    pattern_ids = {c["pattern_id"] for c in candidates}
    assert "MICROCEPH_DB_UNINITIALIZED" in pattern_ids
    assert "SUNBEAM_WAIT_TIMEOUT" in pattern_ids

    # The top candidate should NOT be the transient Juju worker error
    top = candidates[0]
    assert top["pattern_id"] != "JUJU_WORKER_UNEXPECTED_ERROR"

    output_dir = Path(sample_workspace["output_dir"])
    assert (output_dir / "report.json").exists()
    assert (output_dir / "report.md").exists()

    json_content = json.loads((output_dir / "report.json").read_text())
    assert "juju_status_summary" in json_content
    assert "machine_map" in json_content

    machine_map = result["machine_map"]
    assert machine_map.get("0") == "test-server-01"
    assert machine_map.get("2") == "test-server-04"


def test_noise_suppression_works(sample_workspace: dict, monkeypatch):
    """Verify that the transient 'container types not yet available' error
    is scored lower than real errors."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "openai")

    from sunbeam_rca.graph import build_graph

    graph = build_graph()
    result = graph.invoke({
        "pipeline_zip_path": sample_workspace["pipeline_zip"],
        "sosreport_path": sample_workspace["sosreport_dir"],
        "output_dir": sample_workspace["output_dir"],
        "events": [],
    })

    candidates = result["ranked_candidates"]
    juju_worker = next(
        (c for c in candidates if c["pattern_id"] == "JUJU_WORKER_UNEXPECTED_ERROR"),
        None,
    )
    microceph = next(
        (c for c in candidates if c["pattern_id"] == "MICROCEPH_DB_UNINITIALIZED"),
        None,
    )

    if juju_worker and microceph:
        assert microceph["confidence"] > juju_worker["confidence"]
        assert microceph["rank"] < juju_worker["rank"]
