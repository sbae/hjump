from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .templates import config_template

DEFAULT_CONFIG_PATH = Path("~/.config/hjump/config.toml").expanduser()


@dataclass(frozen=True)
class ResourcePreset:
    name: str
    partition: str | None = None
    time: str | None = None
    cpus: int | None = None
    mem: str | None = None
    salloc_extra: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ClusterConfig:
    name: str
    login_host: str
    port: int = 22
    user: str | None = None
    identity_file: str | None = None
    ssh_alias: str | None = None
    default_partition: str | None = None
    default_time: str = "04:00:00"
    default_cpus: int = 1
    default_mem: str = "16G"
    salloc_extra: list[str] = field(default_factory=list)
    remote_init: str | None = None
    remote_project_path: str | None = None
    auto_reuse: bool = True
    job_name_prefix: str = "hjump"
    presets: dict[str, ResourcePreset] = field(default_factory=dict)

    @property
    def effective_ssh_alias(self) -> str:
        return self.ssh_alias or f"{self.name}-current"

    @property
    def effective_user(self) -> str:
        return self.user or os.getlogin()


def _string_list(value: Any, key: str) -> list[str]:
    value = value or []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a list of strings")
    return list(value)


def _parse_presets(raw: Any) -> dict[str, ResourcePreset]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("presets must be a table")

    presets: dict[str, ResourcePreset] = {}
    for name, value in raw.items():
        if not isinstance(value, dict):
            raise ValueError(f"preset {name!r} must be a table")
        presets[str(name)] = ResourcePreset(
            name=str(name),
            partition=str(value["partition"]) if value.get("partition") is not None else None,
            time=str(value["time"]) if value.get("time") is not None else None,
            cpus=int(value["cpus"]) if value.get("cpus") is not None else None,
            mem=str(value["mem"]) if value.get("mem") is not None else None,
            salloc_extra=_string_list(value.get("salloc_extra"), f"presets.{name}.salloc_extra"),
        )
    return presets


def init_config(path: Path = DEFAULT_CONFIG_PATH, cluster_name: str = "my-hpc", overwrite: bool = False) -> Path:
    path = path.expanduser()
    if path.exists() and not overwrite:
        raise FileExistsError(f"Config already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config_template(cluster_name), encoding="utf-8")
    return path


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    path = path.expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("rb") as f:
        return tomllib.load(f)


def load_cluster(name: str, config_path: Path = DEFAULT_CONFIG_PATH) -> ClusterConfig:
    raw = load_config(config_path)
    clusters = raw.get("clusters", {})
    if name not in clusters:
        available = ", ".join(sorted(clusters)) or "none"
        raise KeyError(f"Cluster '{name}' not found in {config_path}. Available: {available}")

    data = dict(clusters[name])
    if not data.get("login_host"):
        raise ValueError(f"Cluster '{name}' missing required key: login_host")

    extra = _string_list(data.get("salloc_extra"), "salloc_extra")
    remote_init = data.get("remote_init")
    if remote_init is not None and not isinstance(remote_init, str):
        raise ValueError("remote_init must be a string")

    return ClusterConfig(
        name=name,
        login_host=str(data["login_host"]),
        port=int(data.get("port", 22)),
        user=data.get("user"),
        identity_file=data.get("identity_file"),
        ssh_alias=data.get("ssh_alias"),
        default_partition=data.get("default_partition"),
        default_time=str(data.get("default_time", "04:00:00")),
        default_cpus=int(data.get("default_cpus", 1)),
        default_mem=str(data.get("default_mem", "16G")),
        salloc_extra=extra,
        remote_init=remote_init,
        remote_project_path=data.get("remote_project_path"),
        auto_reuse=bool(data.get("auto_reuse", True)),
        job_name_prefix=str(data.get("job_name_prefix", "hjump")),
        presets=_parse_presets(data.get("presets")),
    )


def _toml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_cluster_config(cluster: ClusterConfig) -> str:
    lines = [
        f"[clusters.{cluster.name}]",
        f"login_host = {_toml_quote(cluster.login_host)}",
        f"port = {cluster.port}",
    ]
    if cluster.user:
        lines.append(f"user = {_toml_quote(cluster.user)}")
    if cluster.identity_file:
        lines.append(f"identity_file = {_toml_quote(cluster.identity_file)}")
    if cluster.ssh_alias:
        lines.append(f"ssh_alias = {_toml_quote(cluster.ssh_alias)}")
    if cluster.remote_project_path:
        lines.append(f"remote_project_path = {_toml_quote(cluster.remote_project_path)}")
    if cluster.default_partition:
        lines.append(f"default_partition = {_toml_quote(cluster.default_partition)}")
    lines.extend(
        [
            f"default_time = {_toml_quote(cluster.default_time)}",
            f"default_cpus = {cluster.default_cpus}",
            f"default_mem = {_toml_quote(cluster.default_mem)}",
            "salloc_extra = [" + ", ".join(_toml_quote(item) for item in cluster.salloc_extra) + "]",
        ]
    )
    if cluster.remote_init:
        lines.append(f"remote_init = {_toml_quote(cluster.remote_init)}")
    lines.extend(
        [
            f"job_name_prefix = {_toml_quote(cluster.job_name_prefix)}",
            f"auto_reuse = {'true' if cluster.auto_reuse else 'false'}",
        ]
    )

    for name, preset in cluster.presets.items():
        lines.extend(["", f"[clusters.{cluster.name}.presets.{name}]"])
        if preset.partition is not None:
            lines.append(f"partition = {_toml_quote(preset.partition)}")
        if preset.time is not None:
            lines.append(f"time = {_toml_quote(preset.time)}")
        if preset.cpus is not None:
            lines.append(f"cpus = {preset.cpus}")
        if preset.mem is not None:
            lines.append(f"mem = {_toml_quote(preset.mem)}")
        if preset.salloc_extra:
            lines.append(
                "salloc_extra = [" + ", ".join(_toml_quote(item) for item in preset.salloc_extra) + "]"
            )
    return "\n".join(lines) + "\n"


def upsert_cluster_config(
    cluster: ClusterConfig,
    path: Path = DEFAULT_CONFIG_PATH,
    overwrite: bool = False,
) -> Path:
    """Add a cluster to config.toml, or replace its complete table when allowed."""
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    new_block = render_cluster_config(cluster).rstrip()
    if not path.exists():
        path.write_text("# hjump configuration file.\n\n" + new_block + "\n", encoding="utf-8")
        return path

    existing = path.read_text(encoding="utf-8")
    header = f"[clusters.{cluster.name}]"
    lines = existing.splitlines()
    start: int | None = None
    end = len(lines)
    top_level_cluster = re.compile(r"^\[clusters\.([^.\]]+)\]\s*$")
    for index, line in enumerate(lines):
        if line.strip() == header:
            start = index
            continue
        match = top_level_cluster.match(line.strip())
        if start is not None and match and index > start:
            end = index
            break

    if start is not None:
        if not overwrite:
            raise FileExistsError(f"Cluster '{cluster.name}' already exists in {path}")
        before = lines[:start]
        while before and not before[-1].strip():
            before.pop()
        after = lines[end:]
        while after and not after[0].strip():
            after.pop(0)
        pieces = ["\n".join(before).rstrip(), new_block, "\n".join(after).lstrip()]
        updated = "\n\n".join(piece for piece in pieces if piece) + "\n"
    else:
        updated = existing.rstrip() + "\n\n" + new_block + "\n"

    path.write_text(updated, encoding="utf-8")
    return path
