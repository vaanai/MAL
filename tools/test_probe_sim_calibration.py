"""Offline tests for tools/probe_sim_calibration.py: fixture tape and fills, seal, build split."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import probe_executor as pe
from tools import probe_sim_calibration as psc

M1, M2 = "MintAAAA1111pump", "MintBBBB2222pump"
T_AFTER = 1791226000000  # after the build split
T_BEFORE = 1791220000000
V = 17_584_505_451
BASE, Q0 = 400_000_000 * 10**6, 70 * 10**9
SPEND = 50_000_000


def trow(mint, slot, quote, t_ms, base=BASE, **extra):
    """A PumpSwap tape row. `quote` / `base` are the PRE-trade vaults (the tape convention); the row's post-trade state is the
    next row's pre-trade state. `extra` overrides or adds fields (pool, side, sol_lamports, token_raw, event_index, ...)."""
    return {"v": 2, "venue": "pumpswap", "quote_is_wsol": True, "mint": mint, "side": "buy", "pool": "P" + mint, "slot": slot, "tx_index": 0,
            "event_index": 0, "t_recv_ms": t_ms, "quote_reserve": quote, "base_reserve": base, "virtual_quote_reserve": V, **extra}


def fills(mint, t, landed_slot=102, reason="tp", pnl=1_000_000, sell_slot=106, snap=104):
    buy = {"schema": "probe_fill_v1", "mode": "live", "kind": "buy", "ts_ms": t, "mint": mint, "landed": True, "pool": "P" + mint,
           "landed_slot": landed_slot, "spend_lamports": SPEND, "tokens_received": 0, "fee_lamports": 505_000,
           "priority_fee_lamports": 500_000, "v_lamports": V, "snapshot_slot": 101, "entry_vs_quote_bps": -10}
    sell = {"schema": "probe_fill_v1", "mode": "live", "kind": "sell", "ts_ms": t + 5000, "mint": mint, "landed": True,
            "exit_reason": reason, "ret": 0.5, "sol_received_lamports": 70_000_000, "landed_slot": sell_slot,
            "snapshot_slot": snap, "fee_lamports": 505_000, "priority_fee_lamports": 500_000, "pnl_lamports": pnl,
            "hold_ms": 5000, "first_send_ms": t + 4000}
    return buy, sell


def tape_rows(mint, t):
    """PRE-trade rows. Post-trade state per slot (= the next row's pre): 100 Q0, 101 Q0, 103 Q0+5e9, 104 Q0+60e9 (tp),
    106 Q0+60e9 (the last row: no next row, so its post state is read raw)."""
    return [trow(mint, 100, Q0, t - 2000), trow(mint, 101, Q0, t - 1000),
            trow(mint, 103, Q0, t + 1000),
            trow(mint, 104, Q0 + 5 * 10**9, t + 2000),
            trow(mint, 106, Q0 + 60 * 10**9, t + 3000)]


def write_tape(d: Path, rows, t):
    d.mkdir(exist_ok=True)
    with (d / f"trades-{psc._hour_name(t)}.jsonl").open("a") as fh:
        fh.write("\n".join(json.dumps(r) for r in rows) + "\n")


def fills_file(tmp_path, items):
    p = tmp_path / "fills.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in items) + "\n")
    return p


def live_tokens(rows):
    rows = [dict(r) for r in rows]
    psc.attach_post_state(rows)
    return pe.entry_quote(psc.snap_of(rows[1]), SPEND)["tokens"]


def make(t=T_AFTER, mint=M1):
    rows = tape_rows(mint, t)
    b, s = fills(mint, t)
    b["tokens_received"] = live_tokens(rows) * 99 // 100  # live got 1% fewer
    return rows, b, s


def test_build_split():
    assert psc.build_of(1791222783000 - 1) == "8a6849b"
    assert psc.build_of(1791222783000) == "a25eb17"
    assert psc.build_of(1791232766000 - 1) == "a25eb17"
    assert psc.build_of(1791232766000) == "7004b16"
    assert psc.build_of(5, [(10, "x")]) == "unknown"
    assert psc.build_of(15, psc.parse_builds(["10:x", "20:y"])) == "x"


