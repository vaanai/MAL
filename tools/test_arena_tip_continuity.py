"""Tests for tools/arena_tip_continuity.py (F3 (b) and (c)): an engine restarted on the persisted tip tape rebuilds its state by replay, prices the open positions' rule-based exits from the
tape (no `engine_restart`), and never calls a model. SYNTHETIC rows in tmp dirs only: no real tape, ledger, decision log or network.

  /data/mal/venv/bin/python -m pytest tools/test_arena_tip_continuity.py -q
"""
from __future__ import annotations

import collections
import inspect
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("MAL_ARENA_TEST", "1")

from tools import arena_restart_equiv as E  # noqa: E402
from tools import arena_tip_continuity as C  # noqa: E402

N_SLOTS = 1500
SPAN = N_SLOTS * E.SLOT_MS


@pytest.fixture(autouse=True)
def _plenty_of_disk(monkeypatch):
    """The ledger refuses appends below its free-space floor: no test may depend on how full the host's disk is."""
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    work = tmp_path_factory.mktemp("cont")
    base = 1_790_316_000_000 - (1_790_316_000_000 % E.H_MS) + E.H_MS - int(SPAN * 0.4)
    tape = work / "tape"
    E.write_tape(tape, E.synthetic_rows(base, N_SLOTS))
    spec = E.Spec(tape, E.synthetic_entries(base, N_SLOTS), work / "runs", rule=C.StopTargetTimeRule(max_hold_ms=SPAN // 6))
    return spec, E.tape_plan(tape), base


def test_the_fixture_crosses_an_hour_boundary_and_has_a_gap_row(world):
    spec, plan, _ = world
    assert len(plan) == 2 and all(n > 100 for _, n in plan)
    assert any(r.get("type") == "gap" for _, r in C.iter_tape(spec.tape_dir, plan[0][0]))


def first_cursor_with(spec, predicate):
    """The first row index at which `predicate(engine)` holds on an uninterrupted run (to aim a kill at a particular state)."""
    eng = E.build_engine(spec, "probe")
    drv = C.BookDriver(eng, spec.rule, spec.entries)
    n = 0
    for c, row in C.iter_tape(spec.tape_dir, min(C.tape_hours(spec.tape_dir))):
        drv.feed(c, row)
        n += 1
        if predicate(eng):
            return n
    raise AssertionError("the state never occurred in the fixture")


def test_b_open_positions_are_adopted_and_priced_not_marked_engine_restart(world):
    spec, plan, _ = world
    k = first_cursor_with(spec, lambda e: len(e.positions) >= 2)
    case = E.run_case(spec, plan, k, 0, ckpt_lag_rows=10, name="adopt")
    b = case["per_bot"][E.BOT]
    assert case["open_at_restart"] >= 2 and case["restore_counters"]["adopted"] >= 2
    assert b["engine_restart_rows"] == 0 and case["equal"], case
    assert b["kinds_restarted"].get("trade", 0) > 0 and b["kinds_restarted"] == b["kinds_reference"]


def test_b_the_legacy_recover_marks_the_same_positions_engine_restart(world):
    """The contrast: ArenaEngine.recover() builds restarted=True positions and their sell is an `engine_restart` exclusion (Am.23 item 7). The continuity path is what removes that."""
    spec, plan, _ = world
    k = first_cursor_with(spec, lambda e: len(e.positions) >= 2)
    eng1 = E.build_engine(spec, "legacy")
    drv = C.BookDriver(eng1, spec.rule, spec.entries)
    n = 0
    for c, row in C.iter_tape(spec.tape_dir, min(C.tape_hours(spec.tape_dir))):
        drv.feed(c, row)
        n += 1
        if n == k:
            break
    held = sorted(eng1.positions)
    eng2 = E.build_engine(spec, "legacy", fresh=False)
    assert eng2.recover() == len(held) and all(p.restarted for p in eng2.positions.values())
    for _c, row in C.iter_tape(spec.tape_dir, min(C.tape_hours(spec.tape_dir))):
        eng2.on_row(row)
    for pid in held:
        eng2.sell(E.BOT, pid, 1.0, eng2.hw_ms, eng2.hw_slot, "x")
    assert sum(1 for r in eng2.ledger.read_bot(E.BOT) if r.get("exclusion_reason") == "engine_restart") == 3 * len(held)


def test_b_an_exit_decided_in_the_downtime_is_priced_from_the_tape(world):
    spec, plan, _ = world
    k = first_cursor_with(spec, lambda e: len(e.positions) >= 2)
    found = None
    for dt in (60, 150, 300, 500):  # a downtime long enough to contain an exit of a position open at the kill
        case = E.run_case(spec, plan, k, dt, name=f"dt{dt}")
        assert case["equal"] and case["per_bot"][E.BOT]["engine_restart_rows"] == 0, case
        if case["restore_counters"].get("exits", 0) > 0:
            found = case
    assert found is not None, "no downtime in the grid contained an exit"


def test_b_an_exit_decided_but_not_yet_priced_at_the_kill_is_rederived_not_remembered(world):
    spec, plan, _ = world
    k = first_cursor_with(spec, lambda e: bool(e.pending))  # a sell decided, the engine waiting for the print that prices it: lives only in memory
    case = E.run_case(spec, plan, k, 0, name="pending")
    assert case["equal"] and case["restore_counters"].get("exits", 0) >= 1 and case["per_bot"][E.BOT]["engine_restart_rows"] == 0, case


def test_b_a_stale_checkpoint_is_enough_because_the_replay_starts_at_the_oldest_open_position(world):
    spec, plan, _ = world
    k = first_cursor_with(spec, lambda e: len(e.positions) >= 2)
    for lag in (0, 200, 700):
        case = E.run_case(spec, plan, k + 50, 20, ckpt_lag_rows=lag, name=f"lag{lag}")
        assert case["equal"], (lag, case)


def test_b_the_fingerprint_sees_a_different_history(world):
    spec, plan, _ = world
    a = E.run_reference(spec, "fpa")["engine"]
    b = E.build_engine(spec, "fpb")
    drv = C.BookDriver(b, spec.rule, spec.entries)
    for i, (c, row) in enumerate(C.iter_tape(spec.tape_dir, min(C.tape_hours(spec.tape_dir)))):
        if i != 400:  # one print missing
            drv.feed(c, row)
    pools = ["PA", "PB", "PC", "PD"]
    assert C.engine_fingerprint(a, pools) != C.engine_fingerprint(b, pools)
    assert C.engine_fingerprint(a, pools) == C.engine_fingerprint(a, pools)


# ------------------------------------------------------------------------------------------------------------------------------------ (c) no model call
class CountingModel:
    """A stand-in for the book's Haiku rank call: counts every use."""

    def __init__(self) -> None:
        self.calls = 0

    def rank(self, card: str) -> int:
        self.calls += 1
        return int(card[-1]) % 3


def test_c_a_restart_never_calls_the_model_and_the_decisions_are_the_logged_ones(world):
    spec, plan, base = world
    model = CountingModel()
    logged = []  # the live phase: the model decides, the decision goes in the log (decision-log rows, as tools/arena_declog writes them)
    for n, e in enumerate(spec.entries):
        rank = model.rank(f"card{n}")
        logged.append({"seq": n, "arm": e.bot_id, "ms": e.ms, "kind": "entry", "order_id": e.order_id, "pool": e.pool, "decision_slot": e.slot, "stake_sol": e.sol_in,
                       "meta": dict(e.meta, rank=rank)})
    assert model.calls == len(spec.entries)
    spec2 = E.Spec(spec.tape_dir, C.entries_from_log(logged), spec.work, rule=spec.rule)
    k = first_cursor_with(spec2, lambda e: len(e.positions) >= 2)
    before = model.calls
    case = E.run_case(spec2, plan, k, 100, ckpt_lag_rows=5, name="nomodel")
    assert case["equal"] and model.calls == before  # the restore path was never handed the model
    # structure: the restore API has no model / caller parameter, and the driver's `model` is a tripwire
    assert not {"model", "caller", "ask", "llm"} & set(inspect.signature(C.restore_engine).parameters)
    drv = C.BookDriver(E.build_engine(spec2, "trip"), spec.rule, spec2.entries)
    with pytest.raises(C.ModelCalledOnReplay):
        drv.model("rank me")
    with pytest.raises(C.ModelCalledOnReplay):
        drv.model.rank
    # the meta the model produced is read back from the log, byte for byte
    assert [json.dumps(e.meta, sort_keys=True) for e in spec2.entries] == [json.dumps(r["meta"], sort_keys=True) for r in logged]


def test_c_entries_from_log_reads_entry_rows_only_in_ms_order(world):
    rows = [{"seq": 2, "arm": "b", "ms": 20, "kind": "entry", "pool": "P", "decision_slot": 5, "size": 0.25, "meta": {"x": 1}, "order_id": "o2"},
            {"seq": 1, "arm": "a", "ms": 10, "kind": "entry", "pool": "Q", "decision_slot": 4, "stake_sol": 0.5, "meta": {}, "order_id": "o1"},
            {"seq": 3, "arm": "a", "ms": 11, "kind": "exit", "pool": "Q"}, {"seq": 4, "arm": "a", "ms": 12, "kind": "order_refused", "order_id": "o1"}]
    got = C.entries_from_log(rows)
    assert [(e.bot_id, e.ms, e.sol_in, e.order_id) for e in got] == [("a", 10, 0.5, "o1"), ("b", 20, 0.25, "o2")]
    assert C.entries_from_log(rows, bot_of=lambda r: "z")[0].bot_id == "z"


def test_c_an_entry_the_log_shows_without_an_open_row_is_counted_and_never_sent(world):
    spec, plan, base = world
    ghost = C.LoggedEntry(E.BOT, "PA", base + 10_000, 1_025, 0.25, {"ghost": True}, C.position_id(E.BOT, "PA", base + 10_000, 1_025))
    eng = E.build_engine(spec, "ghost")
    drv = C.BookDriver(eng, spec.rule, spec.entries[:2])
    n = 0
    for c, row in C.iter_tape(spec.tape_dir, min(C.tape_hours(spec.tape_dir))):
        drv.feed(c, row)
        n += 1
        if n == 300:
            break
    ck = drv.checkpoint()
    eng2 = E.build_engine(spec, "ghost", fresh=False)
    d2 = C.restore_engine(eng2, spec.tape_dir, [ghost] + spec.entries[:2], spec.rule, ck, until=C.Cursor(min(C.tape_hours(spec.tape_dir)), 300))
    assert d2.counters.get("entry_without_open_row") == 1 and ghost.order_id not in {r.get("position_id") for r in eng2.ledger.read_bot(E.BOT)}


# ------------------------------------------------------------------------------------------------------------------------------------ fail closed
def test_the_restore_fails_closed_with_a_reason_the_caller_can_fall_back_on(world, tmp_path):
    spec, plan, base = world
    first = plan[0][0]
    good = {"schema": C.SCHEMA_CKPT, "cursor": {"hour": first, "lines": 100}, "warm_from_ms": base, "sim_now": base}
    eng = lambda n: E.build_engine(spec, n)  # noqa: E731

    def refused(reason, ck, tape=None, **kw):
        with pytest.raises(C.ContinuityRefused) as ei:
            C.restore_engine(eng("fc"), tape or spec.tape_dir, spec.entries, spec.rule, ck, **kw)
        assert ei.value.reason == reason, ei.value
        return ei.value

    refused("no_checkpoint", tmp_path / "missing.json")
    (tmp_path / "bad.json").write_text("{nope")
    refused("no_checkpoint", tmp_path / "bad.json")
    refused("checkpoint_malformed", {"schema": "other", "cursor": {"hour": first, "lines": 1}})
    refused("cursor_hour_missing", dict(good, cursor={"hour": "2020-01-01T00", "lines": 5}))
    refused("tape_shorter_than_checkpoint", dict(good, cursor={"hour": first, "lines": plan[0][1] + 5}))
    refused("restore_end_before_checkpoint", dict(good, cursor={"hour": plan[1][0], "lines": 10}), until=C.Cursor(first, 50))
    # a missing hour in the middle of the replay span: write a tape with hours A and A+2 only
    gap_tape = tmp_path / "gap"
    h0 = C.hour_start_ms(first)
    rows = [{"type": "trade", "t_recv_ms": h0 + 1000, "slot": 1, "pool": "PA"}, {"type": "trade", "t_recv_ms": h0 + 2 * E.H_MS + 1000, "slot": 2, "pool": "PA"}]
    E.write_tape(gap_tape, rows)
    refused("hour_missing", dict(good, cursor={"hour": C.hour_key(h0 + 2 * E.H_MS), "lines": 1}, warm_from_ms=h0), tape=gap_tape)
    # a replay start older than the oldest file
    refused("replay_start_missing", dict(good, warm_from_ms=h0 - 5 * E.H_MS))
    # the driver itself never swallows a malformed open row
    assert C.ContinuityRefused("x").reason == "x"


def test_a_torn_last_line_of_an_open_hour_is_not_a_row(tmp_path):
    p = tmp_path / "tape-2026-11-01T06.jsonl"
    p.write_bytes(b'{"type":"gap","t_recv_ms":1}\n{"type":"trade","t_recv_ms":2}\n{"type":"tra')
    assert C.count_lines(p) == 2
    assert [c.lines for c, _ in C.iter_tape(tmp_path, "2026-11-01T06")] == [1, 2]
    with pytest.raises(C.ContinuityRefused) as ei:
        list(C.iter_tape(tmp_path, "2026-11-01T06", C.Cursor("2026-11-01T06", 3)))
    assert ei.value.reason == "tape_shorter_than_cursor"


def test_the_checkpoint_round_trips_atomically(world, tmp_path):
    spec, plan, _ = world
    eng = E.build_engine(spec, "ck")
    drv = C.BookDriver(eng, spec.rule, spec.entries)
    for i, (c, row) in enumerate(C.iter_tape(spec.tape_dir, min(C.tape_hours(spec.tape_dir)))):
        drv.feed(c, row)
        if i == 120:
            break
    ck = drv.checkpoint()
    C.save_checkpoint(tmp_path / C.CKPT_NAME, ck)
    assert not (tmp_path / (C.CKPT_NAME + ".tmp")).exists() and C.load_checkpoint(tmp_path / C.CKPT_NAME) == json.loads(json.dumps(ck))
    assert ck["cursor"] == {"hour": plan[0][0], "lines": 121} and ck["warm_from_ms"] <= ck["sim_now"] - C.REPLAY_MARGIN_MS_DEFAULT + 1


def test_a_compacted_closed_hour_replays_like_the_open_one(tmp_path):
    import shutil

    if shutil.which("zstd") is None:
        pytest.skip("the zstd CLI is required")
    base = 1_790_316_000_000 - (1_790_316_000_000 % E.H_MS) + E.H_MS - 200_000
    rows = E.synthetic_rows(base, 1000)
    a, b = tmp_path / "open", tmp_path / "zst"
    E.write_tape(a, rows)
    E.write_tape(b, rows, compress_closed=True)
    assert sorted(p.suffix for p in b.iterdir()) == [".jsonl", ".zst"] or any(p.name.endswith(".zst") for p in b.iterdir())
    first = min(C.tape_hours(a))
    assert [(c, r["t_recv_ms"]) for c, r in C.iter_tape(a, first)] == [(c, r["t_recv_ms"]) for c, r in C.iter_tape(b, first)]
