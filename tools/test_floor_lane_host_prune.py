"""FLOOR lane host: dropping per-pool state of pools FLOOR can never buy (QP-RULING-1125-floor-host-memory Q3). SYNTHETIC tape only; no real tape, ledger, sidecar or key is opened.

Decision equivalence: the host with the prune (head) against (a) the same host with the prune switched off and (b) the host file of main at BASE_SHA, byte for byte over every decision-log
file and the lane checkpoint, on a tape with a synthetic burst of never-buyable pools interleaved with buyable ones: entries, take-profit and time exits, gap refusals, a restart with
resume_reopen / host_down refusals, held positions and engine pending sells. Memory: the state and the engine stay bounded under the burst. Nothing here says the rule makes money."""

from __future__ import annotations

import collections
import hashlib
import importlib.util
import subprocess
import tempfile
from pathlib import Path

import pytest

from tools import arena_rule_l1 as L
from tools import arena_rules as R_
from tools import floor_lane_host as H
from tools.arena_declog import DecisionLog
from tools.arena_paper import ArenaEngine
from tools.arena_rules import register_rules
from tools.arena_rules_testkit import FakeEngine, write_tape
from tools.test_arena_rule_l1 import H0, HOUR, SOLX, _first_print_rules, pool_rows, row
from tools.test_floor_lane_host import BINDING, SHA40, Clocked, _live_phase, gap_row
import tools.test_arena_rule_l1 as T

REPO = Path(__file__).resolve().parent.parent
BASE_SHA = "191c7b94"  # main before this change (the host file at this commit has no prune)
BURST_START, BURST_END = H0 + 29 * HOUR, H0 + 32 * HOUR
BURST_POOLS = 5400  # one new pool a second for 90 minutes: ~a quarter of the real burst's hourly rate, so the bounds below are not generous
BAD_V = 2 * SOLX  # first print outside [17.5, 17.7] SOL: the table never admits the pool


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))
    monkeypatch.setattr(T, "SHA", SHA40)


def burst_rows(n=BURST_POOLS, t0=BURST_START + 5 * 60_000, span=90 * 60_000):
    """n never-buyable pools: first print with V out of band (final: the table decides once), then a trap print 40 s later that WOULD trigger the rule if the pool were in the
    universe (a drained-pool sell with V in band and a high age proxy); every 7th pool prints once more 20 min later (a revived key)."""
    rows = []
    for i in range(n):
        t = t0 + i * span // n
        p = f"N{i}"
        rows.append(row(t, p, "buy", v=BAD_V))
        rows.append(row(t + 40_000, p, "sell", q=600_000_000, sol=200_000_000))
        if i % 7 == 0:
            rows.append(row(t + 20 * 60_000, p, "buy", v=BAD_V, n=1))
    return rows


def tape_rows():
    """Three buyable pools A (take-profit), B and C (time exits) as test_floor_arena_executor, two more buyable pools D, E, the burst interleaved at their trigger hours, and a gap
    right after D's trigger so E's trigger is gap-refused."""
    rows = []
    for p, dt in (("A", 0), ("B", 600_000), ("C", 1_200_000), ("D", 2_400_000), ("E", 3_000_000)):
        pr = [r for r in pool_rows(p=p) if r["pool"] == p]
        for r in pr:
            r["t_recv_ms"] += dt
            r["signature"] += "x"
        rows += pr
    rows.append(row(H0 + 1000, "X"))
    t = max(r["t_recv_ms"] for r in rows if r["pool"] == "A")
    rows.append(row(t + 10_000, "A", "sell", q=400_000_000, sol=1_000_000))  # the landing print of A's buy
    rows.append(row(t + 60_000, "A", "buy", q=60 * SOLX, sol=50_000_000))  # spot far above 1.5 x mark -> take profit
    d_trigger = H0 + 30 * HOUR + 3_300_000  # D's drained-pool sell (55 min after 30 h, E follows 10 min later)
    rows.append(gap_row(d_trigger + 120_000, d_trigger + 150_000))  # after A-D entered, before E's trigger: E is refused for the gap
    rows += burst_rows()
    return sorted(rows, key=lambda r: (r["t_recv_ms"], str(r.get("signature"))))


