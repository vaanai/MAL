"""Tests for floor_cpmm_replay.py (FLOOR-CPMM step 3). SYNTHETIC fixtures only: no real fetch output, tape or ledger is read, no network.

Fixture pool: q0 = 20 SOL, r0 = 1e15 raw tokens, supply S = 1.13 r0, pool fee 2500 ppm, no creator fee. A sell of 0.125 r0 tokens moves spot by (1/1.125)^2 = 0.79 (< 0.80, G8)
and leaves excess = q - k/S about 0.08 SOL (E1) with k / S about 17.7 SOL (E2). Reserves follow x*y = k exactly (no fee leaves the reserve in the fixture).
"""
import json
import math
import sys
from fractions import Fraction as Fr
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))

import floor_cpmm_replay as R  # noqa: E402
from tools import arena_stack as F  # noqa: E402
from tools import launchlab_cpmm_fetch as C  # noqa: E402

WSOL = C.WSOL
TOK = "TokenMint1111111111111111111111111111111111"
M0 = R.WIN_START
CUT = M0 + 20 * 86400
H = 3600
Q0, R0 = 20_000_000_000, 10**15
S0 = int(1.13 * R0)
FEE_PPM = 2500


def meta_ok(**kw):
    m = {"token_mint": TOK, "mint0": WSOL, "mint1": TOK, "wsol_in_pool": True, "supply": S0, "mint_authority": None, "freeze_authority": None, "transfer_fee": False,
         "lp_locked_frac": 1.0, "amm_config": "CfgA", "trade_fee_rate": FEE_PPM, "creator_fee_rate": 0}
    m.update(kw)
    return m


class Fx:
    """A pool of hand-placed swaps. t = seconds after migration; slot = 1000 + 2.5 t (0.4 s slots)."""

    def __init__(self, pool="P1", meta=None, q=Q0, r=R0):
        self.pool, self.meta, self.q, self.r = pool, meta or meta_ok(), q, r
        self.rows: list[dict] = []
        self.n = 0
        self.t_end = 225 * H  # the fetch window: min(M + 225 h, run_end) as seconds after M

    def slot(self, t):
        return 1000 + int(round(t * 2.5))

    def swap(self, t, side, amt, slot=None):
        """side 'buy' (WSOL in, amt lamports) or 'sell' (token in, amt raw)."""
        buy = side == "buy"
        in_res, out_res = (self.q, self.r) if buy else (self.r, self.q)
        out = out_res * amt // (in_res + amt)
        self.n += 1
        row = {"v": 1, "venue": "raydium_cpmm", "type": "swap", "slot": self.slot(t) if slot is None else slot, "block_time": M0 + t, "signature": f"sig{self.n:06d}", "event_index": 0,
               "pool_query": self.pool, "pool": self.pool, "input_vault_before": in_res, "output_vault_before": out_res, "input_amount": amt, "output_amount": out,
               "input_transfer_fee": 0, "output_transfer_fee": 0, "base_input": True, "input_mint": WSOL if buy else TOK, "output_mint": TOK if buy else WSOL,
               "trade_fee": math.ceil(amt * FEE_PPM / 1e6), "creator_fee": 0, "creator_fee_on_input": False}
        self.rows.append(row)
        if buy:
            self.q, self.r = self.q + amt, self.r - out
        else:
            self.r, self.q = self.r + amt, self.q - out
        return row

    def trigger_prelude(self, t):
        """Two prints so that a drained sell at t passes G6 (print within 6 h), G7 (volume) and has a >= 6 h reference print for G8."""
        self.swap(t - 20 * H, "buy", 100_000_000)
        self.swap(t - 3 * H, "buy", 1_000)

    def drain(self, t):
        return self.swap(t, "sell", int(0.125 * self.r))

    def head(self):
        t_end = self.t_end
        return {"v": 1, "venue": "raydium_cpmm", "type": "pool_meta", "src": "backfill", "slot": 1, "t_recv_ms": M0 * 1000, "block_time": M0, "signature": "mig", "event_index": 0,
                "cpmm_pool": self.pool, "window": [M0, M0 + t_end], "window_mode": True, "pages": 1, "txs": len(self.rows), "dropped_outside": 0, "rows": len(self.rows),
                "truncated": False, "covered_to_bt": M0 + t_end}


def write_fetch(d: Path, fixtures: list[Fx], credits=123, cp_extra=None, skip_cp=(), truncated=()):
    (d / "pools").mkdir(parents=True, exist_ok=True)
    cp = {"version": 1, "credits_used": credits, "hours": {}, "migrations": {}, "window": {}}
    migs = []
    metas = {}
    for f in fixtures:
        head = f.head()
        if f.pool in truncated:
            head["truncated"] = True
        with open(d / "pools" / f"pool-{f.pool}.jsonl", "w") as fh:
            for r in [head, *f.rows]:
                fh.write(json.dumps(r) + "\n")
        if f.pool not in skip_cp:
            cp["window"][f.pool] = {"pool": f.pool, "status": "ok", "block_time": M0, "credits": 1, "pages": 1, "truncated": f.pool in truncated}
        migs.append({"pool": f.pool, "bt": M0})
        metas[f.pool] = f.meta
    cp.update(cp_extra or {})
    (d / C.WINDOW_CP).write_text(json.dumps(cp))
    mp = d.parent / (d.name + "-migs.json")
    mp.write_text(json.dumps(migs))
    (d.parent / (d.name + "-meta.json")).write_text(json.dumps({"schema": "floor-cpmm-meta-v2", "pools": metas}))
    return mp


def meta_path(d):
    return str(d.parent / (d.name + "-meta.json"))


def run_fetch(d, cut):
    return R.run(d, C.load_window_migrations(str(d.parent / (d.name + "-migs.json"))), cut, meta_path(d))


