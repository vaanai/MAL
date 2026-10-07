"""DEC-019 probe wallet balance tie-out against the chain (read-only, PUBLIC address only, no key).

Walks every signature of the probe wallet, takes each tx's lamport delta for the wallet from meta.preBalances /
postBalances (so fees, rent and transfers are all in it), and ties:

    balance_now == sum of deltas over all txs   (wallet started at 0 before its first tx)
    non-trade deltas  = deposits - withdrawals + dust
    trade deltas      = the ledger's trades (matched by signature) = realized P&L as the executor books it, plus rent in flight
    + checks that no token account (Token or Token-2022) remains.

The Helius key is read in Python from the env file and never printed. At most --rps requests per second.
    python3 -I tools/probe_rent_audit.py --fills FILE [--env-file /var/lib/mal/backfill/helius.env] [--rps 4] [--out-json F]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WALLET = "5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk"  # public address (DEC-019 / LAB notes)
HELIUS_HTTP = "https://mainnet.helius-rpc.com"
TOKEN_PROGRAMS = ("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
LAMPORTS = 1_000_000_000


def load_url(env_file: str) -> str:
    key = ""
    for line in Path(env_file).read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        if line.startswith("HELIUS_API_KEY="):
            key = line.split("=", 1)[1].strip().strip("\"'")
    if not key or any(ch in key for ch in "\r\n& #"):
        raise SystemExit("no usable HELIUS_API_KEY in env file")
    return f"{HELIUS_HTTP}/?api-key={key}"


class Rpc:
    def __init__(self, url: str, rps: float):
        self._url, self._iv, self._next, self.calls = url, 1.0 / rps, 0.0, 0

    def __call__(self, method: str, params: list[Any]) -> Any:
        wait = self._next - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._next = time.monotonic() + self._iv
        self.calls += 1
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        for attempt in range(4):
            try:
                req = urllib.request.Request(self._url, body, {"Content-Type": "application/json"})
                resp = json.load(urllib.request.urlopen(req, timeout=40))
                if "error" in resp:
                    raise RuntimeError(f"rpc error code {resp['error'].get('code')} {str(resp['error'].get('message'))[:90]}")
                return resp
            except Exception as exc:  # noqa: BLE001  (never print the URL)
                print(f"{method} attempt {attempt}: {type(exc).__name__} {str(exc)[:60]}", file=sys.stderr)
                time.sleep(1.5 * (attempt + 1))
        raise SystemExit(f"{method} failed")


def delta_of(tx: dict[str, Any]) -> tuple[int, int, list[str], int]:
    """(wallet lamport delta, fee, other signers/accounts that gained or lost) for one getTransaction result."""
    keys = [k if isinstance(k, str) else k["pubkey"] for k in tx["transaction"]["message"]["accountKeys"]]
    meta = tx["meta"]
    i = keys.index(WALLET)
    d = meta["postBalances"][i] - meta["preBalances"][i]
    others = [keys[j] for j in range(len(keys)) if j != i and meta["postBalances"][j] != meta["preBalances"][j]
              and abs(meta["postBalances"][j] - meta["preBalances"][j]) >= 1_000_000]
    return d, meta["fee"], others, meta["preBalances"][i]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fills", required=True)
    ap.add_argument("--env-file", default="/var/lib/mal/backfill/helius.env")
    ap.add_argument("--rps", type=float, default=4.0)
    ap.add_argument("--out-json")
    a = ap.parse_args()
    if a.rps > 5:
        raise SystemExit("rps capped at 5")
    ledger_sigs: dict[str, str] = {}
    for line in open(a.fills):
        r = json.loads(line)
        if r.get("mode") == "live" and r.get("signature"):
            ledger_sigs[r["signature"]] = r["kind"]
    rpc = Rpc(load_url(a.env_file), a.rps)
    bal = rpc("getBalance", [WALLET, {"commitment": "finalized"}])["result"]
    out: dict[str, Any] = {"wallet": WALLET, "balance_now_lamports": bal["value"], "balance_context_slot": bal["context"]["slot"]}
    sigs: list[dict[str, Any]] = []
    before = None
    while True:
        p: dict[str, Any] = {"limit": 1000, "commitment": "finalized"}
        if before:
            p["before"] = before
        page = rpc("getSignaturesForAddress", [WALLET, p])["result"]
        sigs += page
        if len(page) < 1000:
            break
        before = page[-1]["signature"]
    sigs.sort(key=lambda s: (s["slot"], s.get("blockTime") or 0))
    txs = []
    for s in sigs:
        t = rpc("getTransaction", [s["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 1, "commitment": "finalized"}])["result"]
        if t is None:
            txs.append({"sig": s["signature"], "slot": s["slot"], "missing": True})
            continue
        d, fee, others, pre = delta_of(t)
        txs.append({"sig": s["signature"], "slot": s["slot"], "time": datetime.fromtimestamp(s["blockTime"], tz=timezone.utc).isoformat() if s.get("blockTime") else None,
                    "failed": bool(t["meta"].get("err")), "delta": d, "fee": fee, "pre_balance": pre, "ledger_kind": ledger_sigs.get(s["signature"]), "others": others})
    out["n_signatures"] = len(sigs)
    out["n_missing_tx"] = sum(1 for t in txs if t.get("missing"))
    good = [t for t in txs if not t.get("missing")]
    out["sum_all_deltas_lamports"] = sum(t["delta"] for t in good)
    out["balance_minus_sum_deltas_lamports"] = out["balance_now_lamports"] - out["sum_all_deltas_lamports"]
    out["first_tx_pre_balance_lamports"] = good[0]["pre_balance"] if good else None
    trade = [t for t in good if t["ledger_kind"]]
    other = [t for t in good if not t["ledger_kind"]]
    out["trade_txs"] = len(trade)
    out["trade_delta_lamports"] = sum(t["delta"] for t in trade)
    out["ledger_signatures"] = len(ledger_sigs)
    out["ledger_signatures_found_on_chain"] = len(trade)
    out["non_trade_txs"] = [{k: t[k] for k in ("sig", "slot", "time", "delta", "fee", "failed", "others")} for t in other]
    out["non_trade_in_lamports"] = sum(t["delta"] for t in other if t["delta"] > 0)
    out["non_trade_out_lamports"] = sum(t["delta"] for t in other if t["delta"] < 0)
    accts = []
    for prog in TOKEN_PROGRAMS:
        r = rpc("getTokenAccountsByOwner", [WALLET, {"programId": prog}, {"encoding": "jsonParsed", "commitment": "finalized"}])["result"]
        for v in r["value"]:
            info = v["account"]["data"]["parsed"]["info"]
            accts.append({"pubkey": v["pubkey"], "mint": info["mint"], "amount": info["tokenAmount"]["amount"], "lamports": v["account"]["lamports"]})
        out["token_accounts_context_slot"] = r["context"]["slot"]
    out["token_accounts"] = accts
    out["rpc_calls"] = rpc.calls
    out["tie_out_ok"] = out["balance_minus_sum_deltas_lamports"] == 0 and out["first_tx_pre_balance_lamports"] == 0 and out["n_missing_tx"] == 0
    out["txs"] = [{k: t.get(k) for k in ("sig", "slot", "time", "delta", "fee", "failed", "ledger_kind")} for t in txs]
    if a.out_json:
        Path(a.out_json).write_text(json.dumps(out, indent=1))
    summary = {k: v for k, v in out.items() if k not in ("txs", "non_trade_txs")}
    print(json.dumps(summary, indent=1))
    for t in out["non_trade_txs"]:
        print(t)
    if not out["tie_out_ok"]:
        print("TIE-OUT FAILED: balance - sum(deltas) != 0, or first pre-balance != 0, or a tx is missing", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
