"""tools/arena_tieout_ledger.py (executor ledger -> tie-out LEDGER_IN) and the executor's arena_send rows. Fixtures only: no RPC, no key, nothing sent."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools import arena_tieout as T
from tools import arena_tieout_ledger as L
from tools import h5_arena_executor as ax
from tools.test_arena_tieout import NAMED, TOK0, bal, good_day
from tools.test_h5_arena_exec_wiring import LiveCase
from tools.test_h5_executor import MINT

M1, M2, M3 = "MintOne", "MintTwo", "MintThree"


def send(sig, side, lv=100):
    return {"mode": "live", "kind": "arena_send", "mint": M1, "signature": sig, "side": side, "last_valid_block_height": lv}


def good_rows():
    """The good_day chain of tools/test_arena_tieout.py as the executor would ledger it: buy1 + sell1 (one trip), fail1 (a buy that failed on chain),
    drop1 (a buy whose blockhash expired)."""
    txs, _ledger, _tok, now = good_day()
    d = {t["signature"]: t["post_balance"] - t["pre_balance"] for t in txs}
    rows = [send("buy1", "buy"), {"mode": "live", "kind": "buy", "mint": M1, "signature": "buy1", "landed": True, "sol_spent_lamports": -d["buy1"], "tokens_received": 10},
            send("sell1", "sell"), {"mode": "live", "kind": "sell", "mint": M1, "signature": "sell1", "landed": True, "sell_net_lamports": d["sell1"], "buy_cost_lamports": -d["buy1"]},
            send("fail1", "buy"), {"mode": "live", "kind": "buy", "mint": M2, "signature": "fail1", "landed": False, "fail_class": "slippage", "err": {"x": 1}},
            send("drop1", "buy"), {"mode": "live", "kind": "buy", "mint": M3, "signature": "drop1", "landed": False, "fail_class": "expired"},
            {"mode": "live", "kind": "decision", "mint": M1}]
    return rows, txs, now


def test_a_clean_day_exports_and_ties_out():
    rows, txs, now = good_rows()
    led = L.export(rows, {"open": {}})
    st = {s["signature"]: s["status"] for s in led["sends"]}
    assert st == {"buy1": "landed", "sell1": "landed", "fail1": "landed", "drop1": "expired"}
    assert led["trips"] == [{"trip_id": M1, "buy_sig": "buy1", "sell_sig": "sell1", "closed": True,
                             "realized_lamports": (txs[1]["post_balance"] - txs[1]["pre_balance"]) + (txs[2]["post_balance"] - txs[2]["pre_balance"])}]
    assert led["open_mints"] == []
    for s in led["sends"]:  # what the fetcher adds to a kept send that is not on chain
        if s["signature"] == "drop1":
            s["status_check"] = {"search_transaction_history": True, "result": None}
    r = T.reconcile(txs, led, TOK0, 0, bal(now), NAMED)
    assert r["signed_off"], r["problems"]
    assert r["confirmed_drops"] == 1


def test_an_expired_send_without_its_height_stays_pending_in_the_tie_out():
    rows, txs, now = good_rows()
    rows[6]["last_valid_block_height"] = None
    led = L.export(rows, {"open": {}})
    for s in led["sends"]:
        if s["signature"] == "drop1":
            assert s["last_valid_block_height"] is None
            s["status_check"] = {"search_transaction_history": True, "result": None}
    assert not T.reconcile(txs, led, TOK0, 0, bal(now), NAMED)["signed_off"]


def test_superseded_and_unresolved_sells():
    rows = [send("b", "buy"), {"mode": "live", "kind": "buy", "mint": M1, "signature": "b", "landed": True, "sol_spent_lamports": 50, "tokens_received": 7},
            send("s1", "sell"), send("s2", "sell"), send("s3", "sell"), send("s4", "sell"),
            {"mode": "live", "kind": "sell_superseded_failed", "mint": M1, "signature": "s1"},
            {"mode": "live", "kind": "sell_prior_unresolved", "mint": M1, "signature": "s2"}]
    led = L.export(rows, {"open": {M1: {}}})
    st = {s["signature"]: s["status"] for s in led["sends"]}
    assert st == {"b": "landed", "s1": "landed", "s2": "dropped", "s3": "pending", "s4": "pending"}
    assert led["trips"] == [{"trip_id": M1, "buy_sig": "b", "sell_sig": None, "closed": False, "realized_lamports": None}]
    assert led["open_mints"] == [M1]


def test_manual_close_and_zero_token_buy():
    rows = [send("b", "buy"), {"mode": "live", "kind": "buy", "mint": M1, "signature": "b", "landed": True, "sol_spent_lamports": 50, "tokens_received": 7},
            {"mode": "live", "kind": "manual_close", "mint": M1, "signature": "mc", "sol_delta_lamports": 40, "buy_cost_lamports": 50},
            send("z", "buy"), {"mode": "live", "kind": "buy", "mint": M2, "signature": "z", "landed": True, "sol_spent_lamports": 9, "tokens_received": 0}]
    led = L.export(rows, {"open": {}})
    trips = {t["trip_id"]: t for t in led["trips"]}
    assert trips[M1] == {"trip_id": M1, "buy_sig": "b", "sell_sig": "mc", "closed": True, "realized_lamports": -10}
    assert trips[M2]["closed"] and trips[M2]["sell_sig"] is None and trips[M2]["realized_lamports"] == -9  # the tie-out halts on it (closed_trip_signature_missing)
    assert {s["signature"]: s["status"] for s in led["sends"]}["mc"] == "landed"


@pytest.mark.parametrize("mutate, match", [
    (lambda r: r.append({"mode": "dryrun", "kind": "decision"}), "only the live ledger"),
    (lambda r: r.append({"kind": "decision"}), "only the live ledger"),
    (lambda r: r[1].update(landed="true"), "must be a bool"),
    (lambda r: r[1].update(sol_spent_lamports=1.5), "expected an integer"),
    (lambda r: r[3].update(sell_net_lamports=None), "expected an integer"),
    (lambda r: r.pop(0), "no arena_send row"),
    (lambda r: r.append(dict(r[1])), "two landed buys"),
    (lambda r: r.append(dict(r[3])), "two closing rows"),
    (lambda r: r.append({"mode": "live", "kind": "sell", "mint": "Other", "signature": "sell1", "landed": True, "sell_net_lamports": 1, "buy_cost_lamports": 1}), "without a landed buy"),
    (lambda r: r.append({"mode": "live", "kind": "arena_send", "mint": M1, "signature": "x", "side": "swap"}), "side buy|sell"),
])
def test_refusals(mutate, match):
    rows, _txs, _now = good_rows()
    mutate(rows)
    with pytest.raises(L.BadLedger, match=match):
        L.export(rows, {"open": {}})


def test_a_buy_neither_open_nor_closed_and_a_bad_state_are_refused():
    rows = [send("b", "buy"), {"mode": "live", "kind": "buy", "mint": M1, "signature": "b", "landed": True, "sol_spent_lamports": 50, "tokens_received": 7}]
    with pytest.raises(L.BadLedger, match="neither open"):
        L.export(rows, {"open": {}})
    with pytest.raises(L.BadLedger, match="no 'open'"):
        L.export(rows, {"pending": {}})


def test_cli_writes_and_refuses(tmp_path, capsys):
    rows, _txs, _now = good_rows()
    led, st, out = tmp_path / "h5-ledger.jsonl", tmp_path / "state-live.json", tmp_path / "LEDGER_IN.json"
    led.write_text("".join(json.dumps(r) + "\n" for r in rows))
    st.write_text(json.dumps({"open": {}}))
    assert L.main(["--ledger", str(led), "--state", str(st), "--out", str(out)]) == 0
    got = json.loads(out.read_text())
    assert set(got) >= {"sends", "trips", "open_mints"}
    led.write_text("not json\n")
    before = out.read_text()
    assert L.main(["--ledger", str(led), "--state", str(st), "--out", str(out)]) == 2
    assert out.read_text() == before  # a refusal writes nothing
    assert "REFUSED" in capsys.readouterr().err
    st.write_text("{")
    led.write_text("")
    assert L.main(["--ledger", str(led), "--state", str(st), "--out", str(out)]) == 2


class TestExecutorSendRows(LiveCase):
    def sends(self, e):
        return [r for r in e.ledger() if r.get("kind") == "arena_send"]

    def test_a_live_buy_writes_one_send_row_with_its_height_and_exports(self):
        e = self.live()
        e.fire()
        rows = self.sends(e)
        assert len(rows) == 1
        p = e.ex.state.pending[MINT]
        assert rows[0]["signature"] == p["signature"] and rows[0]["side"] == "buy" and rows[0]["last_valid_block_height"] == p["lvbh"]
        e.ex._send(p, MINT)  # a rebroadcast of the same signature
        assert len(self.sends(e)) == 1
        e.land_buy()
        led = L.export(e.ledger(), {"open": dict(e.ex.state.open)})
        assert [s["status"] for s in led["sends"]] == ["landed"]
        assert led["trips"][0]["closed"] is False and led["trips"][0]["buy_sig"] == rows[0]["signature"]

    def test_a_stale_buy_that_is_never_sent_writes_no_send_row(self):
        e = self.live()
        orig = e.ex._signal_age_limit_ms
        e.ex._signal_age_limit_ms = lambda: -1  # every signal is stale at the write
        e.fire()
        e.ex._signal_age_limit_ms = orig
        assert self.sends(e) == []

    def test_dry_run_writes_no_send_rows(self):
        e = self.env()
        e.fire()
        assert self.sends(e) == []


def test_the_send_log_is_part_of_the_c0_tripwire():
    assert "_note_send" in ax.GUARD_METHODS and "_note_send" in ax.ArenaExecutor.__dict__
    assert ax.c0_guard_problem() is None


def test_a_landed_false_row_without_fail_class_exports_as_landed():
    """QP #843 condition 2 (declared in the DEC copy item 15): a landed:false row whose fail_class is not "expired" (also none at all, e.g. an RPC
    rejection that never reached chain) exports as landed. The tie-out then halts on landed_send_missing_on_chain: a false hard halt, never a false sign-off."""
    rows = [send("x", "buy"), {"mode": "live", "kind": "buy", "mint": M1, "signature": "x", "landed": False}]
    led = L.export(rows, {"open": {}})
    assert led["sends"][0]["status"] == "landed"
    r = T.reconcile([], led, TOK0, 0, bal(0), NAMED)
    assert not r["signed_off"] and any(p.get("kind", p.get("name")) == "landed_send_missing_on_chain" or "landed_send_missing_on_chain" in json.dumps(p) for p in r["problems"])
