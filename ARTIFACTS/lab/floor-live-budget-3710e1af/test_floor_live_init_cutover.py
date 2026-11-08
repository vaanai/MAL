"""Adversarial dry-to-live INITIALIZATION review of FLOOR at commit 3710e1af.

Synthetic only: throwaway Keypair, probe FakeRpc, no network, no host, no wallet file.
Does not import or send on a real RPC. Does not edit production executors.

Run from repo root:
  python -m pytest ARTIFACTS/lab/floor-live-budget-3710e1af/test_floor_live_init_cutover.py -q
"""

from __future__ import annotations

import collections
import dataclasses
import json
from pathlib import Path

import pytest

from tools import arena_live_bridge as B
from tools import floor_arena_executor as F
from tools import floor_lane_executor as X
from tools import probe_executor as pe
from tools.test_floor_arena_executor import Clock, Log, meta as floor_meta
from tools.test_floor_lane_executor import FakeInner, lane_raw, ledger_rows, write_live_ok
from tools.test_probe_executor import Clock as PClock
from tools.test_probe_executor import MINT
from tools.test_probe_live import LiveRpc

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SHA = "3710e1af8cf434faca570046de896a7702464709"
T0 = 1_700_000_000_000


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    monkeypatch.setattr(
        "tools.arena_paper.shutil.disk_usage",
        lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500),
    )


def test_review_is_pinned_to_the_requested_commit():
    """HEAD may have moved; the send/state files must still match 3710e1af."""
    import subprocess

    files = (
        "tools/floor_lane_executor.py",
        "tools/floor_arena_executor.py",
        "tools/probe_live.py",
        "tools/arena_live_bridge.py",
        "docs/runbooks/arena-live-bridge.md",
        "scripts/mal-fast/floor-lane-live.json.example",
        "scripts/mal-fast/README.md",
    )
    for rel in files:
        pinned = subprocess.check_output(["git", "show", f"{SHA}:{rel}"], cwd=REPO)
        assert pinned == (REPO / rel).read_bytes(), rel


# -- documented seed (existing BridgeState.load/save; no production helper) -------------------------


