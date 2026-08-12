from __future__ import annotations

import getpass
from functools import wraps
from pathlib import Path
from typing import Callable

import typer
from rich.console import Console
from rich.table import Table

from .config import (
    ClusterConfig,
    DEFAULT_CONFIG_PATH,
    init_config,
    load_cluster,
    load_config,
    upsert_cluster_config,
)
from .diag import (
    CheckResult,
    check_code_cli,
    check_config_file,
    check_dns_endpoints,
    check_executable,
    check_login_endpoint,
    check_login_reachable,
    check_python,
    check_remote_command,
    check_ssh_alias,
    check_ssh_config_writable,
    check_vscode_remote_ssh,
    platform_summary,
)
from .slurm import (
    SlurmJob,
    allocate_job,
    cancel_job,
    discover_partitions,
    find_reusable_job,
    get_last_login_endpoint,
    list_owned_jobs,
    resolve_job,
    resolve_login_endpoints,
    run_login,
    set_ssh_config_path,
    set_ssh_verbose,
    wait_for_node,
)
from .ssh_config import (
    DEFAULT_SSH_CONFIG,
    clear_compute_ssh_config,
    ensure_login_ssh_config,
    get_managed_compute_node,
    login_alias,
    resolve_ssh_alias,
    update_ssh_config,
)
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


def _ok(message: str) -> None:
    console.print(f"[green]✓[/green] {message}")


def _warn(message: str) -> None:
    console.print(f"[yellow]![/yellow] {message}")


def _find_identity_files() -> list[Path]:
    ssh_dir = Path("~/.ssh").expanduser()
    found: list[Path] = []
    for name in ("id_ed25519", "id_rsa", "id_ecdsa"):
        candidate = ssh_dir / name
        if candidate.is_file():
            found.append(candidate)
    return found


def _prompt_auth_method(default: int = 2) -> int:
    console.print("\nSSH authentication:")
    console.print("  [1] SSH key")
    console.print("  [2] Password / MFA / interactive OpenSSH")
    console.print("  [3] Existing SSH configuration")
    while True:
        choice = typer.prompt("Choice", default=default, type=int)
        if choice in {1, 2, 3}:
            return choice
        _warn("Choose 1, 2, or 3.")


def _prompt_key_path(default: Path | None = None) -> str:
    while True:
        if default is not None:
            value = typer.prompt("SSH key", default=default.as_posix())
        else:
            value = typer.prompt("SSH key path")
        path = Path(value).expanduser()
        if path.is_file():
            return path.as_posix()
        _warn(f"Key file not found: {path}")


def _configure_login_ssh(cluster: ClusterConfig, ssh_config: Path, verbose: bool = False) -> Path:
    set_ssh_verbose(verbose)
    path = ssh_config.expanduser()
    repaired = ensure_login_ssh_config(cluster, path)
    set_ssh_config_path(path)
    if repaired and verbose:
        console.print(f"[dim]Refreshed managed SSH block in {path}[/dim]")
    return path


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
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _print_pending_progress(job: SlurmJob, elapsed: float) -> None:
    console.print(
        f"Job {job.job_id} pending: [bold]{job.reason or 'unknown'}[/bold] "
        f"(elapsed {_format_elapsed(elapsed)})"
    )


def _resolve_resources(
    cluster: ClusterConfig,
    preset_name: str | None,
    partition: str | None,
    time_limit: str | None,
    cpus: int | None,
    mem: str | None,
) -> tuple[str | None, str, int, str, list[str]]:
    preset = None
    if preset_name:
        preset = cluster.presets.get(preset_name)
        if preset is None:
            available = ", ".join(sorted(cluster.presets)) or "none"
            raise ValueError(f"Unknown preset '{preset_name}'. Available presets: {available}")

    part = partition if partition is not None else (preset.partition if preset and preset.partition is not None else cluster.default_partition)
    tlim = time_limit if time_limit is not None else (preset.time if preset and preset.time is not None else cluster.default_time)
    ncpus = cpus if cpus is not None else (preset.cpus if preset and preset.cpus is not None else cluster.default_cpus)
    memory = mem if mem is not None else (preset.mem if preset and preset.mem is not None else cluster.default_mem)
    extra = list(cluster.salloc_extra)
    if preset:
        extra.extend(preset.salloc_extra)
    return part, tlim, ncpus, memory, extra


