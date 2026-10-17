"""Stage 0 retrieval check: can historical pump.fun token metadata still be recovered?

Count-only. For a deterministic sample of graduated (G) and never-graduated (N) mints, fetch the create
transaction, decode the create `uri`, fetch the metadata JSON and the image, and record per-mint status
flags. No description text is stored. Run on mal-fast-0 as a MiScusi job; never in tests against the network.

Reads only: the shared tokens parquet and the audit-1008 creates parquet (mint, signature, slot, block).
The Helius URL is read from an env file inside the process and is never printed or logged.

Sample note: the creates join happens before the top-N cut, so each group is the first N mints by
sha256(mint) among those that have a create signature (a mint without one cannot be retrieved).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tools import ipfs_fetch as ipf
from tools.pump_history_backfill import (
    CreditBudget,
    CreditBudgetExceeded,
    RateLimiter,
    _program_data,
    cpi_events_from_tx,
    decode_create_event,
    helius_http_url,
)

log = logging.getLogger("meta_stage0")

TOKENS = "/data/mal/hunt-shared/tokens.parquet"
CREATES_DIR = "/data/mal/audit-1008/tape/creates"
# Pinned (QP ruling 2026-10-17): hash salt, order is sha256(salt + mint); the gateway list below is the
# same as DEFAULT_GATEWAYS in tools/meta_capture.py on claude/meta-capture.
HASH_SALT = "MAL-STAGE0-2026-10-17"
# Exploration HOURS only, [start, end) in UTC. Nothing outside these is listed, opened or retrieved.
SEGMENTS = (
    ("2026-08-14T12", "2026-08-28T12"),
    ("2026-09-03T12", "2026-09-15T12"),
    ("2026-09-18T23", "2026-09-25T07"),
)
CENSOR_WINDOW_H = 24
WILSON_Z90 = 1.6448536269514722
DEFAULT_HELIUS_ENV = "/var/lib/mal/fast-listener/helius.env"
DEFAULT_GATEWAYS = ipf.DEFAULT_GATEWAYS  # pinned, shared with tools/meta_capture.py
MAX_RPS = 8.0
MIN_RPS = 0.5
HTTP_DEADLINE_S = 10.0  # total wall clock per gateway request
RPC_DEADLINE_S = 30.0
MAX_RUNTIME_DEFAULT_S = 3000.0
HELIUS_VAR = "HELIUS_API_KEY"
MAX_CALLS_DEFAULT = 3000  # hard cap (QP ruling)
IMAGE_BYTES = 65536
JSON_BYTES = 1 << 20
TIMEOUT_S = 10.0
ATTEMPTS = 3
MIN_JSON_SHARE = 0.95
MAX_GAP = 0.02  # 2 points

GROUPS = ("G", "N")


# ---------------------------------------------------------------- sample

class RefusedOutOfRange(RuntimeError):
    """A create outside the exploration hours (or any October signature) was seen."""


def _hr(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)


def segment_bounds_ms() -> list[tuple[int, int]]:
    return [(int(_hr(a).timestamp() * 1000), int(_hr(b).timestamp() * 1000)) for a, b in SEGMENTS]


def allowed_hours() -> list[str]:
    out: list[str] = []
    for a, b in SEGMENTS:
        t, end = _hr(a), _hr(b)
        while t < end:
            out.append(t.strftime("%Y-%m-%dT%H"))
            t += timedelta(hours=1)
    return out


def allowed_create_files(creates_dir: str = CREATES_DIR) -> list[str]:
    """Explicit per-hour paths built from the allowed hours. The directory is never listed, so no file
    outside the ranges is touched; hours without a file are skipped."""
    paths = [f"{creates_dir}/{h}.parquet" for h in allowed_hours()]
    return [p for p in paths if Path(p).exists()]


def in_allowed_ms(ms: int) -> bool:
    return any(lo <= ms < hi for lo, hi in segment_bounds_ms())


OCT_START_S = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())


def refuse_out_of_range(sample: Sequence[Mapping[str, Any]]) -> None:
    """Raise before any network call if any sampled create is outside the allowed hours."""
    for r in sample:
        bt = r.get("block_time")
        if bt is None:
            raise RefusedOutOfRange("sample row without block_time")
        if int(bt) >= OCT_START_S:
            raise RefusedOutOfRange("October signature refused")
        if not in_allowed_ms(int(bt) * 1000):
            raise RefusedOutOfRange("create outside exploration hours")


def _ms_clause(col: str) -> str:
    return "(" + " OR ".join(f"({col} >= {lo} AND {col} < {hi})" for lo, hi in segment_bounds_ms()) + ")"


def sample_sql(n_per_group: int, tokens: str, files: Sequence[str]) -> str:
    flist = "[" + ", ".join("'" + f.replace("'", "''") + "'" for f in files) + "]"
    return f"""
