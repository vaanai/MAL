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
import urllib.error
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


RETRY_HTTP = (429, 500, 502, 503, 504)  # 403 is not retried here: the q50 rerun must fail loud on a blocked key
GET_TX_MAX_VERSION = 0  # v0 transactions need maxSupportedTransactionVersion >= 0; the call is normalised in RateLimitedRpc


def _tx_config(params: list) -> list:
    """getTransaction params with maxSupportedTransactionVersion forced to >= 0 (a missing one makes the node reject a v0 tx)."""
    out = list(params)
    cfg = dict(out[1]) if len(out) > 1 and isinstance(out[1], dict) else {}
    v = cfg.get("maxSupportedTransactionVersion")
    if not isinstance(v, int) or v < GET_TX_MAX_VERSION:
        cfg["maxSupportedTransactionVersion"] = GET_TX_MAX_VERSION
    if len(out) > 1:
        out[1] = cfg
    else:
        out.append(cfg)
    return out


class RateLimitedRpc:
    """JSON-RPC over HTTP, at most one request per MIN_INTERVAL_S. HTTP 429/5xx and network errors are retried with exponential backoff, at most
    `retries` times per call. `post`, `sleep` and `clock` are injectable for tests. A failure names the method, the HTTP status (or exception type) and,
    for getTransaction, the signature. It never carries the URL, which may hold an API key."""

    def __init__(self, url: str, post: Callable[[str, dict], dict] | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, retries: int = 4, backoff_base: float = 2.0, backoff_cap: float = 30.0):
        self.url, self.post, self.sleep, self.clock = url, post or self._http, sleep, clock
        self.retries, self.backoff_base, self.backoff_cap = retries, backoff_base, backoff_cap
        self.last = -1e9
        self.calls: list[str] = []

    def __repr__(self) -> str:
        return "RateLimitedRpc(<redacted>)"

    @staticmethod
    def _http(url: str, payload: dict) -> dict:
        req = urllib.request.Request(url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=30))

    @staticmethod
    def _label(method: str, params: list, exc: BaseException) -> str:
        code = getattr(exc, "code", None) if isinstance(exc, urllib.error.HTTPError) else None
        what = f"HTTP {code}" if code is not None else type(exc).__name__
        sig = f" sig={params[0]}" if method == "getTransaction" and params and isinstance(params[0], str) else ""
        return f"rpc {method} failed: {what}{sig}"

    def __call__(self, method: str, params: list) -> Any:
        if method == "getTransaction":
            params = _tx_config(params)
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        resp = None
        for attempt in range(self.retries + 1):
            wait = MIN_INTERVAL_S - (self.clock() - self.last)
            if wait > 0:
                self.sleep(wait)
            self.last = self.clock()
            self.calls.append(method)
            try:
                resp = self.post(self.url, payload)
                break
            except Exception as exc:
                code = getattr(exc, "code", None) if isinstance(exc, urllib.error.HTTPError) else None
                retryable = code in RETRY_HTTP if code is not None else isinstance(exc, OSError)  # URLError, timeout, reset are OSError
                if not retryable or attempt >= self.retries:
                    suffix = f" after {attempt + 1} attempts" if attempt else ""
                    raise SystemExit(self._label(method, params, exc) + suffix) from None
                self.sleep(min(self.backoff_cap, self.backoff_base * 2 ** attempt))
        if "error" in resp:
            raise SystemExit(f"rpc {method} error: {resp['error']}")
        return resp["result"]


def build_sell(rpc: Callable[[str, list], Any], mint: str, pool: str | None, owner: Keypair | Pubkey, amount: int, priority: ax.Priority,
               book_stake: int = 100_000_000, emergency: bool = True) -> tuple[dict[str, Any], tx.PoolState]:
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
    stub._due = lambda *a, **kw: True  # normal-exit shape without a quote: the executor's escalated path (floor from the stake), same priority as the emergency
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
    rec = h.H5Executor._arm_sell(stub, mint, pos, 0, emergency)  # type: ignore[arg-type]  # emergency (default): market-sell shape, min_out = 1
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
    out: dict[str, Any] = {"class": "unclassified", "err": err, "ix_index": None, "ix_role": None, "structural_checked_by_chain": "none"}
    if err is None:
        out["class"] = "ok_unexpected"
        out["structural_checked_by_chain"] = "all_instructions_ran"
        return out
    if err == "AccountNotFound":
        out["class"] = "expected_unfunded_owner"
        return out
    idx, code = _custom(err)
    if idx is not None:
        out["ix_index"], out["ix_role"] = idx, IX_ROLES[idx] if idx < len(IX_ROLES) else "?"
        # states only what ran: the failing instruction's own account checks (and the instructions before it), nothing after it
        out["structural_checked_by_chain"] = f"up_to_ix_{idx}_account_checks_failed_at_ix_{idx}"
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


