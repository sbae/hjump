from __future__ import annotations

import re
import shlex
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .config import ClusterConfig

DEFAULT_SSH_TIMEOUT_SECONDS = 60
DEFAULT_ALLOCATION_TIMEOUT_SECONDS = 3600
DEFAULT_SSH_ATTEMPTS = 3
SSH_RETRY_DELAYS_SECONDS = (1.0, 3.0)
_SSH_VERBOSE = False
_SSH_CONFIG_PATH: Path | None = None
_OUTPUT_START = "__HPC_JUMP_OUTPUT_START__"
_OUTPUT_END = "__HPC_JUMP_OUTPUT_END__"
_TRANSIENT_SSH_MARKERS = (
    "banner exchange",
    "kex_exchange_identification",
    "connection reset",
    "connection refused",
    "connection closed by remote host",
    "connection timed out",
    "connection aborted",
)

FATAL_PENDING_REASONS = {
    "PartitionTimeLimit",
    "PartitionNodeLimit",
    "QOSMaxWallDurationPerJobLimit",
    "InvalidAccount",
    "InvalidQOS",
    "BadConstraints",
}
LIMIT_PENDING_REASONS = {
    "QOSMaxJobsPerUserLimit",
    "QOSGrpJobsLimit",
    "AssocMaxJobsLimit",
    "AssocGrpJobsLimit",
    "QOSMaxSubmitJobPerUserLimit",
}


@dataclass(frozen=True)
class SlurmJob:
    job_id: str
    state: str
    node: str | None = None
    name: str | None = None
    reason: str | None = None


class PendingJobError(RuntimeError):
    def __init__(self, job: SlurmJob, message: str, cancelled: bool = False) -> None:
        super().__init__(message)
        self.job = job
        self.cancelled = cancelled


def _ssh_target(cluster: ClusterConfig) -> str:
    return f"{cluster.effective_ssh_alias}-login"


def _ssh_args(cluster: ClusterConfig, endpoint: str | None = None) -> list[str]:
    args = ["ssh"]
    if _SSH_CONFIG_PATH is not None:
        args.extend(["-F", str(_SSH_CONFIG_PATH)])
    if _SSH_VERBOSE:
        args.append("-v")
    if endpoint:
        args.extend(
            [
                "-o",
                f"HostName={endpoint}",
                "-o",
                f"HostKeyAlias={cluster.login_host}",
            ]
        )
    return args


def set_ssh_verbose(enabled: bool) -> None:
    global _SSH_VERBOSE
    _SSH_VERBOSE = enabled


def set_ssh_config_path(path: Path | None) -> None:
    global _SSH_CONFIG_PATH
    _SSH_CONFIG_PATH = path.expanduser() if path is not None else None


def _resolve_login_endpoints(cluster: ClusterConfig) -> list[str]:
    try:
        records = socket.getaddrinfo(
            cluster.login_host,
            cluster.port,
            type=socket.SOCK_STREAM,
        )
    except OSError:
        return []

    endpoints: list[str] = []
    for _family, _socktype, _proto, _canonname, sockaddr in records:
        endpoint = str(sockaddr[0])
        if endpoint not in endpoints:
            endpoints.append(endpoint)
    return endpoints


def _is_transient_ssh_failure(proc: subprocess.CompletedProcess[str]) -> bool:
    text = proc.stderr.lower()
    return proc.returncode == 255 and any(marker in text for marker in _TRANSIENT_SSH_MARKERS)


def _brief_ssh_failure(proc: subprocess.CompletedProcess[str]) -> str:
    lines = [line.strip() for line in proc.stderr.splitlines() if line.strip()]
    for line in reversed(lines):
        if any(marker in line.lower() for marker in _TRANSIENT_SSH_MARKERS):
            return line
    return lines[-1] if lines else "SSH connection failed"


