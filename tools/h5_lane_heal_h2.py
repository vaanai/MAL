"""Pinned COUNTS tool: H2 md5 decision-change account of h5-lane-h (QP-RULING-1104-oc-feedfix section 4, H2).

  python -m tools.h5_lane_heal_h2 --shadow-dir <dir> [--shadow-dir <dir2> ...]     the lane1 and/or lane3 shadow dirs (the MANAGER runs this)

H2 rule (verbatim): "h5-lane-h vs h5-lane-2 (waive off) differ ONLY on `feed_gap` or `bad_intent:base_breaks_unresolved_settled` refusals that h5-lane-h
accepts with heal_applied true; any other difference is a FAIL."

READS (streaming, read-only, through tools/h5_lane_oc_checks.py readers): every non-suppressed pv trigger record of every top-level h5-shadow-*.jsonl of the
dirs, kept ONLY: tools/h5_lane_oc_checks.PROJECTION fields (the rule inputs), slot, s0, and `lane_heal` (an opaque value, never printed). Mint, pool and
every wall-clock field are NOT taken: the fixture's are used (t_detect_ms = fake clock, age 0).
Each kept row is run in a FRESH lane through the pinned h5-lane-2 executor (waive_pool_gap false) and through h5-lane-h (tools/h5_lane_heal_executor.py),
in the fake-RPC harness of tools/test_h5_lane_oc.py / tools/test_h5_lane_heal_executor.py (same stake, same fixture chain state). Per pair:
  EQUAL        refusals, buys sent, decision rows (heal tags removed from h5-lane-h's rows) and RPC counts are identical
  ALLOWED_DIFF h5-lane-2 refused, and only with feed_gap and/or bad_intent:base_breaks_unresolved_settled; h5-lane-h refused nothing, sent exactly one buy,
               and its decision row has heal_applied true
  FAIL         anything else
H2 VERDICT: FAIL if any FAIL pair; NOT_YET if 0 triggers; else PASS. Exit 1 on FAIL, 2 on a usage or IO error, else 0.
OUTPUT: counts per class and per dir, the md5 of each executor's projected decision stream, blob ids of the pins. Never a mint, pool, signature, price, Q,
reserve, fill, outcome or P&L. WRITES: nothing but stdout (fixture files live in a TemporaryDirectory removed at exit).
Sequence effects are not tested (each trigger runs in a fresh lane); the heal hold (flagged gap rows and lane_gap_seq cover) is not modelled: no gap rows are fed.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from collections import Counter
from typing import Any, Iterator

from tools import h5_lane_oc_checks as C

ALLOWED_REFUSALS = {"feed_gap", "bad_intent:base_breaks_unresolved_settled"}
CLASSES = ("EQUAL", "ALLOWED_DIFF", "FAIL")
KEEP = (*C.PROJECTION, "slot", "s0", "lane_heal")
PIN_FILES = ("tools/h5_lane_heal_h2.py", "tools/h5_lane_heal_executor.py", "tools/h5_lane_executor.py", "tools/h5_executor.py", "tools/h5_shadow.py",
             "tools/h5_lane_oc_checks.py", "tools/test_h5_lane_oc.py", "tools/test_h5_lane_heal_executor.py", "tools/test_h5_executor_shadow.py",
             "tools/test_h5_executor.py", "tools/test_probe_executor.py")


def real_pv(dirs: list[str]) -> Iterator[tuple[int, dict]]:
    """(index of the --shadow-dir, kept fields) for each non-suppressed pv trigger."""
    for k, d in enumerate(dirs):
        for t in C.pv_triggers(C.shadow_files([d])):
            yield k, {f: t[f] for f in KEEP if f in t}


def project(clock, real: dict) -> dict:
    row = C.project(clock, real)  # PROJECTION fields + slot offset; a key absent from the real row is absent from the fixture row
    if "lane_heal" in real:
        row["lane_heal"] = copy.deepcopy(real["lane_heal"])
    else:
        row.pop("lane_heal", None)
    return row


def classify(lane2: dict, heal: dict) -> str:
    """lane2 / heal: {'refusals', 'sent', 'dec', 'rpc'} of one trigger (dec of heal without tags); heal also has 'applied' (heal_applied of its decision row)."""
    if (lane2["refusals"], lane2["sent"], lane2["dec"], lane2["rpc"]) == (heal["refusals"], heal["sent"], heal["dec"], heal["rpc"]):
        return "EQUAL"
    r2 = lane2["refusals"]
    if r2 and set(r2) <= ALLOWED_REFUSALS and not lane2["sent"] and heal["refusals"] == [] and len(heal["sent"]) == 1 and heal["applied"] is True:
        return "ALLOWED_DIFF"
    return "FAIL"


def run_pair(case, T, X, HE, i: int, make) -> tuple[dict, dict]:
    """One trigger through both executors in two fresh lanes of `case`'s temp dir."""
    outs = []
    for tag, env_cls, bot in (("a", T.Lane2Env, "h5-lane-2"), ("b", HE, X.BOT_ID)):
        sub = case.tmp / f"{tag}{i}"
        sub.mkdir()
        d = sub / "lane"
        lane = {**case.lane_cfg(d, bot=bot), "max_open": 1, "waive_pool_gap": False}
        extra = {"lane_heal": {"stop_path": str(d / "HEAL-STOP")}} if env_cls is HE else {}
        e = env_cls(sub, lane=lane, **extra)
        e.rpc.balance = 10 * 10**9
        case.feed(e, make(e))
        refusals, sent, dec = T.decisions(e)
        outs.append({"refusals": refusals, "sent": sent, "dec": dec, "rpc": [e.rpc.calls.count("getAccountInfo"), len(e.rpc.calls)],
                     "applied": next((r.get("heal_applied") for r in e.ledger("decision") if "heal_applied" in r), None)})
    a, b = outs
    drop = set(X.HEAL_FIELDS) | {"feed_gap_components"}
    b["dec"] = [{k: v for k, v in r.items() if k not in drop} for r in b["dec"]]
    return a, b