WITH tok AS (
  SELECT mint, block, create_ms,
         CASE WHEN grad_ms IS NOT NULL THEN 'G' ELSE 'N' END AS grp
  FROM read_parquet('{tokens}')
  WHERE has_create IS TRUE AND is_mayhem_mode IS NOT TRUE AND {_ms_clause('create_ms')}
),
cr AS (
  SELECT mint, arg_min(signature, slot) AS signature, min(slot) AS slot, min(block_time) AS block_time
  FROM read_parquet({flist})
  WHERE signature IS NOT NULL AND {_ms_clause('block_time * 1000')}
  GROUP BY mint
),
j AS (
  SELECT tok.mint, tok.grp, tok.block, tok.create_ms, cr.signature, cr.slot, cr.block_time,
         sha256('{HASH_SALT}' || tok.mint) AS h
  FROM tok JOIN cr USING (mint)
),
r AS (SELECT *, row_number() OVER (PARTITION BY grp ORDER BY h, mint) AS rn FROM j)
SELECT mint, grp, block, create_ms, signature, slot, block_time FROM r
WHERE rn <= {int(n_per_group)} ORDER BY grp, rn
"""


def build_sample(
    n_per_group: int, tokens: str = TOKENS, creates_dir: str = CREATES_DIR
) -> list[dict[str, Any]]:
    import duckdb

    files = allowed_create_files(creates_dir)
    if not files:
        raise RefusedOutOfRange("no create files for the allowed hours")
    cur = duckdb.connect().execute(sample_sql(n_per_group, tokens, files))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    refuse_out_of_range(rows)
    return rows


def write_sample(rows: Sequence[Mapping[str, Any]], out: Path) -> str:
    """Write sample.parquet and sample.parquet.sha256; return the hex digest."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    cols = ["mint", "grp", "block", "create_ms", "signature", "slot", "block_time"]
    table = pa.table({c: [r.get(c) for r in rows] for c in cols})
    path = out / "sample.parquet"
    pq.write_table(table, path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (out / "sample.parquet.sha256").write_text(f"{digest}  sample.parquet\n", encoding="utf-8")
    return digest


# ---------------------------------------------------------------- decode

def ev_uri(raw: bytes) -> str | None:
    """The uri string of a CreateEvent blob (decode_create_event does not return it)."""
    off = 8
    for _ in range(2):
        size = int.from_bytes(raw[off : off + 4], "little")
        off += 4 + size
    size = int.from_bytes(raw[off : off + 4], "little")
    try:
        return raw[off + 4 : off + 4 + size].decode("utf-8")
    except UnicodeDecodeError:
        return None


def uri_from_tx(tx: Mapping[str, Any], mint: str | None = None) -> str | None:
    """The create `uri` from a getTransaction result (encoding json): Program data logs, then CPI events."""
    meta = tx.get("meta") if isinstance(tx.get("meta"), dict) else {}
    blobs: list[bytes] = []
    for line in meta.get("logMessages") or []:
        if isinstance(line, str):
            raw = _program_data(line)
            if raw:
                blobs.append(raw)
    blobs += cpi_events_from_tx(tx)
    for raw in blobs:
        ev = decode_create_event(raw)
        if ev is None or (mint is not None and ev["mint"] != mint):
            continue
        return ev_uri(raw)
    return None


# ---------------------------------------------------------------- http

HttpGet = Callable[[str, float, int, bool], "tuple[int, bytes]"]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: a gateway must not bounce us to an internal address."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def read_with_deadline(resp: Any, max_bytes: int, deadline: float, clock: Callable[[], float] = time.monotonic) -> bytes:
    """Read in chunks, enforcing a wall-clock deadline across the whole body (not just per socket read)."""
    chunks: list[bytes] = []
    got = 0
    while got < max_bytes:
        if clock() > deadline:
            raise TimeoutError("deadline")
        chunk = resp.read(min(8192, max_bytes - got))
        if not chunk:
            break
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


class HttpResult(tuple):
    """(status, body) that also carries the server's Retry-After seconds, if any."""

    def __new__(cls, status: int, body: bytes, retry_after: float | None = None) -> "HttpResult":
        obj = super().__new__(cls, (status, body))
        obj.retry_after = retry_after
        return obj


def _get_once(url: str, timeout: float, max_bytes: int, ranged: bool, deadline: float,
              allow_private_hosts: frozenset[str]) -> HttpResult:
    ipf.check_url(url, ipf.KNOWN_GATEWAY_HOSTS, allow_private_hosts)
    headers = {"User-Agent": "mal-meta-stage0"}
    if ranged:
        headers["Range"] = f"bytes=0-{max_bytes - 1}"
    req = urllib.request.Request(url, headers=headers)
    with _OPENER.open(req, timeout=max(0.1, min(timeout, deadline - time.monotonic()))) as resp:
        return HttpResult(resp.status, read_with_deadline(resp, max_bytes, deadline))


def urllib_get(url: str, timeout: float, max_bytes: int, ranged: bool, *,
               allow_private_hosts: frozenset[str] = frozenset()) -> HttpResult:
    """One GET with a wall-clock deadline over everything. At most ipf.MAX_REDIRECTS redirects, each only
    if ipf.redirect_target accepts it (https, public address, /ipfs/<cid> path or pinned gateway host);
    any other 3xx comes back as its status with an empty body. Every hop passes the SSRF guard."""
    deadline = time.monotonic() + min(timeout, HTTP_DEADLINE_S)
    cur = url
    for hop in range(ipf.MAX_REDIRECTS + 1):
        try:
            return _get_once(cur, timeout, max_bytes, ranged, deadline, allow_private_hosts)
        except urllib.error.HTTPError as exc:
            if exc.code in ipf.REDIRECT_CODES and hop < ipf.MAX_REDIRECTS:
                nxt = ipf.redirect_target(
                    cur, exc.headers.get("Location") if exc.headers else None,
                    lambda u: ipf.check_url(u, frozenset(), allow_private_hosts))
                if nxt:
                    cur = nxt
                    continue
            return HttpResult(exc.code, b"", ipf.parse_retry_after(
                exc.headers.get("Retry-After") if exc.headers else None))
    return HttpResult(310, b"")  # unreachable


def make_pool(gateways: Sequence[str], rps: float = ipf.DEFAULT_RPS, burst: float = ipf.DEFAULT_BURST,
              clock: Callable[[], float] = time.monotonic) -> ipf.GatewayPool:
    return ipf.GatewayPool(tuple(gateways), rps, burst, clock)


def candidate_urls(uri: str, gateways: Sequence[str]) -> list[str]:
    """ipfs:// and /ipfs/<path> urls rewritten onto the pinned gateways (any host's /ipfs/ path, so an
    arbitrary host is never contacted); ar:// and the pinned Arweave/metadata hosts directly; else refused."""
    return [u for _, u in ipf.plan_urls(ipf.classify_uri(uri), gateways)]


def fetch_with_retry(
    uri: str, gateways: Sequence[str], http: HttpGet, max_bytes: int, ranged: bool,
    pool: ipf.GatewayPool | None = None, sleep: Callable[[float], None] = time.sleep,
) -> tuple[bytes | None, str | None]:
    """Up to ATTEMPTS tries, rotating the starting gateway. Each try takes a token from the per-gateway
    bucket (waiting up to 5 s), skips gateways in 429 cooldown, and makes one request. If no gateway can
    serve, the error is rate_limited_all and no request is made. Returns (body, error_class)."""
    pairs = ipf.plan_urls(ipf.classify_uri(uri), gateways)
    if not pairs:
        return None, "non_ipfs_uri"
    pool = pool or make_pool(gateways)
    urls = dict(pairs)
    keys = [k for k, _ in pairs]
    err = "http"
    for i in range(ATTEMPTS):
        order = keys[i % len(keys):] + keys[: i % len(keys)]
        picked = pool.reserve(order)
        if picked is None:
            return None, "rate_limited_all"
        key, wait = picked
        if wait > 0:
            sleep(wait)
        try:
            res = http(urls[key], TIMEOUT_S, max_bytes, ranged)
            status, body = res[0], res[1]
        except TimeoutError:
            err = "timeout"
            continue
        except ipf.BlockedUrl:
            err = "blocked_url"
            continue
        except Exception as exc:  # class name only; never the URL
            err = "net_" + type(exc).__name__
            continue
        ok = status in (200, 206)
        pool.report(key, "ok" if ok and body else f"http_{status}", getattr(res, "retry_after", None))
        if ok and body:
            return body, None
        err = f"http_{status}" if not ok else "empty"
    return None, err


# ---------------------------------------------------------------- rpc

RpcFn = Callable[[str], "dict[str, Any] | None"]


def make_rpc(url: str, budget: CreditBudget, limiter: Any) -> RpcFn:
    """getTransaction by signature. Every attempt reserves one call; the URL never leaves this closure."""

    def call(signature: str) -> dict[str, Any] | None:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [signature, {"encoding": "json", "maxSupportedTransactionVersion": 0}],
            }
        ).encode()
        last = "rpc"
        for _ in range(3):
            if not budget.reserve():
                raise CreditBudgetExceeded(budget.used, budget.cap)
            limiter.acquire()
            deadline = time.monotonic() + RPC_DEADLINE_S
            try:
                req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
                with _OPENER.open(req, timeout=RPC_DEADLINE_S) as resp:
                    payload = json.loads(read_with_deadline(resp, 16 << 20, deadline))
            except urllib.error.HTTPError as exc:
                last = f"http_{exc.code}"
                continue
            except Exception as exc:
                last = "net_" + type(exc).__name__
                continue
            if payload.get("error"):
                last = "rpc_error"
                continue
            return payload.get("result")
        raise RuntimeError(last)

    return call


