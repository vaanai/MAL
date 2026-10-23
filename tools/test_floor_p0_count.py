"""Tests for tools/floor_p0_count.py on a synthetic tape. Nothing here says FLOOR makes money."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import arena_pool_table as PT
from tools import arena_rule_l1 as L1
from tools import floor_p0_count as F
from tools.arena_rules_testkit import write_tape

HOUR = 3_600_000
DAY = 86_400_000
H0 = 1_792_000_000_000 // DAY * DAY
WSOL = PT.WSOL
V = 17_600_000_000
SOL = 10**9


def row(t, pool, side="buy", q=10 * SOL, sol=200_000_000, v=V, mint=None, quote=WSOL, n=0):
    return {"v": 1, "type": "trade", "prog": "pumpswap", "venue": "pumpswap", "slot": 1000 + (t - H0) // 400, "t_recv_ms": t, "signature": f"s{t}-{pool}-{n}", "event_index": 0,
            "pool": pool, "mint": mint or "M" + pool, "trader": "tr", "side": side, "sol_lamports": sol, "token_raw": sol * 1000, "quote_reserve": q, "base_reserve": 10**12,
            "virtual_quote_reserves": v, "quote_mint": quote, "event_ts": t // 1000}


def make_rows(days=3, signals=()):
    rows = [row(H0 + 1000, "X"),
            {"type": "create", "t_recv_ms": H0 + HOUR - 1000, "mint": "MP", "is_mayhem_mode": False, "signature": "c", "event_index": 0},
            {"type": "complete", "t_recv_ms": H0 + HOUR, "mint": "MP", "event_ts": (H0 + HOUR) // 1000, "signature": "k", "event_index": 0}]
    t = H0 + HOUR + 10_000
    while t <= H0 + days * DAY:
        rows.append(row(t, "P", "buy" if (t // 1800000) % 2 else "sell"))
        t += 1_800_000
    for off in signals:
        rows.append(row(H0 + off, "P", "sell", q=600_000_000, n=off))
    rows.append({"type": "gap", "t_recv_ms": H0 + DAY + 5 * HOUR, "kind": "x", "prog": "pumpswap", "from_ms": H0 + DAY + 5 * HOUR - 1000, "to_ms": H0 + DAY + 5 * HOUR, "data_loss": True})
    return sorted(rows, key=lambda r: r["t_recv_ms"])


def args(tape, days, **kw):
    return SimpleNamespace(tape_dir=str(tape), days=days, table_from=None, **kw)


def mods():
    return SimpleNamespace(PT=PT, L1=L1, repo=str(Path(__file__).resolve().parent.parent))


def count(tmp_path, signals, days):
    write_tape(tmp_path / "tape", make_rows(3, signals))
    return F.run(args(tmp_path / "tape", days), mods(), "x" * 40)


def test_thinning_bounds_and_day_counts(tmp_path):
    # day 1 (index 1): three signals (two 1 s apart); day 2: one more >24 h after the first
    rows = count(tmp_path, (DAY + 20 * HOUR, DAY + 20 * HOUR + 600_000, DAY + 20 * HOUR + 601_000, 2 * DAY + 22 * HOUR), ["2026-10-15", "2026-10-16"])
    d1, d2, v = rows[1], rows[2], rows[-1]
    assert d1["signals"] == 3 and d1["att_lo"] == 1 and d1["att_hi"] == 2
    assert d2["signals"] == 1 and d2["att_lo"] == 1 and d2["att_hi"] == 1  # lock of the day-1 signal (24 h + 60 s) has ended
    assert v["att_lo_per_day"] == [1, 1] and v["floor_pass_att_lo"] is False  # mean 1.0 < 2.0
    assert d1["gaps"]["pumpswap_data_loss_s_incl_all"] == 1.0
    assert "MP" not in json.dumps(rows[1:])


def test_floor_pass_and_h(tmp_path):
    write_tape(tmp_path / "tape", make_rows(3, ()))
    rows = F.run(args(tmp_path / "tape", ["2026-10-15", "2026-10-16"]), mods(), "x" * 40)
    assert rows[-1]["att_lo_per_day"] == [0, 0] and rows[-1]["floor_pass_att_lo"] is False and rows[-1]["H_days_att_lo"] is None


def test_missing_hour_fails_day(tmp_path):
    rows = count(tmp_path, (), ["2026-10-17"])  # the tape ends on day 3 only partly
    assert rows[1]["status"] == "FAILED" and rows[1]["reason"].startswith("tape_missing_hours")


def test_render_hash_and_pure_function_of_input(tmp_path):
    a = F.render(count(tmp_path, (DAY + 20 * HOUR,), ["2026-10-15"]))
    b = F.render(F.run(args(tmp_path / "tape", ["2026-10-15"]), mods(), "x" * 40))
    assert a == b and json.loads(a.strip().split("\n")[-1])["kind"] == "sha256"


def test_project_drops_unlisted_fields():
    r = F.project({"type": "trade", "t_recv_ms": 5, "trader": "w", "creator": "c", "name": "n", "mint": "m"})
    assert r == {"type": "trade", "t_recv_ms": 5, "mint": "m"} and F.project({"type": "hb", "t_recv_ms": 1}) is None


def test_verify_repo_refuses_wrong_sha_and_dirty(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    subprocess.run(["git", "-C", str(r), "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    head = subprocess.run(["git", "-C", str(r), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert F.verify_repo(str(r), head) == head
    with pytest.raises(F.RepoError):
        F.verify_repo(str(r), "0" * 40)
    (r / "f").write_text("x")
    with pytest.raises(F.RepoError):
        F.verify_repo(str(r), head)


def test_main_exit_codes(tmp_path, capsys):
    assert F.main(["--repo", str(tmp_path), "--days", "bad"]) == 2
    assert F.main(["--repo", str(tmp_path), "--days", "2026-10-15"]) == 2  # not the pinned repo
    assert F.main(["--repo", str(tmp_path), "--days", "2026-10-15", "--expect-sha", "1" * 40]) == 2  # override needs MAL_ARENA_TEST