def test_pair_ignores_dryrun_and_failed(tmp_path):
    b, s = fills(M1, T_AFTER)
    b["tokens_received"] = 5
    dry = {**b, "mode": "dryrun", "mint": M2}
    failed = {**b, "landed": False, "mint": "M3"}
    trades = psc.pair_trades(psc.load_fills(fills_file(tmp_path, [b, s, dry, failed])))
    assert [t["buy"]["mint"] for t in trades] == [M1]


def test_end_to_end_entry_exit_and_seal(tmp_path):
    rows, b, s = make()
    d = tmp_path / "tape"
    write_tape(d, rows + tape_rows(M2, T_AFTER), T_AFTER)  # M2 is on tape but not in fills
    out = tmp_path / "out"
    agg = psc.run(fills_file(tmp_path, [b, s]), d, out)
    res = json.loads((out / "calibration.json").read_text())["trades"]
    assert [r["mint"] for r in res] == [M1]
    r = res[0]
    assert r["status"] == "ok" and r["build"] == "a25eb17"
    assert r["entry_row_slot"] == 101  # last row with slot < landed_slot
    assert r["entry_gap_bps"] == pytest.approx((1 / 0.99 - 1) * 1e4, abs=2)
    assert r["sim_exit_reason"] == "tp" and r["sim_trigger_slot"] == 104 and r["reason_agree"] is True
    assert r["live_sell_delay_slots"] == 2  # 106 - 104
    assert r["sim_pnl_delayed_lamports"] is not None
    assert r["pnl_gap_lamports"] == r["live_pnl_lamports"] - r["sim_pnl_delayed_lamports"]
    assert agg["a25eb17"]["n_trades"] == 1 and agg["8a6849b"]["n_trades"] == 0
    assert (out / "calibration.md").read_text().startswith("# Probe")


def test_priority_and_size_sensitivity():
    rows, b, s = make()
    r = psc.simulate_trade({"buy": b, "sell": s}, rows, {M1})
    base = r["sim_pnl_delayed_lamports"]
    assert r["sensitivity"]["priority_150k"]["sim_pnl_lamports"] == base + 700_000  # 2 sides * (500k - 150k)
    s1, s25 = r["sensitivity"]["size_0.1"], r["sensitivity"]["size_0.25"]
    # price impact is re-simulated with V, so it grows with size (not linear scaling)
    assert 0 < s1["entry_impact_bps_vs_spot"] < s25["entry_impact_bps_vs_spot"]


def test_seal_foreign_mint_rejected():
    rows, b, s = make(mint=M2)
    with pytest.raises(ValueError, match="seal"):
        psc.simulate_trade({"buy": b, "sell": s}, rows, {M1})


def test_tape_reader_drops_foreign_mints(tmp_path):
    d = tmp_path / "tape"
    write_tape(d, tape_rows(M1, T_AFTER) + tape_rows(M2, T_AFTER), T_AFTER)
    got = psc.read_tape_rows(d, [psc._hour_name(T_AFTER)], {M1})
    assert set(got) == {M1}


def test_tp_sl_disagree_and_build_aggregate(tmp_path):
    rows_a, ba, sa = make(T_AFTER, M1)
    sa["exit_reason"] = "sl"  # live says sl, tape says tp
    rows_b = tape_rows(M2, T_BEFORE)
    bb, sb = fills(M2, T_BEFORE)
    bb["tokens_received"] = live_tokens(rows_b)
    d = tmp_path / "tape"
    write_tape(d, rows_a, T_AFTER)
    write_tape(d, rows_b, T_BEFORE)
    agg = psc.run(fills_file(tmp_path, [ba, sa, bb, sb]), d, tmp_path / "o")
    assert agg["a25eb17"]["tp_sl_disagree"] == 1 and agg["8a6849b"]["tp_sl_disagree"] == 0
    assert agg["8a6849b"]["n_trades"] == 1 and agg["all"]["n_trades"] == 2
    assert agg["7004b16"]["n_trades"] == 0 and list(agg) == ["8a6849b", "a25eb17", "7004b16", "faa3192", "all"]
    md = (tmp_path / "o" / "calibration.md").read_text()
    assert "Per trade, every variant" in md and "live_snapshot_correct" in md
    agg2 = psc.run(fills_file(tmp_path, [ba, sa, bb, sb]), d, tmp_path / "o2", builds=psc.parse_builds([f"0:old", f"{T_AFTER}:new"]))
    assert agg2["new"]["n_trades"] == 1 and agg2["old"]["n_trades"] == 1


