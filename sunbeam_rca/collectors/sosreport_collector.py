"""Extract and inventory sosreport archives."""

from __future__ import annotations

import logging
import tarfile
import tempfile
from pathlib import Path

from sunbeam_rca.models import SosReportManifest

logger = logging.getLogger(__name__)


def _find_optional(root: Path, *candidates: str) -> str | None:
    for rel in candidates:
        p = root / rel
        if p.is_file():
            return str(p)
    return None


def _glob_files(root: Path, pattern: str) -> list[str]:
    return sorted(str(p) for p in root.glob(pattern) if p.is_file())


def _read_hostname(root: Path) -> str:
    hostname_file = root / "hostname"
    if hostname_file.is_symlink() or hostname_file.is_file():
        try:
            target = hostname_file.resolve()
            if target.is_file():
                return target.read_text().strip()
        except OSError:
            pass
    sos_env = root / "environment"
    if sos_env.is_file():
        return root.name.split("-")[1] if "-" in root.name else ""
    return ""


def collect_sosreport(path: str) -> SosReportManifest:
    """Build a manifest from a sosreport directory or ``.tar.xz`` archive.

    If *path* points to a tarball, it is extracted first (skipping device
    nodes that require root).
    """
    src = Path(path)

    if src.is_file() and src.name.endswith((".tar.xz", ".tar.gz", ".tar")):
        extract_dir = Path(tempfile.mkdtemp(prefix="sunbeam_sos_"))
        logger.info("Extracting sosreport to %s", extract_dir)
        with tarfile.open(src, "r:*") as tf:
            for member in tf:
                if member.isdev():
                    continue
                try:
                    tf.extract(member, extract_dir, set_attrs=False)
                except (OSError, tarfile.TarError) as exc:
                    logger.debug("Skipping %s: %s", member.name, exc)
        subdirs = [d for d in extract_dir.iterdir() if d.is_dir()]
        root = subdirs[0] if len(subdirs) == 1 else extract_dir
    elif src.is_dir():
        root = src
    else:
        raise FileNotFoundError(f"sosreport path not found: {path}")

    hostname = _read_hostname(root)
    logger.info("sosreport root: %s  hostname: %s", root, hostname)

    ovn_logs = _glob_files(
        root, "var/snap/openstack-hypervisor/common/log/ovn/*.log"
    )

    k8s_cluster_info_dir = root / "sos_commands" / "kubernetes" / "cluster-info"
    k8s_cluster_info_logs: list[str] = []
    if k8s_cluster_info_dir.is_dir():
        k8s_cluster_info_logs = sorted(
            str(p)
            for p in k8s_cluster_info_dir.rglob("*")
            if p.is_file() and p.stat().st_size > 0
        )

    sunbeam_app_logs = _glob_files(
        root, "home/ubuntu/snap/openstack/common/logs/sunbeam-*.log"
    )

    juju_models_file = _find_optional(
        root,
        "sos_commands/sunbeam/juju_models_-c_sunbeam-controller_--format_json",
    )

    manifest = SosReportManifest(
        root_dir=str(root),
        hostname=hostname,
        syslog=_find_optional(root, "var/log/syslog"),
        kern_log=_find_optional(root, "var/log/kern.log"),
        dmesg=_find_optional(root, "sos_commands/kernel/dmesg", "sos_commands/kernel/dmesg_-T"),
        cloud_init_log=_find_optional(root, "var/log/cloud-init.log"),
        cloud_init_output_log=_find_optional(root, "var/log/cloud-init-output.log"),
        juju_logs=_glob_files(root, "var/log/juju/*.log"),
        pod_log_dirs=sorted(
            str(d) for d in (root / "var/log/pods").iterdir() if d.is_dir()
        )
        if (root / "var/log/pods").is_dir()
        else [],
        sunbeam_commands=_glob_files(root, "sos_commands/sunbeam/*"),
        kubernetes_commands=_glob_files(root, "sos_commands/kubernetes/**/*"),
        juju_status_files=[
            str(p)
            for p in root.glob("sos_commands/sunbeam/juju_status_*")
            if p.is_file()
        ],
        environment_file=_find_optional(root, "environment"),
        uname=_find_optional(root, "sos_commands/kernel/uname_-a"),
        meminfo=_find_optional(root, "proc/meminfo"),
        df_output=_find_optional(
            root,
            "sos_commands/filesys/df_-al_-x_autofs",
            "df",
        ),
        ovn_logs=ovn_logs,
        k8s_cluster_info_logs=k8s_cluster_info_logs,
        sunbeam_app_logs=sunbeam_app_logs,
        juju_models_file=juju_models_file,
    )

    logger.info(
        "Manifest: %d juju logs, %d OVN logs, %d k8s cluster-info logs, "
        "%d juju status files, %d sunbeam app logs, juju_models=%s",
        len(manifest.juju_logs),
        len(manifest.ovn_logs),
        len(manifest.k8s_cluster_info_logs),
        len(manifest.juju_status_files),
        len(manifest.sunbeam_app_logs),
        "yes" if manifest.juju_models_file else "no",
    )
    return manifest
