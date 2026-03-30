"""Parser for juju_models JSON output from sosreport.

Extracts the Juju model topology: model names, types (MAAS vs k8s),
status, and controller info.  This gives the auto-triager a map of the
full Sunbeam deployment so it can understand cross-model relationships.

Sunbeam typical layout:
- controller     (kubernetes / caas)
- openstack      (kubernetes / caas)  — K8s charms (keystone, nova, cinder, etc.)
- openstack-machines (manual / iaas)  — bare-metal charms (k8s, microceph, hypervisor)
- openstack-infra (optional, maas)    — infrastructure charms
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_juju_models(file_path: str) -> list[dict]:
    """Parse a ``juju models --format json`` output file.

    Returns a list of model summary dicts, each containing:
      name, short_name, model_type (caas/iaas), cloud, status, agent_version
    """
    p = Path(file_path)
    if not p.is_file():
        return []

    try:
        data = json.loads(p.read_text(errors="replace"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Could not parse juju_models JSON: %s", file_path)
        return []

    models_raw = data.get("models", [])
    models: list[dict] = []
    for m in models_raw:
        models.append({
            "name": m.get("name", ""),
            "short_name": m.get("short-name", ""),
            "model_type": m.get("model-type", m.get("type", "")),
            "cloud": m.get("cloud", ""),
            "region": m.get("region", ""),
            "status": m.get("status", {}).get("current", ""),
            "is_controller": m.get("is-controller", False),
            "agent_version": m.get("agent-version", ""),
            "last_connection": m.get("last-connection", ""),
        })

    logger.info(
        "Parsed juju_models: %d models (%s)",
        len(models),
        ", ".join(m["short_name"] for m in models),
    )
    return models


def format_model_topology(models: list[dict]) -> str:
    """Render model topology for LLM context."""
    if not models:
        return "No model topology available."
    lines = []
    for m in models:
        kind = "K8s" if m["model_type"] == "caas" else "MAAS"
        ctrl = " (controller)" if m["is_controller"] else ""
        lines.append(
            f"- **{m['short_name']}** [{kind}] "
            f"cloud={m['cloud']}/{m['region']} "
            f"status={m['status']}{ctrl}"
        )
    return "\n".join(lines)
