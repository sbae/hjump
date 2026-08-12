# hjump

Small CLI helper for opening VS Code Remote-SSH on a Slurm compute node instead of an HPC login node.

`hjump` uses the login node only for lightweight SSH/Slurm control commands. It allocates or discovers a compute job, maintains SSH aliases with `ProxyJump`, and opens VS Code on the assigned compute node.

## The basic workflow

```bash
hjump init uv
hjump go uv
```

After setup, `hjump go <cluster>` is intended to be the normal user interface.

Typical output:

```text
Connecting to uv...
✓ Login node
✓ Reusing job 25867015 on cn-0025
✓ SSH ready: uv → cn-0025
Opening VS Code...
```

If a new allocation is needed:

```text
Connecting to uv...
✓ Login node
No reusable job found.
Requesting cpu_short · 1 CPU · 16G · 04:00:00
Submitted job 25867102; waiting for a compute node...
✓ Job 25867102 allocated on cn-0041
✓ SSH ready: uv → cn-0041
Opening VS Code...
```

## Commands

| Command | Description |
|---|---|
| `hjump init <cluster>` | Interactive first-run setup and verification |
| `hjump go <cluster>` | Reuse or allocate a Slurm session and open VS Code |
| `hjump status [cluster]` | Show login endpoint, compute alias, jobs, resources, and time remaining |
| `hjump stop <cluster>` | Stop hjump-owned jobs and clear the compute alias |
| `hjump restart <cluster>` | Stop hjump-owned jobs and request a fresh session |
| `hjump config [cluster]` | Open `config.toml` in VS Code |
| `hjump diag [cluster]` | Diagnose local tools, SSH aliases, DNS endpoints, and Slurm |
| `hjump attach <cluster> <job-id>` | Attach the compute alias to an existing Slurm job |
| `hjump cancel <cluster> --job-id <id>` | Power-user command to cancel an explicit Slurm job |
| `hjump ssh-config <cluster> --node <node>` | Point the managed compute alias at a node manually |

## Requirements

- Python 3.11+
- OpenSSH client available as `ssh`
- VS Code command-line launcher available as `code`
- VS Code Remote-SSH extension (`ms-vscode-remote.remote-ssh`)
- Slurm commands available on the HPC login node

Native Windows PowerShell is the primary Windows target. macOS and Linux are supported experimentally.

## Install

With pipx:

```bash
pipx install git+https://github.com/sbae/hjump.git
```

Upgrade a GitHub installation with:

```bash
pipx install --force git+https://github.com/sbae/hjump.git
```

Verify:

```bash
hjump --help
ssh -V
code --version
```

## Interactive setup

```bash
hjump init uv
```

The wizard asks for:

```text
Login host
SSH port
Username
SSH key
Local SSH alias
Default partition
Default time
Default CPUs
Default memory
```

It then writes `~/.config/hjump/config.toml`, creates the managed login alias, and checks SSH, Slurm, VS Code, and Remote-SSH.

For the old editable-template workflow:

```bash
hjump init uv --template
```

## Managed SSH aliases

For a profile named `uv` with `ssh_alias = "uv"`, hjump maintains:

```sshconfig
Host uv-login
    HostName bigpurple.example.edu
    Port 22
    User username
    IdentityFile ~/.ssh/id_ed25519

Host uv
    HostName cn-0025
    User username
    IdentityFile ~/.ssh/id_ed25519
    ProxyJump uv-login
    ServerAliveInterval 30
    ServerAliveCountMax 3
```

All hjump login-node control commands use the generated `uv-login` alias. The compute alias (`uv`) is what VS Code uses.

## Login endpoint failover

If a login hostname resolves to multiple addresses, hjump resolves the endpoints itself and rotates between them when SSH fails before authentication.

Example:

```text
Login endpoint 10.189.18.101 unavailable (attempt 1/3): Connection refused
Trying 10.189.18.102 in 1 second(s)...
SSH connection succeeded on attempt 2/3 via 10.189.18.102.
```

Internal control connections deliberately avoid reusing a stale SSH `ControlMaster` socket so failover remains effective.

## Resource presets

Add optional presets to `config.toml`:

```toml
[clusters.uv]
login_host = "bigpurple.example.edu"
user = "username"
ssh_alias = "uv"
default_partition = "cpu_short"
default_time = "04:00:00"
default_cpus = 1
default_mem = "16G"

[clusters.uv.presets.small]
cpus = 1
mem = "16G"
time = "04:00:00"

[clusters.uv.presets.big]
partition = "cpu_short"
cpus = 8
mem = "128G"
time = "12:00:00"
```

Use them with:

```bash
hjump go uv --preset big
```

Explicit CLI flags override preset values:

```bash
hjump go uv --preset big --mem 192G
```

## Status and lifecycle

```bash
hjump status uv
```

Shows the managed login alias, currently working login endpoint, compute alias, active hjump jobs, partition, CPU/memory request, and remaining time.

Show every configured cluster:

```bash
hjump status
```

Stop hjump-owned jobs without looking up job IDs:

```bash
hjump stop uv
```

Request a completely fresh allocation:

```bash
hjump restart uv
hjump restart uv --preset big
```

## Diagnostics

```bash
hjump diag uv
```

Cluster diagnostics include:

- Python, OpenSSH, and VS Code
- VS Code Remote-SSH
- hjump config and SSH config permissions
- generated `<alias>-login` configuration
- every DNS-resolved login endpoint individually
- rotating/failover SSH login
- `squeue`, `salloc`, `scontrol`, and `scancel`

Verbose mode exposes OpenSSH diagnostics:

```bash
hjump diag uv -v
hjump go uv -v
```

If `go` fails, it points directly to `hjump diag <cluster>`.

## Self-healing behavior

`hjump` refreshes its managed login SSH block before control operations and rewrites the compute alias whenever the selected Slurm node changes. `stop` removes the compute-node target when no hjump-owned jobs remain. Login endpoint failures are retried against alternate DNS addresses.

The managed SSH block is bounded by comments such as:

```text
# >>> hjump managed: uv
...
# <<< hjump managed: uv
```

Content outside those markers is preserved.

## Additional usage

```bash
hjump go uv --time 08:00:00 --cpus 4 --mem 64G
hjump go uv --dir '~/project3'
hjump go uv --existing-job 12345678
hjump attach uv 12345678
```

`auto_reuse` reuses a matching RUNNING Slurm allocation, not prior shell state. Use tmux if persistent shell state is required.
