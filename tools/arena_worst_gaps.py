"""Per-trade END-minus-worst gaps for the scorer's `--worst-in-slot GAPS.json` (DEC-032 section 1 item 2, QP ruling R7). PAPER ONLY. READ-ONLY. No RPC, no key.

    python -m tools.arena_worst_gaps --trades TRADES.jsonl --tape-dir DIR --out GAPS.json [--detail DETAIL.json] [--vmap FILE] [--pad-hours H] [--margin-slots N]

It does NOT redefine worst-in-slot. The definition, the pricing and the END consistency checks are `tools.arena_report_legs` (#747): per trade, the entry is re-priced at the
highest-price state of the landing slot and the exit at the lowest-price state of the exit slot (price = Q / B, V included), through `arena_paper.price_leg`, with the
ledger's own guard reference, ssb, nearby, stake, stack config, E1 floor. This tool only turns that into one number per trade and does it with bounded memory.

INPUT  TRADES.jsonl: ledger rows of kind "trade" (the deciding stake's covered, non-excluded trades of ONE book, as the read wrappers hand them over), one JSON object per line.
       Needed fields: trade_id, pool, prog, stake_sol, state_src, corrected_slot.exit, stack (send, rent, h_in, h_out, k_rounding, strand_rule, e1_floor_per_005, sps_exit,
       press_intercept_provisional), calc.{1p3,1p9}.{landing_slot, spot, ssb, nearby_lamports, Qe, Be, Qx, Bx, by_rule.*.pnl_nofail}. This tool never reads a ledger directory itself.
OUTPUT GAPS.json: {trade_id: gap_lamports >= 0}, the format `tools.arena_score.load_worst_gaps` reads. DETAIL.json: per trade the END and worst pnl, the rule that set the gap,
       the reason a trade was left out, and the counts below.
GAP (lamports, the 1.9 s deciding leg)
       gap_i = max over the strand rules (sc, total) of  pnl_nofail_END - pnl_nofail_NG, where NG is R7's "no guard rescue" row: the worst-in-slot row, except that a trip the
       1.15 guard would revert only at its worst entry state keeps its END row (the guard may not rescue a loser; QP at ec24494). R7's canary figure is the LOWER of the two legs,
       so the gap is taken against NG. One gap serves both strand rules (the scorer applies it to both), so the larger is used: conservative. A negative gap (the worst row beat
       END) is set to 0, never credited, and counted as clipped_negative.
LEFT OUT (never written as 0): a trade whose END state or END pnl recomputed from the tape differs from the ledger's (not the tape the engine priced), a pool not on the tape, a
       state without V, a non-PumpSwap row (R7 is defined for PRE-trade states). The scorer then prints "worst-in-slot not measured": one missing gap is enough.
MEMORY (streams; at most one tape line is decoded at a time, and only rows that can matter are kept): each trade needs only the prints of its pool from its earliest landing or
       exit slot up to the first print at or after its need slot (max landing, exit slot + 1). A row is kept only if its slot is inside such a window, widened at the end by
       --margin-slots (default 2000, about 13 min) to reach that first print; every other row is dropped before it reaches the pool history. Retained rows are therefore proportional to
       the trades, not to the tape, whatever its size; a window that is too short to reach the END print shows as an END mismatch (trade left out), not as a wrong gap. Lines are
       pre-filtered by a byte search for the book's pool ids before json decoding. A pool's V comes from each kept row's own virtual_quote_reserves (or --vmap).
PATHS  every path, and every tape FILE by its resolved (symlink-free) path, is checked with arena_report_legs.check_path (refuses sealed, forward, walk, probe, shadow, canary, shakedown ... before any filesystem access).
HARDLINKS  a tape file with st_nlink > 1 is NOT refused here: the wrapper must read tapes only from the expected tape root and must require rc 0 (documented choice, QP re-check 6368647431 (c)).
OUT   any earlier --out / --detail is removed before the run and the new ones are written to a temp file and renamed only on success: a refused run leaves none. The wrapper must require rc 0.
Run long as a MiScusi job; it holds the kept rows only.
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from tools import arena_stack as F
from tools.arena_report_legs import HOUR_MS, Refused, _lines, check_path, load_pools, tape_files, worst_trip
from tools.arena_paper import PoolHist

TAG = "1p9"
RULES = ("sc", "total")
DEFAULT_MARGIN_SLOTS = 2000


def need_window(r: Mapping[str, Any], margin: int) -> tuple[int, int]:
    """(lo, hi) slots whose prints this trade can read: from its earliest landing / exit slot to need + margin (need = max landing, exit slot, + 1)."""
    lands = [int(r["calc"][t]["landing_slot"]) for t in ("1p3", "1p9")]
    xl = int(r["corrected_slot"]["exit"]) + F.exit_lag_slots(float(r["stack"]["sps_exit"]))
    return min(lands + [xl]), max(lands + [xl]) + 1 + margin


def windows_by_pool(rows: Iterable[Mapping[str, Any]], margin: int = DEFAULT_MARGIN_SLOTS) -> dict[str, list[tuple[int, int]]]:
    out: dict[str, list[tuple[int, int]]] = {}
    for r in rows:
        if r.get("pool"):
            out.setdefault(r["pool"], []).append(need_window(r, margin))
    for p, ws in out.items():
        ws.sort()
        merged: list[tuple[int, int]] = []
        for lo, hi in ws:
            if merged and lo <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
            else:
                merged.append((lo, hi))
        out[p] = merged
    return out


def in_window(ws: Sequence[tuple[int, int]], slot: int) -> bool:
    i = bisect.bisect_right(ws, (slot, float("inf"))) - 1
    return i >= 0 and ws[i][0] <= slot <= ws[i][1]


def iter_window_rows(files: Sequence[Path], windows: Mapping[str, Sequence[tuple[int, int]]], stats: dict[str, int] | None = None) -> Iterator[dict[str, Any]]:
    """Trade rows of the wanted pools whose slot is inside a window, in file order. Streams: one line at a time; a line without a wanted pool id is never decoded."""
    st = stats if stats is not None else {}
    for k in ("lines", "decoded", "kept", "dropped_outside_window"):
        st.setdefault(k, 0)
    if not windows:
        return
    rx = re.compile(b"|".join(re.escape(p.encode()) for p in sorted(windows)))
    for p in files:
        for line in _lines(p):
            st["lines"] += 1
            if b'"trade"' not in line or not rx.search(line):
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            st["decoded"] += 1
            pool = r.get("pool") or r.get("mint")
            if r.get("type", "trade") != "trade" or pool not in windows or r.get("slot") is None:
                continue
            if in_window(windows[pool], int(r["slot"])):
                st["kept"] += 1
                yield r
            else:
                st["dropped_outside_window"] += 1


def gap_of_trade(row: Mapping[str, Any], pools: Mapping[str, PoolHist]) -> dict[str, Any]:
    """One trade: {"gap": lamports} or {"left_out": reason}. Never a 0 for something that could not be measured."""
    h = pools.get(row.get("pool"))
    if h is None:
        return {"left_out": "pool not on the tape"}
    try:
        _, calc_ng, diag = worst_trip(row, h)
    except Refused as exc:
        return {"left_out": str(exc)[:200]}
    if diag["state_mismatch"] or diag["pnl_mismatch"]:
        return {"left_out": f"END consistency failed (state {diag['state_mismatch']}, pnl {diag['pnl_mismatch']})"}
    per_rule = {}
    for rule in RULES:
        led = row["calc"][TAG]["by_rule"].get(rule)
        if led is None:
            continue
        per_rule[rule] = {"end": float(led["pnl_nofail"]), "ng": float(calc_ng[TAG]["by_rule"][rule]["pnl_nofail"])}
    if not per_rule:
        return {"left_out": "no strand rule priced on the 1.9 s leg"}
    raw = {r: v["end"] - v["ng"] for r, v in per_rule.items()}
    rule = max(raw, key=lambda r: raw[r])
    return {"gap": max(0.0, raw[rule]), "raw_gap": raw[rule], "rule": rule, "per_rule": per_rule, "clipped_negative": raw[rule] < 0.0}


def compute(rows: Sequence[Mapping[str, Any]], pools: Mapping[str, PoolHist]) -> tuple[dict[str, float], dict[str, Any]]:
    gaps: dict[str, float] = {}
    trades: dict[str, Any] = {}
    seen: set[str] = set()
    for r in rows:
        tid = r["trade_id"]
        if tid in seen:
            raise Refused(f"duplicated trade_id {tid}")
        seen.add(tid)
        g = gap_of_trade(r, pools)
        trades[tid] = g
        if "gap" in g:
            gaps[tid] = g["gap"]
    left = {t: g["left_out"] for t, g in trades.items() if "left_out" in g}
    return gaps, {"trades": trades, "n": len(rows), "measured": len(gaps), "left_out": left, "clipped_negative": sum(1 for g in trades.values() if g.get("clipped_negative")),
                  "nonzero": sum(1 for v in gaps.values() if v > 0), "all_measured": not left}


def load_trades(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    for i, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            raise Refused(f"{path} line {i}: not JSON") from None
        if r.get("kind") != "trade":
            raise Refused(f"{path} line {i}: kind {r.get('kind')!r}, expected a ledger row of kind 'trade'")
        rows.append(r)
    return rows


def run(rows: Sequence[Mapping[str, Any]], files: Sequence[Path], vmap: Mapping[str, Any] | None = None, margin: int = DEFAULT_MARGIN_SLOTS) -> tuple[dict[str, float], dict[str, Any]]:
    for f in files:  # the guard runs on every tape FILE as well as the directory: check_path resolves symlinks (QP re-check, condition 1)
        check_path(f, "tape file")
    stats: dict[str, int] = {}
    pools = load_pools(iter_window_rows(files, windows_by_pool(rows, margin), stats), vmap)
    gaps, detail = compute(rows, pools)
    detail["stream"] = stats
    detail["margin_slots"] = margin
    detail["rule"] = "max over strand rules of END minus the no-guard-rescue worst-in-slot row, 1.9 s leg, lamports, clipped at 0"
    return gaps, detail


def atomic_write(path: str | Path, text: str) -> None:
    """Write to a temp file in the same directory, fsync, rename. A reader never sees a half file."""
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)


def scan_flag(argv: Sequence[str], flag: str) -> str | None:
    """The value of `flag` in argv without argparse (so it works when argparse itself fails)."""
    for i, t in enumerate(argv):
        if t == flag and i + 1 < len(argv):
            return argv[i + 1]
        if t.startswith(flag + "="):
            return t.split("=", 1)[1]
    return None


def clear_outputs(argv: Sequence[str]) -> None:
    """Remove an earlier --out / --detail FIRST, before the path guard, the argument checks or any read: no refusal of any kind leaves a stale file. A path the guard would refuse
    (closed material) is never touched."""
    for flag in ("--out", "--detail"):
        p = scan_flag(argv, flag)
        if p:
            try:
                check_path(p, flag)
                Path(p).unlink(missing_ok=True)
            except (Refused, OSError):
                pass


def main(argv: Sequence[str] | None = None) -> int:
    clear_outputs(list(sys.argv[1:] if argv is None else argv))
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--trades", required=True)
    ap.add_argument("--tape-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--detail")
    ap.add_argument("--vmap")
    ap.add_argument("--pad-hours", type=float, default=2.0)
    ap.add_argument("--margin-slots", type=int, default=DEFAULT_MARGIN_SLOTS)
    a = ap.parse_args(argv)
    try:
        for what, p in (("trades", a.trades), ("tape dir", a.tape_dir), ("out", a.out), ("detail", a.detail), ("vmap", a.vmap)):
            if p:
                check_path(p, what)
        rows = load_trades(a.trades)
        vmap = json.loads(Path(a.vmap).read_text()) if a.vmap else None
        if not rows:
            raise Refused("no trade rows")
        lo = min(r["decision_ms"]["entry"] for r in rows)
        hi = max((r["decision_ms"]["exit"] or r["decision_ms"]["entry"]) for r in rows)
        pad = int(a.pad_hours * HOUR_MS)
        gaps, detail = run(rows, tape_files(Path(a.tape_dir), lo - pad, hi + pad), vmap, a.margin_slots)
    except (Refused, OSError, KeyError, ValueError) as exc:
        print(f"REFUSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    atomic_write(a.out, json.dumps(gaps, sort_keys=True, indent=1, allow_nan=False) + "\n")
    if a.detail:
        atomic_write(a.detail, json.dumps(detail, sort_keys=True, indent=1, allow_nan=False) + "\n")
    print(f"trades {detail['n']}  measured {detail['measured']}  left out {len(detail['left_out'])}  non-zero gaps {detail['nonzero']}  clipped negative {detail['clipped_negative']}  "
          f"stream {detail['stream']}")
    for t, why in list(detail["left_out"].items())[:5]:
        print(f"  left out {t}: {why}")
    return 0 if detail["all_measured"] else 4


if __name__ == "__main__":
    sys.exit(main())
