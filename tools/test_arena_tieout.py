"""arena_tieout: DEC-032 section 6 reconciliation on JSON fixtures. No RPC, no key, no network."""

from __future__ import annotations

import copy
import json

import pytest

from tools import arena_tieout as T

OWNER, HELM, STRANGER = "OwnerPub", "HelmPub", "StrangerPub"
NAMED = {"deposit_from": [OWNER], "withdraw_to": [OWNER, HELM]}
BASE = 0


def mk(sig, slot, pre, kind, **kw):
    fee, prio = kw.pop("fee", 5_000), kw.pop("prio", 155_000)
    status = kw.get("status", "ok")
    if kind == "transfer":
        post = pre + kw.pop("amount")
        fee = prio = 0 if kw.get("cp_in") else fee
        kw.pop("cp_in", None)
        post_fix = post
    else:
        paid, rent_p = kw.get("sol_paid", 0), kw.get("rent_paid", 0)
        got, rent_r = kw.get("sol_received", 0), kw.get("rent_refunded", 0)
        post_fix = pre - fee - prio - paid - rent_p + got + rent_r
        if status == "failed":
            post_fix = pre - fee - prio
    return {"signature": sig, "slot": slot, "status": status, "pre_balance": pre, "post_balance": post_fix, "fee_lamports": fee, "priority_fee_lamports": prio, "kind": kind, **kw}


def good_day():
    """deposit 350M; buy 50M (+2.04M rent); sell 51M (+2.04M refunded); a failed tx; a closed trip."""
    t1 = mk("dep", 1, 0, "transfer", amount=350_000_000, counterparty=OWNER, cp_in=True)
    t2 = mk("buy1", 2, t1["post_balance"], "buy", sol_paid=50_000_000, rent_paid=2_039_280)
    t3 = mk("sell1", 3, t2["post_balance"], "sell", sol_received=51_000_000, rent_refunded=2_039_280)
    t4 = mk("fail1", 4, t3["post_balance"], "buy", status="failed")
    txs = [t1, t2, t3, t4]
    chain = (t2["post_balance"] - t2["pre_balance"]) + (t3["post_balance"] - t3["pre_balance"])
    ledger = {"sends": [{"signature": "buy1", "side": "buy", "status": "landed"}, {"signature": "sell1", "side": "sell", "status": "landed"},
                        {"signature": "fail1", "side": "buy", "status": "landed"}, {"signature": "drop1", "side": "buy", "status": "dropped"}],
              "trips": [{"trip_id": "t1", "buy_sig": "buy1", "sell_sig": "sell1", "closed": True, "realized_lamports": chain}], "open_mints": []}
    return txs, ledger, [], t4["post_balance"]


def run(txs, ledger, tokens, now, baseline=BASE, named=NAMED):
    return T.reconcile(txs, ledger, tokens, baseline, now, named)


def kinds(r):
    return sorted(p["kind"] for p in r["problems"])


def test_a_clean_day_ties_to_the_lamport_and_is_signed_off():
    txs, led, tok, now = good_day()
    r = run(txs, led, tok, now)
    assert r["signed_off"] and not r["hard_halt"] and r["residual"] == 0 and r["next_baseline"] == now and r["confirmed_drops"] == 1
    s = r["split"]
    assert s["deposits"] == 350_000_000 and s["buys"] == 50_000_000 and s["sells"] == 51_000_000 and s["ata_rent_paid"] == s["ata_rent_refunded"] == 2_039_280
    assert s["failed_tx_fees"] == 160_000 and s["base_fees"] == 10_000 and s["priority_fees"] == 310_000
    assert r["label"] == "unpromoted execution measurement" and r["trip_diffs"][0]["diff"] == 0


def test_residual_is_a_hard_halt():
    txs, led, tok, now = good_day()
    r = run(txs, led, tok, now + 1)
    assert r["residual"] == 1 and "tie_residual" in kinds(r) and r["hard_halt"] and not r["signed_off"] and r["next_baseline"] is None


def test_baseline_must_equal_the_first_tx_pre_balance_and_the_chain_must_be_continuous():
    txs, led, tok, now = good_day()
    assert "first_tx_pre_balance" in kinds(run(txs, led, tok, now, baseline=7)) 
    broken = copy.deepcopy(txs)
    broken[2]["pre_balance"] += 10
    broken[2]["post_balance"] += 10
    assert "balance_chain_break" in kinds(run(broken, led, tok, now + 10))


def test_next_day_starts_from_the_signed_off_balance():
    txs, led, tok, now = good_day()
    day1 = run(txs, led, tok, now)
    t = mk("buy2", 10, now, "buy", sol_paid=1_000, rent_paid=0)
    led2 = {"sends": [{"signature": "buy2", "side": "buy", "status": "landed"}], "trips": [], "open_mints": ["M"]}
    r = run([t], led2, [{"mint": "M", "token_account": "A", "amount": 5}], t["post_balance"], baseline=day1["next_baseline"])
    assert r["signed_off"] and r["open_atas"][0]["open_position"]


