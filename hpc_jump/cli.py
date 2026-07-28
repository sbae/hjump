from __future__ import annotations

from functools import wraps
from pathlib import Path
from typing import Callable

import typer
from rich.console import Console
from rich.table import Table

from .config import ClusterConfig, DEFAULT_CONFIG_PATH, init_config, load_cluster
from .diag import (
    CheckResult,
    check_code_cli,
    check_config_file,
    check_executable,
    check_login_reachable,
    check_python,
    check_remote_command,
    check_ssh_config_writable,
    check_vscode_remote_ssh,
    platform_summary,
)
from .slurm import SlurmJob, allocate_job, cancel_job, find_reusable_job, resolve_job, run_login, set_ssh_verbose, wait_for_node
from .ssh_config import DEFAULT_SSH_CONFIG, update_ssh_config
from .vscode import launch_vscode, open_in_vscode

app = typer.Typer(no_args_is_help=True)
console = Console()


def clean_errors(func: Callable[..., object]) -> Callable[..., object]:
    @wraps(func)
    def wrapper(*args: object, **kwargs: object) -> object:
        try:
            return func(*args, **kwargs)
        except typer.Exit:
            raise
        except (KeyboardInterrupt, EOFError):
            console.print("[red]Cancelled.[/red]")
            raise typer.Exit(130) from None
        except Exception as exc:
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(1) from None

    return wrapper


def resolve_remote_directory(cluster: ClusterConfig, directory: str | None) -> str | None:
    if not directory or (directory != "~" and not directory.startswith("~/")):
        return directory
    home = run_login(cluster, "printf '%s' \"$HOME\"").stdout.strip()
    if not home:
        raise RuntimeError("Could not determine the remote home directory.")
    return home if directory == "~" else home.rstrip("/") + directory[1:]


def _format_elapsed(seconds: float) -> str:
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _print_pending_progress(job: SlurmJob, elapsed: float) -> None:
    console.print(
        f"Job {job.job_id} pending: [bold]{job.reason or 'unknown'}[/bold] "
        f"(elapsed {_format_elapsed(elapsed)})"
    )


@app.command()
def init(
    cluster_name: str = typer.Argument("my-hpc", help="Cluster profile name to create."),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config", help="Path to write config.toml."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing config file."),
) -> None:
    """Create a config template for a cluster profile and open it in VS Code."""
    try:
        path = init_config(config, cluster_name=cluster_name, overwrite=force)
    except FileExistsError as exc:
        console.print(f"[red]{exc}[/red]")
        console.print("Use --force to overwrite it.")
        raise typer.Exit(1) from exc
    console.print(f"Created config template: [bold]{path}[/bold]")
    try:
        open_in_vscode(path)
        console.print("Opening config in VS Code — fill in [bold]login_host[/bold] and [bold]user[/bold], then run [bold]hjump go[/bold].")
    except FileNotFoundError:
        console.print(f"Open this file to configure: [bold]{path}[/bold]")
        console.print("Fill in [bold]login_host[/bold] and [bold]user[/bold], then run [bold]hjump go[/bold].")


@app.command("config")
@clean_errors
def config_command(
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config", help="Path to config.toml."),
) -> None:
    """Open the hjump config file in VS Code."""
    if not config.exists():
        console.print(f"[red]Config not found:[/red] {config}")
        console.print("Run [bold]hjump init <cluster-name>[/bold] to create one.")
        raise typer.Exit(1)
    open_in_vscode(config)


