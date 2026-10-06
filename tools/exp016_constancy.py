"""EXP-016 P1 V-constancy input builder (plan 11 P1; 13 items 1, 2, 7).

Reads the pool-id sample written by `exp016_screen --emit-constancy-sample`, finds for each pool its EARLIEST successful PumpSwap buy print
on the P2 view tape (by slot, then signature), fetches that transaction (getTransaction, encoding json), decodes the BuyEvent and writes
`{pool, v_implied, quote_reserve, sig, slot, reason}` rows: the exact shape `exp016_screen.check_v_constancy` consumes.

Outcome-blind: from the tape it reads only pool / venue / side / signature / slot / err fields (no price, no P&L, no label, no simulation).
The Helius key is read in Python from the env file and the URL is never printed. stdout carries counts only.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Mapping, Sequence

import tools.exp012_backcheck as bc
import tools.exp015_screen as e15
import tools.exp016_screen as x
import tools.exploration_entry_model as eem
from tools import pumpswap_decompose as dec
from tools import pumpswap_simulate as sim
from tools import pumpswap_virtual_history as pvh

DEFAULT_RPS = 4.0
MAX_RPS = 5.0  # the walkers share the Helius plan
RPC_RETRIES = 3  # further attempts on the SAME signature after an HTTP / JSON-RPC error or a null result
BACKOFF_S = (1.0, 2.0, 4.0)
MIN_BASE_OUT = 1_000_000  # dust (plan 13 item 8): a print with base_amount_out below this many base units ...
MIN_NET_QUOTE = 5_000_000  # ... or net quote (quote_amount_in_with_lp_fee - lp_fee) below 0.005 SOL is skipped, the next print is tried
DEFAULT_MAX_TRIES = 5
GET_TX_CONFIG = {"encoding": "json", "maxSupportedTransactionVersion": 1}
Fetch = Callable[[str], "Mapping[str, Any] | None"]


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def collect_candidates(rows: Iterable[Mapping[str, Any]], pools: Iterable[str], k: int) -> dict[str, list[tuple[int, str]]]:
    """Per sampled pool, the `k` earliest distinct (slot, signature) PumpSwap buy prints. Reads venue/side/pool/signature/slot/err only."""
    want = set(pools)
    out: dict[str, set[tuple[int, str]]] = {p: set() for p in want}
    for r in rows:
        p = r.get("pool")
        if p not in want or r.get("venue") != "pumpswap" or r.get("side") != "buy":
            continue
        sig, slot = r.get("signature"), _int(r.get("slot"))
        if not isinstance(sig, str) or not sig or slot is None:
            continue
        if r.get("err") or r.get("success") is False:  # a failed tx is not a print (the tape normally holds none)
            continue
        s = out[p]
        s.add((slot, sig))
        if len(s) > k:
            s.remove(max(s))
    return {p: sorted(s) for p, s in out.items()}


def decode_sample(tx: Mapping[str, Any] | None, pool: str) -> tuple[dict[str, Any] | None, str]:
    """(fields, reason). fields = {v_implied, quote_reserve} when the tx carries a BuyEvent for `pool` with base_amount_out > 0."""
    if not isinstance(tx, Mapping):
        return None, "rpc_error"
    meta = tx.get("meta")
    if not isinstance(meta, Mapping):
        return None, "tx_no_meta"
    if meta.get("err") is not None:
        return None, "tx_failed"
    try:
        ev = dec.find_buy_event(list(meta.get("logMessages") or []))
    except Exception:  # noqa: BLE001 - a malformed log is a missing event
        return None, "event_undecodable"
    if ev is None:
        return None, "event_missing"
    if ev.get("pool") != pool:
        return None, "event_pool_mismatch"
    if not ev.get("base_amount_out", 0) > 0:
        return None, "event_base_out_zero"
    if ev["base_amount_out"] < MIN_BASE_OUT or pvh.pool_net(ev) < MIN_NET_QUOTE:
        return None, "dust"
    try:
        v = round(pvh.implied_virtual(ev))
    except (ZeroDivisionError, KeyError, ValueError):
        return None, "event_not_decodable_to_v"
    return {"v_implied": int(v), "quote_reserve": int(ev["pool_quote_token_reserves"])}, ""


def fetch_retry(fetch: Fetch, sig: str, sleep: Callable[[float], None]) -> Mapping[str, Any] | None:
    """The same signature again after an error or a null result: up to RPC_RETRIES further attempts with bounded backoff."""
    for i in range(1 + RPC_RETRIES):
        tx = fetch(sig)
        if tx is not None:
            return tx
        if i < RPC_RETRIES:
            sleep(BACKOFF_S[i])
    return None


def resolve_pool(pool: str, cands: Sequence[tuple[int, str]], fetch: Fetch, max_tries: int, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Next print only for DECODE reasons (no event, pool mismatch, zero base out, failed tx, dust). A read that still fails after the retries ends the
    pool with reason rpc_error: the next print would not be a different read of the same chain."""
    reason = "" if cands else "no_buy_print_on_tape"
    for slot, sig in list(cands)[:max_tries]:
        tx = fetch_retry(fetch, sig, sleep)
        if tx is None:
            return {"pool": pool, "v_implied": None, "quote_reserve": None, "sig": None, "slot": None, "reason": "rpc_error"}
        fields, why = decode_sample(tx, pool)
        if fields is not None:
            return {"pool": pool, **fields, "sig": sig, "slot": slot, "reason": ""}
        reason = why
    return {"pool": pool, "v_implied": None, "quote_reserve": None, "sig": None, "slot": None, "reason": reason}


