#!/usr/bin/env python3
"""arena_tip_gapfill_proofs: the three md5 proofs owed by DEC-031 Amendment 26 item 11 (P1, P2, P3). PAPER only. Output: md5s, field names and counts only.

HOURS FIRST. Before any row is read into an engine or any slot is fetched, every input is checked against explore-0814 = [2026-08-14T00, 2026-08-28T00) (vendored from
arena_restart_equiv.EXPLORATION["explore-0814"], PR #1183; a test pins the equality once that module is on main): P1 and P3 refuse a row whose block_time (t_recv_ms when block_time is absent)
lies outside the window or that has neither; P2 refuses the walker rows the same way and refuses, before its FIRST fetch, unless [lo - margin, hi + margin] lies inside the slot span of those
allow-listed walker rows. Every directory argument also passes gapfill.refused_path (the sealed tip tree, the archive, the derived copies).

  P1  the engine at OLD (tools/arena_paper.py, main 2fa2e4bc) against the F2 engine (tools/arena_paper_f2.py) on the same rows with no filled marker and a pinned deterministic book, run twice:
      the engine's own gap detection off and at the stack defaults (gap_slots 150, gap_silence_ms 60,000): ledger md5 equal per bot, in order and as a set, both times. The sha256 of both sources is printed.
  P2  gapfill.fill started cold on [lo, hi] against a warm TipFollower run over [lo - margin, hi + margin] and against the walker rows: per-slot row md5 equal over every field except DROP_P2
      (pinned here before any run; a field mapping is never chosen after a mismatch is seen), tx_index presence included. Field-name sets and differing field names are printed on a mismatch.
  P3  ranges deleted from the rows (every requested fillable reason; unresolved_dropped removes ONE pool's prints of a slot and keeps the rest on both sides of the gap row), filled from the
      deleted rows, spliced, and run through the F2 engine with the same book, in four cells: detection off / on x pre-gap rows with a lag rising to B slots before a backlog_jump (applied in both
      streams) off / on. The expected change of every position is COMPUTED from the unbroken run's legs and the plan (Amendment 26 item 6) and the actual classes must equal it exactly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import types
import zlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from tools import arena_paper as base_engine
from tools import arena_paper_f2 as f2_engine
from tools import arena_stack as AS
from tools import arena_tip_adapter as ata
from tools import arena_tip_gapfill as gf
from tools import fast_tip_follower as ftf

SCHEMA = "arena_tip_gapfill_proofs_v2"
EXPLORE_0814 = ("2026-08-14T00", "2026-08-28T00")  # vendored: arena_restart_equiv.EXPLORATION["explore-0814"] (PR #1183)
DROP_P2 = frozenset({"t_recv_ms", "source", "virtual_quote_reserve"})  # PINNED before any run: the fields P2 does not compare
BOOK_ID = "proofbook"
EXIT_REFUSED = 3


class Refused(Exception):
    """An input the proof will not read or fetch (hours outside explore-0814, a sealed path, a window that does not fit). Exit code 3."""


def _hour_s(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp())


WINDOW_S = (_hour_s(EXPLORE_0814[0]), _hour_s(EXPLORE_0814[1]))


def md5(x: Any) -> str:
    return hashlib.md5(json.dumps(x, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def sha256_file(p: str | os.PathLike) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ------------------------------------------------------------------------------------------------------------------------------------ hours and paths
def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def row_ts_s(row: Mapping[str, Any]) -> int | None:
    """block_time (seconds) if the row has one, else t_recv_ms // 1000, else None."""
    if _is_int(row.get("block_time")):
        return row["block_time"]
    if _is_int(row.get("t_recv_ms")):
        return row["t_recv_ms"] // 1000
    return None


def enforce_hours(rows: Iterable[Mapping[str, Any]], what: str = "rows", window: tuple[int, int] = WINDOW_S) -> int:
    """Raise Refused unless EVERY row is inside [window) by row_ts_s; a row with neither time is refused. Returns the row count. Counts only in the message."""
    n = no_time = outside = 0
    for r in rows:
        n += 1
        ts = row_ts_s(r)
        if ts is None:
            no_time += 1
        elif not (window[0] <= ts < window[1]):
            outside += 1
    if no_time or outside:
        raise Refused(f"{what}: {outside} of {n} rows outside explore-0814 and {no_time} with no block_time or t_recv_ms")
    return n


