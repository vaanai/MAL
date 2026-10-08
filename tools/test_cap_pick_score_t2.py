"""Tests for the T2 report-only BOOST-progress exit (rule B90), the stress cut and the paired report in tools/cap_pick_score.py.
Toy paths only: nothing here reads /data/mal. The default (no T2 flag) run is pinned by the phase-1 golden test in test_cap_pick_score.py."""

from __future__ import annotations

import dataclasses
import json

import numpy as np
import pytest

from tools import cap_pick_score as cps
from tools.test_cap_pick_score import CFG, SPH_HALF, V, _trade, _write_zst, buy_and_sell, fix_args, needs_zstd, read_rows_csv, states

B90 = dataclasses.replace(CFG, exit_mode="boost90")
BUDGET = cps.BOOST_BUDGET_LAMPORTS


def t2_attempt(slot, factors, cfg=CFG, boost_idx=None, s0=100):
    qpre, bpre = states(factors)
    n = len(slot)
    assert qpre.size == n + 1
    return cps.simulate_attempt(cfg, day="d", sph=SPH_HALF, v=V, s0=s0, slot=np.asarray(slot, np.int64), isbuy=np.ones(n, bool), sol=np.full(n, 1e9), qpre=qpre, bpre=bpre,
                                hour="d-hour", hour_s=None, bt=None, boost_idx=boost_idx)


# 0.5 s per slot: k = 3, landing slot 103, END bound -> the buy fills at the state before print index 1 (slot 150); the 300 s cap is 600 slots, deadline slot 703 -> icap = 5
SLOTS = [100, 150, 300, 450, 500, 800]
FLAT = [1, 1, 1, 1, 1.3, 1.1, 0.9]  # price factor before print i; no tp (+50%) or sl (-30%) is ever reached


def test_defaults_do_not_turn_t2_on_and_cap_mode_ignores_a_boost_index():
    c = cps.Config()
    assert (c.exit_mode, c.boost_cut, c.paired, c.needs_trader) == ("cap", None, False, False)
    assert cps.config_from_args(cps._parser().parse_args(["--out-dir", "o"])) == c
    a = t2_attempt(SLOTS, FLAT, cfg=CFG, boost_idx=3)
    assert a == t2_attempt(SLOTS, FLAT, cfg=CFG, boost_idx=None) and a["exit_type"] == "deadline"
    assert a["pnl"] == pytest.approx(buy_and_sell(FLAT, 1, 5))


def test_b90_fires_at_the_trigger_print_and_sells_after_the_exit_lag():
    r = t2_attempt(SLOTS, FLAT, cfg=B90, boost_idx=3)  # trigger print: slot 450; sell = state before the first print with slot >= 450 + 2 + 1 -> index 4 (slot 500)
    assert r["exit_type"] == "boost" and r["hold_slots"] == 450 - 103
    assert r["pnl"] == pytest.approx(buy_and_sell(FLAT, 1, 4))
    cap = t2_attempt(SLOTS, FLAT, cfg=CFG)
    assert cap["exit_type"] == "deadline" and cap["pnl"] == pytest.approx(buy_and_sell(FLAT, 1, 5)) and cap["pnl"] != r["pnl"]


def test_b90_without_a_trigger_or_with_a_trigger_at_or_after_the_deadline_is_the_cap():
    cap = t2_attempt(SLOTS, FLAT, cfg=CFG)
    for bi in (None, 5):  # 5 = the first print at or after the deadline (icap): not strictly before it
        r = t2_attempt(SLOTS, FLAT, cfg=B90, boost_idx=bi)
        assert r["exit_type"] == "deadline" and r["pnl"] == cap["pnl"] and r["hold_slots"] == cap["hold_slots"]


def test_b90_before_the_landing_print_exits_at_the_landing_print():
    r = t2_attempt(SLOTS, FLAT, cfg=B90, boost_idx=0)
    assert r["exit_type"] == "boost" and r["pnl"] == pytest.approx(buy_and_sell(FLAT, 1, 2))  # max(boost_idx, je) = 1 (slot 150); sell at the first print >= 153


