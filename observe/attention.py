#!/usr/bin/env python3
"""Paper attention tape: what humans see, not the on-chain trade stream.

Polls free, keyless DexScreener / pump.fun / GeckoTerminal endpoints that back
the DexScreener paid-profile/boost surfaces, pump.fun livestreams, and the
graduation fold that replaced king-of-the-hill. First-seen (unix ms) is when
this process first observed the mint on that surface. No keys, no sends.
Does not touch the trade-tape recorder.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import re
import signal
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from observe.trade_store import (
    HEADROOM_RATIO,
    RESUME_RATIO,
    HourlyJsonlWriter,
    ZstdCompressor,
    filesystem_bytes,
    hour_stamp,
)

log = logging.getLogger("mal.attention")

DEFAULT_OUTPUT_DIR = Path("data/observe")
USER_AGENT = "MAL-paper-attention/0 (+https://github.com/vaanai/MAL; paper research; no keys)"
WSOL_MINT = "So11111111111111111111111111111111111111112"
ORDER_GAP_S = 2.0
MAX_ORDER_QUEUE = 400
BACKOFF_CAP_S = 120.0
HTTP_TIMEOUT_S = 20.0
SEEN_RELOAD_FILES = 72
ATTENTION_RAW_RE = re.compile(r"^attention-\d{4}-\d{2}-\d{2}T\d{2}\.jsonl$")
ATTENTION_FILE_RE = re.compile(r"^attention-\d{4}-\d{2}-\d{2}T\d{2}\.jsonl(\.zst)?$")

DEX_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1"
DEX_BOOSTS_LATEST = "https://api.dexscreener.com/token-boosts/latest/v1"
DEX_BOOSTS_TOP = "https://api.dexscreener.com/token-boosts/top/v1"
DEX_ADS = "https://api.dexscreener.com/ads/latest/v1"
DEX_ORDERS = "https://api.dexscreener.com/orders/v1/solana/{mint}"
PUMP_LIVE = "https://frontend-api-v3.pump.fun/coins/currently-live?offset=0&limit=60&includeNsfw=false"
PUMP_HOT = "https://frontend-api-v3.pump.fun/coins/hot-coin"
PUMP_GRADUATING = "https://frontend-api-v3.pump.fun/coins/boards/graduating?limit=50"
PUMP_MOVERS = "https://frontend-api-v3.pump.fun/coins/boards/movers?limit=50"
PUMP_GREAT = "https://frontend-api-v3.pump.fun/coins/great-coins?topK=20"
PUMP_RUNNERS = "https://frontend-api-v3.pump.fun/coins/top-runners"
GECKO_TRENDING = (
    "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools"
    "?page=1&include=base_token"
)


@dataclass(frozen=True, slots=True)
class Candidate:
    mint: str
    kind: str
    source: str
    chain: str = "solana"
    rank: int | None = None
    paid_at_ms: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceSpec:
    name: str
    url: str
    kind: str
    interval_s: float
    parse: Callable[[object], list[Candidate]]
    headers: dict[str, str] | None = None


class FetchError(Exception):
    def __init__(self, message: str, *, status: int | None = None, retry_after_s: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after_s = retry_after_s


def now_ms() -> int:
    return int(time.time() * 1000)


def iso_from_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def is_solana_chain(chain: object) -> bool:
    if not isinstance(chain, str) or not chain:
        return False
    low = chain.lower()
    return low == "solana" or low.startswith("solana:")


def is_solana_mint(mint: object) -> bool:
    if not isinstance(mint, str) or not mint or mint == WSOL_MINT:
        return False
    if mint.startswith("0x"):
        return False
    if mint.endswith("pump") and 30 <= len(mint) <= 48:
        return True
    return 32 <= len(mint) <= 48 and not mint.startswith("0x")


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        v = int(value)
        return v if v > 0 else None
    if isinstance(value, str) and value.isdigit():
        v = int(value)
        return v if v > 0 else None
    return None


def parse_time_ms(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e12:
            return int(v)
        if v > 1e9:
            return int(v * 1000)
        return None
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _slim_links(links: object) -> list[dict[str, str]]:
    if not isinstance(links, list):
        return []
    out: list[dict[str, str]] = []
    for item in links[:8]:
        if not isinstance(item, dict):
            continue
        row: dict[str, str] = {}
        for key in ("type", "label", "url"):
            val = item.get(key)
            if isinstance(val, str) and val:
                row[key] = val[:240]
        if row:
            out.append(row)
    return out


def _coin_extra(coin: dict[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    for key in (
        "name",
        "symbol",
        "twitter",
        "telegram",
        "website",
        "reply_count",
        "usd_market_cap",
        "market_cap",
        "created_timestamp",
        "king_of_the_hill_timestamp",
        "is_currently_live",
        "num_participants",
        "livestream_title",
        "complete",
        "boost_mode",
        "last_trade_timestamp",
    ):
        if key in coin and coin[key] not in (None, "", []):
            extra[key] = coin[key]
    return extra


def _from_coin(coin: object, *, kind: str, source: str, rank: int | None = None) -> Candidate | None:
    if not isinstance(coin, dict):
        return None
    mint = coin.get("mint") or coin.get("coinMint") or coin.get("tokenAddress")
    if not is_solana_mint(mint):
        return None
    chain = coin.get("chain_id") or coin.get("chainId") or coin.get("chain") or "solana"
    if isinstance(chain, str) and chain and not is_solana_chain(chain) and chain.lower() not in ("solana",):
        if not str(mint).endswith("pump"):
            return None
    paid = parse_time_ms(coin.get("king_of_the_hill_timestamp"))
    extra = _coin_extra(coin)
    return Candidate(
        mint=str(mint),
        kind=kind,
        source=source,
        chain="solana",
        rank=rank,
        paid_at_ms=paid,
        extra=extra,
    )


def parse_dex_list(payload: object, *, kind: str, source: str) -> list[Candidate]:
    if not isinstance(payload, list):
        return []
    out: list[Candidate] = []
    for i, row in enumerate(payload):
        if not isinstance(row, dict):
            continue
        if not is_solana_chain(row.get("chainId")):
            continue
        mint = row.get("tokenAddress")
        if not is_solana_mint(mint):
            continue
        extra: dict[str, Any] = {}
        if row.get("amount") is not None:
            extra["amount"] = row.get("amount")
        if row.get("totalAmount") is not None:
            extra["totalAmount"] = row.get("totalAmount")
        if row.get("cto") is not None:
            extra["cto"] = row.get("cto")
        if row.get("type") is not None:
            extra["ad_type"] = row.get("type")
        if row.get("impressions") is not None:
            extra["impressions"] = row.get("impressions")
        links = _slim_links(row.get("links"))
        if links:
            extra["links"] = links
        paid = parse_time_ms(row.get("date")) or parse_time_ms(row.get("claimDate"))
        out.append(
            Candidate(
                mint=str(mint),
                kind=kind,
                source=source,
                rank=i,
                paid_at_ms=paid,
                extra=extra,
            )
        )
    return out


def parse_pump_list(payload: object, *, kind: str, source: str) -> list[Candidate]:
    rows: list[object]
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        if isinstance(payload.get("coins"), list):
            rows = payload["coins"]
        elif isinstance(payload.get("hotCoin"), dict) and isinstance(payload["hotCoin"].get("coin"), dict):
            rows = [payload["hotCoin"]["coin"]]
        elif payload.get("hotCoin") is None:
            return []
        else:
            rows = [payload]
    else:
        return []
    out: list[Candidate] = []
    for i, row in enumerate(rows):
        coin = row
        if isinstance(row, dict) and isinstance(row.get("coin"), dict):
            coin = row["coin"]
        cand = _from_coin(coin, kind=kind, source=source, rank=i)
        if cand is not None:
            if isinstance(row, dict) and isinstance(row.get("description"), str) and row["description"]:
                extra = dict(cand.extra)
                extra["blurb"] = row["description"][:160]
                cand = Candidate(
                    mint=cand.mint,
                    kind=cand.kind,
                    source=cand.source,
                    chain=cand.chain,
                    rank=cand.rank,
                    paid_at_ms=cand.paid_at_ms,
                    extra=extra,
                )
            out.append(cand)
    return out


def parse_pump_graduating(payload: object) -> list[Candidate]:
    """Rank-0 on the graduating fold is the human KOTH successor (old /king-of-the-hill is 404)."""
    cands = parse_pump_list(payload, kind="pump_graduating", source="pump_graduating")
    out: list[Candidate] = []
    for cand in cands:
        kind = "pump_koth" if cand.rank == 0 else "pump_graduating"
        out.append(
            Candidate(
                mint=cand.mint,
                kind=kind,
                source=cand.source,
                chain=cand.chain,
                rank=cand.rank,
                paid_at_ms=cand.paid_at_ms,
                extra=cand.extra,
            )
        )
    return out


def parse_pump_hot(payload: object) -> list[Candidate]:
    if not isinstance(payload, dict):
        return []
    hot = payload.get("hotCoin")
    if not isinstance(hot, dict):
        return []
    cand = _from_coin(hot.get("coin"), kind="pump_koth", source="pump_hot_coin", rank=0)
    if cand is None:
        return []
    extra = dict(cand.extra)
    extra["expires_at"] = hot.get("expiresAt")
    return [
        Candidate(
            mint=cand.mint,
            kind=cand.kind,
            source=cand.source,
            chain=cand.chain,
            rank=0,
            paid_at_ms=cand.paid_at_ms,
            extra=extra,
        )
    ]


def parse_gecko_trending(payload: object) -> list[Candidate]:
    if not isinstance(payload, dict):
        return []
    included = {
        row.get("id"): row
        for row in payload.get("included") or []
        if isinstance(row, dict) and row.get("id")
    }
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    out: list[Candidate] = []
    for i, pool in enumerate(data):
        if not isinstance(pool, dict):
            continue
        rel = pool.get("relationships") if isinstance(pool.get("relationships"), dict) else {}
        base = rel.get("base_token") if isinstance(rel.get("base_token"), dict) else {}
        base_data = base.get("data") if isinstance(base.get("data"), dict) else {}
        inc = included.get(base_data.get("id"))
        mint = None
        if isinstance(inc, dict):
            attrs = inc.get("attributes") if isinstance(inc.get("attributes"), dict) else {}
            mint = attrs.get("address")
        if not is_solana_mint(mint):
            continue
        attrs = pool.get("attributes") if isinstance(pool.get("attributes"), dict) else {}
        extra: dict[str, Any] = {}
        if attrs.get("name"):
            extra["pool_name"] = attrs["name"]
        if attrs.get("address"):
            extra["pool"] = attrs["address"]
        if attrs.get("reserve_in_usd") is not None:
            extra["reserve_in_usd"] = attrs["reserve_in_usd"]
        vol = attrs.get("volume_usd") if isinstance(attrs.get("volume_usd"), dict) else {}
        if vol.get("h1") is not None:
            extra["volume_usd_h1"] = vol["h1"]
        if attrs.get("pool_created_at"):
            extra["pool_created_at"] = attrs["pool_created_at"]
        out.append(
            Candidate(
                mint=str(mint),
                kind="gecko_trending",
                source="gecko_trending_pools",
                rank=i,
                extra=extra,
            )
        )
    return out


def parse_dex_orders(payload: object, mint: str) -> list[Candidate]:
    if not isinstance(payload, dict):
        return []
    out: list[Candidate] = []
    orders = payload.get("orders")
    if isinstance(orders, list):
        for row in orders:
            if not isinstance(row, dict):
                continue
            if not is_solana_chain(row.get("chainId") or "solana"):
                continue
            token = row.get("tokenAddress") or mint
            if not is_solana_mint(token):
                continue
            otype = row.get("type") if isinstance(row.get("type"), str) else "order"
            kind = "dex_paid_profile" if otype == "tokenProfile" else f"dex_paid_{otype}"
            extra = {
                "order_type": otype,
                "status": row.get("status"),
            }
            out.append(
                Candidate(
                    mint=str(token),
                    kind=kind,
                    source="dex_orders",
                    paid_at_ms=parse_time_ms(row.get("paymentTimestamp")),
                    extra=extra,
                )
            )
    boosts = payload.get("boosts")
    if isinstance(boosts, list):
        for row in boosts:
            if not isinstance(row, dict):
                continue
            token = row.get("tokenAddress") or mint
            if not is_solana_mint(token):
                continue
            extra = {k: row[k] for k in ("amount", "totalAmount", "status") if k in row}
            out.append(
                Candidate(
                    mint=str(token),
                    kind="dex_boost",
                    source="dex_orders",
                    paid_at_ms=parse_time_ms(row.get("paymentTimestamp")),
                    extra=extra,
                )
            )
    return out


PUMP_HEADERS = {
    "Origin": "https://pump.fun",
    "Referer": "https://pump.fun/",
    "Accept": "application/json",
}


def default_sources() -> tuple[SourceSpec, ...]:
    """Verified 2026-09-25: keyless, documented or observed rate limits."""
    return (
        SourceSpec(
            "dex_profiles",
            DEX_PROFILES,
            "dex_profile",
            60.0,
            lambda p: parse_dex_list(p, kind="dex_profile", source="dex_profiles_latest"),
        ),
        SourceSpec(
            "dex_boosts_latest",
            DEX_BOOSTS_LATEST,
            "dex_boost",
            60.0,
            lambda p: parse_dex_list(p, kind="dex_boost", source="dex_boosts_latest"),
        ),
        SourceSpec(
            "dex_boosts_top",
            DEX_BOOSTS_TOP,
            "dex_boost",
            60.0,
            lambda p: parse_dex_list(p, kind="dex_boost", source="dex_boosts_top"),
        ),
        SourceSpec(
            "dex_ads",
            DEX_ADS,
            "dex_ad",
            60.0,
            lambda p: parse_dex_list(p, kind="dex_ad", source="dex_ads_latest"),
        ),
        SourceSpec(
            "pump_live",
            PUMP_LIVE,
            "pump_live",
            15.0,
            lambda p: parse_pump_list(p, kind="pump_live", source="pump_currently_live"),
            PUMP_HEADERS,
        ),
        SourceSpec(
            "pump_hot",
            PUMP_HOT,
            "pump_koth",
            30.0,
            parse_pump_hot,
            PUMP_HEADERS,
        ),
        SourceSpec(
            "pump_graduating",
            PUMP_GRADUATING,
            "pump_koth",
            15.0,
            parse_pump_graduating,
            PUMP_HEADERS,
        ),
        SourceSpec(
            "pump_movers",
            PUMP_MOVERS,
            "pump_movers",
            30.0,
            lambda p: parse_pump_list(p, kind="pump_movers", source="pump_movers"),
            PUMP_HEADERS,
        ),
        SourceSpec(
            "pump_great",
            PUMP_GREAT,
            "pump_featured",
            60.0,
            lambda p: parse_pump_list(p, kind="pump_featured", source="pump_great_coins"),
            PUMP_HEADERS,
        ),
        SourceSpec(
            "pump_runners",
            PUMP_RUNNERS,
            "pump_featured",
            60.0,
            lambda p: parse_pump_list(p, kind="pump_featured", source="pump_top_runners"),
            PUMP_HEADERS,
        ),
        SourceSpec(
            "gecko_trending",
            GECKO_TRENDING,
            "gecko_trending",
            60.0,
            parse_gecko_trending,
        ),
    )


class FirstSeenIndex:
    """(kind, mint) -> first unix ms. Reloaded from sealed files on start."""

    def __init__(self) -> None:
        self.seen: dict[tuple[str, str], int] = {}

    def note(self, kind: str, mint: str, t_ms: int) -> int | None:
        key = (kind, mint)
        prev = self.seen.get(key)
        if prev is not None:
            return None
        self.seen[key] = t_ms
        return t_ms

    def load_record(self, rec: dict[str, Any]) -> None:
        kind = rec.get("kind")
        mint = rec.get("mint")
        t_ms = rec.get("t_first_ms")
        if not isinstance(kind, str) or not isinstance(mint, str):
            return
        if not isinstance(t_ms, int):
            return
        key = (kind, mint)
        prev = self.seen.get(key)
        if prev is None or t_ms < prev:
            self.seen[key] = t_ms


def stored_attention(cand: Candidate, t_first_ms: int, t_seen_ms: int) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "v": 1,
        "type": "attention",
        "kind": cand.kind,
        "source": cand.source,
        "mint": cand.mint,
        "chain": cand.chain,
        "t_first_ms": t_first_ms,
        "t_first": iso_from_ms(t_first_ms),
        "t_seen_ms": t_seen_ms,
    }
    if cand.rank is not None:
        rec["rank"] = cand.rank
    if cand.paid_at_ms is not None:
        rec["paid_at_ms"] = cand.paid_at_ms
    for key, val in cand.extra.items():
        rec[key] = val
    return rec


class TinyDiskHold:
    """Hold writes under 20% free. Never deletes. Attention volume is tiny."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.holding = False
        self.dropped = 0
        self.free_ratio = 1.0

    def tick(self) -> None:
        total, _used, avail = filesystem_bytes(self.output_dir)
        ratio = (avail / total) if total else 1.0
        self.free_ratio = ratio
        if ratio < HEADROOM_RATIO:
            if not self.holding:
                log.error("disk_hold free_ratio=%.3f", ratio)
            self.holding = True
        elif self.holding and ratio >= RESUME_RATIO:
            log.warning("disk_resume free_ratio=%.3f", ratio)
            self.holding = False


