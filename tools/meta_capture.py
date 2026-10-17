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
import functools
import asyncio
import hashlib
import heapq
import io
import json
import logging
import os
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

from tools import ipfs_fetch as ipf
from tools.ipfs_fetch import BlockedUrl, HttpError, check_url

log = logging.getLogger("mal.meta_capture")

DEFAULT_WS_URL = "wss://pumpportal.fun/api/data"
SUBSCRIBE_METHOD = "subscribeNewToken"
DEFAULT_OUT = "/data/mal/meta-capture"
DEFAULT_GATEWAYS = ipf.DEFAULT_GATEWAYS  # pinned, shared with meta_stage0
KNOWN_GATEWAY_HOSTS = ipf.KNOWN_GATEWAY_HOSTS
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
    """ipfs:// and /ipfs/<path> urls become one url per configured gateway; ar:// and every other https
    uri become one direct url (SSRF guard applies at fetch); http and other schemes yield nothing."""
    return [u for _, u in ipf.plan_urls(ipf.classify_uri(uri), gateways)]


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


HttpGet = Callable[[str, float, int], Awaitable[bytes]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None  # surfaces as HTTPError 3xx; never followed


# Empty ProxyHandler: *_proxy environment variables must never route (or leak) these requests.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def _urllib_once(url: str, timeout: float, max_bytes: int, deadline: float, *,
                 http_hosts: frozenset[str], allow_private_hosts: frozenset[str],
                 local_origin: str | None = None) -> bytes:
    check_url(url, http_hosts, allow_private_hosts, local_origin)
    req = urllib.request.Request(url, headers={"User-Agent": "mal-meta-capture/1"})
    chunks: list[bytes] = []
    total = 0
    try:
        with _OPENER.open(req, timeout=max(0.1, min(timeout, deadline - time.monotonic()))) as resp:
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
        raise HttpError(exc.code, location=exc.headers.get("Location") if exc.headers else None,
                        retry_after=ipf.parse_retry_after(
                            exc.headers.get("Retry-After") if exc.headers else None)) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise FetchTimeout() from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise FetchTimeout() from exc
        if ipf.is_refused(exc):
            raise ipf.ConnectionRefused() from exc
        raise HttpError(0) from exc
    except ConnectionRefusedError as exc:
        raise ipf.ConnectionRefused() from exc
    return b"".join(chunks)


def _urllib_get(url: str, timeout: float, max_bytes: int, *,
                http_hosts: frozenset[str] = KNOWN_GATEWAY_HOSTS,
                allow_private_hosts: frozenset[str] = frozenset(),
                local_origin: str | None = None) -> bytes:
    """One GET with a wall-clock cap over the whole body. At most ipf.MAX_REDIRECTS redirects, each
    only if ipf.redirect_target accepts it; any other redirect surfaces as HttpError(3xx)."""
    deadline = time.monotonic() + timeout
    cur = url
    for hop in range(ipf.MAX_REDIRECTS + 1):
        try:
            return _urllib_once(cur, timeout, max_bytes, deadline, http_hosts=http_hosts,
                                allow_private_hosts=allow_private_hosts, local_origin=local_origin)
        except HttpError as exc:
            if exc.status in ipf.REDIRECT_CODES and hop < ipf.MAX_REDIRECTS:
                nxt = ipf.redirect_target(
                    cur, exc.location,
                    lambda u: check_url(u, frozenset(), allow_private_hosts, local_origin), local_origin)
                if nxt:
                    cur = nxt
                    continue
            raise
    raise HttpError(310)  # unreachable


def make_http_get(local_origin: str | None = None) -> HttpGet:
    """The default urllib getter; ``local_origin`` is the only loopback origin it may contact."""
    async def http_get(url: str, timeout: float, max_bytes: int) -> bytes:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(functools.partial(_urllib_get, url, timeout, max_bytes,
                                                    local_origin=local_origin)), timeout + 2
            )
        except asyncio.TimeoutError as exc:
            raise FetchTimeout() from exc
    return http_get


default_http_get = make_http_get(None)


def _status_of(exc: Exception) -> str:
    if isinstance(exc, FetchTimeout):
        return "timeout"
    if isinstance(exc, FetchTooBig):
        return "too_big"
    if isinstance(exc, ipf.ConnectionRefused):
        return "conn_refused"
    if isinstance(exc, BlockedUrl):
        return "blocked_url"
    if isinstance(exc, HttpError):
        return f"http_{exc.status}"
    return "http_0"