def test_a_tp_at_the_same_print_or_earlier_beats_b90_and_an_earlier_b90_beats_a_later_tp():
    jump = [1, 1, 1, 1.7, 1.7, 1.7, 1.7]  # post-state of print 2 (slot 300) is +70%: tp fires at print 2
    assert t2_attempt(SLOTS, jump, cfg=B90, boost_idx=3)["exit_type"] == "tp"
    assert t2_attempt(SLOTS, jump, cfg=B90, boost_idx=2)["exit_type"] == "tp"  # a tie goes to the tp
    assert t2_attempt(SLOTS, jump, cfg=B90, boost_idx=1)["exit_type"] == "boost"


# --- the keeper detector ---------------------------------------------------------------------------------------------------------------


def _path(rows):
    """rows = [(slot, trader id, side, lamports)] -> the arrays boost_scan reads."""
    a = sorted(rows, key=lambda r: r[0])
    return (np.array([r[1] for r in a], np.int64), np.array([r[2] == "buy" for r in a]), np.array([float(r[3]) for r in a]), np.array([r[0] for r in a], np.int64))


def _boost_rows(keeper=7, n=29, per=600_000_000, s0=100, cadence=20, first=8):
    return [(s0 + first + i * cadence, keeper, "buy", per) for i in range(n)]


def test_trader_id_is_stable_and_zero_means_no_trader():
    a = cps.trader_id("8oA8AR34GDzWdTqQg8C4ekMcRju14avYRtSGtHm9np9v")
    assert a == cps.trader_id("8oA8AR34GDzWdTqQg8C4ekMcRju14avYRtSGtHm9np9v") and a != cps.trader_id("9YvBoKcwLX3Ye7ea1cMdnYqvVLXVTNVvrQF2UkEqfU9p") and a != 0
    assert cps.trader_id(None) == 0 and cps.trader_id("") == 0 and cps.trader_id(5) == 0


def test_the_detector_finds_the_wallet_at_the_print_where_its_buys_reach_90_percent():
    rows = _boost_rows() + [(101, 1, "buy", 10**9), (130, 2, "sell", 5 * 10**8), (400, 3, "buy", 10**9)]
    tr, ib, sol, slot = _path(rows)
    idx, wid = cps.boost_scan(tr, ib, sol, slot, 100)
    # 0.6 SOL a slice: 26 slices = 15.6 SOL < 15.8265, the 27th = 16.2 SOL crosses; its slot is 108 + 26 x 20 = 628
    assert wid == 7 and slot[idx] == 628 and tr[idx] == 7 and ib[idx]
    assert int(((tr[: idx + 1] == 7)).sum()) == 27


def test_the_detector_rejects_what_g_rejects():
    tr, ib, sol, slot = _path(_boost_rows())
    assert cps.boost_scan(tr, ib, sol, slot, 100) is not None
    # a sell by the wallet before it crosses
    assert cps.boost_scan(*_path(_boost_rows() + [(150, 7, "sell", 10**8)]), 100) is None
    # one buy above 2 SOL
    big = _boost_rows()
    big[4] = (big[4][0], 7, "buy", 3 * 10**9)
    assert cps.boost_scan(*_path(big), 100) is None
    # 7 buys of 2 SOL = 14 SOL < 0.9 x 17.585; 8 buys of 2 SOL = 16 SOL >= it, and the 8th is the trigger
    assert cps.boost_scan(*_path(_boost_rows(n=7, per=2 * 10**9)), 100) is None
    t8 = _path(_boost_rows(n=8, per=2 * 10**9))
    assert cps.boost_scan(*t8, 100)[0] == 7
    # prints after s0 + 2,500 are not looked at
    assert cps.boost_scan(*_path(_boost_rows(first=2600)), 100) is None
    # trader 0 (no trader on the row) is never a wallet
    assert cps.boost_scan(*_path(_boost_rows(keeper=0)), 100) is None
    # two wallets qualify: the one that crosses first in path order wins
    both = _boost_rows(keeper=7) + _boost_rows(keeper=9, first=9, cadence=19)
    assert cps.boost_scan(*_path(both), 100)[1] == 9


# --- the stress cut: exact on a toy constant-product path ----------------------------------------------------------------------------------


