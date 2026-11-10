"""FLOOR risk accounting options: open_worst_case_frac and wallet_floor_count_held_once. Arithmetic on synthetic orders only; nothing here measures a loss or a fill.
Defaults must decide exactly as before the options existed."""

from __future__ import annotations

import dataclasses
import math

import pytest

from tools import arena_live_bridge as B
from tools import floor_arena_executor as F
from tools import floor_lane_executor as X
from tools import h5_lane_executor as H
from tools.test_floor_arena_executor import Clock, bridge, cfg_dict, _env  # noqa: F401  (fixtures of the executor's own tests)

STAKE, BUDGET, FUNDED, BAL0 = 100_000_000, 600_000_000, 1_000_000_000, 1_340_000_000
FLOOR_LAMPORTS = 750_000_000
SEND, RENT, SELL = F.SEND_FEE_LAMPORTS, F.RENT_RESERVE_LAMPORTS, F.SELL_RESERVE_LAMPORTS


def lane_bridge(tmp, **kw):
    """A FloorBridge whose cfg carries the amended-lane numbers (stake 0.10, budget 0.60, floor 0.75)."""
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


def expected(frac, once, wallet_consistent):
    wc = F.stake_charge(STAKE, frac) + SEND + RENT + SELL
    n = 0
    while True:
        if (n + 1) * wc > BUDGET:
            return n
        bal = BAL0 - (n * STAKE if wallet_consistent else 0)
        if bal < (0 if once else n * STAKE) + STAKE + SEND + RENT + (n + 1) * SELL + FLOOR_LAMPORTS:
            return n
        n += 1


def test_defaults_are_current_and_floor_is_0_75(tmp_path):
    br = lane_bridge(tmp_path)
    assert br.cfg.open_worst_case_frac == 1.0 and br.cfg.wallet_floor_count_held_once is False
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
    assert F.stake_charge(STAKE, 1.0) == STAKE and type(F.stake_charge(STAKE, 1.0)) is int


@pytest.mark.parametrize("frac,once,consistent", [(f, o, c) for f in (1.0, 0.2) for o in (False, True) for c in (False, True)])
def test_opens_admitted_equal_the_arithmetic(tmp_path, frac, once, consistent):
    br = lane_bridge(tmp_path, open_worst_case_frac=frac, wallet_floor_count_held_once=once)
    got = max_opens(br, lambda n: BAL0 - (n * STAKE if consistent else 0))
    assert got == expected(frac, once, consistent)


def test_table_numbers():
    rows = {(f, o, c): expected(f, o, c) for f in (1.0, 0.2) for o in (False, True) for c in (False, True)}
    assert rows[(1.0, False, False)] == 5 and rows[(1.0, True, False)] == 5 and rows[(0.2, False, False)] == 5 and rows[(0.2, True, False)] == 25
    assert rows[(1.0, False, True)] == 3 and rows[(1.0, True, True)] == 5 and rows[(0.2, False, True)] == 3 and rows[(0.2, True, True)] == 5


def test_frac_admits_more_committed_loss_headroom(tmp_path):
    # committed_loss_stop alone: balance huge so the wallet floor never binds
    for frac, want in ((1.0, 5), (0.2, 25)):
        br = lane_bridge(tmp_path, open_worst_case_frac=frac, wallet_floor_count_held_once=True)
        assert max_opens(br, lambda n: 10 ** 13) == want == math.floor(BUDGET / (F.stake_charge(STAKE, frac) + SEND + RENT + SELL))


def test_held_once_drops_exactly_the_held_term(tmp_path):
    a = lane_bridge(tmp_path)
    b = lane_bridge(tmp_path, wallet_floor_count_held_once=True)
    set_opens(a, 3)
    set_opens(b, 3)
    need = STAKE + SEND + RENT + 4 * SELL + FLOOR_LAMPORTS
    assert b.floor_problem(STAKE, need) is None and b.floor_problem(STAKE, need - 1) == "wallet_floor"
    assert a.floor_problem(STAKE, need + 3 * STAKE) is None and a.floor_problem(STAKE, need + 3 * STAKE - 1) == "wallet_floor"


@pytest.mark.parametrize("bad", [0, 0.0, -0.1, 1.0000001, 2, float("nan"), float("inf"), "0.2", None, True, [0.2]])
def test_bad_frac_refuses(tmp_path, bad):
    with pytest.raises(ValueError):
        F.make_config(cfg_dict(tmp_path, open_worst_case_frac=bad))


