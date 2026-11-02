#!/usr/bin/env python3
"""arena_tip_gapfill_proofs: the three md5 proofs owed by DEC-031 Amendment 26 item 11 (P1, P2, P3). PAPER only. Output: md5s, field names and counts only.

It reads CONVERTED ARENA ROWS (JSONL files, slot order) that the pinned writer step produced from explore-0814 hours (hunt23 RULES.md) and walker rows of the same hours; it never opens
the tip tape, the arena tape, the mirror or the writer's derived copy (gapfill.refused_path is applied to every directory argument), and it holds no key. P2 is the only part that
fetches: it takes an injected fetcher (the CLI builds it from a Helius env file read by path, never printed).

  P1  the engine at NEW against the engine at OLD (main 2fa2e4bc) on the same rows with no filled marker and a pinned deterministic book: ledger md5 equal per bot, in order and as a set.
  P2  the fill tool started cold on [lo, hi] against a warm TipFollower run over [lo - margin, hi + margin] and against the walker's rows for the same slots: per-slot row md5 equal over every
      field except t_recv_ms, source and the singular virtual_quote_reserve, tx_index presence included.
  P3  ranges deleted from the rows (every requested fillable reason; unresolved_dropped keeps follower rows on both sides of the gap row), filled from the deleted rows, spliced and run through
      the engine with the same book: positions the amendment does not change by design must have equal rows; the changed ones are listed by count and class (mark_through, feed_gap, late-print flag);
      rows_dup 0 and no mixed (pool, slot) group.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import types
import zlib
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from tools import arena_tip_adapter as ata
from tools import arena_tip_gapfill as gf
from tools import fast_tip_follower as ftf

SCHEMA = "arena_tip_gapfill_proofs_v1"
DROP_P2 = frozenset({"t_recv_ms", "source", "virtual_quote_reserve"})
BOOK_ID = "proofbook"


def md5(x: Any) -> str:
    return hashlib.md5(json.dumps(x, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


# ------------------------------------------------------------------------------------------------------------------------------------ inputs
def read_rows(directory: str | os.PathLike) -> list[dict[str, Any]]:
    """Every *.jsonl in `directory`, file-name order, line order. Refuses a directory under the sealed tip tree, the archive or a derived copy."""
    why = gf.refused_path(directory)
    if why:
        raise SystemExit(f"REFUSED: {directory} {why}")
    out: list[dict[str, Any]] = []
    for p in sorted(Path(directory).glob("*.jsonl")):
        out.extend(json.loads(x) for x in p.read_text().splitlines() if x.strip())
    return out


def load_engine_source(src: str, name: str) -> types.ModuleType:
    """An arena_paper module from source text (the OLD engine, from `git show <sha>:tools/arena_paper.py`), under its own module name."""
    mod = types.ModuleType(name)
    mod.__package__, mod.__file__ = "tools", f"<{name}>"
    sys.modules[name] = mod
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    return mod


def load_engine_at(sha: str, repo: str | os.PathLike) -> types.ModuleType:
    src = subprocess.run(["git", "-C", os.fspath(repo), "show", f"{sha}:tools/arena_paper.py"], capture_output=True, text=True, check=True, timeout=60).stdout
    return load_engine_source(src, f"tools.arena_paper_at_{sha[:8]}")


# ------------------------------------------------------------------------------------------------------------------------------------ the pinned deterministic book
def make_engine(mod: types.ModuleType, root: Path) -> Any:
    os.environ[mod.TEST_ENV] = "1"  # a throwaway replay root: the test-only registry overrides are honoured in this process only
    reg = root / "bots.json"
    mod.register_bot(BOOK_ID, "abc1234", 0, reg, now_ms=0, verify_sha=False, deciding_stake=0.25, model_ids=[])
    eng = mod.ArenaEngine(root / "ledger", reg, mod.StackConfig(feed_delay_slots=2, gap_slots=None, gap_silence_ms=None), now_fn=lambda: 0, code_state_fn=lambda: ("0" * 40, False),
                          sps_fn=lambda: 0.4, min_free_pct=0.0)
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
                except Exception as exc:  # noqa: BLE001 - counted, never printed in full
                    st["sell_refused"] += 1
                    st[f"sell_refused_{type(exc).__name__}"] += 1
                del open_[k]
        if key not in seen and zlib.crc32(key.encode()) % every == 0:
            try:
                open_[key] = (eng.buy(BOOK_ID, key, 0.25, t, slot, {"proof": 1}), slot)
                seen.add(key)
                st["buys"] += 1
            except Exception as exc:  # noqa: BLE001
                st["buy_refused"] += 1
                st[f"buy_refused_{type(exc).__name__}"] += 1
    return st


_VOLATILE = frozenset({"engine_id"})


def ledger_lines(eng: Any) -> list[str]:
    """The bot's ledger as canonical lines without the per-process fields (the start record and engine_id carry a random id)."""
    out = []
    for r in eng.ledger.read_bot(BOOK_ID):
        if r.get("kind") == "start":
            continue
        out.append(json.dumps({k: v for k, v in r.items() if k not in _VOLATILE}, sort_keys=True, separators=(",", ":"), default=str))
    return out


