"""Storage domain agent — MicroCeph, RADOS, OSD, Ceph health."""

from __future__ import annotations

from sunbeam_rca.agents.base_agent import BaseDomainAgent
from sunbeam_rca.agents.models import DOMAIN_STORAGE
from sunbeam_rca.agents.prompts import STORAGE_SYSTEM


class StorageAgent(BaseDomainAgent):
    domain = DOMAIN_STORAGE
    system_prompt = STORAGE_SYSTEM

    def _enrich_user_prompt(self, state: dict) -> str:
        parts: list[str] = []

        summary = state.get("juju_status_summary", {})
        stuck = summary.get("stuck_units", [])
        ceph_units = [
            u for u in stuck
            if "microceph" in u.get("unit", "").lower()
            or "ceph" in u.get("unit", "").lower()
        ]
        if ceph_units:
            parts.append(f"## Ceph/MicroCeph Units ({len(ceph_units)})")
            for u in ceph_units:
                parts.append(
                    f"- {u['unit']}: status={u['status']}, "
                    f"message=\"{u.get('message', '')}\""
                )

        offers = summary.get("offer_issues", [])
        ceph_offers = [
            o for o in offers
            if "microceph" in o.get("offer_name", "").lower()
            or "ceph" in o.get("offer_name", "").lower()
        ]
        if ceph_offers:
            parts.append(f"## Ceph-Related Cross-Model Offers ({len(ceph_offers)})")
            for o in ceph_offers:
                parts.append(
                    f"- {o['offer_name']} ({o.get('url', '?')}): "
                    f"status={o['status']}, "
                    f"message=\"{o.get('message', '')}\""
                )

        return "\n".join(parts)
