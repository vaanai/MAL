"""Offline tests for tools/c1nf_shadow.py. The feature engine and the model are stubs (they belong to parallel builders); no network, no keys."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import struct
import sys
import threading
import time
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
        self.v_events = []

    def on_block(self, slot, bt): self.blocks.append((slot, bt))
    def on_create(self, *a): self.creates.append(a)
    def on_graduation(self, mint, bt): self.graduations.append((mint, bt))
    def set_pool_v(self, pool, v): self.v_set.append((pool, v))
    def on_trade(self, *a, v_event=None): self.trades.append(a); self.v_events.append(v_event)
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


class SeqOracle:
    """Pick oracle stub that returns `answers` in turn (the last one repeats); records every mint asked."""

    def __init__(self, answers):
        self.answers, self.asked = list(answers), []

    def __call__(self, mint):
        self.asked.append(mint)
        return self.answers[min(len(self.asked), len(self.answers)) - 1]


def test_undecided_answer_at_pool_open_is_asked_again_not_frozen():
    ora = SeqOracle([None, False])                               # not yet exported at minute 10, a final False at minute 11
    sh, sink = mk_shadow(only_minute(10, 11), seal_start_ms=BT0 * 1000 - 1000, oracle=ora)
    run_stream(sh, 1000)
    sh.finish("test")
    (pick,) = sink.of("c1nf_pick")
    assert pick["decision_T_ms"] // 1000 - BT0 == 660             # minute 10 withheld only for that minute; minute 11 picks
    assert len(sink.of("c1nf_outcome")) == 1 and sh.c["withheld"] == 1
    assert len(ora.asked) == 3                                    # minute 10, minute 11, and again when the outcome resolves


def test_answer_turning_to_pick_after_the_pick_drops_the_outcome():
    ora = SeqOracle([False, True])                               # a False at the decision that later becomes a CAP-PICK pick
    sh, sink = mk_shadow(only_minute(10), seal_start_ms=BT0 * 1000 - 1000, oracle=ora)
    run_stream(sh, 1000)
    sh.finish("test")
    assert len(sink.of("c1nf_pick")) == 1 and sink.of("c1nf_outcome") == [] and sh.c["withheld"] == 1


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


# ---- streaming bootstrap (job #568: the in-memory hour was OOM-killed at a 1.9 GB cap) ------------------------------------------------------
class _OldTipTail(cs.TipTail):
    """The bootstrap as #503 merged it, kept here as the reference: every kind file of an hour is read into one list (a .zst whole into memory by
    `capture_output`) and the list is sorted with `list.sort(key=row_key)`, which is stable. The new bootstrap must give this row sequence."""

    def old_read_plain(self, kind, hour):
        path = self.dir / f"{kind}-{hour}.jsonl"
        key = (kind, hour)
        try:
            st = path.stat()
        except OSError:
            return []
        pos = self.off.get(key, 0)
        if key in self.ino and (self.ino[key] != st.st_ino or st.st_size < pos):
            pos = 0
            self.resets += 1
        self.ino[key] = st.st_ino
        out = []
        with open(path, "rb") as fh:
            fh.seek(pos)
            while True:
                line = fh.readline()
                if not line.endswith(b"\n"):
                    break
                pos += len(line)
                try:
                    r = json.loads(line)
                except ValueError:
                    self.bad_lines += 1
                    continue
                if isinstance(r, dict):
                    r["_k"] = kind
                    out.append(r)
        self.off[key] = pos
        self.rows_read += len(out)
        return out

    def old_read_zst(self, kind, hour, extra_dirs):
        import subprocess

        for d in (self.dir, *extra_dirs):
            p = d / f"{kind}-{hour}.jsonl.zst"
            if p.is_file():
                res = subprocess.run(["zstd", "-dc", str(p)], capture_output=True, check=True, timeout=300)
                out = []
                for line in res.stdout.splitlines():
                    try:
                        r = json.loads(line)
                    except ValueError:
                        self.bad_lines += 1
                        continue
                    if isinstance(r, dict):
                        r["_k"] = kind
                        out.append(r)
                self.rows_read += len(out)
                return out
        return []

    def old_bootstrap(self, hours, extra_dirs=()):
        from pathlib import Path as _P

        extra = [_P(d) for d in extra_dirs]
        for h in hours:
            rows = []
            for k in cs.KINDS:
                got = self.old_read_plain(k, h) if (self.dir / f"{k}-{h}.jsonl").exists() else self.old_read_zst(k, h, extra)
                rows.extend(got)
            rows.sort(key=cs.row_key)
            yield rows


def _put(path: Path, text: str, zst: bool = False) -> None:
    """Write `text` as path (plain) or path + '.zst'."""
    import subprocess

    if zst:
        subprocess.run(["zstd", "-q", "-f", "-o", str(path) + ".zst"], input=text.encode(), check=True)
    else:
        path.write_bytes(text.encode())


def _mixed_hours(tmp_path: Path) -> tuple[Path, Path, list[str]]:
    """A tip dir and an archive dir that hit every branch of the bootstrap: ties across kinds and inside a file, rows without tx_index / event_index,
    bad lines, non-object JSON, a blank line, a half-written last line, .zst in the tip dir and in the archive dir (CRLF, no final newline), an
    empty file, a file of garbage only, a missing kind, a missing hour, and keys that are floats or past int64 (the exact-key path)."""
    tip, arc = tmp_path / "tip", tmp_path / "arc"
    tip.mkdir()
    arc.mkdir()
    hs = [f"2026-10-09T{h:02d}" for h in range(8, 16)]
    # 08: plain, everything
    _put(tip / f"trades-{hs[0]}.jsonl",
         jl({"slot": 7, "tx_index": 2, "event_index": 0, "uid": "t1"}, {"slot": 5, "tx_index": 1, "event_index": 0, "uid": "t2"},
            {"slot": 5, "tx_index": 1, "event_index": 0, "uid": "t3"}, {"slot": 5, "uid": "t4"},
            {"slot": 5, "tx_index": None, "event_index": None, "uid": "t5"}, {"slot": 6, "tx_index": 0, "uid": "t6"})
         + "garbage\n[1, 2]\n5\nnull\n\n" + jl({"slot": 5, "tx_index": 1, "event_index": 1, "uid": "t7"}) + '{"slot": 4, "uid": "half"')
    _put(tip / f"creates-{hs[0]}.jsonl", jl({"slot": 5, "tx_index": 1, "event_index": 0, "uid": "c1"}, {"slot": 5, "uid": "c2"},
                                            {"slot": 7, "tx_index": 2, "event_index": 0, "uid": "c3"}))
    _put(tip / f"migrations-{hs[0]}.jsonl", jl({"slot": 5, "tx_index": 1, "event_index": 0, "uid": "m1"}, {"slot": 7, "tx_index": 2, "event_index": 0, "uid": "m2"}))
    # 09: trades .zst, creates plain, migrations absent
    _put(tip / f"trades-{hs[1]}.jsonl", jl({"slot": 20, "tx_index": 3, "uid": "z1"}, {"slot": 19, "tx_index": 3, "uid": "z2"}, {"slot": 20, "tx_index": 3, "uid": "z3"}), zst=True)
    _put(tip / f"creates-{hs[1]}.jsonl", jl({"slot": 20, "tx_index": 3, "uid": "zc1"}))
    # 10: an empty plain file, a plain file of garbage only, no migrations
    _put(tip / f"trades-{hs[2]}.jsonl", "")
    _put(tip / f"creates-{hs[2]}.jsonl", "nope\n{broken\n")
    # 11: no file at all
    # 12: .zst in the archive dir (CRLF lines, a bad line, no final newline) and in the tip dir (no final newline)
    _put(arc / f"trades-{hs[4]}.jsonl", jl({"slot": 30, "tx_index": 1, "uid": "a1"}, {"slot": 29, "uid": "a2"}).replace("\n", "\r\n") + "bad\r\n"
         + json.dumps({"slot": 30, "tx_index": 1, "uid": "a3"}), zst=True)
    _put(tip / f"creates-{hs[4]}.jsonl", jl({"slot": 30, "tx_index": 1, "uid": "ac1"}).rstrip("\n"), zst=True)
    # 13: keys that are not plain int64: a float equal to an int, a float tx_index, a value past int64
    _put(tip / f"trades-{hs[5]}.jsonl", jl({"slot": 5.0, "uid": "x1"}, {"slot": 5, "uid": "x2"}, {"slot": 2 ** 70, "uid": "x3"}, {"slot": 3, "tx_index": 2.5, "uid": "x4"},
                                           {"slot": 3, "tx_index": 2, "uid": "x5"}, {"slot": -4, "uid": "x6"}))
    _put(tip / f"migrations-{hs[5]}.jsonl", jl({"slot": 5, "uid": "xm1"}))
    # 14: big ints that still fit int64 (the compact path at its edge)
    _put(tip / f"trades-{hs[6]}.jsonl", jl({"slot": 2 ** 63 - 1, "uid": "e1"}, {"slot": -(2 ** 63), "uid": "e2"}, {"slot": 2 ** 63 - 1, "tx_index": 1, "uid": "e3"}))
    # 15: same slot in the three kinds, tied keys appear in a different order than the kinds
    for k in cs.KINDS:
        _put(tip / f"{k}-{hs[7]}.jsonl", jl({"slot": 9, "tx_index": 4, "event_index": 2, "uid": f"{k}1"}, {"slot": 9, "tx_index": 4, "event_index": 2, "uid": f"{k}2"}))
    return tip, arc, hs


needs_zstd = pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd binary")


def _drain(t, hours, extra, new: bool):
    gen = t.bootstrap(hours, extra) if new else t.old_bootstrap(hours, extra)
    return [list(b) for b in gen]


def _counters(t):
    return {"off": t.off, "ino": t.ino, "rows_read": t.rows_read, "bad_lines": t.bad_lines, "resets": t.resets}


@needs_zstd
def test_bootstrap_two_pass_gives_the_old_row_sequence_and_offsets(tmp_path):
    tip, arc, hs = _mixed_hours(tmp_path)
    old, new = _OldTipTail(tip), cs.TipTail(tip, tmp_dir=tmp_path)
    # hs[6] / hs[5] hold keys past the plain int64 path: the exact-key path must agree too
    want = _drain(old, hs, [arc], new=False)
    got = _drain(new, hs, [arc], new=True)
    assert [len(b) for b in got] == [len(b) for b in want]
    assert got == want                                                 # same dicts, same order, ties included
    # recorded, not derived: by (slot, tx_index or 0, event_index or 0, kind rank creates < migrations < trades), ties by line order
    assert [r["uid"] for r in got[0]] == ["c2", "t4", "t5", "c1", "m1", "t2", "t3", "t7", "t6", "c3", "m2", "t1"]
    assert [r["uid"] for r in got[7]] == ["creates1", "creates2", "migrations1", "migrations2", "trades1", "trades2"]
    assert _counters(new) == _counters(old)
    assert old.bad_lines == 5 and old.rows_read == sum(len(b) for b in want)   # the counters are not trivially zero
    assert [("trades", hs[0]), ("creates", hs[0]), ("migrations", hs[0])] == [k for k in new.off if k[1] == hs[0]]
    assert not any(k[1] == hs[1] and k[0] == "trades" for k in new.off)         # a .zst hour keeps no offset
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".bootstrap")) == []   # no temp file left behind


@needs_zstd
def test_bootstrap_two_pass_empty_hours_are_exactly_the_old_empty_batches(tmp_path):
    tip, arc, hs = _mixed_hours(tmp_path)
    old, new = _OldTipTail(tip), cs.TipTail(tip, tmp_dir=tmp_path)
    want = [not b for b in old.old_bootstrap(hs, [arc])]
    got = [not b for b in new.bootstrap(hs, [arc])]
    assert got == want
    assert want[2] and want[3] and not want[0] and not want[1] and not want[4]   # garbage-only and absent hours are empty


@needs_zstd
def test_bootstrap_two_pass_live_tail_continues_where_the_bootstrap_stopped(tmp_path):
    tip, arc, hs = _mixed_hours(tmp_path)
    old, new = _OldTipTail(tip), cs.TipTail(tip, tmp_dir=tmp_path)
    _drain(old, hs, [arc], new=False)
    _drain(new, hs, [arc], new=True)
    now = int(datetime(2026, 10, 9, 8, 30, tzinfo=timezone.utc).timestamp() * 1000)
    assert [r["uid"] for r in new.poll(now)] == [r["uid"] for r in old.poll(now)] == []      # nothing is read twice, the half line still waits
    with (tip / f"trades-{hs[0]}.jsonl").open("a") as fh:
        fh.write(', "tx_index": 9}\n' + jl({"slot": 8, "uid": "late"}))                    # completes the half-written line, then one more row
    with (tip / f"creates-{hs[0]}.jsonl").open("a") as fh:
        fh.write(jl({"slot": 8, "uid": "latec"}))
    a, b = new.poll(now), old.poll(now)
    assert [r["uid"] for r in a] == [r["uid"] for r in b] == ["half", "latec", "late"] and a == b
    assert _counters(new) == _counters(old)


@pytest.mark.parametrize("seed", [1, 2, 3])
@needs_zstd
def test_bootstrap_two_pass_random_hours_with_many_ties(tmp_path, seed):
    import random

    rnd = random.Random(seed)
    tip = tmp_path / "tip"
    tip.mkdir()
    hs = [f"2026-10-09T{h:02d}" for h in range(3)]
    n = 0
    for h in hs:
        for k in cs.KINDS:
            lines = []
            for _ in range(rnd.randrange(0, 700)):
                row = {"slot": rnd.randrange(12), "uid": n}
                n += 1
                if rnd.random() < 0.7:
                    row["tx_index"] = rnd.choice([None, 0, 1, 2, 3])
                if rnd.random() < 0.5:
                    row["event_index"] = rnd.choice([None, 0, 1])
                lines.append(json.dumps(row) + "\n")
                if rnd.random() < 0.02:
                    lines.append(rnd.choice(["junk\n", "\n", "[]\n"]))
            text = "".join(lines)
            if rnd.random() < 0.3:
                text += '{"slot": 1, "uid": "tail"'                                         # half-written
            _put(tip / f"{k}-{h}.jsonl", text, zst=(seed == 3 and k != "creates"))
    old, new = _OldTipTail(tip), cs.TipTail(tip, tmp_dir=tmp_path)
    want, got = _drain(old, hs, [], new=False), _drain(new, hs, [], new=True)
    assert got == want and sum(map(len, want)) > 500
    assert _counters(new) == _counters(old)


@needs_zstd
def test_bootstrap_two_pass_unorderable_keys_fail_as_the_old_sort_did(tmp_path):
    tip = tmp_path / "tip"
    tip.mkdir()
    _put(tip / "trades-2026-10-09T08.jsonl", jl({"slot": "x", "uid": 1}, {"slot": 1, "uid": 2}))
    with pytest.raises(TypeError):
        _drain(_OldTipTail(tip), ["2026-10-09T08"], [], new=False)
    with pytest.raises(TypeError):
        _drain(cs.TipTail(tip), ["2026-10-09T08"], [], new=True)


def test_bootstrap_two_pass_hour_rows_is_reiterable_and_refuses_a_replaced_file(tmp_path):
    tip = tmp_path / "tip"
    tip.mkdir()
    p = tip / "trades-2026-10-09T08.jsonl"
    _put(p, jl({"slot": 2, "uid": "b"}, {"slot": 1, "uid": "a"}))
    t = cs.TipTail(tip)
    (batch,) = list(t.bootstrap(["2026-10-09T08"]))                    # the generator is done; the hour is still readable
    assert len(batch) == 2 and [r["uid"] for r in batch] == ["a", "b"]
    assert [r["uid"] for r in batch] == ["a", "b"]                     # a second iteration reads it again
    q = tip / "x.tmp"
    _put(q, jl({"slot": 2, "uid": "b"}, {"slot": 1, "uid": "a"}))
    os.replace(q, p)                                                   # same bytes, new inode: the offsets no longer belong to the file we scanned
    with pytest.raises(RuntimeError):
        list(batch)
    p.write_text(jl({"slot": 1, "uid": "z"}))                          # same inode, shorter than the scanned end
    (batch2,) = list(cs.TipTail(tip).bootstrap(["2026-10-09T08"]))
    p.write_text("")
    with pytest.raises(RuntimeError):
        list(batch2)


@needs_zstd
def test_bootstrap_zst_temp_file_is_removed_when_the_consumer_stops_early(tmp_path):
    tip = tmp_path / "tip"
    tip.mkdir()
    _put(tip / "trades-2026-10-09T08.jsonl", jl(*({"slot": i, "uid": i} for i in range(50))), zst=True)
    t = cs.TipTail(tip, tmp_dir=tmp_path)
    (batch,) = list(t.bootstrap(["2026-10-09T08"]))
    it = iter(batch)
    assert next(it)["slot"] == 0
    assert len([p for p in tmp_path.iterdir() if p.name.startswith(".bootstrap-")]) == 1     # decompressed while the iteration runs
    it.close()
    assert [p for p in tmp_path.iterdir() if p.name.startswith(".bootstrap-")] == []
    (tmp_path / ".bootstrap-stale.tmp").write_text("x")                # a killed run's leftover goes at the next bootstrap in that dir
    list(t.bootstrap([]))
    assert not (tmp_path / ".bootstrap-stale.tmp").exists()


@needs_zstd
def test_bootstrap_two_pass_corrupt_zst_raises(tmp_path):
    import subprocess

    tip = tmp_path / "tip"
    tip.mkdir()
    (tip / "trades-2026-10-09T08.jsonl.zst").write_bytes(b"not a zstd frame")
    with pytest.raises(subprocess.CalledProcessError):
        list(cs.TipTail(tip, tmp_dir=tmp_path).bootstrap(["2026-10-09T08"]))
    assert [p for p in tmp_path.iterdir() if p.name.startswith(".bootstrap-")] == []


def _synthetic_tip(tip: Path, hour: str, n_rows: int, zst: bool = False) -> None:
    """About n_rows tip rows of the live shape (a dozen fields, near-sorted by slot like the follower's arrival order): 80% trades, 10% creates, 10% migrations."""
    import random

    rnd = random.Random(7)
    for k, share in (("trades", 0.8), ("creates", 0.1), ("migrations", 0.1)):
        lines = []
        for i in range(int(n_rows * share)):
            slot = 1_000_000 + i // 3 + rnd.randrange(-2, 3)
            lines.append(json.dumps({"venue": "pumpswap", "mint": f"M{i % 997:05d}" + "x" * 38, "trader": f"W{rnd.randrange(50_000):06d}" + "y" * 36, "side": "buy",
                                     "sol_lamports": 1_000_000 + i, "token_raw": 1_000_000_000 + i, "quote_reserve": 120e9 + i, "base_reserve": 1e15,
                                     "pool": f"P{i % 997:05d}" + "z" * 38, "slot": slot, "block_time": 1_788_523_200 + slot // 3, "tx_index": rnd.randrange(1, 300),
                                     "event_index": rnd.randrange(0, 3), "virtual_quote_reserve": 17.58e9, "t_recv_ms": 1}) + "\n")
        _put(tip / f"{k}-{hour}.jsonl", "".join(lines), zst=zst)


def _consume_md5(gen) -> tuple[int, str]:
    """Walk a bootstrap row by row, keeping only a digest of the row sequence (so the check itself holds no list of rows)."""
    import hashlib as _h

    md, n = _h.md5(), 0
    for batch in gen:
        for r in batch:
            md.update(f"{r['_k']}{r['slot']}{r['tx_index']}{r['event_index']}{r['sol_lamports']}".encode())
            n += 1
    return n, md.hexdigest()


def test_bootstrap_two_pass_python_allocations_are_a_fraction_of_the_old_list(tmp_path, capsys):
    """tracemalloc peak (numpy allocations are traced too) of the old bootstrap against the new one on one synthetic hour, same rows, same digest."""
    import tracemalloc

    hour, n_rows = "2026-10-09T08", 40_000
    tip = tmp_path / "tip"
    tip.mkdir()
    _synthetic_tip(tip, hour, n_rows)
    peaks = {}
    digests = {}
    for name, gen in (("old", lambda: _OldTipTail(tip).old_bootstrap([hour], [])), ("new", lambda: cs.TipTail(tip).bootstrap([hour], []))):
        tracemalloc.start()
        try:
            digests[name] = _consume_md5(gen())
            peaks[name] = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
    with capsys.disabled():
        print(f"\nbootstrap tracemalloc peak, {digests['new'][0]} rows: old {peaks['old'] / 1e6:.1f} MB, new {peaks['new'] / 1e6:.1f} MB")
    assert digests["old"] == digests["new"] and digests["new"][0] >= n_rows - 1
    assert peaks["new"] * 8 < peaks["old"]


_RSS_PROBE = r'''
import json, sys
root, tip, mode, tmp = sys.argv[1:5]
sys.path.insert(0, root)
from pathlib import Path
from tools import c1nf_shadow as cs
from tools.test_c1nf_shadow import _OldTipTail, _consume_md5


def hwm_kb():                       # this process's own high-water RSS (getrusage ru_maxrss would carry the parent's peak over the exec)
    for line in open("/proc/self/status"):
        if line.startswith("VmHWM:"):
            return int(line.split()[1])


base_kb = hwm_kb()
hour = "2026-10-09T08"
gen = _OldTipTail(Path(tip)).old_bootstrap([hour], []) if mode == "old" else cs.TipTail(Path(tip), tmp_dir=tmp).bootstrap([hour], [])
n, md = _consume_md5(gen)
print(json.dumps({"n": n, "md5": md, "base_kb": base_kb, "peak_kb": hwm_kb()}))
'''


@needs_zstd
@pytest.mark.skipif(not os.path.exists("/proc/self/status"), reason="Linux /proc")
@pytest.mark.parametrize("zst,n_rows", [(False, 300_000), (True, 120_000)])
def test_bootstrap_two_pass_resident_memory_does_not_follow_the_hour(tmp_path, capsys, zst, n_rows):
    """Peak resident memory (VmHWM) of a fresh process walking one synthetic hour, old bootstrap against new: the cause of job #568 was RSS. The hour
    is written by this process; each probe is its own process, so its baseline (interpreter, numpy, this module) is subtracted."""
    import subprocess
    import sys

    tip = tmp_path / "tip"
    tip.mkdir()
    _synthetic_tip(tip, "2026-10-09T08", n_rows, zst=zst)
    root = str(Path(__file__).resolve().parent.parent)
    res = {}
    for mode in ("old", "new"):
        p = subprocess.run([sys.executable, "-c", _RSS_PROBE, root, str(tip), mode, str(tmp_path)], capture_output=True, text=True, timeout=300)
        assert p.returncode == 0, p.stderr[-2000:]
        res[mode] = json.loads(p.stdout.strip().splitlines()[-1])
    extra = {m: (r["peak_kb"] - r["base_kb"]) / 1024 for m, r in res.items()}
    with capsys.disabled():
        print(f"\nbootstrap RSS ({'zst' if zst else 'plain'}, {res['new']['n']} rows): old +{extra['old']:.0f} MB (peak {res['old']['peak_kb'] / 1024:.0f}), "
              f"new +{extra['new']:.0f} MB (peak {res['new']['peak_kb'] / 1024:.0f})")
    assert res["old"]["n"] == res["new"]["n"] >= n_rows - 1 and res["old"]["md5"] == res["new"]["md5"]
    assert extra["old"] > 100 and extra["new"] < 60 and extra["new"] * 5 < extra["old"]


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
    s.write({"type": "c1nf_pick", "t_ms": t, "decision_T_ms": cs.OUTCOME_START_MS, "x": float("nan")})
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
        s.write({"type": typ, "t_ms": t, "decision_T_ms": cs.OUTCOME_START_MS})
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
    monkeypatch.setattr(cs, "build_engine", lambda ledger=None, v_source=None: eng)
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
class FakeAsof:
    """AsofLedger double: passa_matrix knows th 7 only; manifest names its asof_day."""

    def __init__(self, day):
        self.manifest = {"asof_day": day}

    def passa_matrix(self, th):
        known = np.array([int(t) == 7 for t in th])
        m = np.full((len(th), 7), np.nan)
        m[known] = [10, 2, 1.5, 3, 4, 5, 9.0]
        return known, m


class StaleLedger(Exception):
    """Stands in for tools.c1nf_wallet_ledger.StaleLedger (matched by class name)."""


def test_asof_dir_ledger_opens_through_open_for_day_and_adapts_passa_matrix(tmp_path):
    asked = []

    def open_for_day(root, day):
        asked.append((root, day))
        if day != "2026-10-10":
            raise StaleLedger(f"no as-of snapshot for decision day {day}")
        return FakeAsof(day)

    led = cs.AsofDirLedger(tmp_path, open_for_day=open_for_day, hash_fn=lambda s: 7 if s == "known" else 8)
    snap = led.snapshot_for_day("2026-10-10")
    assert snap.get("known") == (10.0, 2.0, 1.5, 3.0, 4.0, 5.0, 9.0) and snap.get("other") is None
    assert led.snapshot_for_day("2026-10-11") is None and led.stale == 1     # asof-<D> not built yet (00:00Z..rollup): None, never asof-<D-1>
    assert asked == [(tmp_path, "2026-10-10"), (tmp_path, "2026-10-11")]   # only the decision day is ever asked for


def test_asof_dir_ledger_refuses_a_snapshot_for_another_day_and_propagates_other_errors(tmp_path):
    led = cs.AsofDirLedger(tmp_path, open_for_day=lambda root, day: FakeAsof("2026-10-09"), hash_fn=lambda s: 7)
    assert led.snapshot_for_day("2026-10-10") is None and led.stale == 1

    def broken(root, day):
        raise OSError("disk")

    with pytest.raises(OSError):                                  # not a StaleLedger: the engine counts it as ledger_errors and retries
        cs.AsofDirLedger(tmp_path, open_for_day=broken, hash_fn=lambda s: 7).snapshot_for_day("2026-10-10")


def test_asof_dir_ledger_default_seam_is_asofledger_open_for_day(tmp_path, monkeypatch):
    import types

    calls = []

    class AsofLedger:
        @classmethod
        def open_for_day(cls, root, day):
            calls.append((root, day))
            return FakeAsof(day)

    monkeypatch.setitem(sys.modules, "tools.c1nf_wallet_ledger", types.SimpleNamespace(AsofLedger=AsofLedger))
    led = cs.AsofDirLedger(tmp_path, hash_fn=lambda s: 7)
    assert led.snapshot_for_day("2026-10-10").get("x") == (10.0, 2.0, 1.5, 3.0, 4.0, 5.0, 9.0)
    assert calls == [(tmp_path, "2026-10-10")]


def _fake_duckdb_and_ledger(monkeypatch, check_hash_pin):
    """A duckdb whose connection answers hash(?) with 7, and a tools.c1nf_wallet_ledger whose check_hash_pin is the given function."""
    import types

    class Con:
        def execute(self, sql, params=None):
            return types.SimpleNamespace(fetchone=lambda: (7,))

    con = Con()
    monkeypatch.setitem(sys.modules, "duckdb", types.SimpleNamespace(connect=lambda: con))
    monkeypatch.setitem(sys.modules, "tools.c1nf_wallet_ledger",
                        types.SimpleNamespace(AsofLedger=object, check_hash_pin=check_hash_pin))
    return con


def test_asof_dir_ledger_refuses_to_start_when_the_duckdb_hash_pin_fails(tmp_path, monkeypatch):
    class PinRefused(RuntimeError):
        """Stands in for tools.c1nf_wallet_ledger.Refused."""

    def check_hash_pin(con):
        raise PinRefused("duckdb hash() differs from the pinned function")

    _fake_duckdb_and_ledger(monkeypatch, check_hash_pin)
    with pytest.raises(cs.Refused, match="PinRefused.*differs from the pinned"):   # at construction, as the shadow's Refused (main exits 3)
        cs.AsofDirLedger(tmp_path)


def test_asof_dir_ledger_refuses_to_start_without_duckdb(tmp_path, monkeypatch):
    _fake_duckdb_and_ledger(monkeypatch, lambda c: None)
    monkeypatch.setitem(sys.modules, "duckdb", None)   # `import duckdb` raises ImportError
    with pytest.raises(cs.Refused, match="duckdb"):
        cs.AsofDirLedger(tmp_path)


def test_main_exits_3_not_a_traceback_when_the_hash_pin_or_duckdb_fails(tmp_path, monkeypatch, capsys):
    f = tmp_path / "m.txt"
    f.write_text("pinned")
    argv = ["--out-dir", str(tmp_path / "o"), "--model", str(f), "--model-sha256", hashlib.sha256(b"pinned").hexdigest(),
            "--ledger-root", str(tmp_path / "ledger"), "--no-seal"]
    monkeypatch.setattr(cs, "_lgb_loader", lambda path: StubModel())

    def bad_pin(con):
        raise RuntimeError("duckdb hash() differs from the pinned function")

    _fake_duckdb_and_ledger(monkeypatch, bad_pin)
    assert cs.main(argv) == cs.EXIT_REFUSED
    assert "refusing:" in capsys.readouterr().err
    monkeypatch.setitem(sys.modules, "duckdb", None)
    assert cs.main(argv) == cs.EXIT_REFUSED
    assert "refusing:" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()               # nothing was started


def test_asof_dir_ledger_default_hash_is_built_from_the_pinned_connection(tmp_path, monkeypatch):
    checked = []
    con = _fake_duckdb_and_ledger(monkeypatch, lambda c: checked.append(c))
    led = cs.AsofDirLedger(tmp_path, open_for_day=lambda root, day: FakeAsof(day))
    assert checked == [con]                            # the pin was checked, once, on the connection the hash runs on
    assert led.hash_fn("anyone") == 7
    assert led.snapshot_for_day("2026-10-10").get("anyone") == (10.0, 2.0, 1.5, 3.0, 4.0, 5.0, 9.0)


# ---- file-backed snapshot: _PreadSnapshot must return exactly what _AsofSnapshot returns, without mapping the ledger -------------------------
FIX_DAY = "2026-10-10"
I64_EDGES = [0, 1, -1, 2**53, 2**53 + 1, 2**62 + 3, -(2**62) - 5, np.iinfo(np.int64).max, np.iinfo(np.int64).min, 1_048_576, -1_048_577, 7]


def _fixture_root(tmp_path: Path, th: np.ndarray, seed: int = 0, day: str = FIX_DAY) -> Path:
    """<root>/asof/asof-<day>/ in the layout of tools/c1nf_wallet_ledger.py: th.npy (uint64, sorted) and the 11 int64 columns, plus a MANIFEST."""
    from tools import c1nf_wallet_ledger as L

    rng = np.random.default_rng(seed)
    d = tmp_path / "asof" / f"asof-{day}"
    d.mkdir(parents=True)
    np.save(d / "th.npy", np.asarray(th, dtype=np.uint64), allow_pickle=False)
    n = len(th)
    for c in L.ASOF_COLS:
        v = rng.integers(-(2**40), 2**40, size=n, dtype=np.int64)
        if n:
            v[rng.integers(0, n, size=min(n, 12))] = rng.choice(np.array(I64_EDGES, dtype=np.int64), size=min(n, 12))
        np.save(d / f"{c}.npy", v, allow_pickle=False)
    (d / "MANIFEST.json").write_text(json.dumps({"schema": L.SCHEMA_VERSION, "asof_day": day, "days": []}))
    return tmp_path


def _bits(v):
    return None if v is None else (type(v), tuple(type(x) for x in v), struct.pack("<7d", *v))


def _both(root: Path, hash_fn=int):
    from tools import c1nf_wallet_ledger as L

    led = L.AsofLedger.open_for_day(root, FIX_DAY)
    old = cs._AsofSnapshot(led, hash_fn)
    new = cs._PreadSnapshot.from_ledger(led, hash_fn)
    assert isinstance(new, cs._PreadSnapshot)
    return led, old, new


@pytest.mark.parametrize("n,block,dup", [(0, 512, False), (1, 512, False), (2, 4, False), (37, 4, False), (64, 4, False), (65, 4, False),
                                          (512, 512, False), (513, 512, False), (1025, 512, False), (5000, 512, False), (5000, 7, False),
                                          (300, 8, True)])
def test_pread_snapshot_returns_exactly_what_the_memmap_snapshot_returns(tmp_path, monkeypatch, n, block, dup):
    monkeypatch.setattr(cs._PreadSnapshot, "BLOCK", block)
    rng = np.random.default_rng(n * 31 + block)
    top = np.array([0, 1, 2, 2**63, 2**64 - 3, 2**64 - 2, 2**64 - 1], dtype=np.uint64)
    pool = np.unique(np.concatenate([top, rng.integers(0, 2**64 - 1, size=n * 2, dtype=np.uint64, endpoint=True)]))
    th = np.sort(rng.choice(pool, size=min(n, len(pool)), replace=False)) if not dup else np.sort(rng.choice(pool[:20], size=n, replace=True))
    root = _fixture_root(tmp_path, th, seed=n)
    led, old, new = _both(root)
    known = [int(x) for x in th]
    probes = known + [h + d for h in known for d in (-1, 1) if 0 <= h + d < 2**64] + [0, 1, 2**64 - 1, 2**63, 2**63 - 1, 2**63 + 1]
    probes += [int(x) for x in rng.integers(0, 2**64 - 1, size=500, dtype=np.uint64, endpoint=True)]
    hits = 0
    for h in probes:
        vo, vn = old.get(str(h)), new.get(str(h))
        assert _bits(vo) == _bits(vn), h                       # None, or tuple of float, bit for bit
        hits += vo is not None
        old._cache.clear(), new._cache.clear()
    assert hits >= (0 if n == 0 else min(len(th), 1))
    if len(th) and not dup:                                   # and against the ledger's own matrix
        known_m, m = led.passa_matrix(th)
        assert known_m.all()
        for i, h in enumerate(known):
            assert struct.pack("<7d", *new.get(str(h))) == m[i].tobytes()
    new.close()


def test_pread_snapshot_unknown_wallets_and_the_cache_behave_like_the_memmap_snapshot(tmp_path):
    root = _fixture_root(tmp_path, np.arange(10, 4000, 3, dtype=np.uint64))
    _, old, new = _both(root)
    assert old.get("11") is None and new.get("11") is None and "11" in new._cache      # absent is cached as None, as before
    assert new.get("13") is new.get("13") and new.get("13") == old.get("13") and new.get("13") is not None
    assert len(new._cache) == 2
    new._cache_max = 3
    for k in ("16", "19", "22", "25"):
        new.get(k)
    assert len(new._cache) <= 4                               # cleared once past the bound, like the 500_000 of _AsofSnapshot
    for bad in (-1, 1 << 64):                                 # a hash outside uint64 fails the same way in both
        old._hash = new._hash = lambda _s, bad=bad: bad
        with pytest.raises(OverflowError):
            old.get("x")
        with pytest.raises(OverflowError):
            new.get("x")


def test_pread_snapshot_keeps_no_mapping_and_closes_its_descriptors(tmp_path):
    if not os.path.isdir("/proc/self/fd"):
        pytest.skip("needs /proc")
    import gc

    def mapped() -> int:
        return sum(str(tmp_path) in line for line in open("/proc/self/maps"))

    def fds() -> int:
        return sum(os.path.realpath(f"/proc/self/fd/{f}").startswith(str(tmp_path.resolve())) for f in os.listdir("/proc/self/fd") if os.path.exists(f"/proc/self/fd/{f}"))

    root = _fixture_root(tmp_path, np.arange(0, 20000, 2, dtype=np.uint64))
    led = cs.AsofDirLedger(root, hash_fn=int)
    snap = led.snapshot_for_day(FIX_DAY)
    gc.collect()
    assert isinstance(snap, cs._PreadSnapshot) and mapped() == 0                   # AsofLedger's maps closed with the ledger
    assert snap.get("100") is not None and snap.get("101") is None and mapped() == 0     # and lookups map nothing
    assert fds() == 8                                                              # th + the 7 passA columns
    snap.close()
    assert fds() == 0
    snap2 = led.snapshot_for_day(FIX_DAY)
    assert fds() == 8
    del snap2
    gc.collect()
    assert fds() == 0                                                              # dropped by the engine's day rollover: closed by GC


def test_asof_dir_ledger_uses_the_pread_snapshot_for_a_file_backed_ledger_and_the_memmap_one_for_doubles(tmp_path):
    root = _fixture_root(tmp_path / "real", np.arange(5, 500, 5, dtype=np.uint64))
    real = cs.AsofDirLedger(root, hash_fn=int).snapshot_for_day(FIX_DAY)
    assert type(real) is cs._PreadSnapshot and real.get("10") is not None and real.get("11") is None
    assert real.get("10") == cs.AsofDirLedger(root, hash_fn=int).snapshot_for_day(FIX_DAY).get("10")
    dbl = cs.AsofDirLedger(tmp_path, open_for_day=lambda r, d: FakeAsof(d), hash_fn=lambda s: 7).snapshot_for_day(FIX_DAY)
    assert type(dbl) is cs._AsofSnapshot                                          # no .th / .cols: the reference implementation
    assert cs._PreadSnapshot.from_ledger(FakeAsof(FIX_DAY), lambda s: 7) is None


def test_pread_snapshot_refuses_a_truncated_column_instead_of_reading_garbage(tmp_path):
    from tools import c1nf_wallet_ledger as L

    root = _fixture_root(tmp_path, np.arange(0, 3000, 3, dtype=np.uint64))
    led = L.AsofLedger.open_for_day(root, FIX_DAY)
    cut = tmp_path / "asof" / f"asof-{FIX_DAY}" / "nrt.npy"
    with open(cut, "r+b") as fh:                              # the maps were taken at full size; the file is shorter when we open it
        fh.truncate(cut.stat().st_size - 800)
    with pytest.raises(OSError, match="nrt.npy"):
        cs._PreadSnapshot.from_ledger(led, int)


def _real_shadow(v_source):
    sink = cs.MemorySink()
    models = cs.ModelSet([{"from_day": "0000-00-00", "file": str(__file__), "sha256": sha_of(__file__)}], loader=lambda p: StubModel())
    return cs.Shadow(cs.build_engine(None, v_source=v_source), models, sink, seal_start_ms=None)


def test_build_engine_builds_the_real_feature_engine_in_both_modes():
    from tools import c1nf_features as cf

    assert (cs.V_SOURCE_LIVE, cs.V_SOURCE_REPLAY) == (cf.V_EVENT, cf.V_CONST) == ("event", "const")
    for mode in (cf.V_EVENT, cf.V_CONST):
        eng = cs.build_engine(None, v_source=mode)
        assert type(eng) is cf.FeatureEngine and eng.v_source == mode        # no stub
    with pytest.raises(TypeError):                                           # no default: a caller must choose
        cs.build_engine(None)


def test_main_picks_the_v_source_from_the_mode(tmp_path, monkeypatch):
    f = tmp_path / "m.txt"
    f.write_text("pinned")
    monkeypatch.setattr(cs, "_lgb_loader", lambda path: StubModel())
    seen = []
    real_build = cs.build_engine

    def spy(ledger=None, *, v_source):
        seen.append(v_source)
        real_build(ledger, v_source=v_source)                                # the real constructor accepts it
        raise cs.Refused("spy: stop before any run")

    monkeypatch.setattr(cs, "build_engine", spy)
    base = ["--out-dir", str(tmp_path / "o"), "--model", str(f), "--model-sha256", hashlib.sha256(b"pinned").hexdigest(), "--no-seal"]
    assert cs.main(base + ["--tip-dir", str(tmp_path)]) == cs.EXIT_REFUSED
    assert cs.main(base + ["--replay-from", "2026-09-04T12"]) == cs.EXIT_REFUSED
    assert seen == ["event", "const"]


def test_real_engine_event_mode_needs_the_rows_event_v_and_const_mode_takes_the_tip_v():
    """One PumpSwap print through Shadow._on_trade into the real engine. Live ("event"): the pool is accepted only when the row carries
    `virtual_quote_reserves`; the tip follower's `virtual_quote_reserve` alone is not event V, so the shadow's own universe rejects the pool
    `no_v_first_print` before the engine sees it (counted, fail closed), and the engine's own guard still rejects such a print `v0_missing`.
    Replay ("const"): the stamped V0 goes through set_pool_v and the same row is accepted."""
    row = mk_row(S0)
    assert cs.event_v_of({"virtual_quote_reserves": 17_580_000_000.0}) == 17_580_000_000 and cs.event_v_of(row) is None
    assert cs.event_v_of({"virtual_quote_reserves": True}) is None and cs.event_v_of({"virtual_quote_reserves": float("nan")}) is None

    live = _real_shadow("event")
    live._on_trade(dict(row, virtual_quote_reserves=int(V0)), row["slot"], row["block_time"])
    assert live.engine.health()["pool_rejects"] == {}
    bare = _real_shadow("event")
    bare._on_trade(row, row["slot"], row["block_time"])                      # tip key only
    assert bare.universe.counts["no_v_first_print"] == 1 and POOL not in bare.engine._pools
    bare.engine.on_trade("pumpswap", MINT, "w", True, 1_000_000, 1_000_000_000, Q0, B0, POOL, S0, BT0, v_event=None)
    assert bare.engine.health()["pool_rejects"] == {"v0_missing": 1}

    replay = _real_shadow("const")
    replay._on_trade(row, row["slot"], row["block_time"])
    assert replay.engine.health()["pool_rejects"] == {}


X_TIP, Y_EV = 10e9, int(V0)          # the tip follower's cached V (out of band) and the print's own event V (in band)


def _event_shadow_real():
    """Event mode on the real FeatureEngine, on an exploration-hour clock with replay=True (so the outcome guard lets the outcome through; the
    V source is the engine's, not the replay flag's). The engine's decision is replaced by a fixed vector at minute 10; admission, V0 and the
    event mapping stay the engine's."""
    sink = cs.MemorySink()
    models = cs.ModelSet([{"from_day": "0000-00-00", "file": str(__file__), "sha256": sha_of(__file__)}], loader=lambda p: StubModel())
    eng = cs.build_engine(None, v_source="event")
    eng.alive_pools = lambda T: [p for p in eng._pools if T == BT0 + 600]
    eng.features_at = lambda pool, T, sd=None: (np.full(cs.N_FEATURES, 0.5), True, 0.1)
    sh = cs.Shadow(eng, models, sink, replay=True, seal_start_ms=None)
    sh.clock.hour_sps[cs.hour_of(BT0)] = SPS
    sh.clock.hour_sps[cs.hour_of(BT0 + 3600)] = SPS
    return sh, sink


def test_real_engine_live_prices_on_the_rows_event_v_never_the_tip_v():
    """#503 review, MEDIUM: rows carry the tip's `virtual_quote_reserve` = X and the print's event `virtual_quote_reserves` = Y, X != Y. In
    event mode the universe band, the engine's V0, Print.v, the pick's v_lamports / q_lamports and every outcome price are on Y."""
    sh, sink = _event_shadow_real()
    assert sh.event_v and sh.universe.event_v and sh.v_source == "event"
    vchange = S0 + 1500 + 20
    for slot in range(S0, slot_of_sec(1000)):
        y = Y_EV if slot < vchange else Y_EV + 1_000_000_000
        sh.feed(mk_row(slot, side="buy" if slot % 2 else "sell", v=X_TIP, virtual_quote_reserves=y), "trades")
    assert sh.universe.counts["pools_accepted"] == 1 and sh.engine._pools[POOL].V == Y_EV      # band and V0 on Y (X is out of band)
    assert sh.engine.stats.get("set_pool_v_ignored_event_mode", 0) == 0                          # set_pool_v is not called in event mode
    (p,) = sink.of("c1nf_pick")
    sd = p["SD_slot"]
    s = sd - 1
    pr = cs.Print(s, bt_of(s), s % 2 == 1, 1_000_000, 1_000_000_000, Q0 + RAMP * (s - S0), B0, float(Y_EV))
    assert p["v_lamports"] == Y_EV and p["state_slot"] == s and p["q_lamports"] == int(round(pr.post()[0]))
    assert cs.executor_refusal(p) == "pre_window"                              # a valid pick (September clock: never the executor's)
    (o,) = sink.of("c1nf_outcome")
    leg = o["legs"]["1.3"]
    xs = sd + round(1.3 / SPS)
    assert leg["end"]["entry_q"] == pytest.approx(Q0 + RAMP * (xs + 1 - S0) + Y_EV)                # entry priced on that print's Y
    ys = leg["exit_slot"]
    assert leg["end"]["exit_q"] == pytest.approx(Q0 + RAMP * (ys + 1 - S0) + Y_EV + 1e9)          # exit on the later Y, never X

    band, _ = _event_shadow_real()                                           # band on Y: X in band, Y out of band -> rejected
    band.feed(mk_row(S0, v=float(Y_EV), virtual_quote_reserves=int(X_TIP)), "trades")
    assert band.universe.counts["v_out_of_band"] == 1 and POOL not in band.engine._pools

    lost, lsink = _event_shadow_real()                                       # event V lost after SD, tip key still there: no price
    for slot in range(S0, slot_of_sec(1000)):
        kw = {"virtual_quote_reserves": Y_EV} if slot <= S0 + 1500 else {}
        lost.feed(mk_row(slot, side="buy" if slot % 2 else "sell", v=X_TIP, **kw), "trades")
    (o2,) = lsink.of("c1nf_outcome")
    assert o2["complete"] is False and all("incomplete" in leg for leg in o2["legs"].values())


def test_shadow_refuses_a_v_source_that_disagrees_with_the_engine_or_universe():
    models = cs.ModelSet([{"from_day": "0000-00-00", "file": str(__file__), "sha256": sha_of(__file__)}], loader=lambda p: StubModel())
    eng = cs.build_engine(None, v_source="event")
    with pytest.raises(ValueError):
        cs.Shadow(eng, models, cs.MemorySink(), v_source="const")
    with pytest.raises(ValueError):
        cs.Shadow(eng, models, cs.MemorySink(), universe=cs.Universe())                # a const Universe under an event engine
    with pytest.raises(ValueError):
        cs.Shadow(StubEngine(), models, cs.MemorySink(), v_source="bogus")
    assert cs.Shadow(StubEngine(), models, cs.MemorySink()).event_v is False             # a stub without v_source is const
    assert cs.Shadow(StubEngine(), models, cs.MemorySink(), v_source="event").universe.event_v is True
    assert cs.Shadow(eng, models, cs.MemorySink()).universe.event_v is True


@pytest.mark.parametrize("age", [float("nan"), float("inf"), True, False, "5", None])
def test_seal_oracle_age_nan_inf_bool_or_non_number_is_stale(age):
    class O:
        def __call__(self, mint):
            return False

        def staleness_s(self):
            return age

    g = cs.SealGuard(SEAL_MS, O())
    assert g.suppress(MINT, SEAL_MS) is True and g.counters["seal_oracle_stale"] == 1


def _ev_stub_shadow(decide=None):
    sink = cs.MemorySink()
    models = cs.ModelSet([{"from_day": "0000-00-00", "file": str(__file__), "sha256": sha_of(__file__)}], loader=lambda p: StubModel())
    eng = StubEngine(decide or (lambda T: None))
    eng.v_source = "event"
    sh = cs.Shadow(eng, models, sink, replay=True, seal_start_ms=None)
    sh.clock.hour_sps[cs.hour_of(BT0)] = SPS
    sh.clock.hour_sps[cs.hour_of(BT0 + 3600)] = SPS
    return sh, sink


EV = {"virtual_quote_reserves": int(V0)}
COMPLETE = {"type": "complete", "mint": MINT, "slot": S0, "block_time": BT0}


def test_v0_gap_rejects_a_pool_first_seen_after_a_follower_gap_that_reaches_its_opening():
    sh, _ = _ev_stub_shadow()                                                  # opening (complete row) before the gap: rejected
    sh.feed(dict(COMPLETE), "migrations")
    sh.note_follower_gap({"from_slot": S0 + 5, "to_slot": S0 + 9, "reason": "backlog_jump"})
    sh.feed(mk_row(S0 + 20, **EV), "trades")
    assert sh.universe.counts["v0_gap"] == 1 and POOL not in sh.universe.pool_mint and sh.engine.trades == []
    sh.feed(mk_row(S0 + 21, **EV), "trades")                                   # stays rejected
    assert sh.engine.trades == []

    sh, _ = _ev_stub_shadow()                                                  # the migration row is the opening when present
    sh.feed(dict(COMPLETE), "migrations")
    sh.note_follower_gap({"from_slot": S0 + 5, "to_slot": S0 + 9, "reason": "backlog_jump"})
    sh.feed({"type": "migration", "mint": MINT, "pool": POOL, "slot": S0 + 12, "block_time": bt_of(S0 + 12)}, "migrations")
    sh.feed(mk_row(S0 + 20, **EV), "trades")
    assert sh.universe.counts["v0_gap"] == 0 and POOL in sh.universe.pool_mint

    sh, _ = _ev_stub_shadow()                                                  # the gap ends before the opening: accepted
    sh.feed(mk_row(S0 - 50, pool="P0", mint="M0", **EV), "trades")
    sh.note_follower_gap({"from_slot": S0 - 9, "to_slot": S0 - 5, "reason": "backlog_jump"})
    sh.feed(dict(COMPLETE), "migrations")
    sh.feed(mk_row(S0 + 20, **EV), "trades")
    assert sh.universe.counts["v0_gap"] == 0 and POOL in sh.universe.pool_mint

    sh, _ = _ev_stub_shadow()                                                  # opening unknown: a gap within the horizon rejects
    sh.feed(mk_row(S0, pool="P0", mint="M0", **EV), "trades")
    sh.note_follower_gap({"slot": S0 + 3, "kind": "gap", "reason": "unfetchable"})
    sh.feed(mk_row(S0 + 20, **EV), "trades")
    assert sh.universe.counts["v0_gap"] == 1

    sh, _ = _ev_stub_shadow()                                                  # a gap after the first print does not touch V0
    sh.feed(dict(COMPLETE), "migrations")
    sh.feed(mk_row(S0 + 20, **EV), "trades")
    sh.note_follower_gap({"from_slot": S0 + 30, "to_slot": S0 + 40, "reason": "backlog_jump"})
    assert POOL in sh.universe.pool_mint and sh.c["v0_gap_late"] == 0

    sh, _ = mk_shadow(lambda T: None)                                          # const (replay) mode: V0 is the table's, no check
    sh.feed(dict(COMPLETE), "migrations")
    sh.note_follower_gap({"from_slot": S0 + 5, "to_slot": S0 + 9, "reason": "backlog_jump"})
    sh.feed(mk_row(S0 + 20), "trades")
    assert sh.universe.counts["v0_gap"] == 0 and POOL in sh.universe.pool_mint


def test_gap_read_after_the_first_print_stops_decisions_on_that_pool():
    sh, sink = _ev_stub_shadow(only_minute(10))
    sh.feed(dict(COMPLETE), "migrations")
    run_stream(sh, 100, **EV)
    sh.note_follower_gap({"from_slot": S0 - 1, "to_slot": S0 + 1, "reason": "backlog_jump"})    # read late: covers the opening
    run_stream(sh, 1000, first_sec=100, **EV)
    assert sh.c["v0_gap_late"] == 1 and sink.of("c1nf_pick") == []
    ok, oksink = _ev_stub_shadow(only_minute(10))                               # the same stream without the gap picks
    ok.feed(dict(COMPLETE), "migrations")
    run_stream(ok, 1000, **EV)
    assert len(oksink.of("c1nf_pick")) == 1


class WalletEngine(StubEngine):
    """Returns a Features-like object with wallet_ok, as tools.c1nf_features does."""

    def __init__(self, decide, wallet_ok):
        super().__init__(decide)
        self.wallet_ok = wallet_ok

    def features_at(self, pool, T, sd=None):
        r = super().features_at(pool, T, sd)
        if r is None:
            return None
        import types

        return types.SimpleNamespace(vec=r[0], stage1=r[1], h_top1=r[2], sd=None, mint=MINT, wallet_ok=self.wallet_ok)


@pytest.mark.parametrize("wallet_ok,picks", [(False, 0), (True, 1)])
def test_decision_without_the_days_ledger_snapshot_is_never_picked(wallet_ok, picks):
    sink = cs.MemorySink()
    model = StubModel()
    models = cs.ModelSet([{"from_day": "0000-00-00", "file": str(__file__), "sha256": sha_of(__file__)}], loader=lambda p: model)
    sh = cs.Shadow(WalletEngine(only_minute(10), wallet_ok), models, sink, replay=True, seal_start_ms=None)
    sh.clock.hour_sps[cs.hour_of(BT0)] = SPS
    run_stream(sh, 1000)
    sh.finish("test")
    assert len(sink.of("c1nf_pick")) == picks and len(sink.of("c1nf_outcome")) == picks
    assert sh.c["no_wallet_ledger"] == (1 - picks) and model.calls == picks    # refused before the model is asked


# ---- DEC-026 item 8: decision-time state on the pick -----------------------------------------------------------------------------------------
def test_pick_carries_decision_state_of_the_last_print_before_sd():
    sh, sink = mk_shadow(only_minute(10))
    run_stream(sh, 640)
    (pick,) = sink.of("c1nf_pick")
    sd = pick["SD_slot"]
    prev = sd - 1                                                  # the last print with slot < SD (the stream prints every slot)
    want_q, want_b = cs.Print(prev, bt_of(prev), bool(prev % 2), 1e6, 1e9, Q0 + RAMP * (prev - S0), B0, V0).post()
    assert pick["state_slot"] == prev
    assert pick["q_lamports"] == round(want_q) and pick["base_reserve"] == round(want_b) and pick["v_lamports"] == round(V0)
    assert type(pick["q_lamports"]) is int and type(pick["base_reserve"]) is int and pick["q_lamports"] > V0
    assert cs.validate_pick(pick) == []
    assert set(cs.PICK_FIELDS) >= {"q_lamports", "base_reserve", "v_lamports", "state_slot"}
    fx = json.loads((FIX / "pick_example.json").read_text())
    assert fx == cs.PICK_EXAMPLE and fx["q_lamports"] > 0 and fx["base_reserve"] > 0


def test_decision_state_never_uses_a_print_at_or_after_sd():
    sh, _ = mk_shadow()
    sh.last_print[POOL] = cs.Print(500, BT0, True, 1e6, 1e9, 100e9, B0, V0)
    sh.before_slot[POOL] = cs.Print(497, BT0, False, 1e6, 1e9, 90e9, B0, V0)
    q, b, v, slot = sh._decision_state(POOL, 500)                  # the SD-slot print is skipped; the earlier slot's state is used
    assert slot == 497 and (q, b) == tuple(round(x) for x in sh.before_slot[POOL].post()) and v == round(V0)
    assert sh._decision_state(POOL, 497) is None                   # nothing strictly before SD: no state
    assert sh._decision_state("UNSEEN", 500) is None


def test_no_decision_state_means_no_pick_no_book_no_outcome():
    sh, sink = mk_shadow(only_minute(10))
    for slot in range(S0, slot_of_sec(1000)):                      # V known on the first prints, missing on every print before SD
        sh.feed(mk_row(slot, v=V0 if slot < S0 + 10 or slot >= S0 + 1500 else None), "trades")
    sh.finish("test")
    assert sink.of("c1nf_pick") == [] and sink.of("c1nf_outcome") == [] and not sh.book
    assert sh.c["no_decision_state"] == 1 and sh.c["picks"] == 0


@pytest.mark.parametrize("field,val", [("q_lamports", 0), ("q_lamports", -5), ("q_lamports", 1.5e11), ("base_reserve", True),
                                       ("base_reserve", 0), ("state_slot", "1"), ("v_lamports", 0), ("v_lamports", 1.758e10),
                                       ("v_lamports", 137_580_000_000), ("q_lamports", 17_580_000_000)])
def test_validate_pick_rejects_bad_decision_state(field, val):
    assert cs.validate_pick({**cs.PICK_EXAMPLE, field: val}) != []


@pytest.mark.parametrize("field", ["q_lamports", "base_reserve", "v_lamports", "state_slot"])
def test_validate_pick_requires_decision_state(field):
    rec = dict(cs.PICK_EXAMPLE)
    del rec[field]
    assert f"missing {field}" in cs.validate_pick(rec)


SD10 = slot_of_sec(600)                                           # SD of minute 10: the first slot with block_time >= BT0 + 600


def _stream_with_bad_print(bad_side, drop=(), **over):
    """Every slot prints; the last print before SD of minute 10 is `side` with the keys in `drop` removed (a tip key mismatch) or overridden."""
    sh, sink = mk_shadow(only_minute(10))
    for slot in range(S0, slot_of_sec(1000)):
        row = mk_row(slot, side="buy" if slot % 2 else "sell")
        if slot == SD10 - 1:
            row["side"] = bad_side
            for k in drop:
                del row[k]
            row.update(over)
        sh.feed(row, "trades")
    sh.finish("test")
    return sh, sink


def test_sd_minus_one_is_the_state_print():
    assert bt_of(SD10) == BT0 + 600 and bt_of(SD10 - 1) < BT0 + 600
    sh, sink = _stream_with_bad_print("sell")                      # control: a well-formed sell before SD gives a pick
    (pick,) = sink.of("c1nf_pick")
    assert pick["state_slot"] == SD10 - 1 and sh.c["no_decision_state"] == 0


@pytest.mark.parametrize("side", ["buy", "sell"])
@pytest.mark.parametrize("drop,over", [
    (("quote_reserve",), {}), (("base_reserve",), {}), (("token_raw",), {}),          # missing raw keys (tip key mismatch)
    ((), {"quote_reserve": None}), ((), {"base_reserve": 0}), ((), {"token_raw": "1000"}), ((), {"quote_reserve": True}),
    ((), {"base_reserve": float("nan")})])
def test_malformed_state_print_gives_no_pick(side, drop, over):
    """Reviewer probe on 17a6b81: no base_reserve gave a 20x spot, no quote_reserve about V (0.13x), no token_raw the PRE state labelled post.
    Each now gives no pick, no book entry and no outcome."""
    sh, sink = _stream_with_bad_print(side, drop, **over)
    assert sink.of("c1nf_pick") == [] and sink.of("c1nf_outcome") == [] and not sh.book
    assert sh.c["no_decision_state"] == 1 and sh.c["picks"] == 0


@pytest.mark.parametrize("over", [{"sol_lamports": None}, {"sol_lamports": 0}, {"side": None}, {"side": "swap"}])
def test_buy_without_sol_or_unknown_side_gives_no_pick(over):
    sh, sink = _stream_with_bad_print("buy", **over)
    assert sink.of("c1nf_pick") == [] and sh.c["no_decision_state"] == 1


def test_sell_without_sol_still_has_a_state():
    sh, sink = _stream_with_bad_print("sell", sol_lamports=None)   # post() of a sell needs only token_raw; sol is the output
    assert len(sink.of("c1nf_pick")) == 1


def test_post_has_no_pre_state_fallback():
    p = cs.Print(1, BT0, True, 1e6, 2e15, Q0, B0, V0)                # buys more tokens than the pool holds
    with pytest.raises(ValueError):
        p.post()
    for bad in (cs.Print(1, BT0, True, 1e6, 1e9, 0.0, B0, V0), cs.Print(1, BT0, False, 1e6, 1e9, Q0, 0.0, V0)):
        with pytest.raises(ValueError):
            bad.pre()
    with pytest.raises(ValueError):
        cs.Print(1, BT0, False, 0.0, 0.0, Q0, B0, V0).post()


@pytest.mark.parametrize("v", [0.0, -1e9])
def test_state_needs_positive_v_and_quote_above_v(v):
    sh, _ = mk_shadow()
    sh.last_print[POOL] = cs.Print(499, BT0, False, 1e6, 1e9, Q0, B0, v)
    assert sh._decision_state(POOL, 500) is None
    sh.last_print[POOL] = cs.Print(499, BT0, False, 1e6, 1e9, Q0, B0, V0)
    assert sh._decision_state(POOL, 500)[2] == round(V0)


def test_malformed_print_after_sd_makes_the_outcome_incomplete_not_a_price():
    sh, sink = mk_shadow(only_minute(10))
    for slot in range(S0, slot_of_sec(1000)):
        row = mk_row(slot, side="buy" if slot % 2 else "sell")
        if slot == SD10:
            del row["base_reserve"]                                    # the batch spot print: a 20x price if it were read as 0 -> 1
        sh.feed(row, "trades")
    sh.finish("test")
    (o,) = sink.of("c1nf_outcome")
    assert o["complete"] is False and o["spot"] is None and "no_spot" in o["reasons"]


# ---- DEC-026 item 8: binding outcome guard from 2026-10-10T00Z -------------------------------------------------------------------------------
OS_MS = cs.OUTCOME_START_MS
OS_S = OS_MS // 1000


def test_outcome_start_constant_is_2026_10_10T00Z():
    assert OS_MS == int(datetime(2026, 10, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)


@pytest.mark.parametrize("t,ok", [
    (OS_MS, True), (OS_MS - 60_000, False), (OS_MS + 86_400_000, True),
    (BT0 * 1000, True),                                                        # exploration (2026-09-04T12): replay keeps its outcomes
    (int(datetime(2026, 9, 25, 6, 59, tzinfo=timezone.utc).timestamp() * 1000), True),
    (int(datetime(2026, 9, 25, 7, 0, tzinfo=timezone.utc).timestamp() * 1000), False),   # end of the last exploration range (exclusive)
    (int(datetime(2026, 9, 28, tzinfo=timezone.utc).timestamp() * 1000), False),
    (int(datetime(2026, 10, 9, 17, tzinfo=timezone.utc).timestamp() * 1000), False),
    (int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp() * 1000), False)])
def test_outcome_allowed_boundaries(t, ok):
    assert cs.outcome_allowed(t, replay=True) is ok
    assert cs.outcome_allowed(t, replay=False) is (t >= OS_MS)     # live: no exploration exemption, whatever the clock says
    with pytest.raises(TypeError):
        cs.outcome_allowed(t)                                      # the mode is required, never defaulted


def test_live_mode_with_an_exploration_hour_clock_prices_nothing():
    wall = {"t": BT0 * 1000}
    sink = cs.MemorySink()
    models = cs.ModelSet([{"from_day": "0000-00-00", "file": str(__file__), "sha256": sha_of(__file__)}], loader=lambda p: StubModel())
    sh = cs.Shadow(StubEngine(only_minute(10)), models, sink, replay=False, seal_start_ms=None, wall=lambda: wall["t"])
    sh.clock.hour_sps[cs.hour_of(BT0)] = SPS
    for slot in range(S0, slot_of_sec(1000)):
        wall["t"] = bt_of(slot) * 1000 + 700
        sh.feed(mk_row(slot, side="buy" if slot % 2 else "sell"), "trades")
    sh.finish("test")
    assert len(sink.of("c1nf_pick")) == 1 and sink.of("c1nf_outcome") == []
    assert sh.c["outcome_guard_pre_window"] == 1 and not sh.pending


def test_executor_refuses_pre_window_and_invalid_picks():
    sh, sink, run, _ = _midnight_shadow(replay=False)
    run(660 + 400)
    pre, post = sink.of("c1nf_pick")
    assert cs.executor_refusal(pre) == "pre_window" and cs.executor_refusal(post) is None
    assert cs.executor_refusal(cs.PICK_EXAMPLE) is None and cs.PICK_EXAMPLE["decision_T_ms"] >= OS_MS
    assert cs.executor_refusal({**cs.PICK_EXAMPLE, "decision_T_ms": OS_MS - 1}) == "pre_window"
    assert cs.executor_refusal({**cs.PICK_EXAMPLE, "v_lamports": cs.PICK_EXAMPLE["q_lamports"]}) == "invalid_pick"
    rec = dict(cs.PICK_EXAMPLE)
    del rec["q_lamports"]
    assert cs.executor_refusal(rec) == "invalid_pick"


def _midnight_shadow(replay=True, classifier=None, decide_mins=(-10, 0), sink=None):
    """A stream from 2026-10-09T23:49Z across 2026-10-10T00Z. Decisions at the given minutes relative to 00:00Z."""
    base = OS_S - 660
    want = {OS_S + 60 * m for m in decide_mins}
    sink = cs.MemorySink() if sink is None else sink
    models = cs.ModelSet([{"from_day": "0000-00-00", "file": str(__file__), "sha256": sha_of(__file__)}], loader=lambda p: StubModel())
    wall = {"t": base * 1000}
    sh = cs.Shadow(StubEngine(lambda T: (True, 0.1) if T in want else None), models, sink, replay=replay, seal_start_ms=None,
                   wall=lambda: wall["t"], classifier=classifier)
    for h in (base, OS_S):
        sh.clock.hour_sps[cs.hour_of(h)] = SPS
    pend_at_pre = []
    cursor = {"slot": S0}

    def run(until_s):                                              # resumes where the previous call stopped
        start, cursor["slot"] = cursor["slot"], max(cursor["slot"], S0 + int(until_s / SPS))
        for slot in range(start, cursor["slot"]):
            bt = base + int((slot - S0) * SPS)
            wall["t"] = bt * 1000 + 700
            sh.feed(mk_row(slot, side="buy" if slot % 2 else "sell", block_time=bt), "trades")
            if bt == OS_S - 1:
                pend_at_pre.append(sum(len(v) for v in sh.pending.values()))
    return sh, sink, run, pend_at_pre


@pytest.mark.parametrize("replay", [True, False])
def test_pre_window_decision_writes_its_pick_but_nothing_is_priced(replay):
    sh, sink, run, pend_at_pre = _midnight_shadow(replay=replay)
    run(660 + 400)                                                 # to 00:06:40Z, past the 00:00Z pick's exit + grace
    sh.finish("test")
    picks, outs = sink.of("c1nf_pick"), sink.of("c1nf_outcome")
    assert [p["decision_T_ms"] for p in picks] == [OS_MS - 600_000, OS_MS]
    assert all(cs.validate_pick(p) == [] for p in picks)
    assert [o["decision_T_ms"] for o in outs] == [OS_MS]           # only the 00:00Z decision has an outcome
    assert pend_at_pre and set(pend_at_pre) == {0}                 # the 23:50Z pick never had a Pending (nothing priced)
    assert sh.c["outcome_guard_pre_window"] == 1 and sh.c["outcomes"] == 1
    blob = json.dumps([r for r in sink.records if r.get("type") != "c1nf_pick"])
    assert str(OS_MS - 600_000) not in blob                       # no record but the pick names the pre-window decision


@pytest.mark.parametrize("t,prefix", [
    (OS_MS, "c1nf-picks"), (OS_MS + 3_600_000, "c1nf-picks"), (OS_MS - 1, "c1nf-prewindow-picks"), (BT0 * 1000, "c1nf-prewindow-picks"),
    (None, "c1nf-prewindow-picks"), ("1791591000000", "c1nf-prewindow-picks"), (True, "c1nf-prewindow-picks")])
def test_sink_routes_pre_window_picks_away_from_the_executor_stream(tmp_path, t, prefix):
    s = cs.JsonlSink(tmp_path)
    assert s.prefix_of({**cs.PICK_EXAMPLE, "decision_T_ms": t}) == prefix
    assert s.prefix_of({"type": "c1nf_outcome", "decision_T_ms": OS_MS - 1}) == "c1nf-outcomes"


@pytest.mark.parametrize("replay", [True, False])
def test_executor_pick_stream_holds_only_picks_the_executor_may_act_on(tmp_path, replay):
    """Reviewer LOW on 0b679c4: the 23:50Z pick (pre-window) is written to c1nf-prewindow-picks-*, so the executor's tailer never sees it;
    every line of c1nf-picks-* passes executor_refusal()."""
    import fnmatch

    sink = cs.JsonlSink(tmp_path)
    sh, _, run, _ = _midnight_shadow(replay=replay, sink=sink)
    run(660 + 400)
    sh.finish("test")
    sink.close()
    exec_files = sorted(p for p in tmp_path.iterdir() if fnmatch.fnmatch(p.name, "c1nf-picks-*"))
    exec_lines = [json.loads(l) for p in exec_files for l in p.read_text().splitlines()]
    assert [r["decision_T_ms"] for r in exec_lines] == [OS_MS]
    assert all(cs.executor_refusal(r) is None for r in exec_lines)
    (pw,) = sorted(tmp_path.glob("c1nf-prewindow-picks-*.jsonl"))
    assert pw.name == "c1nf-prewindow-picks-2026-10-09T23.jsonl"
    (pre,) = [json.loads(l) for l in pw.read_text().splitlines()]
    assert pre["decision_T_ms"] == OS_MS - 600_000 and cs.executor_refusal(pre) == "pre_window" and cs.validate_pick(pre) == []
    assert not fnmatch.fnmatch(pw.name, "c1nf-picks-*") and not fnmatch.fnmatch(pw.name, "c1nf-picks-????-??-??T??.jsonl")
    assert sh.c["picks"] == 2 and sh.c["picks_prewindow_stream"] == 1


def test_replay_picks_go_to_the_prewindow_stream(tmp_path):
    sink = cs.JsonlSink(tmp_path)
    models = cs.ModelSet([{"from_day": "0000-00-00", "file": str(__file__), "sha256": sha_of(__file__)}], loader=lambda p: StubModel())
    sh = cs.Shadow(StubEngine(only_minute(10)), models, sink, replay=True, seal_start_ms=None)
    sh.clock.hour_sps[cs.hour_of(BT0)] = SPS
    run_stream(sh, 1000)
    sh.finish("test")
    sink.close()
    assert not list(tmp_path.glob("c1nf-picks-*"))
    assert len(list(tmp_path.glob("c1nf-prewindow-picks-*"))) == 1 and list(tmp_path.glob("c1nf-outcomes-*"))   # exploration replay still priced


def test_invalid_pick_is_never_written_booked_or_priced():
    sh, sink = mk_shadow(only_minute(10, 12), pred=float("inf"))
    run_stream(sh, 1000)
    sh.finish("test")
    assert sink.of("c1nf_pick") == [] and sink.of("c1nf_outcome") == [] and not sh.book
    assert sh.c["invalid_pick"] == 2 and sh.c["picks"] == 0


def test_outcome_guard_has_no_switch():
    import inspect
    params = set(inspect.signature(cs.Shadow).parameters)
    assert not any("outcome" in p or "guard" in p for p in params)
    opts = {a for act in cs.build_parser()._actions for a in act.option_strings}
    assert not any("outcome" in o for o in opts)


# ---- EXP-025 Amendment 2 item 5: synthetic class only as a counts stream ---------------------------------------------------------------------
def _counts(**kw):
    out = {f"universe_{c}": 0 for c in cs.SYN_CLASSES}
    out.update(kw)
    return out


SYN_RECORD_KEYS = {"schema", "t_ms", "type", "day_first", "day_last", "partial", "held_back", "universe_total", "counts", "restart_in_span",
                   "bootstrap_unclassified"}


def _two_class(m, p):
    return "synthetic" if p == POOL else "non_synthetic"


def test_synthetic_class_goes_only_to_a_separate_counts_stream():
    asked = []
    sh, sink = mk_shadow(only_minute(10), classifier=lambda m, p: (asked.append((m, p)), _two_class(m, p))[1])
    sh.feed(mk_row(S0, pool="POOLbbbb", mint="MINTbbbb"), "trades")
    run_stream(sh, 1000)
    sh.heartbeat()
    sh.finish("test")
    assert sorted(asked) == sorted([(MINT, POOL), ("MINTbbbb", "POOLbbbb")])   # asked once per pool
    (pick,), (out,) = sink.of("c1nf_pick"), sink.of("c1nf_outcome")
    assert cs.validate_pick(pick) == [] and cs._class_keys(out) == []
    (sc,) = sink.of(cs.SYNCLASS_TYPE)
    assert set(sc) == SYN_RECORD_KEYS and sc["partial"] is True and sc["day_first"] == sc["day_last"] == "2026-09-04"
    assert sc["counts"] == _counts(universe_synthetic=1, universe_non_synthetic=1) and sc["held_back"] is False
    assert not any(k.startswith("pick") for k in sc["counts"])    # no pick count by class (a near-join with daily outcomes)
    assert MINT not in json.dumps(sc) and POOL not in json.dumps(sc) and "POOLbbbb" not in json.dumps(sc)
    others = [r for r in sink.records if r.get("type") != cs.SYNCLASS_TYPE]
    assert "synthetic" not in json.dumps(others) and "unclassified" not in json.dumps(others)   # heartbeat, stop, pick, outcome: no class


def _settle(sh):
    for job in list(sh._cls_pending):
        assert job.ev.wait(timeout=5)


SYN_POOLS = {"POOLcccc", "POOLbbbb"}


def test_synclass_counts_close_each_utc_day_once():
    sh, sink, run, _ = _midnight_shadow(classifier=lambda m, p: "synthetic" if p in SYN_POOLS else "non_synthetic")
    sh.feed(mk_row(S0, pool="POOLcccc", mint="MINTcccc", block_time=OS_S - 660), "trades")           # 10-09: one synthetic, one not
    run(300)
    _settle(sh)
    run(660 + 400)
    assert [(r["day_first"], r["day_last"]) for r in sink.of(cs.SYNCLASS_TYPE)] == [("2026-10-09", "2026-10-09")]   # on entering 10-10
    for i, pool in enumerate(("POOLbbbb", "POOLdddd")):                                                # 10-10: one synthetic, one not
        sh.feed(mk_row(S0 + 2700 + i, pool=pool, mint="MINT" + pool[4:], block_time=OS_S + 420 + i), "trades")
    assert len(sink.of(cs.SYNCLASS_TYPE)) == 1                     # 10-10 is not closed yet
    sh.finish("test")
    recs = sink.of(cs.SYNCLASS_TYPE)
    assert [(r["day_first"], r["day_last"], r["partial"], r["held_back"]) for r in recs] == [
        ("2026-10-09", "2026-10-09", False, False), ("2026-10-10", "2026-10-10", True, False)]
    assert recs[0]["counts"] == _counts(universe_synthetic=1, universe_non_synthetic=1) == recs[1]["counts"]


def test_degenerate_day_is_held_back_and_merged_into_the_next():
    """A day whose universe is all one class (or has no synthetic / no non-synthetic pool) would let an observer attribute that day's
    real-time outcomes to a class: it is never written alone (reviewer LOW on 17a6b81)."""
    sh, sink, run, _ = _midnight_shadow(classifier=lambda m, p: "synthetic" if p in SYN_POOLS else "non_synthetic")
    run(300)
    _settle(sh)
    run(660 + 400)                                                 # 10-09: only POOL (non-synthetic): degenerate
    assert sink.of(cs.SYNCLASS_TYPE) == []
    sh.feed(mk_row(S0 + 2700, pool="POOLbbbb", mint="MINTbbbb", block_time=OS_S + 420), "trades")
    sh.finish("test")
    (rec,) = sink.of(cs.SYNCLASS_TYPE)
    assert (rec["day_first"], rec["day_last"], rec["held_back"]) == ("2026-10-09", "2026-10-10", False)
    assert rec["counts"] == _counts(universe_synthetic=1, universe_non_synthetic=1) and rec["universe_total"] == 2


@pytest.mark.parametrize("cnt,deg", [
    (_counts(universe_synthetic=3), True), (_counts(universe_non_synthetic=2, universe_unclassified=1), True),
    (_counts(universe_synthetic=2, universe_unclassified=1), True), (_counts(universe_unclassified=4), True),
    (_counts(universe_synthetic=1, universe_non_synthetic=1), False), (_counts(universe_synthetic=1, universe_non_synthetic=3, universe_unclassified=2), False)])
def test_degenerate_rule(cnt, deg):
    assert cs.Shadow._cls_degenerate(cnt) is deg


def test_degenerate_to_the_end_is_written_without_class_counts():
    sh, sink = mk_shadow(only_minute(10), classifier=lambda m, p: "synthetic")
    run_stream(sh, 700)
    sh.finish("test")
    (rec,) = sink.of(cs.SYNCLASS_TYPE)
    assert rec["held_back"] is True and rec["counts"] is None and rec["universe_total"] == 1 and "synthetic" not in json.dumps(rec)


@pytest.mark.parametrize("cls", [RuntimeError("rpc down"), "weird", None, True])
def test_classifier_failure_or_junk_is_unclassified(cls):
    def clf(m, p):
        if isinstance(cls, Exception):
            raise cls
        return cls
    sh, sink = mk_shadow(only_minute(10), classifier=clf)
    run_stream(sh, 700)
    sh.finish("test")
    assert sh._cls == {POOL: "unclassified"}
    (sc,) = sink.of(cs.SYNCLASS_TYPE)
    assert sc["held_back"] is True and sc["universe_total"] == 1   # one pool, all unclassified: degenerate, no class counts written
    assert len(sink.of("c1nf_pick")) == 1                          # never a refusal by class


def test_no_classifier_no_class_stream():
    sh, sink = mk_shadow(only_minute(10))
    run_stream(sh, 1000)
    sh.finish("test")
    assert sink.of(cs.SYNCLASS_TYPE) == [] and len(sink.of("c1nf_outcome")) == 1


@pytest.mark.parametrize("rec", [
    {"type": "c1nf_outcome", "mint": MINT, "legs": {"1.3": {"end": {"synthetic": True}}}},
    {"type": "c1nf_outcome", "mint": MINT, "synthetic_class": "synthetic"},
    {**cs.PICK_EXAMPLE, "class": "non_synthetic"},
    {**cs.PICK_EXAMPLE, "extra": [{"is_synthetic": False}]}])
def test_emit_drops_a_per_pool_record_that_carries_the_class(rec):
    sh, sink = mk_shadow()
    sh.emit(rec)
    assert sink.records == [] and sh.c["class_leak_blocked"] == 1


def test_class_stream_is_its_own_file_on_disk(tmp_path):
    sink = cs.JsonlSink(tmp_path)
    models = cs.ModelSet([{"from_day": "0000-00-00", "file": str(__file__), "sha256": sha_of(__file__)}], loader=lambda p: StubModel())
    sh = cs.Shadow(StubEngine(only_minute(10)), models, sink, replay=True, seal_start_ms=None, classifier=_two_class)
    sh.clock.hour_sps[cs.hour_of(BT0)] = SPS
    sh.feed(mk_row(S0, pool="POOLbbbb", mint="MINTbbbb"), "trades")
    run_stream(sh, 1000)
    sh.finish("test")
    sink.close()
    cls_files = list(tmp_path.glob("c1nf-synclass-*"))
    assert len(cls_files) == 1 and "universe_synthetic" in cls_files[0].read_text()
    for p in tmp_path.iterdir():
        if p not in cls_files and p.suffix == ".jsonl":
            assert "synthetic" not in p.read_text(), p.name
    assert list(tmp_path.glob("c1nf-outcomes-*")) and list(tmp_path.glob("c1nf-prewindow-picks-*"))   # replay picks: exploration hours


def test_slow_classifier_does_not_delay_the_pick_and_times_out_to_unclassified():
    started = []

    def slow(m, p):
        started.append(time.monotonic())
        time.sleep(1.0)
        return "synthetic"
    sh, sink = mk_shadow(only_minute(10), classifier=slow, classify_timeout_s=0.2)
    t0 = time.monotonic()
    sh.feed(mk_row(S0, pool="POOLbbbb", mint="MINTbbbb"), "trades")
    run_stream(sh, 700)
    t_feed = time.monotonic() - t0
    assert len(sink.of("c1nf_pick")) == 1 and t_feed < 0.8         # 1 s per classifier call, two pools: the rows never waited on it
    t1 = time.monotonic()
    sh.finish("test")
    assert time.monotonic() - t1 < 0.8                             # stop waits at most the timeout, not the classifier
    assert set(sh._cls.values()) == {"unclassified"} and set(sh._cls) == {POOL, "POOLbbbb"}
    assert started and len(started) <= 2


HANG_RUNNER = r'''
import json, sys, threading, time
from pathlib import Path
from tools import c1nf_shadow as cs
from tools.test_c1nf_shadow import StubEngine, StubModel, mk_row, S0, V0

tip, out, model = sys.argv[1:4]
cs.CLASSIFY_TIMEOUT_S = 0.3
cs.build_engine = lambda ledger=None, v_source=None: StubEngine(lambda T: None)
cs._lgb_loader = lambda p: StubModel()

def append_after_bootstrap():
    time.sleep(0.4)
    bt = cs.now_ms() // 1000
    row = mk_row(S0, block_time=bt, pool="POOLhang", mint="MINThang", t_recv_ms=1, virtual_quote_reserves=int(V0))   # live reads event V
    with open(Path(tip) / f"trades-{cs.hour_of(bt)}.jsonl", "a") as fh:
        fh.write(json.dumps(row) + "\n")

threading.Thread(target=append_after_bootstrap, daemon=True).start()
rc = cs.main(["--tip-dir", tip, "--out-dir", out, "--model", model, "--model-sha256", cs.sha256_file(model), "--bootstrap-hours", "0",
              "--max-seconds", "1.5", "--poll-s", "0.05", "--no-seal", "--synthetic-classifier", "c1nf_hang_clf:hang"])
print("main returned", rc, flush=True)
sys.exit(rc)
'''

HANG_CLF = r'''
import os, threading
from pathlib import Path

def hang(mint, pool):
    Path(os.environ["C1NF_HANG_MARK"]).write_text(pool)
    threading.Event().wait()                     # never returns: a lookup with no timeout of its own
'''


def test_process_exits_although_a_classifier_call_never_returns(tmp_path):
    """Reviewer MEDIUM on 0b679c4: a ThreadPoolExecutor worker is joined at interpreter exit, so a classifier call that never returned kept the
    process alive after c1nf_stop (a MiScusi job that never ends). The worker is a daemon thread now: main returns and the process exits."""
    import subprocess
    import sys

    repo = Path(__file__).resolve().parent.parent
    (tmp_path / "tip").mkdir()
    (tmp_path / "runner.py").write_text(HANG_RUNNER)
    (tmp_path / "c1nf_hang_clf.py").write_text(HANG_CLF)
    (tmp_path / "m.txt").write_text("pinned")
    mark = tmp_path / "called"
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PYTHONPATH": f"{repo}{os.pathsep}{tmp_path}", "C1NF_HANG_MARK": str(mark)}
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, str(tmp_path / "runner.py"), str(tmp_path / "tip"), str(tmp_path / "out"), str(tmp_path / "m.txt")],
                       cwd=repo, env=env, capture_output=True, text=True, timeout=60)
    took = time.monotonic() - t0
    assert r.returncode == 0, r.stderr[-2000:]
    assert mark.read_text() == "POOLhang"                          # the classifier was really called, and never returned
    assert "main returned 0" in r.stdout
    assert took < 1.5 + 0.3 + 6.0                                  # max-seconds + the classify timeout + interpreter start-up margin
    events = [json.loads(l) for p in (tmp_path / "out").glob("c1nf-events-*.jsonl") for l in p.read_text().splitlines()]
    assert [e["type"] for e in events if e["type"] == "c1nf_stop"] == ["c1nf_stop"]
    (sc,) = [json.loads(l) for p in (tmp_path / "out").glob("c1nf-synclass-*.jsonl") for l in p.read_text().splitlines()]
    assert sc["held_back"] is True and sc["universe_total"] == 1   # one pool, timed out: unclassified, written without class counts


