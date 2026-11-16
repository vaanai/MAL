"""u4_u2_deciders + the c1nf_forward_sim runner extension (QP-RULING-1116-u4-u2-p81-forward-specs): U4 balances, exclusions, NS / TOP1 thresholds, once-per-mint, share-fall exit,
U2 residual / breadth / top-3 / ties / stop / take, the placebo seed rule, warm-up no-entries, runner manifest limits, placebo bot and requested sells through the arena engine.
Synthetic fixtures only; no real tape, ledger, fill or P&L is opened. Nothing here says any book makes money."""
import collections
import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "research"))
import c1nf_forward_sim as S  # noqa: E402
import u4_u2_deciders as D  # noqa: E402
from tools import test_c1nf_forward_sim as T1  # noqa: E402

T0 = S.parse_hour("2026-11-17T00") // 1000   # S in seconds
START_MS = T0 * 1000
H = 3600
V = 10_000_000_000


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(S, "MIN_WARMUP_H", 1)  # the real minimum is 26 h; the per-decider minimum (168 / 192 h) is tested on its own
    monkeypatch.setattr(S, "WARMUP_MIN_BY_DECIDER", {})
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


def hr_pda(mint):
    return "HRPDA-" + mint


def slot_of(t):
    return 1_000_000 + t * 5 // 2


def base(typ, t, **kw):
    r = {"v": 1, "type": typ, "slot": slot_of(t), "t_recv_ms": (T0 + t) * 1000, "block_time": T0 + t}
    r.update(kw)
    return r


def create(mint, t, creator="CR", trader="CR"):
    return base("create", t, prog="pumpfun", mint=mint, creator=creator, trader=trader)


def pool_create(pool, mint, t, quote="So11111111111111111111111111111111111111112"):
    return base("pool_create", t, prog="pumpswap", pool=pool, mint=mint, quote_mint=quote)


def trade(mint, pool, t, w, side, tok, prog="pumpswap", q=200_000_000_000, b=210_000_000_000, so=0, v=V, slot=None):
    r = base("trade", t, prog=prog, pool=pool, mint=mint, trader=w, side=side, token_raw=tok, sol_lamports=so, quote_reserve=q, base_reserve=b, signature="s%d-%s-%s" % (t, w, side), event_index=0, tx_index=1)
    if v is not None:
        r["virtual_quote_reserves"] = v
    if slot is not None:
        r["slot"] = slot
    return r


def gap(t, prog="pumpswap"):
    return base("gap", t, prog=prog, kind="tip_gap_backlog_jump", data_loss=True, from_slot=slot_of(t) - 5, to_slot=slot_of(t))


def feed(dec, rows, warmup=False):
    rows = sorted(rows, key=lambda r: (r["block_time"], r["slot"]))
    st = S.RunState(hour="2026-11-17T00", warmup=warmup, day="2026-11-17")
    ents = dec.decide(rows, st)
    return ents, rows, st


# ============================================================================================================================ U4
U4_CREATE_T, U4_POOL_T = -5 * H, -3 * H


def u4_scenario(p1, p2, extra=()):
    """a mint 'M' created at S-5h, graduated at S-3h. p1 = trades before S-1h (warm-up snapshot mark S-1h), p2 = trades in (S-1h, S]; (wallet, side, tokens).
    The creator and an insider (first buy in the create slot) hold tokens that stay in D but never in NS / TOP1. -> (warm-up rows, forward rows)"""
    rows = [create("M", U4_CREATE_T), pool_create("PM", "M", U4_POOL_T),
            trade("M", "PM", U4_CREATE_T, "CR", "buy", 10, prog="pumpfun"), trade("M", "PM", U4_CREATE_T, "INS", "buy", 10, prog="pumpfun")]
    for k, (w, side, tok) in enumerate(p1):
        rows.append(trade("M", "PM", -4 * H + k * 10, w, side, tok))
    for k, (w, side, tok) in enumerate(p2):
        rows.append(trade("M", "PM", -1800 + k * 10, w, side, tok))
    rows += list(extra)
    warm = [r for r in rows if r["block_time"] <= T0]
    fwd = [trade("Z", "PZ", 10, "zz", "buy", 1)]  # the first forward row: mark S is evaluated before it is ingested
    return warm, fwd


def run_u4(p1, p2, extra=(), **kw):
    dec = D.U4Decider(START_MS, hr_pda_fn=hr_pda, **kw)
    warm, fwd = u4_scenario(p1, p2, extra)
    e0, _, _ = feed(dec, warm, warmup=True)
    assert e0 == [] and dec.take_sells() == []
    ents, rows, st = feed(dec, fwd)
    return dec, ents, st


# phase 1: A 50, B 40 - 5 = 35, C 15 -> D = 100 + creator 10 + insider 10 (both excluded from the numerators, kept in D)
P1 = [("A", "buy", 50), ("B", "buy", 40), ("B", "sell", 5), ("C", "buy", 15)]


