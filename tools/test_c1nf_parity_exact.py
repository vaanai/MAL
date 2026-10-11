"""Offline tests for tools/c1nf_parity_exact.py (DEC-026 items 13 and 14a). Synthetic rows only: no tape, no network, no model file."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

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


def _cc_fixture():
    n = len(FEATURE_NAMES)
    base = np.arange(4 * n, dtype=np.float64).reshape(4, n) / 7.0
    base[0, 0] = np.nan
    r506 = {"exact_pool": np.array(["A", "B", "C", "D"]), "exact_T": np.array([60, 60, 120, 60]),
            "exact_stage1": np.array([True, True, False, True]), "exact_vec": base}
    # #594 (a continuous run) also decides E, a pool of another graduation day: outside #506's key set, it does not gate
    vec = np.vstack([base[[0, 1, 3]], np.full((1, n), 5.0)]).astype(np.float32)
    s594 = {"pool": np.array(["A", "B", "D", "E"]), "T": np.array([60, 60, 60, 60]), "vec": vec}
    return r506, s594


def test_cross_compare_counts_differing_rows_by_feature_on_shared_stage1_rows(tmp_path):
    n = len(FEATURE_NAMES)
    assert sum(not px.wallet_feature(x) for x in FEATURE_NAMES) == 65             # QP: the 65 non-wallet columns
    r506, s594 = _cc_fixture()
    v = s594["vec"].copy()
    out = px.cross_compare(r506, s594)
    assert out == {"shared_rows": 3, "stage1_506": 3, "stage1_594": 4, "decision_keys_506": 4, "only_506": 0, "only_594": 0,
                   "only_594_outside_506_keys": 1, "only_594_outside_on_506_pools": 0, "duplicate_keys_506": 0, "duplicate_keys_594": 0,
                   "columns": n, "rows_differing": 0, "by_feature": {}, "pass": True}
    w = next(j for j, x in enumerate(FEATURE_NAMES) if px.wallet_feature(x))
    c = FEATURE_NAMES.index("cr_known_out")
    s594["vec"][1, c] += 1
    s594["vec"][0, w] = np.nextafter(s594["vec"][0, w], np.float32(np.inf))      # 1 float32 ulp is a difference
    out = px.cross_compare(r506, s594)
    assert out["rows_differing"] == 2 and out["by_feature"] == {"cr_known_out": 1, FEATURE_NAMES[w]: 1} and not out["pass"]
    out = px.cross_compare(r506, s594, skip_wallet=True)
    assert out["columns"] == 65 and out["rows_differing"] == 1 and out["by_feature"] == {"cr_known_out": 1} and not out["pass"]
    np.savez(tmp_path / "a.npz", **r506)
    np.savez(tmp_path / "b.npz", **s594)
    assert px.main(["cross-compare", str(tmp_path / "a.npz"), str(tmp_path / "b.npz"), "--json", str(tmp_path / "o.json")]) == px.EXIT_EXACT_CHECK
    assert json.loads((tmp_path / "o.json").read_text())["by_feature"] == {"cr_known_out": 1, FEATURE_NAMES[w]: 1}
    s594["vec"] = v
    np.savez(tmp_path / "c.npz", **s594)
    assert px.main(["cross-compare", str(tmp_path / "a.npz"), str(tmp_path / "c.npz")]) == cs.EXIT_OK


def test_cross_compare_fails_on_stage1_membership_differences_with_zero_differing_rows(tmp_path):
    """QP on 626973f: a state difference that moves a row out of stage 1 on one side drops it from the shared rows; rows_differing stays 0
    and the check must still fail. Both membership counts gate the exit; #594 rows outside #506's decision keys do not."""
    r506, s594 = _cc_fixture()
    np.savez(tmp_path / "r.npz", **r506)
    # (ii) a #506 stage-1 row (D, 60) that #594 did not keep as stage 1
    drop = {"pool": s594["pool"][[0, 1, 3]], "T": s594["T"][[0, 1, 3]], "vec": s594["vec"][[0, 1, 3]]}
    out = px.cross_compare(r506, drop)
    assert out["rows_differing"] == 0 and out["shared_rows"] == 2 and out["only_506"] == 1 and out["only_594"] == 0 and not out["pass"]
    np.savez(tmp_path / "d.npz", **drop)
    assert px.main(["cross-compare", str(tmp_path / "r.npz"), str(tmp_path / "d.npz"), "--skip-wallet"]) == px.EXIT_EXACT_CHECK
    # (iii) a #594 stage-1 row (C, 120) that #506 decided but did not mark stage 1
    extra = {"pool": np.append(s594["pool"], "C"), "T": np.append(s594["T"], 120),
             "vec": np.vstack([s594["vec"], r506["exact_vec"][2:3].astype(np.float32)])}
    out = px.cross_compare(r506, extra)
    assert out["rows_differing"] == 0 and out["only_506"] == 0 and out["only_594"] == 1 and out["only_594_outside_506_keys"] == 1
    assert not out["pass"]
    np.savez(tmp_path / "e.npz", **extra)
    assert px.main(["cross-compare", str(tmp_path / "r.npz"), str(tmp_path / "e.npz")]) == px.EXIT_EXACT_CHECK
    # a #594 row on one of #506's pools at a minute #506 never decides: outside the key set, reported, not gating
    late = {"pool": np.append(s594["pool"], "A"), "T": np.append(s594["T"], 999960), "vec": np.vstack([s594["vec"], s594["vec"][:1]])}
    out = px.cross_compare(r506, late)
    assert out["only_594"] == 0 and out["only_594_outside_506_keys"] == 2 and out["only_594_outside_on_506_pools"] == 1 and out["pass"]
    # a duplicate (pool, T) key would hide a row: fail closed
    dup = {"pool": np.append(s594["pool"], "A"), "T": np.append(s594["T"], 60), "vec": np.vstack([s594["vec"], s594["vec"][:1]])}
    out = px.cross_compare(r506, dup)
    assert out["duplicate_keys_594"] == 1 and not out["pass"]
    # no shared row is never a pass
    assert not px.cross_compare(r506, {"pool": np.array(["E"]), "T": np.array([60]), "vec": s594["vec"][3:]})["pass"]