def test_class_worker_is_one_daemon_thread_and_cancels_queued_jobs():
    gate = threading.Event()
    calls = []

    def clf(m, p):
        calls.append(p)
        gate.wait(timeout=5)
        return "synthetic"
    w = cs.ClassWorker(clf)
    assert w.thread.daemon is True
    a, b = cs.ClassJob("A", "mA", "2026-10-10", time.monotonic()), cs.ClassJob("B", "mB", "2026-10-10", time.monotonic())
    w.submit(a)
    w.submit(b)
    deadline = time.monotonic() + 5
    while a.state != "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert w.cancel(b) is True and w.cancel(a) is False            # b never started; a is running
    gate.set()
    assert a.ev.wait(5) and a.state == "done" and a.result == "synthetic"
    w.close()
    w.thread.join(timeout=5)
    assert not w.thread.is_alive() and calls == ["A"] and b.state == "cancelled"


def test_no_classifier_call_during_the_bootstrap():
    asked = []
    sh, sink = mk_shadow(only_minute(10), classifier=lambda m, p: (asked.append(p), "synthetic")[1])
    sh.decide_enabled = False
    sh.feed(mk_row(S0, pool="POOLbbbb", mint="MINTbbbb"), "trades")
    sh.decide_enabled = True
    run_stream(sh, 700)
    sh.feed(mk_row(slot_of_sec(700), pool="POOLbbbb", mint="MINTbbbb"), "trades")   # not its first print: never asked later either
    sh.finish("test")
    assert asked == [POOL] and "POOLbbbb" not in sh._cls


