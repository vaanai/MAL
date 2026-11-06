"""Tests for scripts/research/h5v2-f1-launch.sh (DEC-031 Amendment 29, book h5v2-bx10-f1). Synthetic only: no tape, tip file, derived copy,
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
    i = d.index("### Amendment 29 (DRAFT; H5 v2 forward on November pools, `h5v2-bx10-f1`")
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


def _int(name: str) -> int:
    return int(_const(name))


def test_param_helper_and_smoke_clock_arithmetic(tmp_path):
    fn = re.search(r"^param\(\) \{.*\}$", _text(), re.M).group(0)
    assert "CL=$(( (NOW + SMOKE_SLIP + 86399) / 86400 * 86400 ))" in _text()
    s = tmp_path / "t.sh"
    s.write_text("set -eu\n" + fn + "\nA=1\necho \"[$(param A)][$(param B)][$(param C)]\"\nSMOKE_SLIP=" + _const("SMOKE_SLIP") + "\n"
                 "for NOW in 1794000600 1794000601; do CL=$(( (NOW + SMOKE_SLIP + 86399) / 86400 * 86400 )); echo $CL; done\n")
    env = dict(os.environ, MISCUSI_PARAM_C="x")
    out = subprocess.run(["sh", str(s)], capture_output=True, text=True, env=env, check=True).stdout.split()
    assert out[0] == "[1][][x]"
    assert out[1] == "1794096000"  # a smoke started at 2026-11-06T21:30:00Z keeps the 2026-11-08T00:00Z clock
    assert out[2] == "1794182400"  # one second later: 2026-11-09T00:00Z


def test_timeline_latest_smoke_fits_book1_submit():
    latest_smoke = 1794000600  # 2026-11-06T21:30:00Z, Am.29 item 8 (d)
    judge_s = 600
    clock = _int("CLOCK_MIN")
    book1_latest_submit = clock - 4200  # 2026-11-07T22:50:00Z
    assert _int("SMOKE_S") >= 86400 + 3600  # DURATION >= 24 h with margin
    assert latest_smoke + _int("SMOKE_S") + judge_s <= book1_latest_submit <= clock - 3600
    assert _int("SMOKE_SLIP") == _int("SMOKE_S") + judge_s + 4800  # 25 h + 10 min + (22:50Z submit .. 00:00Z clock), = 26 h 30 min
    assert latest_smoke + _int("SMOKE_SLIP") == clock


def test_book_instants():
    assert _int("CLOCK_MAX") == 1795132800 and _int("B1_STOP") == 1795294800
    assert _int("CLOCK_MAX") - 3900 < _int("B1_STOP")
    assert _int("B2_EARLIEST") == _int("B1_STOP") - 300 and _int("B2_LATEST") == _int("B1_STOP") + 900
    assert _int("B2_STOP") == _int("E_S") + 900


def _run(tmp_path, mode, **env_extra):
    env = _fake_bin(tmp_path, "mal-research-0")
    for k in ("REG_NOTEBOOK", "SMOKE_PASS_JOB", "FEEDBAR_NOTE", "A3_LOOP_JOB", "CLOCK_DAY"):
        env.pop(k, None)
        env.pop("MISCUSI_PARAM_" + k, None)
    env.update(env_extra)
    return subprocess.run(["sh", str(LAUNCH), mode], capture_output=True, text=True, cwd=tmp_path, env=env)


FOUR = {"REG_NOTEBOOK": "n_x", "SMOKE_PASS_JOB": "1", "FEEDBAR_NOTE": "n_y", "A3_LOOP_JOB": "2"}


@pytest.mark.parametrize("missing", sorted(FOUR))
def test_book1_refuses_missing_confirmation(tmp_path, missing):
    env = {k: v for k, v in FOUR.items() if k != missing}
    r = _run(tmp_path, "book1", **env)
    assert r.returncode == 2 and f"confirmation {missing} is missing" in r.stdout


@pytest.mark.parametrize("day,code,msg", [("2026-11-22", 8, "clock after 2026-11-20"), ("2026-11-21", 8, "clock after 2026-11-20"),
                                          ("2026-11-07", 8, "clock before 2026-11-08"), ("2026-12-01", 2, "REFUSED CLOCK_DAY")])
def test_book1_refuses_clock_day_out_of_range(tmp_path, day, code, msg):
    r = _run(tmp_path, "book1", CLOCK_DAY=day, **FOUR)
    assert r.returncode == code and msg in r.stdout


def test_book2_refuses_before_hand_off(tmp_path):
    r = _run(tmp_path, "book2")  # today is before 2026-11-21T20:55Z
    assert r.returncode == 8 and "REFUSED book2 before 2026-11-21T20:55:00Z" in r.stdout


def test_dec_pins_the_launcher_sha256():
    d = DEC.read_text()
    am = d[d.index("### Amendment 29 (DRAFT; H5 v2 forward on November pools"):]
    assert hashlib.sha256(LAUNCH.read_bytes()).hexdigest() in am
    assert "Amendment 30" not in am and "17:4x" not in am and "2026-11-06T17:16:06Z" in am
