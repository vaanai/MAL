#!/usr/bin/env python3
"""P28-H Stage 0d: fourth fetch of the same stage0c exploration mints' metadata (DESIGN C1).

Reads ONLY the stage0c uris.jsonl and status.jsonl (block name and json_ok flag; grp is never read).
Fetches IPFS-CID uris through the local gateway port only (127.0.0.1:<port>, never the kubo RPC,
never a public gateway, never a non-IPFS host, never /data/mal/meta-capture). Non-IPFS uris and mints
without a uri are counted and not fetched ("fetched 2026-10, not create-time content" class).

Writes under --out: fetched.jsonl (mint, block, status, text fields; no label), summary.json, and
prints counts only (ok / failed / non_ipfs / no_uri per block). No Helius or other paid call.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from tools import ipfs_fetch as ipf
from tools import meta_stage0 as ms

SRC = "/data/mal/hunt-1008/push-1016/hunt13/llm-traders/stage0c"
OUT = "/data/mal/hunt-1008/push-1016/hunt30/p28h/stage0d"
MAX_BYTES = 262144
ATTEMPTS = 3
TEXT_FIELDS = ("name", "symbol", "description", "twitter", "telegram", "website")
FIELD_CAP = 2000
RETRY_STATUSES = ("timeout", "http_0", "http_500", "http_502", "http_503", "http_504", "http_429")


class Limiter:
    def __init__(self, rps: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.gap, self.clock, self.next, self.lock = 1.0 / rps, clock, 0.0, threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = self.clock()
            at = max(now, self.next)
            self.next = at + self.gap
        if at > now:
            time.sleep(at - now)


def parse_meta(body: bytes) -> dict[str, Any] | None:
    """Text fields of a metadata JSON, or None when it is not a JSON object."""
    try:
        obj = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    out: dict[str, Any] = {}
    for k in TEXT_FIELDS:
        v = obj.get(k)
        if v is None and isinstance(obj.get("extensions"), dict):
            v = obj["extensions"].get(k)
        out[k] = v[:FIELD_CAP] if isinstance(v, str) else ""
    return out


def fetch_one(uri: str, local: ipf.LocalGateway, http: Callable[..., Any], limiter: Limiter,
              sleep: Callable[[float], None] = time.sleep) -> tuple[str, dict[str, Any] | None, str | None]:
    """(status, fields, body_sha256). status: ok | non_ipfs | bad_json | http_N | timeout | ..."""
    plan = ipf.classify_uri(uri)
    if plan.ipfs_path is None:
        return "non_ipfs", None, None
    url = local.key + plan.ipfs_path
    err = "http_0"
    for i in range(ATTEMPTS):
        limiter.wait()
        try:
            res = http(url, local.json_timeout_s, MAX_BYTES, False)
            status, body = res[0], res[1]
        except TimeoutError:
            err = "timeout"
        except Exception as exc:  # class name only; never the URL
            err = "net_" + type(exc).__name__
        else:
            if status in (200, 206) and body:
                fields = parse_meta(body)
                digest = hashlib.sha256(body).hexdigest()
                return ("ok", fields, digest) if fields is not None else ("bad_json", None, digest)
            err = f"http_{status}"
        if err not in RETRY_STATUSES or i == ATTEMPTS - 1:
            break
        sleep(2.0 * (i + 1))
    return err, None, None


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def run(src: Path, out: Path, local: ipf.LocalGateway, http: Callable[..., Any], workers: int,
        rps: float, max_runtime_s: float) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    uris = {r["mint"]: r.get("uri") for r in load_jsonl(src / "uris.jsonl")}
    sts = load_jsonl(src / "status.jsonl")
    meta = {r["mint"]: (r["block"], bool(r.get("json_ok"))) for r in sts}  # grp deliberately not read
    mints = sorted(meta, key=lambda m: hashlib.sha256(("P28H-0D" + m).encode()).hexdigest())
    limiter, deadline = Limiter(rps), time.monotonic() + max_runtime_s
    rows: dict[str, dict[str, Any]] = {}

    def work(m: str) -> None:
        block, s0c = meta[m]
        uri = uris.get(m)
        if not uri:
            status, fields, digest = "no_uri", None, None
        elif time.monotonic() > deadline:
            status, fields, digest = "deadline", None, None
        else:
            status, fields, digest = fetch_one(uri, local, http, limiter)
        row = {"mint": m, "block": block, "status": status, "s0c_json_ok": s0c,
               "host_class": ipf.classify_uri(uri).host_class if uri else "none", "body_sha256": digest}
        if fields is not None:
            row.update(fields)
        rows[m] = row

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, mints))
    with open(out / "fetched.jsonl", "w") as fh:
        for m in mints:
            fh.write(json.dumps(rows[m], sort_keys=True) + "\n")
    counts: dict[str, Counter] = {}
    for r in rows.values():
        c = counts.setdefault(r["block"], Counter())
        key = "ok" if r["status"] == "ok" else r["status"] if r["status"] in ("non_ipfs", "no_uri") else "failed"
        c[key] += 1
        c["n"] += 1
        if r["status"] == "ok" and r["s0c_json_ok"]:
            c["ok_and_s0c_json_ok"] += 1
        if r["s0c_json_ok"]:
            c["s0c_json_ok"] += 1
    errs = Counter(r["status"] for r in rows.values() if r["status"] not in ("ok", "non_ipfs", "no_uri"))
    summary = {"per_block": {b: dict(c) for b, c in sorted(counts.items())}, "failed_by_class": dict(errs),
               "total": dict(sum(counts.values(), Counter())), "gateway": "local port only",
               "attempts": ATTEMPTS, "helius_credits": 0}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True))
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--local-gateway", default="http://127.0.0.1:8080")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--rps", type=float, default=10.0)
    ap.add_argument("--max-runtime-s", type=float, default=2400.0)
    a = ap.parse_args(argv)
    if "meta-capture" in a.src or "meta-capture" in a.out:
        raise SystemExit("refused: meta-capture path")
    local = ipf.parse_local_gateway(a.local_gateway, a.rps, 20.0, 30.0, 30.0)
    import functools
    http = functools.partial(ms.urllib_get, local_origin=local.origin)
    s = run(Path(a.src), Path(a.out), local, http, a.workers, a.rps, a.max_runtime_s)
    print(json.dumps(s, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