async def get_once_ra(url: str, http_get: HttpGet, max_bytes: int,
                      timeout: float = FETCH_TIMEOUT_S) -> tuple[str, bytes | None, float | None]:
    """One GET, no retry. Returns (status, body, retry_after seconds if the server sent one)."""
    try:
        return "ok", await http_get(url, timeout, max_bytes), None
    except Exception as exc:  # noqa: BLE001 - classify, never crash worker
        return _status_of(exc), None, getattr(exc, "retry_after", None)


async def get_once(url: str, http_get: HttpGet, max_bytes: int) -> tuple[str, bytes | None]:
    status, body, _ = await get_once_ra(url, http_get, max_bytes)
    return status, body


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
        self.ipfs_fetch_sha256 = sha256_file(Path(ipf.__file__))
        self.gateway_counts: dict[str, int] = {}
        self.source_counts: dict[str, int] = {}
        self.local_gateway: str | None = None  # origin only (not secret); None when unused
        self.git_ref = git_ref
        self.gaps: dict[str, list[dict[str, Any]]] = {}
        self.versions: dict[str, list[dict[str, Any]]] = {}
        self.error_counts: dict[str, int] = {}
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

    def _my_version(self) -> dict[str, Any]:
        return {"tool_sha256": self.tool_sha256, "ipfs_fetch_sha256": self.ipfs_fetch_sha256,
                "git_ref": self.git_ref}

    def _note_version(self, hk: str) -> list[dict[str, Any]]:
        vers = self.versions.setdefault(hk, [])
        if self._my_version() not in vers:
            vers.append(self._my_version())
        return vers

    def load_unsealed_captures(self) -> list[str]:
        """Reload gap logs and tool versions from every unsealed CAPTURE.json under --out, so a restart
        extends them instead of overwriting. Returns the hours loaded."""
        loaded = []
        for f in sorted((self.out / "capture").glob("*.CAPTURE.json")):
            hk = f.name[: -len(".CAPTURE.json")]
            if hk in self.sealed or hk in self.gaps:
                continue
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(doc, dict):
                continue
            self.gaps[hk] = [g for g in doc.get("gap_log") or [] if isinstance(g, dict)]
            vers = [v for v in doc.get("tool_versions") or [] if isinstance(v, dict)]
            if not vers and doc.get("tool_sha256"):  # file written before versions were a list
                ts, fs = doc.get("tool_sha256"), doc.get("ipfs_fetch_sha256")
                vers = [{"tool_sha256": ts if isinstance(ts, str) else None,
                         "ipfs_fetch_sha256": fs if isinstance(fs, str) else None,
                         "git_ref": doc.get("git_ref")}]
            self.versions[hk] = vers
            loaded.append(hk)
        return loaded

    def process_start(self, t_ms: int) -> None:
        """Call once when the process starts: reload unsealed gap logs, then append a process_start event
        (with this process's tool and ipfs_fetch sha256) to each unsealed hour and the current hour."""
        self.load_unsealed_captures()
        event = {"t_ms": t_ms, "event": "process_start", "tool_sha256": self.tool_sha256,
                 "ipfs_fetch_sha256": self.ipfs_fetch_sha256}
        for hk in sorted(set(self.gaps) | {hour_key(t_ms)}):
            if hk in self.sealed:
                continue
            self.gaps.setdefault(hk, []).append(dict(event))
            self.write_capture(hk)

    def write_capture(self, hk: str) -> None:
        if hk in self.sealed:
            return
        vers = self._note_version(hk)
        doc = {"hour": hk, "tool_file": "meta_capture.py",
               "tool_sha256": list(dict.fromkeys(v["tool_sha256"] for v in vers)),
               "ipfs_fetch_file": "ipfs_fetch.py",
               "ipfs_fetch_sha256": list(dict.fromkeys(v["ipfs_fetch_sha256"] for v in vers)),
               "tool_versions": vers,
               "git_ref": self.git_ref, "gap_log": self.gaps.get(hk, []),
               "error_counts": dict(self.error_counts),
               "gateway_counts": dict(self.gateway_counts),
               "local_gateway": self.local_gateway,
               "source_counts": dict(self.source_counts)}
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
        pool: ipf.GatewayPool | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        local: ipf.LocalGateway | None = None,
    ) -> None:
        self.store, self.gateways, self.http_get = store, gateways, http_get
        self.local = local
        store.local_gateway = local.origin if local else None
        self.concurrency = concurrency
        self.max_rss_mb, self.min_free_gb = max_rss_mb, min_free_gb
        self.rss_fn, self.free_fn, self.clock_ms = rss_fn, free_fn, clock_ms
        self.max_pending = max_pending
        self.pool = pool or ipf.GatewayPool(gateways)
        if local is not None:
            self.pool.add_pinned(local.key, local.rps, local.burst, local.breaker_base_s, local.breaker_max_s)
        self.sleep = sleep
        self.source_counts: dict[str, int] = {}
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
            # Type name only: a traceback or message could carry token content.
            name = type(sys.exc_info()[1]).__name__
            log.error("attempt_error type=%s", name)
            self.store.error_counts[name] = self.store.error_counts.get(name, 0) + 1
            self.store.write_capture(hour_key(self.clock_ms()))

    async def _fetch(self, uri: str, max_bytes: int,
                     image: bool = False) -> tuple[str, bytes | None, str | None, int, bool]:
        """One request at most, through the per-gateway bucket. Returns (status, body, gateway, wait_ms).

        The attempt's time is fixed by the schedule; only the gateway choice happens here: the first
        source in order that is not cooling (the local gateway when set, then the pinned public ones;
        waiting up to ipf.MAX_TOKEN_WAIT_S for its token). Exactly one request: a local timeout or 5xx
        ends this attempt and the fixed schedule retries later; a local 404 is final.
        If none can serve, status is rate_limited_all and no request is made."""
        pairs = ipf.plan_urls(ipf.classify_uri(uri), self.gateways, self.local)
        if not pairs:
            return "non_ipfs_uri", None, None, 0, False
        keys = [k for k, _ in pairs]
        urls = dict(pairs)
        total_wait = 0
        refused = False  # the local request was connection-refused (nothing transferred)
        for _pass in range(2):
            picked = self.pool.reserve(keys)
            self.store.gateway_counts = dict(self.pool.counts)
            if picked is None:
                return "rate_limited_all", None, None, total_wait, refused
            key, wait = picked
            if wait > 0:
                await self.sleep(wait)
            total_wait += int(wait * 1000)
            url = urls[key]
            is_local = self.local is not None and key == self.local.key
            timeout = self.local.timeout_for(image) if is_local and self.local else FETCH_TIMEOUT_S
            status, body, retry_after = await get_once_ra(url, self.http_get, max_bytes, timeout)
            if status == "conn_refused" and not is_local:
                status = "http_0"  # keep the public status vocabulary as it was
            self.pool.report(key, status, retry_after)
            src = ipf.LOCAL_LABEL if is_local else "public"
            self.source_counts[src] = self.source_counts.get(src, 0) + 1
            self.store.source_counts = dict(self.source_counts)
            self.store.gateway_counts = dict(self.pool.counts)
            gw = ipf.LOCAL_LABEL if is_local else urlsplit(url).netloc
            if is_local and status == "conn_refused":
                refused = True
                # Nothing was transferred, so the same attempt may go to the public gateways (a timeout
                # or 5xx ends the attempt instead). The breaker now keeps local out of the next picks.
                keys = [k for k in keys if k != key]
                if keys:
                    continue
            return status, body, gw, total_wait, refused
        return "http_0", None, None, total_wait, refused

    async def _attempt(self, item: tuple[int, int, int, dict[str, Any]]) -> None:
        due, _, attempt, row = item
        mint = str(row["mint"])
        uri = str(row.get("uri") or "")
        rec: dict[str, Any] = {"mint": mint, "attempt": attempt, "scheduled_ms": due,
                               "t_attempt_ms": self.clock_ms(), "uri_kind": uri_kind(uri),
                               **ipf.host_fields(ipf.classify_uri(uri)),
                               "gateway": None, "status": "http_0", "bytes": 0, "sha256": None}
        png: bytes | None = None
        status, body, gateway, wait_ms, refused = await self._fetch(uri, MAX_JSON_BYTES)
        rec["status"], rec["gateway"] = status, gateway
        rec["local_refused"] = refused
        if wait_ms:
            rec["wait_ms"] = wait_ms
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
                png = await self._image(rec)
        stored = self.store.add_meta(int(row["t_recv_ms"]), rec, png)
        if rec["status"] != "ok" and stored and attempt + 1 < len(SCHEDULE_MS):
            self._schedule(row, attempt + 1)

    async def _image(self, rec: dict[str, Any]) -> bytes | None:
        image = rec.get("image")
        if not isinstance(image, str) or not image:
            return None
        if not self.disk_ok():
            rec["img_status"] = "skipped_disk"
            return None
        ist, raw, _, _, _ = await self._fetch(image, MAX_IMAGE_BYTES, image=True)
        rec["img_status"] = ist
        if raw is None:
            return None
        png, info = await asyncio.to_thread(process_image, raw)  # off the event loop
        if info.pop("img_too_big", False):
            rec["img_status"] = "too_big"
        if "img_decode_error" in info:
            rec["img_status"] = "bad_image"
        rec.update(info)
        return png


