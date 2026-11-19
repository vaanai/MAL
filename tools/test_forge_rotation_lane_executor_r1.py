"""PR #1466 review round 1 (QP M1-M3, reviewer B1-B2): pinned rotation paths, exact cap constants, the disclosed daily marked arm, the live-port class guard, FLOOR path refusals.

  /data/mal/venv/bin/python -m pytest tools/test_forge_rotation_lane_executor_r1.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tools.forge import rotation_lane_executor as R
from tools.test_floor_arena_executor import Clock, _env  # noqa: F401  (autouse fixture)
from tools.test_floor_lane_executor import T0, FakeInner
from tools.test_forge_rotation_lane_executor import (BOT, BOT2, DAY, R10, floor_paths, intent, loss, mk, mkled, put, raw_cfg, reserve)  # noqa: F401  (floor_paths: autouse fixture)


def _no_paths(tmp, **kw):
    raw = raw_cfg(tmp, **kw)
    for k in ("ledger_path", "stop_path", "book_stop_path"):
        raw["lane"].pop(k)
    return raw


def _repin(monkeypatch, **kw):
    for k, v in kw.items():
        monkeypatch.setattr(R, k, str(v))


def _repin_back(mod, tmp_path):
    mod.FLOOR_LEDGER_PATHS = (str(tmp_path / "floor" / "lane-ledger.jsonl"),)
    mod.FLOOR_STOP_PATHS = (str(tmp_path / "floor" / "STOP"), str(tmp_path / "wallet-STOP"), str(tmp_path / "wallet-HALT"))
    mod.FLOOR_LANE_DIRS = (str(tmp_path / "floor"),)
    mod.WALLET_WIDE_DIR = str(tmp_path)
    mod.ROTATION_LEDGER_PATH = str(tmp_path / "rot" / "ledger.jsonl")
    mod.ROTATION_STOP_PATH = str(tmp_path / "rot" / "STOP")


def test_rotation_ledger_and_lane_stop_paths_are_pinned_in_code(tmp_path):
    import importlib

    pristine = importlib.reload(R)  # the literal pins, before the fixture moved them under tmp
    assert (pristine.ROTATION_LEDGER_PATH, pristine.ROTATION_STOP_PATH) == ("/var/lib/mal-live/rotation-lane/lane-ledger.jsonl", "/var/lib/mal-live/rotation-lane/STOP")
    _repin_back(pristine, tmp_path)
    cfg, lane, _ok, pins = R.make_config(_no_paths(tmp_path))  # omitted: the pinned values
    assert lane.ledger_path == Path(R.ROTATION_LEDGER_PATH) and lane.stop_path == Path(R.ROTATION_STOP_PATH)
    assert pins["rot"].book_stop_path == Path(R.ROTATION_STOP_PATH).parent / f"STOP-{BOT}"
    for k in ("ledger_path", "stop_path", "book_stop_path"):
        with pytest.raises(ValueError, match="pinned rotation path"):
            R.make_config(raw_cfg(tmp_path, lane={k: str(tmp_path / "elsewhere" / k)}))
    assert R.run(raw_cfg(tmp_path, lane={"ledger_path": str(tmp_path / "x" / "l.jsonl")}), live_flag=True, root_checks=False, port_factory=lambda c: pytest.fail("port built")) == 2


def test_rotation_a_fresh_ledger_path_cannot_shed_the_lifetime_stop(tmp_path):
    led = mkled(tmp_path)
    loss(led, "other-1", 2_950_000_000, ts=T0 - 40 * DAY)
    assert reserve(led, "M0") == "rotation_lifetime_stop"
    with pytest.raises(ValueError):
        mkled(tmp_path, bot=BOT2, lane={"ledger_path": str(tmp_path / "fresh" / "ledger.jsonl"), "stop_path": str(tmp_path / "fresh" / "STOP")})
    assert reserve(mkled(tmp_path, bot=BOT2, start=False), "N0") == "rotation_lane_stop_file"  # the pinned paths: the STOP survives


def test_rotation_a_fresh_ledger_path_cannot_shed_the_month_budget(tmp_path):
    led = mkled(tmp_path)
    loss(led, "other-1", 1_450_000_000)
    assert reserve(led, "M0") == "rotation_month_budget"
    with pytest.raises(ValueError):
        mkled(tmp_path, bot=BOT2, lane={"ledger_path": str(tmp_path / "m2" / "ledger.jsonl")})
    assert reserve(mkled(tmp_path, bot=BOT2, start=False), "N0") == "rotation_month_budget"  # same pinned ledger: still refused


def test_rotation_caps_pinned_exactly():
    assert R.ROTATION_BOOK_LOSS_CAP_FIRST_LAMPORTS == 400_000_000 and R.ROTATION_BOOK_LOSS_CAP_STEPPED_LAMPORTS == 750_000_000
    assert R.ROTATION_FUNDED_MIN_LAMPORTS == 500_000_000 and R.ROTATION_FUNDED_MAX_LAMPORTS == 3_000_000_000
    assert R.ROTATION_MAX_TRADES_PER_DAY == 30 and R.ROTATION_RPC_RPS == 2.0
    assert R.STEP_DOWN == {250_000_000: (125_000_000, 50_000_000), 125_000_000: (50_000_000,), 100_000_000: (50_000_000,)}


def test_rotation_daily_marked_arm_refuses_other_books_early_disclosed_not_changed(tmp_path):
    """QP M3: spec-literal (DEC-036 s5 item 4 'as in FloorLaneLedger.evaluate'). Another book's FILLED open positions count 0 in the marked arm, so about 0.30 SOL of its open stake blocks the others for
    the rest of the UTC day, short of the 1.00 SOL exposure envelope. It fails safe. A DEC-036 erratum may change it; this test pins today's behaviour."""
    a, b = mkled(tmp_path, funded=3_000_000_000), mkled(tmp_path, bot=BOT2, funded=3_000_000_000)
    assert reserve(a, "A0") is None  # the day's snapshot (pnl 0)
    for i in range(3):
        intent(b, BOT2, f"B{i}")
        put(b, BOT2, "fill", f"B{i}", sout=R10 + 2_260_000, ok=True)  # 3 filled positions, no loss at all
    assert a.check({}, T0 + 1000, R10) == "rotation_daily_stop"
    assert 3 * R10 < R.ROTATION_OPEN_EXPOSURE_MAX_LAMPORTS


