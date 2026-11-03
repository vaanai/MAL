"""Sell reconciliation (write-ahead sell_intent, late landing, no double-sell, restart, zero balance) and the mark-at-cost rule. Fakes only."""
import os

import pytest

from tools import h5_lane_executor as lane
from tools.slowbot import live_executor as lx
from tools.slowbot.tests.test_live_executor import MINT, MINT2, STAKE, mkexec, put_intent

TOK = STAKE // 20


def held(tmp_path, **kw):
    """A live executor with one filled position (buy landed), then the chain stops landing transactions on its own."""
    ex, jup, signer, chain, clock = mkexec(tmp_path, probe=True, **kw)
    put_intent(tmp_path)
    ex.process_intents()
    assert len(ex.pm.open_positions()) == 1
    chain.landed.add("SIG1")
    chain.confirm = False
    return ex, jup, signer, chain, clock


def evs(ex, *names):
    return [(r["ev"], r.get("signature")) for r in ex.ledger.read() if r["ev"] in names]


def test_late_landing_after_45s_closes_take_half_from_the_real_fill(tmp_path):
    ex, jup, signer, chain, clock = held(tmp_path)
    jup.sell_unit = 28.0  # +40%: take half
    clock.t += 30
    t0 = clock.t
    ex.pm.tick()
    assert clock.t - t0 >= 45  # waited the confirm window
    (p,) = ex.pm.open_positions()
    assert not p["taken"] and list(ex.st["sells"]) == ["SIG2"]
    assert evs(ex, "sell_intent") == [("sell_intent", "SIG2")]  # the probe in FakeChain.send asserted it was on disk before the send
    clock.t += 30
    ex.pm.tick()  # still unresolved: no second sell
    assert len(chain.send_log) == 2
    chain.land("SIG2")
    clock.t += 5
    ex.reconcile(force=True)
    (p,) = ex.pm.open_positions()
    half = TOK // 2
    assert (p["taken"], p["tokens_left"], ex.st["sells"]) == (True, TOK - half, {})
    (pe,) = [r for r in ex.ledger.read() if r["ev"] == "partial_exit"]
    assert pe["signature"] == "SIG2" and pe["sol_in_lamports"] == int(half * 28.0) and pe["fee_network_lamports"] == 5_000
    assert len(chain.send_log) == 2


def test_late_landing_of_a_full_exit_closes_the_position_and_ledger(tmp_path):
    ex, jup, signer, chain, clock = held(tmp_path)
    jup.sell_unit = 10.0  # -50%: stop
    clock.t += 30
    ex.pm.tick()
    assert len(ex.pm.open_positions()) == 1
    chain.land("SIG2")
    ex.reconcile(force=True)
    assert ex.pm.open_positions() == []
    (x,) = [r for r in ex.ledger.read() if r["ev"] == "exit"]
    assert x["signature"] == "SIG2" and x["reason"] == "stop" and x["sol_in_lamports"] == int(TOK * 10.0)
    assert lane.lane_fold(ex.ledger.read(), {}, 0, 60, "slowbot-h-1").open == {}


def test_resell_after_confirmed_failure_sells_only_the_remaining_balance(tmp_path):
    ex, jup, signer, chain, clock = held(tmp_path)
    chain.confirm = True
    chain.fail_sigs.add("SIG2")
    jup.sell_unit = 10.0
    clock.t += 30
    ex.pm.tick()
    assert len(ex.pm.open_positions()) == 1 and ex.st["sells"] == {}
    assert evs(ex, "fee") == [("fee", "SIG2")]
    clock.t += 30
    ex.pm.tick()
    assert ex.pm.open_positions() == []
    assert chain.send_log == [f"buy:{STAKE}:{TOK}", f"sell:{TOK}:{TOK * 10}", f"sell:{TOK}:{TOK * 10}"]  # the failed sell moved no tokens
    assert evs(ex, "exit") == [("exit", "SIG3")]


def test_no_double_sell_when_balance_is_smaller_than_the_position(tmp_path):
    ex, jup, signer, chain, clock = held(tmp_path)
    chain.confirm = True
    chain.balance_override[MINT] = TOK // 4  # part of it already left the wallet
    jup.sell_unit = 10.0
    clock.t += 30
    ex.pm.tick()
    assert chain.send_log[-1] == f"sell:{TOK // 4}:{(TOK // 4) * 10}"


