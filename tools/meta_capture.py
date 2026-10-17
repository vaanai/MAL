#!/usr/bin/env python3
"""Forward create-time metadata collector for mal-fast-0.

Same PumpPortal ``subscribeNewToken`` stream as ``fast_create_listener``.
No chain-node or paid-API call of any kind: the only network traffic is the
vendor websocket and plain HTTPS GETs of each token's metadata uri and image.
Link strings (twitter/telegram/website) are stored, never fetched.

Receive allowlist (QP ruling 2026-10-17), exactly: signature, mint, name,
symbol, creator (traderPublicKey), uri, bondingCurveKey, slot, vendor_time,
t_recv_ms. Everything else, including pool and txType, is dropped before any
write or log line. No raw frames are stored.

Fetch schedule is fixed at receive: attempts at receive, +30 s, +2 min,
+10 min, stop at the first ok, never re-fetch, no market-based trigger.

Layout under --out, every file keyed by the create's RECEIVE hour:
    creates/H.jsonl  meta/H.jsonl  img/H/<mint>.png  capture/H.CAPTURE.json
Each hour is sealed at H+1h15m: sha256 lines appended to MANIFEST.sha256, a
line appended to MANIFEST.chain, files chmod 0440. Console log lines carry
counts and hashes only.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import heapq
import io
import json
import logging
import os
import ipaddress
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urlsplit

log = logging.getLogger("mal.meta_capture")

DEFAULT_WS_URL = "wss://pumpportal.fun/api/data"
SUBSCRIBE_METHOD = "subscribeNewToken"
DEFAULT_OUT = "/data/mal/meta-capture"
DEFAULT_GATEWAYS = (
    "https://gateway.pinata.cloud/ipfs/",
    "https://ipfs.io/ipfs/",
    "https://dweb.link/ipfs/",
)
KNOWN_GATEWAY_HOSTS = frozenset(
    {"ipfs.io", "www.ipfs.io", "cloudflare-ipfs.com", "gateway.pinata.cloud",
     "dweb.link", "nftstorage.link", "w3s.link", "cf-ipfs.com", "pump.mypinata.cloud"}
)
FETCH_TIMEOUT_S = 10.0
SCHEDULE_MS = (0, 30_000, 120_000, 600_000)  # receive, +30 s, +2 min, +10 min
SEAL_DELAY_MS = 3_600_000 + 15 * 60_000  # H + 1h15m
MAX_JSON_BYTES = 1_000_000
MAX_IMAGE_BYTES = 5 * 1024 * 1024
DESC_MAX = 2_000
IMG_SIZE = 224
STR_MAX = 512
MAX_IMAGE_PIXELS = 4_000_000
DRAIN_TIMEOUT_S = 20.0
EXIT_RSS = 3
MAX_PENDING = 50_000

# Exactly the ruled allowlist. output key -> vendor keys tried in order.
CREATE_ALLOW: dict[str, tuple[str, ...]] = {
    "signature": ("signature",),
    "mint": ("mint",),
    "name": ("name",),
    "symbol": ("symbol",),
    "creator": ("traderPublicKey",),
    "uri": ("uri",),
    "bondingCurveKey": ("bondingCurveKey",),
    "slot": ("slot",),
    "vendor_time": ("timestamp", "blockTime"),
}
META_ALLOW = ("name", "symbol", "twitter", "telegram", "website", "image",
              "createdOn", "showName")
_SAFE = re.compile(r"[^A-Za-z0-9_-]")


# ---------------------------------------------------------------- receive
def _clean_scalar(key: str, value: Any) -> Any:
    """Type check and length cap. Strings only; slot and vendor_time may be numbers."""
    if value is None or isinstance(value, bool):
        return None
    if key in ("slot", "vendor_time") and isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value[:STR_MAX]
    return None


def create_row(payload: Mapping[str, Any], t_recv_ms: int) -> dict[str, Any] | None:
    """Allowlisted create row. txType is read only to filter, never stored."""
    tx = payload.get("txType")
    if isinstance(tx, str) and tx.strip().lower() not in ("", "create"):
        return None
    mint = payload.get("mint")
    if not isinstance(mint, str) or not mint.strip():
        return None
    row: dict[str, Any] = {}
    for out_key, vendor_keys in CREATE_ALLOW.items():
        value = None
        for vk in vendor_keys:
            if payload.get(vk) is not None:
                value = payload[vk]
                break
        row[out_key] = _clean_scalar(out_key, value)
    row["mint"] = mint.strip()[:STR_MAX]
    row["t_recv_ms"] = int(t_recv_ms)
    return row


# ------------------------------------------------------------------ fetch
def uri_kind(uri: str) -> str:
    """'cid' for ipfs:// or a /ipfs/<cid> path, else 'mutable'."""
    if isinstance(uri, str) and (uri.startswith("ipfs://") or re.search(r"/ipfs/[^/]+", uri)):
        return "cid"
    return "mutable"


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
    """Allowlisted JSON fields. Links stay strings; nothing here fetches them."""
    out: dict[str, Any] = {}
    if not isinstance(doc, dict):
        return out
    for key in META_ALLOW:
        val = doc.get(key)
        if key == "showName" and isinstance(val, bool):
            out[key] = val
        elif isinstance(val, str):
            out[key] = val[:STR_MAX]
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


