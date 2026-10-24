#!/usr/bin/env python3
"""damm_v2_fetch: Meteora DAMM v2 swap history of pools that a Meteora DBC curve MIGRATED INTO inside the exploration window (P16). PAPER ONLY, read-only RPC key.

    python -m tools.damm_v2_fetch run --out-dir /data2/mal/venue-tape-backfill/meteora_damm_v2 --credit-cap 1000000 --strand-json FILE

Per migration (a DBC `ix` row naming MigrationDammV2 in the exploration tape): getTransaction(signature) -> DAMM pool (inner initialize ix accounts[7], cross-checked with the DBC
instruction's accounts[4]; the DBC virtual pool is accounts[0]) -> getTransactionsForAddress(pool) from the migration block time for 24 h, cut at 2026-10-17T00, succeeded txs only,
rows in the venue_tape schema (venue `meteora_damm_v2`, tools/damm_decode.py). Fetch order = execution order (kept: do not re-sort by signature).
Order of work: all migrations are resolved first (10 assumed credits each); then pools holding a P14 strand (--strand-json) first, then the rest in sha256(pool) order (a uniform
subset if the cap is reached: coverage is recorded in the checkpoint). A page shorter than the limit ends the pool (no empty trailing page). Per-pool page cap 40.
HOLDOUT: a migration with block_time outside [2026-10-14T00, 2026-10-17T00) is refused; history rows at/after 2026-10-17T00 are dropped and counted.
Credits: hard cap before every call; assumed cost gTFA 50, getTransaction 10. The key is read from the env file and never printed. One Helius flock slot, rps <= 8.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from tools import damm_decode as dd
from tools import dbc_decode as bd
from tools import dbc_price as L
from tools import venue_backfill as vb

COST_GTFA, COST_GETTX = 50, 10
HIST_S = 24 * 3600
PAGE_LIMIT = 100
MAX_PAGES = 40
DEFAULT_OUT = "/data2/mal/venue-tape-backfill/meteora_damm_v2"
END_TS = int(L.EXPLORE_END.timestamp())
START_TS = int(L.EXPLORE_START.timestamp())
EXIT_CAP = vb.EXIT_CAP


def migrations_from_tape(root: str = L.BACKFILL_ROOT) -> list[dict[str, Any]]:
    out = []
    for h in L.exploration_hours():
        f = L.hour_file(h, root)
        if not os.path.exists(f):
            continue
        L.check_hour_path(f, root)
        p = subprocess.Popen(["zstd", "-dc", "-q", f], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        assert p.stdout is not None
        try:
            for line in p.stdout:
                if b"MigrationDammV2" in line:
                    d = json.loads(line)
                    if d.get("type") == "ix" and "MigrationDammV2" in (d.get("ix") or []):
                        out.append({"signature": d["signature"], "block_time": int(d.get("block_time") or d["t_recv_ms"] // 1000), "slot": d["slot"]})
        finally:
            p.stdout.close()
            p.wait()
    seen, uniq = set(), []
    for m in sorted(out, key=lambda m: (m["block_time"], m["signature"])):
        if m["signature"] not in seen:
            seen.add(m["signature"])
            uniq.append(m)
    return uniq


def check_migration(m: dict[str, Any]) -> None:
    if not (START_TS <= m["block_time"] < END_TS):
        raise L.Refused(f"migration_outside_exploration_window: {m['signature']}")


def resolve(call: Callable[[str, list[Any]], Any], credits: vb.Credits, m: dict[str, Any]) -> dict[str, Any]:
    check_migration(m)
    credits.charge(COST_GETTX)
    tx = call("getTransaction", [m["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}])
    if not tx:
        return {"status": "no_tx"}
    meta = dd.init_pool_from_migration_tx(tx)
    if meta is None:
        return {"status": "no_damm_init"}
    keys = bd.account_keys(tx)
    msg = (tx.get("transaction") or {}).get("message") or {}
    dbc_pool = next((keys[ix["accounts"][0]] for ix in msg.get("instructions") or [] if keys[ix["programIdIndex"]] == bd.PROGRAM and ix.get("accounts")), None)
    meta.update(status="ok", dbc_pool=dbc_pool, tx_block_time=tx.get("blockTime"))
    return meta


def fetch_pool(call, credits: vb.Credits, pool: str, wsol_is_b: bool, t0: int, max_pages: int = MAX_PAGES) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    t1 = min(t0 + HIST_S, END_TS)
    rows: list[dict[str, Any]] = []
    token = None
    pages = txs = dropped = 0
    truncated = False
    seen: set[str] = set()
    while True:
        if pages >= max_pages:
            truncated = True
            break
        credits.charge(COST_GTFA)
        opts: dict[str, Any] = {"transactionDetails": "full", "sortOrder": "asc", "limit": PAGE_LIMIT, "encoding": "json", "maxSupportedTransactionVersion": 1,
                                "filters": {"blockTime": {"gte": t0, "lt": t1}, "status": "succeeded"}}
        if token:
            opts["paginationToken"] = token
        res = call("getTransactionsForAddress", [pool, opts])
        pages += 1
        data = (res or {}).get("data") or []
        for tx in data:
            bt = tx.get("blockTime")
            sig = ((tx.get("transaction") or {}).get("signatures") or [None])[0]
            if sig in seen:
                continue
            seen.add(sig)
            if bt is None or int(bt) >= END_TS or int(bt) < t0:
                dropped += 1
                continue
            txs += 1
            rows += dd.decode_tx(tx, pool, wsol_is_b)
        token = (res or {}).get("paginationToken")
        if not token or len(data) < PAGE_LIMIT:
            break
    for i, r in enumerate(rows):
        r["seq"] = i  # fetch order = execution order
    last_bt = max((r["block_time"] for r in rows if r.get("block_time")), default=None)
    return rows, {"pages": pages, "txs": txs, "dropped_outside": dropped, "rows": len(rows), "truncated": truncated, "covered_to_bt": last_bt if truncated else t1, "window": [t0, t1]}


def write_pool_file(out: Path, pool: str, rows: list[dict[str, Any]]) -> Path:
    d = out / "pools"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"pool-{pool}.jsonl"
    tmp = p.with_suffix(".part")
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":"), allow_nan=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)
    from tools.arena_zst import compact_file

    compact_file(p)
    return Path(str(p) + ".zst")


def order_pools(metas: dict[str, dict[str, Any]], strand_dbc: set[str]) -> list[str]:
    ok = [s for s, m in metas.items() if m.get("status") == "ok" and m.get("wsol_in_pool")]
    key = lambda s: hashlib.sha256(metas[s]["damm_pool"].encode()).hexdigest()  # noqa: E731
    first = sorted([s for s in ok if metas[s].get("dbc_pool") in strand_dbc], key=key)
    rest = sorted([s for s in ok if metas[s].get("dbc_pool") not in strand_dbc], key=key)
    return first + rest


def acquire_slot(lockdir: str):
    Path(lockdir).mkdir(parents=True, exist_ok=True)
    while True:
        for i in (4, 3, 2, 1):
            fh = open(Path(lockdir) / f"helius-{i}.lock", "a")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                print(f"holding helius slot {i}", flush=True)
                return fh
            except OSError:
                fh.close()
        time.sleep(15)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["run"])
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--env-file", default=vb.HELIUS_ENV)
    ap.add_argument("--lockdir", default=vb.LOCKDIR)
    ap.add_argument("--rps", type=float, default=8.0)
    ap.add_argument("--credit-cap", type=int, default=1_000_000)
    ap.add_argument("--strand-json", default="")
    ap.add_argument("--limit-migrations", type=int, default=0, help="test only")
    a = ap.parse_args(argv)
    try:
        url = vb.make_url(vb.read_key(a.env_file))
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return vb.EXIT_ENV
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if out.resolve().is_relative_to(Path(L.BACKFILL_ROOT).resolve()):
        raise L.Refused("out_dir_inside_dbc_tape")
    cpf = out / "checkpoint.json"
    cp = json.loads(cpf.read_text()) if cpf.is_file() else {"version": 1, "credits_used": 0, "migs": {}, "pools": {}}

    def save() -> None:
        tmp = cpf.with_suffix(".tmp")
        tmp.write_text(json.dumps(cp))
        os.replace(tmp, cpf)

    strand_dbc = set(json.loads(Path(a.strand_json).read_text())["pools"]) if a.strand_json else set()
    migs = migrations_from_tape()
    if a.limit_migrations:
        migs = migs[:a.limit_migrations]
    prog = os.environ.get("MISCUSI_PROGRESS")

    def progress(s: str) -> None:
        print(s, flush=True)
        if prog:
            try:
                Path(prog).write_text(json.dumps({"note": s, "credits_used": credits.used}) + "\n")
            except OSError:
                pass

    credits = vb.Credits(a.credit_cap, COST_GTFA, int(cp["credits_used"]))
    slot = acquire_slot(a.lockdir)  # noqa: F841 (held for the process lifetime)
    call = vb.http_rpc(url, vb.Pacer(a.rps))
    byk = {m["signature"]: m for m in migs}
    try:
        n = 0
        for m in migs:
            if m["signature"] in cp["migs"] and cp["migs"][m["signature"]].get("status") != "no_damm_init":
                continue
            try:
                r = resolve(call, credits, m)
            except RuntimeError as exc:
                if "credit cap" in str(exc):
                    raise
                r = {"status": "error", "detail": str(exc)[:100]}
            cp["migs"][m["signature"]] = {**r, "block_time": m["block_time"], "slot": m["slot"]}
            n += 1
            if n % 200 == 0:
                cp["credits_used"] = credits.used
                save()
                progress(f"resolved {len(cp['migs'])}/{len(migs)} credits={credits.used}")
        cp["credits_used"] = credits.used
        save()
        order = order_pools({s: cp["migs"][s] for s in byk if s in cp["migs"]}, strand_dbc)
        progress(f"resolved all; {len(order)} fetchable pools, {sum(1 for s in order if cp['migs'][s].get('dbc_pool') in strand_dbc)} strand pools first; credits={credits.used}")
        for s in order:
            m = cp["migs"][s]
            pool = m["damm_pool"]
            if pool in cp["pools"]:
                continue
            before = credits.used
            try:
                rows, st = fetch_pool(call, credits, pool, bool(m["wsol_is_b"]), int(m["block_time"]))
            except RuntimeError as exc:
                if "credit cap" in str(exc):
                    raise
                cp["pools"][pool] = {"status": "error", "detail": str(exc)[:100], "credits": credits.used - before}
                continue
            head = {"v": dd.SCHEMA_V, "venue": dd.VENUE, "type": "pool_meta", "src": "backfill", "slot": m["slot"], "t_recv_ms": int(m["block_time"]) * 1000, "block_time": m["block_time"],
                    "signature": s, "event_index": 0, "seq": -1, **{k: m[k] for k in ("damm_pool", "dbc_pool", "index_ok", "config", "token_a_mint", "token_b_mint", "vault_a", "vault_b", "wsol_is_b", "token_mint")}, **st}
            write_pool_file(out, pool, [head, *rows])
            cp["pools"][pool] = {"status": "ok", "credits": credits.used - before, "pages": st["pages"], "txs": st["txs"], "truncated": st["truncated"]}
            cp["credits_used"] = credits.used
            if len(cp["pools"]) % 25 == 0:
                save()
                progress(f"pools {len(cp['pools'])}/{len(order)} credits={credits.used}")
    except vb.CapReached:
        cp["credits_used"] = credits.used
        cp["stopped"] = "credit_cap"
        save()
        progress(f"credit cap reached: pools={len(cp['pools'])} credits={credits.used}")
        return EXIT_CAP
    cp["credits_used"] = credits.used
    cp["stopped"] = "done"
    save()
    progress(f"done pools={len(cp['pools'])} credits={credits.used}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
