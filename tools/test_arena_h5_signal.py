"""arena_h5_signal: trigger facts only, whitelisted; sealed paths refused; no outcome is ever kept. No network, no key."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import arena_h5_signal as S


class FakeEngine:
    def __init__(self, emit, **kw):
        self.emit, self.kw, self.seen = emit, kw, []

    def on_create_pool(self, pool, mint, quote_mint, slot, recv_ms):
        self.seen.append(("create", pool))

    def on_trade(self, row):
        self.seen.append(("trade", row["pool"], row["venue"]))
        if row["signature"] == "trig":
            self.emit({"type": "outcome", "variant": "pv", "pool": row["pool"], "pnl": 1.0, "ladder": {"310": 1}})
            self.emit({"type": "pool", "pool": row["pool"], "min_q": 3.0})
            self.emit({"type": "trigger", "variant": "fv", "pool": row["pool"]})
            self.emit({"type": "trigger", "variant": "pv", "pool": row["pool"], "mint": "M", "s0": 5, "t_recv_ms": row["t_recv_ms"], "q_trigger_sol": 39.0,
                       "boost_spent_sol": 3.5, "exit_ladder_trigger_slots": {"330": 9}, "outcome_x": 1, "pnl_flat": 2, "real_quote_pre": 41 * 10 ** 9})

    def feed_restart(self, now, reason):
        self.seen.append(("restart", reason))


def trade(sig="a", pool="P", prog="pumpswap"):
    return {"type": "trade", "prog": prog, "pool": pool, "signature": sig, "t_recv_ms": 1000, "slot": 5}


def test_only_the_pv_trigger_is_kept_and_it_is_whitelisted():
    sig = S.H5Signal(engine_factory=FakeEngine)
    sig.feed({"type": "pool_create", "prog": "pumpswap", "pool": "P", "mint": "M", "quote_mint": "W", "slot": 1, "t_recv_ms": 1})
    out = sig.feed(trade("trig"))
    assert len(out) == 1 and out[0]["pool"] == "P" and out[0]["schema"] == S.SCHEMA
    assert set(out[0]) <= set(S.TRIGGER_FIELDS) | {"schema"}
    for k in ("exit_ladder_trigger_slots", "outcome_x", "pnl_flat"):
        assert k not in out[0]
    assert sig.dropped == {"outcome": 1, "pool": 1, "trigger": 1}  # counted by type; nothing else is kept
    assert sig.triggers == out and sig.feed(trade("later")) == []
    assert "outcome" not in json.dumps(sig.triggers)


def test_engine_is_built_with_the_arena_seals_and_never_the_look2_observation():
    sig = S.H5Signal(engine_factory=FakeEngine)
    assert sig.engine.kw["seal_start_ms"] is None and sig.engine.kw["h5_look2_observed"] is False and sig.engine.kw["classifier"] is None
    with pytest.raises(ValueError):
        S.H5Signal(engine_factory=FakeEngine, h5_look2_observed=True)


def test_only_pumpswap_rows_reach_the_engine_and_a_data_loss_gap_restarts_the_view():
    sig = S.H5Signal(engine_factory=FakeEngine)
    sig.feed(trade("x", prog="pumpfun"))
    sig.feed({"type": "gap", "prog": "pumpfun", "data_loss": True, "t_recv_ms": 5})
    sig.feed({"type": "gap", "prog": "pumpswap", "data_loss": False, "t_recv_ms": 6})
    sig.feed({"type": "gap", "prog": "pumpswap", "data_loss": True, "t_recv_ms": 7})
    sig.feed(trade("y"))
    assert sig.engine.seen == [("restart", "arena_gap"), ("trade", "P", "pumpswap")]


def test_whitelist_has_no_outcome_like_name_and_trigger_facts_refuses_other_types():
    for k in S.TRIGGER_FIELDS:
        assert not any(w in k for w in S._OUTCOME_WORDS), k
    with pytest.raises(ValueError):
        S.trigger_facts({"type": "outcome", "pool": "P"})


def test_safe_path_refuses_sealed_stores(tmp_path):
    for bad in ("/data/mal/blocks/forward-1016/x", "/data/mal/forward-walk/x", "/x/truth-2/y", "/x/fresh-0815/y", "/x/exp011-a", "/x/tip-tape-archive/z",
                "/var/lib/mal/sealed/q", "/x/shadow/out", "/x/canary/o", "/x/probe/o", "/x/meta-capture/m"):
        with pytest.raises(S.SealedPath):
            S.safe_path(bad)
    assert S.safe_path(tmp_path / "ok.jsonl") == tmp_path / "ok.jsonl"


def test_read_trigger_records_never_parses_an_outcome_line(tmp_path):
    f = tmp_path / "h5-arena.jsonl"
    f.write_bytes(b'{"type": "outcome", "pool": "P", "pnl": 5}\n{"type":"trigger","variant":"pv","pool":"P","q_trigger_sol":38.5,"pnl_leak":1}\n'
                  b'not json {"type": "trigger"\n{"type": "trigger", "variant": "fv", "pool": "Z"}\n{"type":"hb"}\n')
    got = list(S.read_trigger_records(f))
    assert [g["pool"] for g in got] == ["P"] and "pnl_leak" not in got[0]
    with pytest.raises(S.SealedPath):
        list(S.read_trigger_records("/x/forward-1/a.jsonl"))


def test_bst_card_is_before_the_card_instant_and_has_no_text():
    f = {"pool": "P", "t_recv_ms": 1000, "boost_spent_sol": 3.5, "q_trigger_sol": 39.0, "real_quote_pre": 41 * 10 ** 9}
    c = S.bst_card(f, as_of_ms=2000, as_of_slot=9, launch={"same_slot_supply": 0.1, "sniper_share": None})
    assert c["lane"] == "H" and c["id"] == "P" and c["x"] is None and c["f"]["real_quote_pre_sol"] == 41.0 and c["risk"]["sniper_share"] is None
    with pytest.raises(ValueError):
        S.bst_card(f, as_of_ms=1000, as_of_slot=9)


def test_real_engine_wiring_runs_without_a_classifier_and_emits_nothing():
    sig = S.H5Signal()  # the real tools.h5_shadow.Engine, no classifier: every trigger would be excluded
    sig.feed({"type": "pool_create", "prog": "pumpswap", "pool": "P", "mint": "M", "quote_mint": "W", "slot": 1, "t_recv_ms": 1})
    sig.feed({"type": "trade", "prog": "pumpswap", "venue": "pumpswap", "pool": "P", "mint": "M", "slot": 2, "t_recv_ms": 2, "signature": "s", "event_index": 0,
              "trader": "a", "side": "buy", "sol_lamports": 10 ** 9, "token_raw": 10 ** 9, "quote_reserve": 10 ** 9, "base_reserve": 10 ** 15,
              "virtual_quote_reserves": 17_584_505_288, "quote_mint": "W", "event_ts": 1})
    assert sig.triggers == []