# ---- creator history from tape start (QP round 2 on 39a06b4, blocking item 1) ------------------------------------------------------------
def test_history_hours_skip_the_tape_gaps_and_stop_before_the_segment_start():
    s3 = px.history_hours("2026-09-18T23")
    assert len(s3) == 336 + 288 and s3[0] == px.TAPE_START == "2026-08-14T12" and s3[-1] == "2026-09-15T11"
    assert s3 == sorted(set(s3))
    assert not [h for h in s3 if "2026-08-28T12" <= h < "2026-09-03T12" or "2026-09-15T12" <= h < "2026-09-18T23"]   # gap hours skipped
    assert "2026-08-28T11" in s3 and "2026-09-03T12" in s3
    assert px.hour_runs(s3) == [["2026-08-14T12", "2026-08-28T11"], ["2026-09-03T12", "2026-09-15T11"]]
    assert px.history_hours("2026-09-03T12") == s3[:336]
    assert px.history_hours("2026-08-14T12") == [] and px.history_hours("2026-09-18T23", px.HISTORY_NONE) == []
    assert px.history_hours("2026-09-20T00") == s3 + cs.replay_hours("2026-09-18T23", 25)        # mid-segment: S3's own earlier hours too
    cs.refuse_replay([], px.feed_plan("2026-09-18T23", 152).hours)                               # every fed hour is an exploration hour
    with pytest.raises(ValueError):
        px.history_hours("2026-09-18T23", "segment")
    p = px.feed_plan("2026-09-18T23", 75)
    assert p.hours == s3 + cs.replay_hours("2026-09-18T23", 75) and len(p.hours) == 699 and p.decide_from == "2026-09-18T23"
    assert len(px.feed_plan("2026-09-18T23", 152).hours) == 776 and len(px.feed_plan("2026-09-03T12", 288).hours) == 624
    assert px.feed_plan("2026-09-18T23", 75, decide_from="2026-09-20T00").decide_from == "2026-09-20T00"   # a later decide_from holds
    assert px.feed_plan("2026-09-18T23", 75, decide_from="2026-09-04T00").decide_from == "2026-09-18T23"   # never inside the history
    assert px.feed_plan("2026-09-18T23", 75, px.HISTORY_NONE).decide_from is None                          # the cold start, unchanged
    assert px.feed_plan("2026-08-14T12", 10).decide_from is None and px.feed_plan("2026-08-14T12", 10).history_hours == []


def _ep(h, sec=0):
    return int(px._hour_dt(h).timestamp()) + sec


