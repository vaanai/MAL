"""Shared metadata-fetch rules for tools/meta_capture.py (Job A) and tools/meta_stage0.py (Job B).

No network and no chain-node call lives here. This module holds only:
  * the pinned gateway list and pinned host allowlists;
  * uri classification (ipfs_cid / arweave / allowlisted:<host> / other) and the rewrite onto gateways;
  * the SSRF guard (https only to gateways, resolved addresses must be public);
  * the redirect rule (at most 2 hops, each target re-checked);
  * a per-gateway token bucket with 429 cooldown (``GatewayPool``).

The pool is clock-injectable so tests need no sleeping and no sockets.
"""

from __future__ import annotations

import email.utils
import ipaddress
import re
import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable, Sequence
from urllib.parse import urljoin, urlsplit

# Pinned (QP ruling 2026-10-17): order matters, both tools use this one tuple.
DEFAULT_GATEWAYS = (
    "https://gateway.pinata.cloud/ipfs/",
    "https://ipfs.io/ipfs/",
    "https://dweb.link/ipfs/",
)
# Hosts whose /ipfs/<path> urls are rewritten onto DEFAULT_GATEWAYS (never contacted directly).
KNOWN_GATEWAY_HOSTS = frozenset(
    {"ipfs.io", "www.ipfs.io", "cloudflare-ipfs.com", "gateway.pinata.cloud",
     "dweb.link", "nftstorage.link", "w3s.link", "cf-ipfs.com", "pump.mypinata.cloud"}
)
# Arweave and Irys (Arweave bundler) public gateways, fetched directly. Source: public gateway docs
# (arweave.net, ar.io, ArDrive, Irys); ar:// rewrites to https://arweave.net/<id>.
ARWEAVE_HOSTS = frozenset(
    {"arweave.net", "www.arweave.net", "ar-io.net", "ardrive.net", "arweave.dev", "g8way.io",
     "gateway.irys.xyz", "node1.irys.xyz", "node2.irys.xyz"}
)
# Non-IPFS/Arweave hosts that launchpad token metadata and images commonly point at, fetched directly
# through the SSRF guard. Everything not listed here (and not IPFS/Arweave) stays refused.
METADATA_HOSTS = frozenset(
    {"ipfs.filebase.io", "gateway.lighthouse.storage", "i.imgur.com",
     "raw.githubusercontent.com", "shdw-drive.genesysgo.net"}
)
REDIRECT_GATEWAY_HOSTS = KNOWN_GATEWAY_HOSTS | ARWEAVE_HOSTS

REDIRECT_CODES = (301, 302, 303, 307, 308)
MAX_REDIRECTS = 2

# Rate limiting defaults.
DEFAULT_RPS = 1.0
DEFAULT_BURST = 2.0
MAX_TOKEN_WAIT_S = 5.0
RETRY_AFTER_CAP_S = 300.0
RETRY_AFTER_DEFAULT_S = 60.0

_IPFS_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~%/-]*$")
_AR_ID = re.compile(r"^[A-Za-z0-9_-]{20,}(/[A-Za-z0-9._~%/-]*)?$")
_CID_V0 = re.compile(r"^Qm[1-9A-HJ-NP-Za-km-z]{44}$")
_CID_V1 = re.compile(r"^b[a-z2-7]{50,}$")


class BlockedUrl(Exception):
    pass


class HttpError(Exception):
    def __init__(self, status: int, location: str | None = None,
                 retry_after: float | None = None) -> None:
        super().__init__(f"http_{status}")
        self.status = status
        self.location = location
        self.retry_after = retry_after


# ------------------------------------------------------------- classification
def is_valid_cid(text: str) -> bool:
    return bool(_CID_V0.match(text) or _CID_V1.match(text))


def _ipfs_path_ok(path: str) -> bool:
    return bool(_IPFS_PATH.match(path)) and ".." not in path.split("/")


@dataclass(frozen=True)
class UriPlan:
    host_class: str            # ipfs_cid | arweave | allowlisted:<host> | other
    host: str | None           # hostname, set for allowlisted and other only
    ipfs_path: str | None = None
    direct_url: str | None = None

    @property
    def fetchable(self) -> bool:
        return self.ipfs_path is not None or self.direct_url is not None


