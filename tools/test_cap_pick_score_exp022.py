"""Tests for `tools/cap_pick_score.py --exp022` (EXP-022 sections 1, 3, 4, 5). Tiny synthetic trees at 200 ms slots. Nothing here reads /data/mal."""

from __future__ import annotations

import argparse
import contextlib
import csv
import dataclasses
import hashlib
import json
import math

import pytest
from unittest import mock

from tools import cap_pick_score as cps
from tools.test_cap_pick_score import _write_zst, attempt, needs_zstd, run_fix, states, write_fixture, write_out

WSOL = cps.WSOL_MINT
Q0, B0 = cps.SEED_Q, cps.SEED_B
V_OK = 17_600_000_000
HOURS = {"exploration": ("2026-08-20T04", "2026-08-20T05"), "walk2": ("2026-10-16T01", "2026-10-16T02")}
BASE = dict(bad_q=False, pool=None, v=V_OK, quote=WSOL, mayhem=False, create=True, migration=True, vq=True, map_v=None, gap_slots=5, gap_s=1, hour=0, tail=True)
PRIMARY = cps.exp022_config(1300, 550, "conditional")


@pytest.fixture(autouse=True)
def _late_cutoff(monkeypatch):
    """The walk-2 fixture hours are in the counted window (2026-10-16...), after the #461 hour cutoff. The read tool's allowlist is a later PR; the tests lift the cutoff."""
    monkeypatch.setattr(cps, "CUTOFF_HOUR", "2026-12-01T00")