HSLOT0 = 9_000_000                                # 1 slot per second from tape start
HC, HQ, HR, HS, HP, HQP, HPP, HMX = "CREATORh", "MINThqqq", "MINThrrr", "MINThsss", "MINThppp", "POOLhqqq", "POOLhppp", "MINThxxx"
H_S1, H_S2, H_S3 = _ep("2026-08-20T00"), _ep("2026-09-10T00"), _ep("2026-09-19T00")   # S1 and S2 are history; S3 decides
H_SEG = _ep("2026-09-18T23")


def _hrow(bt, k, **kw):
    r = {"_k": k, "slot": HSLOT0 + bt - _ep(px.TAPE_START), "block_time": bt, "tx_index": kw.pop("tx", 1), "event_index": 0}
    r.update(kw)
    return r


def _hps(bt, pool, mint, trader, buy, q, b):
    return cs.stamp_replay_v(_hrow(bt, "trades", venue="pumpswap", pool=pool, mint=mint, trader=trader, side="buy" if buy else "sell",
                                   sol_lamports=2e8, token_raw=1e12, quote_reserve=q, base_reserve=b), V0, event_v=False)


def _hclock(t0, t1):
    return [_hrow(t, "trades", tx=0, venue="pump_bonding", mint=HMX, trader="bx", side="buy", sol_lamports=1e6, token_raw=1e9,
                  quote_reserve=30e9, base_reserve=1e15, pool=None) for t in range(t0, t1, 30)]


def _hpool(g, pool, mint, n_late=0):
    """A graduated pool's prints from g: three opening prints, twelve from g + 700, and n_late more at g + 6 h + 60 (its 6 h outcome)."""
    rows, q, b = [], 85e9, 2e14
    pts = [(g + 7, "p0", True), (g + 60, "p1", True), (g + 120, "p2", False)] + [(g + 700 + 15 * k, f"n{k}", True) for k in range(12)]
    pts += [(g + 21660 + 60 * k, f"l{k}", k % 2 == 0) for k in range(n_late)]
    for t, tr, buy in pts:
        rows.append(_hps(t, pool, mint, tr, buy, q, b))
        q, b = (q + 2e8, b - 1e12) if buy else (q - 2e8, b + 1e12)
    return rows


def _htape():
    """Creator HC: Q (created, graduated, priced past g + 6 h) and R (created only) in S1, S (created only) in S2, and P in S3, the decision
    segment. A whole-minute clock runs while Q is alive in S1 (so a run that decided in the history would decide Q) and while P is alive."""
    gq, gp = H_S1 + 10, H_S3 + 100
    rows = [_hrow(H_S1, "creates", mint=HQ, creator=HC, name="dog moon", symbol="DOG", is_mayhem_mode=False),
            _hrow(H_S1 + 5, "creates", mint=HR, creator=HC, name="cat moon", symbol="CAT", is_mayhem_mode=False),
            _hrow(gq, "migrations", type="complete", mint=HQ),
            _hrow(H_S2, "creates", mint=HS, creator=HC, name="dog star", symbol="DOGST", is_mayhem_mode=False),
            _hrow(H_S3 + 5, "creates", mint=HP, creator=HC, name="dog sun", symbol="DOGS", is_mayhem_mode=False),
            _hrow(gp, "migrations", type="complete", mint=HP)]
    rows += _hpool(gq, HQP, HQ, n_late=3) + _hpool(gp, HPP, HP) + _hclock(H_S1 + 20, H_S1 + 3000) + _hclock(H_S3 + 20, H_S3 + 3000)
    rows.sort(key=cs.row_key)
    creates = [(HQ, H_S1), (HR, H_S1 + 5), (HS, H_S2), (HP, H_S3 + 5)]
    grads = [(HQ, gq), (HP, gp)]
    return rows, creates, grads


class _Frame:
    """The three tokens.parquet columns cs.run_replay reads, for a test venv without pandas."""

    def __init__(self, cols):
        self.cols = cols

    def __getattr__(self, k):
        return self.__dict__["cols"][k]

    def __getitem__(self, mask):
        return _Frame({k: v[np.asarray(mask)] for k, v in self.cols.items()})


