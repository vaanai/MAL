#!/usr/bin/env python3
"""dbc_m10_decode_check: DECODE-ONLY check of recorded DBC tape hours (QP-RULING-1024-read-hardening.md item 6, the only use allowed on the SPENT hours
[2026-10-17T00, 2026-10-24T00)). PAPER ONLY. Stdlib + the zstd CLI. Imports no pricing, screen, cell or engine code.

    python3 tools/dbc_m10_decode_check.py --root /data2/mal/venue-tape-backfill/meteora_dbc_holdout --start 2026-10-17T00 --end 2026-10-24T00 --out DIR

Per hour it prints COUNTS ONLY:
* file present, end row present, gap row present, incomplete (= gap row or no end row, dbc_price.read_hour's rule);
* row types; trade event names (and any not in the known EvtSwap* set); event_undecoded reasons (unknown / undecodable) and their event names;
* EvtSwap3 field presence: Swap3 trades, Swap3 buys, Swap3 buys with fee_on_base_token present, Swap3 trades without quote_reserve_post / config / quote_mint,
  trades without swap-instruction context (ctx_missing), SOL-quoted trades, transfer-hook rows, failed rows.
It prints no price, amount, reserve, pool, trigger, cell, decision or P&L. Refuses any hour outside [2026-10-14T00, 2026-10-24T00) and any root naming the forward tape.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

MIN_H = datetime(2026, 10, 14, 0, tzinfo=timezone.utc)
MAX_H = datetime(2026, 10, 24, 0, tzinfo=timezone.utc)  # exclusive; hours >= 2026-10-25T00 are never read
FORBIDDEN_ROOT_PARTS = ("fwdv1",)
WSOL = "So11111111111111111111111111111111111111112"
KNOWN_TRADE_EVENTS = {"EvtSwap", "EvtSwap2", "EvtSwap2WithTransferHook", "EvtSwap3", "EvtSwap3WithTransferHook"}


class Refused(Exception):
    pass


def parse_h(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)


def guard(root: str, start: datetime, end: datetime) -> None:
    if not (MIN_H <= start < end <= MAX_H):
        raise Refused(f"hours [{start:%Y-%m-%dT%H}, {end:%Y-%m-%dT%H}) not inside [2026-10-14T00, 2026-10-24T00)")
    for p in (root, os.path.realpath(root)):
        if any(x in p for x in FORBIDDEN_ROOT_PARTS):
            raise Refused(f"root refused: {root}")


def lines_of(path: str) -> Iterable[bytes]:
    p = subprocess.Popen(["zstd", "-dc", "-q", path], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    assert p.stdout is not None
    try:
        yield from p.stdout
    finally:
        p.stdout.close()
        rc = p.wait()
    if rc != 0:
        raise RuntimeError(f"zstd -dc exited {rc}")


def check_hour(path: str) -> dict[str, Any]:
    out: dict[str, Any] = {"present": os.path.exists(path)}
    if not out["present"]:
        return out
    types: Counter = Counter()
    events: Counter = Counter()
    undec: Counter = Counter()
    f: Counter = Counter()
    end = gap = False
    bad_json = 0
    for line in lines_of(path):
        try:
            r = json.loads(line)
        except ValueError:
            bad_json += 1
            continue
        t = str(r.get("type"))
        types[t] += 1
        if t == "backfill_hour_end":
            end = True
        elif t == "gap" or r.get("reason") == "backfill_incomplete":
            gap = True
        elif t == "event_undecoded":
            undec[f"{r.get('reason')}:{r.get('event') or ''}"] += 1
        elif t == "trade":
            ev = str(r.get("event"))
            events[ev] += 1
            if r.get("failed"):
                f["failed_trades"] += 1
            if r.get("ctx_missing"):
                f["ctx_missing"] += 1
            if r.get("quote_mint") == WSOL:
                f["sol_quoted"] += 1
            if "TransferHook" in ev or "TransferHook" in str(r.get("ix") or ""):
                f["transfer_hook"] += 1
            if ev.startswith("EvtSwap3"):
                f["sw3"] += 1
                if r.get("side") == "buy":
                    f["sw3_buy"] += 1
                    if r.get("fee_on_base_token") is not None:
                        f["sw3_buy_fob_present"] += 1
                for k in ("quote_reserve_post", "config", "quote_mint"):
                    if r.get(k) is None:
                        f[f"sw3_missing_{k}"] += 1
    out.update({"end_row": end, "gap_row": gap, "incomplete": gap or not end, "bad_json_lines": bad_json, "row_types": dict(sorted(types.items())),
                "trade_events": dict(sorted(events.items())), "unknown_trade_events": sorted(e for e in events if e not in KNOWN_TRADE_EVENTS),
                "event_undecoded": dict(sorted(undec.items())), "fields": dict(sorted(f.items()))})
    return out


def run(root: str, start: datetime, end: datetime) -> dict[str, Any]:
    guard(root, start, end)
    hours: dict[str, Any] = {}
    tot: dict[str, Counter] = {"row_types": Counter(), "trade_events": Counter(), "event_undecoded": Counter(), "fields": Counter()}
    h = start
    while h < end:
        lab = h.strftime("%Y-%m-%dT%H")
        r = check_hour(os.path.join(root, f"tape-{lab}.jsonl.zst"))
        hours[lab] = r
        for k in tot:
            tot[k].update(r.get(k) or {})
        print(lab, json.dumps(r, sort_keys=True), flush=True)
        h += timedelta(hours=1)
    summ = {k: dict(sorted(v.items())) for k, v in tot.items()}
    summ.update({"hours": len(hours), "missing": sorted(k for k, v in hours.items() if not v["present"]),
                 "incomplete": sorted(k for k, v in hours.items() if v.get("incomplete")),
                 "unknown_trade_events": sorted({e for v in hours.values() for e in v.get("unknown_trade_events", [])})})
    return {"schema": "dbc_decode_check_v1", "root": root, "window": [f"{start:%Y-%m-%dT%H}", f"{end:%Y-%m-%dT%H}"], "hours": hours, "summary": summ,
            "taken_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "note": "DECODE-ONLY counts (QP-RULING-1024-read-hardening item 6). No price, cell, trigger, engine or decision."}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", required=True)
    ap.add_argument("--start", default="2026-10-17T00")
    ap.add_argument("--end", default="2026-10-24T00")
    ap.add_argument("--out", required=True)
    ns = ap.parse_args(argv)
    try:
        guard(ns.root, parse_h(ns.start), parse_h(ns.end))
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 3
    out = Path(ns.out)
    if any(x in os.path.realpath(ns.out) for x in FORBIDDEN_ROOT_PARTS) or (out.exists() and any(out.iterdir())):
        print(f"REFUSED: out dir not empty or under the forward tree: {out}", file=sys.stderr)
        return 4
    out.mkdir(parents=True, exist_ok=True)
    res = run(ns.root, parse_h(ns.start), parse_h(ns.end))
    (out / "decode-check.json").write_text(json.dumps(res, indent=1, sort_keys=True))
    print("SUMMARY", json.dumps(res["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