def test_u4_fires_when_ns_up_and_top1_down_and_enters_once_per_mint():
    dec, ents, st = run_u4(P1, [("E", "buy", 40)])
    D1 = 50 + 35 + 15 + 20           # creator + insider tokens are in the denominator
    D2 = D1 + 40
    ns1, top1_1 = (50 + 15) / D1, 50 / D1
    ns2, top1_2 = (50 + 15 + 40) / D2, 50 / D2
    main = [e for e in ents if e.bot == "main"]
    assert len(main) == 1 and main[0].pool == "PM" and main[0].mint == "M"
    e = main[0]
    assert e.exit_after_s == 4 * 3600 and e.exit_from_decision is True and e.meta["mark_s"] == T0
    assert e.at_row == 0 and e.decision_ms == (T0 - 1800) * 1000 and e.decision_slot == slot_of(-1800)  # decided on the last row with block time <= M
    assert e.meta["ns"] == pytest.approx(ns2) and e.meta["top1"] == pytest.approx(top1_2)
    assert e.meta["dns"] == pytest.approx(ns2 - ns1) and e.meta["dtop1"] == pytest.approx(top1_2 - top1_1)
    assert ns2 >= 0.5 and ns2 - ns1 >= 0.05 and top1_2 < top1_1
    assert st.counts["fires"] == 1 and st.counts["marks_evaluated"] == 1
    # once per mint, ever: the next marks (more buying, NS still rising) do not enter again
    more = [trade("M", "PM", 20 + 300 * k, "F%d" % k, "buy", 30) for k in range(5)] + [trade("Z", "PZ", 400 + 300 * k, "zz", "buy", 1) for k in range(5)]
    ents2, _, st2 = feed(dec, more)
    assert [e for e in ents2 if e.bot == "main"] == [] and st2.counts["fires"] == 0


@pytest.mark.parametrize("p1,p2,fires,ns", [
    (P1, [("E", "buy", 40)], True, None),
    # NS exactly 0.5 passes (>=): A 20, B 70 - 10 = 60, C 20, creator + insider 20 in D; E 40 -> NS 80 / 160 = 0.5, dNS 0.167, TOP1 0.5 -> 0.375
    ([("A", "buy", 20), ("B", "buy", 70), ("B", "sell", 10), ("C", "buy", 20)], [("E", "buy", 40)], True, 0.5),
    # NS 0.407 (< 0.5) with dNS 0.103 and TOP1 down: no fire
    ([("A", "buy", 20), ("B", "buy", 70), ("B", "sell", 10), ("C", "buy", 15)], [("E", "buy", 20)], False, None),
    # dNS = 0.0148 < 0.05 (NS 0.5565, TOP1 down): no fire
    (P1, [("E", "buy", 4)], False, None),
    # top-1 share rises (A, never sold, the top holder, buys 30 more; NS 0.633, dNS 0.092): no fire
    (P1, [("A", "buy", 30)], False, None),
])
def test_u4_entry_thresholds(p1, p2, fires, ns):
    dec, ents, st = run_u4(p1, p2)
    got = [e for e in ents if e.bot == "main"]
    assert bool(got) is fires and st.counts["fires"] == int(fires)
    if ns is not None:
        assert got[0].meta["ns"] == pytest.approx(ns)


def test_u4_balances_exclusions_unexplained_sells_and_routers_hand_computed():
    dec = D.U4Decider(START_MS, hr_pda_fn=hr_pda)
    cs = slot_of(U4_CREATE_T)
    rows = [create("M", U4_CREATE_T, creator="CR", trader="TR"), pool_create("PM", "M", U4_POOL_T),
            trade("M", "PM", U4_CREATE_T, "CR", "buy", 50, prog="pumpfun", slot=cs),             # creator: excluded numerator, in D
            trade("M", "PM", U4_CREATE_T, "TR", "buy", 5, prog="pumpfun", slot=cs),              # create row trader: excluded
            trade("M", "PM", U4_CREATE_T + 1, "INS", "buy", 30, prog="pumpfun", slot=cs + 1),    # bundled insider (next slot): excluded
            trade("M", "PM", -4 * H, "A", "buy", 20), trade("M", "PM", -4 * H + 5, "A", "buy", 10),   # never sold: 30
            trade("M", "PM", -4 * H + 10, "B", "buy", 40), trade("M", "PM", -4 * H + 15, "B", "sell", 15),  # sold: b 25
            trade("M", "PM", -4 * H + 20, "X", "sell", 5),                                       # no tape balance: unexplained 5, b stays 0
            trade("M", "PM", -4 * H + 25, "R", "buy", 10)]                                       # router below
    rows += [trade("N%d" % k, "PN%d" % k, -4 * H + 30 + k, "R", "buy", 1) for k in range(50)]   # R: 51 distinct mints in 24 h (> 50)
    feed(dec, rows, warmup=True)
    ms = dec.mints["M"]
    assert ms.bal["A"] == 30 and ms.bal["B"] == 25 and ms.bal.get("X", 0) == 0 and ms.unexplained == 5 and ms.sold >= {"B", "X"}
    routers = dec._routers(T0 - H)
    assert routers == {"R"}
    Dsum = 50 + 5 + 30 + 30 + 25 + 10   # every positive balance, router and creator included
    ns, top1 = dec.ns_top1("M", T0 - H, routers)
    assert ns == pytest.approx(30 / Dsum) and top1 == pytest.approx(30 / Dsum)
    assert dec.ns_top1("M", T0 - H, set())[0] == pytest.approx(40 / Dsum)   # without the router rule R's 10 count too


def test_u4_gap_taint_and_transfer_heavy_remove_the_mint_for_good_and_are_counted():
    # gap after the create: the mint is out for good, no fire although every threshold holds
    dec, ents, st = run_u4(P1, [("E", "buy", 40)], extra=[gap(-3000)])
    assert [e for e in ents if e.bot == "main"] == [] and st.counts["gap_tainted"] == 1
    # a gap of another program (or none matching) does not taint
    dec, ents, st = run_u4(P1, [("E", "buy", 40)], extra=[gap(-3000, prog="other")])
    assert len([e for e in ents if e.bot == "main"]) == 1
    # transfer-heavy: unexplained sells > 5% of supply (supply fixture 100 -> 5): X sells 6 with no buys
    dec, ents, st = run_u4(P1, [("E", "buy", 40)], extra=[trade("M", "PM", -3000, "X", "sell", 6)], supply_raw=100)
    assert [e for e in ents if e.bot == "main"] == [] and st.counts["transfer_heavy"] == 1