def ledger_digest(lines: Sequence[str]) -> dict[str, Any]:
    return {"rows": len(lines), "md5_in_order": hashlib.md5("\n".join(lines).encode()).hexdigest(), "md5_as_set": hashlib.md5("\n".join(sorted(lines)).encode()).hexdigest()}


# ------------------------------------------------------------------------------------------------------------------------------------ P1
def p1(rows: Sequence[Mapping[str, Any]], old: types.ModuleType, new: types.ModuleType, root: Path, *, min_rows: int = 1, **book) -> dict[str, Any]:
    if len(rows) < min_rows:
        return {"proof": "p1", "schema": SCHEMA, "pass": False, "refused": f"fewer than the min-rows floor ({len(rows)} < {min_rows})"}
    res: dict[str, Any] = {"proof": "p1", "schema": SCHEMA, "input_rows": len(rows), "min_rows": min_rows}
    for tag, mod in (("old", old), ("new", new)):
        (root / tag).mkdir(parents=True)
        eng = make_engine(mod, root / tag)
        res[f"book_{tag}"] = dict(run_book(eng, rows, **book))
        res[tag] = ledger_digest(ledger_lines(eng))
    res["pass"] = res["old"] == res["new"] and res["old"]["rows"] > 0 and res["book_old"] == res["book_new"]
    return res


# ------------------------------------------------------------------------------------------------------------------------------------ P2
def row_md5_by_slot(rows: Iterable[Mapping[str, Any]], lo: int, hi: int) -> dict[int, str]:
    by: dict[int, list] = {}
    for r in rows:
        if isinstance(r.get("slot"), int) and lo <= r["slot"] <= hi:
            by.setdefault(r["slot"], []).append({k: v for k, v in r.items() if k not in DROP_P2})
    return {s: md5(sorted(v, key=lambda r: (r.get("tx_index") if isinstance(r.get("tx_index"), int) else 1 << 40, r.get("event_index") or 0, str(r.get("signature"))))) for s, v in by.items()}


def p2_compare(fill_rows: Sequence[Mapping[str, Any]], warm_rows: Sequence[Mapping[str, Any]], walker_rows: Sequence[Mapping[str, Any]], lo: int, hi: int, *, min_slots: int = 1) -> dict[str, Any]:
    srcs = {"fill": row_md5_by_slot(fill_rows, lo, hi), "warm": row_md5_by_slot(warm_rows, lo, hi), "walker": row_md5_by_slot(walker_rows, lo, hi)}
    slots = sorted(set().union(*[set(v) for v in srcs.values()]))
    bad = [s for s in slots if len({srcs[k].get(s) for k in srcs}) != 1]
    no_tx = {k: sum(1 for r in rows if isinstance(r.get("slot"), int) and lo <= r["slot"] <= hi and r.get("tx_index") is None)
             for k, rows in (("fill", fill_rows), ("warm", warm_rows), ("walker", walker_rows))}
    return {"proof": "p2", "schema": SCHEMA, "lo": lo, "hi": hi, "slots_with_rows": len(slots), "min_slots": min_slots, "rows": {"fill": len(fill_rows), "warm": len(warm_rows), "walker": len(walker_rows)},
            "slot_digest_of_digests": md5([[s, srcs["fill"].get(s)] for s in slots]), "mismatched_slots": len(bad), "mismatch_digest": md5(bad), "rows_no_tx_index": no_tx,
            "pass": not bad and len(slots) >= min_slots and len(set(no_tx.values())) == 1}


def trade_rows_of(directory: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for p in sorted(directory.glob("trades-*.jsonl")) for x in p.read_text().splitlines() if x.strip()]