def _subdomain_cid(host: str) -> bool:
    """<cid>.ipfs.<known gateway> subdomain form, which gateways redirect /ipfs/<cid> to."""
    head, sep, rest = host.partition(".ipfs.")
    return bool(sep) and is_valid_cid(head) and rest in KNOWN_GATEWAY_HOSTS


def classify_uri(uri: object) -> UriPlan:
    other = UriPlan("other", None)
    if not isinstance(uri, str):
        return other
    uri = uri.strip()
    if uri.startswith("ipfs://"):
        path = uri[len("ipfs://"):].split("?", 1)[0].split("#", 1)[0]
        if path.startswith("ipfs/"):
            path = path[len("ipfs/"):]
        return UriPlan("ipfs_cid", None, ipfs_path=path) if _ipfs_path_ok(path) else other
    if uri.startswith("ar://"):
        rest = uri[len("ar://"):].split("?", 1)[0].split("#", 1)[0]
        if _AR_ID.match(rest) and ".." not in rest.split("/"):
            return UriPlan("arweave", None, direct_url="https://arweave.net/" + rest)
        return other
    if not uri.startswith(("http://", "https://")):
        return other
    parts = urlsplit(uri)
    try:
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return other
    if not host or parts.username or parts.password:
        return other
    if parts.path.startswith("/ipfs/") and _ipfs_path_ok(parts.path[len("/ipfs/"):]):
        return UriPlan("ipfs_cid", None, ipfs_path=parts.path[len("/ipfs/"):])
    if _subdomain_cid(host):
        tail = parts.path.lstrip("/")
        cid = host.partition(".ipfs.")[0]
        path = cid + ("/" + tail if tail else "")
        if _ipfs_path_ok(path):
            return UriPlan("ipfs_cid", None, ipfs_path=path)
    if host in ARWEAVE_HOSTS or host in METADATA_HOSTS:
        cls = "arweave" if host in ARWEAVE_HOSTS else f"allowlisted:{host}"
        if port not in (None, 443) or ".." in parts.path.split("/"):
            return UriPlan("other", host[:253])
        url = f"https://{host}{parts.path or '/'}" + (f"?{parts.query}" if parts.query else "")
        return UriPlan(cls, host if cls != "arweave" else None, direct_url=url)
    return UriPlan("other", host[:253])


def host_fields(plan: UriPlan) -> dict[str, str]:
    """Record fields: the class always, the hostname only for ``other`` (allowlisted carries it already)."""
    out = {"uri_host_class": plan.host_class}
    if plan.host_class == "other" and plan.host:
        out["uri_host"] = plan.host
    return out


def plan_urls(plan: UriPlan, gateways: Sequence[str]) -> list[tuple[str, str]]:
    """(pool key, url) pairs. IPFS: one per pinned gateway in order. Direct: one, keyed by its host."""
    if plan.ipfs_path is not None:
        return [(g, g.rstrip("/") + "/" + plan.ipfs_path) for g in gateways]
    if plan.direct_url is not None:
        return [("https://" + urlsplit(plan.direct_url).netloc + "/", plan.direct_url)]
    return []


# ------------------------------------------------------------------ ssrf / redirect
def check_url(url: str, http_hosts: frozenset[str] = KNOWN_GATEWAY_HOSTS,
              allow_private_hosts: frozenset[str] = frozenset()) -> None:
    """SSRF guard: https only (http only to listed gateways); resolved addresses must be public.
    DNS can still change between this check and the connect (rebinding); redirects are re-checked
    hop by hop and capped, which bounds the rest."""
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


def redirect_target(base_url: str, location: str | None,
                    check: Callable[[str], None] = check_url) -> str | None:
    """The absolute redirect target if it may be followed, else None (status stays http_3xx).

    Allowed only when the target is https on the default port, its host resolves to public addresses,
    and either its path is /ipfs/<valid cid>[/...], or its host is a pinned IPFS/Arweave gateway
    (including the <cid>.ipfs.<gateway> subdomain form)."""
    if not location or len(location) > 2048:
        return None
    target = urljoin(base_url, location)
    parts = urlsplit(target)
    try:
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" or not host or parts.username or parts.password or port not in (None, 443):
        return None
    ipfs_ok = False
    if parts.path.startswith("/ipfs/"):
        first = parts.path[len("/ipfs/"):].split("/", 1)[0]
        ipfs_ok = is_valid_cid(first) and ".." not in parts.path.split("/")
    if not (ipfs_ok or host in REDIRECT_GATEWAY_HOSTS or _subdomain_cid(host)):
        return None
    try:
        check(target)
    except (BlockedUrl, HttpError):
        return None
    return target


