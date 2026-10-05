"""DEC-019 execution probe, PR-B: LIVE mode. Signs with the probe key and sends real transactions.

Defaults OFF. Live needs BOTH `"mode": "live"` in the config AND the `--live` CLI flag
(tools.probe_executor.resolve_mode). Either one missing runs the keyless dry run.

The key is read in-process from the probe wallet file, never logged, never serialized and never
stored anywhere but the solders Keypair. Only the public key is ever printed. Everything that is
persisted (state file, fill log) holds signatures, signed-tx bytes and counters, never key material.

Flow per signal (state machine, restart-safe):
  1. Limits, balance guard, V-priced quote (same code as the dry run).
  2. Write-ahead: attempts += 1 and pending[mint] = {signature, signed tx, lastValidBlockHeight}
     are fsynced BEFORE the first send.
  3. sendTransaction (skipPreflight, maxRetries 0), then every ~2 s rebroadcast the SAME signed tx
     until getSignatureStatuses shows it or the blockhash expires. A second buy is never built.
  4. Landed: getTransaction meta gives real tokens, real SOL, slot, fee. Position opens.
  5. Exit (tp50/sl30/30 min on V-priced marks): sell the actual full balance, close the token ATA
     and the WSOL account in the same tx. Failure -> fresh blockhash and retry; after N failures the
     position is `stuck`, new buys halt, and the sell retries every 30 s.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import resource
import stat
import sys
import time
from pathlib import Path
from typing import Any, Callable

from solders.hash import Hash
from solders.instruction import Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from tools import probe_executor as pe
from tools import pumpswap_simulate as sim
from tools import pumpswap_tx as tx

CREDENTIAL_NAME = "probe-wallet"  # LoadCredential=probe-wallet:/etc/mal-probe/probe-wallet.json
MIN_BALANCE_BUFFER = 20_000_000  # refuse to buy below size + 0.02 SOL (config may raise, never lower)
REBROADCAST_MS = 2_000
BLOCKHASH_TTL_MS = 10_000
BLOCKHASH_STALE_OK_MS = 40_000  # reuse a cached hash this long if the refresh call fails
EXPIRY_RECHECK_MS = 5_000  # after the block height passes, look once more before calling it expired
PENDING_POLL_MS = 1_000  # confirm-loop cadence (unchanged from the old 1 s loop)
STUCK_RETRY_MS = 30_000  # first stuck retry delay; doubles per failure up to the max
STUCK_RETRY_MAX_MS = 600_000
SELL_MAX_ATTEMPTS = 10  # hard cap per position; config can only lower it
CLOCK_BACK_TOLERANCE_MS = 60_000
EXPIRY_RECHECKS = 2  # status re-reads (searchTransactionHistory), >= EXPIRY_RECHECK_MS apart, before `expired`
COMPUTE_BUDGET_PROGRAM = Pubkey.from_string("ComputeBudget111111111111111111111111111111")
ALLOWED_PROGRAMS = frozenset({COMPUTE_BUDGET_PROGRAM, tx.SYSTEM_PROGRAM, tx.TOKEN_PROGRAM, tx.TOKEN_2022_PROGRAM,
                              tx.ATA_PROGRAM, tx.PUMPSWAP_PROGRAM})
META_FALLBACK_MS = 60_000
LIVE_MAX_RPS = 5.0
# The log-text match ("slippage" in the tx logs) is the PRIMARY signal. The numeric Pump AMM
# ExceededSlippage code below is UNVERIFIED: no IDL or decoder in this repo carries it. Config key
# `slippage_error_codes` overrides.
SLIPPAGE_ERROR_CODES = frozenset({6004})
FAIL_CLASSES = ("expired", "slippage_exceeded", "insufficient_funds", "other")


# --- process hardening and key loading -------------------------------------------------------


def harden_process() -> None:
    """No core dumps, not ptrace-able/dumpable. Must run before the key is read."""
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(4, 0, 0, 0, 0) != 0:  # PR_SET_DUMPABLE = 4
        raise SystemExit("prctl(PR_SET_DUMPABLE, 0) failed: refusing to load the key")


def credential_path(env: dict[str, str] | None = None) -> str:
    """The ONLY place live mode reads the key from: systemd's per-service credential directory
    (LoadCredential=probe-wallet:/etc/mal-probe/probe-wallet.json in the live drop-in). There is no path
    override: the key at /etc/mal-probe is root-only and never opened by this process directly."""
    d = (os.environ if env is None else env).get("CREDENTIALS_DIRECTORY")
    if not d:
        raise SystemExit("CREDENTIALS_DIRECTORY is not set: live mode only loads the key from the systemd credential")
    return str(Path(d) / CREDENTIAL_NAME)


def load_probe_key(path: str | None = None) -> Keypair:
    """Solana CLI keypair file (JSON array of 64 ints) from the systemd credential (default). The credential
    directory is private to the service, so no owner/mode/parent checks are made here. Error text never
    contains file content."""
    path = path or credential_path()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        raise SystemExit("probe key credential cannot be opened") from None
    buf = bytearray(1024)
    secret = bytearray()
    try:
        with os.fdopen(fd, "rb", buffering=0) as fh:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                raise SystemExit("probe key credential is not a regular file")
            n = fh.readinto(buf)
        del buf[n:]
        try:
            arr = json.loads(buf)
            if not (isinstance(arr, list) and len(arr) == 64 and all(isinstance(b, int) and 0 <= b < 256 for b in arr)):
                raise ValueError
            secret = bytearray(arr)
            del arr
            return Keypair.from_bytes(bytes(secret))
        except SystemExit:
            raise
        except Exception:
            raise SystemExit("probe key credential is malformed") from None
    finally:
        for b in (buf, secret):  # best effort: the immutable bytes() copy handed to solders cannot be zeroed
            for i in range(len(b)):
                b[i] = 0


# --- failure classification and meta parsing -------------------------------------------------


def classify_failure(err: Any, logs: list[str] | None = None, codes: frozenset[int] = SLIPPAGE_ERROR_CODES) -> str:
    """slippage_exceeded | insufficient_funds | other. (`expired` is decided by the confirm loop,
    since an expired tx has no on-chain err.)"""
    text = " ".join(logs or []).lower()
    if isinstance(err, str) and err in ("InsufficientFundsForFee", "InsufficientFundsForRent"):
        return "insufficient_funds"
    if isinstance(err, dict) and "InstructionError" in err:
        try:
            _idx, inner = err["InstructionError"]
        except (TypeError, ValueError):
            return "other"
        if inner == "InsufficientFunds":
            return "insufficient_funds"
        if isinstance(inner, dict) and "Custom" in inner:
            code = inner["Custom"]
            if code in codes or "slippage" in text:
                return "slippage_exceeded"
            if code == 1 and "insufficient" in text:
                return "insufficient_funds"
    if "slippage" in text and err is not None:
        return "slippage_exceeded"
    return "other"


def _keys(res: dict) -> list[str]:
    return [k if isinstance(k, str) else k["pubkey"] for k in res["transaction"]["message"]["accountKeys"]]


def _vault_post(res: dict, base_mint: Pubkey, base_vault: str | None, quote_vault: str | None) -> dict[str, int | None]:
    """Post-tx raw balances of the pool's base and quote vaults, by account index in `meta.postTokenBalances`. None for
    a vault that is not identified, absent, duplicated, of the wrong mint or unparseable (never 0)."""
    out: dict[str, int | None] = {"base": None, "quote": None}
    try:
        keys, rows = _keys(res), (res["meta"].get("postTokenBalances") or [])
    except Exception:
        return out
    for name, key, mint in (("base", base_vault, str(base_mint)), ("quote", quote_vault, str(tx.WSOL_MINT))):
        try:
            if not key or key not in keys:
                continue
            idx = keys.index(key)
            vals = [r for r in rows if r.get("accountIndex") == idx]
            if len(vals) != 1 or vals[0].get("mint") not in (None, mint):
                continue
            amount = int(vals[0]["uiTokenAmount"]["amount"])
            out[name] = amount if amount > 0 else None
        except Exception:
            continue
    return out


def buy_tx_mark(p: dict[str, Any], tokens: int, vault_post: dict[str, int | None] | None) -> tuple[float, str]:
    """The position mark, final at creation. Post-buy spot on the V-priced book from our own buy tx's vault balances:
    (quote_vault_post + V) / base_vault_post, in spot_sol_per_ui units = the paper rule's post-buy spot, no drift.
    Fallback: the effective fill price, net_in / tokens (same units). Last resort: the send-state mark."""
    vp, v = vault_post or {}, p.get("v_lamports")
    if vp.get("base") and vp.get("quote") and isinstance(v, int) and v >= 0:
        mark = pe.pcm.spot_sol_per_ui(vp["quote"] + v, vp["base"])
        if mark > 0:
            return mark, "buy_tx_post"
    if tokens > 0 and p.get("q_net_in", 0) > 0:
        return p["q_net_in"] / (tokens * 1000), "fill_price"
    return p["q_mark"], "send_state"


def parse_meta(res: dict, user: Pubkey, base_mint: Pubkey, base_ata: Pubkey,
               base_vault: str | None = None, quote_vault: str | None = None) -> dict[str, Any]:
    """Everything the fill row needs from one getTransaction result (encoding json)."""
    meta = res["meta"]
    keys = _keys(res)
    ui = keys.index(str(user))
    pre, post = int(meta["preBalances"][ui]), int(meta["postBalances"][ui])

    ai = keys.index(str(base_ata)) if str(base_ata) in keys else None

    def tok(rows: list | None, must_exist: bool) -> int | None:
        """Token balance of OUR ata by account index. None means unknown (fail closed), never 0."""
        if rows is None or ai is None:
            return None
        vals = []
        for r in rows:
            if r.get("accountIndex") != ai:
                continue
            if r.get("mint") not in (None, str(base_mint)):
                return None
            vals.append(int(r["uiTokenAmount"]["amount"]))
        if len(vals) > 1 or (not vals and must_exist):
            return None
        return vals[0] if vals else 0

    pre_t, post_t = tok(meta.get("preTokenBalances"), False), tok(meta.get("postTokenBalances"), True)
    delta = 0 if meta.get("err") is not None else (None if pre_t is None or post_t is None else post_t - pre_t)
    return {
        "slot": res.get("slot"),
        "err": meta.get("err"),
        "fee": int(meta["fee"]),
        "sol_delta": post - pre,  # post - pre of the wallet: includes fees, rent, pool legs
        "token_delta": delta,  # None = unknown: caller re-reads the ATA balance
        "ata_pre_amount": pre_t,  # our token ATA balance before the tx; None = unknown
        "ata_rent_pre": int(meta["preBalances"][ai]) if ai is not None else 0,
        "ata_rent_post": int(meta["postBalances"][ai]) if ai is not None else 0,
        "logs": meta.get("logMessages") or [],
        "vault_post": _vault_post(res, base_mint, base_vault, quote_vault),
    }


def bps(actual: float, ref: float) -> float | None:
    return round((actual - ref) * 10_000 / ref, 2) if ref else None


# --- pre-signing whitelist (RPC data never reaches a signature unchecked) -----------------------


class UnsafeTx(pe.RpcError):
    def __init__(self, why: str):
        super().__init__(f"unsafe_tx:{why}")


def validate_message(msg: Message, ps: tx.PoolState, mint: Pubkey, user: Pubkey, max_priority_lamports: int) -> None:
    """Fail closed before every signature. Pool data and the mint's owner come from RPC, so: the base token
    program is Token or Token-2022, quote is WSOL, base mint is the requested mint, the pool and its vaults are
    the derived addresses; only ComputeBudget/System/Token/Token-2022/ATA/PumpSwap are invoked; the user is the
    only signer; every system transfer, ATA create and account close touches only the user's own accounts."""
    if ps.base_mint != mint:
        raise UnsafeTx("base_mint_mismatch")
    if ps.quote_mint != tx.WSOL_MINT:
        raise UnsafeTx("quote_not_wsol")
    if ps.base_token_program not in (tx.TOKEN_PROGRAM, tx.TOKEN_2022_PROGRAM) or ps.quote_token_program != tx.TOKEN_PROGRAM:
        raise UnsafeTx("token_program")
    if ps.pool != tx.canonical_pool(mint):
        raise UnsafeTx("pool_not_canonical")
    if ps.base_vault != tx.ata(ps.pool, mint, ps.base_token_program) or ps.quote_vault != tx.ata(ps.pool, tx.WSOL_MINT, tx.TOKEN_PROGRAM):
        raise UnsafeTx("vault_not_derived")
    keys = list(msg.account_keys)
    if msg.header.num_required_signatures != 1 or keys[0] != user:
        raise UnsafeTx("signers")
    u_base = tx.ata(user, mint, ps.base_token_program)
    u_wsol = tx.ata(user, tx.WSOL_MINT, tx.TOKEN_PROGRAM)
    cu_limit = cu_price = None
    for ix in msg.instructions:
        pid = keys[ix.program_id_index]
        acc = [keys[i] for i in ix.accounts]
        data = bytes(ix.data)
        if pid not in ALLOWED_PROGRAMS:
            raise UnsafeTx("program_not_allowed")
        if pid == COMPUTE_BUDGET_PROGRAM:
            if data[:1] == b"\x02" and len(data) == 5:
                cu_limit = int.from_bytes(data[1:], "little")
            elif data[:1] == b"\x03" and len(data) == 9:
                cu_price = int.from_bytes(data[1:], "little")
            else:
                raise UnsafeTx("compute_budget_ix")
        elif pid == tx.SYSTEM_PROGRAM:
            if not (len(data) == 12 and data[:4] == b"\x02\x00\x00\x00" and acc == [user, u_wsol]):
                raise UnsafeTx("system_ix")
        elif pid == tx.ATA_PROGRAM:
            ok = (data == b"\x01" and len(acc) == 6 and acc[0] == user and acc[2] == user and acc[4] == tx.SYSTEM_PROGRAM
                  and ((acc[3] == mint and acc[1] == u_base and acc[5] == ps.base_token_program)
                       or (acc[3] == tx.WSOL_MINT and acc[1] == u_wsol and acc[5] == tx.TOKEN_PROGRAM)))
            if not ok:
                raise UnsafeTx("ata_ix")
        elif pid in (tx.TOKEN_PROGRAM, tx.TOKEN_2022_PROGRAM):
            if data == b"\x11":
                ok = acc == [u_wsol] and pid == tx.TOKEN_PROGRAM
            elif data == b"\x09":
                ok = len(acc) == 3 and acc[1] == user and acc[2] == user and (
                    (acc[0] == u_wsol and pid == tx.TOKEN_PROGRAM) or (acc[0] == u_base and pid == ps.base_token_program))
            else:
                ok = False
            if not ok:
                raise UnsafeTx("token_ix")
        else:  # PumpSwap
            if (len(acc) < 19 or acc[:9] != [ps.pool, user, tx.GLOBAL_CONFIG, mint, tx.WSOL_MINT, u_base, u_wsol, ps.base_vault, ps.quote_vault]
                    or acc[11] != ps.base_token_program or acc[12] != tx.TOKEN_PROGRAM or acc[13] != tx.SYSTEM_PROGRAM
                    or acc[14] != tx.ATA_PROGRAM or acc[16] != tx.PUMPSWAP_PROGRAM):
                raise UnsafeTx("swap_accounts")
    if cu_limit is None or cu_price is None or cu_price * cu_limit // 1_000_000 > max_priority_lamports + 1:
        raise UnsafeTx("priority_over_cap")


