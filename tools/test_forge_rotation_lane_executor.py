"""DEC-036 rotation lane executor (tools/forge/rotation_lane_executor.py): the section 9 test list (the per-book decay monitor tests are a later PR), with a fake live port. Synthetic only: no key, no
network, no RPC, no solana stack, no tape, no host path.

  /data/mal/venv/bin/python -m pytest tools/test_forge_rotation_lane_executor.py
"""

from __future__ import annotations

import builtins
import hashlib
import json
import os
from pathlib import Path

import pytest

from tools import arena_live_bridge as B
from tools import floor_arena_executor as F
from tools import floor_lane_executor as X
from tools import h5_lane_executor as H
from tools.forge import dsl_lane_executor as D
from tools.forge import rotation_lane_executor as R
from tools.test_floor_arena_executor import Clock, _env  # noqa: F401  (autouse fixture)
from tools.test_floor_lane_executor import T0, FakeInner, a3_file
from tools.test_forge_dsl_lane_executor import FSHA, SSHA, HOLD_S, Log

BOT, BOT2 = "rot-t1", "rot-t2"
R10, R25, R125, R05 = 100_000_000, 250_000_000, 125_000_000, 50_000_000
RES = 3_860_000
PUB = "R" * 43
DAY = 86_400_000


@pytest.fixture(autouse=True)
def floor_paths(tmp_path, monkeypatch):
    fl = tmp_path / "floor" / "lane-ledger.jsonl"
    fl.parent.mkdir()
    fl.write_text("")
    monkeypatch.setattr(R, "FLOOR_LEDGER_PATHS", (str(fl),))
    monkeypatch.setattr(R, "FLOOR_STOP_PATHS", (str(tmp_path / "floor" / "STOP"), str(tmp_path / "wallet-STOP"), str(tmp_path / "wallet-HALT")))
    monkeypatch.setattr(R, "FLOOR_LANE_DIRS", (str(tmp_path / "floor"),))
    monkeypatch.setattr(R, "WALLET_WIDE_DIR", str(tmp_path))
    monkeypatch.setattr(R, "ROTATION_LEDGER_PATH", str(tmp_path / "rot" / "ledger.jsonl"))  # the pinned rotation paths, moved under tmp
    monkeypatch.setattr(R, "ROTATION_STOP_PATH", str(tmp_path / "rot" / "STOP"))
    return fl


def raw_cfg(tmp, bot=BOT, stake=R10, funded=1_500_000_000, **kw):
    d = {"mode": "live", "bot_id": bot, "frozen_sha": FSHA, "spec_sha256": SSHA, "max_hold_s": HOLD_S, "root": str(tmp / "root"), "state_dir": str(tmp / "state" / bot),
         "end_ms": T0 + 30 * DAY, "live_ok_path": str(tmp / f"LIVE_OK-{bot}"), "funded_lamports": funded, "wallet_pubkey": PUB, "floor_ledger_read_path": R.FLOOR_LEDGER_PATHS[0],
         "lane": {"ledger_path": str(tmp / "rot" / "ledger.jsonl"), "stop_path": str(tmp / "rot" / "STOP"), "book_stop_path": str(tmp / "rot" / f"STOP-{bot}"), "stake_lamports": stake}}
    lane_kw = kw.pop("lane", {})
    d.update(kw)
    d["lane"].update(lane_kw)
    return d


def mkled(tmp, bot=BOT, stake=R10, start=True, now=T0 - 3 * DAY, **kw):
    cfg, lane, _ok, pins = R.make_config(raw_cfg(tmp, bot, stake, **kw))
    led = R.RotationLedger(lane, pins["rot"])
    if start:
        led.record("start", "", {}, now, stake_lamports=stake, step_record_sha256=None)
    return led


def put(led, bot, ev, mint, ts=T0, sin=0, sout=0, **kw):
    with led.locked():
        led.append({"ev": ev, "bot_id": bot, "ts_ms": ts, "mint": mint, "signature": kw.pop("signature", None), "sol_in_lamports": sin, "sol_out_lamports": sout, **kw})


def intent(led, bot, mint, stake=R10, ts=T0):
    put(led, bot, "send_intent", mint, ts, stake_lamports=stake)


def trade(led, bot, mint, stake, pnl, ts=T0):
    out = stake + 2_260_000
    intent(led, bot, mint, stake, ts)
    put(led, bot, "fill", mint, ts, sout=out, ok=True)
    put(led, bot, "exit", mint, ts + 1, sin=out + pnl)


def loss(led, bot, lamports, ts=T0 - 2 * DAY):  # the day before: the daily stop (this DAY's loss) is not what these tests are about
    put(led, bot, "fee", "LOSS" + str(ts), ts, sout=lamports)


def reserve(led, mint, now=T0, stake=None):
    return led.reserve(mint, "intent:" + mint, {}, now, stake or led.stake)


def srows(bot, *stakes_ts):
    return [{"ev": "start", "bot_id": bot, "stake_lamports": s, "ts_ms": t} for s, t in stakes_ts]


