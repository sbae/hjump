from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hpc_jump.config import ClusterConfig, ResourcePreset, load_cluster, upsert_cluster_config


class ConfigTests(unittest.TestCase):
    def test_presets_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            cluster = ClusterConfig(
                name="uv",
                login_host="login.example.edu",
                user="alice",
                ssh_alias="uv",
                presets={
                    "big": ResourcePreset(
                        name="big",
                        partition="cpu_short",
                        time="12:00:00",
                        cpus=8,
                        mem="128G",
                        salloc_extra=["--exclusive"],
                    )
                },
            )
            upsert_cluster_config(cluster, path)
            loaded = load_cluster("uv", path)
            self.assertEqual(loaded.presets["big"].cpus, 8)
            self.assertEqual(loaded.presets["big"].mem, "128G")
            self.assertEqual(loaded.presets["big"].salloc_extra, ["--exclusive"])

    def test_upsert_preserves_other_cluster(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            upsert_cluster_config(ClusterConfig(name="a", login_host="a.example"), path)
            upsert_cluster_config(ClusterConfig(name="b", login_host="b.example"), path)
            upsert_cluster_config(
                ClusterConfig(name="a", login_host="new-a.example"),
                path,
                overwrite=True,
            )
            self.assertEqual(load_cluster("a", path).login_host, "new-a.example")
            self.assertEqual(load_cluster("b", path).login_host, "b.example")


if __name__ == "__main__":
    unittest.main()
