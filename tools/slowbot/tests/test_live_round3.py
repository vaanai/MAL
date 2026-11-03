"""Round 3: wallet-wide STOP/HALT, sell-attempt cap, sha pins, landed-but-unbooked sells, loop survival, blockhash release. Fakes only."""
import json
import os

import pytest

from tools import h5_lane_executor as lane
from tools.slowbot import live_executor as lx
from tools.slowbot.tests.test_live_executor import MINT, MINT2, PSHA, RSHA, STAKE, mkcfg, mkexec, put_intent
from tools.slowbot.tests.test_live_sell_reconcile import TOK, evs, held


def touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")


# --- wallet-wide STOP / HALT ---------------------------------------------------------------------------------------------------------------
def test_wallet_constants_are_the_probe_executor_ones():
    from tools import probe_executor as pe
    import inspect
    src = inspect.getsource(lx)
    assert 'WALLET_STOP = pe_mod.LIVE_DIR / "STOP"' in src and 'WALLET_HALT = pe_mod.LIVE_DIR / "HALT"' in src
    assert str(pe.LIVE_DIR / "STOP") == "/var/lib/mal-live/STOP"


@pytest.mark.parametrize("which", ["WALLET_STOP", "WALLET_HALT"])
def test_wallet_stop_or_halt_refuses_buy_in_dry_and_live(tmp_path, which):
    for sub, dry in (("a", True), ("b", False)):
        ex, jup, signer, chain, clock = mkexec(tmp_path / sub, dry=dry)
        touch(getattr(lx, which))
        put_intent(tmp_path / sub)
        ex.process_intents()
        assert list(ex.st["done"].values()) == ["refused:wallet_stop"] and chain.sent == [] and signer.signed == []
        os.unlink(getattr(lx, which))


def test_wallet_stop_lets_exits_continue_but_halt_freezes_sells(tmp_path):
    ex, jup, signer, chain, clock = held(tmp_path)
    chain.confirm = True
    jup.sell_unit = 10.0
    touch(lx.WALLET_HALT)
    clock.t += 30
    ex.pm.tick()
    assert len(chain.send_log) == 1 and len(ex.pm.open_positions()) == 1  # HALT: no sell
    os.unlink(lx.WALLET_HALT)
    touch(lx.WALLET_STOP)
    clock.t += 30
    ex.pm.tick()
    assert ex.pm.open_positions() == [] and len(chain.send_log) == 2  # STOP: the exit still goes


# --- sell attempt cap ----------------------------------------------------------------------------------------------------------------------
def test_cap_formula_and_config_bounds(tmp_path):
    c = mkcfg(tmp_path, fee_reserve_lamports=4_200_000)
    assert c["max_sell_attempts"] == (4_200_000 - 2_500_000 - (5_000 + 200_000)) // (5_000 + 200_000) == 7
    assert mkcfg(tmp_path, fee_reserve_lamports=4_200_000, max_sell_attempts=3)["max_sell_attempts"] == 3
    with pytest.raises(ValueError):
        mkcfg(tmp_path, fee_reserve_lamports=4_200_000, max_sell_attempts=8)
    with pytest.raises(ValueError):
        mkcfg(tmp_path, fee_reserve_lamports=4_200_000, sell_priority_lamports=3_000_000)  # not even one attempt fits


def test_stuck_position_stops_after_cap_alerts_and_stops_entries(tmp_path):
    ex, jup, signer, chain, clock = held(tmp_path, max_sell_attempts=2)
    chain.confirm = True
    chain.fail_sigs.update({"SIG2", "SIG3"})
    jup.sell_unit = 10.0
    for _ in range(4):
        clock.t += 30
        ex.pm.tick()
    (p,) = ex.pm.open_positions()
    assert p["sell_stuck"] is True and p["sell_attempts"] == 2
    assert len(chain.send_log) == 3  # the buy and exactly 2 sell sends
    assert os.path.exists(ex.cfg["bot_stop_path"]) and os.path.exists(os.path.join(ex.cfg["state_dir"], "ALERT-sell_stuck"))
    put_intent(tmp_path, mint=MINT2, clock_t=clock.t)
    ex.process_intents()
    assert list(ex.st["done"].values())[-1] == "refused:bot_stop_file"
    assert lane.lane_fold(ex.ledger.read(), {}, 0, 60, "slowbot-h-1").open.keys() == {"slowbot-h-1:" + MINT}  # still open: tokens are still held


# --- sha pins ------------------------------------------------------------------------------------------------------------------------------
def test_pins_are_required():
    for bad in ({"pin_runner_sha": ""}, {"pin_prompt_sha256": ""}, {"pin_runner_sha": "unknown"}):
        with pytest.raises(ValueError):
            lx.parse_config({"intents_path": "i", "state_dir": "s", "ledger_path": "l", "stop_path": "x", "pin_runner_sha": "a", "pin_prompt_sha256": "b", **bad})