DEADLINE = T_AFTER + 30 * 60_000


def quiet_rows(extra=()):
    return [trow(M1, 100, Q0, T_AFTER - 2000), trow(M1, 101, Q0, T_AFTER - 1000),
            trow(M1, 103, Q0 + 10**9, T_AFTER + 60_000), *extra]


def test_time_stop_quiet_pool_priced_before_deadline():
    _, b, s = make()
    s["exit_reason"] = "time_stop"
    rows = quiet_rows()  # nothing after slot 103: no row ever crosses the deadline
    r = psc.simulate_trade({"buy": b, "sell": s}, rows, {M1})
    assert r["sim_exit_reason"] == "time_stop" and r["sim_trigger_slot"] == 103 and r["sim_trigger_ms"] == DEADLINE
    assert r["reason_agree"] is True
    # an uncovered deadline hour emits no exit
    assert psc.simulate_trade({"buy": b, "sell": s}, rows, {M1}, deadline_covered=False)["status"] == "no_exit_in_tape"


def test_time_stop_not_priced_on_row_past_deadline():
    _, b, s = make()
    # first row after the deadline (its own trade is quiet) and the row after it, whose pre-state makes the first row's post a tp
    spike = [trow(M1, 900, Q0 + 10**9, DEADLINE + 5000), trow(M1, 901, Q0 + 90 * 10**9, DEADLINE + 6000)]
    r = psc.simulate_trade({"buy": b, "sell": s}, quiet_rows(spike), {M1})
    assert r["sim_exit_reason"] == "time_stop" and r["sim_trigger_slot"] == 103


def test_delayed_sell_not_priced_on_own_sell_row():
    rows, b, s = make()
    rows = rows + [trow(M1, 107, Q0 + 1 * 10**9, T_AFTER + 4000)]  # the row at the landing slot (106) crashes the pool: its post state
    r = psc.simulate_trade({"buy": b, "sell": s}, rows, {M1})
    # target slot 104 + 2 = 106 -> last row with slot < 106 is the trigger row itself
    assert r["sim_pnl_delayed_lamports"] == r["sim_pnl_immediate_lamports"]


def test_double_count_variants():
    rows, b, s = make()
    r = psc.simulate_trade({"buy": b, "sell": s}, rows, {M1})
    ve, vc = r["variants"]["sim_executor"], r["variants"]["sim_correct"]
    assert ve["ret"] > vc["ret"]  # executor re-adds our buy: inflates the book ret
    assert r["ret_diff_executor_minus_correct"] > 0
    assert r["ret_diff_executor_minus_correct"] == pytest.approx(r["ret_executor_at_live_trigger"] - r["ret_correct_at_live_trigger"])
    assert r["primary_variant"] == "sim_correct" and r["sim_ret"] == vc["ret"]
    r2 = psc.simulate_trade({"buy": b, "sell": s}, rows, {M1}, own_trade_in_tape=False)
    assert r2["primary_variant"] == "sim_executor" and r2["sim_ret"] == ve["ret"]
    assert set(r["variants"]) == {"sim_executor", "sim_correct", "live_executor", "live_correct",
                                  "live_legacy_executor", "live_legacy_correct",
                                  "live_snapshot_executor", "live_snapshot_correct"}


def test_live_position_marks_post_buy_vs_legacy_vs_snapshot():
    rows, b, s = make()
    psc.attach_post_state(rows)
    er = psc.last_before(rows, int(b["landed_slot"]))
    q = psc.sim_buy(er, int(b["spend_lamports"]))
    cur = psc.live_position(b, er, q)
    snap_er = psc.snap_of(er)
    assert cur["mark_source"] == "buy_tx_post"
    assert cur["mark"] == pytest.approx(psc.pcm.spot_sol_per_ui(snap_er.quote_priced + cur["net_in"], snap_er.base_reserve - cur["tokens"]))
    leg = psc.live_legacy_position(b, rows, er, q)
    assert leg["mark_source"] == "send_state" and leg["tokens"] == cur["tokens"]
    first = next(r for r in rows if r["slot"] >= int(b["landed_slot"]))
    ss = psc.live_snapshot_position(b, rows, er, q)
    sn = psc.snap_of(first)
    assert ss["mark_source"] == "landed_snapshot"
    assert ss["mark"] == pytest.approx(psc.pcm.spot_sol_per_ui(sn.quote_priced, sn.base_reserve))
    fb = psc.live_snapshot_position(b, [], er, q)
    assert fb["mark_source"] == "fill_price" and fb["mark"] == pytest.approx(cur["net_in"] / (cur["tokens"] * 1000))


