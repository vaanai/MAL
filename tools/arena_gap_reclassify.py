#!/usr/bin/env python3
"""Post-hoc gap reclassifier: TRUE uncovered slot ranges behind the feed's slot_jump data_loss gap rows. Read-only, outcome-blind, deterministic.

Why: tools/arena_feed.py keeps ONE high-water slot per program over the merged socket stream and logs a slot_jump data_loss gap whenever a
notice jumps more than GAP_SLOTS (30) past it. When one endpoint runs ahead of the other (endpoint skew) the "skipped" slots arrive late from the
lagging socket and are in the tape. The gap row is never revisited. hunt27 SKEW-TEST: 212/212 PumpSwap gaps had tape rows, 137 were fully covered.
This tool takes the gap rows and the tape rows (same tape files) and reports, per program, which slots inside each flagged range have NO tape
row from any source. It changes nothing in the feed or the tape and relabels no file; a consumer decides what to do with the output.

Reads ONLY, from each tape file: gap rows (type gap) and, from DATA rows (types trade, create, complete, pool_create), the `prog`, `slot` and
`t_recv_ms` fields by regex. No mint, pool, wallet, price, size, side, fee, decision, ledger or outcome field is read. Paths that contain a
ledger/decision/bots/runner-status/probe word are refused by tools/arena_hour_health.guard.

Per slot_jump gap (data_loss true; deduplicated on prog, from_slot, to_slot) the skipped slots are from_slot+1 .. to_slot-1:
  covered           every skipped slot has at least one data row from any source arriving by to_ms + grace_s
  partial           some do, some do not
  empty             none does
  unresolved_tape_cut  the tape of that program ends before to_ms + grace_s: late rows could still be missing, no verdict
A slot with no row is reported as UNCOVERED. It may be a slot with no program transaction or a skipped Solana slot, so uncovered is an UPPER
BOUND on whole-slot loss only. Within-slot loss in covered slots (a slot needs just one row to be covered) is NOT measured. Never read it as proof of loss.
A row covers a slot only if its t_recv_ms <= to_ms + grace_s (default 120, pinned): the class never depends on how much tape lies past the gap.
Per gap, late_until_ms = the max t_recv_ms of the covering rows (null if none): an attempt decided in [from_ms, late_until_ms] had a degraded view.
Inputs must be whole, contiguous, unduplicated tape hours (tape-YYYY-MM-DDTHH.jsonl[.zst]); a missing hour is refused (exit 2). A gap wider than
MAX_GAP_SLOTS is refused (exit 2). --out goes through the same path guard as the inputs.
Gap kinds without a slot range (silence, feed_restart, process_restart, socket_reconnect with data_loss) are NOT reclassified. They are listed
in not_reclassified (counts and union seconds) and stay data loss for any consumer.

Output JSON lines: a {"type":"header"} line (schema, grace_s, max_gap_slots, hours), one {"type":"gap",...} per slot_jump gap (sorted), one {"type":"program",...} per program with counts and the merged uncovered
ranges (inclusive [first, last] slots), a {"type":"not_reclassified"} line, a {"type":"meta"} line, then {"type":"sha256"} over all earlier
lines (each + "\\n").
Exit codes: 0 ok, 2 refused or invalid input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arena_hour_health import Refused, guard  # noqa: E402

SCHEMA = "arena_gap_reclassify_v1"
PROGRAMS = ("pumpswap", "pumpfun")
DATA_TYPES = frozenset({b"trade", b"create", b"complete", b"pool_create"})
TYPE_RE = re.compile(rb'"type":\s*"([a-z_]+)"')
PROG_RE = re.compile(rb'"prog":\s*"([a-z_]+)"')
SLOT_RE = re.compile(rb'"slot":\s*(\d+)')
RECV_RE = re.compile(rb'"t_recv_ms":\s*(\d+)')
DEFAULT_GRACE_S = 120  # SKEW-TEST max observed lag 83.3 s


def _int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _lines(path: Path):
    if path.name.endswith(".zst"):
        exe = shutil.which("zstd")
        if exe is None:
            raise Refused("zstd CLI not found")
        proc = subprocess.Popen([exe, "-dc", "-q", str(path)], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        assert proc.stdout is not None
        try:
            yield from proc.stdout
        finally:
            proc.stdout.close()
            if proc.wait() != 0:
                raise Refused(f"zstd failed on {path.name}")
    else:
        with open(path, "rb") as f:
            yield from f


def scan(paths: list[str]) -> dict[str, Any]:
    """One pass over the tape files. Returns slot sets, the last t_recv_ms per program, and the data_loss gap rows."""
    slots: dict[str, set[int]] = {p: set() for p in PROGRAMS}
    last_ms: dict[str, int] = {p: 0 for p in PROGRAMS}
    jumps: dict[tuple[str, int, int], dict[str, Any]] = {}
    other: dict[str, set[tuple[int, int]]] = {}
    other_n: dict[str, int] = {}
    torn = 0
    for raw in paths:
        p = guard(raw)
        if not p.is_file():
            raise Refused(f"tape file missing: {p.name}")
        for ln in _lines(p):
            core = ln.strip()
            if not core:
                continue
            if not core.startswith(b"{") or not core.endswith(b"}"):
                torn += 1
                continue
            if b'"gap"' in ln:
                try:
                    r = json.loads(ln)
                except ValueError:
                    torn += 1
                    continue
                if not isinstance(r, dict) or r.get("type") != "gap" or r.get("data_loss") is not True:
                    continue
                kind, prog = r.get("kind"), r.get("prog")
                if kind == "slot_jump" and prog in PROGRAMS and all(_int(r.get(k)) for k in ("from_slot", "to_slot", "from_ms", "to_ms")):
                    jumps[(prog, r["from_slot"], r["to_slot"])] = {"from_ms": r["from_ms"], "to_ms": r["to_ms"]}
                else:
                    k = f"{prog}:{kind}"
                    other_n[k] = other_n.get(k, 0) + 1
                    f, t = r.get("from_ms"), r.get("to_ms")
                    if _int(f) and _int(t) and t >= f:
                        other.setdefault(k, set()).add((f, t))
                continue
            tm = TYPE_RE.search(ln)
            if not tm or tm.group(1) not in DATA_TYPES:
                continue
            pm, sm, rm = PROG_RE.search(ln), SLOT_RE.search(ln), RECV_RE.search(ln)
            if not pm or not sm or not rm:
                continue
            prog = pm.group(1).decode()
            if prog not in slots:
                continue
            slots[prog].add(int(sm.group(1)))
            t = int(rm.group(1))
            if t > last_ms[prog]:
                last_ms[prog] = t
    return {"slots": slots, "last_ms": last_ms, "jumps": jumps, "other": other, "other_n": other_n, "torn": torn}


def _runs(missing: list[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for s in missing:
        if out and out[-1][1] == s - 1:
            out[-1][1] = s
        else:
            out.append([s, s])
    return out


def _merge(ranges: list[list[int]]) -> list[list[int]]:
    out: list[list[int]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _union_s(spans) -> float:
    tot, cur = 0, None
    for f, t in sorted(spans):
        if cur is None or f > cur[1]:
            if cur:
                tot += cur[1] - cur[0]
            cur = [f, t]
        else:
            cur[1] = max(cur[1], t)
    if cur:
        tot += cur[1] - cur[0]
    return tot / 1000.0


def reclassify(sc: dict[str, Any], grace_s: int = DEFAULT_GRACE_S) -> list[dict[str, Any]]:
    """Pure function of the scan result. Returns the output records (without the sha256 line)."""
    recs: list[dict[str, Any]] = []
    per: dict[str, dict[str, Any]] = {p: {"flagged": 0, "covered": 0, "partial": 0, "empty": 0, "unresolved_tape_cut": 0,
                                          "skipped_slots": 0, "uncovered_slots": 0, "ranges": []} for p in PROGRAMS}
    for (prog, fs, ts) in sorted(sc["jumps"]):
        j = sc["jumps"][(prog, fs, ts)]
        seen = sc["slots"][prog]
        skipped = max(0, ts - fs - 1)
        missing = [s for s in range(fs + 1, ts) if s not in seen]
        if sc["last_ms"][prog] < j["to_ms"] + grace_s * 1000:
            cls = "unresolved_tape_cut"
        elif not missing:
            cls = "covered"
        elif len(missing) == skipped:
            cls = "empty"
        else:
            cls = "partial"
        runs = _runs(missing)
        recs.append({"type": "gap", "prog": prog, "from_slot": fs, "to_slot": ts, "from_ms": j["from_ms"], "to_ms": j["to_ms"],
                     "skipped_slots": skipped, "uncovered_slots": len(missing), "class": cls, "uncovered_ranges": runs})
        a = per[prog]
        a["flagged"] += 1
        a[cls] += 1
        a["skipped_slots"] += skipped
        a["uncovered_slots"] += len(missing)
        if cls != "unresolved_tape_cut":
            a["ranges"].extend(runs)
    for prog in PROGRAMS:
        a = per[prog]
        merged = _merge(a.pop("ranges"))
        recs.append({"type": "program", "prog": prog, **a, "uncovered_ranges_merged": merged,
                     "uncovered_slots_merged": sum(b - x + 1 for x, b in merged),
                     "covered_share": (a["covered"] / a["flagged"]) if a["flagged"] else None})
    nr = {k: {"rows": sc["other_n"][k], "union_s": _union_s(sc["other"].get(k, set()))} for k in sorted(sc["other_n"])}
    recs.append({"type": "not_reclassified", "kinds": nr})
    recs.append({"type": "meta", "schema": SCHEMA, "grace_s": grace_s, "torn_lines": sc["torn"],
                 "note": "uncovered = no tape row from any source by to_ms + grace_s; an upper bound on whole-slot loss; within-slot loss in covered slots not measured; not proof of loss"})
    return recs


def render(recs: list[dict[str, Any]]) -> list[str]:
    lines = [json.dumps(r, sort_keys=True, separators=(",", ":"), allow_nan=False) for r in recs]
    h = hashlib.sha256()
    for ln in lines:
        h.update((ln + "\n").encode())
    lines.append(json.dumps({"type": "sha256", "lines": len(lines), "sha256": h.hexdigest()}, sort_keys=True, separators=(",", ":")))
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tape", nargs="+", required=True, help="tape hour files (.jsonl or .jsonl.zst); gap rows and data rows are read from these")
    ap.add_argument("--grace-s", type=int, default=DEFAULT_GRACE_S, help="a gap needs tape of its program to run this long past to_ms, else unresolved_tape_cut")
    ap.add_argument("--out", help="output file (default stdout)")
    ap.add_argument("--counts-only", action="store_true", help="omit the per-gap lines and the range lists")
    args = ap.parse_args(argv)
    if args.grace_s < 0:
        print("refused: --grace-s must be >= 0", file=sys.stderr)
        return 2
    try:
        sc = scan(sorted(set(args.tape)))
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    recs = reclassify(sc, args.grace_s)
    if args.counts_only:
        recs = [r for r in recs if r["type"] != "gap"]
        for r in recs:
            r.pop("uncovered_ranges_merged", None)
    text = "\n".join(render(recs)) + "\n"
    if args.out:
        Path(args.out).write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
