"""hv4_c0_tieout: the daily tie-out exporter of the HV4 live C0 adapter (PR 5b, removal precondition 8). OFFLINE and PURE: no RPC, no key, no price.

It writes the bridge's book (positions, fills with the fees actually signed, latches) in the input format the existing tie-out gate expects:
`tools/arena_tieout_fetch.py --ledger` and `tools/arena_tieout.py --ledger` take `{sends, trips, open_mints}` (all three lists, `closed` boolean on every trip). The
`{sends, trips, open_mints}` part is produced by the arena canary's own exporter (`tools.arena_tieout_ledger.export`, unchanged); this module only turns the executor's
fill rows (`<state_dir>/probe/fills.jsonl`: `buy` / `sell` rows with a signature) into the rows that exporter reads, and adds a `bridge` block the tie-out ignores:

    bridge.positions   the bridge's orders that hold a position (order_id, mint, status, stake_lamports)
    bridge.fills       one entry per fill row: kind, mint, signature, landed, fee_lamports, priority_lamports (the priority of the row's own signature, read from its signed bytes; see SEND ROWS below), spend / received
    bridge.trip_fees   per mint: buy_fee_lamports, sell_fee_lamports (every sell send, escalated resends included), sell_priorities
    bridge.latches     the bridge's latches at export time

SEND ROWS (QP #970 precondition 8: the signed priority per signature, and last_valid_block_height on every send row; N7 / N-D and ruling 2). Closed for the hv4_c0 profile
inside the bridge's executor subclass (tools/arena_live_bridge.py `BridgeExecutor._timing` and `_log`, `signed_priority_lamports`); tools/probe_live.py is untouched. Every buy and
sell fill row, landed, landed-failed or EXPIRED, is built from its own pending entry and carries `last_valid_block_height` = that entry's `p["lvbh"]` and `priority_lamports` = the
priority read from the SIGNED BYTES of the signature the row carries. It is neither a recomputation when the send resolves nor a per-mint memory, so an earlier rung that lands
after an escalation, or a landing seen after a restart, cannot produce a false `tieout_mismatch`. A row whose signed bytes cannot be read carries null and `audit_fees` refuses it
(fail closed). With the height on the row, an expired send whose status is null with history search on (tools/arena_tieout_fetch.py `cut_ledger`) is a CONFIRMED DROP in
tools/arena_tieout.py once the finalized height is past it, and the day can sign off; without it the send exports pending and the day never signs off.
STILL OPEN (not in this change): the C0 read / install pack must check `unresolved_hard_halts == []` AND `match` in the parity result, not rc 0 alone (a hard latch does not
change the parity exit code).

FEE AUDIT (fail closed, `TieoutMismatch`). The twin is priced at the fees actually sent, so the exporter checks, per row, fee_lamports == priority_lamports + 5,000;
that a buy was signed at the pinned buy priority; that each sell was signed at a pinned priority (55,000 or 150,000) and never below the schedule for its attempt
index (`sell_priority(i)`); that a landed buy spent the order's S; and, when the parity trips file is given, that its buy_fee_lamports, sell_fee_lamports and
stake_lamports equal the ones recomputed from the rows; every one of those fields, and exit_landed, must be present on every parity trip (a missing field is a mismatch). Any difference raises; the bridge turns it into the hard latch `tieout_mismatch`.

DAILY, WRITE-ONCE. `LEDGER-<day>.json` in the tie-out directory, created with O_EXCL, only for a CLOSED UTC day, from the rows with ts_ms before the end of that day
(the fetcher cuts the window itself). A rerun with an identical core is a no-op ("same"); a different core never overwrites ("conflict", rc 4). The `bridge.latches`
and `bridge.positions` blocks are state at export time and are not part of the comparison.

    python -m tools.hv4_c0_tieout --fills FILLS.jsonl --state BRIDGE_STATE.json --out-dir DIR --day YYYY-MM-DD [--trips TRIPS.jsonl] [--now-ms MS]

Exit codes: 0 written or same, 2 refused input, 3 fee audit mismatch, 4 write-once conflict. Nothing here says a book makes money; it is a bookkeeping export.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

from tools import arena_tieout_ledger as atl
from tools import hv4_live_c0 as c0

SCHEMA = "hv4_c0_tieout_ledger_v1"
DAY_MS = 86_400_000
POSITION_STATUSES = ("open", "sell_sending", "sell_sent")
SELL_PRIORITIES = (c0.PRIORITY_PIN["sell"], c0.PRIORITY_PIN["escalated"])


class TieoutMismatch(Exception):
    """The signed fees or stake do not match the schedule, the order, or the parity trips file."""


class WriteConflict(Exception):
    pass


def _isint(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def day_bounds(day: str) -> tuple[int, int]:
    from datetime import datetime, timezone
    try:
        t = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        raise atl.BadLedger(f"day must be YYYY-MM-DD, got {day!r}") from None
    ms = int(t.timestamp() * 1000)
    return ms, ms + DAY_MS


def fill_rows(rows: Iterable[Mapping[str, Any]], end_ms: int) -> list[dict[str, Any]]:
    """The buy / sell rows before `end_ms`, in file order, as the exporter's input. A buy / sell row without an integer ts_ms is refused (it cannot be cut to a day)."""
    out = []
    for r in rows:
        if not isinstance(r, dict):
            raise atl.BadLedger("a fill row is not an object")
        if r.get("kind") not in ("buy", "sell"):
            continue
        if not _isint(r.get("ts_ms")):
            raise atl.BadLedger(f"a {r.get('kind')} fill row has no integer ts_ms")
        if r["ts_ms"] < end_ms:
            out.append(r)
    return out


