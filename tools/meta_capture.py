#!/usr/bin/env python3
"""Forward create-time metadata collector for mal-fast-0.

Same PumpPortal ``subscribeNewToken`` stream as ``fast_create_listener``.
No chain-node or paid-API call of any kind: the only network traffic is the
vendor websocket and plain HTTPS GETs of each token's metadata uri and image.

At receive an explicit allowlist keeps mint, name, symbol, creator, uri,
vendor time/slot, pool, txType and ``t_recv_ms``. Every other vendor field
(initial buy, sol/token amounts, reserves, market cap, price...) is dropped
before anything reaches disk. A fetch worker (bounded concurrency) pulls the
metadata JSON and image and writes hourly files:

    creates-YYYY-MM-DDTHH.jsonl   meta-YYYY-MM-DDTHH.jsonl
    img/YYYY-MM-DDTHH/<mint>.png  manifest.jsonl (one line per closed hour)

JSONL files are append-only (resumable: a restart keeps appending to the
current hour file). Images and the manifest are written tmp-then-rename.
A manifest line with ``hour_complete: false`` is a shutdown snapshot; a later
line for the same hour supersedes it.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import logging
import os
import re
import shutil
import signal
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

log = logging.getLogger("mal.meta_capture")

DEFAULT_WS_URL = "wss://pumpportal.fun/api/data"
SUBSCRIBE_METHOD = "subscribeNewToken"
DEFAULT_OUT = "/data/mal/meta-capture"
DEFAULT_GATEWAYS = (
    "https://ipfs.io/ipfs/",
    "https://cloudflare-ipfs.com/ipfs/",
    "https://gateway.pinata.cloud/ipfs/",
)
# Hosts whose /ipfs/<cid> path is rewritten onto the configured gateway list.
KNOWN_GATEWAY_HOSTS = frozenset(
    {"ipfs.io", "www.ipfs.io", "cloudflare-ipfs.com", "gateway.pinata.cloud",
     "dweb.link", "nftstorage.link", "w3s.link", "cf-ipfs.com"}
)
FETCH_TIMEOUT_S = 10.0
FETCH_ATTEMPTS = 3
MAX_JSON_BYTES = 1_000_000
MAX_IMAGE_BYTES = 5 * 1024 * 1024
DESC_MAX = 2_000
IMG_SIZE = 224

# Explicit allowlist: output key -> vendor keys tried in order.
CREATE_ALLOW: dict[str, tuple[str, ...]] = {
    "mint": ("mint",),
    "name": ("name",),
    "symbol": ("symbol",),
    "creator": ("traderPublicKey", "creator"),
    "uri": ("uri",),
    "slot": ("slot",),
    "vendor_time": ("timestamp", "blockTime"),
    "pool": ("pool",),
    "tx_type": ("txType",),
}
META_ALLOW = ("name", "symbol", "twitter", "telegram", "website", "image",
              "createdOn", "showName")
_SAFE = re.compile(r"[^A-Za-z0-9_-]")


# ---------------------------------------------------------------- receive
def create_row(payload: Mapping[str, Any], t_recv_ms: int) -> dict[str, Any] | None:
    tx = payload.get("txType")
    if isinstance(tx, str) and tx.strip().lower() not in ("", "create"):
        return None
    mint = payload.get("mint")
    if not isinstance(mint, str) or not mint.strip():
        return None
    row: dict[str, Any] = {"schema": "meta_capture_create_v0"}
    for out_key, vendor_keys in CREATE_ALLOW.items():
        value = None
        for vk in vendor_keys:
            if payload.get(vk) is not None:
                value = payload[vk]
                break
        row[out_key] = value
    row["mint"] = mint.strip()
    row["t_recv_ms"] = int(t_recv_ms)
    return row


# ------------------------------------------------------------------ fetch
def candidate_urls(uri: str, gateways: tuple[str, ...] | list[str]) -> list[str]:
    """ipfs:// and known gateway urls become one url per configured gateway."""
    if not isinstance(uri, str):
        return []
    uri = uri.strip()
    path = None
    if uri.startswith("ipfs://"):
        path = uri[len("ipfs://"):]
        if path.startswith("ipfs/"):
            path = path[len("ipfs/"):]
    elif uri.startswith(("http://", "https://")):
        m = re.match(r"https?://([^/]+)/ipfs/(.+)$", uri)
        if m and m.group(1).lower() in KNOWN_GATEWAY_HOSTS:
            path = m.group(2)
        else:
            return [uri]
    else:
        return []
    if not path:
        return []
    return [g.rstrip("/") + "/" + path for g in gateways]


