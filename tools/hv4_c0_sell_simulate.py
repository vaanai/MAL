"""HV4 C0 sell + base-ATA close, built through the hv4_c0 bridge's PINNED builder and run through simulateTransaction (DEC-032 s2 checklist / s4,
FILL:simulate_ref for the hv4_c0 book).

tools/arena_sell_simulate.py drives the H5 builder (H5Executor._arm_sell). The hv4_c0 profile of tools/arena_live_bridge.py does not sell through that: it
sells through `BridgeExecutor` (the class nested in make_live_port), whose `_start_sell` sets the min_out rung from `hv4_live_c0.sell_slip_bps` and then runs the
UNMODIFIED `probe_live.LiveExecutor._start_sell` (tx.sell_instructions + `probe_live.close_token_account` under the mint's own token program), signed through
the bridge's `_sign` (PR3: a decoded min_out of 0 is never signed) and `probe_live.validate_message`. This tool builds the sell by that same path: it calls
`arena_live_bridge.make_live_port(profile hv4_c0)` and then `port.sell(...)`, the call the Bridge itself makes, once per rung:

  first       sell_attempts 0   min_out = 0.85 of the quote   priority 55,000   (balance from the buy meta: the fast path)
  escalated   sell_attempts 2   min_out = 0.65 of the quote   priority 150,000  (balance read through getTokenAccountBalance)

Before anything else it asserts `hv4_live_c0.sell_pin_problem() is None` and refuses otherwise. Nothing is sent: the RPC handed to the executor is an allowlist
gate (`SimRpc`); `sendTransaction` is answered locally with the signature of the captured transaction and never forwarded. Public RPC only (default
https://api.mainnet-beta.solana.com), 1 request/s, simulateTransaction with sigVerify=false and replaceRecentBlockhash=true. No key: the signer is a
throwaway in-memory Keypair (never written, never printed), or a NullSigner for a public address (unsigned transaction).

Modes (the error classes are those of tools/arena_sell_simulate.classify):
  default   owner = throwaway Keypair (holds nothing, so the chain stops at the fee payer: expected_unfunded_owner) and owner = a funded PUBLIC address,
            unsigned (the chain reaches the swap and stops at the missing token account: expected_no_tokens). Shape and decode only.
  --holder  owner = a PUBLIC ordinary holder of the mint, unsigned: the full-balance sell must run end to end with both closes, and the balance-1 control must
            revert at the base-ATA close (so the close is really exercised). Each rung is run both ways.

Per rung the record holds the decoded message (program ids, roles, that the base ATA is closed under the mint's own token program in the same transaction,
decoded min_out, compute-budget priority) and the checks. The record is written with sorted keys; its sha256 (printed, and stored next to it as <out>.sha256)
is what FILL:simulate_ref cites. A clean simulate is NOT a landing, a price or a P&L claim; no book has cleared the promotion gate.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from solders.hash import Hash
from solders.keypair import Keypair
from solders.null_signer import NullSigner
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from tools import arena_live_bridge as alb
from tools import arena_sell_simulate as ss
from tools import hv4_live_c0 as c0
from tools import probe_executor as pe
from tools import pumpswap_simulate as sim
from tools import pumpswap_tx as tx

SIM_BOT_ID = "hv4-SELLSIM-X"  # passes the arm rule (F or X); a path component of nothing real: the state dir is a tmp dir
SIM_END_MS = 86_400_000
SIM_MAX_HOLD_MS = 3_600_000
SIM_STAKE = c0.C0_LIMITS["stake_lamports"]  # the C0 stake S; the default sell amount is what a buy of S yields at the snapshot
RUNGS = ("first", "escalated")
RUNG_SELL_ATTEMPTS = {"first": 0, "escalated": c0.ESCALATE_AFTER_REVERTS}
RUNG_PRIORITY_KEY = {"first": "sell", "escalated": "escalated"}
COMPUTE_BUDGET = "ComputeBudget111111111111111111111111111111"
READ_METHODS = frozenset({"getAccountInfo", "getMultipleAccounts", "getTokenAccountBalance", "getTokenLargestAccounts", "getSignaturesForAddress",
                          "getTransaction", "simulateTransaction"})
OK_CLASSES = ("expected_unfunded_owner", "expected_no_tokens")
SYSTEM = ss.SYSTEM_PROGRAM
KIND_DEFAULT, KIND_HOLDER = "hv4_c0_sell_simulate.v1", "hv4_c0_sell_simulate_holder.v1"
CODE_FILES = ("tools/hv4_c0_sell_simulate.py", "tools/hv4_live_c0.py", "tools/arena_live_bridge.py", "tools/probe_live.py", "tools/probe_executor.py",
              "tools/pumpswap_tx.py", "tools/pumpswap_simulate.py", "tools/paper_curve_math.py", "tools/arena_sell_simulate.py")


class Refused(SystemExit):
    """The tool refuses before any RPC call (pin mismatch, non-canonical pool, unreadable pool)."""


def pin_refusal() -> str | None:
    why = c0.sell_pin_problem()
    return f"sell_builder_pin:{why}" if why else None


def require_pin() -> None:
    why = pin_refusal()
    if why:
        raise Refused(why)


# ---------------------------------------------------------------- the RPC allowlist gate


class SimRefused(Exception):
    """A method outside the allowlist was asked for."""


class SimRpc:
    """Allowlist in front of the (rate limited) public RPC. Read methods and simulateTransaction are forwarded. sendTransaction is NEVER forwarded: it is
    answered with the captured transaction's own signature. getLatestBlockhash is answered locally (the simulate replaces the blockhash). getTokenAccountBalance
    for an account in `balances` is answered locally (the stub balance of the simulated owner). Anything else raises SimRefused and is listed in `refused`."""

    def __init__(self, inner: Callable[[str, list], Any]):
        self.inner = inner
        self.forwarded: list[str] = []
        self.local: dict[str, int] = {}
        self.captured_sends: list[dict[str, Any]] = []
        self.refused: list[str] = []
        self.balances: dict[str, int] = {}
        self.account_bytes: dict[str, bytes] = {}

    def __repr__(self) -> str:
        return "SimRpc(<redacted>)"

    def _local(self, name: str) -> None:
        self.local[name] = self.local.get(name, 0) + 1

    def __call__(self, method: str, params: list) -> Any:
        if method == "sendTransaction":
            raw = base64.b64decode(params[0])
            sig = str(VersionedTransaction.from_bytes(raw).signatures[0])
            self.captured_sends.append({"signature": sig, "tx_len_bytes": len(raw)})
            self._local(method)
            return sig
        if method == "getLatestBlockhash":
            self._local(method)
            return {"context": {"slot": 0}, "value": {"blockhash": str(Hash.default()), "lastValidBlockHeight": 0}}
        if method == "getTokenAccountBalance" and params and params[0] in self.balances:
            self._local(method)
            n = self.balances[params[0]]
            return {"context": {"slot": 0}, "value": {"amount": str(n), "decimals": 0, "uiAmount": None, "uiAmountString": str(n)}}
        if method in READ_METHODS:
            out = self.inner(method, params)
            self.forwarded.append(method)
            if method == "getAccountInfo" and isinstance(out, dict) and out.get("value"):
                try:
                    self.account_bytes[str(params[0])] = sim._b64(out["value"])
                except Exception:  # noqa: BLE001 - a non-base64 account is simply not kept
                    pass
            return out
        self.refused.append(method)
        raise SimRefused(f"rpc method not allowed in a simulate: {method}")


# ---------------------------------------------------------------- the real executor, built the way the bridge builds it


class SimCtx:
    def __init__(self, port: Any, gate: SimRpc, signer: Keypair | NullSigner):
        self.port, self.gate, self.signer = port, gate, signer
        self.owner: Pubkey = signer.pubkey()
        self.alerts: list[dict[str, Any]] = []
        self.sign_errors: list[str] = []
        ex = port.ex
        orig_alert, orig_sign = ex._alert, ex._sign

        def alert(what: str, mint: str = "", **kw: Any) -> None:
            self.alerts.append({"alert": what, "label": kw.get("label")})
            return orig_alert(what, mint, **kw)

        def sign(msg: Any, ps: Any, mint: str, priority: int | None = None) -> Any:  # observer only: the bridge's _sign runs unchanged, its exception is re-raised
            try:
                return orig_sign(msg, ps, mint, priority)
            except BaseException as exc:
                self.sign_errors.append(f"{type(exc).__name__}:{str(exc)[:80]}")
                raise

        ex._alert, ex._sign = alert, sign


@contextmanager
def sim_port(gate: SimRpc, signer: Keypair | NullSigner):
    """`arena_live_bridge.make_live_port` for profile hv4_c0, in a tmp dir. Host guards are redirected, not skipped: the config is a dry-mode one so the wallet-wide
    STOP/HALT files can point into the tmp dir, and the probe's LIVE_DIR (the cross-profile flat check read at construction) points at an empty tmp dir. Neither
    shapes the transaction. The port's mode is "live" regardless (LiveExecutorPort hard-codes it)."""
    tmp = Path(tempfile.mkdtemp(prefix="hv4-c0-sellsim-"))
    live_dir = tmp / "live-dir-empty"
    live_dir.mkdir()
    saved = pe.LIVE_DIR
    try:
        cfg = alb.Config.from_dict({"bot_id": SIM_BOT_ID, "frozen_sha": c0.FROZEN_SHA, "root": str(tmp), "state_dir": str(tmp / "state"),
                                    "end_ms": int(time.time() * 1000) + SIM_END_MS, "max_hold_ms": SIM_MAX_HOLD_MS, "mode": "dry", "profile": c0.PROFILE,
                                    "stop_file": str(tmp / "STOP"), "halt_file": str(tmp / "HALT")})
        pe.LIVE_DIR = live_dir
        try:
            port = alb.make_live_port(cfg, gate, signer)
        finally:
            pe.LIVE_DIR = saved
        yield SimCtx(port, gate, signer)
    finally:
        pe.LIVE_DIR = saved
        shutil.rmtree(tmp, ignore_errors=True)


