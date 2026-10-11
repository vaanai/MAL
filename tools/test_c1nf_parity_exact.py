"""Offline tests for tools/c1nf_parity_exact.py (DEC-026 items 13 and 14a). Synthetic rows only: no tape, no network, no model file."""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict

import numpy as np
import pytest

from tools import c1nf_parity_exact as px
from tools import c1nf_shadow as cs
from tools import c1nf_parity as cp
from tools.c1nf_features import FEATURE_NAMES, FeatureEngine
from tools.test_c1nf_shadow import BT0, MINT, POOL, SPS, V0
from tools.test_c1nf_vmode_parity import _batches, _models


def _rows(until=3000):
    return _batches(False, until=until)[0]


def _la(rows, pool_v=None):
    la = px.Lookahead(pool_v if pool_v is not None else {POOL: V0})
    la.add_rows(rows)
    return la.freeze()


class _Snap:
    def get(self, trader):
        return None                                           # every wallet unknown: wallet features NaN, wallet_ok True


class _Ledger:
    def snapshot_for_day(self, day):
        return _Snap()


def _capture(eng, force_stage1=False):
    """Wrap the instance's features_at (the shadow calls self.engine.features_at) and keep every answer."""
    seen = []
    base = eng.features_at

    def wrapped(pool, T, sd=None):
        r = base(pool, T, sd)
        if r is not None:
            if force_stage1:
                r.stage1 = True
            seen.append((r.pool, int(r.t), int(r.sd), bool(r.stage1), float(r.h_top1), np.asarray(r.vec, dtype=np.float64).tobytes()))
        return r

    eng.features_at = wrapped
    return seen


def _run(eng, rows, force_stage1=False, hour_sps=None):
    seen = _capture(eng, force_stage1)
    sink = cs.MemorySink()
    cls = px.ExactShadow if getattr(eng, "state_mode", None) == px.STATE_EXACT else cs.Shadow   # run_replay_mode's choice
    sh = cls(eng, _models(), sink, replay=True, seal_start_ms=None, v_source=cs.V_SOURCE_REPLAY)
    sh.clock.hour_sps = hour_sps or {cs.hour_of(BT0): SPS, cs.hour_of(BT0 + 3600): SPS}
    for r in rows:
        sh.feed(r, r["_k"])
    sh.finish("end")
    return seen, sink, sh


# ---- look-ahead ---------------------------------------------------------------------------------------------------------------------------
def _tr(slot, tx, ev, q, b=1e15, pool=POOL, venue="pumpswap", bt=None):
    return {"_k": "trades", "venue": venue, "pool": pool, "slot": slot, "tx_index": tx, "event_index": ev, "block_time": bt if bt is not None else BT0 + slot,
            "quote_reserve": q, "base_reserve": b}


def test_lookahead_returns_the_first_print_at_or_after_sd_in_tape_order():
    rows = [_tr(10, 2, 0, 3.0), _tr(10, 1, 1, 2.0), _tr(10, 1, 0, 1.0), _tr(12, 0, 0, 4.0), _tr(11, 0, 0, 9.0, pool="OTHER"),
            _tr(11, 0, 0, 8.0, venue="pump_bonding")]
    la = _la(rows)
    assert la.rows == 4                                       # other pools (not in pool_v) and bonding rows are not indexed
    assert la.next_state(POOL, 10) == (1.0, 1e15)            # slot 10: (tx 1, ev 0) comes first
    assert la.next_state(POOL, 9) == (1.0, 1e15)
    assert la.next_state(POOL, 11) == (4.0, 1e15)            # no print at 11: the first one after it
    assert la.next_state(POOL, 13) is None
    assert la.next_state("OTHER", 5) is None
    assert la.c["hit"] == 3 and la.c["none_after_sd"] == 1 and la.c["no_pool"] == 1


