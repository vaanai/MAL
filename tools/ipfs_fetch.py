"""Shared metadata-fetch rules for tools/meta_capture.py (Job A) and tools/meta_stage0.py (Job B).

No network and no chain-node call lives here. This module holds only:
  * the pinned gateway list and the pinned gateway host sets (IPFS gateways, Arweave gateways);
  * uri classification (ipfs_cid / arweave / https:<hostname> / refused_scheme) and the rewrite onto gateways;
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


_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
MAX_HOST_STATES = 4096  # non-pinned pool keys kept at once


# Local kubo gateway (client-only node on this host). Narrow SSRF exemption, see parse_local_gateway.
LOCAL_RPS = 20.0
LOCAL_BURST = 20.0
LOCAL_JSON_TIMEOUT_S = 20.0
LOCAL_IMAGE_TIMEOUT_S = 30.0
LOCAL_BREAKER_BASE_S = 30.0
LOCAL_BREAKER_MAX_S = 300.0
LOCAL_CONCURRENCY = 16
LOCAL_LABEL = "local"
_LOCAL_HOSTS = ("127.0.0.1", "::1")


class BlockedUrl(Exception):
    pass


class ConnectionRefused(Exception):
    """The connect itself was refused: no request reached the server, nothing was transferred."""


class HttpError(Exception):
    def __init__(self, status: int, location: str | None = None,
                 retry_after: float | None = None) -> None:
        super().__init__(f"http_{status}")
        self.status = status
        self.location = location
        self.retry_after = retry_after


# ------------------------------------------------------------- local gateway
@dataclass(frozen=True)
class LocalGateway:
    """The one loopback origin that may be contacted: http://127.0.0.1:<port> or http://[::1]:<port>."""
    origin: str                      # e.g. http://127.0.0.1:8080 (no path, no trailing slash); not secret
    rps: float = LOCAL_RPS
    burst: float = LOCAL_BURST
    json_timeout_s: float = LOCAL_JSON_TIMEOUT_S
    image_timeout_s: float = LOCAL_IMAGE_TIMEOUT_S
    breaker_base_s: float = LOCAL_BREAKER_BASE_S
    breaker_max_s: float = LOCAL_BREAKER_MAX_S

    @property
    def key(self) -> str:
        """Pool key and url prefix, same shape as the public gateway entries."""
        return self.origin + "/ipfs/"

    def timeout_for(self, image: bool) -> float:
        return self.image_timeout_s if image else self.json_timeout_s


def origin_of(url: str) -> str | None:
    """scheme://netloc, lowercased, or None when the url does not parse."""
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def parse_local_gateway(url: str, rps: float = LOCAL_RPS, burst: float = LOCAL_BURST,
                        json_timeout_s: float = LOCAL_JSON_TIMEOUT_S,
                        image_timeout_s: float = LOCAL_IMAGE_TIMEOUT_S,
                        breaker_base_s: float = LOCAL_BREAKER_BASE_S,
                        breaker_max_s: float = LOCAL_BREAKER_MAX_S) -> LocalGateway:
    """Accept only http://127.0.0.1:<port> or http://[::1]:<port> (path / or empty). Hostnames such as
    localhost are refused so DNS can never decide what is loopback. Raises ValueError otherwise."""
    if not isinstance(url, str) or url != url.strip():
        raise ValueError("local gateway: bad url")
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.username is not None or parts.password is not None \
            or "@" in parts.netloc:
        raise ValueError("local gateway must be http://127.0.0.1:<port> or http://[::1]:<port>")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("local gateway: origin only, no path, query or fragment")
    try:
        host, port = parts.hostname, parts.port
    except ValueError as exc:
        raise ValueError("local gateway: bad port") from exc
    if host not in _LOCAL_HOSTS or not port or not 1 <= port <= 65535:
        raise ValueError("local gateway host must be literal 127.0.0.1 or [::1] with an explicit port")
    netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    if parts.netloc.lower() != netloc:
        raise ValueError("local gateway: non-canonical host or port")
    if rps <= 0 or burst < 1 or json_timeout_s <= 0 or image_timeout_s <= 0 \
            or breaker_base_s <= 0 or breaker_max_s < breaker_base_s:
        raise ValueError("local gateway: rps/burst/timeouts/breaker must be positive")
    return LocalGateway("http://" + netloc, float(rps), float(burst), float(json_timeout_s),
                        float(image_timeout_s), float(breaker_base_s), float(breaker_max_s))


