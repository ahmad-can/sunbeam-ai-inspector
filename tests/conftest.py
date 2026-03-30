"""Shared test fixtures."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def sample_pipeline_log(tmp_dir: Path) -> Path:
    content = (
        "2026-02-11T09:12:42.4300801Z Current runner version: '2.331.0'\n"
        "2026-02-11T09:12:42.4315384Z Runner name: 'sqa-runners-0'\n"
        "2026-02-11T09:12:47.1603913Z ##[group]Run deploy script\n"
        "2026-02-11T10:00:00.0000000Z Running sunbeam deploy...\n"
        "2026-02-11T10:00:01.0000000Z ##[endgroup]\n"
        "2026-02-11T11:16:21.4215629Z Traceback (most recent call last):\n"
        "2026-02-11T11:16:21.4250000Z   File \"deploy_sunbeam.py\", line 148\n"
        "2026-02-11T11:16:21.4270000Z subprocess.CalledProcessError: Command returned non-zero exit status 1.\n"
        "2026-02-11T11:16:21.4287067Z ##[error]Process completed with exit code 1.\n"
    )
    p = tmp_dir / "1_Run the pipeline.txt"
    p.write_text(content)
    return p


@pytest.fixture
def sample_syslog(tmp_dir: Path) -> Path:
    content = (
        "2026-02-11T09:34:19.166559+00:00 server01 systemd[1]: Started snap.k8s.kubelet.service\n"
        "2026-02-11T10:00:00.000000+00:00 server01 kernel[0]: Out of memory: Killed process 1234 (java)\n"
        "2026-02-11T10:00:01.000000+00:00 server01 systemd[1]: snap.k8s.kubelet.service: Main process exited\n"
        "2026-02-11T10:01:00.000000+00:00 server01 juju-agent[5678]: connection refused to controller\n"
    )
    p = tmp_dir / "syslog"
    p.write_text(content)
    return p


@pytest.fixture
def sample_juju_log(tmp_dir: Path) -> Path:
    content = (
        '2026-02-11 09:45:05 INFO juju.worker.apicaller connect.go:163 successfully connected\n'
        '2026-02-11 09:46:00 ERROR juju.worker.dependency engine.go:695 "uniter" manifold worker returned unexpected error: failed to download charm "ch:amd64/sunbeam-machine-129"\n'
        '2026-02-11 09:47:00 WARNING juju.worker.dependency engine.go:700 restarting worker\n'
    )
    p = tmp_dir / "unit-sunbeam-machine-0.log"
    p.write_text(content)
    return p
