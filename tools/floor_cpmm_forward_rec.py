#!/usr/bin/env python3
"""floor_cpmm_forward_rec: FORWARD-ONLY pool-state recorder for Raydium CPMM pools created by LaunchLab migrations with WSOL as the quote. RECORDING ONLY.

    python -m tools.floor_cpmm_forward_rec --out-dir /data2/mal/floor-cpmm/forward --max-hours 336

NOTHING IS PRICED and nothing is decided here: no entry, no exit, no fee model, no P&L. The DECLARE for any pricing comes later, with QP. PAPER ONLY, public RPC only (no Helius key, no
credits). Pool decoders and constants come from tools.launchlab_cpmm_fetch and scripts.research.floor_cpmm_meta (PR #1480 and its meta tool); they are not copied.

Pools: the 242-record WSOL list (sha256 prefix checked) plus migrations found forward. Discovery polls getSignaturesForAddress on the CPMM create-pool fee receiver (every CPMM
initialize pays it; the address is --discovery-address, public-RPC verified to answer) and keeps only transactions at or after the recorder's start instant whose CPMM initialize sits
beside a top-level LaunchLab instruction with WSOL in the pool (launchlab_cpmm_fetch.pool_from_migration_tx).

Each --interval-s: one getMultipleAccounts batch (<= 100 addresses per call) over every pool's PoolState and its two vaults; getTokenSupply per pool once an hour; at discovery one
getMultipleAccounts over PoolState, then LP mint, token mint and AmmConfig (lp supply, mint authority set?, trade fee rate). One JSONL row per pool per poll goes to
OUT/YYYY-MM-DDTHH.jsonl (created exclusive; fsync at the hour close; a restart inside an hour writes YYYY-MM-DDTHH.rN.jsonl). OUT/status.json holds counters only; OUT/pools.json holds
the pool table and the discovery cursor (resume state). Rate: token bucket at --rps (default 2), exponential backoff on 429.
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

SCHEMA = "floor-cpmm-forward-rec-v1"
RPC_URL = "https://api.mainnet-beta.solana.com"
DISCOVERY_ADDRESS = "G11FKBRaAkHAKuLCgLM6K6NUc9rTjPAznRCjZifrTQe2"  # CPMM create-pool fee receiver (every initialize pays it)
LIST_PATH = "/data2/mal/floor-cpmm/migrations-1121b.json"
LIST_SHA_PREFIX = "1437bcc5"
GMA_BATCH = 100
VAULT0_OFF, VAULT1_OFF, PS_TAIL = 72, 104, (341, 373)  # PoolState vault offsets; raw bytes after lp_supply kept for a later decode (fees owed), not interpreted here
TOKEN_ACCOUNT_AMOUNT_OFF = 64
SUPPLY_EVERY_S = 3600
MAX_PENDING_TRIES = 20


class Refused(Exception):
    pass


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
    """JSON-RPC over HTTPS through the bucket. 429 (HTTP or RPC code) backs off 1, 2, 4 ... 60 s and retries; other failures retry 5 times then raise."""
    import urllib.error
    import urllib.request

    opener = opener or urllib.request.urlopen

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
                    sleep(back)
                    back = min(60.0, back * 2)
                    continue
                ctr.bump("rpc_errors")
                sleep(min(30.0, back))
                back *= 2
                continue
            except (TimeoutError, urllib.error.URLError, ConnectionError, json.JSONDecodeError, OSError) as exc:
                last = type(exc).__name__
                ctr.bump("rpc_errors")
                sleep(min(30.0, back))
                back *= 2
                continue
            if payload.get("error"):
                e = payload["error"]
                last = f"rpc {e.get('code')}"
                if e.get("code") == 429:
                    ctr.bump("rpc_429")
                    sleep(back)
                    back = min(60.0, back * 2)
                    continue
                raise RuntimeError(f"{method} rpc {e.get('code')}")
            return payload.get("result")
        raise RuntimeError(f"{method} gave up ({last})")

    return call


# ------------------------------------------------------------------------------------------------------------------------ decoders
def decode_pool(b: bytes) -> dict[str, Any]:
    d = M.decode_pool_state(b)  # amm_config, lp_mint, mint0, mint1, lp_supply (raises M.Unreadable when short)
    d["vault0"], d["vault1"] = M.pk(b, VAULT0_OFF), M.pk(b, VAULT1_OFF)
    d["ps_tail_b64"] = base64.b64encode(b[PS_TAIL[0]:PS_TAIL[1]]).decode() if len(b) >= PS_TAIL[1] else None
    return d


def decode_token_amount(b: bytes) -> int:
    if len(b) < TOKEN_ACCOUNT_AMOUNT_OFF + 8:
        raise M.Unreadable("token_account_short")
    return struct.unpack_from("<Q", b, TOKEN_ACCOUNT_AMOUNT_OFF)[0]


def gma(call: Callable, ctr: Counters, addrs: list[str]) -> tuple[list[tuple[bytes, str] | None], int | None]:
    """getMultipleAccounts in batches of <= 100. Returns the decoded accounts (None for a missing one) and the slot of the LAST batch (first batch slot if only one)."""
    res: list[tuple[bytes, str] | None] = []
    slots: list[int] = []
    for i in range(0, len(addrs), GMA_BATCH):
        ctr.bump("gma_calls")
        r = call("getMultipleAccounts", [addrs[i:i + GMA_BATCH], {"encoding": "base64", "commitment": "confirmed"}]) or {}
        slots.append((r.get("context") or {}).get("slot"))
        vals = r.get("value") or []
        if len(vals) != len(addrs[i:i + GMA_BATCH]):
            raise RuntimeError("getMultipleAccounts length mismatch")
        for v in vals:
            if not v:
                res.append(None)
                continue
            try:
                res.append((base64.b64decode(v["data"][0], validate=True), v["owner"]))
            except Exception:  # noqa: BLE001
                res.append(None)
    return res, (slots[0] if len(slots) == 1 else (slots[-1] if slots else None))


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
    filled record (vaults, mints, LP mint, AmmConfig fee rate, LP supply, token-mint authority-set flag) or None when the PoolState is not readable (retry later)."""
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
               "lp_supply_at_discovery": None, "mint_authority_set": None, "trade_fee_rate": None, "supply_ms": 0, "token_supply": None}
        lp, tk, cfg = got.get(d["lp_mint"]), got.get(tok), got.get(d["amm_config"])
        try:
            if lp is not None:
                out["lp_supply_at_discovery"] = M.decode_mint(lp[0], lp[1])["supply"]
            if tk is not None:
                out["mint_authority_set"] = M.decode_mint(tk[0], tk[1])["mint_authority"] is not None
            if cfg is not None:
                out["trade_fee_rate"] = M.decode_amm_config(cfg[0])["trade_fee_rate"]
        except M.Unreadable:
            pass
        res.append(out)
    return res


