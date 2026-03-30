"""parse_node — Parse logs into structured events and build a timeline."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sunbeam_rca.models import LogEvent, LogLevel, SourceType
from sunbeam_rca.parsers.cloud_init_parser import CloudInitOutputParser, CloudInitParser
from sunbeam_rca.parsers.dmesg_parser import DmesgParser
from sunbeam_rca.parsers.juju_models_parser import parse_juju_models
from sunbeam_rca.parsers.juju_parser import JujuParser
from sunbeam_rca.parsers.juju_status_parser import parse_juju_status
from sunbeam_rca.parsers.k8s_pod_log_parser import K8sPodLogParser
from sunbeam_rca.parsers.ovn_parser import OvnParser
from sunbeam_rca.parsers.pipeline_parser import PipelineParser
from sunbeam_rca.parsers.sunbeam_log_parser import SunbeamLogParser
from sunbeam_rca.parsers.syslog_parser import SyslogParser
from sunbeam_rca.state import RCAState

logger = logging.getLogger(__name__)


def parse_node(state: RCAState) -> dict:
    """Run parsers on collected files and build a unified event timeline.

    Identifies the pipeline failure timestamp from ``##[error]`` annotations.
    """
    all_events: list[LogEvent] = []
    sos_manifest = state.get("sosreport_manifest", {})

    # ── Pipeline logs ───────────────────────────────────────────────
    pipeline_parser = PipelineParser()
    for fpath in state.get("pipeline_log_files", []):
        if fpath.endswith("system.txt"):
            continue
        events = pipeline_parser.parse(fpath)
        all_events.extend(events)
        logger.info("Parsed %d events from pipeline: %s", len(events), fpath)

    # ── Syslog ──────────────────────────────────────────────────────
    syslog_parser = SyslogParser()
    syslog_path = sos_manifest.get("syslog")
    if syslog_path:
        events = syslog_parser.parse(syslog_path)
        all_events.extend(events)
        logger.info("Parsed %d events from syslog", len(events))

    # ── dmesg / kern.log ─────────────────────────────────────────────
    dmesg_parser = DmesgParser()
    dmesg_path = sos_manifest.get("dmesg")
    if dmesg_path:
        events = dmesg_parser.parse(dmesg_path)
        all_events.extend(events)
        logger.info("Parsed %d events from dmesg", len(events))

    kern_log_path = sos_manifest.get("kern_log")
    if kern_log_path:
        events = dmesg_parser.parse(kern_log_path)
        all_events.extend(events)
        logger.info("Parsed %d events from kern.log", len(events))

    # ── Juju unit/machine logs (MAAS model) ─────────────────────────
    juju_parser = JujuParser()
    for juju_log in sos_manifest.get("juju_logs", []):
        events = juju_parser.parse(juju_log)
        all_events.extend(events)
        logger.info("Parsed %d events from juju: %s", len(events), juju_log)

    # ── Cloud-init logs ─────────────────────────────────────────────
    cloud_init_parser = CloudInitParser()
    ci_log = sos_manifest.get("cloud_init_log")
    if ci_log:
        events = cloud_init_parser.parse(ci_log)
        all_events.extend(events)
        logger.info("Parsed %d events from cloud-init log", len(events))

    cloud_init_output_parser = CloudInitOutputParser()
    ci_output = sos_manifest.get("cloud_init_output_log")
    if ci_output:
        events = cloud_init_output_parser.parse(ci_output)
        all_events.extend(events)
        logger.info("Parsed %d events from cloud-init-output log", len(events))

    # ── OVN logs (openstack-hypervisor snap) ────────────────────────
    ovn_parser = OvnParser()
    for ovn_log in sos_manifest.get("ovn_logs", []):
        events = ovn_parser.parse(ovn_log)
        all_events.extend(events)
        logger.info("Parsed %d events from OVN: %s", len(events), ovn_log)

    # ── Sunbeam application logs ───────────────────────────────────
    sunbeam_parser = SunbeamLogParser()
    for sb_log in sos_manifest.get("sunbeam_app_logs", []):
        events = sunbeam_parser.parse(sb_log)
        all_events.extend(events)
        logger.info("Parsed %d events from sunbeam app log: %s", len(events), sb_log)

    # ── Kubernetes cluster-info pod logs ────────────────────────────
    k8s_parser = K8sPodLogParser()
    for k8s_log in sos_manifest.get("k8s_cluster_info_logs", []):
        events = k8s_parser.parse(k8s_log)
        all_events.extend(events)
        if events:
            logger.info("Parsed %d events from k8s pod log: %s", len(events), k8s_log)

    # ── Kubernetes pod logs from var/log/pods/ ─────────────────────
    pod_log_count = 0
    for pod_dir in sos_manifest.get("pod_log_dirs", []):
        pod_path = Path(pod_dir)
        if not pod_path.is_dir():
            continue
        for log_file in sorted(pod_path.rglob("*.log")):
            events = k8s_parser.parse(str(log_file))
            all_events.extend(events)
            pod_log_count += len(events)
    if pod_log_count:
        logger.info(
            "Parsed %d events from %d pod log dirs",
            pod_log_count, len(sos_manifest.get("pod_log_dirs", [])),
        )

    # ── Infrastructure files: meminfo, df ─────────────────────────
    meminfo_path = sos_manifest.get("meminfo")
    if meminfo_path:
        events = _parse_meminfo(meminfo_path)
        all_events.extend(events)
        if events:
            logger.info("Generated %d synthetic events from meminfo", len(events))

    df_path = sos_manifest.get("df_output")
    if df_path:
        events = _parse_df_output(df_path)
        all_events.extend(events)
        if events:
            logger.info("Generated %d synthetic events from df output", len(events))

    # ── Juju status JSON (multi-model) ──────────────────────────────
    juju_status_summary: dict = _empty_summary()
    machine_map: dict = {}
    juju_status_files = sos_manifest.get("juju_status_files", [])
    json_status_files = [f for f in juju_status_files if "json" in f.lower()]
    for jsf in json_status_files:
        js_events, summary, mmap = parse_juju_status(jsf)
        all_events.extend(js_events)
        if mmap:
            machine_map.update(mmap)
        _merge_status_summary(juju_status_summary, summary)
        logger.info(
            "Parsed Juju status JSON: %d synthetic events from %s", len(js_events), jsf
        )

    # ── Juju models topology ────────────────────────────────────────
    model_topology: list[dict] = []
    juju_models_file = sos_manifest.get("juju_models_file")
    if juju_models_file:
        model_topology = parse_juju_models(juju_models_file)

    # ── Sort and build timeline ─────────────────────────────────────
    all_events.sort(key=lambda e: e.timestamp)

    failure_ts = _find_failure_timestamp(all_events)

    fw_start, fw_end = _compute_failure_window(failure_ts, minutes_before=15)

    error_warning_events = [
        e for e in all_events if e.level in (LogLevel.ERROR, LogLevel.WARNING)
    ]
    timeline_summary = _build_timeline_summary(error_warning_events, limit=80)

    event_dicts = [e.model_dump(mode="json") for e in all_events]

    logger.info(
        "Total events: %d | Errors/Warnings: %d | Failure ts: %s | "
        "Failure window: %s to %s",
        len(all_events),
        len(error_warning_events),
        failure_ts,
        fw_start,
        fw_end,
    )

    return {
        "events": event_dicts,
        "failure_timestamp": failure_ts,
        "failure_window_start": fw_start,
        "failure_window_end": fw_end,
        "timeline_summary": timeline_summary,
        "juju_status_summary": juju_status_summary,
        "machine_map": machine_map,
        "model_topology": model_topology,
    }


def _empty_summary() -> dict:
    return {
        "stuck_units": [],
        "machines_missing_cni": [],
        "unhealthy_apps": [],
        "saas_issues": [],
        "offer_issues": [],
        "machine_count": 0,
        "application_count": 0,
        "saas_count": 0,
        "offer_count": 0,
    }


def _merge_status_summary(target: dict, source: dict) -> None:
    """Merge source summary into target, accumulating lists and max-ing counts."""
    for key in ("stuck_units", "machines_missing_cni", "unhealthy_apps",
                "saas_issues", "offer_issues"):
        target.setdefault(key, []).extend(source.get(key, []))
    for key in ("machine_count", "application_count", "saas_count", "offer_count"):
        target[key] = max(target.get(key, 0), source.get(key, 0))


def _find_failure_timestamp(events: list[LogEvent]) -> str:
    """Find the first ``##[error]`` event timestamp, or the last error."""
    for e in events:
        if e.source_type.value == "pipeline" and "##[error]" in e.message:
            return e.timestamp.isoformat()

    errors = [e for e in events if e.level == LogLevel.ERROR]
    if errors:
        return errors[-1].timestamp.isoformat()

    if events:
        return events[-1].timestamp.isoformat()

    return datetime.now(timezone.utc).isoformat()