def closed_rows(bot, stake, n, ts0, tag="c"):
    rows = []
    for i in range(n):
        m = f"{tag}{i}"
        rows += [{"ev": "send_intent", "bot_id": bot, "mint": m, "ts_ms": ts0 + i, "stake_lamports": stake, "sol_in_lamports": 0, "sol_out_lamports": 0},
                 {"ev": "fill", "bot_id": bot, "mint": m, "ts_ms": ts0 + i, "ok": True, "sol_in_lamports": 0, "sol_out_lamports": stake},
                 {"ev": "exit", "bot_id": bot, "mint": m, "ts_ms": ts0 + i + 1, "sol_in_lamports": stake, "sol_out_lamports": 0}]
    return rows


SHA = "ab" * 32


# ------------------------------------------------------------------------------------------------ stake ladder
def test_rotation_stake_rungs_pinned():
    assert R.ROTATION_STAKE_RUNGS_LAMPORTS == (50_000_000, 100_000_000, 125_000_000, 250_000_000)
    assert R.ROTATION_NEW_BOOK_STAKE_LAMPORTS == 100_000_000 and R.ROTATION_STEP_MIN_CLOSED == 20
    assert R.STEP_UP == {50_000_000: 100_000_000, 100_000_000: 250_000_000, 125_000_000: 250_000_000}


@pytest.mark.parametrize("stake", [0, 49_999_999, 75_000_000, 110_000_000, 250_000_001, 300_000_000, True, None, 1e8 + 0.5])
def test_rotation_stake_not_a_rung_refuses_start(tmp_path, stake):
    raw = raw_cfg(tmp_path)
    raw["lane"]["stake_lamports"] = stake
    with pytest.raises(ValueError):
        R.make_config(raw)
    assert R.run(raw, live_flag=True, root_checks=False, port_factory=lambda c: pytest.fail("port built")) == 2
    assert R.stake_start_problem([], BOT, 75_000_000, SHA) == "stake_not_a_rung"


def test_rotation_new_book_starts_only_at_010():
    assert R.stake_start_problem([], BOT, R10, None) is None
    for s in (R05, R125, R25):
        assert R.stake_start_problem([], BOT, s, SHA) == "new_book_must_start_at_100000000"
    assert R.stake_start_problem(srows(BOT2, (R25, 1)), BOT, R25, SHA) == "new_book_must_start_at_100000000"  # another book's rows do not count


def test_rotation_refuses_skip_rung_up():
    rows = srows(BOT, (R05, 1)) + closed_rows(BOT, R05, 25, 10)
    assert R.stake_start_problem(rows, BOT, R25, SHA) == "stake_move_refused"  # 0.05 -> 0.25 skips 0.10
    assert R.stake_start_problem(rows, BOT, R125, SHA) == "stake_move_refused"
    assert R.stake_start_problem(rows, BOT, R10, SHA) is None
    rows = srows(BOT, (R10, 1)) + closed_rows(BOT, R10, 25, 10)
    assert R.stake_start_problem(rows, BOT, R125, SHA) == "stake_move_refused"
    assert R.stake_start_problem(rows, BOT, R25, SHA) is None


def test_rotation_up_step_requires_record_sha():
    rows = srows(BOT, (R10, 1)) + closed_rows(BOT, R10, 20, 10)
    for bad in (None, "", "ab" * 31, "AB" * 32, "zz" * 32, 5):
        assert R.stake_start_problem(rows, BOT, R25, bad) == "up_step_needs_step_record_sha256"
    assert R.stake_start_problem(rows, BOT, R25, SHA) is None
    with pytest.raises(ValueError):
        R.make_config(raw_cfg(Path("/nonexistent-tmp"), step_record_sha256="xyz"))


def test_rotation_up_step_requires_20_closed_at_current_rung_since_last_change():
    n19 = srows(BOT, (R10, 1)) + closed_rows(BOT, R10, 19, 10)
    assert R.stake_start_problem(n19, BOT, R25, SHA).startswith("up_step_needs_20_closed")
    assert R.stake_start_problem(srows(BOT, (R10, 1)) + closed_rows(BOT, R10, 20, 10), BOT, R25, SHA) is None
    # a book that decayed from 0.25 to 0.05 cannot return on its old 0.10 count: 20 trades at 0.10 before the change do not count at 0.05
    decayed = srows(BOT, (R10, 1), (R25, 1000), (R05, 2000)) + closed_rows(BOT, R10, 20, 10) + closed_rows(BOT, R25, 3, 1100, "d") + closed_rows(BOT, R05, 19, 2100, "e")
    assert R.stake_start_problem(decayed, BOT, R10, SHA).startswith("up_step_needs_20_closed")
    assert R.stake_start_problem(decayed + closed_rows(BOT, R05, 1, 2200, "f"), BOT, R10, SHA) is None
    # trades at the SAME stake before the last change do not count either (0.05 -> 0.10 -> 0.05 decayed back)
    back = srows(BOT, (R05, 1), (R10, 500), (R05, 1000)) + closed_rows(BOT, R05, 20, 10)
    assert R.stake_start_problem(back, BOT, R10, SHA).startswith("up_step_needs_20_closed")
    # a restart at the same stake is not a stake change
    restart = srows(BOT, (R10, 1), (R10, 5000)) + closed_rows(BOT, R10, 20, 10)
    assert R.stake_start_problem(restart, BOT, R25, SHA) is None