def new_position(ctx: SimCtx, mint: str, snap: Any, tokens: int, entry: dict[str, Any], attempts: int, now: int) -> dict[str, Any]:
    base_tp = snap.ps.base_token_program
    pos = {"mint": mint, "pool": str(snap.ps.pool), "t_entry_ms": now - 60_000, "tokens": tokens, "net_in": entry["net_in"], "mark": entry["mark"],
           "mark_send": entry["mark"], "mark_source": "simulate_stub", "mark_shift_bps": 0, "spend": SIM_STAKE, "buy_cost_lamports": SIM_STAKE, "buy_sig": "simulate_stub",
           "base_ata": str(tx.ata(ctx.owner, Pubkey.from_string(mint), base_tp)), "base_mint": mint, "base_tp": str(base_tp), "sell_attempts": attempts,
           "ata_pre_amount": 0, "extra_cost": 0, "stuck": False, "abandoned": False, "exit_reason": None, "balance_pending": False, "q_tokens": tokens, "buy_slot": 0}
    if attempts:
        pos["c0_first_sell_ms"] = now - 5_000  # the first build of a real trip stamped this
    return pos


def build_rung(ctx: SimCtx, mint: str, snap: Any, rung: str, tokens: int, entry: dict[str, Any]) -> dict[str, Any]:
    """One sell through port.sell (the bridge's own call). Returns the pending record's facts, or built=False with the alerts and sign errors."""
    ex = ctx.port.ex
    now = ex.now_ms()
    pos = new_position(ctx, mint, snap, tokens, entry, RUNG_SELL_ATTEMPTS[rung], now)
    ata = pos["base_ata"]
    ex.state.pending.pop(mint, None)
    ex.state.open[mint] = pos
    ctx.gate.balances[ata] = tokens  # the escalated rung reads the balance through RPC: served locally, exactly the amount under test
    n_alerts, n_sign, n_send = len(ctx.alerts), len(ctx.sign_errors), len(ctx.gate.captured_sends)
    try:
        ok, _why = ctx.port.sell({"mint": mint}, "book_exit")
        pend = ex.state.pending.get(mint)
        out: dict[str, Any] = {"built": bool(ok and pend), "alerts": ctx.alerts[n_alerts:], "sign_errors": ctx.sign_errors[n_sign:],
                               "captured_sends": len(ctx.gate.captured_sends) - n_send}
        if pend:
            out.update({"tx_b64": pend["tx_b64"], "min_out": pend["min_out"], "q_out": pend["q_out"], "tokens": pend["tokens"],
                        "balance_source": pend["balance_source"], "signature": pend["signature"]})
        return out
    finally:
        ctx.gate.balances.pop(ata, None)
        ex.state.pending.pop(mint, None)
        ex.state.open.pop(mint, None)