def reached_swap(verdict: dict[str, Any]) -> bool:
    return verdict["structural_checked_by_chain"] == "all_instructions_ran" or (verdict["ix_index"] is not None and verdict["ix_index"] >= 3)


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
        + ([] if reached_swap(verdict) or (probe and reached_swap(probe["verdict"])) else ["on-chain account checks of the swap (stopped before it)"]),
        "not_measured_chain": "the instructions after the failing one never ran in these simulates (no close was exercised); see the holder records",
    }


# ---------------------------------------------------------------- holder mode (QP 2026-10-23, FILL:simulate_ref conditions 1-6)
SYSTEM_PROGRAM = "11111111111111111111111111111111"
MIN_HOLDER_LAMPORTS = 10_000_000  # fee + WSOL-ATA rent with a wide margin
MAX_HOLDER_CANDIDATES = 20


def candidate_token_accounts(rpc: Callable[[str, list], Any], mint: str, pool: Pubkey, token_program: Pubkey) -> tuple[str, list[str]]:
    """Token-account candidates. First choice: getTokenLargestAccounts. The public mainnet endpoint serves that method with a zero limit (HTTP 429), so
    the fallback reads the pool's recent signatures + their post-token-balances and takes the OWNERS of non-pool balances of this mint; their canonical ATA
    is derived and checked below by getAccountInfo, like every candidate."""
    try:
        return "getTokenLargestAccounts", [r["address"] for r in rpc("getTokenLargestAccounts", [mint, {"commitment": "confirmed"}])["value"]][:MAX_HOLDER_CANDIDATES]
    except SystemExit:
        pass
    sigs = rpc("getSignaturesForAddress", [str(pool), {"limit": 80, "commitment": "finalized"}])
    seen: list[str] = []
    for row in sigs:
        if row.get("err"):
            continue
        t = rpc("getTransaction", [row["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1, "commitment": "finalized"}])
        for b in ((t or {}).get("meta") or {}).get("postTokenBalances") or []:
            o = b.get("owner")
            if b.get("mint") == mint and o and o != str(pool) and o not in seen and Pubkey.from_string(o).is_on_curve():
                seen.append(o)
        if len(seen) >= MAX_HOLDER_CANDIDATES:
            break
    mpk = Pubkey.from_string(mint)
    return "pool_history_post_token_balances", [str(tx.ata(Pubkey.from_string(o), mpk, token_program)) for o in seen]


def find_holder(rpc: Callable[[str, list], Any], mint: str, pool: Pubkey, token_program: Pubkey) -> dict[str, Any]:
    """A PUBLIC ordinary holder: system-owned, on-curve wallet, holds `mint` in its canonical ATA (owner, mint, the mint's token program),
    not frozen, balance > 1, enough lamports. The pool, its vault authority and every PDA are excluded. Public keys only."""
    source, big = candidate_token_accounts(rpc, mint, pool, token_program)
    skipped: dict[str, int] = {}
    for tok in big:
        got = rpc("getAccountInfo", [tok, {"encoding": "jsonParsed", "commitment": "confirmed"}])["value"]
        info = (((got or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
        if not got or not info or got.get("owner") != str(token_program):
            skipped["not_token_account"] = skipped.get("not_token_account", 0) + 1
            continue
        owner = info.get("owner", "")
        if info.get("state") != "initialized" or info.get("mint") != mint or int(info["tokenAmount"]["amount"]) < 2:
            skipped["frozen_or_empty"] = skipped.get("frozen_or_empty", 0) + 1
            continue
        close_auth = info.get("closeAuthority")
        if close_auth is not None and close_auth != owner:
            # T22: a foreign close authority makes the SPL CloseAccount fail with 0x4 (owner does not match) for the impersonated owner.
            # That is an artifact of the test holder; our own wallet creates its base ATA without a close authority.
            skipped["foreign_close_authority"] = skipped.get("foreign_close_authority", 0) + 1
            continue
        opk = Pubkey.from_string(owner)
        if owner == str(pool) or not opk.is_on_curve():
            skipped["pool_or_pda_owner"] = skipped.get("pool_or_pda_owner", 0) + 1
            continue
        if str(tx.ata(opk, Pubkey.from_string(mint), token_program)) != tok:
            skipped["not_canonical_ata"] = skipped.get("not_canonical_ata", 0) + 1
            continue
        w = rpc("getAccountInfo", [owner, {"encoding": "base64", "commitment": "confirmed"}])["value"]
        if not w or w.get("owner") != SYSTEM_PROGRAM or w.get("executable") or w.get("lamports", 0) < MIN_HOLDER_LAMPORTS or w.get("space", 0) != 0:
            skipped["owner_not_funded_system_wallet"] = skipped.get("owner_not_funded_system_wallet", 0) + 1
            continue
        return {"owner": owner, "token_account": tok, "owner_lamports": w["lamports"], "candidate_source": source, "candidates_checked": len(big), "skipped": skipped}
    raise SystemExit(f"no ordinary holder found among {len(big)} candidates ({source}): {skipped}")


def read_balance(rpc: Callable[[str, list], Any], token_account: str) -> tuple[int, int]:
    got = rpc("getTokenAccountBalance", [token_account, {"commitment": "confirmed"}])
    return int(got["value"]["amount"]), int((got.get("context") or {}).get("slot"))


def simulate_with_accounts(rpc: Callable[[str, list], Any], tx_b64: str, addresses: list[str]) -> dict[str, Any]:
    cfg = {"encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True, "commitment": "confirmed",
           "accounts": {"encoding": "jsonParsed", "addresses": addresses}}
    got = rpc("simulateTransaction", [tx_b64, cfg])
    v = got["value"]

    def post(a: Any) -> Any:
        if a is None:
            return None
        info = ((a.get("data") or {}).get("parsed") or {}).get("info") if isinstance(a.get("data"), dict) else None
        return {"lamports": a.get("lamports"), "owner": a.get("owner"), "token_amount": ((info or {}).get("tokenAmount") or {}).get("amount")}

    return {"context_slot": (got.get("context") or {}).get("slot"), "err": v.get("err"), "units_consumed": v.get("unitsConsumed"),
            "logs": v.get("logs") or [], "post_state": {addr: post(a) for addr, a in zip(addresses, v.get("accounts") or [None] * len(addresses))}}


def is_closed(post: Any) -> bool:
    """simulateTransaction returns a closed account as null or as a zero-lamport system-owned empty account."""
    return post is None or (post.get("lamports") == 0 and post.get("owner") == SYSTEM_PROGRAM)


def holder_checks(res: dict[str, Any], pos_base_ata: str, wsol_ata: str, base_tp: str) -> dict[str, Any]:
    """Facts read from the logs and the post-state. CloseAccount is counted as top-level token-program invocations that succeed (the Token program does
    not always log 'Instruction: CloseAccount'): the sell tx has exactly two top-level token-program ixs, the WSOL close and the base close."""
    logs = res["logs"]
    top = [i for i, line in enumerate(logs) if line == f"Program {base_tp} invoke [1]" or line == f"Program {tx.TOKEN_PROGRAM} invoke [1]"]
    top_ok = sum(1 for i in top if any(l == logs[i].replace("invoke [1]", "success") for l in logs[i + 1:i + 4]))
    return {"err_is_null": res["err"] is None, "pumpswap_success_log": any(line.startswith(f"Program {tx.PUMPSWAP_PROGRAM} success") for line in logs),
            "top_level_token_ixs": len(top), "top_level_token_ixs_succeeded": top_ok,
            "base_ata_closed_in_post_state": is_closed(res["post_state"].get(pos_base_ata, "absent")) if pos_base_ata in res["post_state"] else False,
            "wsol_ata_closed_in_post_state": is_closed(res["post_state"].get(wsol_ata)) if wsol_ata in res["post_state"] else False,
            "non_native_close_error_log": any("Non-native account can only be closed if its balance is zero" in line for line in logs)}


def holder_run(rpc: Callable[[str, list], Any], mint: str, pool: str | None, priority: ax.Priority, now: Callable[[], datetime] | None = None) -> dict[str, Any]:
    pool_pk = Pubkey.from_string(pool) if pool else tx.canonical_pool(Pubkey.from_string(mint))
    ps0, _ = sim.fetch_pool_state(rpc, str(pool_pk), Pubkey.from_string(SYSTEM_PROGRAM))
    holder = find_holder(rpc, mint, pool_pk, ps0.base_token_program)
    owner = Pubkey.from_string(holder["owner"])
    balance, bal_slot = read_balance(rpc, holder["token_account"])
    runs: dict[str, Any] = {}
    for name, amount in (("full_balance", balance), ("control_balance_minus_1", balance - 1)):
        rec, ps = build_sell(rpc, mint, str(pool_pk), owner, amount, priority)
        shape = decode_shape(rec["tx_b64"], owner, ps)
        base_ata = str(tx.ata(owner, Pubkey.from_string(mint), ps.base_token_program))
        wsol_ata = str(tx.ata(owner, tx.WSOL_MINT, tx.TOKEN_PROGRAM))
        res = simulate_with_accounts(rpc, rec["tx_b64"], [base_ata, wsol_ata])
        runs[name] = {"sell_amount_raw": amount, "min_out": rec["min_out"], "tx_len_bytes": len(base64.b64decode(rec["tx_b64"])), "shape": shape,
                      "simulate": res, "checks": holder_checks(res, base_ata, wsol_ata, str(ps.base_token_program)), "base_ata": base_ata, "wsol_ata": wsol_ata}
        if name == "full_balance":
            # state of the base ATA right before the control run is the pre-state of both runs (simulate never writes)
            runs[name]["balance_read_before_simulate"] = {"amount": balance, "context_slot": bal_slot}
        else:  # simulateTransaction does not report post-state reliably when the tx fails: do not let a flag read as a measurement
            for k in ("base_ata_closed_in_post_state", "wsol_ata_closed_in_post_state"):
                runs[name]["checks"][k] = "not_reported_on_error"
    full, ctl = runs["full_balance"], runs["control_balance_minus_1"]
    close_idx = len(full["shape"]["instructions"]) - 1
    ctl_err = ctl["simulate"]["err"]
    ctl_ix = ctl_err["InstructionError"][0] if isinstance(ctl_err, dict) and "InstructionError" in ctl_err else None
    verdict = {
        "full_sell_ok": all((full["checks"]["err_is_null"], full["checks"]["pumpswap_success_log"], full["checks"]["top_level_token_ixs_succeeded"] >= 2,
                             full["checks"]["base_ata_closed_in_post_state"], full["checks"]["wsol_ata_closed_in_post_state"])),
        "control_reverts_at_base_close": ctl_err is not None and ctl_ix == close_idx and ctl["checks"]["non_native_close_error_log"],
        "control_err": ctl_err,
    }
    stamp = (now or (lambda: datetime.now(timezone.utc)))().strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"kind": "arena_sell_simulate_holder.v1", "utc": stamp, "builder_pin": ax.SELL_BUILDER_PIN, "builder_pin_problem": ax.sell_pin_problem(),
            "mint": mint, "pool": str(pool_pk), "token_program": TOKEN_NAMES.get(str(ps0.base_token_program), str(ps0.base_token_program)),
            "holder": holder, "signed": False, "priority": priority.as_dict(), "shape_note": "emergency shape (min_out = 1); normal-exit shape is tested by ix-list equality",
            "runs": runs, "verdict": verdict, "rpc_host": "api.mainnet-beta.solana.com", "rpc_calls": list(getattr(rpc, "calls", [])),
            "not_measured": ["the post-state accounts of the failed control run (the RPC does not report them reliably on error)", "landing", "proceeds or price impact (min_out = 1, no quote used)", "a signed or sent transaction (none exists)"]}


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
    ap.add_argument("--holder", action="store_true", help="holder mode: unsigned simulate with a public holder of the mint as owner, full balance + balance-1 control")
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)
    if "?" in a.rpc or "api-key" in a.rpc.lower() or "@" in a.rpc:
        raise SystemExit("--rpc must be a plain public URL (no query string, no key)")
    prio = ax.PRIORITY_P155 if a.priority == "p155" else ax.PRIORITY_DEFAULT
    if a.holder:
        rec = holder_run(RateLimitedRpc(a.rpc), a.mint, a.pool, prio)
        sha = write_record(rec, a.out)
        print(json.dumps({"mint": a.mint, "token_program": rec["token_program"], "holder": rec["holder"]["owner"], "verdict": rec["verdict"],
                          "full_slot": rec["runs"]["full_balance"]["simulate"]["context_slot"], "sha256": sha, "out": str(a.out)}))
        return 0
    rec = run(RateLimitedRpc(a.rpc), a.mint, a.pool, a.amount, prio, funded_probe=not a.no_funded_probe)
    sha = write_record(rec, a.out)
    print(json.dumps({"mint": a.mint, "token_program": rec["token_program"], "class": rec["verdict"]["class"], "err": rec["simulate"]["err"],
                      "ix_role": rec["verdict"]["ix_role"],
                      "funded_probe_class": (rec["funded_payer_probe"] or {}).get("verdict", {}).get("class"),
                      "funded_probe_err": (rec["funded_payer_probe"] or {}).get("simulate", {}).get("err"), "sha256": sha, "out": str(a.out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