def test_rotation_down_moves_only_per_decay_table():
    for last, ok in ((R25, (R125, R05)), (R125, (R05,)), (R10, (R05,))):
        for s in R.ROTATION_STAKE_RUNGS_LAMPORTS:
            got = R.stake_start_problem(srows(BOT, (last, 1)), BOT, s, None)
            if s == last or s in ok:
                assert got is None, (last, s)
    assert R.stake_start_problem(srows(BOT, (R125, 1)), BOT, R10, SHA) == "stake_move_refused"  # 0.125 -> 0.10 is never a step
    assert R.stake_start_problem(srows(BOT, (R25, 1)), BOT, R10, SHA) == "stake_move_refused"
    assert R.stake_start_problem(srows(BOT, (R05, 1)), BOT, R05, None) is None


def test_rotation_0125_only_from_025():
    assert R.stake_start_problem(srows(BOT, (R25, 1)), BOT, R125, None) is None
    for last in (R05, R10):
        assert R.stake_start_problem(srows(BOT, (last, 1)) + closed_rows(BOT, last, 30, 10), BOT, R125, SHA) == "stake_move_refused"
    assert R.stake_start_problem([], BOT, R125, SHA) == "new_book_must_start_at_100000000"
    assert R.stake_start_problem(srows(BOT, (R125, 1)), BOT, R125, None) is None


def test_dsl_lane_stake_bounds_unchanged_for_dec035_class(tmp_path):
    assert D.DSL_LANE_STAKE_BOUNDS_LAMPORTS == (20_000_000, 30_000_000)
    raw = {"mode": "live", "bot_id": "dsl-x", "frozen_sha": FSHA, "spec_sha256": SSHA, "max_hold_s": HOLD_S, "root": str(tmp_path), "state_dir": str(tmp_path), "end_ms": T0 + DAY,
           "lane": {"ledger_path": str(tmp_path / "l.jsonl"), "stop_path": str(tmp_path / "S"), "stake_lamports": R10}}
    with pytest.raises(X.LaneConfigError):
        D.make_config(raw)  # 0.10 is a rotation rung, not a DEC-035 DSL stake
    raw["lane"]["stake_lamports"] = 25_000_000
    assert D.make_config(raw)[0].stake_lamports == 25_000_000
    with pytest.raises(ValueError):
        R.make_config(raw_cfg(tmp_path, lane={"stake_lamports": 25_000_000}))  # and 0.025 is not a rotation rung


# ------------------------------------------------------------------------------------------------ per-book cap
def test_rotation_fee_reserve_pinned_3860000(tmp_path):
    assert R.ROTATION_FEE_RESERVE_LAMPORTS == 3_860_000 == F.RENT_RESERVE_LAMPORTS + F.SEND_FEE_LAMPORTS + F.SELL_RESERVE_LAMPORTS
    with pytest.raises(ValueError):
        R.make_config(raw_cfg(tmp_path, lane={"fee_reserve_lamports": 3_859_999}))
    assert R.make_config(raw_cfg(tmp_path, lane={"fee_reserve_lamports": 4_000_000}))[2 - 1].fee_reserve_lamports == 4_000_000  # config may RAISE the reserve


def test_rotation_book_loss_cap_040_without_025_start_row(tmp_path):
    led = mkled(tmp_path)
    assert [reserve(led, f"M{i}") for i in range(3)] == [None, None, None]  # 3 x 103,860,000 = 311,580,000 admitted
    assert reserve(led, "M3") == "rotation_book_cap"  # 4 x 103,860,000 = 415,440,000 > 400,000,000
    assert 4 * (R10 + RES) == 415_440_000 and 3 * (R10 + RES) == 311_580_000


def test_rotation_book_loss_cap_075_after_025_start_row(tmp_path):
    led = mkled(tmp_path, stake=R25, funded=3_000_000_000)
    assert [reserve(led, f"M{i}") for i in range(2)] == [None, None]  # 2 x 253,860,000 = 507,720,000
    assert reserve(led, "M2") == "rotation_book_cap"  # 3 x 253,860,000 = 761,580,000 > 750,000,000
    assert 3 * (R25 + RES) == 761_580_000


def test_rotation_config_cannot_raise_cap_to_075_before_025(tmp_path):
    led = mkled(tmp_path, lane={"book_loss_cap_lamports": 750_000_000})
    assert led.rot.book_cap_cfg == 750_000_000  # accepted as a config value, but the ceiling for a book with no 0.25 start row is 0.40
    assert [reserve(led, f"M{i}") for i in range(3)] == [None] * 3 and reserve(led, "M3") == "rotation_book_cap"
    with pytest.raises(ValueError):
        R.make_config(raw_cfg(tmp_path, lane={"book_loss_cap_lamports": 750_000_001}))
    lowered = mkled(tmp_path, bot=BOT2, lane={"book_loss_cap_lamports": 200_000_000})
    assert [reserve(lowered, "L0")] == [None] and reserve(lowered, "L1") == "rotation_book_cap"  # config may lower it (2 x 103.86M > 200M)


def test_rotation_book_cap_stop_written_when_no_open_and_realized_room_below_one_position(tmp_path):
    led = mkled(tmp_path)
    loss(led, BOT, 300_000_000)  # realized -0.30: room 0.10 < one position (0.10386)
    assert reserve(led, "M0") == "rotation_book_cap_stop"
    assert led.rot.book_stop_path.exists() and not led.cfg.stop_path.exists()
    assert reserve(led, "M1") == "rotation_book_stop_file"
    other = mkled(tmp_path, bot=BOT2)
    assert reserve(other, "O0") is None  # the lane and the other book are not stopped


