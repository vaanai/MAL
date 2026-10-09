"""Offline tests for tools/c1nf_shadow.py. The feature engine and the model are stubs (they belong to parallel builders); no network, no keys."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from tools import c1nf_shadow as cs

FIX = Path(__file__).parent / "fixtures" / "c1nf_shadow"
V0 = 17.58e9
POOL, MINT = "POOLaaaa", "MINTaaaa"
S0, BT0, SPS = 1_000_000, 1_788_523_200, 0.4         # BT0 is a whole minute (2026-09-04T12:00:00Z)
Q0, RAMP, B0 = 120e9, 1_000_000.0, 1e15              # quote ramps 1e6 lamports per slot, base is flat
SEAL_MS = cs.SEAL_START_MS


def bt_of(slot: int) -> int:
    return BT0 + int((slot - S0) * SPS)


def slot_of_sec(t: float) -> int:
    return S0 + int(round(t / SPS))


def mk_row(slot: int, *, side="buy", pool=POOL, mint=MINT, v=V0, venue="pumpswap", **kw) -> dict:
    r = {"venue": venue, "mint": mint, "trader": f"w{slot % 7}", "side": side, "sol_lamports": 1_000_000, "token_raw": 1_000_000_000,
         "quote_reserve": Q0 + RAMP * (slot - S0), "base_reserve": B0, "pool": pool, "slot": slot, "block_time": bt_of(slot), "tx_index": 1,
         "event_index": 0, "virtual_quote_reserve": v, "t_recv_ms": 1}
    r.update(kw)
    return r


class StubModel:
    def __init__(self, pred=0.05):
        self.pred, self.calls = pred, 0

    def predict(self, X):
        self.calls += 1
        assert X.dtype == np.float32 and X.shape[1] == cs.N_FEATURES
        return np.full(len(X), self.pred) if np.isscalar(self.pred) else np.asarray(self.pred)[: len(X)]


class StubEngine:
    """Implements the c1nf_features surface the shadow calls. decide(T) -> (stage1, h_top1) or None."""

    def __init__(self, decide=None):
        self.decide = decide or (lambda T: (True, 0.1))
        self.trades, self.blocks, self.graduations, self.creates, self.v_set, self.asked = [], [], [], [], [], []

    def on_block(self, slot, bt): self.blocks.append((slot, bt))
    def on_create(self, *a): self.creates.append(a)
    def on_graduation(self, mint, bt): self.graduations.append((mint, bt))
    def set_pool_v(self, pool, v): self.v_set.append((pool, v))
    def on_trade(self, *a): self.trades.append(a)
    def expire(self, now_bt): return 0
    def alive_pools(self, T): return [POOL] if self.decide(T) is not None else []

    def features_at(self, pool, T, sd=None):
        self.asked.append((pool, T, sd))
        d = self.decide(T)
        if d is None:
            return None
        vec = np.full(cs.N_FEATURES, 0.5)
        vec[3] = T % 1000
        return vec, d[0], d[1]


def only_minute(*mins):
    want = {BT0 + 60 * m for m in mins}
    return lambda T: (True, 0.1) if T in want else None


def mk_shadow(decide=None, pred=0.05, **kw):
    sink = cs.MemorySink()
    models = cs.ModelSet([{"from_day": "0000-00-00", "file": str(__file__), "sha256": sha_of(__file__)}], loader=lambda p: StubModel(pred))
    sh = cs.Shadow(StubEngine(decide), models, sink, replay=True, seal_start_ms=kw.pop("seal_start_ms", None), **kw)
    sh.clock.hour_sps[cs.hour_of(BT0)] = SPS
    sh.clock.hour_sps[cs.hour_of(BT0 + 3600)] = SPS
    return sh, sink


def sha_of(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_stream(sh: cs.Shadow, seconds: float, first_sec: float = 0.0, **kw) -> None:
    for slot in range(slot_of_sec(first_sec), slot_of_sec(seconds)):
        sh.feed(mk_row(slot, side="buy" if slot % 2 else "sell", **kw), "trades")


# ---- schema ---------------------------------------------------------------------------------------------------------------------------------
def test_pick_example_matches_fixture_and_validates():
    assert json.loads((FIX / "pick_example.json").read_text()) == cs.PICK_EXAMPLE
    assert cs.validate_pick(cs.PICK_EXAMPLE) == []
    assert tuple(cs.PICK_EXAMPLE) == cs.PICK_FIELDS


@pytest.mark.parametrize("field,val", [("pred", float("nan")), ("SD_slot", 1.5), ("stage1", 1), ("feature_hash", "zz"), ("decision_T_ms", True), ("type", "x")])
def test_validate_pick_rejects(field, val):
    assert cs.validate_pick({**cs.PICK_EXAMPLE, field: val})


def test_validate_pick_missing_field():
    rec = dict(cs.PICK_EXAMPLE)
    del rec["model_sha"]
    assert cs.validate_pick(rec) == ["missing model_sha"]


# ---- model pin ------------------------------------------------------------------------------------------------------------------------------
def test_model_pin_match_and_mismatch(tmp_path):
    f = tmp_path / "m.txt"
    f.write_text("model-bytes")
    good = hashlib.sha256(b"model-bytes").hexdigest()
    ms = cs.ModelSet.single(str(f), good, loader=lambda p: StubModel())
    assert ms.shas == [good]
    with pytest.raises(cs.Refused, match="pinned"):
        cs.ModelSet.single(str(f), "0" * 64, loader=lambda p: StubModel())
    with pytest.raises(cs.Refused, match="64 hex"):
        cs.ModelSet.single(str(f), "abc", loader=lambda p: StubModel())
    with pytest.raises(cs.Refused, match="missing"):
        cs.ModelSet.single(str(tmp_path / "nope"), good, loader=lambda p: StubModel())


def test_model_manifest_selects_by_day_and_checks_every_file(tmp_path):
    for n in ("a", "b"):
        (tmp_path / f"{n}.txt").write_text(n)
    man = {"models": [{"from_day": "2026-10-11", "file": "b.txt", "sha256": hashlib.sha256(b"b").hexdigest()},
                      {"from_day": "2026-10-10", "file": "a.txt", "sha256": hashlib.sha256(b"a").hexdigest()}]}
    (tmp_path / "man.json").write_text(json.dumps(man))
    ms = cs.ModelSet.from_manifest(tmp_path / "man.json", loader=lambda p: p)
    assert ms.for_day("2026-10-10")[1] == hashlib.sha256(b"a").hexdigest()
    assert ms.for_day("2026-10-12")[1] == hashlib.sha256(b"b").hexdigest()
    with pytest.raises(cs.Refused, match="no pinned model"):
        ms.for_day("2026-10-09")
    man["models"][0]["sha256"] = "1" * 64
    (tmp_path / "man.json").write_text(json.dumps(man))
    with pytest.raises(cs.Refused):
        cs.ModelSet.from_manifest(tmp_path / "man.json", loader=lambda p: p)


def test_main_refuses_on_hash_mismatch_and_without_model(tmp_path, capsys):
    f = tmp_path / "m.txt"
    f.write_text("x")
    assert cs.main(["--out-dir", str(tmp_path / "o"), "--model", str(f), "--model-sha256", "0" * 64]) == cs.EXIT_REFUSED
    assert cs.main(["--out-dir", str(tmp_path / "o")]) == cs.EXIT_USAGE
    assert not (tmp_path / "o" / "c1nf-picks-x").exists()


def test_real_lightgbm_model_roundtrip(tmp_path):
    lgb = pytest.importorskip("lightgbm")
    rng = np.random.default_rng(1)
    X = rng.normal(size=(400, cs.N_FEATURES)).astype(np.float32)
    y = X[:, 0] * 0.05
    b = lgb.train({"objective": "regression", "verbose": -1, "num_threads": 1, "seed": 1, "deterministic": True}, lgb.Dataset(X, y), 5)
    p = tmp_path / "m.txt"
    b.save_model(str(p))
    ms = cs.ModelSet.single(str(p), sha_of(p))
    m, sha = ms.for_day("2026-10-10")
    assert np.allclose(m.predict(X[:3]), b.predict(X[:3])) and sha == sha_of(p)


# ---- pricing arithmetic ---------------------------------------------------------------------------------------------------------------------
def test_round_trip_flat_market_loses_about_two_fees():
    q, b = 120e9 + V0, 1e15
    rt = cs.round_trip(q, b, q, b)
    f = cs.tier_fee(q, b)
    assert not rt["guarded"]
    assert -2.5 * f < rt["gross_ret"] < -1.5 * f
    pv = cs.pnl_variants(rt)
    assert pv["pnl_505k"] == pytest.approx(pv["pnl_nofee"] - 1_010_000)
    assert pv["pnl_505k_rent"] == pytest.approx(pv["pnl_505k"] - 2 * cs.RENT_LAMPORTS)


def test_guard_reverts_and_costs_one_send():
    q, b = 120e9 + V0, 1e15
    rt = cs.round_trip(q, b, q, b, spot=q / b / 2)       # the buy prints at 2x the decision spot
    assert rt["guarded"]
    pv = cs.pnl_variants(rt)
    assert pv["pnl_505k"] == -505_000 and pv["pnl_55k"] == -55_000 and pv["pnl_505k_rent"] == -505_000 and pv["pnl_nofee"] == 0.0


def test_print_is_pre_trade_and_uses_own_v():
    p = cs.Print(1, 1, True, 1e6, 1e9, 100e9, 1e15, 17.5e9)
    assert p.pre() == (117.5e9, 1e15)
    q2, b2 = p.post()
    assert b2 == 1e15 - 1e9 and q2 > 117.5e9
    with pytest.raises(ValueError):
        cs.Print(1, 1, True, 1, 1, 1, 1, None).pre()


# ---- decisions ------------------------------------------------------------------------------------------------------------------------------
def test_one_decision_per_minute_with_sd_the_first_slot_at_or_after_T():
    sh, sink = mk_shadow(only_minute(10, 11))
    run_stream(sh, 700)
    asked = sh.engine.asked
    assert [a[1] for a in asked] == [BT0 + 600, BT0 + 660]
    for _, T, sd in asked:
        want = min(s for s in range(S0, slot_of_sec(700)) if bt_of(s) >= T)
        assert sd == want and bt_of(sd - 1) < T                     # the first row of the new minute; earlier slots stay out
    assert [r["SD_slot"] for r in sink.of("c1nf_pick")][:1] == [asked[0][2]]


def test_pick_record_schema_hash_and_model_sha():
    sh, sink = mk_shadow(only_minute(10), pred=0.0431)
    run_stream(sh, 640)
    (pick,) = sink.of("c1nf_pick")
    assert cs.validate_pick(pick) == []
    assert pick["mint"] == MINT and pick["pool"] == POOL and pick["decision_T_ms"] == (BT0 + 600) * 1000
    assert pick["pred"] == pytest.approx(0.0431) and pick["h_top1"] == pytest.approx(0.1) and pick["stage1"] is True
    vec = np.full(cs.N_FEATURES, 0.5)
    vec[3] = (BT0 + 600) % 1000
    assert pick["feature_hash"] == hashlib.sha256(vec.astype("<f4").tobytes()).hexdigest()
    assert pick["model_sha"] == sh.models.shas[0]
    assert pick["schema"] == cs.SCHEMA and "t_ms" in pick


def test_threshold_is_strict_and_stage1_is_required():
    sh, sink = mk_shadow(only_minute(10), pred=0.02)
    run_stream(sh, 640)
    assert sink.of("c1nf_pick") == [] and sh.c["stage2"] == 0
    sh, sink = mk_shadow(only_minute(10), pred=0.0201)
    run_stream(sh, 640)
    assert len(sink.of("c1nf_pick")) == 1
    sh, sink = mk_shadow(lambda T: (False, 0.1) if T == BT0 + 600 else None)
    run_stream(sh, 640)
    assert sink.of("c1nf_pick") == [] and sh.engine.asked and sh.models.entries[0]["model"].calls == 0


@pytest.mark.parametrize("h,kept", [(0.5, True), (0.49, True), (0.5000001, False), (0.9, False), (float("nan"), False), (0.0, True)])
def test_cap_applies_before_the_book(h, kept):
    assert cs.cap_ok(h) is kept
    sh, sink = mk_shadow(lambda T: (True, h) if T == BT0 + 600 else None)
    run_stream(sh, 640)
    assert len(sink.of("c1nf_pick")) == (1 if kept else 0)
    assert sh.c["cap_dropped"] == (0 if kept else 1)
    assert not sh.book or kept                                   # a capped row never blocks the mint


def test_one_position_per_mint_with_reentry_60s_after_exit():
    sh, sink = mk_shadow(lambda T: (True, 0.1) if T >= BT0 + 600 else None)     # the stub engine's grid starts at graduation + 600 s
    run_stream(sh, 1100)                                          # decisions at +600 .. +1080
    picks = sink.of("c1nf_pick")
    Ts = [p["decision_T_ms"] // 1000 - BT0 for p in picks]
    assert Ts[0] == 600
    # exit is about T + 1.3 + 300 + 0.55; the next allowed decision is the first whole minute >= exit + 60
    assert Ts[1] >= 600 + 1.3 + 300 + 0.55 + 60 and Ts[1] == 1020      # 961.85 s rounds up to the next whole minute; 960 is blocked
    assert sh.c["book_blocked"] >= 5


def test_model_error_fails_closed_no_pick():
    class Boom:
        def predict(self, X): raise RuntimeError("boom")

    sh, sink = mk_shadow(only_minute(10))
    sh.models.entries[0]["model"] = Boom()
    run_stream(sh, 640)
    assert sink.of("c1nf_pick") == [] and sh.c["model_errors"] == 1


# ---- outcomes -------------------------------------------------------------------------------------------------------------------------------
def test_outcome_prices_pre_trade_state_of_next_print_with_per_print_v():
    sh, sink = mk_shadow(only_minute(10))
    # V changes at slot S0 + 1500 + 20, the first slot after the landing of the 1.3 s entry's END state; per-print V must follow
    vchange = S0 + 1500 + 20
    for slot in range(S0, slot_of_sec(1000)):
        sh.feed(mk_row(slot, side="buy" if slot % 2 else "sell", v=V0 if slot < vchange else V0 + 1e9), "trades")
    (o,) = sink.of("c1nf_outcome")
    assert o["complete"] is True and "suppressed" not in o and o["gap"] is False
    sd = sink.of("c1nf_pick")[0]["SD_slot"]
    leg = o["legs"]["1.3"]
    X = sd + round(1.3 / SPS)                                     # = sd + 3
    assert leg["landing_slot"] == X and leg["end_estimated"] is False
    # END = pre-trade state of the first print with slot > X, with that print's own V
    assert leg["end"]["entry_q"] == pytest.approx(Q0 + RAMP * (X + 1 - S0) + (V0 if X + 1 < vchange else V0 + 1e9))
    assert leg["end"]["entry_b"] == B0
    t_land = bt_of(sd) + (X - sd) * SPS
    sd_exit = min(s for s in range(S0, slot_of_sec(1000)) if bt_of(s) >= t_land + 300)
    Y = sd_exit + math.ceil(0.55 / SPS)
    assert leg["exit_slot"] == Y
    assert leg["end"]["exit_q"] == pytest.approx(Q0 + RAMP * (Y + 1 - S0) + V0 + 1e9)     # the exit print carries the later V
    # the price rose about 1.3 % (750 slots * 1e6 lamports + the 1e9 V step on ~137 SOL), less than the two ~1.25 % tier fees: a small loss
    assert leg["end"]["exit_px"] > leg["end"]["entry_px"] and -0.03 < leg["end"]["gross_ret"] < 0
    assert set(o["legs"]) == {"1.3", "1.9", "3", "4"}
    for k in o["legs"]:
        for bound in ("end", "worst"):
            assert {"pnl_nofee", "pnl_55k", "pnl_505k", "pnl_505k_rent", "guarded", "tokens"} <= set(o["legs"][k][bound])


def test_worst_bound_is_never_better_than_end():
    sh, sink = mk_shadow(only_minute(10))
    # two prints per slot: the second one in each slot moves the quote by a lot, so WORST (buy: highest, sell: lowest) differs from END
    for slot in range(S0, slot_of_sec(1000)):
        for k in range(2):
            sh.feed(mk_row(slot, side="buy", tx_index=k + 1, quote_reserve=Q0 + RAMP * (slot - S0) + (3e9 if k else 0)), "trades")
    (o,) = sink.of("c1nf_outcome")
    for key, leg in o["legs"].items():
        assert leg["worst"]["pnl_nofee"] <= leg["end"]["pnl_nofee"] + 1e-6, key
    assert o["legs"]["1.3"]["worst"]["entry_px"] >= o["legs"]["1.3"]["end"]["entry_px"] * 0.999


def test_stream_ending_early_gives_incomplete_outcome():
    sh, sink = mk_shadow(only_minute(10))
    run_stream(sh, 700)                                           # exit would be at ~+905: not reached
    assert sink.of("c1nf_outcome") == []
    sh.finish("test")
    (o,) = sink.of("c1nf_outcome")
    assert o["complete"] is False and o["legs"]["1.3"]["incomplete"] == "clock_short"
    assert sink.of("c1nf_stop")


def test_missing_v_makes_the_leg_incomplete_not_a_price():
    sh, sink = mk_shadow(only_minute(10))
    for slot in range(S0, slot_of_sec(1000)):
        sh.feed(mk_row(slot, v=V0 if slot <= S0 + 1500 else None), "trades")    # V known up to SD (decision state and spot), lost after
    (o,) = sink.of("c1nf_outcome")
    assert o["complete"] is False
    assert any("incomplete" in leg for leg in o["legs"].values())


# ---- seal -----------------------------------------------------------------------------------------------------------------------------------
class Oracle:
    """Boolean pick oracle stub. `stale` given -> the oracle exposes staleness_s() (None = unknown age)."""

    def __init__(self, ans=False, stale="absent"):
        self.ans, self.asked = ans, []
        if stale != "absent":
            self.staleness_s = lambda: stale

    def __call__(self, mint):
        self.asked.append(mint)
        if isinstance(self.ans, Exception):
            raise self.ans
        return self.ans


T_IN = SEAL_MS // 1000                                           # first print inside the window


@pytest.mark.parametrize("oracle,suppressed", [
    (None, True), (Oracle(False), False), (Oracle(True), True), (Oracle(RuntimeError("down")), True), (Oracle(None), True), (Oracle(0), True),
    (Oracle("False"), True), (Oracle(False, stale=61.0), True), (Oracle(False, stale=5.0), False), (Oracle(False, stale=None), True)])
def test_seal_window_fail_closed(oracle, suppressed):
    g = cs.SealGuard(SEAL_MS, oracle)
    assert g.suppress(MINT, SEAL_MS + 1) is suppressed


def test_seal_before_window_and_unknown_time():
    g = cs.SealGuard(SEAL_MS, None)
    assert g.suppress(MINT, SEAL_MS - 1) is False                 # first print before the window: not in scope
    assert g.suppress(MINT, None) is True                         # cannot place it: fail closed
    assert cs.SealGuard(None, None).suppress(MINT, SEAL_MS + 5) is False


def test_sealed_pool_gets_no_per_pool_record_only_an_unlabelled_aggregate():
    ora = Oracle(True)                                           # the oracle says: this mint is a CAP-PICK pick
    sh, sink = mk_shadow(only_minute(10, 11), seal_start_ms=BT0 * 1000 - 1000, oracle=ora)    # the pool's first print is inside the window
    run_stream(sh, 1000)
    sh.finish("test")
    assert ora.asked == [MINT, MINT]                              # asked at each candidate decision, read as a boolean only
    assert sink.of("c1nf_pick") == [] and sink.of("c1nf_outcome") == [] and not sh.book and not sh.pending
    blob = json.dumps(sink.records).lower()
    for word in (MINT.lower(), POOL.lower(), "cap_pick", "cap-pick", "suppress", "seal", "oracle"):
        assert word not in blob, word
    hb = sink.of("c1nf_heartbeat")[-1]
    assert hb["counters"]["withheld"] == 2 and "seal" not in hb


def test_sealed_pool_leaves_nothing_on_disk(tmp_path):
    sink = cs.JsonlSink(tmp_path)
    models = cs.ModelSet([{"from_day": "0000-00-00", "file": str(__file__), "sha256": sha_of(__file__)}], loader=lambda p: StubModel())
    sh = cs.Shadow(StubEngine(only_minute(10)), models, sink, replay=True, seal_start_ms=BT0 * 1000 - 1000, oracle=None)    # no oracle: fail closed
    sh.clock.hour_sps[cs.hour_of(BT0)] = SPS
    run_stream(sh, 1000)
    sh.finish("test")
    sink.close()
    text = "".join(p.read_text() for p in tmp_path.iterdir())
    assert MINT not in text and POOL not in text and not list(tmp_path.glob("c1nf-picks-*")) and not list(tmp_path.glob("c1nf-outcomes-*"))


def test_oracle_false_follows_the_pool_in_window():
    sh, sink = mk_shadow(only_minute(10), seal_start_ms=BT0 * 1000 - 1000, oracle=Oracle(False))
    run_stream(sh, 1000)
    (o,) = sink.of("c1nf_outcome")
    assert len(sink.of("c1nf_pick")) == 1 and "suppressed" not in o and o["legs"]["1.3"]["end"]["proceeds"] > 0
    assert sh.c["withheld"] == 0


def test_oracle_gone_stale_after_the_pick_drops_the_outcome_silently():
    ora = Oracle(False, stale=1.0)
    sh, sink = mk_shadow(only_minute(10), seal_start_ms=BT0 * 1000 - 1000, oracle=ora)
    run_stream(sh, 700)
    assert len(sink.of("c1nf_pick")) == 1
    ora.staleness_s = lambda: 500.0                              # the oracle stops refreshing before the exit resolves
    run_stream(sh, 1000, first_sec=700)
    sh.finish("test")
    assert sink.of("c1nf_outcome") == [] and sh.c["withheld"] == 1


def test_seal_start_constant_is_2026_10_16T01Z():
    assert SEAL_MS == int(datetime(2026, 10, 16, 1, tzinfo=timezone.utc).timestamp() * 1000)


# ---- universe and row routing ---------------------------------------------------------------------------------------------------------------
def test_universe_filters_quote_band_canonical_and_missing_v():
    u = cs.Universe(pda_fn=lambda m: "CANON" if m == "MC" else None)
    assert u.accept(mk_row(S0)) is True and POOL in u.pool_mint
    assert u.accept(mk_row(S0, pool="P2", quote_mint="OTHER")) is False
    assert u.accept(mk_row(S0, pool="P3", v=10e9)) is False
    assert u.accept(mk_row(S0, pool="P4", v=None)) is False
    assert u.accept(mk_row(S0, pool="P5", mint="MC")) is False          # not the canonical PDA
    assert u.accept(mk_row(S0, pool="CANON", mint="MC")) is True
    assert u.accept(mk_row(S0, pool="P6", mint=cs.WSOL)) is False
    assert u.counts["quote_not_wsol"] == 1 and u.counts["v_out_of_band"] == 1 and u.counts["no_v_first_print"] == 1 and u.counts["not_canonical"] == 1
    assert u.accept(mk_row(S0, pool="P3", v=V0)) is False                # a rejected pool stays rejected


def test_rows_are_routed_to_the_engine_with_v_and_kinds():
    sh, _ = mk_shadow(lambda T: None)
    sh.feed(mk_row(S0, side="sell"), "trades")
    sh.feed(mk_row(S0 + 1, venue="pump_bonding", pool=None, virtual_quote_reserve=None), "trades")
    sh.feed({"type": "complete", "mint": MINT, "slot": S0 + 2, "block_time": BT0}, "migrations")
    sh.feed({"type": "migration", "mint": MINT, "pool": "CANONPOOL", "slot": S0 + 2, "block_time": BT0}, "migrations")
    sh.feed({"mint": MINT, "creator": "C", "name": "n", "symbol": "s", "is_mayhem_mode": True, "slot": S0 + 3, "block_time": BT0}, "creates")
    e = sh.engine
    assert e.v_set == [(POOL, V0)] and e.trades[0][0] == "pumpswap" and e.trades[0][3] is False and e.trades[1][0] == "pump_bonding"
    assert e.graduations == [(MINT, BT0)] and e.creates[0][0] == MINT and e.creates[0][-1] is True
    assert sh.universe.canon[MINT] == "CANONPOOL"
    sh.feed({"slot": "x", "block_time": 1}, "trades")
    assert sh.c["rows_bad"] == 1


def test_features_adapter_accepts_tuple_and_object():
    vec = np.arange(cs.N_FEATURES, dtype=float)

    class Obj:
        pass

    o = Obj()
    o.vec, o.stage1, o.h_top1, o.sd, o.mint = vec, True, 0.2, 77, "M"
    f = cs.unpack_features(o)
    assert (f.stage1, f.h_top1, f.sd, f.mint) == (True, 0.2, 77, "M")
    assert cs.unpack_features((vec, False, 0.3)).sd is None and cs.unpack_features(None) is None
    with pytest.raises(ValueError):
        cs.unpack_features((np.zeros(5), True, 0.1))


# ---- gaps and heartbeat ---------------------------------------------------------------------------------------------------------------------
def test_clock_jump_emits_gap_and_marks_open_pick():
    sh, sink = mk_shadow(only_minute(10))
    run_stream(sh, 700)
    jump = slot_of_sec(700) + 400                                  # 160 s without a row
    sh.feed(mk_row(jump), "trades")
    gaps = sink.of("c1nf_gap")
    assert any(g["kind"] == "clock_jump" for g in gaps)
    sh.finish("test")
    (o,) = sink.of("c1nf_outcome")
    assert o["gap"] is True
    assert sh.c["minutes_skipped"] >= 1


def test_follower_gap_record_and_silence_and_heartbeat():
    wall = [10_000_000]
    sh, sink = mk_shadow(only_minute(10), wall=lambda: wall[0])
    sh.replay = False
    run_stream(sh, 5)
    sh.note_follower_gap({"from_slot": 5, "to_slot": 9, "reason": "backlog_jump"})
    g = sink.of("c1nf_gap")[-1]
    assert g["kind"] == "follower_gap" and g["slot_from"] == 5 and g["reason"] == "backlog_jump"
    wall[0] += 25_000
    sh.tick(wall[0])
    assert any(r["kind"] == "silence" for r in sink.of("c1nf_gap"))
    hb = sink.of("c1nf_heartbeat")[-1]
    assert hb["model_shas"] == sh.models.shas and "counters" in hb and hb["rule"] == cs.RULE_ID and hb["stream_lag_s"] is not None


# ---- tailer ---------------------------------------------------------------------------------------------------------------------------------
def jl(*rows) -> str:
    return "".join(json.dumps(r) + "\n" for r in rows)


def test_tailer_waits_for_partial_line_and_continues(tmp_path):
    p = tmp_path / "trades-2026-10-09T10.jsonl"
    p.write_text("")
    t = cs.TipTail(tmp_path)
    now = int(datetime(2026, 10, 9, 10, 30, tzinfo=timezone.utc).timestamp() * 1000)
    assert t.poll(now) == []
    line = jl({"slot": 1, "n": "a"}, {"slot": 2, "n": "b"})
    with p.open("a") as fh:
        fh.write(line[: len(line) - 8])                              # second line cut
    assert [r["slot"] for r in t.poll(now)] == [1]
    with p.open("a") as fh:
        fh.write(line[len(line) - 8:] + "not json\n" + jl({"slot": 3}))
    assert [r["slot"] for r in t.poll(now)] == [2, 3] and t.bad_lines == 1
    assert t.poll(now) == []                                          # no replay


def test_tailer_truncation_and_replacement_restart_at_zero(tmp_path):
    p = tmp_path / "trades-2026-10-09T10.jsonl"
    p.write_text(jl({"slot": 1}, {"slot": 2}, {"slot": 3}))
    t = cs.TipTail(tmp_path)
    now = int(datetime(2026, 10, 9, 10, 30, tzinfo=timezone.utc).timestamp() * 1000)
    assert len(t.poll(now)) == 3
    p.write_text(jl({"slot": 9}))                                     # same name, shorter: truncated
    assert [r["slot"] for r in t.poll(now)] == [9] and t.resets == 1
    q = tmp_path / "x.tmp"
    q.write_text(jl({"slot": 20}, {"slot": 21}, {"slot": 22}, {"slot": 23}))
    os.replace(q, p)                                                  # replaced by a new inode, longer than the old offset
    assert [r["slot"] for r in t.poll(now)] == [20, 21, 22, 23] and t.resets == 2


def test_tailer_hour_rotation_precreated_file_and_offset_drop(tmp_path):
    h10, h11, h12 = (tmp_path / f"trades-2026-10-09T{h}.jsonl" for h in (10, 11, 12))
    h10.write_text(jl({"slot": 1}))
    h11.write_text("")                                                # the follower precreates the next hour empty
    t = cs.TipTail(tmp_path)
    ms = lambda h, m: int(datetime(2026, 10, 9, h, m, tzinfo=timezone.utc).timestamp() * 1000)
    assert [r["slot"] for r in t.poll(ms(10, 59))] == [1]
    with h10.open("a") as fh:
        fh.write(jl({"slot": 2}))                                     # late rows in the old hour are still read in the next hour
    with h11.open("a") as fh:
        fh.write(jl({"slot": 3}))
    assert [r["slot"] for r in t.poll(ms(11, 0))] == [2, 3]
    h12.write_text(jl({"slot": 4}))
    assert [r["slot"] for r in t.poll(ms(12, 1))] == [4]
    assert ("trades", "2026-10-09T10") not in t.off and ("trades", "2026-10-09T12") in t.off


def test_tailer_merges_kinds_in_chain_order(tmp_path):
    h = "2026-10-09T10"
    (tmp_path / f"trades-{h}.jsonl").write_text(jl({"slot": 5, "tx_index": 2, "event_index": 0, "k": "trade"}, {"slot": 4, "tx_index": 1, "event_index": 0, "k": "t0"}))
    (tmp_path / f"creates-{h}.jsonl").write_text(jl({"slot": 5, "tx_index": 2, "event_index": 0, "k": "create"}))
    (tmp_path / f"migrations-{h}.jsonl").write_text(jl({"slot": 4, "tx_index": 1, "event_index": 1, "k": "mig"}))
    t = cs.TipTail(tmp_path)
    now = int(datetime(2026, 10, 9, 10, 30, tzinfo=timezone.utc).timestamp() * 1000)
    assert [r["k"] for r in t.poll(now)] == ["t0", "mig", "create", "trade"]


@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd binary")
def test_bootstrap_reads_zst_hours_and_continues_live_offsets(tmp_path):
    import subprocess

    d = tmp_path / "tip"
    d.mkdir()
    raw = tmp_path / "trades-2026-10-09T08.jsonl"
    raw.write_text(jl({"slot": 1}, {"slot": 2}))
    subprocess.run(["zstd", "-q", "--rm", str(raw), "-o", str(d / "trades-2026-10-09T08.jsonl.zst")], check=True)
    (d / "trades-2026-10-09T09.jsonl").write_text(jl({"slot": 3}))
    t = cs.TipTail(d)
    batches = list(t.bootstrap(["2026-10-09T08", "2026-10-09T09"]))
    assert [[r["slot"] for r in b] for b in batches] == [[1, 2], [3]]
    now = int(datetime(2026, 10, 9, 9, 30, tzinfo=timezone.utc).timestamp() * 1000)
    assert t.poll(now) == []                                          # the live tail continues after the bootstrap, no duplicate
    with (d / "trades-2026-10-09T09.jsonl").open("a") as fh:
        fh.write(jl({"slot": 4}))
    assert [r["slot"] for r in t.poll(now)] == [4]


def test_gap_tail_primes_at_end_and_reads_new_lines(tmp_path):
    p = tmp_path / "gaps.jsonl"
    p.write_text(jl({"reason": "old"}))
    g = cs.GapTail(p)
    assert g.poll() == []
    with p.open("a") as fh:
        fh.write(jl({"reason": "backlog_jump", "from_slot": 1, "to_slot": 2}))
    assert [r["reason"] for r in g.poll()] == ["backlog_jump"]


# ---- sink -----------------------------------------------------------------------------------------------------------------------------------
def test_sink_streams_hours_and_strict_json(tmp_path):
    s = cs.JsonlSink(tmp_path)
    t = int(datetime(2026, 10, 9, 10, 5, tzinfo=timezone.utc).timestamp() * 1000)
    s.write({"type": "c1nf_pick", "t_ms": t, "x": float("nan")})
    s.write({"type": "c1nf_outcome", "t_ms": t, "x": np.float64(1.5)})
    s.write({"type": "c1nf_heartbeat", "t_ms": t + 3_600_000})
    s.close()
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["c1nf-events-2026-10-09T11.jsonl", "c1nf-outcomes-2026-10-09T10.jsonl", "c1nf-picks-2026-10-09T10.jsonl"]
    assert json.loads((tmp_path / names[2]).read_text())["x"] is None


@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd binary")
def test_sink_compresses_closed_hours_but_not_picks(tmp_path):
    s = cs.JsonlSink(tmp_path)
    t = int(datetime(2026, 10, 9, 1, 5, tzinfo=timezone.utc).timestamp() * 1000)
    for typ in ("c1nf_pick", "c1nf_outcome", "c1nf_heartbeat"):
        s.write({"type": typ, "t_ms": t})
    s.write({"type": "c1nf_outcome", "t_ms": t + 20 * 3_600_000})
    s.write({"type": "c1nf_heartbeat", "t_ms": t + 20 * 3_600_000})
    n = s.compress_closed(t / 1000 + 20 * 3600)
    assert n == 2
    names = sorted(p.name for p in tmp_path.iterdir())
    assert "c1nf-picks-2026-10-09T01.jsonl" in names and "c1nf-outcomes-2026-10-09T01.jsonl.zst" in names and "c1nf-events-2026-10-09T01.jsonl.zst" in names
    assert "c1nf-outcomes-2026-10-09T21.jsonl" in names and "c1nf-events-2026-10-09T21.jsonl" in names


# ---- replay guard ---------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("path,hours", [
    ("/data/mal/forward-1002/x", ["2026-09-04T12"]), ("/data/mal/audit-1008/tape", ["2026-09-16T00"]), ("/data/mal/audit-1008/tape", ["2026-10-03T00"]),
    ("/data/mal/clean-view/fresh-0828/x", ["2026-09-04T12"]), ("/data/mal/audit-1008/tape", ["2026-09-25T07"]), ("/var/lib/mal/helius.env", ["2026-09-04T12"]),
    ("/data/mal/walk-2/tape", ["2026-09-04T12"])])
def test_replay_refuses_forbidden_paths_and_non_exploration_hours(path, hours):
    with pytest.raises(cs.Refused):
        cs.refuse_replay([path], hours)


def test_replay_accepts_exploration_hours():
    cs.refuse_replay([cs.TAPE_DIR], cs.replay_hours("2026-09-04T12", 24))
    cs.refuse_replay([cs.TAPE_DIR], ["2026-09-25T06", "2026-09-18T23", "2026-09-15T11", "2026-08-14T12"])
    assert cs.replay_hours("2026-09-04T22", 4) == ["2026-09-04T22", "2026-09-04T23", "2026-09-05T00", "2026-09-05T01"]


def test_decide_window_limits_decisions_but_builds_state():
    sh, sink = mk_shadow(lambda T: (True, 0.1) if T >= BT0 + 600 else None, decide_from=BT0 + 720, decide_to=BT0 + 780)
    run_stream(sh, 900)
    assert [a[1] for a in sh.engine.asked] == [BT0 + 720]
    assert len(sh.engine.trades) > 2000                                # state kept building outside the window


# ---- live end to end (stubs) ----------------------------------------------------------------------------------------------------------------
def test_run_live_smoke_with_stub_engine(tmp_path, monkeypatch):
    now = cs.now_ms()
    bt_now = now // 1000
    # synthetic tape ending about 20 s ago, aligned so that a whole-minute decision lands inside the stream
    t_start = (bt_now // 60 - 12) * 60 + 30
    h_files: dict[str, list] = {}
    for slot in range(0, 5000):
        bt = t_start + int(slot * 0.4)
        if bt > bt_now - 20:
            break
        row = mk_row(S0 + slot, block_time=bt, tx_index=1)
        h_files.setdefault(cs.hour_of(bt), []).append(row)
    tip = tmp_path / "tip"
    tip.mkdir()
    for h, rows in h_files.items():
        (tip / f"trades-{h}.jsonl").write_text(jl(*rows))
    T_target = (t_start // 60 + 1) * 60
    eng = StubEngine(lambda T: (True, 0.1) if T == T_target else None)
    f = tmp_path / "m.txt"
    f.write_text("pinned")
    monkeypatch.setattr(cs, "build_engine", lambda ledger=None: eng)
    monkeypatch.setattr(cs, "_lgb_loader", lambda path: StubModel(0.07))
    out = tmp_path / "out"
    rc = cs.main(["--tip-dir", str(tip), "--out-dir", str(out), "--model", str(f), "--model-sha256", hashlib.sha256(b"pinned").hexdigest(),
                  "--bootstrap-hours", "1", "--max-seconds", "1.5", "--poll-s", "0.1", "--no-seal"])
    assert rc == 0
    # bootstrap replays the tape with decisions off, so no pick comes from history; the heartbeat and stop records are written
    events = [json.loads(l) for p in out.glob("c1nf-events-*.jsonl") for l in p.read_text().splitlines()]
    assert {"c1nf_start", "c1nf_stop", "c1nf_heartbeat"} <= {e["type"] for e in events}
    assert not list(out.glob("c1nf-picks-*")) or all(not p.read_text() for p in out.glob("c1nf-picks-*"))
    assert json.loads((out / "status.json").read_text())["counters"]["rows"] == len(eng.blocks) > 100
    assert eng.asked == []                                             # decisions were off while rebuilding state


# ---- wrapper --------------------------------------------------------------------------------------------------------------------------------
WRAP = Path(__file__).resolve().parent.parent / "scripts" / "research" / "c1nf-shadow.sh"


def _wrap(env_extra, *args, tmp):
    import subprocess
    import sys

    env = {"PATH": os.environ["PATH"], "HOME": str(tmp), "C1NF_PYTHON": sys.executable, **env_extra}
    return subprocess.run(["sh", str(WRAP), *args], env=env, capture_output=True, text=True, timeout=60)


def test_wrapper_is_posix_sh_and_refuses_without_a_pin(tmp_path):
    import subprocess

    text = WRAP.read_text()
    assert text.startswith("#!/bin/sh") and "[[" not in text and "${PIPESTATUS" not in text and "$PIPESTATUS" not in text
    assert subprocess.run(["sh", "-n", str(WRAP)]).returncode == 0
    r = _wrap({}, tmp=tmp_path)
    assert r.returncode == 2 and "C1NF_MODEL" in r.stderr


def test_wrapper_passes_pin_and_python_refuses_a_bad_hash(tmp_path):
    m = tmp_path / "m.txt"
    m.write_text("model")
    r = _wrap({"C1NF_MODEL": str(m), "C1NF_MODEL_SHA256": "0" * 64}, "--max-seconds", "1", tmp=tmp_path)
    assert r.returncode == cs.EXIT_REFUSED and "pinned" in r.stderr
    assert (tmp_path / "data" / "c1nf-shadow").is_dir()               # default out dir is $HOME/data/c1nf-shadow
    assert not list((tmp_path / "data" / "c1nf-shadow").glob("c1nf-picks-*"))


# ---- ledger adapter -------------------------------------------------------------------------------------------------------------------------
def test_asof_dir_ledger_adapts_passa_matrix_to_get(tmp_path):
    (tmp_path / "asof" / "asof-2026-10-10").mkdir(parents=True)

    class FakeAsof:
        def passa_matrix(self, th):
            known = np.array([int(t) == 7 for t in th])
            m = np.full((len(th), 7), np.nan)
            m[known] = [10, 2, 1.5, 3, 4, 5, 9.0]
            return known, m

    opened = []
    led = cs.AsofDirLedger(tmp_path, opener=lambda p: (opened.append(p), FakeAsof())[1], hash_fn=lambda s: 7 if s == "known" else 8)
    snap = led.snapshot_for_day("2026-10-10")
    assert snap.get("known") == (10.0, 2.0, 1.5, 3.0, 4.0, 5.0, 9.0) and snap.get("other") is None
    assert led.snapshot_for_day("2026-10-09") is None                   # no prior-day snapshot: wallet features stay NaN, as in C1
    assert opened == [tmp_path / "asof" / "asof-2026-10-10"]
