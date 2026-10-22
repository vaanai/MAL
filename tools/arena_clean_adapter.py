#!/usr/bin/env python3
"""arena_clean_adapter: write the hours of a clean-view block (August / September exploration views) as arena-format `tape-<hour>.jsonl.zst` files, so that
tools/arena_port_check (#765) can replay the very hours a rule was screened on through both the batch kit and the streaming host. PAPER only. No key, no RPC, no network.

    python -m tools.arena_clean_adapter --block /data/mal/clean-view/<block>[/w1 ...] --out DIR [--lookback /data/mal/clean-view/<other block> ...]

WHAT IT WRITES (entry-decision material only; nothing is priced)
  * trades: the WSOL-quoted PumpSwap rows (`venue` pumpswap, `quote_is_wsol` true, pool, slot, side). Every other row (bonding-curve trades, non-WSOL pools) is dropped and counted:
    neither the kit's `build_hour` nor an arena rule looks at them. Fields kept: type, prog, venue, slot, t_recv_ms, signature, event_index, mint, trader, side, sol_lamports,
    token_raw, quote_reserve, base_reserve, quote_mint (WSOL), pool, event_ts (= block_time, else event_ts: the kit's bt), virtual_quote_reserves.
  * V: the views carry none; `virtual_quote_reserves` is the kit's pool map value (tools.arena_screen.VMAP_DEFAULT, the screen of record). A pool without a map entry gets null
    (the kit's NaN): outside the V band on both sides.
  * t_recv_ms is SYNTHETIC: the views have none (null), or a different clock. Rows of an hour are put in the kit's own order, (slot, tx_index or 0, event_index) with the file order as
    the tie-break (what `np.lexsort` does in `build_hour`), and the second `block_time` is spread over [0, 1000) ms in that order: t_recv_ms = block_time * 1000 + 1000 * rank // n_in_second.
    Order inside a pool is therefore the kit's; the hour of a row is the hour of its block_time. The adapter REFUSES an hour whose rows are not in non-decreasing block_time in that
    order, a row outside its file hour, (a second with more than 1000 rows gets tied ms values and is counted in the report).
  * `create` and `complete` rows (creates/ and migrations/ of the block, mode complete): in the same order, as `type` create (mint, is_mayhem_mode) and complete (mint, event_ts).
    Creates/migrations of a --lookback block, and of any block hour outside the trade hours, are written FIRST in the first hour (t_recv_ms = hour start + index), because the kit's
    aux tables (l4-audit/aux.py) hold the whole creates/migrations directories of the block and its DECLARE lookback. The first `complete` per mint wins on both sides.
  * `--first-print-only` writes no create / complete rows (oracle-live has none).

WHAT IT IS NOT: a replay of the live feed. The sub-second order of rows in different slots of one second is invented (slot order); the live tape's arrival jitter is not modelled.
It cannot show a mismatch that depends on arrival order differing from slot order. Output never goes inside /data/mal/arena or /data/mal/clean-view.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Iterator

from tools import arena_screen as S

WSOL = S.WSOL
HOUR_RE = re.compile(r"-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl\.zst$")


STATS = {"seconds_over_1000_rows": 0}


class AdapterRefused(Exception):
    pass


def _hour_str(hours_since_epoch: int) -> str:
    import time

    return time.strftime("%Y-%m-%dT%H", time.gmtime(hours_since_epoch * 3600))


def hour_epoch(h: str) -> int:
    import calendar
    import time

    return calendar.timegm(time.strptime(h, "%Y-%m-%dT%H"))


def zlines(path: str) -> Iterator[bytes]:
    p = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE, bufsize=1 << 20)
    assert p.stdout is not None
    yield from p.stdout
    if p.wait() != 0:
        raise AdapterRefused(f"zstd_failed: {path}")


def hour_files(dirs: Iterable[str], kind: str) -> dict[str, str]:
    """hour -> file for dirs/<kind>/<kind>-<hour>.jsonl.zst (workers' dirs hold disjoint hours; a repeated hour is refused)."""
    out: dict[str, str] = {}
    for d in dirs:
        base = Path(d) / kind
        if not base.is_dir():
            continue
        for f in sorted(base.glob(f"{kind}-*.jsonl.zst")):
            m = HOUR_RE.search(f.name)
            if not m:
                continue
            if m.group(1) in out:
                raise AdapterRefused(f"hour_in_two_dirs: {kind} {m.group(1)}")
            out[m.group(1)] = str(f)
    return out


def _ord(r: dict) -> tuple:
    return (r.get("slot") or 0, r.get("tx_index") or 0, r.get("event_index") or 0)


def structure_rows(path: str, typ: str) -> list[dict]:
    pat = b'"type":"%s"' % typ.encode()
    out = []
    for line in zlines(path):
        if pat not in line:
            continue
        r = json.loads(line)
        if r.get("type") != typ or not r.get("mint"):
            continue
        out.append(r)
    return out


def to_create(r: dict) -> dict:
    return {"v": 1, "type": "create", "prog": "pumpfun", "slot": r.get("slot"), "signature": r.get("signature"), "event_index": r.get("event_index") or 0, "mint": r["mint"],
            "event_ts": r.get("block_time") or r.get("event_ts"), "quote_mint": WSOL, "is_mayhem_mode": bool(r.get("is_mayhem_mode"))}


def to_complete(r: dict) -> dict:
    return {"v": 1, "type": "complete", "prog": "pumpfun", "slot": r.get("slot"), "signature": r.get("signature"), "event_index": r.get("event_index") or 0, "mint": r["mint"],
            "event_ts": r.get("block_time") or r.get("event_ts"), "quote_mint": WSOL}


def trade_rows(path: str, vmap: S.VMap) -> tuple[list[dict], dict]:
    """The hour's kept PumpSwap WSOL trades (still in file order, with tx_index kept for ordering) and counts."""
    cnt = {"seen": 0, "pumpswap": 0, "non_wsol": 0, "no_ts": 0, "kept": 0}
    out = []
    for line in zlines(path):
        cnt["seen"] += 1
        if b'"venue":"pumpswap"' not in line:
            continue
        r = json.loads(line)
        if r.get("venue") != "pumpswap":
            continue
        pool = r.get("pool")
        if not pool or r.get("slot") is None or r.get("side") not in ("buy", "sell"):
            continue
        cnt["pumpswap"] += 1
        if not r.get("quote_is_wsol"):
            cnt["non_wsol"] += 1
            continue
        bt = r.get("block_time") or r.get("event_ts")
        if not isinstance(bt, (int, float)) or bt <= 0:
            cnt["no_ts"] += 1
            continue
        v0 = vmap.get(pool)
        out.append({"v": 1, "type": "trade", "prog": "pumpswap", "venue": "pumpswap", "slot": r["slot"], "signature": r.get("signature"), "event_index": r.get("event_index") or 0,
                    "mint": r.get("mint"), "trader": r.get("trader"), "side": r["side"], "sol_lamports": r["sol_lamports"], "token_raw": r["token_raw"],
                    "quote_reserve": r["quote_reserve"], "base_reserve": r["base_reserve"], "quote_mint": WSOL, "pool": pool, "event_ts": int(bt),
                    "virtual_quote_reserves": None if v0 is None else int(v0), "tx_index": r.get("tx_index") or 0})
        tr = r.get("t_recv_ms")
        if isinstance(tr, int) and not isinstance(tr, bool) and tr > 0:
            out[-1]["_real_t"] = tr
        cnt["kept"] += 1
    return out, cnt


def stamp(rows: list[dict], hour: str) -> list[dict]:
    """Order like build_hour (slot, tx_index, event_index; file order on ties) and give each row its synthetic t_recv_ms. Refuses on a non-monotone block_time, a row outside the hour."""
    T = hour_epoch(hour)
    real = [r for r in rows if "_real_t" in r]
    if real:  # a live-feed view (oracle-live): the rows carry their own t_recv_ms, and the file hour is the arrival hour
        if len(real) != len(rows):
            raise AdapterRefused(f"mixed_real_and_missing_t_recv_ms: {hour}")  # a live-feed view has no create / complete rows (--first-print-only)
        rows = sorted(rows, key=lambda r: r["_real_t"])
        for r in rows:
            if not T * 1000 <= r["_real_t"] < (T + 3600) * 1000:
                raise AdapterRefused(f"t_recv_outside_file_hour: {hour}")
            r["t_recv_ms"] = r.pop("_real_t")
            r.pop("tx_index", None)
        return rows
    rows = sorted(rows, key=lambda r: (r["slot"] or 0, r.get("tx_index") or 0, r.get("event_index") or 0))  # sorted() is stable
    prev = 0
    for r in rows:
        bt = r["event_ts"]
        if not T <= bt < T + 3600:
            raise AdapterRefused(f"row_outside_hour: {hour} block_time {bt}")
        if bt < prev:
            raise AdapterRefused(f"block_time_not_monotone_in_slot_order: {hour}")
        prev = bt
    i = 0
    while i < len(rows):
        j = i
        while j < len(rows) and rows[j]["event_ts"] == rows[i]["event_ts"]:
            j += 1
        n = j - i
        if n > 1000:
            STATS["seconds_over_1000_rows"] += 1  # ms ties: file order keeps the kit's order, but build_hour's tie-break would be event_index only (reported, not refused)
        for k in range(i, j):
            rows[k]["t_recv_ms"] = rows[k]["event_ts"] * 1000 + 1000 * (k - i) // n
        i = j
    for r in rows:
        r.pop("tx_index", None)
    return rows


def write_hour(path: Path, rows: list[dict]) -> str:
    tmp = Path(str(path) + ".tmp")
    p = subprocess.Popen(["zstd", "-q", "-3", "-f", "-o", str(tmp)], stdin=subprocess.PIPE)
    assert p.stdin is not None
    for r in rows:
        p.stdin.write((json.dumps(r, separators=(",", ":"), sort_keys=False) + "\n").encode())
    p.stdin.close()
    if p.wait() != 0:
        raise AdapterRefused("zstd_write_failed")
    os.replace(tmp, path)
    return str(path)


def refuse_path(p: str) -> None:
    low = p.lower()
    real = os.path.realpath(p).lower()
    for q in (low, real):
        for w in ("forward-", "truth-", "fresh-08", "exp011-", "exp012-forward", "tip-tape", "/var/lib/mal/sealed", "shadow", "canary", "probe", "walk", "_shakedown", "kalshi"):
            if w in q:
                raise AdapterRefused(f"path_refused ({w}): {p}")


def run(block_dirs: list[str], out: str, lookback: list[str], first_print_only: bool, vmap_paths: list[str] | None = None) -> dict:
    for p in [*block_dirs, *lookback, out]:
        refuse_path(p)
    ro = os.path.realpath(out)
    for root in ("/data/mal/arena", "/data/mal/clean-view"):
        if ro == root or ro.startswith(root + "/"):
            raise AdapterRefused(f"out_inside_{root}")
    for d in [*block_dirs, *lookback]:
        S.check_block_path(d if not re.search(r"/w\d$", d) else os.path.dirname(d), False)
    outp = Path(out)
    if outp.exists() and any(outp.iterdir()):
        raise AdapterRefused("out_not_empty")
    outp.mkdir(parents=True, exist_ok=True)
    vmap = S.VMap(S.check_vmap_paths(vmap_paths or S.VMAP_DEFAULT))
    trades = hour_files(block_dirs, "trades")
    hours = sorted(trades)
    if not hours:
        raise AdapterRefused("no_trade_hours")
    for a, b in zip(hours, hours[1:]):
        if hour_epoch(b) != hour_epoch(a) + 3600:
            raise AdapterRefused(f"gap_between_hours: {a} {b}")
    pre: list[dict] = []
    ncre = nmig = 0
    if not first_print_only:
        cre = hour_files([*lookback, *block_dirs], "creates")
        mig = hour_files([*lookback, *block_dirs], "migrations")
        in_range = set(hours)
        for h in sorted(set(cre) | set(mig)):
            if h in in_range:
                continue  # in-range structure rows are merged into their own hour below
            if hour_epoch(h) > hour_epoch(hours[-1]):
                raise AdapterRefused(f"structure_hour_after_trades: {h}")
            rows = ([to_create(r) for r in structure_rows(cre[h], "create")] if h in cre else []) + ([to_complete(r) for r in structure_rows(mig[h], "complete")] if h in mig else [])
            pre += rows
    else:
        cre = mig = {}
    report: dict[str, Any] = {"hours": {}, "preamble_rows": len(pre), "rebinned_rows": 0}
    pend: dict[str, list[dict]] = {}  # live-feed views: rows are re-binned to the hour of their own t_recv_ms (the port's hour); a row spills at most one file
    cnts: dict[str, dict] = {}
    done: set[str] = set()

    def flush(h: str) -> None:
        rows = pend.pop(h, [])
        extra = [r for r in rows if r["type"] != "trade"]
        allr = stamp(rows, h)
        if h == hours[0] and pre:
            t0 = hour_epoch(h) * 1000
            for r in pre:
                r["t_recv_ms"] = t0
            allr = pre + allr
        write_hour(outp / f"tape-{h}.jsonl.zst", allr)
        cnt = cnts[h]
        cnt["rows_out"], cnt["structure_rows"] = len(allr), len(extra)
        report["hours"][h] = cnt
        done.add(h)
        print(f"{h} {cnt}", flush=True)

    for n, h in enumerate(hours):
        rows, cnt = trade_rows(trades[h], vmap)
        cnts[h] = cnt
        if not first_print_only:
            if h in cre:
                srows = structure_rows(cre[h], "create")
                rows += [{**to_create(r), "tx_index": r.get("tx_index") or 0} for r in srows]
                ncre += len(srows)
            if h in mig:
                srows = structure_rows(mig[h], "complete")
                rows += [{**to_complete(r), "tx_index": r.get("tx_index") or 0} for r in srows]
                nmig += len(srows)
        for r in rows:
            hh = h
            if "_real_t" in r:
                hh = _hour_str(r["_real_t"] // 3600000)
                if hh != h:
                    report["rebinned_rows"] += 1
                if hh not in cnts and hh not in hours:
                    raise AdapterRefused(f"t_recv_outside_block_hours: {hh} (file {h})")
                if hh in done:
                    raise AdapterRefused(f"row_spills_into_a_finished_hour: {hh} (file {h})")
            pend.setdefault(hh, []).append(r)
        prev = _hour_str(hour_epoch(h) // 3600 - 1)
        if prev in cnts and prev not in done:
            flush(prev)
    for h in hours:
        if h not in done:
            flush(h)
    report["seconds_over_1000_rows"] = STATS["seconds_over_1000_rows"]
    report["n_hours"] = len(hours)
    report["first_hour"], report["last_hour"] = hours[0], hours[-1]
    report["creates_in_range"], report["completes_in_range"] = ncre, nmig
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--block", nargs="+", required=True, help="clean-view block dir(s); the explore-0814 workers' dirs w1..w7 are listed together")
    ap.add_argument("--out", required=True)
    ap.add_argument("--lookback", nargs="*", default=[], help="blocks whose creates / migrations go first in the first hour (the kit's DECLARE lookback)")
    ap.add_argument("--first-print-only", action="store_true", help="no create / complete rows (oracle-live has none)")
    a = ap.parse_args(argv)
    try:
        rep = run(a.block, a.out, a.lookback, a.first_print_only)
    except (AdapterRefused, S.Refused) as e:
        print(f"REFUSED {e}", file=sys.stderr)
        return 2
    Path(a.out, "adapter_report.json").write_text(json.dumps(rep, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