def _attention_lines(path: Path) -> Iterable[str]:
    if path.name.endswith(".jsonl.zst"):
        zstd = shutil_which_zstd()
        if zstd is None:
            return []
        proc = subprocess.run(
            [zstd, "-d", "-c", "-q", str(path)],
            check=True,
            capture_output=True,
            timeout=60,
        )
        return proc.stdout.decode("utf-8", errors="replace").splitlines()
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def shutil_which_zstd() -> str | None:
    import shutil

    return shutil.which("zstd")


def load_seen(output_dir: Path, index: FirstSeenIndex, limit_files: int = SEEN_RELOAD_FILES) -> int:
    if not output_dir.is_dir():
        return 0
    files = [
        path
        for path in output_dir.iterdir()
        if path.is_file()
        and not path.is_symlink()
        and ATTENTION_FILE_RE.fullmatch(path.name)
    ]
    files.sort(key=lambda p: p.name)
    loaded = 0
    for path in files[-limit_files:]:
        try:
            lines = _attention_lines(path)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            log.warning("seen_reload_failed path=%s", path.name)
            continue
        for line in lines:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                index.load_record(obj)
                loaded += 1
    if loaded:
        log.info("seen_reloaded rows=%s keys=%s", loaded, len(index.seen))
    return loaded