# --- blockhash cache -------------------------------------------------------------------------


class BlockhashCache:
    def __init__(self, rpc: Callable[[str, list], dict], commitment: str, now_ms: Callable[[], int]):
        self.rpc, self.commitment, self.now_ms = rpc, commitment, now_ms
        self._v: tuple[Hash, int] | None = None
        self._t = 0

    def get(self, fresh: bool = False) -> tuple[Hash, int]:
        now = self.now_ms()
        if self._v is None or fresh or now - self._t >= BLOCKHASH_TTL_MS:
            try:
                v = self.rpc("getLatestBlockhash", [{"commitment": self.commitment}])["value"]
                self._v, self._t = (Hash.from_string(v["blockhash"]), int(v["lastValidBlockHeight"])), now
            except (Exception, SystemExit) as exc:
                if self._v is None or now - self._t >= BLOCKHASH_STALE_OK_MS:
                    raise pe.RpcError(pe.error_label(exc)) from None
        assert self._v is not None
        return self._v


# --- live executor ---------------------------------------------------------------------------


def close_token_account(account: Pubkey, owner: Pubkey, token_program: Pubkey) -> Instruction:
    """Close an empty token account (rent to the owner) under ITS token program (Token or Token-2022)."""
    ix = tx.close_account(account, owner, owner)
    return Instruction(token_program, bytes(ix.data), ix.accounts)