def test_restart_day_record_flags_the_restart_and_counts_bootstrap_pools():
    """Reviewer LOW on 0b679c4: pools first seen during the bootstrap are never classified, so the restart day's record undercounts. It now
    says so: restart_in_span and bootstrap_unclassified (pools of the resume day first seen in the bootstrap). Earlier days' bootstrap pools
    are not reported by this run; bootstrap pools are never classified later."""
    asked = []
    sh, sink = mk_shadow(only_minute(10), classifier=lambda m, p: (asked.append(p), "synthetic" if p in SYN_POOLS else "non_synthetic")[1])
    sh.decide_enabled = False                                      # the bootstrap
    sh.feed(mk_row(S0 - 100_000, pool="POOLxxxx", mint="MINTxxxx", block_time=BT0 - 13 * 3600), "trades")   # 2026-09-03
    sh.feed(mk_row(S0 - 10, pool="POOLyyyy", mint="MINTyyyy", block_time=BT0 - 4), "trades")                 # 2026-09-04
    sh.feed(mk_row(S0 - 9, pool="POOLzzzz", mint="MINTzzzz", block_time=BT0 - 4), "trades")                  # 2026-09-04
    sh.resume_after_bootstrap()
    assert sh.decide_enabled is True and sh.last_T == (BT0 - 4) // 60 * 60
    sh.feed(mk_row(S0, pool="POOLbbbb", mint="MINTbbbb"), "trades")
    run_stream(sh, 700)
    sh.feed(mk_row(slot_of_sec(700), pool="POOLyyyy", mint="MINTyyyy"), "trades")   # a bootstrap pool printing again: never asked
    sh.finish("test")
    assert sorted(asked) == sorted([POOL, "POOLbbbb"])
    (rec,) = sink.of(cs.SYNCLASS_TYPE)
    assert set(rec) == SYN_RECORD_KEYS
    assert (rec["day_first"], rec["day_last"], rec["restart_in_span"], rec["bootstrap_unclassified"]) == ("2026-09-04", "2026-09-04", True, 2)
    assert rec["universe_total"] == 2 and rec["counts"] == _counts(universe_synthetic=1, universe_non_synthetic=1)
    assert "2026-09-03" not in json.dumps(sink.records)            # the earlier day's bootstrap pool is not reported by this run