def sweep_raw_hours(output_dir: Path, compressor: ZstdCompressor, current: str) -> None:
    if not output_dir.is_dir():
        return
    import shutil

    zstd = shutil.which("zstd")
    if zstd is None:
        return
    for path in sorted(output_dir.iterdir()):
        if path.is_symlink() or not path.is_file():
            continue
        if ATTENTION_RAW_RE.fullmatch(path.name) is None:
            continue
        stamp = path.name[len("attention-") : -len(".jsonl")]
        if stamp != current:
            compressor.enqueue(path)


def reap_attention_partials(output_dir: Path) -> None:
    if not output_dir.is_dir():
        return
    for path in output_dir.glob("attention-*.jsonl.zst.partial.*"):
        if path.is_symlink() or not path.is_file():
            continue
        pid_text = path.name.rsplit(".", 1)[-1]
        if not pid_text.isdigit():
            continue
        if Path(f"/proc/{pid_text}").exists():
            continue
        try:
            path.unlink()
        except OSError:
            continue


class HttpJson:
    def __init__(self, opener: Callable[..., Any] | None = None) -> None:
        self._opener = opener or urllib.request.urlopen
        self._ctx = ssl.create_default_context()

    def get(self, url: str, headers: dict[str, str] | None = None) -> object:
        hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, headers=hdrs)
        try:
            with self._opener(req, timeout=HTTP_TIMEOUT_S, context=self._ctx) as resp:
                raw = resp.read()
                status = getattr(resp, "status", 200)
                if status >= 400:
                    retry = _retry_after(getattr(resp, "headers", None))
                    raise FetchError(f"http {status}", status=status, retry_after_s=retry)
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            retry = _retry_after(exc.headers)
            raise FetchError(f"http {exc.code}", status=exc.code, retry_after_s=retry) from exc
        except urllib.error.URLError as exc:
            raise FetchError(str(exc.reason)[:160], status=None) from exc
        except TimeoutError as exc:
            raise FetchError("timeout", status=None) from exc
        except json.JSONDecodeError as exc:
            raise FetchError("bad_json", status=None) from exc


