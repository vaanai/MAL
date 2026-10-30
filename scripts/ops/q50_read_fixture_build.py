"""Synthetic Q50 fixture tree for scripts/ops/q50-read.sh (no real ledger, shadow dir or applied log is opened). PAPER ONLY.

Run with PYTHONPATH = a checkout of a31d9864 (the read tools' tests build the rows):  python q50_read_fixture_build.py ROOT KIND
KIND: pass (11 of 12 pools win), fail (every pool loses), ended (98 counted trades: no boundary by the end-seal), mismatch (a READ record whose refusal count differs from the ledger),
endmismatch (pass, but every strip in the shadow dir is altered after pricing, so the legs tool's END consistency fails: worst-in-slot NOT MEASURED).
The fixture uses the REAL Q50 clock (2026-10-25T00:00Z) and end-seal, so the script's production constants apply unpatched: 24 pools a day on 10-25..10-29 (5 dates, 120 counted)
gives B = 2026-10-30T00:00Z. Written by the code that writes them: the rows by tools/test_arena_h5_read's builder (arena_h5_score.price_trade / ledger_rows / write_ledger), the Am.11
record by tools/arena_h5_q50_instant.record_doc / record. The shadow dir holds one pv trigger (v_print) and the strip of every pool; pools with i % 8 == 7 are out of the V band.
Layout: ROOT/arena/{ledger, h5-q50-2026-10-24, a3-books.json}, ROOT/out/{a3-books-v2-copy.json (0444), q50-read-params.txt}, ROOT/sm/ (the test writes a3-applied.jsonl).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools import arena_h5_q50_instant as qi
from tools import test_arena_h5_read as TR
from tools.test_arena_h5_books import V2_TEXT

CLOCK, END = 1_792_886_400_000, 1_793_923_200_000
root, kind = Path(sys.argv[1]).resolve(), sys.argv[2]
assert kind in ("pass", "fail", "ended", "mismatch", "endmismatch"), kind
Q50 = qi.Q50_ID
TR.BT0 = int(datetime(2026, 10, 25, tzinfo=timezone.utc).timestamp())  # make_book / make_pool read the module global at call time
arena = root / "arena"
ledger, shadow, out, sm = arena / "ledger", arena / "h5-q50-2026-10-24", root / "out", root / "sm"
for d in (ledger, shadow, out, sm):
    d.mkdir(parents=True, exist_ok=True)


class Box:  # make_book uses only self.ledger
    pass


box = Box()
box.ledger = ledger
per_day, excl = (20, {(0, 0), (0, 1)}) if kind == "ended" else (24, set())  # ended: 100 attempts, 98 counted trades, so no Am.11 boundary by the end-seal
kfn = (lambda d, i: "loss") if kind == "fail" else TR.default_kind
book = TR.Base.make_book(box, Q50, per_day=per_day, days=5, kind=kfn, excluded=excl)
recs = []
for d, i, k, _counted in book["pools"]:
    trig, _out, strip = TR.make_pool(d, i, k)
    recs.append(dict(trig, v_print=18_000_000_000 if i % 8 == 7 else 17_600_000_000))
    if kind == "endmismatch":  # not the strip the scorer priced: the exit prints move, so END re-pricing differs from the ledger rows
        strip = dict(strip, rows=[[r[0], r[1] * 1.5, r[2] * 1.5, r[3]] for r in strip["rows"]], post_last=[strip["post_last"][0], strip["post_last"][1] * 1.5, strip["post_last"][2] * 1.5, strip["post_last"][3]])
    recs.append(strip)
(shadow / "h5-shadow-2026-10-25T00.jsonl").write_text("".join(json.dumps(x) + "\n" for x in recs))


def lines(pre: str) -> list[dict]:
    return [json.loads(x) for f in sorted((ledger / Q50).glob(f"{pre}-*.jsonl")) for x in f.read_text().splitlines() if x.strip()]


counted: dict[str, int] = {}
for r in lines("trades"):
    counted[r["pool"]] = r["decision_ms"]
cls = {"counted": sorted(counted.values()), "excluded": sorted(e["decision_ms"] for e in lines("exclusions")), "open": [],
       "refused": sorted((r["decision_ms"], r["reason"]) for r in lines("refusals"))}
now = END if kind == "ended" else 2**62
doc = qi.record_doc(Q50, cls, clock_start_ms=CLOCK, end_ms=END, now_ms=now, attempt_definition="fixture")
if kind == "mismatch":
    doc["refused_before"] = 7  # the ledger holds 0 refusals: the read tool refuses before any figure
print("record", qi.record(ledger / Q50, doc), doc["status"], doc["read_instant_ms"])
(arena / "a3-books.json").write_text(V2_TEXT)
bc = out / "a3-books-v2-copy.json"
bc.write_text(V2_TEXT)
os.chmod(bc, 0o444)
(out / "q50-read-params.txt").write_text(f"# fixture params (QP-VERDICT-1030-q50-read-pack values, plus two synthetic report-only lines)\nN_BOOKS=14\nC=1\n"
                                         f"PRIOR_READ=hv4-BIG-X=FAIL_n_lt_100\nOTHER_P=hv4-CON-X=0.31\nSHADOW_DIR={shadow}\n")
