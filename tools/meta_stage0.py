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
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

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
DEFAULT_GATEWAYS = (  # pinned
    "https://ipfs.io/ipfs/",
    "https://cloudflare-ipfs.com/ipfs/",
    "https://gateway.pinata.cloud/ipfs/",
)
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
_IPFS_RE = re.compile(r"^(?:ipfs://(?:ipfs/)?|https?://[^/]+/ipfs/)([A-Za-z0-9][A-Za-z0-9._/-]*)$")


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


def urllib_get(url: str, timeout: float, max_bytes: int, ranged: bool) -> tuple[int, bytes]:
    deadline = time.monotonic() + min(timeout, HTTP_DEADLINE_S)
    try:
        headers = {"User-Agent": "mal-meta-stage0"}
        if ranged:
            headers["Range"] = f"bytes=0-{max_bytes - 1}"
        req = urllib.request.Request(url, headers=headers)
        with _OPENER.open(req, timeout=timeout) as resp:
            return resp.status, read_with_deadline(resp, max_bytes, deadline)
    except urllib.error.HTTPError as exc:  # includes refused 3xx redirects
        return exc.code, b""


def limited_http(http: "HttpGet", limiter: Any) -> "HttpGet":
    def call(url: str, timeout: float, max_bytes: int, ranged: bool) -> tuple[int, bytes]:
        limiter.acquire()
        return http(url, timeout, max_bytes, ranged)

    return call


def candidate_urls(uri: str, gateways: Sequence[str]) -> list[str]:
    """Only ipfs:// or /ipfs/<cid> uris, rewritten onto the pinned gateways. Anything else is refused."""
    if not isinstance(uri, str):
        return []
    m = _IPFS_RE.match(uri.strip())
    if not m or ".." in m.group(1):
        return []
    return [g.rstrip("/") + "/" + m.group(1) for g in gateways]


def fetch_with_retry(
    uri: str, gateways: Sequence[str], http: HttpGet, max_bytes: int, ranged: bool
) -> tuple[bytes | None, str | None]:
    """Up to ATTEMPTS tries, rotating gateways. Returns (body, error_class)."""
    urls = candidate_urls(uri, gateways)
    if not urls:
        return None, "non_ipfs_uri"
    err = "http"
    for i in range(ATTEMPTS):
        url = urls[i % len(urls)]
        try:
            status, body = http(url, TIMEOUT_S, max_bytes, ranged)
        except TimeoutError:
            err = "timeout"
            continue
        except Exception as exc:  # class name only; never the URL
            err = "net_" + type(exc).__name__
            continue
        if status in (200, 206) and body:
            return body, None
        err = f"http_{status}" if status not in (200, 206) else "empty"
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
    """Value of the one named variable (the repo convention is HELIUS_API_KEY) in the env file, turned into
    the RPC URL. Returned to the caller only; never logged."""
    for line in Path(env_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line or line.startswith("#"):
            continue
        name, val = line.split("=", 1)
        if name.strip() == var and val.strip().strip("'\""):
            return helius_http_url(val.strip().strip("'\""))
    raise SystemExit(f"{var} not set in env file")


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
    row: Mapping[str, Any], rpc: RpcFn, http: HttpGet, gateways: Sequence[str]
) -> dict[str, Any]:
    st: dict[str, Any] = {
        "mint": row["mint"], "grp": row["grp"], "block": row.get("block"),
        "tx_ok": False, "uri_found": False, "json_ok": False, "has_description": False,
        "has_any_social": False, "image_ok": False, "error": None,
    }
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
    st["uri_found"] = True
    body, err = fetch_with_retry(uri, gateways, http, JSON_BYTES, False)
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
    ibody, ierr = fetch_with_retry(image, gateways, http, IMAGE_BYTES, True)
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


def summarize(
    statuses: Sequence[Mapping[str, Any]], calls_used: int, cap_hit: bool = False,
    sample: Sequence[Mapping[str, Any]] | None = None, deadline_hit: bool = False,
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


def run(
    sample: Sequence[Mapping[str, Any]], out: Path, rpc: RpcFn, http: HttpGet, gateways: Sequence[str],
    budget: CreditBudget, max_runtime_s: float = MAX_RUNTIME_DEFAULT_S,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    refuse_out_of_range(sample)
    out.mkdir(parents=True, exist_ok=True)
    status_path = out / "status.jsonl"
    done = load_done(status_path)
    seen = {r["mint"] for r in done}
    cap_hit = deadline_hit = False
    t_end = clock() + max_runtime_s
    with status_path.open("a", encoding="utf-8") as fh:
        for row in sample:
            if row["mint"] in seen:
                continue
            if clock() >= t_end:
                deadline_hit = True
                break
            try:
                st = check_mint(row, rpc, http, gateways)
            except CreditBudgetExceeded:
                cap_hit = True
                break
            fh.write(json.dumps(st, sort_keys=True) + "\n")
            fh.flush()
            done.append(st)
            seen.add(row["mint"])
    summary = summarize(done, budget.used, cap_hit, sample, deadline_hit)
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


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Stage 0 metadata retrieval check (count-only)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-per-group", type=int, default=1000)
    ap.add_argument("--helius-env", default=DEFAULT_HELIUS_ENV)
    ap.add_argument("--max-calls", type=int, default=MAX_CALLS_DEFAULT, help="cumulative cap across runs, <= 3000")
    ap.add_argument("--rps", type=float, default=MAX_RPS)
    ap.add_argument("--max-runtime-s", type=float, default=MAX_RUNTIME_DEFAULT_S)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
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
    summary = run(
        sample, out, make_rpc(url, budget, limiter), limited_http(urllib_get, limiter),
        list(DEFAULT_GATEWAYS), budget, args.max_runtime_s,
    )
    log.info(
        "calls_used=%d cap_hit=%s deadline_hit=%s pass=%s",
        summary["calls_used"], summary["cap_hit"], summary["deadline_hit"], summary["pass"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
