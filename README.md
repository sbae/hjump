# hjump (aka hpc-jump)

> You use a Slurm HPC cluster for work. You can't live without VS Code. Your admin hates you because VS Code
> quietly spawns a server, file watchers, and a language server, all on the login node, and just won't stop.
>
> Is this your story?

Small CLI helper for opening VS Code Remote-SSH on a Slurm compute node instead of on an HPC login node.

The intended workflow is:

1. Use SSH only to run lightweight Slurm commands on the login node.
2. Allocate or discover an interactive Slurm job.
3. Resolve the assigned compute node.
4. Write a managed host entry in `~/.ssh/config` using `ProxyJump` through the login node.
5. Launch VS Code against the compute node alias.

This keeps the VS Code server, file watchers, terminals, and language servers off the login node.

## Commands

| Command | Description |
|---|---|
| `hjump init <cluster>` | Create a config template and open it in VS Code |
| `hjump config` | Re-open the config file in VS Code |
| `hjump go <cluster>` | Allocate a Slurm job (or reuse one) and launch VS Code on the compute node |
| `hjump attach <cluster> <job-id>` | Attach VS Code to an already-running Slurm job |
| `hjump diag [cluster]` | Check local tools; optionally verify the cluster connection end-to-end |
| `hjump cancel <cluster> --job-id <id>` | Cancel a Slurm job |
| `hjump ssh-config <cluster> --node <node>` | Write an SSH config entry for a specific compute node |

## Requirements

- Python 3.11+
- OpenSSH client available as `ssh` (most computers already have it)
- VS Code command-line launcher available as `code`
- VS Code Remote-SSH extension: `ms-vscode-remote.remote-ssh`
- A working SSH config or hostname for the HPC login node

## Install

### Windows

Use native Windows PowerShell, not WSL, for the initial version.

Modern Windows includes OpenSSH. Verify:

```powershell
ssh -V
```

Install pipx:

```powershell
py -m pip install --user pipx
py -m pipx ensurepath
```

Restart PowerShell, then install:

```powershell
pipx install git+https://github.com/sbae/hjump.git
```

Verify:

```powershell
hjump --help
code --version
```

If `code` is missing, reinstall VS Code with the option to add it to PATH, or enable the VS Code command-line launcher.

### macOS (experimental)

Using Homebrew and pipx:

```bash
brew install pipx
pipx ensurepath
pipx install git+https://github.com/sbae/hjump.git
```

Verify:

```bash
ssh -V
code --version
hjump --help
```

If `code` is missing, open VS Code and run `Shell Command: Install 'code' command in PATH` from the command palette.

### Linux (experimental)

Ubuntu/Debian:

```bash
sudo apt install pipx
pipx ensurepath
pipx install git+https://github.com/sbae/hjump.git
```

Generic Python install:

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
pipx install git+https://github.com/sbae/hjump.git
```

Verify:

```bash
ssh -V
code --version
hjump --help
```

### Upgrade

```bash
pipx upgrade hjump
```

## Configure

```bash
hjump init my-hpc
```

This creates a starter config (at `~/.config/hjump/config.toml`) and opens it in VS Code automatically. Fill in at least:

- `login_host` — hostname or alias for your HPC login node
- `user` — your HPC username
- `remote_project_path` — path to your project on the cluster

To open the config again later:

```bash
hjump config
```

To overwrite an existing config:

```bash
hjump init my-hpc --force
```

## Check your setup

Local-only checks:

```bash
hjump diag
```

Check a configured cluster, including login-node Slurm commands:

```bash
hjump diag my-hpc
```

Show each check as it runs and limit every remote check to 10 seconds:

```bash
hjump diag my-hpc --verbose --remote-timeout 10
```

Skip remote checks:

```bash
hjump diag my-hpc --no-remote
```

The `diag` command checks Python, `ssh`, `code`, configuration, VS Code Remote-SSH, login-node reachability, and remote Slurm commands.

## Use

```bash
hjump go my-hpc
```

Show OpenSSH authentication and connection diagnostics:

```bash
hjump go my-hpc --verbose
```

Useful variants:

```bash
hjump go my-hpc --time 04:00:00 --cpus 1 --mem 16G
hjump go my-hpc --dir '~/project3'
hjump go my-hpc --existing-job 12345678
hjump attach my-hpc 12345678
hjump ssh-config my-hpc --node compute123
hjump cancel my-hpc --job-id 12345678
```

## Notes

`auto_reuse` is experimental. It reuses a matching RUNNING Slurm allocation, not shell state. Use tmux if you want to return to the same shell environment.

This tool deliberately uses local OpenSSH via `subprocess` rather than a Python SSH library. That preserves your normal SSH behavior, including keys, MFA, Kerberos/GSSAPI, host-key checking, `ProxyJump`, and `ControlMaster`.

WSL is not a first-class target yet. Native Windows PowerShell is the intended Windows path for now.

### Windows SSH config permissions

`hjump` restricts the generated SSH config ACL to the current user, SYSTEM,
and the local Administrators group, as required by Windows OpenSSH. If an older
generated config reports `Bad owner or permissions`, remove inherited and
`OWNER RIGHTS` access with `icacls`, then grant the current user full control.
