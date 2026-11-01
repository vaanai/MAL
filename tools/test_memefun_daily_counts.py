"""memefun_daily_counts: counting, the P36 share metric, the budget, the cursor and the fixed kill/promote text. Synthetic rows; no network."""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import memefun_daily_counts as dc


def row(t, sig, slot, **kw):
    return {"v": 1, "venue": "memefun", "type": t, "slot": slot, "t_recv_ms": 1_793_000_000_000 + slot, "signature": sig, "event_index": 0, **kw}


def create(sig, slot, mint, creator="C"):
    return row("create", sig, slot, mint=mint, bonding_curve="cv" + mint, creator=creator, ts=1000 + slot, ev_slot=slot, ix=["CreateToken"])


def trade(sig, slot, mint, side, trader, amt, **kw):
    return row("trade", sig, slot, mint=mint, side=side, trader=trader, sol_amount=amt, token_amount=1, ts=1000 + slot, ev_slot=slot, ix=["Buy"], **kw)


def test_count_rows():
    rows = [create("a", 1, "M1"), create("a", 1, "M1"),  # exact duplicate dropped
            create("b", 2, "M2", "D"),
            trade("c", 3, "M1", "buy", "X", 10), trade("d", 4, "M1", "sell", "X", 5),
            row("ix", "e", 5, ix=["Buy"], failed=True), row("ix", "f", 6, ix=["Swap"]),
            row("claim", "g", 7, mint="M1", user="U", amount=1, ts=1, ix=["ClaimCreatorFees"]),
            row("event_undecoded", "h", 8, reason="x"),
            {"type": "gap", "t_recv_ms": 1_793_000_000_000}]
    c = dc.count_rows(rows)
    assert (c["creates"], c["buys"], c["sells"], c["claims"], c["swap_ix_rows"], c["failed_buy_ix_rows"], c["event_undecoded"]) == (2, 1, 1, 1, 1, 1, 1)
    assert c["duplicate_rows_dropped"] == 1 and c["gap_rows"] == 1 and c["distinct_mints_created"] == 2 and c["distinct_creators"] == 2


def test_read_day_skips_torn_lines(tmp_path):
    d = tmp_path / "memefun"
    d.mkdir()
    (d / "tape-2026-10-27T03.jsonl").write_text(json.dumps(create("a", 1, "M1")) + "\n{\"v\":1,\"ty")
    assert [r["signature"] for r in dc.read_day(tmp_path, "2026-10-27")] == ["a"]


def test_coverage_complete_needs_day_over_24_hours_no_gap():
    base = {"hours_with_rows": 24, "gap_rows": 0}
    assert dc.coverage("2026-10-27", base, datetime(2026, 10, 28, 0, 40, tzinfo=timezone.utc))["complete"]
    assert not dc.coverage("2026-10-27", base, datetime(2026, 10, 27, 23, 0, tzinfo=timezone.utc))["complete"]
    assert not dc.coverage("2026-10-27", {**base, "hours_with_rows": 23}, datetime(2026, 10, 28, tzinfo=timezone.utc))["complete"]
    assert not dc.coverage("2026-10-27", {**base, "gap_rows": 1}, datetime(2026, 10, 28, tzinfo=timezone.utc))["complete"]


def test_summarize_buys_excludes_launcher_and_weights():
    buys = [{"slot_off": 0, "amt": 100, "trader": "C", "w": 1.0},      # creator dev buy
            {"slot_off": 1, "amt": 50, "trader": "A", "w": 1.0},       # other, slot 0-1
            {"slot_off": 5, "amt": 25, "trader": "B", "w": 2.0},       # other, later, weight 2 -> 50
            {"slot_off": 9, "amt": 50, "trader": "C", "w": 1.0}]       # creator later
    s = dc.summarize_buys(buys, {"C"})
    assert s["buy_amt_30s"] == 250 and s["buy_amt_slot01"] == 150
    assert s["buy_amt_30s_ex_launcher"] == 100 and s["buy_amt_slot01_ex_launcher"] == 50
    assert s["share_slot01_ex_launcher"] == 0.5 and s["wallets_slot01_ex_launcher"] == 1 and s["first_other_buy_slot"] == 1


def test_summarize_buys_zero_volume_is_none():
    s = dc.summarize_buys([{"slot_off": 0, "amt": 9, "trader": "C", "w": 1.0}], {"C"})
    assert s["share_slot01_ex_launcher"] is None and s["first_other_buy_slot"] is None


def test_tape_crosscheck_window_and_failed():
    l = {"mint": "M1", "creator": "C", "slot": 100, "ts": 1100}
    tr = {"M1": [trade("1", 100, "M1", "buy", "C", 7),
                 trade("2", 101, "M1", "buy", "A", 3),
                 trade("3", 125, "M1", "buy", "B", 6),
                 trade("4", 300, "M1", "buy", "Z", 99),          # ts 1300 > 1100 + 30
                 trade("5", 101, "M1", "buy", "F", 50, failed=True),
                 trade("6", 102, "M1", "sell", "S", 40)]}
    s = dc.tape_crosscheck(tr, l)
    assert s["buy_amt_30s_ex_launcher"] == 9 and s["buy_amt_slot01_ex_launcher"] == 3 and s["n_buys"] == 3