def mkpool(f, rows=None):
    return R.Pool({"meta": f.head(), "rows": rows if rows is not None else f.rows, "pool": f.pool}, R.normalize_meta(f.meta))


def priced(tmp_path, fixtures, **kw):
    d = tmp_path / "fetch"
    write_fetch(d, fixtures, **kw)
    return run_fetch(d, CUT)


def triggered_fixture(pool="P1", t=30 * H, tail=True):
    f = Fx(pool)
    f.trigger_prelude(t)
    f.drain(t)
    if tail:  # quiet small prints, no TP
        f.swap(t + 1 * H, "buy", 1_000)
        f.swap(t + 30 * H, "buy", 1_000)
    return f


def cell(res, stake=0.25, lat=4.085):
    return res["cells"][f"{stake:.2f}@{lat}"]


# ------------------------------------------------------------------------------------------------------------------------ round trip, hand computed
def exact_round_trip(stake, Qe, Be, fee, Qx, Bx, h_in=Fr(2608, 10**6), h_out=Fr(16, 10000), send=505_000):
    stake, Qe, Be, Qx, Bx = map(Fr, (stake, Qe, Be, Qx, Bx))
    net = stake * (1 - fee)
    tok = Be * net / (Qe + net)
    tokh = tok / (1 + h_in)
    Qa, Ba = Qx + net, Bx - tok
    gross = tokh * Qa / (Ba + tokh)
    sv = gross * (1 - fee) * (1 - h_out)
    return sv - stake - 2 * send


def test_round_trip_matches_hand_computation_to_the_lamport():
    rt = R.cpmm_round_trip(250_000_000, 20_000_000_000, 10**15, 2e-5, 19_200_000_000, 1_050_000_000_000_000, 0.0025)
    assert rt["filled"] and not rt["stranded"] and not rt["guarded"]
    assert round(rt["pnl"]) == -24_411_411  # computed once by hand (Fractions): sv 226,598,589.355 - stake - 2 x 505,000
    assert abs(rt["pnl"] - float(exact_round_trip(250_000_000, 20_000_000_000, 10**15, Fr(25, 10000), 19_200_000_000, 1_050_000_000_000_000))) < 1e-3
    assert abs(rt["tokens"] - 12315194913423.254) < 1e-2


def test_round_trip_parity_with_arena_stack_where_the_fee_tiers_agree():
    # at a huge market cap both PumpSwap tiers are 3000 ppm; with the pool fee 0.003 and Q >= V_LO the two round trips are the same function
    for qx, bx in ((39.5e9, 0.95e9), (37.5e9, 1.01e9), (35.5e9, 1.1e9)):  # token units are small here so the PumpSwap mcap tier is the top one
        Qe, Be = 20e9 + 17.5e9, 1e9
        assert F.fee_ppm(Qe, Be) == 3000 and F.fee_ppm(qx, bx) == 3000
        pn, fi = F.round_trip(250_000_000, Qe, Be, Qe / Be, qx, bx)
        rt = R.cpmm_round_trip(250_000_000, Qe, Be, Qe / Be, qx, bx, 0.003)
        assert bool(fi) == rt["filled"]
        assert abs(float(pn) - rt["pnl"]) < 1e-3


def test_round_trip_guard_and_stranding():
    g = R.cpmm_round_trip(250_000_000, 20e9, 1e15, 1e-5, 20e9, 1e15, 0.0025)  # decision spot far below the landing price: guard
    assert not g["filled"] and g["guarded"] and g["pnl"] == -505_000
    s = R.cpmm_round_trip(250_000_000, 20e9, 1e15, 2e-5, 20e9, 1e15 * 1e-9, 0.0025)  # exit pool holds fewer tokens than we bought: C2, stranded
    assert s["stranded"] and s["exit_cause"] == 2 and s["pnl"] == -250_000_000 - 505_000 - 2_039_280
    k = R.cpmm_round_trip(250_000_000, 20e9, 1e15, 2e-5, 10e9, 1e15, 0.0025)  # constant product fell by half: C8
    assert k["stranded"] and k["exit_cause"] == 8
    b = R.cpmm_round_trip(250_000_000, 0.0, 1e15, 2e-5, 20e9, 1e15, 0.0025)  # Q = 0 is an invalid entry state (replaces V_LO)
    assert b["entry_invalid"] and b["stranded"]


# ------------------------------------------------------------------------------------------------------------------------ clock, slot_s, lag
def test_slot_s_estimator_pooled_and_fallback():
    h0 = M0
    pairs = [(h0 + 10, 5000), (h0 + 3600 + 5, 5000 + 8800), (h0 + 7200 + 1, 5000 + 17600)]  # 3595 s / 8800 slots = 0.4085 s
    ck = R.Clock([], pairs)
    assert ck.slot_s(h0 + 100) == pytest.approx(3595 / 8800)
    assert ck.slot_s(h0 + 5 * 3600) == 0.4  # no pair
    ck2 = R.Clock([], [(h0 + 10, 5000), (h0 + 3600 + 5, 5100), (h0 + 3600 + 6, 5101)])
    assert ck2.slot_s(h0) == pytest.approx(3595 / 100)
    ck3 = R.Clock([], [(h0 + 10, 5000), (h0 + 1000, 5100)])  # b missing
    assert ck3.slot_s(h0) == 0.4
    ck4 = R.Clock([], [(h0 + 10, 5000), (h0 + 3600 + 15, 5100)])
    ck4.bts[1] = h0 + 3600 + 15  # span >= 1800 but a single pair: fine
    assert ck4.slot_s(h0) == pytest.approx(3605 / 100)


