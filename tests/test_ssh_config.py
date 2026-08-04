from __future__ import annotations

import unittest

from hpc_jump.ssh_config import _extract_compute_node


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


if __name__ == "__main__":
    unittest.main()
