"""tools/r4bt_c0_tieout.py: the r4bt-c0 daily wallet tie-out wrapper. Synthetic signatures and ledgers only: no RPC, no key, nothing sent."""

from __future__ import annotations

import copy
import json

import pytest

from tools import arena_tieout_fetch as F
from tools import r4bt_c0_tieout as W
from tools.test_arena_tieout import NAMED, TOK0, bal, tok
from tools.test_arena_tieout_ledger import M1, M2, M3, good_rows

WALLET = "6X6dsDsb46bbeciPaydE27Gg9iC4ySjrHxtsj8BV78g6"


def fills_of(rows):
    return [{**{k: v for k, v in r.items() if k != "mode"}, "last_valid_block_height": 100} for r in rows if r["kind"] in ("buy", "sell")]


def case():
    rows, txs, now = good_rows()
    fills = fills_of(rows)
    orders = [{"order_id": f"o{i}", "mint": m} for i, m in enumerate((M1, M2, M3))]
    attempts = [{"attempt_id": "a:o0", "trip_id": "o0", "landed": True}, {"attempt_id": "a:o1", "trip_id": None, "landed": False},
                {"attempt_id": "a:o2", "trip_id": None, "landed": False}]
    led = W.ledger_in(fills)
    for s in led["sends"]:  # what the fetcher adds to a kept send that is not on chain
        if s["signature"] == "drop1":
            s["status_check"] = {"search_transaction_history": True, "result": None}
    return fills, orders, attempts, led, txs, now


def ev(led, txs, now, fills, orders, attempts, **kw):
    return W.evaluate(txs, led, kw.pop("tokens", TOK0), kw.pop("balance", bal(now)), kw.pop("baseline", 0), kw.pop("named", NAMED), fills, orders, attempts, **kw)


def kinds_of(problems):
    return {p["kind"] for p in problems}


def test_clean_day_passes_with_counts():
    fills, orders, attempts, led, txs, now = case()
    r = ev(led, txs, now, fills, orders, attempts)
    assert r["verdict"] == "PASS", r["problems"]
    assert r["residual"] == 0 and r["signed_off"] and r["next_baseline"] == now
    assert r["counts"]["ledger_sends"] == 4 and r["counts"]["orders"] == 3 and r["counts"]["problems"] == 0
    assert "made money" not in json.dumps(r)


def test_residual_fails():
    fills, orders, attempts, led, txs, now = case()
    r = ev(led, txs, now, fills, orders, attempts, balance=bal(now + 1))
    assert r["verdict"] == "FAIL" and r["residual"] == 1
    assert "tie_residual" in kinds_of(r["problems"])


def test_ledger_send_missing_on_chain_and_chain_tx_missing_in_ledger():
    fills, orders, attempts, led, txs, now = case()
    r = ev(led, [t for t in txs if t["signature"] != "buy1"], now, fills, orders, attempts)
    assert r["verdict"] == "FAIL" and "landed_send_missing_on_chain" in kinds_of(r["problems"])
    led2 = copy.deepcopy(led)
    led2["sends"] = [s for s in led2["sends"] if s["signature"] != "fail1"]
    r = ev(led2, txs, now, fills, orders, attempts)
    assert r["verdict"] == "FAIL" and "chain_tx_not_in_ledger" in kinds_of(r["problems"])


def test_unnamed_deposit_fails():
    fills, orders, attempts, led, txs, now = case()
    assert "transfer" in {t.get("kind") for t in txs}  # the fixture day starts with the named deposit
    r = ev(led, txs, now, fills, orders, attempts, named={"deposit_from": ["SomeoneElse"], "withdraw_to": []})
    assert r["verdict"] == "FAIL" and "unexplained_transfer" in kinds_of(r["problems"])


def test_stranded_position_fails():
    fills, orders, attempts, led, txs, now = case()
    r = ev(led, txs, now, fills, orders, attempts, tokens=tok({"mint": "Stray", "token_account": "ta", "amount": "5"}))
    assert r["verdict"] == "FAIL" and r["counts"]["stranded"] == 1


def test_order_without_buy_fill_and_landed_buy_without_order():
    fills, orders, attempts, led, txs, now = case()
    r = ev(led, txs, now, fills, orders + [{"order_id": "o9", "mint": "Ghost"}], attempts)
    assert "order_without_buy_fill" in kinds_of(r["wrapper_problems"])
    r = ev(led, txs, now, fills, orders[1:], attempts)
    assert "landed_buy_without_order" in kinds_of(r["wrapper_problems"])


def test_attempts_landed_count_and_unknown_trip():
    fills, orders, attempts, led, txs, now = case()
    r = ev(led, txs, now, fills, orders, attempts[1:])
    assert "attempts_landed_count" in kinds_of(r["wrapper_problems"])
    bad = copy.deepcopy(attempts)
    bad[0]["trip_id"] = "nope"
    assert "attempt_trip_not_an_order" in kinds_of(ev(led, txs, now, fills, orders, bad)["wrapper_problems"])


def test_straddling_trips_refuse_signoff():
    fills, orders, attempts, led, txs, now = case()
    r = ev(led, txs, now, fills, orders, attempts, straddling=[{"trip_id": M1}])
    assert r["verdict"] == "FAIL" and "straddling_trips" in kinds_of(r["wrapper_problems"])


def test_named_shape_refused():
    for bad in ({}, {"deposit_from": "x", "withdraw_to": []}, [], {"deposit_from": [1], "withdraw_to": []}):
        with pytest.raises(W.Refused):
            W.check_named(bad)