def _cp_tape(events):
    """The tape a constant-product pool would write for `events` = [(slot, trader, side, amount)]: a buy's amount is its SOL in (lamports), a sell's its tokens in. Same
    equations and PRE-trade convention as the scorer; X = quote + V from the seed state."""
    x, b = float(cps.SEED_Q + V), float(cps.SEED_B)
    qp, bp, sol, tok = [], [], [], []
    for _slot, _w, side, amt in events:
        qp.append(x)
        bp.append(b)
        fee = float(cps.fee_ppm(x, b)) / 1e6
        if side == "buy":
            net = amt * (1 - fee)
            t = b * net / (x + net)
            sol.append(float(amt))
            tok.append(t)
            x, b = x + net, b - t
        else:
            g = x * amt / (b + amt)
            sol.append(g * (1 - fee))
            tok.append(float(amt))
            x, b = x - g, b + amt
    qp.append(x)
    bp.append(b)
    return dict(slot=np.array([e[0] for e in events], np.int64), trader=np.array([e[1] if isinstance(e[1], int) else 0 for e in events], np.int64), isbuy=np.array([e[2] == "buy" for e in events]),
                sol=np.array(sol), tok=np.array(tok), qpre=np.array(qp), bpre=np.array(bp), bt=np.full(len(events), -1, np.int64))


OTHERS = [(101, 1, "buy", 10**9), (300, 2, "sell", 5 * 10**12), (500, 3, "buy", 2 * 10**9), (700, 4, "sell", 3 * 10**12)]


def _events():
    return sorted(OTHERS + _boost_rows(), key=lambda e: e[0])


def _cut(f, events=None):
    ev = events or _events()
    t = _cp_tape(ev)
    return ev, t, cps.cut_boost_path(f, trader=t["trader"], slot=t["slot"], isbuy=t["isbuy"], sol=t["sol"], tok=t["tok"], qpre=t["qpre"], bpre=t["bpre"], bt=t["bt"], s0=100)


@pytest.mark.parametrize("f,kept_keeper", [(0.6, 17), (0.8, 23)])
def test_the_cut_resimulation_equals_the_tape_a_pool_would_have_written_without_those_buys(f, kept_keeper):
    ev, t, c = _cut(f)
    assert kept_keeper * 600_000_000 <= f * BUDGET < (kept_keeper + 1) * 600_000_000  # the cut keeps buys while the cumulative, including that buy, is <= f x budget
    assert c["dropped"] == 29 - kept_keeper and c["keeper"] == 7
    keeper_buys = [e for e in ev if e[1] == 7]
    gone = {e[0] for e in keeper_buys[kept_keeper:]}
    want = _cp_tape([e for e in ev if e[0] not in gone])  # an independent route to the same path: build the tape from the remaining events
    for k in ("slot", "trader", "isbuy"):
        assert np.array_equal(c[k], want[k]), k
    for k in ("sol", "tok", "qpre", "bpre"):
        np.testing.assert_allclose(c[k], want[k], rtol=1e-12, atol=0)
    # constant product: every re-simulated state keeps X x B of the first removed print's state (fee leaves the pool on a buy, and a sell is an exact constant-product move)
    i0 = int(np.flatnonzero(np.isin(t["slot"], list(gone)))[0])
    inv = t["qpre"][i0] * t["bpre"][i0]
    np.testing.assert_allclose((c["qpre"] * c["bpre"])[i0:], inv, rtol=1e-12)
    # tokens are conserved: kept buys leave, kept sells arrive
    assert c["bpre"][-1] == pytest.approx(t["bpre"][i0] - c["tok"][i0:][c["isbuy"][i0:]].sum() + c["tok"][i0:][~c["isbuy"][i0:]].sum(), rel=1e-12)
    assert c["qpre"][-1] < t["qpre"][-1]  # less SOL went in


def test_the_cut_leaves_the_path_alone_when_nothing_is_past_the_cut_or_there_is_no_keeper():
    ev, t, c = _cut(1.0)
    assert c["dropped"] == 0 and c["keeper"] == 7 and c["slot"] is t["slot"] and c["qpre"] is t["qpre"]
    ev2, t2, c2 = _cut(0.6, events=sorted(OTHERS, key=lambda e: e[0]))
    assert c2["dropped"] == 0 and c2["keeper"] == 0 and c2["qpre"] is t2["qpre"]