def _verify_setup(cluster: ClusterConfig, ssh_config: Path) -> None:
    checks = [
        check_ssh_alias(cluster, ssh_config),
        check_login_reachable(cluster, timeout=15),
        check_remote_command(cluster, "squeue", timeout=15),
        check_code_cli(),
        check_vscode_remote_ssh(),
    ]
    for result in checks:
        (_ok if result.ok else _warn)(f"{result.name}: {result.detail}")


@app.command()
@clean_errors
def init(
    cluster_name: str = typer.Argument("my-hpc", help="Cluster profile name to create."),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config", help="Path to config.toml."),
    ssh_config: Path = typer.Option(DEFAULT_SSH_CONFIG, "--ssh-config", help="Path to SSH config."),
    force: bool = typer.Option(False, "--force", help="Replace an existing profile with the same name."),
    template: bool = typer.Option(False, "--template", help="Write the editable template instead of running setup."),
    verify: bool = typer.Option(True, "--verify/--no-verify", help="Verify SSH, Slurm, and VS Code after setup."),
) -> None:
    """Interactively configure a cluster profile."""
    if template:
        path = init_config(config, cluster_name=cluster_name, overwrite=force)
        console.print(f"Created template: [bold]{path}[/bold]")
        try:
            open_in_vscode(path)
        except FileNotFoundError:
            pass
        return

    config = config.expanduser()
    ssh_config = ssh_config.expanduser()
    if config.exists():
        clusters = load_config(config).get("clusters", {})
        if cluster_name in clusters and not force:
            if not typer.confirm(f"Cluster '{cluster_name}' already exists. Replace it?", default=False):
                raise typer.Exit(0)
            force = True

    console.print(f"Configure [bold]{cluster_name}[/bold]")

    identity_files = _find_identity_files()
    identity_file: str | None = None
    existing_login_alias: str | None = None
    existing_settings: dict[str, str] | None = None

    if identity_files:
        detected_key = identity_files[0]
        console.print(f"\nFound SSH private key: [bold]{detected_key.as_posix()}[/bold]")
        if typer.confirm("Use this key?", default=True):
            auth_method = 1
            identity_file = detected_key.as_posix()
        else:
            auth_method = _prompt_auth_method(default=2)
    else:
        console.print("\nNo standard SSH private key found in ~/.ssh.")
        auth_method = _prompt_auth_method(default=2)

    if auth_method == 1:
        if identity_file is None:
            identity_file = _prompt_key_path(identity_files[0] if identity_files else None)
    elif auth_method == 2:
        console.print("[dim]hjump will not ask for or store your password. OpenSSH will prompt for password/MFA when needed.[/dim]")
    elif auth_method == 3:
        existing_login_alias = typer.prompt("Existing SSH host/alias")
        existing_settings = resolve_ssh_alias(existing_login_alias, ssh_config)
        resolved_host = existing_settings.get("hostname")
        if not resolved_host:
            raise RuntimeError(f"SSH alias {existing_login_alias!r} has no resolved HostName.")
        resolved_user = existing_settings.get("user", getpass.getuser())
        resolved_port = existing_settings.get("port", "22")
        _ok(f"Existing SSH alias: {existing_login_alias} → {resolved_user}@{resolved_host}:{resolved_port}")

    if existing_settings is not None:
        login_host = existing_settings["hostname"]
        port = int(existing_settings.get("port", "22"))
        user = existing_settings.get("user") or getpass.getuser()
    else:
        login_host = typer.prompt("Login host")
        port = typer.prompt("SSH port", default=22, type=int)
        user = typer.prompt("Username", default=getpass.getuser())

    default_compute_alias = cluster_name
    if existing_login_alias and existing_login_alias == default_compute_alias:
        default_compute_alias = f"{cluster_name}-compute"
    ssh_alias = typer.prompt("Local compute SSH alias", default=default_compute_alias)
    if existing_login_alias and ssh_alias == existing_login_alias:
        raise ValueError("Compute SSH alias must differ from the existing login SSH alias.")

    provisional = ClusterConfig(
        name=cluster_name,
        login_host=login_host,
        port=port,
        user=user,
        identity_file=identity_file,
        ssh_alias=ssh_alias,
        login_ssh_alias=existing_login_alias,
        default_partition=None,
    )
    _configure_login_ssh(provisional, ssh_config)

    console.print("\nTesting SSH login...")
    login_proc = run_login(provisional, "true", check=False)
    login_ok = login_proc.returncode == 0
    if login_ok:
        _ok(f"SSH login via {login_alias(provisional)}")
    else:
        detail = login_proc.stderr.strip().splitlines()[-1] if login_proc.stderr.strip() else "connection failed"
        _warn(f"SSH login test failed: {detail}")
        _warn("Continuing setup; run 'hjump diag' after saving to troubleshoot.")

    partitions: list[str] = []
    detected_default: str | None = None
    if login_ok:
        partitions, detected_default = discover_partitions(provisional)
        if partitions:
            labels = [f"{name}*" if name == detected_default else name for name in partitions]
            console.print(f"Detected Slurm partitions: [bold]{', '.join(labels)}[/bold]")

    if detected_default:
        partition = typer.prompt("Default partition", default=detected_default)
    else:
        partition = typer.prompt(
            "Default partition (blank = cluster default)",
            default="",
            show_default=False,
        )
    if partitions and partition and partition not in partitions:
        _warn(f"Partition {partition!r} was not returned by sinfo; saving it anyway.")

    time_limit = typer.prompt("Default time", default="04:00:00")
    cpus = typer.prompt("Default CPUs", default=1, type=int)
    mem = typer.prompt("Default memory", default="16G")

    cluster = ClusterConfig(
        name=cluster_name,
        login_host=login_host,
        port=port,
        user=user,
        identity_file=identity_file,
        ssh_alias=ssh_alias,
        login_ssh_alias=existing_login_alias,
        default_partition=partition or None,
        default_time=time_limit,
        default_cpus=cpus,
        default_mem=mem,
    )
    path = upsert_cluster_config(cluster, config, overwrite=force)
    _ok(f"Configuration saved: {path}")
    _configure_login_ssh(cluster, ssh_config)
    if cluster.login_ssh_alias:
        _ok(f"Using existing SSH login alias: {cluster.login_ssh_alias}")
    else:
        _ok(f"SSH login alias ready: {login_alias(cluster)}")

    if verify:
        console.print("\nVerifying setup...")
        _verify_setup(cluster, ssh_config)

    console.print(f"\nReady. Run: [bold]hjump go {cluster_name}[/bold]")


