"""Offline tests for tools/c1nf_parity_exact.py (DEC-026 items 13 and 14a). Synthetic rows only: no tape, no network, no model file."""

from __future__ import annotations

import hashlib
import math

import numpy as np
import pytest

from tools import c1nf_parity_exact as px
from tools import c1nf_shadow as cs
from tools.c1nf_features import FeatureEngine
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


def _run(eng, rows, force_stage1=False):
    seen = _capture(eng, force_stage1)
    sink = cs.MemorySink()
    sh = cs.Shadow(eng, _models(), sink, replay=True, seal_start_ms=None, v_source=cs.V_SOURCE_REPLAY)
    sh.clock.hour_sps = {cs.hour_of(BT0): SPS, cs.hour_of(BT0 + 3600): SPS}
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
    ex_seen, ex_sink, _ = _run(eng, rows, force_stage1=True)
    assert [s[:3] for s in ex_seen] == [s[:3] for s in live_seen]              # same decisions (pool, T, SD)
    assert eng.exact_injected == len(ex_seen) and la.c["hit"] == len(ex_seen)
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
