"""H3 counts tool for the A-R tip-replay heal (QP-RULING-1104-oc-feedfix s3.3 / QP verdict on PR #1232, items 4 and 7).

Reads the trigger records of ONE lane shadow dir (`h5-shadow-*.jsonl`) and keeps ONLY `variant` and `lane_heal` of each (lines that do not contain the bytes
`"type":"trigger"` are never parsed; every other field of a trigger, i.e. outcome, price, mint, pool, signature, is dropped at once and never printed). It prints:
  - triggers seen, with a lane_heal record / without one;
  - counts by status and by (status, reason);
  - CONTROL (status not_needed) replay.match true / false / null;
  - HARNESS_ERROR flags: every heal_no_store, every heal_replay_error, every control with match false or null, every trigger with a malformed lane_heal.
Exit 0 on a completed count (flags are in the output, not the exit code); exit 2 on a usage error or an unreadable dir.
A healed trade is never H5 v1 evidence and is never pooled with another bot_id.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import sys
from typing import Any, Iterable, Sequence

MARKS = ('"type":"trigger"', '"type": "trigger"')
ERROR_REASONS = ("heal_no_store", "heal_replay_error")


def _kept(line: str) -> tuple[Any, Any, bool] | None:
    """-> (variant, lane_heal, has_key) of a trigger line, or None if the line is not a trigger. Nothing else of the record leaves this function."""
    if not any(m in line for m in MARKS):
        return None
    try:
        row = json.loads(line)
    except ValueError:
        return None
    if not isinstance(row, dict) or row.get("type") != "trigger":
        return None
    return row.get("variant"), row.get("lane_heal"), "lane_heal" in row


def count_lines(lines: Iterable[str]) -> dict[str, Any]:
    c: dict[str, Any] = {"triggers": 0, "with_lane_heal": 0, "without_lane_heal": 0, "malformed": 0,
                         "by_status": collections.Counter(), "by_status_reason": collections.Counter(), "by_variant_status": collections.Counter(),
                         "control": collections.Counter(), "harness_error": collections.Counter()}
    for line in lines:
        k = _kept(line)
        if k is None:
            continue
        variant, lh, has = k
        c["triggers"] += 1
        if not has:
            c["without_lane_heal"] += 1
            continue
        c["with_lane_heal"] += 1
        if not isinstance(lh, dict) or not isinstance(lh.get("status"), str) or not (lh.get("reason") is None or isinstance(lh.get("reason"), str)):
            c["malformed"] += 1
            c["harness_error"]["malformed_lane_heal"] += 1
            continue
        status, reason = lh["status"], lh.get("reason")
        vname = variant if isinstance(variant, str) and len(variant) <= 40 else "?"
        c["by_status"][status] += 1
        c["by_status_reason"][f"{status}/{reason}"] += 1
        c["by_variant_status"][f"{vname}/{status}"] += 1
        if reason in ERROR_REASONS:
            c["harness_error"][reason] += 1
        if status == "not_needed":
            rep = lh.get("replay")
            m = rep.get("match") if isinstance(rep, dict) else None
            key = "match_true" if m is True else "match_false" if m is False else "match_null"
            c["control"][key] += 1
            if key != "match_true":
                c["harness_error"][f"control_{key}"] += 1
    return c


def count_dir(d: str) -> dict[str, Any]:
    files = sorted(glob.glob(os.path.join(d, "h5-shadow-*.jsonl")))
    if not files:
        raise FileNotFoundError("no h5-shadow-*.jsonl in the given dir")

    def gen() -> Iterable[str]:
        for f in files:
            with open(f, "r", encoding="utf-8", errors="replace") as fh:
                yield from fh

    out = count_lines(gen())
    out["files"] = len(files)
    return out


def render(c: dict[str, Any]) -> str:
    o = [f"files: {c.get('files', '-')}", f"triggers: {c['triggers']} (with lane_heal {c['with_lane_heal']}, without {c['without_lane_heal']}, malformed {c['malformed']})"]
    for name in ("by_status", "by_status_reason", "by_variant_status"):
        o.append(f"{name}:")
        o += [f"  {k}: {v}" for k, v in sorted(c[name].items())] or ["  (none)"]
    ctl = c["control"]
    o.append(f"control replay.match: true {ctl['match_true']}, false {ctl['match_false']}, null {ctl['match_null']}")
    he = c["harness_error"]
    if he:
        o.append("HARNESS_ERROR: yes")
        o += [f"  HARNESS_ERROR {k}: {v}" for k, v in sorted(he.items())]
    else:
        o.append("HARNESS_ERROR: none")
    return "\n".join(o)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("shadow_dir", help="one lane shadow dir; only `variant` and `lane_heal` of its trigger records are read")
    a = ap.parse_args(argv)
    try:
        c = count_dir(a.shadow_dir)
    except OSError as exc:
        print(f"h5_lane_heal_counts: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(render(c))
    return 0


if __name__ == "__main__":
    sys.exit(main())