def test_rotation_book_cap_refusals_wait_while_positions_open(tmp_path):
    led = mkled(tmp_path)
    loss(led, BOT, 200_000_000)
    assert reserve(led, "N0") is None  # 200 + 103.86 = 303.86M < 400M: one position fits
    assert reserve(led, "N1") == "rotation_book_cap"  # 200 + 103.86 + 103.86 = 407.7M >= 400M: refused while N0 is open
    assert reserve(led, "N2") == "rotation_book_cap"
    assert not led.rot.book_stop_path.exists() and not led.cfg.stop_path.exists()  # waits for the exit: no STOP while a position is open
    put(led, BOT, "writeoff", "N0", T0 + 5, reason="x")  # the position leaves (its intent was never filled: nothing lost)
    assert reserve(led, "N3", T0 + 10) is None  # realized room is back to one position: admitted, no retirement


def test_rotation_book_max_open(tmp_path):
    led = mkled(tmp_path, funded=3_000_000_000)
    trade(led, BOT, "W", R10, 1_000_000_000)  # +1 SOL realized: the cap, month and lifetime have room, so only the open cap binds
    assert [reserve(led, f"M{i}") for i in range(4)] == [None] * 4
    assert reserve(led, "M4") == "rotation_book_max_open"
    assert R.ROTATION_BOOK_MAX_OPEN == 4
    lowered = mkled(tmp_path, bot=BOT2, funded=3_000_000_000, lane={"max_open": 1})
    assert reserve(lowered, "L0") is None and reserve(lowered, "L1") == "rotation_book_max_open"
    with pytest.raises(ValueError):
        R.make_config(raw_cfg(tmp_path, lane={"max_open": 5}))


# ------------------------------------------------------------------------------------------------ lane limits
def test_rotation_daily_stop_spans_books_and_resets_at_00z(tmp_path):
    led = mkled(tmp_path)
    other = mkled(tmp_path, bot=BOT2)
    assert led.check({}, T0, R10) is None  # writes the day's snapshot via reserve below; check alone is read-only
    assert reserve(led, "M0") is None  # snapshot of the day written
    put(other, BOT2, "fee", "X", T0 + 1000, sout=300_000_000)  # the OTHER book loses 0.30 SOL
    assert led.check({}, T0 + 2000, R10) == "rotation_daily_stop"
    assert reserve(led, "M1", T0 + 2000) == "rotation_daily_stop"
    assert led.check({}, T0 + DAY + 2000, R10) is None  # next UTC day: a fresh snapshot
    assert R.ROTATION_DAILY_MAX_LAMPORTS == 300_000_000
    assert led.cfg.daily_loss_lamports == 300_000_000
    with pytest.raises(ValueError):
        R.make_config(raw_cfg(tmp_path, lane={"daily_loss_lamports": 300_000_001}))


def test_rotation_open_exposure_cap(tmp_path):
    a, b, c = (mkled(tmp_path, bot=x, funded=3_000_000_000) for x in ("e1", "e2", "e3"))
    for i, led in enumerate((a, b, c)):
        for j in range(3):
            intent(led, led.bot, f"E{i}{j}")  # 9 open at 0.10 across three books
    mine = mkled(tmp_path, funded=3_000_000_000)
    assert reserve(mine, "M0") is None  # 10 x 0.10 = 1.00 exactly: admitted
    assert reserve(mine, "M1") == "rotation_exposure_cap"  # 1.10 > 1.00
    assert R.ROTATION_OPEN_EXPOSURE_MAX_LAMPORTS == 1_000_000_000


def test_rotation_month_budget_presend_full_wipe(tmp_path):
    led = mkled(tmp_path)
    other = mkled(tmp_path, bot=BOT2)
    loss(other, BOT2, 1_300_000_000)  # month realized -1.30 (the other book's own cap does not bind this book)
    assert reserve(led, "M0") is None  # -1.30 - 0.10386 = -1.40386 > -1.50
    assert reserve(led, "M1") == "rotation_month_budget"  # -1.30 - 0.10386 - 0.10386 = -1.50772 <= -1.50: every open position counts as wiped
    assert not led.cfg.stop_path.exists() and not led.rot.book_stop_path.exists()
    assert R.ROTATION_MONTH_BUDGET_MAX_LAMPORTS == 1_500_000_000


def test_rotation_month_budget_lifts_at_month_start_without_stop_file(tmp_path):
    led = mkled(tmp_path)
    loss(led, "other-1", 1_450_000_000)
    assert reserve(led, "M0") == "rotation_month_budget"
    assert not led.cfg.stop_path.exists() and not led.rot.book_stop_path.exists()
    later = T0 + 32 * DAY  # a later UTC month
    assert R.month_of(later) != R.month_of(T0)
    assert reserve(led, "M0", later) is None  # lifted by the calendar, no file touched
    assert not led.cfg.stop_path.exists()