@app.command("config")
@clean_errors
def config_command(
    cluster_name: str | None = typer.Argument(None, help="Optional cluster profile name for context."),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config", help="Path to config.toml."),
) -> None:
    """Open the hjump configuration file in VS Code."""
    config = config.expanduser()
    if not config.exists():
        raise FileNotFoundError(f"Config not found: {config}. Run 'hjump init <cluster-name>'.")
    if cluster_name:
        load_cluster(cluster_name, config)
        console.print(f"Opening configuration for [bold]{cluster_name}[/bold]...")
    open_in_vscode(config)


def _go_impl(
    cluster_name: str,
    config: Path,
    preset: str | None,
    partition: str | None,
    time_limit: str | None,
    cpus: int | None,
    mem: str | None,
    existing_job: str | None,
    no_reuse: bool,
    no_launch: bool,
    ssh_config: Path,
    directory: str | None,
    wait_timeout: int,
    keep_failed_job: bool,
    verbose: bool,
) -> SlurmJob:
    cluster = load_cluster(cluster_name, config)
    ssh_config = _configure_login_ssh(cluster, ssh_config, verbose=verbose)
    part, tlim, ncpus, memory, extra = _resolve_resources(cluster, preset, partition, time_limit, cpus, mem)
    remote_path = directory if directory is not None else cluster.remote_project_path

    console.print(f"Connecting to [bold]{cluster.name}[/bold]...")
    run_login(cluster, "true")
    endpoint = get_last_login_endpoint()
    _ok(f"Login node{f' ({endpoint})' if verbose and endpoint else ''}")

    if existing_job:
        console.print(f"Attaching to Slurm job {existing_job}...")
        job = resolve_job(cluster, existing_job)
        if not job.node:
            job = wait_for_node(
                cluster,
                existing_job,
                timeout_seconds=wait_timeout,
                on_progress=_print_pending_progress,
                cancel_fatal=False,
            )
        _ok(f"Job {job.job_id} on {job.node or 'pending node'}")
    else:
        job = None
        if cluster.auto_reuse and not no_reuse:
            job = find_reusable_job(cluster, partition=part)
            if job:
                _ok(f"Reusing job {job.job_id} on {job.node}")
            else:
                console.print("No reusable job found.")

        if job is None:
            preset_text = f" · preset {preset}" if preset else ""
            console.print(
                f"Requesting [bold]{part or 'cluster default'}[/bold] · {ncpus} CPU{'s' if ncpus != 1 else ''} "
                f"· {memory} · {tlim}{preset_text}"
            )
            job_id = allocate_job(
                cluster=cluster,
                partition=part,
                time_limit=tlim,
                cpus=ncpus,
                mem=memory,
                extra=extra,
                timeout_seconds=wait_timeout,
            )
            console.print(f"Submitted job {job_id}; waiting for a compute node...")
            job = wait_for_node(
                cluster,
                job_id,
                timeout_seconds=wait_timeout,
                on_progress=_print_pending_progress,
                cancel_fatal=not keep_failed_job,
            )
            _ok(f"Job {job.job_id} allocated on {job.node}")

    if not job.node:
        raise RuntimeError("No compute node available for selected job.")

    old_node = get_managed_compute_node(cluster, ssh_config)
    update_ssh_config(cluster, job.node, ssh_config)
    if old_node and old_node != job.node:
        _ok(f"SSH updated: {cluster.effective_ssh_alias} {old_node} → {job.node}")
    else:
        _ok(f"SSH ready: {cluster.effective_ssh_alias} → {job.node}")

    if not no_launch:
        console.print("Opening VS Code...")
        launch_vscode(cluster.effective_ssh_alias, resolve_remote_directory(cluster, remote_path))
    return job


