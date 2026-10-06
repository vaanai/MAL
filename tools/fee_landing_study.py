#!/usr/bin/env python3
"""Fee-landing study v2 (descriptive, exploration days only). Measurement only.

Observational only. Only a live A/B can answer whether a lower fee lands us as early. Any fee change
needs a DEC-019 amendment signed off by Vaan and Helm.

Question: does a cheaper priority fee (150k or 250k lamports per side, vs the probe's 500k) still
land a PumpSwap buy as early after migration? The live probe lands 5-6 slots after migration at 500k.

For every migration (slot m) on the chosen exploration days, fetch getBlock for slots m..m+16 (full
transactions, maxSupportedTransactionVersion 0) and record every successful PumpSwap BUY on the
migrated mint: slot offset k, index in block, compute-unit price and limit (ComputeBudget), raw
priority lamports, "our-equivalent fee" = cu_price * DEFAULT_BUY_CU_LIMIT / 1e6 (what that CU price
would cost at the probe's own CU limit), and the signer. No trade amounts.

Why our-equivalent: leaders order by fee per compute unit, so buckets (b) and (c) are on cu_price
scaled to our CU limit. Raw lamports are report-only.

Tips: a paid landing service tip is counted if the same signer sent a transfer to a known tip account in
ANY transaction in the same slot (bundles tip in a separate tx). The DECIDING read is (b) and (c) on
no-tip buys; the with-tip numbers are context. Only tip accounts that can be cited to a doc are listed
(Jito, Helius Sender). Nozomi/Temporal, bloXroute, 0slot and NextBlock addresses were not sourced and are
left out; the report says so, so a tip through those services is NOT detected and no-tip is an upper bound.

Inputs: one clean-view root (e.g. /data/mal/clean-view/explore-0814/w1) with migrations/*.jsonl.zst.
Holdout, forward, sealed and backup roots are refused (same fence as the EXP-012 tools). VIEW.sha256 is
always verified.

Checkpoint: out/slots.jsonl, one line per fetched slot: the decoded buys of that slot (any mint) and the
per-slot signer -> tip map. Re-running skips slots already there; a torn last line is truncated first. At
--credit-cap the run stops cleanly (exit 3, "cap_reached") and can be resumed. The RPC key is read in
Python from the env file and is never printed. Needs solders (use /data/mal/venv/bin/python).

Run (2 days, about 300 migrations/day x 17 slots x 2 days = about 10,200 getBlock calls at most; adjacent
migrations share slots, so fewer; 1 credit per call assumed):
  /data/mal/venv/bin/python -m tools.fee_landing_study --view-root /data/mal/clean-view/explore-0814/w1 \\
      --days 2026-08-26 2026-08-27 --out-dir /var/lib/mal/fee-landing-study-v2 --rps 5 --credit-cap 60000
  # re-analyse the checkpoint without any RPC: add --analyze-only
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from tools import exp011_freeze as fz
from tools.exp012_latency_sensitivity import _forbidden_hit
from tools.paper_price_path import open_text
from tools.pumpswap_tx import DEFAULT_BUY_CU_LIMIT
import re

DEFAULT_ENV_FILE = "/var/lib/mal/backfill/helius.env"
HELIUS_HTTP = "https://mainnet.helius-rpc.com"
_API_KEY_RE = re.compile(r"(api-key=)[^&\s\"']+", re.IGNORECASE)


# Local copies of tools.pumpswap_simulate.redact_rpc_url / load_rpc_url (a test pins them equal where
# solders is installed); this tool must not need solders.
def redact_rpc_url(text: str) -> str:
    return _API_KEY_RE.sub(r"\1REDACTED", text)


def load_rpc_url(env_file: str) -> str:
    """Key from HELIUS_API_KEY or the env file. The returned URL is never printed or logged."""
    key = (os.environ.get("HELIUS_API_KEY") or "").strip()
    if not key and Path(env_file).exists():
        for line in Path(env_file).read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if line.startswith("HELIUS_API_KEY="):
                key = line.split("=", 1)[1].strip().strip("\"'")
    if not key or any(ch in key for ch in "\r\n& #"):
        raise SystemExit("no usable HELIUS_API_KEY (env or --env-file)")
    return f"{HELIUS_HTTP}/?api-key={key}"


PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
COMPUTE_BUDGET_PROGRAM = "ComputeBudget111111111111111111111111111111"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
# Instruction discriminators (pinned equal to tools.pumpswap_tx by a test).
DISC_BUY = bytes.fromhex("66063d1201daebea")
DISC_BUY_EXACT_QUOTE_IN = bytes.fromhex("c62e1552b4d9e870")
# PumpSwap buy account order: 0 pool, 1 user, 2 global_config, 3 base_mint, 4 quote_mint.
BUY_POOL_IDX, BUY_MINT_IDX = 0, 3
# Tip accounts per paid landing service. Every list needs a citable source; none from memory.
# Jito: the 8 addresses in the getTipAccounts response example, https://docs.jito.wtf/lowlatencytxnsend/
JITO_TIP_ACCOUNTS = frozenset(
    {
        "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
        "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
        "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
        "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
        "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
        "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
        "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
        "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
    }
)
# Helius Sender: the TIP_ACCOUNTS array in the quickstart, https://www.helius.dev/docs/sending-transactions/sender
HELIUS_SENDER_TIP_ACCOUNTS = frozenset(
    {
        "4ACfpUFoaSD9bfPdeu6DBt89gB6ENTeHBXCAi87NhDEE",
        "D2L6yPZ2FmmmTKPgzaMKdhu6EWZcTpLy1Vhx8uvZe7NZ",
        "9bnz4RShgq1hAnLnZbP8kbgBg1kEmcJBYQq3gQbmnSta",
        "5VY91ws6B2hMmBFRsXkoAAdsPHBJwRfBht4DXox3xkwn",
        "2nyhqdwKcJZR2vcqCyrYsaPVdAnFoJjiksCXJ7hfEYgD",
        "2q5pghRs6arqVjRvT5gfgWfWcHWmw1ZuCzphgd5KfWGJ",
        "wyvPkWjVZz1M8fHQnMMCDTQDbkManefNNhweYk5WkcF",
        "3KCKozbAaF75qEU33jtzozcJ29yJuaLJTy2jFdzUY8bT",
        "4vieeGHPYPG2MmyPRcYjdiDmmhN3ww7hsFNap8pVN3Ey",
        "4TQLFNWK8AovT1gFvda5jfw2oJeRMKEmw7aH6MGBJ3or",
    }
)
TIP_SERVICES = {"jito": JITO_TIP_ACCOUNTS, "helius_sender": HELIUS_SENDER_TIP_ACCOUNTS}
TIP_SOURCES = {"jito": "https://docs.jito.wtf/lowlatencytxnsend/", "helius_sender": "https://www.helius.dev/docs/sending-transactions/sender"}
# No address was found in the repo or in a doc fetched and read, so these are NOT detected. Never guessed.
TIP_SERVICES_UNSOURCED = ("nozomi_temporal", "bloxroute", "0slot", "nextblock")
_TIP_SERVICE_OF = {a: svc for svc, accts in TIP_SERVICES.items() for a in accts}
DEFAULT_CU_PER_IX = 200_000  # runtime default per non-ComputeBudget instruction when no limit is set
K_MAX = 16  # fetch window m..m+16; k 9..16 are the late landers
K_MID = 8
K_LAND = 6
OUR_CU_LIMIT = DEFAULT_BUY_CU_LIMIT
BUCKETS = (("le150k", 0, 150_000), ("150k_250k", 150_000, 250_000), ("250k_500k", 250_000, 500_000), ("gt500k", 500_000, None))
SKIP_CODES = frozenset({-32007, -32009})
MAX_RPS = 5.0
DEFAULT_CAP = 60_000
EXIT_CAP = 3
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_IDX = {c: i for i, c in enumerate(_B58)}


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58_IDX[ch]
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * (len(s) - len(s.lstrip("1"))) + body


# --- guards ---------------------------------------------------------------------


def guard_view_root(root: str | Path, out_dir: str | Path, *, verify: bool = True) -> Path:
    rp = os.path.realpath(str(root))
    bad = _forbidden_hit(rp)
    if bad:
        raise SystemExit(f"refusing: view root {str(root)!r} resolves to {rp!r}, inside a reserved holdout location ({bad})")
    low = rp.lower()
    for word in ("holdout", "forward", "backup", "sealed", "fresh"):
        if word in low:
            raise SystemExit(f"refusing: view root {rp!r} looks like a {word} location; exploration days only")
    r = Path(rp)
    if not (r / "migrations").is_dir():
        raise SystemExit(f"refusing: {rp!r} has no migrations/ directory")
    orp = os.path.realpath(str(out_dir))
    if orp == rp or orp.startswith(rp.rstrip("/") + "/"):
        raise SystemExit("refusing: --out-dir is inside the view root")
    if verify:
        fz.verify_view_sha256(r)
    return r


# --- decoding -------------------------------------------------------------------


def _account_keys(tx: dict) -> list[str]:
    keys = list(tx["transaction"]["message"]["accountKeys"])
    la = (tx.get("meta") or {}).get("loadedAddresses") or {}
    return keys + list(la.get("writable") or []) + list(la.get("readonly") or [])


def decode_compute_budget(instrs: list[tuple[str, bytes]]) -> tuple[int | None, int | None]:
    """(price in microlamports, unit limit) from ComputeBudget SetComputeUnitPrice (3) / Limit (2)."""
    price = limit = None
    for prog, data in instrs:
        if prog != COMPUTE_BUDGET_PROGRAM or not data:
            continue
        if data[0] == 3 and len(data) >= 9:
            price = int.from_bytes(data[1:9], "little")
        elif data[0] == 2 and len(data) >= 5:
            limit = int.from_bytes(data[1:5], "little")
    return price, limit


def priority_lamports(price: int | None, limit: int | None, n_other_ix: int) -> tuple[int, bool]:
    """ceil(price * limit / 1e6). With no explicit limit the runtime default is 200k per
    non-ComputeBudget instruction (flag False = approximated)."""
    if not price:
        return 0, True
    explicit = limit is not None
    lim = limit if explicit else DEFAULT_CU_PER_IX * max(1, n_other_ix)
    return -(-price * lim // 1_000_000), explicit


def tx_tips(tx: dict, keys: list[str]) -> list[tuple[str, int, str]]:
    """(source account, lamports, service) for every System transfer (top level and inner) to a known tip account."""
    out = []
    msg = tx["transaction"]["message"]
    groups = [msg.get("instructions") or []]
    for inner in (tx.get("meta") or {}).get("innerInstructions") or []:
        groups.append(inner.get("instructions") or [])
    for grp in groups:
        for ix in grp:
            if keys[ix["programIdIndex"]] != SYSTEM_PROGRAM:
                continue
            data = b58decode(ix["data"])
            accts = ix.get("accounts") or []
            if len(data) >= 12 and int.from_bytes(data[:4], "little") == 2 and len(accts) >= 2:
                svc = _TIP_SERVICE_OF.get(keys[accts[1]])
                if svc:
                    out.append((keys[accts[0]], int.from_bytes(data[4:12], "little"), svc))
    return out


def tipped_in_tx(tx: dict, keys: list[str]) -> bool:
    """True if this one tx has a transfer to a listed tip account. No amount is kept."""
    return bool(tx_tips(tx, keys))


def slot_tips(block: dict) -> dict[str, dict]:
    """signer -> {"tipped": True, "services": [...]} for successful txs of this slot. No lamport amounts are
    kept (minimal data). A bundle tips in a separate tx, so the buy's tx is not enough."""
    acc: dict[str, dict] = {}
    for tx in block.get("transactions") or []:
        if (tx.get("meta") or {}).get("err") is not None:
            continue
        for src, lam, svc in tx_tips(tx, _account_keys(tx)):
            d = acc.setdefault(src, {"tipped": True, "services": []})
            if svc not in d["services"]:
                d["services"].append(svc)
    return acc