def test_rotation_lifetime_budget_presend_full_wipe(tmp_path):
    led = mkled(tmp_path)
    loss(led, "other-1", 2_900_000_000, ts=T0 - 40 * DAY)  # an earlier month: the month budget is clear, the lifetime is not
    assert reserve(led, "M0") == "rotation_lifetime_budget"  # -2.90 - 0.10386 <= -3.00
    assert not led.cfg.stop_path.exists()  # room for the lowest rung (0.05386) remains: no STOP yet
    assert R.ROTATION_LIFETIME_BUDGET_MAX_LAMPORTS == 3_000_000_000
    held = mkled(tmp_path, bot=BOT2)
    intent(held, BOT2, "H0")  # one open position: the refusal waits, no STOP
    assert reserve(led, "M1") == "rotation_lifetime_budget" and not led.cfg.stop_path.exists()


def test_rotation_lifetime_stop_written_when_no_open_and_room_below_lowest_rung(tmp_path):
    led = mkled(tmp_path)
    loss(led, "other-1", 2_950_000_000, ts=T0 - 40 * DAY)  # -2.95 - 0.05386 <= -3.00
    assert reserve(led, "M0") == "rotation_lifetime_stop"
    assert led.cfg.stop_path.exists()
    assert "rotation_lifetime_stop" in led.cfg.stop_path.read_text()


def test_rotation_lifetime_budget_stop_survives_restart(tmp_path):
    led = mkled(tmp_path)
    loss(led, "other-1", 2_950_000_000, ts=T0 - 40 * DAY)
    assert reserve(led, "M0") == "rotation_lifetime_stop"
    again = mkled(tmp_path, start=False)  # a restarted instance: a new object on the same paths
    assert reserve(again, "M0") == "rotation_lane_stop_file" and again.check({}, T0, R10) == "rotation_lane_stop_file"
    assert again.cfg.stop_path.exists()
    assert reserve(mkled(tmp_path, bot=BOT2), "N0") == "rotation_lane_stop_file"  # it stops every rotation book


# ------------------------------------------------------------------------------------------------ wallet and books
def test_rotation_wallet_slice(tmp_path):
    led = mkled(tmp_path, stake=R25, funded=1_500_000_000)  # 40% of 1.5 SOL = 0.60
    assert [reserve(led, f"M{i}") for i in range(2)] == [None, None]  # 2 x 0.25386 = 0.50772
    assert reserve(led, "M2") == "rotation_wallet_slice"  # 3 x 0.25386 = 0.76158 > 0.60
    assert R.ROTATION_WALLET_SLICE_MAX_BPS == 4000
    with pytest.raises(ValueError):
        R.make_config(raw_cfg(tmp_path, lane={"wallet_slice_bps": 4001}))


def test_rotation_wallet_floor_held_once(tmp_path):
    clk = Clock(T0)
    br, _ = mk(tmp_path, clk)
    assert R.ROTATION_WALLET_FLOOR_MIN_LAMPORTS == 250_000_000 and br.rot.wallet_floor == 250_000_000
    orders = {f"o{i}": {"stake_lamports": R10, "status": "open"} for i in range(2)}  # two LANDED positions: the balance already reflects their stakes
    br.open_orders = lambda scope=True: orders  # type: ignore[method-assign]
    need = R10 + F.SEND_FEE_LAMPORTS + F.RENT_RESERVE_LAMPORTS + 3 * F.SELL_RESERVE_LAMPORTS + 250_000_000  # each landed stake counted once (in the balance), the floor once
    assert br.floor_problem(R10, need) is None and br.floor_problem(R10, need - 1) == "wallet_floor"
    orders["o2"] = {"stake_lamports": R10, "status": "buy_sent"}  # an unlanded send: its stake has not left the wallet yet, counted once, plus one more sell reserve
    need2 = need + R10 + F.SELL_RESERVE_LAMPORTS
    assert br.floor_problem(R10, need2) is None and br.floor_problem(R10, need2 - 1) == "wallet_floor"
    assert br.floor_problem(R10, None) == "balance_unreadable"
    raised = R.make_config(raw_cfg(tmp_path, wallet_floor_lamports=300_000_000))[0]
    assert raised.wallet_floor_lamports == 300_000_000  # config may raise it
    assert R.make_config(raw_cfg(tmp_path, wallet_floor_lamports=100_000_000))[0].wallet_floor_lamports == 250_000_000  # never lower


def test_rotation_max_live_books(tmp_path):
    held = []
    for b in ("h1", "h2"):
        held.append(R.take_bot_lock(mkled(tmp_path, bot=b, start=False)))
    br, _ = mk(tmp_path, Clock(T0))  # two others live: a third book may start
    br.release_lane_lock()
    held.append(R.take_bot_lock(mkled(tmp_path, bot="h3", start=False)))
    assert R.ROTATION_MAX_LIVE_BOOKS == 3
    with pytest.raises(SystemExit, match="already live"):
        mk(tmp_path, Clock(T0), bot=BOT2)
    assert R.run(raw_cfg(tmp_path, BOT2), live_flag=True, root_checks=False, port_factory=lambda c: pytest.fail("port built")) == 2
    for f in held:
        f.close()


# ------------------------------------------------------------------------------------------------ isolation
def _scenario(tmp):
    led = mkled(tmp)
    out = [reserve(led, f"M{i}") for i in range(5)]
    loss(led, BOT, 300_000_000)
    out.append(led.check({}, T0 + 5000, R10))
    cfg, lane, _ok, pins = R.make_config(raw_cfg(tmp))
    out += [cfg.max_open, cfg.daily_loss_lamports, cfg.total_loss_lamports, cfg.wallet_floor_lamports, lane.fee_reserve_lamports, lane.max_open, lane.daily_loss_lamports]
    return out