def test_clock_slot_and_exit_lag():
    h0 = M0
    ck = R.Clock([], [(h0 + 10, 5000), (h0 + 3600 + 5, 5000 + 8800), (h0 + 3600 + 5, 5000 + 8801), (h0 + 7200 + 1, 5000 + 17600)])
    ss = ck.slot_s(h0 + 3600 + 100)
    T = h0 + 3600 + 105.9
    assert ck.clock_slot(T) == 5000 + 8801 + round((T - (h0 + 3600 + 5)) / ss)  # latest pooled swap with bt <= T
    assert R.exit_lag(0.4) == 2 and R.exit_lag(0.2) == 3 and R.exit_lag(0.55) == 2 and R.exit_lag(1.0) == 2  # max(2, ceil(0.55 / s - 1e-9)), no assert
    assert R.exit_lag(0.55 / 3) == 3


# ------------------------------------------------------------------------------------------------------------------------ entry, exits, fills
def test_drained_pool_triggers_and_time_exit_matches_hand_calc(tmp_path):
    f = triggered_fixture()
    res = priced(tmp_path, [f])
    assert res["universe"] == 1 and res["exclusions"] == {}
    c = cell(res)
    assert c["flat"]["trades"] == 1 and c["pools_triggered"] == 1 and c["exit_reasons"] == {"tp_1.5x": 0, "time_24h": 1}
    # hand calc: entry state = reserves after the drain (the next prints are tiny), exit state after the quiet swaps
    drain = f.rows[2]
    qa, ra = drain["input_vault_before"], drain["output_vault_before"]  # r before, q before
    q_after, r_after = ra - drain["output_amount"], qa + drain["input_amount"]
    last = f.rows[-1]  # the print at +30 h is after the exit decision (+24 h): the exit state is the state before it
    q_x, r_x = last["input_vault_before"], last["output_vault_before"]
    want = exact_round_trip(250_000_000, q_after, r_after, Fr(FEE_PPM, 10**6), q_x, r_x)
    flat = c["flat"]
    assert flat["mean_sol"] * 1e9 == pytest.approx(float(Fr(85, 100) * want - Fr(15, 100) * 505_000), abs=1.0)  # flat leg: (1-f) P - f send
    assert c["filled_no_fail_mean_sol"] * 1e9 == pytest.approx(float(want), abs=1.0)


def test_never_triggering_pool(tmp_path):
    f = Fx("P2")
    f.trigger_prelude(30 * H)
    f.swap(30 * H, "sell", int(0.01 * f.r))  # spot falls < 2 %, G8 fails
    f.swap(31 * H, "buy", 1_000)
    res = priced(tmp_path, [f])
    c = cell(res)
    assert c["flat"]["trades"] == 0 and c["pools_triggered"] == 0 and c["rows_closed"] == 0
    assert res["kill_line"]["verdict"] == "NOT_DECIDABLE"


@pytest.mark.parametrize("what", ["age_low", "age_high", "depth", "alive", "volume"])
def test_gates_block(tmp_path, what):
    f = Fx("G")
    if what == "age_low":
        t = 20 * H
        f.trigger_prelude(t + 0)
        f.rows.clear(); f.q, f.r = Q0, R0
        f.swap(t - 10 * H, "buy", 100_000_000); f.swap(t - 3 * H, "buy", 1_000)
        f.drain(t)
    elif what == "age_high":
        t = 201 * H
        f.trigger_prelude(t); f.drain(t)
    elif what == "depth":  # k / S below 10 SOL
        f.meta = meta_ok(supply=int(3 * R0))
        f.trigger_prelude(30 * H); f.drain(30 * H)
    elif what == "alive":  # previous print 7 h back
        f.swap(10 * H, "buy", 100_000_000); f.swap(23 * H, "buy", 1_000); f.drain(30 * H)
    else:  # 24 h volume below 1 SOL: tiny reference print and a small drain
        f.swap(10 * H, "buy", 1_000); f.swap(27 * H, "buy", 1_000)
        f.swap(30 * H, "sell", int(0.125 * f.r))
        f.rows[-1]["output_amount"] = 1  # volume counts the WSOL leg: this one is tiny
    res = priced(tmp_path, [f])
    assert cell(res)["flat"]["trades"] == 0 and cell(res)["pools_triggered"] == 0, what


def test_take_profit_fires_on_a_big_buy(tmp_path):
    f = triggered_fixture(tail=False)
    f.swap(31 * H, "buy", 5_000_000_000)  # spot up by (25/20)^2 = 1.56 > 1.5 x the post-buy mark
    f.swap(31 * H + 5, "buy", 1_000)
    c = cell(priced(tmp_path, [f]))
    assert c["exit_reasons"] == {"tp_1.5x": 1, "time_24h": 0} and c["flat"]["trades"] == 1


def test_tp_exit_state_uses_the_declared_lag(tmp_path):
    f = triggered_fixture(tail=False)
    big = f.swap(31 * H, "buy", 5_000_000_000)
    inside = f.swap(31 * H + 0.8, "buy", 200_000_000)  # slot +2: inside the lag of 2 slots
    f.swap(31 * H + 1.2, "buy", 400_000_000)  # slot +3: outside
    assert inside["slot"] == big["slot"] + 2
    P = mkpool(f)
    ck = R.Clock([P], list(zip(P.bts, P.slots)))
    out = R.simulate_pool(P, ck, 0.25, 4.085, CUT)
    row = out["rows"][0]
    assert row["reason"] == "tp_1.5x"
    j = P.state_at_slot(big["slot"] + 2)
    assert P.slots[j] == inside["slot"] and j == len(P.slots) - 2
    net = 0.25e9 * (1 - 0.0025)
    jx = P.state_at_slot(P.slots[P.bts.index(M0 + 31 * H)] + R.exit_lag(0.4))
    rt = R.cpmm_round_trip(0.25e9, float(P.q[P.state_at_slot(row["landing_slot"])]), float(P.r[P.state_at_slot(row["landing_slot"])]), P.spot[P.slots.index(row["entry_slot"])],
                           float(P.q[jx]), float(P.r[jx]), 0.0025)
    assert row["pnl"] == pytest.approx(rt["pnl"], abs=1e-6) and net > 0


