"""Route parsed events and patterns to the correct domain agent.

Some events belong to multiple domains (e.g. syslog entries about microceph
are relevant to both infrastructure and storage). The router uses pattern
category, source_type, and content heuristics to assign events.
"""

from __future__ import annotations

import re

from sunbeam_rca.agents.models import (
    DOMAIN_INFRASTRUCTURE,
    DOMAIN_JUJU,
    DOMAIN_KUBERNETES,
    DOMAIN_NETWORK,
    DOMAIN_OBSERVABILITY,
    DOMAIN_PIPELINE,
    DOMAIN_STORAGE,
)

PATTERN_CATEGORY_TO_DOMAIN: dict[str, str] = {
    "pipeline": DOMAIN_PIPELINE,
    "juju": DOMAIN_JUJU,
    "kernel": DOMAIN_INFRASTRUCTURE,
    "disk": DOMAIN_INFRASTRUCTURE,
    "network": DOMAIN_NETWORK,
    "container": DOMAIN_KUBERNETES,
    "lxd": DOMAIN_INFRASTRUCTURE,
    "ceph": DOMAIN_STORAGE,
    "snap": DOMAIN_INFRASTRUCTURE,
    "sunbeam": DOMAIN_JUJU,
    "openstack": DOMAIN_NETWORK,
    "kubernetes": DOMAIN_KUBERNETES,
    "k8s_control_plane": DOMAIN_KUBERNETES,
    "security": DOMAIN_INFRASTRUCTURE,
    "observability": DOMAIN_OBSERVABILITY,
    "validation": DOMAIN_PIPELINE,
    "infrastructure": DOMAIN_INFRASTRUCTURE,
}

_STORAGE_RE = re.compile(
    r"microceph|ceph|rados|osd\.\d|HEALTH_ERR|HEALTH_WARN", re.IGNORECASE
)
_NETWORK_RE = re.compile(
    r"cilium|ovn|ovsdb|DNS|svc\.cluster\.local|connection refused"
    r"|timed? ?out|unreachable|SSL_read",
    re.IGNORECASE,
)
_K8S_RE = re.compile(
    r"kubelet|kube-proxy|pod|CrashLoopBackOff|NodeNotReady|container",
    re.IGNORECASE,
)
_K8S_CONTROL_PLANE_RE = re.compile(
    r"k8s\.(k8sd|containerd|kubelet|kube-apiserver|kube-scheduler|kube-controller-manager)"
    r"|etcd(server)?:|NetworkPluginNotReady|cni plugin"
    r"|ck-network.*not installed|no network config found",
    re.IGNORECASE,
)
_JUJU_RE = re.compile(
    r"juju|charm|hook failed|agent.*lost|manifold worker|relation|integration",
    re.IGNORECASE,
)
_OBSERVABILITY_RE = re.compile(
    r"opentelemetry|otelcol|grafana.agent|cos.agent|prometheus|otel"
    r"|snap\.opentelemetry-collector|snap\.grafana-agent",
    re.IGNORECASE,
)
_OPENSTACK_RE = re.compile(
    r"keystone|nova|neutron|glance|cinder|horizon|placement|barbican|heat"
    r"|octavia|designate|magnum|manila|ironic|aodh|gnocchi|ceilometer"
    r"|mysql|rabbitmq|amqp|mariadb",
    re.IGNORECASE,
)