class BlockedUrl(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None  # surfaces as HTTPError 3xx; never followed


_OPENER = urllib.request.build_opener(_NoRedirect)


def check_url(url: str, http_hosts: frozenset[str] = KNOWN_GATEWAY_HOSTS,
              allow_private_hosts: frozenset[str] = frozenset()) -> None:
    """SSRF guard: https only (http only to listed gateways); resolved
    addresses must be public. DNS can still change between this check and the
    connect (rebinding); redirects are never followed, which bounds the rest."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("https", "http") or not host:
        raise BlockedUrl("scheme")
    if parts.scheme == "http" and host not in http_hosts and host not in allow_private_hosts:
        raise BlockedUrl("plain_http")
    if host in allow_private_hosts:
        return
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80),
                                   type=socket.SOCK_STREAM)
    except OSError as exc:
        raise HttpError(0) from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified):
            raise BlockedUrl("private_address")


def _urllib_get(url: str, timeout: float, max_bytes: int, *,
                http_hosts: frozenset[str] = KNOWN_GATEWAY_HOSTS,
                allow_private_hosts: frozenset[str] = frozenset()) -> bytes:
    check_url(url, http_hosts, allow_private_hosts)
    deadline = time.monotonic() + timeout  # wall-clock cap on the whole body
    req = urllib.request.Request(url, headers={"User-Agent": "mal-meta-capture/1"})
    chunks: list[bytes] = []
    total = 0
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            while True:
                if time.monotonic() > deadline:
                    raise FetchTimeout()
                chunk = resp.read1(65536)  # returns what has arrived; read(n) would block for n bytes
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise FetchTooBig()
                chunks.append(chunk)
    except urllib.error.HTTPError as exc:
        raise HttpError(exc.code) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise FetchTimeout() from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise FetchTimeout() from exc
        raise HttpError(0) from exc
    return b"".join(chunks)


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
    if isinstance(exc, BlockedUrl):
        return "blocked_url"
    if isinstance(exc, HttpError):
        return f"http_{exc.status}"
    return "http_0"


async def get_once(url: str, http_get: HttpGet, max_bytes: int) -> tuple[str, bytes | None]:
    """One GET, no retry. Returns (status, body)."""
    try:
        return "ok", await http_get(url, FETCH_TIMEOUT_S, max_bytes)
    except Exception as exc:  # noqa: BLE001 - classify, never crash worker
        return _status_of(exc), None


_pillow_warned = False


def process_image(raw: bytes) -> tuple[bytes | None, dict[str, Any]]:
    """224x224 PNG with Pillow; else (None, original sha256 + size)."""
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
        Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
        im = Image.open(io.BytesIO(raw))
        if im.size[0] * im.size[1] > MAX_IMAGE_PIXELS:
            info["img_too_big"] = True
            return None, info
        im = im.convert("RGBA").resize((IMG_SIZE, IMG_SIZE))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        png = buf.getvalue()
        info["img_png_sha256"] = hashlib.sha256(png).hexdigest()
        return png, info
    except Image.DecompressionBombError:
        info["img_too_big"] = True
        return None, info
    except Exception as exc:  # noqa: BLE001
        info["img_decode_error"] = type(exc).__name__
        return None, info


# ---------------------------------------------------------------- storage
def hour_key(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def hour_start_ms(hk: str) -> int:
    dt = datetime.strptime(hk, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def git_ref_of(repo_dir: Path) -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True,
                             text=True, timeout=5, check=True)
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


class Store:
    def __init__(self, out: Path, git_ref: str | None = None) -> None:
        self.out = out
        for sub in ("creates", "meta", "img", "capture"):
            (out / sub).mkdir(parents=True, exist_ok=True)
        self.tool_sha256 = sha256_file(Path(__file__))
        self.git_ref = git_ref
        self.gaps: dict[str, list[dict[str, Any]]] = {}
        self.sealed: set[str] = set()
        self.last_chain: str = "0" * 64
        chain = out / "MANIFEST.chain"
        if chain.exists():
            lines = chain.read_text().splitlines()
            for ln in lines:
                try:
                    self.sealed.add(json.loads(ln)["hour"])
                except (ValueError, KeyError):
                    pass
            if lines:
                self.last_chain = sha256_bytes(lines[-1].encode())

    def creates_path(self, hk: str) -> Path:
        return self.out / "creates" / f"{hk}.jsonl"

    def meta_path(self, hk: str) -> Path:
        return self.out / "meta" / f"{hk}.jsonl"

    def capture_path(self, hk: str) -> Path:
        return self.out / "capture" / f"{hk}.CAPTURE.json"

    def img_path(self, hk: str, mint: str) -> Path:
        return self.out / "img" / hk / f"{_SAFE.sub('_', mint)}.png"

    def _append(self, path: Path, row: Mapping[str, Any]) -> None:
        line = json.dumps(row, separators=(",", ":"), ensure_ascii=False, default=str)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()

    def add_create(self, row: Mapping[str, Any]) -> bool:
        hk = hour_key(int(row["t_recv_ms"]))
        if hk in self.sealed:
            return False
        self._append(self.creates_path(hk), row)
        return True

    def add_meta(self, create_t_recv_ms: int, row: Mapping[str, Any], png: bytes | None) -> bool:
        """Keyed by the create's RECEIVE hour, not the fetch-finish hour."""
        hk = hour_key(create_t_recv_ms)
        if hk in self.sealed:
            return False
        if png is not None:
            atomic_write(self.img_path(hk, str(row["mint"])), png)
        self._append(self.meta_path(hk), row)
        return True

    def gap_event(self, t_ms: int, event: str) -> None:
        hk = hour_key(t_ms)
        if hk in self.sealed:
            return
        self.gaps.setdefault(hk, []).append({"t_ms": t_ms, "event": event})
        self.write_capture(hk)

    def write_capture(self, hk: str) -> None:
        if hk in self.sealed:
            return
        doc = {"hour": hk, "tool_file": "meta_capture.py", "tool_sha256": self.tool_sha256,
               "git_ref": self.git_ref, "gap_log": self.gaps.get(hk, [])}
        atomic_write(self.capture_path(hk), json.dumps(doc, indent=1).encode())

    def hour_files(self, hk: str) -> list[Path]:
        files = [self.creates_path(hk), self.meta_path(hk), self.capture_path(hk)]
        files = [f for f in files if f.exists()]
        imgdir = self.out / "img" / hk
        if imgdir.exists():
            files += sorted(imgdir.glob("*.png"))
        return files

    def seal_due(self, now_ms: int) -> list[str]:
        sealed_now = []
        hours = sorted(p.stem for p in (self.out / "creates").glob("*.jsonl"))
        for hk in hours:
            if hk in self.sealed or now_ms < hour_start_ms(hk) + SEAL_DELAY_MS:
                continue
            self.seal(hk)
            sealed_now.append(hk)
        return sealed_now

    def seal(self, hk: str) -> None:
        self.write_capture(hk)
        files = self.hour_files(hk)
        lines = "".join(f"{sha256_file(f)}  {f.relative_to(self.out).as_posix()}\n" for f in files)
        mf = self.out / "MANIFEST.sha256"
        prev = mf.read_bytes() if mf.exists() else b""
        atomic_write(mf, prev + lines.encode())
        entry = json.dumps({"hour": hk, "manifest_sha256": sha256_bytes(lines.encode()),
                            "prev": self.last_chain, "n_files": len(files)},
                           separators=(",", ":"))
        cf = self.out / "MANIFEST.chain"
        prevc = cf.read_bytes() if cf.exists() else b""
        atomic_write(cf, prevc + (entry + "\n").encode())
        self.last_chain = sha256_bytes(entry.encode())
        for f in files:
            os.chmod(f, 0o440)
        self.sealed.add(hk)
        log.info("sealed hour=%s files=%d creates=%d meta=%d manifest_sha256=%s chain=%s",
                 hk, len(files), len(_read_jsonl(self.creates_path(hk))),
                 len(_read_jsonl(self.meta_path(hk))), sha256_bytes(lines.encode()), self.last_chain)


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
        http_get: HttpGet = default_http_get, concurrency: int = 4,
        max_rss_mb: float = 1500, min_free_gb: float = 50,
        rss_fn: Callable[[], float] = rss_mb,
        free_fn: Callable[[Path], float] = free_gb,
        clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        max_pending: int = MAX_PENDING,
    ) -> None:
        self.store, self.gateways, self.http_get = store, gateways, http_get
        self.concurrency = concurrency
        self.max_rss_mb, self.min_free_gb = max_rss_mb, min_free_gb
        self.rss_fn, self.free_fn, self.clock_ms = rss_fn, free_fn, clock_ms
        self.max_pending = max_pending
        self.heap: list[tuple[int, int, int, dict[str, Any]]] = []  # due, seq, attempt, row
        self.seq = 0
        self.rss_exceeded = False
        self.dropped = 0

    def rss_ok(self) -> bool:
        if self.rss_fn() > self.max_rss_mb:
            self.rss_exceeded = True
        return not self.rss_exceeded

    def disk_ok(self) -> bool:
        return self.free_fn(self.store.out) >= self.min_free_gb

    def _schedule(self, row: dict[str, Any], attempt: int) -> None:
        if len(self.heap) >= self.max_pending:
            self.dropped += 1
            self.store.add_meta(int(row["t_recv_ms"]), {
                "mint": str(row["mint"]), "attempt": attempt, "status": "dropped_backlog",
                "t_attempt_ms": self.clock_ms(), "uri_kind": uri_kind(str(row.get("uri") or "")),
                "gateway": None, "bytes": 0, "sha256": None}, None)
            return
        self.seq += 1
        due = int(row["t_recv_ms"]) + SCHEDULE_MS[attempt]  # fixed at receive time
        heapq.heappush(self.heap, (due, self.seq, attempt, row))

    def on_message(self, payload: Mapping[str, Any], t_recv_ms: int) -> None:
        row = create_row(payload, t_recv_ms)
        if row is None:
            return
        if not self.store.add_create(row):
            return
        if self.rss_ok():
            self._schedule(row, 0)

    def recover(self) -> int:
        """Rebuild unfinished schedules from disk after a restart. Returns count."""
        now = self.clock_ms()
        n = 0
        for hk in {hour_key(now), hour_key(now - SCHEDULE_MS[-1])}:
            if hk in self.store.sealed:
                continue
            attempts: dict[str, set[int]] = {}
            done: set[str] = set()
            for m in _read_jsonl(self.store.meta_path(hk)):
                attempts.setdefault(str(m.get("mint")), set()).add(int(m.get("attempt", -1)))
                if m.get("status") == "ok":
                    done.add(str(m.get("mint")))
            for row in _read_jsonl(self.store.creates_path(hk)):
                mint = str(row.get("mint"))
                if mint in done or int(row["t_recv_ms"]) + SCHEDULE_MS[-1] < now - 60_000:
                    continue
                # Only the lowest missing attempt; _attempt chains the rest, so a
                # restart cannot produce duplicate fetches.
                missing = [a for a in range(len(SCHEDULE_MS)) if a not in attempts.get(mint, set())]
                if missing:
                    self._schedule(row, missing[0])
                    n += 1
        return n

    async def step(self) -> int:
        """Run due attempts (at most ``concurrency`` at once). Returns number run."""
        now = self.clock_ms()
        batch = []
        while self.heap and self.heap[0][0] <= now and len(batch) < self.concurrency:
            batch.append(heapq.heappop(self.heap))
        if batch:
            await asyncio.gather(*(self._guarded(it) for it in batch))
        self.store.seal_due(self.clock_ms())
        return len(batch)

    async def _guarded(self, item: tuple[int, int, int, dict[str, Any]]) -> None:
        try:
            await self._attempt(item)
        except Exception:  # noqa: BLE001
            log.exception("attempt_error")

    async def _attempt(self, item: tuple[int, int, int, dict[str, Any]]) -> None:
        due, _, attempt, row = item
        mint = str(row["mint"])
        uri = str(row.get("uri") or "")
        urls = candidate_urls(uri, self.gateways)
        rec: dict[str, Any] = {"mint": mint, "attempt": attempt, "scheduled_ms": due,
                               "t_attempt_ms": self.clock_ms(), "uri_kind": uri_kind(uri),
                               "gateway": None, "status": "http_0", "bytes": 0, "sha256": None}
        png: bytes | None = None
        if urls:
            url = urls[attempt % len(urls)]
            rec["gateway"] = urlsplit(url).netloc
            status, body = await get_once(url, self.http_get, MAX_JSON_BYTES)
            rec["status"] = status
            if body is not None:
                rec["bytes"], rec["sha256"] = len(body), sha256_bytes(body)
                doc: dict[str, Any] = {}
                try:
                    parsed = json.loads(body)
                    if not isinstance(parsed, dict):
                        raise ValueError("not an object")
                    doc = parsed
                except (ValueError, UnicodeDecodeError, RecursionError):
                    rec["status"] = "bad_json"
                if rec["status"] == "ok":
                    rec.update(extract_meta(doc))
                    png = await self._image(rec, attempt)
        stored = self.store.add_meta(int(row["t_recv_ms"]), rec, png)
        if rec["status"] != "ok" and stored and attempt + 1 < len(SCHEDULE_MS):
            self._schedule(row, attempt + 1)

    async def _image(self, rec: dict[str, Any], attempt: int) -> bytes | None:
        image = rec.get("image")
        if not isinstance(image, str) or not image:
            return None
        if not self.disk_ok():
            rec["img_status"] = "skipped_disk"
            return None
        urls = candidate_urls(image, self.gateways)
        if not urls:
            rec["img_status"] = "http_0"
            return None
        ist, raw = await get_once(urls[attempt % len(urls)], self.http_get, MAX_IMAGE_BYTES)
        rec["img_status"] = ist
        if raw is None:
            return None
        png, info = await asyncio.to_thread(process_image, raw)  # off the event loop
        if info.pop("img_too_big", False):
            rec["img_status"] = "too_big"
        rec.update(info)
        return png


