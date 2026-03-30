"""Parser for Juju status JSON files from sosreport.

Extracts structured data that plain log parsers miss:
- Unit workload statuses (stuck/error/waiting units)
- Machine network interfaces (detect missing cilium_host)
- Machine-to-hostname mapping
- SAAS (cross-model) relations and their health
- Application-level status and messages
- Offers provided by the model
- Model name / type extracted from the status header

Emits synthetic LogEvents for detected anomalies so they can be
picked up by the pattern matcher like any other log event.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from sunbeam_rca.models import LogEvent, LogLevel, SourceType

logger = logging.getLogger(__name__)

EXPECTED_CNI_INTERFACE = "cilium_host"

_UNHEALTHY_STATUSES = {"error", "blocked", "waiting"}


def parse_juju_status(file_path: str) -> tuple[list[LogEvent], dict, dict]:
    """Parse a Juju status JSON file.

    Returns:
        (events, juju_status_summary, machine_map)
        - events: synthetic LogEvents for anomalies
        - juju_status_summary: dict summarising model state
        - machine_map: {machine_id: hostname}
    """
    p = Path(file_path)
    if not p.is_file():
        return [], {}, {}

    try:
        data = json.loads(p.read_text(errors="replace"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Could not parse Juju status JSON: %s", file_path)
        return [], {}, {}

    events: list[LogEvent] = []
    stuck_units: list[dict] = []
    machines_missing_cni: list[dict] = []
    machine_map: dict[str, str] = {}
    unhealthy_apps: list[dict] = []
    saas_issues: list[dict] = []
    offer_issues: list[dict] = []

    report_ts = _extract_timestamp(data)
    model_name = _extract_model_name(data, file_path)

    # ── Machines ────────────────────────────────────────────────────
    machines = data.get("machines", {})
    for mid, mdata in machines.items():
        hostname = mdata.get("hostname", "")
        dns_name = mdata.get("dns-name", "")
        machine_map[mid] = hostname or dns_name

        interfaces = mdata.get("network-interfaces", {})
        has_cni = any(
            EXPECTED_CNI_INTERFACE in iface_name
            for iface_name in interfaces
        )
        if not has_cni and interfaces:
            info = {
                "machine": mid,
                "hostname": hostname or dns_name,
                "interfaces": list(interfaces.keys()),
                "model": model_name,
            }
            machines_missing_cni.append(info)
            events.append(LogEvent(
                timestamp=report_ts,
                source_file=file_path,
                line_number=0,
                level=LogLevel.ERROR,
                message=(
                    f"Machine {mid} ({hostname or dns_name}) is missing "
                    f"{EXPECTED_CNI_INTERFACE} network interface — "
                    f"Kubernetes pod networking (Cilium CNI) not operational. "
                    f"Present interfaces: {', '.join(interfaces.keys())}"
                ),
                source_type=SourceType.JUJU,
                metadata={
                    "synthetic": True,
                    "observation_type": "state_snapshot",
                    "check": "cilium_cni_missing",
                    **info,
                },
            ))

    # ── Applications — app-level status ─────────────────────────────
    applications = data.get("applications", {})
    for app_name, app_data in applications.items():
        app_status = app_data.get("application-status", {})
        app_current = app_status.get("current", "")
        app_message = app_status.get("message", "")
        app_since = app_status.get("since", "")

        if app_current in _UNHEALTHY_STATUSES:
            info = {
                "application": app_name,
                "status": app_current,
                "message": app_message,
                "since": app_since,
                "model": model_name,
                "charm": app_data.get("charm", ""),
                "scale": app_data.get("scale", len(app_data.get("units", {}))),
            }
            unhealthy_apps.append(info)
            app_ts = _parse_since(app_since) or report_ts
            events.append(LogEvent(
                timestamp=app_ts,
                source_file=file_path,
                line_number=0,
                level=LogLevel.WARNING,
                message=(
                    f"[{model_name}] Application {app_name} status: "
                    f"{app_current} — {app_message} (since {app_since})"
                ),
                source_type=SourceType.JUJU,
                metadata={
                    "synthetic": True,
                    "observation_type": "state_snapshot",
                    "check": "app_unhealthy",
                    **info,
                },
            ))

        # ── Units ───────────────────────────────────────────────────
        units = app_data.get("units", {})
        for unit_name, unit_data in units.items():
            workload = unit_data.get("workload-status", {})
            w_current = workload.get("current", "")
            w_message = workload.get("message", "")
            w_since = workload.get("since", "")

            if w_current in _UNHEALTHY_STATUSES:
                info = {
                    "unit": unit_name,
                    "application": app_name,
                    "status": w_current,
                    "message": w_message,
                    "since": w_since,
                    "machine": unit_data.get("machine", ""),
                    "model": model_name,
                }
                stuck_units.append(info)

                unit_ts = _parse_since(w_since) or report_ts
                events.append(LogEvent(
                    timestamp=unit_ts,
                    source_file=file_path,
                    line_number=0,
                    level=LogLevel.ERROR if w_current == "error" else LogLevel.WARNING,
                    message=(
                        f"[{model_name}] Unit {unit_name} workload status: "
                        f"{w_current} — {w_message} (since {w_since})"
                    ),
                    source_type=SourceType.JUJU,
                    metadata={
                        "synthetic": True,
                        "observation_type": "state_snapshot",
                        "check": "unit_unhealthy",
                        **info,
                    },
                ))

            for sub_name, sub_data in unit_data.get("subordinates", {}).items():
                sub_workload = sub_data.get("workload-status", {})
                sw_current = sub_workload.get("current", "")
                sw_message = sub_workload.get("message", "")
                sw_since = sub_workload.get("since", "")
                if sw_current in _UNHEALTHY_STATUSES:
                    info = {
                        "unit": sub_name,
                        "application": app_name,
                        "status": sw_current,
                        "message": sw_message,
                        "since": sw_since,
                        "model": model_name,
                    }
                    stuck_units.append(info)
                    sub_ts = _parse_since(sw_since) or report_ts
                    events.append(LogEvent(
                        timestamp=sub_ts,
                        source_file=file_path,
                        line_number=0,
                        level=LogLevel.ERROR if sw_current == "error" else LogLevel.WARNING,
                        message=(
                            f"[{model_name}] Subordinate unit {sub_name} "
                            f"workload status: {sw_current} — {sw_message} "
                            f"(since {sw_since})"
                        ),
                        source_type=SourceType.JUJU,
                        metadata={
                            "synthetic": True,
                            "observation_type": "state_snapshot",
                            "check": "unit_unhealthy",
                            **info,
                        },
                    ))

    # ── SAAS / remote-applications ──────────────────────────────────
    remote_apps = data.get("remote-applications", {})
    for ra_name, ra_data in remote_apps.items():
        ra_status = ra_data.get("application-status", {}).get("current", "")
        ra_msg = ra_data.get("application-status", {}).get("message", "")
        ra_url = ra_data.get("offer-url", "")
        if ra_status in _UNHEALTHY_STATUSES:
            info = {
                "saas_name": ra_name,
                "status": ra_status,
                "message": ra_msg,
                "offer_url": ra_url,
                "model": model_name,
            }
            saas_issues.append(info)
            events.append(LogEvent(
                timestamp=report_ts,
                source_file=file_path,
                line_number=0,
                level=LogLevel.WARNING,
                message=(
                    f"[{model_name}] SAAS integration {ra_name} "
                    f"(from {ra_url}) status: {ra_status} — {ra_msg}"
                ),
                source_type=SourceType.JUJU,
                metadata={
                    "synthetic": True,
                    "observation_type": "state_snapshot",
                    "check": "saas_unhealthy",
                    **info,
                },
            ))

    # ── Offers (application-endpoints) ──────────────────────────────
    offers = data.get("application-endpoints", {})
    for offer_name, offer_data in offers.items():
        o_status = offer_data.get("application-status", {}).get("current", "")
        o_msg = offer_data.get("application-status", {}).get("message", "")
        o_url = offer_data.get("url", "")
        if o_status in _UNHEALTHY_STATUSES:
            info = {
                "offer_name": offer_name,
                "status": o_status,
                "message": o_msg,
                "url": o_url,
                "model": model_name,
            }
            offer_issues.append(info)
            events.append(LogEvent(
                timestamp=report_ts,
                source_file=file_path,
                line_number=0,
                level=LogLevel.WARNING,
                message=(
                    f"[{model_name}] Cross-model offer {offer_name} "
                    f"({o_url}) status: {o_status} — {o_msg}"
                ),
                source_type=SourceType.JUJU,
                metadata={
                    "synthetic": True,
                    "observation_type": "state_snapshot",
                    "check": "offer_unhealthy",
                    **info,
                },
            ))

    summary = {
        "model_name": model_name,
        "stuck_units": stuck_units,
        "machines_missing_cni": machines_missing_cni,
        "unhealthy_apps": unhealthy_apps,
        "saas_issues": saas_issues,
        "offer_issues": offer_issues,
        "machine_count": len(machines),
        "application_count": len(applications),
        "saas_count": len(remote_apps),
        "offer_count": len(offers),
    }

    logger.info(
        "Juju status [%s]: %d stuck units, %d machines missing CNI, "
        "%d unhealthy apps, %d SAAS issues, %d offer issues",
        model_name,
        len(stuck_units),
        len(machines_missing_cni),
        len(unhealthy_apps),
        len(saas_issues),
        len(offer_issues),
    )
    return events, summary, machine_map


def _extract_model_name(data: dict, file_path: str) -> str:
    """Extract model name from the status JSON or from the filename."""
    model = data.get("model", {})
    name = model.get("name", "")
    if name:
        return name
    basename = os.path.basename(file_path)
    if "openstack-machines" in basename:
        return "openstack-machines"
    if "openstack" in basename:
        return "openstack"
    if "controller" in basename:
        return "controller"
    return "unknown"


def _extract_timestamp(data: dict) -> datetime:
    """Get the controller timestamp from Juju status, or use epoch."""
    ctrl = data.get("controller", {})
    ts_str = ctrl.get("timestamp", "")
    if ts_str:
        try:
            today = datetime.now(timezone.utc).date()
            t = datetime.strptime(ts_str, "%H:%M:%SZ").time()
            return datetime.combine(today, t, tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _parse_since(since_str: str) -> datetime | None:
    """Parse Juju 'since' field like '11 Feb 2026 10:18:40Z'."""
    if not since_str:
        return None
    try:
        return datetime.strptime(since_str, "%d %b %Y %H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None