def is_refused(exc: BaseException | None) -> bool:
    """True for a refused connect (ConnectionRefusedError, bare or wrapped as a URLError reason)."""
    seen = 0
    while exc is not None and seen < 4:
        if isinstance(exc, (ConnectionRefusedError, ConnectionRefused)):
            return True
        exc = getattr(exc, "reason", None) if isinstance(getattr(exc, "reason", None), BaseException) \
            else exc.__cause__
        seen += 1
    return False


# ------------------------------------------------------------- classification
def is_valid_cid(text: str) -> bool:
    return bool(_CID_V0.match(text) or _CID_V1.match(text))


def _ipfs_path_ok(path: str) -> bool:
    return bool(_IPFS_PATH.match(path)) and ".." not in path.split("/")


@dataclass(frozen=True)
class UriPlan:
    host_class: str            # ipfs_cid | arweave | https:<hostname> | refused_scheme
    host: str | None           # hostname, set for https:<hostname> only
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
    """ipfs:// and /ipfs/<path> -> pinned gateways; ar:// -> arweave.net; any other https uri -> fetched
    directly from its own host (class https:<hostname>; the SSRF guard decides at connect time);
    http and every other scheme -> refused_scheme, never fetched."""
    refused = UriPlan("refused_scheme", None)
    if not isinstance(uri, str):
        return refused
    uri = uri.strip()
    if uri.startswith("ipfs://"):
        path = uri[len("ipfs://"):].split("?", 1)[0].split("#", 1)[0]
        if path.startswith("ipfs/"):
            path = path[len("ipfs/"):]
        return UriPlan("ipfs_cid", None, ipfs_path=path) if _ipfs_path_ok(path) else refused
    if uri.startswith("ar://"):
        rest = uri[len("ar://"):].split("?", 1)[0].split("#", 1)[0]
        if _AR_ID.match(rest) and ".." not in rest.split("/"):
            return UriPlan("arweave", None, direct_url="https://arweave.net/" + rest)
        return refused
    if not uri.startswith(("http://", "https://")):
        return refused
    parts = urlsplit(uri)
    try:
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return refused
    if not host:
        return refused
    if parts.path.startswith("/ipfs/"):  # never contacted directly, valid or not
        sub = parts.path[len("/ipfs/"):]
        return UriPlan("ipfs_cid", None, ipfs_path=sub) if _ipfs_path_ok(sub) else refused
    if _subdomain_cid(host):
        tail = parts.path.lstrip("/")
        path = host.partition(".ipfs.")[0] + ("/" + tail if tail else "")
        if _ipfs_path_ok(path):
            return UriPlan("ipfs_cid", None, ipfs_path=path)
    if parts.scheme != "https" or parts.username or parts.password or port not in (None, 443):
        return refused
    netloc = f"[{host}]" if ":" in host else host
    url = f"https://{netloc}{parts.path or '/'}" + (f"?{parts.query}" if parts.query else "")
    cls = "arweave" if host in ARWEAVE_HOSTS else f"https:{host[:253]}"
    return UriPlan(cls, None if cls == "arweave" else host[:253], direct_url=url)


def host_fields(plan: UriPlan) -> dict[str, str]:
    """Record field: the class only (https:<hostname> carries the hostname; never a path or query)."""
    return {"uri_host_class": plan.host_class}


def plan_urls(plan: UriPlan, gateways: Sequence[str],
              local: LocalGateway | None = None) -> list[tuple[str, str]]:
    """(pool key, url) pairs. IPFS: the local gateway first (when set), then one per pinned gateway in
    order. Direct: one, keyed by its host (never local)."""
    if plan.ipfs_path is not None:
        pairs = [(g, g.rstrip("/") + "/" + plan.ipfs_path) for g in gateways]
        if local is not None:
            pairs.insert(0, (local.key, local.key + plan.ipfs_path))
        return pairs
    if plan.direct_url is not None:
        return [("https://" + urlsplit(plan.direct_url).netloc + "/", plan.direct_url)]
    return []