def read_helius_url(env_path: str, var: str = HELIUS_VAR) -> str:
    """Value of the one named variable (HELIUS_API_KEY) from the env file, or from stdin when env_path is
    "-". A value starting with https:// is the URL; otherwise it is a bare key and the RPC URL is built here.
    Returned to the caller only; never logged."""
    text = sys.stdin.read() if env_path == "-" else Path(env_path).read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line or line.startswith("#"):
            continue
        name, val = line.split("=", 1)
        val = val.strip().strip("'\"")
        if name.strip() != var or not val:
            continue
        if val.startswith("https://"):
            return val
        try:
            return helius_http_url(val)
        except ValueError:
            raise SystemExit(f"{var} value unusable") from None
    raise SystemExit(f"{var} not set in env input")


class PersistentBudget(CreditBudget):
    """Cumulative hard cap across runs: the count lives in calls.json and is written before each call."""

    def __init__(self, path: Path, cap: int) -> None:
        used = 0
        if path.exists():
            try:
                used = int(json.loads(path.read_text(encoding="utf-8")).get("calls", 0))
            except (ValueError, OSError):
                raise SystemExit("calls.json unreadable; refusing to run")
        super().__init__(cap, 1, used)
        self.path = path
        self._persist()

    def _persist(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"calls": self.used}), encoding="utf-8")
        os.replace(tmp, self.path)

    def reserve(self) -> bool:
        ok = super().reserve()
        if ok:
            self._persist()
        return ok


