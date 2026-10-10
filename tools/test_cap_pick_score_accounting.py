"""Tests for the quant-proof edits on #461 (PR comment 6067744815): every pick is accounted for, and the path guard checks every file.
Tiny synthetic trees only. Nothing here reads /data/mal."""

from __future__ import annotations

import hashlib
import json

import pytest

from tools import cap_pick_score as cps
from tools.test_cap_pick_score import _trade, _write_zst, fix_args, needs_zstd, run_fix, write_fixture, write_out

FIXTURE_ROWS_MD5 = "7266ed2956c475c0e493396c44ae6075"  # md5 of the phase-1 fixture's rows.csv at the defaults (fixture + code, all columns)


@needs_zstd
def test_default_rows_csv_md5_is_pinned(tmp_path):
    view, vpath = write_fixture(tmp_path)
    s, rows = run_fix(view, vpath)
    out, _summary, _rows = write_out(tmp_path, s, rows)
    assert hashlib.md5((out / "rows.csv").read_bytes()).hexdigest() == FIXTURE_ROWS_MD5


@needs_zstd
def test_index_hours_checks_every_file_not_only_its_directory(tmp_path):
    view = tmp_path / "viewA"
    other = tmp_path / "elsewhere"
    _write_zst(other / "plain" / "trades-2026-08-20T04.jsonl.zst", [{"venue": "pumpswap"}])
    (view / "trades").mkdir(parents=True)
    (view / "trades" / "trades-2026-08-20T04.jsonl.zst").symlink_to(other / "plain" / "trades-2026-08-20T04.jsonl.zst")  # an allowed target: fine
    idx = cps.index_hours([cps.Source("explore-0814", (view,))])
    assert sorted(idx["trades"]) == ["2026-08-20T04"]
    # an allowed NAME whose target sits in a refused path: the directory passes the guard, the file must not
    for bad_dir, kind in (("forward-1002", "trades"), ("exp012-forward", "migrations")):
        v2 = tmp_path / f"view_{kind}"
        _write_zst(tmp_path / bad_dir / f"{kind}-2026-08-20T05.jsonl.zst", [{"venue": "pumpswap"}])
        (v2 / kind).mkdir(parents=True)
        (v2 / kind / f"{kind}-2026-08-20T05.jsonl.zst").symlink_to(tmp_path / bad_dir / f"{kind}-2026-08-20T05.jsonl.zst")
        cps.check_path_allowed(v2)  # the view dir itself is fine
        with pytest.raises(cps.Refused):
            cps.index_hours([cps.Source("explore-0814", (v2,))])
    # a file whose name is not an hour file is checked too (the guard comes before the name filter)
    v3 = tmp_path / "view_odd"
    _write_zst(tmp_path / "forward-1002" / "notes.jsonl.zst", [{"x": 1}])
    (v3 / "trades").mkdir(parents=True)
    (v3 / "trades" / "notes.jsonl.zst").symlink_to(tmp_path / "forward-1002" / "notes.jsonl.zst")
    with pytest.raises(cps.Refused):
        cps.index_hours([cps.Source("explore-0814", (v3,))])


def accounting_tree(tmp_path):
    """Two days on the lab layout. 2026-08-15: one attempt (MintOK) and one mint per reason a mint with a `complete` event is not an attempt.
    2026-08-16: a trade hour with no migrations file, so the day is skipped; MintSkipped's `complete` is in the migrations hour that exists."""
    view = tmp_path / "viewA"
    q0, b0 = cps.SEED_Q, cps.SEED_B
    d1, d2 = "2026-08-15", "2026-08-16"
    mig1 = [{"type": "complete", "mint": m, "slot": 1000, "block_time": 10}
            for m in ("MintOK", "MintBand", "MintLate", "MintNoVmap", "MintNull", "MintNoPrint", "MintBadRes")]
    mig1 += [{"type": "create", "mint": "MintNoComplete", "slot": 1000}]
    _write_zst(view / "migrations" / f"migrations-{d1}T00.jsonl.zst", mig1)
    _write_zst(view / "migrations" / f"migrations-{d1}T01.jsonl.zst", [{"type": "create", "mint": "Other", "slot": 9000}])
    t1 = [
        _trade("MintOK", "PoolOK", 1001, "buy", q0, b0, 10**9, 10**12, tx=1),
        _trade("MintOK", "PoolOK", 1005, "buy", q0 + 10**9, b0 - 10**12, 10**9, 10**12, tx=2),
        _trade("MintBand", "PoolOutOfBand", 1001, "buy", q0, b0, 10**9, 10**12, tx=1),
        _trade("MintLate", "PoolLate", 8000, "buy", q0, b0, 10**9, 10**12, tx=1),
        _trade("MintNoVmap", "PoolUnknown", 1001, "buy", q0, b0, 10**9, 10**12, tx=1),
        _trade("MintNull", "PoolNull", 1001, "buy", q0, b0, 10**9, 10**12, tx=1),
        _trade("MintNoPrint", None, 1001, "buy", q0, b0, 10**9, 10**12, tx=1, venue="pump_bonding"),
        _trade("MintBadRes", "PoolBad", 1001, "buy", -17_600_000_000, b0, 10**9, 10**12, tx=1),  # quote + V = 0
    ]
    _write_zst(view / "trades" / f"trades-{d1}T00.jsonl.zst", t1)
    _write_zst(view / "trades" / f"trades-{d1}T01.jsonl.zst", [_trade("Other", "X", 1001 + 6900 + 5, "buy", q0, b0, 1, 1, tx=1, venue="pump_bonding")])
    _write_zst(view / "migrations" / f"migrations-{d2}T00.jsonl.zst", [{"type": "complete", "mint": "MintSkipped", "slot": 5000, "block_time": 10}])
    _write_zst(view / "trades" / f"trades-{d2}T00.jsonl.zst", [_trade("MintSkipped", "PoolOK", 5001, "buy", q0, b0, 10**9, 10**12, tx=1)])
    _write_zst(view / "trades" / f"trades-{d2}T01.jsonl.zst", [_trade("Other", "X", 5001 + 6900 + 5, "buy", q0, b0, 1, 1, tx=1, venue="pump_bonding")])  # no migrations-d2T01
    vmap = tmp_path / "v.json"
    vmap.write_text(json.dumps({"v": {"PoolOK": 17_600_000_000, "PoolOutOfBand": 18_000_000_000, "PoolLate": 17_600_000_000, "PoolNull": None, "PoolBad": 17_600_000_000}}))
    return view, vmap


