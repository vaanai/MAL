"""Helius preprocessed listeners for mal-fast-0.

Mint-authority creates stay on one account. Each new mint's bonding curve
is subscribed for its first 120 seconds. The pump global withdraw account
is included for graduations. Program ids are refused. Nothing here invents
fill amounts: Buy and Sell rows come from ``observe.trade_decode`` when a
log payload is present. Preprocessed frames carry the signed transaction
only, so ``sol_lamports`` and reserves are not taken from instruction limits.
"""

from __future__ import annotations

import json
import os
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

MINT_AUTHORITY = "TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM"
# Global withdraw authority. 1,000 signatures spanned 11.7h (1.42/min):
# successes are Migrate / MigrateV2, ordinary later buys on a sampled mint
# did not include it. One failed BuyV2 and a failed-tx share keep it off the
# create socket; it is still one account, not the trade firehose.
MIGRATION_ACCOUNT = "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
HELIUS_HOST = "wss://beta.helius-rpc.com"
PUBLIC_WS = "wss://api.mainnet-beta.solana.com"
ACCOUNT_CAP = 5_000
CREATE_DAILY_CAP = 10_000
TRADE_DAILY_CAP = 100_000
PROBE_CREDIT_TRIP = 20_000
CURVE_TTL_S = 120.0
PRE_CREDITS_PER_MESSAGE = 0.1
CREATE_RATE_MAX = 100
TRADE_RATE_MAX = 2_000
RATE_WINDOW_S = 10.0
KEEP_CREDITS_PER_DAY = 100_000
KEEP_COVERAGE = 0.95

PROGRAM_WIDE_DENY = frozenset(
    {
        PUMP_PROGRAM,
        "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
        "11111111111111111111111111111111",
        "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
        "ComputeBudget111111111111111111111111111111",
    }
)

_B58 = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

# sha256("global:<name>")[:8]
_DISC = {
    bytes.fromhex("181ec828051c0777"): "create",
    bytes.fromhex("d6904cec5f8b31b4"): "create_v2",
    bytes.fromhex("66063d1201daebea"): "buy",
    bytes.fromhex("b817ee6167c5d33d"): "buy_v2",
    bytes.fromhex("38fc74089edfcd5f"): "buy_exact_sol_in",
    bytes.fromhex("33e685a4017f83ad"): "sell",
    bytes.fromhex("5df6823ce7e940b2"): "sell_v2",
    bytes.fromhex("9beae792ec9ea21e"): "migrate",
    bytes.fromhex("bbcb121fceedfe29"): "migrate_v2",
}

# Instruction account positions: (side or kind, mint index, curve index).
_LAYOUT: dict[str, tuple[str, int, int]] = {
    "create": ("create", 0, 2),
    "create_v2": ("create", 0, 2),
    "buy": ("buy", 2, 3),
    "buy_v2": ("buy", 1, 10),
    "buy_exact_sol_in": ("buy", 2, 3),
    "sell": ("sell", 2, 3),
    "sell_v2": ("sell", 1, 10),
    "migrate": ("migrate", 2, 3),
    "migrate_v2": ("migrate", 2, 4),
}

_TRADE_KINDS = frozenset({"buy", "sell"})
ALT_META_LEN = 56


def b58encode(data: bytes) -> str:
    n_zeros = len(data) - len(data.lstrip(b"\x00"))
    val = int.from_bytes(data, "big")
    enc = bytearray()
    while val:
        val, mod = divmod(val, 58)
        enc.append(_B58[mod])
    enc.extend(_B58[0:1] * n_zeros)
    return bytes(reversed(enc)).decode("ascii")


