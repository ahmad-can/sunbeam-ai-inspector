"""Network domain agent — Cilium CNI, K8s DNS, OVN, TCP connectivity."""

from __future__ import annotations

from sunbeam_rca.agents.base_agent import BaseDomainAgent
from sunbeam_rca.agents.models import DOMAIN_NETWORK
from sunbeam_rca.agents.prompts import NETWORK_SYSTEM


class NetworkAgent(BaseDomainAgent):
    domain = DOMAIN_NETWORK
    system_prompt = NETWORK_SYSTEM

    def _enrich_user_prompt(self, state: dict) -> str:
        parts: list[str] = []

        summary = state.get("juju_status_summary", {})
        missing_cni = summary.get("machines_missing_cni", [])
        if missing_cni:
            parts.append(f"## Machines Missing Cilium CNI ({len(missing_cni)})")
            for m in missing_cni:
                parts.append(
                    f"- Machine {m.get('machine', '?')} "
                    f"({m.get('hostname', '?')}): "
                    f"interfaces={m.get('interfaces', [])}"
                )

        machine_map = state.get("machine_map", {})
        if machine_map:
            parts.append("## Machine-to-Hostname Mapping")
            for mid, hostname in machine_map.items():
                parts.append(f"- Machine {mid}: {hostname}")

        return "\n".join(parts)

    def _should_analyze_without_matches(self, events: list[dict]) -> bool:
        return True
