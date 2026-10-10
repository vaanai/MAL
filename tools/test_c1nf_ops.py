"""Static tests for the C1-NF pinned installer and manifest script (DEC-026 item 11). No root, no install: the scripts are parsed, not run,
except the installer's live-config check, which is run on its own against good and bad configs."""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FAST = ROOT / "scripts/mal-fast"
INST = FAST / "install-c1nf-executor-pinned.sh"
MAKE = FAST / "make-c1nf-manifest.sh"
TEXT = INST.read_text()
# Files the executor PR (#530, claude/c1nf-executor-v2 @ 8ea24e6) ships: the module and the two JSON configs, nothing else. Everything else the
# installer names (the launcher, the base unit, both drop-ins, check-c1nf-unit.py and the rescue tool included) must already be in this tree.
FROM_EXECUTOR_PR = {"tools/c1nf_executor.py", "scripts/mal-fast/c1nf-executor-live.json", "scripts/mal-fast/c1nf-executor.json"}
OURS = ("scripts/mal-fast/c1nf-watch.py", "scripts/mal-fast/c1nf-daily-check.py", "scripts/mal-fast/check-c1nf-watch-unit.py",
        "scripts/mal-fast/mal-c1nf-watch.service", "scripts/mal-fast/mal-c1nf-watch.timer", "EXP/EXP-025-c1nf-part1-prereg.md",
        "scripts/mal-fast/c1nf_exec_launcher.py", "scripts/mal-fast/mal-c1nf-executor.service", "scripts/mal-fast/check-c1nf-unit.py",
        "scripts/mal-fast/mal-c1nf-executor-live-pinned.conf", "scripts/mal-fast/mal-c1nf-executor-shadow-feed.conf", "tools/c1nf_sell_and_close.py")


def var(name: str) -> str:
    """The same extraction make-c1nf-manifest.sh does: sed -n 's/^NAME="\\(.*\\)"$/\\1/p'."""
    m = re.findall(rf'^{name}="(.*)"$', TEXT, re.M)
    assert len(m) == 1, name
    return m[0]


def paths() -> list[str]:
    out = var("MODULES").split()
    out += [e.split(":", 1)[0] for e in var("EXTRA").split()]
    return out + [var("BASE_UNIT_SRC"), var("BASE_UNIT_CHECK")]


def test_scripts_parse():
    for f in (INST, MAKE):
        assert subprocess.run(["bash", "-n", str(f)], capture_output=True).returncode == 0, f


def test_manifest_script_reads_the_c1nf_installer():
    m = MAKE.read_text()
    assert "INST=scripts/mal-fast/install-c1nf-executor-pinned.sh" in m and "h5" not in m.replace("make-h5-manifest.sh", "")


def test_every_path_exists_or_comes_from_the_executor_pr():
    missing = {p for p in paths() if not (ROOT / p).is_file()}
    assert missing <= FROM_EXECUTOR_PR, sorted(missing - FROM_EXECUTOR_PR)
    for ours in OURS:
        assert ours in paths() and (ROOT / ours).is_file(), ours
    assert len(paths()) == len(set(paths()))


def test_installer_allowlist_checks_pass_on_the_files_it_installs():
    for flag, src in (("--base", var("BASE_UNIT_SRC")), ("--dropin", var("DROPIN_SRC"))):
        r = subprocess.run([sys.executable, "-I", str(ROOT / var("BASE_UNIT_CHECK")), flag, str(ROOT / src)], capture_output=True, text=True)
        assert r.returncode == 0, (flag, r.stderr)


def test_installed_names_match_what_the_daily_check_reads():
    spec = importlib.util.spec_from_file_location("c1nf_daily_check", FAST / "c1nf-daily-check.py")
    dc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dc)
    names = {e.split(":", 1)[1] for e in var("EXTRA").split()} | {"mal-c1nf-executor.service", "check-c1nf-unit.py"}
    for want in (f"{dc.C1NF_UNIT}.service", f"{dc.C1NF_UNIT}-live-pinned.conf", dc.WATCH_SERVICE, dc.WATCH_TIMER, dc.UNIT_CHECKER,
                 dc.LIVE_CONFIG.rsplit("/", 1)[1], "c1nf-watch.py", "c1nf-daily-check.py"):
        assert want in names, want
    assert re.search(r"^DEST=/usr/local/lib/mal-c1nf-exec$", TEXT, re.M) and dc.PINNED_ROOT == "/usr/local/lib/mal-c1nf-exec"
    assert re.search(r"^C1NF_ETC=/etc/mal-c1nf$", TEXT, re.M) and dc.C1NF_ETC == "/etc/mal-c1nf"


