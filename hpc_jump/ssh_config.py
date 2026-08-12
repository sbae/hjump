from __future__ import annotations

import getpass
import os
import subprocess
from pathlib import Path

from .config import ClusterConfig

DEFAULT_SSH_CONFIG = Path("~/.ssh/config").expanduser()


def _markers(cluster_name: str) -> tuple[str, str]:
    start = f"# >>> hjump managed: {cluster_name}"
    end = f"# <<< hjump managed: {cluster_name}"
    return start, end


def _identity_file(cluster: ClusterConfig) -> str | None:
    if not cluster.identity_file:
        return None
    return str(Path(cluster.identity_file).expanduser())


def login_alias(cluster: ClusterConfig) -> str:
    return cluster.effective_login_alias


def resolve_ssh_alias(alias: str, path: Path = DEFAULT_SSH_CONFIG) -> dict[str, str]:
    """Return effective OpenSSH settings for an existing host alias."""
    path = path.expanduser()
    proc = subprocess.run(
        ["ssh", "-F", str(path), "-G", alias],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "invalid SSH configuration"
        raise RuntimeError(f"Could not resolve SSH alias {alias!r}: {detail}")

    settings: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        key, separator, value = line.partition(" ")
        if separator and value.strip():
            settings.setdefault(key.casefold(), value.strip())
    return settings


def _secure_config_permissions(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o600)
        return

    domain = os.environ.get("USERDOMAIN")
    username = os.environ.get("USERNAME") or getpass.getuser()
    principal = f"{domain}\\{username}" if domain else username
    commands = [
        ["icacls", str(path), "/grant:r", f"{principal}:(F)"],
        ["icacls", str(path), "/grant:r", "*S-1-5-18:(F)"],
        ["icacls", str(path), "/grant:r", "*S-1-5-32-544:(F)"],
        ["icacls", str(path), "/inheritance:r"],
        ["icacls", str(path), "/remove", "*S-1-3-4"],
    ]
    for command in commands:
        proc = subprocess.run(command, text=True, capture_output=True, check=False)
        if proc.returncode != 0:
            detail = proc.stderr.strip() or proc.stdout.strip()
            raise RuntimeError(f"Could not secure SSH config permissions: {detail}")


def _render_login_host(cluster: ClusterConfig) -> list[str]:
    if cluster.login_ssh_alias:
        return []

    lines = [
        f"Host {login_alias(cluster)}",
        f"    HostName {cluster.login_host}",
        f"    Port {cluster.port}",
    ]
    if cluster.user:
        lines.append(f"    User {cluster.user}")
    identity_file = _identity_file(cluster)
    if identity_file:
        lines.append(f"    IdentityFile {identity_file}")
    return lines


def render_login_block(cluster: ClusterConfig) -> str:
    if cluster.login_ssh_alias:
        return ""
    start, end = _markers(cluster.name)
    return "\n".join([start, *_render_login_host(cluster), end, ""])


def render_host_block(cluster: ClusterConfig, compute_node: str) -> str:
    start, end = _markers(cluster.name)
    identity_file = _identity_file(cluster)
    lines = [start]
    login_lines = _render_login_host(cluster)
    if login_lines:
        lines.extend([*login_lines, ""])
    lines.extend([f"Host {cluster.effective_ssh_alias}", f"    HostName {compute_node}"])
    if cluster.user:
        lines.append(f"    User {cluster.user}")
    if identity_file:
        lines.append(f"    IdentityFile {identity_file}")
    lines.extend(
        [
            f"    ProxyJump {login_alias(cluster)}",
            "    ServerAliveInterval 30",
            "    ServerAliveCountMax 3",
            end,
            "",
        ]
    )
    return "\n".join(lines)


def _replace_managed_block(existing: str, cluster: ClusterConfig, block: str) -> str:
    start, end = _markers(cluster.name)
    has_start = start in existing
    has_end = end in existing
    if has_start != has_end:
        raise RuntimeError(
            f"SSH config contains a partial hjump managed block for {cluster.name!r}. "
            "Please repair or remove the managed block markers before retrying."
        )

    clean_block = block.strip()
    if has_start:
        before, rest = existing.split(start, 1)
        _, after = rest.split(end, 1)
        pieces = [before.rstrip(), clean_block, after.lstrip()]
        updated = "\n\n".join(piece for piece in pieces if piece)
        return updated + ("\n" if updated else "")

    if not clean_block:
        return existing
    return existing.rstrip() + "\n\n" + clean_block + "\n"


def _extract_compute_node(managed: str, alias: str) -> str | None:
    """Return HostName from the last exact managed compute-host stanza."""
    lines = managed.splitlines()
    expected_host = f"host {alias}".casefold()
    host_indexes = [index for index, line in enumerate(lines) if line.strip().casefold() == expected_host]

    for host_index in reversed(host_indexes):
        for line in lines[host_index + 1 :]:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.casefold().startswith("host "):
                break
            key, separator, value = stripped.partition(" ")
            if separator and key.casefold() == "hostname" and value.strip():
                return value.strip()
    return None


def get_managed_compute_node(cluster: ClusterConfig, path: Path = DEFAULT_SSH_CONFIG) -> str | None:
    path = path.expanduser()
    if not path.exists():
        return None
    existing = path.read_text(encoding="utf-8")
    start, end = _markers(cluster.name)
    if start not in existing or end not in existing:
        return None
    managed = existing.split(start, 1)[1].split(end, 1)[0]
    return _extract_compute_node(managed, cluster.effective_ssh_alias)


def _write_config(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _secure_config_permissions(path)


def ensure_login_ssh_config(cluster: ClusterConfig, path: Path = DEFAULT_SSH_CONFIG) -> bool:
    """Refresh hjump's managed SSH block without modifying an external login alias."""
    path = path.expanduser()
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    start, end = _markers(cluster.name)

    compute_node = None
    if start in existing and end in existing:
        managed = existing.split(start, 1)[1].split(end, 1)[0]
        compute_node = _extract_compute_node(managed, cluster.effective_ssh_alias)

    if compute_node:
        block = render_host_block(cluster, compute_node)
    else:
        block = render_login_block(cluster)
    updated = _replace_managed_block(existing, cluster, block)
    changed = updated != existing
    if changed or (not path.exists() and updated):
        _write_config(path, updated)
    return changed


def update_ssh_config(
    cluster: ClusterConfig,
    compute_node: str,
    path: Path = DEFAULT_SSH_CONFIG,
) -> bool:
    path = path.expanduser()
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    block = render_host_block(cluster, compute_node)
    updated = _replace_managed_block(existing, cluster, block)
    changed = updated != existing
    if changed or not path.exists():
        _write_config(path, updated)
    return changed


def clear_compute_ssh_config(cluster: ClusterConfig, path: Path = DEFAULT_SSH_CONFIG) -> bool:
    """Remove a stale compute-node target while preserving managed login settings."""
    path = path.expanduser()
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    block = render_login_block(cluster)
    updated = _replace_managed_block(existing, cluster, block)
    changed = updated != existing
    if changed:
        _write_config(path, updated)
    return changed