def test_lookahead_horizon_and_invalid_reserves_leave_the_decision_live():
    la = _la([_tr(10, 0, 0, 1.0, bt=1000), _tr(20, 0, 0, None, bt=2000), _tr(30, 0, 0, float("nan"), bt=3000)])
    assert la.next_state(POOL, 5, horizon=1000) is None and la.c["past_horizon"] == 1
    assert la.next_state(POOL, 5, horizon=1001) == (1.0, 1e15)
    assert la.next_state(POOL, 15) is None and la.next_state(POOL, 25) is None and la.c["invalid_reserves"] == 2


def test_lookahead_refuses_hours_out_of_order():
    la = px.Lookahead({POOL: V0})
    la.add_rows([_tr(20, 0, 0, 1.0)])
    with pytest.raises(ValueError):
        la.add_rows([_tr(10, 0, 0, 1.0)])


def test_horizon_is_the_batch_window_end_of_the_graduation_day():
    g0 = 1_788_523_200 + 5 * 3600                             # 2026-09-04T17:00Z
    assert px.horizon_bt(g0) == 1_788_480_000 + 2 * 86400 + 2 * 3600   # 2026-09-06T02:00Z (c1nf_parity.end_hour_of)
    assert px.horizon_bt(None) is None


# ---- the engine switch ----------------------------------------------------------------------------------------------------------------------
def test_engine_settings_are_the_shadow_build_engine_settings():
    ref = cs.build_engine(None, v_source=cs.V_SOURCE_REPLAY)
    eng = px.StateModeEngine(None, v_source=cs.V_SOURCE_REPLAY, state_mode="live")
    assert isinstance(eng, FeatureEngine) and type(ref) is FeatureEngine
    for a in ("ledger_keep_days", "print_window", "v_source", "strict", "prune_bonding_idle_s", "prune_info_idle_s"):
        assert getattr(eng, a) == getattr(ref, a), a
    with pytest.raises(ValueError):
        px.StateModeEngine(None, v_source=cs.V_SOURCE_REPLAY, state_mode="exact")         # no look-ahead
    with pytest.raises(ValueError):
        px.StateModeEngine(None, v_source=cs.V_SOURCE_REPLAY, state_mode="fast", lookahead=_la([]))


def test_live_mode_is_the_shadow_replay_bit_for_bit():
    rows = _rows()
    a_seen, a_sink, a_sh = _run(cs.build_engine(_Ledger(), v_source=cs.V_SOURCE_REPLAY), rows, force_stage1=True)
    b_seen, b_sink, b_sh = _run(px.StateModeEngine(_Ledger(), v_source=cs.V_SOURCE_REPLAY, state_mode="live"), rows, force_stage1=True)
    assert len(a_seen) > 20 and a_seen == b_seen
    assert a_sink.of("c1nf_pick") and a_sink.of("c1nf_pick") == b_sink.of("c1nf_pick")
    assert dict(a_sh.c) == dict(b_sh.c)