def run_rows(keep: list[tuple[int, dict]], n_dirs: int) -> dict[str, Any]:
    from tools import h5_lane_heal_executor as X
    from tools import test_h5_lane_heal_executor as HT
    from tools import test_h5_lane_oc as T
    per_dir = [Counter() for _ in range(n_dirs)]
    n_dir = [0] * n_dirs
    case = T.Settled0EquivalenceTests("test_the_parser_is_restored_after_the_tick")
    case.setUp()
    s2, sh, tot = [], [], Counter()
    try:
        for i, (k, real) in enumerate(keep):
            a, b = run_pair(case, T, X, HT.HealEnv, i, lambda e, r=real: project(e.clock, r))
            cls = classify(a, b)
            tot[cls] += 1
            per_dir[k][cls] += 1
            n_dir[k] += 1
            s2.append([a["refusals"], a["sent"], a["dec"], a["rpc"]])
            sh.append([b["refusals"], b["sent"], b["dec"], b["rpc"]])
    finally:
        case.doCleanups()
    md5 = lambda x: hashlib.md5(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()  # noqa: E731
    n = len(keep)
    verdict = "NOT_YET" if n == 0 else ("FAIL" if tot["FAIL"] else "PASS")
    return {"n": n, "total": tot, "per_dir": per_dir, "n_dir": n_dir, "md5_lane2": md5(s2), "md5_heal": md5(sh), "verdict": verdict, "pins": pin_blobs()}


def h2(dirs: list[str]) -> dict[str, Any]:
    return run_rows(list(real_pv(dirs)), len(dirs))


def pin_blobs() -> dict[str, str]:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = {}
    for rel in PIN_FILES:
        try:
            data = open(os.path.join(root, rel), "rb").read()
            out[rel] = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        except OSError:
            out[rel] = "unreadable"
    return out


def print_h2(res: dict[str, Any], out=None) -> None:
    out = out or sys.stdout
    w = lambda s: print(s, file=out)  # noqa: E731
    fmt = lambda c: ", ".join(f"{k}={c.get(k, 0)}" for k in CLASSES)  # noqa: E731
    w("== H2: h5-lane-h vs pinned h5-lane-2 (waive off), md5 decision-change account (fake RPC harness, real rule fields + lane_heal) ==")
    w(f"n non-suppressed pv triggers compared: {res['n']}  ({fmt(res['total'])})")
    for k, (c, n) in enumerate(zip(res["per_dir"], res["n_dir"])):
        w(f"  --shadow-dir #{k + 1} (order given): n={n} ({fmt(c)})")
    w(f"md5 h5-lane-h (projected, heal tags removed): {res['md5_heal']}")
    w(f"md5 h5-lane-2 (projected):                    {res['md5_lane2']}")
    w("rule: differ ONLY on feed_gap / bad_intent:base_breaks_unresolved_settled refusals that h5-lane-h accepts with heal_applied true; any other difference FAILs.")
    w("trigger fields projected from the real row: " + ", ".join(C.PROJECTION) + ", lane_heal, slot-s0 (as slot offset)")
    w("NOT taken from the real row: mint, pool, wall-clock times (t_detect_ms = fake clock, age 0), chain state (fixture pool). Fresh lane per trigger; no gap rows fed (hold not modelled).")
    w("pins (git blob ids on disk): " + ", ".join(f"{k}={v}" for k, v in res["pins"].items()))
    w(f"H2 VERDICT: {res['verdict']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--shadow-dir", action="append", required=True)
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        res = h2(args.shadow_dir)
    except C.UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: {type(exc).__name__}", file=sys.stderr)
        return 2
    print_h2(res)
    return 1 if res["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
