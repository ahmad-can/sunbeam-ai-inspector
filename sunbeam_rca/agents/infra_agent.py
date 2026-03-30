"""Infrastructure domain agent — OS, kernel, disk, snap, cloud-init, LXD."""

from __future__ import annotations

from sunbeam_rca.agents.base_agent import BaseDomainAgent
from sunbeam_rca.agents.models import DOMAIN_INFRASTRUCTURE
from sunbeam_rca.agents.prompts import INFRA_SYSTEM


class InfraAgent(BaseDomainAgent):
    domain = DOMAIN_INFRASTRUCTURE
    system_prompt = INFRA_SYSTEM

    def _enrich_user_prompt(self, state: dict) -> str:
        parts: list[str] = []

        manifest = state.get("sosreport_manifest", {})
        if manifest.get("meminfo"):
            parts.append(f"## System memory info file: {manifest['meminfo']}")
        if manifest.get("df_output"):
            parts.append(f"## Disk usage file: {manifest['df_output']}")
        if manifest.get("hostname"):
            parts.append(f"## Hostname: {manifest['hostname']}")

        return "\n".join(parts)
