"""r4bt_keeper_check: synthetic fixtures only, built in tmp dirs (no real keeper dir, tape, declog or sealed path is opened). Nothing here says any book makes money."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools import r4b_preview as V
from tools import r4bt_gate as G
from tools import r4bt_keeper_check as K

HOURS = ["2026-11-15T22", "2026-11-15T23", "2026-11-16T00", "2026-11-16T01"]
REPO = Path(__file__).resolve().parent.parent


def name(h):
    return f"tape-{h}.jsonl.zst"


def keeper(tmp_path, hours=HOURS):
    d = tmp_path / "keep"
    d.mkdir()
    rows = []
    for i, h in enumerate(hours):
        f = d / name(h)
        f.write_bytes((f"synthetic-{h}-".encode()) * (50 + i))
        st = f.stat()
        rows.append({"h": h, "name": name(h), "zst_bytes": st.st_size, "zst_sha": hashlib.sha256(f.read_bytes()).hexdigest(), "dev": st.st_dev, "inode": st.st_ino,
                     "linked_utc": "2026-11-16T00:00:00Z"})
    write_rows(d, rows)
    return d, rows


def write_rows(d, rows):
    (d / K.KEEPER_HOURS).write_text("".join(json.dumps(r) + "\n" for r in rows))


def run(d, a=HOURS[0], b=HOURS[-1]):
    return K.main(["--keeper-dir", str(d), "--from-hour", a, "--to-hour", b])


def test_all_equal_exit_0_and_prints_counts(tmp_path, capsys):
    d, rows = keeper(tmp_path)
    assert run(d) == 0
    out = capsys.readouterr().out
    assert "hours checked 4" in out and HOURS[0] in out and HOURS[-1] in out
    assert not any(r["zst_sha"] in out for r in rows)


def test_range_is_inclusive(tmp_path, capsys):
    d, _ = keeper(tmp_path)
    assert run(d, HOURS[1], HOURS[1]) == 0
    assert "hours checked 1" in capsys.readouterr().out


def test_missing_row_is_refusal_2(tmp_path, capsys):
    d, rows = keeper(tmp_path)
    write_rows(d, [r for r in rows if r["h"] != HOURS[2]])
    assert run(d) == 2
    assert "missing_row" in capsys.readouterr().err


def test_missing_file_is_refusal_2(tmp_path, capsys):
    d, _ = keeper(tmp_path)
    (d / name(HOURS[1])).unlink()
    assert run(d) == 2
    assert "missing_file" in capsys.readouterr().err


def test_hour_outside_the_rows_is_refusal_2(tmp_path):
    d, _ = keeper(tmp_path)
    assert run(d, HOURS[0], "2026-11-16T02") == 2


def test_size_mismatch_exit_3(tmp_path, capsys):
    d, rows = keeper(tmp_path)
    rows[1]["zst_bytes"] += 1
    write_rows(d, rows)
    assert run(d) == 3
    assert f"{HOURS[1]}: size_mismatch" in capsys.readouterr().err


def test_sha_mismatch_same_size_exit_3_first_failing_hour(tmp_path, capsys):
    d, rows = keeper(tmp_path)
    f = d / name(HOURS[2])
    b = bytearray(f.read_bytes())
    b[0] ^= 1
    with open(f, "r+b") as fh:  # in place: same inode, same size
        fh.write(bytes(b))
    rows[3]["zst_sha"] = "0" * 64  # a later hour also fails; the first one is reported
    write_rows(d, rows)
    assert run(d) == 3
    assert f"{HOURS[2]}: sha_mismatch" in capsys.readouterr().err


def test_dev_or_inode_mismatch_exit_3(tmp_path, capsys):
    d, rows = keeper(tmp_path)
    rows[0]["inode"] += 1
    write_rows(d, rows)
    assert run(d) == 3
    assert "dev_inode_mismatch" in capsys.readouterr().err
    rows[0]["inode"] -= 1
    rows[0]["dev"] += 1
    write_rows(d, rows)
    assert run(d) == 3


def test_replaced_file_new_inode_exit_3(tmp_path):
    d, rows = keeper(tmp_path)
    f = d / name(HOURS[1])
    tmp = d / "new.part"
    tmp.write_bytes(f.read_bytes())  # same bytes, created while the old file still exists: a new inode, not the receiver's hard link
    os.replace(str(tmp), str(f))
    assert f.stat().st_ino != rows[1]["inode"]
    assert run(d) == 3


def test_duplicate_rows_differ_exit_3_identical_pass(tmp_path, capsys):
    d, rows = keeper(tmp_path)
    write_rows(d, rows + [rows[1]])  # identical duplicate: pass
    assert run(d) == 0
    dup = dict(rows[1], zst_sha="f" * 64)
    write_rows(d, rows + [dup])
    assert run(d) == 3
    assert f"{HOURS[1]}: duplicate_rows_differ" in capsys.readouterr().err
    dup = dict(rows[1], linked_utc="2026-11-17T00:00:00Z")  # only a non-compared field differs: pass
    write_rows(d, rows + [dup])
    assert run(d) == 0


def test_symlinked_hour_is_refused_not_followed(tmp_path):
    d, _ = keeper(tmp_path)
    f = d / name(HOURS[1])
    t = tmp_path / "elsewhere"
    shutil.move(str(f), str(t))
    os.symlink(str(t), str(f))
    assert run(d) == 2


def test_torn_or_invalid_lines_are_skipped_and_a_torn_needed_row_is_a_missing_row(tmp_path):
    d, rows = keeper(tmp_path)
    p = d / K.KEEPER_HOURS
    p.write_text(p.read_text() + '{"h": "x", "name": "tape-2026-11-16T0')
    assert run(d) == 0
    write_rows(d, rows[:3])
    with open(p, "a") as fh:
        fh.write('{"h": "2026-11-16T01", "name": "tape-2026-11-16T01.jsonl.z')
    assert run(d) == 2


def test_no_rows_file_is_refusal_2(tmp_path):
    d, _ = keeper(tmp_path)
    (d / K.KEEPER_HOURS).unlink()
    assert run(d) == 2


@pytest.mark.parametrize("a,b", [("2026-11-15", "2026-11-16T01"), ("2026-11-16T01", "2026-11-15T22"), ("2026-13-15T22", "2026-11-16T01"), ("x", "y")])
def test_bad_hour_arguments_are_refusal_2(tmp_path, a, b):
    d, _ = keeper(tmp_path)
    assert run(d, a, b) == 2


def test_missing_cli_argument_is_exit_2(tmp_path):
    assert K.main(["--keeper-dir", str(tmp_path)]) == 2


@pytest.mark.parametrize("p", ["/data/mal/arena/x", "/var/lib/mal/sealed/fast-trades-tip", "/data/mal/r4b", "/data/mal/blocks/forward-1016", "/data/mal/meta-capture"])
def test_closed_roots_are_refused_without_opening(p, capsys):
    assert K.main(["--keeper-dir", p, "--from-hour", HOURS[0], "--to-hour", HOURS[1]]) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_the_real_keeper_dir_is_allowed_by_the_gate_but_refused_under_pytest():
    assert str(G.check_path(K.REAL_KEEPER_DIR, "k")) == K.REAL_KEEPER_DIR  # the gate does not close it (a path check only, nothing opened)
    assert K.main(["--keeper-dir", K.REAL_KEEPER_DIR, "--from-hour", HOURS[0], "--to-hour", HOURS[1]]) == 2


def test_help_says_inclusive():
    r = subprocess.run([sys.executable, "-m", "tools.r4bt_keeper_check", "--help"], capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0 and r.stdout.count("inclusive") == 2


def test_module_does_not_import_the_preview_or_the_pricer():
    code = "import sys; import tools.r4bt_keeper_check; print('tools.r4b_preview' in sys.modules, 'tools.r4bt_price' in sys.modules)"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO))
    assert r.stdout.split() == ["False", "False"], r.stderr


def _preview_verdict(d, hours):
    env = V.Env(d / "dl", d, d / "out", d / "wk")
    try:
        V.check_keeper_hours(env, [d / name(h) for h in hours])
        return 0
    except (V.Untrusted, G.Refused):
        return 3


@pytest.mark.parametrize("mutation", ["good", "size", "sha", "no_row", "dup_differ", "dup_same"])
def test_same_verdict_as_r4b_preview_check_on_a_shared_fixture(tmp_path, mutation):
    hours = ["2026-11-15T22", "2026-11-15T23", "2026-11-16T00"]
    d, rows = keeper(tmp_path, hours)
    if mutation == "size":
        rows[1]["zst_bytes"] += 1
    elif mutation == "sha":
        rows[2]["zst_sha"] = "1" * 64
    elif mutation == "no_row":
        rows = rows[:1] + rows[2:]
    elif mutation == "dup_differ":
        rows = rows + [dict(rows[0], zst_sha="2" * 64)]
    elif mutation == "dup_same":
        rows = rows + [rows[0]]
    write_rows(d, rows)
    want = _preview_verdict(d, hours)  # the preview: 0 trusted, 3 refused
    got = run(d, hours[0], hours[-1])
    assert (0 if got == 0 else 3) == want
    assert got == {"good": 0, "size": 3, "sha": 3, "no_row": 2, "dup_differ": 3, "dup_same": 0}[mutation]
