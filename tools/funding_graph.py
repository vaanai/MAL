#!/usr/bin/env python3
"""Creator and early-buyer funding graph for paper rug vetoes.

Public Solana JSON-RPC at 1 request/s by default. If HELIUS_API_KEY is set,
or appears in the backfill env file, calls move to Helius at 5 requests/s.
The live trade tape is not this process: stay under that cap and stop cold
on HTTP 429.

Each wallet is resolved once. Rows are append-only JSONL. A later feature
join may use a row only when its first_seen_ms is at or before the decision.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

# Same bot gate as LAYA v0. Imported values are checked in the unit test.
BOT_MIN_BUYS = 25
BOT_MIN_MINTS = 8
BOT_SNIPER_SHARE = 0.45
BOT_MEAN_GAP_MS = 2_000
BOT_MIN_GAPS = 20
SNIPER_SLOT_DELTA = 2

EARLY_BUYERS = 4
CLUSTER_HORIZON_MS = 30_000
CLUSTER_RUG_RATIO = 0.5
FRESH_MAX_S = 3600
MAX_SIGNATURE_PAGES = 3
SIGNATURE_PAGE = 1000
OLDEST_TX_INSPECT = 5
CREATOR_PRIORITY = 0
BUYER_PRIORITY = 1
BACKFILL_PRIORITY = 2
# Public RPC is shared with the trade tape, so stay at 1/s.
# Helius free tier is 10/s. 5/s clears creator lookups inside 30s at the
# observed ~40 creates/min and leaves room if the history backfill shares the key.
PUBLIC_RPS = 1.0
HELIUS_RPS = 5.0
DEFAULT_RPS = PUBLIC_RPS
HELIUS_ENV_FILE = Path("/var/lib/mal/backfill/helius.env")
KEY_CHECK_S = 30.0
BACKOFF_START_S = 5.0
BACKOFF_CAP_S = 120.0
PUBLIC_RPC = "https://api.mainnet-beta.solana.com"

# Public hot wallets only. Unlisted funders are not called exchanges.
# 2026-09-25: Binance SOL hot (widely labeled; CryptoLens), Coinbase Commerce
# (Vybe labeled wallets), Coinbase Hot Wallet 7 (Orb). Disputed labels omitted.
EXCHANGE_WALLETS: dict[str, str] = {
    "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9": "binance",
    "2AQdpHJ2JpcEgPiATUXjQxA8QmafFegfQwSLWSprPicm": "coinbase",
    "AafGzY9eiC5Ud3YFZQwkaKApp48cVBAT2kksGvEjhUvH": "coinbase",
}

# Frozen before any tape score. Fail-open when the funder is unknown.
# Exchange-sized clusters are not rug rings and do not arm the veto.
RUG_VETO_MIN_SCORED = 2
RUG_VETO_RUG_FRAC = 0.50
RUG_VETO_SAME_FUNDER = 2
VETO_RULE = (
    f"veto when the creator funder is not an exchange and any of: "
    f"cluster scored priors >= {RUG_VETO_MIN_SCORED} with rug fraction >= {RUG_VETO_RUG_FRAC}, "
    f"at least {RUG_VETO_SAME_FUNDER} early non-bot buyers share that funder, "
    f"or a funder-creator-buyer loop is already on the graph"
)

# Promotion is tools.laya_v0.book_stats (n, days, CI, drop top 3). One definition.
PRELIMINARY_REASON = (
    "Preliminary. The simulator and LAYA eval are being fixed in parallel for fill optimism "
    "and entry latency. Re-run this veto after those land before treating promote as final."
)

FUNDING_FEATURE_NAMES: tuple[str, ...] = (
    "f_funder_known",
    "f_funder_cluster_hash",
    "f_funder_prior_creates",
    "f_funder_prior_scored",
    "f_funder_prior_rug_frac",
    "f_funder_prior_median_peak",
    "f_creator_funded_by_exchange",
    "f_creator_fresh_wallet",
    "f_creator_wallet_age_s",
    "f_same_funder_early_n",
    "f_funder_creator_buyer_loop",
)

NAN = float("nan")
SCHEMA = "funding_wallet_v1"


def _nan() -> float:
    return NAN


def empty_funding_features() -> dict[str, float]:
    """Unknown graph. Counts stay 0 so the veto cannot fire."""
    return {
        "f_funder_known": 0.0,
        "f_funder_cluster_hash": NAN,
        "f_funder_prior_creates": 0.0,
        "f_funder_prior_scored": 0.0,
        "f_funder_prior_rug_frac": NAN,
        "f_funder_prior_median_peak": NAN,
        "f_creator_funded_by_exchange": NAN,
        "f_creator_fresh_wallet": NAN,
        "f_creator_wallet_age_s": NAN,
        "f_same_funder_early_n": 0.0,
        "f_funder_creator_buyer_loop": 0.0,
    }


def cluster_hash(funder: str) -> float:
    """Stable 32-bit FNV-1a. The cluster id itself is the funder pubkey."""
    h = 2166136261
    for byte in funder.encode("ascii", "ignore"):
        h ^= byte
        h = (h * 16777619) & 0xFFFFFFFF
    return float(h)


def describe_rpc(url: str) -> str:
    """Never log a URL that may carry an API key."""
    lowered = url.lower()
    if "api-key=" in lowered or "helius" in lowered:
        return "helius"
    return "public"


def _key_is_safe(key: str) -> bool:
    return bool(key) and not any(ch in key for ch in "\r\n& #")


def helius_key_from_file(path: Path) -> str:
    """Read HELIUS_API_KEY= from an env file. Empty if missing or unsafe. Never log the value."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != "HELIUS_API_KEY":
            continue
        value = value.strip().strip('"').strip("'")
        return value if _key_is_safe(value) else ""
    return ""