def b58decode(text: str) -> bytes:
    val = 0
    for char in text.encode("ascii"):
        val = val * 58 + _B58.index(char)
    n_zeros = len(text) - len(text.lstrip("1"))
    body = val.to_bytes((val.bit_length() + 7) // 8, "big") if val else b""
    out = b"\x00" * n_zeros + body
    if len(out) < 32:
        out = b"\x00" * (32 - len(out)) + out
    return out


def redact(text: str) -> str:
    key = os.environ.get("HELIUS_API_KEY") or ""
    out = text
    if key:
        out = out.replace(key, "REDACTED")
    marker = "api-key="
    if marker in out:
        head, _, tail = out.partition(marker)
        rest = tail.split("&", 1)
        tail_out = f"&{rest[1]}" if len(rest) > 1 else ""
        out = f"{head}{marker}REDACTED{tail_out}"
    return out[:300]


def helius_ws_url(api_key: str, host: str = HELIUS_HOST) -> str:
    if not api_key:
        raise ValueError("missing HELIUS_API_KEY")
    return f"{host.rstrip('/')}/?api-key={api_key}"


def load_env_file(path: Path) -> bool:
    """Load HELIUS_API_KEY from a mode-600 file. Never logs the value."""
    if os.environ.get("HELIUS_API_KEY"):
        return True
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise PermissionError("helius env file must be mode 600")
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() != "HELIUS_API_KEY":
            continue
        secret = value.strip().strip('"').strip("'")
        if secret:
            os.environ["HELIUS_API_KEY"] = secret
            return True
    return False


def read_compact_u16(buf: bytes, off: int) -> tuple[int, int]:
    value = 0
    shift = 0
    for _ in range(3):
        if off >= len(buf):
            raise ValueError("truncated compact-u16")
        byte = buf[off]
        off += 1
        value |= (byte & 0x7F) << shift
        if byte & 0x80 == 0:
            return value, off
        shift += 7
    raise ValueError("compact-u16 too long")


def _v1_config_len(mask: int) -> int:
    """Bytes of config values. Bits 0 and 1 together are one u64; later bits are u32."""
    if mask & 0x3 and (mask & 0x3) != 0x3:
        raise ValueError("bad v1 priority mask")
    size = 8 if mask & 0x3 else 0
    bits = mask >> 2
    while bits:
        if bits & 1:
            size += 4
        bits >>= 1
    return size


def _parse_v1_transaction(raw: bytes) -> dict[str, Any]:
    """SIMD-0385 transaction. Version byte 0x81, signatures at the tail."""
    if len(raw) < 1 + 3 + 4 + 32 + 2 or raw[0] != 0x81:
        raise ValueError("not a v1 transaction")
    off = 1
    nreq = raw[off]
    off += 3
    mask = int.from_bytes(raw[off : off + 4], "little")
    off += 4
    off += 32
    nins = raw[off]
    naddr = raw[off + 1]
    off += 2
    if nreq < 1 or nreq > 16 or naddr < 1 or naddr > 64 or nins > 64:
        raise ValueError("bad v1 counts")
    if off + 32 * naddr > len(raw):
        raise ValueError("truncated v1 keys")
    keys = []
    for _ in range(naddr):
        keys.append(b58encode(raw[off : off + 32]))
        off += 32
    off += _v1_config_len(mask)
    if off + 4 * nins > len(raw):
        raise ValueError("truncated v1 headers")
    headers: list[tuple[int, int, int]] = []
    for _ in range(nins):
        headers.append((raw[off], raw[off + 1], int.from_bytes(raw[off + 2 : off + 4], "little")))
        off += 4
    instructions: list[dict[str, Any]] = []
    for program_index, nacc, ndata in headers:
        if off + nacc + ndata > len(raw):
            raise ValueError("truncated v1 payload")
        accounts = list(raw[off : off + nacc])
        off += nacc
        data = raw[off : off + ndata]
        off += ndata
        instructions.append(
            {"program_index": program_index, "accounts": accounts, "data": data}
        )
    if off + 64 * nreq != len(raw):
        raise ValueError("bad v1 signatures")
    signatures = [b58encode(raw[off + 64 * i : off + 64 * (i + 1)]) for i in range(nreq)]
    return {
        "signatures": signatures,
        "version": 1,
        "keys": keys,
        "instructions": instructions,
        "lookups": [],
    }


def parse_wire_transaction(raw: bytes) -> dict[str, Any]:
    """Signed transaction bytes (legacy, v0, or v1)."""
    if not raw:
        raise ValueError("empty transaction")
    if raw[0] == 0x81:
        return _parse_v1_transaction(raw)
    nsig, off = read_compact_u16(raw, 0)
    if nsig < 1 or nsig > 16 or off + 64 * nsig > len(raw):
        raise ValueError("bad signature section")
    signatures = [b58encode(raw[off + 64 * i : off + 64 * (i + 1)]) for i in range(nsig)]
    off += 64 * nsig
    msg = raw[off:]
    if not msg:
        raise ValueError("missing message")
    moff = 0
    if msg[0] & 0x80:
        version: int | None = msg[0] & 0x7F
        moff = 1
    else:
        version = None
    if moff + 3 > len(msg):
        raise ValueError("truncated header")
    moff += 3
    nkeys, moff = read_compact_u16(msg, moff)
    if nkeys < 1 or nkeys > 128 or moff + 32 * nkeys + 32 > len(msg):
        raise ValueError("bad account keys")
    keys = []
    for _ in range(nkeys):
        keys.append(b58encode(msg[moff : moff + 32]))
        moff += 32
    moff += 32
    nins, moff = read_compact_u16(msg, moff)
    instructions: list[dict[str, Any]] = []
    for _ in range(nins):
        if moff >= len(msg):
            raise ValueError("truncated instruction")
        program_index = msg[moff]
        moff += 1
        nacc, moff = read_compact_u16(msg, moff)
        if moff + nacc > len(msg):
            raise ValueError("truncated instruction accounts")
        accounts = list(msg[moff : moff + nacc])
        moff += nacc
        ndata, moff = read_compact_u16(msg, moff)
        if moff + ndata > len(msg):
            raise ValueError("truncated instruction data")
        data = msg[moff : moff + ndata]
        moff += ndata
        instructions.append(
            {"program_index": program_index, "accounts": accounts, "data": data}
        )
    lookups: list[dict[str, Any]] = []
    if version is not None and moff < len(msg):
        nlook, moff = read_compact_u16(msg, moff)
        for _ in range(nlook):
            if moff + 32 > len(msg):
                raise ValueError("truncated lookup")
            table = b58encode(msg[moff : moff + 32])
            moff += 32
            nwrite, moff = read_compact_u16(msg, moff)
            writable = list(msg[moff : moff + nwrite])
            moff += nwrite
            nread, moff = read_compact_u16(msg, moff)
            readonly = list(msg[moff : moff + nread])
            moff += nread
            lookups.append({"table": table, "writable": writable, "readonly": readonly})
    return {
        "signatures": signatures,
        "version": version,
        "keys": keys,
        "instructions": instructions,
        "lookups": lookups,
    }


def parse_preprocessed_frame(frame: bytes) -> dict[str, Any] | None:
    """Helius binary frame: version u8, slot u64 LE, signature, wire tx."""
    if len(frame) < 73 or frame[0] != 1:
        return None
    slot = int.from_bytes(frame[1:9], "little")
    signature = b58encode(frame[9:73])
    return {"slot": slot, "signature": signature, "wire": frame[73:]}


def addresses_from_lookup_table(data: bytes) -> list[str]:
    if len(data) < ALT_META_LEN:
        return []
    body = data[ALT_META_LEN:]
    count = len(body) // 32
    return [b58encode(body[i * 32 : (i + 1) * 32]) for i in range(count)]


def loaded_account_keys(
    lookups: Sequence[Mapping[str, Any]],
    tables: Mapping[str, Sequence[str]],
) -> list[str] | None:
    """Writable lookups first, then readonly. None if a table is missing."""
    writable: list[str] = []
    readonly: list[str] = []
    for look in lookups:
        table = tables.get(str(look["table"]))
        if table is None:
            return None
        for index in look["writable"]:
            if index >= len(table):
                return None
            writable.append(table[index])
        for index in look["readonly"]:
            if index >= len(table):
                return None
            readonly.append(table[index])
    return writable + readonly


def account_at(keys: Sequence[str], loaded: Sequence[str], index: int) -> str | None:
    if index < len(keys):
        return keys[index]
    loaded_index = index - len(keys)
    if loaded_index < len(loaded):
        return loaded[loaded_index]
    return None


def pump_actions(tx: Mapping[str, Any], loaded: Sequence[str]) -> list[dict[str, Any]]:
    """Buy, sell, create, and migrate instructions. No fill amounts."""
    keys = tx["keys"]
    actions: list[dict[str, Any]] = []
    trade_index = 0
    for ix in tx["instructions"]:
        program = account_at(keys, loaded, int(ix["program_index"]))
        if program != PUMP_PROGRAM:
            continue
        data = ix["data"]
        if not isinstance(data, (bytes, bytearray)) or len(data) < 8:
            continue
        name = _DISC.get(bytes(data[:8]))
        if name is None:
            continue
        kind, mint_i, curve_i = _LAYOUT[name]
        accounts = ix["accounts"]
        mint = account_at(keys, loaded, accounts[mint_i]) if mint_i < len(accounts) else None
        curve = account_at(keys, loaded, accounts[curve_i]) if curve_i < len(accounts) else None
        action: dict[str, Any] = {
            "instr": name,
            "kind": kind,
            "mint": mint,
            "bonding_curve": curve,
        }
        if kind in _TRADE_KINDS:
            action["ix_event_index"] = trade_index
            trade_index += 1
        actions.append(action)
    return actions


def account_filter_decision(accounts: Sequence[str], cap: int = ACCOUNT_CAP) -> tuple[bool, str]:
    cleaned: list[str] = []
    for raw in accounts:
        if not isinstance(raw, str):
            continue
        account = raw.strip()
        if not account or account in cleaned:
            continue
        cleaned.append(account)
    if not cleaned:
        return False, "empty"
    if len(cleaned) > cap:
        return False, "over_account_cap"
    if any(account in PROGRAM_WIDE_DENY for account in cleaned):
        return False, "program_wide"
    return True, "ok"


def preprocessed_subscribe_request(accounts: Sequence[str], req_id: int) -> dict[str, Any]:
    ok, reason = account_filter_decision(accounts)
    if not ok:
        raise ValueError(reason)
    cleaned: list[str] = []
    for raw in accounts:
        account = raw.strip()
        if account and account not in cleaned:
            cleaned.append(account)
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "preprocessedSubscribe",
        "params": {
            "accountInclude": cleaned,
            "accountExclude": [],
            "accountRequired": [],
        },
    }


