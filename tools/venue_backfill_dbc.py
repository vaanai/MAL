"""Historical Meteora DBC trade tape from Helius getTransactionsForAddress (paper/research only; read-only RPC key file, no wallet, no sends).

Same method and machinery as tools/venue_backfill.py (LaunchLab): hourly windows, status=succeeded (failed txs absent by design), asc, 100 per page,
HARD credit cap checked before every call (exit 5), flock slot under /data/mal/locks, resumable via <out>/checkpoint-meteora_dbc.json.
Rows come from tools/dbc_decode.py (full transactions; swap amounts, fees, mints, sqrt price, quote reserve, vault balances before/after), in the
venue_tape v1 schema, venue `meteora_dbc`, `src`="backfill", `t_recv_ms` = blockTime*1000.  Output: <out>/meteora_dbc/tape-<YYYY-MM-DDTHH>.jsonl.zst.

HOLDOUT GUARD: the manager reserves DBC hours [2026-10-17T00, 2026-10-24T00) as a holdout; run/probe refuse any hour inside it (exit 7).
The key is read from the env file and never printed or logged.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

from tools import dbc_decode as dd
from tools import venue_backfill as vb

VENUE = dd.VENUE
PROGRAM = dd.PROGRAM
DEFAULT_OUT = "/data2/mal/venue-tape-backfill"
HOLDOUT = (vb.parse_hour("2026-10-17T00"), vb.parse_hour("2026-10-24T00"))
EXIT_HOLDOUT = 7


def in_holdout(t0: int) -> bool:
    return HOLDOUT[0] <= t0 < HOLDOUT[1]


def rows_from_tx(tx: dict[str, Any]) -> list[dict[str, Any]]:
    rows = dd.decode_tx(tx)
    bt = tx.get("blockTime")
    for r in rows:
        r["src"] = "backfill"
        r["block_time"] = bt
    return rows


def gtfa_params(t0: int, t1: int, token: str | None, limit: int) -> list[Any]:
    p = vb.gtfa_params(t0, t1, token, limit)
    p[0] = PROGRAM
    return p


def fetch_hour(call: Callable[[str, list[Any]], Any], credits: vb.Credits, t0: int, limit: int = 100):
    items: list[tuple[Any, Any, list[dict[str, Any]]]] = []
    token: str | None = None
    pages = 0
    while True:
        credits.charge()
        res = call("getTransactionsForAddress", gtfa_params(t0, t0 + 3600, token, limit))
        pages += 1
        data = (res or {}).get("data") or []
        for tx in data:
            items.append((((tx.get("transaction") or {}).get("signatures") or [None])[0], tx.get("blockTime"), rows_from_tx(tx)))
        token = (res or {}).get("paginationToken")
        if not token or not data:
            return items, pages


def build_hour_rows(t0: int, items, pages: int, credits_spent: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[Any] = set()
    dup = 0
    for sig, bt, trs in items:
        if sig in seen or (bt is not None and not (t0 <= int(bt) < t0 + 3600)):
            dup += 1
            continue
        seen.add(sig)
        rows.extend(trs)
    rows.sort(key=vb.sort_key)
    types: dict[str, int] = {}
    for r in rows:
        types[r["type"]] = types.get(r["type"], 0) + 1
    head = {"v": dd.SCHEMA_V, "venue": VENUE, "type": "backfill_hour", "src": "backfill", "slot": 0, "t_recv_ms": t0 * 1000, "signature": None,
            "event_index": None, "hour": vb.hour_label(t0), "txs": len(seen), "dropped_dup_or_outside": dup, "pages": pages, "credits": credits_spent,
            "status_filter": "succeeded", "failed_txs_absent": True, "program": PROGRAM}
    return [head, *rows, {**head, "type": "backfill_hour_end", "rows": len(rows), "row_types": types}]


def gap_rows(t0: int, reason: str) -> list[dict[str, Any]]:
    return [{"v": dd.SCHEMA_V, "venue": VENUE, "type": "gap", "src": "backfill", "slot": 0, "t_recv_ms": t0 * 1000, "signature": None, "event_index": None,
             "reason": "backfill_incomplete", "detail": reason[:120], "hour": vb.hour_label(t0)}]


def write_hour(out: Path, t0: int, rows, compact) -> Path:
    d = out / VENUE
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"tape-{vb.hour_label(t0)}.jsonl"
    tmp = p.with_suffix(".jsonl.part")
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":"), allow_nan=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)
    if compact is not None:
        compact(p)
        return Path(str(p) + ".zst")
    return p


HOLDOUT_DIR = "meteora_dbc_holdout"
HOLDOUT_README = "HOLDOUT - do not open before the QP protocol (hunt29/dbc/QP-RULING-1024-dbc-m10.md) is pinned\n"


def run_hours(out: Path, hours: list[int], call, credits: vb.Credits, compact, progress=lambda s: None, max_tries: int = 3,
              allow_holdout: bool = False, cp_name: str = "checkpoint-meteora_dbc.json") -> int:
    for t0 in hours:
        if in_holdout(t0) and not allow_holdout:
            raise ValueError(f"hour {vb.hour_label(t0)} is inside the reserved holdout")
    out.mkdir(parents=True, exist_ok=True)
    cpf = out / cp_name
    cp = vb.load_cp(cpf)
    credits.used = max(credits.used, int(cp["credits_used"]))
    tries: dict[int, int] = {}
    for t0 in hours:
        lab = vb.hour_label(t0)
        if lab in cp["hours"]:
            continue
        while True:
            before = credits.used
            try:
                items, pages = fetch_hour(call, credits, t0)
            except vb.CapReached:
                cp["credits_used"] = credits.used
                vb.save_cp(cpf, cp)
                progress(f"credit cap reached before {lab}")
                return vb.EXIT_CAP
            except RuntimeError as exc:
                tries[t0] = tries.get(t0, 0) + 1
                cp["credits_used"] = credits.used
                if tries[t0] >= max_tries:
                    write_hour(out, t0, gap_rows(t0, str(exc)), compact)
                    cp["hours"][lab] = {"gap": True, "credits": credits.used - before}
                    vb.save_cp(cpf, cp)
                    break
                vb.save_cp(cpf, cp)
                time.sleep(2.0)
                continue
            spent = credits.used - before
            rows = build_hour_rows(t0, items, pages, spent)
            write_hour(out, t0, rows, compact)
            cp["hours"][lab] = {"txs": len(items), "pages": pages, "rows": len(rows) - 2, "credits": spent}
            cp["credits_used"] = credits.used
            vb.save_cp(cpf, cp)
            progress(f"{lab} txs={len(items)} credits={credits.used}")
            break
    return 0


def probe(call, credits: vb.Credits, t0: int, offset_s: int, limit: int, pages: int) -> dict[str, Any]:
    """Dry run: up to `pages` pages from t0+offset_s inside one hour. Writes nothing. Reports tx density from the blockTime span of the pages."""
    token = None
    txs: list[dict[str, Any]] = []
    n = 0
    for _ in range(pages):
        credits.charge()
        res = call("getTransactionsForAddress", gtfa_params(t0 + offset_s, t0 + 3600, token, limit))
        n += 1
        txs.extend((res or {}).get("data") or [])
        token = (res or {}).get("paginationToken")
        if not token:
            break
    types: dict[str, int] = {}
    for tx in txs:
        for r in rows_from_tx(tx):
            types[r["type"]] = types.get(r["type"], 0) + 1
    bts = [t["blockTime"] for t in txs if t.get("blockTime")]
    span = (max(bts) - min(bts)) if bts else None
    return {"hour": vb.hour_label(t0), "offset_s": offset_s, "pages": n, "txs": len(txs), "row_types": types, "credits_assumed": credits.used,
            "blocktime_span_s": span, "txs_per_s": round(len(txs) / span, 3) if span else None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Meteora DBC historical trade tape from Helius (read-only RPC key; paper only)")
    ap.add_argument("mode", choices=["probe", "run"])
    ap.add_argument("--start", required=True)
    ap.add_argument("--end")
    ap.add_argument("--offset-s", type=int, default=0, help="probe: seconds into the hour")
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--env-file", default=vb.HELIUS_ENV)
    ap.add_argument("--lockdir", default=vb.LOCKDIR)
    ap.add_argument("--rps", type=float, default=8.0)
    ap.add_argument("--credit-cap", type=int, default=3_000_000)
    ap.add_argument("--credits-per-call", type=int, default=50)
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--probe-pages", type=int, default=2)
    ap.add_argument("--no-compress", action="store_true")
    ap.add_argument("--allow-holdout-write", action="store_true",
                    help="write-only fetch of the reserved holdout; out-dir must end in meteora_dbc_holdout; prints only hour/tx counts and credits")
    a = ap.parse_args(argv)
    if a.allow_holdout_write:
        if a.mode != "run" or Path(a.out_dir).name != HOLDOUT_DIR:
            print(f"refused: --allow-holdout-write needs mode run and an out-dir named {HOLDOUT_DIR}", file=sys.stderr)
            return 2
    t0 = vb.parse_hour(a.start)
    if a.mode == "probe":
        hours = [t0]
    else:
        if not a.end:
            print("--end required", file=sys.stderr)
            return 2
        hours = vb.hours_between(t0, vb.parse_hour(a.end))[::-1]
    if any(in_holdout(h) for h in hours) and not a.allow_holdout_write:
        print("refused: window touches the reserved holdout [2026-10-17T00, 2026-10-24T00)", file=sys.stderr)
        return EXIT_HOLDOUT
    try:
        url = vb.make_url(vb.read_key(a.env_file))
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return vb.EXIT_ENV
    held = None
    Path(a.lockdir).mkdir(parents=True, exist_ok=True)
    while held is None:
        for i in (4, 3, 2, 1):
            fh = open(Path(a.lockdir) / f"helius-{i}.lock", "a")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = fh
                print(f"holding helius slot {i}", flush=True)
                break
            except OSError:
                fh.close()
        if held is None:
            time.sleep(15)
    call = vb.http_rpc(url, vb.Pacer(a.rps))
    credits = vb.Credits(a.credit_cap, a.credits_per_call)
    if a.mode == "probe":
        print(json.dumps(probe(call, credits, t0, a.offset_s, a.limit, a.probe_pages), sort_keys=True))
        return 0
    from tools.arena_zst import compact_file, zstd_path
    if not a.no_compress and zstd_path() is None:
        print("zstd CLI missing", file=sys.stderr)
        return 4
    prog = os.environ.get("MISCUSI_PROGRESS")

    def progress(s: str) -> None:
        print(s, flush=True)
        if prog:
            try:
                Path(prog).write_text(json.dumps({"note": s, "credits_used": credits.used}) + "\n")
            except OSError:
                pass

    out = Path(a.out_dir)
    cp_name = "checkpoint-meteora_dbc.json"
    if a.allow_holdout_write:
        out.mkdir(parents=True, exist_ok=True, mode=0o750)
        os.chmod(out, 0o750)
        (out / "README.txt").write_text(HOLDOUT_README)
        cp_name = f"checkpoint-{vb.hour_label(t0)}.json"  # per-block checkpoint: each block has its own credit cap
    rc = run_hours(out, hours, call, credits, None if a.no_compress else compact_file, progress, allow_holdout=a.allow_holdout_write, cp_name=cp_name)
    if a.allow_holdout_write:
        for dp, dn, fn in os.walk(out):
            os.chmod(dp, 0o750)
    progress(f"done rc={rc} credits_used={credits.used}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
