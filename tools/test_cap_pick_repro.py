"""Tests for tools/cap_pick_repro.py on synthetic rows. Nothing here reads /data/mal or Parquet."""

from __future__ import annotations

import csv

import pytest

from tools import cap_pick_repro as rp
from tools import cap_pick_score as cps

CFG = cps.Config()


def row(block="explore-0814", day="2026-08-15", status="filled", exit_type="tp", hold=10, k=3, pnl=1_000_000.0, ssb=1, nearby=1e9):
    return {"day": day, "block": block, "status": status, "exit_type": exit_type, "hold_slots": hold, "k": k, "pnl": pnl, "ssb": ssb, "nearby": nearby, "fee": 55_000.0,
            "size": 500_000_000.0}


def book(n=30, **kw):
    return {f"m{i}": row(pnl=-2e6 + 1e5 * i, ssb=i % 4, nearby=1e9 * (i % 3), **kw) for i in range(n)}


def test_identical_books_are_exact_on_every_leg():
    g = book()
    rep = rp.compare(dict(g), g, CFG)
    assert rep["n_matched"] == 30 and rep["n_only_tool"] == rep["n_only_g"] == 0
    for leg in rp.LEGS:
        c = rep["groups"]["all"][leg]
        assert c["n_exact_equal"] == 30 and c["max_abs_lamports"] == 0 and c["mean_diff_pp"] == 0 and c["within_target"]
    assert rep["mismatch_classes"] == {}


def test_only_in_one_side_and_a_lamport_shift_are_reported_and_classified():
    g = book()
    t = dict(g)
    del t["m0"]
    t["extra"] = row()
    t["m1"] = dict(t["m1"], pnl=t["m1"]["pnl"] + 50_000.0)  # 50,000 lamports on 500,000,000 = 0.01 pp on one of 29 matched
    t["m2"] = dict(t["m2"], status="guarded", exit_type="guard", pnl=-55_000.0)
    t["m3"] = dict(t["m3"], exit_type="sl", pnl=t["m3"]["pnl"] - 7.0)
    t["m4"] = dict(t["m4"], hold_slots=11, pnl=t["m4"]["pnl"] + 1.0)
    rep = rp.compare(t, g, CFG)
    assert rep["n_matched"] == 29 and rep["n_only_tool"] == 1 and rep["n_only_g"] == 1
    assert rep["only_tool_sample"] == ["extra"] and rep["only_g_sample"] == ["m0"]
    cls = rep["mismatch_classes"]
    assert cls["lamports only"] == 1 and cls["status tool=guarded G=filled"] == 1 and cls["exit_type tool=sl G=tp"] == 1 and cls["hold_slots"] == 1
    nf = rep["groups"]["all"]["nofail"]
    assert nf["n_exact_equal"] == 25 and nf["max_abs_lamports"] == pytest.approx(1_745_000.0)  # the guarded m2: -55,000 against -1,800,000
    assert [r["mint"] for r in rep["mismatches_largest"]][:2] == ["m2", "m1"]  # largest first: the guarded row, then the 50,000-lamport shift


def test_mean_difference_is_in_percentage_points_of_stake_and_flags_the_target():
    g = book(n=10)
    t = {m: dict(r, pnl=r["pnl"] + 50_000.0) for m, r in g.items()}  # +50,000 lamports every attempt = +0.01 pp of a 0.5 SOL stake
    rep = rp.compare(t, g, CFG)
    assert rep["groups"]["all"]["nofail"]["mean_diff_pp"] == pytest.approx(0.01)
    t2 = {m: dict(r, pnl=r["pnl"] + 500_000.0) for m, r in g.items()}
    c = rp.compare(t2, g, CFG)["groups"]["all"]["nofail"]
    assert c["mean_diff_pp"] == pytest.approx(0.1) and not c["within_target"]


def test_blocks_and_groups_split_by_g_block():
    g = {**{f"a{i}": row(block="explore-0814", pnl=1e5 * i) for i in range(5)}, **{f"b{i}": row(block="oracle-insample-0922", pnl=1e5 * i) for i in range(4)}}
    rep = rp.compare(dict(g), g, CFG)
    assert rep["blocks"]["explore-0814"]["n_matched"] == 5 and rep["blocks"]["oracle-insample-0922"]["n_matched"] == 4
    assert rep["groups"]["P2-P4"]["n_matched"] == 5 and rep["groups"]["P1"]["n_matched"] == 4 and rep["groups"]["all"]["n_matched"] == 9
    assert "P2-P4" in rp.render(rep)


def test_load_tool_rows_roundtrip(tmp_path):
    p = tmp_path / "rows.csv"
    with open(p, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cps.ROW_COLUMNS)
        w.writeheader()
        w.writerow({"day": "2026-08-15", "block": "explore-0814", "mint": "M", "k": 3, "status": "filled", "exit_type": "tp", "hold_slots": 12, "ssb": 2, "nearby_lamports": 3e9,
                    "size": 500000000, "fee": 55000, "pnl_nofail": repr(1234.5)})
    r = rp.load_tool_rows(p)["M"]
    assert r["pnl"] == 1234.5 and r["hold_slots"] == 12 and r["k"] == 3 and r["nearby"] == 3e9