@app.command("go")
@clean_errors
def go(
    cluster_name: str = typer.Argument(..., help="Cluster profile name from config.toml."),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    preset: str | None = typer.Option(None, "--preset", help="Named resource preset."),
    partition: str | None = typer.Option(None, "--partition"),
    time_limit: str | None = typer.Option(None, "--time"),
    cpus: int | None = typer.Option(None, "--cpus"),
    mem: str | None = typer.Option(None, "--mem"),
    existing_job: str | None = typer.Option(None, "--existing-job"),
    no_reuse: bool = typer.Option(False, "--no-reuse"),
    no_launch: bool = typer.Option(False, "--no-launch"),
    ssh_config: Path = typer.Option(DEFAULT_SSH_CONFIG, "--ssh-config"),
    directory: str | None = typer.Option(None, "--dir"),
    wait_timeout: int = typer.Option(3600, "--wait-timeout"),
    keep_failed_job: bool = typer.Option(False, "--keep-failed-job"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Open a VS Code session on a Slurm compute node."""
    try:
        _go_impl(
            cluster_name, config, preset, partition, time_limit, cpus, mem, existing_job,
            no_reuse, no_launch, ssh_config, directory, wait_timeout, keep_failed_job, verbose,
        )
    except Exception as exc:
        raise RuntimeError(f"{exc}\nTry: hjump diag {cluster_name}") from exc


@app.command()
@clean_errors
def status(
    cluster_name: str | None = typer.Argument(None, help="Cluster profile. Omit to show all profiles."),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    ssh_config: Path = typer.Option(DEFAULT_SSH_CONFIG, "--ssh-config"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Show hjump login, SSH alias, and active Slurm session status."""
    if cluster_name is None:
        names = sorted(load_config(config).get("clusters", {}))
        if not names:
            console.print("No cluster profiles configured.")
            return
    else:
        names = [cluster_name]

    for index, name in enumerate(names):
        if index:
            console.print()
        cluster = load_cluster(name, config)
        ssh_path = _configure_login_ssh(cluster, ssh_config, verbose=verbose)
        run_login(cluster, "true")
        jobs = list_owned_jobs(cluster)
        managed_node = get_managed_compute_node(cluster, ssh_path)

        info = Table(title=f"hjump status · {name}", show_header=False)
        info.add_column("Field", style="bold")
        info.add_column("Value")
        info.add_row("Login", f"{login_alias(cluster)} → {cluster.login_host}")
        info.add_row("Endpoint", get_last_login_endpoint() or "unknown")
        info.add_row("SSH alias", f"{cluster.effective_ssh_alias} → {managed_node or '(not assigned)'}")
        info.add_row("Jobs", str(len(jobs)))
        console.print(info)

        if jobs:
            table = Table(show_header=True)
            table.add_column("Job")
            table.add_column("State")
            table.add_column("Node")
            table.add_column("Partition")
            table.add_column("Resources")
            table.add_column("Remaining")
            for job in jobs:
                resources = f"{job.cpus or '?'} CPU · {job.memory or '?'}"
                table.add_row(
                    job.job_id,
                    job.state,
                    job.node or "—",
                    job.partition or "—",
                    resources,
                    job.time_left or "—",
                )
            console.print(table)


@app.command()
@clean_errors
def stop(
    cluster_name: str = typer.Argument(...),
    job_id: str | None = typer.Option(None, "--job-id", help="Stop one hjump-owned job instead of all."),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    ssh_config: Path = typer.Option(DEFAULT_SSH_CONFIG, "--ssh-config"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Stop active hjump sessions and clear a stale compute alias."""
    cluster = load_cluster(cluster_name, config)
    ssh_path = _configure_login_ssh(cluster, ssh_config, verbose=verbose)
    jobs = list_owned_jobs(cluster)
    if job_id:
        jobs = [job for job in jobs if job.job_id == job_id]
        if not jobs:
            raise RuntimeError(f"Job {job_id} is not an active hjump-owned job on {cluster_name}.")

    if not jobs:
        console.print("No active hjump jobs.")
        clear_compute_ssh_config(cluster, ssh_path)
        return

    for job in jobs:
        cancel_job(cluster, job.job_id)
        _ok(f"Stopped job {job.job_id}")

    remaining = list_owned_jobs(cluster)
    if not remaining:
        clear_compute_ssh_config(cluster, ssh_path)
        _ok(f"Cleared compute alias {cluster.effective_ssh_alias}")


@app.command()
@clean_errors
def restart(
    cluster_name: str = typer.Argument(...),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    preset: str | None = typer.Option(None, "--preset"),
    partition: str | None = typer.Option(None, "--partition"),
    time_limit: str | None = typer.Option(None, "--time"),
    cpus: int | None = typer.Option(None, "--cpus"),
    mem: str | None = typer.Option(None, "--mem"),
    no_launch: bool = typer.Option(False, "--no-launch"),
    ssh_config: Path = typer.Option(DEFAULT_SSH_CONFIG, "--ssh-config"),
    directory: str | None = typer.Option(None, "--dir"),
    wait_timeout: int = typer.Option(3600, "--wait-timeout"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Stop hjump-owned sessions and start a fresh allocation."""
    cluster = load_cluster(cluster_name, config)
    ssh_path = _configure_login_ssh(cluster, ssh_config, verbose=verbose)
    jobs = list_owned_jobs(cluster)
    for job in jobs:
        cancel_job(cluster, job.job_id)
        _ok(f"Stopped job {job.job_id}")
    clear_compute_ssh_config(cluster, ssh_path)
    _go_impl(
        cluster_name, config, preset, partition, time_limit, cpus, mem, None,
        True, no_launch, ssh_config, directory, wait_timeout, False, verbose,
    )


@app.command("ssh-config")
@clean_errors
def ssh_config_command(
    cluster_name: str,
    node: str = typer.Option(..., "--node"),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    ssh_config: Path = typer.Option(DEFAULT_SSH_CONFIG, "--ssh-config"),
) -> None:
    """Point the managed compute alias at a specific node."""
    cluster = load_cluster(cluster_name, config)
    update_ssh_config(cluster, node, ssh_config)
    _ok(f"{cluster.effective_ssh_alias} → {node}")


@app.command()
@clean_errors
def attach(
    cluster_name: str,
    job_id: str = typer.Argument(...),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    no_launch: bool = typer.Option(False, "--no-launch"),
    ssh_config: Path = typer.Option(DEFAULT_SSH_CONFIG, "--ssh-config"),
    directory: str | None = typer.Option(None, "--dir"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Attach VS Code to an already-running Slurm job by id."""
    cluster = load_cluster(cluster_name, config)
    ssh_path = _configure_login_ssh(cluster, ssh_config, verbose=verbose)
    job = resolve_job(cluster, job_id)
    if not job.node:
        job = wait_for_node(cluster, job_id, on_progress=_print_pending_progress, cancel_fatal=False)
    if not job.node:
        raise RuntimeError("No compute node available for selected job.")
    update_ssh_config(cluster, job.node, ssh_path)
    _ok(f"{cluster.effective_ssh_alias} → {job.node}")
    if not no_launch:
        remote_path = directory or cluster.remote_project_path
        launch_vscode(cluster.effective_ssh_alias, resolve_remote_directory(cluster, remote_path))


@app.command()
@clean_errors
def cancel(
    cluster_name: str,
    job_id: str = typer.Option(..., "--job-id"),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    ssh_config: Path = typer.Option(DEFAULT_SSH_CONFIG, "--ssh-config"),
) -> None:
    """Cancel any Slurm job by explicit id (legacy/power-user command)."""
    cluster = load_cluster(cluster_name, config)
    _configure_login_ssh(cluster, ssh_config)
    cancel_job(cluster, job_id)
    _ok(f"Cancelled Slurm job {job_id}")


@app.command("diag")
@clean_errors
def diag(
    cluster_name: str | None = typer.Argument(None, help="Optional cluster profile to test."),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    ssh_config: Path = typer.Option(DEFAULT_SSH_CONFIG, "--ssh-config"),
    remote: bool = typer.Option(True, "--remote/--no-remote"),
    remote_timeout: int = typer.Option(15, "--remote-timeout", min=1),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Diagnose local tools, SSH routing, endpoints, and Slurm."""
    set_ssh_verbose(verbose)
    console.print(f"Platform: {platform_summary()}")
    results: list[CheckResult] = []

    def run_check(label: str, check: Callable[[], CheckResult]) -> None:
        if verbose:
            console.print(f"[dim]Checking {label}...[/dim]")
        result = check()
        results.append(result)
        if verbose:
            status_text = "[green]OK[/green]" if result.ok else "[red]FAIL[/red]"
            console.print(f"  {status_text} {result.name}: {result.detail}")

    run_check("Python", check_python)
    run_check("OpenSSH", lambda: check_executable("ssh", ["ssh", "-V"]))
    run_check("VS Code", check_code_cli)
    run_check("config file", lambda: check_config_file(config))
    run_check("SSH config permissions", lambda: check_ssh_config_writable(ssh_config))
    run_check("VS Code Remote-SSH", check_vscode_remote_ssh)

    cluster = None
    if cluster_name is not None:
        try:
            cluster = load_cluster(cluster_name, config)
            results.append(CheckResult("cluster profile", True, cluster_name))
        except Exception as exc:
            results.append(CheckResult("cluster profile", False, str(exc)))

    if cluster is not None and remote:
        try:
            _configure_login_ssh(cluster, ssh_config, verbose=verbose)
            routing_detail = (
                f"existing alias {cluster.login_ssh_alias}"
                if cluster.login_ssh_alias
                else str(ssh_config.expanduser())
            )
            results.append(CheckResult("SSH routing", True, routing_detail))
        except Exception as exc:
            results.append(CheckResult("SSH routing", False, str(exc)))
            cluster = None

    if cluster is not None and remote:
        run_check("login alias", lambda: check_ssh_alias(cluster, ssh_config))
        run_check("login DNS", lambda: check_dns_endpoints(cluster))
        if cluster.login_ssh_alias:
            results.append(
                CheckResult(
                    "endpoint selection",
                    True,
                    f"delegated to OpenSSH alias {cluster.login_ssh_alias}",
                )
            )
        else:
            endpoints = resolve_login_endpoints(cluster)
            for endpoint in endpoints:
                run_check(
                    f"endpoint {endpoint}",
                    lambda endpoint=endpoint: check_login_endpoint(cluster, endpoint, timeout=remote_timeout),
                )
        run_check("SSH login", lambda: check_login_reachable(cluster, timeout=remote_timeout))
        for command in ("squeue", "salloc", "scontrol", "scancel"):
            run_check(
                f"remote {command}",
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
