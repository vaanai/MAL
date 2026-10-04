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

KEY_PATH = "/var/lib/mal-live/probe-wallet.json"
MIN_BALANCE_BUFFER = 20_000_000  # refuse to buy below size + 0.02 SOL (config may raise, never lower)
REBROADCAST_MS = 2_000
BLOCKHASH_TTL_MS = 10_000
BLOCKHASH_STALE_OK_MS = 40_000  # reuse a cached hash this long if the refresh call fails
EXPIRY_RECHECK_MS = 5_000  # after the block height passes, look once more before calling it expired
STUCK_RETRY_MS = 30_000
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


def check_parent_chain(path: str, stat_fn: Callable[[str], os.stat_result] = os.stat) -> None:
    """Every directory above the key's leaf directory must be owned by root and not group/world-writable;
    the leaf directory must be owned by root or the running uid and not group/world-writable. Symlinks in
    the path are resolved first. A writable or foreign-owned ancestor would let someone swap the key."""
    real = os.path.realpath(path)
    parents = []
    cur = os.path.dirname(real)
    while True:
        parents.append(cur)
        if cur == os.path.dirname(cur):
            break
        cur = os.path.dirname(cur)
    for i, d in enumerate(parents):  # parents[0] is the leaf directory
        st = stat_fn(d)
        allowed = (0, os.geteuid()) if i == 0 else (0,)
        if st.st_uid not in allowed:
            raise SystemExit("probe key parent directory has the wrong owner")
        if st.st_mode & 0o022:
            raise SystemExit("probe key parent directory is group or world writable")


def load_probe_key(path: str = KEY_PATH, *, verify_chain: bool = True,
                   stat_fn: Callable[[str], os.stat_result] = os.stat) -> Keypair:
    """Solana CLI keypair file (JSON array of 64 ints). Refuses unless the file is a regular file,
    mode exactly 0400 or 0600, owned by the running uid, and (verify_chain) its parent chain is
    root-owned and not group/world-writable apart from the leaf dir. Error text never contains file content."""
    if verify_chain:
        check_parent_chain(path, stat_fn)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        raise SystemExit("probe key file cannot be opened") from None
    buf = bytearray(1024)
    secret = bytearray()
    try:
        with os.fdopen(fd, "rb", buffering=0) as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode):
                raise SystemExit("probe key file is not a regular file")
            if stat.S_IMODE(st.st_mode) not in (0o400, 0o600):
                raise SystemExit("probe key file mode must be 0400 or 0600")
            if st.st_uid != os.geteuid():
                raise SystemExit("probe key file is not owned by the running uid")
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
            raise SystemExit("probe key file is malformed") from None
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


def parse_meta(res: dict, user: Pubkey, base_mint: Pubkey, base_ata: Pubkey) -> dict[str, Any]:
    """Everything the fill row needs from one getTransaction result (encoding json)."""
    meta = res["meta"]
    keys = _keys(res)
    ui = keys.index(str(user))
    pre, post = int(meta["preBalances"][ui]), int(meta["postBalances"][ui])

    def tok(rows: list | None) -> int:
        tot = 0
        for r in rows or []:
            if r.get("mint") == str(base_mint) and r.get("owner") == str(user):
                tot += int(r["uiTokenAmount"]["amount"])
        return tot

    ai = keys.index(str(base_ata)) if str(base_ata) in keys else None
    return {
        "slot": res.get("slot"),
        "err": meta.get("err"),
        "fee": int(meta["fee"]),
        "sol_delta": post - pre,  # post - pre of the wallet: includes fees, rent, pool legs
        "token_delta": tok(meta.get("postTokenBalances")) - tok(meta.get("preTokenBalances")),
        "ata_rent_pre": int(meta["preBalances"][ai]) if ai is not None else 0,
        "ata_rent_post": int(meta["postBalances"][ai]) if ai is not None else 0,
        "logs": meta.get("logMessages") or [],
    }


