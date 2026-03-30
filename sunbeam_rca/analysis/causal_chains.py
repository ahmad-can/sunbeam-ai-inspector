"""Known causal relationships between failure patterns.

Defines a directed graph where an edge A -> B means "pattern A can cause
pattern B".  The scorer uses this to:
- Boost upstream (root-cause) patterns that have matched downstream effects
- Penalize downstream (symptom) patterns whose upstream cause is present
- Apply transitive depth penalties: a pattern deep in the symptom chain
  (multiple hops from the root cause) gets a progressively larger penalty
"""

from __future__ import annotations

UPSTREAM_BONUS = 0.20
DOWNSTREAM_PENALTY = 0.15
TRANSITIVE_DEPTH_PENALTY = 0.05

CAUSAL_GRAPH: dict[str, list[str]] = {
    # ── etcd / control plane chain ─────────────────────────────────
    # etcd restart (e.g. from charm upgrade hook) → leadership loss
    "ETCD_RESTART": [
        "ETCD_NO_LEADER",
    ],
    # etcd no leader → API server and control plane instability
    "ETCD_NO_LEADER": [
        "K8S_APISERVER_WATCH_ERROR",
        "K8S_CONTROL_PLANE_UNSTABLE",
    ],
    # API server watch errors → downstream pod/service failures
    "K8S_APISERVER_WATCH_ERROR": [
        "CILIUM_POD_PENDING",
        "METALLB_WEBHOOK_FAILURE",
        "K8S_POD_CRASH_LOOP",
    ],
    # Control plane instability → pod and service failures
    "K8S_CONTROL_PLANE_UNSTABLE": [
        "CILIUM_POD_PENDING",
        "K8S_POD_CRASH_LOOP",
        "K8S_NODE_NOT_READY",
    ],
    # Cilium pod stuck Pending → CNI never comes up
    "CILIUM_POD_PENDING": [
        "CILIUM_CNI_MISSING",
        "KUBELET_NETWORK_PLUGIN_NOT_READY",
    ],

    # ── k8sd feature ordering chain ────────────────────────────────
    # k8sd tries to configure features before ck-network is installed
    "K8SD_FEATURE_ORDERING_ERROR": [
        "CONTAINERD_CNI_INIT_FAIL",
        "KUBELET_NETWORK_PLUGIN_NOT_READY",
    ],
    # containerd starts without CNI config → kubelet cannot schedule
    "CONTAINERD_CNI_INIT_FAIL": [
        "KUBELET_NETWORK_PLUGIN_NOT_READY",
    ],
    # kubelet NetworkPluginNotReady → pods cannot start, CNI missing
    "KUBELET_NETWORK_PLUGIN_NOT_READY": [
        "CILIUM_CNI_MISSING",
        "K8S_NODE_NOT_READY",
    ],

    # ── Infrastructure / networking (existing + extended) ──────────
    "CILIUM_CNI_MISSING": [
        "K8S_DNS_RESOLUTION_FAIL",
        "CURL_CONNECT_TIMEOUT",
        "NETWORK_TIMEOUT",
        "K8S_NODE_NOT_READY",
    ],
    "K8S_DNS_RESOLUTION_FAIL": [
        "MICROCEPH_RADOS_ERROR",
        "MICROCEPH_DB_UNINITIALIZED",
        "JUJU_AGENT_LOST",
        "NETWORK_CONNECTION_REFUSED",
        "JUJU_SAAS_INTEGRATION_BLOCKED",
    ],
    "CURL_CONNECT_TIMEOUT": [
        "JUJU_AGENT_LOST",
    ],
    # Ceph chain
    "MICROCEPH_RADOS_ERROR": [
        "MICROCEPH_DB_UNINITIALIZED",
    ],
    "MICROCEPH_DB_UNINITIALIZED": [
        "SUNBEAM_WAIT_TIMEOUT",
        "CEPH_HEALTH_ERR",
        "JUJU_SAAS_INTEGRATION_BLOCKED",
    ],
    # Sunbeam chain
    "SUNBEAM_WAIT_TIMEOUT": [
        "PIPELINE_SUBPROCESS_ERROR",
        "PIPELINE_COMMAND_FAILED",
        "PIPELINE_ERROR_ANNOTATION",
        "SUNBEAM_CLUSTER_JOIN_FAIL",
    ],
    # Juju cross-model chain: SAAS integrations connect models
    "JUJU_SAAS_INTEGRATION_BLOCKED": [
        "JUJU_INTEGRATION_INCOMPLETE",
        "JUJU_APP_WAITING",
        "JUJU_APP_BLOCKED",
    ],
    "JUJU_INTEGRATION_INCOMPLETE": [
        "JUJU_APP_WAITING",
        "JUJU_APP_BLOCKED",
        "OPENSTACK_HYPERVISOR_ERROR",
    ],
    # K8s service disruption → SAAS and application failures
    "K8S_POD_CRASH_LOOP": [
        "JUJU_SAAS_INTEGRATION_BLOCKED",
        "JUJU_APP_BLOCKED",
        "NETWORK_CONNECTION_REFUSED",
    ],
    "K8S_NODE_NOT_READY": [
        "K8S_POD_CRASH_LOOP",
        "K8S_DNS_RESOLUTION_FAIL",
    ],
    # OVN networking chain
    "OVN_OVSDB_CONNECTION_FAILED": [
        "OVN_CONNECTION_RESET",
        "OPENSTACK_HYPERVISOR_ERROR",
    ],
    # Snap / Juju chain
    "SNAP_INSTALL_FAILED": [
        "JUJU_HOOK_FAILED",
        "SNAP_SERVICE_FAILED",
    ],
    "SNAP_SERVICE_FAILED": [
        "JUJU_HOOK_FAILED",
        "OPENSTACK_HYPERVISOR_ERROR",
    ],
    # Kernel
    "OOM_KILL": [
        "JUJU_AGENT_LOST",
        "JUJU_WORKER_UNEXPECTED_ERROR",
        "K8S_NODE_NOT_READY",
    ],
    # Disk
    "DISK_FULL": [
        "SNAP_INSTALL_FAILED",
        "JUJU_HOOK_FAILED",
        "IMAGE_PULL_FAILURE",
    ],

    # ── Observability chain ────────────────────────────────────────
    "OTEL_COLLECTOR_CONFIG_ERROR": [
        "OTEL_COLLECTOR_CRASH_LOOP",
        "OTEL_METRICS_MISSING",
    ],
    "OTEL_COLLECTOR_CRASH_LOOP": [
        "OTEL_METRICS_MISSING",
    ],
    "OTEL_METRICS_MISSING": [
        "PIPELINE_ERROR_ANNOTATION",
        "PYTEST_ASSERTION_FAILURE",
    ],

    # ── Validation / test chain ────────────────────────────────────
    "TEMPEST_TEST_EXECUTION_ERROR": [
        "PIPELINE_ERROR_ANNOTATION",
        "PIPELINE_COMMAND_FAILED",
    ],
    "VALIDATION_SMOKE_FAILURE": [
        "PIPELINE_ERROR_ANNOTATION",
        "PIPELINE_COMMAND_FAILED",
    ],
    "PYTEST_ASSERTION_FAILURE": [
        "PIPELINE_ERROR_ANNOTATION",
    ],

    # Snap crash-loop → service unavailable
    "SNAP_SERVICE_CRASH_LOOP": [
        "SNAP_SERVICE_FAILED",
        "OTEL_COLLECTOR_CRASH_LOOP",
    ],

    # ── Sunbeam application chain ───────────────────────────────────
    "SUNBEAM_K8S_NODE_NOT_FOUND": [
        "SUNBEAM_CLUSTER_JOIN_FAIL",
        "SUNBEAM_STEP_FAILED",
        "PIPELINE_SUBPROCESS_ERROR",
        "PIPELINE_ERROR_ANNOTATION",
    ],
    "SUNBEAM_TERRAFORM_STATE_LOCK": [
        "SUNBEAM_STEP_FAILED",
        "SUNBEAM_CLUSTER_JOIN_FAIL",
        "PIPELINE_SUBPROCESS_ERROR",
        "PIPELINE_ERROR_ANNOTATION",
    ],
    "SUNBEAM_CLUSTERD_UNAVAILABLE": [
        "SUNBEAM_JUJU_ACCOUNT_NOT_FOUND",
        "SUNBEAM_STEP_FAILED",
    ],
    "SUNBEAM_CLUSTER_JOIN_FAIL": [
        "PIPELINE_SUBPROCESS_ERROR",
        "PIPELINE_ERROR_ANNOTATION",
    ],

    # ── Infrastructure threshold chains ──────────────────────────
    "MEMORY_LOW": [
        "OOM_KILL",
        "K8S_POD_CRASH_LOOP",
    ],
    "DISK_FULL_THRESHOLD": [
        "DISK_FULL",
        "SNAP_INSTALL_FAILED",
        "IMAGE_PULL_FAILURE",
    ],
    "FD_EXHAUSTION": [
        "MICROCEPH_RADOS_ERROR",
        "CEPH_OSD_DOWN",
        "NETWORK_CONNECTION_REFUSED",
    ],

    # ── OpenStack service chains ──────────────────────────────────
    "MYSQL_CONNECTION_REFUSED": [
        "KEYSTONE_AUTH_FAILURE",
        "K8S_POD_CRASH_LOOP",
        "JUJU_APP_BLOCKED",
    ],
    "RABBITMQ_CONNECTION_LOST": [
        "NOVA_SCHEDULER_NO_HOST",
        "NEUTRON_AGENT_DOWN",
        "K8S_POD_CRASH_LOOP",
    ],
    "KEYSTONE_AUTH_FAILURE": [
        "JUJU_APP_BLOCKED",
        "OPENSTACK_HYPERVISOR_ERROR",
    ],
    "NOVA_SCHEDULER_NO_HOST": [
        "VALIDATION_SMOKE_FAILURE",
        "TEMPEST_TEST_EXECUTION_ERROR",
    ],
    "NEUTRON_AGENT_DOWN": [
        "NETWORK_TIMEOUT",
        "NETWORK_CONNECTION_REFUSED",
    ],

    # ── Real-world bug chains ─────────────────────────────────────
    "CHARM_CHANNEL_NOT_FOUND": [
        "JUJU_HOOK_FAILED",
        "SUNBEAM_BOOTSTRAP_FAIL",
    ],
    "TERRAFORM_REGISTRY_UNREACHABLE": [
        "SUNBEAM_TERRAFORM_STATE_LOCK",
        "SUNBEAM_BOOTSTRAP_FAIL",
        "SUNBEAM_STEP_FAILED",
    ],
    "MICROCEPH_OSD_MKFS_FAIL": [
        "CEPH_OSD_DOWN",
        "CEPH_HEALTH_ERR",
    ],
    "SUNBEAM_WEBSOCKET_ERROR": [
        "JUJU_WORKER_UNEXPECTED_ERROR",
        "JUJU_AGENT_LOST",
    ],
    "METALLB_TIMEOUT": [
        "METALLB_WEBHOOK_FAILURE",
        "K8S_CONTROL_PLANE_UNSTABLE",
    ],
    "K8S_INSPECT_FAILURE": [
        "K8S_NODE_NOT_READY",
    ],
}


