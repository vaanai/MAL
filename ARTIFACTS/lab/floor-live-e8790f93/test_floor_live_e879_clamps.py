"""Adversarial review of FLOOR live patch e8790f93 (probe-port clamp lift) vs live 3710e1af.

Synthetic only: throwaway Keypair, probe FakeRpc, no network, no host, no wallet file.
Does not edit production executors. Run against the e879 tree:

  PYTHONPATH=/tmp/mal-e879 python3 -m pytest ARTIFACTS/lab/floor-live-e8790f93/test_floor_live_e879_clamps.py -q
"""

from __future__ import annotations

import collections
import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

from tools import arena_live_bridge as B
from tools import floor_arena_executor as F
from tools import floor_lane_executor as X
from tools import h5_lane_executor as H
from tools import probe_executor as pe
from tools import probe_live as pl
from tools.test_floor_arena_executor import Clock, Log
from tools.test_floor_lane_executor import FakeInner, lane_raw, write_live_ok
from tools.test_probe_executor import MINT
from tools.test_probe_executor import Clock as PClock
from tools.test_probe_live import LiveRpc

HERE = Path(__file__).resolve().parent
REPO_HINT = HERE.parents[2]
SHA_E879 = "e8790f9342df6c59a849f2ed0897b59ad5441015"
SHA_3710 = "3710e1af8cf434faca570046de896a7702464709"
BLOB_FLOOR = "6f353716e29f72c8f92699310d44f21ce23dd0dd"
FIRST_SEND_MS = 1_794_148_248_897  # 2026-11-08 06:30:48.897 PST; never reset
END_MS = 1_796_688_000_000  # 2026-12-07 16:00 PST / 2026-12-08 00:00Z
T0 = FIRST_SEND_MS
STAKE = 30_000_000
BUDGET = 300_000_000
RESERVE = 3_860_000
FAIL_ERR = {"InstructionError": [3, {"Custom": 6004}]}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    monkeypatch.setattr(
        "tools.arena_paper.shutil.disk_usage",
        lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500),
    )


