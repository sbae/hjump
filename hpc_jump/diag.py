from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import ClusterConfig
from .slurm import resolve_login_endpoints, run_login
from .ssh_config import login_alias
from .vscode import find_code_executable


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def _run_local(args: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else ""


def check_python() -> CheckResult:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    return CheckResult("Python >= 3.11", sys.version_info >= (3, 11), version)


def check_executable(name: str, command: list[str] | None = None) -> CheckResult:
    exe = shutil.which(name)
    if not exe:
        return CheckResult(name, False, "not found on PATH")
    if command is None:
        return CheckResult(name, True, exe)
    try:
        proc = _run_local(command)
        detail = _first_line(proc.stdout) or _first_line(proc.stderr) or exe
        return CheckResult(name, proc.returncode == 0, detail)
    except Exception as exc:
        return CheckResult(name, False, str(exc))


def check_code_cli() -> CheckResult:
    code = find_code_executable()
    if not code:
        return CheckResult("code", False, "not found on PATH or common VS Code install paths")
    try:
        proc = _run_local([code, "--version"])
        detail = _first_line(proc.stdout) or _first_line(proc.stderr) or code
        return CheckResult("code", proc.returncode == 0, detail)
    except Exception as exc:
        return CheckResult("code", False, str(exc))


def check_config_file(path: Path) -> CheckResult:
    if not path.expanduser().exists():
        return CheckResult("config file", False, f"not found: {path}")
    return CheckResult("config file", True, str(path.expanduser()))


def check_ssh_config_writable(path: Path) -> CheckResult:
    path = path.expanduser()
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.exists():
            writable = os.access(path, os.W_OK)
            return CheckResult("SSH config writable", writable, str(path))
        writable = os.access(path.parent, os.W_OK)
        return CheckResult("SSH config writable", writable, f"parent: {path.parent}")
    except Exception as exc:
        return CheckResult("SSH config writable", False, str(exc))


def check_ssh_alias(cluster: ClusterConfig, ssh_config: Path) -> CheckResult:
    alias = login_alias(cluster)
    try:
        proc = _run_local(["ssh", "-F", str(ssh_config.expanduser()), "-G", alias], timeout=10)
        if proc.returncode != 0:
            detail = _first_line(proc.stderr) or "invalid SSH configuration"
            return CheckResult(f"SSH alias {alias}", False, detail)
        settings: dict[str, str] = {}
        for line in proc.stdout.splitlines():
            key, separator, value = line.partition(" ")
            if separator:
                settings[key.casefold()] = value.strip()
        hostname = settings.get("hostname", "unknown")
        user = settings.get("user", "unknown")
        port = settings.get("port", "unknown")
        ok = hostname.casefold() == cluster.login_host.casefold()
        return CheckResult(f"SSH alias {alias}", ok, f"{user}@{hostname}:{port}")
    except Exception as exc:
        return CheckResult(f"SSH alias {alias}", False, str(exc))


def check_dns_endpoints(cluster: ClusterConfig) -> CheckResult:
    endpoints = resolve_login_endpoints(cluster)
    return CheckResult(
        "login DNS endpoints",
        bool(endpoints),
        ", ".join(endpoints) if endpoints else f"no addresses resolved for {cluster.login_host}",
    )


def check_vscode_remote_ssh() -> CheckResult:
    code = find_code_executable()
    if not code:
        return CheckResult("VS Code Remote-SSH extension", False, "code CLI not found")
    try:
        proc = _run_local([code, "--list-extensions"], timeout=20)
        exts = {line.strip().lower() for line in proc.stdout.splitlines()}
        ok = "ms-vscode-remote.remote-ssh" in exts
        return CheckResult(
            "VS Code Remote-SSH extension",
            ok,
            "installed" if ok else "missing: ms-vscode-remote.remote-ssh",
        )
    except Exception as exc:
        return CheckResult("VS Code Remote-SSH extension", False, str(exc))


def check_login_endpoint(cluster: ClusterConfig, endpoint: str, timeout: int = 15) -> CheckResult:
    try:
        proc = run_login(cluster, "echo ok", check=False, timeout=timeout, attempts=1, endpoint=endpoint)
        ok = proc.returncode == 0 and proc.stdout.strip() == "ok"
        detail = "reachable" if ok else (_first_line(proc.stderr) or _first_line(proc.stdout) or "failed")
        return CheckResult(f"login endpoint {endpoint}", ok, detail)
    except Exception as exc:
        return CheckResult(f"login endpoint {endpoint}", False, str(exc))


def check_login_reachable(cluster: ClusterConfig, timeout: int = 15) -> CheckResult:
    try:
        proc = run_login(cluster, "echo ok", check=False, timeout=timeout)
        ok = proc.returncode == 0 and proc.stdout.strip() == "ok"
        detail = "reachable" if ok else (_first_line(proc.stderr) or _first_line(proc.stdout) or "failed")
        return CheckResult("login node SSH", ok, detail)
    except Exception as exc:
        return CheckResult("login node SSH", False, str(exc))


def check_remote_command(cluster: ClusterConfig, command: str, timeout: int = 15) -> CheckResult:
    try:
        proc = run_login(cluster, f"command -v {command}", check=False, timeout=timeout)
        detail = proc.stdout.strip() or proc.stderr.strip() or "not found"
        return CheckResult(f"remote {command}", proc.returncode == 0, detail)
    except Exception as exc:
        return CheckResult(f"remote {command}", False, str(exc))


def platform_summary() -> str:
    system = platform.system() or "unknown"
    release = platform.release()
    machine = platform.machine()
    return f"{system} {release} ({machine})"
