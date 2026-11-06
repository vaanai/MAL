"""Tests for scripts/research/h5v2-f1-launch.sh (DEC-031 Amendment 30, book h5v2-bx10-f1). Synthetic only: no tape, tip file, derived copy,
shadow dir or job output is read. The launcher's refusals that need no host state are exercised; the pins are checked against git and the DEC text."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LAUNCH = REPO / "scripts" / "research" / "h5v2-f1-launch.sh"
DEC = REPO / "DEC" / "DEC-031-forward-paper-arena.md"
NOTE = REPO / "EXP" / "EXP-024-note-am9-h5v2-bx10-f1.md"
JUDGE = REPO / "tools" / "s1t_smoke_judge.py"

SSTAR = "afa4971d9dd8b0bca2e73a758c22f4539f675d63"
BLOB_SHADOW = "7f859b12deef87573a7314e49e125b5121eaa645"
BLOB_TIPW = "05c5489a786dac4eb0696a74279a0f789ddb1deb"
JUDGE_SHA256 = "a91c29bf5ff70180cdbda8cad818569a45a14248608ae64b995845844d00689d"
BOT = "h5v2-bx10-f1"


def _text() -> str:
    return LAUNCH.read_text()


def _const(name: str) -> str:
    m = re.search(rf"^{name}=(\S+)", _text(), re.M)
    assert m, name
    return m.group(1)


def test_launcher_is_valid_posix_sh():
    sh = shutil.which("dash") or shutil.which("sh")
    assert sh
    subprocess.run([sh, "-n", str(LAUNCH)], check=True)


def test_no_bashisms():
    body = "\n".join(line for line in _text().splitlines() if not line.lstrip().startswith("#"))
    for bad in ("[[", "PIPESTATUS", "<<<", "function ", "$'", "local ", "set -o pipefail", "${!"):
        assert bad not in body, bad


def test_pins_in_launcher():
    assert _const("SSTAR") == SSTAR
    assert _const("BLOB_SHADOW") == BLOB_SHADOW
    assert _const("BLOB_TIPW") == BLOB_TIPW
    assert _const("JUDGE_SHA256") == JUDGE_SHA256
    assert _const("BOT") == BOT
    assert _const("E_S") == "1796083200"  # SUCC_END_MS / 1000 = 2026-12-01T00:00:00Z


def test_judge_sha256_on_disk_matches_pin():
    assert hashlib.sha256(JUDGE.read_bytes()).hexdigest() == JUDGE_SHA256


def _git(*args: str) -> str | None:
    r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


@pytest.mark.skipif(_git("cat-file", "-e", f"{SSTAR}^{{commit}}") is None, reason="frozen commit not in this clone")
def test_blobs_at_frozen_commit():
    assert _git("rev-parse", f"{SSTAR}:tools/h5_shadow.py") == BLOB_SHADOW
    assert _git("rev-parse", f"{SSTAR}:tools/h5_shadow_tip.py") == BLOB_TIPW


def test_flags_exact():
    t = _text()
    book = re.search(r"nice -n 5 \"\$PY\" -m tools\.h5_shadow_tip \\\n(.*?)>", t, re.S).group(1)
    assert "--fresh-forward ARENA --bx10-enable EXP-026" in book
    assert "--q-star-sol" not in book and "--smoke" not in book and "--tip-dir" not in book and "--h5-look2-observed" not in book
    smoke = re.search(r"nice -n 10 \"\$PY\" -m tools\.h5_shadow_tip --smoke \\\n(.*?)>", t, re.S).group(1)
    assert "--fresh-forward ARENA --q-star-sol 50" in smoke and "--bx10-enable" not in smoke
    assert t.count("MemoryMax=3500M -p MemorySwapMax=0") == 2


def test_out_dirs_under_fresh_forward_allowlist():
    t = _text()
    assert 'OUT="$HOME/data/arena/h5-smoke-v2f1-$STAMP"' in t and 'OUT="$HOME/data/arena/h5-v2f1-book-$STAMP"' in t
    assert re.search(r'\[ ! -e "\$OUT" \] \|\| \{ echo "h5v2-f1: REFUSED out dir exists"; cleanup; exit 9; \}', t)


def test_bot_id_prefixes():
    assert BOT.startswith("h5v2") and not BOT.startswith("h5v1")  # the scorer prices bx10 for non-h5v1 ids
    for other in ("h5v2-boostclock-a1", "h5v2-boostclock-a2", "h5v1-boostfloor-a1", "h5v1-boostfloor-a2", "h5v1q50-boostfloor-a1",
                  "h5v1q50-boostfloor-s1", "h5v1q50-boostfloor-s1t"):
        assert not other.startswith(BOT) and not BOT.startswith(other)


def test_dec_and_note_carry_the_pins():
    d = DEC.read_text()
    i = d.index("### Amendment 30 (DRAFT; H5 v2 forward on November pools, `h5v2-bx10-f1`")
    am = d[i:]
    for pin in (SSTAR, BLOB_SHADOW, BLOB_TIPW, JUDGE_SHA256[:8], "5368cc7b757a2f7a6cb064c71193b4e0cd740583f7348a60cb9a864401c190e0",
                "9c22239bd2423d76bee2241f1f88625504a64853f5f91dec2e09bc7a44fb0401", "1794096000000", "1796083200000", "n_61bjmqA3xPk33g",
                "1 - 0.95^4 = 18.5%", "scripts/research/h5v2-f1-launch.sh"):
        assert pin in am, pin
    n = NOTE.read_text()
    for pin in (SSTAR, BLOB_SHADOW, BLOB_TIPW, BOT):
        assert pin in n, pin


def _fake_bin(tmp_path: Path, host: str) -> dict:
    b = tmp_path / "bin"
    b.mkdir()
    h = b / "hostname"
    h.write_text(f"#!/bin/sh\necho {host}\n")
    h.chmod(h.stat().st_mode | stat.S_IXUSR)
    env = dict(os.environ)
    env["PATH"] = f"{b}:{env.get('PATH', '/usr/bin:/bin')}"
    return env


def test_refuses_usage(tmp_path):
    r = subprocess.run(["sh", str(LAUNCH), "nope"], capture_output=True, text=True, cwd=tmp_path, env=_fake_bin(tmp_path, "mal-research-0"))
    assert r.returncode == 2 and "REFUSED usage" in r.stdout


@pytest.mark.parametrize("mode", ["smoke", "book1", "book2"])
def test_refuses_wrong_host(tmp_path, mode):
    r = subprocess.run(["sh", str(LAUNCH), mode], capture_output=True, text=True, cwd=tmp_path, env=_fake_bin(tmp_path, "mal-fast-0"))
    assert r.returncode == 6 and "REFUSED host mal-fast-0" in r.stdout


def test_param_helper_and_smoke_clock_arithmetic(tmp_path):
    fn = re.search(r"^param\(\) \{.*\}$", _text(), re.M).group(0)
    clock = re.search(r"CL=\$\(\( \(NOW \+ 91800 \+ 86399\) / 86400 \* 86400 \)\)", _text()).group(0)
    s = tmp_path / "t.sh"
    s.write_text("set -eu\n" + fn + "\nA=1\necho \"[$(param A)][$(param B)][$(param C)]\"\n"
                 "for NOW in 1794004200 1794004201; do " + clock + "; echo $CL; done\n")
    env = dict(os.environ, MISCUSI_PARAM_C="x")
    out = subprocess.run(["sh", str(s)], capture_output=True, text=True, env=env, check=True).stdout.split()
    assert out[0] == "[1][][x]"
    assert out[1] == "1794096000"  # a smoke started at 2026-11-06T22:30:00Z keeps the 2026-11-08T00:00Z clock
    assert out[2] == "1794182400"  # one second later: 2026-11-09T00:00Z
