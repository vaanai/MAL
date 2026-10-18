"""Haiku trader v4 (HV4) chain facts: the RPC half of the shared risk agent (DESIGN section 2, build plan item 2).

Paper only. No wallet, trading or signing key is touched; every call is a read.

What this module returns, all stamped with `as_of_slot` (the RPC context slot) and `as_of_ms` (the local wall clock when the answer was in hand):

  * `mint_facts(mint)`        getAccountInfo (base64) of the mint: token program, mint and freeze authority, supply, decimals, and the
                              Token-2022 TLV extensions parsed by hand from the account bytes (layout: spl-token-2022 `ExtensionType`).
  * `largest_holders(mint)`   getTokenLargestAccounts with the pool vault, the bonding-curve vault and burn addresses excluded: top1 and
                              top10 share of total supply.
  * `mayhem_flag(mint)`       the Mayhem flag: the create row's flag when the caller has it, else the PumpSwap Pool account byte 243.
                              An unknown flag counts as Mayhem (DESIGN hard veto 3).

Fail closed. An RPC error, a malformed answer, a short or odd account, an unknown extension id, a repeated extension or trailing bytes all
produce a result with ok False and a veto reason `chain_error:<code>`. Nothing here raises to the caller for a chain problem, and nothing
is guessed. DESIGN: "an RPC error means no card".

RPC. The public mainnet endpoint is the default. A Helius endpoint is used only when asked for (`RpcConfig.helius()` or MAL_HV4_RPC=helius); its
key is read at run time from the process environment or a Helius env file and lives only in a private attribute. No URL is printed, logged
or put in an exception text (errors are reduced to short codes and redacted). A token bucket paces every call, a credit counter reports calls
and Helius credits per job and per UTC day, and a daily call cap bounds the spend (DESIGN section 6: about 40k standard calls a day at 2 rps).

Isolation: this module imports no l3, r4b or laya-l3 code and reads no tape, block or sealed file. It only talks to an RPC endpoint.
"""

from __future__ import annotations

import base64
import binascii
import functools
import hashlib
import json
import logging
import os
import re
import struct
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from tools import pump_structure_monitor as M

log = logging.getLogger("hv4_chain")

SCHEMA = "hv4_chain.v1"

# ---- chain constants (public addresses; none is a key) -----------------------------------------------------------------------
SPL_TOKEN_PROGRAM = M.TOKEN_PROGRAM
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
ATA_PROGRAM = M.ATA_PROGRAM
PUMP_PROGRAM = M.PUMP_PROGRAM
PUMPSWAP_PROGRAM = M.PUMPSWAP_PROGRAM
WSOL_MINT = M.WSOL_MINT
BURN_OWNERS = ("1nc1nerator11111111111111111111111111111111", "11111111111111111111111111111111")
TOKEN_PROGRAM_NAMES = {SPL_TOKEN_PROGRAM: "spl-token", TOKEN_2022_PROGRAM: "token-2022"}

PUBLIC_RPC = "https://api.mainnet-beta.solana.com"
HELIUS_ENV_FILES = (Path("/var/lib/mal/fast-listener/helius.env"), Path("/var/lib/mal/backfill/helius.env"))

MAX_ACCOUNT_BYTES = 10_240  # a mint with extensions is a few hundred bytes; anything larger is not a mint we parse
MAYHEM_VETO_AGE_S = 24 * 3600  # DESIGN hard veto 3: Mayhem with age < 24 h

# ---- Token-2022 layout -------------------------------------------------------------------------------------------------------
MINT_LEN = 82  # SPL Token and Token-2022 base mint
ACCOUNT_LEN = 165  # Token-2022 pads an extended mint to the token-account size
MULTISIG_LEN = 355
ACCOUNT_TYPE_OFFSET = 165
ACCOUNT_TYPE_MINT = 1
TLV_START = 166

# ExtensionType ids (spl-token-2022), as the jsonParsed names.
EXT_NAMES: dict[int, str] = {
    1: "transferFeeConfig", 2: "transferFeeAmount", 3: "mintCloseAuthority", 4: "confidentialTransferMint",
    5: "confidentialTransferAccount", 6: "defaultAccountState", 7: "immutableOwner", 8: "memoTransfer", 9: "nonTransferable",
    10: "interestBearingConfig", 11: "cpiGuard", 12: "permanentDelegate", 13: "nonTransferableAccount", 14: "transferHook",
    15: "transferHookAccount", 16: "confidentialTransferFeeConfig", 17: "confidentialTransferFeeAmount", 18: "metadataPointer",
    19: "tokenMetadata", 20: "groupPointer", 21: "tokenGroup", 22: "groupMemberPointer", 23: "tokenGroupMember",
    24: "confidentialMintBurn", 25: "scaledUiAmountConfig", 26: "pausableConfig", 27: "pausableAccount",
}
# Extensions that live on token accounts only. In a mint they mean the bytes are not what they claim to be: parse error.
ACCOUNT_ONLY_EXT = frozenset({2, 5, 7, 8, 11, 13, 15, 17, 27})
# Value lengths that are fixed by the layout (unsure or variable ones are left out: 16, 19, 24).
EXT_FIXED_LEN: dict[int, int] = {
    1: 108, 3: 32, 4: 65, 6: 1, 9: 0, 10: 52, 12: 32, 14: 64, 18: 64, 20: 64, 21: 80, 22: 64, 23: 72, 25: 56, 26: 33,
}
DEFAULT_ACCOUNT_STATE = {1: "initialized", 2: "frozen"}  # 0 (uninitialized) is not a legal default