def test_state_first_run_needs_baseline(tmp_path):
    with pytest.raises(W.Refused):
        W.load_state(tmp_path, None, None)
    assert W.load_state(tmp_path, 0, 0) == (0, 0, None)
    (tmp_path / W.STATE_NAME).write_text(json.dumps({"schema": W.STATE_SCHEMA, "day": "d", "balance_lamports": 7, "context_slot": 9}))
    assert W.load_state(tmp_path, None, None) == (7, 9, "d")
    (tmp_path / W.STATE_NAME).write_text("{}")
    with pytest.raises(W.Refused):
        W.load_state(tmp_path, None, None)


class FakeRpc:
    """An empty wallet holding `lamports`: no txs, no token accounts."""

    def __init__(self, lamports, slot=500):
        self.lamports, self.slot, self.calls = lamports, slot, {}

    def __call__(self, method, params):
        self.calls[method] = self.calls.get(method, 0) + 1
        ctx = {"slot": self.slot}
        if method == "getSignaturesForAddress":
            return []
        if method == "getBalance":
            return {"context": ctx, "value": self.lamports}
        if method == "getBlockHeight":
            return 1000
        if method == "getTokenAccountsByOwner":
            return {"context": ctx, "value": []}
        if method == "getSignatureStatuses":
            return {"context": ctx, "value": [None for _ in params[0]]}
        raise AssertionError(method)


def test_run_writes_result_once_and_state_only_on_pass(tmp_path):
    sd, od = tmp_path / "state", tmp_path / "out"
    sd.mkdir()
    od.mkdir()
    res = W.run(FakeRpc(1_000), WALLET, "2026-11-07", sd, od, [], [], [], NAMED, first_baseline=1_000, first_after_slot=100)
    assert res["verdict"] == "PASS" and res["counts"]["txs"] == 0
    st = json.loads((sd / W.STATE_NAME).read_text())
    assert st["balance_lamports"] == 1_000 and st["context_slot"] == 500
    with pytest.raises(W.Refused):  # never overwritten
        W.run(FakeRpc(1_000), WALLET, "2026-11-07", sd, od, [], [], [], NAMED)
    res = W.run(FakeRpc(1_001, 600), WALLET, "2026-11-08", sd, od, [], [], [], NAMED, stop_on_fail=True)  # +1 lamport with no tx
    assert res["verdict"] == "FAIL" and res["residual"] == 1
    assert json.loads((sd / W.STATE_NAME).read_text())["context_slot"] == 500  # unchanged
    assert (sd / "STOP-tieout").exists()


def test_main_exit_codes_and_pass_line(tmp_path, capsys, monkeypatch):
    sd, od = tmp_path / "s", tmp_path / "o"
    sd.mkdir()
    for n in ("fills", "orders", "attempts"):
        (tmp_path / f"{n}.jsonl").write_text("")
    (tmp_path / "named.json").write_text(json.dumps(NAMED))
    monkeypatch.setattr(F, "Rpc", lambda url, rps: FakeRpc(42))
    base = ["--wallet", WALLET, "--day", "2026-11-07", "--state-dir", str(sd), "--fills", str(tmp_path / "fills.jsonl"), "--order-ledger", str(tmp_path / "orders.jsonl"),
            "--attempts", str(tmp_path / "attempts.jsonl"), "--named", str(tmp_path / "named.json"), "--out-dir", str(od)]
    assert W.main(base) == 2  # first run without a baseline
    assert W.main(base + ["--first-baseline", "42", "--first-after-slot", "0"]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "r4bt-c0-tieout 2026-11-07 PASS"
    assert W.main(base + ["--rpc", "https://x.example/?api-key=1"]) == 2
    monkeypatch.setenv("EMPTY_RPC", "")
    assert W.main(base + ["--rpc-env", "EMPTY_RPC"]) == 2


def reentry_rows(second_sell):
    f = []
    for sig, kind, extra in (("b1", "buy", {"tokens_received": 10}), ("s1", "sell", {}), ("b2", "buy", {"tokens_received": 7})) + ((("s2", "sell", {}),) if second_sell else ()):
        f.append({"kind": kind, "mint": M1, "signature": sig, "landed": True, "last_valid_block_height": 100, "sol_spent_lamports": 100, "sell_net_lamports": 90,
                  "buy_cost_lamports": 100, **extra})
    return f


def test_reentry_open_and_closed_export_by_order():
    led = W.ledger_in(reentry_rows(False))
    assert [t["buy_sig"] for t in led["trips"]] == ["b1", "b2"]
    assert [t["closed"] for t in led["trips"]] == [True, False]
    assert led["trips"][0]["sell_sig"] == "s1" and led["open_mints"] == [M1]
    led = W.ledger_in(reentry_rows(True))
    assert [(t["buy_sig"], t["sell_sig"], t["closed"]) for t in led["trips"]] == [("b1", "s1", True), ("b2", "s2", True)]
    assert led["open_mints"] == []


def test_reentry_attempts_count_rows_not_mints():
    fills = reentry_rows(True)
    orders = [{"order_id": "o1", "mint": M1}, {"order_id": "o2", "mint": M1}]
    ok = [{"attempt_id": "a:o1", "trip_id": "o1", "landed": True}, {"attempt_id": "a:o2", "trip_id": "o2", "landed": True}]
    assert W.cross_checks(fills, orders, ok) == []
    assert "attempts_landed_count" in kinds_of(W.cross_checks(fills, orders, ok[:1]))
