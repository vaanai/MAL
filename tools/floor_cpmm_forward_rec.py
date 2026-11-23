#!/usr/bin/env python3
"""floor_cpmm_forward_rec: FORWARD-ONLY pool-state recorder for Raydium CPMM pools created by LaunchLab migrations with WSOL as the quote. RECORDING ONLY.

    python -m tools.floor_cpmm_forward_rec --out-dir /data2/mal/floor-cpmm/forward --max-hours 336

NOTHING IS PRICED and nothing is decided here: no entry, no exit, no fee model, no P&L. The DECLARE for any pricing comes later, with QP. PAPER ONLY, public RPC only (no Helius key, no
credits; a --rpc-url naming helius or carrying an api-key is refused). Pool decoders and constants come from tools.launchlab_cpmm_fetch and scripts.research.floor_cpmm_meta (PR #1480
and its meta tool); they are not copied.

Pools: the 242-record WSOL list (sha256 prefix checked) plus migrations found forward. Forward discovery polls getSignaturesForAddress on the CPMM create-pool fee receiver
(--discovery-address, default DNXgeM9...: every mainnet CPMM initialize pays it) and keeps only transactions at or after the recorder's start instant whose CPMM initialize sits beside
a top-level LaunchLab instruction with WSOL in the pool (launchlab_cpmm_fetch.pool_from_migration_tx). COVERAGE GAP: pools migrated between the list's last record (bt 2026-11-21T08:51:11Z)
and the recorder's start are in neither the list nor discovery. Rows are forward either way.

Each --interval-s: pools are packed WHOLE into getMultipleAccounts calls (4 accounts per pool: PoolState, vault0, vault1, token mint; 25 pools per call), so one pool's state, both
reserves and the token supply always come from ONE call, and each row carries the context slot of its own call. At discovery, in batches (never per pool): PoolState, then LP mint, token
mint and AmmConfig; LP lock share from getTokenAccountsByOwner (<= --max-lock pools per pass, the pool is polled meanwhile with null lock fields).

PoolState layout (Raydium cp-swap programs/cp-swap/src/states/pool.rs, 637 bytes; checked on mainnet 2026-11-23 on a list pool: open_time @373 equalled its migration time):
amm_config 8, pool_creator 40, vault0 72, vault1 104, lp_mint 136, mint0 168, mint1 200, prog0 232, prog1 264, observation 296, auth_bump 328, status 329, lp_decimals 330, mint0_dec 331,
mint1_dec 332, lp_supply 333, protocol_fees_token_0 341, protocol_fees_token_1 349, fund_fees_token_0 357, fund_fees_token_1 365, open_time 373, recent_epoch 381, creator_fee_on 389 (u8),
enable_creator_fee 390 (bool), padding 391..396, creator_fees_token_0 397, creator_fees_token_1 405, padding [u64; 28] 413..637. Owed fees (protocol, fund, creator) are part of the vault
balances; the raw bytes 341..413 are kept in every row (ps_tail_b64) next to the decoded values.

One JSONL row per pool per poll goes to OUT/YYYY-MM-DDTHH.jsonl (created exclusive; fsync at the hour close; a restart inside an hour writes YYYY-MM-DDTHH.rN.jsonl). OUT/status.json holds
counters, the gap list and the cursor times; OUT/pools.json holds the pool table and the discovery cursor (resume state). Rate: token bucket at --rps (default 2); 429 and every other RPC
error back off and retry; a step that still fails is counted and skipped, never fatal. Only --max-failed-polls CONSECUTIVE failed polls end the run (exit 2, final status written).
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import base64
import hashlib
import json
import os
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from scripts.research import floor_cpmm_meta as M
from tools import launchlab_cpmm_fetch as C
from tools import venue_backfill as vb

SCHEMA = "floor-cpmm-forward-rec-v2"
RPC_URL = "https://api.mainnet-beta.solana.com"
DISCOVERY_ADDRESS = "DNXgeM9EiiaAbaWvwjHj9fQQLAX5ZsfHyvmYUNRAdNC8"  # mainnet CPMM create-pool fee receiver (764 successful txs 2026-11-15..11-23, all CPMM initializes)
LIST_PATH = "/data2/mal/floor-cpmm/migrations-1121b.json"
LIST_SHA_PREFIX = "1437bcc5"
GMA_BATCH = 100
ACCTS_PER_POOL = 4  # PoolState, vault0, vault1, token mint
POOLS_PER_CALL = GMA_BATCH // ACCTS_PER_POOL
VAULT0_OFF, VAULT1_OFF = 72, 104
FEE_OFFS = {"protocol_fees_token_0": 341, "protocol_fees_token_1": 349, "fund_fees_token_0": 357, "fund_fees_token_1": 365}
OPEN_TIME_OFF, CREATOR_FEE_ON_OFF, ENABLE_CREATOR_FEE_OFF = 373, 389, 390
CREATOR_FEE_OFFS = {"creator_fees_token_0": 397, "creator_fees_token_1": 405}
PS_TAIL = (341, 413)
PS_FULL_LEN = 413
TOKEN_ACCOUNT_AMOUNT_OFF = 64
MINT_SUPPLY_OFF = 36
MAX_PENDING_TRIES = 20
MAX_GAPS_KEPT = 200


class Refused(Exception):
    pass


class RpcError(RuntimeError):
    pass


class Deferred(RpcError):
    """A backoff would pass the caller's time budget: the call is given up for now, without sleeping, to be retried on a later pass."""