def exporter_rows(fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows `arena_tieout_ledger.export` reads: each fill row with mode live, preceded by the `arena_send` row of its signature (a send is a fill row with a signature)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in fills:
        sig = r.get("signature")
        if "landed" not in r:  # the write-ahead "sent" shape of a fill row (no resolution yet)
            continue
        if isinstance(sig, str) and sig and sig not in seen:
            seen.add(sig)
            lv = r.get("last_valid_block_height", r.get("lvbh"))
            out.append({"kind": "arena_send", "mode": "live", "mint": r["mint"], "signature": sig, "side": r["kind"], "last_valid_block_height": lv})
        out.append({**r, "mode": "live"})
    return out


def audit_fees(fills: list[dict[str, Any]], stakes: Mapping[str, int], parity_trips: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """Per-mint fees as signed, with the fee audit. Raises TieoutMismatch. Rows that never reached the chain (expired: no fee) carry no fee and are skipped."""
    per: dict[str, dict[str, Any]] = {}
    sell_idx: dict[str, int] = {}
    for r in fills:
        if "landed" not in r:
            continue
        kind, mint, landed = r["kind"], r["mint"], r["landed"]
        t = per.setdefault(mint, {"buy_fee_lamports": None, "sell_fee_lamports": 0, "sell_priorities": [], "stake_lamports": None, "sell_landed": False})
        if kind == "sell":
            i = sell_idx.get(mint, 0)
            sell_idx[mint] = i + 1
        if not landed and r.get("fail_class") == "expired":
            continue
        fee, prio = r.get("fee_lamports"), r.get("priority_lamports")
        if not _isint(fee) or not _isint(prio):
            raise TieoutMismatch(f"{kind} row for {mint} (signature {r.get('signature')}) has no integer fee_lamports / priority_lamports")
        if fee != prio + c0.BASE_FEE_LAMPORTS:
            raise TieoutMismatch(f"{kind} row for {mint}: fee_lamports {fee} != priority_lamports {prio} + {c0.BASE_FEE_LAMPORTS}")
        if kind == "buy":
            if prio != c0.PRIORITY_PIN["buy"]:
                raise TieoutMismatch(f"buy for {mint} signed at priority {prio}, pinned {c0.PRIORITY_PIN['buy']}")
            if landed:
                spend = r.get("spend_lamports")
                if mint in stakes and spend != stakes[mint]:
                    raise TieoutMismatch(f"landed buy for {mint} spent {spend}, the order's S is {stakes[mint]}")
                t["buy_fee_lamports"], t["stake_lamports"] = fee, spend
        else:
            if prio not in SELL_PRIORITIES:
                raise TieoutMismatch(f"sell for {mint} signed at priority {prio}, pinned {SELL_PRIORITIES}")
            if prio < c0.sell_priority(i):
                raise TieoutMismatch(f"sell attempt {i} for {mint} signed at {prio}, below the schedule {c0.sell_priority(i)}")
            t["sell_fee_lamports"] += fee
            t["sell_landed"] = t["sell_landed"] or bool(landed)
            t["sell_priorities"].append(prio)
    if parity_trips is not None:
        for mint, pt in parity_trips.items():
            t = per.get(mint)
            if t is None or t["buy_fee_lamports"] is None:
                raise TieoutMismatch(f"parity trip {mint} has no landed buy in the fill rows")
            for key, mine in (("buy_fee_lamports", t["buy_fee_lamports"]), ("stake_lamports", t["stake_lamports"])):
                if not _isint(pt.get(key)):  # a missing or non-integer field is a mismatch, never a pass (QP R1)
                    raise TieoutMismatch(f"parity trip {mint}: {key} is missing or not an integer")
                if pt[key] != mine:
                    raise TieoutMismatch(f"parity trip {mint}: {key} {pt.get(key)} != fill rows {mine}")
            if not isinstance(pt.get("exit_landed"), bool):
                raise TieoutMismatch(f"parity trip {mint}: exit_landed is missing or not a bool")
            if pt["exit_landed"] != t["sell_landed"]:
                raise TieoutMismatch(f"parity trip {mint}: exit_landed {pt['exit_landed']} but the fill rows have a landed sell: {t['sell_landed']}")
            if pt["exit_landed"]:
                if not _isint(pt.get("sell_fee_lamports")):
                    raise TieoutMismatch(f"parity trip {mint}: sell_fee_lamports is missing or not an integer")
                if pt["sell_fee_lamports"] != t["sell_fee_lamports"]:
                    raise TieoutMismatch(f"parity trip {mint}: sell_fee_lamports {pt['sell_fee_lamports']} != fill rows {t['sell_fee_lamports']}")
    return per


def cut_parity_trips(rows: list[Mapping[str, Any]], parity_trips: Mapping[str, Mapping[str, Any]], end_ms: int) -> dict[str, Mapping[str, Any]]:
    """The parity trips file is CUMULATIVE (the recorder's current view), the fill rows are cut at `end_ms`. Without this, a trip opened after the day's cut
    raised a false hard tieout_mismatch ("no landed buy in the fill rows"), and a trip whose sell landed after the cut raised "exit_landed True but no landed
    sell" (PR #1092 finding 1). The day's audit therefore sees each trip AS IT STOOD AT THE CUT, decided by the trip's own fill rows (all of them, uncut):
      * a trip whose landed buy rows ALL have ts_ms >= end_ms belongs to a later day and is left out of this day's cross-check;
      * a trip with exit_landed True whose landed sell rows ALL have ts_ms >= end_ms was still open at the cut: it is checked as exit_landed False.
    Fail closed: a trip with NO landed buy row anywhere stays in (the audit raises), and a trip with any landed buy or sell inside the cut is checked as recorded.
    A mint is never re-bought by the bridge (one trip per mint), so the mint keys the rows."""
    buys_in: set[str] = set()
    buys_after: set[str] = set()
    sells_in: set[str] = set()
    sells_after: set[str] = set()
    for r in rows:
        if not isinstance(r, Mapping) or r.get("kind") not in ("buy", "sell") or r.get("landed") is not True or not _isint(r.get("ts_ms")):
            continue
        before = r["ts_ms"] < end_ms
        if r["kind"] == "buy":
            (buys_in if before else buys_after).add(r.get("mint"))
        else:
            (sells_in if before else sells_after).add(r.get("mint"))
    out: dict[str, Mapping[str, Any]] = {}
    for mint, pt in parity_trips.items():
        if mint in buys_after and mint not in buys_in:
            continue
        if isinstance(pt, Mapping) and pt.get("exit_landed") is True and mint in sells_after and mint not in sells_in:
            pt = {**pt, "exit_landed": False}
        out[mint] = pt
    return out


def build(rows: Iterable[Mapping[str, Any]], state: Mapping[str, Any], end_ms: int, parity_trips: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """The LEDGER_IN document with the bridge block. Raises atl.BadLedger (unusable input) or TieoutMismatch (fee / stake audit)."""
    if not isinstance(state, Mapping) or not isinstance(state.get("orders"), dict):
        raise atl.BadLedger("the bridge state has no 'orders' object")
    rows = list(rows)
    fills = fill_rows(rows, end_ms)
    if parity_trips is not None:
        parity_trips = cut_parity_trips(rows, parity_trips, end_ms)
    orders = state["orders"]
    by_mint = {o["mint"]: o for o in orders.values() if isinstance(o, dict) and isinstance(o.get("mint"), str)}
    stakes = {m: o["stake_lamports"] for m, o in by_mint.items() if _isint(o.get("stake_lamports"))}
    fees = audit_fees(fills, stakes, parity_trips)
    exp = exporter_rows(fills)
    # open = a landed buy with no landed closing sell inside the cut. The exporter wants the live state's `open` mapping: derive it from the cut rows, so that a
    # rerun of a closed day gives the same document whatever the bridge did afterwards.
    landed_buys = {r["mint"] for r in fills if r["kind"] == "buy" and r.get("landed") is True and _isint(r.get("tokens_received")) and r["tokens_received"] > 0}
    closed = {r["mint"] for r in fills if r["kind"] == "sell" and r.get("landed") is True}
    core = atl.export(exp, {"open": {m: {} for m in sorted(landed_buys - closed)}})
    doc = {
        "schema": SCHEMA, "sends": core["sends"], "trips": core["trips"], "open_mints": core["open_mints"],
        "bridge": {
            "positions": sorted(({"order_id": k, "mint": o["mint"], "status": o.get("status"), "stake_lamports": o.get("stake_lamports")}
                                 for k, o in orders.items() if isinstance(o, dict) and o.get("status") in POSITION_STATUSES), key=lambda p: p["order_id"]),
            "fills": [{"kind": r["kind"], "mint": r["mint"], "signature": r.get("signature"), "landed": r["landed"], "fail_class": r.get("fail_class"),
                       "fee_lamports": r.get("fee_lamports"), "priority_lamports": r.get("priority_lamports"), "spend_lamports": r.get("spend_lamports"),
                       "sol_received_lamports": r.get("sol_received_lamports")} for r in fills if "landed" in r],
            "trip_fees": {m: {k: v for k, v in t.items()} for m, t in sorted(fees.items())},
            "latches": dict(sorted((state.get("latches") or {}).items())),
        },
    }
    return doc


def core_of(doc: Mapping[str, Any]) -> str:
    """What a rerun must reproduce: the ledger lists, the fills and the signed fees. Not the state-at-export blocks."""
    b = doc.get("bridge") or {}
    return json.dumps({"sends": doc.get("sends"), "trips": doc.get("trips"), "open_mints": doc.get("open_mints"), "fills": b.get("fills"), "trip_fees": b.get("trip_fees")},
                      sort_keys=True)


def ledger_name(day: str) -> str:
    return f"LEDGER-{day}.json"


def write_once(out_dir: str | Path, day: str, doc: Mapping[str, Any]) -> str:
    """Create LEDGER-<day>.json (O_EXCL, 0644). "created", "same" (identical core), or WriteConflict. Never overwrites."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / ledger_name(day)
    text = json.dumps({**doc, "day": day}, sort_keys=True, indent=1) + "\n"
    try:
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        try:
            old = json.loads(p.read_text())
        except (OSError, ValueError) as exc:
            raise WriteConflict(f"{p} exists and is unreadable ({type(exc).__name__}); not overwritten") from exc
        if isinstance(old, dict) and old.get("day") == day and core_of(old) == core_of(doc):
            return "same"
        raise WriteConflict(f"{p} exists with a different book; a rerun never overwrites it")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError:
        try:
            os.unlink(p)
        except OSError:
            pass
        raise
    return "created"


def closed_day_problem(day: str, now_ms: int) -> str | None:
    """A day is exported only once it has ended (a final snapshot)."""
    start, end = day_bounds(day)
    return None if now_ms >= end else f"day {day} has not ended at now_ms {now_ms}"


def read_trips(path: str | Path | None) -> dict[str, dict[str, Any]] | None:
    if path is None:
        return None
    out: dict[str, dict[str, Any]] = {}
    try:
        for n, line in enumerate(Path(path).read_text().splitlines(), 1):
            if line.strip():
                r = json.loads(line)
                if not isinstance(r, dict) or not isinstance(r.get("trip_id"), str):
                    raise atl.BadLedger(f"trips line {n} is not a trip")
                out[r["trip_id"]] = r
    except (OSError, ValueError):
        raise atl.BadLedger("the parity trips file cannot be read") from None
    return out


def export_day(fills_path: str | Path, state_path: str | Path, out_dir: str | Path, day: str, now_ms: int, trips_path: str | Path | None = None) -> str:
    """Read, build, write once. Returns "created" or "same". Raises atl.BadLedger, TieoutMismatch, WriteConflict."""
    why = closed_day_problem(day, now_ms)
    if why:
        raise atl.BadLedger(why)
    _, end = day_bounds(day)
    rows = atl.read_rows(Path(fills_path))
    try:
        state = json.loads(Path(state_path).read_text())
    except (OSError, ValueError):
        raise atl.BadLedger("the bridge state cannot be read") from None
    doc = build(rows, state, end, read_trips(trips_path))
    return write_once(out_dir, day, doc)


def main(argv: list[str] | None = None) -> int:
    import time
    ap = argparse.ArgumentParser(description="HV4 C0 bridge book -> daily tie-out LEDGER_IN (offline, write-once)", allow_abbrev=False)
    ap.add_argument("--fills", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--day", required=True)
    ap.add_argument("--trips")
    ap.add_argument("--now-ms", type=int)
    a = ap.parse_args(argv)
    now = a.now_ms if a.now_ms is not None else int(time.time() * 1000)
    try:
        r = export_day(a.fills, a.state, a.out_dir, a.day, now, a.trips)
    except (atl.BadLedger, OSError) as exc:
        print(f"hv4_c0_tieout REFUSED: {exc}", file=sys.stderr)
        return 2
    except TieoutMismatch as exc:
        print(f"hv4_c0_tieout MISMATCH: {exc}", file=sys.stderr)
        return 3
    except WriteConflict as exc:
        print(f"hv4_c0_tieout CONFLICT: {exc}", file=sys.stderr)
        return 4
    print(json.dumps({"day": a.day, "result": r}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