def test_before_the_first_removed_print_the_observed_state_stands():
    ev, t, c = _cut(0.6)
    i0 = int(np.flatnonzero(t["slot"] == 108 + 17 * 20)[0])  # the 18th keeper buy is the first one removed
    np.testing.assert_array_equal(c["qpre"][:i0], t["qpre"][:i0])
    np.testing.assert_array_equal(c["bpre"][:i0], t["bpre"][:i0])


def test_b90_cannot_fire_on_a_path_cut_below_90_percent_so_it_equals_the_cap_exactly():
    ev, t, uncut = _cut(1.0)
    assert cps.boost_scan(uncut["trader"], uncut["isbuy"], uncut["sol"], uncut["slot"], 100)[0] is not None
    for f in (0.8, 0.6):
        _, _, c = _cut(f)
        assert cps.boost_scan(c["trader"], c["isbuy"], c["sol"], c["slot"], 100) is None
        kw = dict(day="d", sph=SPH_HALF, v=V, s0=100, slot=c["slot"], isbuy=c["isbuy"], sol=c["sol"], qpre=c["qpre"], bpre=c["bpre"], hour="h", hour_s=None, bt=None, boost_idx=None)
        a, b = cps.simulate_attempt(CFG, **kw), cps.simulate_attempt(B90, **kw)
        assert a["status"] == "filled" and a["pnl"] == b["pnl"] and a["exit_type"] == b["exit_type"] == "deadline"
    # on the uncut path the rule does fire (trigger: the 27th slice, slot 628, before the 703 deadline) and the paths differ
    bi, _ = cps.boost_scan(uncut["trader"], uncut["isbuy"], uncut["sol"], uncut["slot"], 100)
    kw = dict(day="d", sph=SPH_HALF, v=V, s0=100, slot=uncut["slot"], isbuy=uncut["isbuy"], sol=uncut["sol"], qpre=uncut["qpre"], bpre=uncut["bpre"], hour="h", hour_s=None, bt=None, boost_idx=bi)
    r = cps.simulate_attempt(B90, **kw)
    assert r["exit_type"] == "boost" and uncut["slot"][bi] == 628 and r["pnl"] != cps.simulate_attempt(CFG, **kw)["pnl"]


def test_config_validation_of_the_t2_switches():
    for bad in (dict(exit_mode="b90"), dict(boost_cut=0.0), dict(boost_cut=1.5), dict(boost_cut=-0.2), dict(boost_cut=True)):
        with pytest.raises(cps.Refused):
            dataclasses.replace(cps.Config(), **bad).validate()
    dataclasses.replace(cps.Config(), exit_mode="boost90", boost_cut=1.0, paired=True).validate()


# --- paired statistics ------------------------------------------------------------------------------------------------------------------------


def test_paired_stats_hand_case():
    # two dates: day a has +0.01 and 0 SOL differences, day b has -0.005; stake 0.5 SOL
    s = cps.paired_stats([1e7, 0.0, -5e6], ["a", "a", "b"], 5e8, fires=1)
    assert s["n_attempts"] == 3 and s["n_differ"] == 2 and s["b90_fires"] == 1 and s["dates"] == 2 and s["dates_b90_better"] == 1 and s["dates_b90_worse"] == 1
    assert s["mean_pp"] == pytest.approx(100 * (0.01 - 0.005) / 3 / 0.5) and s["total_sol"] == pytest.approx(0.005)
    assert s["ci90_date_lo_pp"] <= s["mean_pp"] <= s["ci90_date_hi_pp"] + 1e-9
    rng = np.random.default_rng(1)
    di = rng.integers(0, 2, size=(1000, 2))
    dsum, dn = np.array([0.01, -0.005]), np.array([2, 1])
    ref = np.percentile(dsum[di].sum(1) / dn[di].sum(1), 5)
    assert s["ci90_date_lo_pp"] == pytest.approx(100 * ref / 0.5)


def _prow(block, day, cap_pnl, b90_pnl, b90_type, cap_type="deadline", pick="", primary="cap"):
    r = dict(block=block, day=day, status="filled", fee=55_000.0, pick=pick)
    prim, alt, ptype, atype = (cap_pnl, b90_pnl, cap_type, b90_type) if primary == "cap" else (b90_pnl, cap_pnl, b90_type, cap_type)
    for leg in cps.LEGS:
        r["pnl_" + leg], r["pnl_alt_" + leg] = prim, alt
    r["exit_type"], r["alt_exit_type"] = ptype, atype
    return r