# ---------------------------------------------------------------- per mint

def check_mint(
    row: Mapping[str, Any], rpc: RpcFn, http: HttpGet, gateways: Sequence[str],
    pool: ipf.GatewayPool | None = None, uri_cache: Mapping[str, str] | None = None,
    on_uri: Callable[[str, str], None] | None = None, sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """``uri_cache`` (mint -> decoded uri from an earlier run) skips the RPC call. ``on_uri`` receives each
    freshly decoded uri so the caller can store it; the status row itself never carries the uri."""
    pool = pool or make_pool(gateways)
    st: dict[str, Any] = {
        "mint": row["mint"], "grp": row["grp"], "block": row.get("block"),
        "tx_ok": False, "uri_found": False, "json_ok": False, "has_description": False,
        "has_any_social": False, "image_ok": False, "error": None,
    }
    uri = (uri_cache or {}).get(row["mint"])
    if uri:
        st["uri_cached"] = True
        st["tx_ok"] = True
    else:
        try:
            tx = rpc(row["signature"])
        except CreditBudgetExceeded:
            raise
        except RuntimeError as exc:
            st["error"] = "tx_" + str(exc)[:30]
            return st
        if not tx:
            st["error"] = "tx_missing"
            return st
        st["tx_ok"] = True
        uri = uri_from_tx(tx, row["mint"])
        if not uri:
            st["error"] = "uri_not_found"
            return st
    if on_uri:
        on_uri(row["mint"], uri)  # also for cached uris, so this out dir's uris.jsonl is complete
    st["uri_found"] = True
    st.update(ipf.host_fields(ipf.classify_uri(uri)))  # class (+ hostname for 'other' only); never the uri
    body, err = fetch_with_retry(uri, gateways, http, JSON_BYTES, False, pool, sleep)
    if body is None:
        st["error"] = "json_" + str(err)
        return st
    try:
        meta = json.loads(body)
        if not isinstance(meta, dict):
            raise ValueError("not an object")
    except ValueError:
        st["error"] = "json_parse"
        return st
    st["json_ok"] = True
    desc = meta.get("description")
    st["has_description"] = isinstance(desc, str) and bool(desc.strip())
    st["has_any_social"] = any(
        isinstance(meta.get(k), str) and bool(meta[k].strip()) for k in ("twitter", "telegram", "website")
    )
    image = meta.get("image")
    if not isinstance(image, str) or not image:
        st["error"] = "image_missing"
        return st
    ibody, ierr = fetch_with_retry(image, gateways, http, IMAGE_BYTES, True, pool, sleep)
    if ibody is None:
        st["error"] = "image_" + str(ierr)
        return st
    st["image_ok"] = True
    return st


# ---------------------------------------------------------------- summary

def wilson90(k: int, n: int) -> list[float] | None:
    if n == 0:
        return None
    z, p = WILSON_Z90, k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, c - h), min(1.0, c + h)]