def bps(actual: float, ref: float) -> float | None:
    return round((actual - ref) * 10_000 / ref, 2) if ref else None


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
        self.balance_buffer = max(MIN_BALANCE_BUFFER, int(cfg.get("min_balance_buffer_lamports", MIN_BALANCE_BUFFER)))
        self.slip_codes = frozenset(cfg.get("slippage_error_codes") or SLIPPAGE_ERROR_CODES)
        self.poll_ms = int(max(1.0, float(cfg.get("poll_s", 5.0))) * 1000)
        self.bh = BlockhashCache(rpc, self.commitment, self.now_ms)
        self._last_tail = 0
        self._height: int | None = None

    def __repr__(self) -> str:  # never reveal the Keypair
        return f"LiveExecutor(user={self.user})"

    # -- helpers
    def _alert(self, what: str, mint: str = "", **kw: Any) -> None:
        self._log("alert", mint, alert=what, **kw)
        print(f"probe_executor ALERT {what} mint={mint} {json.dumps(kw, default=str)[:300]}", flush=True)

    def _sign(self, msg: Message) -> tuple[str, str]:
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
        return any(pos.get("stuck") for pos in self.state.open.values())

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
        if not why and self.sell_stuck():
            why = "sell_stuck"
        buys_pending = sum(1 for p in self.state.pending.values() if p["kind"] == "buy")
        if not why and len(self.state.open) + buys_pending >= self.limits.max_open:
            why = "max_open"
        if why:
            return self._skip(sig, f"limit:{why}")
        if now - sig["decision_t_ms"] > self.max_signal_age_ms:
            return self._skip(sig, "stale_signal")
        if mint in self.state.open or mint in self.state.pending:
            return self._skip(sig, "already_open")
        snap, pool, err = self._snapshot(mint)
        if snap is None:
            return self._skip(sig, err or "no_pool", pool=pool)
        if snap.quote_priced is None:
            return self._skip(sig, "no_v", pool=pool, pool_slot=snap.slot)
        spend = self.limits.size_lamports
        q = pe.entry_quote(snap, spend)
        if q["tokens"] <= 0:
            return self._skip(sig, "zero_quote", pool=pool, pool_slot=snap.slot)
        bal = self._balance()
        if bal is None:
            return self._skip(sig, "balance_unreadable", pool=pool)
        if bal < spend + self.balance_buffer:
            return self._skip(sig, "balance_guard", pool=pool, balance_lamports=bal)
        try:
            bhash, lvbh = self.bh.get()
            msg = pe.buy_probe_message(snap, self.user, spend, self.slip_bps, q["tokens"], self.limits.priority_lamports, bhash)
            signature, tx_b64 = self._sign(msg)
        except (Exception, SystemExit) as exc:
            return self._skip(sig, f"build_error:{pe.error_label(exc)}", pool=pool)
        # Write-ahead: the attempt, the signature and the signed tx are durable BEFORE the first send.
        self.state.attempts += 1
        if self.state.first_attempt_ms is None:
            self.state.first_attempt_ms = now
        self.state.pending[mint] = {
            "kind": "buy", "signature": signature, "tx_b64": tx_b64, "lvbh": lvbh, "pool": pool, "snap_slot": snap.slot,
            "decision_t_ms": sig["decision_t_ms"], "receive_ms": now, "score": sig.get("score"), "spend": spend,
            "q_tokens": q["tokens"], "q_net_in": q["net_in"], "q_mark": q["mark"], "fee_ppm": q["fee_ppm"],
            "v_lamports": snap.v, "base_ata": str(tx.ata(self.user, snap.ps.base_mint, snap.ps.base_token_program)),
            "base_mint": str(snap.ps.base_mint), "base_tp": str(snap.ps.base_token_program), "sends": 0,
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
                seen = p.setdefault("expired_seen_ms", now)
                if now - seen >= EXPIRY_RECHECK_MS:
                    self._resolve_expired(mint, p)
                continue
            if not halt and now - p.get("last_send_ms", 0) >= REBROADCAST_MS:
                self._send(p, mint)  # the SAME signed tx
        self.save()

    def _timing(self, p: dict[str, Any]) -> dict[str, Any]:
        t_send, t_conf = p.get("first_send_ms"), p.get("confirm_seen_ms") or self.now_ms()
        return dict(
            decision_t_ms=p.get("decision_t_ms"), receive_ms=p.get("receive_ms"), first_send_ms=t_send, confirm_seen_ms=t_conf,
            ms_decision_to_send=(t_send - p["decision_t_ms"]) if t_send and p.get("decision_t_ms") else None,
            ms_send_to_confirm=(t_conf - t_send) if t_send else None, sends=p.get("sends"), signature=p["signature"],
            snapshot_slot=p.get("snap_slot"),
        )

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

    def _resolve_landed(self, mint: str, p: dict[str, Any], st: dict[str, Any]) -> None:
        try:
            res = self.rpc("getTransaction", [p["signature"], {"encoding": "json", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}])
        except (Exception, SystemExit):
            res = None
        now = self.now_ms()
        base_mint, base_ata = Pubkey.from_string(p.get("base_mint") or self.state.open[mint]["base_mint"]), Pubkey.from_string(
            p.get("base_ata") or self.state.open[mint]["base_ata"])
        if not res or not res.get("meta"):
            since = p.setdefault("meta_wait_since", now)
            if now - since >= META_FALLBACK_MS and p["kind"] == "buy" and st.get("err") is None and not p.get("meta_alerted"):
                p["meta_alerted"] = True
                self._alert("meta_missing", mint, signature=p["signature"])
            return
        m = parse_meta(res, self.user, base_mint, base_ata)
        if p["kind"] == "buy":
            self._finish_buy(mint, p, m)
        else:
            self._finish_sell(mint, p, m)

    def _fee_split(self, fee: int) -> tuple[int, int]:
        base = tx.BASE_FEE_PER_SIGNATURE
        return min(base, fee), max(0, fee - base)

    def _finish_buy(self, mint: str, p: dict[str, Any], m: dict[str, Any]) -> None:
        del self.state.pending[mint]
        base_fee, prio_fee = self._fee_split(m["fee"])
        row: dict[str, Any] = dict(
            spend_lamports=p["spend"], expected_tokens=p["q_tokens"], sim_tokens=p.get("sim_tokens"), pool=p["pool"],
            landed_slot=m["slot"], slots_between=(m["slot"] - p["snap_slot"]) if m["slot"] and p.get("snap_slot") else None,
            fee_lamports=m["fee"], base_fee_lamports=base_fee, priority_fee_lamports=prio_fee, v_lamports=p.get("v_lamports"),
            **self._timing(p),
        )
        if m["err"] is not None:
            cls = classify_failure(m["err"], m["logs"], self.slip_codes)
            self.state.realized_lamports += m["sol_delta"]  # the fee actually paid
            self._log("buy", mint, landed=False, fail_class=cls, err=m["err"], cost_lamports=-m["sol_delta"], **row)
            self.save()
            return
        cost = -m["sol_delta"]
        tokens = m["token_delta"]
        row.update(
            landed=True, tokens_received=tokens, sol_spent_lamports=cost, rent_charged_lamports=m["ata_rent_post"],
            pool_fee_est_lamports=p["fee_ppm"] * p["spend"] // 1_000_000,
            entry_vs_quote_bps=bps(tokens, p["q_tokens"]), entry_vs_sim_bps=bps(tokens, p["sim_tokens"]) if p.get("sim_tokens") else None,
            price_vs_quote_bps=bps(p["q_tokens"], tokens) if tokens else None,  # cost per token vs quoted, + = we paid more
        )
        self._log("buy", mint, **row)
        if tokens <= 0:
            self._alert("buy_landed_zero_tokens", mint, signature=p["signature"])
        else:
            self.state.open[mint] = {
                "mint": mint, "pool": p["pool"], "t_entry_ms": self.now_ms(), "tokens": tokens, "net_in": p["q_net_in"],
                "mark": p["q_mark"], "spend": p["spend"], "buy_cost_lamports": cost, "buy_sig": p["signature"],
                "base_ata": p["base_ata"], "base_mint": p["base_mint"], "base_tp": p["base_tp"], "sell_attempts": 0,
                "extra_cost": 0, "stuck": False, "exit_reason": None, "q_tokens": p["q_tokens"], "buy_slot": m["slot"],
            }
        self.save()

    # -- exit
    def _sell_failed(self, pos: dict[str, Any]) -> None:
        pos["sell_attempts"] = pos.get("sell_attempts", 0) + 1
        pos["last_sell_fail_ms"] = self.now_ms()
        if pos["sell_attempts"] >= self.sell_retries and not pos.get("stuck"):
            pos["stuck"] = True
            self._alert("sell_stuck", pos["mint"], attempts=pos["sell_attempts"])

    def _start_sell(self, mint: str, pos: dict[str, Any], snap: pe.Snapshot, reason: str, now: int) -> None:
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
        chk = pe.exit_check({**pos, "tokens": bal}, snap, now)
        if not chk["quote_out"] or chk["quote_out"] <= 0:
            return
        min_out = tx.min_out_with_slippage(chk["quote_out"], self.slip_bps)
        try:
            # A retry needs a FRESH blockhash: the same message + hash signs to the same signature, and a failed
            # tx's signature is already in the status cache.
            bhash, lvbh = self.bh.get(fresh=pos.get("sell_attempts", 0) > 0)
            ixs = [*tx.sell_instructions(snap.ps, self.user, bal, min_out, priority_total_lamports=self.limits.priority_lamports),
                   close_token_account(Pubkey.from_string(pos["base_ata"]), self.user, Pubkey.from_string(pos["base_tp"]))]
            signature, tx_b64 = self._sign(Message.new_with_blockhash(ixs, self.user, bhash))
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
            "ret": chk["ret"], "sends": 0,
        }
        self.save()  # write-ahead
        self._send(self.state.pending[mint], mint)
        self.save()

    def poll_positions(self) -> None:
        for mint, pos in list(self.state.open.items()):
            if pe.check_sell(pe.check_halt_file(self.limits)):
                return
            if mint in self.state.pending:
                continue
            now = self.now_ms()
            if pos.get("stuck") and now - pos.get("last_sell_fail_ms", 0) < STUCK_RETRY_MS:
                continue
            snap, _pool, _err = self._snapshot(mint)
            if snap is None or snap.quote_priced is None:
                # Never sell blind (no min_out) and never price on the vault alone: keep retrying.
                if now - pos.get("unpriced_alert_ms", 0) >= 60_000:
                    pos["unpriced_alert_ms"] = now
                    self._alert("unpriced_position", mint, held_ms=now - pos["t_entry_ms"])
                continue
            reason = pos.get("exit_reason") or pe.exit_check(pos, snap, now)["reason"]
            if reason:
                self._start_sell(mint, pos, snap, reason, now)

    def _finish_sell(self, mint: str, p: dict[str, Any], m: dict[str, Any]) -> None:
        del self.state.pending[mint]
        pos = self.state.open[mint]
        base_fee, prio_fee = self._fee_split(m["fee"])
        row: dict[str, Any] = dict(
            pool=pos["pool"], exit_reason=p["reason"], ret=p["ret"], tokens=p["tokens"], quote_sol_out_lamports=p["q_out"],
            min_sol_out_lamports=p["min_out"], landed_slot=m["slot"], fee_lamports=m["fee"], base_fee_lamports=base_fee,
            priority_fee_lamports=prio_fee, hold_ms=self.now_ms() - pos["t_entry_ms"], **self._timing(p),
        )
        if m["err"] is not None:
            pos["extra_cost"] += -m["sol_delta"]
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
        self.state.realized_lamports += pnl
        del self.state.open[mint]
        self.save()

    # -- loop
    def step(self) -> int:
        self.advance_pending()
        n = 0
        now = self.now_ms()
        if now - self._last_tail >= self.poll_ms:
            self._last_tail = now
            n = super().step()
        self.advance_pending()
        return n


def run_live(cfg: dict[str, Any], args: Any, poll_s: float) -> int:
    harden_process()
    kp = load_probe_key(cfg.get("key_path", KEY_PATH))
    rps = float(cfg.get("rps", pe.MAX_RPS))
    rpc = pe.LimitedRpc(pe.ProbeRpc(sim.load_rpc_url(None, args.env_file)), rps=rps, max_rps=LIVE_MAX_RPS)
    ex = LiveExecutor(rpc, cfg, kp)
    print(f"probe_executor mode=LIVE user={ex.user} book={ex.book} limits={ex.limits}", flush=True)
    while True:
        ex.step()
        if args.once:
            return 0
        time.sleep(1.0)


if __name__ == "__main__":
    sys.exit(pe.main())
