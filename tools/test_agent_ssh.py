"""Dry-run tests for scripts/mal-core/agent-ssh.sh. No network."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "mal-core" / "agent-ssh.sh"

CORE_FP = "SHA256:Hy68mL6wisJ2t+z/JDcSNATPlyA8sudv4Za7A2Y8Ejs"
FAST_FP = "SHA256:q5o6Bf1Vo83LtQhwdSQfIL5D4mkRMjELjaL//Y8XXzQ"


def run(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    for key in (
        "MAL_SSH_KEY_B64",
        "CURSOR_CLOUD_AGENT_SSH_KEY",
        "CF_ACCESS_CLIENT_ID",
        "CF_ACCESS_CLIENT_SECRET",
        "CLOUDFLARE_ACCESS_CLIENT_ID",
        "CLOUDFLARE_ACCESS_CLIENT_SECRET",
        "MAL_SSH_LOCAL_LISTEN",
        "MAL_SSH_KEY_FINGERPRINT",
        "MAL_CURSOR_KEY_FINGERPRINT",
    ):
        merged.pop(key, None)
    if env:
        merged.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
        env=merged,
    )


class AgentSshTests(unittest.TestCase):
    def test_help_names_both_secret_families_and_hosts(self) -> None:
        result = run(["--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MAL_SSH_KEY_B64", result.stdout)
        self.assertIn("CURSOR_CLOUD_AGENT_SSH_KEY", result.stdout)
        self.assertIn("CF_ACCESS_CLIENT_ID", result.stdout)
        self.assertIn("CLOUDFLARE_ACCESS_CLIENT_SECRET", result.stdout)
        self.assertIn("--host core|fast", result.stdout)
        self.assertIn("cloudflared access ssh --hostname %h", result.stdout)

    def test_host_is_required(self) -> None:
        result = run([])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--host", result.stderr)

    def test_unknown_host(self) -> None:
        result = run(["--host", "nope", "--dry-run"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown host", result.stderr)

    def test_dry_run_core_and_fast_pins(self) -> None:
        core = run(["--host", "core", "--dry-run"])
        self.assertEqual(core.returncode, 0, core.stderr)
        self.assertIn("host=core", core.stdout)
        self.assertIn("access_hostname=ssh.tradervaan.com", core.stdout)
        self.assertIn(f"host_key_fingerprint={CORE_FP}", core.stdout)
        self.assertIn("dry_run=1", core.stdout)
        self.assertIn("key_env=unset", core.stdout)

        fast = run(["--host", "fast", "--dry-run"])
        self.assertEqual(fast.returncode, 0, fast.stderr)
        self.assertIn("access_hostname=ssh-fast.tradervaan.com", fast.stdout)
        self.assertIn(f"host_key_fingerprint={FAST_FP}", fast.stdout)
        self.assertIn("local_listen=127.0.0.1:2223", fast.stdout)

    def test_neutral_names_win_over_legacy(self) -> None:
        both = run(
            ["--host", "core", "--dry-run"],
            {
                "MAL_SSH_KEY_B64": "bmV1dHJhbA==",
                "CURSOR_CLOUD_AGENT_SSH_KEY": "bGVnYWN5",
                "CF_ACCESS_CLIENT_ID": "neutral-id",
                "CLOUDFLARE_ACCESS_CLIENT_ID": "legacy-id",
                "CF_ACCESS_CLIENT_SECRET": "neutral-secret",
                "CLOUDFLARE_ACCESS_CLIENT_SECRET": "legacy-secret",
            },
        )
        self.assertEqual(both.returncode, 0, both.stderr)
        self.assertIn("key_env=MAL_SSH_KEY_B64", both.stdout)
        self.assertIn("access_id_env=CF_ACCESS_CLIENT_ID", both.stdout)
        self.assertIn("access_secret_env=CF_ACCESS_CLIENT_SECRET", both.stdout)
        self.assertNotIn("bmV1dHJhbA==", both.stdout)
        self.assertNotIn("neutral-secret", both.stdout)
        self.assertNotIn("legacy-secret", both.stderr)

        legacy = run(
            ["--host", "fast", "--dry-run"],
            {
                "CURSOR_CLOUD_AGENT_SSH_KEY": "bGVnYWN5",
                "CLOUDFLARE_ACCESS_CLIENT_ID": "legacy-id",
                "CLOUDFLARE_ACCESS_CLIENT_SECRET": "legacy-secret",
            },
        )
        self.assertEqual(legacy.returncode, 0, legacy.stderr)
        self.assertIn("key_env=CURSOR_CLOUD_AGENT_SSH_KEY", legacy.stdout)
        self.assertIn("access_id_env=CLOUDFLARE_ACCESS_CLIENT_ID", legacy.stdout)
        self.assertIn("access_secret_env=CLOUDFLARE_ACCESS_CLIENT_SECRET", legacy.stdout)
        self.assertNotIn("legacy-secret", legacy.stdout)

    def test_dry_run_does_not_connect_or_decode_a_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp) / "bin"
            bindir.mkdir()
            marker = Path(tmp) / "called"
            for name in ("ssh", "ssh-keyscan", "cloudflared", "curl", "ssh-keygen", "base64"):
                path = bindir / name
                path.write_text(
                    "#!/bin/sh\n"
                    f"echo {name} >> '{marker}'\n"
                    "exit 99\n",
                    encoding="utf-8",
                )
                path.chmod(path.stat().st_mode | stat.S_IEXEC)
            env = os.environ.copy()
            env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "/usr/bin")
            env["MAL_SSH_KEY_B64"] = "bmV1dHJhbA=="
            result = subprocess.run(
                ["bash", str(SCRIPT), "--host", "core", "--dry-run"],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists(), marker.read_text() if marker.exists() else "")
            self.assertFalse(list(Path("/tmp").glob("mal-ssh-key.*")))

    def test_ssh_config_documents_proxy_command_and_pins(self) -> None:
        result = run(["--ssh-config"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ProxyCommand cloudflared access ssh --hostname %h", result.stdout)
        self.assertIn(CORE_FP, result.stdout)
        self.assertIn(FAST_FP, result.stdout)
        self.assertIn("StrictHostKeyChecking=no", result.stdout)
        self.assertNotIn("BEGIN OPENSSH PRIVATE KEY", result.stdout)

    def test_check_host_key_stops_on_mismatch(self) -> None:
        ssh_keygen = subprocess.run(["ssh-keygen", "-h"], check=False, capture_output=True, text=True)
        if ssh_keygen.returncode not in (0, 1) and "unknown" in (ssh_keygen.stderr + ssh_keygen.stdout).lower():
            self.skipTest("ssh-keygen missing")
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / "id_ed25519"
            made = subprocess.run(
                ["ssh-keygen", "-t", "ed25519", "-f", str(key), "-N", "", "-q"],
                check=False,
                capture_output=True,
                text=True,
            )
            if made.returncode != 0:
                self.skipTest(made.stderr)
            result = run(["--host", "core", "--check-host-key", str(key.with_suffix(".pub"))])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("host-key fingerprint mismatch", result.stderr)
            self.assertIn(CORE_FP, result.stderr)
            self.assertNotIn("PRIVATE KEY", result.stdout + result.stderr)

    def test_script_pins_strict_host_key_checking(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("StrictHostKeyChecking=yes", text)
        self.assertNotIn("StrictHostKeyChecking=accept-new", text)
        self.assertIn(CORE_FP, text)
        self.assertIn(FAST_FP, text)
        self.assertNotIn("BEGIN OPENSSH PRIVATE KEY", text)


if __name__ == "__main__":
    unittest.main()
