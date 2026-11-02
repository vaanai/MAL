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
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from tools import arena_tip_adapter as ata
from tools import arena_tip_reader as tr
from tools import fast_tip_follower as ftf
from tools.pump_history_backfill import rows_from_block

SCHEMA = "arena_tip_gapfill_v1"
MAX_MISSED_SLOTS = 2_000  # about 13 minutes of chain; at ~1 to 2.6 credits a slot that is at most ~5k credits per range. A range above the bound stays a gap.
MAX_TOTAL_SLOTS = 20_000  # per run: a runaway guard, not a rule (a range list above it is refused as a whole)
# reasons whose loss is a slot range the chain still holds. Everything else (tip_bad_line, hour_file_missing, bad_row_*, mixed_tx_index, writer status/reader problems) stays a gap.
FILLABLE_REASONS = frozenset({"backlog_jump", "unfetchable", "skip_contradicted", "skip_contradicted_refetch", "unresolved_dropped", "writer_hole"})
BACKLOG_SLOTS = 50  # the follower's registered --backlog-slots (fast_tip_follower default): a backlog_jump's pre-gap rows arrive up to this many slots late
ROWS_DIR, MANIFEST = "rows", "manifest.json"
LOOKAHEAD_MAX = 100_000  # splice: rows read ahead of a fillable gap row, looking for the first row of slot > to_slot
LOOKBEHIND_SLOTS = 256  # splice: how many slots of already-yielded follower rows it remembers
REFUSED_PATH_PARTS = ("/var/lib/mal/sealed",)  # the CLI never reads or writes under the tip dir, the archive or the writer's derived copy (SEALS-BRIEF 2026-11-01)
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


def warm_pool_cache(f: ftf.TipFollower, block: dict[str, Any], slot: int) -> int:
    """Make the follower's pool cache what a warm, long-running follower would hold for this block: look up the pools the block's PumpSwap prints name that the cache does
    not know (the same `lookup` resolve_unresolved would call), BEFORE the commit decodes the block. With the pool in the cache `rows_from_block` resolves the print inline
    and numbers it (tx_index); cold, the print would come back through resolve_unresolved without tx_index (QP on #1182, C3). Returns the number of pools looked up."""
    block["slot"] = slot
    scratch = dict(f.pool_cache)  # the throwaway decode mutates its cache with CreatePool events; the real one happens in the commit
    pending = rows_from_block(block, scratch, ftf.FEED).get("unresolved") or []
    need = sorted({r["pool"] for r in pending if isinstance(r.get("pool"), str) and r["pool"] not in f.pool_cache})
    for off in range(0, len(need), 100):
        f.pool_cache.update(f._safe_lookup(need[off : off + 100]))
    return len(need)


