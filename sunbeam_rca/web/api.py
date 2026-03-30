"""API routes for the Sunbeam RCA web UI."""

from __future__ import annotations

import json
import logging
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_jobs: dict[str, dict[str, Any]] = {}

NODE_DISPLAY = {
    "collect_node": {"label": "Collect", "index": 0},
    "parse_node": {"label": "Parse", "index": 1},
    "pattern_match_node": {"label": "Patterns", "index": 2},
    "infrastructure_agent": {"label": "Infra Agent", "index": 3, "is_agent": True},
    "network_agent": {"label": "Network Agent", "index": 3, "is_agent": True},
    "kubernetes_agent": {"label": "K8s Agent", "index": 3, "is_agent": True},
    "juju_agent": {"label": "Juju Agent", "index": 3, "is_agent": True},
    "storage_agent": {"label": "Storage Agent", "index": 3, "is_agent": True},
    "observability_agent": {"label": "Observability Agent", "index": 3, "is_agent": True},
    "pipeline_agent": {"label": "Pipeline Agent", "index": 3, "is_agent": True},
    "orchestrator_node": {"label": "Orchestrator", "index": 4},
    "deep_analyze_node": {"label": "Deep Analysis", "index": 5},
    "score_node": {"label": "Score", "index": 6},
    "report_node": {"label": "Report", "index": 7},
}


@router.post("/analyze")
async def analyze(
    pipeline_zip: UploadFile | None = File(None),
    sosreport: UploadFile | None = File(None),
    test_run_url: str = Form(""),
):
    """Accept file uploads or a test run URL and create an analysis job."""
    job_id = str(uuid.uuid4())
    tmp = Path(tempfile.mkdtemp(prefix="sunbeam_job_"))

    pipeline_path = ""
    sosreport_path = ""

    if test_run_url:
        try:
            from sunbeam_rca.web.downloader import download_test_run

            pipeline_path_p, sosreport_path_p = download_test_run(
                test_run_url, tmp
            )
            pipeline_path = str(pipeline_path_p) if pipeline_path_p else ""
            sosreport_path = str(sosreport_path_p) if sosreport_path_p else ""
        except Exception as exc:
            logger.exception("Failed to download test run artifacts")
            return JSONResponse(
                status_code=400,
                content={"error": f"Failed to download: {exc}"},
            )
    else:
        if pipeline_zip and pipeline_zip.filename:
            dest = tmp / pipeline_zip.filename
            dest.write_bytes(await pipeline_zip.read())
            pipeline_path = str(dest)

        if sosreport and sosreport.filename:
            dest = tmp / sosreport.filename
            dest.write_bytes(await sosreport.read())
            sosreport_path = str(dest)

    if not pipeline_path and not sosreport_path:
        return JSONResponse(
            status_code=400,
            content={"error": "Provide at least one log file or a test run URL."},
        )

    _jobs[job_id] = {
        "pipeline_path": pipeline_path,
        "sosreport_path": sosreport_path,
        "status": "pending",
        "events": [],
        "result": None,
        "error": None,
    }

    return {"job_id": job_id}


