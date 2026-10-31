"""HV6 E1 restart drill (QP-RULING-1026-entry-rate, open item 1): stop the runner mid-replay, restart it with resume() and an EARLIER-than-the-window-end
--entries-halt-ms, and check that no arm opens an entry at or after the halt, that X keeps entering up to the halt, and that the chains and positions verify.

StubModel, synthetic tape dated 2026-09-25, fake chains, 0 API calls. Reuses tools.drill_hv6_item6 (fixtures, no runtime change). Prints md5s and counts only.
Usage: python3 -m tools.drill_hv6_e1_halt --drill-id ID --sha SHA
"""

from __future__ import annotations

import argparse
from typing import Any

from tools import drill_hv6_item6 as D6
from tools import hv6_runner as R

E = D6.START - 2 * D6.MIN   # the synthetic tape's one entry tick when the replay starts at START - 3 min (X at it, the LLM arms 50 ms later by the stub latency)
_STRIP = ("seq", "prev", "sha")


def _strip(rs) -> list[dict]:
    return [{k: v for k, v in r.items() if k not in _STRIP} for r in rs]


def _case(D: D6.Drill, ref: R.Runner, tag: str, stop: int, halt: int) -> dict:
    """Run to `stop`, drop the runner, build a NEW one over the same dirs, set the earlier entries-halt, resume(), run to the window end."""
    r1 = D.new_run(tag)
    r1.run(D6.START - 3 * D6.MIN, stop)
    open_at_stop = sum(len(b.open) for b in r1.books.values())
    del r1
    r2 = D.new_run(tag)                            # new Runner over the same logs and ledger, as a host restart would be
    r2.cfg.entries_halt_ms = halt                  # the restart flag --entries-halt-ms (earlier than the window end END)
    res = r2.resume()
    r2.run(stop, D6.END)
    for x in (*R.ARM_ORDER, "SCAN"):
        r2.log.verify(x)                           # raises LogChainError on a break
    per = {}
    for a in R.ARM_ORDER:
        mine, theirs = D6.rows(r2, a, "entry"), D6.rows(ref, a, "entry")
        per[a] = {"entries_after_halt": sum(1 for r in mine if r["ms"] >= halt),
                  "llm_rows_after_halt": sum(1 for r in D6.rows(r2, a) if r["ms"] >= halt and r["kind"] in ("llm_call", "watch_add")),
                  "entries_before_halt": sum(1 for r in mine if r["ms"] < halt), "ref_entries_before_halt": sum(1 for r in theirs if r["ms"] < halt)}
    ref_after = sum(1 for a in R.ARM_ORDER for r in D6.rows(ref, a, "entry") if r["ms"] >= halt)
    ref_before_x = per["X"]["ref_entries_before_halt"]
    p = D6.positions(r2)
    return {"case": tag, "halt_minus_entry_tick_ms": halt - E, "stop_minus_entry_tick_ms": stop - E, "ref_entries_at_or_after_halt": ref_after,
            "ref_x_entries_before_halt": ref_before_x, "open_at_stop": open_at_stop, "resume": res, "per_arm": per, "positions": p, "halt_rows": len(D6.rows(r2, "SCAN", "entries_halt")),
            "run_complete_rows": len(D6.rows(r2, "SCAN", "run_complete")), "log_md5": D6.log_md5(r2.cfg.root),
            "entries_after_halt_total": sum(v["entries_after_halt"] for v in per.values()), "llm_rows_after_halt_total": sum(v["llm_rows_after_halt"] for v in per.values()),
            "arms_equal_ref_before_halt": all(v["entries_before_halt"] == v["ref_entries_before_halt"] for v in per.values()),
            "chain_ok": True, "positions_ok": (p["positions"] == p["distinct_positions"] and p["sell_rows"] == p["distinct_closed"]
                                              and p["positions"] == p["distinct_closed"] + p["open_now"] and p["open_now"] == 0)}


def drill_e1_halt(D: D6.Drill) -> dict:
    """Two restarts of the same replay. The synthetic tape gives one entry tick (all arms at the first tick; X at it, the LLM arms 50 ms later by the stub latency).
    A: stop before it, resume with halt = the entry tick: nobody may enter (the reference has entries at/after this halt, so the check is not vacuous).
    B: stop before it, resume with halt = entry tick + 500 ms: X and the LLM arms enter exactly as the reference (they continue up to the halt), and nothing after it."""
    ref = D.new_run("e1ref")                       # the uninterrupted run, no halt
    ref.run(D6.START - 3 * D6.MIN, D6.END)
    A = _case(D, ref, "e1a", E - 1000, E)
    B = _case(D, ref, "e1b", E - 1000, E + 500)
    okA = (A["ref_entries_at_or_after_halt"] > 0 and A["entries_after_halt_total"] == 0 and A["llm_rows_after_halt_total"] == 0 and A["halt_rows"] == 1
           and A["run_complete_rows"] == 1 and A["positions_ok"])
    okB = (B["ref_x_entries_before_halt"] > 0 and B["arms_equal_ref_before_halt"] and B["entries_after_halt_total"] == 0 and B["llm_rows_after_halt_total"] == 0
           and B["halt_rows"] == 1 and B["run_complete_rows"] == 1 and B["positions_ok"] and B["positions"]["positions"] > 0)
    return {"drill": "e1_halt", "result": "PASS" if okA and okB else "FAIL", "caseA_ok": okA, "caseB_ok": okB, "A": A, "B": B, "api_calls": 0}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HV6 E1 restart-with-earlier-halt drill (StubModel, synthetic tape, fake chains). Prints md5 and counts only.")
    ap.add_argument("--drill-id", required=True)
    ap.add_argument("--sha", required=True)
    a = ap.parse_args(argv)
    D = D6.Drill(a.drill_id, a.sha)
    D6.out({"e1_drill": "start", "sha": a.sha, "tape": "synthetic", "real_arena_hours": 0, "chain": "fake", "model": "StubModel", "api_calls": 0})
    try:
        r: Any = drill_e1_halt(D)
    except Exception as e:  # noqa: BLE001 - class name only
        r = {"drill": "e1_halt", "result": "FAIL", "error_class": type(e).__name__}
    D6.out(r)
    D6.out({"e1_drill": "end", "result": r["result"]})
    return 0 if r["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