@app.command("go")
@clean_errors
def go(
    cluster_name: str = typer.Argument(..., help="Cluster profile name from config.toml."),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config", help="Path to config.toml."),
    partition: str | None = typer.Option(None, "--partition", help="Slurm partition."),
    time_limit: str | None = typer.Option(None, "--time", help="Slurm time limit."),
    cpus: int | None = typer.Option(None, "--cpus", help="CPUs per task."),
    mem: str | None = typer.Option(None, "--mem", help="Memory request."),
    existing_job: str | None = typer.Option(None, "--existing-job", help="Attach to an active Slurm job id."),
    no_reuse: bool = typer.Option(False, "--no-reuse", help="Do not auto-reuse an existing running job."),
    no_launch: bool = typer.Option(False, "--no-launch", help="Update SSH config but do not launch VS Code."),
    ssh_config: Path = typer.Option(DEFAULT_SSH_CONFIG, "--ssh-config", help="Path to SSH config."),
    directory: str | None = typer.Option(None, "--dir", help="Remote directory to open in VS Code."),
    wait_timeout: int = typer.Option(3600, "--wait-timeout", help="Seconds to wait for a new allocation."),
    keep_failed_job: bool = typer.Option(False, "--keep-failed-job", help="Do not cancel a newly submitted job with a fatal pending reason."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print OpenSSH diagnostic output."),
) -> None:
    """Allocate a Slurm job (or reuse one) and open VS Code on the compute node."""
    set_ssh_verbose(verbose)
    cluster = load_cluster(cluster_name, config)
    part = partition if partition is not None else cluster.default_partition
    tlim = time_limit if time_limit is not None else cluster.default_time
    ncpus = cpus if cpus is not None else cluster.default_cpus
    memory = mem if mem is not None else cluster.default_mem
    remote_path = directory if directory is not None else cluster.remote_project_path

    if existing_job:
        console.print(f"Attaching to existing Slurm job {existing_job}...")
        job = resolve_job(cluster, existing_job)
        if not job.node:
            job = wait_for_node(
                cluster,
                existing_job,
                timeout_seconds=wait_timeout,
                on_progress=_print_pending_progress,
                cancel_fatal=False,
            )
    else:
        job = None
        if cluster.auto_reuse and not no_reuse:
            console.print("Looking for reusable running Slurm job...")
            job = find_reusable_job(cluster, partition=part)

        if job is None:
            console.print("Requesting new Slurm allocation...")
            console.print(f"  Partition: {part or '(cluster default)'}")
            console.print(f"  Time: {tlim}")
            console.print(f"  CPUs: {ncpus}")
            console.print(f"  Memory: {memory}")
            console.print(f"  Job name: {cluster.job_name_prefix}")
            if cluster.salloc_extra:
                console.print(f"  Extra flags: {' '.join(cluster.salloc_extra)}")

            job_id = allocate_job(
                cluster=cluster,
                partition=part,
                time_limit=tlim,
                cpus=ncpus,
                mem=memory,
                extra=cluster.salloc_extra,
                timeout_seconds=wait_timeout,
            )
            console.print(f"Submitted Slurm job {job_id}; waiting for compute node...")
            job = wait_for_node(
                cluster,
                job_id,
                timeout_seconds=wait_timeout,
                on_progress=_print_pending_progress,
                cancel_fatal=not keep_failed_job,
            )

    if not job.node:
        raise RuntimeError("No compute node available for selected job.")

    update_ssh_config(cluster, job.node, ssh_config)
    console.print(f"SSH alias [bold]{cluster.effective_ssh_alias}[/bold] now points to {job.node}")
    console.print(f"Slurm job: {job.job_id} ({job.state})")

    if not no_launch:
        console.print("Launching VS Code Remote-SSH...")
        launch_vscode(cluster.effective_ssh_alias, resolve_remote_directory(cluster, remote_path))


@app.command("ssh-config")
def ssh_config_command(
    cluster_name: str,
    node: str = typer.Option(..., "--node", help="Compute node hostname."),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    ssh_config: Path = typer.Option(DEFAULT_SSH_CONFIG, "--ssh-config"),
) -> None:
    """Write an SSH config entry pointing the cluster alias at a specific compute node."""
    cluster = load_cluster(cluster_name, config)
    update_ssh_config(cluster, node, ssh_config)
    console.print(f"Updated {ssh_config}: {cluster.effective_ssh_alias} -> {node}")


@app.command()
@clean_errors
def attach(
    cluster_name: str,
    job_id: str = typer.Argument(..., help="Active Slurm job id."),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    no_launch: bool = typer.Option(False, "--no-launch"),
    ssh_config: Path = typer.Option(DEFAULT_SSH_CONFIG, "--ssh-config"),
    directory: str | None = typer.Option(None, "--dir"),
) -> None:
    """Attach VS Code to an already-running Slurm job by job id."""
    cluster = load_cluster(cluster_name, config)
    job = resolve_job(cluster, job_id)
    if not job.node:
        job = wait_for_node(cluster, job_id, on_progress=_print_pending_progress, cancel_fatal=False)
    if not job.node:
        raise RuntimeError("No compute node available for selected job.")
    update_ssh_config(cluster, job.node, ssh_config)
    console.print(f"Attached {cluster.effective_ssh_alias} to {job.node}")
    if not no_launch:
        remote_path = directory or cluster.remote_project_path
        launch_vscode(cluster.effective_ssh_alias, resolve_remote_directory(cluster, remote_path))


@app.command()
@clean_errors
def cancel(
    cluster_name: str,
    job_id: str = typer.Option(..., "--job-id", help="Slurm job id to cancel."),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
) -> None:
    """Cancel a Slurm job by id."""
    cluster = load_cluster(cluster_name, config)
    cancel_job(cluster, job_id)
    console.print(f"Cancelled Slurm job {job_id}")


@app.command("diag")
def diag(
    cluster_name: str | None = typer.Argument(None, help="Optional cluster profile to test."),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config", help="Path to config.toml."),
    ssh_config: Path = typer.Option(DEFAULT_SSH_CONFIG, "--ssh-config", help="Path to SSH config."),
    remote: bool = typer.Option(True, "--remote/--no-remote", help="Run login-node Slurm checks when a cluster is provided."),
    remote_timeout: int = typer.Option(15, "--remote-timeout", min=1, help="Timeout in seconds for each remote check."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print each check as it runs."),
) -> None:
    """Check local tools and optionally verify a cluster connection end-to-end."""
    console.print(f"Platform: {platform_summary()}")

    results = []

    def run_check(label: str, check: Callable[[], CheckResult]) -> None:
        if verbose:
            console.print(f"[dim]Checking {label}...[/dim]")
        result = check()
        results.append(result)
        if verbose:
            status = "[green]OK[/green]" if result.ok else "[red]FAIL[/red]"
            console.print(f"  {status} {result.name}: {result.detail}")

    run_check("Python", check_python)
    run_check("OpenSSH client", lambda: check_executable("ssh", ["ssh", "-V"]))
    run_check("VS Code CLI", check_code_cli)
    run_check("config file", lambda: check_config_file(config))
    run_check("SSH config permissions", lambda: check_ssh_config_writable(ssh_config))
    run_check("VS Code Remote-SSH extension", check_vscode_remote_ssh)

    cluster = None
    if cluster_name is not None:
        try:
            cluster = load_cluster(cluster_name, config)
            results.append(type(results[0])("cluster profile", True, cluster_name))
        except Exception as exc:
            results.append(type(results[0])("cluster profile", False, str(exc)))

    if cluster is not None and remote:
        target = f"{cluster.user + '@' if cluster.user else ''}{cluster.login_host}:{cluster.port}"
        run_check(
            f"SSH login to {target}",
            lambda: check_login_reachable(cluster, timeout=remote_timeout),
        )
        for command in ["squeue", "salloc", "scontrol", "scancel"]:
            run_check(
                f"remote command: {command}",
                lambda command=command: check_remote_command(cluster, command, timeout=remote_timeout),
            )

    table = Table(title="hjump diag")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    for item in results:
        table.add_row(item.name, "OK" if item.ok else "FAIL", item.detail)
    console.print(table)

    if not all(item.ok for item in results):
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