@pytest.mark.parametrize("field,val", [("runner_sha", "other"), ("prompt_sha256", "other"), ("runner_sha", None), ("prompt_sha256", None)])
def test_intent_with_wrong_or_missing_sha_refused_before_jupiter(tmp_path, field, val):
    ex, jup, signer, chain, clock = mkexec(tmp_path)
    row = put_intent(tmp_path)
    lines = [json.loads(x) for x in (tmp_path / "intents.jsonl").read_text().splitlines()]
    lines[0][field] = val
    (tmp_path / "intents.jsonl").write_text(json.dumps(lines[0]) + "\n")
    ex.process_intents()
    assert list(ex.st["done"].values()) == ["refused:sha_mismatch"] and jup.urls == [] and chain.sent == []
    logrow = [json.loads(x) for x in open(ex.log_path)][-1]
    assert logrow["intent_runner_sha"] == lines[0]["runner_sha"] and logrow["runner_sha"] == RSHA


def test_rows_and_log_carry_the_shas_and_bot_id_record_names_them(tmp_path):
    ex, jup, signer, chain, clock = mkexec(tmp_path)
    put_intent(tmp_path)
    ex.process_intents()
    clock.t += 30
    jup.sell_unit = 10.0
    ex.pm.tick()
    for r in ex.ledger.read():
        if r["ev"] in ("fill", "exit", "sell_intent"):
            assert r["runner_sha"] == RSHA and r["prompt_sha256"] == PSHA, r["ev"]
    assert all(json.loads(x)["runner_sha"] == RSHA for x in open(ex.log_path))
    rec = json.loads((tmp_path / "state" / "bot-id-record.json").read_text())
    assert rec == {"bot_id": "slowbot-h-1", "runner_sha": RSHA, "prompt_sha256": PSHA}
    with pytest.raises(ValueError):  # a code or prompt change is a new bot_id
        mkexec(tmp_path, clock=clock, pin_runner_sha="n" * 40)


# --- A: a confirmed sell whose transaction cannot be read ----------------------------------------------------------------------------------
def test_landed_sell_with_failing_tx_read_is_unbooked_not_zero_balance_closed(tmp_path):
    ex, jup, signer, chain, clock = held(tmp_path)
    chain.confirm = True
    chain.result_fails = True
    jup.sell_unit = 10.0  # the fake lands 1_000_000 tokens x 10 = 0.01 SOL
    clock.t += 30
    ex.pm.tick()
    (p,) = ex.pm.open_positions()
    assert p["sell_landed_unbooked"] is True and ex.st["sells"]["SIG2"]["landed_unbooked"] is True
    assert [r for r in ex.ledger.read() if r["ev"] == "exit"] == []
    clock.t += 30
    ex.pm.tick()  # the wallet balance is now 0, but it must NOT be taken as a zero-balance close: the sell is unresolved
    assert len(ex.pm.open_positions()) == 1 and len(chain.send_log) == 2
    assert not [r for r in ex.ledger.read() if r.get("reason") == "zero_balance_closed"]
    chain.result_fails = False
    clock.t += 120
    ex.reconcile(force=True)
    assert ex.pm.open_positions() == [] and ex.st["sells"] == {}
    (x,) = [r for r in ex.ledger.read() if r["ev"] == "exit"]
    assert x["signature"] == "SIG2" and x["reason"] == "stop" and x["sol_in_lamports"] == 10_000_000 and not x.get("estimated")


# --- B: the loop survives errors -------------------------------------------------------------------------------------------------------------
def test_rpc_error_in_one_tick_does_not_stop_the_next_exit(tmp_path):
    ex, jup, signer, chain, clock = held(tmp_path)
    chain.confirm = True
    jup.sell_unit = 10.0
    real_tick, calls = ex.pm.tick, []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("rpc down")
        return real_tick()
    ex.pm.tick = flaky
    ex.process_intents = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    ex.run(duration=200, poll_s=30)
    assert ex.pm.open_positions() == [] and len(calls) >= 2
    kinds = [json.loads(x) for x in open(ex.log_path)]
    assert {"stage_error"} <= {k["kind"] for k in kinds} and {"OSError", "RuntimeError"} <= {k.get("error_class") for k in kinds}


def test_sell_exception_keeps_position_open_and_retries(tmp_path):
    ex, jup, signer, chain, clock = held(tmp_path)
    chain.confirm = True
    jup.sell_unit = 10.0
    orig = chain.token_balance
    n = []

    def bad(m):
        n.append(1)
        if len(n) == 1:
            raise RuntimeError("x")
        return orig(m)
    chain.token_balance = bad
    clock.t += 30
    ex.pm.tick()
    assert len(ex.pm.open_positions()) == 1
    clock.t += 30
    ex.pm.tick()
    assert ex.pm.open_positions() == []


# --- C: unresolved buy released once the blockhash height has passed ---------------------------------------------------------------------------
def test_unresolved_buy_released_not_written_off_after_last_valid_height(tmp_path):
    ex, jup, signer, chain, clock = mkexec(tmp_path)
    chain.confirm = False
    put_intent(tmp_path)
    ex.process_intents()
    assert ex.st["pending"]["SIG1"]["last_valid"] == 999
    chain.height = 500
    clock.t += 30
    ex.reconcile(force=True)
    assert "SIG1" in ex.st["pending"]  # chain not past last_valid yet, and under 600 s
    chain.height = 1000
    ex.reconcile(force=True)
    assert ex.st["pending"] == {}
    (w,) = [r for r in ex.ledger.read() if r["ev"] == "writeoff"]
    assert w["reason"] == "buy_expired_blockhash" and w["sol_out_lamports"] == 0
    view = lane.lane_fold(ex.ledger.read(), {}, 0, 60, "slowbot-h-1")
    assert view.open == {} and view.cash == 0  # the stake is released, nothing lost