def parse_retry_after(value: str | None, now: Callable[[], float] = time.time) -> float | None:
    if not value:
        return None
    value = value.strip()
    if re.fullmatch(r"\d{1,9}(\.\d+)?", value):
        return float(value)
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, dt.timestamp() - now())


# ----------------------------------------------------------------- rate limit
class _Gw:
    __slots__ = ("tokens", "stamp", "until", "strikes")

    def __init__(self, tokens: float, stamp: float) -> None:
        self.tokens, self.stamp, self.until, self.strikes = tokens, stamp, 0.0, 0


class GatewayPool:
    """Per-gateway token bucket (default 1 req/s, burst 2) plus 429 cooldown.

    ``reserve`` picks a gateway and takes its token; the caller sleeps ``wait`` seconds, makes exactly
    one request, then calls ``report``. A gateway in cooldown is skipped. Pinned order decides ties:
    the first gateway with a token ready now, else the first that can serve within ``max_wait``."""

    def __init__(self, gateways: Sequence[str] = DEFAULT_GATEWAYS, rps: float = DEFAULT_RPS,
                 burst: float = DEFAULT_BURST, clock: Callable[[], float] = time.monotonic) -> None:
        if rps <= 0 or burst < 1:
            raise ValueError("rps must be > 0 and burst >= 1")
        self.rps, self.burst, self.clock = float(rps), float(burst), clock
        self.lock = threading.Lock()
        self.gw: dict[str, _Gw] = {}
        self.counts: dict[str, int] = {"http_429": 0, "http_503_retry_after": 0, "rate_limited_all": 0,
                                       "requests": 0}
        for g in gateways:
            self._state(g, clock())

    def _state(self, key: str, now: float) -> _Gw:
        st = self.gw.get(key)
        if st is None:
            st = self.gw[key] = _Gw(self.burst, now)
        return st

    def cooling(self, key: str) -> bool:
        with self.lock:
            return self.clock() < self._state(key, self.clock()).until

    def cooldown_left(self, key: str) -> float:
        with self.lock:
            now = self.clock()
            return max(0.0, self._state(key, now).until - now)

    def _wait_for(self, st: _Gw, now: float) -> float:
        st.tokens = min(self.burst, st.tokens + (now - st.stamp) * self.rps)
        st.stamp = now
        return 0.0 if st.tokens >= 1 else (1 - st.tokens) / self.rps

    def reserve(self, keys: Sequence[str], max_wait: float = MAX_TOKEN_WAIT_S) -> tuple[str, float] | None:
        """(key, seconds to wait before the request) or None: record ``rate_limited_all``, make no request."""
        with self.lock:
            now = self.clock()
            waits: list[tuple[str, _Gw, float]] = []
            for key in keys:
                st = self._state(key, now)
                if now < st.until:
                    continue
                waits.append((key, st, self._wait_for(st, now)))
            pick = next((w for w in waits if w[2] == 0.0), None) or next(
                (w for w in waits if w[2] <= max_wait), None)
            if pick is None:
                self.counts["rate_limited_all"] += 1
                return None
            key, st, wait = pick
            st.tokens -= 1
            self.counts["requests"] += 1
            return key, wait

    def report(self, key: str, status: str, retry_after: float | None = None) -> None:
        """Feed back one response. ``status`` is "ok" or "http_<code>"; others change nothing."""
        with self.lock:
            now = self.clock()
            st = self._state(key, now)
            if status == "ok":
                st.strikes = 0
                return
            if status == "http_429":
                self.counts["http_429"] += 1
            elif status == "http_503" and retry_after is not None:
                self.counts["http_503_retry_after"] += 1
            else:
                return
            if now >= st.until:  # concurrent in-flight 429s count as one strike
                st.strikes += 1
            base = RETRY_AFTER_DEFAULT_S if retry_after is None else min(max(retry_after, 1.0), RETRY_AFTER_CAP_S)
            cool = min(base * (2 ** (st.strikes - 1)), RETRY_AFTER_CAP_S)
            st.until = max(st.until, now + cool)
