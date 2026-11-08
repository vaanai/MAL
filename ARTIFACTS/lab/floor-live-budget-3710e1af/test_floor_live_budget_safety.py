"""Adversarial fee / lifetime-budget review of FLOOR live at commit 3710e1af.

Synthetic only: throwaway Keypair, probe FakeRpc, no network, no host, no wallet file.
Does not import or send on a real RPC. Does not edit production executors.

Run from repo root:
  python -m pytest ARTIFACTS/lab/floor-live-budget-3710e1af/test_floor_live_budget_safety.py -q
"""

from __future__ import annotations

import ast
import base64
import dataclasses
import json
from pathlib import Path

import pytest

from tools import arena_live_bridge as B
from tools import floor_arena_executor as F
from tools import floor_lane_executor as X
from tools import h5_lane_executor as H
from tools import probe_executor as pe
from tools import probe_live as pl
from tools.test_probe_executor import MINT, Clock as PClock
from tools.test_probe_live import LiveRpc, land_buy, land_sell

STAKE = 30_000_000
BUDGET = 300_000_000
RESERVE = 3_860_000
FAIL_ERR = {"InstructionError": [3, {"Custom": 6004}]}
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SHA = "3710e1af8cf434faca570046de896a7702464709"


def _live_raw(tmp: Path) -> dict:
    return {
        "mode": "live",
        "bot_id": "l1-floor-v2",
        "frozen_sha": F.FROZEN_SHA,
        "root": str(tmp / "rules"),
        "state_dir": str(tmp / "state"),
        "end_ms": 1_794_787_200_000,
        "live_ok_path": str(tmp / "LIVE_OK"),
        "lane": {
            "bot_id": "floor-lane-1",
            "ledger_path": str(tmp / "lane" / "ledger.jsonl"),
            "stop_path": str(tmp / "lane" / "STOP"),
            "stake_lamports": STAKE,
            "max_open": 8,
            "budget_lamports": BUDGET,
            "daily_loss_lamports": 100_000_000,
            "fee_reserve_lamports": RESERVE,
            "mark_stale_s": 60,
        },
    }


def _floor_cfg(tmp: Path):
    cfg, lane, live_ok = X.make_config(_live_raw(tmp))
    cfg = dataclasses.replace(cfg, stop_file=str(tmp / "STOP"), halt_file=str(tmp / "HALT"))
    return cfg, lane, live_ok


def _unique_sigs(rpc: LiveRpc) -> list[str]:
    from solders.transaction import VersionedTransaction

    out = []
    for _t, b64 in rpc.sent:
        sig = str(VersionedTransaction.from_bytes(base64.b64decode(b64)).signatures[0])
        if sig not in out:
            out.append(sig)
    return out


def _signed_priority(b64: str) -> int | None:
    return B.signed_priority_lamports(b64)


@pytest.fixture
def hermetic_live_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pe, "LIVE_DIR", tmp_path / "probe-live-dir")
    return tmp_path


def _make_floor_port(tmp: Path):
    from solders.keypair import Keypair

    clock = PClock()
    rpc = LiveRpc(clock)
    cfg, lane, _ = _floor_cfg(tmp)
    inner = B.make_live_port(cfg, rpc, Keypair(), now_ms=clock)
    port = X.FloorLanePort(inner, cfg)
    port.pre_buy = lambda _o: None
    return port, inner.ex, rpc, clock, cfg, lane


# ------------------------------------------------------------------------------------------------ pins at this sha
def test_review_is_pinned_to_the_requested_commit():
    """HEAD may have moved; the send/budget files must still match 3710e1af."""
    import subprocess

    files = (
        "tools/floor_lane_executor.py",
        "tools/floor_arena_executor.py",
        "tools/probe_live.py",
        "tools/arena_live_bridge.py",
        "tools/h5_lane_executor.py",
        "scripts/mal-fast/floor-lane-live.json.example",
        "DEC/DEC-035-live-learning-lane.md",
    )
    for rel in files:
        pinned = subprocess.check_output(["git", "show", f"{SHA}:{rel}"], cwd=REPO)
        assert pinned == (REPO / rel).read_bytes(), rel