# ------------------------------------------------------------------ runner
def _normalize_messages(raw: str | bytes) -> list[dict[str, Any]]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    data = json.loads(raw)
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    return [data] if isinstance(data, dict) else []


async def run(ws_url: str, col: Collector, stop: asyncio.Event, duration_s: float | None,
              drain_timeout_s: float = DRAIN_TIMEOUT_S) -> int:
    import websockets
    from websockets.exceptions import ConnectionClosed, WebSocketException

    deadline = time.monotonic() + duration_s if duration_s else None

    def done() -> bool:
        if not col.rss_ok():
            log.error("rss_guard_exit rss_mb=%.0f", col.rss_fn())
            return True
        return stop.is_set() or (deadline is not None and time.monotonic() >= deadline)

    async def worker() -> None:
        while not done():
            if await col.step() == 0:
                await asyncio.sleep(0.5)

    # Earlier unsealed hours get their manifest lines now (supersede), then
    # unfinished schedules are rebuilt from disk.
    col.store.seal_due(col.clock_ms())
    log.info("recovered_attempts n=%d", col.recover())
    wtask = asyncio.ensure_future(worker())
    backoff, max_backoff = 1.0, 60.0
    while not done():
        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20,
                                          close_timeout=5, open_timeout=20) as ws:
                backoff = 1.0
                col.store.gap_event(col.clock_ms(), "connected")
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
            col.store.gap_event(col.clock_ms(), f"disconnected:{type(exc).__name__}")
            log.warning("ws_error err=%s", type(exc).__name__)
        if done():
            break
        try:
            await asyncio.wait_for(stop.wait(), timeout=backoff)  # wakes on stop
        except asyncio.TimeoutError:
            pass
        backoff = min(backoff * 2, max_backoff)
    try:
        await asyncio.wait_for(wtask, timeout=drain_timeout_s)
    except asyncio.TimeoutError:
        log.warning("drain_timeout cancelled in-flight attempts")
    log.info("stopped pending=%d dropped=%d", len(col.heap), col.dropped)
    return EXIT_RSS if col.rss_exceeded else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="MAL create-time metadata collector")
    p.add_argument("--ws-url", default=os.environ.get("MAL_PUMPPORTAL_WS_URL", DEFAULT_WS_URL))
    p.add_argument("--out", type=Path, default=Path(DEFAULT_OUT))
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--gateway", action="append", help="ipfs gateway prefix; repeat, tried in order")
    p.add_argument("--max-rss-mb", type=float, default=1500)
    p.add_argument("--min-free-gb", type=float, default=50)
    p.add_argument("--duration-s", type=float, default=None)
    p.add_argument("--git-ref", default=None)
    p.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    args = p.parse_args(argv)
    try:
        import websockets  # noqa: F401
    except ImportError:
        print("websockets missing: pip install -r scripts/mal-core/requirements-fast-create.txt",
              file=sys.stderr)
        return 2
    logging.basicConfig(level=getattr(logging, args.log_level),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s", stream=sys.stderr)
    loop = asyncio.new_event_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    ref = args.git_ref or git_ref_of(Path(__file__).resolve().parent)
    col = Collector(Store(args.out, ref), tuple(args.gateway or DEFAULT_GATEWAYS),
                    concurrency=args.concurrency, max_rss_mb=args.max_rss_mb,
                    min_free_gb=args.min_free_gb)
    try:
        return loop.run_until_complete(run(args.ws_url, col, stop, args.duration_s))
    finally:
        loop.close()


if __name__ == "__main__":
    raise SystemExit(main())