def test_no_later_swap_entry_and_exit_priced_at_last_known_state(tmp_path):
    f = Fx("L")
    f.trigger_prelude(30 * H)
    f.drain(30 * H)  # the drain is the pool's LAST swap: landing slot has no later swap
    res = priced(tmp_path, [f])
    c = cell(res)
    assert c["flat"]["trades"] == 1 and c["unfilled_guarded"] == 0 and c["unfilled_after_cut"] == 0
    qa, ra = f.q, f.r  # last known state, entry and exit alike
    rt = R.cpmm_round_trip(0.25e9, float(qa), float(ra), qa / ra, float(qa), float(ra), 0.0025)
    assert c["filled_no_fail_mean_sol"] * 1e9 == pytest.approx(rt["pnl"], abs=1.0)


def test_guarded_buy_pays_one_send(tmp_path):
    f = Fx("GD")
    f.trigger_prelude(30 * H)
    f.drain(30 * H)
    f.swap(30 * H + 0.4, "buy", 6_000_000_000)  # lands inside the latency: price +30 %, over the 1.15 guard
    res = priced(tmp_path, [f])
    c = cell(res)
    assert c["flat"]["trades"] == 0 and c["unfilled_guarded"] == 1
    assert c["flat"]["guard_cost_sol"] == pytest.approx(-505_000 / 1e9)


def test_entry_after_cut_is_unfilled_and_open_at_run_end_is_reported_separately(tmp_path):
    # entry decision before the cut but landing after it
    f2 = Fx("C2")
    base = 190 * H
    f2.trigger_prelude(base)
    f2.drain(base)
    cut_near = M0 + base + 3  # decision at base, landing at +1.9 s (< 3: fills), exit at +24 h > cut: open at run end
    f2.t_end = base + 3  # the fetch ran to the same cut
    d = tmp_path / "fetch"
    write_fetch(d, [f2])
    c = cell(run_fetch(d, cut_near), lat=1.9)
    assert c["open_at_run_end"] == 1 and c["flat"]["trades"] == 0 and c["rows_closed"] == 0
    cut_early = M0 + base + 1  # landing (+1.9 s) after the cut
    f2.t_end = base + 1
    d2 = tmp_path / "fetch2"
    write_fetch(d2, [f2])
    c2 = cell(run_fetch(d2, cut_early), lat=1.9)
    assert c2["unfilled_after_cut"] == 1 and c2["open_at_run_end"] == 0


def test_reentry_after_cooldown_and_blocked_inside_it(tmp_path):
    def build(second_dt):
        f = Fx("RE")
        f.trigger_prelude(30 * H)
        f.drain(30 * H)
        T = 30 * H + 86400 + 1.9  # first time exit decision
        f.swap(T - 3 * H, "buy", 1_000)
        f.swap(second_dt + 30 * H + 86400, "sell", int(0.125 * f.r))
        return f

    # 30 s after the exit decision (cooldown 60 s): blocked; 62 s after: allowed
    one = cell(priced(tmp_path / "a", [build(30)]))["flat"]["trades"]
    two = cell(priced(tmp_path / "b", [build(62)]))["flat"]["trades"]
    assert (one, two) == (1, 2)


def test_pool_blocked_while_position_open(tmp_path):
    f = triggered_fixture(tail=False)
    f.swap(30 * H + 600, "sell", int(0.125 * f.r))  # a second drain-like sell while the position is open
    assert cell(priced(tmp_path, [f]))["flat"]["trades"] == 1


# ------------------------------------------------------------------------------------------------------------------------ universe vetoes, fee audit
@pytest.mark.parametrize("meta,why", [({"freeze_authority": "Frz"}, "freeze_authority"), ({"transfer_fee": True}, "transfer_fee"), ({"mint_authority": "Mnt"}, "mint_authority"),
                                      ({"lp_locked_frac": 0.5}, "lp_not_locked")])
def test_universe_vetoes(tmp_path, meta, why):
    f = triggered_fixture()
    f.meta = meta_ok(**meta)
    res = priced(tmp_path, [f])
    assert res["exclusions"] == {why: 1} and res["universe"] == 0 and cell(res)["flat"]["trades"] == 0


def test_missing_meta_key_excludes_not_prices(tmp_path):
    m = meta_ok()
    del m["freeze_authority"]
    f = triggered_fixture()
    f.meta = m
    res = priced(tmp_path, [f])
    assert res["exclusions"] == {"meta_missing:freeze_authority": 1} and res["universe"] == 0


def test_fee_mismatch_excluded_and_counted(tmp_path):
    f = triggered_fixture()
    f.meta = meta_ok(trade_fee_rate=2000)  # chain says 2500
    res = priced(tmp_path, [f])
    assert res["exclusions"] == {"fee_mismatch": 1} and cell(res)["flat"]["trades"] == 0
    assert res["fee_audit_by_amm_config"]["CfgA"]["excluded_fee"] == 1


def test_fee_unverified_when_no_swap_carries_fee_fields(tmp_path):
    f = triggered_fixture()
    for r in f.rows:
        for k in ("trade_fee", "creator_fee", "creator_fee_on_input"):
            r.pop(k)
    assert priced(tmp_path, [f])["exclusions"] == {"fee_unverified": 1}