# Hard-veto reason strings (DESIGN section 2, vetoes 1 and 2).
V_MINT_AUTH = "mint_authority"
V_FREEZE_AUTH = "freeze_authority"
V_HOOK = "ext:transfer_hook"
V_FEE = "ext:transfer_fee"
V_DELEGATE = "ext:permanent_delegate"
V_NONTRANSFERABLE = "ext:non_transferable"
V_FROZEN_DEFAULT = "ext:default_account_state_frozen"
V_PAUSABLE = "ext:pausable"
V_UNKNOWN_EXT = "ext:unknown"
CHAIN_ERROR = "chain_error"

# ---- PumpSwap Pool account (pump_amm IDL, same layout as tools/pump_structure_monitor.POOL_MAYHEM_OFFSET) ----------------------
POOL_DISCRIMINATOR = hashlib.sha256(b"account:Pool").digest()[:8]
POOL_BASE_MINT = 43
POOL_QUOTE_MINT = 75
POOL_BASE_VAULT = 139
POOL_QUOTE_VAULT = 171
POOL_COIN_CREATOR = 211
POOL_MAYHEM = M.POOL_MAYHEM_OFFSET  # 243
POOL_MIN_LEN = POOL_MAYHEM + 1


class ChainError(Exception):
    """An RPC or protocol failure. The text is a short code, never a URL, a key or an RPC body."""


class ChainParseError(ValueError):
    """Account bytes that do not parse. Always fails closed."""


# ======================================================================================================================
# small helpers
# ======================================================================================================================
_URL_RE = re.compile(r"https?://\S+")
_KEY_RE = re.compile(r"(api[-_]?key=)[^&\s]+", re.IGNORECASE)
_SAFE_KEY_RE = re.compile(r"[A-Za-z0-9_-]{8,128}")


def redact(text: object, extra: Iterable[str] = ()) -> str:
    out = str(text)
    for s in extra:
        if s:
            out = out.replace(s, "<redacted>")
    out = _KEY_RE.sub(r"\1<redacted>", out)
    return _URL_RE.sub("<url>", out)


def _utc_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, timezone.utc).strftime("%Y-%m-%d")


def _wall_ms() -> int:
    return int(time.time() * 1000)


def is_pubkey(text: object) -> bool:
    if not isinstance(text, str) or not 32 <= len(text) <= 44:
        return False
    try:
        return len(M.b58decode(text)) == 32 and M.b58encode(M.b58decode(text)) == text
    except (ValueError, IndexError):
        return False


def _opt_key(raw: bytes) -> str | None:
    """OptionalNonZeroPubkey: 32 zero bytes means None."""
    return None if raw == b"\0" * 32 else M.b58encode(raw)


def _coption(raw: bytes, off: int) -> str | None:
    """SPL COption<Pubkey>: u32 tag (0 or 1) then 32 bytes. Any other tag is a parse error."""
    tag = struct.unpack_from("<I", raw, off)[0]
    if tag == 0:
        return None
    if tag != 1:
        raise ChainParseError(f"bad COption tag {tag}")
    return M.b58encode(raw[off + 4: off + 36])


@functools.lru_cache(maxsize=65536)
def associated_token_address(owner: str, token_program: str, mint: str) -> str:
    """ATA = PDA([owner, token program, mint], ATA program)."""
    return M.find_program_address([M.b58decode(owner), M.b58decode(token_program), M.b58decode(mint)], ATA_PROGRAM)[0]


@functools.lru_cache(maxsize=65536)
def bonding_curve_pda(mint: str) -> str:
    """["bonding-curve", mint] under the pump program (same derivation as tools/synthetic_class.curve_pda_for_mint)."""
    return M.find_program_address([b"bonding-curve", M.b58decode(mint)], PUMP_PROGRAM)[0]


# ======================================================================================================================
# Token / Token-2022 mint parse (pure; no RPC)
# ======================================================================================================================
@dataclass(frozen=True)
class ParsedMint:
    token_program_id: str
    token_program: str  # "spl-token" | "token-2022"
    mint_authority: str | None
    freeze_authority: str | None
    supply_raw: int
    decimals: int
    extensions: tuple[str, ...]  # jsonParsed names, in TLV order
    ext_info: dict[str, Any]
    veto_reasons: tuple[str, ...]


def _parse_extension(ext_id: int, val: bytes) -> tuple[dict[str, Any], list[str]]:
    """Details for one extension value, and the veto reasons it carries."""
    name = EXT_NAMES[ext_id]
    vetoes: list[str] = []
    info: dict[str, Any] = {}
    if ext_id == 1:  # TransferFeeConfig: authority 32, withdraw authority 32, withheld u64, older fee 18, newer fee 18
        older = struct.unpack_from("<QQH", val, 72)  # epoch, maximum_fee, basis points
        newer = struct.unpack_from("<QQH", val, 90)
        info = {"config_authority": _opt_key(val[0:32]), "withdraw_authority": _opt_key(val[32:64]),
                "older_epoch": older[0], "older_max_fee": older[1], "older_bps": older[2],
                "newer_epoch": newer[0], "newer_max_fee": newer[1], "newer_bps": newer[2]}
        if older[2] > 0 or newer[2] > 0:  # either fee may be the one in force; the epoch is not known here
            vetoes.append(V_FEE)
    elif ext_id == 3:
        info = {"close_authority": _opt_key(val[0:32])}
    elif ext_id == 6:
        state = val[0]
        if state not in DEFAULT_ACCOUNT_STATE:
            raise ChainParseError(f"bad DefaultAccountState {state}")
        info = {"state": DEFAULT_ACCOUNT_STATE[state]}
        if state == 2:
            vetoes.append(V_FROZEN_DEFAULT)
    elif ext_id == 9:
        vetoes.append(V_NONTRANSFERABLE)
    elif ext_id == 12:
        info = {"delegate": _opt_key(val[0:32])}
        vetoes.append(V_DELEGATE)  # presence vetoes, even a null delegate: DESIGN lists the extension, not a value
    elif ext_id == 14:
        info = {"authority": _opt_key(val[0:32]), "program_id": _opt_key(val[32:64])}
        vetoes.append(V_HOOK)
    elif ext_id == 26:
        paused = val[32]
        if paused not in (0, 1):
            raise ChainParseError(f"bad Pausable flag {paused}")
        info = {"authority": _opt_key(val[0:32]), "paused": bool(paused)}
        vetoes.append(V_PAUSABLE)
    elif ext_id == 10:
        info = {"rate_authority": _opt_key(val[0:32]), "current_rate_bps": struct.unpack_from("<h", val, 50)[0]}
    elif ext_id == 18:
        info = {"authority": _opt_key(val[0:32]), "metadata_address": _opt_key(val[32:64])}
    elif ext_id == 25:
        info = {"authority": _opt_key(val[0:32]), "multiplier": struct.unpack_from("<d", val, 32)[0]}
    # 4, 16, 19, 20, 21, 22, 23, 24: present, recorded by name only
    info["name"] = name
    return info, vetoes


