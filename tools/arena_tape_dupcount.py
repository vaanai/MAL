#!/usr/bin/env python3
"""Arena-tape duplicate-row COUNT (structure only). Paper only. stdlib only. No price, no pool, no mint, no wallet in the output.

Allowed by ops/manager18/QP-RULING-1031-kcurve-dupes.md section 3 ("a count-only check by a pinned script, run as a MiScusi job, over
closed arena-tape hours; it outputs per hour only: rows, repeated (signature, event_index) rows, and trade-identical repeats").

What it counts, per CLOSED hour file of the arena tape (tools/arena_tape_schema.md; `tape-<HOUR>.jsonl.zst`, or a `.jsonl` whose hour
ended more than 120 s ago; the `.jsonl` wins while both exist, as the schema says):
  lines        every line of the file
  bad_lines    lines that are not a JSON object
  trades       rows with type == "trade"
  rep_key      trade rows whose (signature, event_index) already appeared on an EARLIER trade row of the same hour file
  rep_trade    of rep_key: rows identical to that earlier row on the QP trade fields (QP_FIELDS below; QP ruling section 2 rule text,
               mapped to the arena tape, which has no tx_index or block_time) -- the re-delivered duplicates
  rep_strict   of rep_key: rows identical on EVERY key except the receive-time field (t_recv_ms)
  xh_key       trade rows whose key is not earlier in this hour but is in the PREVIOUS hour file (cross-hour re-delivery)
  xh_trade     of xh_key: identical on QP_FIELDS
A key repeat that is not rep_trade is a key collision (fields differ) and is NOT a duplicate (QP ruling, condition 1, second sentence).

Output: one TSV line per hour (hour name and the counts above), then per --window the sums and shares. Nothing else.
Run: python3 -I tools/arena_tape_dupcount.py --tape-dir /data/mal/arena/tape --workers 8 --window NAME=FROM_HOUR,TO_HOUR_EXCL ...
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from multiprocessing import Pool

QP_FIELDS = ("prog", "venue", "pool_id", "slot", "event_index", "side", "sol_lamports", "token_raw", "quote_reserve", "base_reserve",
             "trader", "event_ts")
# pool_id = `pool` when set (PumpSwap state key), else `mint` (bonding-curve state key; `pool` is null there).
RECV_FIELDS = ("t_recv_ms",)
COUNT_KEYS = ("lines", "bad_lines", "trades", "rep_key", "rep_trade", "rep_strict", "xh_key", "xh_trade")
HOUR_RE = re.compile(r"^tape-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl(\.zst)?$")
CLOSE_GRACE_S = 120


def hour_dt(h: str) -> datetime:
    return datetime.strptime(h, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)


def closed_hours(tape_dir: str, now: datetime) -> dict[str, str]:
    """hour -> path, closed hours only. A .jsonl wins over a .zst of the same hour (schema: readers use the .jsonl while both exist)."""
    found: dict[str, dict[str, str]] = {}
    for name in os.listdir(tape_dir):
        m = HOUR_RE.match(name)
        if m:
            found.setdefault(m.group(1), {})["zst" if m.group(2) else "jsonl"] = os.path.join(tape_dir, name)
    out = {}
    for h, d in found.items():
        if hour_dt(h) + timedelta(hours=1, seconds=CLOSE_GRACE_S) >= now:
            continue
        out[h] = d.get("jsonl") or d["zst"]
    return dict(sorted(out.items()))


def _lines(path: str):
    if path.endswith(".zst"):
        p = subprocess.Popen(["zstd", "-dc", "-q", path], stdout=subprocess.PIPE, bufsize=1 << 20)
        try:
            yield from p.stdout
        finally:
            p.stdout.close()
            if p.wait() != 0:
                raise RuntimeError(f"zstd failed on {os.path.basename(path)}")
    else:
        with open(path, "rb") as f:
            yield from f


def row_sigs(d: dict) -> tuple[int, int]:
    pool_id = d.get("pool") if d.get("pool") is not None else d.get("mint")
    qp = hash(tuple(pool_id if k == "pool_id" else d.get(k) for k in QP_FIELDS))
    strict = hash(tuple(sorted((k, repr(v)) for k, v in d.items() if k not in RECV_FIELDS)))
    return qp, strict


def scan(path: str, prev: dict | None) -> tuple[dict, dict]:
    """Count one hour file. Returns (counts, key -> (qp_hash, strict_hash) of its first occurrence) for the next hour's cross check."""
    c = dict.fromkeys(COUNT_KEYS, 0)
    seen: dict[str, tuple[int, int]] = {}
    for raw in _lines(path):
        c["lines"] += 1
        if b'"type":"trade"' not in raw:
            try:
                if not isinstance(json.loads(raw), dict):
                    c["bad_lines"] += 1
            except ValueError:
                c["bad_lines"] += 1
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            c["bad_lines"] += 1
            continue
        if not isinstance(d, dict) or d.get("type") != "trade":
            continue
        c["trades"] += 1
        key = f'{d.get("signature")}|{d.get("event_index")}'
        sig = row_sigs(d)
        first = seen.get(key)
        if first is not None:
            c["rep_key"] += 1
            c["rep_trade"] += sig[0] == first[0]
            c["rep_strict"] += sig[1] == first[1]
            continue
        seen[key] = sig
        if prev is not None:
            p = prev.get(key)
            if p is not None:
                c["xh_key"] += 1
                c["xh_trade"] += sig[0] == p[0]
    return c, seen


