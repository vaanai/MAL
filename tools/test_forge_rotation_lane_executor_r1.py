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
from tools.test_forge_rotation_lane_executor import (BOT, BOT2, DAY, R10, feed, floor_paths, intent, loss, mk, mkled, put, raw_cfg, refusals, reserve, started)  # noqa: F401  (floor_paths: autouse fixture)


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


def test_rotation_backlog_pending_at_start_never_strands_an_open_position(tmp_path, monkeypatch):
    """B3: a pending backlog at start is drained; if it will not drain the process still STARTS (exits run), entries are refused until the drain works, and the ladder is then checked."""
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = started(tmp_path)
    lg.entry(T0, "o1")
    feed(br, clk)
    inner.land_buy("MP")
    br._poll_ports()
    br.release_lane_lock()
    led = br.lane
    led._write_line(led.backlog_path, {"schema": "h5-lane-ledger.v1", "ev": "fee", "bot_id": BOT, "mint": "ZZ", "ts_ms": T0 + 1, "sol_in_lamports": 0, "sol_out_lamports": 1000, "row_id": "bl1"})
    real_drain = R.RotationLedger.drain
    monkeypatch.setattr(R.RotationLedger, "drain", lambda self: (_ for _ in ()).throw(OSError("busy")))
    br2, _ = mk(tmp_path, clk, inner=inner)  # starts: no SystemExit
    assert br2._ladder_deferred  # the drain failed at start: the ladder check is deferred (the start row's own write drained the first backlog row)
    br2.lane._write_line(br2.lane.backlog_path, {"schema": "h5-lane-ledger.v1", "ev": "fee", "bot_id": BOT, "mint": "ZY", "ts_ms": T0 + 2, "sol_in_lamports": 0, "sol_out_lamports": 1, "row_id": "bl2"})
    assert br2.lane.backlog_pending()
    lg.entry(T0 + 5_000, "o2", pool="Q")
    lg.exit(T0 + 6_000, "o1", reason="tp")
    feed(br2, clk)
    assert inner.sells == [("MP", "tp")]  # the exit of the open position is processed
    assert len(inner.buys) == 1 and refusals(br2)[-1]["why"] == "lane_backlog_pending"  # the entry is refused while the backlog is pending
    monkeypatch.setattr(R.RotationLedger, "drain", real_drain)
    br2.tick()  # the bridge tick drains
    assert not br2.lane.backlog_pending()
    lg.entry(T0 + 20_000, "o3", pool="R")
    feed(br2, clk)
    assert len(inner.buys) == 2 and not br2._ladder_deferred  # ladder verified on the complete ledger, then entries are allowed
    br2.release_lane_lock()


def test_rotation_refused_stake_writes_no_start_row_and_the_cap_stays_040(tmp_path, monkeypatch):
    """B4: a start held back by an undrained backlog writes NO start row; the deferred ladder check refuses 0.25 (no step record), and a restart still refuses; the book cap stays 0.40."""
    led = mkled(tmp_path)  # the book's last start row: 0.10
    led._write_line(led.backlog_path, {"schema": "h5-lane-ledger.v1", "ev": "fee", "bot_id": BOT, "mint": "ZZ", "ts_ms": T0 - 5, "sol_in_lamports": 0, "sol_out_lamports": 1, "row_id": "bl1"})
    real_drain = R.RotationLedger.drain
    monkeypatch.setattr(R.RotationLedger, "drain", lambda self: (_ for _ in ()).throw(OSError("busy")))
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk, raw=raw_cfg(tmp_path, stake=250_000_000))  # starts (exits would run); 0.25 with no step record
    assert br._ladder_deferred
    monkeypatch.setattr(R.RotationLedger, "drain", real_drain)
    lg = started(tmp_path)
    lg.entry(T0, "o1")
    feed(br, clk)
    assert inner.buys == [] and refusals(br)[-1]["why"] == "rotation_ladder:up_step_needs_step_record_sha256" and br._rot_counts["ladder_refused"] >= 1
    starts = [r for r in br.lane.read() if r["ev"] == "start" and r["bot_id"] == BOT]
    assert [r["stake_lamports"] for r in starts] == [R10]  # no 0.25 start row exists
    assert br.lane.book_cap(R.fold_rotation(br.lane.read())) == 400_000_000
    br.release_lane_lock()
    with pytest.raises(SystemExit, match="up_step_needs_step_record_sha256"):  # a restart (backlog drained) still refuses
        mk(tmp_path, Clock(T0), raw=raw_cfg(tmp_path, stake=250_000_000))
    assert mkled(tmp_path, start=False).book_cap(R.fold_rotation(mkled(tmp_path, start=False).read())) == 400_000_000