def _sha1_blob(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def test_imported_floor_lane_executor_is_the_e879_blob():
    data = Path(X.__file__).read_bytes()
    assert _sha1_blob(data) == BLOB_FLOOR
    assert X.LANE_PROBE_MAX_ATTEMPTS == 100_000
    assert X.LANE_PROBE_MAX_DAYS == 60.0
    assert X.LANE_MAX_OPEN == 8


def test_patch_is_only_three_setattrs_plus_constants():
    """Production delta 3710 → e879 on this file: constants + FloorLanePort setattr. Nothing else."""
    import subprocess

    diff = subprocess.check_output(["git", "diff", f"{SHA_3710}", f"{SHA_E879}", "--", "tools/floor_lane_executor.py"], cwd=REPO_HINT).decode()
    assert "object.__setattr__(ex.limits, \"max_open\", int(cfg.max_open))" in diff
    assert "object.__setattr__(ex.limits, \"max_attempts\", LANE_PROBE_MAX_ATTEMPTS)" in diff
    assert "object.__setattr__(ex.limits, \"max_days\", LANE_PROBE_MAX_DAYS)" in diff
    plus = "\n".join(l for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
    assert "capped_pnl" not in plus
    assert "first_send_ms" not in plus
    assert "CANARY" not in plus
    assert "abandoned" not in plus
    assert "_start_sell" not in plus
    assert "SELL_RESERVE" not in plus
    assert "evaluate" not in plus


def test_unchanged_guardrail_files_match_3710():
    import subprocess

    for rel in (
        "tools/floor_arena_executor.py",
        "tools/probe_live.py",
        "tools/arena_live_bridge.py",
        "tools/h5_lane_executor.py",
        "scripts/mal-fast/floor-lane-live.json.example",
    ):
        a = subprocess.check_output(["git", "show", f"{SHA_3710}:{rel}"], cwd=REPO_HINT)
        b = subprocess.check_output(["git", "show", f"{SHA_E879}:{rel}"], cwd=REPO_HINT)
        assert a == b, rel


def test_3710_floor_lane_port_does_not_lift_probe_clamps():
    import subprocess

    src = subprocess.check_output(["git", "show", f"{SHA_3710}:tools/floor_lane_executor.py"], cwd=REPO_HINT).decode()
    assert "LANE_PROBE_MAX_ATTEMPTS" not in src
    assert 'object.__setattr__(ex.limits, "max_open"' not in src
    assert 'object.__setattr__(ex.limits, "max_attempts"' not in src
    assert 'object.__setattr__(ex.limits, "max_days"' not in src


# -- true clamps -----------------------------------------------------------------------------------


def test_dec019_construction_clamps_max_open_8_to_3_and_keeps_60_and_7():
    lim = pe.Limits.from_config({"size_lamports": STAKE, "max_open": 8, "max_attempts": 60, "loss_cap_lamports": BUDGET, "priority_lamports": 500_000})
    object.__setattr__(lim, "end_ms", END_MS)  # dec019 construction pins end_ms to 2026-10-12; the live port replaces it with cfg.end_ms
    assert lim.max_open == 3
    assert lim.max_attempts == 60
    assert lim.max_days == 7
    st = pe.State(attempts=0, first_attempt_ms=T0)
    st.open = {f"M{i}": {} for i in range(3)}
    assert pe.check_buy(lim, st, T0, False, "live") == "max_open"
    st.open = {f"M{i}": {} for i in range(2)}
    assert pe.check_buy(lim, st, T0, False, "live") is None
    st2 = pe.State(attempts=60, first_attempt_ms=T0)
    assert pe.check_buy(lim, st2, T0, False, "live") == "max_attempts"
    st3 = pe.State(attempts=1, first_attempt_ms=T0)
    assert pe.check_buy(lim, st3, T0 + 7 * 86_400_000, False, "live") == "max_days"
    assert pe.check_buy(lim, st3, T0 + 7 * 86_400_000 - 1, False, "live") is None


def test_canary_and_end_ms_constants_unchanged():
    assert F.CANARY_MAX_MS == 14 * 86_400_000
    assert FIRST_SEND_MS + F.CANARY_MAX_MS == 1_795_357_848_897  # 2026-11-22 14:30:48.897Z, before end_ms
    assert FIRST_SEND_MS + 7 * 86_400_000 == 1_794_753_048_897  # 2026-11-15 14:30:48.897Z = the 7-day probe cliff
    assert END_MS == 1_796_688_000_000
    assert FIRST_SEND_MS + F.CANARY_MAX_MS < END_MS < FIRST_SEND_MS + 60 * 86_400_000


def _stop_halt(cfg, tmp):
    return dataclasses.replace(cfg, stop_file=str(tmp / "nstop"), halt_file=str(tmp / "nhalt"), end_ms=END_MS)


def live_bridge(tmp, clk, inner=None):
    write_live_ok(tmp)
    raw = lane_raw(tmp)
    raw["end_ms"] = END_MS
    cfg, lane, live_ok = X.make_config(raw)
    cfg = _stop_halt(cfg, tmp)
    inner = inner or FakeInner()
    port = X.FloorLanePort(inner, cfg)
    br = X.FloorLaneBridge(cfg, port, lane, now_ms=clk, live_ok_path=live_ok, root_checks=False)
    return br, inner


def feed(br, clk, ms=None):
    rows = br.read_new_rows()
    for r in rows:
        clk.t = r["ms"] + 50 if ms is None else ms
        br.handle_row(r)
        br._poll_ports()
    br._save()
    return rows


def test_first_send_ms_is_write_once_and_survives_restart(tmp_path):
    clk = Clock(T0)
    br, _ = live_bridge(tmp_path, clk)
    br.fs["first_send_ms"] = FIRST_SEND_MS
    br._save()
    assert br.fs["first_send_ms"] == FIRST_SEND_MS
    br.release_lane_lock()
    clk2 = Clock(T0 + 60_000)
    br2, inner = live_bridge(tmp_path, clk2)
    assert br2.fs["first_send_ms"] == FIRST_SEND_MS
    lg = Log(tmp_path)
    lg.start(T0 - 1000)
    lg.entry(T0 + 60_000, "o1", pool="P")
    feed(br2, clk2)
    assert br2.fs["first_send_ms"] == FIRST_SEND_MS  # handle_entry must not extend or reset
    assert inner.buys  # still inside the 14-day window
    br2.release_lane_lock()


def test_canary_expired_still_binds_at_first_send_plus_14d(tmp_path):
    clk = Clock(FIRST_SEND_MS + F.CANARY_MAX_MS + 1)
    br, inner = live_bridge(tmp_path, clk)
    br.fs["first_send_ms"] = FIRST_SEND_MS
    br._save()
    lg = Log(tmp_path)
    lg.start(T0 - 1000)
    lg.entry(int(clk.t) - 50, "o-late", pool="P")
    feed(br, clk, int(clk.t))
    why = [json.loads(x)["why"] for x in br.log_path.read_text().splitlines() if '"entry_refused"' in x]
    assert "canary_expired" in why
    assert inner.buys == []
    br.release_lane_lock()


def test_entry_one_ms_before_canary_end_is_not_expired(tmp_path):
    now = FIRST_SEND_MS + F.CANARY_MAX_MS
    clk = Clock(now)
    br, inner = live_bridge(tmp_path, clk)
    br.fs["first_send_ms"] = FIRST_SEND_MS
    br._save()
    lg = Log(tmp_path)
    lg.start(T0 - 1000)
    lg.entry(now - 50, "o-last", pool="P")
    feed(br, clk, now)
    why = [json.loads(x).get("why") for x in br.log_path.read_text().splitlines() if '"entry_refused"' in x]
    assert "canary_expired" not in why
    assert [o["order_id"] for o in inner.buys] == ["o-last"]
    br.release_lane_lock()


def test_end_ms_still_refuses_after_dec7(tmp_path):
    clk = Clock(END_MS)
    br, inner = live_bridge(tmp_path, clk)
    br.fs["first_send_ms"] = FIRST_SEND_MS
    lg = Log(tmp_path)
    lg.start(T0 - 1000)
    lg.entry(END_MS - 50, "o-end", pool="P")
    feed(br, clk, END_MS)
    why = [json.loads(x)["why"] for x in br.log_path.read_text().splitlines() if '"entry_refused"' in x]
    assert "end_ms" in why
    assert inner.buys == []
    br.release_lane_lock()


def test_cursor_survives_executor_restart(tmp_path):
    clk = Clock(T0)
    br, inner = live_bridge(tmp_path, clk)
    br.fs["first_send_ms"] = FIRST_SEND_MS
    br.state.start_seen = True
    br.state.seq = 3
    br.state.prev = "abc"
    br.state.cursor = {"2026-11-08.jsonl": 1234}
    br._save()
    seq, prev, cursor, first = br.state.seq, br.state.prev, dict(br.state.cursor), br.fs["first_send_ms"]
    br.release_lane_lock()
    br2, _ = live_bridge(tmp_path, clk)
    assert (br2.state.seq, br2.state.prev, br2.state.cursor, br2.fs["first_send_ms"]) == (seq, prev, cursor, first)
    br2.release_lane_lock()
    assert inner.buys == []


def test_budget_daily_reserve_constants_untouched():
    assert X.LANE_BUDGET_MAX_LAMPORTS == BUDGET
    assert X.LANE_DAILY_MAX_LAMPORTS == 100_000_000
    assert X.LANE_MIN_FEE_RESERVE == RESERVE == 2_100_000 + 160_000 + 1_600_000
    assert F.PRIORITY_LAMPORTS == 155_000
    assert F.SELL_RESERVE_SENDS == pl.SELL_MAX_ATTEMPTS == 10
    assert F.CANARY_MAX_MS == 14 * 86_400_000


def test_whole_checkout_does_not_import_forge_or_lane_daily_report():
    """e879 also touches forge host/events and lane_daily_report. Those are not on the FLOOR live import path.
    Retargeting the whole checkout must not pull them into the executor process."""
    import sys

    assert not any(n == "tools.lane_daily_report" or n.startswith("tools.forge") for n in sys.modules)
    import tools.floor_lane_executor as _x  # noqa: F401

    assert "tools.lane_daily_report" not in sys.modules
    assert "tools.forge.dsl_lane_host" not in sys.modules
    assert "tools.forge.events" not in sys.modules
    assert "tools.forge" not in sys.modules


def test_floor_live_import_closure_only_floor_lane_executor_differs_from_3710():
    import ast
    import subprocess

    repo = Path(X.__file__).resolve().parents[1]

    def children(mod: str):
        p = repo / (mod.replace(".", "/") + ".py")
        if not p.exists():
            return []
        tree = ast.parse(p.read_text())
        out = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                out.extend(a.name for a in n.names if a.name.startswith("tools"))
            elif isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("tools"):
                out.append(n.module)
                for a in n.names:
                    if a.name == "*":
                        continue
                    cand = n.module + "." + a.name
                    if (repo / cand.replace(".", "/")).with_suffix(".py").exists():
                        out.append(cand)
        return out

    seen, q, files = set(), ["tools.floor_lane_executor"], []
    while q:
        name = q.pop()
        if name in seen:
            continue
        seen.add(name)
        rel = name.replace(".", "/") + ".py"
        if (repo / rel).exists():
            files.append(rel)
        q.extend(children(name))
    changed = []
    for rel in files:
        a = subprocess.check_output(["git", "show", f"{SHA_3710}:{rel}"], cwd=REPO_HINT)
        b = (repo / rel).read_bytes()
        if a != b:
            changed.append(rel)
    assert changed == ["tools/floor_lane_executor.py"], changed
    assert "tools/forge/dsl_lane_host.py" not in files
    assert "tools/forge/events.py" not in files
    assert "tools/lane_daily_report.py" not in files


def _lifted_limits():
    lim = pe.Limits.from_config(
        {"size_lamports": STAKE, "max_open": 8, "max_attempts": 60, "loss_cap_lamports": BUDGET, "priority_lamports": 500_000}
    )
    object.__setattr__(lim, "max_open", 8)
    object.__setattr__(lim, "max_attempts", 100_000)
    object.__setattr__(lim, "max_days", 60.0)
    object.__setattr__(lim, "end_ms", END_MS)
    return lim


def test_inner_60d_and_100k_would_allow_day20_but_outer_14d_canary_refuses(tmp_path):
    """Most important: first_send_ms 1794148248897 + 14d = Nov 22 14:30:48.897Z must not extend to the inner 60-day lift.
    Day 20 is past the canary, still inside inner max_days=60 and before end_ms Dec 7 16:00 PST."""
    day20 = FIRST_SEND_MS + 20 * 86_400_000
    assert FIRST_SEND_MS + F.CANARY_MAX_MS < day20 < END_MS
    assert day20 - FIRST_SEND_MS < 60 * 86_400_000
    lim = _lifted_limits()
    st = pe.State(attempts=61, first_attempt_ms=FIRST_SEND_MS)
    assert pe.check_buy(lim, st, day20, False, "live") is None  # inner 60d / 100k would SEND
    clk = Clock(day20)
    br, inner = live_bridge(tmp_path, clk)
    br.fs["first_send_ms"] = FIRST_SEND_MS
    br._save()
    lg = Log(tmp_path)
    lg.start(T0 - 1000)
    lg.entry(day20 - 50, "o-day20", pool="P")
    feed(br, clk, day20)
    why = [json.loads(x)["why"] for x in br.log_path.read_text().splitlines() if '"entry_refused"' in x]
    assert why == ["canary_expired"], why
    assert inner.buys == []
    assert br.fs["first_send_ms"] == FIRST_SEND_MS  # cutoff origin not extended
    br.release_lane_lock()


def test_inner_100k_does_not_bypass_030_lifetime_presend(tmp_path):
    """Most important: 100_000 inner attempts do not loosen the cost-capped -0.30 SOL pre-send."""
    lim = _lifted_limits()
    st = pe.State(attempts=61, first_attempt_ms=FIRST_SEND_MS)
    assert pe.check_buy(lim, st, T0, False, "live") is None  # inner would SEND the 61st attempt
    lc = H.LaneConfig(tmp_path / "l.jsonl", tmp_path / "STOP", X.LANE_BOT_ID, STAKE, 8, BUDGET, 100_000_000, RESERVE, 60.0)
    led = X.FloorLaneLedger(lc)
    now = T0
    cash = -7 * (STAKE + 3_000_000) - 60_000_000
    op = {f"{lc.bot_id}:m{i}": {"stake": STAKE, "filled": True, "ts_ms": now, "signature": f"s{i}"} for i in range(7)}
    marks = {f"m{i}": (2 * STAKE, now) for i in range(7)}
    led._marks = marks
    view = H.LaneView(cash=cash, pnl=cash + 14 * STAKE, cost=cash + 7 * STAKE, open=op, snaps={})
    assert led.evaluate(view, now, STAKE) == "lane_budget_presend"
    # 8 full wipes at max stake + reserve still fit the 0.30 bound; the pre-send is what refuses the 8th at +100% marks
    assert 8 * (STAKE + RESERVE) == 270_880_000 < BUDGET


def test_cost_capped_presend_still_binds_at_e879(tmp_path):
    lc = H.LaneConfig(tmp_path / "l.jsonl", tmp_path / "STOP", X.LANE_BOT_ID, STAKE, 8, BUDGET, 100_000_000, RESERVE, 60.0)
    now = T0
    led = X.FloorLaneLedger(lc)
    cash = -7 * (STAKE + 3_000_000) - 60_000_000
    marks = {f"m{i}": (2 * STAKE, now) for i in range(7)}
    led._marks = marks
    op = {f"{lc.bot_id}:m{i}": {"stake": STAKE, "filled": True, "ts_ms": now, "signature": f"s{i}"} for i in range(7)}
    view = H.LaneView(cash=cash, pnl=cash + 14 * STAKE, cost=cash + 7 * STAKE, open=op, snaps={})
    assert led.evaluate(view, now, STAKE) == "lane_budget_presend"


# -- FakeRpc / throwaway key: wrapper vs unwrapped probe -------------------------------------------


@pytest.fixture
def hermetic_live_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pe, "LIVE_DIR", tmp_path / "probe-live-dir")
    return tmp_path


def _cfg(tmp: Path):
    write_live_ok(tmp)
    raw = {
        "mode": "live",
        "bot_id": "l1-floor-v2",
        "frozen_sha": F.FROZEN_SHA,
        "root": str(tmp / "rules"),
        "state_dir": str(tmp / "state"),
        "end_ms": END_MS,
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
    cfg, lane, live_ok = X.make_config(raw)
    cfg = dataclasses.replace(cfg, stop_file=str(tmp / "STOP"), halt_file=str(tmp / "HALT"), end_ms=END_MS)
    return cfg, lane, live_ok


def _make_ports(tmp: Path):
    from solders.keypair import Keypair

    clock = PClock(T0)
    rpc = LiveRpc(clock)
    cfg, lane, _ = _cfg(tmp)
    inner = B.make_live_port(cfg, rpc, Keypair(), now_ms=clock)
    wrapped = X.FloorLanePort(inner, cfg)
    wrapped.pre_buy = lambda _o: None
    return wrapped, inner, rpc, clock, cfg


@pytest.mark.skipif(pytest.importorskip("solders", reason="solders required") is None, reason="solders")
def test_unwrapped_probe_still_clamps_open_to_3(hermetic_live_dir):
    """3710 production path: FloorLanePort did not lift max_open, so BridgeExecutor stayed at 3."""
    wrapped, inner, rpc, clock, cfg = _make_ports(hermetic_live_dir)
    raw = inner.ex.limits
    # the WRAPPER lifted; the same Limits object is mutated in place — capture via a fresh unwrapped executor
    from solders.keypair import Keypair

    clock2 = PClock(T0)
    rpc2 = LiveRpc(clock2)
    inner2 = B.make_live_port(cfg, rpc2, Keypair(), now_ms=clock2)
    assert inner2.ex.limits.max_open == 3
    assert inner2.ex.limits.max_attempts == 60
    assert inner2.ex.limits.max_days == 7
    inner2.ex.state.open = {f"dummy{i}": {"kind": "open"} for i in range(3)}
    inner2.ex.handle_signal({"mint": MINT, "decision_t_ms": clock2(), "score": None, "seen_ms": clock2()})
    assert inner2.ex.last_skip == "limit:max_open"
    assert rpc2.sent == []
    assert raw.max_open == 8  # wrapped port did lift THIS instance


@pytest.mark.skipif(pytest.importorskip("solders", reason="solders required") is None, reason="solders")
def test_wrapped_port_allows_4th_through_8th_and_refuses_9th(hermetic_live_dir):
    wrapped, inner, rpc, clock, cfg = _make_ports(hermetic_live_dir)
    ex = inner.ex
    assert ex.limits.max_open == 8
    assert ex.limits.max_attempts == 100_000
    assert ex.limits.max_days == 60.0
    ex.state.open = {f"dummy{i}": {"kind": "open"} for i in range(3)}
    ok, why = wrapped.buy({"order_id": "o4", "mint": MINT, "decision_ms": clock(), "stake_lamports": cfg.stake_lamports})
    assert ok, why
    assert rpc.sent, "4th buy must send after the lift"
    ex.state.pending.clear()
    rpc.sent.clear()
    ex.state.bought = []
    ex.state.open = {f"dummy{i}": {"kind": "open"} for i in range(8)}
    ok, why = wrapped.buy({"order_id": "o9", "mint": MINT, "decision_ms": clock(), "stake_lamports": cfg.stake_lamports})
    assert not ok
    assert why == "limit:max_open" or (hasattr(ex, "last_skip") and ex.last_skip == "limit:max_open")
    assert rpc.sent == []


@pytest.mark.skipif(pytest.importorskip("solders", reason="solders required") is None, reason="solders")
def test_wrapped_port_allows_attempt_61_and_day_8(hermetic_live_dir):
    wrapped, inner, rpc, clock, cfg = _make_ports(hermetic_live_dir)
    ex = inner.ex
    ex.state.attempts = 60
    ex.state.first_attempt_ms = T0
    ok, why = wrapped.buy({"order_id": "o61", "mint": MINT, "decision_ms": clock(), "stake_lamports": cfg.stake_lamports})
    assert ok, why
    assert rpc.sent
    rpc.sent.clear()
    ex.state.pending.clear()
    ex.state.bought = []
    clock.t = T0 + 8 * 86_400_000
    ok, why = wrapped.buy({"order_id": "o-d8", "mint": MINT, "decision_ms": clock(), "stake_lamports": cfg.stake_lamports})
    assert ok, why
    assert rpc.sent


@pytest.mark.skipif(pytest.importorskip("solders", reason="solders required") is None, reason="solders")
def test_unwrapped_probe_still_halts_at_60_and_7d(hermetic_live_dir):
    from solders.keypair import Keypair

    cfg, _, _ = _cfg(hermetic_live_dir)
    clock = PClock(T0)
    inner = B.make_live_port(cfg, LiveRpc(clock), Keypair(), now_ms=clock)
    inner.ex.state.attempts = 60
    inner.ex.state.first_attempt_ms = T0
    inner.ex.handle_signal({"mint": MINT, "decision_t_ms": clock(), "score": None, "seen_ms": clock()})
    assert inner.ex.last_skip == "limit:max_attempts"
    clock.t = T0 + 7 * 86_400_000
    inner.ex.state.attempts = 1
    inner.ex.last_skip = None
    inner.ex.handle_signal({"mint": MINT, "decision_t_ms": clock(), "score": None, "seen_ms": clock()})
    assert inner.ex.last_skip == "limit:max_days"


@pytest.mark.skipif(pytest.importorskip("solders", reason="solders required") is None, reason="solders")
def test_probe_open_and_first_attempt_survive_same_config_restart(hermetic_live_dir):
    wrapped, inner, rpc, clock, cfg = _make_ports(hermetic_live_dir)
    ok, why = wrapped.buy({"order_id": "o1", "mint": MINT, "decision_ms": clock(), "stake_lamports": cfg.stake_lamports})
    assert ok, why
    from tools.test_probe_live import land_buy

    land_buy(inner.ex, rpc)
    wrapped.poll()
    assert MINT in inner.ex.state.open
    first = inner.ex.state.first_attempt_ms
    attempts = inner.ex.state.attempts
    assert first == T0 and attempts >= 1
    state_path = inner.ex.state_path
    saved = json.loads(Path(state_path).read_text())
    assert saved["first_attempt_ms"] == first
    assert MINT in saved["open"]

    from solders.keypair import Keypair

    clock2 = PClock(T0 + 5_000)
    rpc2 = LiveRpc(clock2)
    inner2 = B.make_live_port(cfg, rpc2, Keypair(), now_ms=clock2)
    wrapped2 = X.FloorLanePort(inner2, cfg)
    wrapped2.pre_buy = lambda _o: None
    assert MINT in inner2.ex.state.open
    assert inner2.ex.state.first_attempt_ms == first
    assert inner2.ex.state.attempts == attempts
    assert inner2.ex.limits.max_open == 8  # lift re-applied on restart; state not reset
    assert inner2.ex.limits.max_attempts == 100_000
    assert inner2.ex.limits.max_days == 60.0


@pytest.mark.skipif(pytest.importorskip("solders", reason="solders required") is None, reason="solders")
def test_ten_failed_sells_still_abandon_at_p155(hermetic_live_dir):
    wrapped, inner, rpc, clock, cfg = _make_ports(hermetic_live_dir)
    ok, why = wrapped.buy({"order_id": "o1", "mint": MINT, "decision_ms": clock(), "stake_lamports": cfg.stake_lamports})
    assert ok, why
    from tools.test_probe_live import land_buy, land_sell
    from solders.transaction import VersionedTransaction
    import base64

    land_buy(inner.ex, rpc)
    wrapped.poll()
    ok, why = wrapped.sell({"mint": MINT}, "time_24h")
    assert ok, why
    fee = F.SEND_FEE_LAMPORTS
    for _ in range(99):
        if MINT in inner.ex.state.pending:
            land_sell(inner.ex, rpc, proceeds=0, err=FAIL_ERR, fee=fee)
        clock.t += 11 * 60_000
        wrapped.poll()
    sigs = []
    for _t, b64 in rpc.sent:
        s = str(VersionedTransaction.from_bytes(base64.b64decode(b64)).signatures[0])
        if s not in sigs:
            sigs.append(s)
    assert len(sigs) - 1 == 10
    assert inner.ex.state.open[MINT]["abandoned"] is True
    for _t, b64 in rpc.sent[1:]:
        assert B.signed_priority_lamports(b64) == F.PRIORITY_LAMPORTS
