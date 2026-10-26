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

_STRIP = ("seq", "prev", "sha")


def _strip(rs) -> list[dict]:
    return [{k: v for k, v in r.items() if k not in _STRIP} for r in rs]


def drill_e1_halt(D: D6.Drill) -> dict:
    ref = D.new_run("e1ref")                       # the uninterrupted run, no halt
    ref.run(D6.START, D6.END)
    ents = sorted((r["ms"], a) for a in R.ARM_ORDER for r in D6.rows(ref, a, "entry"))
    after_stop = [e for e in ents if e[0] >= D6.MID]
    if len(after_stop) < 2:
        return {"drill": "e1_halt", "result": "FAIL", "why": "reference_has_fewer_than_2_entries_after_stop", "ref_entries_after_stop": len(after_stop)}
    halt = after_stop[len(after_stop) // 2][0]     # strictly inside the entries that follow the stop: some enter before it, some would enter at/after it
    if halt <= D6.MID:
        halt = after_stop[-1][0]
    r1 = D.new_run("e1")
    r1.run(D6.START, D6.MID)
    open_at_stop = sum(len(b.open) for b in r1.books.values())
    del r1
    r2 = D.new_run("e1")                           # new Runner over the same logs and ledger, as a host restart would be
    r2.cfg.entries_halt_ms = halt                  # the restart flag --entries-halt-ms (earlier than the window end END)
    res = r2.resume()
    r2.run(D6.MID, D6.END)
    for x in (*R.ARM_ORDER, "SCAN"):
        r2.log.verify(x)                           # raises LogChainError on a break
    per = {a: {"entries_after_halt": sum(1 for r in D6.rows(r2, a, "entry") if r["ms"] >= halt),
               "llm_rows_after_halt": sum(1 for r in D6.rows(r2, a) if r["ms"] >= halt and r["kind"] in ("llm_call", "watch_add")),
               "entries_stop_to_halt": sum(1 for r in D6.rows(r2, a, "entry") if D6.MID <= r["ms"] < halt),
               "ref_entries_stop_to_halt": sum(1 for r in D6.rows(ref, a, "entry") if D6.MID <= r["ms"] < halt)} for a in R.ARM_ORDER}
    ref_after = sum(1 for a in R.ARM_ORDER for r in D6.rows(ref, a, "entry") if r["ms"] >= halt)
    llm_arms = [a for a in R.ARM_ORDER if R.A.ARMS[a].llm]
    before_equal = all(_strip(r for r in D6.rows(r2, x) if r["ms"] < halt) == _strip(r for r in D6.rows(ref, x) if r["ms"] < halt) for x in (*R.ARM_ORDER, "SCAN"))
    p = D6.positions(r2)
    halt_rows = D6.rows(r2, "SCAN", "entries_halt")
    ok = (before_equal and ref_after > 0
          and all(per[a]["entries_after_halt"] == 0 and per[a]["llm_rows_after_halt"] == 0 for a in R.ARM_ORDER)
          and per["X"]["entries_stop_to_halt"] == per["X"]["ref_entries_stop_to_halt"] and per["X"]["entries_stop_to_halt"] > 0   # X continued until the halt, as the reference
          and all(per[a]["entries_stop_to_halt"] == per[a]["ref_entries_stop_to_halt"] for a in llm_arms)
          and len(halt_rows) == 1 and len(D6.rows(r2, "SCAN", "run_complete")) == 1
          and p["positions"] == p["distinct_positions"] and p["sell_rows"] == p["distinct_closed"] and p["positions"] == p["distinct_closed"] + p["open_now"]
          and p["positions"] > 0 and p["open_now"] == 0)
    return {"drill": "e1_halt", "result": "PASS" if ok else "FAIL", "halt_ms_after_stop": halt - D6.MID, "window_end_minus_halt_ms": D6.END - halt,
            "ref_entries_at_or_after_halt": ref_after, "rows_before_halt_equal_to_reference": before_equal, "open_at_stop": open_at_stop, "resume": res,
            "per_arm": per, "positions": p, "log_md5": D6.log_md5(r2.cfg.root), "api_calls": 0}


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