def test_rotation_limits_independent_of_floor_constants(tmp_path, monkeypatch):
    import shutil

    base = _scenario(tmp_path)
    shutil.rmtree(tmp_path / "rot")
    shutil.rmtree(tmp_path / "state", ignore_errors=True)
    for mod in (X, H, D):
        for name in [n for n in dir(mod) if n.startswith(("LANE_", "FLOOR_LANE_", "DSL_LANE_")) and isinstance(getattr(mod, n), (int, float, tuple)) and not isinstance(getattr(mod, n), bool)]:
            v = getattr(mod, name)
            monkeypatch.setattr(mod, name, (1,) * len(v) if isinstance(v, tuple) else 1)
    assert _scenario(tmp_path) == base


def test_rotation_refuses_floor_ledger_stop_path_and_pubkey(tmp_path):
    fl, stops = R.FLOOR_LEDGER_PATHS[0], R.FLOOR_STOP_PATHS
    for k, v in (("ledger_path", fl), ("stop_path", stops[0]), ("stop_path", stops[1]), ("book_stop_path", stops[0]), ("book_stop_path", stops[2])):
        with pytest.raises(ValueError):
            R.make_config(raw_cfg(tmp_path, lane={k: v}))
    with pytest.raises(ValueError):
        R.make_config(raw_cfg(tmp_path, lane={"book_stop_path": str(tmp_path / "rot" / "STOP")}))  # lane and book STOP must differ
    with pytest.raises(ValueError, match="floor-lane-1's wallet"):
        R.make_config(raw_cfg(tmp_path, wallet_pubkey=R.FLOOR_WALLET_PUBKEY))
    for bad in (None, "short", "0" * 43):
        with pytest.raises(ValueError):
            R.make_config(raw_cfg(tmp_path, wallet_pubkey=bad))
    assert R.FLOOR_WALLET_PUBKEY == "5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk"  # docs/HANDOFF.md, public key only


def test_pinned_floor_paths_in_code_match_the_documented_ones():
    import importlib

    pristine = importlib.reload(R)
    assert pristine.FLOOR_LEDGER_PATHS == ("/var/lib/mal-live/h5-lane/lane-ledger.jsonl",)
    assert "/var/lib/mal-live/h5-lane/STOP" in pristine.FLOOR_STOP_PATHS and "/var/lib/mal-live/STOP" in pristine.FLOOR_STOP_PATHS and "/var/lib/mal-live/HALT" in pristine.FLOOR_STOP_PATHS


def test_rotation_refuses_key_path_in_config(tmp_path):
    for k in ("key_path", "keypair_path", "private_key", "wallet_key_file", "KEYPAIR", "credential_path"):
        with pytest.raises(ValueError, match="key_path_in_config"):
            R.make_config(raw_cfg(tmp_path, **{k: "/root/k.json"}))
    with pytest.raises(ValueError):
        R.make_config(raw_cfg(tmp_path, lane={"key_path": "/root/k.json"}))
    with pytest.raises(ValueError):
        R.make_config(raw_cfg(tmp_path, stop_file="/x"))  # unknown keys are refused (the wallet-wide files are the bridge's)
    assert R.run(raw_cfg(tmp_path, key_path="/root/k.json"), live_flag=True, root_checks=False, port_factory=lambda c: pytest.fail("port built")) == 2


@pytest.mark.parametrize("bot", ["floor-lane-1", "h5-lane", "l1-floor-v2", "hv4-x", "a:b", "a/b", None, ""])
def test_rotation_reserved_bot_ids(tmp_path, bot):
    with pytest.raises(ValueError):
        R.make_config(raw_cfg(tmp_path) | {"bot_id": bot})
    assert R.bot_id_problem(bot)


