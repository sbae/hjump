from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hpc_jump.cli import _find_identity_files, _prompt_authentication


class InitAuthenticationTests(unittest.TestCase):
    def _write_key(self, path: Path, marker: str = "-----BEGIN OPENSSH PRIVATE KEY-----") -> None:
        path.write_text(f"{marker}\nfake-key-data\n", encoding="utf-8")

    def test_discovers_standard_and_custom_named_private_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ssh_dir = Path(tmp)
            custom = ssh_dir / "bigpurple_key"
            rsa = ssh_dir / "id_rsa"
            ed25519 = ssh_dir / "id_ed25519"
            public = ssh_dir / "id_rsa.pub"
            config = ssh_dir / "config"

            self._write_key(custom)
            self._write_key(rsa, "-----BEGIN RSA PRIVATE KEY-----")
            self._write_key(ed25519)
            public.write_text("ssh-rsa AAAA...", encoding="utf-8")
            config.write_text("Host example\n    HostName example.edu\n", encoding="utf-8")

            found = _find_identity_files(ssh_dir)

            self.assertEqual(found, [ed25519, rsa, custom])
            self.assertNotIn(public, found)
            self.assertNotIn(config, found)

    @patch("hpc_jump.cli.typer.prompt", return_value=2)
    def test_selects_second_detected_key(self, _prompt) -> None:
        keys = [Path("/tmp/id_ed25519"), Path("/tmp/id_rsa")]
        method, identity = _prompt_authentication(keys)
        self.assertEqual(method, "key")
        self.assertEqual(identity, "/tmp/id_rsa")

    @patch("hpc_jump.cli.typer.prompt", return_value=2)
    def test_no_keys_defaults_menu_to_password_option(self, _prompt) -> None:
        method, identity = _prompt_authentication([])
        self.assertEqual(method, "password")
        self.assertIsNone(identity)


if __name__ == "__main__":
    unittest.main()
