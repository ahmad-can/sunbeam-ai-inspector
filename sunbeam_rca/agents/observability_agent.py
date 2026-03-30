"""Observability domain agent — OpenTelemetry, Grafana Agent, COS, Prometheus."""

from __future__ import annotations

from sunbeam_rca.agents.base_agent import BaseDomainAgent
from sunbeam_rca.agents.models import DOMAIN_OBSERVABILITY
from sunbeam_rca.agents.prompts import OBSERVABILITY_SYSTEM


class ObservabilityAgent(BaseDomainAgent):
    domain = DOMAIN_OBSERVABILITY
    system_prompt = OBSERVABILITY_SYSTEM

    def _enrich_user_prompt(self, state: dict) -> str:
        parts: list[str] = []

        manifest = state.get("sosreport_manifest", {})
        hostname = manifest.get("hostname", "")
        if hostname:
            parts.append(f"## Hostname: {hostname}")

        return "\n".join(parts)

    def _should_analyze_without_matches(self, events: list[dict]) -> bool:
        error_count = sum(
            1 for e in events if e.get("level") in ("ERROR", "WARNING")
        )
        return error_count >= 3
