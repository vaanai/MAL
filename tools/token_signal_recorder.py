#!/usr/bin/env python3
"""Forward recorder for free public token-signal endpoints (JUP-ORGANIC, THREAD-WAKE).

README
------
What it does
    Every --interval-min minutes (default 15) it reads a mints file (one mint
    per line, '#' comments, re-read every cycle) and appends JSONL rows to
    <out-dir>/signals-YYYYMMDD.jsonl (UTC day). Row:
    {"mint", "ts" (UTC ISO, ms), "source", "fields": {...}}.
    It records snapshots only. It prices nothing and reads no tape.

Sources (free, keyless, no Helius, no wallet)
    jupiter_token  GET https://api.jup.ag/tokens/v2/search?query=<m1,m2,...>
                   (up to 100 mints per call). Fields kept: organicScore,
                   organicScoreLabel, isVerified, tags (contains "strict" /
                   "verified"), holderCount, liquidity, usdPrice, and
                   numTraders / numOrganicBuyers / numNetBuyers / priceChange
                   for stats5m, stats1h, stats6h. Docs:
                   https://developers.jup.ag/docs/tokens/token-information.md
                   https://developers.jup.ag/llms.txt (keyless 0.5 RPS on
                   api.jup.ag). A mint Jupiter does not index gets
                   {"found": false}.
    jupiter_rank   GET https://api.jup.ag/tokens/v2/toptrending/<interval>?limit=100
                   and .../toporganicscore/<interval>?limit=100 (default 1h).
                   One row per recorded mint: rank (0-based) in each list, or
                   null when absent. Docs: same page, "Get Category".
    pump_coin      GET https://frontend-api-v3.pump.fun/coins/<mint>
                   (reply_count, last_reply, is_currently_live). OFF by
                   default (--pump to enable). Checked 2026-11-20: this host
                   answers an automated client with a Cloudflare "Just a
                   moment" challenge, and pump.fun publishes no public API
                   docs or terms for it. The tool does not try to get past a
                   challenge: on a 403/429/503 or an HTML body it counts a
                   block, and after 3 in a row it turns the source off for
                   the run and writes one row {"blocked": true}. Treat
                   THREAD-WAKE as NOT available from a plain HTTP client
                   until that changes.

Politeness and limits
    - Generic User-Agent "token-signal-recorder/1.0 (research; polite)".
    - Min gap between calls --min-gap-s (default 2.5 s, below 0.5 rps).
    - Retries: 3 tries, exponential backoff, Retry-After honoured (capped 60 s).
    - Hard cap --max-calls-per-hour (default 400), sliding 3600 s window,
      retries count. At the cap a cycle is cut short and a status row
      (source "recorder", mint null) says so.
    - SIGTERM / SIGINT: finish the current line, then exit 0.

Usage
    python3 tools/token_signal_recorder.py --mints-file mints.txt \
        --out-dir /data2/mal/token-signals [--interval-min 15] [--pump] [--once]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timezone

UA = "token-signal-recorder/1.0 (research; polite)"
JUP_BASE = "https://api.jup.ag/tokens/v2"
PUMP_BASE = "https://frontend-api-v3.pump.fun"
MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
STATS_KEYS = ("priceChange", "numTraders", "numOrganicBuyers", "numNetBuyers")
PUMP_KEYS = ("reply_count", "last_reply", "is_currently_live", "usd_market_cap")


class Blocked(Exception):
    """Endpoint refused an automated client (challenge, 403, 429 after retries)."""


class CapReached(Exception):
    """Hourly call cap hit."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def read_mints(path: str) -> list[str]:
    out, seen = [], set()
    with open(path) as fh:
        for line in fh:
            m = line.split("#", 1)[0].strip()
            if m and MINT_RE.match(m) and m not in seen:
                seen.add(m)
                out.append(m)
    return out