def test_restart_flag_stays_on_the_resume_days_span_only():
    sh, sink, run, _ = _midnight_shadow(classifier=lambda m, p: "synthetic" if p in SYN_POOLS else "non_synthetic")
    sh.decide_enabled = False
    run(300)                                                       # POOL's first print is in the bootstrap (2026-10-09)
    sh.resume_after_bootstrap()
    for i, pool in enumerate(("POOLcccc", "POOLdddd")):            # 10-09 after the resume: one synthetic, one not
        sh.feed(mk_row(S0 + 751 + i, pool=pool, mint="MINT" + pool[4:], block_time=OS_S - 660 + 301), "trades")
    _settle(sh)
    run(660 + 400)
    for i, pool in enumerate(("POOLbbbb", "POOLeeee")):            # 10-10: one synthetic, one not
        sh.feed(mk_row(S0 + 2700 + i, pool=pool, mint="MINT" + pool[4:], block_time=OS_S + 420 + i), "trades")
    sh.finish("test")
    recs = sink.of(cs.SYNCLASS_TYPE)
    assert [(r["day_first"], r["day_last"], r["restart_in_span"], r["bootstrap_unclassified"], r["partial"]) for r in recs] == [
        ("2026-10-09", "2026-10-09", True, 1, False), ("2026-10-10", "2026-10-10", False, 0, True)]
    assert all(r["counts"] == _counts(universe_synthetic=1, universe_non_synthetic=1) for r in recs)
    assert POOL not in sh._cls


