#!/usr/bin/env python3
"""hv7_restart_equiv: restart continuity wired around HV7's REAL runner (tools/hv7_runner.py, not edited) and the restart-equivalence comparison on it (QP B4 / proof P2 of the verdict on #1183).

PAPER only. No key, no wallet, no network; the model is whatever the factory gives (a StubModel in the tests, the decision log or the replay cache for a real-hours proof: never an API call).

`Hv7ContinuityRunner` is HV7's runner with the F3 restart wired in from the outside:
  * resume() replaces engine.recover() (restarted=True, so every later sell is `engine_restart`) by tools.arena_tip_continuity.recover_with_continuity: ledger-open positions are adopted
    with restarted=False, and a sell the decision log shows (kind "sell", not followed by a refusal) and the engine had not priced is applied AS LOGGED from the sell journal, or the
    position is booked `engine_restart` (fail closed). The runner itself never re-derives a logged sell: a position with one is not in `self.open`.
  * The engine's state is rebuilt by the runner's OWN replay (state.poll from the tape start, no decisions: tick() decides, the poll does not), exactly as before; the checkpoint's state
    check (fingerprint of the open positions' pools at the checkpoint's row, B3) runs on that replay, and the first tick is refused (ContinuityRefused) if it fails. The first exit decision
    after a restart is therefore the first tick at or after `resume(now)`: nothing is decided in the downtime (B1).
  * A CheckpointRecorder and a SellJournal are installed from the outside; a live run keeps them for its next restart.

`run_hv7_case(make_runner, work, ...)` compares, on any factory that builds the runner class it is given: the uninterrupted runner that skips the ticks of the downtime (the same blind interval)
against kill + resume. `make_runner(dir, runner_cls)` is a dependency: the tests pass a synthetic HV7 fixture; the proof job on exploration hours (docs/runbooks/tip-restart-proof-jobs.md) needs its
own factory (the model replaced by the decision log or the replay cache). That factory is NOT built here.
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python3 tools/hv7_restart_equiv.py` from the repo root
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import importlib
import json
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tools import arena_tip_continuity as C
from tools import hv7_runner as R
from tools.arena_paper import canon

TICK_MS = R.TICK_MS
SCHEMA = "hv7_restart_equiv_v1"


class Hv7ContinuityRunner(R.Hv7Runner):
    """HV7's runner, restart-continuous. Construct it like Hv7Runner, then call `configure` once (the factory cannot pass extra arguments)."""

    def configure(self, journal: C.SellJournal | None = None, checkpoint: Mapping[str, Any] | None = None, margin_ms: int = C.REPLAY_MARGIN_MS_DEFAULT) -> "Hv7ContinuityRunner":
        self.journal = journal
        self._check: C.StateCheck | None = None
        self._check_t: int | None = None
        if checkpoint is not None:
            if checkpoint.get("fp") is None or checkpoint.get("since_slot") is None or checkpoint.get("fp_from_slot") is None:
                raise C.ContinuityRefused("checkpoint_without_state_fingerprint")
            C.check_nothing_priced_after(self.engine, checkpoint)
            self._check = C.StateCheck(self.engine, dict(checkpoint))
            self._check_t = int(checkpoint["last_row_id"][3])
        if journal is not None:
            C.attach_sell_journal(self.engine, journal)
        self.recorder = C.CheckpointRecorder(self.engine, margin_ms=margin_ms, every_rows=10**9)
        return self

    def poll_to(self, T: int) -> None:
        super().poll_to(T)
        chk = getattr(self, "_check", None)
        if chk is not None:
            chk.raise_if_failed()
            if self.engine.hw_ms is not None and self.engine.hw_ms >= (self._check_t or 0):
                chk.require_done()  # the checkpoint's row is behind us: the replayed state must equal the checkpoint's, else nothing is decided on it
                chk.uninstall()
                self._check = None

    def resume(self, now_ms: int) -> int:
        """HV7's own resume (rules 1 to 6), with engine.recover() replaced by the continuity adoption. Logged sells are read from the runner's decision logs."""
        rows = [dict(r) for arm in R.ARM_ORDER for r in self.log.rows(arm)]
        sells = C.sells_from_log(rows, bot_of=lambda r: R.bot_id(r["arm"]))
        eng = self.engine
        eng.recover = lambda: C.recover_with_continuity(eng, sells, getattr(self, "journal", None))  # type: ignore[method-assign]
        try:
            return super().resume(now_ms)
        finally:
            del eng.recover