# ------------------------------------------------------------------ ssrf / redirect
def check_url(url: str, http_hosts: frozenset[str] = KNOWN_GATEWAY_HOSTS,
              allow_private_hosts: frozenset[str] = frozenset(),
              local_origin: str | None = None) -> None:
    """SSRF guard, applied to the first hop and to every redirect hop.

    Scheme https (http only to the pinned gateway hosts), no userinfo, port 443 (80 for http) unless the
    host is in ``allow_private_hosts`` (tests only), and every resolved address must be public: private,
    loopback, link-local, CGNAT, multicast, reserved, unspecified and NAT64 are refused, and an IPv4-mapped
    IPv6 address is judged by its embedded IPv4 address. One non-public answer refuses the url.
    ``local_origin`` (the --local-gateway origin) is the one loopback exemption: a url whose scheme, host
    and port equal it exactly passes with no DNS lookup; any other loopback or private url is still refused.
    DNS can still change between this check and the connect (rebinding); redirects are re-checked hop by
    hop and capped, which bounds the rest."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("https", "http") or not host:
        raise BlockedUrl("scheme")
    if local_origin is not None and origin_of(url) == local_origin:
        if parts.username or parts.password or "@" in parts.netloc:
            raise BlockedUrl("userinfo")
        return
    if parts.scheme == "http" and host not in http_hosts and host not in allow_private_hosts:
        raise BlockedUrl("plain_http")
    if host in allow_private_hosts:
        return
    try:
        port = parts.port
    except ValueError as exc:
        raise BlockedUrl("port") from exc
    if parts.username or parts.password:
        raise BlockedUrl("userinfo")
    if port not in (None, 443 if parts.scheme == "https" else 80):
        raise BlockedUrl("port")
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80),
                                   type=socket.SOCK_STREAM)
    except OSError as exc:
        raise HttpError(0) from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if any(ip in net for net in _NAT64 if ip.version == 6):
            raise BlockedUrl("private_address")
        if (not ip.is_global or ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified or ip in _CGNAT):
            raise BlockedUrl("private_address")


def redirect_target(base_url: str, location: str | None,
                    check: Callable[[str], None] = check_url,
                    local_origin: str | None = None) -> str | None:
    """The absolute redirect target if it may be followed, else None (status stays http_3xx).

    Allowed only when the target is https on the default port with no userinfo and its host resolves to
    public addresses only (the same guard as the first hop). The caller caps the hops at MAX_REDIRECTS."""
    if not location or len(location) > 2048:
        return None
    target = urljoin(base_url, location)
    parts = urlsplit(target)
    try:
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return None
    if local_origin is not None and origin_of(base_url) == local_origin:
        # From the local gateway: same origin (kubo's /ipfs/<cid> -> /ipfs/<cid>/), or an /ipfs/<cid>
        # path on a public https host that passes the normal guard below. Anything else is refused.
        if origin_of(target) == local_origin:
            if parts.username or parts.password:
                return None
            if not parts.path.startswith("/ipfs/") or not _ipfs_path_ok(parts.path[len("/ipfs/"):]):
                return None  # e.g. /api/v0/id: the local origin serves more than content
            return target
        if not parts.path.startswith("/ipfs/") or not _ipfs_path_ok(parts.path[len("/ipfs/"):]):
            return None
    if parts.scheme != "https" or not host or parts.username or parts.password or port not in (None, 443):
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
    __slots__ = ("tokens", "stamp", "until", "strikes", "rps", "burst", "bstrikes")

    def __init__(self, tokens: float, stamp: float, rps: float = DEFAULT_RPS,
                 burst: float = DEFAULT_BURST) -> None:
        self.tokens, self.stamp, self.until, self.strikes = tokens, stamp, 0.0, 0
        self.rps, self.burst = rps, burst
        self.bstrikes = 0


class GatewayPool:
    """Per-gateway token bucket (default 1 req/s, burst 2) plus 429 cooldown.

    ``reserve`` picks a gateway and takes its token; the caller sleeps ``wait`` seconds, makes exactly
    one request, then calls ``report``. A gateway in cooldown is skipped. Pinned order decides ties:
    the first gateway with a token ready now, else the first that can serve within ``max_wait``.
    Direct (non-gateway) hosts get their own entry on first use; at most MAX_HOST_STATES of them are kept,
    evicting only idle, non-cooling entries and never the pinned gateways."""

    def __init__(self, gateways: Sequence[str] = DEFAULT_GATEWAYS, rps: float = DEFAULT_RPS,
                 burst: float = DEFAULT_BURST, clock: Callable[[], float] = time.monotonic) -> None:
        if rps <= 0 or burst < 1:
            raise ValueError("rps must be > 0 and burst >= 1")
        self.rps, self.burst, self.clock = float(rps), float(burst), clock
        self.lock = threading.Lock()
        self.gw: dict[str, _Gw] = {}
        self.counts: dict[str, int] = {"http_429": 0, "http_503_retry_after": 0, "rate_limited_all": 0,
                                       "requests": 0}
        self.sources: dict[str, int] = {}  # requests by source: "local" vs "public"
        self.local_key: str | None = None
        self.breaker = (LOCAL_BREAKER_BASE_S, LOCAL_BREAKER_MAX_S)
        self.counts["local_breaker_trips"] = 0
        for g in gateways:
            self._state(g, clock())
        self.pinned = frozenset(self.gw)

    def add_pinned(self, key: str, rps: float, burst: float, breaker_base_s: float = LOCAL_BREAKER_BASE_S,
                   breaker_max_s: float = LOCAL_BREAKER_MAX_S) -> None:
        """Add a gateway with its own bucket (the local one). Never evicted."""
        if rps <= 0 or burst < 1:
            raise ValueError("rps must be > 0 and burst >= 1")
        with self.lock:
            self.gw[key] = _Gw(float(burst), self.clock(), float(rps), float(burst))
            self.pinned = frozenset(self.pinned | {key})
            self.local_key = key
            self.breaker = (float(breaker_base_s), float(breaker_max_s))

    def _evict_idle(self, now: float) -> None:
        """Drop non-pinned entries that are not cooling and have a full bucket (nothing to remember)."""
        for key in [k for k, st in self.gw.items() if k not in self.pinned and now >= st.until
                    and min(self.burst, st.tokens + (now - st.stamp) * self.rps) >= self.burst]:
            del self.gw[key]

    def _state(self, key: str, now: float) -> _Gw:
        st = self.gw.get(key)
        if st is None:
            if len(self.gw) - len(getattr(self, "pinned", ())) >= MAX_HOST_STATES:
                self._evict_idle(now)
                if len(self.gw) - len(self.pinned) >= MAX_HOST_STATES:
                    return _Gw(self.burst, now, self.rps, self.burst)  # all busy or cooling: untracked one-off
            st = self.gw[key] = _Gw(self.burst, now, self.rps, self.burst)
        return st

    def cooling(self, key: str) -> bool:
        with self.lock:
            return self.clock() < self._state(key, self.clock()).until

    def cooldown_left(self, key: str) -> float:
        with self.lock:
            now = self.clock()
            return max(0.0, self._state(key, now).until - now)

    def _wait_for(self, st: _Gw, now: float) -> float:
        st.tokens = min(st.burst, st.tokens + (now - st.stamp) * st.rps)
        st.stamp = now
        return 0.0 if st.tokens >= 1 else (1 - st.tokens) / st.rps

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
            src = LOCAL_LABEL if key == self.local_key else "public"
            self.sources[src] = self.sources.get(src, 0) + 1
            return key, wait

    def report(self, key: str, status: str, retry_after: float | None = None) -> None:
        """Feed back one response. ``status`` is "ok" or "http_<code>"; others change nothing."""
        with self.lock:
            now = self.clock()
            st = self._state(key, now)
            if key == self.local_key:
                # 504 is kubo's own retrieval timeout: the node is alive, the CID was not found. It never
                # trips the breaker (it counts as an answer).
                if status in LOCAL_FAIL_STATUSES or (status.startswith("http_") and status[5:].isdigit()
                                                     and int(status[5:]) >= 500 and status != "http_504") \
                        or status.startswith("net_"):
                    # Breaker: connection refused, timeout or 5xx -> capped doubling cooldown.
                    if now >= st.until:  # concurrent in-flight failures count as one strike
                        st.bstrikes += 1
                        self.counts["local_breaker_trips"] += 1
                    cool = min(self.breaker[0] * (2 ** (st.bstrikes - 1)), self.breaker[1])
                    st.until = max(st.until, now + cool)
                    return
                if status == "ok" or status.startswith("http_4") or status == "http_504":
                    st.bstrikes = 0  # the node answered
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


LOCAL_FAIL_STATUSES = frozenset({"timeout", "conn_refused", "http_0"})


def local_may_fall_back(status: str) -> bool:
    """After a request to the local gateway: True when the public gateways may be tried (timeout, any 5xx
    including 504, or the node unreachable / network error). A 404 (kubo gave up after its timeout: not
    found), a blocked url and every other final answer return False. 429 is handled by the pool cooldown
    like any gateway."""
    if status in ("timeout", "http_0", "conn_refused") or status.startswith("net_"):
        return True
    if status.startswith("http_") and status[5:].isdigit():
        return int(status[5:]) >= 500
    return False