def discover_pool(call: Callable, ctr: Counters, rec: dict[str, Any]) -> dict[str, Any] | None:
    return discover_batch(call, ctr, [rec])[0]


def token_supply(call: Callable, ctr: Counters, mint: str) -> int | None:
    ctr.bump("supply_calls")
    r = call("getTokenSupply", [mint, {"commitment": "confirmed"}]) or {}
    try:
        return int(((r.get("value") or {}).get("amount")))
    except (TypeError, ValueError):
        return None


def poll_rows(call: Callable, ctr: Counters, pools: list[dict[str, Any]], now_ms: int) -> list[dict[str, Any]]:
    """One row per pool from ONE getMultipleAccounts pass over [pool, vault0, vault1] x pools. A missing or undecodable account gives a row with `err` and null fields."""
    addrs: list[str] = []
    for p in pools:
        addrs += [p["pool"], p["vault0"], p["vault1"]]
    accs, slot = gma(call, ctr, addrs)
    rows = []
    for i, p in enumerate(pools):
        ps, v0, v1 = accs[3 * i], accs[3 * i + 1], accs[3 * i + 2]
        row: dict[str, Any] = {"ms": now_ms, "slot": slot, "pool": p["pool"], "token_mint": p["token_mint"], "base_reserve": None, "quote_reserve": None,
                               "token_supply": p.get("token_supply"), "lp_supply": None, "mint_authority_set": p.get("mint_authority_set"),
                               "trade_fee_rate": p.get("trade_fee_rate"), "ps_tail_b64": None, "err": None}
        try:
            if ps is None or v0 is None or v1 is None:
                raise M.Unreadable("account_missing")
            d = decode_pool(ps[0])
            a0, a1 = decode_token_amount(v0[0]), decode_token_amount(v1[0])
            row["quote_reserve"], row["base_reserve"] = (a0, a1) if p["wsol_is_token0"] else (a1, a0)
            row["lp_supply"], row["ps_tail_b64"] = d["lp_supply"], d["ps_tail_b64"]
        except M.Unreadable as exc:
            row["err"] = str(exc)
            ctr.bump("row_errors")
        rows.append(row)
    return rows