def test_u4_universe_filters_age_holder_rewards_quote_and_create_row():
    # holder-rewards coin: create creator == the PDA -> excluded, counted
    dec = D.U4Decider(START_MS, hr_pda_fn=hr_pda)
    warm, fwd = u4_scenario(P1, [("E", "buy", 40)])
    warm = [dict(r, creator=hr_pda("M")) if r.get("type") == "create" else r for r in warm]
    feed(dec, warm, warmup=True)
    ents, _, st = feed(dec, fwd)
    assert ents == [] and st.counts["hr_coin_excluded"] == 1
    # unknown / non-WSOL quote: excluded, counted
    dec = D.U4Decider(START_MS, hr_pda_fn=hr_pda)
    warm, fwd = u4_scenario(P1, [("E", "buy", 40)])
    warm = [dict(r, quote_mint="USDC") if r.get("type") == "pool_create" else r for r in warm]
    feed(dec, warm, warmup=True)
    ents, _, st = feed(dec, fwd)
    assert ents == [] and st.counts["quote_unknown"] == 1
    # no create row: excluded, counted
    dec = D.U4Decider(START_MS, hr_pda_fn=hr_pda)
    warm, fwd = u4_scenario(P1, [("E", "buy", 40)])
    feed(dec, [r for r in warm if r.get("type") != "create"], warmup=True)
    ents, _, st = feed(dec, fwd)
    assert ents == [] and st.counts["no_create_row"] == 1


def test_u4_pool_age_window_is_one_hour_to_24_hours():
    for pool_t, fires in ((-3000, False), (-3600, True), (-86_400 + 1, True), (-86_400, False)):  # age at M = S: 3000 s, 3600 s, 86399 s, 86400 s
        dec = D.U4Decider(START_MS, hr_pda_fn=hr_pda)
        warm, fwd = u4_scenario(P1, [("E", "buy", 40)])
        warm = [dict(r, block_time=T0 + pool_t, t_recv_ms=(T0 + pool_t) * 1000) if r.get("type") == "pool_create" else r for r in warm]
        feed(dec, warm, warmup=True)
        ents, _, st = feed(dec, fwd)
        assert bool([e for e in ents if e.bot == "main"]) is fires, pool_t


def test_u4_needs_a_pumpswap_print_in_the_last_60_minutes():
    dec = D.U4Decider(START_MS, hr_pda_fn=hr_pda)
    warm, fwd = u4_scenario(P1, [("E", "buy", 40)])
    warm = [dict(r, prog="pumpfun") if (r.get("type") == "trade" and r["block_time"] > T0 - H) else r for r in warm]   # phase 2 prints are bonding rows
    feed(dec, warm, warmup=True)
    ents, _, st = feed(dec, fwd)
    assert [e for e in ents if e.bot == "main"] == []


def test_u4_share_fall_exit_is_a_sell_request_and_a_gap_after_entry_disables_it():
    dec, ents, st = run_u4(P1, [("E", "buy", 40)])
    assert len([e for e in ents if e.bot == "main"]) == 1
    ns0 = [e for e in ents if e.bot == "main"][0].meta["ns"]
    # 25 minutes later E-like holders dump: wallets A and E sell everything -> NS falls below 0.80 x NS0 at a later 5 min mark
    dump = [trade("M", "PM", 600, "A", "sell", 50), trade("M", "PM", 610, "E", "sell", 40), trade("Z", "PZ", 1000, "zz", "buy", 1)]
    ents2, rows, st2 = feed(dec, dump)
    sells = dec.take_sells()
    assert [s.reason for s in sells if s.bot == "main"] == ["ns_fall"] and sells[0].pool == "PM" and st2.counts["ns_fall_exits"] == 1
    assert sells[0].decision_ms == (T0 + 600) * 1000 and rows[sells[0].at_row]["block_time"] == T0 + 610   # the last row with block time <= the mark; at_row is the first row after it
    # gap reaching the mint after entry: the NS exit is off for that position (counted), it exits timed only
    dec2, ents, _ = run_u4(P1, [("E", "buy", 40)])
    feed(dec2, [gap(300), trade("M", "PM", 301, "A", "sell", 50), trade("Z", "PZ", 1000, "zz", "buy", 1)])
    assert dec2.take_sells() == []


def test_u4_warmup_computes_no_universe_no_fire_and_snapshots_only_in_the_last_60_minutes():
    dec = D.U4Decider(START_MS, hr_pda_fn=hr_pda)
    warm, fwd = u4_scenario(P1, [("E", "buy", 40)])
    # a forward-looking row inside a warm-up call must not evaluate marks >= S
    warm_plus = warm + [trade("Z", "PZ", 10, "zz", "buy", 1)]
    ents, rows, st = feed(dec, warm_plus, warmup=True)
    assert ents == [] and dec.take_sells() == [] and st.counts["marks_evaluated"] == 0 and st.counts["fires"] == 0
    snaps = sorted(k for k in dec.mints["M"].snaps)
    assert snaps and min(snaps) >= T0 - H and max(snaps) < T0          # snapshot marks in [S - 60 min, S) only
    # the first forward row evaluates mark S with the deltas from the warm-up snapshot
    ents, _, st = feed(dec, [trade("Z", "PZ", 20, "zz", "buy", 1)])
    assert st.counts["marks_evaluated"] == 1 and len([e for e in ents if e.bot == "main"]) == 1