def test_restart_day_with_only_bootstrap_pools_is_merged_into_the_next_day():
    sh, sink, run, _ = _midnight_shadow(classifier=lambda m, p: "synthetic" if p in SYN_POOLS else "non_synthetic")
    sh.decide_enabled = False
    run(300)
    sh.resume_after_bootstrap()                                    # 10-09: no pool after the resume, one bootstrap pool
    run(660 + 400)
    assert sink.of(cs.SYNCLASS_TYPE) == []                         # 0 classified on 10-09: degenerate, held back
    for i, pool in enumerate(("POOLbbbb", "POOLeeee")):
        sh.feed(mk_row(S0 + 2700 + i, pool=pool, mint="MINT" + pool[4:], block_time=OS_S + 420 + i), "trades")
    sh.finish("test")
    (rec,) = sink.of(cs.SYNCLASS_TYPE)
    assert (rec["day_first"], rec["day_last"], rec["restart_in_span"], rec["bootstrap_unclassified"]) == ("2026-10-09", "2026-10-10", True, 1)
    assert rec["counts"] == _counts(universe_synthetic=1, universe_non_synthetic=1)


def test_run_live_resumes_through_resume_after_bootstrap():
    import inspect
    src = inspect.getsource(cs.run_live)
    assert "sh.resume_after_bootstrap()" in src and "sh.decide_enabled = True" not in src


