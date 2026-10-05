"""Offline comparison of tools.fast_grad_stream output against the confirmed tip tape.

  python -m tools.grad_stream_compare --start ISO --end ISO [--stream-dir D] [--tip-dir D]

Joins grad-*.jsonl complete rows to the tip tape's migrations-*.jsonl `complete` rows by mint
(first row per mint wins on each side) and reports JSON plus a short text summary:
  coverage             share of tip-tape completes in the window the stream also saw
  lead_ms              tip t_recv_ms minus stream t_recv_ms (positive = stream earlier), p10/p50/p90
  before_first_pumpswap  share of mints (stream complete and tip PumpSwap trade both present) where the
                       stream's complete arrived before the tip tape's first PumpSwap trade
  rollback             processed (signature, kind) rows for kind trade and complete whose signature
                       never appears on the confirmed tip tape for that mint and kind
Window membership is by t_recv_ms. The tip side is read `slack_ms` past the end because it lags.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from tools.migration_stream_probe import hour_of_ms, parse_time_ms, pct

DEFAULT_STREAM = "/var/lib/mal/fast-grad-stream"
DEFAULT_TIP = "/var/lib/mal/sealed/fast-trades-tip"
_HOUR_RE = re.compile(r"^[a-z-]+-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl$")


def iter_rows(directory: Path, prefix: str, start_ms: int, end_ms: int) -> Iterator[dict[str, Any]]:
    if not directory.is_dir():
        return
    lo, hi = hour_of_ms(start_ms), hour_of_ms(end_ms)
    for p in sorted(directory.rglob(f"{prefix}-*.jsonl")):
        m = _HOUR_RE.match(p.name)
        if not m or not lo <= m.group(1) <= hi:
            continue
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                t = r.get("t_recv_ms")
                if isinstance(t, int) and start_ms <= t <= end_ms:
                    yield r


def _first_by_mint(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        m = r.get("mint")
        if m and (m not in out or r["t_recv_ms"] < out[m]["t_recv_ms"]):
            out[m] = r
    return out


class SlotSet:
    """Single slots plus inclusive ranges the tip follower did not cover."""

    def __init__(self) -> None:
        self.slots: set[int] = set()
        self.ranges: list[tuple[int, int]] = []

    def add_row(self, r: dict[str, Any]) -> None:
        if isinstance(r.get("slot"), int):
            self.slots.add(r["slot"])
        if isinstance(r.get("from_slot"), int) and isinstance(r.get("to_slot"), int):
            self.ranges.append((r["from_slot"], r["to_slot"]))

    def contains(self, slot: Any) -> bool:
        return isinstance(slot, int) and (slot in self.slots or any(a <= slot <= b for a, b in self.ranges))


def load_skipped(tip_dir: Path, start_ms: int, end_ms: int) -> SlotSet:
    """skipped-slots-<hour>.jsonl rows {slot,kind:skipped} and gaps.jsonl rows ({slot,kind:gap} or
    a backlog_jump {from_slot,to_slot}), as written by tools/fast_tip_follower.py."""
    out = SlotSet()
    for r in iter_rows(tip_dir, "skipped-slots", start_ms, end_ms):
        out.add_row(r)
    gaps = tip_dir / "gaps.jsonl"
    if gaps.is_file():
        with open(gaps, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                t = r.get("t_recv_ms")
                if isinstance(t, int) and start_ms <= t <= end_ms:
                    out.add_row(r)
    return out


def compare(stream_rows: Iterable[dict[str, Any]], tip_mig: Iterable[dict[str, Any]],
            tip_trades: Iterable[dict[str, Any]], skipped: "SlotSet | None" = None) -> dict[str, Any]:
    stream_rows = list(stream_rows)
    tip_mig = list(tip_mig)
    s_complete = _first_by_mint(r for r in stream_rows if r.get("kind") == "complete")
    s_migrate = _first_by_mint(r for r in stream_rows if r.get("kind") == "migrate")
    t_complete = _first_by_mint(r for r in tip_mig if r.get("type") == "complete")
    t_swap = _first_by_mint(r for r in tip_trades if r.get("venue") == "pumpswap")
    both = sorted(set(s_complete) & set(t_complete))
    leads = [t_complete[m]["t_recv_ms"] - s_complete[m]["t_recv_ms"] for m in both]
    # stream complete vs the tip's first PumpSwap trade for the same mint
    sw = sorted(set(s_complete) & set(t_swap))
    sw_leads = [t_swap[m]["t_recv_ms"] - s_complete[m]["t_recv_ms"] for m in sw]
    # migrate rows vs tip first PumpSwap trade, for context (the migrate tx lands ~3 slots late)
    mw = sorted(set(s_migrate) & set(t_swap))
    mw_leads = [t_swap[m]["t_recv_ms"] - s_migrate[m]["t_recv_ms"] for m in mw]

    # rollback: processed signatures absent from the confirmed tape, same mint and kind
    conf: dict[str, set[tuple[str, str]]] = {"trade": set(), "complete": set()}
    for r in tip_trades:
        if r.get("signature") and r.get("mint"):
            conf["trade"].add((r["mint"], r["signature"]))
    for r in tip_mig:
        if r.get("type") == "complete" and r.get("signature") and r.get("mint"):
            conf["complete"].add((r["mint"], r["signature"]))
    def roll_for(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for kind in ("trade", "complete"):
            seen = {(r["mint"], r["signature"]) for r in rows
                    if r.get("kind") == kind and r.get("mint") and r.get("signature")}
            missing = len(seen - conf[kind])
            out[kind] = {"processed": len(seen), "not_confirmed": missing,
                         "rate": (missing / len(seen)) if seen else None}
        return out

    roll = roll_for(stream_rows)
    kept = [r for r in stream_rows if not (skipped and skipped.contains(r.get("slot")))]
    roll_excl = roll_for(kept)
    return {
        "n_tip_complete": len(t_complete),
        "n_stream_complete": len(s_complete),
        "n_matched": len(both),
        "coverage": (len(both) / len(t_complete)) if t_complete else None,
        "lead_ms": {"p10": pct(leads, 10), "p50": pct(leads, 50), "p90": pct(leads, 90)},
        "before_first_pumpswap": {
            "n": len(sw),
            "share": (sum(1 for d in sw_leads if d > 0) / len(sw)) if sw else None,
            "lead_ms_p10": pct(sw_leads, 10), "lead_ms_p50": pct(sw_leads, 50),
            "lead_ms_p90": pct(sw_leads, 90),
        },
        "migrate_vs_first_pumpswap": {
            "n": len(mw), "lead_ms_p10": pct(mw_leads, 10), "lead_ms_p50": pct(mw_leads, 50),
            "lead_ms_p90": pct(mw_leads, 90),
        },
        "rollback": roll,
        "rollback_excl_tip_skipped_or_gap_slots": {
            **roll_excl, "excluded_rows": len(stream_rows) - len(kept)},
    }


def compare_dirs(stream_dir: Path, tip_dir: Path, start_ms: int, end_ms: int,
                 slack_ms: int = 600_000) -> dict[str, Any]:
    stream = list(iter_rows(stream_dir, "grad", start_ms, end_ms))
    tip_mig = list(iter_rows(tip_dir, "migrations", start_ms, end_ms + slack_ms))
    # only the columns the comparison needs, so a busy trades tape stays small in memory
    mints = {r["mint"] for r in stream if r.get("mint")} | {r["mint"] for r in tip_mig if r.get("mint")}
    sigs = {r["signature"] for r in stream if r.get("signature")}
    tip_trades = [
        {"mint": r.get("mint"), "venue": r.get("venue"), "signature": r.get("signature"),
         "t_recv_ms": r["t_recv_ms"]}
        for r in iter_rows(tip_dir, "trades", start_ms, end_ms + slack_ms)
        if r.get("mint") in mints or r.get("signature") in sigs
    ]
    return compare(stream, tip_mig, tip_trades, load_skipped(tip_dir, start_ms, end_ms + slack_ms))


def summary_text(res: dict[str, Any]) -> str:
    def f(x: Any) -> str:
        return "n/a" if x is None else (f"{x:.3f}" if isinstance(x, float) and abs(x) < 10 else f"{x:.0f}")

    lead, bf, rb = res["lead_ms"], res["before_first_pumpswap"], res["rollback"]
    return "\n".join([
        f"tip completes {res['n_tip_complete']}, stream completes {res['n_stream_complete']}, "
        f"matched {res['n_matched']}, coverage {f(res['coverage'])}",
        f"lead_ms (tip - stream) p10/p50/p90: {f(lead['p10'])} / {f(lead['p50'])} / {f(lead['p90'])}",
        f"stream complete before tip first PumpSwap trade: {f(bf['share'])} of {bf['n']} "
        f"(lead p50 {f(bf['lead_ms_p50'])} ms)",
        f"rollback (all rows) trade {f(rb['trade']['rate'])} of {rb['trade']['processed']}, "
        f"complete {f(rb['complete']['rate'])} of {rb['complete']['processed']}",
        "rollback excluding tip skipped/gap slots: "
        f"trade {f(res['rollback_excl_tip_skipped_or_gap_slots']['trade']['rate'])}, "
        f"complete {f(res['rollback_excl_tip_skipped_or_gap_slots']['complete']['rate'])} "
        f"({res['rollback_excl_tip_skipped_or_gap_slots']['excluded_rows']} rows excluded)",
    ])


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--stream-dir", default=DEFAULT_STREAM)
    ap.add_argument("--tip-dir", default=DEFAULT_TIP)
    args = ap.parse_args(argv)
    res = compare_dirs(Path(args.stream_dir), Path(args.tip_dir), parse_time_ms(args.start),
                       parse_time_ms(args.end))
    print(json.dumps(res, indent=2, sort_keys=True))
    print(summary_text(res), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