def parse_tlv(data: bytes) -> tuple[tuple[str, ...], dict[str, Any], tuple[str, ...]]:
    """Extensions of a Token-2022 mint, from the TLV region that starts at byte 166. Returns (names, info by name, veto reasons)."""
    names: list[str] = []
    info: dict[str, Any] = {}
    vetoes: list[str] = []
    seen: set[int] = set()
    i = TLV_START
    n = len(data)
    while i < n:
        if n - i < 4:
            if any(data[i:]):
                raise ChainParseError("trailing bytes after TLV")
            break
        ext_id, ln = struct.unpack_from("<HH", data, i)
        if ext_id == 0:  # Uninitialized terminates the list; the rest must be padding
            if any(data[i:]):
                raise ChainParseError("nonzero bytes after TLV terminator")
            break
        if i + 4 + ln > n:
            raise ChainParseError("TLV value runs past the account")
        if ext_id in seen:
            raise ChainParseError(f"repeated extension {ext_id}")
        seen.add(ext_id)
        val = data[i + 4: i + 4 + ln]
        if ext_id in ACCOUNT_ONLY_EXT:
            raise ChainParseError(f"account-only extension {ext_id} in a mint")
        if ext_id not in EXT_NAMES:
            names.append(f"unknown{ext_id}")
            info[f"unknown{ext_id}"] = {"name": f"unknown{ext_id}", "length": ln}
            vetoes.append(f"{V_UNKNOWN_EXT}:{ext_id}")  # a new extension might block a sell; fail closed
        else:
            want = EXT_FIXED_LEN.get(ext_id)
            if want is not None and ln != want:
                raise ChainParseError(f"extension {ext_id} length {ln}, expected {want}")
            ext_info, ext_vetoes = _parse_extension(ext_id, val)
            names.append(EXT_NAMES[ext_id])
            info[EXT_NAMES[ext_id]] = ext_info
            vetoes.extend(ext_vetoes)
        i += 4 + ln
    return tuple(names), info, tuple(vetoes)


def parse_mint_account(data: bytes, owner: str) -> ParsedMint:
    """Parse the raw data of a mint account owned by `owner`. Raises ChainParseError on anything unexpected."""
    if owner not in TOKEN_PROGRAM_NAMES:
        raise ChainParseError("owner is not a token program")
    if not isinstance(data, (bytes, bytearray)) or len(data) < MINT_LEN or len(data) > MAX_ACCOUNT_BYTES:
        raise ChainParseError("account data length is not a mint")
    data = bytes(data)
    if data[45] != 1:
        raise ChainParseError("mint is not initialized")
    mint_auth = _coption(data, 0)
    freeze_auth = _coption(data, 46)
    supply = struct.unpack_from("<Q", data, 36)[0]
    decimals = data[44]
    names: tuple[str, ...] = ()
    info: dict[str, Any] = {}
    ext_vetoes: tuple[str, ...] = ()
    if owner == SPL_TOKEN_PROGRAM:
        if len(data) != MINT_LEN:
            raise ChainParseError("spl-token mint is not 82 bytes")
    elif len(data) > MINT_LEN:  # Token-2022: an extended mint is padded to 165, then an account-type byte, then TLV
        if len(data) <= ACCOUNT_LEN or len(data) == MULTISIG_LEN:
            raise ChainParseError("token-2022 mint length is not a legal extended mint")
        if data[ACCOUNT_TYPE_OFFSET] != ACCOUNT_TYPE_MINT:
            raise ChainParseError("token-2022 account type is not Mint")
        if any(data[MINT_LEN:ACCOUNT_LEN]):
            raise ChainParseError("nonzero padding before the account type")
        names, info, ext_vetoes = parse_tlv(data)
    vetoes: list[str] = []
    if mint_auth is not None:
        vetoes.append(V_MINT_AUTH)
    if freeze_auth is not None:
        vetoes.append(V_FREEZE_AUTH)
    vetoes.extend(ext_vetoes)
    return ParsedMint(token_program_id=owner, token_program=TOKEN_PROGRAM_NAMES[owner], mint_authority=mint_auth,
                      freeze_authority=freeze_auth, supply_raw=supply, decimals=decimals, extensions=names, ext_info=info,
                      veto_reasons=tuple(vetoes))


# ======================================================================================================================
# PumpSwap Pool account (pure; no RPC)
# ======================================================================================================================
@dataclass(frozen=True)
class PoolInfo:
    base_mint: str
    quote_mint: str
    base_vault: str
    quote_vault: str
    coin_creator: str
    is_mayhem: bool