class HttpFetch:
    """getTransaction over Helius, rate limited to `rps`. The URL (key) is held privately and never printed; errors are redacted."""

    def __init__(self, url: str, rps: float):
        self._url, self.interval, self._next, self.calls = url, 1.0 / rps, 0.0, 0

    def __call__(self, sig: str) -> Mapping[str, Any] | None:
        wait = self._next - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._next = time.monotonic() + self.interval
        self.calls += 1
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getTransaction", "params": [sig, GET_TX_CONFIG]}).encode()
        req = urllib.request.Request(self._url, body, {"Content-Type": "application/json"})
        try:
            resp = json.load(urllib.request.urlopen(req, timeout=30))
        except Exception as exc:  # noqa: BLE001
            print(sim.redact_rpc_url(f"getTransaction failed: {type(exc).__name__}"), file=sys.stderr)
            return None
        return resp.get("result") if isinstance(resp, dict) else None


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def guard_views(view_dirs: Sequence[Path]) -> dict[str, Any]:
    """The screen's P2 guards, unchanged: reserved / confirmation paths refused, sealed P2 hours only."""
    ns = SimpleNamespace(p1_fast_dir="", p1_oracle_insample_dir="", p1_oracle_live_dir="", p3_root="", p2_view_dir=list(view_dirs), p4_view_dir=[])
    try:
        g2 = e15.guard_p2(view_dirs, True, True)
        e15.assert_hours_allowed(list(g2["pool"]), with_p4=False)
        x.refuse_extra_reserved(ns)
    except e15.Refused as exc:
        raise x.Refused(str(exc)) from None
    return g2