def _retry_after(headers: Any) -> float | None:
    if headers is None:
        return None
    raw = headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def backoff_s(errors: int, retry_after: float | None = None) -> float:
    if retry_after is not None and retry_after > 0:
        return min(BACKOFF_CAP_S, retry_after)
    delay = min(BACKOFF_CAP_S, 5.0 * (2 ** max(0, errors - 1)))
    jitter = delay * 0.1 * random.random()
    return delay + jitter


def attention_record_from_candidate(
    cand: Candidate, index: FirstSeenIndex, t_seen_ms: int
) -> dict[str, Any] | None:
    first = index.note(cand.kind, cand.mint, t_seen_ms)
    if first is None:
        return None
    return stored_attention(cand, first, t_seen_ms)


async def run_attention(
    output_dir: Path,
    stop: asyncio.Event,
    *,
    http: HttpJson | None = None,
    sources: tuple[SourceSpec, ...] | None = None,
    clock_ms: Callable[[], int] | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    compressor = ZstdCompressor()
    writer = HourlyJsonlWriter(output_dir, "attention", compressor)
    guard = TinyDiskHold(output_dir)
    index = FirstSeenIndex()
    load_seen(output_dir, index)
    current = hour_stamp(datetime.now(timezone.utc))
    reap_attention_partials(output_dir)
    sweep_raw_hours(output_dir, compressor, current)
    http = http or HttpJson()
    sources = sources or default_sources()
    clock = clock_ms or now_ms
    order_q: asyncio.Queue[str] = asyncio.Queue(maxsize=MAX_ORDER_QUEUE)
    queued_orders: set[str] = set()
    stats = {
        "polls": 0,
        "errors": 0,
        "written": 0,
        "dupes": 0,
        "http_429": 0,
    }
    errors_by: dict[str, int] = {s.name: 0 for s in sources}
    next_due: dict[str, float] = {}
    loop = asyncio.get_running_loop()
    # Stagger first fire so we do not burst every host at t=0.
    for i, spec in enumerate(sources):
        next_due[spec.name] = loop.time() + min(2.0 * i, spec.interval_s)

    def enqueue_order(mint: str) -> None:
        if mint in queued_orders:
            return
        if order_q.full():
            return
        queued_orders.add(mint)
        order_q.put_nowait(mint)

    def emit(cand: Candidate, t_seen_ms: int) -> None:
        if guard.holding:
            guard.dropped += 1
            return
        rec = attention_record_from_candidate(cand, index, t_seen_ms)
        if rec is None:
            stats["dupes"] += 1
            return
        writer.write(rec)
        stats["written"] += 1
        if cand.kind in ("dex_profile", "dex_boost", "dex_ad"):
            enqueue_order(cand.mint)

    async def poll_one(spec: SourceSpec) -> None:
        t_seen = clock()
        try:
            payload = await asyncio.to_thread(http.get, spec.url, spec.headers)
        except FetchError as exc:
            stats["errors"] += 1
            errors_by[spec.name] = errors_by.get(spec.name, 0) + 1
            if exc.status == 429:
                stats["http_429"] += 1
            delay = backoff_s(errors_by[spec.name], exc.retry_after_s)
            next_due[spec.name] = loop.time() + delay
            log.warning(
                "poll_failed source=%s status=%s backoff_s=%.1f err=%s",
                spec.name,
                exc.status,
                delay,
                str(exc)[:120],
            )
            return
        stats["polls"] += 1
        errors_by[spec.name] = 0
        next_due[spec.name] = loop.time() + spec.interval_s
        try:
            cands = spec.parse(payload)
        except Exception as exc:
            log.warning("parse_failed source=%s err=%s", spec.name, exc)
            return
        for cand in cands:
            emit(cand, t_seen)

    async def poll_loop() -> None:
        while not stop.is_set():
            now = loop.time()
            due = [s for s in sources if now >= next_due.get(s.name, 0)]
            if due:
                # One source at a time; stay polite and cheap on CPU.
                await poll_one(due[0])
                continue
            sleep_for = min((next_due[s.name] - now) for s in sources)
            sleep_for = max(0.05, min(sleep_for, 1.0))
            try:
                await asyncio.wait_for(stop.wait(), timeout=sleep_for)
            except asyncio.TimeoutError:
                pass

    async def orders_loop() -> None:
        order_errors = 0
        while not stop.is_set():
            try:
                mint = await asyncio.wait_for(order_q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            queued_orders.discard(mint)
            url = DEX_ORDERS.format(mint=mint)
            t_seen = clock()
            try:
                payload = await asyncio.to_thread(http.get, url, None)
            except FetchError as exc:
                stats["errors"] += 1
                order_errors += 1
                if exc.status == 429:
                    stats["http_429"] += 1
                delay = backoff_s(order_errors, exc.retry_after_s)
                log.warning("orders_failed mint=%s status=%s backoff_s=%.1f", mint[:8], exc.status, delay)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                enqueue_order(mint)
                continue
            order_errors = 0
            try:
                cands = parse_dex_orders(payload, mint)
            except Exception as exc:
                log.warning("orders_parse_failed mint=%s err=%s", mint[:8], exc)
                cands = []
            for cand in cands:
                emit(cand, t_seen)
            try:
                await asyncio.wait_for(stop.wait(), timeout=ORDER_GAP_S)
            except asyncio.TimeoutError:
                pass

    async def heartbeat() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                writer.rotate()
                guard.tick()
                log.info(
                    "heartbeat written=%s dupes=%s polls=%s errors=%s http_429=%s "
                    "seen=%s bytes=%s hold=%s dropped=%s free_ratio=%.3f q_orders=%s",
                    stats["written"],
                    stats["dupes"],
                    stats["polls"],
                    stats["errors"],
                    stats["http_429"],
                    len(index.seen),
                    writer.bytes,
                    int(guard.holding),
                    guard.dropped,
                    guard.free_ratio,
                    order_q.qsize(),
                )

    log.info("attention_start sources=%s output=%s", ",".join(s.name for s in sources), output_dir)
    guard.tick()
    try:
        await asyncio.gather(poll_loop(), orders_loop(), heartbeat())
    finally:
        writer.close()
        compressor.close()
        log.info(
            "attention_stopped written=%s bytes=%s dropped=%s zstd_files=%s",
            stats["written"],
            writer.bytes,
            guard.dropped,
            compressor.compressed,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MAL paper attention tape (DexScreener / pump.fun / GeckoTerminal)")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(os.environ.get("MAL_ATTENTION_OUTPUT_DIR", DEFAULT_OUTPUT_DIR)),
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("MAL_LOG_LEVEL", "INFO"),
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    stop = asyncio.Event()
    loop = asyncio.new_event_loop()

    def _request_stop(*_: object) -> None:
        log.info("shutdown_requested")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_a: _request_stop())
    try:
        loop.run_until_complete(run_attention(args.output_dir, stop))
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