def parse_pool_account(data: bytes, owner: str) -> PoolInfo:
    if owner != PUMPSWAP_PROGRAM:
        raise ChainParseError("pool account is not owned by PumpSwap")
    if not isinstance(data, (bytes, bytearray)) or len(data) < POOL_MIN_LEN or len(data) > MAX_ACCOUNT_BYTES:
        raise ChainParseError("pool account length")
    data = bytes(data)
    if data[:8] != POOL_DISCRIMINATOR:
        raise ChainParseError("not a Pool account")
    flag = data[POOL_MAYHEM]
    if flag not in (0, 1):
        raise ChainParseError(f"bad mayhem byte {flag}")
    return PoolInfo(base_mint=M.b58encode(data[POOL_BASE_MINT: POOL_BASE_MINT + 32]),
                    quote_mint=M.b58encode(data[POOL_QUOTE_MINT: POOL_QUOTE_MINT + 32]),
                    base_vault=M.b58encode(data[POOL_BASE_VAULT: POOL_BASE_VAULT + 32]),
                    quote_vault=M.b58encode(data[POOL_QUOTE_VAULT: POOL_QUOTE_VAULT + 32]),
                    coin_creator=M.b58encode(data[POOL_COIN_CREATOR: POOL_COIN_CREATOR + 32]), is_mayhem=bool(flag))


# ======================================================================================================================
# token bucket, cache, credit counter
# ======================================================================================================================
class TokenBucket:
    """Reservation token bucket. `acquire` blocks until the tokens are there and returns the seconds waited. Thread-safe.

    A reservation may drive the balance negative; the next caller then waits behind it, so a burst of threads is served in order and the
    long-run rate never exceeds `rate_per_s`."""

    def __init__(self, rate_per_s: float, burst: float | None = None, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if not rate_per_s > 0:
            raise ValueError("rate_per_s must be positive")
        self.rate = float(rate_per_s)
        self.burst = float(burst if burst is not None else max(1.0, rate_per_s))
        if self.burst < 1.0:
            raise ValueError("burst must be at least 1")
        self._clock, self._sleep = clock, sleep
        self._tokens = self.burst
        self._t = clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.burst, self._tokens + (now - self._t) * self.rate)
        self._t = now

    def try_acquire(self, n: float = 1.0) -> bool:
        with self._lock:
            self._refill()
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False

    def acquire(self, n: float = 1.0) -> float:
        with self._lock:
            self._refill()
            self._tokens -= n
            wait = -self._tokens / self.rate if self._tokens < 0 else 0.0
        if wait > 0:
            self._sleep(wait)
        return wait


class TtlCache:
    """Small thread-safe LRU cache with a per-entry time to live."""

    def __init__(self, max_items: int = 20000, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.max_items = max_items
        self._clock = clock
        self._d: OrderedDict[Any, tuple[float, float, Any]] = OrderedDict()  # key -> (stored_at, expires_at, value)
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: Any, max_age_s: float | None = None) -> Any | None:
        """The value if present, not expired and (when given) no older than `max_age_s`; else None."""
        now = self._clock()
        with self._lock:
            item = self._d.get(key)
            if item is None:
                self.misses += 1
                return None
            stored, expires, value = item
            if now >= expires or (max_age_s is not None and now - stored > max_age_s):
                if now >= expires:
                    del self._d[key]
                self.misses += 1
                return None
            self._d.move_to_end(key)
            self.hits += 1
            return value

    def put(self, key: Any, value: Any, ttl_s: float) -> None:
        now = self._clock()
        with self._lock:
            self._d[key] = (now, now + ttl_s, value)
            self._d.move_to_end(key)
            while len(self._d) > self.max_items:
                self._d.popitem(last=False)

    def __len__(self) -> int:
        return len(self._d)


# Helius charges 1 credit for a standard read; confirm against the dashboard. Methods listed here override the default.
HELIUS_CREDITS: dict[str, int] = {}
HELIUS_DEFAULT_CREDITS = 1


class CreditCounter:
    """Calls (every attempt sent), Helius credits (calls x weight, 0 on a public endpoint), errors and cache hits, per method and UTC day."""

    def __init__(self, provider: str, *, now_ms: Callable[[], int] = _wall_ms) -> None:
        self.provider = provider
        self._now_ms = now_ms
        self._lock = threading.Lock()
        self.calls = 0
        self.credits = 0
        self.errors = 0
        self.cache_hits = 0
        self.throttle_wait_s = 0.0
        self.by_method: dict[str, int] = {}
        self.by_day: dict[str, int] = {}

    def calls_today(self) -> int:
        with self._lock:
            return self.by_day.get(_utc_day(self._now_ms()), 0)

    def record_call(self, method: str) -> None:
        with self._lock:
            self.calls += 1
            self.by_method[method] = self.by_method.get(method, 0) + 1
            day = _utc_day(self._now_ms())
            self.by_day[day] = self.by_day.get(day, 0) + 1
            if self.provider == "helius":
                self.credits += HELIUS_CREDITS.get(method, HELIUS_DEFAULT_CREDITS)

    def record_error(self) -> None:
        with self._lock:
            self.errors += 1

    def record_cache_hit(self) -> None:
        with self._lock:
            self.cache_hits += 1

    def record_wait(self, seconds: float) -> None:
        with self._lock:
            self.throttle_wait_s += seconds

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"schema": SCHEMA, "provider": self.provider, "calls": self.calls, "credits": self.credits, "errors": self.errors,
                    "cache_hits": self.cache_hits, "throttle_wait_s": round(self.throttle_wait_s, 3),
                    "by_method": dict(self.by_method), "by_day": dict(self.by_day)}