def seed_live_cursor_from_dry(state_dir: Path) -> B.BridgeState:
    """The only live-init shape this review defends: copy the decision-log cursor, never orders.

    Uses existing `BridgeState.load` / `save`. Refuses if dry is latched or already has live=true orders.
    """
    dry = B.BridgeState.load(state_dir / "bridge-state-dry.json", "dry")
    if dry.latches:
        raise AssertionError(f"cutover refused: dry latches {dry.latches}")
    if any(o.get("live") for o in dry.orders.values()):
        raise AssertionError("cutover refused: dry state contains live=true orders")
    live = B.BridgeState(mode="live", prev=dry.prev, seq=dry.seq, cursor=dict(dry.cursor), start_seen=dry.start_seen)
    live.save(state_dir / "bridge-state-live.json")
    fs_path = state_dir / "floor-state-dry.json"
    fs = json.loads(fs_path.read_text()) if fs_path.exists() else {}
    (state_dir / "floor-state-live.json").write_text(
        json.dumps(
            {
                "schema": F.FS_SCHEMA,
                "first_send_ms": None,
                "gap_hold_until_ms": int(fs.get("gap_hold_until_ms") or 0),
                "starts_seen": int(fs.get("starts_seen") or 0),
                "gaps": list(fs.get("gaps") or []),
                "exits_not_followed": {},
                "late_exits": [],
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return live


def _stop_halt(cfg: B.Config, tmp: Path) -> B.Config:
    return dataclasses.replace(cfg, stop_file=str(tmp / "nstop"), halt_file=str(tmp / "nhalt"))


def dry_bridge(tmp: Path, clk: Clock, balance: int = 600_000_000):
    raw = lane_raw(tmp, mode="dry")
    raw["dry_balance_lamports"] = balance
    cfg, lane, _ = X.make_config(raw)
    cfg = _stop_halt(cfg, tmp)
    port = B.DryPort(balance)
    br = X.FloorLaneBridge(cfg, port, lane, now_ms=clk, root_checks=False)
    return br, port


def live_bridge(tmp: Path, clk: Clock, inner=None, **kw):
    write_live_ok(tmp)
    cfg, lane, live_ok = X.make_config(lane_raw(tmp))
    cfg = _stop_halt(cfg, tmp)
    inner = inner or FakeInner()
    port = X.FloorLanePort(inner, cfg)
    br = X.FloorLaneBridge(cfg, port, lane, now_ms=clk, live_ok_path=live_ok, root_checks=False, **kw)
    return br, inner


def feed(br, clk, ms=None):
    rows = br.read_new_rows()
    for r in rows:
        clk.t = r["ms"] + 50 if ms is None else ms
        br.handle_row(r)
        br._poll_ports()
    br._save()
    return rows


def run_dry_like_host(tmp: Path, clk: Clock):
    """Reproduce the reported dry executor: two wallet_floor refusals, then one DryPort buy_landed."""
    br, port = dry_bridge(tmp, clk, balance=1_000_000)
    lg = Log(tmp)
    lg.start(T0 - 1000)
    lg.entry(T0, "o-hist-1", pool="P1")
    lg.entry(T0 + 1, "o-hist-2", pool="P2")
    feed(br, clk)
    why = [json.loads(x)["why"] for x in br.log_path.read_text().splitlines() if '"entry_refused"' in x]
    assert why == ["wallet_floor", "wallet_floor"], why
    port._balance = 600_000_000
    lg.entry(T0 + 2, "o-open", pool="P3")
    feed(br, clk)
    o = br.state.orders["o-open"]
    assert o["status"] == "open" and o["live"] is False
    assert "o-hist-1" not in br.state.orders and "o-hist-2" not in br.state.orders
    assert br.state.seq == 3 and br.state.start_seen is True
    assert br.lane is None
    br._save()
    return br, port, lg


# -- files / markers --------------------------------------------------------------------------------


def test_live_executor_uses_mode_separated_files_and_fresh_probe_state(tmp_path):
    clk = Clock(T0)
    br, _, _ = run_dry_like_host(tmp_path, clk)
    sd = Path(br.cfg.state_dir)
    assert br.state_path == sd / "bridge-state-dry.json"
    assert br.fs_path == sd / "floor-state-dry.json"
    assert br.log_path == sd / "bridge-log-dry.jsonl"
    assert not (sd / "bridge-state-live.json").exists()
    assert not (sd / "floor-state-live.json").exists()
    assert not (sd / "probe").exists()
    # live process, same state_dir, empty live files: different paths, probe dir is created by make_live_port only
    br2, _ = live_bridge(tmp_path, clk)
    assert br2.state_path == sd / "bridge-state-live.json"
    assert br2.fs_path == sd / "floor-state-live.json"
    assert br2.log_path == sd / "bridge-log-live.jsonl"
    assert br2.state.seq == -1 and br2.state.orders == {}
    assert br2.lane is not None
    br2.release_lane_lock()


def test_dry_process_lock_does_not_cover_live_so_operator_must_stop_dry(tmp_path):
    """take_bot_lock is live-only. A leftover dry unit can run beside live; the code will not serialize them."""
    clk = Clock(T0)
    dry, _ = dry_bridge(tmp_path, clk)
    live, _ = live_bridge(tmp_path, clk)  # would SystemExit if dry held the bot lock
    assert dry.lane is None and live.lane is not None
    live.release_lane_lock()


# -- naive paths are NO-GO --------------------------------------------------------------------------


def test_naive_empty_live_replays_historical_entries_as_real_buys(tmp_path):
    """Switching mode to live with no bridge-state-live.json re-reads seq 0..3.
    The two wallet_floor paper refusals become real buys if the live wallet clears the floor."""
    clk = Clock(T0)
    run_dry_like_host(tmp_path, clk)
    br, inner = live_bridge(tmp_path, clk)
    feed(br, clk)
    bought = [o["order_id"] for o in inner.buys]
    assert bought == ["o-hist-1", "o-hist-2", "o-open"], bought
    assert all(br.state.orders[oid]["live"] is True for oid in bought)
    intents = [r for r in ledger_rows(tmp_path) if r.get("ev") == "send_intent"]
    assert {r["signature"] for r in intents} == {"intent:o-hist-1", "intent:o-hist-2", "intent:o-open"}
    br.release_lane_lock()


def test_naive_copy_dry_orders_into_live_poisons_ledger_on_paper_clock_exit(tmp_path):
    """Copying bridge-state-dry.json → live and rewriting mode keeps live=false open orders.
    FloorLaneBridge.apply_events books DryPort sell_landed (no _row) into the REAL lane ledger
    and latches ata_rent_not_refunded — new live entries stop. No chain sell (routed to DryPort)."""
    clk = Clock(T0)
    dry, _port, _ = run_dry_like_host(tmp_path, clk)
    sd = Path(dry.cfg.state_dir)
    raw = json.loads((sd / "bridge-state-dry.json").read_text())
    raw["mode"] = "live"
    (sd / "bridge-state-live.json").write_text(json.dumps(raw, separators=(",", ":"), sort_keys=True))
    (sd / "floor-state-live.json").write_text((sd / "floor-state-dry.json").read_text())
    br, inner = live_bridge(tmp_path, clk)
    o = br.state.orders["o-open"]
    assert o["live"] is False and o["status"] == "open"
    clk.t = int(o["decision_ms"]) + F.HOLD_MS + 5_000
    br.tick()
    br._poll_ports()
    assert inner.sells == []  # live port never asked to sell a token the wallet does not hold
    assert any(w["side"] == "sell" and w["order_id"] == "o-open" for w in br.dry_port.would_send)
    exits = [r for r in ledger_rows(tmp_path) if r.get("ev") == "exit"]
    assert len(exits) == 1 and exits[0].get("estimated") is True
    assert X.LATCH_NO_ATA_REFUND in br.state.latches
    br.release_lane_lock()


def test_inconsistent_seq_vs_cursor_fail_closes_instead_of_replaying(tmp_path):
    """Byte cursor is what skips old rows. seq-1 with cursor still at EOF does not reread history;
    the next new row then chain_breaks (fail closed). Replay needs prev+seq+cursor reset together."""
    clk = Clock(T0)
    dry, _, _ = run_dry_like_host(tmp_path, clk)
    live_st = seed_live_cursor_from_dry(Path(dry.cfg.state_dir))
    live_st.seq = dry.state.seq - 1
    live_st.save(Path(dry.cfg.state_dir) / "bridge-state-live.json")
    br, inner = live_bridge(tmp_path, clk)
    feed(br, clk)
    assert inner.buys == []
    lg = Log(tmp_path)
    lg.entry(T0 + 4_000, "o-fresh", pool="P-new")
    clk.t = T0 + 4_050
    feed(br, clk, clk.t)
    assert inner.buys == []
    assert "chain_break" in br.state.latches
    br.release_lane_lock()


def test_copy_dry_file_without_rewriting_mode_is_refused(tmp_path):
    clk = Clock(T0)
    dry, _, _ = run_dry_like_host(tmp_path, clk)
    sd = Path(dry.cfg.state_dir)
    (sd / "bridge-state-live.json").write_bytes((sd / "bridge-state-dry.json").read_bytes())
    with pytest.raises(SystemExit, match="does not match run mode"):
        live_bridge(tmp_path, clk)


def test_copying_bought_without_orders_permanently_blocks_that_mint(tmp_path):
    clk = Clock(T0)
    dry, _, _ = run_dry_like_host(tmp_path, clk)
    live_st = seed_live_cursor_from_dry(Path(dry.cfg.state_dir))
    live_st.bought = list(dry.state.bought)
    live_st.save(Path(dry.cfg.state_dir) / "bridge-state-live.json")
    br, inner = live_bridge(tmp_path, clk)
    lg = Log(tmp_path)
    lg.entry(T0 + 10_000, "o-reentry", pool="P3")  # same mint MP3, after paper would have exited
    clk.t = T0 + 10_050
    feed(br, clk, clk.t)
    assert inner.buys == []
    why = [json.loads(x)["why"] for x in br.log_path.read_text().splitlines() if '"entry_refused"' in x]
    assert "already_bought" in why
    br.release_lane_lock()


# -- defended procedure -----------------------------------------------------------------------------


def test_cursor_seed_does_not_replay_and_ignores_paper_exits(tmp_path):
    clk = Clock(T0)
    dry, _, _ = run_dry_like_host(tmp_path, clk)
    seed_live_cursor_from_dry(Path(dry.cfg.state_dir))
    br, inner = live_bridge(tmp_path, clk)
    assert br.state.seq == 3 and br.state.orders == {} and br.state.bought == []
    feed(br, clk)
    assert inner.buys == [] and inner.sells == []
    lg = Log(tmp_path)
    lg.exit(T0 + 3_600_000, "o-hist-1", pool="P1")
    lg.exit(T0 + 3_600_001, "o-hist-2", pool="P2")
    lg.exit(T0 + 3_600_002, "o-open", pool="P3")
    clk.t = T0 + 3_600_050
    feed(br, clk, clk.t)
    assert inner.sells == []
    assert br.state.counts.get("exit_unknown_order") == 3
    assert not [r for r in ledger_rows(tmp_path) if r.get("ev") in ("fill", "exit")]
    assert "ata_rent_not_refunded" not in br.state.latches
    br.release_lane_lock()


def test_cursor_seed_then_fresh_entry_is_a_real_live_buy(tmp_path):
    clk = Clock(T0)
    dry, _, _ = run_dry_like_host(tmp_path, clk)
    seed_live_cursor_from_dry(Path(dry.cfg.state_dir))
    br, inner = live_bridge(tmp_path, clk)
    lg = Log(tmp_path)
    lg.entry(T0 + 5_000, "o-fresh", pool="P-new")
    clk.t = T0 + 5_050
    feed(br, clk, clk.t)
    assert [o["order_id"] for o in inner.buys] == ["o-fresh"]
    assert br.state.orders["o-fresh"]["live"] is True
    assert "o-open" not in br.state.orders
    intents = [r for r in ledger_rows(tmp_path) if r.get("ev") == "send_intent"]
    assert [r["signature"] for r in intents] == ["intent:o-fresh"]
    br.release_lane_lock()


def test_live_restart_after_seed_does_not_replay(tmp_path):
    clk = Clock(T0)
    dry, _, _ = run_dry_like_host(tmp_path, clk)
    seed_live_cursor_from_dry(Path(dry.cfg.state_dir))
    br, inner = live_bridge(tmp_path, clk)
    feed(br, clk)
    seq, prev, cursor = br.state.seq, br.state.prev, dict(br.state.cursor)
    br.release_lane_lock()
    inner2 = FakeInner()
    br2, _ = live_bridge(tmp_path, clk, inner=inner2)
    feed(br2, clk)
    assert inner2.buys == []
    assert br2.state.seq == seq and br2.state.prev == prev and br2.state.cursor == cursor
    br2.release_lane_lock()


def test_later_dry_resume_exits_the_virtual_position_without_touching_live_ledger(tmp_path):
    clk = Clock(T0)
    dry, port, _ = run_dry_like_host(tmp_path, clk)
    seed_live_cursor_from_dry(Path(dry.cfg.state_dir))
    live, inner = live_bridge(tmp_path, clk)
    lg = Log(tmp_path)
    lg.exit(T0 + 3_600_000, "o-open", pool="P3")
    clk.t = T0 + 3_600_050
    feed(dry, clk, clk.t)
    assert any(w["side"] == "sell" and w["order_id"] == "o-open" for w in port.would_send)
    assert dry.state.orders["o-open"]["status"] == "closed"
    feed(live, clk, clk.t)
    assert inner.sells == []
    assert not [r for r in ledger_rows(tmp_path) if r.get("ev") in ("fill", "exit")]
    live.release_lane_lock()


def test_seeded_starts_seen_gap_holds_on_the_next_host_start(tmp_path):
    clk = Clock(T0)
    dry, _, _ = run_dry_like_host(tmp_path, clk)
    seed_live_cursor_from_dry(Path(dry.cfg.state_dir))
    br, inner = live_bridge(tmp_path, clk)
    assert br.fs["starts_seen"] == 1 and br.fs["first_send_ms"] is None
    lg = Log(tmp_path)
    lg.start(T0 + 8_000)
    lg.entry(T0 + 8_100, "o-after-restart", pool="P-r")
    clk.t = T0 + 8_150
    feed(br, clk, clk.t)
    assert br.fs["starts_seen"] == 2
    assert inner.buys == []
    why = [json.loads(x)["why"] for x in br.log_path.read_text().splitlines() if '"entry_refused"' in x]
    assert why == ["gap_hold"]
    br.release_lane_lock()


def test_missing_sidecar_starts_seen_skips_executor_gap_hold(tmp_path):
    """Why the sidecar starts_seen copy is required. Host still 24h-blocks; this is the executor belt."""
    clk = Clock(T0)
    dry, _, _ = run_dry_like_host(tmp_path, clk)
    seed_live_cursor_from_dry(Path(dry.cfg.state_dir))
    (Path(dry.cfg.state_dir) / "floor-state-live.json").unlink()
    br, inner = live_bridge(tmp_path, clk)
    assert br.fs["starts_seen"] == 0
    lg = Log(tmp_path)
    lg.start(T0 + 8_000)
    lg.entry(T0 + 8_100, "o-after-restart", pool="P-r")
    clk.t = T0 + 8_150
    feed(br, clk, clk.t)
    assert br.fs["starts_seen"] == 1  # this start looked like the first
    assert [o["order_id"] for o in inner.buys] == ["o-after-restart"]
    br.release_lane_lock()


# -- FakeRpc / throwaway key: replay actually signs; seed does not ----------------------------------


def _live_rpc_port(tmp: Path, clock: PClock):
    from solders.keypair import Keypair

    write_live_ok(tmp)
    cfg, lane, live_ok = X.make_config(lane_raw(tmp))
    cfg = _stop_halt(cfg, tmp)
    rpc = LiveRpc(clock)
    inner = B.make_live_port(cfg, rpc, Keypair(), now_ms=clock)
    port = X.FloorLanePort(inner, cfg)
    port.mint_for_pool = lambda pool, meta: str(meta.get("mint") or MINT)  # type: ignore[method-assign]
    port.universe_problem = lambda pool, mint: None  # type: ignore[method-assign]
    port.mint_screen_problem = lambda mint: None  # type: ignore[method-assign]
    br = X.FloorLaneBridge(cfg, port, lane, now_ms=clock, live_ok_path=live_ok, root_checks=False)
    return br, inner, rpc


def run_dry_one_open_on_probe_mint(tmp: Path, clk: Clock):
    """One dry open whose meta.mint is the FakeRpc fixture (meta.mint must equal mint_for_pool)."""
    br, port = dry_bridge(tmp, clk, balance=600_000_000)
    lg = Log(tmp)
    lg.start(T0 - 1000)
    lg.other(T0, kind="entry", order_id="o-open", pool="P3", meta=floor_meta(mint=MINT), decision_slot=1, stake_sol=0.25, attempt_key=None)
    feed(br, clk)
    assert br.state.orders["o-open"]["live"] is False and br.state.seq == 1
    br._save()
    return br, port, lg


@pytest.mark.skipif(pytest.importorskip("solders", reason="solders required") is None, reason="solders")
def test_fakerpc_naive_empty_live_signs_a_buy_for_the_paper_open(tmp_path, monkeypatch):
    monkeypatch.setattr(pe, "LIVE_DIR", tmp_path / "probe-live-dir")
    clk = Clock(T0)
    run_dry_one_open_on_probe_mint(tmp_path, clk)
    clock = PClock()
    clock.t = T0 + 50
    br, inner, rpc = _live_rpc_port(tmp_path, clock)
    feed(br, clk)
    assert rpc.sent, "naive empty live must hit sendTransaction for a paper oid"
    assert MINT in inner.ex.state.pending
    assert "secret" not in repr(inner.ex).lower()
    br.release_lane_lock()


@pytest.mark.skipif(pytest.importorskip("solders", reason="solders required") is None, reason="solders")
def test_fakerpc_cursor_seed_does_not_sign_historical_then_signs_a_fresh_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(pe, "LIVE_DIR", tmp_path / "probe-live-dir")
    clk = Clock(T0)
    dry, _, _ = run_dry_one_open_on_probe_mint(tmp_path, clk)
    seed_live_cursor_from_dry(Path(dry.cfg.state_dir))
    clock = PClock()
    clock.t = T0 + 50
    br, inner, rpc = _live_rpc_port(tmp_path, clock)
    feed(br, clk)
    assert rpc.sent == []
    assert inner.ex.state.pending == {} and inner.ex.state.open == {}
    lg = Log(tmp_path)
    lg.other(T0 + 9_000, kind="entry", order_id="o-fresh", pool="P-new", meta=floor_meta(mint=MINT), decision_slot=1, stake_sol=0.25, attempt_key=None)
    clk.t = T0 + 9_050
    clock.t = clk.t
    feed(br, clk, clk.t)
    assert rpc.sent, "a post-cutover entry must still be able to send"
    assert MINT in inner.ex.state.pending
    br.release_lane_lock()
