#!/usr/bin/env python3
"""Daily pump.fun structure monitor.

MAL's only remaining candidate depends on pump.fun protocol behaviour that has changed
silently before: the BOOST TWAP buy after each graduation, the fee config, the program
binaries, the synthetic migration, and slot time. This tool reads that structure from PUBLIC
mainnet RPC, appends ONE JSON line per run, and prints a short summary with HALT flags.

What it reads (about 150-250 RPC calls per run at the defaults, hard cap 400):
  - getRecentPerformanceSamples / getEpochInfo: ms per slot, epoch, the 200 ms feature gate.
  - Config accounts: pump bonding-curve FeeConfig, PumpSwap FeeConfig, PumpSwap GlobalConfig
    (sha256 vs tools/pump_structure_pins.json) and pump Global (sha256, informational).
  - Program deploy slots (programdata) for the pump, PumpSwap and fee programs vs the pins.
  - The N newest graduations (migrate txs on the migration fee account, settled for
    SETTLE_S_DEFAULT seconds so BOOST has finished): mayhem flag, quote-mint class,
    InitBoost, synthetic migration (PostCompleteBuyEvent), and for BOOST pools the keeper
    slice count / SOL / timing from the boost vault authority's signature list.
  - A fixed sample of recent PumpSwap txs, reading ONLY instruction names (v2 trade share).

What it never reads or writes: prices, reserves, returns, P&L, per-mint or per-wallet values,
non-BOOST trade amounts, the lab tape, forward-1002 files, any API key or .env (it refuses
Helius URLs; 0 Helius credits). Output carries distributions (min/median/max), shares and
counts only. No mint, pool, wallet or signature is written.

Exit code 0, except 2 when the RPC is unreachable before any data was read, and 1 when the
JSON line cannot be written (the summary is printed first, so a HALT is never lost). Halts
are data in the JSON line, not crashes. HALT and WARN flags have no side effects.

Decoding logic is adapted from the 2026-10-08 audit prototypes (all read-only public RPC):
  /data/mal/audit-1008/work/g_october_structure_check/s03_config_accounts.py   (FeeConfig /
      GlobalConfig / programdata decode)
  .../s02_decode_gates.py, s01_slot_ms.py                                      (gates, ms/slot)
  .../inv_b/s05_october_sample.py, s12_synthetic_migration.py, s15_initboost_xtab.py
      (migrate-tx sample rule, InitBoost, PostCompleteBuyEvent)
  .../s20_october_probe.py                                                     (emit_cpi event
      blobs, BOOST vault authority PDA, Pool account mayhem byte)
and from tools/pump_history_backfill.py (migration / complete event offsets).
Reports: /data/mal/audit-1008/reports/g_october_structure_check{,_invB}.md.

Run: /data/mal/venv/bin/python -m tools.pump_structure_monitor --out /data/mal/structure-monitor/daily.jsonl
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
import re
import statistics
import struct
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlparse

SCHEMA = "pump_structure_monitor.v1"
DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
DEFAULT_OUT = "/data/mal/structure-monitor/daily.jsonl"
DEFAULT_PINS = Path(__file__).with_name("pump_structure_pins.json")

# ---- addresses (all public chain constants; none is a key) ----------------------------------
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
FEE_PROGRAM = "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ"
PROGRAMS = {"pump": PUMP_PROGRAM, "pumpswap": PUMPSWAP_PROGRAM, "fees": FEE_PROGRAM}
PUMP_GLOBAL = "4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf"
BONDING_FEE_CONFIG = "8Wf5TiAheLUqBrKXeYg2JtAFFMWtKdG2BSFgqUcPVwTt"
PUMPSWAP_FEE_CONFIG = "5PHirr8joyTMp9JMm6nW7hNDVyEYdkzDqazxPD7RaTjx"
PUMPSWAP_GLOBAL_CONFIG = "ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw"
# Pinned (and halting) accounts. pump_global is read and hashed but is informational only.
PINNED_ACCOUNTS = {
    "bonding_fee_config": BONDING_FEE_CONFIG,
    "pumpswap_fee_config": PUMPSWAP_FEE_CONFIG,
    "pumpswap_global_config": PUMPSWAP_GLOBAL_CONFIG,
}
INFO_ACCOUNTS = {"pump_global": PUMP_GLOBAL}
# SIMD-0525 slot-time feature gates, keyed by target slot time (audit s02/inv_b s07).
GATES = {
    "350ms": "iBRL5RuWhw4yqaAZu96RUULHckHTZAoe2b77qaV38JZ",
    "300ms": "iBRLL3k18HST852F1Mf3Lv83waTNQmmqvKDxvYGwQFL",
    "250ms": "iBRLMc81UjRa8fn8A6eE8bJTnRbgQoPTynM51akENCV",
    "200ms": "iBRLjhJnkmDZgNoZRDMW11d8ZV7HvsL3vAyRjZB5npW",
}
MIGRATION_FEE_ACCOUNT = "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"
WSOL_MINT = "So11111111111111111111111111111111111111112"
NATIVE_SOL = "11111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
ATA_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
SLOTS_PER_EPOCH = 432_000

# ---- event discriminators and layouts -------------------------------------------------------
EVENT_CPI_TAG = bytes.fromhex("e445a52e51cb9a1d")  # anchor emit_cpi instruction prefix
DISC_MIGRATE = bytes.fromhex("bde95db95c94ea94")  # CompletePumpAmmMigrationEvent
DISC_COMPLETE = bytes.fromhex("5f72619cd42e9808")  # CompleteEvent
DISC_POST_COMPLETE_BUY = bytes.fromhex("6fb06d8b316cd5fb")  # PostCompleteBuyEvent (synthetic migration)
_TS_MIN, _TS_MAX = 1_700_000_000, 1_900_000_000

# ---- pump.fun Pool account (pump_amm IDL, 2026-10-07): is_mayhem_mode at byte 243 ------------
POOL_MAYHEM_OFFSET = 243
# PumpSwap GlobalConfig (949 bytes, IDL offsets): boost_authority 907..939, boost_enabled 939.
GLOBAL_CONFIG_LEN = 949
GLOBAL_CONFIG_BOOST_AUTHORITY = 907
GLOBAL_CONFIG_BOOST_ENABLED = 939

# ---- halt / warn thresholds (task spec) ------------------------------------------------------
BOOST_SHARE_MIN = 0.80  # BOOST on < 80% of sampled non-mayhem WSOL graduations -> halt
LAST_SLICE_MIN_S = 315  # median BOOST last slice < 315 s after the migrate tx -> halt
BOOST_CHANGE_MAX = 0.20  # BOOST budget or slice count moved > 20% vs pins -> halt
SYNTHETIC_MAX = 0.35  # synthetic-migration share > 35% -> halt
MS_PER_SLOT_WARN = 0.10  # ms/slot moved > 10% from the last run -> WARN
MIN_EVAL_N_DEFAULT = 5  # share rules need at least this many graduations to be evaluated
MIN_BOOST_POOLS = 3  # BOOST timing/size rules need at least this many profiled pools
SETTLE_S_DEFAULT = 480  # BOOST last slice was 329-351 s after migrate in the 10-08 sample

V1_TRADE_IX = frozenset({"Buy", "Sell", "BuyExactQuoteIn"})
V2_TRADE_IX = frozenset({"BuyV2", "SellV2", "BuyExactQuoteInV2"})

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_SKIP_RPC_CODES = frozenset({-32004, -32007, -32009})  # block/slot missing or skipped: result is "none"
_RETRY_RPC_CODES = frozenset({429, -32005})


# =============================================================================================
# small helpers
# =============================================================================================
def b58encode(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + out


def b58decode(text: str) -> bytes:
    n = 0
    for ch in text:
        n = n * 58 + _B58.index(ch)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\0" * (len(text) - len(text.lstrip("1"))) + raw


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def dist(values: Iterable[float | int | None]) -> dict[str, Any] | None:
    """min / median / max over the non-null values, or None when there are none."""
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    med = statistics.median(v)
    return {
        "n": len(v),
        "min": round(v[0], 4) if isinstance(v[0], float) else v[0],
        "median": round(med, 4) if isinstance(med, float) else med,
        "max": round(v[-1], 4) if isinstance(v[-1], float) else v[-1],
    }


def share(num: int, den: int) -> float | None:
    return round(num / den, 4) if den > 0 else None


# =============================================================================================
# program-derived addresses (pure python, no solders dependency)
# =============================================================================================
_P = 2**255 - 19
_D = (-121665 * pow(121666, -1, _P)) % _P


def _on_curve(raw: bytes) -> bool:
    """True when the 32 bytes decompress to an ed25519 point (such a key cannot be a PDA)."""
    y = int.from_bytes(raw, "little") & ((1 << 255) - 1)
    u = (y * y - 1) % _P
    v = (_D * y * y + 1) % _P
    x2 = (u * pow(v, -1, _P)) % _P
    return x2 == 0 or pow(x2, (_P - 1) // 2, _P) == 1


def find_program_address(seeds: Sequence[bytes], program_id: str) -> tuple[str, int]:
    pid = b58decode(program_id)
    for bump in range(255, -1, -1):
        h = hashlib.sha256(b"".join(seeds) + bytes([bump]) + pid + b"ProgramDerivedAddress").digest()
        if not _on_curve(h):
            return b58encode(h), bump
    raise ValueError("no viable bump seed")


def boost_vault_authority(pool: str) -> str:
    """PDA(["boost_vault", pool], PumpSwap). Signs every BOOST slice (audit s13: 99.58% of tape BOOST buyers)."""
    return find_program_address([b"boost_vault", b58decode(pool)], PUMPSWAP_PROGRAM)[0]


def boost_vault(pool: str, quote_mint: str = WSOL_MINT) -> str:
    """boost_vault = ATA(boost_vault_authority, token program, quote mint) per the pump_amm IDL."""
    authority = boost_vault_authority(pool)
    return find_program_address([b58decode(authority), b58decode(TOKEN_PROGRAM), b58decode(quote_mint)], ATA_PROGRAM)[0]


# =============================================================================================
# RPC client: public endpoint, polite pacing, retries on 429/5xx/network errors, hard call cap
# =============================================================================================
class RpcUnreachable(Exception):
    """Retries exhausted on network errors, 429 or 5xx."""


class RpcError(Exception):
    """A definitive JSON-RPC or HTTP error that retrying will not fix."""


class CallBudgetExceeded(Exception):
    """The per-run call cap was reached. No further calls are made."""


Transport = Callable[[str, bytes, float], "tuple[int, Mapping[str, str], bytes]"]


def urllib_transport(url: str, body: bytes, timeout: float) -> tuple[int, Mapping[str, str], bytes]:
    req = urllib.request.Request(url, body, {"Content-Type": "application/json", "User-Agent": "mal-pump-structure-monitor/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:  # 429 etc. are answers, not transport failures
        try:
            payload = exc.read()
        except Exception:
            payload = b""
        return exc.code, dict(exc.headers or {}), payload


class RpcClient:
    def __init__(
        self,
        url: str,
        *,
        min_interval: float = 0.5,
        max_retries: int = 5,
        timeout: float = 45.0,
        max_calls: int = 400,
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url = url
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self.max_calls = max_calls
        self._transport = transport
        self._sleep = sleep
        self._clock = clock
        self._last = None  # type: float | None
        self.calls = Counter()  # attempts per method, retries included
        self.ok_calls = 0
        self.retries = 0

    @property
    def total_calls(self) -> int:
        return sum(self.calls.values())

    def _throttle(self) -> None:
        if self._last is not None:
            wait = self.min_interval - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()

    def _backoff(self, attempt: int, retry_after: str | None) -> None:
        delay = min(60.0, 2.0 * (2**attempt))
        if retry_after:
            try:
                delay = min(60.0, max(delay, float(retry_after)))
            except ValueError:
                pass
        self.retries += 1
        self._sleep(delay)

    def call(self, method: str, params: Sequence[Any]) -> Any:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": list(params)}).encode()
        last = "no attempt"
        for attempt in range(self.max_retries + 1):
            if self.total_calls >= self.max_calls:
                raise CallBudgetExceeded(f"call cap {self.max_calls} reached before {method}")
            self._throttle()
            self.calls[method] += 1
            try:
                status, headers, payload = self._transport(self.url, body, self.timeout)
            except (OSError, http.client.HTTPException) as exc:
                last = f"network {type(exc).__name__}"
                if attempt < self.max_retries:
                    self._backoff(attempt, None)
                continue
            if status == 429 or status >= 500:
                last = f"http {status}"
                if attempt < self.max_retries:
                    self._backoff(attempt, {k.lower(): v for k, v in headers.items()}.get("retry-after"))
                continue
            if status != 200:
                raise RpcError(f"http {status} on {method}")
            try:
                obj = json.loads(payload)
            except ValueError:
                last = "undecodable body"
                if attempt < self.max_retries:
                    self._backoff(attempt, None)
                continue
            err = obj.get("error") if isinstance(obj, dict) else None
            if err:
                code = err.get("code") if isinstance(err, dict) else None
                if code in _RETRY_RPC_CODES:
                    last = f"rpc {code}"
                    if attempt < self.max_retries:
                        self._backoff(attempt, None)
                    continue
                if code in _SKIP_RPC_CODES:
                    self.ok_calls += 1
                    return None
                msg = str(err.get("message", ""))[:300] if isinstance(err, dict) else str(err)[:300]
                raise RpcError(f"rpc error {code} on {method}: {msg}")
            self.ok_calls += 1
            return obj.get("result") if isinstance(obj, dict) else None
        raise RpcUnreachable(f"{method}: {last} after {self.max_retries + 1} attempts")


# =============================================================================================
# tx helpers
# =============================================================================================
_INVOKE = re.compile(r"^Program (\S+) invoke \[\d+\]")
_DONE = re.compile(r"^Program (\S+) (?:success|failed)")
_INSTR = re.compile(r"^Program log: Instruction: (\w+)")


def program_instructions(logs: Sequence[str], program_id: str) -> list[str]:
    """Names from 'Program log: Instruction: X' lines logged while `program_id` is the running program."""
    stack: list[str] = []
    out: list[str] = []
    for line in logs:
        if not isinstance(line, str):
            continue
        m = _INVOKE.match(line)
        if m:
            stack.append(m.group(1))
            continue
        m = _INSTR.match(line)
        if m:
            if stack and stack[-1] == program_id:
                out.append(m.group(1))
            continue
        if _DONE.match(line) and stack:
            stack.pop()
    return out


def tx_logs(tx: Mapping[str, Any]) -> list[str]:
    return list((tx.get("meta") or {}).get("logMessages") or [])


def tx_keys(tx: Mapping[str, Any]) -> list[str]:
    """Static account keys plus address-table-loaded keys (json encoding, version 0)."""
    msg = (tx.get("transaction") or {}).get("message") or {}
    keys = [k if isinstance(k, str) else k.get("pubkey") for k in msg.get("accountKeys") or []]
    loaded = (tx.get("meta") or {}).get("loadedAddresses") or {}
    return keys + list(loaded.get("writable") or []) + list(loaded.get("readonly") or [])


def tx_signers(tx: Mapping[str, Any]) -> list[str]:
    msg = (tx.get("transaction") or {}).get("message") or {}
    n = int((msg.get("header") or {}).get("numRequiredSignatures") or 0)
    return tx_keys(tx)[:n]


def tx_event_blobs(tx: Mapping[str, Any]) -> list[bytes]:
    """Anchor event payloads (discriminator first) from 'Program data:' logs and emit_cpi inner instructions.

    Sept-20 migrate txs carry the events as emit_cpi self-CPIs, Oct-8 migrate txs as log lines
    (inv_b s11); both are read and de-duplicated.
    """
    blobs: list[bytes] = []
    for line in tx_logs(tx):
        if isinstance(line, str) and "Program data: " in line:
            try:
                blobs.append(base64.b64decode(line.split("Program data: ", 1)[1].strip()))
            except ValueError:
                continue
    keys = tx_keys(tx)
    for group in (tx.get("meta") or {}).get("innerInstructions") or []:
        for ix in group.get("instructions") or []:
            pid_idx = ix.get("programIdIndex")
            if pid_idx is None or pid_idx >= len(keys) or keys[pid_idx] not in (PUMP_PROGRAM, PUMPSWAP_PROGRAM):
                continue
            try:
                data = b58decode(ix.get("data") or "")
            except ValueError:
                continue
            if data[:8] == EVENT_CPI_TAG:
                blobs.append(data[8:])
    seen: set[bytes] = set()
    uniq: list[bytes] = []
    for b in blobs:
        if b not in seen:
            seen.add(b)
            uniq.append(b)
    return uniq


def decode_migration_event(raw: bytes) -> dict[str, Any] | None:
    """CompletePumpAmmMigrationEvent (offsets from tools/pump_history_backfill.decode_migration_event)."""
    if len(raw) < 200 or raw[:8] != DISC_MIGRATE:
        return None
    ts = int.from_bytes(raw[128:136], "little", signed=True)
    if not _TS_MIN <= ts <= _TS_MAX:
        return None
    quote = b58encode(raw[168:200])
    return {
        "mint": b58encode(raw[40:72]),
        "curve": b58encode(raw[96:128]),
        "pool": b58encode(raw[136:168]),
        "quote_mint": WSOL_MINT if quote == NATIVE_SOL else quote,
    }


def decode_complete_event(raw: bytes) -> dict[str, Any] | None:
    """CompleteEvent: the bonding curve is complete (mint at 40..72, curve at 72..104)."""
    if len(raw) < 144 or raw[:8] != DISC_COMPLETE:
        return None
    return {"mint": b58encode(raw[40:72]), "curve": b58encode(raw[72:104])}


def decode_post_complete_buy(raw: bytes) -> dict[str, Any] | None:
    """PostCompleteBuyEvent (pump IDL 2026-10-07): user, mint, bonding_curve, quote_mint, timestamp, base_out, quote_in, ...

    Only the identity is decoded. base_out and quote_in are trade sizes and are not read.
    """
    if len(raw) < 8 + 4 * 32 + 8 + 8 + 8 or raw[:8] != DISC_POST_COMPLETE_BUY:
        return None
    return {"mint": b58encode(raw[40:72]), "curve": b58encode(raw[72:104])}


def _events(blobs: Iterable[bytes], decoder: Callable[[bytes], dict[str, Any] | None]) -> list[dict[str, Any]]:
    return [e for e in (decoder(b) for b in blobs) if e is not None]


def post_complete_buy_seen(blobs: Iterable[bytes], mint: str) -> tuple[bool, bool | None]:
    """(seen, mint_match). `seen` is true on the PostCompleteBuyEvent discriminator alone, so a layout change or a
    mint-decode miss over-counts synthetic migrations instead of hiding them (this feeds a kill-switch).
    `mint_match` is the refinement: True / False when the event decodes, None when it carries the discriminator but is too short."""
    hits = [b for b in blobs if b[:8] == DISC_POST_COMPLETE_BUY]
    if not hits:
        return False, None
    decoded = _events(hits, decode_post_complete_buy)
    return True, (any(e["mint"] == mint for e in decoded) if decoded else None)


def parse_migrate_tx(tx: Mapping[str, Any]) -> dict[str, Any] | None:
    """Structure fields of one migrate tx, or None when it carries no CompletePumpAmmMigrationEvent."""
    blobs = tx_event_blobs(tx)
    migs = _events(blobs, decode_migration_event)
    if not migs:
        return None
    ev = migs[0]
    logs = tx_logs(tx)
    pump_ix = program_instructions(logs, PUMP_PROGRAM)
    amm_ix = program_instructions(logs, PUMPSWAP_PROGRAM)
    wsol = ev["quote_mint"] == WSOL_MINT
    pcb_seen, pcb_match = post_complete_buy_seen(blobs, ev["mint"])
    budget_lamports = None
    if wsol:  # budget = lamport delta of the boost vault in the migrate tx (includes token-account rent)
        keys = tx_keys(tx)
        vault = boost_vault(ev["pool"])
        if vault in keys:
            i = keys.index(vault)
            meta = tx["meta"]
            budget_lamports = int(meta["postBalances"][i]) - int(meta["preBalances"][i])
    return {
        "mint": ev["mint"],
        "pool": ev["pool"],
        "curve": ev["curve"],
        "quote_wsol": wsol,
        "init_boost": "InitBoost" in amm_ix,
        "migrate_ix": next((n for n in pump_ix if n.startswith("Migrate")), None),
        "slot": int(tx["slot"]),
        "block_time": tx.get("blockTime"),
        "complete_in_tx": any(e["mint"] == ev["mint"] for e in _events(blobs, decode_complete_event)),
        "post_complete_in_tx": pcb_seen,
        "post_complete_mint_match_in_tx": pcb_match,
        "budget_lamports": budget_lamports,
    }


def completion_info(tx: Mapping[str, Any], mint: str) -> dict[str, Any] | None:
    """If `tx` carries the CompleteEvent for `mint`, return its slot and whether a PostCompleteBuyEvent rode along
    (`synthetic`: discriminator seen; `synthetic_mint_match`: the refinement, see post_complete_buy_seen)."""
    blobs = tx_event_blobs(tx)
    if not any(e["mint"] == mint for e in _events(blobs, decode_complete_event)):
        return None
    synthetic, mint_match = post_complete_buy_seen(blobs, mint)
    return {"slot": int(tx["slot"]), "synthetic": synthetic, "synthetic_mint_match": mint_match}


# =============================================================================================
# account decoders
# =============================================================================================
def decode_slot_time(samples: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    ms = [s["samplePeriodSecs"] * 1000.0 / s["numSlots"] for s in samples or [] if s.get("numSlots")]
    if not ms:
        return {"ms_per_slot_median": None, "n_samples": 0}
    return {
        "ms_per_slot_median": round(statistics.median(ms), 2),
        "ms_per_slot_min": round(min(ms), 2),
        "ms_per_slot_max": round(max(ms), 2),
        "n_samples": len(ms),
    }


def decode_feature_gate(data: bytes | None) -> dict[str, Any]:
    """Feature account: byte 0 = Option tag, then activated_at slot (u64). No account = not staged."""
    if data is None:
        return {"status": "absent"}
    if len(data) >= 9 and data[0] == 1:
        slot = struct.unpack_from("<Q", data, 1)[0]
        return {"status": "activated", "activated_at_slot": slot, "activated_epoch": slot // SLOTS_PER_EPOCH}
    return {"status": "pending"}


def decode_fee_config(data: bytes) -> dict[str, Any] | None:
    """FeeConfig: bump, admin, flat fees (lp, protocol, creator bps), then two u128-threshold tier vecs."""
    try:
        o = 8 + 1 + 32
        flat = struct.unpack_from("<3Q", data, o)
        o += 24
        lists = []
        for _ in range(2):
            n = struct.unpack_from("<I", data, o)[0]
            o += 4
            if n > 64:
                return None
            tiers = []
            for _ in range(n):
                lo, hi = struct.unpack_from("<QQ", data, o)
                fees = struct.unpack_from("<3Q", data, o + 16)
                tiers.append(((hi << 64) + lo, fees))
                o += 40
            lists.append(tiers)
        exotic = list(struct.unpack_from("<3Q", data, o)) if o + 24 <= len(data) else None
        o += 24 if exotic else 0
    except struct.error:
        return None
    tier0 = lists[0][0][1] if lists[0] else None
    return {
        "flat_bps": list(flat),
        "exotic_flat_bps": exotic,
        "tier0_bps": list(tier0) if tier0 else None,
        "tier0_total_bps": sum(tier0) if tier0 else None,
        "n_tiers": len(lists[0]),
        "n_tiers_second_list": len(lists[1]),
        "bytes_consumed": o,
    }


def decode_global_config(data: bytes) -> dict[str, Any]:
    """PumpSwap GlobalConfig fields the monitor needs. boost_enabled is trusted only on the 949-byte layout."""
    out: dict[str, Any] = {"len": len(data), "layout_ok": len(data) == GLOBAL_CONFIG_LEN, "boost_enabled": None, "boost_authority": None}
    if len(data) > GLOBAL_CONFIG_BOOST_ENABLED:
        out["boost_enabled"] = data[GLOBAL_CONFIG_BOOST_ENABLED] == 1
        out["boost_authority"] = b58encode(data[GLOBAL_CONFIG_BOOST_AUTHORITY : GLOBAL_CONFIG_BOOST_AUTHORITY + 32])
    return out


def decode_programdata_slot(data: bytes | None) -> int | None:
    """UpgradeableLoaderState::ProgramData { slot } -> last deploy slot (tag 3, u64 at byte 4)."""
    if data is None or len(data) < 12 or struct.unpack_from("<I", data, 0)[0] != 3:
        return None
    return struct.unpack_from("<Q", data, 4)[0]


def decode_program_account(data: bytes | None) -> str | None:
    """UpgradeableLoaderState::Program { programdata_address } (tag 2)."""
    if data is None or len(data) < 36 or struct.unpack_from("<I", data, 0)[0] != 2:
        return None
    return b58encode(data[4:36])


def decode_pool_mayhem(data: bytes | None) -> bool | None:
    if data is None or len(data) <= POOL_MAYHEM_OFFSET:
        return None
    return data[POOL_MAYHEM_OFFSET] == 1


def _account_bytes(entry: Mapping[str, Any] | None) -> bytes | None:
    if not entry:
        return None
    return base64.b64decode(entry["data"][0])


# =============================================================================================
# stages (each takes a client and returns plain data; failures are caught by the caller)
# =============================================================================================
def stage_slot_time(client: RpcClient, epoch_info: Mapping[str, Any], now: float) -> dict[str, Any]:
    samples = client.call("getRecentPerformanceSamples", [30])
    out = decode_slot_time(samples)
    slot_index = int(epoch_info["slotIndex"])
    slots_in_epoch = int(epoch_info.get("slotsInEpoch") or SLOTS_PER_EPOCH)
    out.update(
        epoch=int(epoch_info["epoch"]),
        absolute_slot=int(epoch_info["absoluteSlot"]),
        slot_index=slot_index,
        slots_left_in_epoch=slots_in_epoch - slot_index,
    )
    if out["ms_per_slot_median"]:
        out["next_epoch_eta_utc"] = iso(now + out["slots_left_in_epoch"] * out["ms_per_slot_median"] / 1000.0)
    return out


def stage_accounts(client: RpcClient, epoch_info: Mapping[str, Any], now: float) -> dict[str, Any]:
    """One getMultipleAccounts for the config accounts, the slot-time gates and the program accounts."""
    names = list(PINNED_ACCOUNTS) + list(INFO_ACCOUNTS)
    addrs = [PINNED_ACCOUNTS.get(n) or INFO_ACCOUNTS[n] for n in names]
    gate_names = list(GATES)
    prog_names = list(PROGRAMS)
    keys = addrs + [GATES[g] for g in gate_names] + [PROGRAMS[p] for p in prog_names]
    res = client.call("getMultipleAccounts", [keys, {"encoding": "base64", "commitment": "finalized"}])
    values = (res or {}).get("value") or []
    if len(values) != len(keys):
        raise RpcError(f"getMultipleAccounts returned {len(values)} of {len(keys)} accounts")
    out: dict[str, Any] = {"context_slot": (res or {}).get("context", {}).get("slot"), "accounts": {}, "gates": {}, "program_accounts": {}}
    for i, name in enumerate(names):
        data = _account_bytes(values[i])
        rec: dict[str, Any] = {"address": addrs[i], "pinned": name in PINNED_ACCOUNTS, "len": None if data is None else len(data), "sha256": None if data is None else sha256_hex(data)}
        if data is not None and name in ("bonding_fee_config", "pumpswap_fee_config"):
            rec["decoded"] = decode_fee_config(data)
        if data is not None and name == "pumpswap_global_config":
            out["global_config"] = decode_global_config(data)
        out["accounts"][name] = rec
    base = len(names)
    slot_now = int(epoch_info["absoluteSlot"])
    for j, g in enumerate(gate_names):
        gate = decode_feature_gate(_account_bytes(values[base + j]))
        gate["address"] = GATES[g]
        if g == "200ms" and gate["status"] == "activated":
            # Inferred from the 350 ms gate (activated epoch 1019, stepped at 1020): each step lands one epoch after activation.
            eff_epoch = gate["activated_epoch"] + 1
            gate["effect_epoch_inferred"] = eff_epoch
            gate["effect_slot_inferred"] = eff_epoch * SLOTS_PER_EPOCH
            gate["effect_pending"] = slot_now < gate["effect_slot_inferred"]
        out["gates"][g] = gate
    base += len(gate_names)
    for k, p in enumerate(prog_names):
        out["program_accounts"][p] = decode_program_account(_account_bytes(values[base + k]))
    return out


def stage_programs(client: RpcClient, program_accounts: Mapping[str, str | None], pins: Mapping[str, Any]) -> dict[str, Any]:
    """Last deploy slot of each program from its programdata account (one slice read). Block time only on change."""
    names = [p for p in PROGRAMS if program_accounts.get(p)]
    out: dict[str, Any] = {p: {"program_id": PROGRAMS[p], "programdata": program_accounts.get(p), "deploy_slot": None, "deploy_utc": None} for p in PROGRAMS}
    if not names:
        return out
    res = client.call("getMultipleAccounts", [[program_accounts[p] for p in names], {"encoding": "base64", "dataSlice": {"offset": 0, "length": 45}, "commitment": "finalized"}])
    values = (res or {}).get("value") or []
    pinned = (pins.get("programs") or {})
    for p, entry in zip(names, values):
        slot = decode_programdata_slot(_account_bytes(entry))
        out[p]["deploy_slot"] = slot
        pin = pinned.get(p) or {}
        if slot is not None and slot == pin.get("deploy_slot") and pin.get("deploy_utc"):
            out[p]["deploy_utc"] = pin["deploy_utc"]
        elif slot is not None:
            try:
                bt = client.call("getBlockTime", [slot])
            except (RpcError, RpcUnreachable):
                bt = None
            out[p]["deploy_utc"] = iso(bt) if isinstance(bt, int) else None
    return out


_VERSION_HINT = re.compile(r"maxSupportedTransactionVersion\D{0,12}(\d+)")
MAX_TX_VERSION = 1  # version-1 txs exist on mainnet since late Sept 2026 (code -32015 at version 0)


def fetch_tx(client: RpcClient, sig: str) -> dict[str, Any] | None:
    """getTransaction, json encoding. On 'version not supported' (-32015) retry once at the version the node names."""
    version = MAX_TX_VERSION
    for _ in range(2):
        try:
            return client.call("getTransaction", [sig, {"encoding": "json", "maxSupportedTransactionVersion": version, "commitment": "finalized"}])
        except RpcError as exc:
            hint = _VERSION_HINT.search(str(exc))
            if "-32015" in str(exc) and hint and int(hint.group(1)) > version:
                version = int(hint.group(1))
                continue
            raise
    return None


def sample_graduations(client: RpcClient, n: int, cutoff_bt: int, *, max_pages: int = 3) -> list[dict[str, Any]]:
    """The N newest successful migrate txs on the migration fee account with blockTime <= cutoff_bt (fixed rule).

    Each candidate is one getTransaction; txs without a CompletePumpAmmMigrationEvent are skipped.
    """
    grads: list[dict[str, Any]] = []
    seen: set[str] = set()
    before: str | None = None
    for _ in range(max_pages):
        opts: dict[str, Any] = {"limit": 100, "commitment": "finalized"}
        if before:
            opts["before"] = before
        sigs = client.call("getSignaturesForAddress", [MIGRATION_FEE_ACCOUNT, opts]) or []
        if not sigs:
            break
        for s in sigs:
            before = s["signature"]
            bt = s.get("blockTime")
            if s.get("err") is not None or bt is None or bt > cutoff_bt:
                continue
            tx = fetch_tx(client, s["signature"])
            if tx is None:
                continue
            g = parse_migrate_tx(tx)
            if g is None or g["mint"] in seen:
                continue
            seen.add(g["mint"])
            g["sig"] = s["signature"]
            grads.append(g)
            if len(grads) >= n:
                return grads
        if len(sigs) < 100:
            break
    return grads


def annotate_mayhem(client: RpcClient, grads: list[dict[str, Any]]) -> None:
    """Mayhem flag from the PumpSwap Pool account (one getMultipleAccounts per 100 pools)."""
    for i in range(0, len(grads), 100):
        chunk = grads[i : i + 100]
        res = client.call("getMultipleAccounts", [[g["pool"] for g in chunk], {"encoding": "base64", "commitment": "finalized"}])
        for g, entry in zip(chunk, (res or {}).get("value") or []):
            g["mayhem"] = decode_pool_mayhem(_account_bytes(entry))


def annotate_completion(client: RpcClient, grads: list[dict[str, Any]], *, max_txs: int = 4) -> None:
    """Find the completing curve tx (newest successful curve tx before the migrate tx with a CompleteEvent)."""
    for g in grads:
        g["synthetic"] = None
        g["synthetic_mint_match"] = None
        g["complete_to_migrate_slots"] = None
        if g["complete_in_tx"]:  # CompleteEvent rode in the migrate tx itself
            g["synthetic"] = g["post_complete_in_tx"]
            g["synthetic_mint_match"] = g["post_complete_mint_match_in_tx"]
            g["complete_to_migrate_slots"] = 0
            continue
        sigs = client.call("getSignaturesForAddress", [g["curve"], {"limit": 10, "before": g["sig"], "commitment": "finalized"}]) or []
        tried = 0
        for s in sigs:
            if s.get("err") is not None:
                continue
            if tried >= max_txs:
                break
            tried += 1
            tx = fetch_tx(client, s["signature"])
            info = completion_info(tx, g["mint"]) if tx else None
            if info:
                g["synthetic"] = info["synthetic"] or g["post_complete_in_tx"]
                g["synthetic_mint_match"] = info["synthetic_mint_match"] if info["synthetic"] else g["post_complete_mint_match_in_tx"]
                g["complete_to_migrate_slots"] = g["slot"] - info["slot"]
                break


def profile_boost(client: RpcClient, grads: list[dict[str, Any]], boost_authority_key: str | None, *, max_verify_fetches: int = 3) -> dict[str, Any]:
    """BOOST keeper slices for graduations that carry InitBoost on a WSOL pool.

    Slice count and first/span timing come from the vault authority PDA's signature list minus the funding migrate
    tx. That count is an UPPER BOUND: the 10-08 check found 0-2 non-keeper txs per pool in the list (they touch the
    boost vault too, so its list is no cleaner). The last-slice time is exact: the newest signatures are fetched (at
    most `max_verify_fetches`) until one is signed by GlobalConfig.boost_authority and runs BoostBuyAndBurn. The SOL
    total is derived: lamports funded in the migrate tx minus the vault's lamports now (one getMultipleAccounts).
    """
    pools = [g for g in grads if g["init_boost"] and g["quote_wsol"] and g.get("block_time")]
    for g in pools:
        sigs = client.call("getSignaturesForAddress", [boost_vault_authority(g["pool"]), {"limit": 100, "commitment": "finalized"}]) or []
        slices = [s for s in sigs if s.get("err") is None and s["signature"] != g["sig"] and s.get("blockTime") is not None]
        slices.sort(key=lambda s: (s["slot"], s.get("transactionIndex") or 0), reverse=True)  # newest first
        times = sorted(s["blockTime"] for s in slices)
        last_t, verified = (times[-1] if times else None), False
        if boost_authority_key:
            for s in slices[:max_verify_fetches]:
                tx = fetch_tx(client, s["signature"])
                if tx and boost_authority_key in tx_signers(tx) and "BoostBuyAndBurn" in program_instructions(tx_logs(tx), PUMPSWAP_PROGRAM):
                    last_t, verified = s["blockTime"], True
                    break
        g["boost"] = {
            "n_slices": len(times),
            "first_after_s": times[0] - g["block_time"] if times else None,
            # Zero slices count as "BOOST ended at 0 s": the median halt rule then fires, which is the right outcome.
            "last_after_s": last_t - g["block_time"] if last_t is not None else 0,
            "last_verified": verified,
            "span_s": (last_t - times[0]) if times and last_t is not None else None,
            "truncated": len(sigs) >= 100,
        }
    if pools:  # remaining vault lamports -> total spent
        res = client.call("getMultipleAccounts", [[boost_vault(g["pool"]) for g in pools], {"encoding": "base64", "dataSlice": {"offset": 0, "length": 0}, "commitment": "finalized"}])
        for g, entry in zip(pools, (res or {}).get("value") or []):
            left = int(entry["lamports"]) if entry else 0
            g["boost"]["vault_left_lamports"] = left
            if g["budget_lamports"] is not None:
                g["boost"]["spent_lamports"] = g["budget_lamports"] - left
    lam = 1e9
    return {
        "n_profiled": len(pools),
        "slices_note": "signature-list count after the migrate tx: an upper bound that includes 0-2 non-keeper txs per pool",
        "slices": dist(g["boost"]["n_slices"] for g in pools),
        "budget_sol": dist(g["budget_lamports"] / lam for g in pools if g["budget_lamports"] is not None),
        "sol_total": dist(g["boost"]["spent_lamports"] / lam for g in pools if g["boost"].get("spent_lamports") is not None),
        "first_slice_after_migrate_s": dist(g["boost"]["first_after_s"] for g in pools),
        "last_slice_after_migrate_s": dist(g["boost"]["last_after_s"] for g in pools),
        "first_to_last_span_s": dist(g["boost"]["span_s"] for g in pools),
        "n_zero_slices": sum(1 for g in pools if g["boost"]["n_slices"] == 0),
        "n_truncated_at_100": sum(1 for g in pools if g["boost"]["truncated"]),
        "n_vault_drained": sum(1 for g in pools if g["boost"].get("vault_left_lamports") == 0),
        "last_slice_verified": sum(1 for g in pools if g["boost"]["last_verified"]),
    }


def summarize_graduations(grads: Sequence[Mapping[str, Any]], n_requested: int, cutoff_bt: int) -> dict[str, Any]:
    def flag(v: Any) -> str:
        return "?" if v is None else str(int(bool(v)))

    nonmayhem_wsol = [g for g in grads if g.get("mayhem") is False and g["quote_wsol"]]
    synth_known = [g for g in grads if g.get("synthetic") is not None]
    patterns = Counter(
        f"mayhem={flag(g.get('mayhem'))},quote={'wsol' if g['quote_wsol'] else 'other'},init_boost={flag(g['init_boost'])},synthetic={flag(g.get('synthetic'))}"
        for g in grads
    )
    bts = [g["block_time"] for g in grads if g.get("block_time")]
    return {
        "rule": f"newest {n_requested} successful migrate txs on the migration fee account with blockTime <= run - settle",
        "n_requested": n_requested,
        "n": len(grads),
        "cutoff_block_time": cutoff_bt,
        "slot_min": min((g["slot"] for g in grads), default=None),
        "slot_max": max((g["slot"] for g in grads), default=None),
        "block_time_min": min(bts, default=None),
        "block_time_max": max(bts, default=None),
        "migrate_ix": dict(Counter(g["migrate_ix"] or "?" for g in grads)),
        "mayhem": {"true": sum(1 for g in grads if g.get("mayhem") is True), "false": sum(1 for g in grads if g.get("mayhem") is False), "unknown": sum(1 for g in grads if g.get("mayhem") is None)},
        "quote": {"wsol": sum(1 for g in grads if g["quote_wsol"]), "other": sum(1 for g in grads if not g["quote_wsol"])},
        "init_boost": {"true": sum(1 for g in grads if g["init_boost"]), "false": sum(1 for g in grads if not g["init_boost"])},
        "boost_denominator": {
            "n_nonmayhem_wsol": len(nonmayhem_wsol),
            "n_init_boost": sum(1 for g in nonmayhem_wsol if g["init_boost"]),
            "share": share(sum(1 for g in nonmayhem_wsol if g["init_boost"]), len(nonmayhem_wsol)),
        },
        "synthetic": {
            "n_checked": len(synth_known),
            "n_synthetic": sum(1 for g in synth_known if g["synthetic"]),
            "n_mint_match": sum(1 for g in synth_known if g["synthetic"] and g.get("synthetic_mint_match") is True),  # refinement; the halt uses n_synthetic
            "share": share(sum(1 for g in synth_known if g["synthetic"]), len(synth_known)),
            "n_completion_not_found": len(grads) - len(synth_known),
        },
        "complete_to_migrate_slots": dist(g.get("complete_to_migrate_slots") for g in grads),
        "patterns": dict(sorted(patterns.items())),
    }


def stage_pumpswap_mix(client: RpcClient, n_txs: int) -> dict[str, Any]:
    """Instruction names only, over the first `n_txs` successful txs of the newest 100 on the PumpSwap program."""
    sigs = client.call("getSignaturesForAddress", [PUMPSWAP_PROGRAM, {"limit": 100, "commitment": "finalized"}]) or []
    picked = [s for s in sigs if s.get("err") is None][:n_txs]
    names: Counter[str] = Counter()
    times: list[int] = []
    for s in picked:
        tx = fetch_tx(client, s["signature"])
        if not tx:
            continue
        names.update(program_instructions(tx_logs(tx), PUMPSWAP_PROGRAM))
        if tx.get("blockTime"):
            times.append(tx["blockTime"])
    v1 = sum(c for n, c in names.items() if n in V1_TRADE_IX)
    v2 = sum(c for n, c in names.items() if n in V2_TRADE_IX)
    return {
        "rule": f"first {n_txs} successful txs among the newest 100 signatures of the PumpSwap program",
        "n_txs": len(times) if times else len(picked),
        "span_s": (max(times) - min(times)) if times else None,
        "trade_ix_v1": v1,
        "trade_ix_v2": v2,
        "v2_share": share(v2, v1 + v2),
        "ix_counts": dict(names.most_common(12)),
    }


# =============================================================================================
# pins, halt and warn flags (pure functions of the record)
# =============================================================================================
def load_pins(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        pins = json.load(fh)
    if not isinstance(pins, dict):
        raise ValueError("pins file is not a JSON object")
    return pins


def compare_pins(rec: Mapping[str, Any], pins: Mapping[str, Any]) -> dict[str, Any]:
    """Compare observed account sha256 / deploy slots with the pins. A null pin or an unread item is not a change."""
    changed: list[str] = []
    unpinned: list[str] = []
    unread: list[str] = []
    ok: list[str] = []
    pin_acc = pins.get("accounts") or {}
    for name, obs in (rec.get("accounts") or {}).items():
        if not obs.get("pinned"):
            continue
        want = (pin_acc.get(name) or {}).get("sha256")
        got = obs.get("sha256")
        if want is None:
            unpinned.append(name)
        elif got is None:
            unread.append(name)
        elif got != want:
            changed.append(f"{name} sha256 {want[:12]} -> {got[:12]}")
        else:
            ok.append(name)
    pin_prog = pins.get("programs") or {}
    for name, obs in (rec.get("programs") or {}).items():
        want = (pin_prog.get(name) or {}).get("deploy_slot")
        got = obs.get("deploy_slot")
        label = f"program_{name}"
        if want is None:
            unpinned.append(label)
        elif got is None:
            unread.append(label)
        elif got != want:
            changed.append(f"{label} deploy_slot {want} -> {got}" + (f" ({obs.get('deploy_utc')})" if obs.get("deploy_utc") else ""))
        else:
            ok.append(label)
    return {"changed": changed, "unpinned": unpinned, "unread": unread, "ok": ok}


def _flag(halt: bool, reason: str, evaluated: bool = True) -> dict[str, Any]:
    return {"halt": bool(halt), "evaluated": evaluated, "reason": reason}


def compute_flags(rec: Mapping[str, Any], pins: Mapping[str, Any], prev_ms_per_slot: float | None, *, min_eval_n: int = MIN_EVAL_N_DEFAULT) -> tuple[dict[str, Any], dict[str, Any]]:
    """HALT flags (six) and WARN flags. Each is {halt|warn, evaluated, reason}. A rule whose inputs are missing is
    not evaluated (halt false) and listed in warn.rules_not_evaluated."""
    halt: dict[str, Any] = {}

    # 1. config / fee / program changed vs pins
    cmp_ = compare_pins(rec, pins)
    if cmp_["changed"]:
        halt["pins_changed"] = _flag(True, "; ".join(cmp_["changed"]))
    elif not cmp_["ok"]:
        halt["pins_changed"] = _flag(False, f"nothing comparable (unpinned: {cmp_['unpinned']}, unread: {cmp_['unread']})", evaluated=False)
    else:
        extra = []
        if cmp_["unpinned"]:
            extra.append(f"unpinned: {cmp_['unpinned']}")
        if cmp_["unread"]:
            extra.append(f"unread: {cmp_['unread']}")
        halt["pins_changed"] = _flag(False, f"{len(cmp_['ok'])} pinned items unchanged" + (f" ({'; '.join(extra)})" if extra else ""))

    # 2. boost_enabled false
    gc = rec.get("global_config") or {}
    be = gc.get("boost_enabled")
    if be is None:
        halt["boost_disabled"] = _flag(False, "boost_enabled not decodable (GlobalConfig unread)", evaluated=False)
    elif not gc.get("layout_ok"):
        halt["boost_disabled"] = _flag(False, f"GlobalConfig layout changed (len {gc.get('len')}), boost_enabled untrusted; pins_changed covers the change", evaluated=False)
    else:
        halt["boost_disabled"] = _flag(be is False, f"GlobalConfig.boost_enabled = {int(be)}")

    grads = rec.get("graduations") or {}
    den = (grads.get("boost_denominator") or {})
    n_den, n_ib = den.get("n_nonmayhem_wsol", 0), den.get("n_init_boost", 0)

    # 3. BOOST present on < 80% of sampled non-mayhem WSOL graduations
    if n_den < min_eval_n:
        halt["boost_share_low"] = _flag(False, f"only {n_den} non-mayhem WSOL graduations sampled, need >= {min_eval_n}", evaluated=False)
    else:
        sh = n_ib / n_den
        halt["boost_share_low"] = _flag(sh < BOOST_SHARE_MIN, f"InitBoost on {n_ib}/{n_den} = {sh:.3f} of non-mayhem WSOL graduations (halt below {BOOST_SHARE_MIN})")

    boost = rec.get("boost") or {}
    n_pools = boost.get("n_profiled", 0)
    last = boost.get("last_slice_after_migrate_s")

    # 4. median BOOST last-slice time < 315 s after the migrate tx
    if n_pools < MIN_BOOST_POOLS or not last:
        halt["boost_last_slice_early"] = _flag(False, f"only {n_pools} BOOST pools profiled, need >= {MIN_BOOST_POOLS}", evaluated=False)
    else:
        halt["boost_last_slice_early"] = _flag(last["median"] < LAST_SLICE_MIN_S, f"median last slice {last['median']} s after migrate over {last['n']} pools (halt below {LAST_SLICE_MIN_S} s)")

    # 5. BOOST budget or slice count changed > 20% vs pins
    pin_boost = pins.get("boost") or {}
    budget_pin, slices_pin = pin_boost.get("budget_sol"), pin_boost.get("slices")
    bud, sl = boost.get("budget_sol"), boost.get("slices")
    if n_pools < MIN_BOOST_POOLS or not bud or not sl or not budget_pin or not slices_pin:
        halt["boost_budget_or_slices_changed"] = _flag(False, f"needs >= {MIN_BOOST_POOLS} profiled pools with budget and slices, and both pins", evaluated=False)
    else:
        d_bud = bud["median"] / budget_pin - 1.0
        d_sl = sl["median"] / slices_pin - 1.0
        bad = abs(d_bud) > BOOST_CHANGE_MAX or abs(d_sl) > BOOST_CHANGE_MAX
        halt["boost_budget_or_slices_changed"] = _flag(
            bad,
            f"median budget {bud['median']} SOL vs pin {budget_pin} ({d_bud:+.1%}); median slices {sl['median']} vs pin {slices_pin} ({d_sl:+.1%}); halt beyond +/-{BOOST_CHANGE_MAX:.0%}",
        )

    # 6. synthetic-migration share > 35%
    syn = grads.get("synthetic") or {}
    n_chk, n_syn = syn.get("n_checked", 0), syn.get("n_synthetic", 0)
    if n_chk < min_eval_n:
        halt["synthetic_share_high"] = _flag(False, f"only {n_chk} graduations with a found completing tx, need >= {min_eval_n}", evaluated=False)
    else:
        sh = n_syn / n_chk
        halt["synthetic_share_high"] = _flag(sh > SYNTHETIC_MAX, f"PostCompleteBuyEvent on {n_syn}/{n_chk} = {sh:.3f} of graduations (halt above {SYNTHETIC_MAX})")

    warn: dict[str, Any] = {}
    cur = (rec.get("slot_time") or {}).get("ms_per_slot_median")
    if cur is None:
        warn["ms_per_slot_moved"] = {"warn": False, "evaluated": False, "reason": "ms/slot not measured this run"}
    elif prev_ms_per_slot is None:
        warn["ms_per_slot_moved"] = {"warn": False, "evaluated": False, "reason": f"no previous run; ms/slot {cur}"}
    else:
        mv = cur / prev_ms_per_slot - 1.0
        warn["ms_per_slot_moved"] = {"warn": abs(mv) > MS_PER_SLOT_WARN, "evaluated": True, "reason": f"ms/slot {prev_ms_per_slot} -> {cur} ({mv:+.1%}); warn beyond +/-{MS_PER_SLOT_WARN:.0%}"}
    n_req, n_got = grads.get("n_requested", 0), grads.get("n", 0)
    warn["graduation_sample_short"] = {"warn": bool(grads) and n_got < n_req, "evaluated": bool(grads), "reason": f"{n_got} of {n_req} requested graduations sampled"}
    not_eval = sorted(k for k, v in halt.items() if not v["evaluated"])
    warn["rules_not_evaluated"] = {"warn": bool(not_eval), "evaluated": True, "reason": ", ".join(not_eval) if not_eval else "all halt rules evaluated"}
    if rec.get("errors"):
        warn["stage_errors"] = {"warn": True, "evaluated": True, "reason": f"{len(rec['errors'])} stage error(s), see errors"}
    return halt, warn


# =============================================================================================
# record assembly, output
# =============================================================================================
def _stage(errors: list[str], name: str, fn: Callable[[], Any], default: Any = None) -> Any:
    """Run one stage. Any failure is data (an `errors` entry), never a crash."""
    try:
        return fn()
    except CallBudgetExceeded as exc:
        errors.append(f"{name}: {exc}")
    except Exception as exc:  # noqa: BLE001 - halts and errors are data; the daily run must always finish
        errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:160]}")
    return default


def build_record(
    client: RpcClient,
    epoch_info: Mapping[str, Any],
    pins: Mapping[str, Any],
    *,
    now: float,
    n_grads: int,
    settle_s: int,
    v2_sample: int,
    min_eval_n: int,
    prev_ms_per_slot: float | None,
    rpc_host: str,
    pins_error: str | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    if pins_error:
        errors.append(f"pins: {pins_error}")
    rec: dict[str, Any] = {"schema": SCHEMA, "run_utc": iso(now), "run_unix": int(now), "rpc_host": rpc_host}

    rec["slot_time"] = _stage(errors, "slot_time", lambda: stage_slot_time(client, epoch_info, now), {})
    acc = _stage(errors, "accounts", lambda: stage_accounts(client, epoch_info, now), {}) or {}
    rec["accounts"] = acc.get("accounts", {})
    rec["gates"] = acc.get("gates", {})
    rec["boost_enabled"] = None
    if acc.get("global_config"):
        rec["global_config"] = acc["global_config"]
        rec["boost_enabled"] = acc["global_config"]["boost_enabled"] if acc["global_config"]["layout_ok"] else None
    rec["accounts_context_slot"] = acc.get("context_slot")
    rec["programs"] = _stage(errors, "programs", lambda: stage_programs(client, acc.get("program_accounts", {}), pins), {})

    cutoff_bt = int(now) - settle_s
    grads: list[dict[str, Any]] = []
    if n_grads > 0:
        grads = _stage(errors, "graduations", lambda: sample_graduations(client, n_grads, cutoff_bt), []) or []
    if grads:
        _stage(errors, "graduations.mayhem", lambda: annotate_mayhem(client, grads))
        _stage(errors, "graduations.completion", lambda: annotate_completion(client, grads))
        authority = (rec.get("global_config") or {}).get("boost_authority")
        rec["boost"] = _stage(errors, "boost", lambda: profile_boost(client, grads, authority), {})
    rec["graduations"] = summarize_graduations(grads, n_grads, cutoff_bt) if n_grads > 0 else {}
    rec["pumpswap_trade_mix"] = _stage(errors, "pumpswap_trade_mix", lambda: stage_pumpswap_mix(client, v2_sample), None) if v2_sample > 0 else None

    rec["errors"] = errors
    rec["rpc"] = {"calls": client.total_calls, "ok": client.ok_calls, "retries": client.retries, "by_method": dict(client.calls), "cap": client.max_calls}
    rec["pins_check"] = compare_pins(rec, pins)
    for name, obs in rec["accounts"].items():
        if obs.get("pinned"):
            obs["pinned_sha256"] = ((pins.get("accounts") or {}).get(name) or {}).get("sha256")
    for name, obs in (rec.get("programs") or {}).items():
        obs["pinned_deploy_slot"] = ((pins.get("programs") or {}).get(name) or {}).get("deploy_slot")
    halt, warn = compute_flags(rec, pins, prev_ms_per_slot, min_eval_n=min_eval_n)
    rec["prev_ms_per_slot"] = prev_ms_per_slot
    rec["halt"] = {"any": any(v["halt"] for v in halt.values()), "flags": halt}
    rec["warn"] = {"any": any(v["warn"] for v in warn.values()), "flags": warn}
    rec["status"] = "ok" if not errors else "partial"
    return rec


def last_ms_per_slot(path: Path) -> float | None:
    """ms/slot of the newest record in the output file that has one (reads only the file tail)."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            fh.seek(max(0, size - 262_144))
            tail = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(tail):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        v = ((obj.get("slot_time") or {}) if isinstance(obj, dict) else {}).get("ms_per_slot_median")
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