def preprocessed_unsubscribe_request(sub_id: int, req_id: int) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "preprocessedUnsubscribe",
        "params": [sub_id],
    }


def logs_subscribe_request(account: str, req_id: int, commitment: str = "processed") -> dict[str, Any]:
    ok, reason = account_filter_decision([account])
    if not ok:
        raise ValueError(reason)
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "logsSubscribe",
        "params": [{"mentions": [account]}, {"commitment": commitment}],
    }


def logs_unsubscribe_request(sub_id: int, req_id: int) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "logsUnsubscribe",
        "params": [sub_id],
    }


class CreditMeter:
    """0.1 credit per preprocessed delivery. ``trip`` 0 disables the credit stop."""

    def __init__(
        self,
        trip: float = PROBE_CREDIT_TRIP,
        rate_max: int = TRADE_RATE_MAX,
        rate_window_s: float = RATE_WINDOW_S,
    ) -> None:
        if trip < 0:
            raise ValueError("trip must be >= 0")
        self.trip = float(trip)
        self.rate_max = rate_max
        self.rate_window_s = rate_window_s
        self.credits = 0.0
        self.messages = 0
        self.tripped = False
        self.reason = ""
        self._recent: list[float] = []

    def note(self, now: float) -> bool:
        self.messages += 1
        self.credits += PRE_CREDITS_PER_MESSAGE
        self._recent.append(now)
        cutoff = now - self.rate_window_s
        while self._recent and self._recent[0] < cutoff:
            self._recent.pop(0)
        if self.trip > 0 and self.credits >= self.trip - 1e-6:
            self.tripped = True
            self.reason = "credit_trip"
        elif len(self._recent) > self.rate_max:
            self.tripped = True
            self.reason = "rate_trip"
        return self.tripped

    def snapshot(self) -> dict[str, Any]:
        return {
            "credits": round(self.credits, 4),
            "messages": self.messages,
            "tripped": self.tripped,
            "reason": self.reason,
        }