def _hrun(tmp_path, monkeypatch, mode, plan, rows):
    """cs.run_replay through px.run_replay_mode on an in-memory tape: tape_rows yields each asked hour's rows; returns what was asked."""
    import sys
    import types

    by_hour = defaultdict(list)
    for r in rows:
        by_hour[cs.hour_of(r["block_time"])].append(r)
    asked = {"rows": [], "sps": []}

    def tape_rows(tape_dir, hours, pool_v, pool_of_mint, *, event_v=False):
        asked["rows"] = list(hours)
        for h in hours:
            yield [dict(r) for r in by_hour.get(h, [])]

    def hour_sps(tape_dir, hours):
        asked["sps"] = list(hours)
        return {h: 1.0 for h in hours}

    fake_pd = types.ModuleType("pandas")
    fake_pd.read_parquet = lambda path, columns=None: _Frame({"mint": np.array([HQ, HP], dtype=object), "pool": np.array([HQP, HPP], dtype=object),
                                                              "v0_lamports": np.array([V0, V0])})
    monkeypatch.setitem(sys.modules, "pandas", fake_pd)
    monkeypatch.setattr(cs, "tape_rows", tape_rows)
    monkeypatch.setattr(cs, "hour_sps_from_tape", hour_sps)
    made, real = [], cs.Shadow

    def rec(*a, **k):
        s = real(*a, **k)
        made.append(s)
        return s

    monkeypatch.setattr(cs, "Shadow", rec)
    pool_v = {HQP: V0, HPP: V0}
    la = _la([r for h in plan.decide_hours for r in by_hour.get(h, [])], pool_v) if mode == px.STATE_EXACT else None
    eng = px.StateModeEngine(_Ledger(), v_source=cs.V_SOURCE_REPLAY, state_mode=mode, lookahead=la)
    seen = _capture(eng, force_stage1=True)
    out = tmp_path / f"{mode}-{plan.history}-{plan.decide_from}"
    out.mkdir()
    args = px.build_parser().parse_args(["--state-mode", mode, "--replay-from", plan.decide_hours[0], "--replay-hours", str(len(plan.decide_hours)),
                                         "--model", "m", "--model-sha256", "x", "--out-dir", str(out)])
    sink = cs.MemorySink()
    rc, xsh = px.run_replay_mode(args, _models(), eng, sink, plan)
    sh = xsh if xsh is not None else made[0]
    assert rc == cs.EXIT_OK and cs.replay_hours is not None and cs.replay_hours("2026-09-04T00", 2) == ["2026-09-04T00", "2026-09-04T01"]
    return seen, sink, sh, asked, eng


CR_COLS = ("cr_prev_creates", "cr_prev_grads", "cr_known_out", "cr_lmax6", "cr_lend6", "cr_big6")