def helius_api_key(env: dict[str, str] | None = None, env_file: Path | None = None) -> str:
    """Process environment first, then the env file. Unsafe env values raise."""
    src = os.environ if env is None else env
    key = (src.get("HELIUS_API_KEY") or "").strip()
    if key:
        if not _key_is_safe(key):
            raise ValueError("HELIUS_API_KEY is empty or unsafe")
        return key
    if env_file is None:
        raw = (src.get("MAL_HELIUS_ENV_FILE") or "").strip()
        env_file = Path(raw) if raw else HELIUS_ENV_FILE
    return helius_key_from_file(env_file)


def resolve_rpc_url(env: dict[str, str] | None = None, env_file: Path | None = None) -> str:
    """Public RPC unless a Helius key is in the environment or the env file."""
    key = helius_api_key(env, env_file)
    if key:
        return f"https://mainnet.helius-rpc.com/?api-key={key}"
    src = os.environ if env is None else env
    explicit = (src.get("MAL_SOLANA_HTTP_URL") or "").strip()
    return explicit or PUBLIC_RPC


def choose_rps(url: str, explicit: float | None) -> float:
    """Operator --rps / MAL_FUNDING_RPS wins. Otherwise 1/s public, 5/s Helius."""
    if explicit is not None:
        return float(explicit)
    if describe_rpc(url) == "helius":
        return HELIUS_RPS
    return PUBLIC_RPS


def maybe_switch_rpc(
    client: "RpcClient",
    explicit_rps: float | None,
    env: dict[str, str] | None = None,
    env_file: Path | None = None,
) -> "RpcClient":
    """Move a public client onto Helius once a key exists. Do not switch back."""
    url = resolve_rpc_url(env, env_file)
    if describe_rpc(url) != "helius" or describe_rpc(client.url) == "helius":
        return client
    return RpcClient(url, rps=choose_rps(url, explicit_rps))


class RpcError(Exception):
    def __init__(self, message: str, *, limited: bool = False) -> None:
        super().__init__(message)
        self.limited = limited


@dataclass
class WalletRecord:
    wallet: str
    funder: str | None
    amount_lamports: int | None
    wallet_first_tx_ms: int | None
    funded_at_ms: int | None
    exchange: bool
    exchange_name: str | None
    history_capped: bool
    status: str
    first_seen_ms: int
    role_hint: str
    rpc_pages: int

    def to_json(self) -> dict[str, Any]:
        return {
            "v": 1,
            "schema": SCHEMA,
            "type": "wallet_funding",
            "wallet": self.wallet,
            "funder": self.funder,
            "amount_lamports": self.amount_lamports,
            "wallet_first_tx_ms": self.wallet_first_tx_ms,
            "funded_at_ms": self.funded_at_ms,
            "exchange": self.exchange,
            "exchange_name": self.exchange_name,
            "history_capped": self.history_capped,
            "status": self.status,
            "first_seen_ms": self.first_seen_ms,
            "role_hint": self.role_hint,
            "rpc_pages": self.rpc_pages,
        }

    @classmethod
    def from_json(cls, row: dict[str, Any]) -> "WalletRecord | None":
        if row.get("type") not in (None, "wallet_funding"):
            return None
        wallet = row.get("wallet")
        if not isinstance(wallet, str) or not wallet:
            return None
        seen = row.get("first_seen_ms")
        if not isinstance(seen, int):
            return None
        funder = row.get("funder")
        if not isinstance(funder, str) or not funder:
            funder = None
        return cls(
            wallet=wallet,
            funder=funder,
            amount_lamports=_int_or_none(row.get("amount_lamports")),
            wallet_first_tx_ms=_int_or_none(row.get("wallet_first_tx_ms")),
            funded_at_ms=_int_or_none(row.get("funded_at_ms")),
            exchange=bool(row.get("exchange")),
            exchange_name=row.get("exchange_name") if isinstance(row.get("exchange_name"), str) else None,
            history_capped=bool(row.get("history_capped")),
            status=str(row.get("status") or "resolved"),
            first_seen_ms=seen,
            role_hint=str(row.get("role_hint") or ""),
            rpc_pages=int(row.get("rpc_pages") or 0),
        )


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    return None


class FundingGraph:
    """In-memory view of the append-only log. First row for a wallet wins."""

    def __init__(self, records: Iterable[WalletRecord] = ()) -> None:
        self.by_wallet: dict[str, WalletRecord] = {}
        for rec in records:
            self.by_wallet.setdefault(rec.wallet, rec)
        self._index_books: dict[int, dict[str, list[Any]]] | None = None
        self._index_id: int | None = None

    def __len__(self) -> int:
        return len(self.by_wallet)

    @classmethod
    def load(cls, directory: Path) -> "FundingGraph":
        records: list[WalletRecord] = []
        if directory.is_dir():
            for path in sorted(directory.glob("funding-*.jsonl")):
                records.extend(_read_jsonl(path))
        return cls(records)

    def visible(self, wallet: str | None, t_ms: int) -> WalletRecord | None:
        if not wallet:
            return None
        rec = self.by_wallet.get(wallet)
        if rec is None or rec.first_seen_ms > t_ms:
            return None
        return rec

    def funder_index(self, books: dict[str, Any]) -> dict[str, list[Any]]:
        """Non-exchange funder -> creator books. Visibility is applied at read time."""
        key = id(books)
        if self._index_id == key and self._index_books is not None:
            return self._index_books[key]
        grouped: dict[str, list[Any]] = defaultdict(list)
        for book in books.values():
            creator = getattr(book.create, "creator", None)
            rec = self.by_wallet.get(creator) if isinstance(creator, str) else None
            if rec is None or not rec.funder or rec.exchange:
                continue
            grouped[rec.funder].append(book)
        for rows in grouped.values():
            rows.sort(key=lambda book: (book.create.t_signal_ms, book.create.mint))
        self._index_books = {key: grouped}
        self._index_id = key
        return grouped