def test_pnl_parity_extra_cost_and_rent():
    rows, b, s = make()
    base = psc.simulate_trade({"buy": b, "sell": s}, rows, {M1})["sim_pnl_delayed_lamports"]
    b2 = {**b, "rent_charged_lamports": 2_039_280}
    s2 = {**s, "failed_attempt_cost_lamports": 7_000, "rent_refunded_lamports": 2_039_000}
    r = psc.simulate_trade({"buy": b2, "sell": s2}, rows, {M1})
    assert r["rent_net_lamports"] == 280
    assert r["sim_pnl_delayed_lamports"] == base - 7_000 - 280


def test_cli_flag_default_on(tmp_path):
    rows, b, s = make()
    d = tmp_path / "tape"
    write_tape(d, rows, T_AFTER)
    f = fills_file(tmp_path, [b, s])
    assert psc.main(["--fills", str(f), "--tape-dir", str(d), "--out-dir", str(tmp_path / "o1")]) == 0
    assert json.loads((tmp_path / "o1/calibration.json").read_text())["own_trade_in_tape"] is True
    assert json.loads((tmp_path / "o1/calibration.json").read_text())["schema_version"] == psc.SCHEMA_VERSION == 4
    psc.main(["--fills", str(f), "--tape-dir", str(d), "--out-dir", str(tmp_path / "o2"), "--no-own-trade-in-tape"])
    assert json.loads((tmp_path / "o2/calibration.json").read_text())["own_trade_in_tape"] is False
    assert "Sim reproduces the executor's exit decisions on 1/1 (n=1)" in (tmp_path / "o1/calibration.md").read_text()


def _cap_rows(frac, mint=M1, t=T_AFTER):
    """Migration-slot print at the base price (slot 100), then a print at `frac` x that V-priced price (slot 101)."""
    q1 = int((Q0 + V) * frac) - V
    return [trow(mint, 100, Q0, t - 2000), trow(mint, 101, q1, t - 1000)]


@pytest.mark.parametrize("frac,miss", [(1.0, False), (1.10, False), (1.14, False), (1.20, True), (0.6, False)])
def test_cap_fields_flag_matches_sim_cap(frac, miss):
    r = psc.cap_fields(_cap_rows(frac), 102, SPEND)
    ref = (Q0 + V) / (BASE * 1000)
    assert r["p_mig_first_print"] == pytest.approx(ref)
    assert r["drift_land"] == pytest.approx(frac - 1.0, abs=1e-9)
    assert r["sim_cap_miss"] is miss


def test_cap_flag_uses_the_sim_constant_not_a_copy(monkeypatch):
    assert psc.cap_fields(_cap_rows(1.20), 102, SPEND)["sim_cap_miss"] is True
    monkeypatch.setattr(psc.lc, "SLIPPAGE_CAP", 0.5)
    assert psc.cap_fields(_cap_rows(1.20), 102, SPEND)["sim_cap_miss"] is False


def test_cap_fields_unknown_without_prints_or_state():
    assert psc.cap_fields([], 102, SPEND)["sim_cap_miss"] is None
    only_mig = psc.cap_fields(_cap_rows(1.0)[:1], 100, SPEND)  # no print before the landing slot
    assert only_mig["p_mig_first_print"] is not None and only_mig["sim_cap_miss"] is None
    no_v = [{k: v for k, v in r.items() if k != "virtual_quote_reserve"} for r in _cap_rows(1.0)]
    assert psc.cap_fields(no_v, 102, SPEND)["p_mig_first_print"] is None  # never a V-less price


