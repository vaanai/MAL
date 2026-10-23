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


def make_multi(pools, ndays, signals, create_extra=None):
    """pools: list of names; signals: {pool: [offset_ms from H0]}. Every pool completes at H0+1h and prints every 30 min."""
    rows = [row(H0 + 1000, "X")]
    for p in pools:
        c = {"type": "create", "t_recv_ms": H0 + HOUR - 1000, "mint": "M" + p, "is_mayhem_mode": False, "signature": "c" + p, "event_index": 0}
        if create_extra:
            c.update(create_extra)
        rows += [c, {"type": "complete", "t_recv_ms": H0 + HOUR, "mint": "M" + p, "event_ts": (H0 + HOUR) // 1000, "signature": "k" + p, "event_index": 0}]
        t = H0 + HOUR + 10_000
        while t <= H0 + ndays * DAY:
            rows.append(row(t, p, "buy" if (t // 1800000) % 2 else "sell"))
            t += 1_800_000
        for off in signals.get(p, ()):
            rows.append(row(H0 + off, p, "sell", q=600_000_000, n=off))
    return sorted(rows, key=lambda r: r["t_recv_ms"])


def dd(i):
    from datetime import datetime, timedelta, timezone
    return (datetime.fromtimestamp(H0 / 1000, timezone.utc) + timedelta(days=i)).strftime("%Y-%m-%d")


def run_multi(tmp_path, pools, ndays, signals, sel, **kw):
    write_tape(tmp_path / "tape", make_multi(pools, ndays, signals, **kw))
    return F.run(args(tmp_path / "tape", [dd(i) for i in sel]), mods(), "x" * 40)


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


def test_k1_att_lo_independent_of_selected_days(tmp_path):
    sig = {"P": [DAY + 20 * HOUR, 2 * DAY + 5 * HOUR]}  # the second signal is locked by the first (9 h later)
    out = {}
    for sel in ([2], [1, 2], [0, 2]):
        rows = run_multi(tmp_path / ("s" + "".join(map(str, sel))), ["P"], 3, sig, sel)
        out[tuple(sel)] = {r["day"]: r["att_lo"] for r in rows if r["kind"] == "day"}
        assert rows[0]["lock_warmup_from"]
    assert out[(2,)][dd(2)] == out[(1, 2)][dd(2)] == out[(0, 2)][dd(2)] == 0
    assert out[(1, 2)][dd(1)] == 1


def test_k1_carried_lock_signals_reported(tmp_path):
    rows = run_multi(tmp_path, ["P"], 3, {"P": [DAY + 20 * HOUR]}, [2])
    assert rows[1]["carried_lock_signals"] == 1


def test_k2_pumpfun_create_named_trade_does_not_drop_the_pool(tmp_path):
    rows = run_multi(tmp_path, ["P"], 3, {"P": [DAY + 20 * HOUR]}, [1], create_extra={"prog": "pumpfun", "name": "trade", "symbol": "trade"})
    assert rows[1]["att_lo"] == 1


def test_k3_bad_line_fails_day(tmp_path):
    write_tape(tmp_path / "tape", make_multi(["P"], 3, {"P": [DAY + 20 * HOUR]}))
    f = sorted((tmp_path / "tape").iterdir())[DAY // HOUR + 5]
    f.write_text(f.read_text() + '{"type":"trade","t_recv_ms":12\n')
    rows = F.run(args(tmp_path / "tape", [dd(1)]), mods(), "x" * 40)
    assert rows[1]["status"] == "FAILED" and rows[1]["reason"] == "bad_lines=1" and rows[1]["bad_lines"] == 1


def pass_signals(extra=None, skip_day=None):
    sig = {}
    for p, base in (("P", 51), ("Q", 63), ("R", 55), ("S", 67)):
        sig[p] = [(base + 25 * k) * HOUR for k in range(3)]
    return sig


def test_k4_floor_boundary_pass_and_exact_h(tmp_path):
    # P and Q: one signal per day on days 2,3,4 (25 h apart), so att_lo = 2,2,2: average exactly 2.0 passes; H = ceil(150 / 2.0) = 75
    sig = {p: pass_signals()[p] for p in ("P", "Q")}
    rows = run_multi(tmp_path, ["P", "Q"], 6, sig, [2, 3, 4])
    v = rows[-1]
    assert v["att_lo_per_day"] == [2, 2, 2] and v["avg_att_lo"] == 2.0 and v["floor_pass_att_lo"] is True and v["days_lt_3"] is False and v["H_days_att_lo"] == 75


def test_k4_h_for_higher_rate(tmp_path):
    sig = pass_signals()  # four pools -> 4 a day, H = max(14, ceil(150 / 4)) = 38
    rows = run_multi(tmp_path, ["P", "Q", "R", "S"], 6, sig, [2, 3, 4])
    v = rows[-1]
    assert v["att_lo_per_day"] == [4, 4, 4] and v["H_days_att_lo"] == 38


def test_k4_any_day_zero_fails_floor(tmp_path):
    sig = {p: [(base + 0) * HOUR, (base + 52) * HOUR] for p, base in (("P", 50), ("Q", 51), ("R", 52), ("S", 53))}  # days 2 and 4 only
    rows = run_multi(tmp_path, ["P", "Q", "R", "S"], 6, sig, [2, 3, 4])
    v = rows[-1]
    assert v["att_lo_per_day"] == [4, 0, 4] and v["avg_att_lo"] > 2.0 and v["floor_pass_att_lo"] is False and v["any_day_zero_att_lo"] is True


def test_k5_fewer_than_three_days_never_passes(tmp_path):
    sig = pass_signals()
    rows = run_multi(tmp_path, ["P", "Q", "R", "S"], 6, sig, [2, 3])
    v = rows[-1]
    assert v["avg_att_lo"] >= 2.0 and v["floor_pass_att_lo"] is False and v["days_lt_3"] is True