def test_exact_mode_feeds_the_next_prints_pre_trade_state_and_changes_only_the_state():
    rows = _rows()
    la = _la(rows)
    live_seen, live_sink, _ = _run(px.StateModeEngine(_Ledger(), v_source=cs.V_SOURCE_REPLAY, state_mode="live"), rows, force_stage1=True)
    eng = px.StateModeEngine(_Ledger(), v_source=cs.V_SOURCE_REPLAY, state_mode="exact", lookahead=la)
    ex_seen, ex_sink, ex_sh = _run(eng, rows, force_stage1=True)
    assert [s[:3] for s in ex_seen] == [s[:3] for s in live_seen]              # same decisions (pool, T, SD)
    assert eng.exact_injected == len(ex_seen) and la.c["hit"] == len(ex_seen)
    rep = ex_sh.exact_report()                                                  # the pool prints every slot: each deferral resolves at SD
    assert rep["ok"] and rep["deferred"] == rep["resolved"] == len(ex_seen) and rep["outcomes_not_priced"] >= 1
    # each exact answer equals the base engine asked with the next print's pre-trade reserves, computed independently
    ref = cs.build_engine(_Ledger(), v_source=cs.V_SOURCE_REPLAY)
    want = {}
    nxt = sorted((r["slot"], r["quote_reserve"], r["base_reserve"]) for r in rows if r.get("venue") == "pumpswap")
    base_fa = ref.features_at

    def ref_fa(pool, T, sd=None):
        q, b = next((q, b) for s, q, b in nxt if s >= sd)
        r = base_fa(pool, T, sd, next_state=(q, b))
        want[(pool, int(T))] = np.asarray(r.vec, dtype=np.float64).tobytes()
        r.stage1 = True
        return r

    ref.features_at = ref_fa
    sink = cs.MemorySink()
    sh = cs.Shadow(ref, _models(), sink, replay=True, seal_start_ms=None, v_source=cs.V_SOURCE_REPLAY)
    sh.clock.hour_sps = {cs.hour_of(BT0): SPS, cs.hour_of(BT0 + 3600): SPS}
    for r in rows:
        sh.feed(r, r["_k"])
    assert all(want[(p, T)] == v for p, T, _, _, _, v in ex_seen)
    assert sum(a[5] != b[5] for a, b in zip(ex_seen, live_seen)) > 0         # the state differs on this ramp
    # the pick record: same keys and decision state (prints with slot < SD); the feature hash follows the vector
    lp, ep = live_sink.of("c1nf_pick"), ex_sink.of("c1nf_pick")
    key = ("mint", "decision_T_ms", "SD_slot", "q_lamports", "base_reserve", "v_lamports", "state_slot")
    assert [tuple(p[k] for k in key) for p in lp] == [tuple(p[k] for k in key) for p in ep]
    assert px.p3_md5(lp)["md5"] == px.p3_md5(ep)["md5"]
    assert any(a["feature_hash"] != b["feature_hash"] for a, b in zip(lp, ep))


def test_stage1_recorder_keeps_float32_vectors_of_stage1_rows_with_a_wallet_snapshot():
    eng = px.StateModeEngine(_Ledger(), v_source=cs.V_SOURCE_REPLAY, state_mode="live")
    seen, _, _ = _run(eng, _rows(), force_stage1=False)
    s1 = [s for s in seen if s[3]]
    assert len(eng.stage1_rows) == len(s1)
    for (pool, mint, T, sd, h, vec), s in zip(eng.stage1_rows, s1):
        assert (pool, T, sd) == s[:3] and vec.dtype == np.float32 and mint == MINT
    eng2 = px.StateModeEngine(None, v_source=cs.V_SOURCE_REPLAY, state_mode="live")        # no ledger: wallet_ok False, nothing kept
    _run(eng2, _rows(), force_stage1=True)
    assert eng2.stage1_rows == []


# ---- P3's list ----------------------------------------------------------------------------------------------------------------------------
def test_p3_md5_is_the_sorted_newline_terminated_list_per_day_and_overall():
    picks = [{"mint": "B", "decision_T_ms": 1_788_566_400_000 + 60_000, "SD_slot": 7},        # 2026-09-05
             {"mint": "A", "decision_T_ms": 1_788_523_200_000, "SD_slot": 5}]                # 2026-09-04
    out = px.p3_md5(picks)
    lines = ["A,1788523200000,5", "B,1788566460000,7"]
    assert out["n"] == 2 and out["md5"] == hashlib.md5(("\n".join(lines) + "\n").encode()).hexdigest()
    assert out["days"]["2026-09-04"] == {"n": 1, "md5": hashlib.md5(b"A,1788523200000,5\n").hexdigest()}
    assert px.p3_md5([])["md5"] == hashlib.md5(b"").hexdigest()