def http_get(url: str, timeout: float = 20.0):
    """Default transport: returns (status, headers dict, body bytes)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()


class Client:
    def __init__(self, fetch=http_get, sleep=None, clock=time.monotonic, stop=None,
                 min_gap_s=2.5, max_calls_per_hour=400, tries=3):
        self.fetch = fetch
        self.stop = stop or threading.Event()
        self.sleep = sleep or (lambda s: self.stop.wait(s))
        self.clock = clock
        self.min_gap_s = min_gap_s
        self.cap = max_calls_per_hour
        self.tries = tries
        self.calls = deque()
        self.last = None
        self.total_calls = 0

    def _budget(self):
        now = self.clock()
        while self.calls and now - self.calls[0] >= 3600:
            self.calls.popleft()
        if len(self.calls) >= self.cap:
            raise CapReached()

    def get_json(self, url: str):
        err = None
        for attempt in range(self.tries):
            if self.stop.is_set():
                raise InterruptedError()
            self._budget()
            if self.last is not None:
                wait = self.min_gap_s - (self.clock() - self.last)
                if wait > 0:
                    self.sleep(wait)
            self.last = self.clock()
            self.calls.append(self.last)
            self.total_calls += 1
            try:
                status, headers, body = self.fetch(url)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                err = f"net:{type(e).__name__}"
                self.sleep(min(2 ** attempt * 2, 60))
                continue
            if status == 200:
                head = body[:64].lstrip().lower()
                if head.startswith(b"<"):
                    raise Blocked("html body")
                try:
                    return json.loads(body)
                except ValueError:
                    err = "bad json"
            elif status in (403, 503) and body[:200].lstrip().lower().startswith((b"<!doctype", b"<html")):
                raise Blocked(f"challenge {status}")
            elif status == 429 or status >= 500:
                err = f"http {status}"
                try:
                    ra = float({k.lower(): v for k, v in headers.items()}.get("retry-after", ""))
                except ValueError:
                    ra = 0.0
                self.sleep(min(max(ra, 2 ** attempt * 2), 60))
                continue
            elif status in (401, 403):
                raise Blocked(f"http {status}")
            else:
                err = f"http {status}"
            self.sleep(min(2 ** attempt * 2, 60))
        if err == "http 429":
            raise Blocked("429 after retries")
        raise RuntimeError(err or "failed")


def jup_fields(tok: dict) -> dict:
    f = {
        "found": True,
        "organicScore": tok.get("organicScore"),
        "organicScoreLabel": tok.get("organicScoreLabel"),
        "isVerified": tok.get("isVerified"),
        "tags": tok.get("tags"),
        "holderCount": tok.get("holderCount"),
        "liquidity": tok.get("liquidity"),
        "usdPrice": tok.get("usdPrice"),
        "updatedAt": tok.get("updatedAt"),
    }
    for w in ("stats5m", "stats1h", "stats6h"):
        s = tok.get(w) or {}
        f[w] = {k: s.get(k) for k in STATS_KEYS}
    return f


class Recorder:
    def __init__(self, client: Client, out_dir: str, mints_file: str, pump=False,
                 rank_interval="1h", now=utc_now):
        self.c = client
        self.out_dir = out_dir
        self.mints_file = mints_file
        self.pump = pump
        self.rank_interval = rank_interval
        self.now = now
        self.pump_blocks = 0
        self.rows_written = 0
        os.makedirs(out_dir, exist_ok=True)

    def write(self, mint, source, fields):
        t = self.now()
        path = os.path.join(self.out_dir, f"signals-{t.strftime('%Y%m%d')}.jsonl")
        row = {"mint": mint, "ts": iso(t), "source": source, "fields": fields}
        with open(path, "a") as fh:
            fh.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
        self.rows_written += 1

    def cycle(self) -> dict:
        mints = read_mints(self.mints_file)
        info = {"mints": len(mints), "capped": False, "errors": []}
        try:
            self._jupiter(mints, info)
            if self.pump and self.pump_blocks < 3:
                self._pump(mints, info)
        except CapReached:
            info["capped"] = True
        except InterruptedError:
            info["interrupted"] = True
        if info["capped"] or info["errors"]:
            self.write(None, "recorder", {"capped": info["capped"], "errors": info["errors"][:5],
                                          "calls_total": self.c.total_calls})
        return info

    def _jupiter(self, mints, info):
        if not mints:
            return
        ranks = {}
        for name in ("toptrending", "toporganicscore"):
            url = f"{JUP_BASE}/{name}/{self.rank_interval}?limit=100"
            try:
                data = self.c.get_json(url)
                ranks[name] = {t.get("id"): i for i, t in enumerate(data)} if isinstance(data, list) else None
            except (RuntimeError, Blocked) as e:
                info["errors"].append(f"{name}: {e}")
                ranks[name] = None
        for i in range(0, len(mints), 100):
            chunk = mints[i:i + 100]
            url = f"{JUP_BASE}/search?query={urllib.parse.quote(','.join(chunk), safe=',')}"
            try:
                data = self.c.get_json(url)
            except (RuntimeError, Blocked) as e:
                info["errors"].append(f"search: {e}")
                continue
            by_id = {t.get("id"): t for t in data} if isinstance(data, list) else {}
            for m in chunk:
                tok = by_id.get(m)
                self.write(m, "jupiter_token", jup_fields(tok) if tok else {"found": False})
        for m in mints:
            self.write(m, "jupiter_rank", {
                "interval": self.rank_interval,
                "trending_rank": None if ranks.get("toptrending") is None else ranks["toptrending"].get(m),
                "organic_rank": None if ranks.get("toporganicscore") is None else ranks["toporganicscore"].get(m),
                "lists_ok": [ranks.get("toptrending") is not None, ranks.get("toporganicscore") is not None],
            })

    def _pump(self, mints, info):
        for m in mints:
            if self.c.stop.is_set():
                raise InterruptedError()
            try:
                d = self.c.get_json(f"{PUMP_BASE}/coins/{m}")
                self.pump_blocks = 0
                d = d if isinstance(d, dict) else {}
                self.write(m, "pump_coin", {k: d.get(k) for k in PUMP_KEYS})
            except Blocked as e:
                self.pump_blocks += 1
                info["errors"].append(f"pump: {e}")
                if self.pump_blocks >= 3:
                    self.write(None, "pump_coin", {"blocked": True, "reason": str(e),
                                                   "note": "source disabled for this run"})
                    return
            except RuntimeError as e:
                info["errors"].append(f"pump: {e}")


def run(rec: Recorder, interval_s: float, once=False):
    while not rec.c.stop.is_set():
        started = rec.c.clock()
        rec.cycle()
        if once:
            break
        rec.c.stop.wait(max(0.0, interval_s - (rec.c.clock() - started)))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mints-file", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--interval-min", type=float, default=15.0)
    ap.add_argument("--min-gap-s", type=float, default=2.5)
    ap.add_argument("--max-calls-per-hour", type=int, default=400)
    ap.add_argument("--rank-interval", default="1h", choices=["5m", "1h", "6h", "24h"])
    ap.add_argument("--pump", action="store_true", help="also poll pump.fun coin endpoint (see docstring)")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    stop = threading.Event()
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda *_: stop.set())
    client = Client(stop=stop, min_gap_s=a.min_gap_s, max_calls_per_hour=a.max_calls_per_hour)
    rec = Recorder(client, a.out_dir, a.mints_file, pump=a.pump, rank_interval=a.rank_interval)
    return run(rec, a.interval_min * 60, once=a.once)


if __name__ == "__main__":
    sys.exit(main())
