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
CAPF = (MF / "mal-c1nf-executor-cap-pick.conf").read_bytes()


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
    # the pinned configs (claude/c1nf-executor-v2 @ 32265af) read "intents_file": "/srv/mal-c1nf-shadow"
    for cfg in ("c1nf-executor.json", "c1nf-executor-live.json"):
        p = MF / cfg
        if p.exists():
            import json
            assert json.loads(p.read_text())["intents_file"] == chk.SHADOW_DEST


def test_launcher_runs_c1nf_modules_only():
    src = (MF / "c1nf_exec_launcher.py").read_text()
    code = "\n".join(ln for ln in src.split('"""')[2].splitlines() if not ln.lstrip().startswith("#"))
    assert 'MODULE = "tools.c1nf_executor"' in code and 'TOOLS = {"sell_and_close": "tools.c1nf_sell_and_close"}' in code
    assert "h5_sell_and_close" not in code and "tools.h5_executor" not in code  # H5's tool is never offered for the second wallet
    assert "runpy.run_module(MODULE" in code


def test_launcher_refuses_foreign_tools_package(tmp_path):
    (tmp_path / "launcher.py").write_bytes((MF / "c1nf_exec_launcher.py").read_bytes())
    r = subprocess.run([sys.executable, "-I", "-B", str(tmp_path / "launcher.py"), "--status"], capture_output=True, text=True,
                       cwd=str(tmp_path))
    assert r.returncode != 0  # no tools/ beside it: import fails, nothing runs


def _fake_tree(tmp_path):
    (tmp_path / "launcher.py").write_bytes((MF / "c1nf_exec_launcher.py").read_bytes())
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "__init__.py").write_text("")
    (tools / "c1nf_executor.py").write_text("raise SystemExit('ran executor')\n")
    (tools / "c1nf_sell_and_close.py").write_text("import sys\nraise SystemExit('ran c1nf sell_and_close ' + ' '.join(sys.argv[1:]))\n")
    (tools / "h5_sell_and_close.py").write_text("raise SystemExit('ran H5 sell_and_close')\n")
    return tmp_path / "launcher.py"


def test_launcher_run_tool_is_c1nfs_sell_and_close_only(tmp_path):
    launcher = _fake_tree(tmp_path)

    def go(*argv):
        return subprocess.run([sys.executable, "-I", "-B", str(launcher), *argv], capture_output=True, text=True)

    r = go("--run-tool", "sell_and_close", "--mint", "M")
    assert r.returncode == 1 and "ran c1nf sell_and_close --mint M" in r.stderr and "H5" not in r.stderr
    for bad in (("--run-tool",), ("--run-tool", "h5_sell_and_close"), ("--run-tool", "withdraw")):
        r = go(*bad)
        assert r.returncode == 1 and "--run-tool needs one of: sell_and_close" in r.stderr, bad
    r = go("--status")
    assert "ran executor" in r.stderr


def test_cap_pick_template_and_fill(tmp_path):
    """DEC-026 Amendment 1 item B: the CAP-PICK oracle directory, bound read-only at /srv/mal-cap-pick (the H5 shadow-feed pattern)."""
    assert chk.problems(CAPF, "cap-pick")  # the unfilled template is invalid on purpose
    good = CAPF.replace(b"__CAP_PICK_DIR__", b"/home/claude/data/h5-shadow/cap-pick")  # the one exporter's CAP_PICK_OUT (H5's pick_file reads it too)
    assert chk.problems(good, "cap-pick") == []
    for bad in (b"/home/claude/.claude/cap-pick-oracle", b"/home/claude/../root/cap-pick-oracle", b"/srv/mal-cap-pick-src/cap-pick",
                b"/home/claude/data/c1nf-shadow", b"/home/claude/data/cap-pick-oracle /etc", b"/var/lib/mal-live/cap-pick"):
        assert chk.problems(CAPF.replace(b"__CAP_PICK_DIR__", bad), "cap-pick"), bad
    # the manager's decision on #544: the EXACT directory name. The exporter's FINAL marker lives in ~/data/cap-pick-oracle and must never be bound.
    for bad in (b"/home/claude/data/cap-pick-oracle", b"/home/claude/data/h5-shadow/cap-pick-oracle", b"/home/claude/data/h5-shadow/cap-pick2",
                b"/home/claude/data/h5-shadow/cap-pick.bak", b"/home/claude/data/h5-shadow/cap-pick-", b"/home/claude/data/h5-shadow/cap-pick/",
                b"/home/claude/data/h5-shadow/cap-pick/sub", b"/home/claude/data/h5-shadow/Cap-pick"):
        assert chk.problems(CAPF.replace(b"__CAP_PICK_DIR__", bad), "cap-pick"), bad
    for ok in (b"/home/claude/data/h5-shadow/cap-pick", b"/home/claude/cap-pick", b"/home/mal-user/data/x_y.z/cap-pick"):
        assert chk.problems(CAPF.replace(b"__CAP_PICK_DIR__", ok), "cap-pick") == [], ok
    assert chk.problems(good.replace(b":/srv/mal-cap-pick", b":/srv/mal-c1nf-shadow"), "cap-pick")
    assert chk.problems(good.replace(b"BindReadOnlyPaths=", b"BindPaths="), "cap-pick")  # never writable
    assert chk.problems(good + b"BindReadOnlyPaths=-/home/claude/data/cap-pick2:/srv/mal-cap-pick\n", "cap-pick")
    # the two binds are not interchangeable
    assert chk.problems(good, "shadow-feed")
    assert chk.problems(FEED.replace(b"__SHADOW_DIR__", b"/home/claude/data/c1nf-shadow"), "cap-pick")
    p = tmp_path / "20-cap-pick.conf"
    p.write_bytes(good)
    assert chk.main(["check-c1nf-unit.py", "--cap-pick", str(p)]) == 0
    p.write_bytes(CAPF)
    assert chk.main(["check-c1nf-unit.py", "--cap-pick", str(p)]) == 1


def test_cap_pick_dest_matches_live_pick_file():
    import json
    live = json.loads((MF / "c1nf-executor-live.json").read_text())
    assert live["pick_file"] == chk.CAP_PICK_DEST + "/picks.jsonl"
    assert "pick_file" not in json.loads((MF / "c1nf-executor.json").read_text())  # the keyless dry run runs before the seal window