# ------------------------------------------------------------------------------------------------ same pool
def floor_put(tmp, rows, torn=None):
    p = Path(R.FLOOR_LEDGER_PATHS[0])
    with open(p, "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
        if torn:
            f.write(torn)
    return p


def fl(ev, mint, **kw):
    return {"ev": ev, "bot_id": R.FLOOR_BOT_ID, "mint": mint, "ts_ms": T0, "stake_lamports": 1, "sol_in_lamports": 111, "sol_out_lamports": 222, **kw}


def mk(tmp, clk, inner=None, raw=None, bot=BOT, **kw):
    (tmp / f"LIVE_OK-{bot}").write_text(bot + "\n")
    cfg, lane, live_ok, pins = R.make_config(raw or raw_cfg(tmp, bot))
    inner = inner or FakeInner()
    inner.balance = lambda: 5_000_000_000  # type: ignore[method-assign]
    br = R.RotationLaneBridge(cfg, X.FloorLanePort(inner, cfg), lane, pins, now_ms=clk, live_ok_path=live_ok, root_checks=False, **kw)
    return br, inner


def feed(br, clk, ms=None):
    rows = br.read_new_rows()
    for r in rows:
        clk.t = r["ms"] + 50 if ms is None else ms
        br.handle_row(r)
        br._poll_ports()
    br._save()
    return rows


def refusals(br):
    return [json.loads(x) for x in br.log_path.read_text().splitlines() if '"entry_refused"' in x]


def started(tmp, bot=BOT):
    lg = Log(tmp, bot)
    lg.start(T0 - 1000)
    return lg


def test_rotation_trades_at_its_rung_and_logs_the_start_row(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk, raw=raw_cfg(tmp_path, step_record_sha256=None))
    started(tmp_path).entry(T0, "o1")
    feed(br, clk)
    assert len(inner.buys) == 1 and inner.buys[0]["stake_lamports"] == R10
    rows = [json.loads(x) for x in (tmp_path / "rot" / "ledger.jsonl").read_text().splitlines()]
    start = [r for r in rows if r["ev"] == "start"][0]
    assert start["stake_lamports"] == R10 and start["lane_class"] == "rotation" and start["bot_id"] == BOT
    assert [r for r in rows if r["ev"] == "send_intent"][0]["stake_lamports"] == R10
    br.release_lane_lock()


def test_a_restart_with_a_down_move_logs_it_and_an_up_step_logs_the_record_sha(tmp_path):
    clk = Clock(T0)
    br, _ = mk(tmp_path, clk)
    br.release_lane_lock()
    for i in range(20):
        trade(br.lane, BOT, f"T{i}", R10, 1000, T0 + i)
    with pytest.raises(SystemExit, match="up_step_needs_step_record_sha256"):
        mk(tmp_path, clk, raw=raw_cfg(tmp_path, stake=R25))
    br2, _ = mk(tmp_path, clk, raw=raw_cfg(tmp_path, stake=R25, step_record_sha256=SHA))
    rows = [json.loads(x) for x in (tmp_path / "rot" / "ledger.jsonl").read_text().splitlines() if '"start"' in x]
    assert rows[-1]["stake_lamports"] == R25 and rows[-1]["step_record_sha256"] == SHA
    assert br2.lane.book_cap(R.fold_rotation(br2.lane.read())) == 750_000_000  # the 0.25 start row lifts the book cap
    br2.release_lane_lock()


def test_rotation_pre_buy_refuses_an_off_rung_stake(tmp_path):
    br, _ = mk(tmp_path, Clock(T0))
    for s in (R05, R25, 99_999_999, True, None):
        assert br._lane_pre_buy({"order_id": "x", "mint": "MZ", "stake_lamports": s}) == "rotation_stake_out_of_bounds"
    br.release_lane_lock()


def test_rotation_refuses_pool_open_in_floor_lane(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    floor_put(tmp_path, [fl("send_intent", "MP"), fl("fill", "MP", ok=True)])
    lg = started(tmp_path)
    lg.entry(T0, "o1")
    feed(br, clk)
    assert inner.buys == [] and refusals(br)[-1]["why"] == "floor_pool_open" and br._rot_counts["floor_pool_open"] == 1
    assert br._lane_pre_buy({"order_id": "x", "mint": "MP", "stake_lamports": R10}) == "floor_pool_open"  # the send-time re-read
    floor_put(tmp_path, [fl("exit", "MP")])  # FLOOR closed it: the pool is free
    lg.entry(T0 + 10_000, "o2")
    feed(br, clk)
    assert len(inner.buys) == 1
    floor_put(tmp_path, [fl("send_intent", "MQ"), fl("fill", "MQ", ok=False)])  # a failed buy is closed
    lg.entry(T0 + 20_000, "o3", pool="Q")
    feed(br, clk)
    assert len(inner.buys) == 2
    br.release_lane_lock()


def test_rotation_never_opens_floor_ledger_for_write(tmp_path, monkeypatch):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    floor_put(tmp_path, [fl("send_intent", "MZ")])
    path = R.FLOOR_LEDGER_PATHS[0]
    before = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    seen = []
    real_open, real_os_open = builtins.open, os.open

    def spy_open(f, mode="r", *a, **k):
        if str(f).startswith(str(Path(path).parent)):
            seen.append(("open", str(f), mode))
        return real_open(f, mode, *a, **k)

    def spy_os_open(f, flags, *a, **k):
        if str(f).startswith(str(Path(path).parent)):
            seen.append(("os.open", str(f), flags))
        return real_os_open(f, flags, *a, **k)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(os, "open", spy_os_open)
    started(tmp_path).entry(T0, "o1")
    feed(br, clk)
    br._lane_pre_buy({"order_id": "x", "mint": "MP", "stake_lamports": R10})
    monkeypatch.undo()
    assert seen and all(s[0] == "open" and s[1] == path and s[2] == "rb" for s in seen), seen
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == before
    assert sorted(p.name for p in Path(path).parent.iterdir()) == ["lane-ledger.jsonl"]  # no lock file, nothing else created beside it
    assert len(inner.buys) == 1
    br.release_lane_lock()


def test_floor_open_keys_keeps_keys_only(tmp_path):
    floor_put(tmp_path, [fl("send_intent", "A", pool="PA"), fl("fill", "A", ok=True), fl("send_intent", "B"), fl("exit", "B"), {"ev": "snap", "day": "d", "pnl_lamports": 5},
                         {"ev": "send_intent", "bot_id": "dsl-other", "mint": "C", "ts_ms": 1, "stake_lamports": 1}, {"ev": "start", "bot_id": R.FLOOR_BOT_ID, "mint": ""}])
    assert R.floor_open_keys(R.FLOOR_LEDGER_PATHS[0]) == {"A", "PA"}


def test_rotation_floor_ledger_torn_last_line_ignored(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    floor_put(tmp_path, [fl("send_intent", "MZ")], torn=json.dumps(fl("send_intent", "MP")))  # a complete JSON object, but no newline yet: a write in progress
    assert R.floor_open_keys(R.FLOOR_LEDGER_PATHS[0]) == {"MZ"}
    floor_put(tmp_path, [], torn="")
    started(tmp_path).entry(T0, "o1")
    feed(br, clk)
    assert len(inner.buys) == 1 and "floor_ledger_unreadable" not in br._rot_counts
    br.release_lane_lock()
    p = Path(R.FLOOR_LEDGER_PATHS[0])
    p.write_text(json.dumps(fl("send_intent", "A")) + "\n" + '{"ev": "send_int')  # a torn half line: ignored
    assert R.floor_open_keys(str(p)) == {"A"}


def test_rotation_floor_ledger_unreadable_refuses_entry(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    p = Path(R.FLOOR_LEDGER_PATHS[0])
    p.write_text(json.dumps(fl("send_intent", "A")) + "\n" + "not json\n")  # a COMPLETE line that does not parse
    lg = started(tmp_path)
    lg.entry(T0, "o1")
    feed(br, clk)
    assert inner.buys == [] and refusals(br)[-1]["why"] == "floor_ledger_unreadable" and br._rot_counts["floor_ledger_unreadable"] == 1
    p.unlink()  # missing
    lg.entry(T0 + 10_000, "o2", pool="Q")
    feed(br, clk)
    assert inner.buys == [] and br._rot_counts["floor_ledger_unreadable"] == 2
    assert br._lane_pre_buy({"order_id": "x", "mint": "MQ", "stake_lamports": R10}) == "floor_ledger_unreadable"
    br.release_lane_lock()


# ------------------------------------------------------------------------------------------------ stops
def test_rotation_honours_wallet_wide_stop_and_a3_pause(tmp_path, monkeypatch):
    clk = Clock(T0)
    a3 = a3_file(tmp_path)
    br, inner = mk(tmp_path, clk, a3_record_file=str(a3))
    monkeypatch.setattr(br, "_files", lambda: (False, True))  # the wallet-wide STOP
    lg = started(tmp_path)
    lg.entry(T0, "o1")
    feed(br, clk)
    assert inner.buys == [] and refusals(br)[-1]["why"] == "stop_file"
    assert br._lane_pre_buy({"order_id": "x", "mint": "MX", "stake_lamports": R10}) == "stop_file"
    monkeypatch.setattr(br, "_files", lambda: (True, False))
    assert br._lane_pre_buy({"order_id": "x", "mint": "MX", "stake_lamports": R10}) == "halt_file"
    monkeypatch.setattr(br, "_files", lambda: (False, False))
    a3_file(tmp_path, {"pins_changed": {"halt": True, "evaluated": True}})
    clk.t = T0 + 120_000
    lg.entry(clk.t, "o2", pool="Q")
    feed(br, clk, clk.t + 50)
    assert inner.buys == [] and refusals(br)[-1]["why"] == "a3_pause:a3_flags:pins_changed"
    br.release_lane_lock()


def test_rotation_book_stop_does_not_stop_other_books(tmp_path):
    a, b = mkled(tmp_path, bot=BOT), mkled(tmp_path, bot=BOT2)
    a.write_book_stop("manager", T0)
    assert reserve(a, "M0") == "rotation_book_stop_file" and a.check({}, T0, R10) == "rotation_book_stop_file"
    assert reserve(b, "M0") is None and b.check({}, T0, R10) is None
    a.cfg.stop_path.write_text("manager\n")  # the lane STOP stops every rotation book
    assert reserve(b, "M1") == "rotation_lane_stop_file"


def test_book_stop_on_the_bridge_refuses_entries_before_the_send(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    br.lane.write_book_stop("manager", T0)
    started(tmp_path).entry(T0, "o1")
    feed(br, clk)
    assert inner.buys == [] and refusals(br)[-1]["why"] == "rotation_book_stop_file"
    br.release_lane_lock()


# ------------------------------------------------------------------------------------------------ ledger fold
def test_fold_realized_counts_every_row_of_a_closed_position_and_nothing_of_an_open_one(tmp_path):
    led = mkled(tmp_path)
    trade(led, BOT, "A", R10, -5_000_000)
    intent(led, BOT, "B")
    put(led, BOT, "fill", "B", sout=R10 + 2_260_000, ok=True)
    put(led, BOT, "fee", "B", sout=160_000)  # a failed sell's fee on an OPEN position stays with it
    put(led, BOT, "fill", "C", sout=777, ok=False)  # a stray failed buy row
    st = R.fold_rotation(led.read())
    assert sum(a for _t, b, a in st["realized"] if b == BOT) == -5_000_000 - 777
    assert list(st["open"]) == [f"{BOT}:B"] and st["open"][f"{BOT}:B"]["cum"] == -(R10 + 2_260_000) - 160_000
    assert [c["stake"] for c in st["closed"]] == [R10]