def test_rotation_live_start_needs_the_expected_live_port_class(tmp_path):
    cfg, lane, live_ok, pins = R.make_config(raw_cfg(tmp_path))
    (tmp_path / f"LIVE_OK-{BOT}").write_text(BOT + "\n")
    with pytest.raises(SystemExit, match="FloorLanePort"):
        R.RotationLaneBridge(cfg, FakeInner(), lane, pins, now_ms=Clock(T0), live_ok_path=live_ok, root_checks=False)  # a live port that is not the lane port: no pre_buy gate
    assert R.run(raw_cfg(tmp_path), live_flag=True, root_checks=False, port_factory=lambda c: FakeInner(), now_ms=Clock(T0)) == 2


def test_rotation_start_refused_while_the_ledger_backlog_is_pending(tmp_path):
    led = mkled(tmp_path, start=False)
    led._write_line(led.backlog_path, {"ev": "start", "bot_id": BOT, "stake_lamports": R10, "ts_ms": T0, "mint": "", "row_id": "r1"})
    with pytest.raises(SystemExit, match="backlog pending"):
        mk(tmp_path, Clock(T0))


def test_rotation_refuses_paths_in_floor_lane_dir_incl_backlog_and_wallet_wide_files(tmp_path, monkeypatch):
    floor_dir = tmp_path / "floor"
    for bad in (floor_dir / "lane-ledger.jsonl.backlog.floor-lane-1", floor_dir / "lane-ledger.jsonl", floor_dir / "anything", tmp_path / "wallet-STOP", tmp_path / "wallet-HALT", tmp_path / "x-STOP"):
        _repin(monkeypatch, ROTATION_LEDGER_PATH=bad)
        with pytest.raises(ValueError, match="ledger_path refused"):
            R.make_config(_no_paths(tmp_path))
    _repin(monkeypatch, ROTATION_LEDGER_PATH=tmp_path / "rot" / "ledger.jsonl")
    for bad in (floor_dir / "STOP", floor_dir / "other-STOP", tmp_path / "wallet-STOP", tmp_path / "wallet-HALT"):
        _repin(monkeypatch, ROTATION_STOP_PATH=bad)
        with pytest.raises(ValueError, match="refused"):
            R.make_config(_no_paths(tmp_path))
    assert R.floor_path_problem(str(tmp_path / "rot" / "ledger.jsonl")) is None


def test_rotation_refuses_a_symlinked_or_dotdot_path_into_floor(tmp_path):
    (tmp_path / "rot").mkdir(exist_ok=True)
    link = tmp_path / "rot" / "ledger.jsonl"
    link.symlink_to(R.FLOOR_LEDGER_PATHS[0])
    assert R.floor_path_problem(str(link))
    assert R.floor_path_problem(str(tmp_path / "rot" / ".." / "floor" / "lane-ledger.jsonl"))


def test_rotation_refuses_a_hardlink_of_floors_ledger(tmp_path, monkeypatch):
    (tmp_path / "rot").mkdir(exist_ok=True)
    hard = tmp_path / "rot" / "hard.jsonl"
    os.link(R.FLOOR_LEDGER_PATHS[0], hard)
    assert R.floor_path_problem(str(hard)) == "the same inode as a FLOOR file"
    _repin(monkeypatch, ROTATION_LEDGER_PATH=hard)
    with pytest.raises(ValueError, match="same inode"):
        R.make_config(_no_paths(tmp_path))


def test_rotation_funded_and_wallet_floor_must_be_whole_numbers(tmp_path):
    for k, v in (("funded_lamports", 1_500_000_000.5), ("funded_lamports", True), ("wallet_floor_lamports", 3e8 + 0.5), ("wallet_floor_lamports", True)):
        with pytest.raises(ValueError):
            R.make_config(raw_cfg(tmp_path, **{k: v}))