def test_cli_accepts_a_synthetic_classifier_spec():
    args = cs.build_parser().parse_args(["--synthetic-classifier", "tools.synthetic_class:nope"])
    assert args.synthetic_classifier == "tools.synthetic_class:nope"
    assert cs.build_parser().parse_args([]).synthetic_classifier is None


# ---- DEC-026 section 6: the paper twin at the canary's stake ---------------------------------------------------------------------------------
def test_each_bound_carries_a_twin_at_the_canary_stake():
    sh, sink = mk_shadow(only_minute(10))
    run_stream(sh, 1000)
    (o,) = sink.of("c1nf_outcome")
    assert o["stake_lamports"] == 250_000_000 and cs.CANARY_STAKE_LAMPORTS == 50_000_000
    for k, leg in o["legs"].items():
        for bound in ("end", "worst"):
            c = leg[bound]["canary"]
            assert c["stake_lamports"] == 50_000_000
            if not c["guarded"]:
                assert c["pnl_505k"] == pytest.approx(c["proceeds"] - 50_000_000 - 2 * 505_000)
                assert c["pnl_505k_rent"] == pytest.approx(c["pnl_505k"] - 2 * cs.RENT_LAMPORTS)
                assert c["gross_ret"] == pytest.approx(c["proceeds"] / 50_000_000 - 1)
            # a smaller buy moves the pool less: the twin's gross return is not worse than the 0.25 SOL cell's
            assert c["gross_ret"] >= leg[bound]["gross_ret"] - 1e-12
            assert c["guard_ref"] == "pick_state" and c["guarded"] == c["guarded_at_batch_spot"]   # the two references agree here
    (pick,) = sink.of("c1nf_pick")
    assert o["pick_spot"] == pytest.approx(pick["q_lamports"] / pick["base_reserve"])