def iter_view_rows(g2: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    hours_fn = bc.MultiViewHours(dict(g2["roots"]))
    for h in g2["pool"]:
        yield from eem._iter_trades(hours_fn(h)["trade"])


def load_sample(path: Path) -> tuple[list[str], list[str]]:
    """(primary, reserve). The emitted object {"primary", "reserve"}; the old plain list is read as primary with no reserve."""
    d = json.loads(Path(path).read_text())
    prim, res = (d, []) if isinstance(d, list) else (d.get("primary"), d.get("reserve", [])) if isinstance(d, dict) else (None, None)
    if not all(isinstance(v, list) and all(isinstance(p, str) for p in v) for v in (prim, res)):
        raise x.Refused("--sample must be a JSON list of pool ids or {primary: [...], reserve: [...]}")
    return prim, res


def build(primary: Sequence[str], rows: Iterable[Mapping[str, Any]], fetch: Fetch, max_tries: int, reserve: Sequence[str] = (),
          sleep: Callable[[float], None] = time.sleep) -> list[dict[str, Any]]:
    """Primary pools first (sorted); then, in the pre-declared reserve order, one reserve pool per null primary row. Every row is written, nulls included."""
    prim = sorted(set(primary))
    res = [p for p in reserve if p not in set(prim)]
    cands = collect_candidates(rows, prim + res, max_tries)
    out = [resolve_pool(p, cands.get(p, []), fetch, max_tries, sleep) for p in prim]
    k = sum(1 for r in out if r["v_implied"] is None)
    out += [resolve_pool(p, cands.get(p, []), fetch, max_tries, sleep) for p in res[:k]]
    return out


def v_counts(out: Sequence[Mapping[str, Any]], vmap: Mapping[str, int | None] | None) -> dict[str, Any]:
    """Counts only. Checked = a readable implied V AND a readable stored V (the screen's rule)."""
    if vmap is None:
        return {}
    chk = [vmap[r["pool"]] for r in out if r["v_implied"] is not None and vmap.get(r["pool"]) is not None]
    return {"n_checked": len(chk), "n_checked_v_gt0": sum(1 for v in chk if v > 0), "n_checked_v_le0": sum(1 for v in chk if v <= 0)}


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample", type=Path, required=True)
    ap.add_argument("--p2-view-dir", type=Path, action="append", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--rps", type=float, default=DEFAULT_RPS)
    ap.add_argument("--max-tries-per-pool", type=int, default=DEFAULT_MAX_TRIES)
    ap.add_argument("--env-file", default=sim.DEFAULT_ENV_FILE)
    ap.add_argument("--vmap", type=Path, default=None, help="optional: only for the meta counts of checked pools with stored V > 0 and <= 0")
    return ap


def main(argv: Sequence[str] | None = None, fetch: Fetch | None = None) -> int:
    a = _parser().parse_args(argv)
    if not 0 < a.rps <= MAX_RPS or a.max_tries_per_pool < 1:
        print(f"refusing: --rps must be in (0, {MAX_RPS:g}] and --max-tries-per-pool >= 1", file=sys.stderr)
        return 2
    try:
        g2 = guard_views(a.p2_view_dir)
        primary, reserve = load_sample(a.sample)
        vmap = x.load_pinned_vmap(a.vmap) if a.vmap else None
    except (x.Refused, OSError, ValueError) as exc:
        print(f"refusing: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if fetch is None:
        fetch = HttpFetch(sim.load_rpc_url(None, a.env_file), a.rps)
    out = build(primary, iter_view_rows(g2), fetch, a.max_tries_per_pool, reserve)
    a.out.write_text(json.dumps(out) + "\n", encoding="utf-8")
    n_null = sum(1 for r in out if r["v_implied"] is None)
    meta = {"out_sha256": sha256_file(a.out), "sample_sha256": sha256_file(a.sample), "view_dirs": [str(p) for p in a.p2_view_dir], "n": len(out),
            "n_null": n_null, "null_reasons": dict(sorted(collections.Counter(r["reason"] for r in out if r["v_implied"] is None).items())),
            "n_primary": len(set(primary)), "n_reserve_used": len(out) - len(set(primary)), **v_counts(out, vmap), "git_head": e15.git_state()["head"], "rpc_calls": getattr(fetch, "calls", None), "max_tries_per_pool": a.max_tries_per_pool, "rps": a.rps,
            "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    a.out.with_name(a.out.name + ".meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    print(f"pools={len(out)} readable={len(out) - n_null} null={n_null} rpc_calls={meta['rpc_calls']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
