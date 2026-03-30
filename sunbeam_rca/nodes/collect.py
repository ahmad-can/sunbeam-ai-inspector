"""collect_node — Extract and inventory input archives."""

from __future__ import annotations

import logging

from sunbeam_rca.collectors.pipeline_collector import collect_pipeline
from sunbeam_rca.collectors.sosreport_collector import collect_sosreport
from sunbeam_rca.state import RCAState

logger = logging.getLogger(__name__)


def collect_node(state: RCAState) -> dict:
    """Extract archives and build file manifests.

    Reads ``pipeline_zip_path`` and ``sosreport_path`` from state.
    Returns pipeline log file paths and sosreport manifest dict.
    """
    result: dict = {}

    pipeline_path = state.get("pipeline_zip_path", "")
    if pipeline_path:
        manifest = collect_pipeline(pipeline_path)
        result["pipeline_log_files"] = manifest.job_logs + manifest.system_logs
        logger.info("Collected %d pipeline files", len(result["pipeline_log_files"]))
    else:
        result["pipeline_log_files"] = []

    sos_path = state.get("sosreport_path", "")
    if sos_path:
        sos_manifest = collect_sosreport(sos_path)
        result["sosreport_manifest"] = sos_manifest.model_dump()
        logger.info("Collected sosreport: hostname=%s", sos_manifest.hostname)
    else:
        result["sosreport_manifest"] = {}

    return result
