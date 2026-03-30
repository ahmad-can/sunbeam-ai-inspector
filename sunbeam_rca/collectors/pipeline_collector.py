"""Extract and inventory GitHub Actions log archives."""

from __future__ import annotations

import logging
import tempfile
import zipfile
from pathlib import Path

from sunbeam_rca.models import PipelineManifest

logger = logging.getLogger(__name__)


def collect_pipeline(zip_path: str) -> PipelineManifest:
    """Extract a GitHub Actions ``.zip`` log archive and return a manifest.

    The archive typically contains numbered job logs (``0_<name>.txt``,
    ``1_<name>.txt``, ...) and per-job ``system.txt`` files in subdirectories.
    """
    zip_path_obj = Path(zip_path)
    if not zip_path_obj.exists():
        raise FileNotFoundError(f"Pipeline log archive not found: {zip_path}")

    extract_dir = Path(tempfile.mkdtemp(prefix="sunbeam_pipeline_"))
    logger.info("Extracting pipeline logs to %s", extract_dir)

    with zipfile.ZipFile(zip_path_obj, "r") as zf:
        zf.extractall(extract_dir)

    job_logs: list[str] = []
    system_logs: list[str] = []

    for p in sorted(extract_dir.rglob("*.txt")):
        rel = str(p.relative_to(extract_dir))
        if p.name == "system.txt":
            system_logs.append(str(p))
        else:
            job_logs.append(str(p))

    logger.info(
        "Pipeline manifest: %d job logs, %d system logs",
        len(job_logs),
        len(system_logs),
    )

    return PipelineManifest(
        root_dir=str(extract_dir),
        job_logs=job_logs,
        system_logs=system_logs,
    )