def test_cap_miss_aggregate_per_build_and_trade_fields(tmp_path):
    ra, ba, sa = make(T_AFTER, M1)  # fixture tape sits at ~1.0 x the migration price at landing: not a miss
    rb = _cap_rows(1.25, M2, T_BEFORE) + tape_rows(M2, T_BEFORE)[2:]
    bb, sb = fills(M2, T_BEFORE, pnl=-7_000_000)
    bb["tokens_received"] = live_tokens(tape_rows(M2, T_BEFORE))
    d = tmp_path / "tape"
    write_tape(d, ra, T_AFTER)
    write_tape(d, rb, T_BEFORE)
    agg = psc.run(fills_file(tmp_path, [ba, sa, bb, sb]), d, tmp_path / "o")
    old, new = agg["8a6849b"]["sim_cap_miss"], agg["a25eb17"]["sim_cap_miss"]
    assert (old["n_sim_cap_miss"], old["live_pnl_lamports_cap_miss"], old["live_pnl_lamports_others"]) == (1, -7_000_000, 0)
    assert (new["n_sim_cap_miss"], new["n_not_cap_miss"], new["live_pnl_lamports_others"]) == (0, 1, 1_000_000)
    assert agg["all"]["sim_cap_miss"]["n_sim_cap_miss"] == 1 and old["cap"] == psc.lc.SLIPPAGE_CAP
    trades = json.loads((tmp_path / "o/calibration.json").read_text())["trades"]
    miss = next(t for t in trades if t["mint"] == M2)
    assert miss["sim_cap_miss"] is True and miss["drift_land"] == pytest.approx(0.25, abs=1e-6) and miss["p_mig_first_print"] > 0


# --- PumpSwap tape reserves are PRE-trade (audit 2026-10-08, xcheck_reserves): the price state is the next row's pre-state ---


def chain(mint, pre_quotes, pool=None, t0=T_AFTER, slot0=200):
    """Rows of one pool, one per pre-trade quote, a trade every 1000 ms; base falls by 10^9 per row so that no two states repeat."""
    extra = {} if pool is None else {"pool": pool}
    return [trow(mint, slot0 + i, q, t0 + 1000 * i, base=BASE - i * 10**9, **extra) for i, q in enumerate(pre_quotes)]


def test_post_state_of_a_row_is_the_next_rows_pre_state():
    rows = chain(M1, [Q0, Q0 + 1 * 10**9, Q0 + 3 * 10**9])
    psc.attach_post_state(rows)
    assert [(r["_post_q"], r["_post_b"]) for r in rows[:2]] == [(r["quote_reserve"], r["base_reserve"]) for r in rows[1:]]
    for prev, nxt in zip(rows, rows[1:]):
        snap = psc.snap_of(prev)
        assert (snap.quote_vault, snap.base_reserve) == (nxt["quote_reserve"], nxt["base_reserve"])
        assert psc.post_state_of(prev)[0] == "next_row"
    assert "_post_q" not in rows[-1]


def test_post_state_through_the_tape_reader_and_sorting(tmp_path):
    rows = chain(M1, [Q0, Q0 + 1 * 10**9, Q0 + 3 * 10**9])
    d = tmp_path / "tape"
    write_tape(d, [rows[2], rows[0], rows[1]], T_AFTER)  # file order is not slot order: the reader sorts, then attaches
    got = psc.read_tape_rows(d, [psc._hour_name(T_AFTER)], {M1})[M1]
    assert [r["slot"] for r in got] == [200, 201, 202]
    assert got[0]["_post_q"] == Q0 + 1 * 10**9 and got[1]["_post_q"] == Q0 + 3 * 10**9
    assert got[0]["_post_b"] == BASE - 1 * 10**9 and "_post_q" not in got[2]


def test_post_state_is_per_pool_when_a_mint_spans_pools():
    a, b = chain(M1, [Q0, Q0 + 1 * 10**9], pool="PA"), chain(M1, [Q0 * 2, Q0 * 2 + 5 * 10**9], pool="PB", t0=T_AFTER + 500)
    rows = [a[0], b[0], a[1], b[1]]  # interleaved in slot order: PA 200, PB 200, PA 201, PB 201
    rows[1]["event_index"], rows[3]["event_index"] = 1, 1
    psc.attach_post_state(rows)
    assert rows[0]["_post_q"] == Q0 + 1 * 10**9 and rows[1]["_post_q"] == Q0 * 2 + 5 * 10**9
    assert "_post_q" not in rows[2] and "_post_q" not in rows[3]  # the last row of each pool