# ------------------------------------------------------------------ runner
def configure_logging(level: str) -> None:
    """Root level from --log-level, but the websockets logger tree is pinned to
    WARNING: at DEBUG it logs raw vendor frames, including dropped fields."""
    logging.basicConfig(level=getattr(logging, level),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s", stream=sys.stderr)
    logging.getLogger().setLevel(getattr(logging, level))
    pin_websockets_logger()


def pin_websockets_logger() -> None:
    logging.getLogger("websockets").setLevel(logging.WARNING)


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

    pin_websockets_logger()
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
    col.store.process_start(col.clock_ms())
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
    log.info("stopped pending=%d dropped=%d local_gateway=%s source_counts=%s", len(col.heap),
             col.dropped, col.store.local_gateway, json.dumps(col.source_counts, sort_keys=True))
    return EXIT_RSS if col.rss_exceeded else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="MAL create-time metadata collector")
    p.add_argument("--ws-url", default=os.environ.get("MAL_PUMPPORTAL_WS_URL", DEFAULT_WS_URL))
    p.add_argument("--out", type=Path, default=Path(DEFAULT_OUT))
    p.add_argument("--concurrency", type=int, default=None,
                   help="default 4, or 16 when --local-gateway is set")
    p.add_argument("--gateway", action="append", help="ipfs gateway prefix; repeat, tried in order")
    p.add_argument("--gateway-rps", type=float, default=ipf.DEFAULT_RPS,
                   help="per-gateway token bucket rate")
    p.add_argument("--gateway-burst", type=float, default=ipf.DEFAULT_BURST)
    p.add_argument("--local-gateway", default=None,
                   help="http://127.0.0.1:<port> (or [::1]) kubo gateway tried first for ipfs uris; "
                        "the only loopback origin allowed")
    p.add_argument("--local-rps", type=float, default=ipf.LOCAL_RPS)
    p.add_argument("--local-burst", type=float, default=ipf.LOCAL_BURST)
    p.add_argument("--local-json-timeout-s", type=float, default=ipf.LOCAL_JSON_TIMEOUT_S)
    p.add_argument("--local-image-timeout-s", type=float, default=ipf.LOCAL_IMAGE_TIMEOUT_S)
    p.add_argument("--local-breaker-base-s", type=float, default=ipf.LOCAL_BREAKER_BASE_S)
    p.add_argument("--local-breaker-max-s", type=float, default=ipf.LOCAL_BREAKER_MAX_S)
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
    configure_logging(args.log_level)
    local = None
    if args.local_gateway:
        try:
            local = ipf.parse_local_gateway(args.local_gateway, args.local_rps, args.local_burst,
                                            args.local_json_timeout_s, args.local_image_timeout_s,
                                            args.local_breaker_base_s, args.local_breaker_max_s)
        except ValueError as exc:
            print(f"--local-gateway refused: {exc}", file=sys.stderr)
            return 2
        log.info("local_gateway origin=%s rps=%s", local.origin, local.rps)
    loop = asyncio.new_event_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    ref = args.git_ref or git_ref_of(Path(__file__).resolve().parent)
    gateways = tuple(args.gateway or DEFAULT_GATEWAYS)
    col = Collector(Store(args.out, ref), gateways,
                    pool=ipf.GatewayPool(gateways, args.gateway_rps, args.gateway_burst),
                    http_get=make_http_get(local.origin if local else None), local=local,
                    concurrency=args.concurrency or (ipf.LOCAL_CONCURRENCY if local else 4), max_rss_mb=args.max_rss_mb,
                    min_free_gb=args.min_free_gb)
    try:
        return loop.run_until_complete(run(args.ws_url, col, stop, args.duration_s))
    finally:
        loop.close()


if __name__ == "__main__":
    raise SystemExit(main())