def ledger_digest(run: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for arm in R.ARM_ORDER:
        bot = R.bot_id(arm)
        rows = [{k: v for k, v in r.items() if k != "engine_id"} for r in run.engine.ledger.read_bot(bot) if r.get("kind") != "start"]
        lines = [canon(r) for r in rows]
        out[arm] = {"rows": len(lines), "md5_ordered": hashlib.md5("\n".join(lines).encode()).hexdigest(), "md5_set": hashlib.md5("\n".join(sorted(lines)).encode()).hexdigest(),
                    "engine_restart_rows": sum(1 for r in rows if r.get("exclusion_reason") == "engine_restart"), "trade_rows": sum(1 for r in rows if r.get("kind", "trade") == "trade")}
    return out


def log_digest(run: Any) -> dict[str, str]:
    return {arm: hashlib.md5("\n".join(canon(r) for r in run.log.rows(arm)).encode()).hexdigest() for arm in ("SCAN",) + R.ARM_ORDER}


def sells_in(run: Any, lo: int, hi: int) -> int:
    """Sell decisions logged with an instant in [lo, hi): the blind interval must hold none."""
    return sum(1 for arm in R.ARM_ORDER for r in run.log.rows(arm) if r.get("kind") == "sell" and lo <= int(r["ms"]) < hi)


def run_hv7_case(make_runner: Callable[[Path, type], Any], work: Path, start_ms: int, kill_ms: int, restart_ms: int, end_ms: int, *, ckpt_lag_events: int = 0,
                 journal_mode: str = "journal", margin_ms: int = C.REPLAY_MARGIN_MS_DEFAULT) -> dict[str, Any]:
    """Reference: one Hv7ContinuityRunner that never stops but skips the ticks [kill_ms, restart_ms) (no process, no decision). Restarted: a runner is run to kill_ms and dropped, a fresh one
    on the same dirs is configured with a (possibly stale) checkpoint and the journal, resume(restart_ms) and run to end_ms. Same dirs layout as tools/test_hv7_resume.py."""
    ref = make_runner(work / "ref", Hv7ContinuityRunner).configure(margin_ms=margin_ms)
    ref.run(start_ms, kill_ms)
    ref.run(restart_ms, end_ms)
    first = make_runner(work / "rst", Hv7ContinuityRunner).configure(C.SellJournal(work / "rst" / "sell-journal.jsonl"), margin_ms=margin_ms)
    first.run(start_ms, kill_ms)
    if len(first.recorder.history) <= ckpt_lag_events:
        raise C.ContinuityRefused("no_checkpoint_that_old")
    ck = first.recorder.history[-1 - ckpt_lag_events]
    pending_at_kill = sum(len(v) for v in first.engine.pending.values())
    open_at_kill = len(first.engine.positions)
    # the process dies here: `first` is dropped with its memory (pending sells included)
    journal = C.SellJournal(work / "rst" / "sell-journal.jsonl")
    if journal_mode == "lost":
        journal = C.SellJournal(work / "rst" / "no-such-journal.jsonl")
    second = make_runner(work / "rst", Hv7ContinuityRunner).configure(journal, ck, margin_ms=margin_ms)
    nxt = second.resume(restart_ms)
    second.run(nxt, end_ms)
    a, b = ledger_digest(ref), ledger_digest(second)
    equal = all(a[arm]["md5_ordered"] == b[arm]["md5_ordered"] and a[arm]["md5_set"] == b[arm]["md5_set"] and b[arm]["engine_restart_rows"] == 0 for arm in R.ARM_ORDER)
    return {"kill_ms_offset_s": (kill_ms - start_ms) // 1000, "downtime_s": (restart_ms - kill_ms) // 1000, "ckpt_lag_events": ckpt_lag_events, "open_at_kill": open_at_kill,
            "pending_sells_at_kill": pending_at_kill, "reference": a, "restarted": b, "log_md5_equal": log_digest(ref) == log_digest(second), "sells_in_blind_interval": sells_in(second, kill_ms, restart_ms),
            "equal": bool(equal), "runner": ref}


def find_pending_sell_kill(make_runner: Callable[[Path, type], Any], work: Path, start_ms: int, end_ms: int) -> int | None:
    """P3: the first tick after which the runner holds a sell that the engine has decided and not yet priced (it lives in memory only): the kill instant between a logged sell and its pricing."""
    probe = make_runner(work / "probe", Hv7ContinuityRunner).configure()
    for T in range(start_ms, end_ms, TICK_MS):
        probe.tick(T)
        if sum(len(v) for v in probe.engine.pending.values()):
            return T + TICK_MS
    return None


def report(res: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in res.items() if k != "runner"}


def main(argv: Sequence[str] | None = None) -> int:
    """The proof job's entry point (scripts/ops/hv7-restart-equiv-job.sh): P2 (kills, downtimes, checkpoint lags) and P3 (--kills-ms pending-sell). Names, counts and md5s only.
    NOT run on real data when it was written; the factory for exploration hours is not built here."""
    from tools import arena_restart_equiv as E

    ap = argparse.ArgumentParser(description="HV7 real-runner restart equivalence (P2) and the kill between a logged sell and its pricing (P3)")
    ap.add_argument("--factory", required=True, help="module:function; function(work_dir, runner_cls, tape_dir) -> an HV7 runner of that class (model = decision log / replay cache, no API)")
    ap.add_argument("--tape-dir", required=True)
    ap.add_argument("--exploration", required=True, help="a pinned exploration list (explore-0814)")
    ap.add_argument("--allow-hours")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--start-ms", type=int, required=True)
    ap.add_argument("--end-ms", type=int, required=True)
    ap.add_argument("--kills-ms", required=True, help="comma list of kill instants (tick multiples), or 'pending-sell' (P3)")
    ap.add_argument("--downtimes-s", default="0,120,3600")
    ap.add_argument("--lags", default="0,1", help="checkpoint lags in events")
    ap.add_argument("--margin-ms", type=int, default=C.REPLAY_MARGIN_MS_DEFAULT)
    ap.add_argument("--control", action="store_true", help="also run the lost-journal control (must differ)")
    a = ap.parse_args(argv)
    try:
        work, out, tape = E.check_path(a.work_dir, "work-dir"), E.check_path(a.out, "out"), E.check_path(a.tape_dir, "tape-dir")
        E.check_hours(tape, E.parse_allow(a.allow_hours, a.exploration))
        mod, _, fn = a.factory.partition(":")
        factory = getattr(importlib.import_module(mod), fn)
    except (E.Refused, ImportError, AttributeError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    def make(d: Path, cls: type) -> Any:
        return factory(d, cls, tape)

    if a.kills_ms == "pending-sell":
        k = find_pending_sell_kill(make, work, a.start_ms, a.end_ms)
        if k is None:
            print("REFUSED: no pending sell occurs in the window (P3 has nothing to kill between)", file=sys.stderr)
            return 2
        kills = [k]
    else:
        kills = [int(x) for x in a.kills_ms.split(",") if x]
    cases: list[dict[str, Any]] = []
    for i, kill in enumerate(kills):
        for dt in (int(x) for x in a.downtimes_s.split(",") if x != ""):
            for lag in (int(x) for x in a.lags.split(",") if x != ""):
                try:
                    cases.append(report(run_hv7_case(make, work / f"c{i}-{dt}-{lag}", a.start_ms, kill, kill + dt * 1000, a.end_ms, ckpt_lag_events=lag, margin_ms=a.margin_ms)))
                except C.ContinuityRefused as exc:  # failed closed: in production the legacy recover() path (engine_restart), not a pass
                    cases.append({"kill_ms": kill, "downtime_s": dt, "ckpt_lag_events": lag, "refused": exc.reason, "equal": False})
        if a.control:
            c = report(run_hv7_case(make, work / f"ctl{i}", a.start_ms, kill, kill + 120_000, a.end_ms, journal_mode="lost", margin_ms=a.margin_ms))
            cases.append(dict(c, control="lost_journal", differs=not c["equal"]))
    real = [c for c in cases if not c.get("control")]
    ctl = [c for c in cases if c.get("control")]
    rep = {"schema": SCHEMA, "exploration": a.exploration, "cases": cases, "all_equal": bool(real) and all(c["equal"] for c in real),
           "control_differs": all(c["differs"] for c in ctl) if ctl else None}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1, sort_keys=True) + "\n")
    print(f"hv7 restart-equivalence: {sum(1 for c in real if c['equal'])}/{len(real)} cases equal; all_equal={rep['all_equal']}; control_differs={rep['control_differs']}; report {out}")
    return 0 if rep["all_equal"] and rep["control_differs"] in (None, True) else 1


if __name__ == "__main__":
    sys.exit(main())
