"""Arena sell + base-ATA close, built by the PINNED builder and run through simulateTransaction (DEC-032 section 4, FILL:simulate_ref).

The builder is the unmodified H5Executor._arm_sell (tools/h5_arena_executor.py SELL_BUILDER_PIN): budget, WSOL ATA, PumpSwap sell, WSOL close,
then close of the base ATA under the mint's own token program. This tool drives it with a throwaway IN-MEMORY keypair (never written, never
printed as a secret; only the public key is recorded) and a stub that supplies the pool state and a fixed token amount. NOTHING IS SENT.

Public RPC only (default https://api.mainnet-beta.solana.com), 1 request/s, sigVerify=false, replaceRecentBlockhash=true.

What the result means. The throwaway owner holds no SOL and no tokens, so a clean simulate is NOT expected. The record classifies the error:
  expected_unfunded_owner   the fee payer does not exist (AccountNotFound): the runtime stopped before any instruction. The accounts and programs were
                            NOT exercised by the chain; only the local validate_message check (inside _sign) and the decode below speak for them.
  expected_no_tokens        an instruction error at the sell / close that says the owner has no token account or no balance.
  structural                a wrong-account / wrong-program / seed / address error: the builder or the pool read is wrong. Investigate.
  unclassified              anything else. Not a pass.
  ok_unexpected             no error at all. Not expected with an empty owner.
The JSON record is written with sorted keys; its sha256 (printed, and stored next to it as <out>.sha256) is what FILL:simulate_ref cites.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any, Callable

from solders.hash import Hash
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from tools import h5_arena_executor as ax
from tools import h5_executor as h
from tools import probe_live as pl
from tools import pumpswap_simulate as sim
from tools import pumpswap_tx as tx

DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
MIN_INTERVAL_S = 1.0
DEFAULT_AMOUNT = 1_000_000  # raw base tokens in the sell; the owner holds none, so the amount only has to be > 0
TOKEN_NAMES = {str(tx.TOKEN_PROGRAM): "token", str(tx.TOKEN_2022_PROGRAM): "token-2022"}
# instruction index -> role, in the order of tx.sell_instructions + the base close (2 compute-budget ixs first)
IX_ROLES = ("compute_unit_limit", "compute_unit_price", "create_wsol_ata", "pumpswap_sell", "close_wsol", "close_base_ata")
# Anchor error numbers that say "your own token account is missing / empty" (3012 AccountNotInitialized, 3014 AccountNotInitialized-owned...)
NO_TOKENS_ANCHOR = {3012}
STRUCTURAL_ANCHOR = {2003, 2006, 2012, 2014, 3007, 3008, 3009, 3010, 3011, 3015, 3016, 3003, 3004, 3005, 3006}
STRUCTURAL_STR = ("IncorrectProgramId", "InvalidProgramId", "ProgramAccountNotFound", "InvalidAccountOwner", "InvalidSeeds", "MissingRequiredSignature",
                  "InvalidArgument", "NotEnoughAccountKeys", "InvalidAccountData")
NO_TOKENS_LOG = ("insufficient funds", "owner does not match", "account not initialized", "AccountNotInitialized")


class RateLimitedRpc:
    """JSON-RPC over HTTP, at most one request per MIN_INTERVAL_S. `post` and `sleep`/`clock` are injectable for tests."""

    def __init__(self, url: str, post: Callable[[str, dict], dict] | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.url, self.post, self.sleep, self.clock = url, post or self._http, sleep, clock
        self.last = -1e9
        self.calls: list[str] = []

    @staticmethod
    def _http(url: str, payload: dict) -> dict:
        req = urllib.request.Request(url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=30))

    def __call__(self, method: str, params: list) -> Any:
        wait = MIN_INTERVAL_S - (self.clock() - self.last)
        if wait > 0:
            self.sleep(wait)
        self.last = self.clock()
        self.calls.append(method)
        try:
            resp = self.post(self.url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        except Exception as exc:
            raise SystemExit(f"rpc {method} failed: {type(exc).__name__}") from None
        if "error" in resp:
            raise SystemExit(f"rpc {method} error: {resp['error']}")
        return resp["result"]


def build_sell(rpc: Callable[[str, list], Any], mint: str, pool: str | None, owner: Keypair | Pubkey, amount: int, priority: ax.Priority,
               book_stake: int = 100_000_000) -> tuple[dict[str, Any], tx.PoolState]:
    """Run the pinned H5Executor._arm_sell against a stub. Returns (pending record, pool state). Emergency shape: min_out = 1, no quote needed.
    `owner` is a Keypair (signed, the throwaway) or a bare Pubkey (the funded-payer probe: UNSIGNED, validate_message still runs)."""
    owner_pk = owner.pubkey() if isinstance(owner, Keypair) else owner
    pool_s = pool or str(tx.canonical_pool(Pubkey.from_string(mint)))
    ps, _extra = sim.fetch_pool_state(rpc, pool_s, owner_pk)
    if str(ps.base_mint) != mint:
        raise SystemExit("pool base_mint is not the requested mint")
    stub = SimpleNamespace(
        user=owner_pk, _kp=owner, dry_run=False, armed={}, pool_cache={mint: (ps,)}, exit_commitment="processed", exit_poll_ms=0,
        h5=SimpleNamespace(stake_lamports=book_stake, sell_priority_lamports=priority.sell, escalated_priority_lamports=priority.escalated,
                           sell_cu_limit=tx.DEFAULT_SELL_CU_LIMIT),
        alerts=[],
    )
    stub.now_ms = lambda: int(time.time() * 1000)
    stub._sell_balance = lambda pos: (amount, "simulate_stub")
    stub._snapshot = lambda m, c=None: (None, "", "stub")
    stub._alert = lambda kind, mint_=None, **kw: stub.alerts.append((kind, kw))
    stub._exit_fields = lambda kind: {"exit_poll_ms": 0, "exit_commitment": "processed", "exit_snapshot": kind}
    stub.bh = SimpleNamespace(get=lambda fresh=False: (Hash.default(), 0))
    stub._signer_spend_cap = MethodType(h.H5Executor._signer_spend_cap, stub)
    if isinstance(owner, Keypair):
        stub._sign = MethodType(h.H5Executor._sign, stub)
    else:
        def _unsigned(msg, ps_, mint_, cap=None):
            pl.validate_message(msg, ps_, Pubkey.from_string(mint_), owner_pk, cap if cap is not None else priority.escalated, book_stake)
            t = tx.unsigned_transaction(msg)
            return str(t.signatures[0]), base64.b64encode(bytes(t)).decode()
        stub._sign = _unsigned
    base_tp = str(ps.base_token_program)
    pos = {"mint": mint, "pool": str(ps.pool), "base_ata": str(tx.ata(owner_pk, Pubkey.from_string(mint), ps.base_token_program)),
           "base_tp": base_tp, "spend": book_stake, "sell_attempts": 0, "h5": {"plan": {}}}
    rec = h.H5Executor._arm_sell(stub, mint, pos, 0, True)  # type: ignore[arg-type]  # emergency: the market-sell shape, no quote
    if rec is None:
        raise SystemExit(f"sell build failed: {stub.alerts}")
    return rec, ps


def decode_shape(tx_b64: str, owner: Pubkey, ps: tx.PoolState) -> dict[str, Any]:
    """Role, program and account count of each instruction of the built transaction (local, no RPC)."""
    msg = VersionedTransaction.from_bytes(base64.b64decode(tx_b64)).message
    keys = list(msg.account_keys)
    out = []
    for i, ix in enumerate(msg.instructions):
        out.append({"i": i, "role": IX_ROLES[i] if i < len(IX_ROLES) else "?", "program": str(keys[ix.program_id_index]), "n_accounts": len(ix.accounts)})
    return {"fee_payer_is_owner": keys[0] == owner, "n_instructions": len(out), "instructions": out,
            "close_base_program_is_mint_program": bool(out) and out[-1]["program"] == str(ps.base_token_program)}


def _custom(err: Any) -> tuple[int | None, int | None]:
    if isinstance(err, dict) and "InstructionError" in err:
        idx, detail = err["InstructionError"]
        if isinstance(detail, dict) and "Custom" in detail:
            return idx, int(detail["Custom"])
        return idx, None
    return None, None


def classify(sim_value: dict[str, Any]) -> dict[str, Any]:
    err, logs = sim_value.get("err"), sim_value.get("logs") or []
    text = json.dumps(err)
    out: dict[str, Any] = {"class": "unclassified", "err": err, "ix_index": None, "ix_role": None, "structural_checked_by_chain": False}
    if err is None:
        out["class"] = "ok_unexpected"
        out["structural_checked_by_chain"] = True
        return out
    if err == "AccountNotFound":
        out["class"] = "expected_unfunded_owner"
        return out
    idx, code = _custom(err)
    if idx is not None:
        out["ix_index"], out["ix_role"] = idx, IX_ROLES[idx] if idx < len(IX_ROLES) else "?"
        out["structural_checked_by_chain"] = idx >= 3  # reached the swap: its account checks ran at least in part
        out["custom_code"] = code
        detail = err["InstructionError"][1]
        dstr = detail if isinstance(detail, str) else ""
        low = " ".join(logs).lower()
        if code in NO_TOKENS_ANCHOR or (idx in (3, 5) and any(s.lower() in low for s in NO_TOKENS_LOG) and code not in STRUCTURAL_ANCHOR):
            out["class"] = "expected_no_tokens"
        elif code in STRUCTURAL_ANCHOR or dstr in STRUCTURAL_STR:
            out["class"] = "structural"
        elif idx == 2 and ("insufficient" in low or dstr in ("InsufficientFunds", "Custom")) and code is None:
            out["class"] = "expected_unfunded_owner"
        return out
    if any(s in text for s in ("InsufficientFundsForFee", "InsufficientFundsForRent")):
        out["class"] = "expected_unfunded_owner"
    elif any(s in text for s in ("ProgramAccountNotFound", "InvalidProgramForExecution", "InvalidAccountForFee")):
        out["class"] = "structural"
    return out


def simulate(rpc: Callable[[str, list], Any], tx_b64: str) -> dict[str, Any]:
    cfg = {"encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True, "commitment": "processed"}
    got = rpc("simulateTransaction", [tx_b64, cfg])
    v = got["value"]
    return {"context_slot": (got.get("context") or {}).get("slot"), "err": v.get("err"), "units_consumed": v.get("unitsConsumed"),
            "logs": (v.get("logs") or [])[:40]}


def funded_payer(rpc: Callable[[str, list], Any]) -> Pubkey:
    """A public, funded, system-owned address (the first PumpSwap protocol fee recipient, as tools/pumpswap_simulate.py). Public key only."""
    info = rpc("getAccountInfo", [str(tx.GLOBAL_CONFIG), {"encoding": "base64"}])["value"]
    return tx.parse_global_config(base64.b64decode(info["data"][0]))["protocol_fee_recipients"][0]


def run(rpc: Callable[[str, list], Any], mint: str, pool: str | None, amount: int, priority: ax.Priority, now: Callable[[], datetime] | None = None,
        funded_probe: bool = True) -> dict[str, Any]:
    owner = Keypair()  # throwaway, in memory only
    rec, ps = build_sell(rpc, mint, pool, owner, amount, priority)
    shape = decode_shape(rec["tx_b64"], owner.pubkey(), ps)
    res = simulate(rpc, rec["tx_b64"])
    verdict = classify(res)
    probe = None
    if funded_probe:  # the throwaway cannot pay a fee, so the chain stops before the swap; repeat with a funded PUBLIC address as owner, unsigned
        payer = funded_payer(rpc)
        prec, pps = build_sell(rpc, mint, pool, payer, amount, priority)
        pres = simulate(rpc, prec["tx_b64"])
        probe = {"owner_pubkey_funded_public": str(payer), "signed": False, "shape": decode_shape(prec["tx_b64"], payer, pps), "simulate": pres, "verdict": classify(pres)}
    stamp = (now or (lambda: datetime.now(timezone.utc)))().strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "kind": "arena_sell_simulate.v1", "utc": stamp, "builder_pin": ax.SELL_BUILDER_PIN, "builder_pin_problem": ax.sell_pin_problem(),
        "mint": mint, "pool": str(ps.pool), "token_program": TOKEN_NAMES.get(str(ps.base_token_program), str(ps.base_token_program)),
        "owner_pubkey_throwaway": str(owner.pubkey()), "sell_amount_raw": amount, "min_out": rec["min_out"], "priority": priority.as_dict(),
        "tx_len_bytes": len(base64.b64decode(rec["tx_b64"])), "shape": shape, "simulate": res, "verdict": verdict, "funded_payer_probe": probe,
        "rpc_host": "api.mainnet-beta.solana.com", "rpc_calls": list(getattr(rpc, "calls", [])),
        "not_measured": ["a funded sell (no owner here holds tokens)", "landing", "price impact or proceeds"]
        + ([] if verdict["structural_checked_by_chain"] or (probe and probe["verdict"]["structural_checked_by_chain"]) else ["on-chain account checks of the swap (stopped before it)"]),
    }


def record_sha256(rec: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(rec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_record(rec: dict[str, Any], out: Path) -> str:
    sha = record_sha256(rec)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, sort_keys=True, separators=(",", ":")))
    out.with_name(out.name + ".sha256").write_text(f"{sha}  {out.name}\n")
    return sha


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    ap.add_argument("--mint", required=True)
    ap.add_argument("--pool", help="default: the canonical pump.fun pool of the mint")
    ap.add_argument("--rpc", default=DEFAULT_RPC, help="public RPC URL (no key in it)")
    ap.add_argument("--amount", type=int, default=DEFAULT_AMOUNT)
    ap.add_argument("--priority", choices=("default", "p155"), default="p155")
    ap.add_argument("--no-funded-probe", action="store_true")
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)
    if "?" in a.rpc or "api-key" in a.rpc.lower() or "@" in a.rpc:
        raise SystemExit("--rpc must be a plain public URL (no query string, no key)")
    prio = ax.PRIORITY_P155 if a.priority == "p155" else ax.PRIORITY_DEFAULT
    rec = run(RateLimitedRpc(a.rpc), a.mint, a.pool, a.amount, prio, funded_probe=not a.no_funded_probe)
    sha = write_record(rec, a.out)
    print(json.dumps({"mint": a.mint, "token_program": rec["token_program"], "class": rec["verdict"]["class"], "err": rec["simulate"]["err"],
                      "ix_role": rec["verdict"]["ix_role"],
                      "funded_probe_class": (rec["funded_payer_probe"] or {}).get("verdict", {}).get("class"),
                      "funded_probe_err": (rec["funded_payer_probe"] or {}).get("simulate", {}).get("err"), "sha256": sha, "out": str(a.out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