def route_event(event: dict) -> set[str]:
    """Return the set of domain names an event is relevant to."""
    domains: set[str] = set()
    source_type = event.get("source_type", "")
    message = event.get("message", "")

    if source_type == "pipeline":
        domains.add(DOMAIN_PIPELINE)
        if _OBSERVABILITY_RE.search(message):
            domains.add(DOMAIN_OBSERVABILITY)
    elif source_type == "sunbeam":
        domains.add(DOMAIN_JUJU)
        if _K8S_RE.search(message) or "k8s" in message.lower():
            domains.add(DOMAIN_KUBERNETES)
        if _NETWORK_RE.search(message):
            domains.add(DOMAIN_NETWORK)
        if "terraform" in message.lower() or "state lock" in message.lower():
            domains.add(DOMAIN_INFRASTRUCTURE)
    elif source_type == "kubernetes":
        domains.add(DOMAIN_KUBERNETES)
        if _OPENSTACK_RE.search(message):
            domains.add(DOMAIN_NETWORK)
        if _STORAGE_RE.search(message):
            domains.add(DOMAIN_STORAGE)
        if _JUJU_RE.search(message):
            domains.add(DOMAIN_JUJU)
        if _OBSERVABILITY_RE.search(message):
            domains.add(DOMAIN_OBSERVABILITY)
    elif source_type == "cloud_init":
        domains.add(DOMAIN_INFRASTRUCTURE)
    elif source_type == "dmesg":
        domains.add(DOMAIN_INFRASTRUCTURE)

    if source_type in ("syslog", "juju"):
        if _STORAGE_RE.search(message):
            domains.add(DOMAIN_STORAGE)
        if _NETWORK_RE.search(message):
            domains.add(DOMAIN_NETWORK)
        if _K8S_RE.search(message):
            domains.add(DOMAIN_KUBERNETES)
        if _K8S_CONTROL_PLANE_RE.search(message):
            domains.add(DOMAIN_KUBERNETES)
            domains.add(DOMAIN_INFRASTRUCTURE)
        if _JUJU_RE.search(message):
            domains.add(DOMAIN_JUJU)
        if _OBSERVABILITY_RE.search(message):
            domains.add(DOMAIN_OBSERVABILITY)

    if source_type == "syslog":
        process = event.get("metadata", {}).get("process", "")
        if process in (
            "k8s.k8sd", "k8s.containerd", "k8s.kubelet",
            "k8s.kube-apiserver", "k8s.kube-scheduler",
            "k8s.kube-controller-manager",
        ):
            domains.add(DOMAIN_KUBERNETES)
            domains.add(DOMAIN_INFRASTRUCTURE)
        if process in (
            "opentelemetry-collector.opentelemetry-collector",
            "grafana-agent.grafana-agent",
        ) or "opentelemetry" in process or "grafana-agent" in process:
            domains.add(DOMAIN_OBSERVABILITY)

    if source_type == "syslog" and not domains:
        domains.add(DOMAIN_INFRASTRUCTURE)
    if source_type == "juju" and DOMAIN_JUJU not in domains:
        domains.add(DOMAIN_JUJU)

    metadata = event.get("metadata", {})
    if metadata.get("synthetic"):
        msg_lower = message.lower()
        if "cilium" in msg_lower or "missing" in msg_lower:
            domains.add(DOMAIN_NETWORK)
        if "saas" in msg_lower or "offer" in msg_lower:
            domains.add(DOMAIN_JUJU)
        if "microceph" in msg_lower or "ceph" in msg_lower:
            domains.add(DOMAIN_STORAGE)

    if not domains:
        domains.add(DOMAIN_INFRASTRUCTURE)

    return domains


def route_pattern(category: str) -> str:
    """Map a pattern category to its primary domain."""
    return PATTERN_CATEGORY_TO_DOMAIN.get(category, DOMAIN_INFRASTRUCTURE)


def partition_events(events: list[dict]) -> dict[str, list[dict]]:
    """Split all events into per-domain buckets.

    An event may appear in multiple domain buckets if it is relevant
    to more than one domain.
    """
    buckets: dict[str, list[dict]] = {
        DOMAIN_INFRASTRUCTURE: [],
        DOMAIN_NETWORK: [],
        DOMAIN_KUBERNETES: [],
        DOMAIN_JUJU: [],
        DOMAIN_STORAGE: [],
        DOMAIN_OBSERVABILITY: [],
        DOMAIN_PIPELINE: [],
    }
    for event in events:
        for domain in route_event(event):
            buckets[domain].append(event)
    return buckets


def partition_patterns(patterns: list) -> dict[str, list]:
    """Split patterns into per-domain buckets by category."""
    buckets: dict[str, list] = {
        DOMAIN_INFRASTRUCTURE: [],
        DOMAIN_NETWORK: [],
        DOMAIN_KUBERNETES: [],
        DOMAIN_JUJU: [],
        DOMAIN_STORAGE: [],
        DOMAIN_OBSERVABILITY: [],
        DOMAIN_PIPELINE: [],
    }
    for p in patterns:
        cat = p.category if hasattr(p, "category") else p.get("category", "")
        domain = route_pattern(cat)
        buckets[domain].append(p)
    return buckets
