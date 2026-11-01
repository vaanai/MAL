#!/usr/bin/env python3
"""arena_tip_gapfill: repair before exclude, for tip-source arena books (F2; QP-RULING-1101-hv4-reads 4.1 (C) and L4-nd-audit F2). PAPER only: it holds no wallet key, sends nothing.

WHAT IT DOES. Each tip gap range [from_slot, to_slot] whose missed_slots is at or under a PINNED bound is fetched slot by slot with getBlock and decoded by the REAL
tools.fast_tip_follower.TipFollower (the pinned walker decoder blobs: observe/trade_decode.py, observe/trade_store.py, tools/pump_history_backfill.py, in event-V mode),
so a repair row is the row the follower would have written for that slot. Rows go to a SEALED FILL SEGMENT (a new directory, write-once, read-only afterwards) with a
manifest: the decoder blobs, the bound, per source gap the slots fetched, the slots listed skipped, the slots that failed, the sha256 of every file. A range is FILLED
only if every slot in it was fetched or is listed skipped (completeness); any other range stays a gap (`filled: false`, with a code) and the engine still excludes by it.

OUTCOME-BLIND. The ranges come from gaps.jsonl (or the adapter's gap rows); nothing is priced, no tape row is read, and no row content decides whether a range is fetched.
The rule is the same for every gap: fillable reason, a slot range, a follower time, missed_slots <= bound. Live decisions are never regenerated: this tool writes tape rows only.

FAIL CLOSED. The decoder blobs this process imported must equal fast_tip_follower.PINNED_DECODER_BLOBS (the EXP027_DECODER_REF blobs) before the first fetch and again
when a segment is loaded for use; a mismatch raises PinMismatch and nothing is fetched or used. A segment that exists is never overwritten; a manifest whose file digests or
pins do not match is refused.

REPAIR ROWS. t_recv_ms of every repair row of a gap is that gap's own follower time (`t_recv_ms` of the gaps.jsonl row, `to_ms` of the adapter's gap row): deterministic, so
a fill is reproducible, and it keeps the stream non-decreasing (the follower noticed the gap at that instant, so every later follower row is at or after it). Every row has
tx_index and block_time unless the decoder itself left tx_index off (a PumpSwap print that came back through resolve_unresolved; the adapter and the engine handle those).
The singular `virtual_quote_reserve` is NOT stamped (a later account read is not the V of that slot; the adapter drops that key anyway); the plural event V comes from the block's logs.

SPLICING. `splice()` replaces an adapter gap row that matches a FILLED range by (a) the repair rows as arena rows (the adapter's converters, flagged `fill: true`, `src: "tip_fill"`) and
(b) one marker gap row with `filled: true`, `data_loss: false` (the engine records a fill, not a DEC-031 1.4 gap), and drops later follower rows that repeat a repair row
(dedupe key: type, slot, signature, event_index). A gap that is not filled passes through unchanged.

NO NETWORK IN TESTS. The fetcher is injected. The CLI is the only code that opens an RPC; it never prints the URL or the key.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from tools import arena_tip_adapter as ata
from tools import fast_tip_follower as ftf

SCHEMA = "arena_tip_gapfill_v1"
MAX_MISSED_SLOTS = 2_000  # about 13 minutes of chain; at ~1 to 2.6 credits a slot that is at most ~5k credits per range. A range above the bound stays a gap.
MAX_TOTAL_SLOTS = 20_000  # per run: a runaway guard, not a rule (a range list above it is refused as a whole)
# reasons whose loss is a slot range the chain still holds. Everything else (tip_bad_line, hour_file_missing, bad_row_*, mixed_tx_index, writer status/reader problems) stays a gap.
FILLABLE_REASONS = frozenset({"backlog_jump", "unfetchable", "skip_contradicted", "skip_contradicted_refetch", "unresolved_dropped", "writer_hole"})
ROWS_DIR, MANIFEST = "rows", "manifest.json"
KIND_ORDER = {"migration": 0, "create": 1, "trade": 2}  # the reader's order inside one slot (arena_tip_adapter: migrations, creates, trades)
FEED_KINDS = ("trades", "creates", "migrations")
FILE_RE = re.compile(r"^(trades|creates|migrations|skipped-slots)-\d{4}-\d{2}-\d{2}T\d{2}\.jsonl$")


class PinMismatch(Exception):
    """The decoder this process imported is not the pinned one, or a segment was made by another decoder. Nothing is fetched or used."""


class SegmentRefused(Exception):
    """A segment that exists, is incomplete, or fails its digests."""


# ------------------------------------------------------------------------------------------------------------------------------------ pins
def check_pins(blobs_fn: Callable[[], Mapping[str, Any]] = ftf.decoder_blobs, pinned: Mapping[str, str] = ftf.PINNED_DECODER_BLOBS) -> dict[str, str | None]:
    blobs = dict(blobs_fn())
    if blobs != dict(pinned):
        bad = sorted(k for k in set(blobs) | set(pinned) if blobs.get(k) != pinned.get(k))
        raise PinMismatch("decoder blobs are not the pinned EXP027_DECODER_REF blobs: " + ", ".join(bad))
    return blobs


# ------------------------------------------------------------------------------------------------------------------------------------ the plan
@dataclass(frozen=True)
class GapRange:
    gid: str
    lo: int | None
    hi: int | None
    reason: str
    t_recv_ms: int | None
    missed: int | None

    @property
    def slots(self) -> int:
        return self.hi - self.lo + 1 if self.lo is not None and self.hi is not None else 0


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def parse_gap(obj: Mapping[str, Any], pos: int = 0) -> GapRange:
    """A gaps.jsonl row or an adapter gap row. Never raises: a row that is not a slot range with a follower time comes back with lo/hi/t None (the plan refuses it)."""
    raw = obj.get("reason") if isinstance(obj.get("reason"), str) else (obj.get("kind") if isinstance(obj.get("kind"), str) else "unknown")
    reason = re.sub(r"[^a-z0-9_]", "_", raw.lower().removeprefix("tip_gap_"))
    fs, ts, s = obj.get("from_slot"), obj.get("to_slot"), obj.get("slot")
    t = obj.get("to_ms") if _is_int(obj.get("to_ms")) else (obj.get("t_recv_ms") if _is_int(obj.get("t_recv_ms")) else None)
    if _is_int(fs) and _is_int(ts) and ts >= fs:
        lo, hi = fs, ts
    elif _is_int(s) and not (obj.get("from_slot") is not None or obj.get("to_slot") is not None):
        lo = hi = s
    else:
        lo = hi = None
    missed = obj.get("missed_slots") if _is_int(obj.get("missed_slots")) else (obj.get("slots") if _is_int(obj.get("slots")) else None)
    return GapRange(f"{reason}:{lo}-{hi}" if lo is not None else f"{reason}:row{pos}", lo, hi, reason, t, missed)


@dataclass
class Plan:
    fill: list[GapRange] = field(default_factory=list)  # in slot order
    refused: list[tuple[GapRange, str]] = field(default_factory=list)  # (range, code): stays a gap
    slots: list[int] = field(default_factory=list)  # the union of slots to fetch, ascending

    def total_slots(self) -> int:
        return len(self.slots)


def make_plan(gaps: Iterable[Mapping[str, Any]], *, max_missed: int = MAX_MISSED_SLOTS, max_total: int = MAX_TOTAL_SLOTS) -> Plan:
    """The fill rule, the same for every gap: fillable reason, slot range, follower time, and missed_slots (the row's, else hi - lo + 1) at or under `max_missed`."""
    plan, seen, slots = Plan(), set(), set()
    for i, obj in enumerate(gaps):
        g = parse_gap(obj, i)
        if g.gid in seen:
            continue  # the same range listed twice (an adapter row and its gaps.jsonl row) is one range
        seen.add(g.gid)
        if g.lo is None:
            plan.refused.append((g, "no_slot_range"))
        elif g.reason not in FILLABLE_REASONS:
            plan.refused.append((g, "reason_not_fillable"))
        elif g.t_recv_ms is None:
            plan.refused.append((g, "no_follower_time"))
        elif max(g.missed or 0, g.slots) > max_missed:
            plan.refused.append((g, "over_bound"))
        else:
            plan.fill.append(g)
            slots.update(range(g.lo, g.hi + 1))
    plan.fill.sort(key=lambda g: (g.lo, g.hi, g.gid))
    plan.slots = sorted(slots)
    if len(plan.slots) > max_total:
        raise ValueError(f"{len(plan.slots)} slots to fetch exceeds the per-run cap {max_total}; split the range list")
    return plan


# ------------------------------------------------------------------------------------------------------------------------------------ the fill
def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_jsonl(p: Path) -> list[dict[str, Any]]:
    out = []
    for line in p.read_text().splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _lock_down(root: Path) -> None:
    for p in sorted(root.rglob("*"), reverse=True):
        os.chmod(p, stat.S_IRUSR | stat.S_IRGRP | (stat.S_IXUSR | stat.S_IXGRP if p.is_dir() else 0))
    os.chmod(root, stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP)


def fill(gaps: Iterable[Mapping[str, Any]], fetch: ftf.Fetcher, segment_dir: str | os.PathLike, *, max_missed: int = MAX_MISSED_SLOTS, max_total: int = MAX_TOTAL_SLOTS,
         lookup: Callable[[list[str]], dict[str, tuple[str, str]]] | None = None, blobs_fn: Callable[[], Mapping[str, Any]] = ftf.decoder_blobs,
         pinned: Mapping[str, str] = ftf.PINNED_DECODER_BLOBS, sleep: Callable[[float], None] = lambda s: None, credits: Callable[[], int] = lambda: 0) -> dict[str, Any]:
    """Fetch and decode every fillable range into a NEW sealed segment; return the manifest (also written to <segment>/manifest.json). Raises PinMismatch before any fetch,
    SegmentRefused if the segment directory exists."""
    blobs = check_pins(blobs_fn, pinned)
    seg = Path(segment_dir)
    if seg.exists():
        raise SegmentRefused(f"segment {seg.name} exists: a sealed segment is never overwritten")
    plan = make_plan(gaps, max_missed=max_missed, max_total=max_total)
    rows_dir, work = seg / ROWS_DIR, seg / "_work"
    t_now = {"t": 0}
    f = ftf.TipFollower(out_dir=rows_dir, state_dir=work, fetch=fetch, get_tip=lambda: 0, lookup=lookup, v_lookup=lambda pools: {}, credits=credits, clock_ms=lambda: t_now["t"],
                        sleep=sleep, max_retries=3, backoff_s=0.0, backoff_cap_s=0.0, breaker_pause_s=0.0, trade_event_v=True, log=lambda m: None)
    outcome: dict[int, str] = {}
    for g in plan.fill:  # slot order; each range stamped with its own follower time, kept non-decreasing by the follower's max()
        t_now["t"] = int(g.t_recv_ms)  # type: ignore[arg-type]
        for s in range(g.lo, g.hi + 1):  # type: ignore[arg-type]
            if s in outcome:
                continue
            b0, k0 = f.blocks, f.skipped
            f.process_slot(s)
            outcome[s] = "ok" if f.blocks > b0 else "skipped" if f.skipped > k0 else "failed"
    # the follower's own gaps.jsonl in the segment: a slot it could not decode in full is NOT filled; a skip it contradicted and then refetched is ok
    bad: set[int] = set()
    gp = rows_dir / "gaps.jsonl"
    if gp.exists():
        for r in _read_jsonl(gp):
            if _is_int(r.get("slot")):
                if r.get("reason") == "skip_contradicted":
                    outcome[r["slot"]] = "ok" if outcome.get(r["slot"]) != "failed" else "failed"
                else:
                    bad.add(r["slot"])
    for s in bad:
        outcome[s] = "failed"
    ranges = []
    for g in plan.fill:
        oks = [s for s in range(g.lo, g.hi + 1) if outcome.get(s) == "ok"]  # type: ignore[arg-type]
        sks = [s for s in range(g.lo, g.hi + 1) if outcome.get(s) == "skipped"]  # type: ignore[arg-type]
        bd = [s for s in range(g.lo, g.hi + 1) if outcome.get(s) not in ("ok", "skipped")]  # type: ignore[arg-type]
        ranges.append({"gid": g.gid, "reason": g.reason, "from_slot": g.lo, "to_slot": g.hi, "t_recv_ms": g.t_recv_ms, "slots": g.slots, "fetched": len(oks),
                       "skipped_slots": sks, "failed_slots": bd, "filled": not bd and len(oks) + len(sks) == g.slots})
    for g, code in plan.refused:
        ranges.append({"gid": g.gid, "reason": g.reason, "from_slot": g.lo, "to_slot": g.hi, "t_recv_ms": g.t_recv_ms, "slots": g.slots, "fetched": 0, "skipped_slots": [],
                       "failed_slots": [], "filled": False, "refused": code})
    shutil.rmtree(work, ignore_errors=True)
    rows_dir.mkdir(parents=True, exist_ok=True)
    files = {p.name: _sha256(p) for p in sorted(rows_dir.iterdir()) if p.is_file() and p.stat().st_size > 0}
    for p in sorted(rows_dir.iterdir()):
        if p.is_file() and p.stat().st_size == 0:
            p.unlink()  # the follower pre-creates next-hour files; an empty file is not part of a segment
    man = {"schema": SCHEMA, "decoder_blobs": blobs, "max_missed_slots": max_missed, "slots_fetched": len(outcome), "outcomes": dict(Counter(outcome.values())),
           "ranges": ranges, "files": files, "complete": all(r["filled"] for r in ranges if not r.get("refused")), "credits": int(credits())}
    (seg / MANIFEST).write_text(json.dumps(man, sort_keys=True, indent=1) + "\n")
    _lock_down(seg)
    return man


def completeness(man: Mapping[str, Any], gaps: Iterable[Mapping[str, Any]], *, max_missed: int | None = None) -> list[str]:
    """Problems found when the manifest is checked against the gap list it was made for ([] = complete): every fillable range present, every slot of every filled range
    fetched or listed skipped, no range filled with a failed slot, every refusal carrying its code. Pure: reads only the two arguments."""
    bound = man.get("max_missed_slots") if max_missed is None else max_missed
    plan = make_plan(gaps, max_missed=int(bound), max_total=10**9)
    by_gid = {r["gid"]: r for r in man.get("ranges", [])}
    probs = []
    for g in plan.fill:
        r = by_gid.get(g.gid)
        if r is None:
            probs.append(f"{g.gid}: fillable but not in the manifest")
            continue
        got = r["fetched"] + len(r["skipped_slots"]) + len(r["failed_slots"])
        if got != g.slots:
            probs.append(f"{g.gid}: {got} of {g.slots} slots accounted for")
        if r["filled"] and r["failed_slots"]:
            probs.append(f"{g.gid}: filled with failed slots")
        if r["filled"] and r["fetched"] + len(r["skipped_slots"]) != g.slots:
            probs.append(f"{g.gid}: filled but not every slot fetched or skipped")
    for g, code in plan.refused:
        r = by_gid.get(g.gid)
        if r is None or r.get("filled") or r.get("refused") != code:
            probs.append(f"{g.gid}: refusal {code} not recorded as a gap")
    return probs


# ------------------------------------------------------------------------------------------------------------------------------------ using a segment
@dataclass
class Segment:
    manifest: dict[str, Any]
    walker_rows: list[tuple[str, dict[str, Any]]]  # (reader item kind, walker row), in the reader's slot order

    def filled(self) -> dict[tuple[str, int, int], dict[str, Any]]:
        return {(r["reason"], r["from_slot"], r["to_slot"]): r for r in self.manifest["ranges"] if r["filled"]}


def load_segment(segment_dir: str | os.PathLike, *, blobs_fn: Callable[[], Mapping[str, Any]] = ftf.decoder_blobs, pinned: Mapping[str, str] = ftf.PINNED_DECODER_BLOBS) -> Segment:
    """Verify (pins, schema, the file set, every sha256) and load a sealed segment. Raises PinMismatch or SegmentRefused. Reads only inside the segment."""
    check_pins(blobs_fn, pinned)
    seg = Path(segment_dir)
    try:
        man = json.loads((seg / MANIFEST).read_text())
    except (OSError, ValueError) as exc:
        raise SegmentRefused(f"manifest unreadable: {type(exc).__name__}") from None
    if man.get("schema") != SCHEMA:
        raise SegmentRefused("manifest schema")
    if man.get("decoder_blobs") != dict(pinned):
        raise PinMismatch("the segment was made by a decoder that is not the pinned one")
    have = {p.name for p in (seg / ROWS_DIR).iterdir() if p.is_file() and p.stat().st_size > 0} if (seg / ROWS_DIR).is_dir() else set()
    if have != set(man.get("files", {})):
        raise SegmentRefused("file set differs from the manifest")
    rows: list[tuple[str, dict[str, Any]]] = []
    for name, digest in sorted(man["files"].items()):
        if not FILE_RE.match(name) and name != "gaps.jsonl":
            raise SegmentRefused(f"unexpected file {name}")
        if _sha256(seg / ROWS_DIR / name) != digest:
            raise SegmentRefused(f"digest mismatch: {name}")
        kind = {"trades": "trade", "creates": "create", "migrations": "migration"}.get(name.split("-")[0])
        if kind:
            rows.extend((kind, r) for r in _read_jsonl(seg / ROWS_DIR / name))
    rows.sort(key=lambda kr: (kr[1].get("slot", 0), KIND_ORDER[kr[0]], kr[1].get("tx_index") if _is_int(kr[1].get("tx_index")) else 1 << 40, kr[1].get("event_index") or 0,
                              str(kr[1].get("signature"))))
    return Segment(man, rows)


def row_key(row: Mapping[str, Any]) -> tuple:
    return (row.get("type"), row.get("slot"), row.get("signature"), row.get("event_index"))


def dedupe(fill_rows: Sequence[Mapping[str, Any]], follower_rows: Iterable[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], int]:
    """Repair rows that a follower row already carries are dropped (the follower row stands). Returns (kept repair rows, dropped count)."""
    have = {row_key(r) for r in follower_rows}
    kept = [r for r in fill_rows if row_key(r) not in have]
    return kept, len(fill_rows) - len(kept)


def arena_rows(seg: Segment, lo: int, hi: int) -> list[dict[str, Any]]:
    """The repair rows of slots [lo, hi] as arena rows (the adapter's converters and its bad-row handling), flagged `fill`. A bad row becomes the adapter's own data_loss gap."""
    ad = ata.ArenaTipAdapter()
    items = [(k, r) for k, r in seg.walker_rows if lo <= r.get("slot", -1) <= hi]
    out = ad.feed(items, released_D=hi)
    for r in out:
        if r["type"] != "gap":
            r["fill"], r["src"] = True, "tip_fill"
    return out


def marker_row(rng: Mapping[str, Any], gap: Mapping[str, Any]) -> dict[str, Any]:
    """The row that tells the engine the range is filled (not a DEC-031 1.4 gap): the source gap row's placement, data_loss false, filled true."""
    row = dict(gap)
    row.update({"kind": str(gap.get("kind")) + "_filled", "data_loss": False, "filled": True, "fill_slots": rng["slots"], "fill_skipped": len(rng["skipped_slots"]),
                "fill_fetched": rng["fetched"]})
    return row


def splice(rows: Iterable[Mapping[str, Any]], seg: Segment, counts: Counter | None = None) -> Iterator[dict[str, Any]]:
    """The adapter's arena rows with every FILLED gap replaced by its repair rows plus the filled marker, and later follower rows that repeat a repair row dropped.
    Gaps that are not filled (or not in the segment) pass through unchanged; order is the adapter's."""
    c = counts if counts is not None else Counter()
    filled = seg.filled()
    spans: list[tuple[int, int, set]] = []
    for row in rows:
        if row.get("type") == "gap" and row.get("data_loss") and row.get("src") == "tip":
            rng = filled.get((str(row.get("reason")), row.get("from_slot"), row.get("to_slot")))
            if rng is not None:
                fr = arena_rows(seg, rng["from_slot"], rng["to_slot"])
                keys = {row_key(r) for r in fr if r["type"] != "gap"}
                spans.append((rng["from_slot"], rng["to_slot"], keys))
                for r in fr:
                    r["t_recv_ms"] = max(int(r["t_recv_ms"]), int(rng["t_recv_ms"])) if r["type"] == "gap" else r["t_recv_ms"]
                    c["fill_rows" if r["type"] != "gap" else "fill_bad_row_gaps"] += 1
                    yield r
                c["filled_ranges"] += 1
                c["filled_slots"] += rng["slots"]
                yield marker_row(rng, row)
                continue
            c["gaps_not_filled"] += 1
        elif row.get("type") != "gap" and isinstance(row.get("slot"), int) and any(a <= row["slot"] <= b and row_key(row) in ks for a, b, ks in spans):
            c["follower_dup_dropped"] += 1
            continue
        yield dict(row)


# ------------------------------------------------------------------------------------------------------------------------------------ CLI (the only code that opens an RPC)
def read_env_file(path: str | os.PathLike) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip().removeprefix("export ").strip()] = v.strip().strip("'\"")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--gaps", required=True, help="JSONL of gap rows (gaps.jsonl form or the adapter's gap rows)")
    ap.add_argument("--segment-dir", required=True, help="a NEW directory (write-once)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--helius-env", help="path of the env file that holds HELIUS_API_KEY (read, never printed)")
    src.add_argument("--public-rpc", action="store_true", help="the public RPC (slow, rate limited)")
    ap.add_argument("--max-missed-slots", type=int, default=MAX_MISSED_SLOTS)
    ap.add_argument("--rps", type=float, default=5.0)
    ap.add_argument("--check-pins", action="store_true", help="print the decoder blobs and exit 0 only if pinned (no RPC)")
    a = ap.parse_args(argv)
    try:
        blobs = check_pins()
    except PinMismatch as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    if a.check_pins:
        print(json.dumps({"decoder_blobs": blobs, "pinned": True}, sort_keys=True))
        return 0
    gaps = [json.loads(line) for line in Path(a.gaps).read_text().splitlines() if line.strip()]
    environ = read_env_file(a.helius_env) if a.helius_env else {}
    url, kind = ftf.resolve_rpc_url(None, environ)
    fetch, _tip, credits, lookup = ftf.make_rpc(url, min(max(a.rps, 0.5), 20.0))
    man = fill(gaps, fetch, a.segment_dir, max_missed=a.max_missed_slots, lookup=lookup, credits=credits)
    print(json.dumps({"rpc_kind": kind, "complete": man["complete"], "slots_fetched": man["slots_fetched"], "outcomes": man["outcomes"], "credits": man["credits"],
                      "ranges": len(man["ranges"]), "filled": sum(r["filled"] for r in man["ranges"])}, sort_keys=True))
    return 0 if man["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