def _read_jsonl(path: Path) -> list[WalletRecord]:
    out: list[WalletRecord] = []
    try:
        fh = path.open("r", encoding="utf-8")
    except OSError:
        return out
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            rec = WalletRecord.from_json(row)
            if rec is not None:
                out.append(rec)
    return out


class JsonlAppender:
    """Append-only daily files. Never rewrites a line."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = (directory / ".writer.lock").open("a", encoding="utf-8")
        try:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemExit("funding graph writer already running") from exc

    def path_for(self, first_seen_ms: int) -> Path:
        day = time.strftime("%Y-%m-%d", time.gmtime(first_seen_ms / 1000.0))
        return self.directory / f"funding-{day}.jsonl"

    def append(self, rec: WalletRecord) -> None:
        path = self.path_for(rec.first_seen_ms)
        line = json.dumps(rec.to_json(), separators=(",", ":"), sort_keys=True) + "\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())


class RpcClient:
    """Polite JSON-RPC. Sleeps live in the clock so tests can fast-forward."""

    def __init__(
        self,
        url: str,
        *,
        rps: float = DEFAULT_RPS,
        transport: Callable[[str, dict[str, Any]], Any] | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        if rps <= 0:
            raise ValueError("rps must be positive")
        self.url = url
        self.min_interval = 1.0 / rps
        self._transport = transport or _urllib_transport
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._next_ok = 0.0
        self._backoff_until = 0.0
        self._backoff_s = 0.0
        self.calls = 0
        self.limited = 0
        self.errors = 0
        self._id = 1

    def call(self, method: str, params: list[Any]) -> Any:
        self._wait()
        body = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        self._id += 1
        self.calls += 1
        try:
            payload = self._transport(self.url, body)
        except RpcError as exc:
            if exc.limited:
                self._punish()
            else:
                self.errors += 1
            raise
        if isinstance(payload, dict) and payload.get("error"):
            err = payload["error"]
            limited = _is_limited(err)
            if limited:
                self._punish()
                raise RpcError(str(err)[:180], limited=True)
            self.errors += 1
            raise RpcError(str(err)[:180], limited=False)
        self._backoff_s = 0.0
        if isinstance(payload, dict) and "result" in payload:
            return payload["result"]
        return payload

    def _wait(self) -> None:
        now = self._clock()
        gate = max(self._next_ok, self._backoff_until)
        if gate > now:
            self._sleep(gate - now)
        self._next_ok = self._clock() + self.min_interval

    def _punish(self) -> None:
        self.limited += 1
        self._backoff_s = BACKOFF_START_S if self._backoff_s <= 0 else min(BACKOFF_CAP_S, self._backoff_s * 2)
        self._backoff_until = self._clock() + self._backoff_s


def _is_limited(err: Any) -> bool:
    text = str(err).lower()
    if isinstance(err, dict):
        code = err.get("code")
        if code == 429:
            return True
    return "429" in text or "too many" in text or "rate limit" in text


def _is_oversized(exc: BaseException) -> bool:
    """Public RPC returns HTTP 413 when a parsed transaction is too large to send back."""
    text = str(exc).lower()
    return "413" in text or "payload too large" in text or "response too large" in text


def _urllib_transport(url: str, body: dict[str, Any]) -> Any:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RpcError("http 429", limited=True) from exc
        raise RpcError(f"http {exc.code}", limited=False) from exc
    except urllib.error.URLError as exc:
        raise RpcError(str(exc.reason)[:180], limited=False) from exc


def iter_instructions(tx: dict[str, Any]) -> Iterable[dict[str, Any]]:
    message = ((tx.get("transaction") or {}).get("message") or {})
    outers = message.get("instructions") or []
    grouped = {}
    for inner in (tx.get("meta") or {}).get("innerInstructions") or []:
        if isinstance(inner, dict):
            grouped[inner.get("index")] = inner.get("instructions") or []
    for index, ix in enumerate(outers):
        if isinstance(ix, dict):
            yield ix
        for inner in grouped.get(index, []):
            if isinstance(inner, dict):
                yield inner


def inbound_sol(tx: dict[str, Any], wallet: str) -> tuple[str, int] | None:
    """First system transfer or createAccount that pays this wallet."""
    meta = tx.get("meta") or {}
    if meta.get("err"):
        return None
    for ix in iter_instructions(tx):
        parsed = ix.get("parsed")
        if not isinstance(parsed, dict):
            continue
        info = parsed.get("info")
        if not isinstance(info, dict):
            continue
        kind = parsed.get("type")
        lamports = info.get("lamports")
        if isinstance(lamports, bool) or not isinstance(lamports, int) or lamports <= 0:
            continue
        source = info.get("source")
        if not isinstance(source, str) or source == wallet:
            continue
        if kind == "transfer" and info.get("destination") == wallet:
            return source, lamports
        if kind in ("createAccount", "createAccountWithSeed") and info.get("newAccount") == wallet:
            return source, lamports
    return None


def _block_ms(sig: dict[str, Any]) -> int | None:
    block = sig.get("blockTime")
    if isinstance(block, int) and block > 0:
        return block * 1000
    return None


def _blank_wallet(
    wallet: str,
    *,
    now_ms: int,
    role_hint: str,
    status: str,
    history_capped: bool,
    rpc_pages: int,
) -> WalletRecord:
    """Fail-open row. No funder, so the veto cannot arm, and the wallet is not retried."""
    return WalletRecord(
        wallet=wallet,
        funder=None,
        amount_lamports=None,
        wallet_first_tx_ms=None,
        funded_at_ms=None,
        exchange=False,
        exchange_name=None,
        history_capped=history_capped,
        status=status,
        first_seen_ms=now_ms,
        role_hint=role_hint,
        rpc_pages=rpc_pages,
    )


def resolve_wallet(
    client: RpcClient,
    wallet: str,
    *,
    now_ms: int,
    role_hint: str = "",
    exchanges: dict[str, str] | None = None,
    max_pages: int = MAX_SIGNATURE_PAGES,
) -> WalletRecord:
    """Oldest confirmed signatures, then the first inbound SOL on those txs."""
    book = exchanges if exchanges is not None else EXCHANGE_WALLETS
    before: str | None = None
    last_page: list[dict[str, Any]] = []
    pages = 0
    reached = False
    page_limit = SIGNATURE_PAGE
    while pages < max_pages:
        cfg: dict[str, Any] = {"limit": page_limit, "commitment": "confirmed"}
        if before:
            cfg["before"] = before
        try:
            result = client.call("getSignaturesForAddress", [wallet, cfg])
        except RpcError as exc:
            if not _is_oversized(exc) or page_limit <= 200:
                if _is_oversized(exc):
                    return _blank_wallet(
                        wallet,
                        now_ms=now_ms,
                        role_hint=role_hint,
                        status="rpc_rejected",
                        history_capped=True,
                        rpc_pages=pages,
                    )
                raise
            page_limit = 200
            continue
        pages += 1
        if not isinstance(result, list) or not result:
            reached = True
            break
        last_page = [row for row in result if isinstance(row, dict) and isinstance(row.get("signature"), str)]
        if len(result) < page_limit:
            reached = True
            break
        before = str(last_page[-1]["signature"]) if last_page else None
        if before is None:
            break
    if not last_page and reached:
        return WalletRecord(
            wallet=wallet,
            funder=None,
            amount_lamports=None,
            wallet_first_tx_ms=None,
            funded_at_ms=None,
            exchange=False,
            exchange_name=None,
            history_capped=False,
            status="no_history",
            first_seen_ms=now_ms,
            role_hint=role_hint,
            rpc_pages=pages,
        )
    if not reached:
        return WalletRecord(
            wallet=wallet,
            funder=None,
            amount_lamports=None,
            wallet_first_tx_ms=None,
            funded_at_ms=None,
            exchange=False,
            exchange_name=None,
            history_capped=True,
            status="history_capped",
            first_seen_ms=now_ms,
            role_hint=role_hint,
            rpc_pages=pages,
        )
    oldest_first = list(reversed(last_page))[:OLDEST_TX_INSPECT]
    first_ms = _block_ms(oldest_first[0]) if oldest_first else None
    funder: str | None = None
    amount: int | None = None
    funded_ms: int | None = None
    oversized = False
    for sig in oldest_first:
        try:
            tx = client.call(
                "getTransaction",
                [
                    sig["signature"],
                    {"encoding": "jsonParsed", "commitment": "confirmed", "maxSupportedTransactionVersion": 0},
                ],
            )
        except RpcError as exc:
            if not _is_oversized(exc):
                raise
            # An unread older transaction may be the real funder. Do not guess from a newer one.
            oversized = True
            break
        if not isinstance(tx, dict):
            continue
        found = inbound_sol(tx, wallet)
        if found is None:
            continue
        funder, amount = found
        funded_ms = _block_ms(sig)
        break
    if oversized and funder is None:
        return _blank_wallet(
            wallet,
            now_ms=now_ms,
            role_hint=role_hint,
            status="tx_unavailable",
            history_capped=True,
            rpc_pages=pages,
        )
    name = book.get(funder) if funder else None
    status = "resolved" if funder else "no_inbound"
    return WalletRecord(
        wallet=wallet,
        funder=funder,
        amount_lamports=amount,
        wallet_first_tx_ms=first_ms,
        funded_at_ms=funded_ms,
        exchange=bool(name),
        exchange_name=name,
        history_capped=False,
        status=status,
        first_seen_ms=now_ms,
        role_hint=role_hint,
        rpc_pages=pages,
    )


def _price_at(book: Any, t_ms: int) -> float | None:
    last = None
    for pr in book.flow:
        if pr.t_recv_ms > t_ms:
            break
        price = getattr(pr, "price_sol", 0) or 0
        if price > 0:
            last = float(price)
    if last is not None:
        return last
    path = getattr(book, "path", None)
    anchor = path.anchor() if path is not None and hasattr(path, "anchor") else None
    if anchor is not None and anchor.t_recv_ms <= t_ms and anchor.price_sol > 0:
        return float(anchor.price_sol)
    return None


def _outcome(book: Any, *, start_ms: int, end_ms: int) -> tuple[float, float] | None:
    """(end/start, peak/start - 1) using prints inside a closed horizon."""
    start = _price_at(book, start_ms)
    if start is None or start <= 0:
        return None
    end = start
    peak = start
    for pr in book.flow:
        if pr.t_recv_ms <= start_ms:
            continue
        if pr.t_recv_ms > end_ms:
            break
        price = getattr(pr, "price_sol", 0) or 0
        if price > 0:
            price = float(price)
            end = price
            if price > peak:
                peak = price
    return end / start, peak / start - 1.0


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def early_buyers(book: Any, t_ms: int, wallets: Any, limit: int = EARLY_BUYERS) -> list[str]:
    """First non-bot, non-creator buyers at or before the decision."""
    creator = book.create.creator
    bots = getattr(wallets, "bots", ()) if wallets is not None else ()
    snipers = getattr(wallets, "snipers", ()) if wallets is not None else ()
    found: list[str] = []
    seen: set[str] = set()
    for pr in book.flow:
        if pr.t_recv_ms > t_ms:
            break
        if getattr(pr, "side", None) != "buy":
            continue
        trader = getattr(pr, "trader", None)
        if not isinstance(trader, str) or not trader or trader == creator:
            continue
        if trader in bots or trader in snipers or trader in seen:
            continue
        seen.add(trader)
        found.append(trader)
        if len(found) >= limit:
            break
    return found


def _loop(creator: str, funder: str, buyers: Sequence[str], graph: FundingGraph, t_ms: int) -> float:
    """1 when funder buys, or creator-funded buyer sends back to funder or creator."""
    if funder in buyers:
        return 1.0
    for buyer in buyers:
        rec = graph.visible(buyer, t_ms)
        if rec is None or rec.funder != creator:
            continue
        funder_rec = graph.visible(funder, t_ms)
        if buyer == funder or (funder_rec is not None and funder_rec.funder == buyer):
            return 1.0
        creator_rec = graph.visible(creator, t_ms)
        if creator_rec is not None and creator_rec.funder == buyer:
            return 1.0
    return 0.0


def fill_funding_features(
    book: Any,
    t_ms: int,
    feats: dict[str, float],
    wallets: Any,
    graph: FundingGraph | None,
    library: dict[str, Any] | None = None,
) -> None:
    """Write funding columns. Rows with first_seen_ms > t_ms are invisible."""
    base = empty_funding_features()
    if graph is None or not graph.by_wallet:
        feats.update(base)
        return
    creator = book.create.creator if isinstance(book.create.creator, str) else None
    rec = graph.visible(creator, t_ms)
    if rec is None or not rec.funder:
        feats.update(base)
        return
    funder = rec.funder
    base["f_funder_known"] = 1.0
    base["f_funder_cluster_hash"] = cluster_hash(funder)
    base["f_creator_funded_by_exchange"] = 1.0 if rec.exchange else 0.0
    if isinstance(rec.wallet_first_tx_ms, int):
        age_s = max(0.0, (book.create.t_signal_ms - rec.wallet_first_tx_ms) / 1000.0)
        base["f_creator_wallet_age_s"] = age_s
        if rec.exchange:
            base["f_creator_fresh_wallet"] = 0.0
        else:
            base["f_creator_fresh_wallet"] = 1.0 if age_s < FRESH_MAX_S else 0.0
    buyers = early_buyers(book, t_ms, wallets)
    base["f_funder_creator_buyer_loop"] = _loop(creator or "", funder, buyers, graph, t_ms)
    if not rec.exchange:
        same = 0
        for buyer in buyers:
            other = graph.visible(buyer, t_ms)
            if other is not None and other.funder == funder:
                same += 1
        base["f_same_funder_early_n"] = float(same)
        books = library if library is not None else {book.create.mint: book}
        priors = graph.funder_index(books).get(funder, ())
        creates = 0
        rugs = 0
        peaks: list[float] = []
        for other in priors:
            if other.create.mint == book.create.mint or other.create.t_signal_ms >= book.create.t_signal_ms:
                continue
            other_creator = other.create.creator if isinstance(other.create.creator, str) else None
            other_rec = graph.visible(other_creator, t_ms)
            if other_rec is None or other_rec.funder != funder:
                continue
            creates += 1
            horizon = other.create.t_signal_ms + CLUSTER_HORIZON_MS
            if horizon > t_ms:
                continue
            outcome = _outcome(other, start_ms=other.create.t_signal_ms, end_ms=horizon)
            if outcome is None:
                continue
            ratio, peak = outcome
            if ratio < CLUSTER_RUG_RATIO:
                rugs += 1
            peaks.append(peak)
        base["f_funder_prior_creates"] = float(creates)
        base["f_funder_prior_scored"] = float(len(peaks))
        if peaks:
            base["f_funder_prior_rug_frac"] = rugs / len(peaks)
            base["f_funder_prior_median_peak"] = _median(peaks)
    feats.update(base)


def rug_veto(feats: dict[str, float]) -> bool:
    """Pre-registered rule. Missing features do not veto."""
    scored = feats.get("f_funder_prior_scored", 0.0)
    frac = feats.get("f_funder_prior_rug_frac", NAN)
    same = feats.get("f_same_funder_early_n", 0.0)
    loop = feats.get("f_funder_creator_buyer_loop", 0.0)
    if scored == scored and frac == frac and scored >= RUG_VETO_MIN_SCORED and frac >= RUG_VETO_RUG_FRAC:
        return True
    if same == same and same >= RUG_VETO_SAME_FUNDER:
        return True
    if loop == loop and loop >= 1.0:
        return True
    return False


class _BotWallet:
    __slots__ = ("n_buys", "mints", "sniper_buys", "gap_sum_ms", "gap_n", "last_t", "is_bot")

    def __init__(self) -> None:
        self.n_buys = 0
        self.mints: set[str] = set()
        self.sniper_buys = 0
        self.gap_sum_ms = 0
        self.gap_n = 0
        self.last_t: int | None = None
        self.is_bot = False

    def observe(self, *, mint: str, t_ms: int, slot: int, first_slot: int) -> None:
        if self.last_t is not None and t_ms >= self.last_t:
            self.gap_sum_ms += t_ms - self.last_t
            self.gap_n += 1
        self.last_t = t_ms
        self.mints.add(mint)
        self.n_buys += 1
        if slot <= first_slot + SNIPER_SLOT_DELTA:
            self.sniper_buys += 1
        sniper_share = self.sniper_buys / self.n_buys if self.n_buys else 0.0
        mean_gap = (self.gap_sum_ms / self.gap_n) if self.gap_n else 1e12
        fast = self.gap_n >= BOT_MIN_GAPS and mean_gap <= BOT_MEAN_GAP_MS
        snipy = sniper_share >= BOT_SNIPER_SHARE
        self.is_bot = self.n_buys >= BOT_MIN_BUYS and len(self.mints) >= BOT_MIN_MINTS and (fast or snipy)


class JobQueue:
    def __init__(self) -> None:
        self._heap: list[tuple[int, int, str, str]] = []
        self._seq = 0
        self.queued: set[str] = set()

    def __len__(self) -> int:
        return len(self._heap)

    def push(self, priority: int, wallet: str, role: str) -> bool:
        if not wallet or wallet in self.queued:
            return False
        import heapq

        heapq.heappush(self._heap, (priority, self._seq, wallet, role))
        self._seq += 1
        self.queued.add(wallet)
        return True

    def pop(self) -> tuple[str, str] | None:
        import heapq

        while self._heap:
            _pri, _seq, wallet, role = heapq.heappop(self._heap)
            self.queued.discard(wallet)
            return wallet, role
        return None


def seed_recent_creators(creates_dir: Path, limit: int) -> list[str]:
    """Last creators in the live observe file. Oldest of that slice first."""
    if limit <= 0 or not creates_dir.is_dir():
        return []
    files = sorted(creates_dir.glob("observe-*.jsonl"))
    if not files:
        return []
    path = files[-1]
    try:
        size = path.stat().st_size
    except OSError:
        return []
    start = max(0, size - 2_000_000)
    with path.open("rb") as fh:
        fh.seek(start)
        blob = fh.read().decode("utf-8", "replace")
    if start > 0:
        blob = blob.split("\n", 1)[-1]
    found: list[str] = []
    seen: set[str] = set()
    from tools.paper_price_path import create_from_observe_row

    for line in blob.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        create = create_from_observe_row(row)
        if create is None or not create.creator or create.creator in seen:
            continue
        seen.add(create.creator)
        found.append(create.creator)
    return found[-limit:]


class TapeCursor:
    """Byte offsets into the live JSONL tails. Not a feature source."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.pos: dict[str, int] = {}
        self._buf: dict[str, str] = {}
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
            if isinstance(raw, dict):
                for key, value in raw.items():
                    if isinstance(key, str) and isinstance(value, int) and value >= 0:
                        self.pos[key] = value

    def save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.pos, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def read_new(self, path: Path, *, start_at_end: bool) -> list[dict[str, Any]]:
        key = str(path)
        try:
            size = path.stat().st_size
        except OSError:
            return []
        if key not in self.pos:
            self.pos[key] = size if start_at_end else 0
        pos = self.pos[key]
        if size < pos:
            pos = 0
        if size == pos and not self._buf.get(key):
            return []
        with path.open("rb") as fh:
            fh.seek(pos)
            data = fh.read()
        self.pos[key] = pos + len(data)
        text = self._buf.get(key, "") + data.decode("utf-8", "replace")
        if not text.endswith("\n"):
            if "\n" not in text:
                self._buf[key] = text
                return []
            head, _, tail = text.rpartition("\n")
            self._buf[key] = tail
            lines = head.split("\n")
        else:
            self._buf[key] = ""
            lines = text.splitlines()
        rows: list[dict[str, Any]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows


class Enricher:
    def __init__(
        self,
        *,
        graph_dir: Path,
        trades_dir: Path,
        creates_dir: Path,
        client: RpcClient,
        early_n: int = EARLY_BUYERS,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self.graph_dir = graph_dir
        self.trades_dir = trades_dir
        self.creates_dir = creates_dir
        self.client = client
        self.early_n = early_n
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.graph = FundingGraph.load(graph_dir)
        self.appender = JsonlAppender(graph_dir)
        self.queue = JobQueue()
        self.cursor = TapeCursor(graph_dir / "tail.json")
        self._fresh_tail = not (graph_dir / "tail.json").is_file()
        self.creators: dict[str, str] = {}
        self.early: dict[str, list[str]] = defaultdict(list)
        self.first_slot: dict[str, int] = {}
        self.bots: dict[str, _BotWallet] = {}
        self.resolved = 0
        self.skipped = 0
        self.unavailable = 0

    def note_create(self, mint: str, creator: str | None) -> None:
        if not creator or mint in self.creators:
            return
        self.creators[mint] = creator
        self._enqueue(creator, "creator", CREATOR_PRIORITY)

    def note_trade(self, row: dict[str, Any]) -> None:
        mint = row.get("mint")
        trader = row.get("trader")
        if not isinstance(mint, str) or mint not in self.creators:
            return
        if row.get("side") != "buy" or not isinstance(trader, str) or not trader:
            return
        if trader == self.creators.get(mint):
            return
        slot = row.get("slot")
        slot_i = slot if isinstance(slot, int) else 0
        self.first_slot.setdefault(mint, slot_i)
        bot = self.bots.get(trader)
        if bot is None:
            bot = _BotWallet()
            self.bots[trader] = bot
        t_ms = row.get("t_recv_ms")
        t_i = t_ms if isinstance(t_ms, int) else 0
        bot.observe(mint=mint, t_ms=t_i, slot=slot_i, first_slot=self.first_slot[mint])
        if bot.is_bot:
            return
        chosen = self.early[mint]
        if trader in chosen or len(chosen) >= self.early_n:
            return
        chosen.append(trader)
        self._enqueue(trader, "early_buyer", BUYER_PRIORITY)

    def _enqueue(self, wallet: str, role: str, priority: int) -> bool:
        if wallet in self.graph.by_wallet:
            self.skipped += 1
            return False
        return self.queue.push(priority, wallet, role)

    def poll_files(self) -> None:
        from tools.paper_price_path import create_from_observe_row

        start_at_end = self._fresh_tail
        if self.creates_dir.is_dir():
            for path in sorted(self.creates_dir.glob("observe-*.jsonl")):
                for row in self.cursor.read_new(path, start_at_end=start_at_end):
                    create = create_from_observe_row(row)
                    if create is not None:
                        self.note_create(create.mint, create.creator)
        if self.trades_dir.is_dir():
            for path in sorted(self.trades_dir.glob("trades-*.jsonl")):
                for row in self.cursor.read_new(path, start_at_end=start_at_end):
                    self.note_trade(row)
        self._fresh_tail = False

    def drain_one(self) -> WalletRecord | None:
        item = self.queue.pop()
        if item is None:
            return None
        wallet, role = item
        if wallet in self.graph.by_wallet:
            return None
        priority = CREATOR_PRIORITY if role == "creator" else BUYER_PRIORITY
        try:
            rec = resolve_wallet(self.client, wallet, now_ms=self.now_ms(), role_hint=role)
        except RpcError:
            self.queue.push(priority, wallet, role)
            raise
        if rec.status == "no_history":
            # A just-landed creator can be invisible for a moment. Retry twice.
            tries = getattr(self, "_empty_tries", {})
            n = tries.get(wallet, 0) + 1
            tries[wallet] = n
            self._empty_tries = tries
            if n < 3:
                self.queue.push(CREATOR_PRIORITY if role == "creator" else BUYER_PRIORITY, wallet, role)
                return None
        self.appender.append(rec)
        self.graph.by_wallet.setdefault(rec.wallet, rec)
        self.resolved += 1
        if rec.status in ("tx_unavailable", "rpc_rejected"):
            self.unavailable += 1
        return rec

    def seed_backfill(self, limit: int) -> int:
        n = 0
        for wallet in seed_recent_creators(self.creates_dir, limit):
            if self._enqueue(wallet, "creator", BACKFILL_PRIORITY):
                n += 1
        return n


def score_rows(rows: Sequence[Any]) -> dict[str, Any]:
    """Walk-forward the frozen veto. Not a pool-wide top-k."""
    from tools.laya_v0 import PROMOTION_RULE, BookTrade, book_stats, walk_forward

    ordered = [row for row in rows if getattr(row, "pnl", None) is not None]
    folds = walk_forward([int(row.decision_t_ms) for row in ordered]) if ordered else []
    base: list[BookTrade] = []
    kept: list[BookTrade] = []
    vetoed: list[BookTrade] = []
    covered = 0
    for _train, test in folds:
        for index in test:
            row = ordered[index]
            trade = BookTrade(str(row.mint), int(row.decision_t_ms), int(row.pnl))
            base.append(trade)
            feats = row.features
            if float(feats.get("f_funder_known", 0.0) or 0.0) >= 1.0:
                covered += 1
            if rug_veto(feats):
                vetoed.append(trade)
            else:
                kept.append(trade)
    kept_stats = book_stats(kept)
    base_stats = book_stats(base)
    return {
        "rule": VETO_RULE,
        "promotion_rule": PROMOTION_RULE,
        "selection": "veto filter, not top-k; ranked books use forward_paper._RankWindow",
        "join": "first_seen_ms <= decision_t_ms",
        "preliminary": True,
        "preliminary_reason": PRELIMINARY_REASON,
        "oos_n": len(base),
        "oos_covered": covered,
        "oos_vetoed": len(vetoed),
        "baseline": base_stats,
        "kept": kept_stats,
        "vetoed": book_stats(vetoed),
        "lift_mean_sol": _lift(kept_stats, base_stats),
        "promote": bool(kept_stats.get("promote")),
        "feature_slices": _slices(ordered, folds),
    }


def _lift(kept: dict[str, Any], base: dict[str, Any]) -> float | None:
    left = kept.get("mean_sol")
    right = base.get("mean_sol")
    if left is None or right is None:
        return None
    return float(left) - float(right)


def _slices(rows: Sequence[Any], folds: Sequence[tuple[list[int], list[int]]]) -> dict[str, Any]:
    """Descriptive OOS means. Not a second promotion."""
    from tools.laya_v0 import BookTrade, book_stats

    test_idx = [i for _train, test in folds for i in test]
    out: dict[str, Any] = {}
    flags = (
        ("exchange", "f_creator_funded_by_exchange"),
        ("fresh", "f_creator_fresh_wallet"),
        ("same_funder", "f_same_funder_early_n"),
        ("loop", "f_funder_creator_buyer_loop"),
        ("cluster_rug", "f_funder_prior_rug_frac"),
    )
    for name, key in flags:
        on: list[BookTrade] = []
        off: list[BookTrade] = []
        for index in test_idx:
            row = rows[index]
            if getattr(row, "pnl", None) is None:
                continue
            value = float(row.features.get(key, NAN))
            if value != value:
                continue
            trade = BookTrade(str(row.mint), int(row.decision_t_ms), int(row.pnl))
            fire = value >= RUG_VETO_RUG_FRAC if key.endswith("rug_frac") else value >= (RUG_VETO_SAME_FUNDER if "same" in key else 1.0)
            (on if fire else off).append(trade)
        out[name] = {"on": book_stats(on), "off": book_stats(off)}
    return out


def run_serve(
    *,
    graph_dir: Path,
    trades_dir: Path,
    creates_dir: Path,
    rps: float | None,
    seed_limit: int,
    once: bool = False,
) -> int:
    url = resolve_rpc_url()
    rate = choose_rps(url, rps)
    enricher = Enricher(
        graph_dir=graph_dir,
        trades_dir=trades_dir,
        creates_dir=creates_dir,
        client=RpcClient(url, rps=rate),
    )
    seeded = enricher.seed_backfill(seed_limit)
    print(
        f"funding_graph rpc={describe_rpc(url)} rps={rate} cached={len(enricher.graph)} seeded={seeded}",
        file=sys.stderr,
    )
    next_log = time.monotonic() + 60.0
    next_key = time.monotonic() + KEY_CHECK_S
    while True:
        if time.monotonic() >= next_key:
            nxt = maybe_switch_rpc(enricher.client, rps)
            if nxt is not enricher.client:
                enricher.client = nxt
                print(
                    f"funding_graph rpc=helius rps={choose_rps(nxt.url, rps)}",
                    file=sys.stderr,
                )
            next_key = time.monotonic() + KEY_CHECK_S
        enricher.poll_files()
        client = enricher.client
        if enricher.queue:
            try:
                enricher.drain_one()
            except RpcError as exc:
                if exc.limited:
                    print(
                        f"funding_graph backoff_s={client._backoff_s:.0f} limited={client.limited}",
                        file=sys.stderr,
                    )
                else:
                    print(f"funding_graph rpc_error={exc}", file=sys.stderr)
                if once:
                    break
                time.sleep(min(5.0, max(1.0, client._backoff_s)))
        else:
            enricher.cursor.save()
            if once:
                break
            time.sleep(1.0)
        now = time.monotonic()
        if now >= next_log:
            print(
                f"funding_graph resolved={enricher.resolved} queue={len(enricher.queue)} "
                f"calls={client.calls} limited={client.limited} unavailable={enricher.unavailable} "
                f"cached={len(enricher.graph)}",
                file=sys.stderr,
            )
            enricher.cursor.save()
            next_log = now + 60.0
    enricher.cursor.save()
    return 0


def run_probe(*, wallets: Sequence[str], rps: float, max_pages: int) -> dict[str, Any]:
    url = resolve_rpc_url()
    client = RpcClient(url, rps=rps)
    started = time.perf_counter()
    ok = 0
    for wallet in wallets:
        try:
            resolve_wallet(client, wallet, now_ms=int(time.time() * 1000), role_hint="probe", max_pages=max_pages)
            ok += 1
        except RpcError as exc:
            if not exc.limited:
                raise
            break
    elapsed = max(1e-6, time.perf_counter() - started)
    return {
        "rpc": describe_rpc(url),
        "rps_target": rps,
        "wallets_attempted": len(wallets),
        "wallets_resolved": ok,
        "calls": client.calls,
        "limited": client.limited,
        "elapsed_s": round(elapsed, 3),
        "achieved_wallets_per_min": round(ok / elapsed * 60.0, 2),
        "achieved_calls_per_s": round(client.calls / elapsed, 3),
    }


def _parse_score_rows(path: Path) -> list[Any]:
    @dataclass
    class _Row:
        mint: str
        decision_t_ms: int
        pnl: int | None
        features: dict[str, float]

    rows: list[_Row] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            pnl = raw.get("pnl_lamports")
            rows.append(
                _Row(
                    mint=str(raw["mint"]),
                    decision_t_ms=int(raw["decision_t_ms"]),
                    pnl=None if pnl is None else int(pnl),
                    features={str(k): float(v) for k, v in (raw.get("features") or {}).items()},
                )
            )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paper funding graph enricher")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="Tail creates and resolve funders")
    serve.add_argument("--graph-dir", type=Path, default=Path(os.environ.get("MAL_FUNDING_GRAPH_DIR", "/var/lib/mal/graph")))
    serve.add_argument("--trades-dir", type=Path, default=Path(os.environ.get("MAL_TRADE_TAPE_OUTPUT_DIR", "/var/lib/mal/sealed/trades")))
    serve.add_argument("--creates-dir", type=Path, default=Path(os.environ.get("MAL_OBSERVE_JSONL_DIR", "/var/lib/mal/sealed/jsonl")))
    serve.add_argument("--rps", type=float, default=None, help="Override. Default is 1/s public, 5/s when a Helius key is present")
    serve.add_argument("--seed-creators", type=int, default=int(os.environ.get("MAL_FUNDING_SEED", "40")))
    serve.add_argument("--once", action="store_true")

    probe = sub.add_parser("probe", help="Measure public RPC throughput on a wallet list")
    probe.add_argument("--wallet", action="append", default=[])
    probe.add_argument("--wallets-file", type=Path)
    probe.add_argument("--rps", type=float, default=2.0)
    probe.add_argument("--max-pages", type=int, default=1)
    probe.add_argument("--limit", type=int, default=12)

    score = sub.add_parser("score", help="Score the frozen veto from a feature JSONL")
    score.add_argument("--rows", type=Path, required=True)
    score.add_argument("--output", type=Path)

    args = parser.parse_args(argv)
    if args.cmd == "serve":
        explicit = args.rps
        if explicit is None:
            raw = (os.environ.get("MAL_FUNDING_RPS") or "").strip()
            explicit = float(raw) if raw else None
        return run_serve(
            graph_dir=args.graph_dir,
            trades_dir=args.trades_dir,
            creates_dir=args.creates_dir,
            rps=explicit,
            seed_limit=args.seed_creators,
            once=args.once,
        )
    if args.cmd == "probe":
        wallets = list(args.wallet)
        if args.wallets_file:
            for line in args.wallets_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    wallets.append(line.split()[0])
        wallets = wallets[: args.limit]
        if not wallets:
            print("probe: no wallets", file=sys.stderr)
            return 1
        report = run_probe(wallets=wallets, rps=args.rps, max_pages=args.max_pages)
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0 if report["limited"] == 0 else 2
    report = score_rows(_parse_score_rows(args.rows))
    text = json.dumps(report, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