def fill(gaps: Iterable[Mapping[str, Any]], fetch: ftf.Fetcher, segment_dir: str | os.PathLike, *, max_missed: int = MAX_MISSED_SLOTS, max_total: int = MAX_TOTAL_SLOTS,
         lookup: Callable[[list[str]], dict[str, tuple[str, str]]] | None = None, blobs_fn: Callable[[], Mapping[str, Any]] = ftf.decoder_blobs,
         pinned: Mapping[str, str] = ftf.PINNED_DECODER_BLOBS, sleep: Callable[[float], None] = lambda s: None, credits: Callable[[], int] = lambda: 0,
         backlog_slots: int = BACKLOG_SLOTS) -> dict[str, Any]:
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
    prev = None
    for g in plan.fill:  # slot order; each range stamped with its own follower time, kept non-decreasing by the follower's max()
        t_now["t"] = int(g.t_recv_ms)  # type: ignore[arg-type]
        for s in range(g.lo, g.hi + 1):  # type: ignore[arg-type]
            if s in outcome:
                continue
            if prev is None or s != prev + 1:
                f._pending_skips.clear()  # skips pending from a range that is not contiguous with this one are not contradicted by this block's parentSlot (QP/reviewer #1182)
            prev = s
            b0, k0 = f.blocks, f.skipped
            block, out = f._fetch_with_retry(s)  # the follower's own fetch / retry / skip handling, then the same commit as process_slot
            if out == "ok" and block is not None:
                warm_pool_cache(f, block, s)
            f._commit(s, block, out)
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
                       "back_slots": backlog_slots if g.reason == "backlog_jump" else 0,
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
    no_tx = sum(1 for p in sorted(rows_dir.glob("trades-*.jsonl")) for r in _read_jsonl(p) if r.get("tx_index") is None)
    man = {"schema": SCHEMA, "decoder_blobs": blobs, "max_missed_slots": max_missed, "backlog_slots": backlog_slots, "rows_no_tx_index": no_tx, "slots_fetched": len(outcome), "outcomes": dict(Counter(outcome.values())),
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
    """The row that tells the engine the range is filled (not a DEC-031 1.4 gap): the source gap row's placement, data_loss false, filled true. `back_slots` is the margin
    before from_slot inside which an entry or exit leg still keeps the 1.4 exclusion (the follower's backlog slots for a backlog_jump, else 0)."""
    row = dict(gap)
    row.update({"kind": str(gap.get("kind")) + "_filled", "data_loss": False, "filled": True, "fill_slots": rng["slots"], "fill_skipped": len(rng["skipped_slots"]),
                "fill_fetched": rng["fetched"], "back_slots": int(rng.get("back_slots", 0))})
    return row


_DATA_TYPES = ("trade", "create", "complete", "pool_create")


def _form(row: Mapping[str, Any]) -> tuple | None:
    """((pool, slot), has tx_index) of a PumpSwap trade row: the key of the mixed-form check (the adapter's: per pool and slot). None for any other row."""
    if row.get("type") == "trade" and row.get("prog") == "pumpswap" and row.get("pool") and isinstance(row.get("slot"), int):
        return (row["pool"], row["slot"]), row.get("tx_index") is not None
    return None


def mixed_groups(rows: Iterable[Mapping[str, Any]]) -> int:
    """Number of (pool, slot) groups that hold both a numbered (tx_index) and an unnumbered PumpSwap print: their within-slot order is two forms in one pool."""
    seen: dict[tuple, set] = {}
    for r in rows:
        f = _form(r)
        if f is not None:
            seen.setdefault(f[0], set()).add(f[1])
    return sum(len(v) == 2 for v in seen.values())


def _is_fillable_gap(row: Mapping[str, Any]) -> bool:
    return row.get("type") == "gap" and bool(row.get("data_loss")) and row.get("src") == "tip"


def splice(rows: Iterable[Mapping[str, Any]], seg: Segment, counts: Counter | None = None, *, lookahead_max: int = LOOKAHEAD_MAX) -> Iterator[dict[str, Any]]:
    """The adapter's arena rows with every FILLED gap replaced by the filled MARKER and then the repair rows (marker first: an exit priced while the repair rows stream in
    must already know the range).

    Dedupe (QP C2): a repair row is dropped if the follower already carries a row with the same (type, slot, signature, event_index) in slots [from_slot, to_slot], on EITHER
    side of the gap row (the rows already passed, remembered for LOOKBEHIND_SLOTS slots, and the rows after it up to the first row of slot > to_slot); the follower row stands
    and tx_index plays no part in the key. If the follower rows and the kept repair rows would put a numbered and an unnumbered PumpSwap print in one (pool, slot), or a repair
    row is a bad row, the range is NOT filled: the original data_loss gap row passes unchanged (fail closed). A gap that is not filled (or not in the segment) passes through
    unchanged; order is the adapter's."""
    c = counts if counts is not None else Counter()
    filled = seg.filled()
    it, queue = iter(rows), deque()
    recent: dict[int, list[dict[str, Any]]] = {}
    end = object()

    def nxt() -> Any:
        return queue.popleft() if queue else next(it, end)

    while True:
        row = nxt()
        if row is end:
            return
        rng = filled.get((str(row.get("reason")), row.get("from_slot"), row.get("to_slot"))) if _is_fillable_gap(row) else None
        if _is_fillable_gap(row) and rng is None:
            c["gaps_not_filled"] += 1
        if rng is not None:
            lo, hi = rng["from_slot"], rng["to_slot"]
            ahead: list[Any] = []
            while len(ahead) < lookahead_max:
                r = nxt()
                if r is end:
                    break
                ahead.append(r)
                if isinstance(r.get("slot"), int) and r["slot"] > hi:
                    break
            queue.extendleft(reversed(ahead))  # read ahead, not consumed: the main loop still sees every one of them
            follower = [r for sl in range(lo, hi + 1) for r in recent.get(sl, ())] + [r for r in ahead if r.get("type") in _DATA_TYPES and isinstance(r.get("slot"), int) and lo <= r["slot"] <= hi]
            repair = arena_rows(seg, lo, hi)
            bad = [r for r in repair if r["type"] == "gap"]
            kept, dropped = dedupe([r for r in repair if r["type"] != "gap"], follower)
            if bad or mixed_groups([*follower, *kept]):
                c["fill_refused_bad_row" if bad else "fill_refused_mixed_form"] += 1
                c["gaps_not_filled"] += 1
                yield dict(row)
                continue
            c["filled_ranges"] += 1
            c["filled_slots"] += rng["slots"]
            c["fill_rows"] += len(kept)
            c["fill_rows_dup_dropped"] += dropped
            yield marker_row(rng, row)
            yield from kept
            continue
        if row.get("type") in _DATA_TYPES and isinstance(row.get("slot"), int):
            recent.setdefault(row["slot"], []).append(row)
            for old in [k for k in recent if k < row["slot"] - LOOKBEHIND_SLOTS]:
                del recent[old]
        yield dict(row)


# ------------------------------------------------------------------------------------------------------------------------------------ the post-fill gap record (hour-health, the cap)
def post_fill_gap_record(gaps: Iterable[Mapping[str, Any]], man: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One entry per distinct gap range in `gaps`, with whether the manifest filled it: what an hour-health tool reads for filled ranges. Counts and slot numbers only (no
    row content). `hour` is the UTC hour of the gap's follower time. An entry the manifest does not know is unfilled."""
    by_gid = {r["gid"]: r for r in man.get("ranges", [])}
    out, seen = [], set()
    for i, obj in enumerate(gaps):
        g = parse_gap(obj, i)
        if g.gid in seen:
            continue
        seen.add(g.gid)
        r = by_gid.get(g.gid)
        out.append({"gid": g.gid, "reason": g.reason, "from_slot": g.lo, "to_slot": g.hi, "slots": g.slots, "missed_slots": g.missed, "t_recv_ms": g.t_recv_ms,
                    "hour": ftf.hour_of_ms(g.t_recv_ms) if g.t_recv_ms is not None else None, "filled": bool(r and r.get("filled")),
                    "refused": (r or {}).get("refused"), "failed_slots": len((r or {}).get("failed_slots", []))})
    return out


def hour_fill_counts(record: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    """{UTC hour: {filled_ranges, unfilled_ranges, filled_slots, unfilled_slots}} from post_fill_gap_record. Both counts are always reported (Amendment item 5)."""
    out: dict[str, dict[str, int]] = {}
    for e in record:
        h = out.setdefault(str(e["hour"]), {"filled_ranges": 0, "unfilled_ranges": 0, "filled_slots": 0, "unfilled_slots": 0})
        k = "filled" if e["filled"] else "unfilled"
        h[f"{k}_ranges"] += 1
        h[f"{k}_slots"] += int(e.get("missed_slots") or e.get("slots") or 0)
    return out


def mark_through_ids(rows: Iterable[Mapping[str, Any]], stake: float) -> set[str]:
    """Position ids of PRICED trade rows at `stake` that the engine marked through a filled range. They stay priced and kept, and they count in the section 1.4 5% numerator
    (QP C4): numerator = the scorer's s14 exclusions plus these."""
    return {r["position_id"] for r in rows if r.get("kind") == "trade" and r.get("stake_sol") == stake and r.get("mark_through")}


def cap_counts(rows: Sequence[Mapping[str, Any]], stake: float) -> dict[str, Any]:
    """The section 1.4 numerator for a tip-source book at `stake`: the cut tool's s14 reasons (feed_gap exclusions, missing V, late-print gap, data refusals) plus the
    marked-through trades (priced). Names and counts only. The pinned scorer must apply the same rule (owed with the registration: it is a new scorer pin)."""
    from tools import arena_am6_cut as cut

    s14 = cut.s14_reasons(rows, stake)
    mt = mark_through_ids(rows, stake) - set(s14)
    pids = {r["position_id"] for r in rows if r.get("kind") in ("trade", "excluded") and r.get("stake_sol") == stake}
    return {"n_positions": len(pids), "n_s14": len(s14), "n_mark_through": len(mt), "numerator": len(s14) + len(mt),
            "share": (len(s14) + len(mt)) / len(pids) if pids else None, "s14_reasons": dict(Counter(s14.values()))}


# ------------------------------------------------------------------------------------------------------------------------------------ CLI (the only code that opens an RPC)
def read_env_file(path: str | os.PathLike) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip().removeprefix("export ").strip()] = v.strip().strip("'\"")
    return out


def refused_path(path: str | os.PathLike) -> str | None:
    """Why the CLI will not read or write `path` (None = allowed): under the sealed tip tree, the archive, or the writer's derived copy (QP C7). The fill's gap input is the
    writer's counts-log gap entries or the adapter's in-host stream, never the tip dir's gaps.jsonl."""
    from tools import arena_tip_push as push
    from tools import arena_tip_tape_writer as w

    derived = (os.path.realpath(w.DEFAULT_OUT), w.DEFAULT_OUT, os.path.realpath(push.DST_ROOTS[0]), push.DST_ROOTS[0])  # fast-0 writer copy and the research-0 derived copy
    for cand in {os.path.abspath(os.fspath(path)), os.path.realpath(os.fspath(path))}:
        if tr.ARCHIVE_MARK in cand:
            return "is under the tip tape archive"
        for pre in (*REFUSED_PATH_PARTS, *derived):
            if cand == pre or cand.startswith(pre.rstrip("/") + "/"):
                return "is under the sealed tip tree or the writer's derived copy"
    return None


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
    for flag, path in ("--gaps", a.gaps), ("--segment-dir", a.segment_dir):
        why = refused_path(path)
        if why:
            print(f"REFUSED: {flag} {why}", file=sys.stderr)
            return 4
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
