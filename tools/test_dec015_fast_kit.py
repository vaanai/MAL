"""Checks on the DEC-015 fast-host forward-paper kit (scripts/mal-fast/). Nothing here
installs or starts anything real: the installer runs use a temp MAL_ROOT, a temp systemd
dir, and stubbed sudo and systemctl."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.forward_paper import books_from_config

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "scripts" / "mal-fast"
EXP012 = ROOT / "ARTIFACTS" / "exp012"
CFG = KIT / "fast-forward-paper.json"
SCRIPTS = sorted(KIT.glob("*.sh"))
INSTALLERS = [KIT / "install-fast-observe.sh", KIT / "install-fast-forward-paper.sh"]
NOT_BEFORE = dt.datetime(2026, 10, 5, 5, 0, 0, tzinfo=dt.timezone.utc)
FLOOR = "d7485d2"


def _frozen() -> dict[str, str]:
    out = {}
    for line in (EXP012 / "FROZEN.md5").read_text().splitlines():
        parts = line.split()
        if len(parts) == 2:
            out[parts[1]] = parts[0]
    return out


def _fixture_config(tmp_path: Path) -> dict:
    raw = json.loads(CFG.read_text())
    d = tmp_path / "exp012"
    d.mkdir()
    for f in ("model.txt", "features.json", "threshold.json"):
        shutil.copy(EXP012 / f, d / f)
    book = raw["books"][0]
    book["entry_model"] = str(d / "model.txt")
    book["entry_features"] = str(d / "features.json")
    raw["output_dir"] = str(tmp_path / "out")
    raw["kill_file"] = str(tmp_path / "out" / "KILL")
    return raw


# ---------------------------------------------------------------- config

def test_config_yields_one_gated_migrate_book(tmp_path):
    raw = _fixture_config(tmp_path)
    books = books_from_config(raw)
    assert len(books) == 1
    b = books[0]
    assert b.kind == "migrate"
    assert b.exit_rule == "tp50_sl30"
    assert b.entry_model and b.entry_model_md5 and b.entry_features
    assert b.size_lamports == 50_000_000


def test_config_paths_and_scope():
    raw = json.loads(CFG.read_text())
    assert raw["tape_dir"] == "/var/lib/mal/sealed/fast-trades-tip"  # DEC-015 2.2 tip follower (V field, #288)
    assert raw["creates_dir"] == "/var/lib/mal/sealed/fast-creates-tip"
    assert raw["output_dir"] == "/var/lib/mal/paper/fast-forward-paper"
    assert raw["graph_dir"] == "/var/lib/mal/fast-forward/no-graph"
    assert "attention_dir" not in raw
    assert raw["size_sol"] == 0.05
    book = raw["books"][0]
    for k in ("entry_model", "entry_features"):
        assert book[k].startswith("/var/lib/mal/fast-forward/exp012/")
    assert len(raw["books"]) == 1


def test_config_matches_frozen_artifacts():
    book = json.loads(CFG.read_text())["books"][0]
    assert book["entry_model_md5"] == _frozen()["model.txt"]
    thr = json.loads((EXP012 / "threshold.json").read_text())["threshold"]
    assert book["entry_threshold"] == thr


@pytest.mark.parametrize("name", ["model.txt", "features.json", "threshold.json"])
def test_frozen_md5_matches_files(name):
    assert hashlib.md5((EXP012 / name).read_bytes()).hexdigest() == _frozen()[name]


# ---------------------------------------------------------------- unit files

def test_forward_unit_and_slice():
    unit = (KIT / "mal-fast-forward-paper.service").read_text()
    assert re.search(r"^Slice=mal-forward\.slice$", unit, re.M)
    assert re.search(r"^User=ubuntu$", unit, re.M)
    assert re.search(r"^Restart=on-failure$", unit, re.M)
    assert re.search(r"^Nice=19$", unit, re.M)
    for var in ("OMP", "OPENBLAS", "MKL", "NUMEXPR"):
        assert f"Environment={var}_NUM_THREADS=1" in unit
    assert "/var/lib/mal/fast-forward/config.json" in unit
    for line in (
        "ProtectSystem=strict",
        "ReadWritePaths=/var/lib/mal/paper/fast-forward-paper /var/lib/mal/logs",
        "ReadOnlyPaths=/var/lib/mal/sealed /var/lib/mal/fast-forward",
        "ProtectHome=true",
        "PrivateTmp=true",
        "ProtectKernelTunables=true",
        "ProtectKernelModules=true",
        "ProtectControlGroups=true",
        "RestrictSUIDSGID=true",
        "LockPersonality=true",
        "MemorySwapMax=0",
    ):
        assert re.search(rf"^{re.escape(line)}$", unit, re.M), line
    assert not re.search(r"^RestrictAddressFamilies=", unit, re.M)
    sl = (KIT / "mal-forward.slice").read_text()
    assert re.search(r"^MemoryHigh=5G$", sl, re.M)
    assert re.search(r"^MemoryMax=6G$", sl, re.M)
    assert not re.search(r"^Slice=", sl, re.M)  # a system slice, no user-1000 parent


def test_tip_follower_unit_and_installer():
    unit = (KIT / "mal-fast-tip-follower.service").read_text()
    assert re.search(r"^Slice=mal-forward\.slice$", unit, re.M)
    assert re.search(r"^User=ubuntu$", unit, re.M)
    assert re.search(r"^Restart=always$", unit, re.M)
    assert re.search(r"^EnvironmentFile=/var/lib/mal/fast-listener/helius\.env$", unit, re.M)
    for line in ("ProtectSystem=strict", "NoNewPrivileges=true", "ProtectHome=true", "PrivateTmp=true",
                 "ProtectKernelTunables=true", "ProtectKernelModules=true", "ProtectControlGroups=true",
                 "RestrictSUIDSGID=true", "LockPersonality=true", "MemorySwapMax=0",
                 "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX", "CapabilityBoundingSet=",
                 "PrivateDevices=true", "ProtectKernelLogs=true", "ProtectClock=true",
                 "SystemCallFilter=@system-service", "UMask=0077", "RestartSec=30", "StartLimitBurst=5"):
        assert re.search(rf"^{re.escape(line)}$", unit, re.M), line
    assert "--creates-out /var/lib/mal/sealed/fast-creates-tip" in unit
    assert "--max-keep-days 3" in unit
    assert "-m tools.fast_tip_follower" in unit
    assert "api-key" not in unit.lower() and "HELIUS_API_KEY=" not in unit
    inst = (KIT / "install-fast-forward-paper.sh").read_text()
    assert 'TIP_UNIT="mal-fast-tip-follower.service"' in inst
    assert '"${TIP_UNIT}" "${PROBE_UNIT}"; do' in inst
    cfg = json.loads(CFG.read_text())
    assert cfg["tape_dir"] == "/var/lib/mal/sealed/fast-trades-tip"  # switched: pumpswap_virtual=require needs the tip tape


def test_observe_unit():
    unit = (KIT / "mal-fast-observe.service").read_text()
    assert re.search(r"^MemoryMax=1G$", unit, re.M)
    assert re.search(r"^Restart=always$", unit, re.M)
    assert "-m observe" in unit
    assert "/var/lib/mal/sealed/fast-observe" in unit
    assert not re.search(r"^User=", unit, re.M)  # user unit


def test_restart_units():
    svc = (KIT / "mal-fast-forward-paper-restart.service").read_text()
    assert re.search(r"^Type=oneshot$", svc, re.M)
    assert "ExecStart=/usr/bin/systemctl restart mal-fast-forward-paper.service" in svc
    tm = (KIT / "mal-fast-forward-paper-restart.timer").read_text()
    assert "OnCalendar=*-*-* 00:00:00 UTC" in tm
    assert re.search(r"^Persistent=false$", tm, re.M)


def test_forward_launcher_is_paper_only():
    text = (KIT / "fast-forward-paper.sh").read_text()
    assert "tools.forward_paper serve" in text
    assert "/var/lib/mal/fast-forward" in text
    assert not re.search(r"(?i)helius|private|keypair|wallet", text)


# ---------------------------------------------------------------- static installer checks

@pytest.mark.parametrize("path", INSTALLERS, ids=lambda p: p.name)
def test_installers_static(path):
    text = path.read_text()
    assert "--dry-run" in text and "--commit" in text
    assert 'git -C "${ROOT}" archive' in text
    executed = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Printing the manual next step is allowed; executing it is not.
        if line.startswith(("log ", "echo ", "printf ", "die ")):
            continue
        executed.append(line)
    for line in executed:
        if "systemctl" in line:
            # also catches variable-built calls: any systemctl on an executed line must be daemon-reload
            assert "daemon-reload" in line, line
        assert not re.search(r"\b(enable|--now|unmask)\b", line), line
    # exactly one executed systemctl invocation
    assert len([ln for ln in executed if "systemctl" in ln]) == 1
    # variable-built command names are not allowed to smuggle one in
    assert not re.search(r"^\s*(\$\{?[A-Za-z_]+\}?|eval)\b.*(start|enable)", "\n".join(executed), re.M)


def test_installers_never_create_no_graph():
    for path in INSTALLERS:
        for raw in path.read_text().splitlines():
            if "no-graph" in raw:
                assert raw.strip().startswith("#"), f"{path.name}: {raw}"


def test_observe_installer_refuses_root_statically():
    text = (KIT / "install-fast-observe.sh").read_text()
    assert '"${EUID}" == 0' in text
    assert "sudo -u ubuntu XDG_RUNTIME_DIR=/run/user/1000 bash install-fast-observe.sh --commit" in text


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_bash_syntax(path):
    subprocess.run(["bash", "-n", str(path)], check=True)


# ---------------------------------------------------------------- repo fixture

@pytest.fixture(scope="module")
def kit_repo(tmp_path_factory):
    """A local clone of this repo with the current kit committed on top of origin/main."""
    base = tmp_path_factory.mktemp("kitrepo")
    repo = base / "repo"

    def git(*a, check=True):
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=check)

    r = subprocess.run(["git", "clone", "-q", str(ROOT), str(repo)], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"cannot clone repo: {r.stderr}")
    if git("rev-parse", "--verify", "--quiet", FLOOR + "^{commit}", check=False).returncode != 0:
        pytest.skip("floor commit not in this clone")
    if git("rev-parse", "--verify", "--quiet", "origin/main", check=False).returncode != 0:
        pytest.skip("no origin/main in clone")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    git("checkout", "-q", "-B", "work", "origin/main")
    dest = repo / "scripts" / "mal-fast"
    dest.mkdir(parents=True, exist_ok=True)
    for f in KIT.iterdir():
        if f.is_file():
            shutil.copy2(f, dest / f.name)
    git("add", "-A")
    git("commit", "-q", "-m", "kit")
    good = git("rev-parse", "HEAD").stdout.strip()
    git("update-ref", "refs/remotes/origin/main", good)
    git("checkout", "-q", "-b", "side")
    (repo / "side.txt").write_text("x")
    git("add", "side.txt")
    git("commit", "-q", "-m", "side")
    side = git("rev-parse", "HEAD").stdout.strip()
    git("checkout", "-q", "work")
    prefloor = git("rev-parse", FLOOR + "~1").stdout.strip()
    return {"repo": repo, "good": good, "side": side, "prefloor": prefloor, "git": git}


def _stubs(tmp_path: Path) -> tuple[Path, Path]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "systemctl.calls"
    (bindir / "sudo").write_text('#!/usr/bin/env bash\nif [ "$1" = "-n" ]; then shift; fi\nexec "$@"\n')
    (bindir / "systemctl").write_text(f'#!/usr/bin/env bash\necho "$@" >> {calls}\n')
    for f in bindir.iterdir():
        f.chmod(0o755)
    calls.write_text("")
    return bindir, calls


def _env(tmp_path: Path, bindir: Path) -> dict:
    env = dict(os.environ)
    env.update(
        PATH=f"{bindir}:{env['PATH']}",
        MAL_ROOT=str(tmp_path / "mal"),
        MAL_SYSTEMD_DIR=str(tmp_path / "systemd"),
        MAL_FORWARD_OWNER="",
        HOME=str(tmp_path / "home"),
        XDG_RUNTIME_DIR=str(tmp_path / "run"),
    )
    (tmp_path / "systemd").mkdir()
    (tmp_path / "home").mkdir()
    return env


def _fwd(repo, *args, env=None):
    return subprocess.run(
        ["bash", str(repo / "scripts" / "mal-fast" / "install-fast-forward-paper.sh"), *args],
        capture_output=True, text=True, env=env,
    )


def _obs(repo, *args, env=None):
    return subprocess.run(
        ["bash", str(repo / "scripts" / "mal-fast" / "install-fast-observe.sh"), *args],
        capture_output=True, text=True, env=env,
    )


# ---------------------------------------------------------------- --commit refusals

def test_commit_required(kit_repo):
    assert _fwd(kit_repo["repo"], "--dry-run", "--files-only").returncode != 0
    assert _obs(kit_repo["repo"], "--dry-run").returncode != 0


@pytest.mark.parametrize("which", ["side", "prefloor"])
def test_commit_refusals(kit_repo, which):
    sha = kit_repo[which]
    want = "not an ancestor of origin/main" if which == "side" else "not a descendant of the code floor"
    r = _fwd(kit_repo["repo"], "--dry-run", "--files-only", "--commit", sha)
    assert r.returncode != 0 and want in r.stderr, r.stderr
    r = _obs(kit_repo["repo"], "--dry-run", "--commit", sha)
    assert r.returncode != 0 and want in r.stderr, r.stderr


def test_commit_refuses_dirty_tree(kit_repo):
    repo = kit_repo["repo"]
    (repo / "scripts" / "mal-fast" / "fast-forward-paper.sh").write_text("# dirty\n")
    try:
        r = _fwd(repo, "--dry-run", "--files-only", "--commit", kit_repo["good"])
        assert r.returncode != 0 and "dirty" in r.stderr, r.stderr
        r = _obs(repo, "--dry-run", "--commit", kit_repo["good"])
        assert r.returncode != 0 and "dirty" in r.stderr, r.stderr
    finally:
        kit_repo["git"]("checkout", "--", ".")


def test_dry_runs_ok(kit_repo):
    repo, sha = kit_repo["repo"], kit_repo["good"]
    r = _fwd(repo, "--dry-run", "--files-only", "--commit", sha)
    assert r.returncode == 0, r.stderr
    assert "md5 ok model.txt" in r.stdout
    assert "daemon-reload" not in r.stdout and "mal-forward.slice" not in r.stdout
    r = _obs(repo, "--dry-run", "--commit", sha)
    assert r.returncode == 0, r.stderr
    assert "DRY-RUN" in r.stdout and "archive" in r.stdout


# ---------------------------------------------------------------- real path, stubbed sudo/systemctl

def test_forward_files_only_real(kit_repo, tmp_path):
    bindir, calls = _stubs(tmp_path)
    env = _env(tmp_path, bindir)
    r = _fwd(kit_repo["repo"], "--files-only", "--commit", kit_repo["good"], env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    fwd = tmp_path / "mal" / "fast-forward"
    for f in ("model.txt", "features.json", "threshold.json"):
        assert (fwd / "exp012" / f).is_file()
    assert (fwd / "config.json").is_file()
    assert not (fwd / "src").exists()
    assert not (fwd / "no-graph").exists()
    assert not list((tmp_path / "systemd").iterdir())
    assert calls.read_text() == ""


def test_forward_date_fence_real_path(kit_repo, tmp_path):
    bindir, calls = _stubs(tmp_path)
    env = _env(tmp_path, bindir)
    stale = tmp_path / "mal" / "fast-forward" / "src" / "tools" / "stale.py"
    stale.parent.mkdir(parents=True)
    stale.write_text("x")
    r = _fwd(kit_repo["repo"], "--commit", kit_repo["good"], env=env)
    if dt.datetime.now(dt.timezone.utc) < NOT_BEFORE:
        assert r.returncode != 0
        assert "refusing before 2026-10-05T05:00:00Z" in r.stderr
        assert stale.exists()  # nothing touched
        assert not (tmp_path / "mal" / "fast-forward" / "config.json").exists()
        assert calls.read_text() == ""
        return
    assert r.returncode == 0, r.stderr + r.stdout
    fwd = tmp_path / "mal" / "fast-forward"
    assert not stale.exists()
    assert (fwd / "src" / "SOURCE_COMMIT").read_text().strip() == kit_repo["good"]
    assert (fwd / "src" / "tools" / "forward_paper.py").is_file()
    assert not (fwd / "src.new").exists() and not (fwd / "src.old").exists()
    assert not (fwd / "no-graph").exists()
    assert (tmp_path / "mal" / "logs" / "fast-forward-paper.log").is_file()
    for u in (
        "mal-forward.slice",
        "mal-fast-forward-paper.service",
        "mal-fast-forward-paper-restart.service",
        "mal-fast-forward-paper-restart.timer",
    ):
        assert (tmp_path / "systemd" / u).is_file()
    # nothing started or enabled: only daemon-reload
    assert [ln.strip() for ln in calls.read_text().splitlines()] == ["daemon-reload"]


def test_observe_real_path(kit_repo, tmp_path):
    if os.geteuid() == 0:
        pytest.skip("observe installer refuses root")
    bindir, calls = _stubs(tmp_path)
    env = _env(tmp_path, bindir)
    py = tmp_path / "mal" / "fast-listener" / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    stale = tmp_path / "mal" / "eng-observe" / "observe" / "stale.py"
    stale.parent.mkdir(parents=True)
    stale.write_text("x")
    r = _obs(kit_repo["repo"], "--commit", kit_repo["good"], env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    eng = tmp_path / "mal" / "eng-observe"
    assert not stale.exists()
    assert (eng / "SOURCE_COMMIT").read_text().strip() == kit_repo["good"]
    for f in ("__init__.py", "__main__.py", "client.py", "regime.py", "attention.py", "trade_tape.py"):
        assert (eng / "observe" / f).is_file(), f  # the whole package, not four files
    assert (tmp_path / "home" / ".config" / "systemd" / "user" / "mal-fast-observe.service").is_file()
    assert [ln.strip() for ln in calls.read_text().splitlines()] == ["--user daemon-reload"]


# ---------------------------------------------------------------- reconnect counter

def test_observe_reconnects(tmp_path):
    h0 = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H")
    lines = [
        f"{h0}:01:02,123 INFO observe.client ws_connect url=wss://x/y",
        f"{h0}:02:02,123 WARNING observe.client ws_closed code=1006 reason=",
        f"{h0}:02:03,123 INFO observe.client ws_reconnect sleep_s=1.0",
        f"{h0}:03:03,123 WARNING observe.client ws_handshake_rejected status_code=413",
        f"{h0}:04:03,123 WARNING observe.client ws_error err=boom",
        f"{h0}:04:04,123 WARNING observe.client ws_connect_failed err=X",
        f"{h0}:05:04,123 INFO observe.client subscribed method=subscribeNewToken",
        "2020-01-01 00:00:00,000 INFO observe.client ws_connect url=old",
    ]
    log = tmp_path / "o.log"
    log.write_text("\n".join(lines) + "\n")
    r = subprocess.run([str(KIT / "observe-reconnects.sh"), str(log), "2"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    rows = r.stdout.strip().splitlines()
    assert len(rows) == 2  # header + one hour; the 2020 line is outside the window
    assert rows[1].split()[1:] == ["1", "1", "1", "1", "1"]
    assert "wss" not in r.stdout
    assert subprocess.run([str(KIT / "observe-reconnects.sh")], capture_output=True).returncode == 2


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyze not available")
@pytest.mark.parametrize(
    "name",
    [
        "mal-forward.slice",
        "mal-fast-forward-paper.service",
        "mal-fast-observe.service",
        "mal-fast-forward-paper-restart.service",
        "mal-fast-forward-paper-restart.timer",
        "mal-fast-runner-heartbeat.service",
        "mal-fast-runner-heartbeat.timer",
        "mal-fast-tip-follower.service",
    ],
)
def test_systemd_analyze_syntax(name):
    r = subprocess.run(["systemd-analyze", "verify", str(KIT / name)], capture_output=True, text=True)
    out = r.stdout + r.stderr
    # Paths and the ubuntu user do not exist on this host. Only syntax problems count.
    bad = re.compile(r"Unknown (key|section)|Failed to parse|Invalid|Unknown lvalue|Assignment outside", re.I)
    assert not [ln for ln in out.splitlines() if bad.search(ln)], out


def test_installer_probe_dryrun_only():
    """DEC-019: the installer ships the dry-run executor unit and its configs, never the live drop-in."""
    inst = (KIT / "install-fast-forward-paper.sh").read_text()
    assert 'PROBE_UNIT="mal-probe-executor.service"' in inst
    assert '"${TIP_UNIT}" "${PROBE_UNIT}"; do' in inst
    assert "scripts/mal-fast/probe-executor.json scripts/mal-fast/probe-executor-live.json" in inst
    code = "\n".join(l for l in inst.splitlines() if not l.lstrip().startswith("#"))
    assert "service.d" not in code and "LoadCredential" not in code and "live.conf" not in code
    assert "enable" not in "\n".join(l for l in inst.splitlines() if "PROBE" in l)
    unit = (KIT / "mal-probe-executor.service").read_text()
    assert "LoadCredential" not in unit
    assert "--config /var/lib/mal/fast-forward/src/scripts/mal-fast/probe-executor.json" in unit
    assert json.loads((KIT / "probe-executor.json").read_text())["mode"] == "dryrun"
