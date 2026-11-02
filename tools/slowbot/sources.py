"""DexScreener and GeckoTerminal clients with token-bucket limiters and retry.

Transport is injectable so tests never touch the network.
transport(url) -> (status:int, body:str, headers:dict)
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

DS_BASE = "https://api.dexscreener.com"
GT_BASE = "https://api.geckoterminal.com/api/v2"

RATE_LIMITS_PER_MIN = {"ds_pairs": 250, "ds_profiles": 55, "gecko": 25}
RETRY = {"max_tries": 4, "base_s": 1.0, "cap_s": 30.0}
BATCH = 30


class TokenBucket:
    def __init__(self, per_min, clock=time.monotonic, sleep=time.sleep):
        self.rate = per_min / 60.0
        self.cap = float(max(1, per_min // 6))  # small burst
        self.tokens = self.cap
        self.clock, self.sleep = clock, sleep
        self.t = clock()
        self.lock = threading.Lock()

    def acquire(self):
        with self.lock:
            while True:
                now = self.clock()
                self.tokens = min(self.cap, self.tokens + (now - self.t) * self.rate)
                self.t = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                self.sleep((1 - self.tokens) / self.rate)


def urllib_transport(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "mal-slowbot/0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers or {})


class HttpError(RuntimeError):
    pass


def get_json(url, bucket, transport=urllib_transport, sleep=time.sleep, retry=RETRY):
    last = None
    for i in range(retry["max_tries"]):
        bucket.acquire()
        try:
            status, body, hdrs = transport(url)
        except Exception as e:  # network error: retry
            status, body, hdrs, last = 599, "", {}, type(e).__name__
        if status == 200:
            return json.loads(body)
        last = f"status {status}"
        if status == 429 or status >= 500:
            wait = min(retry["cap_s"], retry["base_s"] * 2 ** i)
            try:
                wait = max(wait, min(retry["cap_s"], float((hdrs or {}).get("Retry-After"))))
            except (TypeError, ValueError):
                pass
            sleep(wait)
            continue
        raise HttpError(f"{last} for {url.split('?')[0]}")
    raise HttpError(f"gave up after {retry['max_tries']} tries ({last}) for {url.split('?')[0]}")


class DexScreener:
    def __init__(self, transport=urllib_transport, sleep=time.sleep, clock=time.monotonic, limits=None):
        lim = limits or RATE_LIMITS_PER_MIN
        self.transport, self.sleep = transport, sleep
        self.pairs_bucket = TokenBucket(lim["ds_pairs"], clock, sleep)
        self.profiles_bucket = TokenBucket(lim["ds_profiles"], clock, sleep)

    def _get(self, path, bucket):
        return get_json(DS_BASE + path, bucket, self.transport, self.sleep)

    @staticmethod
    def _sol_addrs(rows):
        out, seen = [], set()
        for r in rows if isinstance(rows, list) else []:
            if r.get("chainId") == "solana" and r.get("tokenAddress") and r["tokenAddress"] not in seen:
                seen.add(r["tokenAddress"])
                out.append(r["tokenAddress"])
        return out

    def recent_boosts(self):
        return self._sol_addrs(self._get("/token-boosts/latest/v1", self.profiles_bucket))

    def recent_profiles(self):
        return self._sol_addrs(self._get("/token-profiles/latest/v1", self.profiles_bucket))

    def community_takeovers(self):
        return self._sol_addrs(self._get("/community-takeovers/latest/v1", self.profiles_bucket))

    def pool_snapshots(self, mints):
        """Pairs for token addresses, batches of <= 30 (solana pairs only)."""
        mints = list(dict.fromkeys(mints))
        out = []
        for i in range(0, len(mints), BATCH):
            rows = self._get("/tokens/v1/solana/" + ",".join(mints[i:i + BATCH]), self.pairs_bucket)
            out.extend(p for p in rows if isinstance(p, dict) and p.get("chainId") == "solana")
        return out


class Gecko:
    def __init__(self, transport=urllib_transport, sleep=time.sleep, clock=time.monotonic, limits=None):
        lim = limits or RATE_LIMITS_PER_MIN
        self.transport, self.sleep = transport, sleep
        self.bucket = TokenBucket(lim["gecko"], clock, sleep)

    @staticmethod
    def _mints(doc):
        out = []
        for row in (doc or {}).get("data", []):
            tid = (((row.get("relationships") or {}).get("base_token") or {}).get("data") or {}).get("id", "")
            if tid.startswith("solana_"):
                out.append(tid[len("solana_"):])
        return list(dict.fromkeys(out))

    def new_pool_mints(self):
        return self._mints(get_json(GT_BASE + "/networks/solana/new_pools", self.bucket, self.transport, self.sleep))

    def trending_pool_mints(self):
        return self._mints(get_json(GT_BASE + "/networks/solana/trending_pools", self.bucket, self.transport, self.sleep))