def p2_run(lo: int, hi: int, margin_slots: int, fetch: ftf.Fetcher, walker_rows: Sequence[Mapping[str, Any]], root: Path, *, lookup=None, min_slots: int = 1) -> dict[str, Any]:
    """The warm reference: a TipFollower (event V on, no singular V) over [lo - margin, hi + margin]; the test: gapfill.fill on [lo, hi], cold, same fetcher and lookup."""
    warm = ftf.TipFollower(out_dir=root / "warm" / "out", state_dir=root / "warm" / "state", fetch=fetch, get_tip=lambda: 0, lookup=lookup, v_lookup=lambda p: {}, clock_ms=lambda: 1, sleep=lambda s: None,
                           log=lambda m: None, trade_event_v=True)
    for s in range(lo - margin_slots, hi + margin_slots + 1):
        warm.process_slot(s)
    man = gf.fill([{"kind": "gap", "reason": "backlog_jump", "from_slot": lo, "to_slot": hi, "slots": hi - lo + 1, "t_recv_ms": 1}], fetch, root / "seg", lookup=lookup)
    out = p2_compare(trade_rows_of(root / "seg" / "rows"), trade_rows_of(root / "warm" / "out"), list(walker_rows), lo, hi, min_slots=min_slots)
    out["fill_complete"] = man["complete"]
    out["pass"] = bool(out["pass"] and man["complete"])
    return out


# ------------------------------------------------------------------------------------------------------------------------------------ P3
def walker_of(r: Mapping[str, Any]) -> dict[str, Any]:
    w = dict(r)
    for k in ("sol_lamports", "token_raw", "quote_reserve", "base_reserve"):
        if isinstance(w.get(k), float):
            w[k] = int(w[k])
    return w