@pytest.mark.parametrize("primary", ["cap", "boost90"])
def test_paired_report_is_always_b90_minus_cap_whichever_arm_is_primary(primary):
    cfg = dataclasses.replace(cps.Config(), exit_mode=primary, paired=True)
    rows = [_prow(cps.BLOCK_P2, "d1", 100.0, 5e7 + 100.0, "boost", pick="pick", primary=primary), _prow(cps.BLOCK_P2, "d2", 100.0, 100.0, "deadline", pick="non_pick", primary=primary),
            _prow(cps.BLOCK_P1A, "d3", 100.0, 100.0 - 2.5e7, "boost", pick="non_pick", primary=primary)]
    rep = cps.paired_report(rows, cfg, have_picks=True)
    a = rep["books"]["all"]
    assert a["P2-P4"]["flat"]["total_sol"] == pytest.approx(0.05) and a["P2-P4"]["flat"]["b90_fires"] == 1 and a["P1"]["flat"]["total_sol"] == pytest.approx(-0.025)
    assert a["all"]["press"]["n_attempts"] == 3 and a["all"]["flat"]["b90_fires"] == 2
    p = rep["books"]["picks"]
    assert p["P2-P4"]["flat"]["n_attempts"] == 1 and p["P2-P4"]["flat"]["mean_pp"] == pytest.approx(100 * 0.05 / 0.5) and "P1" not in p


# --- end to end on the zst layout ----------------------------------------------------------------------------------------------------------

BASE = 1_000_000
DAY = "2026-08-15"  # 8,657.5 slots an hour: k = 3, the 300 s cap is 721 slots


def write_keeper_fixture(tmp_path, cadence=20):
    """One mint, one pool, V = 17.6 SOL. Others print at base+1 (s0), base+600 and base+650; the keeper buys 0.6 SOL x 29 from base+8 every `cadence` slots; a late print makes
    the mint uncensored. Reserves are the constant-product tape for those events."""
    keeper = [(BASE + 8 + i * cadence, "KEEPER", "buy", 600_000_000) for i in range(29)]
    ev = sorted([(BASE + 1, "o1", "buy", 10**9), (BASE + 600, "o2", "sell", 5 * 10**12), (BASE + 650, "o3", "buy", 2 * 10**9), (BASE + 7500, "o4", "buy", 10**9)] + keeper, key=lambda e: e[0])
    t = _cp_tape(ev)
    view = tmp_path / "viewK"
    _write_zst(view / "migrations" / f"migrations-{DAY}T00.jsonl.zst", [{"type": "complete", "mint": "MintK", "slot": BASE, "block_time": 1_786_000_000}])
    _write_zst(view / "migrations" / f"migrations-{DAY}T01.jsonl.zst", [{"type": "create", "mint": "Other", "slot": 1}])
    rows = []
    for tx, (e, q, b, sol, tok) in enumerate(zip(ev, t["qpre"], t["bpre"], t["sol"], t["tok"]), 1):
        r = _trade("MintK", "PoolK", e[0], e[2], int(q - V), int(b), int(sol), int(tok), tx=tx, bt=1_786_000_000 + int((e[0] - BASE) * 0.4))
        r["trader"] = e[1]
        rows.append(r)
    _write_zst(view / "trades" / f"trades-{DAY}T00.jsonl.zst", rows)
    _write_zst(view / "trades" / f"trades-{DAY}T01.jsonl.zst", [_trade("Other", "X", BASE + 7600, "buy", 1, 1, 1, 1, tx=1, venue="pump_bonding")])
    vpath = tmp_path / "v.json"
    vpath.write_text(json.dumps({"v": {"PoolK": int(V)}}))
    return view, vpath


def run_keeper(view, vpath, cfg):
    s = cps.run(fix_args(view, vpath), cfg, lambda m: None)
    return s, s.pop("_rows")


