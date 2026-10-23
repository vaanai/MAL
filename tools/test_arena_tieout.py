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
                        {"signature": "fail1", "side": "buy", "status": "landed"}, {"signature": "drop1", "side": "buy", "status": "dropped", "last_valid_block_height": 100,
                         "status_check": {"search_transaction_history": True, "result": None}}],
              "trips": [{"trip_id": "t1", "buy_sig": "buy1", "sell_sig": "sell1", "closed": True, "realized_lamports": chain}], "open_mints": []}
    return txs, ledger, TOK0, t4["post_balance"]


TOK0 = {"programs_covered": list(T.TOKEN_PROGRAMS), "accounts": []}
FIN = 200


def tok(*accounts):
    return {"programs_covered": list(T.TOKEN_PROGRAMS), "accounts": list(accounts)}


def bal(now, slot=10**6, **kw):
    return dict({"lamports": now, "context_slot": slot, "commitment": "finalized", "finalized_block_height": FIN}, **kw)


def run(txs, ledger, tokens, now, baseline=BASE, named=NAMED, balance=None):
    return T.reconcile(txs, ledger, tokens, baseline, balance or bal(now), named)


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
    txs, led, tok0, now = good_day()
    day1 = run(txs, led, tok0, now)
    t = mk("buy2", 10, now, "buy", sol_paid=1_000, rent_paid=0)
    led2 = {"sends": [{"signature": "buy2", "side": "buy", "status": "landed"}], "trips": [], "open_mints": ["M"]}
    r = run([t], led2, tok({"mint": "M", "token_account": "A", "amount": 5}), t["post_balance"], baseline=day1["next_baseline"])
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
    txs, led, tok0, now = good_day()
    r = run(txs, led, tok({"mint": "M1", "token_account": "A1", "amount": "123"}, {"mint": "M2", "token_account": "A2", "amount": 0}), now)
    assert kinds(r) == ["stranded_position", "zero_amount_ata_open"] and not r["signed_off"] and len(r["open_atas"]) == 1
    assert r["problems"][0]["alert"] == "P4-class"
    led2 = copy.deepcopy(led)
    led2["open_mints"] = ["M1"]
    assert run(txs, led2, tok({"mint": "M1", "token_account": "A1", "amount": 123}), now)["signed_off"]


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


def test_balance_comes_from_its_own_input_and_must_be_finalized_and_not_before_the_last_tx():
    txs, led, tok0, now = good_day()
    assert "tie_residual" in kinds(run(txs, led, tok0, now, balance=bal(now + 1)))  # an independent balance that disagrees is caught (a last-tx post-balance would never disagree)
    assert "balance_context_before_last_tx" in kinds(run(txs, led, tok0, now, balance=bal(now, slot=3)))
    for bad in (bal(now, commitment="confirmed"), {k: v for k, v in bal(now).items() if k != "finalized_block_height"}, {"lamports": now}):
        with pytest.raises(T.BadInput):
            run(txs, led, tok0, now, balance=bad)
    assert run(txs, led, tok0, now)["balance_context_slot"] == 10**6


def test_a_ledger_with_a_missing_key_is_refused_not_skipped():
    txs, led, tok0, now = good_day()
    for k in T.LEDGER_KEYS:
        l2 = {a: b for a, b in led.items() if a != k}
        with pytest.raises(T.BadInput, match="missing key"):
            run(txs, l2, tok0, now)
    with pytest.raises(T.BadInput):
        run(txs, dict(led, trips=None), tok0, now)


def test_tokens_must_declare_both_token_programs():
    txs, led, tok0, now = good_day()
    for bad in ({"accounts": []}, {"programs_covered": [T.TOKEN_PROGRAMS[0]], "accounts": []}, [], {"programs_covered": list(T.TOKEN_PROGRAMS)}):
        with pytest.raises(T.BadInput):
            run(txs, led, bad, now)


def test_an_open_mint_without_a_token_account_is_flagged_and_zero_amount_atas_are_listed():
    txs, led, tok0, now = good_day()
    l2 = dict(led, open_mints=["MX"])
    r = run(txs, l2, tok0, now)
    assert kinds(r) == ["open_mint_without_token_account"] and r["problems"][0]["mint"] == "MX"
    assert run(txs, l2, tok({"mint": "MX", "token_account": "AX", "amount": 7}), now)["signed_off"]
    r = run(txs, led, tok({"mint": "Z", "token_account": "AZ", "amount": 0}), now)
    assert kinds(r) == ["zero_amount_ata_open"] and r["zero_amount_atas"] == [{"mint": "Z", "token_account": "AZ"}] and r["problems"][0]["rent_leak"]