def check_rpc_url(url: str) -> str:
    """Public endpoints only: refuse a paid-provider host or any API key in the URL."""
    u = url.lower()
    if not u.startswith("https://"):
        raise Refused("rpc_url_not_https")
    if "helius" in u:
        raise Refused("rpc_url_names_helius")
    query = u.split("?", 1)[1] if "?" in u else ""
    if any(k in query for k in ("api-key", "apikey", "api_key")):
        raise Refused("rpc_url_carries_api_key")
    return url


class TokenBucket:
    """Token bucket: `rps` tokens per second, capacity max(1, burst). take() sleeps until a token is available."""

    def __init__(self, rps: float, burst: float = 1.0, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        if rps <= 0:
            raise ValueError("rps must be > 0")
        self.rps, self.cap, self._clock, self._sleep = rps, max(1.0, burst), clock, sleep
        self.tokens, self.t = self.cap, clock()

    def take(self) -> float:
        waited = 0.0
        while True:
            now = self._clock()
            self.tokens = min(self.cap, self.tokens + (now - self.t) * self.rps)
            self.t = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return waited
            need = (1.0 - self.tokens) / self.rps
            self._sleep(need)
            waited += need


class Counters(dict):
    def bump(self, k: str, n: int = 1) -> None:
        self[k] = self.get(k, 0) + n


def http_call(bucket: TokenBucket, ctr: Counters, url: str = RPC_URL, timeout: float = 30.0, sleep: Callable[[float], None] = time.sleep,
              opener: Callable[..., Any] | None = None) -> Callable[[str, list[Any]], Any]:
    """JSON-RPC over HTTPS through the bucket. 429 (HTTP or RPC code) backs off 1, 2, 4 ... 60 s and retries; every other failure (HTTP status, transport error, any RPC error code such as
    -32015) is counted by code, backed off (1, 2, 4 ... 30 s) and retried. After 8 attempts the call raises RpcError; the caller counts it and goes on."""
    import urllib.error
    import urllib.request

    check_rpc_url(url)
    opener = opener or urllib.request.urlopen
    ctl: dict[str, Any] = {"until": None, "now": time.time}  # run() sets a time budget here for the discovery step

    def backoff(wait: float) -> None:
        if ctl["until"] is not None and ctl["now"]() + wait > ctl["until"]:
            ctr.bump("rpc_deferred")
            raise Deferred(f"backoff {wait:.0f}s passes the budget")
        sleep(wait)

    def call(method: str, params: list[Any]) -> Any:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        last, back = "?", 1.0
        for _ in range(8):
            bucket.take()
            ctr.bump("rpc_calls")
            try:
                req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
                with opener(req, timeout=timeout) as resp:
                    payload = json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                last = f"http {exc.code}"
                if exc.code == 429:
                    ctr.bump("rpc_429")
                    backoff(back)
                    back = min(60.0, back * 2)
                    continue
                ctr.bump(f"rpc_err_http_{exc.code}")
                backoff(min(30.0, back))
                back *= 2
                continue
            except (TimeoutError, urllib.error.URLError, ConnectionError, json.JSONDecodeError, OSError) as exc:
                last = type(exc).__name__
                ctr.bump(f"rpc_err_{last}")
                backoff(min(30.0, back))
                back *= 2
                continue
            if payload.get("error"):
                e = payload["error"]
                last = f"rpc {e.get('code')}"
                if e.get("code") == 429:
                    ctr.bump("rpc_429")
                    backoff(back)
                    back = min(60.0, back * 2)
                    continue
                ctr.bump(f"rpc_err_{e.get('code')}")
                backoff(min(30.0, back))
                back *= 2
                continue
            return payload.get("result")
        ctr.bump("rpc_gave_up")
        raise RpcError(f"{method} gave up ({last})")

    call.ctl = ctl  # type: ignore[attr-defined]
    return call


# ------------------------------------------------------------------------------------------------------------------------ decoders
def decode_pool(b: bytes) -> dict[str, Any]:
    d = M.decode_pool_state(b)  # amm_config, lp_mint, mint0, mint1, lp_supply (raises M.Unreadable when short)
    d["vault0"], d["vault1"] = M.pk(b, VAULT0_OFF), M.pk(b, VAULT1_OFF)
    full = len(b) >= PS_FULL_LEN
    for k, o in {**FEE_OFFS, **CREATOR_FEE_OFFS}.items():
        d[k] = struct.unpack_from("<Q", b, o)[0] if full else None
    d["open_time"] = struct.unpack_from("<Q", b, OPEN_TIME_OFF)[0] if full else None
    d["creator_fee_on"] = b[CREATOR_FEE_ON_OFF] if full else None
    d["enable_creator_fee"] = bool(b[ENABLE_CREATOR_FEE_OFF]) if full else None
    d["ps_tail_b64"] = base64.b64encode(b[PS_TAIL[0]:PS_TAIL[1]]).decode() if full else None
    return d


def decode_token_amount(b: bytes) -> int:
    if len(b) < TOKEN_ACCOUNT_AMOUNT_OFF + 8:
        raise M.Unreadable("token_account_short")
    return struct.unpack_from("<Q", b, TOKEN_ACCOUNT_AMOUNT_OFF)[0]


def decode_mint_supply(b: bytes) -> int:
    if len(b) < MINT_SUPPLY_OFF + 8:
        raise M.Unreadable("mint_short")
    return struct.unpack_from("<Q", b, MINT_SUPPLY_OFF)[0]


def gma_slots(call: Callable, ctr: Counters, addrs: list[str], batch: int = GMA_BATCH) -> tuple[list[tuple[bytes, str] | None], list[int | None]]:
    """getMultipleAccounts in batches of <= `batch`. Returns the decoded accounts (None for a missing one) and, per address, the context slot of the call that returned it."""
    res: list[tuple[bytes, str] | None] = []
    slots: list[int | None] = []
    for i in range(0, len(addrs), batch):
        chunk = addrs[i:i + batch]
        ctr.bump("gma_calls")
        r = call("getMultipleAccounts", [chunk, {"encoding": "base64", "commitment": "confirmed"}]) or {}
        slot = (r.get("context") or {}).get("slot")
        vals = r.get("value") or []
        if len(vals) != len(chunk):
            raise RpcError("getMultipleAccounts length mismatch")
        for v in vals:
            slots.append(slot)
            if not v:
                res.append(None)
                continue
            try:
                res.append((base64.b64decode(v["data"][0], validate=True), v["owner"]))
            except Exception:  # noqa: BLE001
                res.append(None)
    return res, slots


def gma(call: Callable, ctr: Counters, addrs: list[str]) -> tuple[list[tuple[bytes, str] | None], int | None]:
    """As gma_slots, returning the slot of the LAST call (discovery use only; polls use per-row slots)."""
    res, slots = gma_slots(call, ctr, addrs)
    return res, (slots[-1] if slots else None)


# ------------------------------------------------------------------------------------------------------------------------ pool table
def load_list(path: str, sha_prefix: str = LIST_SHA_PREFIX) -> list[dict[str, Any]]:
    raw = Path(path).read_bytes()
    if sha_prefix and not hashlib.sha256(raw).hexdigest().startswith(sha_prefix):
        raise Refused("list_sha256_mismatch")
    out = []
    for r in json.loads(raw):
        if r.get("quote", C.WSOL) != C.WSOL:
            continue
        pool, tok = r.get("pool"), r.get("tokmint")
        if not pool or not tok:
            raise Refused(f"bad_list_record: {json.dumps(r)[:80]}")
        out.append({"pool": pool, "token_mint": tok, "bt": r.get("bt"), "src": "list"})
    return out


def discover_batch(call: Callable, ctr: Counters, recs: list[dict[str, Any]]) -> list[dict[str, Any] | None]:
    """Fill pool records in batches, never per pool: one getMultipleAccounts pass over the PoolStates, then one over the distinct LP mints, token mints and AmmConfigs. Each result is the
    filled record (vaults, mints, LP mint, all four AmmConfig fee rates, LP supply and burned amount, token-mint authority / freeze / token-program / transfer-fee flags) or None when the
    PoolState is not readable (retry later). The LP lock share is a separate step (lock_step)."""
    pss, _ = gma(call, ctr, [r["pool"] for r in recs])
    parts: list[tuple[dict[str, Any], dict[str, Any], bool, str] | None] = []
    for rec, ps in zip(recs, pss):
        d = None
        if ps is not None and ps[1] == C.CPMM:
            try:
                d = decode_pool(ps[0])
            except M.Unreadable:
                d = None
        if d is None or C.WSOL not in (d["mint0"], d["mint1"]):
            parts.append(None)
            continue
        wsol0 = d["mint0"] == C.WSOL
        tok = d["mint1"] if wsol0 else d["mint0"]
        parts.append(None if rec.get("token_mint") and rec["token_mint"] != tok else (rec, d, wsol0, tok))
    addrs = sorted({a for p in parts if p for a in (p[1]["lp_mint"], p[3], p[1]["amm_config"])})
    got = dict(zip(addrs, gma(call, ctr, addrs)[0])) if addrs else {}
    res: list[dict[str, Any] | None] = []
    for p in parts:
        if p is None:
            res.append(None)
            continue
        rec, d, wsol0, tok = p
        out = {**rec, "token_mint": tok, "lp_mint": d["lp_mint"], "amm_config": d["amm_config"], "vault0": d["vault0"], "vault1": d["vault1"], "wsol_is_token0": wsol0,
               "lp_supply_ps_at_discovery": d["lp_supply"], "lp_supply_mint": None, "lp_burned": None, "lp_locked_lock_authority": None, "lp_locked_frac": None, "lock_done": False,
               "mint_authority_set": None, "freeze_authority_set": None, "token_program": None, "token_2022_transfer_fee": None,
               "trade_fee_rate": None, "protocol_fee_rate": None, "fund_fee_rate": None, "creator_fee_rate": None}
        lp, tk, cfg = got.get(d["lp_mint"]), got.get(tok), got.get(d["amm_config"])
        try:
            if lp is not None:
                out["lp_supply_mint"] = M.decode_mint(lp[0], lp[1])["supply"]
                if out["lp_supply_mint"] <= d["lp_supply"]:
                    out["lp_burned"] = d["lp_supply"] - out["lp_supply_mint"]
            if tk is not None:
                m = M.decode_mint(tk[0], tk[1])
                out["mint_authority_set"] = m["mint_authority"] is not None
                out["freeze_authority_set"] = m["freeze_authority"] is not None
                out["token_program"], out["token_2022_transfer_fee"] = m["token_program"], m["transfer_fee"]
            if cfg is not None:
                out.update(M.decode_amm_config(cfg[0]))
        except M.Unreadable:
            pass
        res.append(out)
    return res


def discover_pool(call: Callable, ctr: Counters, rec: dict[str, Any]) -> dict[str, Any] | None:
    return discover_batch(call, ctr, [rec])[0]


def lock_one(call: Callable, p: dict[str, Any]) -> None:
    """LP lock share, once per pool: (burned + LP held by the lock authority) / PoolState.lp_supply, as floor_cpmm_meta.build_meta computes it. getTokenAccountsByOwner, public RPC."""
    lock = M.locked_lp(call, vb.Credits(10**12, 0), p["lp_mint"])
    p["lp_locked_lock_authority"] = lock
    if p.get("lp_burned") is not None and p["lp_supply_ps_at_discovery"] > 0:
        p["lp_locked_frac"] = (p["lp_burned"] + lock) / p["lp_supply_ps_at_discovery"]
    p["lock_done"] = True


def poll_rows(call: Callable, ctr: Counters, pools: list[dict[str, Any]], now_ms: int) -> list[dict[str, Any]]:
    """One row per pool. Pools are packed whole, POOLS_PER_CALL per getMultipleAccounts call over [PoolState, vault0, vault1, token mint], so a row's state, reserves and supply come from
    one call and its `slot` is that call's context slot. A failed call is counted and its pools get no row this poll; a missing or undecodable account gives a row with `err`."""
    rows: list[dict[str, Any]] = []
    for i in range(0, len(pools), POOLS_PER_CALL):
        chunk = pools[i:i + POOLS_PER_CALL]
        addrs: list[str] = []
        for p in chunk:
            addrs += [p["pool"], p["vault0"], p["vault1"], p["token_mint"]]
        try:
            accs, slots = gma_slots(call, ctr, addrs, batch=len(addrs))
        except Exception:  # noqa: BLE001
            ctr.bump("poll_chunk_errors")
            continue
        for j, p in enumerate(chunk):
            ps, v0, v1, mt = accs[4 * j:4 * j + 4]
            row: dict[str, Any] = {"ms": now_ms, "slot": slots[4 * j], "pool": p["pool"], "token_mint": p["token_mint"], "base_reserve": None, "quote_reserve": None,
                                   "token_supply": None, "token_supply_ms": None, "lp_supply": None, "ps_tail_b64": None, "err": None}
            for k in ("mint_authority_set", "freeze_authority_set", "token_program", "token_2022_transfer_fee", "trade_fee_rate", "protocol_fee_rate", "fund_fee_rate",
                      "creator_fee_rate", "lp_supply_mint", "lp_burned", "lp_locked_lock_authority", "lp_locked_frac"):
                row[k] = p.get(k)
            for k in list(FEE_OFFS) + list(CREATOR_FEE_OFFS) + ["open_time", "creator_fee_on", "enable_creator_fee"]:
                row[k] = None
            try:
                if ps is None or v0 is None or v1 is None:
                    raise M.Unreadable("account_missing")
                d = decode_pool(ps[0])
                a0, a1 = decode_token_amount(v0[0]), decode_token_amount(v1[0])
                row["quote_reserve"], row["base_reserve"] = (a0, a1) if p["wsol_is_token0"] else (a1, a0)
                for k in ["lp_supply", "ps_tail_b64", "open_time", "creator_fee_on", "enable_creator_fee"] + list(FEE_OFFS) + list(CREATOR_FEE_OFFS):
                    row[k] = d[k]
                if mt is not None:
                    row["token_supply"], row["token_supply_ms"] = decode_mint_supply(mt[0]), now_ms
            except M.Unreadable as exc:
                row["err"] = str(exc)
                ctr.bump("row_errors")
            rows.append(row)
    return rows


# ------------------------------------------------------------------------------------------------------------------------ output files
def hour_name(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def fsync_dir(d: Path) -> None:
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class HourlyWriter:
    """One open file per UTC hour. Created exclusive ('x'); a restart inside an hour takes the next free YYYY-MM-DDTHH.rN.jsonl. Rotation (and close) flushes and fsyncs the file and the dir."""

    def __init__(self, out: Path, ctr: Counters) -> None:
        self.out, self.ctr, self.hour, self.f = out, ctr, None, None

    def _open(self, hour: str):
        n = 0
        while True:
            name = f"{hour}.jsonl" if n == 0 else f"{hour}.r{n}.jsonl"
            try:
                return open(self.out / name, "x", encoding="utf-8")
            except FileExistsError:
                n += 1

    def write(self, ms: int, rows: list[dict[str, Any]]) -> None:
        h = hour_name(ms)
        if h != self.hour:
            self.close()
            self.f, self.hour = self._open(h), h
            self.ctr.bump("files_opened")
        for r in rows:
            self.f.write(json.dumps(r, separators=(",", ":"), sort_keys=True) + "\n")
        self.f.flush()
        self.ctr.bump("rows", len(rows))

    def close(self) -> None:
        if self.f is None:
            return
        self.f.flush()
        os.fsync(self.f.fileno())
        self.f.close()
        fsync_dir(self.out)
        self.f, self.hour = None, None
        self.ctr.bump("files_closed")


def atomic_write(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fsync_dir(path.parent)


# ------------------------------------------------------------------------------------------------------------------------ discovery of new migrations
def discover_new(call: Callable, ctr: Counters, state: dict[str, Any], start_s: int, address: str = DISCOVERY_ADDRESS, max_pages: int = 5, max_tx: int = 50,
                 stop: Callable[[], bool] = lambda: False) -> list[dict[str, Any]]:
    """Poll getSignaturesForAddress (newest first, `until` = the cursor), queue unseen successful signatures at or after start_s, then resolve at most `max_tx` queued ones with
    getTransaction (the rest stay pending for the next pass). `stop()` is checked before every call. A failed listing or getTransaction is counted and skipped: a failed getTransaction
    counts as a try against MAX_PENDING_TRIES, after which the signature is dropped (tx_dropped). When the page cap is hit before the old cursor is reached, `disc_gap` is counted and the
    cursor still moves to the newest signature (the signatures in between are skipped). Returns new pool records {pool, token_mint, bt, sig, src}.
    state: {cursor, pending: {sig: [bt, tries]}}."""
    before, newest, listed = None, None, True
    try:
        for _ in range(max_pages):
            if stop():
                break
            opts: dict[str, Any] = {"limit": 100, "commitment": "confirmed"}
            if state.get("cursor"):
                opts["until"] = state["cursor"]
            if before:
                opts["before"] = before
            ctr.bump("sig_calls")
            page = call("getSignaturesForAddress", [address, opts]) or []
            if not page:
                break
            newest = newest or page[0]["signature"]
            for s in page:
                bt = s.get("blockTime")
                if bt is not None and bt < start_s:
                    continue
                if s.get("err") is None and s["signature"] not in state["pending"]:
                    state["pending"][s["signature"]] = [bt, 0]
                    ctr.bump("sigs_seen")
            before = page[-1]["signature"]
            if len(page) < 100 or (page[-1].get("blockTime") or start_s) < start_s:
                break
        else:
            ctr.bump("disc_gap")
    except Deferred:
        ctr.bump("sig_deferred")
        listed = False
    except Exception:  # noqa: BLE001
        ctr.bump("sig_errors")
        listed = False
    if newest and listed:
        state["cursor"] = newest
    found = []
    done = 0
    for sig in sorted(state["pending"], key=lambda k: (state["pending"][k][0] or 0, k)):
        if done >= max_tx or stop():
            break
        done += 1
        ctr.bump("tx_calls")
        try:
            tx = call("getTransaction", [sig, {"encoding": "json", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}])
        except Deferred:
            ctr.bump("tx_deferred")
            break
        except Exception:  # noqa: BLE001
            ctr.bump("tx_errors")
            tx = None
        if not tx:
            state["pending"][sig][1] += 1
            if state["pending"][sig][1] >= MAX_PENDING_TRIES:
                del state["pending"][sig]
                ctr.bump("tx_dropped")
            continue
        del state["pending"][sig]
        bt = tx.get("blockTime")
        if bt is None or bt < start_s:
            continue
        info = C.pool_from_migration_tx(tx)
        if info is None or not info["ll_accounts"] or not info["wsol_in_pool"]:
            ctr.bump("not_launchlab_wsol")
            continue
        found.append({"pool": info["cpmm_pool"], "token_mint": info["token_mint"], "bt": bt, "sig": sig, "src": "forward"})
        ctr.bump("migrations_found")
    return found


# ------------------------------------------------------------------------------------------------------------------------ run
def load_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text()) if path.exists() else default


def run(out: Path, call: Callable, ctr: Counters, pools_list: list[dict[str, Any]], *, interval_s: float = 60.0, max_hours: float = 336.0, discover_s: float = 300.0,
        discovery_address: str = DISCOVERY_ADDRESS, now: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep, max_polls: int | None = None,
        max_pools: int = 100, max_tx: int = 50, max_lock: int = 25, max_failed_polls: int = 60, max_discover_tries: int = 20, discover_budget_s: float = 20.0,
        progress: Callable[[str], None] | None = None) -> int:
    """Passes of: bounded discovery of new migrations, ONE batch of <= max_pools waiting pools resolved, <= max_lock LP-lock reads, one poll, save, one progress line. The deadline is
    checked before every RPC-issuing step and exits cleanly with a final save. Discovery gets `discover_budget_s` per pass and defers (no sleeping) a backoff that would pass it. A step that raises is counted (step_errors, step_errors_<step>) and skipped. A poll that raises or returns
    no rows is a failed poll; `max_failed_polls` of them in a row end the run (return 2, final save). A successful poll after more than two intervals of silence records a gap."""
    out.mkdir(parents=True, exist_ok=True)
    sp, pp = out / "status.json", out / "pools.json"
    status = load_json(sp, {})
    for k, v in (status.get("counters") or {}).items():
        ctr[k] = v
    start_ms = status.get("start_ms") or int(now() * 1000)
    last_ok_ms = status.get("last_ok_ms")
    gaps = list(status.get("gaps") or [])
    state = load_json(pp, {"pools": {}, "disc": {"cursor": None, "pending": {}}, "waiting": {}})
    for r in pools_list:  # list pools are known at start; discovery state per pool fills in below
        if r["pool"] not in state["pools"] and r["pool"] not in state["waiting"]:
            state["waiting"][r["pool"]] = r
    writer = HourlyWriter(out, ctr)
    deadline_ms = start_ms + int(max_hours * 3600_000)
    last_disc = None
    consec = 0
    if progress is None:
        def progress(line: str) -> None:
            print(line, file=sys.stderr, flush=True)

    def stop() -> bool:
        return int(now() * 1000) >= deadline_ms

    def save(final: bool = False) -> None:
        atomic_write(pp, state)
        atomic_write(sp, {"schema": SCHEMA, "start_ms": start_ms, "deadline_ms": deadline_ms, "last_ms": int(now() * 1000), "last_ok_ms": last_ok_ms, "pools": len(state["pools"]),
                          "waiting": len(state["waiting"]), "consecutive_failed_polls": consec, "gaps": gaps[-MAX_GAPS_KEPT:], "done": final, "counters": dict(ctr)})

    def step(name: str, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            ctr.bump("step_errors")
            ctr.bump(f"step_errors_{name}")
            ctr["last_error"] = f"{name}: {type(exc).__name__}"
            return None

    def resolve_waiting() -> None:
        batch = list(state["waiting"].items())[:max_pools]
        for (pool, rec), full in zip(batch, discover_batch(call, ctr, [r for _, r in batch])):
            del state["waiting"][pool]
            if full is not None:
                state["pools"][pool] = full
                ctr.bump("pools_discovered")
                continue
            rec = {**rec, "discover_tries": rec.get("discover_tries", 0) + 1}
            if rec["discover_tries"] >= max_discover_tries:
                ctr.bump("pools_dropped")
            else:
                state["waiting"][pool] = rec  # unreadable now: back of the queue, retried on a later pass
                ctr.bump("discover_retry")

    def lock_step() -> None:
        n = 0
        for p in state["pools"].values():
            if n >= max_lock or stop():
                break
            if not p.get("lock_done"):
                n += 1
                try:
                    lock_one(call, p)
                except Exception:  # noqa: BLE001
                    ctr.bump("lock_errors")

    polls = 0
    code = 0
    try:
        while not stop() and (max_polls is None or polls < max_polls):
            t0 = now()
            if last_disc is None or t0 - last_disc >= discover_s:
                last_disc = t0
                until = now() + discover_budget_s  # discovery has a time budget; what it cannot resolve stays pending for the next pass
                ctl = getattr(call, "ctl", None)
                if ctl is not None:
                    ctl["until"], ctl["now"] = until, now
                try:
                    recs_new = step("discover_new", lambda: discover_new(call, ctr, state["disc"], start_ms // 1000, discovery_address, max_tx=max_tx,
                                                                         stop=lambda: stop() or now() >= until)) or []
                finally:
                    if ctl is not None:
                        ctl["until"] = None
                for rec in recs_new:
                    if rec["pool"] not in state["pools"]:
                        state["waiting"].setdefault(rec["pool"], rec)
                save()
            if state["waiting"] and not stop():
                step("resolve", resolve_waiting)
                save()
            if state["pools"] and not stop():
                step("lock", lock_step)
            rows: list[dict[str, Any]] = []
            if state["pools"] and not stop():
                ms = int(now() * 1000)
                rows = step("poll", lambda: poll_rows(call, ctr, list(state["pools"].values()), ms)) or []
                if rows:
                    writer.write(ms, rows)
                    ctr.bump("polls")
                    if last_ok_ms is not None and ms - last_ok_ms > 2 * interval_s * 1000:
                        gaps.append([last_ok_ms, ms])
                        ctr.bump("gap_ms", ms - last_ok_ms)
                    last_ok_ms, consec = ms, 0
                else:
                    consec += 1
                    ctr.bump("polls_failed")
            polls += 1
            save()
            progress(f"pass {polls} pools={len(state['pools'])} waiting={len(state['waiting'])} polls={ctr.get('polls', 0)} rpc_calls={ctr.get('rpc_calls', 0)} "
                     f"rpc_429={ctr.get('rpc_429', 0)} step_errors={ctr.get('step_errors', 0)} failed_polls_in_a_row={consec} secs={now() - t0:.1f}")
            if consec >= max_failed_polls:
                code = 2
                break
            sleep(max(0.0, min(interval_s - (now() - t0), deadline_ms / 1000 - now())))
    finally:
        writer.close()
        save(final=stop() or code != 0)
    return code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--interval-s", type=float, default=60.0)
    ap.add_argument("--rps", type=float, default=2.0)
    ap.add_argument("--max-hours", type=float, default=336.0)
    ap.add_argument("--discover-s", type=float, default=300.0)
    ap.add_argument("--max-pools", type=int, default=100, help="pools resolved per pass")
    ap.add_argument("--max-tx", type=int, default=50, help="getTransaction resolutions per pass")
    ap.add_argument("--discover-budget-s", type=float, default=20.0, help="time budget of the discovery step per pass")
    ap.add_argument("--max-lock", type=int, default=25, help="LP-lock reads per pass")
    ap.add_argument("--max-failed-polls", type=int, default=60, help="consecutive failed polls that end the run")
    ap.add_argument("--max-discover-tries", type=int, default=20)
    ap.add_argument("--list", default=LIST_PATH)
    ap.add_argument("--list-sha256-prefix", default=LIST_SHA_PREFIX)
    ap.add_argument("--discovery-address", default=DISCOVERY_ADDRESS)
    ap.add_argument("--rpc-url", default=RPC_URL)
    a = ap.parse_args(argv)
    if a.rps > 10:
        raise Refused("rps_above_public_limit")
    check_rpc_url(a.rpc_url)
    out = Path(a.out_dir)
    C.check_window_out_dir(out)
    ctr = Counters()
    call = http_call(TokenBucket(a.rps), ctr, url=a.rpc_url)
    return run(out, call, ctr, load_list(a.list, a.list_sha256_prefix), interval_s=a.interval_s, max_hours=a.max_hours, discover_s=a.discover_s, discovery_address=a.discovery_address,
               max_pools=a.max_pools, max_tx=a.max_tx, max_lock=a.max_lock, max_failed_polls=a.max_failed_polls, max_discover_tries=a.max_discover_tries, discover_budget_s=a.discover_budget_s)


if __name__ == "__main__":
    sys.exit(main())
