#!/usr/bin/env python3
"""launchlab_cpmm_fetch: Raydium CPMM history of pools that GRADUATED from LaunchLab inside the exploration window (P11 / I5 data fetch). PAPER ONLY, read-only RPC key.

    python -m tools.launchlab_cpmm_fetch probe --n 5 --out-dir DIR           # 5 migrations, pool address + 24 h history, credits per pool
    python -m tools.launchlab_cpmm_fetch run   --out-dir /data2/mal/venue-tape-backfill/launchlab_cpmm --credit-cap 1000000

Steps per migration (a LaunchLab `ix` row naming a Migrate instruction, from the exploration tape): getTransaction(signature) -> the CPMM `initialize` instruction (top level or inner)
-> pool_state (accounts[3]), token mints (accounts[4], [5]), vaults (accounts[10], [11]) and the LaunchLab instruction's account list (to map back to the LaunchLab pool);
then getTransactionsForAddress(pool_state) from the migration block time for 24 h (cut at the exploration end), succeeded transactions only, rows in the venue_tape schema
(venue `raydium_cpmm`, type `swap` from the program's `SwapEvent`, `ix` for CPMM instructions without a decodable event, `pool_meta` once per pool).

HOLDOUT: a migration with block_time outside [2026-09-24T00, 2026-10-17T00) is refused, and the history window is cut at 2026-10-17T00 (rows at or after it are dropped and counted).
Credits: a hard cap stops the run before any call that would pass it. Assumed cost per call: gTFA 50 (measured by P10 for the backfill), getTransaction 10 (an upper bound; standard
RPC is lower). The key is read from the env file and never printed. One Helius flock slot per batch, rps <= 8.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import base64
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from tools import launchlab_price as L
from tools import venue_backfill as vb
from tools import venue_tape as vt

CPMM = "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C"
LAUNCHLAB = vt.VENUES["launchlab"]["program"]
WSOL = "So11111111111111111111111111111111111111112"
INIT_DISCS = {hashlib.sha256(b"global:initialize").digest()[:8], hashlib.sha256(b"global:initialize_with_permission").digest()[:8]}  # LaunchLab migrates via the second
SWAP_DISC = hashlib.sha256(b"event:SwapEvent").digest()[:8]
VENUE = "raydium_cpmm"
HIST_S = 24 * 3600
COST_GTFA, COST_GETTX = 50, 10
DEFAULT_OUT = "/data2/mal/venue-tape-backfill/launchlab_cpmm"
EXIT_CAP = vb.EXIT_CAP
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(s) - len(s.lstrip("1"))) + body


def u64(b: bytes, o: int) -> int:
    return int.from_bytes(b[o:o + 8], "little")


# ------------------------------------------------------------------------------------------------------------------------ migrations from the tape
def migrations_from_tape(root: str = L.BACKFILL_ROOT, hours: list[datetime] | None = None) -> list[dict[str, Any]]:
    """LaunchLab `ix` rows naming a Migrate instruction, exploration hours only (guarded reads). Returns [{signature, block_time, slot, ix}] sorted by time."""
    out = []
    for h in hours if hours is not None else L.exploration_hours():
        f = L.hour_file(h, root)
        if not os.path.exists(f):
            continue
        L.check_hour_path(f, root)
        p = subprocess.Popen(["zstd", "-dc", "-q", f], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        assert p.stdout is not None
        try:
            for line in p.stdout:
                if b"igrate" in line and b'"ix"' in line:
                    d = json.loads(line)
                    if d.get("type") == "ix" and any("igrate" in str(x) for x in d.get("ix") or []):
                        out.append({"signature": d["signature"], "block_time": int(d.get("block_time") or d["t_recv_ms"] // 1000), "slot": d["slot"], "ix": d["ix"]})
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
    bt = m["block_time"]
    if not (L.EXPLORE_START.timestamp() <= bt < L.EXPLORE_END.timestamp()):
        raise L.Refused(f"migration_outside_exploration_window: {m['signature']} bt={bt}")


# ------------------------------------------------------------------------------------------------------------------------ transaction -> pool
def all_keys(tx: dict[str, Any]) -> list[str]:
    msg = (tx.get("transaction") or {}).get("message") or {}
    keys = list(msg.get("accountKeys") or [])
    la = (tx.get("meta") or {}).get("loadedAddresses") or {}
    return keys + list(la.get("writable") or []) + list(la.get("readonly") or [])


def instructions(tx: dict[str, Any]) -> list[tuple[str, list[str], bytes, int]]:
    """(program, account addresses, data, stack depth 1 top / 2 inner) for every instruction, top level then inner."""
    keys = all_keys(tx)
    msg = (tx.get("transaction") or {}).get("message") or {}
    out = []

    def one(ix: dict[str, Any], depth: int) -> None:
        try:
            prog = keys[ix["programIdIndex"]]
            accs = [keys[i] for i in ix.get("accounts") or []]
            data = b58decode(ix.get("data") or "")
        except (IndexError, KeyError, ValueError):
            return
        out.append((prog, accs, data, depth))

    for ix in msg.get("instructions") or []:
        one(ix, 1)
    for grp in (tx.get("meta") or {}).get("innerInstructions") or []:
        for ix in grp.get("instructions") or []:
            one(ix, 2)
    return out


def pool_from_migration_tx(tx: dict[str, Any]) -> dict[str, Any] | None:
    ixs = instructions(tx)
    init = next(((a, d) for p, a, d, _ in ixs if p == CPMM and d[:8] in INIT_DISCS and len(a) >= 13), None)
    if init is None:
        return None
    a, _ = init
    ll = next((acc for p, acc, d, dep in ixs if p == LAUNCHLAB and dep == 1), [])
    # initialize_with_permission accounts (verified against the on-chain PoolState of two migrations: amm_config, creator, vaults and mints match):
    # 0 payer, 1 creator, 2 amm_config, 3 permission, 4 pool_state, 5 token_0_mint, 6 token_1_mint, 7 lp_mint, ..., 11 token_0_vault, 12 token_1_vault
    m0, m1 = a[5], a[6]
    return {"cpmm_pool": a[4], "mint0": m0, "mint1": m1, "vault0": a[11], "vault1": a[12], "amm_config": a[2], "creator": a[1],
            "token_mint": m1 if m0 == WSOL else m0, "wsol_in_pool": WSOL in (m0, m1), "wsol_is_token0": m0 == WSOL, "ll_accounts": ll}


# ------------------------------------------------------------------------------------------------------------------------ CPMM swap decode
def decode_swap(b64: str) -> dict[str, Any] | None:
    """SwapEvent: pool_id, input_vault_before, output_vault_before, input_amount, output_amount, input_transfer_fee, output_transfer_fee, base_input,
    then (newer program versions) input_mint, output_mint, trade_fee, creator_fee, creator_fee_on_input. Length-driven; the tail that is not understood is kept raw."""
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception:  # noqa: BLE001
        return None
    if raw[:8] != SWAP_DISC or len(raw) < 8 + 32 + 6 * 8 + 1:
        return None
    o = 8
    row: dict[str, Any] = {"pool": vt.b58(raw[o:o + 32])}
    o += 32
    for n in ("input_vault_before", "output_vault_before", "input_amount", "output_amount", "input_transfer_fee", "output_transfer_fee"):
        row[n] = u64(raw, o)
        o += 8
    row["base_input"] = bool(raw[o])
    o += 1
    row["event_len"] = len(raw)
    if len(raw) >= o + 64:
        row["input_mint"], row["output_mint"] = vt.b58(raw[o:o + 32]), vt.b58(raw[o + 32:o + 64])
        o += 64
        if len(raw) >= o + 16:
            row["trade_fee"], row["creator_fee"] = u64(raw, o), u64(raw, o + 8)
            o += 16
            if len(raw) >= o + 1:
                row["creator_fee_on_input"] = bool(raw[o])
                o += 1
    if len(raw) > o:
        row["tail_undecoded_b64"] = base64.b64encode(raw[o:]).decode()
    return row


def rows_for_cpmm_tx(tx: dict[str, Any], pool: str) -> list[dict[str, Any]]:
    n = vb.tx_notice(tx)
    if n is None:
        return []
    pe = vt.program_events(n.logs, CPMM)
    if pe["depth"] == 0:
        return []
    base = {"v": vt.SCHEMA_V, "venue": VENUE, "slot": n.slot, "t_recv_ms": n.t_recv_ms, "block_time": n.t_recv_ms // 1000, "src": "backfill", "signature": n.signature, "pool_query": pool}
    if pe["depth"] > 1:
        base["via_cpi"] = True
    rows = []
    k = 0
    for b in pe["data"]:
        d = decode_swap(b)
        if d is not None:
            rows.append({**base, "type": "swap", "event_index": k, **d, "ix": pe["ix"]})
            k += 1
    if not rows:
        rows.append({**base, "type": "ix", "event_index": 0, "ix": pe["ix"]})
    return rows


# ------------------------------------------------------------------------------------------------------------------------ fetch
def fetch_pool(call: Callable[[str, list[Any]], Any], credits: vb.Credits, pool: str, t0: int, t1: int, limit: int = 100, max_pages: int = 10**9) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """All succeeded transactions touching `pool` with blockTime in [t0, t1). Returns (rows, stats). Rows at/after the exploration end are dropped and counted."""
    end = int(L.EXPLORE_END.timestamp())
    t1 = min(t1, end)
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
        opts: dict[str, Any] = {"transactionDetails": "full", "sortOrder": "asc", "limit": limit, "encoding": "json", "maxSupportedTransactionVersion": 1,
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
            if bt is None or int(bt) >= end or int(bt) < t0:
                dropped += 1
                continue
            txs += 1
            rows += rows_for_cpmm_tx(tx, pool)
        token = (res or {}).get("paginationToken")
        if not token or not data:
            break
    rows.sort(key=lambda r: (r["slot"], r["signature"] or "", r.get("event_index") or 0))
    last_bt = max((r["block_time"] for r in rows), default=None)
    return rows, {"pages": pages, "txs": txs, "dropped_outside": dropped, "rows": len(rows), "truncated": truncated, "covered_to_bt": last_bt if truncated else t1}


def resolve_migration(call: Callable[[str, list[Any]], Any], credits: vb.Credits, m: dict[str, Any]) -> dict[str, Any] | None:
    check_migration(m)
    credits.charge(COST_GETTX)
    tx = call("getTransaction", [m["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}])
    if not tx:
        return None
    return pool_from_migration_tx(tx)


def write_pool_file(out: Path, pool: str, rows: list[dict[str, Any]], compress: bool = True) -> Path:
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
    if compress:
        from tools.arena_zst import compact_file

        compact_file(p)
        return Path(str(p) + ".zst")
    return p


def process_migration(out: Path, call, credits: vb.Credits, m: dict[str, Any], max_pages: int = 10**9) -> dict[str, Any]:
    before = credits.used
    meta = resolve_migration(call, credits, m)
    if meta is None:
        return {"signature": m["signature"], "status": "no_cpmm_init", "credits": credits.used - before}
    t0 = m["block_time"]
    if not meta["wsol_in_pool"]:
        write_pool_file(out, meta["cpmm_pool"], [{"v": vt.SCHEMA_V, "venue": VENUE, "type": "pool_meta", "src": "backfill", "slot": m["slot"], "t_recv_ms": t0 * 1000, "block_time": t0,
                                                  "signature": m["signature"], "event_index": 0, **meta, "history_not_fetched": "non_wsol_quote"}])
        return {"signature": m["signature"], "status": "non_wsol_quote", "cpmm_pool": meta["cpmm_pool"], "block_time": t0, "credits": credits.used - before}
    rows, st = fetch_pool(call, credits, meta["cpmm_pool"], t0, t0 + HIST_S, max_pages=max_pages)
    head = {"v": vt.SCHEMA_V, "venue": VENUE, "type": "pool_meta", "src": "backfill", "slot": m["slot"], "t_recv_ms": t0 * 1000, "block_time": t0, "signature": m["signature"],
            "event_index": 0, **meta, "window": [t0, min(t0 + HIST_S, int(L.EXPLORE_END.timestamp()))], **st}
    write_pool_file(out, meta["cpmm_pool"], [head, *rows])
    return {"signature": m["signature"], "status": "ok", "cpmm_pool": meta["cpmm_pool"], "block_time": t0, "credits": credits.used - before, **st}


# ------------------------------------------------------------------------------------------------------------------------ main
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
    ap.add_argument("mode", choices=["probe", "run"])
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--env-file", default=vb.HELIUS_ENV)
    ap.add_argument("--lockdir", default=vb.LOCKDIR)
    ap.add_argument("--rps", type=float, default=6.0)
    ap.add_argument("--credit-cap", type=int, default=1_000_000)
    ap.add_argument("--max-pages", type=int, default=10**9, help="per-pool page cap (100 txs per page); a capped pool is marked truncated with covered_to_bt")
    ap.add_argument("--n", type=int, default=5, help="probe: number of migrations")
    ap.add_argument("--probe-from", default="2026-10-10T00", help="probe: take migrations at or after this hour")
    ap.add_argument("--wait-backfill", action="store_true", help="run: keep re-scanning until all 552 exploration hours exist (max --wait-h hours)")
    ap.add_argument("--wait-h", type=float, default=8.0)
    a = ap.parse_args(argv)
    try:
        url = vb.make_url(vb.read_key(a.env_file))
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return vb.EXIT_ENV
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if out.resolve().is_relative_to(Path(L.BACKFILL_ROOT).resolve()):
        raise L.Refused("out_dir_inside_launchlab_tape")
    cpf = out / "checkpoint.json"
    cp = vb.load_cp(cpf) if cpf.is_file() else {"version": 1, "credits_used": 0, "hours": {}}
    cp.setdefault("migrations", {})
    credits = vb.Credits(a.credit_cap, COST_GTFA, int(cp.get("credits_used", 0)))
    prog = os.environ.get("MISCUSI_PROGRESS")

    def progress(s: str) -> None:
        print(s, flush=True)
        if prog:
            try:
                Path(prog).write_text(json.dumps({"note": s, "credits_used": credits.used}) + "\n")
            except OSError:
                pass

    errs: dict[str, int] = {}

    def step(call, ms):
        for m in ms:
            if m["signature"] in cp["migrations"]:
                continue
            try:
                r = process_migration(out, call, credits, m, a.max_pages)
            except vb.CapReached:
                cp["credits_used"] = credits.used
                vb.save_cp(cpf, cp)
                progress(f"credit cap reached at {credits.used}")
                return EXIT_CAP
            except RuntimeError as exc:
                r = {"signature": m["signature"], "status": "error", "error": str(exc)[:150], "retry": True}
            if r.get("retry"):
                errs[m["signature"]] = errs.get(m["signature"], 0) + 1
                progress(f"error {r['error']} (try {errs[m['signature']]})")
                if errs[m["signature"]] < 3:
                    time.sleep(3)
                    continue
                r = {"signature": m["signature"], "status": "error_final", "error": r["error"]}
            cp["migrations"][m["signature"]] = r
            cp["credits_used"] = credits.used
            vb.save_cp(cpf, cp)
            progress(f"{len(cp['migrations'])} migrations done, credits={credits.used}, last {r['status']} txs={r.get('txs')} pages={r.get('pages')}")
        return 0

    if a.mode == "probe":
        start = datetime.strptime(a.probe_from, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
        hrs = [h for h in L.exploration_hours() if h >= start][:48]
        ms = migrations_from_tape(hours=hrs)[:40]
        print(f"probe: scanning up to {len(ms)} migrations for {a.n} WSOL pools", flush=True)
        slot = acquire_slot(a.lockdir)
        call = vb.http_rpc(url, vb.Pacer(a.rps))
        res = []
        for m in ms:
            r = process_migration(out, call, credits, m, a.max_pages)
            res.append(r)
            print(json.dumps(r), flush=True)
            if sum(1 for x in res if x["status"] == "ok") >= a.n:
                break
        print(json.dumps({"probe_credits_assumed": credits.used, "migrations_resolved": len(res), "pools_fetched": sum(1 for x in res if x["status"] == "ok"), "non_wsol": sum(1 for x in res if x["status"] == "non_wsol_quote")}))
        slot.close()
        return 0
    deadline = time.time() + a.wait_h * 3600
    while True:
        done_hours = len([1 for h in L.exploration_hours() if os.path.exists(L.hour_file(h))])
        ms = migrations_from_tape()
        todo = [m for m in ms if m["signature"] not in cp["migrations"]]
        progress(f"scan: {done_hours}/552 exploration hours, {len(ms)} migrations, {len(todo)} to do")
        if todo:
            slot = acquire_slot(a.lockdir)
            call = vb.http_rpc(url, vb.Pacer(a.rps))
            rc = step(call, sorted(todo, key=lambda m: -m["block_time"]))  # newest first = block A before block B
            slot.close()
            if rc:
                return rc
            continue
        if done_hours >= 552 or not a.wait_backfill or time.time() > deadline:
            break
        time.sleep(600)
    progress(f"done credits_used={credits.used}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