def check_path(path: str | os.PathLike, flag: str) -> None:
    why = gf.refused_path(path)
    if why:
        raise Refused(f"{flag} {why}")


def read_rows(directory: str | os.PathLike, what: str = "rows") -> list[dict[str, Any]]:
    """Every *.jsonl in `directory`, file-name order, line order, after the path check and the hours check."""
    check_path(directory, what)
    out: list[dict[str, Any]] = []
    for p in sorted(Path(directory).glob("*.jsonl")):
        out.extend(json.loads(x) for x in p.read_text().splitlines() if x.strip())
    enforce_hours(out, what)
    return out


# ------------------------------------------------------------------------------------------------------------------------------------ engines
def load_engine_source(src: str, name: str) -> types.ModuleType:
    """An arena_paper module from source text (the OLD engine, from `git show <sha>:tools/arena_paper.py`), under its own module name."""
    mod = types.ModuleType(name)
    mod.__package__, mod.__file__ = "tools", f"<{name}>"
    mod.__source_sha256__ = hashlib.sha256(src.encode()).hexdigest()  # type: ignore[attr-defined]
    sys.modules[name] = mod
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    return mod


def load_engine_at(sha: str, repo: str | os.PathLike) -> types.ModuleType:
    src = subprocess.run(["git", "-C", os.fspath(repo), "show", f"{sha}:tools/arena_paper.py"], capture_output=True, text=True, check=True, timeout=60).stdout
    return load_engine_source(src, f"tools.arena_paper_at_{sha[:8]}")