def delete_ranges(rows: Sequence[Mapping[str, Any]], reasons: Sequence[str], *, span: int = 12) -> tuple[list[dict[str, Any]], gf.Segment, list[dict[str, Any]]]:
    """(stream with the ranges deleted and the gap rows in, the segment that fills them, the plan). Deterministic: reason i takes the distinct slots from position (i + 1) / (n + 1) of the
    slot list. `unresolved_dropped` is a single slot whose rows are split around the gap row (follower rows on both sides); every other reason deletes `span` slots."""
    slots = sorted({r["slot"] for r in rows if isinstance(r.get("slot"), int) and r.get("type", "trade") == "trade"})
    plan, out, deleted = [], [], []
    cuts: dict[int, tuple[int, int, str]] = {}
    for i, reason in enumerate(reasons):
        j = slots[(i + 1) * len(slots) // (len(reasons) + 1)]
        lo, hi = (j, j) if reason == "unresolved_dropped" else (j, j + span - 1)
        cuts[lo] = (lo, hi, reason)
        plan.append({"reason": reason, "from_slot": lo, "to_slot": hi})
    ranges, walker = [], []
    idx = 0
    rows = list(rows)
    while idx < len(rows):
        r = rows[idx]
        s = r.get("slot")
        cut = cuts.get(s) if isinstance(s, int) else None
        if cut is None:
            out.append(dict(r))
            idx += 1
            continue
        lo, hi, reason = cut
        inside = [x for x in rows[idx:] if isinstance(x.get("slot"), int) and lo <= x["slot"] <= hi]
        t = inside[0].get("t_recv_ms") if inside else r.get("t_recv_ms")
        gap = {"v": 1, "type": "gap", "prog": "pumpswap" if reason == "unresolved_dropped" else "all", "slot": lo, "t_recv_ms": t, "signature": None, "event_index": None, "kind": ata.gap_kind(reason),
               "data_loss": True, "from_ms": t, "to_ms": t, "reason": reason, "from_slot": lo, "to_slot": hi, "missed_slots": hi - lo + 1, "src": "tip"}
        ranges.append({"gid": f"{reason}:{lo}-{hi}", "reason": reason, "from_slot": lo, "to_slot": hi, "t_recv_ms": t, "slots": hi - lo + 1, "fetched": hi - lo + 1, "skipped_slots": [],
                       "failed_slots": [], "filled": True, "back_slots": gf.BACKLOG_SLOTS if reason == "backlog_jump" else 0})
        walker.extend(("trade", walker_of(x)) for x in inside if x.get("type", "trade") == "trade")
        if reason == "unresolved_dropped":
            half = max(1, len(inside) // 2)
            out.extend(dict(x) for x in inside[:half])
            out.append(gap)
            out.extend(dict(x) for x in inside[half:])
        else:
            out.append(gap)
        deleted.append((lo, hi))
        idx += len(inside)
    return out, gf.Segment({"ranges": ranges}, walker), plan


def by_position(eng: Any) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line, r in zip(ledger_lines(eng), [x for x in eng.ledger.read_bot(BOOK_ID) if x.get("kind") != "start"]):
        out.setdefault(str(r.get("position_id")), []).append(line)
    return out


def p3(rows: Sequence[Mapping[str, Any]], new: types.ModuleType, root: Path, reasons: Sequence[str], *, min_rows: int = 1, **book) -> dict[str, Any]:
    if len(rows) < min_rows:
        return {"proof": "p3", "schema": SCHEMA, "pass": False, "refused": f"fewer than the min-rows floor ({len(rows)} < {min_rows})"}
    stream, seg, plan = delete_ranges(rows, reasons)
    counts: Counter = Counter()
    spliced = list(gf.splice(stream, seg, counts))
    (root / "base").mkdir(parents=True)
    (root / "filled").mkdir()
    base, filled = make_engine(new, root / "base"), make_engine(new, root / "filled")
    run_book(base, rows, **book)
    run_book(filled, spliced, **book)
    a, b = by_position(base), by_position(filled)
    flagged = {r["position_id"] for r in filled.ledger.read_bot(BOOK_ID) if r.get("kind") == "late_print" and r.get("fill_margin")}
    cls: dict[str, str] = {}
    for r in filled.ledger.read_bot(BOOK_ID):
        pid = r.get("position_id")
        if r.get("kind") == "trade" and r.get("mark_through"):
            cls[pid] = "mark_through"
        elif r.get("kind") == "excluded":
            cls[pid] = "excluded_" + str(r.get("exclusion_reason"))
    for pid in flagged:
        cls.setdefault(pid, "late_print_flag")
    unaffected = [p for p in a if p not in cls]
    bad = [p for p in unaffected if [x for x in a[p] if '"kind":"late_print"' not in x] != [x for x in b.get(p, []) if '"kind":"late_print"' not in x]]
    fills = [r for r in spliced if r.get("fill")]
    eqs = Counter({"rows_dup": filled.counters.get("rows_dup", 0)})
    return {"proof": "p3", "schema": SCHEMA, "input_rows": len(rows), "min_rows": min_rows, "plan": plan, "splice": dict(counts), "fill_rows": len(fills), "positions_unbroken": len(a),
            "positions_filled": len(b), "changed_by_design": dict(Counter(cls.values())), "late_print_count": len(flagged), "unaffected_positions": len(unaffected), "unaffected_mismatched": len(bad),
            "mismatch_digest": md5(sorted(bad)), "rows_dup": eqs["rows_dup"], "mixed_groups": gf.mixed_groups(spliced), "gaps_after_splice": len(filled.gaps), "fills_seen": len(filled.fills),
            "ledger_unbroken": ledger_digest(ledger_lines(base)), "ledger_filled": ledger_digest(ledger_lines(filled)),
            "pass": not bad and eqs["rows_dup"] == 0 and gf.mixed_groups(spliced) == 0 and len(filled.fills) == counts["filled_ranges"] and counts["filled_ranges"] == len(plan) and len(a) > 0}


# ------------------------------------------------------------------------------------------------------------------------------------ CLI
def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("p1")
    a1.add_argument("--hours-dir", required=True, help="converted arena rows of explore-0814 hours (JSONL)")
    a1.add_argument("--old-sha", required=True)
    a1.add_argument("--new-sha", help="default: the working tree")
    a1.add_argument("--repo", default=".")
    a1.add_argument("--book", default="pinned-deterministic")
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
    a3.add_argument("--book", default="pinned-deterministic")
    a3.add_argument("--backlog-slots", type=int, default=gf.BACKLOG_SLOTS)
    a3.add_argument("--min-rows", type=int, required=True)
    a3.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    import tempfile

    from tools import arena_paper as cur

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        if a.cmd == "p1":
            old = load_engine_at(a.old_sha, a.repo)
            new = cur if not a.new_sha else load_engine_at(a.new_sha, a.repo)
            res = p1(read_rows(a.hours_dir), old, new, root, min_rows=a.min_rows)
        elif a.cmd == "p3":
            res = p3(read_rows(a.hours_dir), cur, root, [x for x in a.reasons.split(",") if x], min_rows=a.min_rows)
        else:
            url, kind = ftf.resolve_rpc_url(None, gf.read_env_file(a.helius_env))
            fetch, _tip, credits, lookup = ftf.make_rpc(url, min(max(a.rps, 0.5), 20.0))
            res = p2_run(a.lo, a.hi, int(a.warm_margin_hours * 9000), fetch, read_rows(a.walker_hours_dir), root, lookup=lookup, min_slots=a.min_slots)
            res.update({"rpc_kind": kind, "credits": int(credits())})
    Path(a.out).write_text(json.dumps(res, sort_keys=True, indent=1) + "\n")
    print(json.dumps({k: res[k] for k in ("proof", "pass") if k in res}, sort_keys=True))
    return 0 if res.get("pass") else 2


if __name__ == "__main__":
    raise SystemExit(main())