def _compute_failure_window(
    failure_ts_str: str, minutes_before: int = 15,
) -> tuple[str, str]:
    """Return (start, end) ISO timestamps for the failure window.

    The window spans from *minutes_before* minutes before the failure
    timestamp to the failure timestamp itself.
    """
    try:
        ts = datetime.fromisoformat(failure_ts_str).astimezone(timezone.utc)
    except (ValueError, TypeError):
        ts = datetime.now(timezone.utc)
    start = ts - timedelta(minutes=minutes_before)
    return start.isoformat(), ts.isoformat()


def _build_timeline_summary(events: list[LogEvent], limit: int = 80) -> str:
    """Create a concise text timeline of error/warning events."""
    if not events:
        return "No error or warning events found."

    selected = events[:limit]
    lines: list[str] = []
    for e in selected:
        lines.append(e.to_context_str(max_msg_len=200))

    if len(events) > limit:
        lines.append(f"... and {len(events) - limit} more error/warning events")

    return "\n".join(lines)


def _parse_meminfo(file_path: str) -> list[LogEvent]:
    """Parse /proc/meminfo and emit a synthetic event if memory is low."""
    import re as _re

    events: list[LogEvent] = []
    try:
        text = Path(file_path).read_text(errors="replace")
    except OSError:
        return events

    mem_total = mem_available = None
    for line in text.splitlines():
        m = _re.match(r"MemTotal:\s+(\d+)\s+kB", line)
        if m:
            mem_total = int(m.group(1))
        m = _re.match(r"MemAvailable:\s+(\d+)\s+kB", line)
        if m:
            mem_available = int(m.group(1))

    if mem_total and mem_available:
        pct_available = (mem_available / mem_total) * 100
        if pct_available < 10:
            events.append(
                LogEvent(
                    timestamp=datetime.now(timezone.utc),
                    source_file=file_path,
                    line_number=0,
                    level=LogLevel.WARNING,
                    message=(
                        f"Low memory: {mem_available} kB available "
                        f"out of {mem_total} kB ({pct_available:.1f}% free)"
                    ),
                    source_type=SourceType.SYSLOG,
                    metadata={"synthetic": True, "mem_pct_available": round(pct_available, 1)},
                )
            )
    return events


def _parse_df_output(file_path: str) -> list[LogEvent]:
    """Parse df output and emit synthetic events for near-full filesystems."""
    import re as _re

    events: list[LogEvent] = []
    try:
        text = Path(file_path).read_text(errors="replace")
    except OSError:
        return events

    for line in text.splitlines():
        m = _re.search(r"(\d+)%\s+(/\S*)", line)
        if not m:
            continue
        usage_pct = int(m.group(1))
        mount_point = m.group(2)
        if mount_point.startswith("/snap/"):
            continue
        if usage_pct >= 95:
            events.append(
                LogEvent(
                    timestamp=datetime.now(timezone.utc),
                    source_file=file_path,
                    line_number=0,
                    level=LogLevel.WARNING,
                    message=(
                        f"Disk nearly full: {mount_point} at {usage_pct}% usage"
                    ),
                    source_type=SourceType.SYSLOG,
                    metadata={"synthetic": True, "mount": mount_point, "usage_pct": usage_pct},
                )
            )
    return events
