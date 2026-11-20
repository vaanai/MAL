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
    The recorder takes no lab data. By default it records Jupiter's own lists.
    jupiter_list   GET https://api.jup.ag/tokens/v2/toptrending/<interval>?limit=100
                   and .../toporganicscore/<interval>?limit=100 (default 1h).
                   One row per list entry per cycle: list, rank (0-based),
                   organicScore, organicScoreLabel, isVerified, holderCount.
                   Docs: https://developers.jup.ag/docs/tokens/token-information.md
                   ("Get Category"); keyless 0.5 RPS: https://developers.jup.ag/llms.txt
    jupiter_token  GET https://api.jup.ag/tokens/v2/search?query=<m1,m2,...>
                   (up to 100 mints per call) for every mint in either list,
                   plus the optional --mints-file mints (non-sealed sources
                   only; never FLOOR/OSB candidates, see docs/research/
                   token-signal-recorder.md). Fields kept: organicScore,
                   organicScoreLabel, isVerified, tags, holderCount,
                   numTraders / numOrganicBuyers / numNetBuyers for stats5m,
                   stats1h, stats6h, updatedAt. NO price, liquidity, volume or
                   priceChange field is kept (QP B2). A mint Jupiter does not
                   index gets {"found": false}.
    recorder       One header row per cycle (kind "cycle"): cycle_id, start and
                   end ts, tool_sha256 and tool_git (commit), mints_file_sha256,
                   lists_sha256 (hash of the sorted list ids), n_list_mints,
                   n_file_mints, lists_ok, calls, capped, errors. Written every
                   cycle, also when a cycle fails.
    pump_coin      GET https://frontend-api-v3.pump.fun/coins/<mint> (only for
                   --mints-file mints, only with --pump). OFF by default.
                   Checked 2026-11-20: a plain client gets a Cloudflare "Just a
                   moment" challenge and pump.fun publishes no public docs or
                   terms for it. The tool never tries to get past a challenge:
                   a 403/429/5xx or an HTML body counts a block, and after 3 in
                   a row (across cycles) the source is off for the run with one
                   row {"blocked": true}. THREAD-WAKE has no usable free source
                   today.

