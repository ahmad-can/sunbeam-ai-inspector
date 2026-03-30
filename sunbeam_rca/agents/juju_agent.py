"""Juju domain agent — models, units, relations, SAAS, charm lifecycle."""

from __future__ import annotations

from sunbeam_rca.agents.base_agent import BaseDomainAgent
from sunbeam_rca.agents.models import DOMAIN_JUJU
from sunbeam_rca.agents.prompts import JUJU_SYSTEM


class JujuAgent(BaseDomainAgent):
    domain = DOMAIN_JUJU
    system_prompt = JUJU_SYSTEM

    def _enrich_user_prompt(self, state: dict) -> str:
        parts: list[str] = []

        topology = state.get("model_topology", [])
        if topology:
            parts.append("## Juju Model Topology")
            for m in topology:
                kind = "K8s" if m.get("model_type") == "caas" else "MAAS"
                ctrl = " (controller)" if m.get("is_controller") else ""
                parts.append(
                    f"- **{m.get('short_name', '?')}** [{kind}] "
                    f"cloud={m.get('cloud', '?')}/{m.get('region', '?')} "
                    f"status={m.get('status', '?')}{ctrl}"
                )

        summary = state.get("juju_status_summary", {})
        if summary:
            unhealthy_apps = summary.get("unhealthy_apps", [])
            if unhealthy_apps:
                parts.append(f"## Unhealthy Applications ({len(unhealthy_apps)})")
                for a in unhealthy_apps[:15]:
                    parts.append(
                        f"- [{a.get('model', '?')}] {a['application']}: "
                        f"status={a['status']}, "
                        f"message=\"{a.get('message', '')}\""
                    )

            stuck = summary.get("stuck_units", [])
            if stuck:
                parts.append(f"## Stuck Units ({len(stuck)})")
                for u in stuck[:20]:
                    parts.append(
                        f"- [{u.get('model', '?')}] {u['unit']}: "
                        f"status={u['status']}, "
                        f"message=\"{u.get('message', '')}\", "
                        f"since={u.get('since', '?')}"
                    )

            saas = summary.get("saas_issues", [])
            if saas:
                parts.append(f"## SAAS Integration Issues ({len(saas)})")
                for s in saas[:10]:
                    parts.append(
                        f"- [{s.get('model', '?')}] {s.get('saas_name', '?')} "
                        f"(from {s.get('offer_url', '?')}): "
                        f"status={s['status']}"
                    )

            offers = summary.get("offer_issues", [])
            if offers:
                parts.append(f"## Cross-Model Offer Issues ({len(offers)})")
                for o in offers[:10]:
                    parts.append(
                        f"- [{o.get('model', '?')}] {o['offer_name']} "
                        f"({o.get('url', '?')}): status={o['status']}, "
                        f"message=\"{o.get('message', '')}\""
                    )

        return "\n".join(parts)

    def _should_analyze_without_matches(self, events: list[dict]) -> bool:
        return True