def scan_chunk(args: tuple[list[tuple[str, str]], str | None]) -> list[tuple[str, dict]]:
    """Contiguous hours in order; the hour before the chunk (if any) is read first only to seed the cross-hour check."""
    hours, prev_path = args
    prev = scan(prev_path, None)[1] if prev_path else None
    out = []
    last_h = None
    for h, path in hours:
        if last_h is not None and hour_dt(h) != hour_dt(last_h) + timedelta(hours=1):
            prev = None  # missing hour file: no cross-hour neighbour
        c, seen = scan(path, prev)
        out.append((h, c))
        prev, last_h = seen, h
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tape-dir", default="/data/mal/arena/tape")
    ap.add_argument("--from-hour", default=None, help="first hour to count, YYYY-MM-DDTHH (default: all closed)")
    ap.add_argument("--to-hour", default=None, help="exclusive end hour, YYYY-MM-DDTHH")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--window", action="append", default=[], help="NAME=FROM_HOUR,TO_HOUR_EXCL (summary only; hours as above)")
    ap.add_argument("--line", type=float, default=0.001, help="share line for the flag column (QP: 0.1%%)")
    a = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    allh = closed_hours(a.tape_dir, now)
    hours = [(h, p) for h, p in allh.items() if (a.from_hour is None or h >= a.from_hour) and (a.to_hour is None or h < a.to_hour)]
    names = list(allh)
    print(f"# arena_tape_dupcount run_utc={now.strftime('%Y-%m-%dT%H:%M:%SZ')} closed_hours_selected={len(hours)} "
          f"first={hours[0][0] if hours else '-'} last={hours[-1][0] if hours else '-'}")
    print("# key=(signature,event_index) on type=trade rows; QP_FIELDS=" + ",".join(QP_FIELDS) + "; strict=all keys except " + ",".join(RECV_FIELDS))
    n = max(1, min(a.workers, len(hours)))
    size = -(-len(hours) // n) if hours else 0
    chunks = []
    for i in range(0, len(hours), size or 1):
        part = hours[i:i + size]
        j = names.index(part[0][0])
        prev_path = None
        if j > 0 and hour_dt(names[j - 1]) + timedelta(hours=1) == hour_dt(part[0][0]):
            prev_path = allh[names[j - 1]]
        chunks.append((part, prev_path))
    t0 = time.time()
    with Pool(n) as pool:
        results = [r for chunk in pool.map(scan_chunk, chunks) for r in chunk]
    print("hour\t" + "\t".join(COUNT_KEYS) + "\trep_trade_share\tall_dup_share\tflag")
    per = {}
    for h, c in results:
        per[h] = c
        share = c["rep_trade"] / c["trades"] if c["trades"] else 0.0
        alls = (c["rep_trade"] + c["xh_trade"]) / c["trades"] if c["trades"] else 0.0
        print(f"{h}\t" + "\t".join(str(c[k]) for k in COUNT_KEYS) + f"\t{share:.6%}\t{alls:.6%}\t{'OVER' if max(share, alls) > a.line else 'ok'}")
    print("window\tfrom\tto_excl\thours_present\thours_expected\t" + "\t".join(COUNT_KEYS) + "\trep_trade_share\tall_dup_share\tmax_hour_all_share\tmax_hour\tflag")
    for w in a.window:
        name, rng = w.split("=", 1)
        lo, hi = rng.split(",")
        hs = [h for h in per if lo <= h < hi]
        exp = int((hour_dt(hi) - hour_dt(lo)).total_seconds() // 3600)
        tot = {k: sum(per[h][k] for h in hs) for k in COUNT_KEYS}
        share = tot["rep_trade"] / tot["trades"] if tot["trades"] else 0.0
        alls = (tot["rep_trade"] + tot["xh_trade"]) / tot["trades"] if tot["trades"] else 0.0
        mx, mxh = 0.0, "-"
        for h in hs:
            s = (per[h]["rep_trade"] + per[h]["xh_trade"]) / per[h]["trades"] if per[h]["trades"] else 0.0
            if s > mx:
                mx, mxh = s, h
        flag = "OVER" if max(share, alls, mx) > a.line else "ok"
        print(f"{name}\t{lo}\t{hi}\t{len(hs)}\t{exp}\t" + "\t".join(str(tot[k]) for k in COUNT_KEYS) + f"\t{share:.6%}\t{alls:.6%}\t{mx:.6%}\t{mxh}\t{flag}")
    print(f"# elapsed_s={time.time() - t0:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