def test_a_missing_dropped_send_is_a_confirmed_drop_only_with_expiry_and_a_null_status():
    txs, led, tok0, now = good_day()
    assert run(txs, led, tok0, now)["confirmed_drops"] == 1
    def variant(**send):
        l2 = copy.deepcopy(led)
        l2["sends"][3] = dict(l2["sends"][3], **send)
        return run(txs, l2, tok0, now)
    r = variant(last_valid_block_height=FIN)       # not yet expired against the finalized height
    assert kinds(r) == ["send_pending"] and r["pending_sends"] == 1 and r["confirmed_drops"] == 0 and not r["signed_off"]
    r = variant(last_valid_block_height=FIN + 50)
    assert kinds(r) == ["send_pending"] and r["problems"][0]["blockhash_expired"] is False
    r = variant(status_check={"search_transaction_history": False, "result": None})  # status looked up without history
    assert kinds(r) == ["send_pending"] and r["problems"][0]["status_null_with_history"] is False
    r = variant(status_check={"search_transaction_history": True, "result": {"slot": 5}})  # a status exists
    assert kinds(r) == ["send_pending"]
    r = variant(status_check=None)
    assert kinds(r) == ["send_pending"]
    r = variant(last_valid_block_height=None)
    assert kinds(r) == ["send_pending"]
    r = variant(status="expired")
    assert r["confirmed_drops"] == 1 and r["signed_off"]


def _files(tmp_path, txs, led, tk, now, balance=None):
    files = {}
    for name, obj in (("txs", txs), ("ledger", led), ("tokens", tk), ("named", NAMED), ("balance", balance or bal(now))):
        files[name] = tmp_path / f"{name}.json"
        files[name].write_text(json.dumps(obj))
    return [f"--{k}={v}" for k, v in files.items()] + ["--baseline=0", f"--stop-file={tmp_path / 'STOP'}", f"--json={tmp_path / 'o.json'}"], files


def test_cli_clean_day_exits_0_and_makes_no_stop(tmp_path, capsys):
    txs, led, tk, now = good_day()
    args, _ = _files(tmp_path, txs, led, tk, now)
    assert T.main(args) == 0 and "SIGNED OFF" in capsys.readouterr().out and json.loads((tmp_path / "o.json").read_text())["signed_off"] and not (tmp_path / "STOP").exists()


def test_every_non_zero_exit_creates_stop(tmp_path, capsys):
    txs, led, tk, now = good_day()
    # exit 1: hard halt (residual)
    args, files = _files(tmp_path, txs, led, tk, now, balance=bal(now + 1))
    assert T.main(args) == 1 and "NOT SIGNED OFF" in capsys.readouterr().out
    body = json.loads((tmp_path / "STOP").read_text())
    assert body["schema"] == T.STOP_SCHEMA and body["exit_code"] == 1 and "tie_residual" in body["reason"] and body["hard"] is True
    first = (tmp_path / "STOP").read_text()
    # exit 2 on unusable input; the existing STOP is never overwritten
    files["txs"].write_text("{")
    assert T.main(args) == 2 and "UNUSABLE INPUT" in capsys.readouterr().err and (tmp_path / "STOP").read_text() == first
    # a ledger with a missing key is exit 2 and a STOP
    (tmp_path / "STOP").unlink()
    txs2 = tmp_path / "txs2.json"
    txs2.write_text(json.dumps(txs))
    files["txs"].write_text(json.dumps(txs))
    files["ledger"].write_text(json.dumps({k: v for k, v in led.items() if k != "trips"}))
    assert T.main(args) == 2 and (tmp_path / "STOP").exists() and "exit_code\": 2" in (tmp_path / "STOP").read_text()
    capsys.readouterr()


def test_a_crash_creates_stop_and_exits_3(tmp_path, capsys, monkeypatch):
    txs, led, tk, now = good_day()
    args, _ = _files(tmp_path, txs, led, tk, now)
    monkeypatch.setattr(T, "render", lambda r: 1 / 0)
    assert T.main(args) == 3 and "CRASH: ZeroDivisionError" in capsys.readouterr().err
    assert json.loads((tmp_path / "STOP").read_text())["exit_code"] == 3


def test_if_stop_cannot_be_written_it_fails_closed_with_exit_5(tmp_path, capsys):
    txs, led, tk, now = good_day()
    args, _ = _files(tmp_path, txs, led, tk, now, balance=bal(now + 1))
    args = [a if not a.startswith("--stop-file") else f"--stop-file={tmp_path / 'no_such_dir' / 'STOP'}" for a in args]
    assert T.main(args) == 5 and "FAIL CLOSED" in capsys.readouterr().err