class DailyCreditGate:
    """Persisted UTC-day cap. A trip stays shut until the next UTC date."""

    def __init__(
        self,
        path: Path,
        cap: float,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if cap <= 0:
            raise ValueError("cap must be > 0")
        self.path = path
        self.cap = float(cap)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.utc_date = ""
        self.credits = 0.0
        self.tripped = False
        self.reason = ""
        self.load()

    def _today(self) -> str:
        return self.clock().strftime("%Y-%m-%d")

    def load(self) -> None:
        today = self._today()
        self.utc_date = today
        self.credits = 0.0
        self.tripped = False
        self.reason = ""
        if not self.path.is_file():
            self.save()
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.save()
            return
        if not isinstance(payload, dict) or payload.get("utc_date") != today:
            self.save()
            return
        self.credits = float(payload.get("credits") or 0)
        self.tripped = bool(payload.get("tripped"))
        self.reason = str(payload.get("reason") or "")
        if self.credits >= self.cap - 1e-6:
            self.tripped = True
            self.reason = self.reason or "credit_trip"

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(
            {
                "utc_date": self.utc_date,
                "credits": round(self.credits, 4),
                "tripped": self.tripped,
                "reason": self.reason,
                "cap": self.cap,
            },
            separators=(",", ":"),
        )
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, body.encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)

    def note(self, now_s: float | None = None) -> bool:
        del now_s
        if self._today() != self.utc_date:
            self.utc_date = self._today()
            self.credits = 0.0
            self.tripped = False
            self.reason = ""
        if self.tripped:
            return True
        self.credits += PRE_CREDITS_PER_MESSAGE
        if self.credits >= self.cap - 1e-6:
            self.tripped = True
            self.reason = "credit_trip"
        self.save()
        return self.tripped

    def hold(self, reason: str) -> None:
        self.tripped = True
        self.reason = reason
        self.save()


