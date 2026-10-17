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
import fcntl
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
import threading
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
EXIT_LOCKED = 5   # another meta_capture holds <out>/.lock
EXIT_STALL = 6    # watchdog: creates arrive but no meta row written (or worker restarts exhausted)
LOCK_NAME = ".lock"
WATCHDOG_S = 60.0
HEARTBEAT_S = 300.0
STALL_WINDOW_S = 600.0
MAX_WORKER_RESTARTS = 5
STEP_ERROR_SLEEP_S = 1.0
ATTEMPT_DEADLINE_S = 180.0  # wall-clock cap on one whole attempt (json fetch + image fetch + waits)
MAX_PENDING = 50_000
# Hours written while several meta_capture processes shared one --out (incident 2026-10-17).
MULTI_WRITER_HOURS = ("2026-10-17T20", "2026-10-17T21", "2026-10-17T22")
MULTI_WRITER_FLAG = "multi-writer 2026-10-17T21:09:01Z\u201322:35:45Z"
SEG2_REASON = "multi-writer"
HOUR_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}")

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
    # pid in the name: two processes on one --out must not share a tmp file (the loser's os.replace
    # raised FileNotFoundError, which used to kill the worker task silently).
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
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


class AlreadyRunning(Exception):
    pass


def acquire_single_instance(out: Path) -> int:
    """Exclusive non-blocking flock on <out>/.lock; the pid is written into it. Returns
    the open fd, which the caller keeps for the life of the process (not inherited by children).
    Raises AlreadyRunning if another process holds it."""
    out.mkdir(parents=True, exist_ok=True)
    fd = os.open(out / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        raise AlreadyRunning() from exc
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    return fd


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
        self.segment2 = (out / "MANIFEST-2.chain").exists()
        for name in ("MANIFEST.chain", "MANIFEST-2.chain"):
            chain = out / name
            if not chain.exists():
                continue
            lines = chain.read_text().splitlines()
            for ln in lines:
                try:
                    self.sealed.add(json.loads(ln)["hour"])
                except (ValueError, KeyError, TypeError):
                    pass
            if lines:
                self.last_chain = sha256_bytes(lines[-1].encode())

    @property
    def manifest_name(self) -> str:
        return "MANIFEST-2.sha256" if self.segment2 else "MANIFEST.sha256"

    @property
    def chain_name(self) -> str:
        return "MANIFEST-2.chain" if self.segment2 else "MANIFEST.chain"

    def init_segment2(self, first_hour: str) -> bool:
        """First v4 start: open chain segment 2. Hashes (never edits) the old MANIFEST.sha256 and
        MANIFEST.chain, starts MANIFEST-2.* with a segment_start entry, re-hashes the files of
        already sealed hours >= first_hour (hash only, no content parsing), and routes all later
        sealing to segment 2. No-op (False) if MANIFEST-2.chain exists. The chain file is written
        last, so a crash before it leaves no segment and the next start redoes this."""
        if not HOUR_RE.fullmatch(first_hour):
            raise ValueError("first hour must be YYYY-MM-DDTHH")
        hour_start_ms(first_hour)
        if self.segment2:
            return False
        start = {"segment_start": True, "prev_chain_sha256": sha256_file(self.out / "MANIFEST.chain"),
                 "prev_manifest_sha256": sha256_file(self.out / "MANIFEST.sha256"),
                 "first_hour": first_hour, "reason": SEG2_REASON,
                 "flagged_hours": {hk: MULTI_WRITER_FLAG for hk in MULTI_WRITER_HOURS}}
        start_line = json.dumps(start, separators=(",", ":"))
        chain_lines = [start_line]
        prev = sha256_bytes(start_line.encode())
        man = start_line + "\n"
        for hk in sorted(h for h in self.sealed if h >= first_hour):
            files = self.hour_files(hk)
            lines = "".join(f"{sha256_file(f)}  {f.relative_to(self.out).as_posix()}\n" for f in files)
            man += lines
            entry = json.dumps({"hour": hk, "manifest_sha256": sha256_bytes(lines.encode()),
                                "prev": prev, "n_files": len(files), "rehash": True},
                               separators=(",", ":"))
            chain_lines.append(entry)
            prev = sha256_bytes(entry.encode())
        self.segment2 = True
        self._write_manifest_file(self.out / "MANIFEST-2.sha256", man.encode())
        self._write_manifest_file(self.out / "MANIFEST-2.chain", ("\n".join(chain_lines) + "\n").encode())
        self.last_chain = prev
        log.info("segment2_started first_hour=%s rehashed=%d", first_hour, len(chain_lines) - 1)
        return True

    def note_flags_for_sealed(self) -> None:
        """Flagged hours whose CAPTURE.json is already sealed cannot carry the flag: write one line per
        hour to MANIFEST-2.notes instead (idempotent). Sealed files are never modified."""
        if not self.segment2:
            return
        notes = self.out / "MANIFEST-2.notes"
        have = notes.read_text() if notes.exists() else ""
        add = ""
        for hk in MULTI_WRITER_HOURS:
            if hk in self.sealed and f'"hour":"{hk}"' not in have:
                add += json.dumps({"hour": hk, "pointer": "MANIFEST-2.chain segment_start.flagged_hours"}, separators=(",", ":"),
                                  ensure_ascii=False) + "\n"
        if add:
            self._write_manifest_file(notes, (have + add).encode())

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
        for hk in MULTI_WRITER_HOURS:  # flagged hours that are still open get the flag now
            if hk not in self.sealed and self.capture_path(hk).exists():
                self.write_capture(hk)
        self.note_flags_for_sealed()

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
        if hk in MULTI_WRITER_HOURS:
            doc["flags"] = [MULTI_WRITER_FLAG]
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

    def _write_manifest_file(self, path: Path, data: bytes,
                             on_written: Callable[[], None] | None = None) -> None:
        """Segment-2 files are replaced atomically and left mode 0440. Segment-1 files (legacy mode,
        tests only) are never chmod'ed here."""
        atomic_write(path, data)
        if on_written is not None:
            on_written()  # state that must follow the write even if the chmod below fails
        if self.segment2 and path.name.startswith("MANIFEST-2"):
            os.chmod(path, 0o440)

    def _hour_manifest_line(self, line: str, hk: str) -> bool:
        path = line.split("  ", 1)[-1]
        return (path.startswith((f"creates/{hk}.", f"meta/{hk}.", f"capture/{hk}.", f"img/{hk}/")))

    def seal(self, hk: str) -> None:
        """Exactly one manifest block and one chain entry per hour, however often it is retried. A
        failure after the manifest write leaves a block with no chain entry; the retry replaces that
        block. A failure after the chain write: the retry only finishes chmod and bookkeeping."""
        cf = self.out / self.chain_name
        chain_lines = cf.read_text().splitlines() if cf.exists() else []
        have_entry = any(self._chain_hour(ln) == hk for ln in chain_lines)
        files: list[Path]
        manifest_sha = ""
        if have_entry:
            files = self.hour_files(hk)
            mine = next(json.loads(ln) for ln in chain_lines if self._chain_hour(ln) == hk)
            manifest_sha = str(mine.get("manifest_sha256"))
            self.last_chain = sha256_bytes(chain_lines[-1].encode())  # backstop after a failed attempt
            if self.segment2 and cf.exists():
                os.chmod(cf, 0o440)
        else:
            self.write_capture(hk)
            files = self.hour_files(hk)
            lines = "".join(f"{sha256_file(f)}  {f.relative_to(self.out).as_posix()}\n" for f in files)
            mf = self.out / self.manifest_name
            prev = mf.read_text() if mf.exists() else ""
            kept = "".join(ln + "\n" for ln in prev.splitlines() if not self._hour_manifest_line(ln, hk))
            self._write_manifest_file(mf, (kept + lines).encode())
            entry = json.dumps({"hour": hk, "manifest_sha256": sha256_bytes(lines.encode()),
                                "prev": self.last_chain, "n_files": len(files)},
                               separators=(",", ":"))
            manifest_sha = sha256_bytes(lines.encode())

            def advance() -> None:
                self.last_chain = sha256_bytes(entry.encode())
            self._write_manifest_file(cf, ("".join(ln + "\n" for ln in chain_lines) + entry + "\n").encode(),
                                      on_written=advance)
        for f in files:
            os.chmod(f, 0o440)
        self.sealed.add(hk)
        log.info("sealed hour=%s files=%d creates=%d meta=%d manifest_sha256=%s chain=%s",
                 hk, len(files), len(_read_jsonl(self.creates_path(hk))),
                 len(_read_jsonl(self.meta_path(hk))), manifest_sha, self.last_chain)

    @staticmethod
    def _chain_hour(line: str) -> str | None:
        try:
            h = json.loads(line).get("hour")
        except (ValueError, AttributeError):
            return None
        return h if isinstance(h, str) else None


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
        mono: Callable[[], float] = time.monotonic,
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
        self.mono = mono
        self.attempt_deadline_s = ATTEMPT_DEADLINE_S
        self.start_mono = mono()
        self.creates_received = 0
        self.last_create_mono: float | None = None
        self.last_meta_mono: float | None = None
        self.attempt_counts: dict[str, int] = {}
        self.recorded: dict[tuple[str, int], float] = {}  # (mint, attempt) with a row written
        self.inflight_rec: dict[tuple[str, int], dict[str, Any]] = {}
        self.inflight = {"local": 0, "public": 0}

    def rss_ok(self) -> bool:
        if self.rss_fn() > self.max_rss_mb:
            self.rss_exceeded = True
        return not self.rss_exceeded

    def disk_ok(self) -> bool:
        return self.free_fn(self.store.out) >= self.min_free_gb

    def _add_meta(self, create_t_recv_ms: int, row: Mapping[str, Any], png: bytes | None) -> bool:
        stored = self.store.add_meta(create_t_recv_ms, row, png)
        if stored:
            self.last_meta_mono = self.mono()
        return stored

    def _schedule(self, row: dict[str, Any], attempt: int) -> None:
        if len(self.heap) >= self.max_pending:
            self.dropped += 1
            self._add_meta(int(row["t_recv_ms"]), {
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
        self.creates_received += 1
        self.last_create_mono = self.mono()
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

    @staticmethod
    def _key(item: tuple[int, int, int, dict[str, Any]]) -> tuple[str, int]:
        return str(item[3].get("mint")), item[2]

    def _mark_recorded(self, key: tuple[str, int]) -> None:
        self.recorded[key] = self.mono()
        self.inflight_rec.pop(key, None)
        if len(self.recorded) > 200_000:  # attempts older than an hour can never come back
            cut = self.mono() - 3600
            self.recorded = {k: v for k, v in self.recorded.items() if v >= cut}

    async def _guarded(self, item: tuple[int, int, int, dict[str, Any]]) -> None:
        try:
            await asyncio.wait_for(self._attempt(item), timeout=self.attempt_deadline_s)
        except asyncio.CancelledError:
            # SIGTERM drain / watchdog exit: the request may be in flight. Record it so that recover()
            # never re-sends it, then let the cancellation continue.
            try:
                self._record_failed_attempt(item, "cancelled_in_flight", schedule=False)
            except Exception:  # noqa: BLE001
                log.error("attempt_row_write type=%s", type(sys.exc_info()[1]).__name__)
            raise
        except Exception:  # noqa: BLE001
            # Type name only: a traceback or message could carry token content.
            name = type(sys.exc_info()[1]).__name__
            log.error("attempt_error type=%s", name)
            self.store.error_counts[name] = self.store.error_counts.get(name, 0) + 1
            status = "timeout_cap" if name == "TimeoutError" else f"error_{name}"
            try:
                # A request may already have gone out: record the attempt so that recover() after a
                # restart never re-sends it (one request per attempt), and keep the fixed schedule.
                self._record_failed_attempt(item, status)
            except Exception:  # noqa: BLE001
                log.error("attempt_row_write type=%s", type(sys.exc_info()[1]).__name__)
            try:
                self.store.write_capture(hour_key(self.clock_ms()))
            except Exception:  # noqa: BLE001 - the error path must never raise out of the worker
                log.error("attempt_error_capture_write type=%s", type(sys.exc_info()[1]).__name__)

    def _record_failed_attempt(self, item: tuple[int, int, int, dict[str, Any]], status: str,
                               schedule: bool = True) -> None:
        """One row per attempt. If the attempt already has a row, only the caller's log and count
        remain: no second row, never a schedule advance. If the JSON fetch came back ok, that ok row is
        written with img_status=status and nothing further is scheduled for the mint."""
        due, _, attempt, row = item
        key = self._key(item)
        if key in self.recorded:
            return
        rec = self.inflight_rec.get(key)
        self._mark_recorded(key)
        self.attempt_counts[status] = self.attempt_counts.get(status, 0) + 1
        if rec is not None and rec.get("status") == "ok":
            rec["img_status"] = status
            self._add_meta(int(row["t_recv_ms"]), rec, None)
            return
        stored = self._add_meta(int(row["t_recv_ms"]), {
            "mint": str(row["mint"]), "attempt": attempt, "scheduled_ms": due,
            "t_attempt_ms": self.clock_ms(), "uri_kind": uri_kind(str(row.get("uri") or "")),
            "gateway": None, "status": status, "bytes": 0, "sha256": None}, None)
        if stored and schedule and attempt + 1 < len(SCHEDULE_MS):
            self._schedule(row, attempt + 1)

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
            side = "local" if is_local else "public"
            self.inflight[side] += 1
            try:
                status, body, retry_after = await get_once_ra(url, self.http_get, max_bytes, timeout)
            finally:
                self.inflight[side] -= 1
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
        self.inflight_rec[(mint, attempt)] = rec
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
        self.attempt_counts[str(rec["status"])] = self.attempt_counts.get(str(rec["status"]), 0) + 1
        key = (mint, attempt)
        try:
            stored = self._add_meta(int(row["t_recv_ms"]), rec, png)
        finally:
            self._mark_recorded(key)  # later errors for this attempt must not advance the schedule
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


# ----------------------------------------------------- watchdog + heartbeat
class Supervisor:
    """Keeps one worker task alive. A task that ended with an exception is logged (type only) and
    restarted, at most ``max_restarts`` times; after that ``exhausted`` is True."""

    def __init__(self, factory: Callable[[], Awaitable[None]], max_restarts: int = MAX_WORKER_RESTARTS) -> None:
        self.factory, self.max_restarts = factory, max_restarts
        self.restarts = 0
        self.exhausted = False
        self.task: asyncio.Future = asyncio.ensure_future(factory())

    def check(self) -> None:
        if not self.task.done() or self.task.cancelled() or self.exhausted:
            return
        exc = self.task.exception()
        if exc is None:
            return
        log.error("worker_died type=%s restarts=%d", type(exc).__name__, self.restarts)
        if self.restarts >= self.max_restarts:
            self.exhausted = True
            return
        self.restarts += 1
        self.task = asyncio.ensure_future(self.factory())


def stalled(col: Collector, window_s: float = STALL_WINDOW_S) -> bool:
    """Creates arrived within the window, but no meta row was written for the whole window."""
    now = col.mono()
    if col.last_create_mono is None or now - col.last_create_mono > window_s:
        return False
    last_meta = col.last_meta_mono if col.last_meta_mono is not None else col.start_mono
    return now - last_meta >= window_s


def heartbeat_line(col: Collector) -> str:
    """Counts only: never a name, uri or content."""
    local = col.local
    breaker = "none"
    if local is not None:
        left = col.pool.cooldown_left(local.key)
        breaker = f"open_{left:.0f}s" if left > 0 else "closed"
    return ("heartbeat creates=%d attempts=%s queue=%d inflight_local=%d inflight_public=%d breaker=%s "
            "dropped=%d" % (col.creates_received, json.dumps(col.attempt_counts, sort_keys=True),
                            len(col.heap), col.inflight["local"], col.inflight["public"], breaker,
                            col.dropped))


async def monitor(col: Collector, sup: Supervisor, stop: asyncio.Event, flags: dict[str, bool], *,
                  watchdog_s: float = WATCHDOG_S, heartbeat_s: float = HEARTBEAT_S,
                  stall_window_s: float = STALL_WINDOW_S) -> None:
    """Watchdog + heartbeat. Limit (accepted): it runs on the event loop, so it does not detect a
    blocked loop; the hourly lag check covers that."""
    next_hb = col.mono() + heartbeat_s
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=watchdog_s)
            return
        except asyncio.TimeoutError:
            pass
        sup.check()
        if sup.exhausted or stalled(col, stall_window_s):
            log.error("watchdog_exit reason=%s creates=%d attempts=%s queue=%d",
                      "worker_restarts_exhausted" if sup.exhausted else "no_meta_rows",
                      col.creates_received, json.dumps(col.attempt_counts, sort_keys=True), len(col.heap))
            flags["stalled"] = True
            stop.set()
            return
        if col.mono() >= next_hb:
            next_hb = col.mono() + heartbeat_s
            log.info(heartbeat_line(col))


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


async def worker_loop(col: Collector, done: Callable[[], bool]) -> None:
    """The one fetch worker. A step that raises (seal, disk, anything outside the per-attempt guard)
    is logged by type and retried: this used to end the task silently while the websocket kept
    delivering creates (2026-10-17 stall)."""
    while not done():
        try:
            n = await col.step()
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            log.error("worker_step_error type=%s", name)
            col.store.error_counts[name] = col.store.error_counts.get(name, 0) + 1
            n = 0
            await asyncio.sleep(STEP_ERROR_SLEEP_S)
        if n == 0:
            await asyncio.sleep(0.5)


async def run(ws_url: str, col: Collector, stop: asyncio.Event, duration_s: float | None,
              drain_timeout_s: float = DRAIN_TIMEOUT_S, watchdog_s: float = WATCHDOG_S,
              heartbeat_s: float = HEARTBEAT_S, stall_window_s: float = STALL_WINDOW_S) -> int:
    import websockets
    from websockets.exceptions import ConnectionClosed, WebSocketException

    pin_websockets_logger()
    deadline = time.monotonic() + duration_s if duration_s else None

    def done() -> bool:
        if not col.rss_ok():
            log.error("rss_guard_exit rss_mb=%.0f", col.rss_fn())
            return True
        return stop.is_set() or (deadline is not None and time.monotonic() >= deadline)

    # Earlier unsealed hours get their manifest lines now (supersede), then
    # unfinished schedules are rebuilt from disk.
    col.store.process_start(col.clock_ms())
    col.store.seal_due(col.clock_ms())
    log.info("recovered_attempts n=%d", col.recover())
    flags = {"stalled": False}
    sup = Supervisor(lambda: worker_loop(col, done))
    mtask = asyncio.ensure_future(monitor(col, sup, stop, flags, watchdog_s=watchdog_s,
                                          heartbeat_s=heartbeat_s, stall_window_s=stall_window_s))
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
        await asyncio.wait_for(sup.task, timeout=drain_timeout_s)
    except asyncio.TimeoutError:
        log.warning("drain_timeout cancelled in-flight attempts")
    except Exception:  # noqa: BLE001 - a dead worker is already logged by the supervisor
        pass
    mtask.cancel()
    log.info("stopped pending=%d dropped=%d local_gateway=%s source_counts=%s", len(col.heap),
             col.dropped, col.store.local_gateway, json.dumps(col.source_counts, sort_keys=True))
    if flags["stalled"]:
        return EXIT_STALL
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
    p.add_argument("--segment2-first-hour", default=None,
                   help="YYYY-MM-DDTHH; required the first time (no MANIFEST-2.chain yet): opens chain "
                        "segment 2 and re-hashes sealed hours from this hour onward")
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
    try:
        lock_fd = acquire_single_instance(args.out)  # held until the process exits
    except AlreadyRunning:
        print("another meta_capture holds the lock on --out; refusing to start", file=sys.stderr)
        return EXIT_LOCKED
    log.info("lock_acquired pid=%d", os.getpid())
    try:
        return _main_locked(args)
    finally:
        os.close(lock_fd)


def _main_locked(args: argparse.Namespace) -> int:
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
    store = Store(args.out, ref)
    if not store.segment2:
        if not args.segment2_first_hour:
            print("--segment2-first-hour is required: MANIFEST-2.chain does not exist yet", file=sys.stderr)
            return 2
        try:
            store.init_segment2(args.segment2_first_hour)
        except ValueError:
            print("--segment2-first-hour must look like 2026-10-17T23", file=sys.stderr)
            return 2
    col = Collector(store, gateways,
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
