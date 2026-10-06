"""EXP-016 P1 V-constancy input builder (plan 11 P1; 13 items 1, 2, 7).

Reads the pool-id sample written by `exp016_screen --emit-constancy-sample`, finds for each pool its EARLIEST successful PumpSwap buy print
on the P2 view tape (by slot, then signature), fetches that transaction (getTransaction, encoding json), decodes the BuyEvent and writes
`{pool, v_implied, quote_reserve, sig, slot, reason}` rows: the exact shape `exp016_screen.check_v_constancy` consumes.

Outcome-blind: from the tape it reads only pool / venue / side / signature / slot / err fields (no price, no P&L, no label, no simulation).
The Helius key is read in Python from the env file and the URL is never printed. stdout carries counts only.
"""

from __future__ import annotations

import argparse
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
        return None, "tx_missing"
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
    try:
        v = round(pvh.implied_virtual(ev))
    except (ZeroDivisionError, KeyError, ValueError):
        return None, "event_not_decodable_to_v"
    return {"v_implied": int(v), "quote_reserve": int(ev["pool_quote_token_reserves"])}, ""


def resolve_pool(pool: str, cands: Sequence[tuple[int, str]], fetch: Fetch, max_tries: int) -> dict[str, Any]:
    reason = "" if cands else "no_buy_print_on_tape"
    for slot, sig in list(cands)[:max_tries]:
        fields, why = decode_sample(fetch(sig), pool)
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


def build(sample: Sequence[str], rows: Iterable[Mapping[str, Any]], fetch: Fetch, max_tries: int) -> list[dict[str, Any]]:
    cands = collect_candidates(rows, sample, max_tries)
    return [resolve_pool(p, cands.get(p, []), fetch, max_tries) for p in sorted(set(sample))]


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample", type=Path, required=True)
    ap.add_argument("--p2-view-dir", type=Path, action="append", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--rps", type=float, default=DEFAULT_RPS)
    ap.add_argument("--max-tries-per-pool", type=int, default=DEFAULT_MAX_TRIES)
    ap.add_argument("--env-file", default=sim.DEFAULT_ENV_FILE)
    return ap


def main(argv: Sequence[str] | None = None, fetch: Fetch | None = None) -> int:
    a = _parser().parse_args(argv)
    if not 0 < a.rps <= MAX_RPS or a.max_tries_per_pool < 1:
        print(f"refusing: --rps must be in (0, {MAX_RPS:g}] and --max-tries-per-pool >= 1", file=sys.stderr)
        return 2
    try:
        g2 = guard_views(a.p2_view_dir)
        sample = json.loads(a.sample.read_text())
        if not isinstance(sample, list) or not all(isinstance(p, str) for p in sample):
            raise x.Refused("--sample must be a JSON list of pool id strings")
    except (x.Refused, OSError, ValueError) as exc:
        print(f"refusing: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if fetch is None:
        fetch = HttpFetch(sim.load_rpc_url(None, a.env_file), a.rps)
    out = build(sample, iter_view_rows(g2), fetch, a.max_tries_per_pool)
    a.out.write_text(json.dumps(out) + "\n", encoding="utf-8")
    n_null = sum(1 for r in out if r["v_implied"] is None)
    meta = {"out_sha256": sha256_file(a.out), "sample_sha256": sha256_file(a.sample), "view_dirs": [str(p) for p in a.p2_view_dir], "n": len(out),
            "n_null": n_null, "rpc_calls": getattr(fetch, "calls", None), "max_tries_per_pool": a.max_tries_per_pool, "rps": a.rps,
            "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    a.out.with_name(a.out.name + ".meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    print(f"pools={len(out)} readable={len(out) - n_null} null={n_null} rpc_calls={meta['rpc_calls']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