def test_u4_placebo_seed_rule_and_reproducibility():
    decoys = []
    for k in range(4):   # four pools in the universe that never fire (every wallet has sold: NS 0), mints Dk, graduated at S-3h
        mt, pl = "D%d" % k, "PD%d" % k
        decoys += [create(mt, U4_CREATE_T), pool_create(pl, mt, U4_POOL_T), trade(mt, pl, -4 * H, "a%d" % k, "buy", 10), trade(mt, pl, -4 * H + 1, "b%d" % k, "buy", 10),
                   trade(mt, pl, -4 * H + 2, "b%d" % k, "sell", 5), trade(mt, pl, -1500, "c%d" % k, "buy", 10),
                   trade(mt, pl, -1400, "a%d" % k, "sell", 5), trade(mt, pl, -1300, "c%d" % k, "sell", 5)]
    runs = []
    for _ in range(2):
        dec, ents, st = run_u4(P1, [("E", "buy", 40)], extra=decoys)
        runs.append([(e.pool, e.bot, e.at_row) for e in ents])
    assert runs[0] == runs[1]                                           # reproducible
    plc = [e for e in ents if e.bot == "placebo"]
    assert len(plc) == 1 and plc[0].exit_after_s == 4 * 3600 and plc[0].exit_from_decision
    cands = sorted("PD%d" % k for k in range(4))                        # the pools that did not fire, in the universe (PM fired and is excluded)
    assert plc[0].pool == random.Random(1 + T0 + 0).choice(cands)       # seed = 1 + mark epoch seconds + draw index (0)
    assert plc[0].meta["draw_index"] == 0 and st.counts["placebo_entries"] == 1


# ============================================================================================================================ U2
def u2_print(pool, mint, t, price, quote=200_000_000_000):
    b = int(round((quote + V) / price))
    return trade(mint, pool, t, "w", "buy", 0, q=quote, b=b, so=0)


def u2_cohort(rs_by_bucket, quote=200_000_000_000, ages_h=(30, 60, 120), names=None):
    """pools with the 24 h returns R of rs_by_bucket (a list per age bucket). Each pool: create + pool_create at M - age (M = S), a print at S - 24 h - 100 at price 1.0 and a print at S - 3000 at 1 + R.
    -> (rows, {pool: R}) ; names default P<bucket><k>"""
    rows, rr = [], {}
    for bi, rs in enumerate(rs_by_bucket):
        for k, r in enumerate(rs):
            pool = (names[bi][k] if names else "P%d%d" % (bi, k))
            mint = "M" + pool
            ct = -ages_h[bi] * H
            rows += [create(mint, ct - 100), pool_create(pool, mint, ct), u2_print(pool, mint, -24 * H - 100, 1.0, quote), u2_print(pool, mint, -3000, 1.0 + r, quote)]
            rr[pool] = r
    return rows, rr


def run_u2(rows, extra_fwd=(), start_ms=START_MS):
    dec = D.U2Decider(start_ms, hr_pda_fn=hr_pda)
    _, _, wst = feed(dec, rows, warmup=True)
    ents, frows, st = feed(dec, [trade("ZZ", "PZZ", 20, "w", "buy", 0)] + list(extra_fwd))
    st.counts.update(wst.counts)   # warm-up counters (missing_v, ...) beside the forward ones
    return dec, ents, st, frows


COHORT = [[0.30, 0.20, 0.10, 0.05, -0.10, -0.20], [0.40, 0.10, -0.05, -0.10, -0.30]]


def test_u2_cohort_median_residual_breadth_and_top3():
    rows, rr = u2_cohort(COHORT)
    dec, ents, st, _ = run_u2(rows)
    med1, med2 = (0.10 + 0.05) / 2, -0.05          # bucket medians: 6 pools (mean of the middle two) and 5 pools
    res = {}
    for k, r in enumerate(COHORT[0]):
        res["P0%d" % k] = r - med1
    for k, r in enumerate(COHORT[1]):
        res["P1%d" % k] = r - med2
    expect = [p for p, e in sorted(res.items(), key=lambda kv: (-kv[1], kv[0])) if e > 0][:3]
    got = [e.pool for e in ents if e.bot == "main"]
    assert got == expect == ["P10", "P00", "P11"]                    # residuals 0.45, 0.225, 0.15
    assert sum(1 for r in rr.values() if r > 0) / len(rr) == pytest.approx(6 / 11)
    e0 = ents[0]
    assert e0.exit_after_s == 12 * 3600 and e0.exit_from_decision and e0.meta["mark_s"] == T0 and e0.at_row == 0
    recs = dec.drain_marks()
    assert recs[0]["universe"] == 11 and recs[0]["breadth"] == round(6 / 11, 3) and recs[0]["gate"] is True and recs[0]["picks"] == 3 and recs[0]["buckets"] == [6, 5, 0]
    assert st.counts["picks"] == 3 and st.counts["marks_evaluated"] == 1


def test_u2_breadth_gate_is_strictly_above_040():
    # 4 of 10 pools with R > 0 -> breadth 0.4: no trade
    rows, _ = u2_cohort([[0.3, 0.2, 0.1, -0.1, -0.2], [0.4, -0.1, -0.2, -0.3, -0.4]])
    dec, ents, st, _ = run_u2(rows)
    assert ents == [] and dec.drain_marks()[0]["breadth"] == 0.4 and st.counts["picks"] == 0
    # 5 of 10 -> 0.5: trades
    rows, _ = u2_cohort([[0.3, 0.2, 0.1, -0.1, -0.2], [0.4, 0.3, -0.2, -0.3, -0.4]])
    dec, ents, st, _ = run_u2(rows)
    assert len([e for e in ents if e.bot == "main"]) >= 1 and dec.drain_marks()[0]["breadth"] == 0.5