def test_installer_touches_nothing_of_h5_and_never_creates_the_gate():
    code = "\n".join(ln for ln in TEXT.splitlines() if not ln.lstrip().startswith("#"))
    for bad in ("mal-h5-exec", "mal-h5-executor", "/etc/mal-h5", "/var/lib/mal-live/h5", "mal-h5-watch", "mal-probe-executor", "probe-wallet"):
        assert bad not in code, bad
    assert not re.search(r"(touch|install|cp|ln)[^\n]*LIVE_OK", code)
    assert not re.search(r"(echo|printf)[^\n]*>[^\n]*TIER", code)
    assert "--watch-service" in code and '"$TMP/$WATCH_UNIT_CHECK" --watch-service' in code


def _config_check() -> str:
    m = re.search(r"/usr/bin/python3 -I -S -c '\n(.*?)\n' \"\$TMP/\$LIVE_CFG_SRC\"", TEXT, re.S)
    assert m
    return m.group(1)


GOOD = {"mode": "live", "state_dir": "/var/lib/mal-live/c1nf", "stake_lamports": 50_000_000, "buy_priority_lamports": 505_000, "end_ms": 1_792_801_800_000,
        "jito_enabled": False, "jito_tip_lamports": 0, "entry_tolerance_bps": 1500, "feed_heartbeat_max_age_ms": 150_000}  # v2 @ 8ea24e6


@pytest.mark.parametrize("change,ok", [({}, True), ({"stake_lamports": 100_000_000}, False), ({"buy_priority_lamports": 55_000}, False),
                                       ({"end_ms": None}, False), ({"state_dir": "/var/lib/mal-live/h5"}, False), ({"mode": "dry"}, False),
                                       ({"jito_enabled": True}, False), ({"jito_enabled": None}, False), ({"jito_enabled": 0}, False),
                                       ({"jito_tip_lamports": 1_000}, False), ({"jito_tip_lamports": None}, False),
                                       ({"jito_tip_lamports": False}, False), ({"entry_tolerance_bps": 2_000}, False),
                                       ({"entry_tolerance_bps": 1_000}, True), ({"entry_tolerance_bps": None}, True),
                                       ({"feed_heartbeat_max_age_ms": 300_000}, False), ({"feed_heartbeat_max_age_ms": 60_000}, True),
                                       ({"feed_heartbeat_max_age_ms": 150_000.0}, False)])
def test_installer_live_config_check(tmp_path, change, ok):
    cfg = {**GOOD, **change}
    f = tmp_path / "c.json"
    f.write_text(json.dumps({k: v for k, v in cfg.items() if v is not None}))
    r = subprocess.run([sys.executable, "-I", "-S", "-c", _config_check(), str(f)], capture_output=True, text=True)
    assert (r.returncode == 0) is ok, r.stderr


def _closure(starts: list[str]) -> set[str]:
    seen: set[str] = set()
    todo = list(starts)
    while todo:
        rel = todo.pop()
        if rel in seen or not (ROOT / rel).is_file():
            continue
        seen.add(rel)
        for node in ast.walk(ast.parse((ROOT / rel).read_text())):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names if a.name.startswith("tools.")]
            elif isinstance(node, ast.ImportFrom):
                if node.level == 1 or node.module == "tools":
                    names = [f"tools.{(node.module + '.') if node.level == 1 and node.module else ''}{a.name}" for a in node.names]
                    if node.level == 1 and node.module:
                        names = [f"tools.{node.module}"]
                elif node.module and node.module.startswith("tools."):
                    names = [node.module]
            for n in names:
                cand = n.replace(".", "/") + ".py"
                if (ROOT / cand).is_file():
                    todo.append(cand)
    return seen | {"tools/__init__.py"}


def test_modules_are_the_import_closure_once_the_executor_is_here():
    if not (ROOT / "tools/c1nf_executor.py").is_file():
        pytest.skip("tools/c1nf_executor.py is not on this branch yet (executor PR claude/c1nf-executor-v2)")
    assert set(var("MODULES").split()) == _closure(["tools/c1nf_executor.py", "tools/c1nf_sell_and_close.py"])


def test_smoke_import_is_the_c1nf_modules():
    imports = re.findall(r'import (tools\.\S+, tools\.\S+)" "\$', TEXT)
    assert imports == ["tools.c1nf_executor, tools.c1nf_sell_and_close"] * 2, imports


def test_rescue_tool_closure_is_in_modules_without_the_executor():
    # on this branch alone: everything the rescue tool imports (h5_sell_and_close and the probe modules) is installed
    assert _closure(["tools/c1nf_sell_and_close.py"]) - {"tools/c1nf_executor.py"} <= set(var("MODULES").split())
