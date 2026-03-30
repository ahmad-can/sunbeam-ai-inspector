"""Pipeline domain agent — GitHub Actions, subprocess, timeouts."""

from __future__ import annotations

from sunbeam_rca.agents.base_agent import BaseDomainAgent
from sunbeam_rca.agents.models import DOMAIN_PIPELINE
from sunbeam_rca.agents.prompts import PIPELINE_SYSTEM


class PipelineAgent(BaseDomainAgent):
    domain = DOMAIN_PIPELINE
    system_prompt = PIPELINE_SYSTEM

    def _enrich_user_prompt(self, state: dict) -> str:
        parts: list[str] = []

        machine_map = state.get("machine_map", {})
        if machine_map:
            parts.append("## Machine-to-Hostname Mapping")
            for mid, hostname in machine_map.items():
                parts.append(f"- Machine {mid}: {hostname}")

        return "\n".join(parts)