def test_same_event_twice_does_not_stand_in_for_the_next_trade():
    r0, r1, r2 = chain(M1, [Q0, Q0 + 1 * 10**9, Q0 + 3 * 10**9])
    rows = [r0, dict(r0, t_recv_ms=r0["t_recv_ms"] + 7), r1, r2]  # r0 recorded twice (same slot / tx_index / event_index / pre-state)
    psc.attach_post_state(rows)
    assert [r.get("_post_q") for r in rows] == [Q0 + 1 * 10**9, Q0 + 1 * 10**9, Q0 + 3 * 10**9, None]
    # a duplicated LAST row has no next row either: both copies fall back
    rows = [r2, dict(r2)]
    psc.attach_post_state(rows)
    assert all("_post_q" not in r for r in rows)
    # the same pre-state under another event_index is a different trade, not a duplicate
    other = dict(r0, event_index=1)
    rows = [r0, other, r1]
    psc.attach_post_state(rows)
    assert rows[0]["_post_q"] == other["quote_reserve"] and rows[1]["_post_q"] == r1["quote_reserve"]


def test_attach_is_idempotent_and_drops_stale_post_state():
    rows = chain(M1, [Q0, Q0 + 1 * 10**9, Q0 + 3 * 10**9])
    psc.attach_post_state(rows)
    once = [dict(r) for r in rows]
    psc.attach_post_state(rows)
    assert rows == once
    psc.attach_post_state(rows[:1])  # now the first row is the last row of its list: the stale post state must go
    assert "_post_q" not in rows[0]


def test_last_row_falls_back_to_its_own_advanced_print(tmp_path):
    # last row of the pool with sol_lamports / token_raw: the lab's own advance (paper_price_path.pumpswap_post_trade_reserves)
    tok, sol = 2 * 10**12, 3 * 10**9
    last = trow(M1, 300, Q0, T_AFTER, side="buy", sol_lamports=sol, token_raw=tok)
    d = tmp_path / "tape"
    write_tape(d, [last], T_AFTER)
    got = psc.read_tape_rows(d, [psc._hour_name(T_AFTER)], {M1})[M1][0]
    psc.SNAP_FALLBACK_RAW["n"] = 0
    src, q, b = psc.post_state_of(got)
    assert src == "print" and b == BASE - tok and Q0 < q <= Q0 + sol  # base exact; quote = the lab's pool-net estimate
    snap = psc.snap_of(got)
    assert (snap.quote_vault, snap.base_reserve, snap.v) == (q, b, V)
    assert psc.SNAP_FALLBACK_RAW["n"] == 0


def test_next_row_state_equals_the_lab_advance_on_a_consistent_chain(tmp_path):
    # when the next row's pre-state IS the lab's advanced state (exact in base, the lab's fee model in quote), both sources agree
    tok, sol = 2 * 10**12, 3 * 10**9
    first = trow(M1, 300, Q0, T_AFTER, side="buy", sol_lamports=sol, token_raw=tok)
    first["_print"] = psc.print_of(first)
    q, b = first["_print"].quote_reserve - V, first["_print"].base_reserve
    assert psc.post_state_of(first) == ("print", q, b)
    rows = [first, trow(M1, 301, q, T_AFTER + 1000, base=b)]
    psc.attach_post_state(rows)
    assert psc.post_state_of(rows[0]) == ("next_row", q, b)


def test_raw_fallback_is_counted_per_call():
    psc.SNAP_FALLBACK_RAW["n"] = 0
    lone = trow(M1, 400, Q0, T_AFTER)  # last row, no sol_lamports / token_raw: the lab cannot advance it
    lone["_print"] = psc.print_of(lone)
    assert lone["_print"] is not None and lone["_print"].base_reserve == lone["base_reserve"]  # not advanced: must not count as "print"
    assert psc.post_state_of(lone) == ("raw", Q0, BASE)
    psc.snap_of(lone), psc.snap_of(lone)
    assert psc.SNAP_FALLBACK_RAW["n"] == 2
    psc.snap_of(dict(lone, _post_q=Q0 + 1, _post_b=BASE))  # a row with a post state is not a fallback
    assert psc.SNAP_FALLBACK_RAW["n"] == 2