def extract_meta(doc: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not isinstance(doc, dict):
        return out
    for key in META_ALLOW:
        if key in doc and doc[key] is not None:
            out[key] = doc[key]
    desc = doc.get("description")
    if isinstance(desc, str):
        out["description"] = desc[:DESC_MAX]
    return out


class FetchTimeout(Exception):
    pass


class FetchTooBig(Exception):
    pass


class HttpError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"http_{status}")
        self.status = status


HttpGet = Callable[[str, float, int], Awaitable[bytes]]


def _urllib_get(url: str, timeout: float, max_bytes: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "mal-meta-capture/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        raise HttpError(exc.code) from exc
    except TimeoutError as exc:
        raise FetchTimeout() from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise FetchTimeout() from exc
        raise HttpError(0) from exc
    if len(body) > max_bytes:
        raise FetchTooBig()
    return body


async def default_http_get(url: str, timeout: float, max_bytes: int) -> bytes:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_urllib_get, url, timeout, max_bytes), timeout + 2
        )
    except asyncio.TimeoutError as exc:
        raise FetchTimeout() from exc


def _status_of(exc: Exception) -> str:
    if isinstance(exc, FetchTimeout):
        return "timeout"
    if isinstance(exc, FetchTooBig):
        return "too_big"
    if isinstance(exc, HttpError):
        return f"http_{exc.status}"
    return "http_0"


async def fetch_bytes(
    url: str, gateways: tuple[str, ...] | list[str], http_get: HttpGet,
    max_bytes: int, attempts: int = FETCH_ATTEMPTS, timeout: float = FETCH_TIMEOUT_S,
) -> tuple[str, bytes | None]:
    """Try candidates in order, at most ``attempts`` GETs in total."""
    urls = candidate_urls(url, gateways)
    if not urls:
        return "http_0", None
    status = "http_0"
    for i in range(attempts):
        target = urls[i % len(urls)]
        try:
            return "ok", await http_get(target, timeout, max_bytes)
        except FetchTooBig:
            return "too_big", None
        except Exception as exc:  # noqa: BLE001 - classify, never crash worker
            status = _status_of(exc)
    return status, None


_pillow_warned = False


def process_image(raw: bytes) -> tuple[bytes | None, dict[str, Any]]:
    """224x224 PNG with Pillow; else (None, sha256 + size of the raw bytes)."""
    global _pillow_warned
    info: dict[str, Any] = {"img_sha256": hashlib.sha256(raw).hexdigest(), "img_bytes": len(raw)}
    try:
        from PIL import Image
    except ImportError:
        if not _pillow_warned:
            _pillow_warned = True
            log.warning("pillow_missing storing sha256+size only, no png")
        return None, info
    try:
        im = Image.open(io.BytesIO(raw))
        im = im.convert("RGBA").resize((IMG_SIZE, IMG_SIZE))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue(), info
    except Exception as exc:  # noqa: BLE001
        info["img_decode_error"] = type(exc).__name__
        return None, info


