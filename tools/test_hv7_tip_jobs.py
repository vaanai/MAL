"""The two HV7 tip-proof job commands (scripts/ops/hv7-tip-equiv.sh, scripts/ops/hv7-tip-p0.sh): plain sh (MiScusi jobs run under sh), a memory-capped systemd scope, no pipe, no bash-ism, API
keys unset, a refusal on a dirty tree, and a usage refusal that starts nothing. The scripts are NOT run here (they need the F1 sha, a pinned notebook floor and real hours)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = {"equiv": ROOT / "scripts" / "ops" / "hv7-tip-equiv.sh", "p0": ROOT / "scripts" / "ops" / "hv7-tip-p0.sh"}
EXEC = {"equiv": "tools.hv7_tip_equiv", "p0": "tools.hv7_tip_p0_count"}


def code_lines(p: Path) -> str:
    return "\n".join(line for line in p.read_text().splitlines() if not line.lstrip().startswith("#"))


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_the_job_script_is_plain_sh_with_a_scope_cap_and_no_pipe_or_bashism(name):
    p = SCRIPTS[name]
    text, code = p.read_text(), code_lines(p)
    assert text.startswith("#!/bin/sh\n") and "set -eu" in code
    assert re.search(r"systemd-run --user --scope -q -p MemoryMax=\d+G -p MemorySwapMax=0 nice -n 19 ionice -c3 timeout \d+", code)
    assert f"-m {EXEC[name]}" in code and "/data/mal/venv/bin/python -s -B" in code
    for bad in ("[[", "PIPESTATUS", "<<<", "$((", "&>", "source ", "function ", "declare ", "local "):
        assert bad not in code, bad
    assert not re.search(r"(?<![|\\])\|(?!\|)", code), "a pipe in the job command (a wrapper crash blocks after-chains)"
    assert "unset ANTHROPIC_API_KEY OPENROUTER_API_KEY HELIUS_API_KEY" in code
    assert "git status --porcelain --untracked-files=no" in code and "dirty_tree" in code


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_the_job_script_refuses_without_its_arguments_and_starts_nothing(name):
    r = subprocess.run(["sh", str(SCRIPTS[name])], capture_output=True, text=True, cwd=str(ROOT), timeout=30)
    assert r.returncode != 0 and "usage" in r.stderr and r.stdout == ""


def test_the_p0_job_needs_the_notebook_ref_and_h0_and_the_equiv_job_needs_the_pinned_floor():
    for args, name in ((["2026-11-03,2026-11-04,2026-11-05"], "p0"), (["2026-11-03,2026-11-04,2026-11-05", "n_1"], "p0"), (["/data2/mal/scratch/hv7-tip-equiv/x"], "equiv")):
        r = subprocess.run(["sh", str(SCRIPTS[name]), *args], capture_output=True, text=True, cwd=str(ROOT), timeout=30)
        assert r.returncode != 0 and "usage" in r.stderr and r.stdout == ""


def test_the_p0_job_passes_h0_and_the_notebook_ref_to_the_counter_and_names_the_receiver_copy_only_in_comments():
    code = code_lines(SCRIPTS["p0"])
    assert '--notebook-ref "$REF"' in code and '--h0 "$H0"' in code and '--days "$DAYS"' in code
    assert "arena-tip-feed" not in code and "/var/lib/mal/sealed" not in code            # the counter's own refusals fix its directory; the script names no data path
    assert "2026-11-03,2026-11-04,2026-11-05" in SCRIPTS["p0"].read_text()                # the day-list example for T0 2026-11-07


def test_the_p0_job_carries_qps_pins_and_the_sizing_from_the_receivers_hour_sizes():
    text = SCRIPTS["p0"].read_text()
    for needle in ("2026-10-29T09 (1793264400000", "2026-11-03,2026-11-04,2026-11-05", "150/14 = 10.714285714285714", "8,961.0 MB", "mean 103.0 MB", "183 hour files", "18.8 GB compressed", "23.6 GB at p90", "740,168.6 an hour", "135.5M trades", "about 1.5 h", "the 144 screens",
                   "MemoryMax=16G", "timeout 21600", "NOT measured"):
        assert needle in text, needle