Politeness and limits
    - Generic User-Agent "token-signal-recorder/1.0 (research; polite)".
    - Min gap between calls --min-gap-s (default 2.5 s, below 0.5 rps).
    - Retries: 3 tries, exponential backoff, Retry-After honoured (capped 60 s).
    - Hard cap --max-calls-per-hour (default 400), sliding 3600 s window,
      retries count. At the cap a cycle is cut short and a status row
      (source "recorder", mint null) says so.
    - SIGTERM / SIGINT: finish the current line, then exit 0.
    - Any per-cycle exception is logged as a recorder row and the loop goes on.
    - --max-hours N ends the run cleanly after N hours (the job's end time).
    - Stateless and append-only, so a restarted job (resumable) loses at most
      one cycle.

Usage
    python3 tools/token_signal_recorder.py --out-dir /data2/mal/token-signals/out \
        [--mints-file mints.txt] [--interval-min 15] [--max-hours 132] [--pump] [--once]
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import signal
import subprocess
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
STATS_KEYS = ("numTraders", "numOrganicBuyers", "numNetBuyers")  # no price or volume fields (QP B2)
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
    with open(path) as fh:  # OSError handled by the caller
        for line in fh:
            m = line.split("#", 1)[0].strip()
            if m and MINT_RE.match(m) and m not in seen:
                seen.add(m)
                out.append(m)
    return out


def sha256_file(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def tool_git():
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        return subprocess.run(["git", "-C", here, "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


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
        self._backoff = 2

    def _budget(self):
        now = self.clock()
        while self.calls and now - self.calls[0] >= 3600:
            self.calls.popleft()
        if len(self.calls) >= self.cap:
            raise CapReached()

    def get_json(self, url: str):
        err = None
        for attempt in range(self.tries):
            if attempt:
                self.sleep(min(self._backoff, 60))
            if self.stop.is_set():
                raise InterruptedError()
            self._budget()
            if self.last is not None:
                wait = self.min_gap_s - (self.clock() - self.last)
                if wait > 0:
                    self.sleep(wait)
                    if self.stop.is_set():
                        raise InterruptedError()
            self.last = self.clock()
            self.calls.append(self.last)
            self.total_calls += 1
            self._backoff = 2 ** (attempt + 1)
            try:
                status, headers, body = self.fetch(url)
            except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
                err = f"net:{type(e).__name__}"
                continue
            is_html = body[:200].lstrip().lower().startswith((b"<!doctype", b"<html", b"<"))
            if status == 200:
                if is_html:
                    raise Blocked("html body")
                try:
                    return json.loads(body)
                except ValueError:
                    err = "bad json"
                    continue
            if status in (403, 429, 503) and is_html:
                raise Blocked(f"challenge {status}")
            if status == 429 or status >= 500:
                err = f"http {status}"
                try:
                    ra = float({k.lower(): v for k, v in headers.items()}.get("retry-after", ""))
                except ValueError:
                    ra = 0.0
                self._backoff = max(ra, self._backoff)
                continue
            if status in (401, 403):
                raise Blocked(f"http {status}")
            err = f"http {status}"
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
        "updatedAt": tok.get("updatedAt"),
    }
    for w in ("stats5m", "stats1h", "stats6h"):
        s = tok.get(w)
        s = s if isinstance(s, dict) else {}
        f[w] = {k: s.get(k) for k in STATS_KEYS}
    return f


def list_fields(name, interval, rank, tok):
    return {"list": name, "interval": interval, "rank": rank,
            "organicScore": tok.get("organicScore"),
            "organicScoreLabel": tok.get("organicScoreLabel"),
            "isVerified": tok.get("isVerified"), "holderCount": tok.get("holderCount")}


def dicts(data):
    return [x for x in data if isinstance(x, dict) and isinstance(x.get("id"), str)] if isinstance(data, list) else []


class Recorder:
    def __init__(self, client: Client, out_dir: str, mints_file=None, pump=False,
                 rank_interval="1h", now=utc_now):
        self.c = client
        self.out_dir = out_dir
        self.mints_file = mints_file
        self.pump = pump
        self.rank_interval = rank_interval
        self.now = now
        self.pump_blocks = 0
        self.rows_written = 0
        self.cycle_n = 0
        self.last_file_mints = []
        self.tool_sha = sha256_file(os.path.abspath(__file__))
        self.tool_git = tool_git()
        os.makedirs(out_dir, exist_ok=True)

    def write(self, mint, source, fields, fts=None):
        t = self.now()
        path = os.path.join(self.out_dir, f"signals-{t.strftime('%Y%m%d')}.jsonl")
        row = {"mint": mint, "ts": iso(t), "source": source, "fields": fields}
        if fts:
            row["fetch_ts"] = fts
        with open(path, "a") as fh:
            fh.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
        self.rows_written += 1

    def cycle(self) -> dict:
        self.cycle_n += 1
        start = iso(self.now())
        info = {"capped": False, "errors": [], "lists_ok": [False, False], "n_list_mints": 0,
                "lists_sha256": None}
        file_mints = self.last_file_mints
        if self.mints_file:
            try:
                file_mints = self.last_file_mints = read_mints(self.mints_file)
            except OSError as e:
                info["errors"].append(f"mints_file: {type(e).__name__} (kept last good list)")
        calls0 = self.c.total_calls
        try:
            self._jupiter(file_mints, info)
            if self.pump and self.pump_blocks < 3:
                self._pump(file_mints, info)
        except CapReached:
            info["capped"] = True
        except InterruptedError:
            info["interrupted"] = True
        except Exception as e:  # keep the run alive: log and carry on
            info["errors"].append(f"cycle: {type(e).__name__}: {str(e)[:80]}")
        self.write(None, "recorder", {
            "kind": "cycle", "cycle_id": f"{start}#{self.cycle_n}", "start": start,
            "end": iso(self.now()), "tool_sha256": self.tool_sha, "tool_git": self.tool_git,
            "mints_file_sha256": sha256_file(self.mints_file) if self.mints_file else None,
            "lists_sha256": info["lists_sha256"], "n_list_mints": info["n_list_mints"],
            "n_file_mints": len(file_mints), "lists_ok": info["lists_ok"],
            "calls": self.c.total_calls - calls0, "capped": info["capped"],
            "errors": info["errors"][:5], "pump_on": bool(self.pump and self.pump_blocks < 3)})
        return info

    def _jupiter(self, file_mints, info):
        ids = []
        for i, name in enumerate(("toptrending", "toporganicscore")):
            url = f"{JUP_BASE}/{name}/{self.rank_interval}?limit=100"
            try:
                data = self.c.get_json(url)
            except (RuntimeError, Blocked) as e:
                info["errors"].append(f"{name}: {e}")
                continue
            fts = iso(self.now())
            toks = dicts(data)
            info["lists_ok"][i] = isinstance(data, list)
            for rank, tok in enumerate(toks):
                self.write(tok["id"], "jupiter_list", list_fields(name, self.rank_interval, rank, tok), fts)
                ids.append(tok["id"])
        info["lists_sha256"] = hashlib.sha256("\n".join(sorted(set(ids))).encode()).hexdigest()
        info["n_list_mints"] = len(set(ids))
        mints = list(dict.fromkeys(ids + list(file_mints)))
        for i in range(0, len(mints), 100):
            chunk = mints[i:i + 100]
            url = f"{JUP_BASE}/search?query={urllib.parse.quote(','.join(chunk), safe=',')}"
            try:
                data = self.c.get_json(url)
            except (RuntimeError, Blocked) as e:
                info["errors"].append(f"search: {e}")
                continue
            fts = iso(self.now())
            by_id = {tok["id"]: tok for tok in dicts(data)}
            for m in chunk:
                tok = by_id.get(m)
                self.write(m, "jupiter_token", jup_fields(tok) if tok else {"found": False}, fts)

    def _pump(self, mints, info):
        for m in mints:
            if self.c.stop.is_set():
                raise InterruptedError()
            if self.pump_blocks >= 3:
                return
            try:
                d = self.c.get_json(f"{PUMP_BASE}/coins/{m}")
                self.pump_blocks = 0
                d = d if isinstance(d, dict) else {}
                self.write(m, "pump_coin", {k: d.get(k) for k in PUMP_KEYS}, iso(self.now()))
            except (Blocked, RuntimeError) as e:
                self.pump_blocks += 1
                info["errors"].append(f"pump: {e}")
                if self.pump_blocks >= 3:
                    self.write(None, "pump_coin", {"blocked": True, "reason": str(e),
                                                   "note": "source disabled for this run"})
                    return


def run(rec: Recorder, interval_s: float, once=False, max_hours=None):
    end = None if max_hours is None else rec.c.clock() + max_hours * 3600
    while not rec.c.stop.is_set():
        started = rec.c.clock()
        try:
            rec.cycle()
        except Exception as e:  # e.g. disk error while writing: never end the run
            print(f"cycle failed: {type(e).__name__}: {str(e)[:80]}", file=sys.stderr, flush=True)
        if once or (end is not None and rec.c.clock() >= end):
            break
        rec.c.stop.wait(max(0.0, interval_s - (rec.c.clock() - started)))
        if end is not None and rec.c.clock() >= end:
            break
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mints-file", default=None, help="optional extra mints (non-sealed sources only)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--interval-min", type=float, default=15.0)
    ap.add_argument("--min-gap-s", type=float, default=2.5)
    ap.add_argument("--max-calls-per-hour", type=int, default=400)
    ap.add_argument("--max-hours", type=float, default=None, help="end the run after this many hours")
    ap.add_argument("--rank-interval", default="1h", choices=["5m", "1h", "6h", "24h"])
    ap.add_argument("--pump", action="store_true", help="also poll pump.fun coin endpoint (see docstring)")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    stop = threading.Event()
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda *_: stop.set())
    client = Client(stop=stop, min_gap_s=a.min_gap_s, max_calls_per_hour=a.max_calls_per_hour)
    rec = Recorder(client, a.out_dir, a.mints_file, pump=a.pump, rank_interval=a.rank_interval)
    return run(rec, a.interval_min * 60, once=a.once, max_hours=a.max_hours)


if __name__ == "__main__":
    sys.exit(main())