def test_fee_reserve_arithmetic_is_rent_plus_one_buy_plus_ten_sells():
    assert F.RENT_RESERVE_LAMPORTS == 2_100_000
    assert F.SEND_FEE_LAMPORTS == 160_000
    assert F.SELL_RESERVE_SENDS == 10
    assert F.SELL_RESERVE_LAMPORTS == 1_600_000
    assert X.LANE_MIN_FEE_RESERVE == 2_100_000 + 160_000 + 1_600_000 == RESERVE
    example = json.loads((REPO / "scripts/mal-fast/floor-lane-live.json.example").read_text())
    assert example["lane"]["fee_reserve_lamports"] == RESERVE
    assert example["lane"]["budget_lamports"] == BUDGET
    assert example["lane"]["max_open"] == 8


def test_floor_reserve_constant_matches_probe_live_hard_cap_at_this_sha():
    """H5 binds LANE_MIN_FEE_RESERVE to pl.SELL_MAX_ATTEMPTS. FLOOR binds it to F.SELL_RESERVE_SENDS.
    At this sha they are equal; they are not the same name, so a later probe_live bump would desync FLOOR."""
    assert F.SELL_RESERVE_SENDS == pl.SELL_MAX_ATTEMPTS == 10
    src = Path(X.__file__).read_text()
    tree = ast.parse(src)
    assigned = {n.targets[0].id: ast.unparse(n.value) for n in ast.walk(tree) if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)}
    assert "pl.SELL_MAX_ATTEMPTS" not in assigned.get("LANE_MIN_FEE_RESERVE", "")
    assert "F.SELL_RESERVE_LAMPORTS" in assigned.get("LANE_MIN_FEE_RESERVE", "")


