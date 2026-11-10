"""FLOOR risk accounting options: wallet_floor_count_held_once. Arithmetic on synthetic orders only; nothing here measures a loss or a fill.
Defaults must decide exactly as before the options existed."""

from __future__ import annotations

import dataclasses

import pytest

from tools import arena_live_bridge as B
from tools import floor_arena_executor as F
from tools import floor_lane_executor as X
from tools.test_floor_arena_executor import Clock, bridge, cfg_dict, _env  # noqa: F401  (fixtures of the executor's own tests)

STAKE, BUDGET, FUNDED, BAL0 = 30_000_000, 600_000_000, 1_000_000_000, 1_338_700_000
FLOOR_LAMPORTS = 750_000_000
SEND, RENT, SELL = F.SEND_FEE_LAMPORTS, F.RENT_RESERVE_LAMPORTS, F.SELL_RESERVE_LAMPORTS


def lane_bridge(tmp, **kw):
    """A FloorBridge whose cfg carries the amended-lane numbers (stake 0.03, budget 0.60, floor 0.75)."""
    br, _ = bridge(tmp, Clock(), funded_lamports=FUNDED, **kw)
    br.cfg = dataclasses.replace(br.cfg, stake_lamports=STAKE, total_loss_lamports=BUDGET, max_open=100)
    return br


def set_opens(br, n):
    br.state.orders = {f"o{i}": {"status": "open", "live": False, "stake_lamports": STAKE} for i in range(n)}


def max_opens(br, bal_at, limit=60):
    """Opens admitted one after the other; bal_at(n) is the balance the check sees with n opens already held."""
    for n in range(limit):
        set_opens(br, n)
        if br.floor_problem(STAKE, bal_at(n)) is not None:
            return n
    return limit


def expected(once):
    """Opens admitted when each open lowers the balance by its stake (balance falling as stakes are paid)."""
    wc = STAKE + SEND + RENT + SELL
    n = 0
    while (n + 1) * wc <= BUDGET:
        bal = BAL0 - n * STAKE
        if bal < (0 if once else n * STAKE) + STAKE + SEND + RENT + (n + 1) * SELL + FLOOR_LAMPORTS:
            break
        n += 1
    return n


def test_defaults_are_current_and_floor_is_0_75(tmp_path):
    br = lane_bridge(tmp_path)
    assert br.cfg.wallet_floor_count_held_once is False
    assert br.cfg.wallet_floor_lamports == FLOOR_LAMPORTS
    assert isinstance(br.cfg, B.Config)


def test_default_decisions_match_the_old_formula(tmp_path):
    br = lane_bridge(tmp_path)
    for n in range(0, 9):
        set_opens(br, n)
        for bal in range(600_000_000, 1_500_000_000, 37_000_000):
            wc = STAKE + SEND + RENT + SELL
            if n * wc + wc > BUDGET:
                want = "committed_loss_stop"
            else:
                need = n * STAKE + STAKE + SEND + RENT + (n + 1) * SELL + FLOOR_LAMPORTS
                want = "wallet_floor" if bal < need else None
            assert br.floor_problem(STAKE, bal) == want


@pytest.mark.parametrize("once", [False, True])
def test_opens_admitted_equal_the_arithmetic(tmp_path, once):
    br = lane_bridge(tmp_path, wallet_floor_count_held_once=once)
    assert max_opens(br, lambda n: BAL0 - n * STAKE) == expected(once)


def test_table_numbers():
    assert expected(False) == 10 and expected(True) == 17


def test_held_once_drops_exactly_the_held_term(tmp_path):
    a = lane_bridge(tmp_path)
    b = lane_bridge(tmp_path, wallet_floor_count_held_once=True)
    set_opens(a, 3)
    set_opens(b, 3)
    need = STAKE + SEND + RENT + 4 * SELL + FLOOR_LAMPORTS
    assert b.floor_problem(STAKE, need) is None and b.floor_problem(STAKE, need - 1) == "wallet_floor"
    assert a.floor_problem(STAKE, need + 3 * STAKE) is None and a.floor_problem(STAKE, need + 3 * STAKE - 1) == "wallet_floor"


@pytest.mark.parametrize("bad", [1, 0, "true", None, 0.0])
def test_bad_held_once_refuses(tmp_path, bad):
    with pytest.raises(ValueError):
        F.make_config(cfg_dict(tmp_path, wallet_floor_count_held_once=bad))


def test_good_values_accepted(tmp_path):
    assert F.make_config(cfg_dict(tmp_path, wallet_floor_count_held_once=True)).wallet_floor_count_held_once is True


def test_lane_config_passes_the_option_through(tmp_path):
    raw = cfg_dict(tmp_path, wallet_floor_count_held_once=True, funded_lamports=FUNDED)
    raw["lane"] = {"ledger_path": str(tmp_path / "l.jsonl"), "stop_path": str(tmp_path / "STOP"), "stake_lamports": 30_000_000}
    cfg, lane, _ = X.make_config(raw)
    assert cfg.wallet_floor_count_held_once is True
    raw["wallet_floor_count_held_once"] = "yes"
    with pytest.raises(ValueError):
        X.make_config(raw)


@pytest.mark.parametrize("status", ["buy_sending", "buy_sent"])
def test_held_once_still_counts_unlanded_buys(tmp_path, status):
    """PR #1314 review MAJOR 1: a buy not yet landed has not left the balance, so held-once keeps its stake in `need`."""
    b = lane_bridge(tmp_path, wallet_floor_count_held_once=True)
    b.state.orders = {"o0": {"status": "open", "live": False, "stake_lamports": STAKE},
                      "o1": {"status": status, "live": False, "stake_lamports": STAKE}}
    need = STAKE + STAKE + SEND + RENT + 3 * SELL + FLOOR_LAMPORTS  # o1's unlanded stake + the new stake
    assert b.floor_problem(STAKE, need) is None
    assert b.floor_problem(STAKE, need - 1) == "wallet_floor"