# ------------------------------------------------------------------------------------------------------------------------ fail legs and statistics
def test_fail_legs_are_arena_stack_fail_legs(tmp_path):
    f = triggered_fixture()
    c = cell(priced(tmp_path, [f]))
    pnl = c["filled_no_fail_mean_sol"] * 1e9
    flat, press, _c, _p = F.fail_legs(np.array([pnl]), np.array([True]), np.array([0.0]), np.array([0.0]))
    assert c["flat"]["mean_sol"] * 1e9 == pytest.approx(float(flat[0]), abs=1.0)
    assert c["press"]["mean_sol"] * 1e9 == pytest.approx(float(press[0]), abs=1.0)  # one fill: the intercept refit sets p = 0.289 exactly
    assert c["press"]["mean_sol"] * 1e9 == pytest.approx((1 - 0.289) * pnl - 0.289 * 505_000, abs=1.0)


def test_ssb_and_nearby_count_cpmm_buys(tmp_path):
    f = Fx("SB")
    f.trigger_prelude(30 * H)
    f.drain(30 * H)
    # lat 4.085 s at 0.4 s slots: k = round(10.2) = 10 slots; landing slot = drain slot + 10 = 30 h + 4 s
    f.swap(30 * H + 3.2, "buy", 1_000, slot=f.slot(30 * H) + 8)
    f.swap(30 * H + 4, "buy", 1_000, slot=f.slot(30 * H) + 10)
    f.swap(30 * H + 4, "buy", 2_000, slot=f.slot(30 * H) + 10)
    f.swap(30 * H + 4, "sell", 1_000, slot=f.slot(30 * H) + 10)  # a sell: not counted
    P = mkpool(f)
    ck = R.Clock([P], list(zip(P.bts, P.slots)))
    row = R.simulate_pool(P, ck, 0.25, 4.085, CUT)["rows"][0]
    assert row["landing_slot"] == f.slot(30 * H) + 10
    assert row["ssb"] == 2.0
    assert row["nearby"] == 3_000 + 1_000  # slots [x - round(2 / 0.4), x] = [x - 5, x]: the slot x-2 buy is in, nothing earlier


def test_kill_line_words():
    def mk(mean_f, mean_p, ex_f, ex_p, trades, tpd):
        return {"stake_sol": 0.25, "lat_s": 4.085, "flat": {"trades": trades, "mean_sol": mean_f, "total_ex_top3_incl_guard_sol": ex_f, "trades_per_day": tpd},
                "press": {"trades": trades, "mean_sol": mean_p, "total_ex_top3_incl_guard_sol": ex_p, "trades_per_day": tpd}}
    assert R.kill_line(mk(0.1, 0.1, 1, 1, 25, 0.9))["verdict"] == "PASS_IN_SAMPLE"
    assert R.kill_line(mk(0.1, -0.1, 1, 1, 25, 0.9))["verdict"] == "KILLED"  # K1 needs both legs
    assert R.kill_line(mk(0.1, 0.1, 1, -1, 25, 0.9))["verdict"] == "KILLED"  # K4 needs both legs
    assert R.kill_line(mk(0.1, 0.1, 1, 1, 25, 0.2))["verdict"] == "KILLED"  # K2
    assert R.kill_line(mk(0.1, 0.1, 1, 1, 19, 0.9))["verdict"] == "NOT_DECIDABLE"  # K3: a stop, not a pass
    assert R.kill_line(mk(None, None, None, None, 0, 0.0))["verdict"] == "NOT_DECIDABLE"


def test_leg_stats_days_top3_pool_and_boot():
    rows = [{"filled": True, "day": f"2026-11-0{1 + i % 2}", "pool": f"p{i % 3}", "mig_day": f"2026-10-2{i % 4}"} for i in range(8)]
    leg = np.array([1e9, 1e9, 1e9, 1e9, -1e9, 2e9, 3e9, -0.5e9])
    s = R.leg_stats(rows, leg, 10.0)
    assert s["trades"] == 8 and s["total_sol"] == pytest.approx(7.5)
    assert s["total_ex_top3_sol"] == pytest.approx(7.5 - 6.0) and s["distinct_days"] == 2
    assert s["positive_days"] == sum(1 for v in s["per_day_sol"].values() if v > 0) and s["trades_per_day"] == pytest.approx(0.8)
    assert s["boot_lo_sol"] == pytest.approx(F.boot_lo(leg) / 1e9)


# ------------------------------------------------------------------------------------------------------------------------ refusals (DECLARE s8) and the CLI
def run_main(d, mp, capsys, run_end="2026-11-14T01:00:00Z", out=None):
    argv = ["--fetch-dir", str(d), "--migrations", str(mp), "--meta", meta_path(d), "--run-end", run_end] + (["--out", str(out)] if out else [])
    rc = R.main(argv)
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def test_cli_prices_and_prints_json_and_summary(tmp_path, capsys):
    f = triggered_fixture()
    d = tmp_path / "fetch"
    mp = write_fetch(d, [f])
    rc, out, err = run_main(d, mp, capsys, out=tmp_path / "res.json")
    assert rc == 0
    res = json.loads(out)
    assert res == json.loads((tmp_path / "res.json").read_text())
    assert res["kill_line"]["verdict"] == "NOT_DECIDABLE" and "IN-SAMPLE" in res["statement"] and res["credits_used"] == 123
    assert set(res["cells"]) == {"0.10@1.9", "0.10@4.085", "0.25@1.9", "0.25@4.085"}
    import hashlib
    assert res["meta_sha256"] == hashlib.sha256(Path(meta_path(d)).read_bytes()).hexdigest() and "CURRENT-STATE" in res["statement"] and "NOT computed" in res["statement"]
    assert "VERDICT NOT_DECIDABLE" in err and "makes money" in err and res["reserve_variants"]["raw_vault"].startswith("not_computed (declared deviation")
    assert res["window"]["D_days"] == pytest.approx((R.iso_ts("2026-11-14T01:00:00Z") - R.WIN_START) / 86400)


