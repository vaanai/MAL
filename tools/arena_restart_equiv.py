#!/usr/bin/env python3
"""arena_restart_equiv: the md5 restart-equivalence harness for tip-source books (F3 (d) of ops/reports/2026-11-02/L4-nd-audit.md; DEC-031 Am.23 item 7).

PAPER only. No key, no wallet, no network, no model call. It replays a persisted hour-file tape (tools/arena_tip_continuity.py's layout) twice per case and compares the books' ledgers:

  reference   one engine, fed the whole tape without a stop (the logged entries are executed at their instants; exits come from the book's exit rule; the sim clock is the tape's).
  restarted   engine #1 is fed up to a KILL cursor and dropped without a flush (what a SIGKILL leaves: the ledger on disk, nothing in memory), then a fresh engine #2 is rebuilt with
              tools.arena_tip_continuity.restore_engine from a (possibly stale) checkpoint, the ledger, the logged entries and the logged sells (+ the sell journal), caught up to a RESTART
              cursor (downtime = the rows between the two) STATE ONLY, and fed the rest of the tape. No entry and NO EXIT is decided in the downtime (QP B1): the reference is run with the same
              blind interval for both, so the comparison is like for like. A sell logged before the kill is used as logged (B2); the replayed state is checked against the checkpoint (B3).

PASS for a case: the ledgers' canonical rows have the same md5, in order and as a set, per bot (the engine's own `start` record and `engine_id`, which differ by design, are not compared and
are counted), no row of the restarted run is an `engine_restart` exclusion, and the pools of the positions open at the restart cursor have the same fingerprint (engine_fingerprint) in both runs.
THE REFERENCE DRIVER (BookDriver + a reference exit rule) IS NOT ANY BOOK'S RUNNER. The proof with HV7's real runner is tools/hv7_restart_equiv.py (QP B4 / P2). The tape must lie inside a pinned
exploration list (B4): `Spec.allow_hours`, `--exploration explore-0814`; anything else is refused.

The report holds NAMES, COUNTS AND MD5s only: no price, mint, pool, signature, amount or exit reason (replay outputs are structural, DEC-030 s2).

Use: a unit test runs it on a synthetic fixture (tools/test_arena_restart_equiv.py); `--selftest` does the same from the command line. The real read is a MiScusi job
(scripts/ops/arena-restart-equiv-job.sh) on EXPLORATION hours converted to the hour-file layout: never on the tip tape, the arena tape, the writer's derived copy or any sealed hour (the
path guard below refuses those names). This file was NOT run on real data when it was written.

  python3 tools/arena_restart_equiv.py --selftest --work-dir /tmp/x/restart-equiv --out /tmp/x/report.json
  python3 tools/arena_restart_equiv.py --tape-dir D --entries-jsonl E --work-dir W --kill-rows 5000,90000 --downtime-rows 0,800 --out report.json
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python3 tools/arena_restart_equiv.py` from the repo root
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools import arena_tip_continuity as C
from tools.arena_paper import MIN_FREE_PCT_FLOOR, TEST_ENV, ArenaEngine, StackConfig, canon, register_bot

SCHEMA = "arena_restart_equiv_v1"
H_MS = 3_600_000
# the closed names of the sealed tapes and their copies (tools/arena_tip_tape_writer.FORBIDDEN_PARTS plus the tip feed's own dirs): this harness reads EXPLORATION hours only
FORBIDDEN_PARTS = ("sealed", "forward", "shadow", "canary", "probe", "walk", "exp011", "exp012", "tip-tape", "meta-capture", "fresh-08", "truth-", "_shakedown", "_smoke", "kalshi", "fwdv1",
                   "hv4", "haiku-v4", "q50", "bots.json", "arena-tip", "arena-tipfeed", "/data/mal/arena", "/var/lib/mal/sealed")
BOT = "restart.t1"
# B4: the pinned exploration lists (hunt23 RULES.md: explore-0814 = /data/mal/clean-view/explore-0814, w1..w7, Aug 14-28). [lo, hi) in UTC hour keys. "Aug 14-28" is read as the 14 days
# 2026-08-14T00 .. 2026-08-28T00 (w1..w7 are seven 2-day windows), the narrower of the two readings. A tape hour outside the list is a refusal.
EXPLORATION = {"explore-0814": ("2026-08-14T00", "2026-08-28T00")}


class Refused(Exception):
    pass


def check_path(path: str | os.PathLike, what: str) -> Path:
    p = str(Path(path).resolve()).lower()
    for tok in FORBIDDEN_PARTS:
        if tok in p:
            raise Refused(f"{what}: the path carries the closed name {tok!r}; this harness reads exploration hours only")
    return Path(path)


# ------------------------------------------------------------------------------------------------------------------------------------ the synthetic fixture
V = 17_584_505_288
SLOT_MS = 400


def synthetic_rows(base_ms: int, n_slots: int = 9000, pools: Sequence[str] = ("PA", "PB", "PC", "PD")) -> list[dict]:
    """PumpSwap prints on four pools for n_slots slots (0.4 s each, so 9000 slots = 1 h), a deterministic wiggle (pool A drifts up so it hits its target, pool B drifts down so it hits its stop,
    C and D range so they hit the time stop), one data_loss gap row in the middle (a trade whose span overlaps it is excluded under the gap rule, not as engine_restart)."""
    rows: list[dict] = []
    i = 0
    drift = {"PA": 1.0006, "PB": 0.9994, "PC": 1.0, "PD": 1.0}
    q = {p: 60e9 for p in pools}
    for s in range(n_slots):
        slot = 1_000 + s
        t = base_ms + s * SLOT_MS
        for k, p in enumerate(pools):
            if (s + k) % 4:
                continue
            i += 1
            wig = 1.0 + (((i * 2654435761) % 1000) - 500) / 400_000.0
            q[p] = q[p] * drift[p] * wig
            rows.append({"type": "trade", "prog": "pumpswap", "venue": "pumpswap", "pool": p, "mint": "M" + p, "side": "buy" if i % 3 else "sell", "sol_lamports": 1_000_000_000,
                         "token_raw": 1e11, "quote_reserve": q[p], "base_reserve": 1e15, "slot": slot, "tx_index": None, "event_index": i, "event_ts": None,
                         "signature": f"sig{p}{i}", "t_recv_ms": t, "virtual_quote_reserves": V})
        if s == n_slots // 2:
            rows.append({"type": "gap", "kind": "tip_gap_reader_problem", "prog": "all", "data_loss": True, "slot": slot, "t_recv_ms": t, "silent_ms": 30_000, "src": "writer"})
    return rows


def write_tape(tape_dir: str | os.PathLike, rows: Sequence[Mapping[str, Any]], compress_closed: bool = False) -> list[str]:
    """Rows into tape-<UTC hour of t_recv_ms>.jsonl in the given order (the arena feed's layout); optionally compact every hour but the last to .zst. Returns the hours."""
    d = Path(tape_dir)
    d.mkdir(parents=True, exist_ok=True)
    by: dict[str, list[bytes]] = {}
    for r in rows:
        by.setdefault(C.hour_key(int(r["t_recv_ms"])), []).append(canon(dict(r)).encode() + b"\n")
    hours = sorted(by)
    for h in hours:
        (d / f"tape-{h}.jsonl").write_bytes(b"".join(by[h]))
    if compress_closed and shutil.which("zstd"):
        from tools.arena_zst import compact_file

        for h in hours[:-1]:
            compact_file(d / f"tape-{h}.jsonl")
    return hours


def synthetic_entries(base_ms: int, n_slots: int = 9000, bot: str = BOT) -> list[C.LoggedEntry]:
    """Logged entry decisions of a model-driven book (the meta carries what a rank row would: a model name, a rank and a card digest, as DATA). Ten entries over four pools, at fixed
    fractions of the tape's span, so that some exits fire before and some after any given kill point."""
    span = n_slots * SLOT_MS
    out = []
    for n, (pool, frac) in enumerate((("PA", 0.03), ("PB", 0.05), ("PC", 0.09), ("PD", 0.11), ("PA", 0.2), ("PB", 0.24), ("PC", 0.36), ("PD", 0.46), ("PA", 0.55), ("PB", 0.68))):
        ms = base_ms + int(span * frac) + n * 1_000
        slot = 1_000 + (ms - base_ms) // SLOT_MS
        out.append(C.LoggedEntry(bot, pool, ms, slot, 0.25, {"model": "stub-haiku", "rank": n % 3, "card_sha256": hashlib.sha256(f"card{n}".encode()).hexdigest()[:16]},
                                 C.position_id(bot, pool, ms, slot)))
    return out


# ------------------------------------------------------------------------------------------------------------------------------------ one run
@dataclass
class Spec:
    tape_dir: Path
    entries: list[C.LoggedEntry]
    work: Path
    rule: C.ExitRule = field(default_factory=lambda: C.StopTargetTimeRule(max_hold_ms=20 * 60_000))
    sps: float | None = 0.4  # constant seconds per slot (the fixtures'); None = the engine's own rolling slot clock
    feed_delay_slots: int = 2
    margin_ms: int = C.REPLAY_MARGIN_MS_DEFAULT
    min_free_pct: float = MIN_FREE_PCT_FLOOR
    allow_hours: tuple[str, str] | None = None  # B4: the pinned exploration list [lo, hi) the tape's hour files must lie in

    @property
    def bots(self) -> list[str]:
        return sorted({e.bot_id for e in self.entries})


def build_engine(spec: Spec, name: str, *, fresh: bool = True) -> ArenaEngine:
    """An engine on <work>/<name>/{ledger,bots.json}. fresh=False reopens the same dirs (what a restart does)."""
    os.environ.setdefault(TEST_ENV, "1")  # throwaway registry lines (fake sha / clock) are a test-mode feature; the scratch dirs are never a production ledger
    root = spec.work / name
    if fresh and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    reg = root / "bots.json"
    for b in spec.bots:
        register_bot(b, "abc1234", 0, reg, deciding_stake=0.25, model_ids=["stub-haiku"], now_ms=0, verify_sha=False)
    eng = ArenaEngine(root / "ledger", reg, StackConfig(feed_delay_slots=spec.feed_delay_slots, strand_rule="total"), sps_fn=(lambda: spec.sps) if spec.sps else None,
                      code_state_fn=lambda: ("abc1234", False), min_free_pct=spec.min_free_pct)
    return eng


def book_rows(eng: ArenaEngine, bot: str) -> list[dict]:
    """The ledger rows of a book without what differs by design between two engine runs: the engine's `start` record and the engine_id field."""
    return [{k: v for k, v in r.items() if k != "engine_id"} for r in eng.ledger.read_bot(bot) if r.get("kind") != "start"]


def digests(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    lines = [canon(r) for r in rows]
    return {"md5_ordered": hashlib.md5("\n".join(lines).encode()).hexdigest(), "md5_set": hashlib.md5("\n".join(sorted(lines)).encode()).hexdigest(), "rows": len(lines)}


def kinds(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[str(r.get("kind", "trade"))] = out.get(str(r.get("kind", "trade")), 0) + 1
    return dict(sorted(out.items()))


def tape_plan(tape_dir: Path) -> list[tuple[str, int]]:
    hours = C.tape_hours(tape_dir)
    return [(h, C.count_lines(hours[h])) for h in sorted(hours)]


def cursor_at(plan: Sequence[tuple[str, int]], index: int) -> C.Cursor:
    """The cursor after the first `index` rows of the tape (1-based count; index 0 is the start of the first hour)."""
    left = index
    for h, n in plan:
        if left <= n:
            return C.Cursor(h, left)
        left -= n
    raise Refused(f"row index {index} is past the end of the tape ({sum(n for _, n in plan)} rows)")


def _after(cur: C.Cursor, c: C.Cursor) -> bool:
    return (c.hour, c.lines) > (cur.hour, cur.lines)


def run_reference(spec: Spec, name: str = "ref", blind: tuple[int, int] | None = None, blind_rows: tuple[C.Cursor, C.Cursor] | None = None, probe: C.Cursor | None = None,
                  fp_since_slot: int | None = None) -> dict[str, Any]:
    """The engine that never stopped. `blind` (ms) removes the entry decisions of a downtime and `blind_rows` its exit decisions: nobody decides while no process runs."""
    eng = build_engine(spec, name)
    drv = C.BookDriver(eng, spec.rule, spec.entries, mode="live", blind=blind, blind_rows=blind_rows)
    first = min(C.tape_hours(spec.tape_dir))
    fp = None
    open_at_probe: list[str] = []
    for c, row in C.iter_tape(spec.tape_dir, first):
        drv.feed(c, row)
        if probe is not None and c == probe:
            open_at_probe = sorted(eng.positions)
            fp = C.engine_fingerprint(eng, None, fp_since_slot)
    eng.flush()
    return {"engine": eng, "driver": drv, "fingerprint_at_probe": fp, "open_at_probe": open_at_probe}


def run_restart(spec: Spec, kill: C.Cursor, restart: C.Cursor, name: str = "rst", ckpt_lag_events: int = 0, journal_mode: str = "journal") -> dict[str, Any]:
    """Phase 1 to the kill cursor (no flush), drop, phase 2: a fresh engine on the same ledger, restore_engine up to the restart cursor (state only, B1), logged sells as logged (B2), the
    state checked against a (possibly stale) checkpoint (B3), then the rest of the tape. journal_mode "lost" gives the restore no sell journal (the kill took it): the logged unpriced sells
    must become `engine_restart`."""
    eng1 = build_engine(spec, name)
    journal = C.SellJournal(spec.work / name / "sell-journal.jsonl")
    C.attach_sell_journal(eng1, journal)
    d1 = C.BookDriver(eng1, spec.rule, spec.entries, mode="live")
    rec1 = C.CheckpointRecorder(eng1, margin_ms=spec.margin_ms, every_rows=10**9, cursor_fn=lambda: d1.cursor)
    first = min(C.tape_hours(spec.tape_dir))
    kill_ms = None
    for c, row in C.iter_tape(spec.tape_dir, first):
        d1.feed(c, row)
        if c == kill:
            kill_ms = d1.sim_now
            break
    if kill_ms is None:
        raise Refused("the kill cursor was not reached")
    if len(rec1.history) <= ckpt_lag_events:
        raise Refused("no checkpoint that old exists")
    ckpt = rec1.history[-1 - ckpt_lag_events]  # the checkpoint a recorder had saved `ckpt_lag_events` events before the kill
    issued = d1.entries[: d1.i]  # the decision log at the kill: every entry the live book decided before it died
    sells = C.sells_from_log(d1.decision_log)
    open_at_kill = sorted(eng1.positions)
    pending_at_kill = sorted(p for ids in eng1.pending.values() for p in ids)
    # the process dies here: eng1, d1 and the recorder are dropped with whatever they held in memory (pending sells included)
    restart_ms = kill_ms
    for c, row in C.iter_tape(spec.tape_dir, restart.hour, restart):
        if c == restart:
            restart_ms = max(kill_ms, int(row.get("t_recv_ms") or kill_ms))
    blind = (kill_ms, restart_ms)
    eng2 = build_engine(spec, name, fresh=False)
    d2 = C.restore_engine(eng2, spec.tape_dir, issued, spec.rule, ckpt, logged_sells=sells, journal=None if journal_mode == "lost" else journal, until=restart, margin_ms=spec.margin_ms,
                          blind=blind)
    restored = dict(d2.counters)
    fp = C.engine_fingerprint(eng2, None, ckpt["fp_from_slot"])
    open_at_restart = sorted(eng2.positions)
    C.attach_sell_journal(eng2, journal)
    d2.entries = sorted((e for e in spec.entries if e.ms > restart_ms), key=lambda e: (e.ms, e.bot_id, e.pool))
    d2.i = 0
    d2.counters = {}
    C.CheckpointRecorder(eng2, margin_ms=spec.margin_ms, every_rows=10**9, cursor_fn=lambda: d2.cursor)
    for c, row in C.iter_tape(spec.tape_dir, restart.hour):
        if _after(restart, c):
            d2.feed(c, row)
    eng2.flush()
    return {"engine": eng2, "driver": d2, "restored": restored, "fingerprint_at_restart": fp, "open_at_restart": open_at_restart, "blind": blind, "kill_ms": kill_ms,
            "restart_ms": restart_ms, "ckpt": ckpt, "open_at_kill": open_at_kill, "pending_at_kill": pending_at_kill, "n_logged_sells": len(sells)}


def run_case(spec: Spec, plan: Sequence[tuple[str, int]], kill_row: int, downtime_rows: int, ckpt_lag_events: int = 0, name: str = "case", journal_mode: str = "journal") -> dict[str, Any]:
    kill, restart = cursor_at(plan, kill_row), cursor_at(plan, kill_row + downtime_rows)
    try:
        rst = run_restart(spec, kill, restart, f"{name}-rst", ckpt_lag_events, journal_mode)
    except C.ContinuityRefused as exc:  # the restore failed closed: in production this is the legacy recover() path (engine_restart), not a pass
        return {"name": name, "kill_row": kill_row, "downtime_rows": downtime_rows, "ckpt_lag_events": ckpt_lag_events, "refused": exc.reason, "equal": False}
    ref = run_reference(spec, f"{name}-ref", blind=rst["blind"], blind_rows=(kill, restart), probe=restart, fp_since_slot=rst["ckpt"]["fp_from_slot"])
    per_bot = {}
    ok = True
    for b in spec.bots:
        a, r = book_rows(ref["engine"], b), book_rows(rst["engine"], b)
        da, dr = digests(a), digests(r)
        restart_excl = sum(1 for x in r if x.get("exclusion_reason") == "engine_restart")
        eq_o, eq_s = da["md5_ordered"] == dr["md5_ordered"], da["md5_set"] == dr["md5_set"]
        per_bot[b] = {"reference": da, "restarted": dr, "equal_ordered": eq_o, "equal_set": eq_s, "engine_restart_rows": restart_excl, "kinds_reference": kinds(a),
                      "kinds_restarted": kinds(r), "start_records_restarted": sum(1 for x in rst["engine"].ledger.read_bot(b) if x.get("kind") == "start")}
        ok = ok and eq_o and eq_s and restart_excl == 0
    fp_eq = ref["fingerprint_at_probe"] == rst["fingerprint_at_restart"] and ref["open_at_probe"] == rst["open_at_restart"]
    exits_blind = ref["driver"].counters.get("exits_blind_rows", 0)
    return {"name": name, "kill_row": kill_row, "downtime_rows": downtime_rows, "ckpt_lag_events": ckpt_lag_events, "kill": kill.as_dict(), "restart": restart.as_dict(),
            "downtime_ms": rst["restart_ms"] - rst["kill_ms"], "open_at_kill": len(rst["open_at_kill"]), "pending_sells_at_kill": len(rst["pending_at_kill"]),
            "open_at_restart": len(rst["open_at_restart"]), "fingerprint_equal": fp_eq, "per_bot": per_bot, "restore_counters": dict(sorted(rst["restored"].items())),
            "blind_rows_without_exit_decision": exits_blind, "equal": bool(ok and fp_eq)}


def check_hours(tape_dir: Path, allow: tuple[str, str] | None) -> list[str]:
    """B4: every hour file of the tape must lie inside the pinned exploration list [lo, hi). No list, or one hour outside it, is a refusal (not just a deny-list of names)."""
    if allow is None:
        raise Refused("no exploration allow-list given: the harness reads pinned exploration hours only")
    lo, hi = allow
    hours = sorted(C.tape_hours(tape_dir))
    if not hours:
        raise Refused("the tape dir holds no hour file")
    bad = [h for h in hours if not (lo <= h < hi)]
    if bad:
        raise Refused(f"{len(bad)} tape hour(s) outside the allow-list [{lo}, {hi}): first {bad[0]}")
    return hours


def parse_allow(arg: str | None, exploration: str | None) -> tuple[str, str] | None:
    """--exploration NAME picks a pinned list; --allow-hours LO:HI (hour keys, HI exclusive) may only NARROW it. Neither: None."""
    if exploration is None:
        if arg:
            raise Refused("--allow-hours needs --exploration (it can only narrow a pinned list)")
        return None
    if exploration not in EXPLORATION:
        raise Refused(f"unknown exploration list {exploration!r}; pinned: {sorted(EXPLORATION)}")
    lo, hi = EXPLORATION[exploration]
    if arg:
        a, _, b = arg.partition(":")
        if not (lo <= a < b <= hi):
            raise Refused(f"--allow-hours {arg} is not inside the pinned list {exploration} [{lo}, {hi})")
        lo, hi = a, b
    return lo, hi


def run_all(spec: Spec, kill_rows: Sequence[int], downtime_rows: Sequence[int], ckpt_lag_events: int = 0) -> dict[str, Any]:
    check_hours(spec.tape_dir, spec.allow_hours)
    plan = tape_plan(spec.tape_dir)
    cases = []
    for k in kill_rows:
        for dt in downtime_rows:
            cases.append(run_case(spec, plan, k, dt, ckpt_lag_events, name=f"k{k}-d{dt}"))
    return {"schema": SCHEMA, "tape_hours": [h for h, _ in plan], "tape_rows": sum(n for _, n in plan), "entries": len(spec.entries), "bots": spec.bots, "rule": spec.rule.name,
            "margin_ms": spec.margin_ms, "allow_hours": list(spec.allow_hours or []), "cases": cases, "all_equal": all(c["equal"] for c in cases)}


def selftest(work: Path, kill_rows: Sequence[int] | None = None, downtime_rows: Sequence[int] = (0, 400), n_slots: int = 9000) -> dict[str, Any]:
    span = n_slots * SLOT_MS
    base = 1_790_316_000_000 - (1_790_316_000_000 % H_MS) + H_MS - int(span * 0.4)  # the tape starts 40% of its span before a UTC hour mark and crosses it
    tape = work / "tape"
    hours = write_tape(tape, synthetic_rows(base, n_slots), compress_closed=True)
    spec = Spec(tape, synthetic_entries(base, n_slots), work / "runs", rule=C.StopTargetTimeRule(max_hold_ms=span // 6), allow_hours=(hours[0], C.hour_key(C.hour_start_ms(hours[-1]) + H_MS)),
                margin_ms=60_000)
    n = sum(c for _, c in tape_plan(tape))
    return run_all(spec, kill_rows or [n // 10, n // 4, n // 2, (3 * n) // 5, (9 * n) // 10], downtime_rows, ckpt_lag_events=0)


def load_entries(path: Path) -> list[C.LoggedEntry]:
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    if rows and rows[0].get("kind") == "entry":
        return C.entries_from_log(rows)
    return [C.LoggedEntry(r["bot_id"], r["pool"], int(r["ms"]), int(r["slot"]), r.get("sol_in"), r.get("meta") or {}, r.get("order_id")) for r in rows]


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--selftest", action="store_true", help="run the synthetic fixture (no real data)")
    ap.add_argument("--tape-dir")
    ap.add_argument("--entries-jsonl", help="decision-log entry rows, or LoggedEntry dicts (bot_id, pool, ms, slot, sol_in, meta, order_id)")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--kill-rows", default="", help="comma list of absolute row indexes (rows consumed before the kill)")
    ap.add_argument("--downtime-rows", default="0", help="comma list: rows between the kill and the restart")
    ap.add_argument("--ckpt-lag-events", type=int, default=0, help="how many checkpoint events old the checkpoint is")
    ap.add_argument("--exploration", help="a pinned exploration list (explore-0814); required with --tape-dir")
    ap.add_argument("--allow-hours", help="LO:HI hour keys (HI exclusive) narrowing the pinned list")
    ap.add_argument("--margin-ms", type=int, default=C.REPLAY_MARGIN_MS_DEFAULT)
    ap.add_argument("--sps", type=float, default=0.4, help="constant seconds per slot; 0 = the engine's own slot clock")
    ap.add_argument("--selftest-slots", type=int, default=9000, help="slots (0.4 s each) of the synthetic tape")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        work = check_path(a.work_dir, "work-dir")
        out = check_path(a.out, "out")
        dts = [int(x) for x in a.downtime_rows.split(",") if x != ""]
        if a.selftest:
            rep = selftest(work, [int(x) for x in a.kill_rows.split(",") if x] or None, dts or (0,), a.selftest_slots)
        else:
            if not a.tape_dir or not a.entries_jsonl or not a.kill_rows:
                raise Refused("--tape-dir, --entries-jsonl and --kill-rows are required (or --selftest)")
            tape = check_path(a.tape_dir, "tape-dir")
            ents = load_entries(check_path(a.entries_jsonl, "entries-jsonl"))
            if not ents:
                raise Refused("no entry decisions in --entries-jsonl")
            spec = Spec(tape, ents, work / "runs", sps=a.sps or None, margin_ms=a.margin_ms, allow_hours=parse_allow(a.allow_hours, a.exploration))
            rep = run_all(spec, [int(x) for x in a.kill_rows.split(",") if x], dts, a.ckpt_lag_events)
    except (Refused, C.ContinuityRefused) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1, sort_keys=True) + "\n")
    print(f"restart-equivalence: {sum(1 for c in rep['cases'] if c['equal'])}/{len(rep['cases'])} cases equal; all_equal={rep['all_equal']}; report {out}")
    return 0 if rep["all_equal"] else 1


if __name__ == "__main__":
    sys.exit(main())