def test_u2_small_universe_and_small_bucket_are_skipped_and_counted():
    rows, _ = u2_cohort([[0.3, 0.2, 0.1, 0.05, 0.02], [0.4, 0.3, 0.2, 0.1]])      # 9 pools: no breadth
    dec, ents, st, _ = run_u2(rows)
    assert ents == [] and st.counts["universe_small"] == 1
    rows, _ = u2_cohort([[0.3, 0.2, 0.1, 0.05, 0.02], [0.4, 0.3, 0.2, 0.1, 0.0], [0.9, 0.0]])  # 12 pools; bucket 3 has 2 (< 5): its pools cannot be picked
    dec, ents, st, _ = run_u2(rows)
    assert st.counts["bucket_skipped"] == 1 and all(not e.pool.startswith("P2") for e in ents if e.bot == "main")


def test_u2_ties_break_by_pool_address_and_zero_residual_is_not_picked():
    names = [["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9"], ["U1", "U2", "U3", "U4", "U5"]]
    rows, _ = u2_cohort([[0.5, 0.5, 0.5, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0], [0.1] * 5], names=names)
    dec, ents, st, _ = run_u2(rows)
    assert [e.pool for e in ents if e.bot == "main"] == ["T1", "T2", "T3"]        # four tied at residual 0.5: the three lowest addresses