def test_cursor_after_is_strictly_after():
    idx = [(10, "a"), (12, "b"), (20, "c")]
    assert dc.cursor_after(idx, 12) == "c" and dc.cursor_after(idx, 20) is None and dc.cursor_after(idx, 0) == "a"


def test_pick_launches_seeded_and_capped():
    cr = [create(str(i), i, f"M{i}") for i in range(50)]
    a, b = dc.pick_launches(cr, 20, 5), dc.pick_launches(cr, 20, 5)
    assert a == b and len(a) == 20 and len({r["mint"] for r in a}) == 20
    assert len(dc.pick_launches(cr[:3], 20, 5)) == 3


class FakeResp:
    def __init__(self, d): self.d = json.dumps(d).encode()
    def read(self, *a): return self.d
    def __enter__(self): return self
    def __exit__(self, *a): return False


def make_rpc(tmp_path, budget, responses):
    it = iter(responses)

    def opener(req, timeout):
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return FakeResp(r)

    return dc.Rpc(tmp_path / "led.jsonl", budget=budget, spacing_s=0, opener=opener, sleep=lambda s: None)


def test_rpc_budget_counts_every_attempt_and_stops(tmp_path):
    rpc = make_rpc(tmp_path, 3, [Exception("HTTP 429"), {"result": 1}, {"result": 2}, {"result": 3}])
    assert rpc.call("getSlot", []) == 1 and rpc.used == 2  # a failed attempt costs a call
    assert rpc.call("getSlot", []) == 2 and rpc.used == 3
    with pytest.raises(RuntimeError):
        rpc.call("getSlot", [])
    assert sum(1 for _ in open(tmp_path / "led.jsonl")) == 3


def test_rpc_ledger_persists_across_instances(tmp_path):
    rpc = make_rpc(tmp_path, 5, [{"result": 1}, {"result": 2}])
    rpc.call("a", []); rpc.call("b", [])
    again = make_rpc(tmp_path, 5, [])
    assert again.used == 2 and again.remaining == 3


def test_rpc_refuses_keyed_url(tmp_path):
    with pytest.raises(SystemExit):
        dc.Rpc(tmp_path / "l", url="https://x.helius-rpc.com/?api-key=k")


def day(d, creates, complete=True, num=None, den=None):
    out = {"day": d, "counts": {"creates": creates}, "coverage": {"complete": complete}, "sample": {}}
    if num is not None:
        out["sample"] = {"rpc_pooled": {"pooled_share_slot01_ex_launcher": num / den}, "rpc_sums": {"slot01": num, "total": den}}
    return out


def test_rules_kill_on_five_low_days():
    days = [day(f"d{i}", 49 if i < 5 else 400) for i in range(7)]
    r = dc.evaluate_rules(days)
    assert r["status"] == "kill" and r["kill_creates_rule"] and len(r["days_below_50"]) == 5


def test_rules_four_low_days_is_not_kill():
    days = [day(f"d{i}", 10 if i < 4 else 400) for i in range(7)]
    assert dc.evaluate_rules(days)["status"] == "open"


def test_rules_partial_days_do_not_count():
    days = [day(f"d{i}", 1, complete=False) for i in range(7)]
    assert dc.evaluate_rules(days)["status"] == "open"


def test_rules_kill_on_share_at_30pct():
    days = [day("d0", 300, num=30, den=100)]
    assert dc.evaluate_rules(days)["status"] == "kill"
    assert dc.evaluate_rules([day("d0", 300, num=29, den=100)])["status"] == "open"


def test_rules_promote_needs_7_days_200_and_share_under_10pct():
    days = [day(f"d{i}", 200, num=9, den=100) for i in range(7)]
    assert dc.evaluate_rules(days)["status"] == "promote"
    assert dc.evaluate_rules(days[:6])["status"] == "open"
    days[3] = day("d3", 199, num=9, den=100)
    assert dc.evaluate_rules(days)["status"] == "open"
    assert dc.evaluate_rules([day(f"d{i}", 500, num=10, den=100) for i in range(7)])["status"] == "open"  # share exactly 10% is not < 10%


def test_run_day_no_rpc_end_to_end(tmp_path):
    d = tmp_path / "memefun"
    d.mkdir()
    rows = [create("c1", 100, "M1"), trade("t1", 101, "M1", "buy", "A", 3), trade("t2", 130, "M1", "buy", "B", 7)]
    for r in rows:
        r["t_recv_ms"] = 1_793_037_000_000  # 2026-10-26T17
    (d / "tape-2026-10-26T17.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    out = dc.run_day(tmp_path, "2026-10-26", 20, 1, 190, False, now=datetime(2026, 10, 27, 0, 40, tzinfo=timezone.utc))
    assert out["counts"]["creates"] == 1 and out["counts"]["buys"] == 2 and not out["coverage"]["complete"]
    assert out["sample"]["tape_pooled"]["pooled_share_slot01_ex_launcher"] == pytest.approx(0.3)
    json.dumps(out, allow_nan=False)