@pytest.mark.parametrize("mode", [px.STATE_LIVE, px.STATE_EXACT])
def test_history_feed_gives_the_tape_start_creator_history_and_makes_no_decision_before_the_segment(tmp_path, monkeypatch, mode):
    rows, creates, grads = _htape()
    plan = px.feed_plan("2026-09-18T23", 3)                                  # history: S1 + S2 (624 h); decisions 09-18T23 .. 09-19T01
    seen, sink, sh, asked, eng = _hrun(tmp_path, monkeypatch, mode, plan, rows)
    assert asked["rows"] == asked["sps"] == plan.hours and len(plan.hours) == 627          # gap hours never asked
    fi = {n: j for j, n in enumerate(FEATURE_NAMES)}
    vec = lambda s: np.frombuffer(s[5], dtype=np.float64)                                  # noqa: E731
    p_rows = [s for s in seen if s[0] == HPP]
    assert len(p_rows) > 30 and {s[0] for s in seen} == {HPP}                              # Q, alive in S1, is never decided
    # the creator record is computed on real stage-1 rows (the recorder's rows; _capture forces the flag on every row)
    s1 = [(int(r[2]), r[5].astype(np.float64)) for r in eng.stage1_rows]
    assert len(s1) >= 3 and {r[0] for r in eng.stage1_rows} == {HPP}
    # 12_passC.py:59-62 on every create of the tape: prior creates of the creator before P's create; graduations before T, own excluded
    cbt_p = dict(creates)[HP]
    for T, v in s1:
        assert v[fi["cr_prev_creates"]] == sum(1 for m, c in creates if c < cbt_p) == 3
        assert v[fi["cr_prev_grads"]] == sum(1 for m, g in grads if g < T) - (1 if dict(grads)[HP] < T else 0) == 1
        assert v[fi["cr_known_out"]] == 1.0 and all(np.isfinite(v[fi[c]]) for c in ("cr_lmax6", "cr_lend6", "cr_big6"))
    # no decision, stage-1 row, deferral, pick or book entry during the history feed
    assert min(s[1] for s in seen) >= H_SEG and min(r[2] for r in eng.stage1_rows) >= H_SEG
    picks = sink.of("c1nf_pick")
    assert picks and all(p["decision_T_ms"] >= H_SEG * 1000 and p["mint"] == HP for p in picks)
    assert set(sh.book) == {HP} and min(sh.book.values()) >= H_SEG
    if mode == px.STATE_EXACT:
        rep = sh.exact_report()
        assert rep["ok"] and rep["deferred"] > 0 and rep["resolved"] == rep["deferred"], rep
    # the same rows fed with decisions on everywhere (a plain tape-start run): Q is decided and picked in S1, and P's vectors are equal
    every = px.FeedPlan(px.HISTORY_TAPE_START, plan.history_hours, plan.decide_hours, None)
    a_seen, a_sink, a_sh, _, _ = _hrun(tmp_path, monkeypatch, mode, every, rows)
    assert any(s[0] == HQP and s[1] < H_SEG for s in a_seen) and any(p["mint"] == HQ for p in a_sink.of("c1nf_pick")) and HQ in a_sh.book
    a_p = {(s[0], s[1]): s for s in a_seen if s[0] == HPP}
    assert {(s[0], s[1]) for s in p_rows} <= set(a_p)
    for s in p_rows:
        assert np.array_equal(vec(s), vec(a_p[(s[0], s[1])]), equal_nan=True)
    # the cold start (--history none, the run before this change): P's creator record is empty, and only the creator columns move
    cold = px.feed_plan("2026-09-18T23", 3, px.HISTORY_NONE)
    c_seen, _, _, c_asked, c_eng = _hrun(tmp_path, monkeypatch, mode, cold, rows)
    assert c_asked["rows"] == cs.replay_hours("2026-09-18T23", 3)
    c_s1 = {int(r[2]): r[5].astype(np.float64) for r in c_eng.stage1_rows if r[0] == HPP}
    assert sorted(c_s1) == [T for T, _ in s1]                               # the creator record is not a stage-1 input: the same rows
    moved = {n for n, j in fi.items() for T, v in s1 if not np.array_equal(v[j], c_s1[T][j], equal_nan=True)}
    assert {"cr_prev_creates", "cr_prev_grads", "cr_known_out"} <= moved <= set(CR_COLS), moved
    assert all(c_s1[T][fi["cr_prev_creates"]] == 0 and c_s1[T][fi["cr_known_out"]] == 0 for T, _ in s1)


def test_main_feeds_the_history_by_default_and_records_it(tmp_path, monkeypatch):
    got = {}

    def fake(args, models, engine, sink, plan=None):
        got["plan"], got["decide_from"] = plan, args.decide_from
        return cs.EXIT_OK, None

    monkeypatch.setattr(px, "run_replay_mode", fake)
    monkeypatch.setattr(cs.ModelSet, "single", classmethod(lambda cls, path, sha, loader=None: _models()))
    tok = tmp_path / "tokens.parquet"
    tok.write_bytes(b"x")
    out = tmp_path / "o"
    base = ["--state-mode", "live", "--replay-from", "2026-09-18T23", "--replay-hours", "75", "--decide-to", "2026-09-22T00",
            "--model", "m", "--model-sha256", "x", "--tokens", str(tok)]
    assert px.main(base + ["--out-dir", str(out)]) == cs.EXIT_OK
    plan = got["plan"]
    assert plan.decide_from == "2026-09-18T23" and len(plan.history_hours) == 624 and len(plan.hours) == 699 and got["decide_from"] is None
    summ = json.loads((out / "parity-summary-live.json").read_text())
    assert summ["history"]["convention"] == "tape-start" and summ["history"]["hours"] == 624 and summ["hours_fed"] == 699
    assert summ["decide_from_effective"] == "2026-09-18T23" and summ["decide_from"] is None and summ["hours"] == ["2026-09-18T23", "2026-09-22T01"]
    assert px.main(base + ["--history", "none", "--out-dir", str(tmp_path / "n")]) == cs.EXIT_OK
    assert got["plan"].history_hours == [] and got["plan"].decide_from is None


def test_shadow_and_feature_engine_blobs_are_unchanged():
    """The harness changes neither the soak's pinned shadow blob (MiScusi #658) nor the feature engine (14(b) at these blobs)."""
    here = Path(px.__file__).resolve().parent
    assert px.git_blob(here / "c1nf_shadow.py") == "c2dafcdcdc69f7f0269746431c01755dc7f45e4b"
    assert px.git_blob(here / "c1nf_features.py") == "d6c87354a520ea25efc59975d7d041570d8226f7"


