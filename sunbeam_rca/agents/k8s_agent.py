"""Kubernetes domain agent — pods, nodes, containers, kubelet."""

from __future__ import annotations

from sunbeam_rca.agents.base_agent import BaseDomainAgent
from sunbeam_rca.agents.models import DOMAIN_KUBERNETES
from sunbeam_rca.agents.prompts import K8S_SYSTEM


class K8sAgent(BaseDomainAgent):
    domain = DOMAIN_KUBERNETES
    system_prompt = K8S_SYSTEM

    def _enrich_user_prompt(self, state: dict) -> str:
        parts: list[str] = []

        manifest = state.get("sosreport_manifest", {})
        k8s_logs = manifest.get("k8s_cluster_info_logs", [])
        if k8s_logs:
            parts.append(f"## K8s cluster-info log files ({len(k8s_logs)} files)")
            for f in k8s_logs[:10]:
                parts.append(f"- {f}")

        pod_dirs = manifest.get("pod_log_dirs", [])
        if pod_dirs:
            parts.append(f"## Pod log directories ({len(pod_dirs)} pods)")
            for d in pod_dirs[:10]:
                parts.append(f"- {d}")

        return "\n".join(parts)