def _build_reverse_graph() -> dict[str, set[str]]:
    """Return {downstream_id: set_of_upstream_ids}."""
    reverse: dict[str, set[str]] = {}
    for upstream, downstreams in CAUSAL_GRAPH.items():
        for ds in downstreams:
            reverse.setdefault(ds, set()).add(upstream)
    return reverse


_REVERSE_GRAPH = _build_reverse_graph()


def get_downstream_ids(pattern_id: str) -> set[str]:
    """Return the set of pattern IDs that can be caused by *pattern_id*."""
    return set(CAUSAL_GRAPH.get(pattern_id, []))


def get_upstream_ids(pattern_id: str) -> set[str]:
    """Return the set of pattern IDs that can cause *pattern_id*."""
    return _REVERSE_GRAPH.get(pattern_id, set())


def compute_causal_depth(
    pattern_id: str,
    matched_pattern_ids: set[str],
    _visited: set[str] | None = None,
) -> int:
    """Compute how many transitive upstream hops separate *pattern_id*
    from a true root cause (a matched pattern with no matched upstream).

    Returns 0 if the pattern is itself a root (no matched upstream),
    1 if it has one matched upstream ancestor, etc.
    """
    if _visited is None:
        _visited = set()
    if pattern_id in _visited:
        return 0
    _visited.add(pattern_id)

    upstream = get_upstream_ids(pattern_id) & matched_pattern_ids
    if not upstream:
        return 0

    max_depth = 0
    for uid in upstream:
        depth = compute_causal_depth(uid, matched_pattern_ids, _visited)
        max_depth = max(max_depth, depth)
    return max_depth + 1


def compute_causal_adjustments(
    matched_pattern_ids: set[str],
) -> dict[str, float]:
    """Given the set of all matched pattern IDs, compute a score adjustment
    for each one based on causal relationships.

    Returns:
        {pattern_id: adjustment} where positive means "more likely root cause"
        and negative means "more likely symptom".

    Transitive depth: a pattern 2 hops downstream of a matched root cause
    gets a larger penalty than one 1 hop away.
    """
    adjustments: dict[str, float] = {}

    for pid in matched_pattern_ids:
        adj = 0.0
        downstream = get_downstream_ids(pid)
        matched_downstream = downstream & matched_pattern_ids
        if matched_downstream:
            adj += UPSTREAM_BONUS

        upstream = get_upstream_ids(pid)
        matched_upstream = upstream & matched_pattern_ids
        if matched_upstream:
            adj -= DOWNSTREAM_PENALTY

        depth = compute_causal_depth(pid, matched_pattern_ids)
        if depth > 1:
            adj -= TRANSITIVE_DEPTH_PENALTY * (depth - 1)

        if adj != 0.0:
            adjustments[pid] = adj

    return adjustments