def our_equiv_lamports(cu_price: int) -> int:
    """What this CU price would cost at our own CU limit: ceil(price * DEFAULT_BUY_CU_LIMIT / 1e6)."""
    return -(-cu_price * OUR_CU_LIMIT // 1_000_000)


def extract_buys(block: dict, slot: int) -> list[dict]:
    """Every successful top-level PumpSwap buy in the block (any mint). No amounts are kept."""
    out: list[dict] = []
    for idx, tx in enumerate(block.get("transactions") or []):
        if (tx.get("meta") or {}).get("err") is not None:
            continue
        keys = _account_keys(tx)
        ixs = tx["transaction"]["message"]["instructions"]
        decoded = [(keys[ix["programIdIndex"]], ix) for ix in ixs]
        buy = None
        for prog, ix in decoded:
            if prog == PUMPSWAP_PROGRAM and len(ix.get("accounts") or []) > BUY_MINT_IDX:
                if b58decode(ix["data"])[:8] in (DISC_BUY, DISC_BUY_EXACT_QUOTE_IN):
                    buy = ix
                    break
        if buy is None:
            continue
        price, limit = decode_compute_budget([(p, b58decode(i["data"])) for p, i in decoded])
        n_other = sum(1 for p, _ in decoded if p != COMPUTE_BUDGET_PROGRAM)
        lam, explicit = priority_lamports(price, limit, n_other)
        out.append(
            {
                "slot": slot,
                "idx": idx,
                "mint": keys[buy["accounts"][BUY_MINT_IDX]],
                "pool": keys[buy["accounts"][BUY_POOL_IDX]],
                "signer": keys[0],
                "cu_price": price or 0,
                "cu_limit": limit,
                "limit_explicit": explicit,
                "priority_lamports": lam,
                "our_equiv_lamports": our_equiv_lamports(price or 0),
                "tip_in_tx": tipped_in_tx(tx, keys),
            }
        )
    return out


# --- migrations -----------------------------------------------------------------


def load_migrations(view: Path, days: Iterable[str]) -> list[dict]:
    """First 'complete' row per mint whose UTC day is in `days`, sorted by slot."""
    want = set(days)
    seen: dict[str, dict] = {}
    for f in sorted((view / "migrations").glob("migrations-*.jsonl*")):
        day = f.name[len("migrations-") :][:10]
        if day not in want:
            continue
        with open_text(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                if r.get("type") != "complete" or not r.get("mint") or r.get("slot") is None:
                    continue
                cur = seen.get(r["mint"])
                if cur is None or r["slot"] < cur["slot"]:
                    seen[r["mint"]] = {"mint": r["mint"], "slot": int(r["slot"]), "day": day}
    return sorted(seen.values(), key=lambda r: r["slot"])


# --- fetching with checkpoint, rate limit, credit cap ----------------------------


class Budget:
    def __init__(self, cap: int, per_call: int = 1):
        self.cap, self.per_call, self.used = cap, per_call, 0

    def can_spend(self) -> bool:
        return self.used + self.per_call <= self.cap

    def spend(self) -> None:
        self.used += self.per_call


def load_checkpoint(path: Path) -> dict[int, dict]:
    done: dict[int, dict] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue  # a torn last line from a crash: the slot is simply fetched again
                done[r["slot"]] = r
    return done


def repair_checkpoint(path: Path) -> int:
    """Truncate a torn last line (bytes after the final newline) so the next append starts on a clean line.
    Returns the number of bytes removed. The slot on that line is simply fetched again."""
    if not path.exists():
        return 0
    data = path.read_bytes()
    if not data or data.endswith(b"\n"):
        return 0
    cut = data.rfind(b"\n") + 1  # 0 when there is no newline at all
    with path.open("r+b") as fh:
        fh.truncate(cut)
    return len(data) - cut


def fetch_all(
    migrations: list[dict],
    fetch: Callable[[int], tuple[dict | None, int | None]],
    out_dir: Path,
    budget: Budget,
    rps: float = MAX_RPS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    max_consecutive_errors: int = 5,
) -> dict[str, Any]:
    """Fetch slots m..m+16 per migration, skipping slots in the checkpoint. Returns a status dict."""
    if rps > MAX_RPS:
        raise SystemExit(f"refusing: --rps {rps} > {MAX_RPS}")
    out_dir.mkdir(parents=True, exist_ok=True)
    ckpt = out_dir / "slots.jsonl"
    removed = repair_checkpoint(ckpt)
    done = load_checkpoint(ckpt)
    status: dict[str, Any] = {"state": "complete", "torn_bytes_removed": removed, "fetched": 0, "resumed": len(done), "skipped_slots": 0, "errors": 0}
    gap, last, consec = 1.0 / rps, None, 0
    with ckpt.open("a") as fh:
        for mg in migrations:
            for slot in range(mg["slot"], mg["slot"] + K_MAX + 1):
                if slot in done:
                    continue
                if not budget.can_spend():
                    status["state"] = "cap_reached"
                    status["credits_used"] = budget.used
                    return status
                if last is not None and clock() - last < gap:
                    sleep(gap - (clock() - last))
                last = clock()
                budget.spend()
                try:
                    block, code = fetch(slot)
                    consec = 0
                except Exception as exc:  # never leak the key
                    status["errors"] += 1
                    consec += 1
                    status["last_error"] = redact_rpc_url(f"{type(exc).__name__}: {exc}")[:200]
                    if consec >= max_consecutive_errors:
                        status["state"] = "aborted_errors"
                        status["credits_used"] = budget.used
                        return status
                    continue
                if block is None:
                    rec = {"slot": slot, "status": "skipped", "code": code, "buys": []}
                    status["skipped_slots"] += 1
                else:
                    rec = {"slot": slot, "status": "ok", "n_tx": len(block.get("transactions") or []), "buys": extract_buys(block, slot), "tips": slot_tips(block)}
                fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
                fh.flush()
                done[slot] = rec
                status["fetched"] += 1
    status["credits_used"] = budget.used
    return status


def make_fetcher(url: str) -> Callable[[int], tuple[dict | None, int | None]]:
    def fetch(slot: int):
        params = [slot, {"encoding": "json", "transactionDetails": "full", "rewards": False, "commitment": "confirmed", "maxSupportedTransactionVersion": 0}]
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getBlock", "params": params}).encode()
        req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
        try:
            resp = json.load(urllib.request.urlopen(req, timeout=60))
        except Exception as exc:
            raise RuntimeError(redact_rpc_url(f"getBlock {slot} failed: {type(exc).__name__}: {exc}")) from None
        if "error" in resp:
            code = resp["error"].get("code")
            if code in SKIP_CODES:
                return None, code
            raise RuntimeError(f"getBlock {slot} error code {code}")
        return resp.get("result"), None

    return fetch


# --- analysis -------------------------------------------------------------------


def pct(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    pos = q * (len(s) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def bucket_of(lam: int) -> str:
    """Bucket of an our-equivalent fee (cu_price x our CU limit / 1e6, lamports)."""
    for name, _lo, hi in BUCKETS:
        if hi is None or lam <= hi:
            return name
    return "gt500k"


def join_buys(migrations: list[dict], done: dict[int, dict]) -> list[dict]:
    """Buys on each migration's mint in slots m..m+16, with k = slot - m, the slot-wide tip of the same
    signer (any tx in that slot), and the our-equivalent fee."""
    rows = []
    for mg in migrations:
        for slot in range(mg["slot"], mg["slot"] + K_MAX + 1):
            rec = done.get(slot) or {}
            tips = rec.get("tips") or {}
            for b in rec.get("buys", []):
                if b["mint"] == mg["mint"]:
                    t = tips.get(b["signer"]) or {}
                    tipped = bool(t.get("tipped")) or bool(b.get("tip_in_tx"))
                    rows.append(
                        {
                            **b,
                            "k": slot - mg["slot"],
                            "mig_slot": mg["slot"],
                            "our_equiv_lamports": b["our_equiv_lamports"] if "our_equiv_lamports" in b else our_equiv_lamports(b["cu_price"]),
                            "tip_services": list(t.get("services", [])),
                            "has_tip": tipped,
                            "no_price": not b["cu_price"],
                        }
                    )
    return rows


def first_buys(rows: list[dict]) -> list[dict]:
    """First buy per (signer, mint): lowest k, then lowest in-block index."""
    best: dict[tuple, dict] = {}
    for r in sorted(rows, key=lambda r: (r["k"], r["idx"])):
        best.setdefault((r["signer"], r["mint"]), r)
    return list(best.values())


def _p_land(firsts: list[dict]) -> dict[str, dict]:
    out = {}
    for name, _, _ in BUCKETS:
        sub = [r for r in firsts if not r["no_price"] and bucket_of(r["our_equiv_lamports"]) == name]
        n = len(sub)
        late = sum(1 for r in sub if r["k"] > K_MID)
        out[name] = {
            "n": n,
            "n_k_le6": sum(1 for r in sub if r["k"] <= K_LAND),
            "n_k_7_8": sum(1 for r in sub if K_LAND < r["k"] <= K_MID),
            "n_late_k_9_16": late,
            "p": (sum(1 for r in sub if r["k"] <= K_LAND) / n) if n else None,
            "share_late": (late / n) if n else None,
        }
    return out


def bootstrap_p_land(firsts: list[dict], n_boot: int = 1000, seed: int = 1) -> dict[str, dict]:
    """Cluster bootstrap over migrations (mint): 5th/95th percentile of P(k<=6 | bucket) and of the late share."""
    by_mint: dict[str, list[dict]] = defaultdict(list)
    for r in firsts:
        by_mint[r["mint"]].append(r)
    mints = sorted(by_mint)
    rng = random.Random(seed)
    draws: dict[str, list[float]] = {name: [] for name, _, _ in BUCKETS}
    late: dict[str, list[float]] = {name: [] for name, _, _ in BUCKETS}
    for _ in range(n_boot if mints else 0):
        sample: list[dict] = []
        for _i in range(len(mints)):
            sample.extend(by_mint[mints[rng.randrange(len(mints))]])
        for name, v in _p_land(sample).items():
            if v["p"] is not None:
                draws[name].append(v["p"])
                late[name].append(v["share_late"])
    point = _p_land(firsts)
    for name in point:
        d, lt = draws[name], late[name]
        point[name]["ci90"] = [pct(d, 0.05), pct(d, 0.95)] if d else None
        point[name]["share_late_ci90"] = [pct(lt, 0.05), pct(lt, 0.95)] if lt else None
        point[name]["boot_draws_defined"] = len(d)
    return point


def _spearman(x: list[float], y: list[float]) -> float | None:
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for t in range(i, j + 1):
                r[order[t]] = (i + j) / 2
            i = j + 1
        return r

    rx, ry = ranks(x), ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if sx == 0 or sy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / (sx * sy)


def within_slot_order(rows: list[dict]) -> dict[str, Any]:
    """Within each slot (buys on migrated mints only): how often does the higher fee per CU (cu_price) sit
    earlier in the block? Equal-price pairs are ignored. "no_tip" drops buys whose signer tipped in that slot
    (a bundle is placed by the tip, not the priority fee); no_tip is the deciding read."""

    def run(sub: list[dict]) -> dict[str, Any]:
        by_slot: dict[int, list[dict]] = defaultdict(list)
        for r in sub:
            if r["no_price"]:
                continue  # no ComputeBudget price: no fee-per-CU to order by
            by_slot[r["slot"]].append(r)
        conc = disc = 0
        rhos = []
        for rs in by_slot.values():
            if len(rs) < 2:
                continue
            for i in range(len(rs)):
                for j in range(i + 1, len(rs)):
                    a, b = rs[i], rs[j]
                    if a["cu_price"] == b["cu_price"] or a["idx"] == b["idx"]:
                        continue
                    first, second = (a, b) if a["idx"] < b["idx"] else (b, a)
                    if first["cu_price"] > second["cu_price"]:
                        conc += 1
                    else:
                        disc += 1
            rho = _spearman([float(r["idx"]) for r in rs], [-float(r["cu_price"]) for r in rs])
            if rho is not None:
                rhos.append(rho)
        tot = conc + disc
        return {
            "slots_with_2plus_buys": sum(1 for rs in by_slot.values() if len(rs) >= 2),
            "pairs_unequal_fee": tot,
            "share_higher_fee_earlier": conc / tot if tot else None,
            "mean_spearman_fee_rank_vs_position": sum(rhos) / len(rhos) if rhos else None,
            "slots_spearman_defined": len(rhos),
        }

    return {"no_tip": run([r for r in rows if not r["has_tip"]]), "all": run(rows)}


def no_price_report(rows: list[dict], firsts: list[dict]) -> dict[str, Any]:
    """Buys with no ComputeBudget price (cu_price 0). Kept out of every fee bucket and out of the (d) ordering."""
    np_rows = [r for r in rows if r["no_price"]]
    np_first = [r for r in firsts if r["no_price"]]
    kd = {str(k): sum(1 for r in np_rows if r["k"] == k) for k in range(K_MAX + 1)}
    return {
        "buys": len(np_rows),
        "first_buys": len(np_first),
        "k_distribution_buys": kd,
        "first_buys_k_le6": sum(1 for r in np_first if r["k"] <= K_LAND),
        "first_buys_late_k_9_16": sum(1 for r in np_first if r["k"] > K_MID),
        "tipped_share_buys": (sum(1 for r in np_rows if r["has_tip"]) / len(np_rows)) if np_rows else None,
        "tipped_share_first_buys": (sum(1 for r in np_first if r["has_tip"]) / len(np_first)) if np_first else None,
    }


SUBSETS = (
    ("no_tip", "DECIDING", lambda r: not r["has_tip"]),
    ("with_tip", "context only", lambda r: r["has_tip"]),
    ("all", "context only", lambda r: True),
)

CAVEAT = (
    "Observational only. Only a live A/B can answer whether a lower fee lands us as early. "
    "Any fee change needs a DEC-019 amendment signed off by Vaan and Helm."
)


def analyze(migrations: list[dict], done: dict[int, dict], n_boot: int = 1000, seed: int = 1) -> dict[str, Any]:
    rows = join_buys(migrations, done)
    by_k = {}
    for k in range(0, K_MAX + 1):
        at_all = [r for r in rows if r["k"] == k]
        at = [r for r in at_all if not r["no_price"]]
        eq = [r["our_equiv_lamports"] for r in at]
        eq_nt = [r["our_equiv_lamports"] for r in at if not r["has_tip"]]
        raw = [r["priority_lamports"] for r in at]
        by_k[str(k)] = {
            "n": len(at),
            "n_no_price_excluded": len(at_all) - len(at),
            "n_with_tip": sum(1 for r in at if r["has_tip"]),
            "our_equiv_p10": pct(eq, 0.1), "our_equiv_p50": pct(eq, 0.5), "our_equiv_p90": pct(eq, 0.9),
            "no_tip_our_equiv_p10": pct(eq_nt, 0.1), "no_tip_our_equiv_p50": pct(eq_nt, 0.5), "no_tip_our_equiv_p90": pct(eq_nt, 0.9),
            "raw_priority_p10": pct(raw, 0.1), "raw_priority_p50": pct(raw, 0.5), "raw_priority_p90": pct(raw, 0.9),
        }
    firsts_all = first_buys(rows)
    shares: dict[str, dict] = {}
    cdict: dict[str, dict] = {}
    for name, role, pred in SUBSETS:
        early = [r for r in rows if r["k"] <= K_LAND and pred(r) and not r["no_price"]]
        sh: dict[str, Any] = {"role": role, "n": len(early)}
        for cap in (150_000, 250_000, 500_000):
            sh[f"le{cap // 1000}k"] = (sum(1 for r in early if r["our_equiv_lamports"] <= cap) / len(early)) if early else None
        shares[name] = sh
        cdict[name] = {"role": role, "buckets": bootstrap_p_land([r for r in firsts_all if pred(r)], n_boot, seed)}
    missing = sum(1 for mg in migrations for s in range(mg["slot"], mg["slot"] + K_MAX + 1) if s not in done)
    return {
        "observational_only": True,
        "caveat": CAVEAT,
        "definitions": {
            "k": "landing slot minus the slot of the 'complete' event (migration slot m); fetched k = 0..16",
            "our_equiv_lamports": f"ceil(cu_price * {OUR_CU_LIMIT} / 1e6), {OUR_CU_LIMIT} = DEFAULT_BUY_CU_LIMIT of the probe; leaders order by fee per CU, so (b) and (c) bucket on this. Raw priority lamports are report-only (a)",
            "raw_priority": "ceil(cu_price * cu_limit / 1e6); limit defaults to 200k per non-ComputeBudget instruction when unset",
            "has_tip": "the buy's signer sent a transfer to a known tip account in ANY successful tx of the same slot",
            "scope": "successful top-level PumpSwap buy / buy_exact_quote_in instructions on the migrated mint; buys via a router/CPI are not seen",
            "p_land": f"P(k <= {K_LAND}) among each wallet's first buy per mint, conditional on that buy landing within k <= {K_MAX}; wallets that never landed in the window are not in the denominator. Late landers (k 9..16) are counted per bucket",
            "first_buy_tip_status": "a wallet's tip status is that of its first buy",
            "no_price": "buys with no ComputeBudget price (cu_price 0) are excluded from the fee buckets in (a), (b), (c) and from the (d) ordering; they are reported on their own in (e)",
            "tip_data": "per-slot tip map holds signer -> tipped and service names only; no lamport amounts are stored",
        },
        "tip_detection": {
            "services_listed": {svc: {"n_accounts": len(a), "source": TIP_SOURCES[svc]} for svc, a in TIP_SERVICES.items()},
            "services_NOT_listed_no_sourced_address": list(TIP_SERVICES_UNSOURCED),
            "consequence": "a tip paid through an unlisted service is not detected, so no-tip is an upper bound on truly tip-free buys",
        },
        "counts": {
            "migrations": len(migrations), "buys": len(rows), "first_buys": len(firsts_all),
            "first_buys_no_tip": sum(1 for r in firsts_all if not r["has_tip"]),
            "slots_fetched": len(done), "slots_missing": missing,
            "slots_skipped": sum(1 for r in done.values() if r.get("status") == "skipped"),
        },
        "a_fee_by_k": by_k,
        "b_early_buyer_fee_shares": shares,
        "c_p_land_by_fee_bucket": cdict,
        "c_bootstrap": {"draws": n_boot, "seed": seed, "unit": "migration (mint)", "interval": "5th-95th percentile"},
        "d_within_slot_fee_order": within_slot_order(rows),
        "e_no_price": no_price_report(rows, firsts_all),
    }


def render_md(rep: dict, status: dict | None, days: list[str]) -> str:
    f = lambda v: "n/a" if v is None else (f"{v:,.0f}" if isinstance(v, (int, float)) and v >= 100 else f"{v:.3f}")
    fc = lambda c: ("[" + f(c[0]) + ", " + f(c[1]) + "]") if c else "n/a"
    L = ["# Fee landing study v2", "", f"**{rep['caveat']}**", "", f"Days: {', '.join(days)}. Counts: {json.dumps(rep['counts'])}.", ""]
    td = rep["tip_detection"]
    L += [f"Tip accounts listed: {json.dumps(td['services_listed'])}. NOT listed (no sourced address): {', '.join(td['services_NOT_listed_no_sourced_address'])}. {td['consequence']}.", ""]
    L += ["The DECIDING read is the no-tip rows of (b) and (c). With-tip and all-buys rows are context only. Fees are our-equivalent (cu_price x our CU limit / 1e6) except where marked raw.", ""]
    if status:
        L += [f"Fetch status: {json.dumps(status)}", ""]
    L += ["## (a) Fee by landing offset k", "", "| k | n | with tip | our-eq p10/p50/p90 | no-tip our-eq p10/p50/p90 | raw lamports p10/p50/p90 |", "|---|---|---|---|---|---|"]
    for k, v in rep["a_fee_by_k"].items():
        L.append(
            f"| {k} | {v['n']} | {v['n_with_tip']} | {f(v['our_equiv_p10'])}/{f(v['our_equiv_p50'])}/{f(v['our_equiv_p90'])} "
            f"| {f(v['no_tip_our_equiv_p10'])}/{f(v['no_tip_our_equiv_p50'])}/{f(v['no_tip_our_equiv_p90'])} "
            f"| {f(v['raw_priority_p10'])}/{f(v['raw_priority_p50'])}/{f(v['raw_priority_p90'])} |"
        )
    L += ["", f"## (b) Buys landing at k <= {K_LAND}: share with our-equivalent fee at most", ""]
    for name, s in rep["b_early_buyer_fee_shares"].items():
        L.append(f"- {name} ({s['role']}): n = {s['n']}; <=150k: {f(s['le150k'])}; <=250k: {f(s['le250k'])}; <=500k: {f(s['le500k'])}")
    L += ["", f"## (c) P(k <= {K_LAND} | our-equivalent fee bucket), first buy per wallet per pool, with late landers (k 9..16)", ""]
    for name, blk in rep["c_p_land_by_fee_bucket"].items():
        L += [f"### {name} ({blk['role']})", "", "| bucket | n | k<=6 | k 7-8 | late k 9-16 | P(k<=6) [90% CI] | late share [90% CI] |", "|---|---|---|---|---|---|---|"]
        for b, v in blk["buckets"].items():
            L.append(f"| {b} | {v['n']} | {v['n_k_le6']} | {v['n_k_7_8']} | {v['n_late_k_9_16']} | {f(v['p'])} {fc(v.get('ci90'))} | {f(v['share_late'])} {fc(v.get('share_late_ci90'))} |")
        L.append("")
    L += ["## (d) Fee per CU vs position inside a slot", ""]
    for name, v in rep["d_within_slot_fee_order"].items():
        L.append(f"- {name}: slots with 2+ buys {v['slots_with_2plus_buys']}, unequal-price pairs {v['pairs_unequal_fee']}, share with higher cu_price earlier {f(v['share_higher_fee_earlier'])}, mean Spearman {f(v['mean_spearman_fee_rank_vs_position'])}")
    e = rep["e_no_price"]
    L += ["", "## (e) Buys with no ComputeBudget price (excluded from the fee buckets and from (d))", "",
          f"buys {e['buys']}, first buys {e['first_buys']} (k<=6: {e['first_buys_k_le6']}, late k 9-16: {e['first_buys_late_k_9_16']}), "
          f"tipped share (buys) {f(e['tipped_share_buys'])}, (first buys) {f(e['tipped_share_first_buys'])}. k distribution of buys: {json.dumps(e['k_distribution_buys'])}"]
    return "\n".join(L) + "\n"


# --- CLI ------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--view-root", required=True)
    ap.add_argument("--days", nargs="+", required=True, help="UTC days YYYY-MM-DD")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    ap.add_argument("--rps", type=float, default=MAX_RPS)
    ap.add_argument("--credit-cap", type=int, default=DEFAULT_CAP)
    ap.add_argument("--credits-per-call", type=int, default=1, help="Helius getBlock is about 1 credit per call on this plan; raise it if the dashboard says otherwise")
    ap.add_argument("--boot", type=int, default=1000)
    ap.add_argument("--analyze-only", action="store_true")
    args = ap.parse_args(argv)
    if args.rps > MAX_RPS or args.rps <= 0:
        raise SystemExit(f"refusing: --rps must be in (0, {MAX_RPS}]")
    out = Path(args.out_dir)
    view = guard_view_root(args.view_root, out)
    migrations = load_migrations(view, args.days)
    if not migrations:
        raise SystemExit("no migrations found for those days")
    status = None
    if not args.analyze_only:
        url = load_rpc_url(args.env_file)
        status = fetch_all(migrations, make_fetcher(url), out, Budget(args.credit_cap, args.credits_per_call), rps=args.rps)
        print(json.dumps(status))
    done = load_checkpoint(out / "slots.jsonl")
    rep = analyze(migrations, done, args.boot)
    rep["fetch_status"] = status
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(rep, indent=1) + "\n")
    (out / "report.md").write_text(render_md(rep, status, args.days))
    print(f"wrote {out / 'report.json'} and report.md")
    return EXIT_CAP if status and status["state"] == "cap_reached" else (1 if status and status["state"] != "complete" else 0)


if __name__ == "__main__":
    sys.exit(main())