def load_base():
    """The host file of main at BASE_SHA as a module of package `tools` (skip when the commit is not in this clone)."""
    r = subprocess.run(["git", "-C", str(REPO), "show", f"{BASE_SHA}:tools/floor_lane_host.py"], capture_output=True)
    if r.returncode != 0:
        pytest.skip(f"{BASE_SHA} not in this clone")
    d = Path(tempfile.mkdtemp(prefix="floor-host-base-"))
    p = d / "floor_lane_host_base.py"
    p.write_bytes(r.stdout)
    spec = importlib.util.spec_from_file_location("tools.floor_lane_host_base", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def build(mod, tmp, rows, *, live, real_engine=True, engine=None):
    tape = tmp / "tape"
    if not tape.exists():
        write_tape(tape, rows)
    reg = tmp / "bots.json"
    if not reg.exists():
        register_rules(_first_print_rules(), str(reg), SHA40, H0, now_ms=H0 - 1, verify_sha=False)
    eng = engine or (ArenaEngine(str(tmp / "ledger"), str(reg), cfg=R_.stack_config(7)) if real_engine else FakeEngine(tmp / "rules"))
    host = mod.make_lane_host(root=str(tmp / "rules"), ledger_root=str(tmp / "ledger"), registry=str(reg), tape_dir=str(tape), clock_start_ms=H0, live=live, binding=BINDING, start_ms=0,
                              table_mode="first_print", engine=eng)
    if real_engine and engine is None:  # the engine's call clock is the host's own (the default is the wall clock: a run would not be repeatable)
        eng.now_fn = (lambda: host.now_fn()) if live else (lambda: host.sim_now)
    return host


def fingerprint(tmp):
    """md5 of every decision-log file byte for byte (all kinds, chain included) and of the lane checkpoint; plus the rows for the non-vacuity checks."""
    h = hashlib.md5()
    files = sorted((tmp / "rules" / "decisions").rglob("*.jsonl"))
    for f in files:
        h.update(f.name.encode())
        h.update(f.read_bytes())
    h.update((tmp / "rules" / "lane" / "checkpoint.json").read_bytes())
    return h.hexdigest(), DecisionLog(str(tmp / "rules")).rows(L.BOT_ID)


def run_sim(mod, tmp, rows, hold_until=None):
    host = build(mod, tmp, rows, live=False)
    host.resume()
    host.run(H0, hold_until or H0 + 58 * HOUR)
    return host


def run_restart(mod, tmp, rows):
    """A live host crashes at 31 h 10 min (A, B, C, D hold open positions, E was gap-refused) and restarts at 33 h on the same root, ledger, table checkpoint and engine."""
    a = build(mod, tmp, rows, live=True)
    Clocked(a)
    a.resume()
    _live_phase(a, H0, H0 + 31 * HOUR + 600_000)
    b = build(mod, tmp, rows, live=True)  # a new process: a NEW engine (same ledger), as in production
    Clocked(b)
    b.lane_start(H0 + 33 * HOUR)
    b.resume()
    _live_phase(b, H0 + 33 * HOUR, H0 + 45 * HOUR)
    return a, b


def no_prune(monkeypatch):
    monkeypatch.setattr(H.LaneHost, "_maybe_prune", lambda self, t: None)


def kinds_of(rows):
    return collections.Counter(r["kind"] for r in rows)


# ------------------------------------------------------------------------------------------------ decision equivalence
def test_sim_replay_md5_equal_with_burst_head_vs_prune_off_vs_main(tmp_path, monkeypatch):
    rows = tape_rows()
    base_mod = load_base()
    base_host = run_sim(base_mod, tmp_path / "main", rows)
    fp_main, log_main = fingerprint(tmp_path / "main")
    with monkeypatch.context() as mp:
        no_prune(mp)
        off_host = run_sim(H, tmp_path / "off", rows)
    fp_off, _ = fingerprint(tmp_path / "off")
    head_host = run_sim(H, tmp_path / "head", rows)
    fp_head, log_head = fingerprint(tmp_path / "head")
    assert fp_head == fp_off == fp_main
    assert log_head == log_main
    # the precondition of the engine's own trim (arena_paper TRIM_KEEP_SLOTS note): no print was dropped below a floor, so no late duplicate could tell a dropped pool from a kept one
    tc = base_host.status()["trim_counters"]
    assert tc["rows_below_trim_floor"] == 0 and tc["refused_history_trimmed"] == 0 and tc["hist_trimmed_prints"] > 0, tc
    k = kinds_of(log_head)  # non-vacuous: entries, both exit reasons, gap refusals, and the engine priced and refused what it was handed
    assert k["entry"] == 4 and k[H.KIND_GAP_REFUSED] >= 1 and k["start"] == 1 and k["exit"] == 4, k
    assert sorted({r["reason"] for r in log_head if r["kind"] == "exit"}) == sorted([L.REASON_TP, L.REASON_TIME])
    assert [r["reason"] for r in log_head if r["kind"] == H.KIND_GAP_REFUSED], "refusals carry their reasons"
    # the prune did real work on this tape, the others did none
    assert head_host.prune_counters["errors"] == 0
    assert head_host.prune_counters["state_pools"] > BURST_POOLS // 2 and head_host.prune_counters["engine_pools"] > BURST_POOLS // 2, head_host.prune_counters
    assert off_host.prune_counters["passes"] == 0 and not hasattr(base_host, "prune_counters")
    # the trap prints and the revived keys changed nothing: no entry on any burst pool
    assert not [r for r in log_head if r.get("pool", "").startswith("N")]


def test_restart_replay_md5_equal_with_burst_held_positions_and_resume_rows(tmp_path, monkeypatch):
    rows = tape_rows()
    base_mod = load_base()
    run_restart(base_mod, tmp_path / "main", rows)
    fp_main, log_main = fingerprint(tmp_path / "main")
    with monkeypatch.context() as mp:
        no_prune(mp)
        run_restart(H, tmp_path / "off", rows)
    fp_off, _ = fingerprint(tmp_path / "off")
    _, hb = run_restart(H, tmp_path / "head", rows)
    fp_head, log_head = fingerprint(tmp_path / "head")
    assert fp_head == fp_off == fp_main and log_head == log_main
    k = kinds_of(log_head)
    # held positions at the crash resume as resume_reopen; the downtime refuses entries; refusals and exits of every kind present
    assert k["entry"] >= 4 and k["resume_reopen"] >= 1 and k["start"] == 2, k
    assert k[H.KIND_GAP_REFUSED] >= 1 and hb.prune_counters["errors"] == 0 and hb.prune_counters["state_pools"] > 0, (k, hb.prune_counters)


# ------------------------------------------------------------------------------------------------ memory
def test_state_and_engine_stay_bounded_under_the_burst(tmp_path, monkeypatch):
    rows = tape_rows()
    peaks: dict[str, list] = {}

    def sampler(label, prune):
        orig = H.LaneHost.prune_nonbuyable

        def f(self):
            peaks.setdefault(label, []).append((len(self.state._pools), len(self.engine.pools)))
            return orig(self) if prune else (0, 0)
        return f

    with monkeypatch.context() as mp:
        mp.setattr(H.LaneHost, "prune_nonbuyable", sampler("off", False))
        run_sim(H, tmp_path / "off", rows, hold_until=H0 + 36 * HOUR)
    with monkeypatch.context() as mp:
        mp.setattr(H.LaneHost, "prune_nonbuyable", sampler("on", True))
        host = run_sim(H, tmp_path / "on", rows, hold_until=H0 + 36 * HOUR)
    off_state, off_eng = max(p[0] for p in peaks["off"]), max(p[1] for p in peaks["off"])
    on_state, on_eng = max(p[0] for p in peaks["on"]), max(p[1] for p in peaks["on"])
    assert off_state > BURST_POOLS and off_eng > BURST_POOLS, (off_state, off_eng)  # without it every pool of the burst is still held
    rate = BURST_POOLS / (90 * 60.0)  # new pools a second
    state_bound = rate * (H.PRUNE_STATE_QUIET_MS + 2 * H.PRUNE_EVERY_MS) / 1000 * 1.5 + 200  # quiet window + one pass interval, revived keys and the buyable pools on top
    from tools import arena_paper as AP
    eng_bound = rate * (AP.TRIM_KEEP_SLOTS * 0.4 + 2 * H.PRUNE_EVERY_MS / 1000) * 1.5 + 200  # slots are 400 ms on this tape
    assert on_state <= state_bound and on_eng <= eng_bound, (on_state, on_eng, state_bound, eng_bound)
    assert on_state < off_state // 3 and on_eng < off_eng // 2
    # after the burst the maps are small (the engine's pools stay until quiet for TRIM_KEEP_SLOTS of the feed's own slots, and this tape's feed stops at the burst's end), and the
    # buyable pools survived
    assert len(host.state._pools) < 300 and len(host.engine.pools) <= eng_bound
    for p in ("A", "B", "C", "D", "E"):
        assert p in host.table.uni and p in host.state._pools


# ------------------------------------------------------------------------------------------------ the predicate
def test_never_buyable_is_the_tables_own_decision_and_spares_pending_and_held(tmp_path):
    host = build(H, tmp_path, [row(H0 + 1000, "X")], live=False, real_engine=False)
    tab = host.table
    tab.seen.update({"S": 1, "U": 1, "P": 1, "K": 1})
    tab.uni["U"] = 1  # admitted
    tab.pending["Mx"] = [("P", 1)]  # first print judged but a create / complete row may still admit it
    held = {"K"}
    ok = host._never_buyable(held)
    assert ok("S") and not ok("U") and not ok("P") and not ok("K")
    assert not ok("never_seen")  # the table has not judged it: not provably final