class LiveExecutor(pe.Executor):
    def __init__(self, rpc: Callable[[str, list], dict], cfg: dict[str, Any], keypair: Keypair, *,
                 now_ms: Callable[[], int] | None = None):
        super().__init__(rpc, {**cfg, "mode": "live"}, now_ms=now_ms)
        self._kp = keypair
        self.user = keypair.pubkey()
        self.sell_retries = max(1, int(cfg.get("sell_retries", 5)))
        self.sell_max_attempts = min(SELL_MAX_ATTEMPTS, max(self.sell_retries, int(cfg.get("sell_max_attempts", SELL_MAX_ATTEMPTS))))
        self.balance_buffer = max(MIN_BALANCE_BUFFER, int(cfg.get("min_balance_buffer_lamports", MIN_BALANCE_BUFFER)))
        self.slip_codes = frozenset(cfg.get("slippage_error_codes") or SLIPPAGE_ERROR_CODES)
        self.poll_ms = int(max(1.0, float(cfg.get("poll_s", 5.0))) * 1000)
        self.bh = BlockhashCache(rpc, self.commitment, self.now_ms)
        self._last_tail = 0
        self._height: int | None = None
        self._last_adv = 0
        self._clock_saved = 0

    def __repr__(self) -> str:  # never reveal the Keypair
        return f"LiveExecutor(user={self.user})"

    # -- helpers
    def _alert(self, what: str, mint: str = "", **kw: Any) -> None:
        self._log("alert", mint, alert=what, **kw)
        print(f"probe_executor ALERT {what} mint={mint} {json.dumps(kw, default=str)[:300]}", flush=True)

    def _sign(self, msg: Message, ps: tx.PoolState, mint: str) -> tuple[str, str]:
        validate_message(msg, ps, Pubkey.from_string(mint), self.user, self.limits.priority_lamports)
        t = VersionedTransaction(msg, [self._kp])
        raw = bytes(t)
        if len(raw) > tx.TX_SIZE_LIMIT:
            raise ValueError("tx too large")
        return str(t.signatures[0]), base64.b64encode(raw).decode()

    def _send(self, p: dict[str, Any], mint: str) -> None:
        try:
            got = self.rpc("sendTransaction", [p["tx_b64"], {"encoding": "base64", "skipPreflight": True, "maxRetries": 0}])
            if got != p["signature"]:
                self._alert("signature_mismatch", mint)
        except (Exception, SystemExit) as exc:
            self._alert("send_error", mint, label=pe.error_label(exc), kind_=p["kind"])
        now = self.now_ms()
        p["last_send_ms"] = now
        p["sends"] = p.get("sends", 0) + 1
        p.setdefault("first_send_ms", now)

    def sell_stuck(self) -> bool:
        return any(pos.get("stuck") or pos.get("abandoned") for pos in self.state.open.values())

    def _ata_balance(self, ata: str) -> int | None:
        try:
            return int(self.rpc("getTokenAccountBalance", [ata, {"commitment": self.commitment}])["value"]["amount"])
        except (Exception, SystemExit):
            return None

    def _balance(self) -> int | None:
        try:
            return int(self.rpc("getBalance", [str(self.user), {"commitment": self.commitment}])["value"])
        except (Exception, SystemExit):
            return None

    # -- entry
    def handle_signal(self, sig: dict[str, Any]) -> None:
        now = self.now_ms()
        mint = sig["mint"]
        why = pe.check_buy(self.limits, self.state, now, pe.check_stop_file(self.limits), "live", pe.check_halt_file(self.limits))
        if not why and now + CLOCK_BACK_TOLERANCE_MS < self.state.max_seen_ms:
            why = "clock_backwards"
        if not why and self.sell_stuck():
            why = "sell_stuck"
        buys_pending = sum(1 for p in self.state.pending.values() if p["kind"] == "buy")
        if not why and len(self.state.open) + buys_pending >= self.limits.max_open:
            why = "max_open"
        if why:
            return self._skip(sig, f"limit:{why}")
        if pe.signal_age_ms(sig, now) > self.max_signal_age_ms:
            return self._skip(sig, "stale_signal")
        if mint in self.state.open or mint in self.state.pending:
            return self._skip(sig, "already_open")
        if mint in self.state.bought:
            return self._skip(sig, "already_bought")
        snap, pool, err = self._snapshot(mint)
        t_state = self.now_ms()
        if snap is None:
            return self._skip(sig, err or "no_pool", pool=pool)
        if snap.quote_priced is None:
            return self._skip(sig, "no_v", pool=pool, pool_slot=snap.slot)
        drift = pe.drift_vs_seed(snap)  # logged only; never a reason to skip
        spend = self.limits.size_lamports
        q = pe.entry_quote(snap, spend)
        if q["tokens"] <= 0:
            return self._skip(sig, "zero_quote", pool=pool, pool_slot=snap.slot, drift_vs_seed=drift)
        bal = self._balance()
        if bal is None:
            return self._skip(sig, "balance_unreadable", pool=pool, drift_vs_seed=drift)
        if bal < spend + self.balance_buffer:
            return self._skip(sig, "balance_guard", pool=pool, balance_lamports=bal, drift_vs_seed=drift)
        try:
            bhash, lvbh = self.bh.get()
            msg = pe.buy_probe_message(snap, self.user, spend, self.slip_bps, q["tokens"], self.limits.priority_lamports, bhash)
            signature, tx_b64 = self._sign(msg, snap.ps, mint)
        except (Exception, SystemExit) as exc:
            if isinstance(exc, UnsafeTx):
                self._alert("unsafe_tx_refused", mint, why=exc.label)
            return self._skip(sig, f"build_error:{pe.error_label(exc)}", pool=pool, drift_vs_seed=drift)
        t_built = self.now_ms()
        # Write-ahead: the attempt, the signature and the signed tx are durable BEFORE the first send.
        self.state.attempts += 1
        self.state.bought.append(mint)
        if self.state.first_attempt_ms is None:
            self.state.first_attempt_ms = now
        self.state.pending[mint] = {
            "kind": "buy", "signature": signature, "tx_b64": tx_b64, "lvbh": lvbh, "pool": pool, "snap_slot": snap.slot, "drift_vs_seed": drift,
            "decision_t_ms": sig["decision_t_ms"], "receive_ms": now, "score": sig.get("score"), "spend": spend,
            "q_tokens": q["tokens"], "q_net_in": q["net_in"], "q_mark": q["mark"], "fee_ppm": q["fee_ppm"],
            "v_lamports": snap.v, "base_vault": str(snap.ps.base_vault), "quote_vault": str(snap.ps.quote_vault), "base_ata": str(tx.ata(self.user, snap.ps.base_mint, snap.ps.base_token_program)),
            "base_mint": str(snap.ps.base_mint), "base_tp": str(snap.ps.base_token_program), "sends": 0,
            "seen_ms": sig.get("seen_ms"), "state_ms": t_state, "state_slot": snap.slot, "built_ms": t_built,
            "runner_latency": sig.get("latency"),
        }
        self.save()
        p = self.state.pending[mint]
        self._send(p, mint)
        self.save()
        try:  # sim_tokens for the "vs simulated buy" column; after the send so it never delays landing
            res = pe.sim_message(self.rpc, msg, self.user, [self.user, Pubkey.from_string(p["base_ata"])])
            accts = res.get("accounts") or [None, None]
            if res.get("err") is None and len(accts) > 1 and accts[1]:
                p["sim_tokens"] = sim.token_amount(sim._b64(accts[1]))
        except (Exception, SystemExit):
            pass
        self.save()

    # -- confirm loop
    def _block_height(self) -> int | None:
        try:
            return int(self.rpc("getBlockHeight", [{"commitment": "confirmed"}]))
        except (Exception, SystemExit):
            return None

    @pe.critical
    def advance_pending(self) -> None:
        pend = self.state.pending
        if not pend:
            return
        mints = list(pend)
        try:
            res = self.rpc("getSignatureStatuses", [[pend[m]["signature"] for m in mints], {"searchTransactionHistory": True}])
            statuses = res["value"]
        except (Exception, SystemExit):
            return  # cannot judge anything this step; never rebroadcast or expire blind
        height: int | None = None
        halt = pe.check_halt_file(self.limits)  # STOP does not stop rebroadcasts: in-flight txs must land or expire
        for mint, st in zip(mints, statuses):
            p = pend[mint]
            now = self.now_ms()
            if st and (st.get("err") is not None or st.get("confirmationStatus") in ("confirmed", "finalized")):
                p.setdefault("confirm_seen_ms", now)
                p["status_slot"] = st.get("slot")
                self._resolve_landed(mint, p, st)
                continue
            if st:  # processed only: wait
                continue
            if height is None:
                height = self._block_height()
            if height is not None and height > p["lvbh"]:
                last = p.setdefault("expired_seen_ms", now)
                p.setdefault("expiry_checks", 0)
                if now - last >= EXPIRY_RECHECK_MS:  # this step's statuses (history search on) were still empty
                    p["expiry_checks"] += 1
                    p["expired_seen_ms"] = now
                    if p["expiry_checks"] >= EXPIRY_RECHECKS:
                        self._resolve_expired(mint, p)
                continue
            if not halt and now - p.get("last_send_ms", 0) >= REBROADCAST_MS:
                self._send(p, mint)  # the SAME signed tx
        self.save()

    def _timing(self, p: dict[str, Any]) -> dict[str, Any]:
        t_send, t_conf = p.get("first_send_ms"), p.get("confirm_seen_ms") or self.now_ms()
        stages: dict[str, Any] = {}
        if p.get("kind") == "buy":  # stage stamps (see `--latency-report`); `latency` is the runner row's own object, verbatim
            stages = dict(seen_ms=p.get("seen_ms"), state_ms=p.get("state_ms"), state_slot=p.get("state_slot"),
                          built_ms=p.get("built_ms"), sent_ms=t_send, latency=p.get("runner_latency"))
        return dict(
            **stages,
            decision_t_ms=p.get("decision_t_ms"), receive_ms=p.get("receive_ms"), first_send_ms=t_send, confirm_seen_ms=t_conf,
            ms_decision_to_send=(t_send - p["decision_t_ms"]) if t_send and p.get("decision_t_ms") else None,
            ms_send_to_confirm=(t_conf - t_send) if t_send else None, sends=p.get("sends"), signature=p["signature"],
            snapshot_slot=p.get("snap_slot"),
        )

    @pe.critical
    def _resolve_expired(self, mint: str, p: dict[str, Any]) -> None:
        del self.state.pending[mint]
        row = dict(landed=False, fail_class="expired", **self._timing(p), pool=p.get("pool"))
        if p["kind"] == "buy":
            self._log("buy", mint, spend_lamports=p["spend"], expected_tokens=p["q_tokens"], **row)
        else:
            pos = self.state.open[mint]
            self._sell_failed(pos)
            self._log("sell", mint, sell_attempt=pos["sell_attempts"], stuck=bool(pos.get("stuck")), **row)
        self.save()

    @pe.critical
    def _resolve_landed(self, mint: str, p: dict[str, Any], st: dict[str, Any]) -> None:
        try:
            res = self.rpc("getTransaction", [p["signature"], {"encoding": "json", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}])
        except (Exception, SystemExit):
            res = None
        now = self.now_ms()
        src = p if p["kind"] == "buy" else self.state.open[mint]
        m = None
        if res and res.get("meta"):
            try:  # malformed or foreign data must never crash the loop: retry later
                sigs = (res.get("transaction") or {}).get("signatures") or []
                if not sigs or sigs[0] != p["signature"]:
                    raise ValueError("signature mismatch")
                m = parse_meta(res, self.user, Pubkey.from_string(src["base_mint"]), Pubkey.from_string(src["base_ata"]),
                                 src.get("base_vault"), src.get("quote_vault"))
            except Exception:
                if not p.get("parse_alerted"):
                    p["parse_alerted"] = True
                    self._alert("meta_malformed", mint, signature=p["signature"])
        if m is None:
            since = p.setdefault("meta_wait_since", now)
            if now - since >= META_FALLBACK_MS and p["kind"] == "buy":
                if not p.get("meta_alerted"):
                    p["meta_alerted"] = True
                    self._alert("meta_missing", mint, signature=p["signature"])
                m = self._fallback_buy_meta(p, st)
            if m is None:
                return
        if p["kind"] == "buy":
            self._finish_buy(mint, p, m)
        else:
            self._finish_sell(mint, p, m)

    def _fallback_buy_meta(self, p: dict[str, Any], st: dict[str, Any]) -> dict[str, Any] | None:
        """No usable meta for 60 s: book the estimated fee (and spend if tokens arrived), flagged `estimated`."""
        fee = tx.BASE_FEE_PER_SIGNATURE + self.limits.priority_lamports
        base = dict(slot=st.get("slot"), fee=fee, ata_rent_pre=0, ata_rent_post=0, logs=[], estimated=True)
        if st.get("err") is not None:
            return {**base, "err": st["err"], "sol_delta": -fee, "token_delta": 0}
        bal = self._ata_balance(p["base_ata"])
        if bal is None:
            return None
        return {**base, "err": None, "sol_delta": -(p["spend"] + fee), "token_delta": bal}

    def _fee_split(self, fee: int) -> tuple[int, int]:
        base = tx.BASE_FEE_PER_SIGNATURE
        return min(base, fee), max(0, fee - base)

    @pe.critical
    def _finish_buy(self, mint: str, p: dict[str, Any], m: dict[str, Any]) -> None:
        # The ATA balance read (an RPC call) happens BEFORE pending is deleted, so the position is always in
        # `pending` or `open` whenever state is saved or counted.
        base_fee, prio_fee = self._fee_split(m["fee"])
        row: dict[str, Any] = dict(
            spend_lamports=p["spend"], expected_tokens=p["q_tokens"], sim_tokens=p.get("sim_tokens"), pool=p["pool"],
            landed_slot=m["slot"], slots_between=(m["slot"] - p["snap_slot"]) if m["slot"] and p.get("snap_slot") else None,
            fee_lamports=m["fee"], base_fee_lamports=base_fee, priority_fee_lamports=prio_fee, priority_lamports=self.limits.priority_lamports, v_lamports=p.get("v_lamports"),
            drift_vs_seed=p.get("drift_vs_seed"), pool_slot=p.get("snap_slot"),
            **self._timing(p),
        )
        if m["err"] is not None:
            cls = classify_failure(m["err"], m["logs"], self.slip_codes)
            del self.state.pending[mint]
            self.state.realized_lamports += m["sol_delta"]  # the fee actually paid
            self._log("buy", mint, landed=False, fail_class=cls, err=m["err"], cost_lamports=-m["sol_delta"], **row)
            self.save()
            return
        cost = -m["sol_delta"]
        tokens = m["token_delta"]
        tokens_src = "meta"
        if tokens is None:  # fail closed: unknown is not zero. The ATA balance is the authority.
            tokens, tokens_src = self._ata_balance(p["base_ata"]), "ata_balance"
        balance_pending = tokens is None
        if balance_pending:  # still unknown: open flagged, and the sell reads whatever the ATA holds
            tokens, tokens_src = p["q_tokens"], "quote_estimate"
        row.update(tokens_source=tokens_src, estimated=bool(m.get("estimated")),
            landed=True, tokens_received=tokens, sol_spent_lamports=cost, rent_charged_lamports=m["ata_rent_post"],
            pool_fee_est_lamports=p["fee_ppm"] * p["spend"] // 1_000_000,
            entry_vs_quote_bps=bps(tokens, p["q_tokens"]), entry_vs_sim_bps=bps(tokens, p["sim_tokens"]) if p.get("sim_tokens") else None,
            price_vs_quote_bps=bps(p["q_tokens"], tokens) if tokens else None,  # cost per token vs quoted, + = we paid more
        )
        del self.state.pending[mint]
        mark, mark_source = buy_tx_mark(p, tokens, m.get("vault_post"))
        mark_shift = bps(mark, p["q_mark"])
        row.update(mark_send=p["q_mark"], mark=mark, mark_source=mark_source, mark_shift_bps=mark_shift)
        self._log("buy", mint, **row)
        if tokens <= 0:
            self.state.realized_lamports -= cost  # SOL is spent and nothing came back: a realized loss now
            self._alert("buy_landed_zero_tokens", mint, signature=p["signature"], cost_lamports=cost)
        else:
            self.state.open[mint] = {
                "mint": mint, "pool": p["pool"], "t_entry_ms": self.now_ms(), "tokens": tokens, "net_in": p["q_net_in"],
                "mark": mark, "mark_send": p["q_mark"], "mark_source": mark_source, "mark_shift_bps": mark_shift, "spend": p["spend"], "buy_cost_lamports": cost, "buy_sig": p["signature"],
                "base_ata": p["base_ata"], "base_mint": p["base_mint"], "base_tp": p["base_tp"], "sell_attempts": 0,
                "ata_pre_amount": m.get("ata_pre_amount"), "extra_cost": 0, "stuck": False, "abandoned": False, "exit_reason": None, "balance_pending": balance_pending, "q_tokens": p["q_tokens"], "buy_slot": m["slot"],
            }
        self.save()

    # -- exit
    def _sell_failed(self, pos: dict[str, Any]) -> None:
        pos["sell_attempts"] = pos.get("sell_attempts", 0) + 1
        pos["last_sell_fail_ms"] = self.now_ms()
        if pos["sell_attempts"] >= self.sell_retries and not pos.get("stuck"):
            pos["stuck"] = True
            self._alert("sell_stuck", pos["mint"], attempts=pos["sell_attempts"])
        if pos["sell_attempts"] >= self.sell_max_attempts and not pos.get("abandoned"):
            pos["abandoned"] = True  # HALT-like for this position: no more sells, buys stay stopped
            self._alert("sell_abandoned", pos["mint"], attempts=pos["sell_attempts"])

    @pe.critical
    def _start_sell(self, mint: str, pos: dict[str, Any], snap: pe.Snapshot, reason: str, now: int,
                    exit_snapshot: str = "full") -> None:
        known = pos.get("tokens")
        # Accepted risk: tokens received on the buy equal the ATA balance only if the ATA was empty before it.
        # Each mint gets a fresh ATA and the probe never re-buys a mint, so a pre-existing balance is not expected;
        # when the buy meta shows a non-zero (or unknown) pre-balance anyway, the first sell reads the RPC.
        if (self.fast_exit and pos.get("sell_attempts", 0) == 0 and isinstance(known, int) and known > 0
                and not pos.get("balance_pending") and pos.get("ata_pre_amount") == 0):
            bal, balance_source = known, "buy_meta"  # first attempt: the buy's own token delta, no RPC round trip
        else:
            balance_source = "rpc"
            try:
                bal = int(self.rpc("getTokenAccountBalance", [pos["base_ata"], {"commitment": self.commitment}])["value"]["amount"])
            except (Exception, SystemExit):
                return
        if bal <= 0:
            if not pos.get("zero_alerted"):
                pos["zero_alerted"] = True
                self._alert("zero_token_balance", mint)
            self._sell_failed(pos)
            self.save()
            return
        chk = pe.exit_check({**pos, "tokens": bal}, snap, now, own_trade_in_state=True)
        if not chk["quote_out"] or chk["quote_out"] <= 0:
            return
        min_out = tx.min_out_with_slippage(chk["quote_out"], self.slip_bps)
        try:
            # A retry needs a FRESH blockhash: the same message + hash signs to the same signature, and a failed
            # tx's signature is already in the status cache.
            bhash, lvbh = self.bh.get(fresh=pos.get("sell_attempts", 0) > 0)
            ixs = [*tx.sell_instructions(snap.ps, self.user, bal, min_out, priority_total_lamports=self.limits.priority_lamports),
                   close_token_account(Pubkey.from_string(pos["base_ata"]), self.user, Pubkey.from_string(pos["base_tp"]))]
            signature, tx_b64 = self._sign(Message.new_with_blockhash(ixs, self.user, bhash), snap.ps, mint)
            if signature in pos.setdefault("sell_sigs", []):
                raise pe.RpcError("duplicate_signature")
            pos["sell_sigs"].append(signature)
        except (Exception, SystemExit) as exc:
            self._alert("sell_build_error", mint, label=pe.error_label(exc))
            return
        pos["exit_reason"] = reason
        self.state.pending[mint] = {
            "kind": "sell", "signature": signature, "tx_b64": tx_b64, "lvbh": lvbh, "pool": pos["pool"], "snap_slot": snap.slot,
            "decision_t_ms": now, "receive_ms": now, "tokens": bal, "q_out": chk["quote_out"], "min_out": min_out, "reason": reason,
            "ret": chk["ret"], "sends": 0, **self._exit_fields(exit_snapshot), "balance_source": balance_source,
        }
        self.save()  # write-ahead
        self._send(self.state.pending[mint], mint)
        self.save()

    def _exit_candidates(self) -> list[str]:
        now = self.now_ms()
        out = []
        for mint, pos in self.state.open.items():
            if mint in self.state.pending or pos.get("abandoned"):
                continue
            if pos.get("stuck"):
                wait = min(STUCK_RETRY_MS * 2 ** max(0, pos["sell_attempts"] - self.sell_retries), STUCK_RETRY_MAX_MS)
                if now - pos.get("last_sell_fail_ms", 0) < wait:
                    continue
            out.append(mint)
        return out

    def poll_positions(self) -> None:
        batched = self._batched_exit_snapshots(self._exit_candidates())
        for mint, pos in list(self.state.open.items()):
            if pe.check_sell(pe.check_halt_file(self.limits)):
                return
            if mint in self.state.pending:
                continue
            if self._batch_stale(mint, batched):
                continue
            now = self.now_ms()
            if pos.get("abandoned"):
                continue
            if pos.get("stuck"):
                wait = min(STUCK_RETRY_MS * 2 ** max(0, pos["sell_attempts"] - self.sell_retries), STUCK_RETRY_MAX_MS)
                if now - pos.get("last_sell_fail_ms", 0) < wait:
                    continue
            snap, _pool, _err, kind = self._exit_snapshot(mint, batched)
            if snap is None or snap.quote_priced is None:
                # Never sell blind (no min_out) and never price on the vault alone: keep retrying.
                if now - pos.get("unpriced_alert_ms", 0) >= 60_000:
                    pos["unpriced_alert_ms"] = now
                    self._alert("unpriced_position", mint, held_ms=now - pos["t_entry_ms"])
                continue
            if "first_exit_snap_slot" not in pos:  # post-landing drift is measured, it does not set the mark
                pos["first_exit_snap_slot"], pos["first_exit_snap_ms"] = snap.slot, now
                self.save()
            reason = pos.get("exit_reason") or pe.exit_check(pos, snap, now, own_trade_in_state=True)["reason"]
            if reason:
                with self._exit_prio():
                    self._start_sell(mint, pos, snap, reason, now, kind)

    @pe.critical
    def _finish_sell(self, mint: str, p: dict[str, Any], m: dict[str, Any]) -> None:
        del self.state.pending[mint]
        pos = self.state.open[mint]
        base_fee, prio_fee = self._fee_split(m["fee"])
        row: dict[str, Any] = dict(
            pool=pos["pool"], exit_reason=p["reason"], ret=p["ret"], tokens=p["tokens"], quote_sol_out_lamports=p["q_out"],
            min_sol_out_lamports=p["min_out"], landed_slot=m["slot"], fee_lamports=m["fee"], base_fee_lamports=base_fee,
            priority_fee_lamports=prio_fee, priority_lamports=self.limits.priority_lamports, hold_ms=self.now_ms() - pos["t_entry_ms"], **self._timing(p),
            **{k: p[k] for k in ("exit_poll_ms", "exit_commitment", "exit_snapshot", "balance_source") if k in p},
            **{k: pos[k] for k in ("mark_send", "mark", "mark_source", "mark_shift_bps") if k in pos},
            buy_landed_slot=pos.get("buy_slot"), first_exit_snap_slot=pos.get("first_exit_snap_slot"), first_exit_snap_ms=pos.get("first_exit_snap_ms"),
            first_exit_snap_dslot=(pos["first_exit_snap_slot"] - pos["buy_slot"]) if isinstance(pos.get("first_exit_snap_slot"), int) and isinstance(pos.get("buy_slot"), int) else None,
            first_exit_snap_dms=(pos["first_exit_snap_ms"] - pos["t_entry_ms"]) if "first_exit_snap_ms" in pos else None,
        )
        if m["err"] is not None:
            pos["extra_cost"] += -m["sol_delta"]
            self.state.realized_lamports += m["sol_delta"]  # a landed failed sell's fees are realized loss NOW
            self._sell_failed(pos)
            self._log("sell", mint, landed=False, fail_class=classify_failure(m["err"], m["logs"], self.slip_codes), err=m["err"],
                      sell_attempt=pos["sell_attempts"], stuck=bool(pos.get("stuck")), cost_lamports=-m["sol_delta"], **row)
            self.save()
            return
        rent_refund = m["ata_rent_pre"]
        proceeds = m["sol_delta"] + m["fee"] - rent_refund  # pool leg only, no fee, no rent
        pnl = m["sol_delta"] - pos["buy_cost_lamports"] - pos.get("extra_cost", 0)  # SOL after sell - SOL before buy
        self._log(
            "sell", mint, landed=True, sol_received_lamports=proceeds, rent_refunded_lamports=rent_refund,
            exit_vs_quote_bps=bps(proceeds, p["q_out"]), pnl_lamports=pnl, buy_cost_lamports=pos["buy_cost_lamports"],
            sell_net_lamports=m["sol_delta"], sell_attempts=pos["sell_attempts"] + 1, failed_attempt_cost_lamports=pos.get("extra_cost", 0),
            ret_exit_vs_entry_actual=bps(proceeds, pos["spend"]), **row,
        )
        self.state.realized_lamports += pnl + pos.get("extra_cost", 0)  # failed-sell fees were booked as they landed
        del self.state.open[mint]
        self.save()

    # -- loop
    def prewarm(self) -> None:
        """Slow loop: refresh the static accounts and keep the blockhash cache warm (the cache's own 10 s TTL
        decides whether a call is made), so the buy path signs with a cached hash."""
        super().prewarm()
        try:
            self.bh.get()
        except (Exception, SystemExit):
            pass

    def _clock_note(self, now: int) -> None:
        if now + CLOCK_BACK_TOLERANCE_MS >= self.state.max_seen_ms:  # a backwards step is never absorbed
            self.state.max_seen_ms = max(self.state.max_seen_ms, now)
        if now - self._clock_saved >= self.poll_ms:  # persisted at the slow cadence (every buy/sell save carries it too)
            self._clock_saved = now
            self.save()

    def housekeeping(self, now: int) -> None:
        """Confirm loop and rebroadcasts: their own ~1 s timer (the old loop period), after the signal check."""
        if self.state.pending and now - self._last_adv >= PENDING_POLL_MS:
            self._last_adv = now
            self.advance_pending()

    def step(self) -> int:
        self.advance_pending()
        n = 0
        now = self.now_ms()
        self._clock_note(now)
        self.save()
        if now - self._last_tail >= self.poll_ms:
            self._last_tail = now
            n = super().step()
        self.advance_pending()
        return n


def run_live(cfg: dict[str, Any], args: Any, poll_s: float) -> int:
    harden_process()
    if "key_path" in cfg:
        raise SystemExit("live mode has no key path override: the key comes from the systemd credential only")
    # Fail closed BEFORE the wallet key loads: the RPC key must come from the root-only EnvironmentFile via the
    # environment. No env-file fallback in live mode (the fallback files are not root-only).
    if not (os.environ.get("HELIUS_API_KEY") or "").strip():
        print("probe_executor ALERT startup_refused rpc_key_missing", flush=True)
        return 2
    kp = load_probe_key()
    rps = float(cfg.get("rps", pe.MAX_RPS))
    rpc = pe.LimitedRpc(pe.ProbeRpc(sim.load_rpc_url(None, args.env_file, use_env_file=False)), rps=rps, max_rps=LIVE_MAX_RPS)
    ex = LiveExecutor(rpc, cfg, kp)
    print(f"probe_executor mode=LIVE user={ex.user} book={ex.book} limits={ex.limits}", flush=True)
    if args.once:
        ex.step()
        return 0
    ex.run_loop()
    return 0


if __name__ == "__main__":
    sys.exit(pe.main())