# ---------------------------------------------------------------- decode and checks


def decode_sell(tx_b64: str, owner: Pubkey, ps: tx.PoolState, mint: str) -> dict[str, Any]:
    """Role, program and account count of each instruction of the built transaction (local, no RPC), plus the facts DEC-032 s4 asks for."""
    t = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
    msg = t.message
    keys = list(msg.account_keys)
    base_tp = ps.base_token_program
    u_base = tx.ata(owner, Pubkey.from_string(mint), base_tp)
    u_wsol = tx.ata(owner, tx.WSOL_MINT, tx.TOKEN_PROGRAM)
    ixs: list[dict[str, Any]] = []
    cu_limit = cu_price = sell_in = sell_min = None
    close_ok = False
    for i, ix in enumerate(msg.instructions):
        pid = keys[ix.program_id_index]
        acc = [keys[a] for a in ix.accounts]
        data = bytes(ix.data)
        role = "?"
        if str(pid) == COMPUTE_BUDGET and data[:1] == b"\x02" and len(data) == 5:
            role, cu_limit = "compute_unit_limit", int.from_bytes(data[1:], "little")
        elif str(pid) == COMPUTE_BUDGET and data[:1] == b"\x03" and len(data) == 9:
            role, cu_price = "compute_unit_price", int.from_bytes(data[1:], "little")
        elif pid == tx.ATA_PROGRAM and data == b"\x01" and len(acc) == 6 and acc[1] == u_wsol:
            role = "create_wsol_ata"
        elif pid == tx.PUMPSWAP_PROGRAM and data[:8] == bytes(tx.DISC_SELL) and len(data) >= 24:
            role, sell_in, sell_min = "pumpswap_sell", int.from_bytes(data[8:16], "little"), int.from_bytes(data[16:24], "little")
        elif data == b"\x09" and len(acc) == 3 and acc[0] == u_wsol and pid == tx.TOKEN_PROGRAM:
            role = "close_wsol"
        elif data == b"\x09" and len(acc) == 3 and acc[0] == u_base and pid == base_tp:
            role = "close_base_ata"
            close_ok = acc[1] == owner and acc[2] == owner
        ixs.append({"i": i, "role": role, "program": str(pid), "n_accounts": len(acc)})
    roles = [x["role"] for x in ixs]
    programs: list[str] = []
    for x in ixs:
        if x["program"] not in programs:
            programs.append(x["program"])
    allowed = {COMPUTE_BUDGET, str(tx.ATA_PROGRAM), str(tx.PUMPSWAP_PROGRAM), str(tx.TOKEN_PROGRAM), str(base_tp)}
    swap_i = roles.index("pumpswap_sell") if "pumpswap_sell" in roles else -1
    close_i = roles.index("close_base_ata") if "close_base_ata" in roles else -1
    return {
        "n_instructions": len(ixs), "instructions": ixs, "program_ids": programs, "roles": roles, "layout_is_pinned_order": roles == list(ss.IX_ROLES),
        "only_expected_programs": set(programs) <= allowed, "fee_payer_is_owner": keys[0] == owner, "n_required_signatures": msg.header.num_required_signatures,
        "first_signature_is_zero": t.signatures[0] == Signature.default(), "tx_len_bytes": len(base64.b64decode(tx_b64)),
        "base_ata": str(u_base), "base_token_program": str(base_tp), "token_program_name": ss.TOKEN_NAMES.get(str(base_tp), str(base_tp)),
        "base_ata_closed_in_tx": close_ok and roles.count("close_base_ata") == 1 and close_i == len(ixs) - 1,
        "base_close_under_mint_token_program": close_i >= 0 and ixs[close_i]["program"] == str(base_tp),
        "base_close_after_swap": swap_i >= 0 and close_i > swap_i,
        "wsol_closed_in_tx": roles.count("close_wsol") == 1,
        "compute_unit_limit": cu_limit, "compute_unit_price_micro_lamports": cu_price,
        "priority_lamports": tx.priority_fee_lamports(cu_price, cu_limit) if cu_price is not None and cu_limit else None,
        "sell_amount_in": sell_in, "sell_min_out": sell_min,
    }