def build_tree(tmp_path, source, mints):
    """The lab layout under tmp_path/viewA. mints = {name: overrides of BASE}. Slots are 200 ms apart (5 per second). Every mint: a `complete` at slot MS, its first
    PumpSwap print s0 = MS + gap_slots (block_time complete + gap_s), prints at s0 + 0, 5, 10, 1600, 1700 (price flat, then +5.9% from 1600, so the exit is the 300 s deadline and its fill depends on V0).
    Returns (view dir, v-map path)."""
    h0, h1 = HOURS[source]
    view = tmp_path / "viewA"
    mig, cre, trd = {h0: [], h1: []}, {h0: [], h1: []}, {h0: [], h1: []}
    vmap = {}
    for i, (name, ov) in enumerate(sorted(mints.items())):
        sp = {**BASE, **ov}
        h = (h0, h1)[sp["hour"]]
        ms, cbt = 1_000_000 + 10_000 * i, cps._hour_epoch(h) + 100 + 10 * i
        pool = sp["pool"] or f"Pool_{name}"
        s0, s0bt = ms + sp["gap_slots"], cbt + sp["gap_s"]
        mig[h].append({"type": "complete", "mint": name, "slot": ms, "block_time": cbt})
        if sp["migration"]:
            mig[h].append({"type": "migration", "mint": name, "pool": pool, "quote_mint": sp["quote"], "slot": ms, "block_time": cbt, "tx_index": 1, "event_index": 0})
        if sp["create"]:
            cre[h].append({"type": "create", "mint": name, "is_mayhem_mode": sp["mayhem"], "slot": ms - 50, "block_time": cbt - 10})
        tok = int(1e9 * B0 / (Q0 + V_OK))  # the same for every V0, so two trees that differ only in V0 share one tape
        for tx, off in enumerate((0, 5, 10, 1600, 1700) if sp["tail"] else (0, 5, 10, 1600), 1):
            row = {"v": 2, "venue": "pumpswap", "mint": name, "trader": "t", "side": "buy", "sol_lamports": 10**9, "token_raw": tok, "quote_reserve": -sp["v"] if sp["bad_q"] else Q0 + (5_000_000_000 if off >= 1600 else 0), "base_reserve": B0,
                   "pool": pool, "slot": s0 + off, "event_index": 0, "block_time": s0bt + off // 5, "tx_index": tx}
            if sp["vq"]:
                row["virtual_quote_reserves"] = sp["v"]
            trd[h].append(row)
        if sp["map_v"] != "absent":
            vmap[pool] = sp["v"] if sp["map_v"] is None else sp["map_v"]
    last_slot = 1_000_000 + 10_000 * len(mints) + 7_000 + cps.UNCENSORED_HORIZON
    trd[h1].append({"v": 2, "venue": "pump_bonding", "mint": "Other", "pool": None, "slot": last_slot, "side": "buy", "block_time": 1, "tx_index": 1})
    for h in (h0, h1):
        _write_zst(view / "migrations" / f"migrations-{h}.jsonl.zst", mig[h] + [{"type": "noise"}])
        _write_zst(view / "creates" / f"creates-{h}.jsonl.zst", cre[h] + [{"type": "noise"}])
        _write_zst(view / "trades" / f"trades-{h}.jsonl.zst", trd[h])
    vp = tmp_path / "v.json"
    vp.write_text(json.dumps({"v": vmap}))
    return view, vp


@contextlib.contextmanager
def pinned(source, vp, hj):
    """The exploration adapter pins its inputs to canonical /data/mal paths; the tests point the pins at their own tmp files."""
    if source != "exploration":
        yield
        return
    with mock.patch.dict(cps.EXP022_INPUTS, {"exploration": {"vmap": str(vp), "hour_sph_json": None if hj is None else str(hj)}}):
        yield


def write_picks(tmp_path, picks, others=()):
    p = tmp_path / "decisions.jsonl"
    lines = [{"kind": "decision", "mint": m, "decision": "pick"} for m in picks] + [{"kind": "decision", "mint": m, "decision": "below"} for m in others]
    p.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return p


def run_exp(tmp_path, source, mints, picks=None, others=(), sph=18000.0, hour_json=True, **ns):
    view, vp = build_tree(tmp_path, source, mints)
    pk = write_picks(tmp_path, sorted(mints) if picks is None else picks, others)
    hj = tmp_path / "hours.json"
    hj.write_text(json.dumps({h: sph for h in HOURS[source]}))
    d = dict(p1_fast_dir=None, p1_oracle_insample_dir=None, p2_view_dir=[str(view)], p3_root=None, p4_view_dir=None, vmap=str(vp), picks=str(pk), only_day=None, sph_json=None,
             hour_sph_json=str(hj) if hour_json else None, exp022=True, exp022_source=source)
    d.update(ns)
    with pinned(source, vp, hj if hour_json else None):
        s = cps.run(argparse.Namespace(**d), PRIMARY, lambda m: None)
    return s, s["_rows"]


# --- constants -------------------------------------------------------------------------------------------------------------------------


def test_every_cell_is_built_from_the_exp022_constants():
    cells = cps.exp022_cells()
    assert list(cells) == ["primary_1300", "latency_1900", "exit_lag_1350", "rent_always"]
    assert [(c[0], c[1]) for c in cps.EXP022_CELLS] == [("primary_1300", "binding"), ("latency_1900", "binding"), ("exit_lag_1350", "report-only"), ("rent_always", "report-only")]
    for name, c in cells.items():
        assert (c.bound, c.k_mode, c.k_rounding, c.guard, c.guard_basis, c.guard_ratio, c.guard_min_out_rounding) == ("END", "hour", "ceil", "min_out", "gross", 1.15, "floor"), name
        assert (c.tp, c.sl, c.cap_seconds, c.cap_anchor, c.cap_missing) == (0.5, 0.3, 300.0, "block-time", "refuse"), name
        assert (c.size_lamports, c.fee_lamports, c.rent_lamports, c.flat_fail, c.live_fail, c.book, c.final_state) == (100_000_000, 55_000, 2_039_280, 0.15, 1 / 62, "picks", "lab"), name
        c.validate()
    assert [(c.k_seconds, c.exit_lag_ms, c.rent_mode) for c in cells.values()] == [(1.3, 550.0, "conditional"), (1.9, 550.0, "conditional"), (1.3, 1350.0, "conditional"),
                                                                                   (1.3, 550.0, "always")]


def test_run_records_the_source_and_every_constant(tmp_path):
    s, rows = run_exp(tmp_path, "exploration", {"MintA": {}})
    assert s["mode"] == "exp022" and s["exp022"]["source"] == "exploration" and s["exp022"]["constants"] == json.loads(json.dumps(cps.EXP022))
    assert s["exp022"]["flags_pinned"] == json.loads(json.dumps(cps.EXP022_FLAGS)) and s["exp022"]["adapter"] == cps.ADAPTER_NOTES["exploration"]
    assert s["config"]["k_mode"] == "hour" and s["config"]["guard_min_out_rounding"] == "floor" and s["config"]["cap_missing"] == "refuse"
    assert set(s["exp022"]["cells"]) == {"primary_1300", "latency_1900", "exit_lag_1350", "rent_always"} and len(rows) == 1


@pytest.mark.parametrize("sph,k1300,k1900,lag550,lag1350", [(18000.0, 7, 10, 3, 7), (9000.0, 4, 5, 2, 4), (3_600_000 / 260.0, 5, 8, 3, 6)])
def test_k_and_the_exit_lag_are_ceil_of_the_hours_ms_per_slot(tmp_path, sph, k1300, k1900, lag550, lag1350):
    s, rows = run_exp(tmp_path, "exploration", {"MintA": {}}, sph=sph)  # 200 ms, 400 ms and 260 ms (1300 / 260 = 5.0 exactly: 5, not 6)
    got = {"primary_1300": rows[0], "latency_1900": s["_cell_rows"]["latency_1900"][0], "exit_lag_1350": s["_cell_rows"]["exit_lag_1350"][0]}
    assert got["primary_1300"]["k"] == k1300 and got["latency_1900"]["k"] == k1900
    assert got["primary_1300"]["exit_lag_slots"] == lag550 and got["exit_lag_1350"]["exit_lag_slots"] == lag1350
    assert got["primary_1300"]["landing_slot"] == got["primary_1300"]["s0"] + k1300  # END bound on s0 + k


def test_no_day_fallback_an_unmeasurable_hour_is_refused(tmp_path):
    with pytest.raises(cps.Refused, match="no day-table fallback"):
        run_exp(tmp_path, "exploration", {"MintA": {}}, hour_json=False)  # the fixture hours are too short to measure and no sealed slot span is given


def _tokens_out(f, size=100_000_000):
    """tokens out of the 0.1 SOL buy at price factor f (the same state before and after the one print), computed independently of simulate_attempt."""
    qpre, bpre = states([f, f])
    net = size * (1 - float(cps.fee_ppm(qpre[0], bpre[0])) / 1e6)
    return float(bpre[0] * net / (qpre[0] + net))


def test_the_gross_guard_floors_min_out():
    f = 1.0
    lo, hi = 1.0, 1.3  # tokens_out(f) falls as the price factor rises; find f where tokens_out == the unrounded min_out
    seed_p = (cps.SEED_Q + V_OK) / cps.SEED_B
    target = 100_000_000 / (1.15 * seed_p)
    for _ in range(200):
        f = (lo + hi) / 2
        if _tokens_out(f) > target:
            lo = f
        else:
            hi = f
    tokens = _tokens_out(f)
    assert math.floor(tokens) == math.floor(target) < math.ceil(target)  # non-integer min_out and tokens inside the same unit
    path = dict(slot=[100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 120], factors=[f] * 15, s0=100, hour_s=0.2, bt=[1000 + i for i in range(14)])
    floor_cfg = cps.exp022_config(1300, 550, "conditional")
    ceil_cfg = dataclasses.replace(floor_cfg, guard_min_out_rounding="ceil")
    assert attempt(**path, cfg=floor_cfg, sph={})["status"] == "filled"  # executes: floor(tokens) >= floor(min_out)
    assert attempt(**path, cfg=ceil_cfg, sph={})["status"] == "guarded"  # the #461 draft's ceil rejects the same buy


def test_the_cap_without_a_block_time_is_a_refusal_not_a_fallback():
    path = dict(slot=[100, 105, 110, 1700], factors=[1.0] * 5, s0=100, hour_s=0.2)
    with pytest.raises(cps.Refused, match="block_time"):
        attempt(**path, cfg=PRIMARY, sph={}, bt=None)
    with pytest.raises(cps.Refused, match="block_time"):
        attempt(**path, cfg=PRIMARY, sph={}, bt=[-1, -1, -1, -1])
    ok = attempt(**path, cfg=PRIMARY, sph={}, bt=[1000, 1001, 1002, 1340])
    assert ok["cap_anchor_used"] == "block-time" and ok["status"] == "filled"


def test_the_deadline_sell_pays_the_exit_lag_and_rent_is_charged_only_when_the_sell_cannot_fill():
    full = attempt(slot=[100, 105, 110, 1600, 1700], factors=[1.0] * 6, s0=100, hour_s=0.2, bt=[1000, 1001, 1002, 1320, 1340], cfg=PRIMARY, sph={})
    assert full["rent"] == 0 and full["exit_lag_slots"] == 3 and full["hold_slots"] == 1600 - 107
    short = attempt(slot=[100, 105, 110, 1600], factors=[1.0] * 5, s0=100, hour_s=0.2, bt=[1000, 1001, 1002, 1320], cfg=PRIMARY, sph={})  # no print at or after D + 3 + 1
    norent = attempt(slot=[100, 105, 110, 1600], factors=[1.0] * 5, s0=100, hour_s=0.2, bt=[1000, 1001, 1002, 1320], cfg=dataclasses.replace(PRIMARY, rent_mode="none", rent_lamports=0), sph={})
    assert short["rent"] == 2_039_280 and short["pnl"] == norent["pnl"] - 2_039_280 and norent["rent"] == 0
    always = cps.exp022_cells()["rent_always"]
    assert attempt(slot=[100, 105, 110, 1600, 1700], factors=[1.0] * 6, s0=100, hour_s=0.2, bt=[1000, 1001, 1002, 1320, 1340], cfg=always, sph={})["rent"] == 2_039_280


def test_rent_is_counted_per_cell_in_the_run(tmp_path):
    s, rows = run_exp(tmp_path, "exploration", {"MintFull": {}, "MintShort": {"tail": False}})
    by = {r["mint"]: r for r in rows}
    assert by["MintFull"]["rent"] == 0 and by["MintShort"]["rent"] == 2_039_280
    cells = s["exp022"]["cells"]
    assert cells["primary_1300"]["rent_charged_fills"] == 1 and cells["rent_always"]["rent_charged_fills"] == 2 and cells["rent_always"]["role"] == "report-only"
    assert cells["primary_1300"]["role"] == cells["latency_1900"]["role"] == "binding"


def test_the_pressure_intercept_is_refit_per_cell(tmp_path):
    s, _ = run_exp(tmp_path, "exploration", {f"Mint{i}": {} for i in range(4)})
    for name, c in s["exp022"]["cells"].items():
        assert c["fail_legs"]["pressure_mean_p"] == pytest.approx(cps.TARGET_FAIL_RATE, abs=1e-9), name
        assert c["fail_legs"]["flat"] == 0.15 and c["fail_legs"]["live"] == 1 / 62


# --- conflicting flags -----------------------------------------------------------------------------------------------------------------

CONFLICTS = {
    "--k-seconds": "1.2", "--entry-latency-ms": "1000", "--k-mode": "day", "--k-rounding": "round", "--bound": "START", "--exit-lag": "2", "--exit-lag-ms": "1350",
    "--guard": "none", "--guard-ratio": "1.2", "--guard-basis": "net", "--cap-seconds": "600", "--cap-anchor": "day-mean", "--tp": "0.6", "--sl": "0.2",
    "--size-lamports": "500000000", "--fee-lamports": "5000", "--rent-lamports": "0", "--rent-mode": "always", "--live-fail": "0.1", "--flat-fail": "0.2",
    "--final-state": "g", "--boot-p-draws": "1000", "--pick-threshold": "0.5", "--book": "all", "--sph-json": "days.json",
}
EXP = ["--exp022", "--exp022-source", "exploration", "--out-dir", "o"]


def test_the_conflict_table_covers_every_pinned_flag():
    assert {f[2:].replace("-", "_") for f in CONFLICTS} == set(cps.EXP022_FLAGS)


@pytest.mark.parametrize("flag", sorted(CONFLICTS))
def test_each_conflicting_flag_is_refused(flag, capsys):
    with pytest.raises(cps.Refused, match=flag):
        cps.parse_args(EXP + [flag, CONFLICTS[flag]])
    assert cps.main(EXP + [flag, CONFLICTS[flag], "--picks", "p.jsonl"]) == 2 and "REFUSED" in capsys.readouterr().err


def test_an_abbreviated_conflicting_flag_is_refused_too():
    with pytest.raises(cps.Refused):
        cps.parse_args(EXP + ["--k-sec", "1.2"])


def test_flags_equal_to_their_constants_are_accepted_and_the_namespace_carries_only_constants():
    eq = ["--k-seconds", "1.3", "--entry-latency-ms", "1300", "--k-mode", "hour", "--k-rounding", "ceil", "--bound", "END", "--exit-lag-ms", "550", "--guard", "min_out",
          "--guard-ratio", "1.15", "--guard-basis", "gross", "--cap-seconds", "300", "--cap-anchor", "block-time", "--tp", "0.5", "--sl", "0.3",
          "--size-lamports", "100000000", "--fee-lamports", "55000", "--rent-lamports", "2039280", "--rent-mode", "conditional", "--flat-fail", "0.15",
          "--final-state", "lab", "--boot-p-draws", "10000", "--book", "picks"]
    args, explicit = cps.parse_args(EXP + eq)
    assert args.exp022 and args.exp022_source == "exploration" and "k_mode" in explicit
    assert {d: getattr(args, d) for d in cps.EXP022_FLAGS} == cps.EXP022_FLAGS
    plain, none = cps.parse_args(EXP)
    assert {d: getattr(plain, d) for d in cps.EXP022_FLAGS} == cps.EXP022_FLAGS and none == {}


def test_exp022_needs_a_source_and_picks_and_the_source_needs_exp022(tmp_path, capsys):
    with pytest.raises(cps.Refused, match="--exp022-source needs --exp022"):
        cps.parse_args(["--out-dir", "o", "--exp022-source", "walk2"])
    assert cps.main(["--exp022", "--out-dir", str(tmp_path / "o"), "--p2-view-dir", str(tmp_path)]) == 2 and "needs --exp022-source" in capsys.readouterr().err
    assert cps.main(["--exp022", "--exp022-source", "walk2", "--out-dir", str(tmp_path / "o"), "--p2-view-dir", str(tmp_path)]) == 2 and "needs --picks" in capsys.readouterr().err


def test_a_direct_caller_cannot_override_a_constant_either(tmp_path):
    with pytest.raises(cps.Refused, match="k-mode"):
        run_exp(tmp_path, "exploration", {"MintA": {}}, k_mode="day")
    with pytest.raises(cps.Refused, match="sph-json"):
        run_exp(tmp_path, "exploration", {"MintB": {}}, sph_json=str(tmp_path / "d.json"))


# --- the universe, function level: every condition, both adapters --------------------------------------------------------------------------


def ev(**kw):
    base = dict(mint="M", complete_slot=10, complete_bt=1000, pools={"P": cps.PoolFirst(15, 1001, float(V_OK))}, migration_pool="P", migration_quote_mint=WSOL, mayhem=False)
    base.update(kw)
    return cps.MintEvidence(**base)


def uni(source, e, **kw):
    kw.setdefault("vband", {"P": V_OK} if source == "exploration" else None)
    kw.setdefault("window", (900, 5000) if source == "walk2" else None)
    return cps.exp022_universe(source, {e.mint: e}, **kw)


def pf(s0=15, bt=1001, vq=float(V_OK)):
    return {"P": cps.PoolFirst(s0, bt, vq)}


WALK2_CASES = [
    ("ok", {}, None),
    ("no migration event", dict(migration_pool=None), cps.X_NO_MIGRATION),
    ("quote is not WSOL", dict(migration_quote_mint="USDCmint"), cps.X_NOT_WSOL),
    ("mayhem", dict(mayhem=True), cps.X_MAYHEM),
    ("no create event", dict(mayhem=None), cps.X_NO_CREATE),
    ("no print of the migration pool", dict(pools={}), cps.X_NO_POOL_PRINT),
    ("prints only on another pool", dict(pools={"Q": cps.PoolFirst(15, 1001, float(V_OK))}), cps.X_NO_POOL_PRINT),
    ("V0 below the band", dict(pools=pf(vq=17_499_999_999.0)), cps.X_V0_RANGE),
    ("V0 above the band", dict(pools=pf(vq=17_700_000_001.0)), cps.X_V0_RANGE),
    ("V0 at the lower edge", dict(pools=pf(vq=17_500_000_000.0)), None),
    ("V0 at the upper edge", dict(pools=pf(vq=17_700_000_000.0)), None),
    ("s0 block_time before the window", dict(pools=pf(bt=899), complete_bt=895), cps.X_WINDOW),
    ("s0 block_time at the window start", dict(pools=pf(bt=900), complete_bt=895), None),
    ("s0 block_time at the window end (exclusive)", dict(pools=pf(bt=5000), complete_bt=4990), cps.X_WINDOW),
    ("s0 block_time just inside the window end", dict(pools=pf(bt=4999), complete_bt=4990), None),
    ("s0 exactly 80 s after complete", dict(pools=pf(bt=1080)), None),
    ("s0 81 s after complete", dict(pools=pf(bt=1081)), cps.X_GAP),
    ("s0 block_time missing", dict(pools=pf(bt=None)), cps.X_NO_BT),
    ("complete block_time missing", dict(complete_bt=None), cps.X_NO_BT),
]


@pytest.mark.parametrize("why,over,reason", WALK2_CASES, ids=[c[0] for c in WALK2_CASES])
def test_walk2_universe_conditions(why, over, reason):
    r = uni("walk2", ev(**over))
    if reason is None:
        assert list(r.attempts) == ["M"] and not r.excluded
        a = r.attempts["M"]
        assert a["pool"] == "P" and a["v0_source"] == "event_v" and not a["v0_missing"] and a["v0"] == int(over.get("pools", pf())["P"].vq)
    else:
        assert r.excluded == {"M": reason} and not r.attempts


def test_walk2_missing_v0_is_kept_and_flagged_and_the_window_is_optional():
    r = uni("walk2", ev(pools=pf(vq=None)))
    assert r.attempts["M"]["v0"] is None and r.attempts["M"]["v0_missing"] is True and not r.excluded
    assert uni("walk2", ev(pools=pf(bt=100000), complete_bt=99990), window=None).attempts.keys() == {"M"}  # window=None: no counted-window filter (a caller's choice)


def test_walk2_uses_the_migration_events_pool_not_the_earliest_pool():
    e = ev(pools={"P": cps.PoolFirst(40, 1008, float(V_OK)), "Q": cps.PoolFirst(15, 1001, float(V_OK))})
    assert uni("walk2", e).attempts["M"]["pool"] == "P" and uni("walk2", e).attempts["M"]["s0"] == 40


def test_walk2_tape_coverage_is_report_only_and_the_exploration_adapter_still_drops_it():
    r = uni("walk2", ev(), max_slot=15 + cps.UNCENSORED_HORIZON - 1)
    assert list(r.attempts) == ["M"] and r.report_only["tape_coverage_short"] == ["M"]
    assert uni("walk2", ev(), max_slot=15 + cps.UNCENSORED_HORIZON).report_only["tape_coverage_short"] == []
    x = uni("exploration", ev(), max_slot=15 + cps.UNCENSORED_HORIZON - 1)
    assert x.excluded == {"M": cps.X_TAPE} and not x.attempts


EXPLORATION_CASES = [
    ("ok", {}, {}, None),
    ("V0 is the static map, never the print's V", dict(pools=pf(vq=18e9)), {}, None),
    ("pool missing from the band", dict(other_pool_reason=cps.R_V_OUT, pools={}), {}, cps.R_V_OUT),
    ("no print at all", dict(pools={}), {}, cps.R_NO_PRINT),
    ("mayhem", dict(mayhem=True), {}, cps.X_MAYHEM),
    ("no create event", dict(mayhem=None), {}, cps.X_NO_CREATE),
    ("80 s", dict(pools=pf(bt=1080)), {}, None),
    ("81 s", dict(pools=pf(bt=1081)), {}, cps.X_GAP),
    ("no counted window", dict(pools=pf(bt=100), complete_bt=95), {"window": (900, 5000)}, None),
    ("s0 block_time missing", dict(pools=pf(bt=None)), {}, cps.X_NO_BT),
]


@pytest.mark.parametrize("why,over,kw,reason", EXPLORATION_CASES, ids=[c[0] for c in EXPLORATION_CASES])
def test_exploration_universe_conditions(why, over, kw, reason):
    r = uni("exploration", ev(**over), **kw)
    if reason is None:
        a = r.attempts["M"]
        assert a["v0"] == V_OK and a["v0_source"] == "static_map" and not a["v0_missing"] and not r.excluded
    else:
        assert r.excluded == {"M": reason}


def test_exploration_takes_the_earliest_band_pool_as_g_does_and_ignores_the_migration_event():
    e = ev(pools={"P": cps.PoolFirst(40, 1008, None), "Q": cps.PoolFirst(15, 1001, None), "R": cps.PoolFirst(15, 1001, None)}, migration_pool="P")
    r = uni("exploration", e, vband={"P": V_OK, "Q": 17_550_000_000, "R": 17_650_000_000})
    assert r.attempts["M"]["pool"] == "Q" and r.attempts["M"]["v0"] == 17_550_000_000  # earliest s0, ties by name; not the event's pool P
    assert uni("exploration", e, vband={"P": V_OK}).attempts["M"]["pool"] == "P"  # a pool outside the band is not a candidate


def test_the_universe_needs_a_known_source_and_the_exploration_adapter_the_static_map():
    with pytest.raises(cps.Refused):
        cps.exp022_universe("live", {})
    with pytest.raises(cps.Refused):
        cps.exp022_universe("exploration", {})


# --- the universe, end to end on a tiny tree, both adapters ----------------------------------------------------------------------------

WALK2_TREE = {
    "MintOK": {}, "MintOK2": {"hour": 1}, "MintNoMig": {"migration": False}, "MintUsdc": {"quote": "USDCmint"}, "MintMayhem": {"mayhem": True}, "MintNoCreate": {"create": False},
    "MintV0Low": {"v": 17_400_000_000}, "MintMissingV": {"vq": False}, "MintGap81": {"gap_s": 81},
}
WALK2_EXPECT_ATTEMPTS = {"MintOK", "MintOK2", "MintMissingV"}  # MintNonPick is a universe attempt that is not a pick


def test_walk2_end_to_end_attempts_are_exactly_the_picks_inside_the_universe(tmp_path):
    tree = {**WALK2_TREE, "MintNonPick": {}}
    s, rows = run_exp(tmp_path, "walk2", tree, picks=sorted(WALK2_TREE) + ["MintGhost"], others=["MintNonPick"])
    assert sorted(r["mint"] for r in rows) == sorted(WALK2_EXPECT_ATTEMPTS)  # --book picks: B's picks inside the universe, nothing else
    assert s["picks_not_attempts"] == {cps.X_NO_MIGRATION: ["MintNoMig"], cps.X_NOT_WSOL: ["MintUsdc"], cps.X_MAYHEM: ["MintMayhem"], cps.X_NO_CREATE: ["MintNoCreate"],
                                       cps.X_V0_RANGE: ["MintV0Low"], cps.X_GAP: ["MintGap81"], cps.R_NO_COMPLETE: ["MintGhost"]}
    assert s["picks_in_input"] == len(WALK2_TREE) + 1 == s["counts"]["pick_attempts"] + 7
    assert s["exp022"]["universe"]["attempts_in_universe"] == 4 and s["exp022"]["universe"]["pick_attempts"] == 3  # the non-pick attempt is in the universe, not in the book
    out, summary, _ = write_out(tmp_path, s, rows)
    u = {r["mint"]: r for r in csv.DictReader(open(out / "universe.csv"))}
    assert u["MintNonPick"]["status"] == "attempt" and u["MintNonPick"]["pick"] == "0" and u["MintGap81"]["reason"] == cps.X_GAP and u["MintMissingV"]["v0_missing"] == "1"
    assert {r["day"] for r in rows} == {"2026-10-16"} and {r["v0_source"] for r in rows} == {"event_v"}


def test_walk2_counted_window_excludes_a_day_before_it(tmp_path, monkeypatch):
    monkeypatch.setitem(HOURS, "walk2", ("2026-10-15T22", "2026-10-15T23"))
    s, rows = run_exp(tmp_path, "walk2", {"MintEarly": {}})
    assert rows == [] and s["picks_not_attempts"] == {cps.X_WINDOW: ["MintEarly"]}


EXPLORATION_TREE = {
    "MintOK": {}, "MintOK2": {"hour": 1}, "MintMayhem": {"mayhem": True}, "MintNoCreate": {"create": False}, "MintGap81": {"gap_s": 81}, "MintNotInMap": {"map_v": "absent"},
    "MintZeroV": {"map_v": 0, "v": 0}, "MintOutOfBand": {"v": 18_000_000_000, "vq": False}, "MintNoMigEvent": {"migration": False, "vq": False}, "MintUsdc": {"quote": "USDCmint"},
}


def test_exploration_end_to_end_needs_no_migration_event_or_event_v(tmp_path):
    s, rows = run_exp(tmp_path, "exploration", EXPLORATION_TREE)
    # no migration event, no event V: WSOL is V-band membership, V0 is the static map. MintUsdc has a non-WSOL migration row that this adapter never reads.
    assert sorted(r["mint"] for r in rows) == ["MintNoMigEvent", "MintOK", "MintOK2", "MintUsdc"]
    assert s["picks_not_attempts"] == {cps.X_MAYHEM: ["MintMayhem"], cps.X_NO_CREATE: ["MintNoCreate"], cps.X_GAP: ["MintGap81"], cps.R_NOT_IN_VMAP: ["MintNotInMap"],
                                       cps.R_V_OUT: ["MintOutOfBand", "MintZeroV"]}
    assert {r["v0_source"] for r in rows} == {"static_map"} and all(r["v"] == V_OK for r in rows)


def test_exploration_v_null_in_the_map_is_a_named_exclusion(tmp_path):
    view, vp = build_tree(tmp_path, "exploration", {"MintNull": {}})
    vp.write_text(json.dumps({"v": {"Pool_MintNull": None}}))
    pk = write_picks(tmp_path, ["MintNull"])
    hj = tmp_path / "h.json"
    hj.write_text(json.dumps({h: 18000.0 for h in HOURS["exploration"]}))
    args = argparse.Namespace(p1_fast_dir=None, p1_oracle_insample_dir=None, p2_view_dir=[str(view)], p3_root=None, p4_view_dir=None, vmap=str(vp), picks=str(pk), only_day=None,
                              sph_json=None, hour_sph_json=str(hj), exp022=True, exp022_source="exploration")
    with pinned("exploration", vp, hj):
        s = cps.run(args, PRIMARY, lambda m: None)
    assert s["_rows"] == [] and s["picks_not_attempts"] == {cps.R_V_NULL: ["MintNull"]}


@pytest.mark.parametrize("source", ["exploration", "walk2"])
def test_the_80_s_rule_is_in_seconds_not_slots_at_200_ms_slots(tmp_path, source):
    # 200 ms slots: 80 s = 400 slots. The rule reads block_time, so 380 slots can be too late and 401 slots can be in time.
    tree = {"InTime380": {"gap_slots": 380, "gap_s": 80}, "Late380": {"gap_slots": 380, "gap_s": 81}, "InTime401": {"gap_slots": 401, "gap_s": 80}, "Late401": {"gap_slots": 401, "gap_s": 81}}
    s, rows = run_exp(tmp_path, source, tree)
    assert sorted(r["mint"] for r in rows) == ["InTime380", "InTime401"]
    assert s["picks_not_attempts"] == {cps.X_GAP: ["Late380", "Late401"]}
    assert {r["s0_gap_s"] for r in rows} == {80}


def test_exploration_keeps_the_6900_slot_tape_rule_end_to_end(tmp_path):
    view, vp = build_tree(tmp_path, "exploration", {"MintA": {}})
    last = [p for p in (view / "trades").iterdir() if p.name.endswith("05.jsonl.zst")][0]
    _write_zst(last, [{"v": 2, "venue": "pump_bonding", "mint": "Other", "pool": None, "slot": 1_000_005 + 100, "side": "buy", "block_time": 1, "tx_index": 1}])  # a short tape
    pk = write_picks(tmp_path, ["MintA"])
    hj = tmp_path / "h.json"
    hj.write_text(json.dumps({h: 18000.0 for h in HOURS["exploration"]}))
    args = argparse.Namespace(p1_fast_dir=None, p1_oracle_insample_dir=None, p2_view_dir=[str(view)], p3_root=None, p4_view_dir=None, vmap=str(vp), picks=str(pk), only_day=None,
                              sph_json=None, hour_sph_json=str(hj), exp022=True, exp022_source="exploration")
    with pinned("exploration", vp, hj):
        s = cps.run(args, PRIMARY, lambda m: None)
    assert s["_rows"] == [] and s["picks_not_attempts"] == {cps.X_TAPE: ["MintA"]}


# --- missing V0 -----------------------------------------------------------------------------------------------------------------------


def test_a_missing_v0_is_scored_at_both_bounds_and_takes_the_lower_pnl(tmp_path):
    s, rows = run_exp(_sub(tmp_path, "miss"), "walk2", {"MintX": {"vq": False}})
    lo_s, lo_rows = run_exp(_sub(tmp_path, "lo"), "walk2", {"MintX": {"v": 17_500_000_000}})
    hi_s, hi_rows = run_exp(_sub(tmp_path, "hi"), "walk2", {"MintX": {"v": 17_700_000_000}})
    (r,), (lo,), (hi,) = rows, lo_rows, hi_rows
    assert r["v0_missing"] == 1 and r["v"] == "missing" and r["v0_source"] == "event_v"
    assert r["pnl_nofail"] == min(lo["pnl_nofail"], hi["pnl_nofail"])
    assert lo["pnl_nofail"] != hi["pnl_nofail"]  # the two bounds really differ on this path
    for cell in ("latency_1900", "exit_lag_1350", "rent_always"):  # every cell takes its own lower P&L
        (c,), (cl,), (ch,) = s["_cell_rows"][cell], lo_s["_cell_rows"][cell], hi_s["_cell_rows"][cell]
        assert c["pnl_nofail"] == min(cl["pnl_nofail"], ch["pnl_nofail"])
    m = s["exp022"]["universe"]["missing_v0"]
    assert m["n_missing_v0"] == 1 and m["mints"] == ["MintX"] and m["share_of_attempts"] == 1.0 and m["look_not_decidable_inputs"] is True


def _sub(tmp_path, name):
    d = tmp_path / name
    d.mkdir()
    return d


def _rows(n, missing_rank=None, bad=()):
    out = [{"mint": f"M{i:03d}", "v0_missing": 0, "pnl_flat": 1000.0 - i, "pnl_press": 1000.0 - i} for i in range(n)]
    for i in bad:
        out[i]["v0_missing"] = 1
    return out


def test_missing_v0_look_rule_inputs_top3_and_one_percent():
    cells = lambda rs: {"primary_1300": rs, "latency_1900": [dict(r) for r in rs]}  # noqa: E731
    assert cps.missing_v0_report(cells(_rows(200)))["look_not_decidable_inputs"] is False  # none missing
    r1 = cps.missing_v0_report(cells(_rows(200, bad=(150,))))  # 0.5% of the attempts, not in any top 3
    assert r1["share_of_attempts"] == 0.005 and not any(r1["in_top3"].values()) and r1["look_not_decidable_inputs"] is False
    assert cps.missing_v0_report(cells(_rows(200, bad=(2,))))["look_not_decidable_inputs"] is True  # in the top 3 of a binding leg
    assert cps.missing_v0_report(cells(_rows(200, bad=(3,))))["look_not_decidable_inputs"] is False  # fourth: not in the top 3
    r3 = cps.missing_v0_report(cells(_rows(100, bad=(50, 60))))  # 2% of the attempts
    assert r3["share_of_attempts"] == 0.02 and r3["look_not_decidable_inputs"] is True
    assert cps.missing_v0_report(cells(_rows(100, bad=(50,))))["look_not_decidable_inputs"] is False  # exactly 1% is not "more than 1%"


# --- the default path is untouched -----------------------------------------------------------------------------------------------------


@needs_zstd
def test_defaults_are_unchanged_and_write_no_exp022_files(tmp_path):
    view, vpath = write_fixture(tmp_path)
    s, rows = run_fix(view, vpath)
    out, summary, _ = write_out(tmp_path, s, rows)
    assert hashlib.md5((out / "rows.csv").read_bytes()).hexdigest() == "7266ed2956c475c0e493396c44ae6075"
    assert sorted(p.name for p in out.iterdir()) == ["rows.csv", "summary.json"] and "mode" not in summary and "exp022" not in summary
    assert cps.Config().guard_min_out_rounding == "ceil" and cps.Config().cap_missing == "fallback" and cps.Config().rent_mode == "none"
    args, explicit = cps.parse_args(["--out-dir", "o"])
    assert not args.exp022 and explicit == {} and args.k_mode == "day" and args.size_lamports == 500_000_000


def test_exp022_book_is_the_pick_set_inside_the_universe(tmp_path):
    s, rows = run_exp(tmp_path, "exploration", {"MintA": {}, "MintB": {}, "MintC": {"mayhem": True}}, picks=["MintA", "MintC"], others=["MintB"])
    assert [r["mint"] for r in rows] == ["MintA"] and s["books"]["picks"]["all"]["gate"]["flat"]["n_attempts"] == 1
    assert s["counts"]["pick_attempts"] == 1 and s["picks_not_attempts"] == {cps.X_MAYHEM: ["MintC"]}


# --- pinned inputs (exploration) and unpriced picks -----------------------------------------------------------------------------------------


def _ns(view, vp, pk, hj, **kw):
    d = dict(p1_fast_dir=None, p1_oracle_insample_dir=None, p2_view_dir=[str(view)], p3_root=None, p4_view_dir=None, vmap=str(vp), picks=str(pk), only_day=None, sph_json=None,
             hour_sph_json=None if hj is None else str(hj), exp022=True, exp022_source="exploration")
    d.update(kw)
    return argparse.Namespace(**d)


def test_exploration_pins_the_static_map_and_the_hour_source(tmp_path):
    assert cps.EXP022_INPUTS["exploration"] == {"vmap": "/data/mal/pumpswap-virtual/pool_v_0909.json", "hour_sph_json": None} and "walk2" not in cps.EXP022_INPUTS
    view, vp = build_tree(tmp_path, "exploration", {"MintA": {}})
    pk = write_picks(tmp_path, ["MintA"])
    hj = tmp_path / "h.json"
    hj.write_text(json.dumps({h: 18000.0 for h in HOURS["exploration"]}))
    other = tmp_path / "other_v.json"
    other.write_text(vp.read_text())
    with pinned("exploration", vp, hj):  # the pins are these files: accepted, recorded, and --only-day stays allowed
        s = cps.run(_ns(view, vp, pk, hj, only_day=["2026-08-20"]), PRIMARY, lambda m: None)
        assert s["exp022"]["inputs_pinned"] == {"vmap": str(vp), "hour_sph_json": str(hj)} and len(s["_rows"]) == 1 and s["days"] == ["2026-08-20"]
        with pytest.raises(cps.Refused, match="pins --vmap"):
            cps.run(_ns(view, other, pk, hj), PRIMARY, lambda m: None)  # same bytes, another path
        with pytest.raises(cps.Refused, match="pins --hour-sph-json"):
            other_h = tmp_path / "h2.json"
            other_h.write_text(hj.read_text())
            cps.run(_ns(view, vp, pk, other_h), PRIMARY, lambda m: None)
    with pinned("exploration", vp, None):  # the shipped hour pin is "no file": any --hour-sph-json is refused
        with pytest.raises(cps.Refused, match="pins --hour-sph-json to None"):
            cps.run(_ns(view, vp, pk, hj), PRIMARY, lambda m: None)
    with pytest.raises(cps.Refused, match="pins --vmap"):  # the shipped pins are the /data/mal paths, not these tmp files
        cps.run(_ns(view, vp, pk, None), PRIMARY, lambda m: None)


def test_main_refuses_another_vmap_or_hour_json_for_the_exploration_adapter(tmp_path, capsys):
    base = ["--exp022", "--exp022-source", "exploration", "--picks", "p.jsonl", "--out-dir", str(tmp_path / "o"), "--p2-view-dir", str(tmp_path)]
    assert cps.main(base + ["--vmap", "/tmp/other_v.json"]) == 2 and "pins --vmap" in capsys.readouterr().err
    assert cps.main(base + ["--hour-sph-json", "/tmp/h.json"]) == 2 and "pins --hour-sph-json" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()


@pytest.mark.parametrize("source", ["exploration", "walk2"])
def test_a_bad_reserves_pick_stays_in_the_universe_marked_unpriced(tmp_path, source):
    tree = {"MintOK": {}, "MintBad": {"bad_q": True}, "MintNonPick": {}}
    s, rows = run_exp(tmp_path, source, tree, picks=["MintOK", "MintBad"], others=["MintNonPick"])
    assert [r["mint"] for r in rows] == ["MintOK"] and s["picks_not_attempts"] == {cps.R_BAD_RESERVES: ["MintBad"]} and s["counts"]["bad_reserves"] == 1
    assert s["exp022"]["universe"]["unpriced_picks"] == {cps.R_BAD_RESERVES: ["MintBad"]}
    out, _summary, new_rows = write_out(tmp_path, s, rows)
    u = {r["mint"]: r for r in csv.DictReader(open(out / "universe.csv"))}
    assert (u["MintBad"]["status"], u["MintBad"]["priced"], u["MintBad"]["unpriced_reason"], u["MintBad"]["pick"]) == ("attempt", "false", cps.R_BAD_RESERVES, "1")
    assert (u["MintOK"]["status"], u["MintOK"]["priced"], u["MintOK"]["unpriced_reason"]) == ("attempt", "true", "")
    assert (u["MintNonPick"]["status"], u["MintNonPick"]["priced"]) == ("attempt", "")  # not a pick: never simulated, so no flag
    # the harness's C-vs-U check: U keeps the bad-reserves pick, C (rows.csv) does not, so the md5s differ
    c_mints = sorted(r["mint"] for r in new_rows)
    u_picks = sorted(m for m, r in u.items() if r["status"] == "attempt" and r["pick"] == "1")
    assert c_mints == ["MintOK"] and u_picks == ["MintBad", "MintOK"]
    assert hashlib.md5("\n".join(c_mints).encode()).hexdigest() != hashlib.md5("\n".join(u_picks).encode()).hexdigest()
