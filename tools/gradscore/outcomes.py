"""Outcome tracker for gradscore (separate process, may run later). PAPER ONLY, public GeckoTerminal OHLCV.

  python -m tools.gradscore.outcomes --out DIR [--config FILE] [--once]

Reads DIR/discoveries-*.jsonl (mint, pool, first_seen_s, price_native) and writes DIR/outcomes-DATE.jsonl (never the score files).
Per scored mint, one row per horizon (+15m, +1h, +4h, +24h after first sight): the price (quote-token, i.e. SOL, close of the 5 m
candle containing first_seen + horizon), the running max high since first sight, and the multiple vs the first-sight price.
The +24h row's max_mult is the 24 h max price multiple. A row is written once per (mint, horizon); `status` is ok or no_data
(written after 3 failed attempts, or when the candles hold no usable price).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import signal
import sys
import time

from tools.gradscore import config as cfgmod
from tools.gradscore.run import day_file, iso
from tools.gradscore.score import append_jsonl
from tools.slowbot.positions import atomic_write_json
from tools.slowbot.sources import GT_BASE, Gecko, HttpError, get_json, urllib_transport

HORIZONS_MIN = (15, 60, 240, 1440)
CANDLE_S = 300
MAX_ATTEMPTS = 3
GRACE_S = 120  # wait this long past the horizon so the covering candle is closed


def load_discoveries(out_dir):
    rows = {}
    for p in sorted(glob.glob(os.path.join(out_dir, "discoveries-*.jsonl"))):
        for line in open(p):
            try:
                r = json.loads(line)
                if r.get("is_pump_suffix"):  # the tokens that get scored (suffix heuristic; see run._handle)
                    rows.setdefault(r["mint"], r)
            except (ValueError, KeyError, TypeError):
                continue
    return rows


def due_work(discoveries, done, now_s):
    """[(mint, horizon_min)] whose due time (first_seen + horizon + grace) has passed and that have no outcome row yet."""
    work = []
    for mint, r in discoveries.items():
        try:
            t0 = float(r["first_seen_s"])
        except (KeyError, TypeError, ValueError):
            continue
        for h in HORIZONS_MIN:
            if f"{mint}|{h}" not in done and now_s >= t0 + h * 60 + GRACE_S:
                work.append((t0 + h * 60, mint, h))
    work.sort()
    return [(m, h) for _, m, h in work]


def parse_candles(doc):
    """[(ts, o, h, l, c)] ascending, from a Gecko ohlcv doc. [] if unusable."""
    try:
        rows = doc["data"]["attributes"]["ohlcv_list"]
        out = sorted((int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows)
        return [c for c in out if c[4] > 0]
    except (KeyError, TypeError, ValueError, IndexError):
        return []


def summarise(candles, t0, horizon_min, entry):
    """price at horizon (close of the candle covering t0+h), running max high since first sight, multiples. None if no candle covers."""
    th = t0 + horizon_min * 60
    cover = [c for c in candles if c[0] <= th < c[0] + CANDLE_S]
    since = [c for c in candles if c[0] + CANDLE_S > t0 and c[0] <= th]
    if not cover or not since or not entry or entry <= 0:
        return None
    price = cover[-1][4]
    mx = max(c[2] for c in since)
    return {"price_native": price, "ret": price / entry - 1.0, "max_high_native": mx, "max_mult": mx / entry, "candles_n": len(since)}


class Tracker:
    def __init__(self, out_dir, gecko, clock=time.time, sleep=time.sleep, transport=urllib_transport):
        self.out, self.gecko, self.clock, self.sleep, self.transport = out_dir, gecko, clock, sleep, transport
        self.state_path = os.path.join(out_dir, "outcomes-state.json")
        try:
            with open(self.state_path) as f:
                self.state = json.load(f)
        except Exception:
            self.state = {}
        self.state.setdefault("done", {})
        self.state.setdefault("attempts", {})
        for p in glob.glob(os.path.join(out_dir, "outcomes-2*.jsonl")):  # rebuild done-set from the log if state is lost
            for line in open(p):
                try:
                    r = json.loads(line)
                    self.state["done"].setdefault(f"{r['mint']}|{r['horizon_min']}", 1)
                except (ValueError, KeyError, TypeError):
                    continue
        self.stop = False

    def _candles(self, pool, t0, horizon_min):
        before = int(t0 + horizon_min * 60 + CANDLE_S)
        n = min(1000, horizon_min * 60 // CANDLE_S + 3)
        url = (GT_BASE + f"/networks/solana/pools/{pool}/ohlcv/minute?aggregate=5&limit={n}&currency=token&before_timestamp={before}")
        return parse_candles(get_json(url, self.gecko.bucket, self.transport, self.sleep))

    def step(self, max_calls=None):
        now = self.clock()
        disc = load_discoveries(self.out)
        calls = wrote = 0
        for mint, h in due_work(disc, self.state["done"], now):
            if self.stop or (max_calls is not None and calls >= max_calls):
                break
            r = disc[mint]
            key = f"{mint}|{h}"
            t0, entry = float(r["first_seen_s"]), r.get("price_native")
            status, res = "ok", None
            calls += 1
            try:
                res = summarise(self._candles(r["pool"], t0, h), t0, h, entry)
                if res is None:
                    status = "no_data"
            except (HttpError, OSError, ValueError, KeyError):
                status = "error"
            if status == "error":
                n = self.state["attempts"][key] = self.state["attempts"].get(key, 0) + 1
                if n < MAX_ATTEMPTS:
                    continue
                status = "no_data"
            row = {"mint": mint, "pool": r["pool"], "horizon_min": h, "first_seen_s": t0, "entry_price_native": entry,
                   "ts_utc": iso(now), "status": status}
            if res:
                row.update(res)
            append_jsonl(day_file(self.out, "outcomes", now), row)
            self.state["done"][key] = 1
            wrote += 1
        atomic_write_json(self.state_path, self.state)
        return {"calls": calls, "rows": wrote}

    def run(self, once=False, idle_s=300):
        while not self.stop:
            self.step()
            if once:
                break
            self.sleep(idle_s)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--config", default=None)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    cfg = cfgmod.load_config(a.config)
    gt = Gecko(urllib_transport, limits={"ds_pairs": 250, "ds_profiles": 55, "gecko": cfg["outcomes_gecko_per_min"]})
    tr = Tracker(a.out, gt)
    signal.signal(signal.SIGTERM, lambda *_: setattr(tr, "stop", True))
    signal.signal(signal.SIGINT, lambda *_: setattr(tr, "stop", True))
    tr.run(once=a.once)
    print("outcomes done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
