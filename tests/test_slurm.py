from __future__ import annotations

import socket
import subprocess
import unittest
from unittest.mock import patch

from hpc_jump.config import ClusterConfig
from hpc_jump.slurm import _parse_job_line, discover_partitions, resolve_login_endpoints


class SlurmTests(unittest.TestCase):
    def test_parse_job_status_fields(self) -> None:
        cluster = ClusterConfig(name="uv", login_host="login.example.edu")
        job = _parse_job_line(
            cluster,
            "123|RUNNING|cpu_short|cn-0025|hjump|4|64G|03:12:09|04:00:00|None",
        )
        self.assertEqual(job.job_id, "123")
        self.assertEqual(job.node, "cn-0025")
        self.assertEqual(job.partition, "cpu_short")
        self.assertEqual(job.cpus, 4)
        self.assertEqual(job.memory, "64G")
        self.assertEqual(job.time_left, "03:12:09")

    @patch("hpc_jump.slurm.socket.getaddrinfo")
    def test_resolve_login_endpoints_deduplicates(self, getaddrinfo) -> None:
        getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 22)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 22)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.2", 22)),
        ]
        cluster = ClusterConfig(name="uv", login_host="login.example.edu")
        self.assertEqual(resolve_login_endpoints(cluster), ["10.0.0.1", "10.0.0.2"])

    @patch("hpc_jump.slurm.run_login")
    def test_discover_partitions_uses_slurm_default(self, run_login_mock) -> None:
        run_login_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="cpu_short*\ncpu_long\ncpu_short*\n",
            stderr="",
        )
        cluster = ClusterConfig(name="uv", login_host="login.example.edu")
        partitions, default_partition = discover_partitions(cluster)
        self.assertEqual(partitions, ["cpu_short", "cpu_long"])
        self.assertEqual(default_partition, "cpu_short")


if __name__ == "__main__":
    unittest.main()