ACCOUNTING_PICKS = ("MintOK", "MintBand", "MintLate", "MintNoVmap", "MintNull", "MintNoPrint", "MintBadRes", "MintSkipped", "MintNoComplete", "MintGhost")


@needs_zstd
def test_no_pick_vanishes_without_a_reason(tmp_path):
    view, vmap = accounting_tree(tmp_path)
    p = tmp_path / "picks.jsonl"
    lines = [{"kind": "decision", "mint": m, "decision": "pick"} for m in ACCOUNTING_PICKS] + [{"kind": "decision", "mint": "MintNonPick", "decision": "below"}]
    p.write_text("".join(json.dumps(x) + "\n" for x in lines))
    for book in ("all", "picks"):
        s, rows = run_fix(view, vmap, cps.Config(book=book), picks=str(p))
        assert [r["mint"] for r in rows] == ["MintOK"]
        assert s["picks_in_input"] == len(ACCOUNTING_PICKS) == 10  # the non-pick is not counted
        assert s["picks_not_attempts"] == {
            "bad_reserves": ["MintBadRes"], "censored": ["MintLate"], "no_complete_event_in_days_read": ["MintGhost", "MintNoComplete"],
            "no_pumpswap_print_in_window": ["MintNoPrint"], "pool_not_in_vmap": ["MintNoVmap"], "skipped_incomplete_migrations": ["MintSkipped"],
            "v_null_in_vmap": ["MintNull"], "v_outside_band": ["MintBand"],
        }
        assert s["counts"]["pick_attempts"] == 1
        assert s["picks_in_input"] == s["counts"]["pick_attempts"] + sum(len(v) for v in s["picks_not_attempts"].values())
        assert s["days_skipped_incomplete_migrations"] == ["2026-08-16"] and s["days"] == ["2026-08-15"]
        _out, summary, _ = write_out(tmp_path, s, rows, name=f"out_{book}")
        assert summary["picks_not_attempts"]["censored"] == ["MintLate"] and summary["picks_in_input"] == 10  # reaches summary.json


@needs_zstd
def test_pick_accounting_csv_uses_the_threshold_and_no_picks_means_none(tmp_path):
    view, vmap = accounting_tree(tmp_path)
    p = tmp_path / "scores.csv"
    p.write_text("mint,score\nMintOK,0.9\nMintBand,0.9\nMintLate,0.1\nMintGhost,0.9\n")
    s, _rows = run_fix(view, vmap, picks=str(p))
    assert s["picks_in_input"] == 3 and s["picks_not_attempts"] == {"no_complete_event_in_days_read": ["MintGhost"], "v_outside_band": ["MintBand"]}
    s0, _ = run_fix(view, vmap)
    assert s0["picks_in_input"] is None and s0["picks_not_attempts"] is None


@needs_zstd
def test_the_reasons_do_not_change_which_mints_are_attempts(tmp_path):
    view, vmap = accounting_tree(tmp_path)
    idx = cps.index_hours(cps.build_sources(fix_args(view, vmap)))
    vband = cps.load_vband(vmap, cps.V_LO, cps.V_HI)
    plain = cps.read_day("2026-08-15", idx, vband, cps.Config(), lambda m: None)
    why: dict = {}
    with_why = cps.read_day("2026-08-15", idx, vband, cps.Config(), lambda m: None, reasons=why, vall=cps.load_vmap(vmap))
    assert plain[1] == with_why[1] and sorted(plain[0]) == sorted(with_why[0]) == ["MintBadRes", "MintOK"]  # bad_reserves is found in run(), not read_day
    assert why["MintBand"] == cps.R_V_OUT and why["MintNull"] == cps.R_V_NULL and why["MintNoVmap"] == cps.R_NOT_IN_VMAP and why["MintLate"] == cps.R_CENSORED
    assert cps.read_day("2026-08-16", idx, vband, cps.Config(), lambda m: None)[1]["skipped_incomplete_migrations"] == 1