@pytest.mark.parametrize("bad", [1, 0, "true", None, 0.0])
def test_bad_held_once_refuses(tmp_path, bad):
    with pytest.raises(ValueError):
        F.make_config(cfg_dict(tmp_path, wallet_floor_count_held_once=bad))


def test_good_values_accepted(tmp_path):
    c = F.make_config(cfg_dict(tmp_path, open_worst_case_frac=1, wallet_floor_count_held_once=True))
    assert c.open_worst_case_frac == 1.0 and c.wallet_floor_count_held_once is True
    assert F.make_config(cfg_dict(tmp_path, open_worst_case_frac=0.2)).open_worst_case_frac == 0.2


def test_lane_config_passes_the_options_through(tmp_path):
    raw = cfg_dict(tmp_path, open_worst_case_frac=0.2, wallet_floor_count_held_once=True, funded_lamports=FUNDED)
    raw["lane"] = {"ledger_path": str(tmp_path / "l.jsonl"), "stop_path": str(tmp_path / "STOP"), "stake_lamports": 30_000_000}
    cfg, lane, _ = X.make_config(raw)
    assert cfg.open_worst_case_frac == 0.2 and cfg.wallet_floor_count_held_once is True
    raw["open_worst_case_frac"] = 0
    with pytest.raises(ValueError):
        X.make_config(raw)


# --- the pre-send full-wipe bound in the lane ledger ---------------------------------------------------------------------------------------------------
T0 = 1_800_000_000_000
LSTAKE = 30_000_000


def ledger(tmp_path, frac, budget=300_000_000):
    lc = H.LaneConfig(tmp_path / "l.jsonl", tmp_path / "STOP", X.LANE_BOT_ID, LSTAKE, 40, budget, 10 ** 12, X.LANE_MIN_FEE_RESERVE, 60.0)
    return X.FloorLaneLedger(lc, frac), lc


def view_of(lc, cash, n_filled, n_unfilled):
    op = {f"{lc.bot_id}:m{i}": {"stake": LSTAKE, "filled": i < n_filled, "ts_ms": T0, "signature": f"s{i}"} for i in range(n_filled + n_unfilled)}
    return H.LaneView(cash=cash, pnl=cash, cost=cash + n_filled * LSTAKE, open=op, snaps={})


def test_ledger_default_is_the_amendment_3_bound(tmp_path):
    led, lc = ledger(tmp_path, 1.0)
    assert X.FloorLaneLedger(lc).open_worst_case_frac == 1.0
    v = view_of(lc, -100_000_000, 3, 2)
    assert led.wipe_floor(v, T0) == -100_000_000 - 2 * LSTAKE
    reserve = lc.fee_reserve_lamports * 6
    assert (led.evaluate(v, T0, LSTAKE) is None) == (led.wipe_floor(v, T0) - (LSTAKE + reserve) > -lc.budget_lamports)


def test_ledger_frac_changes_the_bound_by_the_arithmetic(tmp_path):
    led, lc = ledger(tmp_path, 0.2)
    v = view_of(lc, -100_000_000, 3, 2)
    charge = F.stake_charge(LSTAKE, 0.2)
    assert charge == 6_000_000
    # filled positions keep (stake - charge) each; unfilled intents are charged `charge` each
    assert led.wipe_floor(v, T0) == -100_000_000 + 3 * (LSTAKE - charge) - 2 * charge


def test_ledger_frac_admits_exactly_the_arithmetic_number(tmp_path):
    def admitted(frac):
        led, lc = ledger(tmp_path, frac)
        n = 0
        while n < 39:
            cash = -n * (LSTAKE + 3_000_000)  # each filled buy took stake + fee/rent out of cash
            if led.evaluate(view_of(lc, cash, n, 0), T0, LSTAKE) is not None:
                break
            n += 1
        return n

    def arithmetic(frac):
        ch = F.stake_charge(LSTAKE, frac)
        res = X.LANE_MIN_FEE_RESERVE
        n = 0
        while n < 39:
            cash = -n * (LSTAKE + 3_000_000)
            floor = cash + n * (LSTAKE - ch)
            if floor - (ch + res * (n + 1)) <= -300_000_000 or cash <= -300_000_000:
                break
            n += 1
        return n

    assert admitted(1.0) == arithmetic(1.0) and admitted(0.2) == arithmetic(0.2) and admitted(0.2) > admitted(1.0)
