from __future__ import annotations

import socket
import unittest
from unittest.mock import patch

from hpc_jump.config import ClusterConfig
from hpc_jump.slurm import _parse_job_line, resolve_login_endpoints


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


if __name__ == "__main__":
    unittest.main()