# ======================================================================================================================
# RPC
# ======================================================================================================================
def _read_helius_key(env: Mapping[str, str], env_files: Sequence[Path]) -> str:
    key = (env.get("HELIUS_API_KEY") or "").strip()
    if not key:
        for path in env_files:
            try:
                text = Path(path).read_text(encoding="utf-8")
            except OSError:
                continue
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, val = line.partition("=")
                if name.strip().removeprefix("export ").strip() == "HELIUS_API_KEY":
                    key = val.strip().strip("'\"")
            if key:
                break
    if not key or not _SAFE_KEY_RE.fullmatch(key):
        raise ChainError("helius_key_missing_or_malformed")
    return key


class RpcConfig:
    """Endpoint choice. The URL is private: `repr`, `str` and `provider` never show it, and an API key is never stored anywhere else."""

    __slots__ = ("_url", "provider", "_key")

    def __init__(self, url: str, provider: str, key: str = "") -> None:
        self._url = url
        self.provider = provider
        self._key = key

    def __repr__(self) -> str:
        return f"RpcConfig(provider={self.provider!r})"

    __str__ = __repr__

    @classmethod
    def public(cls) -> "RpcConfig":
        return cls(PUBLIC_RPC, "public")

    @classmethod
    def custom(cls, url: str) -> "RpcConfig":
        """Any other endpoint (tests, a private node). Treated as secret: it may carry a key."""
        return cls(url, "custom")

    @classmethod
    def helius(cls, env: Mapping[str, str] | None = None, env_files: Sequence[Path] | None = None) -> "RpcConfig":
        env = os.environ if env is None else env
        if env_files is None:
            explicit = (env.get("MAL_HELIUS_ENV_FILE") or "").strip()
            env_files = (Path(explicit),) if explicit else HELIUS_ENV_FILES
        key = _read_helius_key(env, env_files)
        return cls(f"https://mainnet.helius-rpc.com/?api-key={key}", "helius", key)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, env_files: Sequence[Path] | None = None) -> "RpcConfig":
        """Public unless MAL_HV4_RPC=helius."""
        env = os.environ if env is None else env
        if (env.get("MAL_HV4_RPC") or "public").strip().lower() == "helius":
            return cls.helius(env, env_files)
        return cls.public()

    def secrets(self) -> list[str]:
        return [self._url, self._key] if self.provider != "public" else []