# ---- tape NULL rows (2026-10-11) -----------------------------------------------------------------------------------------------------
def _write_hour(tmp_path, sub, h, rows):
    import pandas as pd

    d = tmp_path / "tape" / sub
    d.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    for c in ("slot", "block_time", "tx_index", "event_index", "base_reserve"):
        if c in df:
            df[c] = df[c].astype("Int64")      # nullable int64, as the tape writer stores it
    df.to_parquet(d / f"{h}.parquet")
    return d / f"{h}.parquet"


def test_tape_null_filter_drops_only_null_rows_and_keeps_exact_ints(tmp_path):
    import pandas as pd

    big = 999_999_999_999_999                       # above 2**53 would lose precision as float; this one must stay an exact int
    rows = [dict(slot=455_000_001, block_time=1_757_660_000, tx_index=3, event_index=0, mint="M1", base_reserve=big),
            dict(slot=None, block_time=None, tx_index=None, event_index=None, mint=None, base_reserve=None),
            dict(slot=455_000_002, block_time=1_757_660_001, tx_index=0, event_index=1, mint="M2", base_reserve=7)]
    f = _write_hour(tmp_path, "creates", "2026-09-12T07", rows)
    raw = pd.read_parquet(f)
    assert raw["slot"].dtype.kind == "f" or str(raw["slot"].dtype) == "Int64"   # unfiltered: float (or nullable) because of the NULL
    px.TAPE_NULL_ROWS.clear()
    with px.tape_null_filter():
        got = pd.read_parquet(f).to_dict("records")
        sub = pd.read_parquet(f, columns=["slot", "block_time", "mint"])
    assert pd.read_parquet is not None and pd.read_parquet(f) is not None   # restored after the block
    assert [r["mint"] for r in got] == ["M1", "M2"]
    assert all(type(r["slot"]) is int and type(r["block_time"]) is int for r in got)
    assert got[0]["base_reserve"] == big and type(got[0]["base_reserve"]) is int
    assert list(sub.columns) == ["slot", "block_time", "mint"] and len(sub) == 2
    assert sum(px.TAPE_NULL_ROWS.values()) == 2 and str(f) in px.TAPE_NULL_ROWS


def test_tape_null_filter_leaves_a_clean_file_byte_for_byte_the_same_path(tmp_path):
    import pandas as pd

    rows = [dict(slot=455_000_001 + i, block_time=1_757_660_000 + i, tx_index=i, event_index=0, mint=f"M{i}") for i in range(5)]
    f = _write_hour(tmp_path, "trades", "2026-09-12T06", rows)
    a = pd.read_parquet(f)
    px.TAPE_NULL_ROWS.clear()
    with px.tape_null_filter():
        b = pd.read_parquet(f)
    pd.testing.assert_frame_equal(a, b)
    assert not px.TAPE_NULL_ROWS


def test_shadow_tape_rows_survive_a_null_create_under_the_filter(tmp_path):
    rows_c = [dict(slot=455_000_001, block_time=1_757_660_000, tx_index=0, event_index=0, mint="MC", creator="CR", name="n", symbol="s",
                   is_mayhem_mode=False),
              dict(slot=None, block_time=None, tx_index=None, event_index=None, mint=None, creator=None, name=None, symbol=None,
                   is_mayhem_mode=None)]
    _write_hour(tmp_path, "creates", "2026-09-12T07", rows_c)
    _write_hour(tmp_path, "migrations", "2026-09-12T07", [dict(type="complete", mint="MX", slot=455_000_003, block_time=1_757_660_002,
                                                               tx_index=0, event_index=0)])
    _write_hour(tmp_path, "trades", "2026-09-12T07", [dict(venue="pump_bonding", mint="MC", pool=None, slot=455_000_002, block_time=1_757_660_001,
                                                           tx_index=0, event_index=0)])
    with pytest.raises(TypeError):
        list(cs.tape_rows(str(tmp_path / "tape"), ["2026-09-12T07"], {}, {}))      # the bug: sorting (None, ...) against ints
    with px.tape_null_filter():
        out = list(cs.tape_rows(str(tmp_path / "tape"), ["2026-09-12T07"], {}, {}))
    rows = out[0]
    assert [(r["_k"], r["slot"]) for r in rows] == [("creates", 455_000_001), ("trades", 455_000_002), ("migrations", 455_000_003)]
    assert all(type(r["slot"]) is int for r in rows)