def test_refuses_missing_pool_in_checkpoint(tmp_path, capsys):
    a, b = triggered_fixture("A"), triggered_fixture("B")
    d = tmp_path / "fetch"
    mp = write_fetch(d, [a, b], skip_cp=("B",))  # the credit cap stopped before B
    rc, out, err = run_main(d, mp, capsys)
    assert rc == 2 and out == "" and "NOT_DECIDABLE" in err


def test_refuses_truncated_pool(tmp_path, capsys):
    a = triggered_fixture("A")
    d = tmp_path / "fetch"
    mp = write_fetch(d, [a], truncated=("A",))
    rc, out, err = run_main(d, mp, capsys)
    assert rc == 2 and "truncated" in err


def test_refuses_head_truncated_even_if_checkpoint_clean(tmp_path, capsys):
    a = triggered_fixture("A")
    d = tmp_path / "fetch"
    mp = write_fetch(d, [a], truncated=("A",))
    cp = json.loads((d / C.WINDOW_CP).read_text())
    cp["window"]["A"]["truncated"] = False
    (d / C.WINDOW_CP).write_text(json.dumps(cp))
    rc, _o, err = run_main(d, mp, capsys)
    assert rc == 2 and "head says truncated" in err


def test_refuses_missing_pool_file_and_partial_file(tmp_path, capsys):
    a = triggered_fixture("A")
    d = tmp_path / "fetch"
    mp = write_fetch(d, [a])
    (d / "pools" / "pool-A.jsonl").rename(d / "pools" / "pool-A.part")
    assert run_main(d, mp, capsys)[0] == 2
    (d / "pools" / "pool-A.part").unlink()
    assert run_main(d, mp, capsys)[0] == 2


@pytest.mark.parametrize("key", ["cap_reached", "cap_stop", "stopped"])
def test_refuses_checkpoint_cap_stop(tmp_path, capsys, key):
    a = triggered_fixture("A")
    d = tmp_path / "fetch"
    mp = write_fetch(d, [a], cp_extra={key: True})
    rc, _o, err = run_main(d, mp, capsys)
    assert rc == 2 and "cap_stop" in err


def test_refuses_missing_checkpoint_and_other_run_checkpoint(tmp_path, capsys):
    a = triggered_fixture("A")
    d = tmp_path / "fetch"
    mp = write_fetch(d, [a])
    (d / "checkpoint.json").write_text("{}")
    assert run_main(d, mp, capsys)[0] == 2
    (d / "checkpoint.json").unlink()
    (d / C.WINDOW_CP).unlink()
    assert run_main(d, mp, capsys)[0] == 2


def test_refuses_fetch_dir_under_forbidden_root(tmp_path, capsys, monkeypatch):
    a = triggered_fixture("A")
    d = tmp_path / "sealed" / "fetch"
    mp = write_fetch(d, [a])
    monkeypatch.setattr(C, "WINDOW_FORBIDDEN_OUT", (str(tmp_path / "sealed"),))
    rc, _o, err = run_main(d, mp, capsys)
    assert rc == 2 and "sealed_root" in err


def test_real_forbidden_roots_are_refused_by_name(tmp_path):
    for root in ("/data2/mal/venue-tape-backfill/launchlab_cpmm", "/data2/mal/venue-tape", "/data/mal/arena/tape", "/data2/mal/arena-tip-feed/hv7"):
        with pytest.raises(R.Refused):
            R.check_fetch_dir(Path(root))


def test_refuses_run_end_not_after_window_start(tmp_path, capsys):
    a = triggered_fixture("A")
    d = tmp_path / "fetch"
    mp = write_fetch(d, [a])
    assert run_main(d, mp, capsys, run_end="2026-10-24T01:00:00Z")[0] == 2


def test_zst_pool_file_is_read(tmp_path):
    import shutil
    import subprocess

    if not shutil.which("zstd"):
        pytest.skip("no zstd")
    f = triggered_fixture("Z")
    d = tmp_path / "fetch"
    write_fetch(d, [f])
    p = d / "pools" / "pool-Z.jsonl"
    subprocess.run(["zstd", "-q", "--rm", str(p)], check=True)
    rows = R.read_rows(Path(str(p) + ".zst"))
    assert rows[0]["type"] == "pool_meta" and len(rows) == len(f.rows) + 1
    assert cell(run_fetch(d, CUT))["flat"]["trades"] == 1


# ------------------------------------------------------------------------------------------------------------------------ reserve rebuild
def test_reserves_after_swap_are_next_vault_before_unless_a_nonswap_ix_sits_between():
    f = Fx("RS")
    a = f.swap(1 * H, "buy", 1_000_000_000)
    b = f.swap(2 * H, "buy", 500_000_000)
    P = mkpool(f)
    assert (P.q[0], P.r[0]) == (b["input_vault_before"], b["output_vault_before"])
    assert (P.q[1], P.r[1]) == (f.q, f.r)  # last swap: derived from its own amounts (the fixture reserves carry no fee leak, so equal)
    # a liquidity change between the swaps: the state after swap 0 is derived from swap 0's amounts, not read from swap 1
    rows = list(f.rows)
    rows[1] = {**rows[1], "input_vault_before": rows[1]["input_vault_before"] + 777}
    rows.insert(1, {"type": "ix", "slot": a["slot"], "signature": a["signature"] + "z", "event_index": 0, "block_time": a["block_time"], "ix": ["deposit"]})
    P2 = mkpool(f, rows)
    assert P2.n_ix == 1 and P2.q[0] == a["input_vault_before"] + a["input_amount"] and P2.r[0] == a["output_vault_before"] - a["output_amount"]