def censoring(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Graduation-label censoring: creates within 24 h before a segment end can graduate after it."""
    win = CENSOR_WINDOW_H * 3600 * 1000
    ends = [hi for _, hi in segment_bounds_ms()]
    out: dict[str, Any] = {"window_h": CENSOR_WINDOW_H}
    for g in GROUPS:
        gr = [r for r in rows if r.get("grp") == g and r.get("create_ms") is not None]
        near = sum(1 for r in gr if any(0 <= e - int(r["create_ms"]) < win for e in ends))
        out[g] = {"n": len(gr), "near_segment_end": near, "share": (near / len(gr)) if gr else None}
    return out


def _shares(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    out: dict[str, Any] = {"n": n}
    for k in ("tx_ok", "uri_found", "json_ok", "has_description", "has_any_social", "image_ok"):
        c = sum(1 for r in rows if r.get(k))
        out[k] = c
        out[k + "_share"] = (c / n) if n else None
        out[k + "_wilson90"] = wilson90(c, n)
    errs: dict[str, int] = {}
    for r in rows:
        if r.get("error"):
            errs[r["error"]] = errs.get(r["error"], 0) + 1
    out["errors"] = errs
    return out


def host_class_counts(statuses: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per uri host class: mints with a decoded uri and how many reached json_ok. Hostnames are listed
    only for class 'other' (an identity, not content), most common first, at most 100."""
    classes: dict[str, dict[str, int]] = {}
    others: dict[str, int] = {}
    for r in statuses:
        if not r.get("uri_found"):
            continue
        c = classes.setdefault(r.get("uri_host_class") or "unknown", {"n": 0, "json_ok": 0})
        c["n"] += 1
        c["json_ok"] += 1 if r.get("json_ok") else 0
        if r.get("uri_host_class") == "other" and r.get("uri_host"):
            others[r["uri_host"]] = others.get(r["uri_host"], 0) + 1
    top = sorted(others.items(), key=lambda kv: (-kv[1], kv[0]))[:100]
    return {"classes": dict(sorted(classes.items())), "other_hosts": dict(top)}


def rate_limit_counts(statuses: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Mints whose first failing fetch ended on 429 / all gateways limited / refused uri."""
    out = {"http_429": 0, "rate_limited_all": 0, "non_ipfs_uri": 0}
    for r in statuses:
        e = str(r.get("error") or "")
        for k in out:
            if e.endswith(k):
                out[k] += 1
    return out


def summarize(
    statuses: Sequence[Mapping[str, Any]], calls_used: int, cap_hit: bool = False,
    sample: Sequence[Mapping[str, Any]] | None = None, deadline_hit: bool = False,
    gateway_counts: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    by_group = {g: _shares([r for r in statuses if r["grp"] == g]) for g in GROUPS}
    blocks = sorted({r.get("block") for r in statuses if r.get("block") is not None})
    by_block = {
        str(b): {g: _shares([r for r in statuses if r.get("block") == b and r["grp"] == g]) for g in GROUPS}
        for b in blocks
    }
    gs, ns = by_group["G"]["json_ok_share"], by_group["N"]["json_ok_share"]
    gap = None if gs is None or ns is None else gs - ns
    passed = (
        gap is not None
        and gs >= MIN_JSON_SHARE
        and ns >= MIN_JSON_SHARE
        and abs(gap) <= MAX_GAP + 1e-12
    )
    return {
        "by_group": by_group,
        "by_block": by_block,
        "json_ok_gap_G_minus_N": gap,
        "lower_bounds_json_ok_wilson90": {
            g: (by_group[g]["json_ok_wilson90"] or [None])[0] for g in GROUPS
        },
        "bar": {"min_json_ok_share_each_group": MIN_JSON_SHARE, "max_abs_gap": MAX_GAP},
        "pass": bool(passed),
        "calls_used": calls_used,
        "cap_hit": cap_hit,
        "deadline_hit": deadline_hit,
        "censoring": censoring(sample or []),
        "uri_host_classes": host_class_counts(statuses),
        "rate_limit_mint_errors": rate_limit_counts(statuses),
        "gateway_counts": dict(gateway_counts or {}),
        "note": "metadata present must never be used as a feature",
    }


# ---------------------------------------------------------------- run

def load_done(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass  # torn last line: that mint is redone
    return out


def load_uris(path: Path) -> dict[str, str]:
    """mint -> decoded uri from a uris.jsonl (torn or foreign lines skipped)."""
    out: dict[str, str] = {}
    for rec in load_done(path):
        if isinstance(rec.get("mint"), str) and isinstance(rec.get("uri"), str):
            out[rec["mint"]] = rec["uri"]
    return out


def run(
    sample: Sequence[Mapping[str, Any]], out: Path, rpc: RpcFn, http: HttpGet, gateways: Sequence[str],
    budget: CreditBudget, max_runtime_s: float = MAX_RUNTIME_DEFAULT_S,
    clock: Callable[[], float] = time.monotonic,
    pool: ipf.GatewayPool | None = None, extra_uris: Mapping[str, str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Every decoded uri is appended to out/uris.jsonl (the one file that holds uris; status rows and the
    summary never do), so a later re-fetch needs no RPC. Uris from ``extra_uris`` and from an existing
    uris.jsonl in ``out`` are reused instead of calling RPC."""
    refuse_out_of_range(sample)
    out.mkdir(parents=True, exist_ok=True)
    status_path = out / "status.jsonl"
    uris_path = out / "uris.jsonl"
    done = load_done(status_path)
    seen = {r["mint"] for r in done}
    uris = dict(extra_uris or {})
    stored = load_uris(uris_path)
    uris.update(stored)
    pool = pool or make_pool(gateways)
    cap_hit = deadline_hit = False
    t_end = clock() + max_runtime_s
    with status_path.open("a", encoding="utf-8") as fh, uris_path.open("a", encoding="utf-8") as ufh:
        def on_uri(mint: str, uri: str) -> None:
            if mint not in stored:
                ufh.write(json.dumps({"mint": mint, "uri": uri}, sort_keys=True) + "\n")
                ufh.flush()
                stored[mint] = uri
            uris[mint] = uri

        for row in sample:
            if row["mint"] in seen:
                continue
            if clock() >= t_end:
                deadline_hit = True
                break
            try:
                st = check_mint(row, rpc, http, gateways, pool, uris, on_uri, sleep)
            except CreditBudgetExceeded:
                cap_hit = True
                break
            fh.write(json.dumps(st, sort_keys=True) + "\n")
            fh.flush()
            done.append(st)
            seen.add(row["mint"])
    summary = summarize(done, budget.used, cap_hit, sample, deadline_hit, pool.counts)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def load_reused_sample(out: Path) -> list[dict[str, Any]]:
    """Reuse sample.parquet only if it matches its recorded sha256."""
    import duckdb

    path = out / "sample.parquet"
    want = (out / "sample.parquet.sha256").read_text(encoding="utf-8").split()[0]
    if hashlib.sha256(path.read_bytes()).hexdigest() != want:
        raise SystemExit("sample.parquet does not match its sha256; refusing to run")
    cur = duckdb.connect().execute(f"select * from read_parquet('{path}')")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def adopt_sample(old: Path, out: Path) -> None:
    """Copy a previous run's sample.parquet (+ .sha256) into ``out`` after checking its sha256. The new
    out dir keeps its own calls.json, so the 3,000 cap applies afresh to this run's RPC calls."""
    if old.resolve() == out.resolve():
        raise SystemExit("--resume-from must be a different directory from --out")
    path, sha = old / "sample.parquet", old / "sample.parquet.sha256"
    if not path.exists() or not sha.exists():
        raise SystemExit("--resume-from has no sample.parquet with a recorded sha256; refusing to run")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != sha.read_text(encoding="utf-8").split()[0]:
        raise SystemExit("sample.parquet does not match its sha256; refusing to run")
    (out / "sample.parquet").write_bytes(raw)
    (out / "sample.parquet.sha256").write_text(f"{digest}  sample.parquet\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Stage 0 metadata retrieval check (count-only)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-per-group", type=int, default=1000)
    ap.add_argument("--helius-env", default=DEFAULT_HELIUS_ENV)
    ap.add_argument("--max-calls", type=int, default=MAX_CALLS_DEFAULT, help="cumulative cap across runs, <= 3000")
    ap.add_argument("--rps", type=float, default=MAX_RPS)
    ap.add_argument("--resume-from", default=None,
                    help="old out dir: reuse its sample.parquet (sha256 checked) and any uris.jsonl in it")
    ap.add_argument("--gateway-rps", type=float, default=ipf.DEFAULT_RPS, help="per-gateway token bucket rate")
    ap.add_argument("--gateway-burst", type=float, default=ipf.DEFAULT_BURST)
    ap.add_argument("--max-runtime-s", type=float, default=MAX_RUNTIME_DEFAULT_S)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.resume_from and not (out / "sample.parquet").exists():
        adopt_sample(Path(args.resume_from), out)
    if (out / "sample.parquet").exists():
        sample = load_reused_sample(out)
        log.info("sample reused n=%d", len(sample))
    else:
        sample = build_sample(args.n_per_group)
        digest = write_sample(sample, out)
        log.info("sample written n=%d sha256=%s", len(sample), digest)
    url = read_helius_url(args.helius_env)
    budget = PersistentBudget(out / "calls.json", min(args.max_calls, MAX_CALLS_DEFAULT))
    limiter = RateLimiter(min(max(args.rps, MIN_RPS), MAX_RPS))
    extra = load_uris(Path(args.resume_from) / "uris.jsonl") if args.resume_from else {}
    pool = make_pool(DEFAULT_GATEWAYS, args.gateway_rps, args.gateway_burst)
    summary = run(
        sample, out, make_rpc(url, budget, limiter), urllib_get,
        list(DEFAULT_GATEWAYS), budget, args.max_runtime_s, pool=pool, extra_uris=extra,
    )
    log.info(
        "calls_used=%d cap_hit=%s deadline_hit=%s pass=%s",
        summary["calls_used"], summary["cap_hit"], summary["deadline_hit"], summary["pass"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