def append_record(path: Path, rec: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def format_summary(rec: Mapping[str, Any]) -> str:
    st = rec.get("slot_time") or {}
    gate = (rec.get("gates") or {}).get("200ms") or {}
    g = rec.get("graduations") or {}
    bo = rec.get("boost") or {}
    mix = rec.get("pumpswap_trade_mix") or {}
    lines = [f"pump_structure_monitor {rec['run_utc']} rpc={rec['rpc_host']} calls={rec['rpc']['calls']} status={rec['status']}"]
    gate_txt = gate.get("status", "n/a")
    if gate.get("status") == "activated":
        gate_txt += f" at slot {gate['activated_at_slot']} (epoch {gate['activated_epoch']})"
        if gate.get("effect_pending"):
            gate_txt += f", step inferred at epoch {gate['effect_epoch_inferred']} ~{st.get('next_epoch_eta_utc', '?')}"
    lines.append(f"slot time: {st.get('ms_per_slot_median')} ms/slot (median of {st.get('n_samples')}), epoch {st.get('epoch')}, 200ms gate {gate_txt}")
    pc = rec.get("pins_check") or {}
    lines.append(f"pins: {len(pc.get('ok', []))} unchanged, {len(pc.get('changed', []))} changed, {len(pc.get('unpinned', []))} unpinned, {len(pc.get('unread', []))} unread; boost_enabled={rec.get('boost_enabled')}")
    if g:
        d = g.get("boost_denominator", {})
        s = g.get("synthetic", {})
        lines.append(
            f"graduations: n={g.get('n')} mayhem={g.get('mayhem', {}).get('true')} non-WSOL={g.get('quote', {}).get('other')} "
            f"InitBoost {d.get('n_init_boost')}/{d.get('n_nonmayhem_wsol')} non-mayhem WSOL, synthetic {s.get('n_synthetic')}/{s.get('n_checked')}, migrate_ix {g.get('migrate_ix')}"
        )
    if bo:
        def med(k: str) -> Any:
            return (bo.get(k) or {}).get("median")

        lines.append(
            f"boost: {bo.get('n_profiled')} pools, slices median {med('slices')} (min {(bo.get('slices') or {}).get('min')}, max {(bo.get('slices') or {}).get('max')}), "
            f"budget median {med('budget_sol')} SOL, spent median {med('sol_total')} SOL, last slice median {med('last_slice_after_migrate_s')} s "
            f"(min {(bo.get('last_slice_after_migrate_s') or {}).get('min')}) after migrate, keeper-verified {bo.get('last_slice_verified')}/{bo.get('n_profiled')}"
        )
    if mix:
        lines.append(f"pumpswap trade ix sample: {mix.get('n_txs')} txs, v2 {mix.get('trade_ix_v2')} / v1 {mix.get('trade_ix_v1')} (v2 share {mix.get('v2_share')})")
    halts = [(k, v) for k, v in rec["halt"]["flags"].items() if v["halt"]]
    if halts:
        for k, v in halts:
            lines.append(f"HALT {k}: {v['reason']}")
    else:
        lines.append("HALT: none")
    for k, v in rec["warn"]["flags"].items():
        if v["warn"]:
            lines.append(f"WARN {k}: {v['reason']}")
    for e in rec.get("errors", []):
        lines.append(f"ERROR {e}")
    return "\n".join(lines)


# =============================================================================================
# pins writer and CLI
# =============================================================================================
def build_pins(rec: Mapping[str, Any], previous: Mapping[str, Any] | None) -> dict[str, Any]:
    """Pins from one live read. BOOST budget/slices carry over from the previous pins (spec: 17.585 SOL, 29 slices)."""
    prev_boost = (previous or {}).get("boost") or {}
    return {
        "schema": "pump_structure_pins.v1",
        "note": "Written by tools/pump_structure_monitor.py --write-pins from a live public-RPC read. Re-pin only after reviewing the change that halted the monitor.",
        "pinned_utc": rec["run_utc"],
        "pinned_at_slot": rec.get("accounts_context_slot"),
        "accounts": {n: {"address": o["address"], "len": o["len"], "sha256": o["sha256"]} for n, o in rec["accounts"].items() if o.get("pinned")},
        "programs": {n: {"program_id": o["program_id"], "deploy_slot": o["deploy_slot"], "deploy_utc": o["deploy_utc"]} for n, o in rec["programs"].items()},
        "boost": {"budget_sol": prev_boost.get("budget_sol", 17.585), "slices": prev_boost.get("slices", 29)},
    }


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Daily pump.fun structure monitor (public RPC only, no prices).")
    p.add_argument("--rpc-url", default=DEFAULT_RPC, help="public mainnet RPC (default %(default)s); Helius URLs are refused")
    p.add_argument("--out", default=DEFAULT_OUT, help="JSONL file to append one record to (default %(default)s)")
    p.add_argument("--pins", default=str(DEFAULT_PINS), help="pins JSON (default tools/pump_structure_pins.json)")
    p.add_argument("--n", type=int, default=20, help="newest graduations to sample (default 20)")
    p.add_argument("--settle-s", type=int, default=SETTLE_S_DEFAULT, help="skip graduations younger than this many seconds so BOOST has finished")
    p.add_argument("--v2-sample", type=int, default=40, help="PumpSwap txs to read instruction names from (0 to skip)")
    p.add_argument("--min-eval-n", type=int, default=MIN_EVAL_N_DEFAULT, help="minimum sample for the share-based halt rules")
    p.add_argument("--min-interval", type=float, default=0.5, help="seconds between RPC calls")
    p.add_argument("--max-calls", type=int, default=400, help="hard cap on RPC calls per run, retries included")
    p.add_argument("--write-pins", metavar="PATH", help="read config/program state only, write a pins file to PATH, append nothing")
    return p.parse_args(argv)


def rpc_host(url: str) -> str:
    """hostname[:port] only. Never userinfo, path or query: any of them can carry a credential."""
    try:
        parsed = urlparse(url)
        host, port = parsed.hostname or "unknown", parsed.port
    except ValueError:
        return "unknown"
    return f"{host}:{port}" if port else host


def main(argv: Sequence[str] | None = None, *, client: RpcClient | None = None, now: float | None = None) -> int:
    args = parse_args(argv)
    host = rpc_host(args.rpc_url)
    if "helius" in args.rpc_url.lower():
        print("refused: this monitor uses public RPC only (0 Helius credits)", file=sys.stderr)
        return 64
    t_now = time.time() if now is None else now
    if client is None:
        client = RpcClient(args.rpc_url, min_interval=args.min_interval, max_calls=args.max_calls)
    try:
        epoch_info = client.call("getEpochInfo", [])
        if not isinstance(epoch_info, dict) or "epoch" not in epoch_info:
            raise RpcError("getEpochInfo returned no epoch")
    except (RpcUnreachable, RpcError, CallBudgetExceeded) as exc:
        print(f"RPC unreachable ({host}): {exc}", file=sys.stderr)
        return 2

    pins: dict[str, Any] = {}
    pins_error: str | None = None
    try:
        pins = load_pins(Path(args.pins))
    except (OSError, ValueError) as exc:
        pins_error = f"{type(exc).__name__}: {str(exc)[:120]}"

    out_path = Path(args.out)
    if args.write_pins:
        rec = build_record(client, epoch_info, pins, now=t_now, n_grads=0, settle_s=args.settle_s, v2_sample=0, min_eval_n=args.min_eval_n, prev_ms_per_slot=None, rpc_host=host, pins_error=pins_error)
        if not rec["accounts"] or not rec["programs"]:
            print("pins not written: config or program read failed: " + "; ".join(rec["errors"]), file=sys.stderr)
            return 2
        Path(args.write_pins).write_text(json.dumps(build_pins(rec, pins), indent=2) + "\n", encoding="utf-8")
        print(f"wrote pins to {args.write_pins} from slot {rec.get('accounts_context_slot')}")
        return 0

    rec = build_record(
        client, epoch_info, pins, now=t_now, n_grads=args.n, settle_s=args.settle_s, v2_sample=args.v2_sample, min_eval_n=args.min_eval_n,
        prev_ms_per_slot=last_ms_per_slot(out_path), rpc_host=host, pins_error=pins_error,
    )
    print(format_summary(rec), flush=True)  # first: a HALT must reach the log even when the file write fails
    try:
        append_record(out_path, rec)
    except OSError as exc:
        print(f"WRITE FAILED {out_path}: {type(exc).__name__}: {str(exc)[:160]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
