from __future__ import annotations

import unittest

from hpc_jump.config import ClusterConfig
from hpc_jump.ssh_config import _extract_compute_node, render_host_block, render_login_block


class ExtractComputeNodeTests(unittest.TestCase):
    def test_exact_alias_does_not_match_login_alias_prefix(self) -> None:
        managed = """
Host uv-login
    HostName bigpurple.nyumc.org

Host uv
    HostName cn-0025
    ProxyJump uv-login
"""
        self.assertEqual(_extract_compute_node(managed, "uv"), "cn-0025")

    def test_uses_last_exact_stanza_to_repair_corrupted_block(self) -> None:
        managed = """
Host uv-login
    HostName bigpurple.nyumc.org

Host uv
-login
    HostName bigpurple.nyumc.org

Host uv
    HostName cn-0025
    ProxyJump uv-login
"""
        self.assertEqual(_extract_compute_node(managed, "uv"), "cn-0025")

    def test_external_login_alias_is_not_redefined(self) -> None:
        cluster = ClusterConfig(
            name="uv",
            login_host="bigpurple.nyumc.org",
            user="alice",
            ssh_alias="uv",
            login_ssh_alias="existing-login",
        )
        self.assertEqual(render_login_block(cluster), "")
        block = render_host_block(cluster, "cn-0025")
        self.assertNotIn("Host existing-login", block)
        self.assertIn("Host uv", block)
        self.assertIn("ProxyJump existing-login", block)


if __name__ == "__main__":
    unittest.main()