def test_bonding_rows_stay_post_trade_and_do_not_join_the_chain(tmp_path):
    bond = {"venue": "pump_bonding", "mint": M1, "slot": 201, "tx_index": 0, "event_index": 0, "t_recv_ms": T_AFTER + 1000, "side": "buy",
            "quote_reserve": 31 * 10**9, "base_reserve": 900 * 10**12}
    ps = chain(M1, [Q0, Q0 + 1 * 10**9, Q0 + 3 * 10**9])
    rows = [ps[0], bond, ps[1], ps[2]]
    psc.attach_post_state(rows)
    assert "_post_q" not in bond and rows[0]["_post_q"] == Q0 + 1 * 10**9  # not chained to the bonding row
    psc.SNAP_FALLBACK_RAW["n"] = 0
    assert psc.post_state_of(bond) == ("post_trade_venue", 31 * 10**9, 900 * 10**12)
    assert psc.snap_of(bond).quote_vault == 31 * 10**9 and psc.SNAP_FALLBACK_RAW["n"] == 0
    # the reader never keeps bonding rows (this tool prices PumpSwap only)
    d = tmp_path / "tape"
    write_tape(d, [bond, *ps], T_AFTER)
    kept = psc.read_tape_rows(d, [psc._hour_name(T_AFTER)], {M1})[M1]
    assert len(kept) == 3 and all(r["quote_reserve"] != 31 * 10**9 for r in kept)


def test_entry_is_priced_on_the_post_trade_state_of_the_last_row_before_landing():
    # entry row = slot 101 (< landed 102). Its pre-state is Q0; the next row (103) shows the pool at Q0 + 2e9 before it traded.
    t = T_AFTER
    rows = [trow(M1, 100, Q0, t - 2000), trow(M1, 101, Q0, t - 1000), trow(M1, 103, Q0 + 2 * 10**9, t + 1000)]
    b, s = fills(M1, t)
    b["tokens_received"] = 1
    r = psc.simulate_trade({"buy": b, "sell": s}, rows, {M1})
    want = pe.entry_quote(pe.Snapshot(ps=None, slot=101, quote_vault=Q0 + 2 * 10**9, base_reserve=BASE, v=V), SPEND)["tokens"]  # type: ignore[arg-type]
    raw = pe.entry_quote(pe.Snapshot(ps=None, slot=101, quote_vault=Q0, base_reserve=BASE, v=V), SPEND)["tokens"]  # type: ignore[arg-type]
    assert r["entry_row_slot"] == 101 and r["sim_tokens"] == want != raw


def test_run_reports_the_post_state_sources_and_raw_fallback_calls(tmp_path):
    rows, b, s = make()
    d = tmp_path / "tape"
    write_tape(d, rows, T_AFTER)
    psc.run(fills_file(tmp_path, [b, s]), d, tmp_path / "o")
    out = json.loads((tmp_path / "o/calibration.json").read_text())
    assert out["tape_convention"] == "pumpswap_pre_trade" and out["schema_version"] == psc.SCHEMA_VERSION
    assert out["post_state_sources"] == {"next_row": 4, "raw": 1}  # slots 100..104 have a next row; 106 is the last row
    assert out["snap_fallback_raw_calls"] == 0  # the walk stops at the tp row (104) and never touches the raw last row
    assert "post_state_sources" in (tmp_path / "o/calibration.md").read_text()
    # a quiet pool: the time stop is priced at the LAST row (103, no next row), which is read raw on every touch
    b["ts_ms"], s["exit_reason"] = T_AFTER, "time_stop"
    d2 = tmp_path / "tape2"
    write_tape(d2, quiet_rows(), T_AFTER)
    write_tape(d2, [], T_AFTER + 3_600_000)  # the deadline hour is on disk (empty), so the time stop is emitted
    psc.run(fills_file(tmp_path, [b, s]), d2, tmp_path / "o2")
    out2 = json.loads((tmp_path / "o2/calibration.json").read_text())
    assert out2["post_state_sources"] == {"next_row": 2, "raw": 1} and out2["snap_fallback_raw_calls"] > 0