def test_derive_with_transfer_and_creator_fees():
    row = {"input_amount": 1_000, "output_amount": 400, "input_transfer_fee": 10, "output_transfer_fee": 5, "creator_fee": 7, "creator_fee_on_input": True}
    assert R.Pool._derive(row, (5_000, 9_000), True) == (5_000 + 1_000 - 10 - 7, 9_000 - 400 - 5)
    row["creator_fee_on_input"] = False
    assert R.Pool._derive(row, (5_000, 9_000), False) == (5_000 - 400 - 5 - 7, 9_000 + 1_000 - 10)


# ------------------------------------------------------------------------------------------------------------------------ fix round (QP / reviewer on 2911ac79)
def test_universe_fields_come_from_meta_not_from_the_fetch_head(tmp_path):
    f = triggered_fixture()
    d = tmp_path / "fetch"
    write_fetch(d, [f])
    head = json.loads((d / "pools" / "pool-P1.jsonl").read_text().splitlines()[0])
    assert "freeze_authority" not in head and "supply" not in head  # the fixture head carries none: only --meta can supply them
    assert cell(run_fetch(d, CUT))["flat"]["trades"] == 1
    # a head WITH a freeze authority and a clean meta: the meta wins (the head is not read for universe fields)
    lines = (d / "pools" / "pool-P1.jsonl").read_text().splitlines()
    lines[0] = json.dumps({**json.loads(lines[0]), "freeze_authority": "Frz", "supply": 1})
    (d / "pools" / "pool-P1.jsonl").write_text("\n".join(lines) + "\n")
    assert run_fetch(d, CUT)["exclusions"] == {}


def test_v1_shaped_meta_record_is_mapped_explicitly_and_never_gives_q_fields(tmp_path):
    v1 = {"pool": "P1", "S": S0, "mint_auth_null": True, "tokmint": TOK, "burned": 1, "top_share": 0.9, "q": 1, "qfloor": 2, "excess": 3}
    n = R.normalize_meta(v1)
    assert n == {"supply": S0, "mint_authority": None, "token_mint": TOK}  # q / qfloor / excess / burned / top_share are never copied
    assert R.normalize_meta({**v1, "mint_auth_null": False})["mint_authority"] == "set"
    assert R.universe_reason(n) == "meta_missing:freeze_authority"  # v1 cannot supply the rest: excluded and counted, never assumed
    f = triggered_fixture()
    d = tmp_path / "fetch"
    write_fetch(d, [f])
    Path(meta_path(d)).write_text(json.dumps([v1]))
    res = run_fetch(d, CUT)
    assert res["exclusions"] == {"meta_missing:freeze_authority": 1} and res["universe"] == 0


def test_unreadable_meta_record_is_excluded(tmp_path):
    f = triggered_fixture()
    d = tmp_path / "fetch"
    write_fetch(d, [f])
    Path(meta_path(d)).write_text(json.dumps({"pools": {"P1": {"unreadable": "mint_undecodable"}}}))
    assert run_fetch(d, CUT)["exclusions"] == {"meta_unreadable:mint_undecodable": 1}


def test_refuses_meta_pool_set_mismatch(tmp_path, capsys):
    a, b = triggered_fixture("A"), triggered_fixture("B")
    d = tmp_path / "fetch"
    mp = write_fetch(d, [a, b])
    Path(meta_path(d)).write_text(json.dumps({"pools": {"A": a.meta}}))
    rc, _o, err = run_main(d, mp, capsys)
    assert rc == 2 and "meta_pool_set_differs" in err
    Path(meta_path(d)).write_text(json.dumps({"pools": {"A": a.meta, "B": b.meta, "C": b.meta}}))
    assert run_main(d, mp, capsys)[0] == 2


def test_refuses_fetch_dir_with_a_pool_file_not_in_the_list(tmp_path, capsys):
    a, b = triggered_fixture("A"), triggered_fixture("B")
    d = tmp_path / "fetch"
    mp = write_fetch(d, [a, b])
    mp.write_text(json.dumps([{"pool": "A", "bt": M0}]))
    Path(meta_path(d)).write_text(json.dumps({"pools": {"A": a.meta}}))
    assert run_main(d, mp, capsys)[0] == 2


def test_refuses_cut_mismatch_in_either_direction(tmp_path, capsys):
    f = triggered_fixture()
    d = tmp_path / "fetch"
    mp = write_fetch(d, [f])  # fetched to min(M + 225 h, 2026-11-14T01:00Z) = M + 225 h
    assert run_main(d, mp, capsys)[0] == 0
    f.t_end = 100 * H  # the fetch stopped earlier than the pre-registered cut allows (e.g. a shorter run end)
    d2 = tmp_path / "fetch2"
    mp2 = write_fetch(d2, [f])
    rc, _o, err = run_main(d2, mp2, capsys)
    assert rc == 2 and "cut_mismatch" in err
    # a cut earlier than the fetch: min(M + 225 h, run_end) = run_end differs from the head
    rc, _o, err = run_main(d, mp, capsys, run_end="2026-10-30T00:00:00Z")
    assert rc == 2 and "cut_mismatch" in err


def test_same_slot_swaps_in_reverse_signature_order(tmp_path):
    f = Fx("SS")
    f.swap(1 * H, "buy", 1_000_000_000)
    sl = f.slot(2 * H)
    a = f.swap(2 * H, "buy", 800_000_000, slot=sl)  # executes first
    a_post = (f.q, f.r)
    b = f.swap(2 * H, "sell", 10**13, slot=sl)  # executes second
    b_post = (f.q, f.r)
    a["signature"], b["signature"] = "sig_z", "sig_a"  # the fetcher's (slot, signature) order puts b first
    f.swap(3 * H, "buy", 1_000)
    P = mkpool(f)
    ia, ib = P.slots.index(sl), P.slots.index(sl) + 1
    assert P.sw[ia] is b and P.sw[ib] is a
    assert (P.q[ia], P.r[ia]) == (b["output_vault_before"] - b["output_amount"], b["input_vault_before"] + b["input_amount"])  # b: its OWN derived post-state, not a's pre-state
    assert (P.q[ib], P.r[ib]) == b_post and b_post != a_post  # the last swap of the slot carries the exact slot-end state (= vault_before of the next, later-slot swap)
    assert P.state_at_slot(sl) == ib


