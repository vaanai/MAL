"""hv4_c0_read_check: the check a C0 read must pass before anyone writes "parity shown" (QP #970 ruling 1; QP #959 comment 6423545454 removal precondition 14).

    python -m tools.hv4_c0_read_check --parity-dir DIR [--result F] [--state F] [--trips F] [--parity-rc N] [--bridge-log F] [--bridge-state F] [--json OUT]

PARITY. The parity tool (tools/arena_live_parity.py) exits 0 for a stake or fee mismatch: the bridge latches `stake_mismatch` / `tieout_mismatch` and the bridge's
state.json carries them in `unresolved_hard_halts`, which makes `match` false but leaves the tool's exit code at 0 (QP #970 ruling 1). So an exit code of 0 is NEVER read
as clear here. This check exits 0 only when ALL of these hold:
  * the parity result file (`parity-result.json`, the tool's `--json` output) exists, `match` is the boolean true, `verdict` is "LIVE FILLS MATCH PAPER", and its own
    `unresolved_hard_halts` and `hard_halts` are present and empty lists;
  * the bridge's parity state file (`state.json`) has `unresolved_hard_halts` present and equal to [] (a missing key, a non-list or a non-empty list is "not shown");
  * the result covers the trips file as it stands now (`shares.trips` equals the number of lines in trips.jsonl), so a result from before later trips is not accepted;
  * when `--parity-rc` is given it is 0 (a non-zero rc is "not shown"; a zero rc adds nothing);
  * when `--bridge-state` is given, none of the bridge's hard latches (tools.hv4_c0_parity.HARD_LATCHES) is set in it (a latch raised after the last parity run).
Anything else prints "parity not shown: <reasons>" and exits 2. Unreadable, linked, oversize or malformed inputs are "not shown". Soft halts in the result are printed as notes.

BACKSTOP CLOSES. A trade the bridge closed with its OWN time stop or stop-loss backstop is not the book's exit: the paper twin exited by the book's rule, the live trade by the
bridge's. Every such trade is printed as `BACKSTOP_FLAG` and the read must carry them apart from book-exit trades. They come from the parity trips file (`exit_reason` is
`time_stop_backstop` or `sl_backstop`), the bridge log (`sell_sent` / `sell_port_refused` with that reason: attempts, also those that never landed) and the bridge state
(`orders[].sell_reason`). For hv4_c0 the bridge logs `runner_silent_ms` on every sell (ms since the newest decision row it had consumed, or since its own start; the same
base as the `runner_silent` rule, so a value above tools.hv4_live_c0.RUNNER_SILENT_MS is a runner silence by the bridge's own definition). Each flag says over / under that
bound, or `unknown` when the log has no such field (a log from before the field, or no log given): unknown is flagged, never dropped. With a flag and parity shown the exit is 3.

EXIT CODES  0 parity shown and no backstop-closed trade   2 parity not shown (or an input unusable)   3 parity shown, backstop-closed trade(s) flagged.
Stdlib only (the hard-latch list is imported lazily and only for --bridge-state). Reads only the files named; refuses any path under the sealed arena prefix as a string.
It prints no P&L, price or fee. Nothing here says a book makes money. No book has cleared the promotion gate.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Mapping

from tools import hv4_live_c0 as c0

SCHEMA = "hv4_c0_read_check_v1"
BACKSTOP_REASONS = ("time_stop_backstop", "sl_backstop")  # the two strings Bridge.backstops passes to _try_exit (a test scans the bridge source for them)
VERDICT_MATCH = "LIVE FILLS MATCH PAPER"  # tools.arena_live_parity.evaluate
RESULT_NAME, STATE_NAME, TRIPS_NAME = "parity-result.json", "state.json", "trips.jsonl"  # tools.h5_arena_gates.LiveGates / ParityRecorder file names
RC_CLEAR, RC_NOT_SHOWN, RC_BACKSTOP = 0, 2, 3
MAX_BYTES = 64 * 1024 * 1024
SEALED_PREFIX = "/data/mal/arena"


def path_refusal(path: str) -> str | None:
    p = os.path.abspath(path)
    return f"path_under_sealed_arena:{p}" if p == SEALED_PREFIX or p.startswith(SEALED_PREFIX + "/") else None


def _read_bytes(path: str) -> tuple[bytes | None, str | None]:
    """(data, problem). A link, a non-regular file, an oversize file or an unreadable one is a problem."""
    why = path_refusal(path)
    if why:
        return None, why
    try:
        ls = os.lstat(path)
    except FileNotFoundError:
        return None, "missing"
    except OSError as exc:
        return None, f"unreadable:{type(exc).__name__}"
    if stat.S_ISLNK(ls.st_mode) or not stat.S_ISREG(ls.st_mode):
        return None, "not_a_regular_file"
    if ls.st_size > MAX_BYTES:
        return None, "oversize"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as fh:
            return fh.read(MAX_BYTES + 1), None
    except OSError as exc:
        return None, f"unreadable:{type(exc).__name__}"


def read_json(path: str) -> tuple[Any, str | None]:
    data, why = _read_bytes(path)
    if why:
        return None, why
    try:
        return json.loads(data or b""), None
    except ValueError:
        return None, "malformed_json"


def read_jsonl(path: str) -> tuple[list[dict[str, Any]], str | None]:
    """Every non-empty line must be a JSON object; a torn last line (no newline) is a problem too, not silently dropped."""
    data, why = _read_bytes(path)
    if why:
        return [], why
    out: list[dict[str, Any]] = []
    for ln in (data or b"").split(b"\n"):
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            return out, "malformed_line"
        if not isinstance(r, dict):
            return out, "line_not_an_object"
        out.append(r)
    return out, None


def _empty_list(v: Any) -> bool:
    return isinstance(v, list) and v == []


def parity_reasons(result: Any, state: Any, trips_count: int | None, rc: int | None, bridge_state: Any = None) -> tuple[list[str], list[str]]:
    """(reasons parity is not shown, notes). No reason means shown. An absent or malformed input is a reason."""
    why: list[str] = []
    notes: list[str] = []
    if rc is not None and rc != 0:
        why.append(f"parity tool rc {rc}")
    if not isinstance(result, dict):
        why.append("parity result missing, unreadable or not an object")
    else:
        if result.get("match") is not True:
            why.append(f"result match is {result.get('match')!r}, not true" + (f" ({'; '.join(map(str, result['not_shown_because']))})" if isinstance(result.get("not_shown_because"), list) and result["not_shown_because"] else ""))
        if result.get("verdict") != VERDICT_MATCH:
            why.append(f"result verdict is {result.get('verdict')!r}")
        for key in ("unresolved_hard_halts", "hard_halts"):
            if key not in result:
                why.append(f"result has no {key}")
            elif not _empty_list(result[key]):
                why.append(f"result {key} is {result[key]!r}, not []")
        shares = result.get("shares")
        n_res = shares.get("trips") if isinstance(shares, dict) else None
        if isinstance(n_res, bool) or not isinstance(n_res, int):
            why.append("result has no integer shares.trips")
        elif trips_count is None:
            why.append("trips file not readable: the result's coverage cannot be checked")
        elif n_res != trips_count:
            why.append(f"result covers {n_res} trips, the trips file has {trips_count}: run the parity tool again")
        soft = result.get("soft_halts")
        if isinstance(soft, list) and soft:
            notes.append(f"soft halts in the result: {soft}")
    if not isinstance(state, dict):
        why.append("parity state file missing, unreadable or not an object")
    elif "unresolved_hard_halts" not in state:
        why.append("state has no unresolved_hard_halts")
    elif not _empty_list(state["unresolved_hard_halts"]):
        why.append(f"state unresolved_hard_halts is {state['unresolved_hard_halts']!r}, not []")
    if bridge_state is not None:
        if not isinstance(bridge_state, dict) or not isinstance(bridge_state.get("latches"), dict):
            why.append("bridge state unreadable or has no latches object")
        else:
            try:
                from tools.hv4_c0_parity import HARD_LATCHES  # lazy: pulls numpy through the gates module
            except Exception as exc:  # noqa: BLE001 - a missing list is never a pass
                why.append(f"cannot load the hard latch list ({type(exc).__name__})")
            else:
                hard = sorted(set(bridge_state["latches"]) & set(HARD_LATCHES))
                if hard:
                    why.append(f"bridge state has hard latch(es) {hard}")
                soft_l = sorted(set(bridge_state["latches"]) - set(HARD_LATCHES))
                if soft_l:
                    notes.append(f"bridge state has non-parity latch(es) {soft_l}")
    return why, notes


def collect_backstops(trips: list[dict[str, Any]], log: list[dict[str, Any]] | None, bridge_state: Any) -> list[dict[str, Any]]:
    """One record per mint with any backstop evidence. closed = the trade ended through a backstop sell (trips exit_reason, a landed sell_landed row or a closed order whose
    sell_reason is a backstop). runner_silent_ms is taken from the FIRST backstop attempt in the log; None when that row has no integer field."""
    recs: dict[str, dict[str, Any]] = {}

    def rec(mint: str) -> dict[str, Any]:
        return recs.setdefault(mint, {"mint": mint, "order_ids": [], "reasons": [], "closed": False, "attempts": 0, "runner_silent_ms": None, "sources": []})

    def add(r: dict[str, Any], key: str, v: Any) -> None:
        if v is not None and v not in r[key]:
            r[key].append(v)

    for t in trips:
        if t.get("exit_reason") in BACKSTOP_REASONS and isinstance(t.get("trip_id"), str):
            r = rec(t["trip_id"])
            r["closed"] = r["closed"] or t.get("exit_landed") is True
            add(r, "reasons", t["exit_reason"])
            add(r, "sources", "trips")
    order_mint: dict[str, str] = {}
    first_seen: set[str] = set()
    for row in log or []:
        oid, mint, reason = row.get("order_id"), row.get("mint"), row.get("reason")
        if isinstance(oid, str) and isinstance(mint, str):
            order_mint[oid] = mint
        if row.get("kind") in ("sell_sent", "sell_port_refused") and reason in BACKSTOP_REASONS and isinstance(mint, str):
            r = rec(mint)
            r["attempts"] += 1
            add(r, "order_ids", oid)
            add(r, "reasons", reason)
            add(r, "sources", "bridge_log")
            if mint not in first_seen:
                first_seen.add(mint)
                ms = row.get("runner_silent_ms")
                r["runner_silent_ms"] = ms if isinstance(ms, int) and not isinstance(ms, bool) else None
        elif row.get("kind") == "sell_landed" and reason in BACKSTOP_REASONS and isinstance(oid, str) and oid in order_mint:
            r = rec(order_mint[oid])
            r["closed"] = True
            add(r, "reasons", reason)
    orders = bridge_state.get("orders") if isinstance(bridge_state, dict) else None
    if isinstance(orders, dict):
        for oid, o in orders.items():
            if isinstance(o, dict) and o.get("sell_reason") in BACKSTOP_REASONS and isinstance(o.get("mint"), str):
                r = rec(o["mint"])
                r["closed"] = r["closed"] or o.get("status") == "closed"
                add(r, "order_ids", oid)
                add(r, "reasons", o["sell_reason"])
                add(r, "sources", "bridge_state")
    for r in recs.values():
        ms = r["runner_silent_ms"]
        r["vs_bound"] = "unknown" if ms is None else ("over" if ms > c0.RUNNER_SILENT_MS else "under")
    return [recs[m] for m in sorted(recs)]


def render_flag(r: Mapping[str, Any]) -> str:
    ms = r["runner_silent_ms"]
    return (f"BACKSTOP_FLAG mint={r['mint']} order_ids={','.join(r['order_ids']) or 'unknown'} reason={','.join(r['reasons'])} closed_by_backstop={'yes' if r['closed'] else 'no'} "
            f"attempts={r['attempts']} runner_silent_ms={'unknown' if ms is None else ms} vs_{c0.RUNNER_SILENT_MS}ms_bound={r['vs_bound']} sources={','.join(r['sources'])}")


def check(parity_dir: str | None, result_path: str | None, state_path: str | None, trips_path: str | None, rc: int | None, log_path: str | None,
          bridge_state_path: str | None) -> tuple[int, list[str], dict[str, Any]]:
    """Run the whole check; returns (exit code, output lines, record)."""
    base = Path(parity_dir) if parity_dir else None
    result_path = result_path or (str(base / RESULT_NAME) if base else None)
    state_path = state_path or (str(base / STATE_NAME) if base else None)
    trips_path = trips_path or (str(base / TRIPS_NAME) if base else None)
    problems: list[str] = []

    def load(label: str, path: str | None, jsonl: bool) -> Any:
        if not path:
            problems.append(f"{label}: no path given")
            return None
        val, why = read_jsonl(path) if jsonl else read_json(path)
        if why:
            problems.append(f"{label} {path}: {why}")
            return None
        return val

    result = load("parity result", result_path, False)
    state = load("parity state", state_path, False)
    trips = load("trips", trips_path, True)
    bridge_state = load("bridge state", bridge_state_path, False) if bridge_state_path else None
    log = load("bridge log", log_path, True) if log_path else None
    why, notes = parity_reasons(result, state, None if trips is None else len(trips), rc, bridge_state if bridge_state_path else None)
    why = problems + why
    flags = collect_backstops(trips or [], log, bridge_state)
    lines = ["HV4 C0 READ CHECK (execution parity only; not gate evidence; no book has cleared the promotion gate)",
             f"inputs: result={result_path} state={state_path} trips={trips_path} bridge_log={log_path or 'not given'} bridge_state={bridge_state_path or 'not given'} parity_rc={rc if rc is not None else 'not given'}",
             "an exit code of 0 from the parity tool is never read as clear: the result's match and every unresolved_hard_halts list are checked"]
    if why:
        lines.append("parity not shown: " + "; ".join(why))
    else:
        lines.append(f"PARITY SHOWN: {VERDICT_MATCH}; match true; unresolved_hard_halts [] in the result and in the state")
    lines += [f"NOTE {n}" for n in notes]
    if log_path is None:
        lines.append("NOTE no bridge log given: backstop flags come from the trips file and the bridge state only, and runner silence is unknown")
    lines += [render_flag(r) for r in flags]
    lines.append(f"backstop_flagged_trades={len(flags)} (a flag means the live exit was the bridge's backstop, not the book's rule; carry these apart from book-exit trades in the read)")
    code = RC_NOT_SHOWN if why else RC_BACKSTOP if flags else RC_CLEAR
    lines.append({RC_CLEAR: "RESULT CLEAR (parity shown; no backstop-closed trade)", RC_NOT_SHOWN: "RESULT NOT CLEAR: parity not shown (exit 2)",
                  RC_BACKSTOP: "RESULT PARITY SHOWN, BACKSTOP-CLOSED TRADES FLAGGED (exit 3)"}[code])
    record = {"schema": SCHEMA, "exit_code": code, "parity_shown": not why, "parity_not_shown_because": why, "notes": notes, "backstop_flags": flags}
    return code, lines, record


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], allow_abbrev=False)
    ap.add_argument("--parity-dir", help="the bridge's parity_dir: defaults for --result, --state and --trips")
    ap.add_argument("--result", help=f"the parity tool's --json output (default <parity-dir>/{RESULT_NAME})")
    ap.add_argument("--state", help=f"the bridge's parity state file (default <parity-dir>/{STATE_NAME})")
    ap.add_argument("--trips", help=f"the parity trips file (default <parity-dir>/{TRIPS_NAME})")
    ap.add_argument("--parity-rc", type=int, help="the exit code the parity tool returned, if known (non-zero blocks; zero alone never clears)")
    ap.add_argument("--bridge-log", help="bridge-log-<mode>.jsonl")
    ap.add_argument("--bridge-state", help="bridge-state-<mode>.json")
    ap.add_argument("--json", help="write the record here")
    a = ap.parse_args(argv)
    if not (a.parity_dir or (a.result and a.state and a.trips)):
        ap.error("give --parity-dir, or all of --result, --state and --trips")
    code, lines, record = check(a.parity_dir, a.result, a.state, a.trips, a.parity_rc, a.bridge_log, a.bridge_state)
    print("\n".join(lines))
    if a.json:
        try:
            Path(a.json).write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
        except OSError as exc:
            print(f"JSON NOT WRITTEN ({exc}); the lines above are the only copy")
    return code


if __name__ == "__main__":
    sys.exit(main())