# ------------------------------------------------------------------------------------------------------------------------ output files
def hour_name(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")


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
        fd = os.open(self.out, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        self.f, self.hour = None, None
        self.ctr.bump("files_closed")


def atomic_write(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# ------------------------------------------------------------------------------------------------------------------------ discovery of new migrations
def discover_new(call: Callable, ctr: Counters, state: dict[str, Any], start_s: int, address: str = DISCOVERY_ADDRESS, max_pages: int = 5, max_tx: int = 50,
                 stop: Callable[[], bool] = lambda: False) -> list[dict[str, Any]]:
    """Poll getSignaturesForAddress (newest first, `until` = the cursor), queue unseen successful signatures at or after start_s, then resolve at most `max_tx` queued ones with
    getTransaction (the rest stay pending for the next pass). `stop()` is checked before every call. Returns new pool records {pool, token_mint, bt, sig, src}.
    state: {cursor, pending: {sig: [bt, tries]}}."""
    before, newest = None, None
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
            if s.get("err") is None:
                state["pending"].setdefault(s["signature"], [bt, 0])
        before = page[-1]["signature"]
        if len(page) < 100 or (page[-1].get("blockTime") or start_s) < start_s:
            break
    if newest:
        state["cursor"] = newest
    found = []
    done = 0
    for sig in sorted(state["pending"], key=lambda k: (state["pending"][k][0] or 0, k)):
        if done >= max_tx or stop():
            break
        done += 1
        ctr.bump("tx_calls")
        tx = call("getTransaction", [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}])
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
        max_pools: int = 100, max_tx: int = 50, max_supply: int = 50, progress: Callable[[str], None] | None = None) -> int:
    """Passes of: bounded discovery of new migrations, ONE batch of <= max_pools waiting pools resolved, <= max_supply token-supply calls, one poll, save, one progress line.
    The deadline is checked before every RPC-issuing step; reaching it exits cleanly with a final save."""
    out.mkdir(parents=True, exist_ok=True)
    sp, pp = out / "status.json", out / "pools.json"
    status = load_json(sp, {})
    for k, v in (status.get("counters") or {}).items():
        ctr[k] = v
    start_ms = status.get("start_ms") or int(now() * 1000)
    state = load_json(pp, {"pools": {}, "disc": {"cursor": None, "pending": {}}, "waiting": {}})
    for r in pools_list:  # list pools are known at start; discovery state per pool fills in below
        if r["pool"] not in state["pools"] and r["pool"] not in state["waiting"]:
            state["waiting"][r["pool"]] = r
    writer = HourlyWriter(out, ctr)
    deadline_ms = start_ms + int(max_hours * 3600_000)
    last_disc = None
    if progress is None:
        def progress(line: str) -> None:
            print(line, file=sys.stderr, flush=True)

    def stop() -> bool:
        return int(now() * 1000) >= deadline_ms

    def save(final: bool = False) -> None:
        atomic_write(pp, state)
        atomic_write(sp, {"schema": SCHEMA, "start_ms": start_ms, "deadline_ms": deadline_ms, "last_ms": int(now() * 1000), "pools": len(state["pools"]),
                          "waiting": len(state["waiting"]), "done": final, "counters": dict(ctr)})

    polls = 0
    try:
        while not stop() and (max_polls is None or polls < max_polls):
            t0 = now()
            if last_disc is None or t0 - last_disc >= discover_s:
                last_disc = t0
                for rec in discover_new(call, ctr, state["disc"], start_ms // 1000, discovery_address, max_tx=max_tx, stop=stop):
                    if rec["pool"] not in state["pools"]:
                        state["waiting"].setdefault(rec["pool"], rec)
                save()
            if state["waiting"] and not stop():
                batch = list(state["waiting"].items())[:max_pools]
                for (pool, rec), full in zip(batch, discover_batch(call, ctr, [r for _, r in batch])):
                    del state["waiting"][pool]
                    if full is not None:
                        state["pools"][pool] = full
                        ctr.bump("pools_discovered")
                    else:
                        state["waiting"][pool] = rec  # unreadable now: back of the queue, retried on a later pass
                        ctr.bump("discover_retry")
                save()
            ms = int(now() * 1000)
            nsup = 0
            for p in state["pools"].values():  # token supply once per pool per hour, at most max_supply calls per pass
                if nsup >= max_supply or stop():
                    break
                if ms - p["supply_ms"] >= SUPPLY_EVERY_S * 1000:
                    nsup += 1
                    s = token_supply(call, ctr, p["token_mint"])
                    if s is not None:
                        p["token_supply"], p["supply_ms"] = s, ms
            if state["pools"] and not stop():
                ms = int(now() * 1000)
                rows = poll_rows(call, ctr, list(state["pools"].values()), ms)
                writer.write(ms, rows)
                ctr.bump("polls")
            polls += 1
            save()
            progress(f"pass {polls} pools={len(state['pools'])} waiting={len(state['waiting'])} polls={ctr.get('polls', 0)} rpc_calls={ctr.get('rpc_calls', 0)} "
                     f"rpc_429={ctr.get('rpc_429', 0)} secs={now() - t0:.1f}")
            sleep(max(0.0, min(interval_s - (now() - t0), deadline_ms / 1000 - now())))
    finally:
        writer.close()
        save(final=stop())
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--interval-s", type=float, default=60.0)
    ap.add_argument("--rps", type=float, default=2.0)
    ap.add_argument("--max-hours", type=float, default=336.0)
    ap.add_argument("--discover-s", type=float, default=300.0)
    ap.add_argument("--max-pools", type=int, default=100, help="pools resolved per pass")
    ap.add_argument("--max-tx", type=int, default=50, help="getTransaction resolutions per pass")
    ap.add_argument("--max-supply", type=int, default=50, help="getTokenSupply calls per pass")
    ap.add_argument("--list", default=LIST_PATH)
    ap.add_argument("--list-sha256-prefix", default=LIST_SHA_PREFIX)
    ap.add_argument("--discovery-address", default=DISCOVERY_ADDRESS)
    ap.add_argument("--rpc-url", default=RPC_URL)
    a = ap.parse_args(argv)
    if a.rps > 10:
        raise Refused("rps_above_public_limit")
    out = Path(a.out_dir)
    C.check_window_out_dir(out)
    ctr = Counters()
    call = http_call(TokenBucket(a.rps), ctr, url=a.rpc_url)
    return run(out, call, ctr, load_list(a.list, a.list_sha256_prefix), interval_s=a.interval_s, max_hours=a.max_hours, discover_s=a.discover_s, discovery_address=a.discovery_address,
               max_pools=a.max_pools, max_tx=a.max_tx, max_supply=a.max_supply)


if __name__ == "__main__":
    sys.exit(main())
