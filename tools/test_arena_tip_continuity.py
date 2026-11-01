"""Tests for tools/arena_tip_continuity.py (F3 (b), (c) and QP's B1 to B3 on #1183): an engine restarted on the persisted tip tape rebuilds its state by replay (state only), uses a sell logged
before the kill as logged, adopts the open positions (no `engine_restart`), checks the replayed state against the checkpoint, decides nothing in the downtime and never calls a model. SYNTHETIC rows
in tmp dirs only: no real tape, ledger, decision log or network.

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
MARGIN = 60_000  # a replay that starts late in the tape (the second hour file) for the later kills: the margin is short on purpose


@pytest.fixture(autouse=True)
def _plenty_of_disk(monkeypatch):
    """The ledger refuses appends below its free-space floor: no test may depend on how full the host's disk is."""
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    work = tmp_path_factory.mktemp("cont")
    base = 1_790_316_000_000 - (1_790_316_000_000 % E.H_MS) + E.H_MS - int(SPAN * 0.4)
    tape = work / "tape"
    hours = E.write_tape(tape, E.synthetic_rows(base, N_SLOTS))
    allow = (hours[0], C.hour_key(C.hour_start_ms(hours[-1]) + E.H_MS))
    spec = E.Spec(tape, E.synthetic_entries(base, N_SLOTS), work / "runs", rule=C.StopTargetTimeRule(max_hold_ms=SPAN // 6), margin_ms=MARGIN, allow_hours=allow)
    return spec, E.tape_plan(tape), base


def test_the_fixture_crosses_an_hour_boundary_and_has_a_gap_row(world):
    spec, plan, _ = world
    assert len(plan) == 2 and all(n > 100 for _, n in plan)
    assert any(r.get("type") == "gap" for _, r in C.iter_tape(spec.tape_dir, plan[0][0]))


def first_cursor_with(spec, predicate, start=0):
    """The first row index at which `predicate(engine)` holds on an uninterrupted run (to aim a kill at a particular state)."""
    eng = E.build_engine(spec, "probe")
    drv = C.BookDriver(eng, spec.rule, spec.entries)
    n = 0
    for c, row in C.iter_tape(spec.tape_dir, min(C.tape_hours(spec.tape_dir))):
        drv.feed(c, row)
        n += 1
        if n > start and predicate(eng):
            return n
    raise AssertionError("the state never occurred in the fixture")


def test_b_open_positions_are_adopted_and_priced_not_marked_engine_restart(world):
    spec, plan, _ = world
    k = first_cursor_with(spec, lambda e: len(e.positions) >= 2)
    case = E.run_case(spec, plan, k, 0, name="adopt")
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


# ------------------------------------------------------------------------------------------------------------------------------------ B1: no decision in the downtime
def test_b1_no_exit_is_decided_for_any_row_of_the_downtime_or_of_the_replay(world):
    spec, plan, _ = world
    k = first_cursor_with(spec, lambda e: len(e.positions) >= 2)
    seen_blind = 0
    for dt in (60, 150, 300, 500):
        case = E.run_case(spec, plan, k, dt, name=f"dt{dt}")
        assert case["equal"] and case["per_bot"][E.BOT]["engine_restart_rows"] == 0, case
        assert "exits" not in case["restore_counters"]  # the replay (the downtime included) decided nothing
        seen_blind += case["blind_rows_without_exit_decision"]
    assert seen_blind > 0


class FireAtSlot(C.ExitRule):
    """A rule that fires on one print only (a stop crossed for an instant) and never again: its decision is a pure function of the print."""

    name = "fire_at_slot"

    def __init__(self, slot: int) -> None:
        self.slot = slot

    def check(self, pos, hist, t_ms, slot):
        return "spike" if slot == self.slot and pos.pool == "PA" else None


def test_b1_a_stop_crossed_in_the_downtime_and_recovered_since_is_not_sold_at_once(world):
    spec, plan, base = world
    entry = next(e for e in spec.entries if e.pool == "PA")
    # the print the rule fires on is inside the downtime of a restart that happens while the position is open
    fire_slot = entry.slot + 70
    rows = [r for _, r in C.iter_tape(spec.tape_dir, plan[0][0]) if r.get("type") == "trade" and r["pool"] == "PA" and r["slot"] >= fire_slot]
    assert rows and rows[0]["slot"] >= fire_slot
    fire_slot = rows[0]["slot"]
    spec2 = E.Spec(spec.tape_dir, [entry], spec.work, rule=FireAtSlot(fire_slot), margin_ms=MARGIN, allow_hours=spec.allow_hours)
    idx = sum(1 for _, r in C.iter_tape(spec.tape_dir, plan[0][0]) if r.get("slot", 0) < fire_slot - 20)  # kill 20 slots before the spike
    case = E.run_case(spec2, plan, idx, 80, name="spike")  # downtime 80 rows: contains the spike print
    assert case["equal"] and case["blind_rows_without_exit_decision"] > 0
    b = case["per_bot"][E.BOT]
    # the position was NOT sold at the spike (it was never decided): the book holds it to the flush, where it is priced like any unsold position
    live = E.run_reference(spec2, "spike-live")
    exits_live = live["driver"].counters.get("exits", 0)
    assert exits_live == 1 and case["restore_counters"].get("exits", 0) == 0


# ------------------------------------------------------------------------------------------------------------------------------------ B2: logged sells as logged
def test_b2_a_sell_logged_but_not_priced_at_the_kill_is_used_as_logged_from_the_journal(world):
    spec, plan, _ = world
    k = first_cursor_with(spec, lambda e: bool(e.pending))  # a sell decided, the engine waiting for the print that prices it: lives only in memory
    case = E.run_case(spec, plan, k, 0, name="pending")
    assert case["pending_sells_at_kill"] >= 1 and case["equal"], case
    assert case["restore_counters"].get("sell_reapplied", 0) >= 1 and "exits" not in case["restore_counters"]
    assert case["per_bot"][E.BOT]["engine_restart_rows"] == 0


def test_b2_without_a_matching_journal_row_the_logged_sell_is_engine_restart_never_re_derived(world):
    spec, plan, _ = world
    k = first_cursor_with(spec, lambda e: bool(e.pending))
    case = E.run_case(spec, plan, k, 0, name="nojournal", journal_mode="lost")
    b = case["per_bot"][E.BOT]
    assert case["restore_counters"].get("sell_unmatched", 0) >= 1 and "exits" not in case["restore_counters"]
    assert b["engine_restart_rows"] >= 3 and b["engine_restart_rows"] % 3 == 0  # whole positions, one row per stake
    assert not case["equal"]  # fail closed is a different book from the uninterrupted one, by design


def test_b2_sells_from_log_drops_a_refused_sell_and_reads_both_row_shapes():
    rows = [{"seq": 0, "arm": "a", "kind": "sell", "pid": "p1", "ms": 10, "decision_slot": 7, "reason": "stop"},
            {"seq": 1, "arm": "a", "kind": "order_refused", "side": "sell", "order_id": "p1", "ms": 10},
            {"seq": 2, "arm": "a", "kind": "sell", "pid": "p1", "ms": 11, "decision_slot": 8, "reason": "stop"},
            {"seq": 3, "arm": "b", "kind": "exit", "order_id": "p2", "ms": 12, "decision_slot": 9, "reason": "time"},
            {"seq": 4, "arm": "b", "kind": "exit", "order_id": "p3", "ms": 13, "decision_slot": 9, "reason": "time"},
            {"seq": 5, "arm": "b", "kind": "order_refused", "side": "sell", "order_id": "p3", "ms": 13}]
    got = C.sells_from_log(rows)
    assert [(s.bot_id, s.pid, s.ms, s.slot, s.reason) for s in got] == [("a", "p1", 11, 8, "stop"), ("b", "p2", 12, 9, "time")]


def test_b2_the_journal_ignores_a_torn_last_line_and_keeps_the_last_row_per_position(tmp_path):
    j = C.SellJournal(tmp_path / "j.jsonl")
    (tmp_path / "j.jsonl").write_bytes(b'{"position_id":"p1","reason":"a"}\n{"position_id":"p1","reason":"b"}\n{"position_id":"p2","rea')
    assert j.rows() == {"p1": {"position_id": "p1", "reason": "b"}}


# ------------------------------------------------------------------------------------------------------------------------------------ B3: the state check
def test_b3_a_stale_checkpoint_is_refused_when_a_position_was_priced_after_it_and_fine_when_not(world):
    spec, plan, _ = world
    k = first_cursor_with(spec, lambda e: len(e.positions) >= 2)
    fresh = E.run_case(spec, plan, k + 50, 20, ckpt_lag_events=0, name="fresh")
    assert fresh["equal"]
    seen = set()
    for lag in (1, 2, 3, 5):
        c = E.run_case(spec, plan, k + 400, 20, ckpt_lag_events=lag, name=f"lag{lag}")
        seen.add(c.get("refused") or "ok")
        assert c.get("refused") in (None, "priced_after_checkpoint") and (c.get("refused") or c["equal"]), c
    assert "priced_after_checkpoint" in seen


def test_b3_a_tampered_or_mismatching_checkpoint_fails_closed(world):
    spec, plan, _ = world
    first = min(C.tape_hours(spec.tape_dir))
    k = first_cursor_with(spec, lambda e: bool(e.priced) and len(e.positions) >= 1)  # something is already priced, and a position is open
    eng = E.build_engine(spec, "mm")
    drv = C.BookDriver(eng, spec.rule, spec.entries)
    rec = C.CheckpointRecorder(eng, margin_ms=MARGIN, cursor_fn=lambda: drv.cursor)
    n = 0
    for c, row in C.iter_tape(spec.tape_dir, first):
        drv.feed(c, row)
        n += 1
        if n == k + 5:
            break
    ck = rec.history[-1]
    assert ck["fp"] and ck["since_slot"] is not None and ck["fp_from_slot"] >= ck["since_slot"] and ck["priced"] is not None

    def restore(c, **kw):
        return C.restore_engine(E.build_engine(spec, "mm", fresh=False), spec.tape_dir, drv.entries[: drv.i], spec.rule, c, **kw)

    for bad, reason in ((dict(ck, fp="0" * 32), "state_mismatch"), (dict(ck, last_row_id=[1, "nope", 0, 1]), "checkpoint_row_not_replayed"), (dict(ck, fp=None), "checkpoint_without_state_fingerprint"),
                        (dict(ck, since_slot=None), "checkpoint_without_state_fingerprint"), (dict(ck, now_ms=ck["now_ms"] - 10**9), "priced_after_checkpoint")):
        with pytest.raises(C.ContinuityRefused) as ei:
            restore(bad)
        assert ei.value.reason == reason, (reason, ei.value)
    d = restore(ck)  # the untampered checkpoint restores
    assert d.counters.get("adopted", 0) >= 1


def test_b3_a_short_replay_start_that_loses_the_v_carry_or_history_is_caught(world):
    """The margin is a floor, not a proof: shrink it until the replay starts after prints the open positions need, and the fingerprint (or the position's pricing inputs) differs."""
    spec, plan, _ = world
    first = min(C.tape_hours(spec.tape_dir))
    eng = E.build_engine(spec, "lm")
    drv = C.BookDriver(eng, spec.rule, spec.entries)
    rec = C.CheckpointRecorder(eng, margin_ms=MARGIN, cursor_fn=lambda: drv.cursor)
    k = first_cursor_with(spec, lambda e: len(e.positions) >= 2, start=450)
    n = 0
    for c, row in C.iter_tape(spec.tape_dir, first):
        drv.feed(c, row)
        n += 1
        if n == k:
            break
    ck = dict(rec.history[-1], warm_from_ms=10**15)  # no warm-up claim: only the oldest open position minus a negative margin
    restored = C.restore_engine(E.build_engine(spec, "lm", fresh=False), spec.tape_dir, drv.entries[: drv.i], spec.rule, ck, margin_ms=MARGIN)
    assert restored.counters["adopted"] >= 2  # a margin that covers the open positions replays fine
    late = dict(ck, warm_from_ms=10**15)
    with pytest.raises(C.ContinuityRefused) as ei:
        C.restore_engine(E.build_engine(spec, "lm", fresh=False), spec.tape_dir, drv.entries[: drv.i], spec.rule, late, margin_ms=-SPAN)  # replay starts after the entries: nothing to compare
    assert ei.value.reason in ("state_mismatch", "checkpoint_row_not_replayed", "replay_start_missing", "hour_missing", "cursor_hour_missing")


def test_b3_the_fingerprint_sees_a_different_history_and_the_sps(world):
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
    a2 = E.build_engine(E.Spec(spec.tape_dir, spec.entries, spec.work, sps=0.41), "fpc")
    assert C.engine_fingerprint(a2, pools) != C.engine_fingerprint(E.build_engine(spec, "fpd"), pools)


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
    spec2 = E.Spec(spec.tape_dir, C.entries_from_log(logged), spec.work, rule=spec.rule, margin_ms=MARGIN, allow_hours=spec.allow_hours)
    k = first_cursor_with(spec2, lambda e: len(e.positions) >= 2)
    before = model.calls
    case = E.run_case(spec2, plan, k, 100, name="nomodel")
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
    first = min(C.tape_hours(spec.tape_dir))
    eng = E.build_engine(spec, "ghost")
    drv = C.BookDriver(eng, spec.rule, spec.entries[:2])
    rec = C.CheckpointRecorder(eng, margin_ms=MARGIN, cursor_fn=lambda: drv.cursor)
    n = 0
    for c, row in C.iter_tape(spec.tape_dir, first):
        drv.feed(c, row)
        n += 1
        if n == 300:
            break
    eng2 = E.build_engine(spec, "ghost", fresh=False)
    d2 = C.restore_engine(eng2, spec.tape_dir, [ghost] + spec.entries[:2], spec.rule, rec.history[-1], until=C.Cursor(first, 300), margin_ms=MARGIN)
    assert d2.counters.get("entry_without_open_row") == 1 and ghost.order_id not in {r.get("position_id") for r in eng2.ledger.read_bot(E.BOT)}


# ------------------------------------------------------------------------------------------------------------------------------------ fail closed
def test_the_restore_fails_closed_with_a_reason_the_caller_can_fall_back_on(world, tmp_path):
    spec, plan, base = world
    first = plan[0][0]
    good = {"schema": C.SCHEMA_CKPT, "last_row_id": [1, "s", 0, base], "cursor": {"hour": first, "lines": 100}, "warm_from_ms": base, "now_ms": base, "since_slot": 1, "fp_from_slot": 1,
            "fp": "0" * 32, "pools": [], "priced": {}}

    def refused(reason, ck, tape=None, **kw):
        with pytest.raises(C.ContinuityRefused) as ei:
            C.restore_engine(E.build_engine(spec, "fc"), tape or spec.tape_dir, spec.entries, spec.rule, ck, **kw)
        assert ei.value.reason == reason, ei.value

    refused("no_checkpoint", tmp_path / "missing.json")
    (tmp_path / "bad.json").write_text("{nope")
    refused("no_checkpoint", tmp_path / "bad.json")
    refused("checkpoint_malformed", {"schema": "other", "last_row_id": [1]})
    refused("cursor_hour_missing", dict(good, cursor={"hour": "2020-01-01T00", "lines": 5}))
    refused("tape_shorter_than_checkpoint", dict(good, cursor={"hour": first, "lines": plan[0][1] + 5}))
    refused("restore_end_before_checkpoint", dict(good, cursor={"hour": plan[1][0], "lines": 10}), until=C.Cursor(first, 50))
    gap_tape = tmp_path / "gap"  # a missing hour in the middle of the replay span: hours A and A+2 only
    h0 = C.hour_start_ms(first)
    rows = [{"type": "trade", "t_recv_ms": h0 + 1000, "slot": 1, "pool": "PA"}, {"type": "trade", "t_recv_ms": h0 + 2 * E.H_MS + 1000, "slot": 2, "pool": "PA"}]
    E.write_tape(gap_tape, rows)
    refused("hour_missing", dict(good, cursor={"hour": C.hour_key(h0 + 2 * E.H_MS), "lines": 1}, warm_from_ms=h0), tape=gap_tape)
    refused("replay_start_missing", dict(good, warm_from_ms=h0 - 5 * E.H_MS))


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
    rec = C.CheckpointRecorder(eng, margin_ms=MARGIN, cursor_fn=lambda: drv.cursor, on_save=lambda ck: C.save_checkpoint(tmp_path / C.CKPT_NAME, ck))
    k = first_cursor_with(spec, lambda e: len(e.positions) >= 1)
    for i, (c, row) in enumerate(C.iter_tape(spec.tape_dir, min(C.tape_hours(spec.tape_dir)))):
        drv.feed(c, row)
        if i + 1 == k + 5:
            break
    assert not (tmp_path / (C.CKPT_NAME + ".tmp")).exists()
    assert C.load_checkpoint(tmp_path / C.CKPT_NAME) == json.loads(json.dumps(rec.history[-1]))
    assert rec.history[-1]["cursor"]["hour"] == plan[0][0] and rec.history[-1]["n_open"] >= 1


def test_a_compacted_closed_hour_replays_like_the_open_one(tmp_path):
    import shutil

    if shutil.which("zstd") is None:
        pytest.skip("the zstd CLI is required")
    base = 1_790_316_000_000 - (1_790_316_000_000 % E.H_MS) + E.H_MS - 200_000
    rows = E.synthetic_rows(base, 1000)
    a, b = tmp_path / "open", tmp_path / "zst"
    E.write_tape(a, rows)
    E.write_tape(b, rows, compress_closed=True)
    assert any(p.name.endswith(".zst") for p in b.iterdir())
    first = min(C.tape_hours(a))
    assert [(c, r["t_recv_ms"]) for c, r in C.iter_tape(a, first)] == [(c, r["t_recv_ms"]) for c, r in C.iter_tape(b, first)]