def _http_post(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - https endpoint chosen by RpcConfig
        return json.loads(r.read())


# Paced calls per second. DESIGN section 6: about 40k standard calls a day at 2 rps. The public endpoint throttles heavy methods harder.
DEFAULT_RPS = {"public": 2.0, "helius": 2.0, "custom": 2.0}
DEFAULT_DAILY_CALL_CAP = 40_000
RETRY_HTTP = frozenset({429, 500, 502, 503, 504})
RETRY_RPC_CODES = frozenset({429, -32005})


class RpcClient:
    """One endpoint, paced by a token bucket, counted, capped per UTC day. `call` returns the JSON-RPC `result` or raises ChainError."""

    def __init__(self, config: RpcConfig | None = None, *, bucket: TokenBucket | None = None,
                 method_buckets: Mapping[str, TokenBucket] | None = None, counter: CreditCounter | None = None,
                 post: Callable[[str, dict[str, Any], float], dict[str, Any]] = _http_post, sleep: Callable[[float], None] = time.sleep,
                 now_ms: Callable[[], int] = _wall_ms, attempts: int = 3, timeout_s: float = 10.0, backoff_s: float = 1.0,
                 daily_call_cap: int = DEFAULT_DAILY_CALL_CAP) -> None:
        self.config = config or RpcConfig.public()
        self.provider = self.config.provider
        self.bucket = bucket or TokenBucket(DEFAULT_RPS.get(self.provider, 2.0), burst=2.0, clock=time.monotonic, sleep=sleep)
        self.method_buckets = dict(method_buckets or {})
        self.counter = counter or CreditCounter(self.provider, now_ms=now_ms)
        self._post, self._sleep, self._now_ms = post, sleep, now_ms
        self.attempts, self.timeout_s, self.backoff_s = max(1, attempts), timeout_s, backoff_s
        self.daily_call_cap = daily_call_cap
        self._id = 0
        self._id_lock = threading.Lock()

    def __repr__(self) -> str:
        return f"RpcClient(provider={self.provider!r}, calls={self.counter.calls})"

    def _pace(self, method: str) -> None:
        waited = self.bucket.acquire()
        mb = self.method_buckets.get(method)
        if mb is not None:
            waited += mb.acquire()
        if waited:
            self.counter.record_wait(waited)

    def call(self, method: str, params: list[Any]) -> Any:
        secrets = self.config.secrets()
        last = "rpc_failed"
        for attempt in range(self.attempts):
            if self.counter.calls_today() >= self.daily_call_cap:
                raise ChainError("daily_call_cap")
            self._pace(method)
            with self._id_lock:
                self._id += 1
                rid = self._id
            self.counter.record_call(method)
            retry = False
            try:
                resp = self._post(self.config._url, {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}, self.timeout_s)
            except urllib.error.HTTPError as e:
                last = f"http_{e.code}"
                retry = e.code in RETRY_HTTP
                ra = e.headers.get("Retry-After") if getattr(e, "headers", None) else None
                if retry and ra and ra.isdigit():
                    self._sleep(min(float(ra), 30.0))
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as e:
                last = f"network_{type(e).__name__}"
                retry = not isinstance(e, (json.JSONDecodeError, ValueError))
            except Exception as e:  # noqa: BLE001 - never leak the text of an unknown exception
                last = f"error_{type(e).__name__}"
            else:
                if not isinstance(resp, dict):
                    last = "malformed_response"
                elif resp.get("error") is not None:
                    err = resp["error"]
                    code = err.get("code") if isinstance(err, dict) else None
                    last = f"rpc_{code}"
                    retry = code in RETRY_RPC_CODES
                elif "result" not in resp:
                    last = "malformed_response"
                else:
                    log.debug("hv4_chain %s ok provider=%s", method, self.provider)
                    return resp["result"]
            self.counter.record_error()
            log.debug("hv4_chain %s failed provider=%s code=%s", method, self.provider, redact(last, secrets))
            if retry and attempt + 1 < self.attempts:
                self._sleep(self.backoff_s * (2 ** attempt))
                continue
            break
        raise ChainError(redact(last, secrets))


# ======================================================================================================================
# results
# ======================================================================================================================
@dataclass(frozen=True)
class MintFacts:
    mint: str
    ok: bool
    error: str | None
    token_program_id: str | None = None
    token_program: str | None = None
    mint_authority: str | None = None
    freeze_authority: str | None = None
    supply_raw: int | None = None
    decimals: int | None = None
    extensions: tuple[str, ...] = ()
    ext_info: dict[str, Any] = field(default_factory=dict)
    veto_reasons: tuple[str, ...] = ()
    data_sha256: str | None = None
    as_of_slot: int | None = None
    as_of_ms: int | None = None

    @property
    def vetoed(self) -> bool:
        """True on any veto reason, and on any chain failure (fail closed)."""
        return bool(self.veto_reasons)

    @property
    def allows_transfer(self) -> bool:
        """The token-program half of the sell-route check (DESIGN section 2): known token program, no extension that can block or tax a transfer."""
        return self.ok and not any(r.startswith("ext:") for r in self.veto_reasons)

    def to_card_fields(self) -> dict[str, Any]:
        return {"mint_authority": self.mint_authority, "freeze_authority": self.freeze_authority, "token_program": self.token_program,
                "extensions": list(self.extensions), "mint_veto_reasons": list(self.veto_reasons), "mint_facts_ok": self.ok,
                "mint_as_of_slot": self.as_of_slot, "mint_as_of_ms": self.as_of_ms}


def _failed_mint(mint: str, code: str, now_ms: int) -> MintFacts:
    return MintFacts(mint=mint, ok=False, error=code, veto_reasons=(f"{CHAIN_ERROR}:{code}",), as_of_ms=now_ms)


@dataclass(frozen=True)
class Holders:
    mint: str
    ok: bool
    error: str | None
    supply_raw: int | None = None
    top1_frac: float | None = None
    top10_frac: float | None = None
    excluded_frac: float | None = None  # excluded accounts seen in the top 20, as a share of supply
    n_listed: int = 0
    n_excluded: int = 0
    n_counted: int = 0
    listing_complete: bool = False  # fewer than 20 accounts came back, so the list is every non-empty account
    top10: tuple[tuple[str, int], ...] = ()  # (token account, raw amount) after exclusion
    as_of_slot: int | None = None
    as_of_ms: int | None = None

    def to_card_fields(self) -> dict[str, Any]:
        return {"top1_frac": self.top1_frac, "top10_frac": self.top10_frac, "holders_excluded_frac": self.excluded_frac,
                "holders_ok": self.ok, "holders_complete": self.listing_complete, "holders_as_of_slot": self.as_of_slot,
                "holders_as_of_ms": self.as_of_ms}


@dataclass(frozen=True)
class MayhemFlag:
    mint: str
    is_mayhem: bool | None  # None = unknown
    source: str  # "tape" | "pool_account" | "conflict" | "unknown"
    error: str | None = None
    as_of_slot: int | None = None
    as_of_ms: int | None = None

    @property
    def counts_as_mayhem(self) -> bool:
        """An unknown flag counts as Mayhem (DESIGN hard veto 3)."""
        return self.is_mayhem is not False

    def to_card_fields(self) -> dict[str, Any]:
        return {"mayhem": self.is_mayhem, "mayhem_source": self.source, "mayhem_counts": self.counts_as_mayhem,
                "mayhem_as_of_slot": self.as_of_slot, "mayhem_as_of_ms": self.as_of_ms}


def mayhem_veto(flag: MayhemFlag, age_s: float | None) -> bool:
    """DESIGN hard veto 3: Mayhem with age < 24 h. An unknown flag counts as Mayhem; an unknown age counts as young."""
    if not flag.counts_as_mayhem:
        return False
    return age_s is None or age_s < MAYHEM_VETO_AGE_S


# ======================================================================================================================
# the service
# ======================================================================================================================
# A clean mint cannot get worse (a revoked authority never comes back), so it is kept long. A vetoed mint is re-read in case an authority
# was revoked. Holders move with trading; supply barely moves.
TTL_MINT_CLEAN_S = 24 * 3600
TTL_MINT_VETOED_S = 900
TTL_HOLDERS_S = 900
TTL_SUPPLY_S = 3600
HOLDERS_MAX_AGE_S = 900
LARGEST_LIST_MAX = 20


def _decode_account_value(value: Any) -> tuple[bytes, str]:
    """(data, owner) from a getAccountInfo `value` in base64 encoding. Strict."""
    if not isinstance(value, dict):
        raise ChainParseError("no account value")
    data = value.get("data")
    owner = value.get("owner")
    if not (isinstance(data, list) and len(data) == 2 and data[1] == "base64" and isinstance(data[0], str)):
        raise ChainParseError("data is not base64")
    if not isinstance(owner, str) or not is_pubkey(owner):
        raise ChainParseError("owner is not a pubkey")
    try:
        raw = base64.b64decode(data[0], validate=True)
    except (binascii.Error, ValueError):
        raise ChainParseError("bad base64") from None
    return raw, owner


def _context_slot(result: Any) -> int:
    ctx = result.get("context") if isinstance(result, dict) else None
    slot = ctx.get("slot") if isinstance(ctx, dict) else None
    if not isinstance(slot, int) or isinstance(slot, bool) or slot < 0:
        raise ChainParseError("no context slot")
    return slot


def _uint_str(v: Any) -> int:
    if not isinstance(v, str) or not v.isdigit() or len(v) > 24:
        raise ChainParseError("bad amount")
    return int(v)


class HV4Chain:
    """mint_facts, largest_holders and mayhem_flag over one RpcClient, one cache and one credit counter."""

    def __init__(self, rpc: RpcClient | None = None, cache: TtlCache | None = None, *, commitment: str = "confirmed",
                 now_ms: Callable[[], int] = _wall_ms) -> None:
        self.rpc = rpc or RpcClient(RpcConfig.from_env(), now_ms=now_ms)
        self.cache = cache or TtlCache()
        self.commitment = commitment
        self._now_ms = now_ms

    @property
    def counter(self) -> CreditCounter:
        return self.rpc.counter

    def credit_report(self) -> dict[str, Any]:
        snap = self.rpc.counter.snapshot()
        snap["cache_entries"] = len(self.cache)
        snap["cache_hits_total"] = self.cache.hits
        snap["cache_misses_total"] = self.cache.misses
        return snap

    def _cached(self, key: Any, max_age_s: float | None = None) -> Any | None:
        hit = self.cache.get(key, max_age_s)
        if hit is not None:
            self.rpc.counter.record_cache_hit()
        return hit

    # ---- mint facts ----------------------------------------------------------------------------------------------------
    def mint_facts(self, mint: str, *, force: bool = False) -> MintFacts:
        key = ("mint", mint)
        if not force:
            hit = self._cached(key)
            if hit is not None:
                return hit
        now = self._now_ms()
        if not is_pubkey(mint):
            return _failed_mint(str(mint)[:44], "bad_mint_address", now)
        try:
            res = self.rpc.call("getAccountInfo", [mint, {"encoding": "base64", "commitment": self.commitment}])
            slot = _context_slot(res)
            value = res.get("value")
            if value is None:
                raise ChainParseError("account_not_found")
            data, owner = _decode_account_value(value)
            parsed = parse_mint_account(data, owner)
            facts = MintFacts(mint=mint, ok=True, error=None, token_program_id=parsed.token_program_id, token_program=parsed.token_program,
                              mint_authority=parsed.mint_authority, freeze_authority=parsed.freeze_authority, supply_raw=parsed.supply_raw,
                              decimals=parsed.decimals, extensions=parsed.extensions, ext_info=parsed.ext_info,
                              veto_reasons=parsed.veto_reasons, data_sha256=hashlib.sha256(data).hexdigest(), as_of_slot=slot,
                              as_of_ms=self._now_ms())
        except ChainError as e:
            return _failed_mint(mint, str(e), now)
        except ChainParseError as e:
            return _failed_mint(mint, f"parse:{redact(e)[:80]}", now)
        except Exception as e:  # noqa: BLE001 - fail closed on anything, keep only the class name
            return _failed_mint(mint, f"unexpected:{type(e).__name__}", now)
        self.cache.put(key, facts, TTL_MINT_VETOED_S if facts.vetoed else TTL_MINT_CLEAN_S)
        return facts

    # ---- supply (cached) -----------------------------------------------------------------------------------------------
    def _supply(self, mint: str, *, fresh: bool = False) -> int:
        key = ("supply", mint)
        if not fresh:
            hit = self._cached(key)
            if hit is not None:
                return hit
        res = self.rpc.call("getTokenSupply", [mint, {"commitment": self.commitment}])
        val = res.get("value") if isinstance(res, dict) else None
        supply = _uint_str(val.get("amount")) if isinstance(val, dict) else None
        if supply is None or supply <= 0:
            raise ChainParseError("bad supply")
        self.cache.put(key, supply, TTL_SUPPLY_S)
        return supply

    # ---- holders -------------------------------------------------------------------------------------------------------
    def excluded_accounts(self, mint: str, token_program: str, *, pool: str | None = None, pool_vault: str | None = None,
                          exclude_owners: Iterable[str] = (), exclude_accounts: Iterable[str] = ()) -> frozenset[str]:
        """Token accounts that are not holders: the pool vault, the bonding curve's vault, burn addresses, and anything the caller adds."""
        owners = {bonding_curve_pda(mint), *BURN_OWNERS, *exclude_owners}
        if pool:
            owners.add(pool)
        out = {associated_token_address(o, token_program, mint) for o in owners if is_pubkey(o)}
        out.update(a for a in (pool_vault, *exclude_accounts) if a)
        return frozenset(out)

    def largest_holders(self, mint: str, *, token_program: str | None = None, pool: str | None = None, pool_vault: str | None = None,
                        exclude_owners: Iterable[str] = (), exclude_accounts: Iterable[str] = (),
                        max_age_s: float = HOLDERS_MAX_AGE_S) -> Holders:
        """top1 and top10 share of total supply over the token accounts getTokenLargestAccounts lists (at most 20), after exclusion.

        Shares are per token account, not per owner. `max_age_s` 900 serves the 15-minute refresh from cache; pass 0 at card time.
        `token_program` defaults to the mint's own (one cached getAccountInfo)."""
        now = self._now_ms()
        if not is_pubkey(mint):
            return Holders(mint=str(mint)[:44], ok=False, error="bad_mint_address", as_of_ms=now)
        try:
            if token_program is None:
                facts = self.mint_facts(mint)
                if not facts.ok or facts.token_program_id is None:
                    raise ChainError(f"mint_facts:{facts.error}")
                token_program = facts.token_program_id
            if token_program not in TOKEN_PROGRAM_NAMES:
                raise ChainParseError("unknown token program")
            for extra in (pool, pool_vault, *exclude_owners, *exclude_accounts):
                if extra is not None and not is_pubkey(extra):
                    raise ChainParseError("bad exclusion address")
            excluded = self.excluded_accounts(mint, token_program, pool=pool, pool_vault=pool_vault, exclude_owners=exclude_owners,
                                              exclude_accounts=exclude_accounts)
            key = ("holders", mint, tuple(sorted(excluded)))
            if max_age_s > 0:  # 0 means a card-time read: always go to the chain
                hit = self._cached(key, max_age_s)
                if hit is not None:
                    return hit
            holders = self._holders_once(mint, excluded, now, fresh_supply=False)
            if holders.ok is False and holders.error == "inconsistent_supply":  # supply moved (a burn): read it again once
                holders = self._holders_once(mint, excluded, now, fresh_supply=True)
        except ChainError as e:
            return Holders(mint=mint, ok=False, error=str(e), as_of_ms=now)
        except ChainParseError as e:
            return Holders(mint=mint, ok=False, error=f"parse:{redact(e)[:80]}", as_of_ms=now)
        except Exception as e:  # noqa: BLE001
            return Holders(mint=mint, ok=False, error=f"unexpected:{type(e).__name__}", as_of_ms=now)
        if holders.ok:
            self.cache.put(key, holders, TTL_HOLDERS_S)
        return holders

    def _holders_once(self, mint: str, excluded: frozenset[str], now: int, *, fresh_supply: bool) -> Holders:
        supply = self._supply(mint, fresh=fresh_supply)
        res = self.rpc.call("getTokenLargestAccounts", [mint, {"commitment": self.commitment}])
        slot = _context_slot(res)
        value = res.get("value")
        if not isinstance(value, list) or len(value) > LARGEST_LIST_MAX:
            raise ChainParseError("bad holder list")
        rows: list[tuple[str, int]] = []
        seen: set[str] = set()
        for v in value:
            if not isinstance(v, dict) or not is_pubkey(v.get("address")) or v["address"] in seen:
                raise ChainParseError("bad holder row")
            seen.add(v["address"])
            rows.append((v["address"], _uint_str(v.get("amount"))))
        rows.sort(key=lambda r: (-r[1], r[0]))
        kept = [r for r in rows if r[0] not in excluded and r[1] > 0]
        out_amt = sum(a for addr, a in rows if addr in excluded)
        complete = len(rows) < LARGEST_LIST_MAX
        top10 = kept[:10]
        if sum(a for _, a in rows) > supply:
            return Holders(mint=mint, ok=False, error="inconsistent_supply", as_of_slot=slot, as_of_ms=now)
        if len(top10) < 10 and not complete:
            return Holders(mint=mint, ok=False, error="top10_incomplete", as_of_slot=slot, as_of_ms=now)
        return Holders(mint=mint, ok=True, error=None, supply_raw=supply, top1_frac=(top10[0][1] / supply) if top10 else 0.0,
                       top10_frac=sum(a for _, a in top10) / supply, excluded_frac=out_amt / supply, n_listed=len(rows),
                       n_excluded=sum(1 for r in rows if r[0] in excluded), n_counted=len(kept), listing_complete=complete,
                       top10=tuple(top10), as_of_slot=slot, as_of_ms=self._now_ms())

    # ---- Mayhem --------------------------------------------------------------------------------------------------------
    def mayhem_flag(self, mint: str, *, tape_is_mayhem: bool | None = None, pool: str | None = None, verify: bool = False) -> MayhemFlag:
        """The create row's `is_mayhem_mode` when the caller has it (strictly a bool), else the PumpSwap Pool account's byte 243.
        With `verify` the pool is read even when the tape has a value, and a disagreement is reported as True with source "conflict".
        No source gives is_mayhem None, which `counts_as_mayhem`."""
        now = self._now_ms()
        tape = tape_is_mayhem if isinstance(tape_is_mayhem, bool) else None
        if tape is True or (tape is False and not verify) or not pool:
            if tape is None:
                return MayhemFlag(mint=mint, is_mayhem=None, source="unknown", error="no_tape_flag_and_no_pool", as_of_ms=now)
            return MayhemFlag(mint=mint, is_mayhem=tape, source="tape", as_of_ms=now)
        try:
            if not is_pubkey(mint) or not is_pubkey(pool):
                raise ChainParseError("bad address")
            key = ("pool", pool)
            info = self._cached(key)
            slot = None
            if info is None:
                res = self.rpc.call("getAccountInfo", [pool, {"encoding": "base64", "commitment": self.commitment}])
                slot = _context_slot(res)
                if res.get("value") is None:
                    raise ChainParseError("pool_not_found")
                data, owner = _decode_account_value(res["value"])
                info = parse_pool_account(data, owner)
                self.cache.put(key, info, 10 * 365 * 24 * 3600)  # the flag is set at creation and never changes
            if info.base_mint != mint:
                raise ChainParseError("pool base mint is not this mint")
            if tape is not None and tape != info.is_mayhem:
                return MayhemFlag(mint=mint, is_mayhem=True, source="conflict", as_of_slot=slot, as_of_ms=self._now_ms())
            return MayhemFlag(mint=mint, is_mayhem=info.is_mayhem, source="pool_account", as_of_slot=slot, as_of_ms=self._now_ms())
        except ChainError as e:
            return MayhemFlag(mint=mint, is_mayhem=tape if tape is not None else None, source="tape" if tape is not None else "unknown",
                              error=str(e), as_of_ms=now)
        except ChainParseError as e:
            return MayhemFlag(mint=mint, is_mayhem=tape if tape is not None else None, source="tape" if tape is not None else "unknown",
                              error=f"parse:{redact(e)[:80]}", as_of_ms=now)
        except Exception as e:  # noqa: BLE001
            return MayhemFlag(mint=mint, is_mayhem=tape if tape is not None else None, source="tape" if tape is not None else "unknown",
                              error=f"unexpected:{type(e).__name__}", as_of_ms=now)
