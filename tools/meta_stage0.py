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
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tools.pump_history_backfill import (
    CreditBudget,
    CreditBudgetExceeded,
    RateLimiter,
    _program_data,
    cpi_events_from_tx,
    decode_create_event,
)

log = logging.getLogger("meta_stage0")

TOKENS = "/data/mal/hunt-shared/tokens.parquet"
CREATES_GLOB = "/data/mal/audit-1008/tape/creates/*.parquet"
DEFAULT_HELIUS_ENV = "/var/lib/mal/fast-listener/helius.env"
DEFAULT_GATEWAYS = (
    "https://ipfs.io/ipfs/",
    "https://cloudflare-ipfs.com/ipfs/",
    "https://gateway.pinata.cloud/ipfs/",
)
MAX_RPS = 8.0
IMAGE_BYTES = 65536
JSON_BYTES = 1 << 20
TIMEOUT_S = 10.0
ATTEMPTS = 3
MIN_JSON_SHARE = 0.95
MAX_GAP = 0.02  # 2 points

GROUPS = ("G", "N")
_IPFS_RE = re.compile(r"^(?:ipfs://(?:ipfs/)?|https?://[^/]+/ipfs/)(.+)$")


# ---------------------------------------------------------------- sample

def sample_sql(n_per_group: int, tokens: str, creates: str) -> str:
    return f"""
WITH tok AS (
  SELECT mint, block, create_ms,
         CASE WHEN grad_ms IS NOT NULL THEN 'G' ELSE 'N' END AS grp
  FROM read_parquet('{tokens}')
  WHERE has_create IS TRUE AND is_mayhem_mode IS NOT TRUE
),
cr AS (
  SELECT mint, arg_min(signature, slot) AS signature, min(slot) AS slot, min(block_time) AS block_time
  FROM read_parquet('{creates}')
  WHERE signature IS NOT NULL
  GROUP BY mint
),
j AS (
  SELECT tok.mint, tok.grp, tok.block, tok.create_ms, cr.signature, cr.slot, cr.block_time,
         sha256(tok.mint) AS h
  FROM tok JOIN cr USING (mint)
),
r AS (SELECT *, row_number() OVER (PARTITION BY grp ORDER BY h, mint) AS rn FROM j)
SELECT mint, grp, block, create_ms, signature, slot, block_time FROM r
WHERE rn <= {int(n_per_group)} ORDER BY grp, rn
"""


def build_sample(n_per_group: int, tokens: str = TOKENS, creates: str = CREATES_GLOB) -> list[dict[str, Any]]:
    import duckdb

    cur = duckdb.connect().execute(sample_sql(n_per_group, tokens, creates))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


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


def urllib_get(url: str, timeout: float, max_bytes: int, ranged: bool) -> tuple[int, bytes]:
    headers = {"User-Agent": "mal-meta-stage0"}
    if ranged:
        headers["Range"] = f"bytes=0-{max_bytes - 1}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(max_bytes)
    except urllib.error.HTTPError as exc:
        return exc.code, b""


def candidate_urls(uri: str, gateways: Sequence[str]) -> list[str]:
    m = _IPFS_RE.match(uri)
    if m:
        return [g.rstrip("/") + "/" + m.group(1) for g in gateways]
    if uri.startswith(("http://", "https://")):
        return [uri]
    return []


def fetch_with_retry(
    uri: str, gateways: Sequence[str], http: HttpGet, max_bytes: int, ranged: bool
) -> tuple[bytes | None, str | None]:
    """Up to ATTEMPTS tries, rotating gateways. Returns (body, error_class)."""
    urls = candidate_urls(uri, gateways)
    if not urls:
        return None, "bad_scheme"
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


def make_rpc(url: str, budget: CreditBudget, limiter: RateLimiter) -> RpcFn:
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
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    payload = json.loads(resp.read())
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


def read_helius_url(env_path: str) -> str:
    """First http URL (or API key value) in the env file. Returned to the caller only; never logged."""
    for line in Path(env_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, val = line.split("=", 1)
        val = val.strip().strip("'\"")
        if val.startswith("http"):
            return val
        if val and "KEY" in name.upper():
            return f"https://mainnet.helius-rpc.com/?api-key={val}"
    raise SystemExit("no helius url in env file")


# ---------------------------------------------------------------- per mint

def check_mint(
    row: Mapping[str, Any], rpc: RpcFn, http: HttpGet, gateways: Sequence[str], keep_dir: Path | None = None
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
    if keep_dir is not None:
        keep_dir.mkdir(parents=True, exist_ok=True)
        (keep_dir / f"{row['mint']}.json").write_bytes(body)
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

def _shares(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    out: dict[str, Any] = {"n": n}
    for k in ("tx_ok", "uri_found", "json_ok", "has_description", "has_any_social", "image_ok"):
        c = sum(1 for r in rows if r.get(k))
        out[k] = c
        out[k + "_share"] = (c / n) if n else None
    errs: dict[str, int] = {}
    for r in rows:
        if r.get("error"):
            errs[r["error"]] = errs.get(r["error"], 0) + 1
    out["errors"] = errs
    return out


def summarize(statuses: Sequence[Mapping[str, Any]], calls_used: int, cap_hit: bool = False) -> dict[str, Any]:
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
        "bar": {"min_json_ok_share_each_group": MIN_JSON_SHARE, "max_abs_gap": MAX_GAP},
        "pass": bool(passed),
        "calls_used": calls_used,
        "cap_hit": cap_hit,
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
    budget: CreditBudget, keep_json: bool = False,
) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    status_path = out / "status.jsonl"
    done = load_done(status_path)
    seen = {r["mint"] for r in done}
    cap_hit = False
    keep_dir = out / "json" if keep_json else None
    with status_path.open("a", encoding="utf-8") as fh:
        for row in sample:
            if row["mint"] in seen:
                continue
            try:
                st = check_mint(row, rpc, http, gateways, keep_dir)
            except CreditBudgetExceeded:
                cap_hit = True
                break
            fh.write(json.dumps(st, sort_keys=True) + "\n")
            fh.flush()
            done.append(st)
            seen.add(row["mint"])
    summary = summarize(done, budget.used, cap_hit)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Stage 0 metadata retrieval check (count-only)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-per-group", type=int, default=1000)
    ap.add_argument("--helius-env", default=DEFAULT_HELIUS_ENV)
    ap.add_argument("--max-calls", type=int, default=2500)
    ap.add_argument("--rps", type=float, default=MAX_RPS)
    ap.add_argument("--gateway", action="append", help="IPFS gateway base (repeatable)")
    ap.add_argument("--keep-json", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sample_path = out / "sample.parquet"
    if sample_path.exists():
        import duckdb

        cur = duckdb.connect().execute(f"select * from read_parquet('{sample_path}')")
        cols = [d[0] for d in cur.description]
        sample = [dict(zip(cols, r)) for r in cur.fetchall()]
        log.info("sample reused n=%d", len(sample))
    else:
        sample = build_sample(args.n_per_group)
        digest = write_sample(sample, out)
        log.info("sample written n=%d sha256=%s", len(sample), digest)
    url = read_helius_url(args.helius_env)
    budget = CreditBudget(args.max_calls, 1)
    limiter = RateLimiter(min(args.rps, MAX_RPS))
    summary = run(
        sample, out, make_rpc(url, budget, limiter), urllib_get,
        args.gateway or list(DEFAULT_GATEWAYS), budget, args.keep_json,
    )
    log.info("calls_used=%d cap_hit=%s pass=%s", summary["calls_used"], summary["cap_hit"], summary["pass"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