@router.get("/stream/{job_id}")
async def stream(job_id: str):
    """SSE endpoint that streams pipeline progress for a given job."""
    job = _jobs.get(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": "Job not found"})

    if job["status"] == "pending":
        job["status"] = "running"
        t = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
        t.start()

    async def event_generator():
        sent = 0
        while True:
            events = job["events"]
            while sent < len(events):
                evt = events[sent]
                sent += 1
                yield {
                    "event": evt["type"],
                    "data": json.dumps(evt["data"]),
                }

            if job["status"] in ("done", "error"):
                if job["status"] == "error":
                    yield {
                        "event": "error",
                        "data": json.dumps({"message": job.get("error", "Unknown error")}),
                    }
                yield {"event": "done", "data": "{}"}
                break

            await _async_sleep(0.3)

    return EventSourceResponse(event_generator())


@router.get("/jobs/{job_id}/report")
async def get_report(job_id: str):
    """Return the full JSON report for download."""
    job = _jobs.get(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    if job["status"] != "done" or not job.get("result"):
        return JSONResponse(status_code=202, content={"status": job["status"]})
    return JSONResponse(content=job["result"])


async def _async_sleep(secs: float):
    import asyncio

    await asyncio.sleep(secs)


def _run_job(job_id: str) -> None:
    """Execute the LangGraph pipeline in a background thread, emitting events."""
    job = _jobs[job_id]

    try:
        from dotenv import load_dotenv

        load_dotenv()

        from sunbeam_rca.graph import build_graph

        graph = build_graph()
        output_dir = tempfile.mkdtemp(prefix="sunbeam_output_")

        initial_state = {
            "pipeline_zip_path": job["pipeline_path"],
            "sosreport_path": job["sosreport_path"],
            "output_dir": output_dir,
            "events": [],
        }

        last_node = None
        last_chunk = None

        for chunk in graph.stream(initial_state):
            node_name = list(chunk.keys())[0]

            if last_node and last_node != node_name:
                info = NODE_DISPLAY.get(last_node, {})
                _emit(job, "node_done", {
                    "node": last_node,
                    "label": info.get("label", last_node),
                    "index": info.get("index", -1),
                    "is_agent": info.get("is_agent", False),
                    **_extract_stats(last_chunk, last_node),
                })

            if node_name != last_node:
                info = NODE_DISPLAY.get(node_name, {})
                _emit(job, "node_start", {
                    "node": node_name,
                    "label": info.get("label", node_name),
                    "index": info.get("index", -1),
                    "is_agent": info.get("is_agent", False),
                })

            last_node = node_name
            last_chunk = chunk

            if node_name == "report_node":
                report_data = chunk.get("report_node", {})
                result = _build_result(report_data, output_dir, initial_state, chunk)
                job["result"] = result

        if last_node:
            info = NODE_DISPLAY.get(last_node, {})
            _emit(job, "node_done", {
                "node": last_node,
                "label": info.get("label", last_node),
                "index": info.get("index", -1),
                "is_agent": info.get("is_agent", False),
                **(
                    _extract_stats(last_chunk, last_node)
                    if last_chunk
                    else {}
                ),
            })

        if job["result"]:
            _emit(job, "report", job["result"])

        job["status"] = "done"

    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        job["error"] = str(exc)
        job["status"] = "error"


def _emit(job: dict, event_type: str, data: dict) -> None:
    job["events"].append({"type": event_type, "data": data})


def _extract_stats(chunk: dict, node_name: str) -> dict:
    """Pull useful stats from a node's output for the progress display."""
    data = chunk.get(node_name, {})
    stats: dict[str, Any] = {}

    event_count = len(data.get("events", []))
    if event_count:
        stats["event_count"] = event_count

    match_count = len(data.get("pattern_matches", []))
    if match_count:
        stats["match_count"] = match_count

    candidate_count = len(data.get("ranked_candidates", []))
    if candidate_count:
        stats["candidate_count"] = candidate_count

    domain_findings = data.get("domain_findings", [])
    if domain_findings:
        stats["domain_findings"] = [
            {
                "domain": df.get("domain", "?"),
                "status": df.get("status", "?"),
                "match_count": df.get("match_count", 0),
            }
            for df in domain_findings
        ]

    if node_name == "deep_analyze_node":
        synth = data.get("pattern_matches", [])
        stats["llm_discoveries"] = len(synth)

    return stats


def _build_result(
    report_data: dict,
    output_dir: str,
    initial_state: dict,
    last_chunk: dict,
) -> dict:
    """Assemble the final result payload from the report node output."""
    json_report_str = report_data.get("json_report", "{}")
    try:
        json_report = json.loads(json_report_str)
    except (json.JSONDecodeError, TypeError):
        json_report = {}

    return {
        "markdown": report_data.get("markdown_report", ""),
        "json_report": json_report,
        "output_dir": output_dir,
    }