def test_start_sell_itself_does_not_consult_the_attempt_cap():
    """The 10-sell bound is abandoned-in-_exit_candidates, not a guard inside _start_sell.
    A send that reached _start_sell with abandoned cleared would pay another fee."""
    tree = ast.parse(Path(pl.__file__).read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_start_sell")
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert "sell_max_attempts" not in names
    assert "abandoned" not in names


def test_eight_full_wipes_at_max_stake_fit_inside_the_lifetime_budget():
    n, stake = 8, STAKE
    per = stake + X.LANE_MIN_FEE_RESERVE
    assert n * per == 270_880_000
    assert n * per < BUDGET
    # extra sells to breach after a full reserved wipe: leftover / per-sell
    leftover = BUDGET - n * per
    assert leftover == 29_120_000
    assert leftover // F.SEND_FEE_LAMPORTS == 182  # ~22 extra sells/position, unbounded retries would get there
    # 11 sells/position still fits; 33 does not
    assert n * (stake + F.RENT_RESERVE_LAMPORTS + F.SEND_FEE_LAMPORTS + 11 * F.SEND_FEE_LAMPORTS) < BUDGET
    assert n * (stake + F.RENT_RESERVE_LAMPORTS + F.SEND_FEE_LAMPORTS + 33 * F.SEND_FEE_LAMPORTS) > BUDGET


def test_daily_stop_is_marked_preentry_not_a_hard_cash_ceiling(tmp_path):
    """0.10 SOL is a pre-entry marked/cost check, not a hard daily cash-loss ceiling.
    Naive 0.10 + 8*0.03 = 0.34 SOL exceeds the 0.30 lifetime budget; the lifetime cost-capped
    pre-send is what binds. A day can still lose more than 0.10 (a 5-position wipe is 0.15)."""
    naive_day = 100_000_000 + 8 * STAKE
    assert naive_day == 340_000_000
    assert naive_day > BUDGET
    lc = H.LaneConfig(tmp_path / "l.jsonl", tmp_path / "STOP", X.LANE_BOT_ID, STAKE, 8, BUDGET, 100_000_000, RESERVE, 60.0)
    now = 1_700_000_000_000
    op4 = {f"{lc.bot_id}:m{i}": {"stake": STAKE, "filled": True, "ts_ms": now, "signature": f"s{i}"} for i in range(4)}
    led = X.FloorLaneLedger(lc)
    led._marks = {f"m{i}": (STAKE, now) for i in range(4)}
    view = H.LaneView(cash=-4 * STAKE, pnl=0, cost=0, open=op4, snaps={H.day_of(now): (0, 0)})
    assert led.evaluate(view, now, STAKE) is None
    assert H.LaneLedger(lc).evaluate(view, now, STAKE) == "lane_daily_presend"
    assert 5 * STAKE == 150_000_000
    assert 100_000_000 < 5 * STAKE < BUDGET
    assert 5 * (STAKE + RESERVE) < BUDGET


def test_h5_daily_presend_is_absent_on_floor_ledger(tmp_path):
    lc = H.LaneConfig(tmp_path / "l.jsonl", tmp_path / "STOP", X.LANE_BOT_ID, STAKE, 8, BUDGET, 100_000_000, RESERVE, 60.0)
    view = H.LaneView(cash=0, pnl=0, cost=0, open={f"b:m{i}": {"stake": STAKE, "filled": True} for i in range(5)}, snaps={H.day_of(0): (0, 0)})
    assert X.FloorLaneLedger(lc).evaluate(view, 0, STAKE) is None
    assert H.LaneLedger(lc).evaluate(view, 0, STAKE) == "lane_daily_presend"


# ------------------------------------------------------------------------------------------------ real send path
@pytest.mark.skipif(pytest.importorskip("solders", reason="solders required for live-port tests") is None, reason="solders")
def test_floor_live_port_pins_p155_not_bridge_default_500k(hermetic_live_dir):
    port, ex, rpc, clock, cfg, _ = _make_floor_port(hermetic_live_dir)
    assert ex.limits.priority_lamports == F.PRIORITY_LAMPORTS == 155_000
    assert ex.sell_max_attempts == pl.SELL_MAX_ATTEMPTS == F.SELL_RESERVE_SENDS
    assert ex.sell_max_attempts == 10
    # make_live_port's pcfg default is 500k; FloorLanePort must have overwritten it before any send
    assert cfg.mode == "live"


@pytest.mark.skipif(pytest.importorskip("solders", reason="solders required") is None, reason="solders")
def test_one_hundred_failed_sells_on_floor_bridge_executor_send_exactly_ten(hermetic_live_dir):
    """The missing proof: FLOOR live uses BridgeExecutor.poll_positions, not the probe's own poll_positions.
    Existing tools/test_probe_live.py::test_100_failed_sells_capped_and_abandoned does not cover this class."""
    port, ex, rpc, clock, cfg, _ = _make_floor_port(hermetic_live_dir)
    order = {"order_id": "o1", "mint": MINT, "decision_ms": clock(), "stake_lamports": cfg.stake_lamports}
    ok, why = port.buy(order)
    assert ok, why
    land_buy(ex, rpc)
    port.poll()
    ok, why = port.sell({"mint": MINT}, "time_24h")
    assert ok, why
    fee = F.SEND_FEE_LAMPORTS
    for _ in range(99):
        if MINT in ex.state.pending:
            land_sell(ex, rpc, proceeds=0, err=FAIL_ERR, fee=fee)
        clock.t += 11 * 60_000  # past stuck backoff
        port.poll()  # production tick: advance_pending + poll_positions
    n_sigs = len(_unique_sigs(rpc))
    n_sells = n_sigs - 1
    pos = ex.state.open[MINT]
    assert n_sells == pl.SELL_MAX_ATTEMPTS == 10, f"sells={n_sells} sigs={n_sigs} attempts={pos.get('sell_attempts')} abandoned={pos.get('abandoned')}"
    assert pos["abandoned"] is True
    assert pos["sell_attempts"] >= 10
    for _t, b64 in rpc.sent[1:]:  # every sell at p155, never the 500k bridge default
        prio = _signed_priority(b64)
        assert prio == F.PRIORITY_LAMPORTS, prio
    for _ in range(20):
        clock.t += 11 * 60_000
        port.poll()
    assert len(_unique_sigs(rpc)) - 1 == 10
    assert MINT not in ex.state.pending


@pytest.mark.skipif(pytest.importorskip("solders", reason="solders required") is None, reason="solders")
def test_expired_sells_also_count_toward_the_ten_and_do_not_unbound(hermetic_live_dir):
    port, ex, rpc, clock, cfg, _ = _make_floor_port(hermetic_live_dir)
    ok, why = port.buy({"order_id": "o1", "mint": MINT, "decision_ms": clock(), "stake_lamports": cfg.stake_lamports})
    assert ok, why
    land_buy(ex, rpc)
    port.poll()
    ok, why = port.sell({"mint": MINT}, "time_24h")
    assert ok, why
    for _ in range(30):
        if MINT in ex.state.pending:
            rpc.height = ex.state.pending[MINT]["lvbh"] + 1
            for _i in range(pl.EXPIRY_RECHECKS + 1):
                ex.advance_pending()
                clock.t += pl.EXPIRY_RECHECK_MS
        clock.t += 11 * 60_000
        port.poll()
    n_sells = len(_unique_sigs(rpc)) - 1
    pos = ex.state.open[MINT]
    assert n_sells == 10, n_sells
    assert pos["abandoned"] is True


@pytest.mark.skipif(pytest.importorskip("solders", reason="solders required") is None, reason="solders")
def test_rebroadcast_of_the_same_signature_is_not_an_extra_attempt(hermetic_live_dir):
    port, ex, rpc, clock, cfg, _ = _make_floor_port(hermetic_live_dir)
    ok, why = port.buy({"order_id": "o1", "mint": MINT, "decision_ms": clock(), "stake_lamports": cfg.stake_lamports})
    assert ok, why
    land_buy(ex, rpc)
    port.poll()
    ok, why = port.sell({"mint": MINT}, "time_24h")
    assert ok, why
    sig0 = ex.state.pending[MINT]["signature"]
    for _ in range(5):
        clock.t += pl.REBROADCAST_MS + 1
        ex.advance_pending()
    assert ex.state.open[MINT]["sell_attempts"] == 0  # still in flight
    assert ex.state.pending[MINT]["signature"] == sig0
    assert ex.state.pending[MINT]["sends"] >= 2  # rebroadcasts of the SAME tx
    assert len(_unique_sigs(rpc)) == 2  # 1 buy + 1 sell


@pytest.mark.skipif(pytest.importorskip("solders", reason="solders required") is None, reason="solders")
def test_abandoned_survives_executor_reload(hermetic_live_dir):
    from solders.keypair import Keypair

    port, ex, rpc, clock, cfg, _ = _make_floor_port(hermetic_live_dir)
    kp = ex._kp
    ok, why = port.buy({"order_id": "o1", "mint": MINT, "decision_ms": clock(), "stake_lamports": cfg.stake_lamports})
    assert ok, why
    land_buy(ex, rpc)
    port.sell({"mint": MINT}, "time_24h")
    for _ in range(20):
        if MINT in ex.state.pending:
            land_sell(ex, rpc, proceeds=0, err=FAIL_ERR, fee=F.SEND_FEE_LAMPORTS)
        clock.t += 11 * 60_000
        port.poll()
    assert ex.state.open[MINT]["abandoned"] is True
    n_sells = len(_unique_sigs(rpc)) - 1
    inner2 = B.make_live_port(cfg, rpc, kp, now_ms=clock)
    port2 = X.FloorLanePort(inner2, cfg)
    port2.pre_buy = lambda _o: None
    ex2 = inner2.ex
    assert ex2.state.open[MINT]["abandoned"] is True
    assert ex2.state.open[MINT]["sell_attempts"] >= 10
    for _ in range(10):
        clock.t += 11 * 60_000
        port2.poll()
        port2.sell({"mint": MINT}, "time_24h")
    assert len(_unique_sigs(rpc)) - 1 == n_sells == 10


@pytest.mark.skipif(pytest.importorskip("solders", reason="solders required") is None, reason="solders")
def test_clearing_abandoned_allows_an_eleventh_sell_so_the_cap_is_the_flag(hermetic_live_dir):
    """Not a production path. Shows the cap is the abandoned flag, not _start_sell."""
    port, ex, rpc, clock, cfg, _ = _make_floor_port(hermetic_live_dir)
    ok, why = port.buy({"order_id": "o1", "mint": MINT, "decision_ms": clock(), "stake_lamports": cfg.stake_lamports})
    assert ok, why
    land_buy(ex, rpc)
    port.sell({"mint": MINT}, "time_24h")
    for _ in range(20):
        if MINT in ex.state.pending:
            land_sell(ex, rpc, proceeds=0, err=FAIL_ERR, fee=F.SEND_FEE_LAMPORTS)
        clock.t += 11 * 60_000
        port.poll()
    assert ex.state.open[MINT]["abandoned"] is True
    assert len(_unique_sigs(rpc)) - 1 == 10
    ex.state.open[MINT]["abandoned"] = False
    clock.t += 11 * 60_000
    port.poll()
    assert MINT in ex.state.pending
    assert len(_unique_sigs(rpc)) - 1 == 11


def test_capped_pnl_refuses_the_qp_seven_up_eighth_case(tmp_path):
    """The prior live check credited unrealised gain; 3710e1af uses min(mark, stake)."""
    lc = H.LaneConfig(tmp_path / "l.jsonl", tmp_path / "STOP", X.LANE_BOT_ID, STAKE, 8, BUDGET, 100_000_000, RESERVE, 60.0)
    led = X.FloorLaneLedger(lc)
    cash = -7 * (STAKE + 3_000_000) - 60_000_000
    op = {f"{lc.bot_id}:m{i}": {"stake": STAKE, "filled": True, "ts_ms": 0, "signature": f"s{i}"} for i in range(7)}
    marks = {f"m{i}": (2 * STAKE, 0) for i in range(7)}
    pnl = cash + 7 * 2 * STAKE
    view = H.LaneView(cash=cash, pnl=pnl, cost=cash + 7 * STAKE, open=op, snaps={})
    led._marks = marks
    assert view.pnl - (view.open_stakes + STAKE + RESERVE * 8) > -BUDGET
    assert led.evaluate(view, 0, STAKE) == "lane_budget_presend"
    assert led.capped_pnl(view, 0) == cash + 7 * STAKE


def test_full_wipe_property_includes_configured_fee_reserve(tmp_path):
    import random

    lc = H.LaneConfig(tmp_path / "l.jsonl", tmp_path / "STOP", X.LANE_BOT_ID, STAKE, 8, BUDGET, 100_000_000, RESERVE, 60.0)
    led = X.FloorLaneLedger(lc)
    rng = random.Random(1)
    accepted = 0
    for _ in range(2000):
        n = rng.randint(0, 7)
        stakes = [rng.choice([20_000_000, 25_000_000, 30_000_000]) for _ in range(n)]
        filled = [rng.random() < 0.8 for _ in range(n)]
        cash = -sum(s for s, f in zip(stakes, filled) if f) - rng.randint(0, 250_000_000) + rng.randint(0, 150_000_000)
        marks_v = [rng.choice([None, 0, s // 2, s, 2 * s, 5 * s]) for s in stakes]
        op = {f"{lc.bot_id}:m{i}": {"stake": st, "filled": f, "ts_ms": 0, "signature": f"s{i}"} for i, (st, f) in enumerate(zip(stakes, filled))}
        md = {f"m{i}": (v, 0) for i, v in enumerate(marks_v) if v is not None}
        value = sum(md[f"m{i}"][0] for i in range(n) if f"m{i}" in md and filled[i])
        view = H.LaneView(cash=cash, pnl=cash + value, cost=cash + sum(st for st, f in zip(stakes, filled) if f), open=op, snaps={})
        led._marks = md
        stake = rng.choice([20_000_000, 25_000_000, 30_000_000])
        if led.evaluate(view, 0, stake) is None:
            accepted += 1
            unfilled = sum(s for s, f in zip(stakes, filled) if not f)
            worst = cash - unfilled - stake
            assert worst > -BUDGET + RESERVE * (n + 1)
    assert accepted > 100
