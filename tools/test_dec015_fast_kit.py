"""Static checks on the DEC-015 fast-host forward-paper kit (scripts/mal-fast/). Nothing here
installs or starts anything."""

from __future__ import annotations

import datetime as dt
import json
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
    assert raw["tape_dir"] == "/var/lib/mal/sealed/fast-trades-live"
    assert raw["creates_dir"] == "/var/lib/mal/sealed/fast-observe"
    assert raw["output_dir"] == "/var/lib/mal/paper/fast-forward-paper"
    assert "graph_dir" not in raw and "attention_dir" not in raw
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
    import hashlib

    assert hashlib.md5((EXP012 / name).read_bytes()).hexdigest() == _frozen()[name]


def test_forward_unit_and_slice():
    unit = (KIT / "mal-fast-forward-paper.service").read_text()
    assert re.search(r"^Slice=mal-forward\.slice$", unit, re.M)
    assert re.search(r"^User=ubuntu$", unit, re.M)
    assert re.search(r"^Restart=on-failure$", unit, re.M)
    assert re.search(r"^Nice=19$", unit, re.M)
    for var in ("OMP", "OPENBLAS", "MKL", "NUMEXPR"):
        assert f"Environment={var}_NUM_THREADS=1" in unit
    assert "/var/lib/mal/fast-forward/config.json" in unit
    sl = (KIT / "mal-forward.slice").read_text()
    assert re.search(r"^MemoryHigh=5G$", sl, re.M)
    assert re.search(r"^MemoryMax=6G$", sl, re.M)
    # the slice is a system slice: no Slice= parent line
    assert not re.search(r"^Slice=", sl, re.M)


def test_observe_unit():
    unit = (KIT / "mal-fast-observe.service").read_text()
    assert re.search(r"^MemoryMax=1G$", unit, re.M)
    assert re.search(r"^Restart=always$", unit, re.M)
    assert "-m observe" in unit
    assert "/var/lib/mal/sealed/fast-observe" in unit
    assert not re.search(r"^User=", unit, re.M)  # user unit


def test_forward_launcher_is_paper_only():
    text = (KIT / "fast-forward-paper.sh").read_text()
    assert "tools.forward_paper serve" in text
    assert "/var/lib/mal/fast-forward" in text
    assert not re.search(r"(?i)helius|private|keypair|wallet", text)


@pytest.mark.parametrize("path", INSTALLERS, ids=lambda p: p.name)
def test_installers_have_dry_run_and_never_start_or_enable(path):
    text = path.read_text()
    assert "--dry-run" in text
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        # Printing the manual next step is allowed; executing it is not.
        if line.startswith(("log ", "echo ", "printf ")):
            continue
        assert not re.search(r"systemctl\b.*\b(start|enable|restart|reload-or-restart|--now)\b", line), line
        assert not re.search(r"\bsystemctl\b.*\bunmask\b", line), line


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_bash_syntax(path):
    subprocess.run(["bash", "-n", str(path)], check=True)


def test_observe_installer_dry_run_runs():
    r = subprocess.run([str(KIT / "install-fast-observe.sh"), "--dry-run"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "DRY-RUN: install" in r.stdout


def test_forward_installer_files_only_dry_run():
    r = subprocess.run(
        [str(KIT / "install-fast-forward-paper.sh"), "--dry-run", "--files-only"], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    assert "md5 ok model.txt" in r.stdout
    assert "mal-forward.slice" not in r.stdout
    assert "daemon-reload" not in r.stdout


@pytest.mark.skipif(dt.datetime.now(dt.timezone.utc) >= NOT_BEFORE, reason="fence date has passed")
def test_forward_installer_refuses_before_kill_review():
    r = subprocess.run([str(KIT / "install-fast-forward-paper.sh"), "--dry-run"], capture_output=True, text=True)
    assert r.returncode != 0
    assert "refusing before 2026-10-05T05:00:00Z" in r.stderr


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyze not available")
@pytest.mark.parametrize("name", ["mal-forward.slice", "mal-fast-forward-paper.service", "mal-fast-observe.service"])
def test_systemd_analyze_syntax(name):
    r = subprocess.run(["systemd-analyze", "verify", str(KIT / name)], capture_output=True, text=True)
    out = r.stdout + r.stderr
    # Paths and the ubuntu user do not exist on this host. Only syntax problems count.
    bad = re.compile(r"Unknown (key|section)|Failed to parse|Invalid|Unknown lvalue|Assignment outside", re.I)
    assert not [ln for ln in out.splitlines() if bad.search(ln)], out