def test_fee_check_sums_the_input_side_fee_within_two_lamports(tmp_path):
    f = triggered_fixture()
    for r in f.rows:  # split the fee Raydium-style: trade 2000 ppm + creator 500 ppm on input, each ceil-rounded: the sum can be off by 1 lamport from the config rate
        ia = r["input_amount"]
        r["trade_fee"], r["creator_fee"], r["creator_fee_on_input"] = math.ceil(ia * 2000 / 1e6), math.ceil(ia * 500 / 1e6), True
    f.meta = meta_ok(trade_fee_rate=2000, creator_fee_rate=500)
    res = priced(tmp_path / "ok", [f])
    assert res["exclusions"] == {} and res["fee_audit_by_amm_config"]["CfgA"]["fee_bad"] == 0 and res["fee_audit_by_amm_config"]["CfgA"]["fee_checked"] == len(f.rows)
    assert res["fee_audit_by_amm_config"]["CfgA"]["max_dev_lamports"] <= 2 and res["fee_audit_by_amm_config"]["CfgA"]["fee_rate_ppm"] == 2500
    f.rows[2]["input_amount"], f.rows[2]["trade_fee"], f.rows[2]["creator_fee"] = 10**14, 2 * 10**11, 5 * 10**10  # an exact multiple: no rounding in the deviation
    f.rows[2]["trade_fee"] += 3  # 3 lamports over the summed rate: a real mismatch
    res = priced(tmp_path / "bad", [f])
    assert res["exclusions"] == {"fee_mismatch": 1}
    f.rows[2]["trade_fee"] -= 1  # 2 over: still inside the tolerance
    res = priced(tmp_path / "edge", [f])
    assert res["exclusions"] == {}


def test_creator_fee_on_output_is_not_added_to_the_input_side_check(tmp_path):
    f = triggered_fixture()
    for r in f.rows:
        r["creator_fee_on_input"], r["creator_fee"] = False, 123_456  # creator fee taken from the output: unchecked, not part of the input-side sum
    f.meta = meta_ok(creator_fee_rate=500)
    res = priced(tmp_path, [f])
    assert res["exclusions"] == {} and res["fee_audit_by_amm_config"]["CfgA"]["fee_rate_ppm"] == 3000  # the creator rate still prices the round trip


def test_parity_with_arena_stack_guarded_c2_c8_and_auto_strand():
    Qe, Be = 37.5e9, 1e9  # Q >= V_LO, top PumpSwap fee tier (3000 ppm) at every state below
    spot = Qe / Be
    cases = {
        "guarded": (250_000_000, Qe, Be, spot * 0.8, 37.5e9, 1e9),  # decision spot 20 % under the landing price: > 1.15 x
        "c2_exit_has_fewer_tokens": (250_000_000, Qe, Be, spot, 37.5e9, 1e6),
        "c2_exit_q_below_v_lo_region": (250_000_000, Qe, Be, spot, float("nan"), 1e9),
        "c8_k_fell": (250_000_000, Qe, Be, spot, 30e9, 1.1e9),
        "auto_strand_proceeds_below_send": (1_000_000, Qe, Be, spot, 37.5e9, 3e9),
        "filled": (250_000_000, Qe, Be, spot, 39e9, 0.97e9),
    }
    seen = set()
    for name, (stake, qe, be, sp, qx, bx) in cases.items():
        pn, fi, st = F.round_trip(stake, qe, be, sp, qx, bx, return_stranded=True)
        rt = R.cpmm_round_trip(stake, qe, be, sp, qx, bx, 0.003)
        assert bool(fi) == rt["filled"] and bool(st) == rt["stranded"], name
        assert abs(float(pn) - rt["pnl"]) < 1e-3, name
        seen.add((rt["filled"], rt["stranded"]))
    assert seen == {(False, False), (True, True), (True, False)}  # guarded, stranded, normal fill all exercised


def test_guarded_decision_blocks_the_pool_until_its_exit_decision_plus_cooldown(tmp_path):
    """Q8 (QP): the code keeps the pool blocked after a guarded buy; a guarded decision still holds the pool."""
    def build(second_dt):
        f = Fx("GB")
        f.trigger_prelude(30 * H)
        f.drain(30 * H)
        f.swap(30 * H + 0.4, "buy", 1_400_000_000)  # lifts the price inside the latency: guard (> 1.15), E1 still clear for a later drain
        T = 30 * H + 86400 + 1.9
        f.swap(T - 3 * H, "buy", 1_000)
        f.swap(second_dt + 30 * H + 86400, "sell", int(0.125 * f.r))
        return f

    blocked = cell(priced(tmp_path / "a", [build(30)]))
    free = cell(priced(tmp_path / "b", [build(62)]))
    assert blocked["rows_closed"] == 1 and blocked["unfilled_guarded"] == 1 and blocked["flat"]["trades"] == 0
    assert free["rows_closed"] == 2 and free["unfilled_guarded"] >= 1


def test_tp_decided_in_landing_slot_is_counted_report_only(tmp_path):
    f = triggered_fixture(tail=False)
    f.swap(31 * H, "buy", 5_000_000_000)
    f.swap(31 * H + 5, "buy", 1_000)
    c = cell(priced(tmp_path, [f]))
    assert c["tp_decided_in_landing_slot_report_only"] == 0 and c["exit_reasons"]["tp_1.5x"] == 1