# ---- the CLI's refusals ---------------------------------------------------------------------------------------------------------------------
def test_main_refuses_before_reading(tmp_path, monkeypatch):
    monkeypatch.setattr(px, "lookahead_from_tape", lambda *a, **k: (_ for _ in ()).throw(AssertionError("read the tape")))
    base = ["--state-mode", "exact", "--replay-hours", "2", "--model", "m", "--model-sha256", "x"]
    assert px.main(base + ["--replay-from", "2026-10-02T00", "--out-dir", str(tmp_path / "a")]) == cs.EXIT_REFUSED   # October hour
    assert px.main(base + ["--replay-from", "2026-09-04T00", "--out-dir", str(tmp_path / "walk-2")]) == cs.EXIT_REFUSED
    full = tmp_path / "full"
    full.mkdir()
    (full / "x").write_text("1")
    assert px.main(base + ["--replay-from", "2026-09-04T00", "--out-dir", str(full)]) == cs.EXIT_REFUSED            # not empty
    assert px.main(["--state-mode", "live", "--replay-from", "2026-09-04T00", "--replay-hours", "2",
                    "--out-dir", str(tmp_path / "b")]) == cs.EXIT_USAGE                                               # no model


def test_lookahead_from_tape_matches_add_rows(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    rows = _rows(until=200)
    (tmp_path / "trades").mkdir()
    df = pd.DataFrame([{k: r.get(k) for k in ("venue", "pool", "slot", "tx_index", "event_index", "block_time", "quote_reserve", "base_reserve")}
                       for r in rows if r["_k"] == "trades"])
    h = cs.hour_of(BT0)
    df.to_parquet(tmp_path / "trades" / f"{h}.parquet")
    a = px.lookahead_from_tape(str(tmp_path), [h], {POOL: V0})
    b = _la(rows)
    assert a.rows == b.rows and all(np.array_equal(x, y) for x, y in zip(a._idx[POOL], b._idx[POOL]))
    assert not math.isnan(a.next_state(POOL, rows[5]["slot"])[0])


# ---- cross-harness: run A's evaluation point is #506's (tools/c1nf_parity.py:225-230) ----------------------------------------------------
# One creator C with two graduated mints. Q (graduated at t=10) prints once, so at P's decisions its 6 h outcome is unknown
# (outcome_now: fewer than 2 prints) and P's cr_known_out is 0. Q prints again (at or after g0 + 6 h) AFTER P's SD and BEFORE P's next
# print: at #506's evaluation point Q.outcome is exact and cr_known_out is 1, cr_lmax6 / cr_lend6 / cr_big6 are set.
XS, XBT = 5_000_000, BT0                          # slot = XS + t, block_time = XBT + t (1 slot per second)
XC, XQ, XP, XQP, XPP, XMX = "CREATORc", "MINTqqqq", "MINTpppp", "POOLqqqq", "POOLpppp", "MINTxxxx"
X_GQ, X_GP = 10, 21650                            # graduation offsets; Q's g0 + 6 h = 21610 <= every T of P's grid
X_Q2, X_PNEXT, X_END = 22850, 23150, 23400        # Q's second print; P's next print after its quiet spell; end of the fixture


def _xrow(t, k, **kw):
    r = {"_k": k, "slot": XS + t, "block_time": XBT + t, "tx_index": kw.pop("tx", 1), "event_index": 0}
    r.update(kw)
    return r


def _xps(t, pool, mint, trader, buy, sol, tok, q, b):
    return cs.stamp_replay_v(_xrow(t, "trades", venue="pumpswap", pool=pool, mint=mint, trader=trader, side="buy" if buy else "sell",
                                   sol_lamports=sol, token_raw=tok, quote_reserve=q, base_reserve=b), V0, event_v=False)


def _xrows():
    rows = [_xrow(0, "creates", mint=XQ, creator=XC, name="dog moon", symbol="DOG", is_mayhem_mode=False),
            _xrow(5, "creates", mint=XP, creator=XC, name="dog sun", symbol="DOGS", is_mayhem_mode=False),
            _xrow(X_GQ, "migrations", type="complete", mint=XQ),
            _xps(15, XQP, XQ, "qa", True, 1e8, 1e11, 85e9, 2e14),
            _xrow(X_GP, "migrations", type="complete", mint=XP),
            _xps(X_Q2, XQP, XQ, "qb", False, 1e8, 1e11, 95e9, 1.9e14)]
    q, b = 85e9, 2e14
    pts = [(X_GP + 7, "p0", True), (X_GP + 60, "p1", True), (X_GP + 120, "p2", False)]
    pts += [(X_GP + 700 + 15 * k, f"n{k}", True) for k in range(12)]
    pts += [(X_PNEXT, "late", True), (X_PNEXT + 60, "late2", False), (X_PNEXT + 120, "late3", True)]
    for t, tr, buy in pts:
        rows.append(_xps(t, XPP, XP, tr, buy, 2e8, 1e12, q, b))
        q, b = (q + 2e8, b - 1e12) if buy else (q - 2e8, b + 1e12)
    for t in range(20, X_END, 30):                # the clock: a bonding print every 30 s (every whole minute has a row at T)
        rows.append(_xrow(t, "trades", tx=0, venue="pump_bonding", mint=XMX, trader="bx", side="buy", sol_lamports=1e6, token_raw=1e9,
                          quote_reserve=30e9, base_reserve=1e15, pool=None))
    rows.sort(key=cs.row_key)
    return rows


def _ref506(rows, la):
    """tools/c1nf_parity.py replay_days' loop (lines 158-246), on in-memory rows: decisions at every whole minute with SD = the least trade
    slot with block_time >= T, evaluated live; each one again at the pool's next PumpSwap print, before on_trade, with that print's
    (quote_reserve, base_reserve); the hourly expire(lastbt - 86400, market_keep_s=1e9). Also returns the pre-fix #594 answer (the
    look-ahead state injected at SD), to show the fixture separates the two."""
    eng = FeatureEngine(ledger=_Ledger(), v_source="const")
    for pool in (XQP, XPP):
        eng.set_pool_v(pool, V0)
    trades = [r for r in rows if r["_k"] == "trades"]
    first = {}
    for r in trades:
        first[r["block_time"]] = min(first.get(r["block_time"], 1 << 62), r["slot"])
    cbt = np.array(sorted(first), dtype=np.int64)
    cs_ = np.array([first[x] for x in cbt], dtype=np.int64)
    Ts = np.arange((int(cbt[0]) + 59) // 60 * 60, int(cbt[-1]) + 1, 60, dtype=np.int64)
    sds = cs_[np.clip(np.searchsorted(cbt, Ts, "left"), 0, len(cs_) - 1)]
    order = np.lexsort((Ts, sds))
    dec = list(zip(sds[order].tolist(), Ts[order].tolist()))
    live, exact, old, pend = {}, {}, {}, defaultdict(list)
    di, hour, lastbt = 0, None, 0
    for r in rows:
        h = r["block_time"] // 3600
        if hour is not None and h != hour and lastbt:
            eng.expire(lastbt - 86400, market_keep_s=10 ** 9)
        hour = h
        if r["_k"] == "creates":
            eng.on_create(r["mint"], r["creator"], r["name"], r["symbol"], r["block_time"], r["is_mayhem_mode"])
            continue
        if r["_k"] == "migrations":
            eng.on_graduation(r["mint"], r["block_time"])
            continue
        slot = r["slot"]
        while di < len(dec) and dec[di][0] <= slot:
            sd, T = dec[di]
            di += 1
            for pid in eng.alive_pools(T):
                if T > cp.t_end_of(eng._pools[pid].g0):
                    continue
                f = eng.features_at(pid, T, sd)
                if f is None:
                    continue
                live[(pid, T)] = f.vec.copy()
                pend[pid].append((T, sd))
                ns = la.next_state(pid, sd, px.horizon_bt(eng._pools[pid].g0))
                o = eng.features_at(pid, T, sd, next_state=ns) if ns is not None else f
                old[(pid, T)] = o.vec.copy()
        if r["venue"] == "pumpswap" and r["pool"] in pend:
            for T, sd in pend.pop(r["pool"]):
                f = eng.features_at(r["pool"], T, sd, next_state=(r["quote_reserve"], r["base_reserve"]))
                if f is not None:
                    exact[(r["pool"], T)] = f.vec.copy()
        eng.on_trade(r["venue"], r["mint"], r["trader"], r["side"] == "buy", r["sol_lamports"], r["token_raw"], r["quote_reserve"],
                     r["base_reserve"], r.get("pool"), slot, r["block_time"])
        lastbt = r["block_time"]
    return {k: exact.get(k, v) for k, v in live.items()}, old


def test_exact_mode_equals_506s_harness_on_every_feature_including_the_creator_record():
    rows = _xrows()
    pool_v = {XQP: V0, XPP: V0}
    ref, old = _ref506(rows, _la(rows, pool_v))
    fi = {n: j for j, n in enumerate(FEATURE_NAMES)}
    ck = fi["cr_known_out"]
    moved = [k for k in ref if k[0] == XPP and ref[k][ck] == 1.0 and old[k][ck] == 0.0]
    assert len(moved) >= 3                                       # the fixture separates "creator record at SD" from #506's point
    for k in moved:
        for n in ("cr_lmax6", "cr_lend6", "cr_big6"):
            assert np.isnan(old[k][fi[n]]) and not np.isnan(ref[k][fi[n]]), n

    la = _la(rows, pool_v)
    eng = px.StateModeEngine(_Ledger(), v_source=cs.V_SOURCE_REPLAY, state_mode="exact", lookahead=la)
    hs = {cs.hour_of(XBT + 3600 * i): 1.0 for i in range(8)}
    seen, sink, sh = _run(eng, rows, force_stage1=True, hour_sps=hs)
    got = {(p, T): np.frombuffer(v, dtype=np.float64) for p, T, _, _, _, v in seen}
    assert set(got) == set(ref) and len(got) > 30                # the same decision rows (pool, T)
    bad = {n: sum(not np.array_equal(got[k][j], ref[k][j], equal_nan=True) for k in ref) for n, j in fi.items()}
    assert sum(bad.values()) == 0, {n: c for n, c in bad.items() if c}
    for k in moved:                                              # bit-equal, as float32 too (P3's matched-row bar)
        assert np.array_equal(got[k].astype(np.float32), ref[k].astype(np.float32), equal_nan=True)
    rep = sh.exact_report()
    assert rep["ok"] and rep["deferred"] >= len(moved) and rep["resolved"] == rep["deferred"] and rep["row_state_mismatch"] == 0
    picks = sink.of("c1nf_pick")
    deferred_picks = [p for p in picks if X_GP + 600 <= p["decision_T_ms"] // 1000 - XBT < X_PNEXT]
    assert deferred_picks and all(0 <= p["t_ms"] - p["decision_T_ms"] < 60_000 for p in picks)   # stamped at the decision row, not the print
    assert sink.of("c1nf_outcome") == []                                   # exact mode prices no outcome


def test_exact_mode_defers_only_pools_with_a_next_print_and_keeps_the_book_order():
    rows = _xrows()
    la = _la(rows, {XQP: V0, XPP: V0})
    eng = px.StateModeEngine(_Ledger(), v_source=cs.V_SOURCE_REPLAY, state_mode="exact", lookahead=la)
    seen, sink, sh = _run(eng, rows, force_stage1=True, hour_sps={cs.hour_of(XBT + 3600 * i): 1.0 for i in range(8)})
    rep = sh.exact_report()
    assert rep["decided_at_sd"] > 0 and rep["deferred"] > 0                # Q after its last print and P after X_PNEXT + 120: live at SD
    ts = [p["decision_T_ms"] for p in sink.of("c1nf_pick") if p["mint"] == XP]
    assert ts == sorted(ts) and all(b - a >= 60_000 for a, b in zip(ts, ts[1:]))
    live_eng = px.StateModeEngine(_Ledger(), v_source=cs.V_SOURCE_REPLAY, state_mode="live")
    lseen, _, lsh = _run(live_eng, rows, force_stage1=True, hour_sps={cs.hour_of(XBT + 3600 * i): 1.0 for i in range(8)})
    assert sorted(s[:3] for s in seen) == sorted(s[:3] for s in lseen)        # the same (pool, T, SD) rows, decided at another point
    for k in ("minutes", "decision_rows", "stage1", "not_decidable"):           # every minute and row is counted once, as in live
        assert sh.c[k] == lsh.c[k], k


def test_run_replay_mode_builds_exact_shadow_for_one_call_and_restores_shadow(monkeypatch):
    built = []

    def fake_run_replay(args, models, engine, sink):
        built.append(cs.Shadow(engine, models, sink, replay=True, seal_start_ms=None, v_source=cs.V_SOURCE_REPLAY))
        return cs.EXIT_OK

    monkeypatch.setattr(cs, "run_replay", fake_run_replay)
    orig = cs.Shadow
    eng = px.StateModeEngine(_Ledger(), v_source=cs.V_SOURCE_REPLAY, state_mode="exact", lookahead=_la([]))
    rc, sh = px.run_replay_mode(None, _models(), eng, cs.MemorySink())
    assert rc == cs.EXIT_OK and isinstance(sh, px.ExactShadow) and built == [sh] and cs.Shadow is orig
    live = px.StateModeEngine(_Ledger(), v_source=cs.V_SOURCE_REPLAY, state_mode="live")
    rc, sh = px.run_replay_mode(None, _models(), live, cs.MemorySink())
    assert sh is None and type(built[-1]) is cs.Shadow and cs.Shadow is orig
    with pytest.raises(ValueError):
        px.ExactShadow(live, _models(), cs.MemorySink(), replay=True, seal_start_ms=None, v_source=cs.V_SOURCE_REPLAY)


def test_cross_compare_counts_differing_rows_by_feature_on_shared_stage1_rows(tmp_path):
    n = len(FEATURE_NAMES)
    assert sum(not px.wallet_feature(x) for x in FEATURE_NAMES) == 65             # QP: the 65 non-wallet columns
    base = np.arange(3 * n, dtype=np.float64).reshape(3, n) / 7.0
    base[0, 0] = np.nan
    r506 = {"exact_pool": np.array(["A", "B", "C"]), "exact_T": np.array([60, 60, 120]), "exact_stage1": np.array([True, True, False]),
            "exact_vec": base}
    v = base.astype(np.float32)
    s594 = {"pool": np.array(["A", "B", "C"]), "T": np.array([60, 60, 120]), "vec": v.copy()}
    out = px.cross_compare(r506, s594)
    assert out == {"shared_rows": 2, "only_506": 0, "only_594": 1, "columns": n, "rows_differing": 0, "by_feature": {}}
    w = next(j for j, x in enumerate(FEATURE_NAMES) if px.wallet_feature(x))
    c = FEATURE_NAMES.index("cr_known_out")
    s594["vec"][1, c] += 1
    s594["vec"][0, w] = np.nextafter(s594["vec"][0, w], np.float32(np.inf))      # 1 float32 ulp is a difference
    out = px.cross_compare(r506, s594)
    assert out["rows_differing"] == 2 and out["by_feature"] == {"cr_known_out": 1, FEATURE_NAMES[w]: 1}
    out = px.cross_compare(r506, s594, skip_wallet=True)
    assert out["columns"] == 65 and out["rows_differing"] == 1 and out["by_feature"] == {"cr_known_out": 1}
    np.savez(tmp_path / "a.npz", **r506)
    np.savez(tmp_path / "b.npz", **s594)
    assert px.main(["cross-compare", str(tmp_path / "a.npz"), str(tmp_path / "b.npz"), "--json", str(tmp_path / "o.json")]) == px.EXIT_EXACT_CHECK
    s594["vec"] = v
    np.savez(tmp_path / "c.npz", **s594)
    assert px.main(["cross-compare", str(tmp_path / "a.npz"), str(tmp_path / "c.npz")]) == cs.EXIT_OK