def seconds_until_next_utc_day(now: datetime | None = None) -> float:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    nxt = (moment + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(0.0, (nxt - moment).total_seconds())


class CurveBook:
    """Permanent accounts plus bonding curves for ``ttl_s`` after first sight."""

    def __init__(
        self,
        permanent: Sequence[str] = (),
        cap: int = ACCOUNT_CAP,
        ttl_s: float = CURVE_TTL_S,
    ) -> None:
        if cap < 1:
            raise ValueError("cap must be >= 1")
        self.cap = cap
        self.ttl_s = ttl_s
        self.permanent: list[str] = []
        for raw in permanent:
            account = raw.strip()
            if not account or account in self.permanent:
                continue
            if account in PROGRAM_WIDE_DENY:
                raise ValueError("program_wide")
            self.permanent.append(account)
        if len(self.permanent) > cap:
            raise ValueError("over_account_cap")
        self.curves: dict[str, tuple[str, float]] = {}

    def add(self, curve: str, mint: str, now: float) -> str:
        curve = curve.strip()
        mint = mint.strip()
        if not curve or not mint or curve in PROGRAM_WIDE_DENY or mint in PROGRAM_WIDE_DENY:
            return "rejected"
        if curve in self.curves or curve in self.permanent:
            return "present"
        if len(self.accounts()) >= self.cap:
            return "cap"
        self.curves[curve] = (mint, now + self.ttl_s)
        return "added"

    def expire(self, now: float) -> list[str]:
        dead = [curve for curve, (_, exp) in self.curves.items() if exp <= now]
        for curve in dead:
            del self.curves[curve]
        return dead

    def accounts(self) -> list[str]:
        out = list(self.permanent)
        for curve in self.curves:
            if curve not in out:
                out.append(curve)
        return out

    def curve_to_mint(self) -> dict[str, str]:
        return {curve: mint for curve, (mint, _) in self.curves.items()}


def seen_records(
    signature: str,
    slot: int,
    t_recv_ms: int,
    actions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Identity rows. Instruction limits are not copied into sol amounts."""
    trades = [action for action in actions if action.get("kind") in _TRADE_KINDS]
    if not trades:
        first = actions[0] if actions else None
        return [
            {
                "schema": "fast_early_seen_v0",
                "signature": signature,
                "slot": slot,
                "t_recv_ms": t_recv_ms,
                "side": None,
                "mint": None if first is None else first.get("mint"),
                "bonding_curve": None if first is None else first.get("bonding_curve"),
                "ix_event_index": None,
                "instr": None if first is None else first.get("instr"),
            }
        ]
    rows = []
    for action in trades:
        rows.append(
            {
                "schema": "fast_early_seen_v0",
                "signature": signature,
                "slot": slot,
                "t_recv_ms": t_recv_ms,
                "side": action.get("kind"),
                "mint": action.get("mint"),
                "bonding_curve": action.get("bonding_curve"),
                "ix_event_index": action.get("ix_event_index"),
                "instr": action.get("instr"),
            }
        )
    return rows


def graduation_records(
    signature: str,
    slot: int,
    t_recv_ms: int,
    actions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for action in actions:
        if action.get("kind") != "migrate":
            continue
        rows.append(
            {
                "schema": "fast_graduation_v0",
                "signature": signature,
                "slot": slot,
                "t_recv_ms": t_recv_ms,
                "mint": action.get("mint"),
                "bonding_curve": action.get("bonding_curve"),
                "instr": action.get("instr"),
            }
        )
    return rows


def create_record(
    signature: str,
    slot: int,
    t_recv_ms: int,
    actions: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    for action in actions:
        if action.get("kind") != "create":
            continue
        mint = action.get("mint")
        curve = action.get("bonding_curve")
        if not isinstance(mint, str) or not isinstance(curve, str):
            return None
        return {
            "schema": "fast_pre_create_v0",
            "source": "helius_preprocessed",
            "stream": "preprocessedSubscribe",
            "mint": mint,
            "bonding_curve": curve,
            "slot": slot,
            "signature": signature,
            "t_recv_ms": t_recv_ms,
            "instr": action.get("instr"),
        }
    return None


def mints_touched(
    actions: Sequence[Mapping[str, Any]],
    keys: Sequence[str],
    curve_to_mint: Mapping[str, str],
) -> list[str]:
    found: list[str] = []
    for action in actions:
        curve = action.get("bonding_curve")
        mint = curve_to_mint.get(curve) if isinstance(curve, str) else None
        if mint and mint not in found:
            found.append(mint)
    for key in keys:
        mint = curve_to_mint.get(key)
        if mint and mint not in found:
            found.append(mint)
    return found


def sealed_trades_from_logs(
    logs: Sequence[str],
    *,
    slot: int,
    signature: str,
    t_recv_ms: int,
    commitment: str = "processed",
    feed: str = "fast_early_trade",
) -> list[dict[str, Any]]:
    """Tape rows from real Program data. Empty when the frame has no logs."""
    from observe.trade_decode import records_from_logs

    return records_from_logs(
        logs,
        slot=slot,
        signature=signature,
        t_recv_ms=t_recv_ms,
        commitment=commitment,
        feed=feed,
    )


def stamp_fast_recv(row: Mapping[str, Any], t_pre_ms: int, slot: int | None = None) -> dict[str, Any]:
    """Keep decoder economics. Replace receive time with the preprocessed stamp."""
    from observe.trade_decode import iso_from_ms

    out = dict(row)
    out["t_logs_ms"] = out.get("t_recv_ms")
    out["t_recv_ms"] = int(t_pre_ms)
    out["t_recv"] = iso_from_ms(int(t_pre_ms))
    out["t_recv_source"] = "preprocessed"
    if slot is not None:
        out["slot"] = int(slot)
    return out


def parse_logs_notice(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    if payload.get("method") != "logsNotification":
        return None
    params = payload.get("params")
    if not isinstance(params, dict):
        return None
    result = params.get("result")
    if not isinstance(result, dict):
        return None
    value = result.get("value") if isinstance(result.get("value"), dict) else {}
    signature = value.get("signature")
    if not isinstance(signature, str):
        return None
    logs = value.get("logs")
    lines = [line for line in logs if isinstance(line, str)] if isinstance(logs, list) else []
    context = result.get("context") if isinstance(result.get("context"), dict) else {}
    slot = context.get("slot")
    slot_out = slot if isinstance(slot, int) and not isinstance(slot, bool) else None
    return {
        "signature": signature,
        "logs": lines,
        "slot": slot_out,
        "err": value.get("err"),
        "subscription": params.get("subscription"),
    }


def project_daily_credits(credits: float, elapsed_s: float) -> float:
    if elapsed_s <= 0:
        raise ValueError("elapsed_s must be > 0")
    return float(credits) * 86400.0 / float(elapsed_s)


def credits_per_mint_summary(messages_by_mint: Mapping[str, int]) -> dict[str, Any]:
    vals = [count * PRE_CREDITS_PER_MESSAGE for count in messages_by_mint.values() if count > 0]
    if not vals:
        return {"mints": 0, "mean_credits": None, "median_credits": None}
    return {
        "mints": len(vals),
        "mean_credits": sum(vals) / len(vals),
        "median_credits": float(statistics.median(vals)),
    }


def _first_times(rows: Sequence[Mapping[str, Any]], key: str = "signature") -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        sig = row.get(key)
        t_recv = row.get("t_recv_ms")
        if not isinstance(sig, str) or not isinstance(t_recv, int) or isinstance(t_recv, bool):
            continue
        if sig not in out or t_recv < out[sig]:
            out[sig] = t_recv
    return out


def trade_coverage(
    creates: Sequence[Mapping[str, Any]],
    seen: Sequence[Mapping[str, Any]] | Mapping[str, int],
    tape: Sequence[Mapping[str, Any]],
    *,
    ttl_ms: int = 120_000,
    skew_ms: int = 2_000,
    max_abs_delta_ms: int = 5_000,
) -> dict[str, Any]:
    """Signature coverage of tape prints in the first 120s after each create.

    ``later_trades`` drops the create transaction itself. That signature is
    already on the mint-authority socket before the curve subscription exists.
    """
    windows: dict[str, list[int]] = {}
    create_sigs: set[str] = set()
    for row in creates:
        mint = row.get("mint")
        t0 = row.get("t_recv_ms")
        sig = row.get("signature")
        if not isinstance(mint, str) or not isinstance(t0, int) or isinstance(t0, bool):
            continue
        windows.setdefault(mint, []).append(t0)
        if isinstance(sig, str):
            create_sigs.add(sig)
    if isinstance(seen, Mapping):
        seen_map = {str(sig): int(t_recv) for sig, t_recv in seen.items()}
    else:
        seen_map = _first_times(seen)

    def collect(drop_create: bool) -> dict[str, Any]:
        oracle: dict[str, int] = {}
        for row in tape:
            mint = row.get("mint")
            sig = row.get("signature")
            t_recv = row.get("t_recv_ms")
            if not isinstance(mint, str) or not isinstance(sig, str):
                continue
            if not isinstance(t_recv, int) or isinstance(t_recv, bool):
                continue
            if drop_create and sig in create_sigs:
                continue
            slots = windows.get(mint)
            if not slots:
                continue
            if not any((t0 - skew_ms) <= t_recv <= (t0 + ttl_ms) for t0 in slots):
                continue
            if sig not in oracle or t_recv < oracle[sig]:
                oracle[sig] = t_recv
        deltas: list[int] = []
        covered = 0
        for sig, t_oracle in oracle.items():
            t_fast = seen_map.get(sig)
            if t_fast is None:
                continue
            covered += 1
            delta = t_oracle - t_fast
            if abs(delta) <= max_abs_delta_ms:
                deltas.append(delta)
        n_oracle = len(oracle)
        return {
            "oracle_signatures": n_oracle,
            "covered": covered,
            "coverage": (covered / n_oracle) if n_oracle else None,
            "paired": len(deltas),
            "median_oracle_minus_fast_ms": float(statistics.median(deltas)) if deltas else None,
        }

    return {"later_trades": collect(True), "including_create_tx": collect(False)}


def keep_early_trade(credits_per_day: float, coverage: float | None) -> bool:
    if coverage is None:
        return False
    return credits_per_day <= KEEP_CREDITS_PER_DAY and coverage >= KEEP_COVERAGE


def append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, separators=(",", ":"), ensure_ascii=False, default=str)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()


def day_path(directory: Path, stem: str, now: datetime | None = None) -> Path:
    moment = now or datetime.now(timezone.utc)
    return directory / f"{stem}-{moment.strftime('%Y-%m-%d')}.jsonl"