# ---------------------------------------------------------------- storage
def hour_key(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("rb") as fh:
        return sum(1 for _ in fh)


class Store:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.out.mkdir(parents=True, exist_ok=True)
        self.current: str | None = None

    def creates_path(self, hk: str) -> Path:
        return self.out / f"creates-{hk}.jsonl"

    def meta_path(self, hk: str) -> Path:
        return self.out / f"meta-{hk}.jsonl"

    def img_path(self, hk: str, mint: str) -> Path:
        return self.out / "img" / hk / f"{_SAFE.sub('_', mint)}.png"

    def _append(self, path: Path, row: Mapping[str, Any]) -> None:
        line = json.dumps(row, separators=(",", ":"), ensure_ascii=False, default=str)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()

    def roll(self, hk: str) -> None:
        """Close the previous hour (manifest) when the hour changes."""
        if self.current is not None and self.current != hk:
            self.write_manifest(self.current, complete=True)
        self.current = hk

    def add_create(self, row: Mapping[str, Any], now_ms: int) -> None:
        hk = hour_key(now_ms)
        self.roll(hk)
        self._append(self.creates_path(hk), row)

    def add_meta(self, row: Mapping[str, Any], png: bytes | None, now_ms: int) -> None:
        hk = hour_key(now_ms)
        self.roll(hk)
        if png is not None:
            atomic_write(self.img_path(hk, str(row["mint"])), png)
        self._append(self.meta_path(hk), row)

    def write_manifest(self, hk: str, complete: bool) -> dict[str, Any]:
        cp, mp = self.creates_path(hk), self.meta_path(hk)
        imgdir = self.out / "img" / hk
        entry = {
            "hour": hk,
            "hour_complete": complete,
            "creates_file": cp.name, "creates_sha256": sha256_file(cp), "creates_n": _count_lines(cp),
            "meta_file": mp.name, "meta_sha256": sha256_file(mp), "meta_n": _count_lines(mp),
            "img_n": len(list(imgdir.glob("*.png"))) if imgdir.exists() else 0,
            "written_ms": int(time.time() * 1000),
        }
        mf = self.out / "manifest.jsonl"
        prev = mf.read_bytes() if mf.exists() else b""
        line = json.dumps(entry, separators=(",", ":")) + "\n"
        atomic_write(mf, prev + line.encode())
        return entry

    def close(self) -> None:
        if self.current is not None:
            self.write_manifest(self.current, complete=False)


# ----------------------------------------------------------------- guards
def rss_mb() -> float:
    try:
        with open("/proc/self/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return 0.0


def free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / 1e9


# -------------------------------------------------------------- collector
class Collector:
    def __init__(
        self, store: Store, gateways: tuple[str, ...] = DEFAULT_GATEWAYS,
        http_get: HttpGet = default_http_get, concurrency: int = 8,
        max_rss_mb: float = 1500, min_free_gb: float = 50,
        rss_fn: Callable[[], float] = rss_mb,
        free_fn: Callable[[Path], float] = free_gb,
        clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self.store, self.gateways, self.http_get = store, gateways, http_get
        self.sem = asyncio.Semaphore(concurrency)
        self.max_rss_mb, self.min_free_gb = max_rss_mb, min_free_gb
        self.rss_fn, self.free_fn, self.clock_ms = rss_fn, free_fn, clock_ms
        self.tasks: set[asyncio.Task[None]] = set()
        self.rss_exceeded = False

    def rss_ok(self) -> bool:
        if self.rss_fn() > self.max_rss_mb:
            self.rss_exceeded = True
        return not self.rss_exceeded

    def disk_ok(self) -> bool:
        return self.free_fn(self.store.out) >= self.min_free_gb

    def on_message(self, payload: Mapping[str, Any], t_recv_ms: int) -> None:
        row = create_row(payload, t_recv_ms)
        if row is None:
            return
        self.store.add_create(row, self.clock_ms())
        if not self.rss_ok():
            return
        task = asyncio.ensure_future(self._fetch(row))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _fetch(self, row: Mapping[str, Any]) -> None:
        async with self.sem:
            try:
                await self._fetch_inner(row)
            except Exception:  # noqa: BLE001
                log.exception("fetch_worker_error mint=%s", row.get("mint"))

    async def _fetch_inner(self, row: Mapping[str, Any]) -> None:
        mint = str(row["mint"])
        out: dict[str, Any] = {"schema": "meta_capture_meta_v0", "mint": mint,
                               "uri": row.get("uri"), "t_fetch_ms": self.clock_ms()}
        status, body = await fetch_bytes(str(row.get("uri") or ""), self.gateways,
                                         self.http_get, MAX_JSON_BYTES)
        png: bytes | None = None
        doc: dict[str, Any] = {}
        if status == "ok":
            try:
                parsed = json.loads(body or b"")
                if not isinstance(parsed, dict):
                    raise ValueError("not an object")
                doc = parsed
                out.update(extract_meta(doc))
            except (ValueError, UnicodeDecodeError):
                status = "bad_json"
            image = doc.get("image") if status == "ok" else None
            if isinstance(image, str) and image:
                if not self.disk_ok():
                    out["img_status"] = "skipped_disk"
                else:
                    ist, raw = await fetch_bytes(image, self.gateways, self.http_get, MAX_IMAGE_BYTES)
                    out["img_status"] = ist
                    if raw is not None:
                        png, info = process_image(raw)
                        out.update(info)
        out["status"] = status
        self.store.add_meta(out, png, self.clock_ms())

    async def drain(self) -> None:
        while self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)


# ------------------------------------------------------------------ runner
def _normalize_messages(raw: str | bytes) -> list[dict[str, Any]]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    data = json.loads(raw)
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    return [data] if isinstance(data, dict) else []


async def run(ws_url: str, col: Collector, stop: asyncio.Event, duration_s: float | None) -> None:
    import websockets
    from websockets.exceptions import ConnectionClosed, WebSocketException

    deadline = time.monotonic() + duration_s if duration_s else None

    def done() -> bool:
        if not col.rss_ok():
            log.error("rss_guard_exit rss_mb=%.0f", col.rss_fn())
            return True
        return stop.is_set() or (deadline is not None and time.monotonic() >= deadline)

    backoff, max_backoff = 1.0, 60.0
    while not done():
        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20,
                                          close_timeout=5, open_timeout=20) as ws:
                backoff = 1.0
                await ws.send(json.dumps({"method": SUBSCRIBE_METHOD}))
                while not done():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    except asyncio.TimeoutError:
                        continue
                    t_recv_ms = time.time_ns() // 1_000_000
                    try:
                        msgs = _normalize_messages(raw)
                    except json.JSONDecodeError:
                        continue
                    for m in msgs:
                        col.on_message(m, t_recv_ms)
        except (ConnectionClosed, WebSocketException, asyncio.TimeoutError, OSError) as exc:
            log.warning("ws_error err=%s", type(exc).__name__)
        if done():
            break
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)
    await col.drain()
    col.store.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="MAL create-time metadata collector")
    p.add_argument("--ws-url", default=os.environ.get("MAL_PUMPPORTAL_WS_URL", DEFAULT_WS_URL))
    p.add_argument("--out", type=Path, default=Path(DEFAULT_OUT))
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--gateway", action="append", help="ipfs gateway prefix; repeat, tried in order")
    p.add_argument("--max-rss-mb", type=float, default=1500)
    p.add_argument("--min-free-gb", type=float, default=50)
    p.add_argument("--duration-s", type=float, default=None)
    p.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    args = p.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s", stream=sys.stderr)
    loop = asyncio.new_event_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    col = Collector(Store(args.out), tuple(args.gateway or DEFAULT_GATEWAYS),
                    concurrency=args.concurrency, max_rss_mb=args.max_rss_mb,
                    min_free_gb=args.min_free_gb)
    try:
        loop.run_until_complete(run(args.ws_url, col, stop, args.duration_s))
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