def run_login(
    cluster: ClusterConfig,
    command: str,
    check: bool = True,
    timeout: int = DEFAULT_SSH_TIMEOUT_SECONDS,
    attempts: int = DEFAULT_SSH_ATTEMPTS,
) -> subprocess.CompletedProcess[str]:
    init = f"{cluster.remote_init} || exit $?; " if cluster.remote_init else ""
    framed = (
        f"{init}printf '{_OUTPUT_START}\n'; {command}; status=$?; "
        f"printf '\n{_OUTPUT_END}\n'; exit $status"
    )
    remote_command = f"bash -lc {shlex.quote(framed)}"

    endpoints = _resolve_login_endpoints(cluster)
    total_attempts = max(1, attempts, len(endpoints))
    candidates: list[str | None]
    if endpoints:
        candidates = [endpoints[index % len(endpoints)] for index in range(total_attempts)]
    else:
        candidates = [None] * total_attempts

    proc: subprocess.CompletedProcess[str] | None = None
    for index, endpoint in enumerate(candidates):
        attempt = index + 1
        proc = subprocess.run(
            [
                *_ssh_args(cluster, endpoint),
                "-o",
                f"ConnectTimeout={min(timeout, DEFAULT_SSH_TIMEOUT_SECONDS)}",
                _ssh_target(cluster),
                remote_command,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )

        if _SSH_VERBOSE and proc.stderr:
            print(proc.stderr, file=sys.stderr, end="" if proc.stderr.endswith("\n") else "\n")

        if not _is_transient_ssh_failure(proc) or attempt == total_attempts:
            if attempt > 1 and proc.returncode == 0:
                endpoint_text = f" via {endpoint}" if endpoint else ""
                print(
                    f"SSH connection succeeded on attempt {attempt}/{total_attempts}{endpoint_text}.",
                    file=sys.stderr,
                )
            break

        next_endpoint = candidates[index + 1]
        delay = SSH_RETRY_DELAYS_SECONDS[min(index, len(SSH_RETRY_DELAYS_SECONDS) - 1)]
        endpoint_text = endpoint or cluster.login_host
        print(
            f"SSH connection to login endpoint {endpoint_text} failed before authentication "
            f"(attempt {attempt}/{total_attempts}): {_brief_ssh_failure(proc)}",
            file=sys.stderr,
        )
        if next_endpoint and next_endpoint != endpoint:
            print(
                f"Trying next login endpoint {next_endpoint} in {delay:g} second(s)...",
                file=sys.stderr,
            )
        else:
            print(f"Retrying SSH connection in {delay:g} second(s)...", file=sys.stderr)
        time.sleep(delay)

    if proc is None:
        raise RuntimeError("SSH command was not attempted.")

    if _OUTPUT_START in proc.stdout and _OUTPUT_END in proc.stdout:
        stdout = proc.stdout.rsplit(_OUTPUT_START, 1)[1].split(_OUTPUT_END, 1)[0]
        stdout = stdout.removeprefix("\r\n").removeprefix("\n").removesuffix("\r\n").removesuffix("\n")
        proc = subprocess.CompletedProcess(proc.args, proc.returncode, stdout, proc.stderr)

    if check and proc.returncode != 0:
        detail = "" if _SSH_VERBOSE else (proc.stderr.strip() or proc.stdout.strip())
        message = f"Remote SSH command failed with exit code {proc.returncode}."
        if detail:
            message = f"{message}\n{detail}"
        raise RuntimeError(message)
    return proc


def _first_host_from_nodelist(cluster: ClusterConfig, nodelist: str) -> str | None:
    if not nodelist or nodelist in {"(null)", "None", "N/A"}:
        return None
    out = run_login(cluster, f"scontrol show hostnames {shlex.quote(nodelist)} | head -n 1").stdout.strip()
    return out or None


def resolve_job(cluster: ClusterConfig, job_id: str) -> SlurmJob:
    fmt = "%i|%T|%N|%j|%r"
    out = run_login(cluster, f"squeue -j {shlex.quote(job_id)} -h -o {shlex.quote(fmt)}").stdout.strip()
    if not out:
        raise RuntimeError(f"No active Slurm job found with id {job_id}")
    line = out.splitlines()[0]
    parts = line.split("|", 4)
    if len(parts) != 5:
        raise RuntimeError(f"Could not parse squeue output: {line}")
    job, state, nodelist, name, reason = parts
    return SlurmJob(
        job_id=job,
        state=state,
        node=_first_host_from_nodelist(cluster, nodelist),
        name=name,
        reason=None if reason in {"", "None", "(null)", "N/A"} else reason,
    )


def find_reusable_job(cluster: ClusterConfig, partition: str | None = None) -> SlurmJob | None:
    fmt = "%i|%T|%P|%N|%j"
    cmd = f"squeue -u $USER -h -t RUNNING -o {shlex.quote(fmt)}"
    proc = run_login(cluster, cmd, check=False)
    if proc.returncode != 0:
        detail = "" if _SSH_VERBOSE else (proc.stderr.strip() or proc.stdout.strip())
        message = (
            "Could not check for reusable Slurm jobs because the remote SSH command failed. "
            f"Exit code={proc.returncode}."
        )
        if detail:
            message = f"{message}\n{detail}"
        raise RuntimeError(message)
    if not proc.stdout.strip():
        return None

    for line in proc.stdout.splitlines():
        parts = line.split("|", 4)
        if len(parts) != 5:
            continue
        job_id, state, job_partition, nodelist, name = parts
        if name != cluster.job_name_prefix or (partition and job_partition != partition):
            continue
        node = _first_host_from_nodelist(cluster, nodelist)
        if node:
            return SlurmJob(job_id=job_id, state=state, node=node, name=name)
    return None


def allocate_job(
    cluster: ClusterConfig,
    partition: str | None,
    time_limit: str,
    cpus: int,
    mem: str,
    extra: Sequence[str] | None = None,
    timeout_seconds: int = DEFAULT_ALLOCATION_TIMEOUT_SECONDS,
) -> str:
    args = [
        "salloc",
        "--no-shell",
        f"--job-name={cluster.job_name_prefix}",
        f"--time={time_limit}",
        f"--cpus-per-task={cpus}",
        f"--mem={mem}",
    ]
    if partition:
        args.append(f"--partition={partition}")
    args.extend(extra or [])

    proc = run_login(
        cluster,
        " ".join(shlex.quote(x) for x in args),
        check=False,
        timeout=timeout_seconds,
    )
    combined = "\n".join([proc.stdout, proc.stderr])
    match = re.search(r"\b(?:Granted|Pending) job allocation (\d+)\b", combined)
    if match:
        return match.group(1)

    if proc.returncode == 255:
        message = (
            "SSH connection to the HPC login node failed before Slurm could allocate a job. "
            f"Target={_ssh_target(cluster)}."
        )
        if not _SSH_VERBOSE:
            message = f"{message}\nSSH stderr: {proc.stderr.strip() or '(empty)'}"
        raise RuntimeError(message)
    raise RuntimeError(
        "Slurm allocation failed or its job id could not be parsed. "
        f"Remote exit code={proc.returncode}. stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )


def wait_for_node(
    cluster: ClusterConfig,
    job_id: str,
    poll_seconds: float = 3.0,
    timeout_seconds: int = 3600,
    on_progress: Callable[[SlurmJob, float], None] | None = None,
    cancel_fatal: bool = True,
) -> SlurmJob:
    started = time.monotonic()
    deadline = started + timeout_seconds
    last: SlurmJob | None = None
    last_reason: str | None = None
    last_report = started

    while time.monotonic() < deadline:
        job = resolve_job(cluster, job_id)
        last = job
        elapsed = time.monotonic() - started
        if job.state == "RUNNING" and job.node:
            return job
        if job.state in {"FAILED", "CANCELLED", "TIMEOUT", "COMPLETED"}:
            raise RuntimeError(f"Job {job_id} ended before a node was available: {job.state}")

        reason = job.reason
        if reason in FATAL_PENDING_REASONS or reason in LIMIT_PENDING_REASONS:
            cancelled = False
            if cancel_fatal:
                cancel_job(cluster, job_id)
                cancelled = True
            category = (
                "request is invalid for this partition/QOS"
                if reason in FATAL_PENDING_REASONS
                else "account or QOS limit has been reached"
            )
            action = "The pending job was cancelled." if cancelled else "The pending job was left in the queue."
            raise PendingJobError(
                job,
                f"Slurm job {job_id} cannot start: {reason}. The {category}. {action}",
                cancelled=cancelled,
            )

        now = time.monotonic()
        if on_progress and (reason != last_reason or now - last_report >= 30):
            on_progress(job, elapsed)
            last_reason = reason
            last_report = now
        time.sleep(poll_seconds)

    elapsed = time.monotonic() - started
    reason = last.reason if last else None
    state = last.state if last else "unknown"
    raise TimeoutError(
        f"Timed out after {elapsed:.0f}s waiting for Slurm job {job_id}. "
        f"Current state={state}; pending reason={reason or 'unknown'}. The job was left pending."
    )


def cancel_job(cluster: ClusterConfig, job_id: str) -> None:
    run_login(cluster, f"scancel {shlex.quote(job_id)}")
