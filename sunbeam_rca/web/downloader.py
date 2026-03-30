"""Download test run artifacts from solutions.qa.canonical.com."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

TESTRUN_URL_RE = re.compile(
    r"https?://solutions\.qa\.canonical\.com/testruns?/([0-9a-f-]+)",
    re.IGNORECASE,
)

API_BASE = "https://solutions.qa.canonical.com"
REQUEST_TIMEOUT = 120.0


def download_test_run(
    url: str,
    dest_dir: Path,
) -> tuple[Path | None, Path | None]:
    """Download pipeline logs and sosreport from a test run URL.

    Args:
        url: Full URL to the test run page.
        dest_dir: Directory to save downloaded files into.

    Returns:
        (pipeline_zip_path, sosreport_path) -- either may be None if
        the corresponding artifact was not found.
    """
    match = TESTRUN_URL_RE.search(url)
    if not match:
        raise ValueError(
            f"Could not parse test run UUID from URL: {url}. "
            f"Expected format: https://solutions.qa.canonical.com/testruns/<uuid>"
        )

    run_id = match.group(1)
    logger.info("Downloading artifacts for test run %s", run_id)

    token = os.environ.get("SOLUTIONS_QA_TOKEN", "")
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    dest_dir.mkdir(parents=True, exist_ok=True)

    pipeline_path = _try_download_pipeline_logs(run_id, dest_dir, headers)
    sosreport_path = _try_download_sosreport(run_id, dest_dir, headers)

    if not pipeline_path and not sosreport_path:
        raise RuntimeError(
            f"No downloadable artifacts found for test run {run_id}. "
            "Check that the URL is correct and you have access."
        )

    return pipeline_path, sosreport_path


def _try_download_pipeline_logs(
    run_id: str,
    dest_dir: Path,
    headers: dict[str, str],
) -> Path | None:
    """Attempt to download the GitHub Actions pipeline log archive."""
    artifacts_url = f"{API_BASE}/api/v1/testruns/{run_id}/artifacts"

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(artifacts_url, headers=headers)
            if resp.status_code != 200:
                logger.warning(
                    "Artifacts endpoint returned %d for %s",
                    resp.status_code,
                    run_id,
                )
                return None

            data = resp.json()
            artifacts = data if isinstance(data, list) else data.get("artifacts", [])

            log_artifact = None
            for a in artifacts:
                name = a.get("name", "").lower()
                if "pipeline" in name or "log" in name:
                    log_artifact = a
                    break
                if name.endswith(".zip"):
                    log_artifact = a

            if not log_artifact:
                logger.info("No pipeline log artifact found for %s", run_id)
                return None

            download_url = log_artifact.get("download_url") or log_artifact.get("url")
            if not download_url:
                return None

            if not download_url.startswith("http"):
                download_url = f"{API_BASE}{download_url}"

            filename = log_artifact.get("name", f"logs_{run_id}.zip")
            dest = dest_dir / filename
            _download_file(client, download_url, dest, headers)
            logger.info("Downloaded pipeline logs to %s", dest)
            return dest

    except Exception:
        logger.exception("Failed to download pipeline logs for %s", run_id)
        return None


def _try_download_sosreport(
    run_id: str,
    dest_dir: Path,
    headers: dict[str, str],
) -> Path | None:
    """Attempt to download the sosreport tarball."""
    artifacts_url = f"{API_BASE}/api/v1/testruns/{run_id}/artifacts"

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(artifacts_url, headers=headers)
            if resp.status_code != 200:
                return None

            data = resp.json()
            artifacts = data if isinstance(data, list) else data.get("artifacts", [])

            sos_artifact = None
            for a in artifacts:
                name = a.get("name", "").lower()
                if "sosreport" in name or name.endswith(".tar.xz"):
                    sos_artifact = a
                    break

            if not sos_artifact:
                logger.info("No sosreport artifact found for %s", run_id)
                return None

            download_url = sos_artifact.get("download_url") or sos_artifact.get("url")
            if not download_url:
                return None

            if not download_url.startswith("http"):
                download_url = f"{API_BASE}{download_url}"

            filename = sos_artifact.get("name", f"sosreport_{run_id}.tar.xz")
            dest = dest_dir / filename
            _download_file(client, download_url, dest, headers)
            logger.info("Downloaded sosreport to %s", dest)
            return dest

    except Exception:
        logger.exception("Failed to download sosreport for %s", run_id)
        return None


def _download_file(
    client: httpx.Client,
    url: str,
    dest: Path,
    headers: dict[str, str],
) -> None:
    """Stream-download a file to disk."""
    with client.stream("GET", url, headers=headers) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=65536):
                f.write(chunk)