def test_every_ledger_send_must_be_on_chain_and_every_chain_tx_in_the_ledger():
    txs, led, tok, now = good_day()
    l2 = copy.deepcopy(led)
    l2["sends"].append({"signature": "ghost", "side": "buy", "status": "landed"})
    assert kinds(run(txs, l2, tok, now)) == ["landed_send_missing_on_chain"]
    l3 = copy.deepcopy(led)
    l3["sends"] = [s for s in l3["sends"] if s["signature"] != "fail1"]
    assert kinds(run(txs, l3, tok, now)) == ["chain_tx_not_in_ledger"]
    l4 = copy.deepcopy(led)
    l4["sends"][3] = {"signature": "fail1", "side": "buy", "status": "dropped"}  # ledger says dropped, but it is on chain
    assert "dropped_send_is_on_chain" in kinds(run(txs, l4, tok, now))


def test_unnamed_transfers_are_unexplained_but_tiny_inflows_are_listed_dust():
    txs, led, tok, now = good_day()
    big = mk("odd", 5, now, "transfer", amount=5_000_000, counterparty=STRANGER, cp_in=True)
    r = run(txs + [big], led, tok, now + 5_000_000)
    assert kinds(r) == ["chain_tx_not_in_ledger", "unexplained_transfer"]
    out = mk("out", 5, now, "transfer", amount=-1_000_000, counterparty=STRANGER, cp_in=True)
    assert "unexplained_transfer" in kinds(run(txs + [out], led, tok, now - 1_000_000))
    dust = mk("dust", 5, now, "transfer", amount=1_000, counterparty=STRANGER, cp_in=True)
    r = run(txs + [dust], led, tok, now + 1_000)
    assert r["signed_off"] and r["dust_inflows"] == [{"signature": "dust", "lamports": 1_000, "counterparty": STRANGER}] and "DUST inflow 1000" in T.render(r)


def test_withdrawal_to_a_named_address_is_split_out():
    txs, led, tok, now = good_day()
    w = mk("wd", 5, now, "transfer", amount=-100_000_000, counterparty=HELM, cp_in=True)
    r = run(txs + [w], led, tok, now - 100_000_000)
    assert r["signed_off"] and r["split"]["withdrawals"] == 100_000_000


def test_a_stranded_token_account_is_a_p4_class_alert_and_a_hard_halt():
    txs, led, tok, now = good_day()
    r = run(txs, led, [{"mint": "M1", "token_account": "A1", "amount": "123"}, {"mint": "M2", "token_account": "A2", "amount": 0}], now)
    assert kinds(r) == ["stranded_position"] and r["problems"][0]["alert"] == "P4-class" and not r["signed_off"] and len(r["open_atas"]) == 1
    led2 = copy.deepcopy(led)
    led2["open_mints"] = ["M1"]
    assert run(txs, led2, [{"mint": "M1", "token_account": "A1", "amount": 123}], now)["signed_off"]


def test_trip_pnl_difference_above_one_base_fee_is_unexplained():
    txs, led, tok, now = good_day()
    l2 = copy.deepcopy(led)
    l2["trips"][0]["realized_lamports"] += 5_000
    assert run(txs, l2, tok, now)["signed_off"]
    l2["trips"][0]["realized_lamports"] += 1
    r = run(txs, l2, tok, now)
    assert kinds(r) == ["trip_pnl_unexplained"] and r["problems"][0]["diff"] == 5_001


def test_open_trips_are_not_compared_and_a_closed_trip_with_a_missing_sig_halts():
    txs, led, tok, now = good_day()
    l2 = copy.deepcopy(led)
    l2["trips"].append({"trip_id": "t2", "buy_sig": "x", "sell_sig": None, "closed": False, "realized_lamports": 0})
    assert run(txs, l2, tok, now)["signed_off"]
    l2["trips"][1]["closed"] = True
    assert "closed_trip_signature_missing" in kinds(run(txs, l2, tok, now))


def test_a_buy_whose_delta_is_not_explained_by_its_fields_and_a_failed_tx_that_cost_too_much():
    txs, led, tok, now = good_day()
    bad = copy.deepcopy(txs)
    bad[1]["sol_paid"] += 1  # fields no longer explain the delta
    assert "buy_delta_unexplained" in kinds(run(bad, led, tok, now))
    bad = copy.deepcopy(txs)
    bad[3]["post_balance"] -= 9
    assert "failed_tx_cost_mismatch" in kinds(run(bad, led, tok, now - 9))


def test_cli(tmp_path, capsys):
    txs, led, tok, now = good_day()
    files = {}
    for name, obj in (("txs", txs), ("ledger", led), ("tokens", tok), ("named", NAMED)):
        files[name] = tmp_path / f"{name}.json"
        files[name].write_text(json.dumps(obj))
    args = [f"--{k}={v}" for k, v in files.items()] + ["--baseline=0", f"--balance-now={now}", f"--json={tmp_path / 'o.json'}"]
    assert T.main(args) == 0 and "SIGNED OFF" in capsys.readouterr().out and json.loads((tmp_path / "o.json").read_text())["signed_off"]
    assert T.main(args[:-2] + [f"--balance-now={now + 1}"]) == 1 and "NOT SIGNED OFF" in capsys.readouterr().out
    files["txs"].write_text("{")
    assert T.main(args) == 2 and "UNUSABLE INPUT" in capsys.readouterr().err
