"""r4bt_writes: the `--writes` PRODUCER for `tools/r4bt_a3.py classify` (QP-RULING-1102-r4bt Q2 item 2; QP on #1196 C2). PAPER ONLY. Read-only RPC. New file (r4bt_*).

`r4bt_a3 classify --writes W.json` needs the EARLIEST write of each program since the pinned slot, because the record's `deploy_utc` dates only the NEWEST write (on 10-19 the first
write was 15:46Z and deploy_utc said 17:47Z). This tool lists EVERY upgrade write of each differing program BACK TO THE PINNED SLOT: `a3_redeploy_check.list_upgrade_writes`
(imported, not copied; it pages the programdata signature list until the pinned slot is reached, else fails closed, and requires the newest enumerated write to equal the record's
deploy slot). There is no "since the last clean run" filter on purpose: the classifier takes the earliest, and a filter could hide the earliest write.

Reads from the A3 monitor's daily record ONLY `run_utc` and the structural fields `programs.<name>.{deploy_slot, deploy_utc, programdata}` and `accounts` (through `pins_diff`);
no halt flag, count or other field is read. Programs listed: pump, pumpswap, fees (the programs `r4bt_a3` dates by earliest write).

    python -m tools.r4bt_writes --daily DAILY.jsonl --monitor-pins PINS.json [--run-utc U] [--out W.json] [--rpc-url U | --rpc-env-file F [--rpc-var V]]

Output: the JSON list [{"name", "slot", "at_ms"}] oldest first (an empty list when no program differs), and ONE status line on stderr, always:
  r4bt_writes: STATUS=COMPLETE n_writes=N                          or
  r4bt_writes: STATUS=PARTIAL failed={"pump": "CheckError"} n_writes=N
Exit 0 for COMPLETE and for PARTIAL: a chained `r4bt_a3 classify` job runs only after an exit 0, and a missing program is safe (the classifier falls back to its earlier-or-equal
floor, the last clean run or the pinned instant, so the halt can only be earlier, never later; the status line is the loud part and the daily job's log keeps it). `--strict` makes
PARTIAL exit 3 instead (for a gate job that wants to stop). Exit 2: refused or unreadable input (nothing written).
NEEDS QP OK BEFORE FIRST USE (the reviewer's pin: the registration pins --writes and --last-clean-run-utc for the daily A3 job).
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools import a3_redeploy_check as ARC
from tools import pump_structure_monitor as mon
from tools.r4bt_decode_cli import CliRefused, _write_new, add_rpc_args, build_client

PROGRAMS = ("pump", "pumpswap", "fees")


def produce(rec: Mapping[str, Any], pins: Mapping[str, Any], client: Any) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """(writes, failed): writes = [{name, slot, at_ms}] oldest first, every write back to the pinned slot of each differing program; failed = {program: reason class}."""
    ARC.utc_ms(rec["run_utc"])  # a record without a valid run_utc is refused (ValueError/KeyError to the caller)
    out: list[dict[str, Any]] = []
    failed: dict[str, str] = {}
    for d in ARC.pins_diff(rec, pins):
        if d["kind"] != "program" or d["name"] not in PROGRAMS:
            continue
        pdata = ((rec.get("programs") or {}).get(d["name"]) or {}).get("programdata")
        try:
            if not pdata:
                raise ARC.CheckError("no programdata address in the record")
            ws = ARC.list_upgrade_writes(client, d["name"], pdata, d["old"], d["new"])
            if not ws or any(not (isinstance(w.get("new"), int) and w["new"] > d["old"]) for w in ws) or max(w["new"] for w in ws) != d["new"]:
                raise ARC.CheckError("enumeration does not span the pinned slot to the record's slot")
            out += [{"name": d["name"], "slot": int(w["new"]), "at_ms": ARC.utc_ms(w["deploy_utc"])} for w in ws]
        except Exception as exc:  # noqa: BLE001 - fail closed per program: absent from the list, named in `failed` (class only)
            failed[d["name"]] = type(exc).__name__
    return sorted(out, key=lambda w: (w["at_ms"], w["name"], w["slot"])), failed


def main(argv: Sequence[str] | None = None, *, client: Any = None) -> int:
    ap = argparse.ArgumentParser(description="r4-b-T A3 --writes producer.")
    ap.add_argument("--daily", required=True)
    ap.add_argument("--monitor-pins", required=True)
    ap.add_argument("--run-utc", default=None, help="the record to read (default: the newest)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--strict", action="store_true", help="exit 3 on a PARTIAL result (default: exit 0 with the STATUS line, so a chained classify job still runs)")
    add_rpc_args(ap)
    a = ap.parse_args(argv)
    try:
        recs = ARC.read_records(Path(a.daily))
        rec = next((r for r in recs if r.get("run_utc") == a.run_utc), None) if a.run_utc else (recs[-1] if recs else None)
        if rec is None:
            raise CliRefused("no such record in the daily file")
        pins = mon.load_pins(Path(a.monitor_pins))
        writes, failed = produce(rec, pins, client or build_client(a, min_interval=0.2, max_calls=300))
        text = json.dumps(writes, sort_keys=True)
        if a.out:
            _write_new(a.out, writes)
        print(text)
        if failed:
            print(f"r4bt_writes: STATUS=PARTIAL failed={json.dumps(failed, sort_keys=True)} n_writes={len(writes)}", file=sys.stderr)
            return 3 if a.strict else 0
        print(f"r4bt_writes: STATUS=COMPLETE n_writes={len(writes)}", file=sys.stderr)
        return 0
    except (CliRefused, ARC.CheckError, OSError, ValueError, KeyError) as exc:
        print(f"r4bt_writes: REFUSED: {type(exc).__name__}: {exc if isinstance(exc, CliRefused) else ''}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