def rung_checks(shape: dict[str, Any], built: dict[str, Any], rung: str, tokens: int) -> dict[str, bool]:
    keep = c0.SELL_MIN_OUT_KEEP_BPS["escalated" if rung == "escalated" else "first"]
    mo = shape["sell_min_out"]
    return {
        "min_out_positive": isinstance(mo, int) and mo > 0,
        "min_out_equals_pending": mo == built["min_out"],
        "min_out_is_rung_share_of_quote": mo == built["q_out"] * keep // 10_000,
        "priority_equals_pin": shape["priority_lamports"] == c0.PRIORITY_PIN[RUNG_PRIORITY_KEY[rung]],
        "sell_amount_is_tokens": shape["sell_amount_in"] == tokens == built["tokens"],
        "layout_is_pinned_order": shape["layout_is_pinned_order"],
        "base_ata_closed_in_tx_under_mint_token_program": shape["base_ata_closed_in_tx"] and shape["base_close_under_mint_token_program"] and shape["base_close_after_swap"],
        "only_expected_programs": shape["only_expected_programs"],
        "fee_payer_is_owner_single_signer": shape["fee_payer_is_owner"] and shape["n_required_signatures"] == 1,
        "built_balance_source_as_rung": built["balance_source"] == ("buy_meta" if rung == "first" else "rpc"),
    }