def head_of(repo: str | os.PathLike) -> str | None:
    try:
        return subprocess.run(["git", "-C", os.fspath(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def make_engine(cls: type, mod: types.ModuleType, root: Path, detect: bool) -> Any:
    """`cls` from `mod` (or from the module `mod` imports its types from). detect=False turns the engine's own silence / slot-jump rules off; True leaves the stack defaults."""
    os.environ[mod.TEST_ENV] = "1"  # a throwaway replay root: the test-only registry overrides are honoured in this process only
    reg = root / "bots.json"
    mod.register_bot(BOOK_ID, "abc1234", 0, reg, now_ms=0, verify_sha=False, deciding_stake=0.25, model_ids=[])
    kw = {} if detect else {"gap_slots": None, "gap_silence_ms": None}
    eng = cls(root / "ledger", reg, mod.StackConfig(feed_delay_slots=2, **kw), now_fn=lambda: 0, code_state_fn=lambda: ("0" * 40, False), sps_fn=lambda: 0.4, min_free_pct=0.0)
    eng.now_fn = lambda: eng.hw_ms or 0
    return eng


def run_book(eng: Any, rows: Iterable[Mapping[str, Any]], *, every: int = 3, hold_slots: int = 40) -> Counter:
    """Feed `rows` to the engine; buy the first print of every pool whose crc32 is 0 mod `every`, sell it `hold_slots` slots later. A function of the rows only."""
    st: Counter = Counter()
    seen: set[str] = set()
    open_: dict[str, tuple[str, int]] = {}
    for row in rows:
        eng.on_row(dict(row))
        if row.get("type", "trade") != "trade":
            continue
        key, slot, t = row.get("pool") or row.get("mint"), row.get("slot"), row.get("t_recv_ms")
        if not key or slot is None or t is None:
            continue
        for k, (pid, s0) in list(open_.items()):
            if slot >= s0 + hold_slots:
                try:
                    eng.sell(BOOK_ID, pid, 1.0, t, slot, "hold")
                    st["sells"] += 1
                except Exception as exc:  # noqa: BLE001 - counted by class name only
                    st[f"sell_refused_{type(exc).__name__}"] += 1
                del open_[k]
        if key not in seen and zlib.crc32(key.encode()) % every == 0:
            try:
                open_[key] = (eng.buy(BOOK_ID, key, 0.25, t, slot, {"proof": 1}), slot)
                seen.add(key)
                st["buys"] += 1
            except Exception as exc:  # noqa: BLE001
                st[f"buy_refused_{type(exc).__name__}"] += 1
    return st


def ledger_rows(eng: Any) -> list[dict[str, Any]]:
    return [r for r in eng.ledger.read_bot(BOOK_ID) if r.get("kind") != "start"]


def canon_line(r: Mapping[str, Any]) -> str:
    return json.dumps({k: v for k, v in r.items() if k != "engine_id"}, sort_keys=True, separators=(",", ":"), default=str)  # engine_id is a random per-process id


def ledger_digest(lines: Sequence[str]) -> dict[str, Any]:
    return {"rows": len(lines), "md5_in_order": hashlib.md5("\n".join(lines).encode()).hexdigest(), "md5_as_set": hashlib.md5("\n".join(sorted(lines)).encode()).hexdigest()}


# ------------------------------------------------------------------------------------------------------------------------------------ P1
def p1(rows: Sequence[Mapping[str, Any]], old: types.ModuleType, root: Path, *, min_rows: int = 1, new_files: Sequence[str] | None = None, **book) -> dict[str, Any]:
    """OLD = `old.ArenaEngine`; NEW = ArenaEngineF2 (tools/arena_paper_f2.py over tools/arena_paper.py)."""
    enforce_hours(rows, "p1 rows")
    res: dict[str, Any] = {"proof": "p1", "schema": SCHEMA, "input_rows": len(rows), "min_rows": min_rows,
                           "old_engine_sha256": getattr(old, "__source_sha256__", None) or sha256_file(old.__file__),
                           "new_engine_sha256": {Path(f).name: sha256_file(f) for f in (new_files or (base_engine.__file__, f2_engine.__file__))}}
    if len(rows) < min_rows:
        return {**res, "pass": False, "refused": f"fewer than the min-rows floor ({len(rows)} < {min_rows})"}
    res["runs"] = {}
    for detect in (False, True):
        cell: dict[str, Any] = {}
        for tag, cls, mod in (("old", old.ArenaEngine, old), ("new", f2_engine.ArenaEngineF2, base_engine)):
            (root / f"{tag}_{int(detect)}").mkdir(parents=True)
            eng = make_engine(cls, mod, root / f"{tag}_{int(detect)}", detect)
            cell[f"book_{tag}"] = dict(run_book(eng, rows, **book))
            cell[tag] = ledger_digest([canon_line(r) for r in ledger_rows(eng)])
            cell[f"gaps_{tag}"] = len(eng.gaps)
        cell["pass"] = cell["old"] == cell["new"] and cell["old"]["rows"] > 0 and cell["book_old"] == cell["book_new"] and cell["gaps_old"] == cell["gaps_new"]
        res["runs"]["detection_on" if detect else "detection_off"] = cell
    res["pass"] = all(c["pass"] for c in res["runs"].values())
    return res


# ------------------------------------------------------------------------------------------------------------------------------------ P2
def row_md5_by_slot(rows: Iterable[Mapping[str, Any]], lo: int, hi: int) -> dict[int, str]:
    by: dict[int, list] = {}
    for r in rows:
        if _is_int(r.get("slot")) and lo <= r["slot"] <= hi:
            by.setdefault(r["slot"], []).append({k: v for k, v in r.items() if k not in DROP_P2})
    return {s: md5(sorted(v, key=lambda r: (r.get("tx_index") if _is_int(r.get("tx_index")) else 1 << 40, r.get("event_index") or 0, str(r.get("signature"))))) for s, v in by.items()}


def field_diagnostics(srcs: Mapping[str, Sequence[Mapping[str, Any]]], lo: int, hi: int) -> dict[str, Any]:
    """Names only: the field-name set of each source, and the names of the fields whose values differ between sources on rows with the same (slot, signature, event_index)."""
    names = {k: sorted({f for r in rows if _is_int(r.get("slot")) and lo <= r["slot"] <= hi for f in r if f not in DROP_P2}) for k, rows in srcs.items()}
    keyed = {k: {(r["slot"], r.get("signature"), r.get("event_index")): r for r in rows if _is_int(r.get("slot")) and lo <= r["slot"] <= hi} for k, rows in srcs.items()}
    differ: set[str] = set()
    for key in set().union(*[set(v) for v in keyed.values()]):
        have = [v[key] for v in keyed.values() if key in v]
        for f in set().union(*[set(r) for r in have]) - DROP_P2:
            if len({json.dumps(r.get(f, "<absent>"), sort_keys=True, default=str) for r in have}) > 1:
                differ.add(f)
    missing = {k: sum(1 for key in set().union(*[set(v) for v in keyed.values()]) if key not in keyed[k]) for k in keyed}
    return {"field_names": names, "differing_fields": sorted(differ), "rows_missing_from_source": missing}


def p2_compare(fill_rows: Sequence[Mapping[str, Any]], warm_rows: Sequence[Mapping[str, Any]], walker_rows: Sequence[Mapping[str, Any]], lo: int, hi: int, *, min_slots: int = 1) -> dict[str, Any]:
    srcs = {"fill": row_md5_by_slot(fill_rows, lo, hi), "warm": row_md5_by_slot(warm_rows, lo, hi), "walker": row_md5_by_slot(walker_rows, lo, hi)}
    slots = sorted(set().union(*[set(v) for v in srcs.values()]))
    bad = [s for s in slots if len({srcs[k].get(s) for k in srcs}) != 1]
    rows = {"fill": fill_rows, "warm": warm_rows, "walker": walker_rows}
    no_tx = {k: sum(1 for r in v if _is_int(r.get("slot")) and lo <= r["slot"] <= hi and r.get("tx_index") is None) for k, v in rows.items()}
    out = {"proof": "p2", "schema": SCHEMA, "lo": lo, "hi": hi, "slots_with_rows": len(slots), "min_slots": min_slots, "rows": {k: len(v) for k, v in rows.items()},
           "slot_digest_of_digests": md5([[s, srcs["fill"].get(s)] for s in slots]), "mismatched_slots": len(bad), "mismatch_digest": md5(bad), "rows_no_tx_index": no_tx,
           "dropped_fields": sorted(DROP_P2), "pass": not bad and len(slots) >= min_slots and len(set(no_tx.values())) == 1}
    if bad or len(set(no_tx.values())) != 1:
        out["diagnostics"] = field_diagnostics(rows, lo, hi)
    return out


def trade_rows_of(directory: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for p in sorted(directory.glob("trades-*.jsonl")) for x in p.read_text().splitlines() if x.strip()]


def p2_run(lo: int, hi: int, margin_slots: int, fetch: ftf.Fetcher, walker_rows: Sequence[Mapping[str, Any]], root: Path, *, lookup=None, min_slots: int = 1,
           sleep: Callable[[float], None] = lambda s: None) -> dict[str, Any]:
    """The warm reference: a TipFollower (event V on, no singular V) over [lo - margin, hi + margin]; the test: gapfill.fill on [lo, hi], cold, same fetcher and lookup. Refuses before
    the first fetch unless the walker rows are inside explore-0814 and their slot span holds [lo - margin, hi + margin]."""
    enforce_hours(walker_rows, "p2 walker rows")
    slots = [r["slot"] for r in walker_rows if _is_int(r.get("slot"))]
    if not slots or lo - margin_slots < min(slots) or hi + margin_slots > max(slots):
        raise Refused(f"p2: [lo - margin, hi + margin] is not inside the slot span of the {len(slots)} allow-listed walker rows")
    planned = 2 * margin_slots + 2 * (hi - lo + 1) + 1
    warm = ftf.TipFollower(out_dir=root / "warm" / "out", state_dir=root / "warm" / "state", fetch=fetch, get_tip=lambda: 0, lookup=lookup, v_lookup=lambda p: {}, clock_ms=lambda: 1,
                           sleep=sleep, log=lambda m: None, trade_event_v=True)
    for s in range(lo - margin_slots, hi + margin_slots + 1):
        warm.process_slot(s)
    man = gf.fill([{"kind": "gap", "reason": "backlog_jump", "from_slot": lo, "to_slot": hi, "slots": hi - lo + 1, "t_recv_ms": 1}], fetch, root / "seg", lookup=lookup, sleep=sleep)
    out = p2_compare(trade_rows_of(root / "seg" / "rows"), trade_rows_of(root / "warm" / "out"), list(walker_rows), lo, hi, min_slots=min_slots)
    out.update({"fill_complete": man["complete"], "planned_getblock_calls": planned, "margin_slots": margin_slots})
    out["pass"] = bool(out["pass"] and man["complete"])
    return out


# ------------------------------------------------------------------------------------------------------------------------------------ P3
def walker_of(r: Mapping[str, Any]) -> dict[str, Any]:
    w = dict(r)
    for k in ("sol_lamports", "token_raw", "quote_reserve", "base_reserve"):
        if isinstance(w.get(k), float):
            w[k] = int(w[k])
    return w


def plan_cuts(rows: Sequence[Mapping[str, Any]], reasons: Sequence[str], *, span: int = 12, lag: bool = False) -> list[dict[str, Any]]:
    """Deterministic cuts: reason i takes the distinct slots from position (i + 1) / (n + 1) of the slot list. unresolved_dropped is one slot with at least 3 pools; a backlog_jump is
    max(span, B) slots long in the lag variant (the follower jumps only when it is more than B behind)."""
    trades = [r for r in rows if r.get("type", "trade") == "trade" and _is_int(r.get("slot"))]
    slots = sorted({r["slot"] for r in trades})
    pools: dict[int, set] = {}
    for r in trades:
        pools.setdefault(r["slot"], set()).add(r.get("pool") or r.get("mint"))
    cuts: list[dict[str, Any]] = []
    for i, reason in enumerate(reasons):
        j = slots[(i + 1) * len(slots) // (len(reasons) + 1)]
        if reason == "unresolved_dropped":
            j2 = next((s for s in slots if s >= j and len(pools[s]) >= 3), None)
            if j2 is None:
                raise Refused("p3: no slot with 3 pools for unresolved_dropped")
            cuts.append({"reason": reason, "lo": j2, "hi": j2, "back": 0, "prog": "pumpswap"})
        else:
            sp = max(span, gf.BACKLOG_SLOTS) if (reason == "backlog_jump" and lag) else span
            cuts.append({"reason": reason, "lo": j, "hi": j + sp - 1, "back": gf.BACKLOG_SLOTS if reason == "backlog_jump" else 0, "prog": "all"})
    cuts.sort(key=lambda c: c["lo"])
    for a, b in zip(cuts, cuts[1:]):
        if b["lo"] - b["back"] <= a["hi"]:
            raise Refused("p3: the planned ranges (with their margins) overlap; use fewer reasons or more rows")
    return cuts


def apply_rising_lag(rows: Sequence[Mapping[str, Any]], cuts: Sequence[Mapping[str, Any]], slot_ms: int = 400) -> list[dict[str, Any]]:
    """For every backlog_jump cut: rows in [lo - B, lo) arrive with a lag rising by one slot per slot (so `t_recv_ms` trails the chain by up to B slots just before `lo`), and the rows of the
    range itself are clamped to not go back in time (the follower stamps a non-decreasing wall clock). The same rows feed both streams."""
    out = [dict(r) for r in rows]
    for c in cuts:
        if c["reason"] != "backlog_jump":
            continue
        lo, hi, b = c["lo"], c["hi"], c["back"]
        last = 0
        for r in out:
            s = r.get("slot")
            if not _is_int(s) or not (lo - b <= s <= hi) or not _is_int(r.get("t_recv_ms")):
                continue
            lag = (s - (lo - b) + 1) * slot_ms if s < lo else 0
            r["t_recv_ms"] = max(r["t_recv_ms"] + lag, last)
            last = r["t_recv_ms"]
    return out


def delete_ranges(rows: Sequence[Mapping[str, Any]], cuts: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], gf.Segment, dict[str, int]]:
    """(the stream with the ranges deleted and the adapter-form gap rows in, the segment that fills them, the expected fill counts). A cut deletes every trade row of [lo, hi]; the
    unresolved_dropped cut deletes ONE pool's prints of its slot and keeps the others on both sides of the gap row (the segment holds the whole slot: the fill refetches it)."""
    cutmap = {c["lo"]: c for c in cuts}
    out: list[dict[str, Any]] = []
    walker: list[tuple[str, dict[str, Any]]] = []
    ranges: list[dict[str, Any]] = []
    expect = {"fill_rows": 0, "twin_rows": 0}
    rows = [dict(r) for r in rows]
    idx = 0
    while idx < len(rows):
        r = rows[idx]
        cut = cutmap.get(r["slot"]) if _is_int(r.get("slot")) else None
        if cut is None:
            out.append(r)
            idx += 1
            continue
        lo, hi, reason = cut["lo"], cut["hi"], cut["reason"]
        inside = [x for x in rows[idx:] if _is_int(x.get("slot")) and lo <= x["slot"] <= hi]
        trades = [x for x in inside if x.get("type", "trade") == "trade"]
        t = inside[0].get("t_recv_ms")
        gap = {"v": 1, "type": "gap", "prog": cut["prog"], "slot": lo, "t_recv_ms": t, "signature": None, "event_index": None, "kind": ata.gap_kind(reason), "data_loss": True, "from_ms": t,
               "to_ms": t, "reason": reason, "from_slot": lo, "to_slot": hi, "missed_slots": hi - lo + 1, "src": "tip"}
        ranges.append({"gid": f"{reason}:{lo}-{hi}", "reason": reason, "from_slot": lo, "to_slot": hi, "t_recv_ms": t, "slots": hi - lo + 1, "fetched": hi - lo + 1, "skipped_slots": [],
                       "failed_slots": [], "filled": True, "back_slots": cut["back"]})
        walker.extend(("trade", walker_of(x)) for x in trades)
        if reason == "unresolved_dropped":
            gone = sorted({x.get("pool") or x.get("mint") for x in trades})[-1]
            remain = [x for x in inside if (x.get("pool") or x.get("mint")) != gone]
            half = max(1, len(remain) // 2)
            out.extend(remain[:half] + [gap] + remain[half:])
            expect["fill_rows"] += len(trades) - len([x for x in remain if x.get("type", "trade") == "trade"])
            expect["twin_rows"] += len([x for x in remain if x.get("type", "trade") == "trade"])
        else:
            out.append(gap)
            expect["fill_rows"] += len(trades)
        idx += len(inside)
    return out, gf.Segment({"ranges": ranges}, walker), expect


def position_infos(eng: Any) -> dict[str, dict[str, Any]]:
    """From the UNBROKEN run's ledger: per position the entry landing slots, the exit pricing slot (None if never sold) and whether it was priced: the legs `_legs_of` uses."""
    opens: dict[str, Any] = {}
    exits: dict[str, Any] = {}
    for r in ledger_rows(eng):
        pid = r.get("position_id")
        if r["kind"] == "open":
            opens[pid] = r
        elif r["kind"] in ("trade", "excluded") and pid not in exits:
            exits[pid] = r
    out = {}
    for pid, o in opens.items():
        x = exits.get(pid)
        d = o["feed_delay_slots"]
        lands = [o["effective_slot"]["entry"] + d + int(AS.k_slots(lat, o["sps_entry"], o["stack"]["k_rounding"])) for lat in base_engine.LATS]
        xs = x["effective_slot"]["exit"] if x is not None else None
        out[pid] = {"prog": o["prog"], "lands": lands, "priced": bool(x and x["kind"] == "trade"),
                    "x_trig": None if xs is None else xs + d + AS.exit_lag_slots(x["stack"]["sps_exit"]) + 1}
    return out


def prog_match(prog: str | None, pos_prog: str) -> bool:
    return prog in (None, pos_prog, "all") or (prog == "pumpfun" and pos_prog == "pump_bonding")


def expected_class(info: Mapping[str, Any], cuts: Sequence[Mapping[str, Any]]) -> str:
    """Amendment 26 item 6: fill_hit when an entry leg lands in [lo - B, hi] or the exit leg in [lo - B, lo); mark_through when the exit leg is in [lo, hi] and nothing hits; else unchanged."""
    if info["x_trig"] is None:
        return "unchanged"  # never sold: nothing is priced, nothing to exclude or mark
    hit = mark = False
    for c in cuts:
        if not prog_match(c["prog"], info["prog"]):
            continue
        lo, hi, b = c["lo"], c["hi"], c["back"]
        if any(lo - b <= x <= hi for x in info["lands"]) or lo - b <= info["x_trig"] < lo:
            hit = True
        elif lo <= info["x_trig"] <= hi:
            mark = True
    return "fill_hit" if hit else "mark_through" if mark else "unchanged"


def actual_class(base_lines: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> str:
    if [canon_line(r) for r in rows] == list(base_lines):
        return "unchanged"
    flagged = any(r["kind"] == "late_print" and r.get("fill_margin") for r in rows)
    gapx = any(r["kind"] == "excluded" and r.get("exclusion_reason") == "feed_gap" for r in rows)
    marked = any(r.get("mark_through") for r in rows)
    if flagged or gapx:
        return "fill_hit"
    return "mark_through" if marked else "unexpected"


def late_print_kinds(eng: Any) -> dict[str, int]:
    c: Counter = Counter()
    for r in ledger_rows(eng):
        if r["kind"] == "late_print":
            c["fill_margin_flag" if r.get("fill_margin") else "gap_overlap_true" if r.get("gap_overlap") else "gap_overlap_false"] += 1
    return {k: c.get(k, 0) for k in ("fill_margin_flag", "gap_overlap_true", "gap_overlap_false")}


def by_position(eng: Any) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for r in ledger_rows(eng):
        out.setdefault(str(r.get("position_id")), []).append(r)
    return out


def p3_cell(rows: Sequence[Mapping[str, Any]], root: Path, reasons: Sequence[str], detect: bool, lag: bool, **book) -> dict[str, Any]:
    cuts = plan_cuts(rows, reasons, lag=lag)
    lagged = apply_rising_lag(rows, cuts) if lag else [dict(r) for r in rows]
    stream, seg, expect = delete_ranges(lagged, cuts)
    counts: Counter = Counter()
    spliced = list(gf.splice(stream, seg, counts))
    (root / "base").mkdir(parents=True)
    (root / "filled").mkdir()
    base_eng = make_engine(f2_engine.ArenaEngineF2, base_engine, root / "base", detect)
    fill_eng = make_engine(f2_engine.ArenaEngineF2, base_engine, root / "filled", detect)
    run_book(base_eng, lagged, **book)
    run_book(fill_eng, spliced, **book)
    infos, a, b = position_infos(base_eng), by_position(base_eng), by_position(fill_eng)
    exp = {pid: expected_class(i, cuts) for pid, i in infos.items()}
    act = {pid: actual_class([canon_line(r) for r in a[pid]], b.get(pid, [])) for pid in a}
    wrong = sorted(pid for pid in a if exp[pid] != act[pid])
    extra = sorted(set(b) - set(a))
    rescued = sum(1 for pid, i in infos.items() if i["priced"] and exp[pid] == "unchanged" and act.get(pid) == "unchanged"
                  and any(prog_match(c["prog"], i["prog"]) and max(i["lands"]) < c["lo"] - c["back"] and i["x_trig"] is not None and i["x_trig"] > c["hi"] for c in cuts))
    mixed = gf.mixed_groups(spliced)
    ok = (not wrong and not extra and counts["fill_rows"] == expect["fill_rows"] and counts["fill_rows_dup_dropped"] == expect["twin_rows"] and counts["filled_ranges"] == len(cuts)
          and fill_eng.counters.get("rows_dup", 0) == 0 and mixed == 0 and len(fill_eng.gaps) == len(base_eng.gaps) and len(fill_eng.fills) == len(cuts) and len(a) > 0)
    return {"plan": [{k: c[k] for k in ("reason", "lo", "hi", "back")} for c in cuts], "splice": dict(counts), "expected_fill_rows": expect["fill_rows"], "expected_twin_rows": expect["twin_rows"],
            "positions": len(a), "expected_classes": dict(Counter(exp.values())), "actual_classes": dict(Counter(act.values())), "mismatched_positions": len(wrong), "mismatch_digest": md5(wrong),
            "extra_positions_in_filled_run": len(extra), "late_prints": {"unbroken": late_print_kinds(base_eng), "filled": late_print_kinds(fill_eng)},
            "spanning_priced_unchanged": rescued, "rows_dup": fill_eng.counters.get("rows_dup", 0), "mixed_groups": mixed, "gaps": {"unbroken": len(base_eng.gaps), "filled": len(fill_eng.gaps)},
            "fills_seen": len(fill_eng.fills), "ledger_unbroken": ledger_digest([canon_line(r) for r in ledger_rows(base_eng)]),
            "ledger_filled": ledger_digest([canon_line(r) for r in ledger_rows(fill_eng)]), "pass": ok}


def p3(rows: Sequence[Mapping[str, Any]], root: Path, reasons: Sequence[str], *, min_rows: int = 1, **book) -> dict[str, Any]:
    enforce_hours(rows, "p3 rows")
    res: dict[str, Any] = {"proof": "p3", "schema": SCHEMA, "input_rows": len(rows), "min_rows": min_rows, "reasons": list(reasons),
                           "engine_sha256": {Path(f).name: sha256_file(f) for f in (base_engine.__file__, f2_engine.__file__)}}
    if len(rows) < min_rows:
        return {**res, "pass": False, "refused": f"fewer than the min-rows floor ({len(rows)} < {min_rows})"}
    res["cells"] = {}
    for lag in (False, True):
        if lag and "backlog_jump" not in reasons:
            continue
        for detect in (False, True):
            name = f"lag_{'rising' if lag else 'none'}_detection_{'on' if detect else 'off'}"
            res["cells"][name] = p3_cell(rows, root / name, reasons, detect, lag, **book)
    res["pass"] = bool(res["cells"]) and all(c["pass"] for c in res["cells"].values())
    return res


# ------------------------------------------------------------------------------------------------------------------------------------ CLI
def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    book = {"choices": ["pinned-deterministic"], "default": "pinned-deterministic"}
    a1 = sub.add_parser("p1")
    a1.add_argument("--hours-dir", required=True, help="converted arena rows of explore-0814 hours (JSONL)")
    a1.add_argument("--old-sha", required=True)
    a1.add_argument("--repo", default=".")
    a1.add_argument("--book", **book)
    a1.add_argument("--min-rows", type=int, required=True)
    a1.add_argument("--out", required=True)
    a2 = sub.add_parser("p2")
    a2.add_argument("--lo", type=int, required=True)
    a2.add_argument("--hi", type=int, required=True)
    a2.add_argument("--warm-margin-hours", type=float, default=1.0)
    a2.add_argument("--walker-hours-dir", required=True)
    a2.add_argument("--helius-env", required=True, help="env file path (read, never printed)")
    a2.add_argument("--rps", type=float, default=5.0)
    a2.add_argument("--min-slots", type=int, required=True)
    a2.add_argument("--out", required=True)
    a3 = sub.add_parser("p3")
    a3.add_argument("--hours-dir", required=True)
    a3.add_argument("--reasons", required=True)
    a3.add_argument("--book", **book)
    a3.add_argument("--min-rows", type=int, required=True)
    a3.add_argument("--out", required=True)
    for sp in (a1, a2, a3):
        sp.add_argument("--scratch-dir", default="/data2/mal/scratch" if os.path.isdir("/data2/mal/scratch") else None, help="where P2's warm follower and the engines write (default /data2/mal/scratch when it exists)")
    a = ap.parse_args(argv)
    import tempfile

    try:
        check_path(a.out, "--out")
        if a.scratch_dir:
            check_path(a.scratch_dir, "--scratch-dir")
        if a.cmd == "p1":
            check_path(a.repo, "--repo")
        with tempfile.TemporaryDirectory(dir=a.scratch_dir) as td:
            root = Path(td)
            if a.cmd == "p1":
                res = p1(read_rows(a.hours_dir), load_engine_at(a.old_sha, a.repo), root, min_rows=a.min_rows)
                res["harness_head"] = head_of(a.repo)
            elif a.cmd == "p3":
                res = p3(read_rows(a.hours_dir), root, [x for x in a.reasons.split(",") if x], min_rows=a.min_rows)
            else:
                walker = read_rows(a.walker_hours_dir, "--walker-hours-dir")
                url, kind = ftf.resolve_rpc_url(None, gf.read_env_file(a.helius_env))
                if kind != "helius":
                    raise Refused("--helius-env holds no key: the proof will not fall back to the public RPC")
                fetch, _tip, credits, lookup = ftf.make_rpc(url, min(max(a.rps, 0.5), 20.0))
                res = p2_run(a.lo, a.hi, int(a.warm_margin_hours * 9000), fetch, walker, root, lookup=lookup, min_slots=a.min_slots, sleep=time.sleep)
                res.update({"rpc_kind": kind, "credits": int(credits())})
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    Path(a.out).write_text(json.dumps(res, sort_keys=True, indent=1) + "\n")
    print(json.dumps({k: res[k] for k in ("proof", "pass") if k in res}, sort_keys=True))
    return 0 if res.get("pass") else 2


if __name__ == "__main__":
    raise SystemExit(main())
