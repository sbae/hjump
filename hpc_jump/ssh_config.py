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
    return f"{cluster.effective_ssh_alias}-login"


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
    start, end = _markers(cluster.name)
    return "\n".join([start, *_render_login_host(cluster), end, ""])


def render_host_block(cluster: ClusterConfig, compute_node: str) -> str:
    start, end = _markers(cluster.name)
    identity_file = _identity_file(cluster)
    lines = [start, *_render_login_host(cluster), "", f"Host {cluster.effective_ssh_alias}", f"    HostName {compute_node}"]
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

    if has_start:
        before, rest = existing.split(start, 1)
        _, after = rest.split(end, 1)
        return before.rstrip() + "\n\n" + block + after.lstrip()
    return existing.rstrip() + "\n\n" + block


def _write_config(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _secure_config_permissions(path)


def ensure_login_ssh_config(cluster: ClusterConfig, path: Path = DEFAULT_SSH_CONFIG) -> None:
    """Create or refresh the managed login alias without discarding a compute alias."""
    path = path.expanduser()
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    start, end = _markers(cluster.name)

    compute_lines: list[str] = []
    if start in existing and end in existing:
        managed = existing.split(start, 1)[1].split(end, 1)[0]
        compute_marker = f"Host {cluster.effective_ssh_alias}"
        if compute_marker in managed:
            tail = managed.split(compute_marker, 1)[1].strip("\r\n")
            compute_lines = ["", compute_marker, *tail.splitlines()]

    block = "\n".join([start, *_render_login_host(cluster), *compute_lines, end, ""])
    _write_config(path, _replace_managed_block(existing, cluster, block))


def update_ssh_config(
    cluster: ClusterConfig,
    compute_node: str,
    path: Path = DEFAULT_SSH_CONFIG,
) -> None:
    path = path.expanduser()
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    block = render_host_block(cluster, compute_node)
    _write_config(path, _replace_managed_block(existing, cluster, block))