def test_twin_guards_on_the_pick_state_and_records_where_the_batch_spot_disagrees():
    """The batch's spot (PRE state of the SD print) is half the quote; the pick's state (post of SD-1) and the landing are not. The 0.25 SOL
    cell stays on the batch spot and is guarded; the 0.05 SOL twin guards on the pick's state, as the executor will, and fills."""
    sh, sink = mk_shadow(only_minute(10))
    for slot in range(S0, slot_of_sec(1000)):
        row = mk_row(slot, side="buy" if slot % 2 else "sell")
        if slot == SD10:
            row["quote_reserve"] = row["quote_reserve"] / 2
        sh.feed(row, "trades")
    sh.finish("test")
    (pick,), (o,) = sink.of("c1nf_pick"), sink.of("c1nf_outcome")
    assert o["spot"] < 0.6 * o["pick_spot"] and o["pick_spot"] == pytest.approx(pick["q_lamports"] / pick["base_reserve"])
    end = o["legs"]["1.3"]["end"]
    assert end["guarded"] is True                                  # the deciding 0.25 SOL cell: batch spot, unchanged
    assert end["canary"]["guarded"] is False and end["canary"]["guarded_at_batch_spot"] is True
    assert end["canary"]["pnl_505k"] == pytest.approx(end["canary"]["proceeds"] - 50_000_000 - 2 * 505_000)


