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
Optional --produced-slots FILE (one program per file; pinned, written by tools/arena_fetch_produced_slots.py from a public RPC getBlocks): an uncovered slot with no
produced block (leader-skipped) lost nothing and counts as covered; the tool stays offline. Gaps made covered only that way are counted in
covered_via_no_block, and no_block_slots is printed per gap.
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
import bisect
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


HOUR_RE = re.compile(r"^tape-(\d{4}-\d\d-\d\dT\d\d)\.jsonl(\.zst)?$")
HOUR_MS = 3_600_000
MAX_GAP_SLOTS = 100_000  # a slot_jump wider than this is refused (corrupt row); the largest observed PumpSwap/pump.fun jump is a few hundred slots


def check_hours(paths: list[str]) -> list[tuple[int, Path]]:
    """Guard every path; names must be tape-YYYY-MM-DDTHH.jsonl[.zst], one file per hour, hours contiguous. Returns [(hour_start_ms, path)]."""
    from datetime import datetime, timezone
    out: list[tuple[int, Path]] = []
    for raw in sorted(set(paths)):
        p = guard(raw)
        m = HOUR_RE.match(p.name)
        if not m:
            raise Refused(f"not a tape hour file name: {p.name}")
        if not p.is_file():
            raise Refused(f"tape file missing: {p.name}")
        h = int(datetime.strptime(m.group(1), "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)
        out.append((h, p))
    out.sort()
    for (a, pa), (b, pb) in zip(out, out[1:]):
        if a == b:
            raise Refused(f"two files for one hour: {pa.name}, {pb.name}")
        if b - a != HOUR_MS:
            raise Refused(f"tape hours are not contiguous: {pa.name} then {pb.name}")
    if not out:
        raise Refused("no tape files")
    return out


def scan(paths: list[str], grace_s: int = DEFAULT_GRACE_S) -> dict[str, Any]:
    """Two passes over contiguous tape hours. Pass 1: data_loss gap rows. Pass 2: for each data row of a program, the gaps whose skipped range holds
    its slot; the row covers that slot only if t_recv_ms <= gap.to_ms + grace_s. Memory: per gap only the covered skipped slots."""
    hours = check_hours(paths)
    files = [p for _, p in hours]
    grace_ms = grace_s * 1000
    jumps: dict[tuple[str, int, int], dict[str, Any]] = {}
    other: dict[str, set[tuple[int, int]]] = {}
    other_n: dict[str, int] = {}
    torn = 0
    for p in files:
        for ln in _lines(p):
            core = ln.strip()
            if not core:
                continue
            if not core.startswith(b"{") or not core.endswith(b"}"):
                torn += 1
                continue
            if b'"gap"' not in ln:
                continue
            try:
                r = json.loads(ln)
            except ValueError:
                torn += 1
                continue
            if not isinstance(r, dict) or r.get("type") != "gap" or r.get("data_loss") is not True:
                continue
            kind, prog = r.get("kind"), r.get("prog")
            if kind == "slot_jump" and prog in PROGRAMS and all(_int(r.get(k)) for k in ("from_slot", "to_slot", "from_ms", "to_ms")):
                if r["to_slot"] - r["from_slot"] > MAX_GAP_SLOTS:
                    raise Refused(f"slot_jump wider than MAX_GAP_SLOTS={MAX_GAP_SLOTS}")
                jumps[(prog, r["from_slot"], r["to_slot"])] = {"from_ms": r["from_ms"], "to_ms": r["to_ms"]}
            else:
                k = f"{prog}:{kind}"
                other_n[k] = other_n.get(k, 0) + 1
                f, t = r.get("from_ms"), r.get("to_ms")
                if _int(f) and _int(t) and t >= f:
                    other.setdefault(k, set()).add((f, t))
    keys = sorted(jumps)
    cover: dict[tuple[str, int, int], dict[str, Any]] = {k: {"slots": set(), "late": 0} for k in keys}
    starts = {p: [k[1] for k in keys if k[0] == p] for p in PROGRAMS}
    gaps_of = {p: [k for k in keys if k[0] == p] for p in PROGRAMS}
    maxw = {p: max([k[2] - k[1] for k in gaps_of[p]] or [0]) for p in PROGRAMS}
    last_ms: dict[str, int] = {p: 0 for p in PROGRAMS}
    for p in files:
        for ln in _lines(p):
            if b'"gap"' in ln:
                continue
            tm = TYPE_RE.search(ln)
            if not tm or tm.group(1) not in DATA_TYPES:
                continue
            pm, sm, rm = PROG_RE.search(ln), SLOT_RE.search(ln), RECV_RE.search(ln)
            if not pm or not sm or not rm:
                continue
            prog = pm.group(1).decode()
            if prog not in last_ms:
                continue
            t = int(rm.group(1))
            if t > last_ms[prog]:
                last_ms[prog] = t
            if not starts[prog]:
                continue
            sl = int(sm.group(1))
            i = bisect.bisect_left(starts[prog], sl) - 1  # last gap with from_slot < sl
            while i >= 0 and starts[prog][i] > sl - maxw[prog] - 1:
                k = gaps_of[prog][i]
                if sl < k[2] and t <= jumps[k]["to_ms"] + grace_ms:
                    c = cover[k]
                    c["slots"].add(sl)
                    if t > c["late"]:
                        c["late"] = t
                i -= 1
    return {"hours": [h for h, _ in hours], "last_ms": last_ms, "jumps": jumps, "cover": cover, "other": other, "other_n": other_n, "torn": torn}


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


def load_produced(path: str) -> dict[str, Any]:
    """Read a pinned file written by tools/arena_fetch_produced_slots.py. Verifies its own sha256 line. Returns first/last/ranges/sha256."""
    p = guard(path)
    if not p.is_file():
        raise Refused("produced-slots file missing")
    lines = p.read_text().splitlines()
    if len(lines) != 2:
        raise Refused("produced-slots file must have 2 lines")
    try:
        head, tail = json.loads(lines[0]), json.loads(lines[1])
    except ValueError:
        raise Refused("produced-slots file is not JSON")
    if head.get("type") != "produced_slots" or tail.get("type") != "sha256":
        raise Refused("produced-slots file has the wrong record types")
    if hashlib.sha256((lines[0] + "\n").encode()).hexdigest() != tail.get("sha256"):
        raise Refused("produced-slots sha256 mismatch")
    rg = head.get("ranges")
    if not (_int(head.get("first")) and _int(head.get("last")) and isinstance(rg, list)):
        raise Refused("produced-slots file malformed")
    q = head.get("queried")
    if not (isinstance(q, list) and q and all(isinstance(r, list) and len(r) == 2 and _int(r[0]) and _int(r[1]) and r[0] <= r[1] for r in q)
            and all(a[1] < b[0] for a, b in zip(q, q[1:]))):
        raise Refused("produced-slots queried ranges missing or not sorted and disjoint")
    prev = head["first"] - 2
    for r in rg:
        if not (isinstance(r, list) and len(r) == 2 and _int(r[0]) and _int(r[1]) and r[0] <= r[1] and r[0] > prev + 1 and head["first"] <= r[0] and r[1] <= head["last"]):
            raise Refused("produced-slots ranges not sorted, disjoint and inside [first, last]")
        prev = r[1]
    if head.get("prog") not in PROGRAMS:
        raise Refused("produced-slots file has no valid prog")
    return {"prog": head["prog"], "queried": q, "qstarts": [r[0] for r in q], "first": head["first"], "last": head["last"], "ranges": rg, "starts": [r[0] for r in rg], "sha256": tail["sha256"]}


def _queried(pr: dict[str, Any], s: int) -> bool:
    i = bisect.bisect_right(pr["qstarts"], s) - 1
    return i >= 0 and pr["queried"][i][1] >= s


def _produced(pr: dict[str, Any], s: int) -> bool:
    i = bisect.bisect_right(pr["starts"], s) - 1
    return i >= 0 and pr["ranges"][i][1] >= s


def _day(ms: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def reclassify(sc: dict[str, Any], grace_s: int = DEFAULT_GRACE_S, windows: tuple[int, ...] = (), produced: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Pure function of the scan result (scan must have used the same grace_s). Returns the output records (without the sha256 line)."""
    recs: list[dict[str, Any]] = [{"type": "header", "schema": SCHEMA, "grace_s": grace_s, "max_gap_slots": MAX_GAP_SLOTS,
                                   "first_hour_ms": sc["hours"][0], "last_hour_ms": sc["hours"][-1], "hours": len(sc["hours"]),
                                   "produced_slots_sha256": produced["sha256"] if produced else None}]
    per: dict[str, dict[str, Any]] = {p: {"flagged": 0, "covered": 0, "partial": 0, "empty": 0, "unresolved_tape_cut": 0,
                                          "skipped_slots": 0, "uncovered_slots": 0, "no_block_slots": 0, "covered_via_no_block": 0, "ranges": []} for p in PROGRAMS}
    by_day: dict[str, dict[str, dict[str, int]]] = {p: {} for p in PROGRAMS}
    gaprows: list[dict[str, Any]] = []
    for (prog, fs, ts) in sorted(sc["jumps"]):
        j = sc["jumps"][(prog, fs, ts)]
        cov = sc["cover"][(prog, fs, ts)]
        skipped = max(0, ts - fs - 1)
        missing = [s for s in range(fs + 1, ts) if s not in cov["slots"]]
        raw_missing = len(missing)
        is_cut = sc["last_ms"][prog] < j["to_ms"] + grace_s * 1000
        if produced is not None and produced["prog"] == prog and missing and not is_cut:
            if not all(_queried(produced, x) for x in missing):
                raise Refused("uncovered slot outside the produced-slots queried ranges")
            missing = [s for s in missing if _produced(produced, s)]  # an uncovered slot with no produced block lost nothing
        if is_cut:
            cls = "unresolved_tape_cut"
        elif not missing:
            cls = "covered"
        elif len(missing) == skipped:
            cls = "empty"
        else:
            cls = "partial"
        runs = _runs(missing)
        late = cov["late"] or None
        gaprows.append({"prog": prog, "from_ms": j["from_ms"], "end_ms": max(j["to_ms"], late or 0), "class": cls})
        recs.append({"type": "gap", "prog": prog, "from_slot": fs, "to_slot": ts, "from_ms": j["from_ms"], "to_ms": j["to_ms"],
                     "late_until_ms": late, "skipped_slots": skipped, "uncovered_slots": len(missing), "no_block_slots": raw_missing - len(missing), "class": cls, "uncovered_ranges": runs})
        a = per[prog]
        a["flagged"] += 1
        a[cls] += 1
        a["skipped_slots"] += skipped
        a["uncovered_slots"] += len(missing)
        a["no_block_slots"] += raw_missing - len(missing)
        a["covered_via_no_block"] += int(cls == "covered" and raw_missing > 0)
        d = by_day[prog].setdefault(_day(j["from_ms"]), {"flagged": 0, "covered": 0, "partial": 0, "empty": 0, "unresolved_tape_cut": 0})
        d["flagged"] += 1
        d[cls] += 1
        if cls != "unresolved_tape_cut":
            a["ranges"].extend(runs)
    for prog in PROGRAMS:
        a = per[prog]
        merged = _merge(a.pop("ranges"))
        recs.append({"type": "program", "prog": prog, **a, "uncovered_ranges_merged": merged,
                     "uncovered_slots_merged": sum(b - x + 1 for x, b in merged),
                     "covered_share": (a["covered"] / a["flagged"]) if a["flagged"] else None,
                     "by_day": {k: by_day[prog][k] for k in sorted(by_day[prog])}})
    nr = {k: {"rows": sc["other_n"][k], "union_s": _union_s(sc["other"].get(k, set()))} for k in sorted(sc["other_n"])}
    recs.append({"type": "not_reclassified", "kinds": nr})
    for n in windows:
        for prog in PROGRAMS:
            h0, h1 = sc["hours"][0], sc["hours"][-1] + HOUR_MS
            other_spans = [sp for k, v in sc["other"].items() if k.split(":")[0] in (prog, "all", "None") for sp in v]
            mine = [g for g in gaprows if g["prog"] == prog]
            tot = ok = ok_other = 0
            s = h0
            while s + n * HOUR_MS <= h1:
                e = s + n * HOUR_MS
                tot += 1
                if all(g["class"] == "covered" for g in mine if g["from_ms"] < e and g["end_ms"] >= s):
                    ok += 1
                    if not any(f < e and t >= s for f, t in other_spans):
                        ok_other += 1
                s += HOUR_MS
            recs.append({"type": "windows", "prog": prog, "hours": n, "windows": tot, "all_covered": ok,
                         "all_covered_and_no_other_data_loss_kind": ok_other,
                         "share_all_covered": (ok / tot) if tot else None})
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
    ap.add_argument("--tape", nargs="+", required=True, help="contiguous tape hour files (.jsonl or .jsonl.zst); gap rows and data rows are read from these")
    ap.add_argument("--grace-s", type=int, default=DEFAULT_GRACE_S, help="a row covers a slot only if t_recv_ms <= to_ms + grace_s; pinned default 120")
    ap.add_argument("--windows", nargs="*", type=int, default=[], help="window lengths in hours (start every hour): count windows whose flagged gaps are all covered")
    ap.add_argument("--produced-slots", help="pinned file from tools/arena_fetch_produced_slots.py: an uncovered slot with NO produced block counts as covered (leader-skipped)")
    ap.add_argument("--out", help="output file (default stdout)")
    ap.add_argument("--counts-only", action="store_true", help="omit the per-gap lines and the range lists")
    args = ap.parse_args(argv)
    if args.grace_s < 0 or any(w <= 0 for w in args.windows):
        print("refused: --grace-s must be >= 0 and windows > 0", file=sys.stderr)
        return 2
    try:
        outp = guard(args.out) if args.out else None
        produced = load_produced(args.produced_slots) if args.produced_slots else None
        sc = scan(args.tape, args.grace_s)
        recs = reclassify(sc, args.grace_s, tuple(args.windows), produced)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    if args.counts_only:
        recs = [r for r in recs if r["type"] != "gap"]
        for r in recs:
            r.pop("uncovered_ranges_merged", None)
    text = "\n".join(render(recs)) + "\n"
    if outp:
        outp.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