def test_rotation_new_book_stuck_backlog_025_leaves_no_start_row_and_010_restart_is_allowed(tmp_path, monkeypatch):
    """QP M4 probe: a NEW book, a stuck backlog, started at 0.25 with no step record."""
    led = mkled(tmp_path, start=False)  # this book has no start row
    led._write_line(led.backlog_path, {"schema": "h5-lane-ledger.v1", "ev": "fee", "bot_id": BOT, "mint": "ZZ", "ts_ms": T0 - 5, "sol_in_lamports": 0, "sol_out_lamports": 1, "row_id": "bq1"})
    real_drain = R.RotationLedger.drain
    monkeypatch.setattr(R.RotationLedger, "drain", lambda self: (_ for _ in ()).throw(OSError("busy")))
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk, raw=raw_cfg(tmp_path, stake=250_000_000))
    assert br._ladder_deferred
    monkeypatch.setattr(R.RotationLedger, "drain", real_drain)
    started(tmp_path).entry(T0, "o1")
    feed(br, clk)
    assert inner.buys == [] and refusals(br)[-1]["why"] == "rotation_ladder:new_book_must_start_at_100000000"
    assert [r for r in br.lane.read() if r["ev"] == "start" and r["bot_id"] == BOT] == []  # no 0.25 start row persists
    assert br.lane.book_cap(R.fold_rotation(br.lane.read())) == 400_000_000
    br.release_lane_lock()
    with pytest.raises(SystemExit, match="new_book_must_start_at_100000000"):  # a later 0.25 restart with no record is refused
        mk(tmp_path, Clock(T0), raw=raw_cfg(tmp_path, stake=250_000_000))
    ok, _ = mk(tmp_path, Clock(T0))  # the legal 0.10 restart IS allowed
    assert [r["stake_lamports"] for r in ok.lane.read() if r["ev"] == "start" and r["bot_id"] == BOT] == [R10]
    ok.release_lane_lock()


def test_rotation_normal_start_writes_its_start_row_at_once(tmp_path):
    br, _ = mk(tmp_path, Clock(T0))
    assert not br._ladder_deferred
    assert [r["stake_lamports"] for r in br.lane.read() if r["ev"] == "start"] == [R10]
    br.release_lane_lock()


def test_rotation_wallet_floor_counts_other_books_in_flight_buys(tmp_path):
    clk = Clock(T0)
    br, _ = mk(tmp_path, clk)
    other = mkled(tmp_path, bot=BOT2)
    intent(other, BOT2, "X0")  # another book's send, not landed: its stake has not left the wallet
    put(other, BOT2, "fill", "X1", sout=1, ok=True)
    intent(other, BOT2, "X2")
    put(other, BOT2, "fill", "X2", sout=R10 + 2_260_000, ok=True)  # landed: already in the balance, not counted
    base = R10 + 160_000 + 2_100_000 + 1_600_000 + 250_000_000
    assert br.floor_problem(R10, base + R10) is None and br.floor_problem(R10, base + R10 - 1) == "wallet_floor"
    br.release_lane_lock()


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