@pytest.mark.parametrize("lose_state", [False, True])
def test_restart_mid_sell_reconciles_from_state_or_ledger(tmp_path, lose_state):
    ex, jup, signer, chain, clock = held(tmp_path)
    jup.sell_unit = 10.0
    clock.t += 30
    ex.pm.tick()
    assert list(ex.st["sells"]) == ["SIG2"]
    if lose_state:
        os.unlink(tmp_path / "state" / "executor-state.json")
    ex2, jup2, signer2, chain2, _ = mkexec(tmp_path, clock=clock)
    signer2.signed, chain2.sent, chain2.confirm = list(signer.signed), list(chain.sent), False
    chain2.landed = {"SIG1", "SIG2"}
    assert len(ex2.pm.open_positions()) == 1
    ex2.reconcile(force=True)
    assert ex2.pm.open_positions() == [] and ex2.st["sells"] == {}
    assert evs(ex2, "exit") == [("exit", "SIG2")]
    assert chain2.send_log == []  # the restarted process sent nothing


def test_zero_balance_is_closed_without_a_send(tmp_path):
    ex, jup, signer, chain, clock = held(tmp_path)
    chain.confirm = True
    chain.balance_override[MINT] = 0
    jup.sell_unit = 10.0
    clock.t += 30
    ex.pm.tick()
    assert ex.pm.open_positions() == [] and len(chain.send_log) == 1  # only the buy
    (x,) = [r for r in ex.ledger.read() if r["ev"] == "exit"]
    assert x["reason"] == "zero_balance_closed" and x["estimated"] is True and x["sol_in_lamports"] == 0
    assert lane.lane_fold(ex.ledger.read(), {}, 0, 60, "slowbot-h-1").open == {}


def test_balance_read_failure_fails_closed(tmp_path):
    ex, jup, signer, chain, clock = held(tmp_path)

    def boom(mint):
        raise OSError("rpc down")
    chain.token_balance = boom
    jup.sell_unit = 10.0
    clock.t += 30
    ex.pm.tick()
    assert len(chain.send_log) == 1 and len(ex.pm.open_positions()) == 1


def test_unresolved_sell_followed_with_backoff_then_declared_dead_and_balance_guards_the_resell(tmp_path):
    ex, jup, signer, chain, clock = held(tmp_path)
    jup.sell_unit = 10.0
    clock.t += 30
    ex.pm.tick()
    nexts = []
    for _ in range(7):
        clock.t += 100
        ex.reconcile_sells(force=True)
        nexts.append(ex.st["sells"].get("SIG2", {}).get("next"))
    assert ex.st["sells"] == {} and ex.st["dead"] == ["SIG2"]  # > 600 s since the send
    # the dead signature is not adopted again from the ledger
    ex._adopt_sells_from_ledger()
    assert ex.st["sells"] == {}
    ex.pm.tick()  # position still open and the balance is still full: a fresh sell is allowed, sized by the balance
    assert chain.send_log[-1] == f"sell:{TOK}:{TOK * 10}" and len(chain.send_log) == 3


def test_backoff_is_bounded(tmp_path):
    ex, jup, signer, chain, clock = held(tmp_path)
    jup.sell_unit = 10.0
    clock.t += 30
    ex.pm.tick()
    for _ in range(8):
        ex.reconcile_sells(force=True)
    info = ex.st["sells"]["SIG2"]
    assert info["next"] - clock.t <= 60


# --- mark at cost -----------------------------------------------------------------------------------------------------------------------
def test_fill_is_marked_at_cost_until_the_first_sell_quote(tmp_path):
    ex, jup, signer, chain, clock = mkexec(tmp_path)
    put_intent(tmp_path)
    ex.process_intents()
    assert ex.marks[MINT] == (STAKE, int(clock.t * 1000))
    assert ex.own_limits(MINT2, STAKE) is None  # the second entry is allowed right after the first
    ex.marks.clear()  # without the mark the lane's double count refuses it
    assert ex.own_limits(MINT2, STAKE) == "bot_budget_presend"
    ex.marks[MINT] = (STAKE, int(clock.t * 1000))
    clock.t += 30
    jup.sell_unit = 21.0
    ex.pm.tick()
    assert ex.marks[MINT] == (TOK * 21, int(clock.t * 1000))  # replaced by the first real quote