def test_an_argument_error_also_stops_or_fails_closed(tmp_path, capsys):
    stop = tmp_path / "STOP"
    assert T.main([f"--stop-file={stop}"]) == 2 and stop.exists()  # required arguments missing
    stop.unlink()
    assert T.main([]) == 5  # no STOP path to write at all: fail closed
    capsys.readouterr()


# ------------------------------------------------------------------------------------------------------------------------------ QP re-check 2026-10-23 (B1, B2, no-result = STOP)
@pytest.mark.parametrize("status", ["pending", "submitted", "", None, 7])
def test_b1_a_send_with_an_unknown_status_that_is_not_on_chain_is_pending_not_signed_off(status):
    txs, led, tok0, now = good_day()
    l2 = copy.deepcopy(led)
    l2["sends"].append({"signature": "wal1", "side": "buy", "status": status})
    r = run(txs, l2, tok0, now)
    assert kinds(r) == ["send_pending"] and r["pending_sends"] == 1 and not r["signed_off"]
    assert r["problems"][0]["signature"] == "wal1"


def test_b1_an_unknown_status_send_that_is_on_chain_is_matched():
    txs, led, tok0, now = good_day()
    l2 = copy.deepcopy(led)
    l2["sends"][0]["status"] = "submitted"  # buy1 is on chain: matched by signature
    assert run(txs, l2, tok0, now)["signed_off"]


def test_b2_a_trip_without_a_boolean_closed_is_refused():
    txs, led, tok0, now = good_day()
    for bad in (None, "yes", 1, "missing"):
        l2 = copy.deepcopy(led)
        if bad == "missing":
            del l2["trips"][0]["closed"]
        else:
            l2["trips"][0]["closed"] = bad
        with pytest.raises(T.BadInput, match="closed"):
            run(txs, l2, tok0, now)
    l3 = copy.deepcopy(led)
    l3["trips"][0]["realized_lamports"] += 10**9  # the case QP reproduced: a 1e9 error with `closed` absent must not be signed off
    del l3["trips"][0]["closed"]
    with pytest.raises(T.BadInput):
        run(txs, l3, tok0, now)


def test_b2_through_the_cli_it_is_exit_2_and_stop(tmp_path, capsys):
    txs, led, tk, now = good_day()
    del led["trips"][0]["closed"]
    args, _ = _files(tmp_path, txs, led, tk, now)
    assert T.main(args) == 2 and (tmp_path / "STOP").exists()
    capsys.readouterr()


DAYMS = 86_400_000


def test_no_result_for_the_last_closed_utc_day_is_stop(tmp_path, capsys):
    now = 1_789_000_000_000 + 3 * 3_600_000
    day = T.last_closed_day(now)
    assert day == T.utc_day_str(now - (now % DAYMS) - DAYMS)
    d = tmp_path / "results"
    d.mkdir()
    assert T.check_last_closed_day(d, now)[0] is False                                            # no file
    (d / f"tieout-{day}.json").write_text("{")
    assert "unreadable" in T.check_last_closed_day(d, now)[1]                                       # torn
    (d / f"tieout-{day}.json").write_text(json.dumps({"day": day, "signed_off": False}))
    assert "not signed off" in T.check_last_closed_day(d, now)[1]
    (d / f"tieout-{day}.json").write_text(json.dumps({"day": "2000-01-01", "signed_off": True}))
    assert "day" in T.check_last_closed_day(d, now)[1]                                              # wrong day stamp
    (d / f"tieout-{day}.json").write_text(json.dumps({"day": day, "signed_off": True}))
    assert T.check_last_closed_day(d, now) == (True, "")
    stop = tmp_path / "STOP"
    base = ["--check-last-day", str(d), f"--now-ms={now}", f"--stop-file={stop}"]
    assert T.main(base) == 0 and not stop.exists()
    (d / f"tieout-{day}.json").unlink()
    assert T.main(base) == 1 and json.loads(stop.read_text())["exit_code"] == 1                      # no result = STOP
    stop.unlink()
    assert T.main(base[:2] + [f"--now-ms={now}", f"--stop-file={tmp_path / 'nodir' / 'STOP'}"]) == 5  # cannot write: fail closed
    capsys.readouterr()


def test_the_day_stamp_is_written_by_the_main_run(tmp_path, capsys):
    txs, led, tk, now = good_day()
    args, _ = _files(tmp_path, txs, led, tk, now)
    assert T.main(args + ["--day=2026-10-22"]) == 0
    assert json.loads((tmp_path / "o.json").read_text())["day"] == "2026-10-22"
    capsys.readouterr()
