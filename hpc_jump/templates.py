from __future__ import annotations


def config_template(cluster_name: str = "my-hpc") -> str:
    safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in cluster_name)
    return rf'''# hjump configuration file.
# Create one [clusters.<name>] section per cluster.

[clusters.{safe_name}]
login_host = "login.hpc.edu"
port = 22
user = "your_username"

# Authentication option A: explicit SSH private key.
# Forward slashes are easiest on Windows.
identity_file = "C:/Users/your_windows_username/.ssh/id_ed25519"

# Authentication option B: password/MFA/SSO.
# Omit identity_file; hjump never stores passwords and lets OpenSSH prompt.

# Authentication option C: reuse an existing Host entry from ~/.ssh/config.
# When set, hjump uses that alias exactly for login-node commands and ProxyJump.
# Omit identity_file if the existing alias already handles authentication.
# login_ssh_alias = "existing-hpc-login"

# Stable local compute alias. If login_ssh_alias is not set, hjump also
# manages <ssh_alias>-login for the login node.
ssh_alias = "{safe_name}"

# Optional project folder opened automatically in VS Code.
# remote_project_path = "/home/your_username/project"

# Omit default_partition to use the Slurm cluster default.
# The interactive init wizard discovers partitions with sinfo when possible.
# default_partition = "cpu"
default_time = "04:00:00"
default_cpus = 1
default_mem = "16G"
salloc_extra = []

# Optional command run before every remote Slurm command.
# remote_init = "module load slurm"

job_name_prefix = "hjump"
auto_reuse = true

# Optional named resource presets. CLI flags override preset values.
# Example usage: hjump go {safe_name} --preset big
#
# [clusters.{safe_name}.presets.small]
# cpus = 1
# mem = "16G"
# time = "04:00:00"
#
# [clusters.{safe_name}.presets.big]
# partition = "cpu"
# cpus = 8
# mem = "128G"
# time = "12:00:00"
# salloc_extra = []
'''