# ---- memory Build-A (job #608): ledger memo size, one cached ledger day, malloc_trim, heartbeat memory fields ------------------------------
def test_pread_snapshot_default_cache_is_200k_and_lookups_are_equal_across_cache_sizes(tmp_path):
    from tools import c1nf_wallet_ledger as L

    assert cs.LEDGER_CACHE_MAX == 200_000
    root = _fixture_root(tmp_path, np.arange(10, 6000, 3, dtype=np.uint64))
    led = L.AsofLedger.open_for_day(root, FIX_DAY)
    default = cs._PreadSnapshot.from_ledger(led, int)
    assert default._cache_max == cs.LEDGER_CACHE_MAX
    assert cs.AsofDirLedger(root, hash_fn=int).snapshot_for_day(FIX_DAY)._cache_max == cs.LEDGER_CACHE_MAX     # the live path's snapshot
    rng = np.random.default_rng(5)
    probes = [str(int(x)) for x in rng.integers(0, 6100, 3000)]                 # repeats; known (1 in 3) and unknown wallets
    ref = [_bits(cs._AsofSnapshot(led, int).get(p)) for p in probes]
    assert sum(r is not None for r in ref) > 500
    for snap in [default] + [cs._PreadSnapshot.from_ledger(led, int, cache_max=m) for m in (0, 1, 3, 500_000, 10**9)]:
        assert [_bits(snap.get(p)) for p in probes] == ref                       # a pure memo: same values, bit for bit, at any size
        assert len(snap._cache) <= snap._cache_max + 1
        snap.close()


def test_build_engine_keeps_one_ledger_day_and_the_engine_default_stays_two():
    from tools import c1nf_features as cf

    assert cs.LIVE_LEDGER_KEEP_DAYS == 1
    for mode in (cf.V_EVENT, cf.V_CONST):
        assert cs.build_engine(None, v_source=mode).ledger_keep_days == 1        # run_live and c1nf_vmode_parity
    assert cf.FeatureEngine(v_source=cf.V_CONST).ledger_keep_days == 2           # c1nf_parity builds the engine itself: unchanged


def test_malloc_trim_is_guarded(monkeypatch):
    import ctypes

    assert cs.malloc_trim() in (True, False)                                     # this host (glibc: True); never raises

    def boom(_n):
        raise OSError("trim failed")

    monkeypatch.setattr(cs, "_MALLOC_TRIM", boom)
    assert cs.malloc_trim() is False
    monkeypatch.setattr(cs, "_MALLOC_TRIM", False)
    assert cs.malloc_trim() is False
    monkeypatch.setattr(cs, "_MALLOC_TRIM", None)                                # a host without libc: looked up once, then False

    def no_libc(*_a, **_k):
        raise OSError("no libc")

    monkeypatch.setattr(ctypes, "CDLL", no_libc)
    assert cs.malloc_trim() is False and cs._MALLOC_TRIM is False


def test_stream_expire_trims_after_the_engine_expire_and_a_failing_trim_never_stops_it(monkeypatch):
    sh, _ = mk_shadow(only_minute(10), stream_expire_s=600)
    calls = []
    sh.engine.expire = lambda now_bt: calls.append(("expire", now_bt)) or 0
    monkeypatch.setattr(cs, "malloc_trim", lambda: calls.append(("trim",)) or True)
    sh._expire(BT0 + 600)
    assert calls == [("expire", BT0 + 600), ("trim",)] and sh.c["malloc_trim"] == 1
    monkeypatch.setattr(cs, "malloc_trim", lambda: False)
    sh._expire(BT0 + 1200)
    assert sh.c["malloc_trim_unavailable"] == 1

    def boom():
        raise RuntimeError("trim")

    monkeypatch.setattr(cs, "malloc_trim", boom)
    sh._expire(BT0 + 1800)
    assert sh.c["malloc_trim_unavailable"] == 2 and sh.c["stream_expires"] == 3 and sh.c["expire_errors"] == 0
    monkeypatch.undo()
    sh2, _ = mk_shadow(only_minute(10), stream_expire_s=600)                     # through the stream clock, with the real trim
    run_stream(sh2, 1500)
    assert sh2.c["stream_expires"] >= 2 and sh2.c["malloc_trim"] + sh2.c["malloc_trim_unavailable"] == sh2.c["stream_expires"]


def test_proc_mem_kb_reads_rss_anon_and_vm_hwm(tmp_path):
    f = tmp_path / "status"
    f.write_text("Name:\tpython3\nVmPeak:\t 999 kB\nVmHWM:\t  123456 kB\nVmRSS:\t   100 kB\nRssAnon:\t   65432 kB\nRssFile:\t 7 kB\n")
    assert cs.proc_mem_kb(str(f)) == {"RssAnon": 65432, "VmHWM": 123456}
    assert cs.proc_mem_kb(str(tmp_path / "missing")) == {}
    (tmp_path / "bad").write_text("RssAnon:\t\n")
    assert cs.proc_mem_kb(str(tmp_path / "bad")) == {}


def test_heartbeat_carries_memory_fields():
    sh, sink = mk_shadow(only_minute(10))                                          # stub engine: no held_sizes, no stats
    hb = sh.heartbeat()
    assert hb["held_prints"] is None and hb["held_pairs"] is None and hb["window_violation"] == 0
    rec = sink.of("c1nf_heartbeat")[-1]
    assert {k: v for k, v in rec.items() if k not in cs.PICK_ENVELOPE} == hb and json.loads(json.dumps(rec)) == rec
    assert {"rss_anon_kb", "vm_hwm_kb", "held_prints", "held_pairs", "window_violation"} <= set(rec)
    if os.path.exists("/proc/self/status"):
        assert isinstance(hb["rss_anon_kb"], int) and hb["rss_anon_kb"] > 0 and hb["vm_hwm_kb"] >= hb["rss_anon_kb"]
    real = _real_shadow("const")
    eng = real.engine
    eng.set_pool_v(POOL, V0)
    eng.on_graduation(MINT, BT0)
    for i, w in enumerate(("w1", "w2", "w1")):
        eng.on_trade("pumpswap", MINT, w, True, 1e9, 1e12, Q0 + i * 1e9, B0, POOL, S0 + i, BT0 + 1 + i)
    hb = real.heartbeat()
    assert (hb["held_prints"], hb["held_pairs"], hb["window_violation"]) == (3, 2, 0)
    eng.stats["window_violation"] += 2                                             # the counter Build-B adds; read as is
    assert real.heartbeat()["window_violation"] == 2