def test_u2_universe_filters_age_depth_freshness_missing_v():
    rows, _ = u2_cohort([[0.3, 0.2, 0.1, 0.05, 0.02, -0.1], [0.4, 0.1, -0.05, -0.1, -0.3]])
    dec, ents, st, _ = run_u2(rows)
    # depth: every pool at 149.999 SOL real quote reserve -> empty universe
    rows2, _ = u2_cohort([[0.3, 0.2, 0.1, 0.05, 0.02, -0.1], [0.4, 0.1, -0.05, -0.1, -0.3]], quote=149_999_999_999)
    dec, ents, st, _ = run_u2(rows2)
    assert ents == [] and st.counts["universe_small"] == 1 and dec.drain_marks()[0]["universe"] == 0
    # age: 169 h and 23 h pools are outside [24 h, 168 h)
    rows3, _ = u2_cohort([[0.3] * 6, [0.1] * 5], ages_h=(23, 169, 169))
    dec, ents, st, _ = run_u2(rows3)
    assert dec.drain_marks()[0]["universe"] == 0
    # freshness: last print older than 6 h -> out
    stale = [dict(r, block_time=r["block_time"] - 7 * H, t_recv_ms=r["t_recv_ms"] - 7 * H * 1000, slot=r["slot"] - 7 * H * 5 // 2) if r["block_time"] == T0 - 3000 else r for r in rows]
    dec, ents, st, _ = run_u2(stale)
    assert dec.drain_marks()[0]["universe"] == 0
    # a print without event V has no price: it does not enter the series (counted missing_v)
    rows4 = [dict(r) for r in rows]
    for r in rows4:
        if r["block_time"] == T0 - 3000:
            r.pop("virtual_quote_reserves")
    dec, ents, st, _ = run_u2(rows4)
    assert st.counts["missing_v"] == 11


def test_u2_holder_rewards_coin_is_excluded_and_counted():
    rows, _ = u2_cohort([[0.3, 0.2, 0.1, 0.05, 0.02, -0.1], [0.4, 0.1, -0.05, -0.1, -0.3]])
    rows = [dict(r, creator=hr_pda("MP00")) if (r.get("type") == "create" and r["mint"] == "MP00") else r for r in rows]
    dec, ents, st, _ = run_u2(rows)
    assert st.counts["hr_coin_excluded"] == 1 and dec.drain_marks()[0]["universe"] == 10


def test_u2_schedule_is_00_06_12_18_utc_and_marks_before_s_are_not_evaluated():
    dec = D.U2Decider(START_MS, hr_pda_fn=hr_pda)
    assert [dec.first_mark_at_or_after(T0 + x) for x in (0, 1, 6 * H, 6 * H + 1, 18 * H + 1)] == [T0, T0 + 6 * H, T0 + 6 * H, T0 + 12 * H, T0 + 24 * H]
    assert dec.mark_after(T0) == T0 + 6 * H
    rows, _ = u2_cohort(COHORT)
    dec = D.U2Decider(START_MS, hr_pda_fn=hr_pda)
    ents, _, st = feed(dec, rows + [trade("ZZ", "PZZ", 20, "w", "buy", 0)], warmup=True)
    assert ents == [] and dec.take_sells() == [] and st.counts["marks_evaluated"] == 0 and st.counts["picks"] == 0 and dec.mark_records == []


@pytest.mark.parametrize("price,reason", [(0.8, "stop"), (0.7999, "stop"), (0.81, None), (1.4, "take"), (1.41, "take"), (1.39, None)])
def test_u2_stop_20_take_40_on_the_entry_print_spot_without_own_impact(price, reason):
    rows, _ = u2_cohort(COHORT)
    dec = D.U2Decider(START_MS, hr_pda_fn=hr_pda)
    feed(dec, rows, warmup=True)
    ents, _, st = feed(dec, [trade("ZZ", "PZZ", 20, "w", "buy", 0)])
    assert "P10" in [e.pool for e in ents if e.bot == "main"]
    L = D.LAT_SLOTS
    ent = next(e for e in ents if e.pool == "P10" and e.bot == "main")
    ref_t = 25                                   # a print at slot <= SD + 19 (the entry print), price 1.0 (the stop / take reference)
    ref_row = u2_print("P10", "MP10", 0, 1.0)
    ref_row.update(slot=ent.decision_slot + L, t_recv_ms=(T0 + 25) * 1000, block_time=T0 + 25)
    trig = u2_print("P10", "MP10", 100, price)
    trig.update(slot=ent.decision_slot + L + 1, t_recv_ms=(T0 + 100) * 1000, block_time=T0 + 100)
    # a print at slot <= SD + 19 below the stop level must not trigger (not yet landed)
    early = u2_print("P10", "MP10", 10, 0.5)
    early.update(slot=ent.decision_slot + 3, t_recv_ms=(T0 + 10) * 1000, block_time=T0 + 10)
    ents2, frows, _ = feed(dec, [early, ref_row, trig, trade("ZZ", "PZZ", 200, "w", "buy", 0)])
    sells = [s for s in dec.take_sells() if s.bot == "main"]
    if reason is None:
        assert sells == []
    else:
        assert len(sells) == 1 and sells[0].reason == reason and sells[0].pool == "P10"
        assert frows[sells[0].at_row - 1] is trig and sells[0].decision_slot == trig["slot"]       # decided on the trigger print, handed over just after it


def test_u2_placebo_draws_the_same_count_from_the_same_universe_with_the_seed_rule():
    rows, _ = u2_cohort(COHORT)
    runs = []
    for _ in range(2):
        dec, ents, st, _ = run_u2(rows)
        runs.append([(e.pool, e.bot) for e in ents])
    assert runs[0] == runs[1]
    picks = [e.pool for e in ents if e.bot == "main"]
    plc = [e.pool for e in ents if e.bot == "placebo"]
    assert len(plc) == 3 and not set(plc) & set(picks)
    cands = sorted(f"P0{k}" for k in range(6)) + sorted(f"P1{k}" for k in range(5))
    cands = sorted(c for c in cands if c not in picks)
    assert plc == random.Random(1 + T0).sample(cands, 3)                # seed = 1 + mark epoch seconds, uniform without replacement
    assert all(e.exit_after_s == 12 * 3600 for e in ents if e.bot == "placebo")


def test_u2_no_trade_means_no_placebo_draw():
    rows, _ = u2_cohort([[0.3, 0.2, 0.1, -0.1, -0.2], [0.4, -0.1, -0.2, -0.3, -0.4]])     # breadth 0.4
    dec, ents, st, _ = run_u2(rows)
    assert ents == [] and st.counts["placebo_entries"] == 0


def test_u2_second_mark_skips_held_pools_and_takes_the_next():
    rows, rr = u2_cohort(COHORT)
    # the series needed for mark S + 6 h: a print at S - 18 h - 100 (price 1.0) in the warm-up, and the 24 h return of the same R at S + 6 h - 3000
    names = sorted(rr)
    extra_warm = [u2_print(p, "M" + p, -18 * H - 100, 1.0) for p in names]
    extra_fwd = [u2_print(p, "M" + p, 6 * H - 3000, 1.0 + rr[p]) for p in names]
    dec = D.U2Decider(START_MS, hr_pda_fn=hr_pda)
    feed(dec, rows + extra_warm, warmup=True)
    ents, _, _ = feed(dec, [trade("ZZ", "PZZ", 20, "w", "buy", 0)] + extra_fwd + [trade("ZZ", "PZZ", 6 * H + 20, "w", "buy", 0)])
    picks = [e for e in ents if e.bot == "main"]
    first = [e.pool for e in picks if e.meta["mark_s"] == T0]
    second = [e.pool for e in picks if e.meta["mark_s"] == T0 + 6 * H]
    assert first == ["P10", "P00", "P11"]
    assert second == ["P01", "P02"]       # the three held pools are skipped; only two more residuals are > 0 (0.125, 0.025)


# ============================================================================================================================ runner extension
def mf(tmp, decider, **kw):
    m = T1.manifest(tmp, decider=decider, **kw)
    m.pop("wallet_ledger_root", None)
    return m


def test_manifest_exit_s_limits_per_decider(tmp_path):
    S.check_manifest(mf(tmp_path, "u4", exit_s=259_200))
    S.check_manifest(mf(tmp_path, "u2", exit_s=43_200))
    for dec, es in (("u4", 259_201), ("u2", 0), ("c1nf", 3601)):
        with pytest.raises(S.Refused):
            S.check_manifest(mf(tmp_path, dec, exit_s=es))
    S.check_manifest(mf(tmp_path, "c1nf", exit_s=3600))


def test_warmup_minimum_per_decider_u4_168_u2_192(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "WARMUP_MIN_BY_DECIDER", {"u4": 168, "u2": 192})
    monkeypatch.setattr(S, "WARMUP_START_MIN", "2026-11-10T00")   # 168 h before S = 2026-11-17T00
    S.hours_needed(mf(tmp_path, "u4", warmup_hours=168))
    with pytest.raises(S.Refused):
        S.hours_needed(mf(tmp_path, "u4", warmup_hours=100))
    with pytest.raises(S.Refused):
        S.hours_needed(mf(tmp_path, "u2", warmup_hours=168))      # only 168 h available, 192 needed: S moves later
    monkeypatch.setattr(S, "WARMUP_START_MIN", "2026-11-09T00")
    assert len(S.hours_needed(mf(tmp_path, "u2", warmup_hours=192))[0]) == 192


def test_entry_as_dict_is_unchanged_for_c1nf_entries():
    e = S.Entry(pool="P", mint="M", at_row=1, decision_ms=2, decision_slot=3)
    assert set(e.as_dict()) == {"pool", "mint", "at_row", "decision_ms", "decision_slot", "exit_after_s", "meta"}   # decision md5 byte-identical to the pre-extension runner
    assert "bot" in S.Entry(pool="P", mint="M", at_row=1, decision_ms=2, decision_slot=3, bot="placebo").as_dict()


class TwoBotDecider:
    """entry on the main and placebo bots at row `at`, a requested sell of the main position at row `sell_at`; timed exit from the decision instant"""

    def __init__(self, at=100, sell_at=400, exit_s=3000.0):
        self.at, self.sell_at, self.exit_s, self.pending = at, sell_at, exit_s, []

    def decide(self, rows, state):
        if state.warmup or state.hour != T1.HOUR0:
            return []
        prev = rows[self.at - 1]
        mk = lambda bot: S.Entry(pool="P1", mint="MP1", at_row=self.at, decision_ms=prev["t_recv_ms"], decision_slot=prev["slot"], exit_after_s=self.exit_s, bot=bot, exit_from_decision=True)  # noqa: E731
        p = rows[self.sell_at - 1]
        self.pending = [S.SellReq(pool="P1", at_row=self.sell_at, decision_ms=p["t_recv_ms"], decision_slot=p["slot"], reason="stop", bot="main")]
        return [mk("main"), mk("placebo")]

    def take_sells(self):
        out, self.pending = self.pending, []
        return out


def test_placebo_bot_and_requested_sell_go_through_the_arena_engine(tmp_path):
    T1.write_keep(tmp_path, 3)
    m = mf(tmp_path, "u2", exit_s=3000)
    assert T1.run(tmp_path, m, TwoBotDecider()) == 0
    out = Path(m["out_root"])
    rows = T1.ledger_rows(m["out_root"])
    by_bot = collections.defaultdict(list)
    for r in rows:
        by_bot[r["bot_id"]].append(r)
    assert len(by_bot) == 2 and {b.endswith("-plc") for b in by_bot} == {True, False}
    main = next(v for k, v in by_bot.items() if not k.endswith("-plc"))
    plc = next(v for k, v in by_bot.items() if k.endswith("-plc"))
    mt = next(r for r in main if r["kind"] == "trade")
    pt = next(r for r in plc if r["kind"] == "trade")
    # the requested sell (decided at row 400) closes the main position; the placebo holds to its timed exit 3000 s after the decision
    assert mt["decision_ms"]["exit"] - mt["decision_ms"]["entry"] < 7_200_000
    assert mt["decision_ms"]["exit"] < pt["decision_ms"]["exit"] and pt["decision_ms"]["exit"] - pt["decision_ms"]["entry"] == 3_000_000
    st = json.loads((out / "status.json").read_text())
    assert st["book_counts"]["buys"] == 1 and st["book_counts"]["buys_placebo"] == 1 and st["book_counts"]["sell_req_stop"] == 1
    assert (out / "tapelink").is_dir() and not (out / "wallet-ledger").exists() and not (out / "ledger-src").exists()   # no C1-NF wallet ledger is built for these books


def test_a_sell_decided_before_s_or_in_a_warmup_hour_is_refused(tmp_path):
    class Early(TwoBotDecider):
        def decide(self, rows, state):
            if state.warmup:
                return []
            return super().decide(rows, state)

        def take_sells(self):
            return []
    T1.write_keep(tmp_path, 2)
    book_m = mf(tmp_path, "u2", exit_s=3000)
    bk = S.Book(S.check_manifest(book_m), tmp_path / "run", "0" * 64, T1.SPS)
    with pytest.raises(S.Refused):
        bk.sell_req(S.SellReq(pool="P1", at_row=0, decision_ms=START_MS - 1, decision_slot=1, reason="stop"))
    with pytest.raises(S.Refused):
        bk.buy(S.Entry(pool="P1", mint="M", at_row=0, decision_ms=START_MS - 1, decision_slot=1, bot="placebo", exit_from_decision=True))


class WarmupSells(TwoBotDecider):
    def decide(self, rows, state):
        return [S.Entry(pool="P1", mint="M", at_row=0, decision_ms=0, decision_slot=0)] if False else []

    def take_sells(self):
        return [S.SellReq(pool="P1", at_row=0, decision_ms=0, decision_slot=0, reason="stop")]


def test_a_warmup_hour_with_sells_is_refused(tmp_path):
    T1.write_keep(tmp_path, 2)
    assert T1.run(tmp_path, mf(tmp_path, "u2", exit_s=3000), WarmupSells()) == S.EXIT_REFUSED


def test_decider_factory_default_builds_the_u4_and_u2_deciders(tmp_path):
    for name, cls in (("u4", D.U4Decider), ("u2", D.U2Decider)):
        d = S.default_decider(mf(tmp_path, name, exit_s=3600))
        assert isinstance(d, cls) and d.start_s == T0


def test_hr_pda_default_matches_solders_find_program_address():
    pytest.importorskip("solders")
    from solders.pubkey import Pubkey
    mint = str(Pubkey.new_unique())
    want = Pubkey.find_program_address([b"holder-rewards", bytes(Pubkey.from_string(mint))], Pubkey.from_string(D.PUMP_PROGRAM))[0]
    assert D.hr_pda_default(mint) == str(want)


# ============================================================================================================================ ADD1 (QP-RULING-1116-u4-u2-p81-forward-specs-ADD1, sha256 e5009a3c0e3ecb2716befc62ae6fc5b2aa7553fec18a2c9f7f055085f66d6665)
def test_u4_supply_is_the_create_rows_token_raw_per_mint_with_a_counted_fallback():
    # the create row's token_raw is the CreateEvent token_total_supply (tools/pump_history_backfill.py line 274): 5% of 100 = 5 unexplained tokens is the cut; no Mayhem exclusion
    x_sell = [trade("M", "PM", -3000, "X", "sell", 6)]

    def warm_rows(sup, **kw):
        return [dict(r, token_raw=sup, **kw) if r.get("type") == "create" else r for r in u4_scenario(P1, [("E", "buy", 40)], x_sell)[0]]

    for sup, mayhem, heavy in ((100, False, True), (100, True, True), (10 ** 6, False, False)):
        dec = D.U4Decider(START_MS, hr_pda_fn=hr_pda)           # the fallback supply 1e15 is NOT what applies: the row's own supply does
        feed(dec, warm_rows(sup, is_mayhem_mode=mayhem), warmup=True)
        ents, _, st = feed(dec, u4_scenario(P1, [("E", "buy", 40)])[1])
        assert (st.counts["transfer_heavy"] == 1) is heavy and bool([e for e in ents if e.bot == "main"]) is (not heavy) and st.counts["supply_fallback"] == 0
    # no token_raw on the create row: fallback 1e15 (6 unexplained tokens are far below 5%), counted once per mint
    dec = D.U4Decider(START_MS, hr_pda_fn=hr_pda)
    _, _, wst = feed(dec, u4_scenario(P1, [("E", "buy", 40)], x_sell)[0], warmup=True)
    ents, _, st = feed(dec, u4_scenario(P1, [("E", "buy", 40)])[1])
    st.counts.update(wst.counts)
    assert st.counts["supply_fallback"] == 1 and st.counts["transfer_heavy"] == 0 and len([e for e in ents if e.bot == "main"]) == 1


def test_twins_14_and_26_slots_use_the_same_u2_entry_reference_and_the_same_sells(tmp_path):
    rows, _ = u2_cohort(COHORT)
    outs = []
    for lat in (3.010, 4.085, 5.590):    # 14, 19 (deciding), 26 slots: pricing only, the decider never sees the latency
        m = S.check_manifest(mf(tmp_path, "u2", exit_s=43_200, pricing_lat_entry_s=lat))
        dec = D.make_decider(m, hour_to_ms=S.parse_hour, hr_pda_fn=hr_pda)
        feed(dec, rows, warmup=True)
        ents, _, _ = feed(dec, [trade("ZZ", "PZZ", 20, "w", "buy", 0)])
        ent = next(e for e in ents if e.pool == "P10" and e.bot == "main")
        ref = u2_print("P10", "MP10", 0, 1.0)
        ref.update(slot=ent.decision_slot + D.LAT_SLOTS, t_recv_ms=(T0 + 25) * 1000, block_time=T0 + 25)
        trig = u2_print("P10", "MP10", 100, 0.7)
        trig.update(slot=ent.decision_slot + D.LAT_SLOTS + 1, t_recv_ms=(T0 + 100) * 1000, block_time=T0 + 100)
        feed(dec, [ref, trig, trade("ZZ", "PZZ", 200, "w", "buy", 0)])
        outs.append(([e.as_dict() for e in ents], [s.as_dict() for s in dec.take_sells()]))
    assert outs[0] == outs[1] == outs[2] and outs[0][1] and outs[0][1][0]["reason"] == "stop"


def test_gap_exclusions_are_split_by_the_deciders_exit_kind_and_carry_the_disclosure(tmp_path):
    m = S.check_manifest(mf(tmp_path, "u2", exit_s=3000))
    bk = S.Book(m, tmp_path / "run", "0" * 64, T1.SPS)
    day = S.day_key(START_MS)
    for pid, kind, bot in (("p1", "timed", "main"), ("p2", "requested", "main"), ("p3", "timed", "placebo"), ("p4", "requested", "main")):
        bk.exit_kind[pid] = kind
        bk.n_entries[(bot, day)] += 1
    d = tmp_path / "run" / "ledger" / "x"
    d.mkdir(parents=True)

    def ex(pid, bot, reason="feed_gap"):
        return {"kind": "excluded", "position_id": pid, "bot_id": bk.bots[bot], "exclusion_reason": reason, "decision_ms": {"entry": START_MS}}

    rows = [ex("p1", "main"), ex("p1", "main"), ex("p2", "main"), ex("p3", "placebo"), ex("p4", "main", "missing_v"), {"kind": "trade", "position_id": "p4", "bot_id": bk.bots["main"]}]
    (d / ("trades-%s.jsonl" % day)).write_text("".join(json.dumps(r) + "\n" for r in rows))
    out = bk.gap_exclusion_split()
    assert out["main"]["total"] == {"entries": 3, "feed_gap_timed": 1, "feed_gap_requested": 1}      # a position's per-stake rows count once; missing_v is not a gap exclusion
    assert out["placebo"]["total"] == {"entries": 1, "feed_gap_timed": 1, "feed_gap_requested": 0}
    assert out["main"]["by_day"][day]["excluded_share"] == round(2 / 3, 4)
    assert out["disclosure"] == S.GAP_DISCLOSURE.format(n=3, N=4, timed=2, requested=1) and "excluded 3 of 4 entries (2/1)" in out["disclosure"]
    assert out["disclosure"].startswith("Strict feed-gap exclusion: any data_loss gap between decision and exit removes the trade")


def test_the_book_job_writes_gap_exclusions_json(tmp_path):
    T1.write_keep(tmp_path, 3)
    m = mf(tmp_path, "u2", exit_s=3000)
    assert T1.run(tmp_path, m, TwoBotDecider()) == 0
    g = json.loads((Path(m["out_root"]) / "gap_exclusions.json").read_text())
    assert g["main"]["total"]["entries"] == 1 and g["placebo"]["total"]["entries"] == 1 and "Strict feed-gap exclusion" in g["disclosure"]