@needs_zstd
def test_end_to_end_b90_fires_at_the_keeper_print_and_the_cap_arm_rides_to_the_deadline(tmp_path):
    view, vpath = write_keeper_fixture(tmp_path)
    s, rows = run_keeper(view, vpath, dataclasses.replace(cps.Config(), exit_mode="boost90", paired=True))
    (r,) = rows
    assert r["status"] == "filled" and r["boost_found"] == 1 and r["boost_trigger_slot"] == BASE + 8 + 26 * 20  # the 27th slice
    assert r["exit_type"] == "boost" and r["alt_exit_type"] == "deadline" and r["hold_slots"] < r["alt_hold_slots"] and r["pnl"] != r["pnl_alt"]
    assert s["exit_types"]["boost"] == 1 and s["t2"]["counts"]["boost_found"] == 1
    flat = s["paired"]["books"]["all"]["P2-P4"]["flat"]
    assert flat["b90_fires"] == 1 and flat["total_sol"] == pytest.approx((r["pnl_flat"] - r["pnl_alt_flat"]) / 1e9)
    # the cap-primary run is the same pair, same sign
    s2, rows2 = run_keeper(view, vpath, dataclasses.replace(cps.Config(), paired=True))
    assert rows2[0]["pnl"] == r["pnl_alt"] and rows2[0]["pnl_alt"] == r["pnl"] and s2["paired"]["books"]["all"]["P2-P4"]["flat"]["total_sol"] == pytest.approx(flat["total_sol"])


@needs_zstd
def test_end_to_end_a_cut_run_removes_the_keepers_late_buys_and_b90_equals_the_cap(tmp_path):
    view, vpath = write_keeper_fixture(tmp_path)
    s, rows = run_keeper(view, vpath, dataclasses.replace(cps.Config(), exit_mode="boost90", paired=True, boost_cut=0.6))
    (r,) = rows
    assert r["boost_cut_dropped"] == 12 and r["boost_cut_keeper"] == 1 and r["boost_found"] == 0
    assert r["exit_type"] == r["alt_exit_type"] == "deadline" and r["pnl"] == r["pnl_alt"]
    assert s["t2"]["counts"]["prints_removed"] == 12 and s["paired"]["books"]["all"]["P2-P4"]["flat"]["mean_pp"] == 0.0
    # the cap book itself moved: the price path is no longer the tape's
    base_s, base_rows = run_keeper(view, vpath, cps.Config())
    assert base_rows[0]["pnl"] != r["pnl_alt"] and "t2" not in base_s and "paired" not in base_s


@needs_zstd
def test_the_default_run_does_not_read_the_trader_field_and_writes_the_phase_2_header(tmp_path, monkeypatch):
    view, vpath = write_keeper_fixture(tmp_path)
    calls = []
    real = cps.trader_id
    monkeypatch.setattr(cps, "trader_id", lambda x: calls.append(x) or real(x))
    s, rows = run_keeper(view, vpath, cps.Config())
    assert calls == [] and "boost_found" not in rows[0]
    s["_rows"] = rows
    cps.write_outputs(s, tmp_path / "d")
    assert read_rows_csv(tmp_path / "d" / "rows.csv")[0].keys() == dict.fromkeys(cps.ROW_COLUMNS).keys()


@needs_zstd
def test_cli_t2_flags_write_the_extra_columns_and_the_paired_summary(tmp_path):
    view, vpath = write_keeper_fixture(tmp_path)
    out = tmp_path / "cli"
    assert cps.main(["--p2-view-dir", str(view), "--vmap", str(vpath), "--out-dir", str(out), "--exit-mode", "boost90", "--paired", "--boost-cut", "0.8"]) == 0
    c = json.loads((out / "summary.json").read_text())
    assert (c["config"]["exit_mode"], c["config"]["boost_cut"], c["config"]["paired"]) == ("boost90", 0.8, True)
    assert c["t2"]["counts"]["prints_removed"] == 6 and "paired" in c and c["paired"]["books"]["all"]["all"]["flat"]["n_attempts"] == 1
    row = read_rows_csv(out / "rows.csv")[0]
    assert set(cps.ROW_COLUMNS_T2) <= set(row) and row["boost_cut_dropped"] == "6" and row["exit_type"] == row["alt_exit_type"]
    assert cps.main(["--p2-view-dir", str(view), "--vmap", str(vpath), "--out-dir", str(tmp_path / "bad"), "--boost-cut", "1.5"]) == 2