def rung_record(ctx: SimCtx, mint: str, snap: Any, rung: str, tokens: int, entry: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """(record, tx_b64 or None). The record carries the expected rung values next to what was decoded."""
    built = build_rung(ctx, mint, snap, rung, tokens, entry)
    rec: dict[str, Any] = {"rung": rung, "sell_attempts": RUNG_SELL_ATTEMPTS[rung], "expected_priority_lamports": c0.PRIORITY_PIN[RUNG_PRIORITY_KEY[rung]],
                           "expected_min_out_keep_bps": c0.SELL_MIN_OUT_KEEP_BPS["escalated" if rung == "escalated" else "first"], "built": built["built"],
                           "alerts": built["alerts"], "sign_errors": built["sign_errors"]}
    if not built["built"]:
        rec["checks"], rec["rung_ok"] = {}, False
        return rec, None
    shape = decode_sell(built["tx_b64"], ctx.owner, snap.ps, mint)
    checks = rung_checks(shape, built, rung, tokens)
    rec.update({"shape": shape, "min_out": built["min_out"], "q_out": built["q_out"], "tokens": built["tokens"], "balance_source": built["balance_source"],
                "checks": checks, "rung_ok": all(checks.values())})
    return rec, built["tx_b64"]


# ---------------------------------------------------------------- default mode


def prime(ctx: SimCtx, mint: str) -> tuple[Any, dict[str, Any], bool | None]:
    """The executor's own full snapshot (getAccountInfo pool + getMultipleAccounts), which also fills its pool cache for the batched exit read."""
    snap, _pool, err = ctx.port.ex._snapshot(mint)
    if snap is None:
        raise Refused(f"snapshot_refused:{err}")
    if snap.quote_priced is None:
        raise Refused("pool_v_unreadable")
    entry = pe.entry_quote(snap, SIM_STAKE)
    if entry["tokens"] <= 0:
        raise Refused("zero_entry_quote")
    raw = ctx.gate.account_bytes.get(str(snap.ps.pool))
    return snap, entry, (tx.pool_is_mayhem(raw) if raw else None)


def variant_run(gate: SimRpc, signer: Keypair | NullSigner, name: str, mint: str, amount: int) -> dict[str, Any]:
    with sim_port(gate, signer) as ctx:
        snap, entry, mayhem = prime(ctx, mint)
        tokens = amount or entry["tokens"]
        rungs: dict[str, Any] = {}
        for rung in RUNGS:
            rec, b64 = rung_record(ctx, mint, snap, rung, tokens, entry)
            if b64:
                res = ss.simulate(gate, b64)
                rec["simulate"], rec["verdict"] = res, ss.classify(res)
            rungs[rung] = rec
        return {"variant": name, "owner_pubkey": str(ctx.owner), "signed": isinstance(signer, Keypair), "pool_is_mayhem": mayhem, "sell_amount_raw": tokens,
                "snapshot": {"slot": snap.slot, "v_lamports": snap.v, "v_in_c0_universe_band": c0.universe_problem(snap.ps.quote_mint, snap.v) is None},
                "token_program": ss.TOKEN_NAMES.get(str(snap.ps.base_token_program), str(snap.ps.base_token_program)), "pool": str(snap.ps.pool), "rungs": rungs}


def default_problems(variants: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for v in variants:
        for rung, r in v["rungs"].items():
            tag = f"{v['variant']}/{rung}"
            if not r["built"]:
                out.append(f"{tag}:not_built:{r['sign_errors'] or r['alerts']}")
                continue
            out += [f"{tag}:check_failed:{k}" for k, ok in r["checks"].items() if not ok]
            cls = r["verdict"]["class"]
            if cls not in OK_CLASSES:
                out.append(f"{tag}:class:{cls}")
    return out


def common_header(kind: str, mint: str, now: Callable[[], datetime] | None, git_sha: str | None, rpc_host: str) -> dict[str, Any]:
    stamp = (now or (lambda: datetime.now(timezone.utc)))().strftime("%Y-%m-%dT%H:%M:%SZ")
    root = Path(__file__).resolve().parent.parent
    files = {}
    for rel in CODE_FILES:
        p = root / rel
        files[rel] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
    pin = c0.SELL_BUILDER_PIN
    return {"kind": kind, "utc": stamp, "code_git_sha": git_sha, "code_files_sha256": files, "profile": c0.PROFILE, "bot_id_stub": SIM_BOT_ID,
            "builder": {"path": "tools/arena_live_bridge.py make_live_port -> BridgeExecutor.poll_positions -> BridgeExecutor._start_sell -> probe_live.LiveExecutor._start_sell",
                        "pin_problem": pin_refusal(), "pin_sha256": hashlib.sha256(json.dumps(pin, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                        "pin_items": {k: pin[k] for k in ("symbol_sha256", "close_token_account_sha256", "ptx.sell_instructions_sha256", "c0.ladder_sha256",
                                                          "bridge.poll_positions_sha256")}},
            "priority_pin": c0.PRIORITY_PIN, "min_out_keep_bps": c0.SELL_MIN_OUT_KEEP_BPS, "escalate_after_reverts": c0.ESCALATE_AFTER_REVERTS,
            "host_guards": "stop/halt files and the probe LIVE_DIR cross-check point into an empty tmp dir (they do not shape the transaction)",
            "mint": mint, "rpc_host": rpc_host}


def check_pool(mint: str, pool: str | None) -> str:
    canon = str(tx.canonical_pool(Pubkey.from_string(mint)))
    if pool and pool != canon:
        raise Refused(f"pool_not_canonical:{pool}")
    return canon


def run(rpc: Callable[[str, list], Any], mint: str, pool: str | None, amount: int = 0, now: Callable[[], datetime] | None = None, git_sha: str | None = None,
        rpc_host: str = "api.mainnet-beta.solana.com", funded_probe: bool = True) -> dict[str, Any]:
    require_pin()
    canon = check_pool(mint, pool)
    gate = SimRpc(rpc)
    variants = [variant_run(gate, Keypair(), "throwaway", mint, amount)]  # in memory only; only the public key is recorded
    if funded_probe:
        variants.append(variant_run(gate, NullSigner(ss.funded_payer(gate)), "funded_public_payer_unsigned", mint, amount))
    problems = default_problems(variants)
    if gate.refused:
        problems.append(f"rpc_refused:{gate.refused}")
    n_builds = sum(1 for v in variants for r in v["rungs"].values() if r["built"])
    if len(gate.captured_sends) != n_builds:
        problems.append(f"captured_sends:{len(gate.captured_sends)}!={n_builds}")
    rec = common_header(KIND_DEFAULT, mint, now, git_sha, rpc_host)
    rec.update({"pool": canon, "variants": variants, "verdict": {"pass": not problems, "problems": problems}, **gate_facts(gate),
                "not_measured": ["a funded sell (no owner here holds tokens)", "landing", "proceeds, price impact or any P&L", "the escalated rung's time bound (345 s)",
                                 "the stuck-position path (the same escalation predicate; covered by tools/test_hv4_live_c0_pr3.py)"]})
    return rec


def gate_facts(gate: SimRpc) -> dict[str, Any]:
    return {"rpc_calls_forwarded": list(gate.forwarded), "rpc_answered_locally": dict(gate.local), "rpc_refused": list(gate.refused),
            "sends_captured_not_forwarded": len(gate.captured_sends), "sent": 0}


# ---------------------------------------------------------------- holder mode


def holder_verdict(full: dict[str, Any], ctl: dict[str, Any]) -> dict[str, Any]:
    fs, cs = full["simulate"], ctl["simulate"]
    close_idx = len(full["rec"]["shape"]["instructions"]) - 1
    ctl_err = cs["err"]
    ctl_ix = ctl_err["InstructionError"][0] if isinstance(ctl_err, dict) and "InstructionError" in ctl_err else None
    c = full["checks"]
    return {"full_sell_ok": all((c["err_is_null"], c["pumpswap_success_log"], c["top_level_token_ixs_succeeded"] >= 2, c["base_ata_closed_in_post_state"],
                                 c["wsol_ata_closed_in_post_state"])),
            "control_reverts_at_base_close": ctl_err is not None and ctl_ix == close_idx and ctl["checks"]["non_native_close_error_log"],
            "control_err": ctl_err, "full_err": fs["err"]}


def holder_run(rpc: Callable[[str, list], Any], mint: str, pool: str | None, now: Callable[[], datetime] | None = None, git_sha: str | None = None,
               rpc_host: str = "api.mainnet-beta.solana.com") -> dict[str, Any]:
    require_pin()
    canon = check_pool(mint, pool)
    gate = SimRpc(rpc)
    with sim_port(gate, NullSigner(Pubkey.from_string(SYSTEM))) as pre:  # the bridge's own snapshot names the mint's token program for the holder search
        tp0 = prime(pre, mint)[0].ps.base_token_program
    holder = ss.find_holder(gate, mint, Pubkey.from_string(canon), tp0)
    owner = Pubkey.from_string(holder["owner"])
    rungs: dict[str, Any] = {}
    problems: list[str] = []
    mayhem = None
    with sim_port(gate, NullSigner(owner)) as ctx:  # unsigned: the zero signature, sigVerify false
        snap, entry, mayhem = prime(ctx, mint)
        base_ata = str(tx.ata(owner, Pubkey.from_string(mint), snap.ps.base_token_program))
        wsol_ata = str(tx.ata(owner, tx.WSOL_MINT, tx.TOKEN_PROGRAM))
        for rung in RUNGS:
            balance, bal_slot = ss.read_balance(gate, holder["token_account"])  # the real balance, read before the stub balance is registered
            runs: dict[str, Any] = {}
            for name, amount in (("full_balance", balance), ("control_balance_minus_1", balance - 1)):
                rec, b64 = rung_record(ctx, mint, snap, rung, amount, entry)
                run_: dict[str, Any] = {"rec": rec, "sell_amount_raw": amount}
                if b64:
                    res = ss.simulate_with_accounts(gate, b64, [base_ata, wsol_ata])
                    run_["simulate"], run_["checks"] = res, ss.holder_checks(res, base_ata, wsol_ata, str(snap.ps.base_token_program))
                    if name == "control_balance_minus_1":  # the RPC does not report post-state reliably on error: do not let a flag read as a measurement
                        for k in ("base_ata_closed_in_post_state", "wsol_ata_closed_in_post_state"):
                            run_["checks"][k] = "not_reported_on_error"
                else:
                    run_["simulate"] = run_["checks"] = None
                runs[name] = run_
            runs["full_balance"]["balance_read_before_simulate"] = {"amount": balance, "context_slot": bal_slot}
            tag = f"{rung}"
            if not all(r["simulate"] for r in runs.values()):
                problems.append(f"{tag}:not_built:{[r['rec']['sign_errors'] or r['rec']['alerts'] for r in runs.values()]}")
                verdict = {"full_sell_ok": False, "control_reverts_at_base_close": False}
            else:
                verdict = holder_verdict(runs["full_balance"], runs["control_balance_minus_1"])
            for name, r in runs.items():
                problems += [f"{tag}/{name}:check_failed:{k}" for k, ok in r["rec"]["checks"].items() if not ok]
            if not (verdict["full_sell_ok"] and verdict["control_reverts_at_base_close"]):
                problems.append(f"{tag}:holder_verdict:{verdict.get('full_err')},{verdict.get('control_err')}")
            rungs[rung] = {"runs": runs, "verdict": verdict}
        token_program = ss.TOKEN_NAMES.get(str(snap.ps.base_token_program), str(snap.ps.base_token_program))
        v_info = {"slot": snap.slot, "v_lamports": snap.v, "v_in_c0_universe_band": c0.universe_problem(snap.ps.quote_mint, snap.v) is None}
    if gate.refused:
        problems.append(f"rpc_refused:{gate.refused}")
    rec = common_header(KIND_HOLDER, mint, now, git_sha, rpc_host)
    rec.update({"pool": canon, "token_program": token_program, "pool_is_mayhem": mayhem, "holder": holder, "signed": False, "owner_pubkey": holder["owner"],
                "snapshot": v_info, "rungs": rungs, "verdict": {"pass": not problems, "problems": problems}, **gate_facts(gate),
                "not_measured": ["the post-state accounts of the failed control runs (the RPC does not report them reliably on error)", "landing",
                                 "proceeds, price impact or any P&L", "a signed or sent transaction (none exists)", "the escalated rung's time bound (345 s)"]})
    return rec


# ---------------------------------------------------------------- record + CLI


def write_record(rec: dict[str, Any], out: Path) -> str:
    return ss.write_record(rec, out)


def summary_line(rec: dict[str, Any], sha: str, out: Path, mode: str) -> dict[str, Any]:
    if rec["kind"] == KIND_HOLDER:
        rungs = {r: {"full_sell_ok": v["verdict"]["full_sell_ok"], "control_reverts_at_base_close": v["verdict"]["control_reverts_at_base_close"],
                     "priority_lamports": v["runs"]["full_balance"]["rec"].get("shape", {}).get("priority_lamports"),
                     "min_out_positive": v["runs"]["full_balance"]["rec"].get("checks", {}).get("min_out_positive")} for r, v in rec["rungs"].items()}
    else:
        rungs = {v["variant"]: {r: {"class": x.get("verdict", {}).get("class"), "priority_lamports": x.get("shape", {}).get("priority_lamports"),
                                    "min_out_positive": x.get("checks", {}).get("min_out_positive")} for r, x in v["rungs"].items()} for v in rec["variants"]}
    return {"mint": rec["mint"], "mode": mode, "token_program": rec.get("token_program") or rec["variants"][0]["token_program"],
            "verdict": "PASS" if rec["verdict"]["pass"] else "FAIL", "problems": rec["verdict"]["problems"], "rungs": rungs, "sha256": sha, "out": str(out)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    ap.add_argument("--mint", required=True)
    ap.add_argument("--pool", help="default: the canonical pump.fun pool of the mint; anything else is refused")
    ap.add_argument("--rpc", default=ss.DEFAULT_RPC, help="public RPC URL (no key in it)")
    ap.add_argument("--amount", type=int, default=0, help="raw base tokens to sell (default mode only); 0 = what a buy of the C0 stake yields at the snapshot")
    ap.add_argument("--no-funded-probe", action="store_true")
    ap.add_argument("--holder", action="store_true", help="holder mode: unsigned simulate with a public holder as owner, full balance + balance-1 control, both rungs")
    ap.add_argument("--git-sha", default=None, help="the 40-hex commit the code was exported from (recorded)")
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)
    if "?" in a.rpc or "api-key" in a.rpc.lower() or "@" in a.rpc:
        raise SystemExit("--rpc must be a plain public URL (no query string, no key)")
    if a.git_sha is not None and (len(a.git_sha) != 40 or any(ch not in "0123456789abcdef" for ch in a.git_sha)):
        raise SystemExit("--git-sha must be 40 lowercase hex")
    host = a.rpc.split("/")[2] if "//" in a.rpc else "unknown"
    try:
        rpc = ss.RateLimitedRpc(a.rpc)
        if a.holder:
            rec = holder_run(rpc, a.mint, a.pool, git_sha=a.git_sha, rpc_host=host)
        else:
            rec = run(rpc, a.mint, a.pool, a.amount, git_sha=a.git_sha, rpc_host=host, funded_probe=not a.no_funded_probe)
    except Refused as exc:
        print(json.dumps({"verdict": "REFUSED", "refusal": str(exc.code), "mint": a.mint}))
        return 2
    except SystemExit as exc:  # an RPC failure or "no ordinary holder": labels only, never a URL
        print(json.dumps({"verdict": "ERROR", "error": str(exc.code)[:300], "mint": a.mint}))
        return 3
    sha = write_record(rec, a.out)
    print(json.dumps(summary_line(rec, sha, a.out, "holder" if a.holder else "default")))
    return 0 if rec["verdict"]["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
