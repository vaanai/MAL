"""Allowlist tests for the C1-NF executor unit files (DEC-026): base unit, live drop-in, shadow-feed template, launcher."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MF = ROOT / "scripts" / "mal-fast"


def _load(name: str, fname: str):
    spec = importlib.util.spec_from_file_location(name, MF / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


chk = _load("check_c1nf_unit", "check-c1nf-unit.py")
h5chk = _load("check_h5_unit_for_c1nf_test", "check-h5-unit.py")
BASE = (MF / "mal-c1nf-executor.service").read_bytes()
DROPIN = (MF / "mal-c1nf-executor-live-pinned.conf").read_bytes()
FEED = (MF / "mal-c1nf-executor-shadow-feed.conf").read_bytes()


def test_committed_files_pass():
    assert chk.problems(BASE, "base") == []
    assert chk.problems(DROPIN, "dropin") == []


def test_cli_exit_codes(tmp_path):
    for flag, data, rc in (("--base", BASE, 0), ("--dropin", DROPIN, 0), ("--dropin", BASE, 1), ("--shadow-feed", FEED, 1)):
        p = tmp_path / "f"
        p.write_bytes(data)
        r = subprocess.run([sys.executable, "-I", str(MF / "check-c1nf-unit.py"), flag, str(p)], capture_output=True)
        assert r.returncode == rc, (flag, r.stderr)


def test_dropin_hands_over_only_the_second_wallet():
    assert b"LoadCredential=c1nf-wallet:/etc/mal-c1nf-key/c1nf-wallet.json\n" in DROPIN
    assert b"probe-wallet" not in DROPIN.split(b"[Service]")[1]
    h5_cred = DROPIN.replace(b"c1nf-wallet:/etc/mal-c1nf-key/c1nf-wallet.json", b"probe-wallet:/etc/mal-probe/probe-wallet.json")
    assert chk.problems(h5_cred, "dropin")
    extra = DROPIN + b"LoadCredential=probe-wallet:/etc/mal-probe/probe-wallet.json\n"
    assert chk.problems(extra, "dropin")


def test_base_unit_is_keyless_and_c1nf_scoped():
    body = BASE.decode()
    lines = [ln for ln in body.split("\n") if ln and not ln.startswith("#")]
    assert not any(ln.startswith("LoadCredential") for ln in lines)
    assert not any(ln.startswith("Conflicts=") for ln in lines)  # starting C1-NF must never stop H5
    assert "ReadWritePaths=/var/lib/mal-live/c1nf" in lines
    assert "--live" not in "\n".join(lines)
    # H5's own files and key dir are hidden, not just read-only
    assert "InaccessiblePaths=-/var/lib/mal-live/h5 -/etc/mal-h5 -/etc/mal-probe -/usr/local/lib/mal-h5-exec" in lines


@pytest.mark.parametrize("mutation", [
    (b"ReadWritePaths=/var/lib/mal-live/c1nf", b"ReadWritePaths=/var/lib/mal-live"),
    (b"ReadWritePaths=/var/lib/mal-live/c1nf", b"ReadWritePaths=/var/lib/mal-live/h5"),
    (b"Wants=network-online.target\n", b"Wants=network-online.target\nConflicts=mal-h5-executor.service\n"),
    (b"ProtectHome=tmpfs", b"ProtectHome=read-only"),
    (b"c1nf-executor.json", b"h5-executor.json"),
    (b"[Service]\n", b"[Service]\nLoadCredential=c1nf-wallet:/etc/mal-c1nf-key/c1nf-wallet.json\n"),
    (b"User=mal-live", b"User =mal-live"),
    (b"\n", b"\r\n"),
])
def test_base_mutations_refuse(mutation):
    old, new = mutation
    assert old in BASE
    assert chk.problems(BASE.replace(old, new, 1), "base")


def test_c1nf_files_fail_h5s_checker_and_vice_versa():
    assert h5chk.problems(BASE, "base")
    assert h5chk.problems(DROPIN, "dropin")
    assert chk.problems((MF / "mal-h5-executor.service").read_bytes(), "base")
    assert chk.problems((MF / "mal-h5-executor-live-pinned.conf").read_bytes(), "dropin")


def test_shadow_feed_template_and_fill():
    assert chk.problems(FEED, "shadow-feed")  # the unfilled template is invalid on purpose
    good = FEED.replace(b"__SHADOW_DIR__", b"/home/ubuntu/data/c1nf-shadow")
    assert chk.problems(good, "shadow-feed") == []
    for bad in (b"/home/ubuntu/.claude/c1nf-shadow", b"/home/ubuntu/../root/c1nf-shadow", b"/var/lib/mal/c1nf-shadow",
                b"/home/ubuntu/data/h5-shadow", b"/home/ubuntu/data/c1nf-shadow /etc"):
        assert chk.problems(FEED.replace(b"__SHADOW_DIR__", bad), "shadow-feed"), bad
    h5dest = good.replace(b"/srv/mal-c1nf-shadow", b"/srv/mal-h5-shadow")
    assert chk.problems(h5dest, "shadow-feed")
    two = good + b"BindReadOnlyPaths=-/home/ubuntu/data/c1nf-shadow2:/srv/mal-c1nf-shadow\n"
    assert chk.problems(two, "shadow-feed")


def test_feed_dest_matches_v2_config_intents_file():
    # the pinned configs (claude/c1nf-executor-v2 @ 156a941) read "intents_file": "/srv/mal-c1nf-shadow"
    for cfg in ("c1nf-executor.json", "c1nf-executor-live.json"):
        p = MF / cfg
        if p.exists():
            import json
            assert json.loads(p.read_text())["intents_file"] == chk.SHADOW_DEST


def test_launcher_runs_c1nf_executor_only():
    src = (MF / "c1nf_exec_launcher.py").read_text()
    assert 'MODULE = "tools.c1nf_executor"' in src
    assert "h5_sell_and_close" not in src and "tools.h5_executor\"" not in src
    assert "runpy.run_module(MODULE" in src


def test_launcher_refuses_foreign_tools_package(tmp_path):
    (tmp_path / "launcher.py").write_bytes((MF / "c1nf_exec_launcher.py").read_bytes())
    r = subprocess.run([sys.executable, "-I", "-B", str(tmp_path / "launcher.py"), "--status"], capture_output=True, text=True,
                       cwd=str(tmp_path))
    assert r.returncode != 0  # no tools/ beside it: import fails, nothing runs


def test_launcher_refuses_run_tool(tmp_path):
    (tmp_path / "launcher.py").write_bytes((MF / "c1nf_exec_launcher.py").read_bytes())
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "__init__.py").write_text("")
    (tools / "c1nf_executor.py").write_text("raise SystemExit('ran executor')\n")
    r = subprocess.run([sys.executable, "-I", "-B", str(tmp_path / "launcher.py"), "--run-tool", "sell_and_close"], capture_output=True,
                       text=True)
    assert r.returncode == 1 and "no --run-tool" in r.stderr
    r = subprocess.run([sys.executable, "-I", "-B", str(tmp_path / "launcher.py"), "--status"], capture_output=True, text=True)
    assert "ran executor" in r.stderr
