"""Arena feed delay (hunt20): how far behind the chain the arena tape is, in whole slots. PAPER ONLY. Keyless public RPC, at most 2 requests per second.

    python -m tools.arena_feed_delay --tape-dir /data/mal/arena/tape --out /data/mal/arena/feed-delay/<UTC stamp>.json [--last-hours N]

The engine (tools/arena_paper.py) maps a decision instant to a tape slot from the feed's own arrival times. The feed is at confirmed commitment and lags the
chain, so without a correction every book would get that delay for free. This tool measures it ONCE per feed setup:

  1. read the tape's trade rows (type "trade"; the timing fields `slot` and `t_recv_ms` only: no fill, no reserve, no P&L is kept) and keep the first
     receive time of each slot;
  2. sample every 50th distinct slot, and at least 300 (the stride shrinks to get 300; fewer than 300 distinct slots is refused);
  3. fetch getBlockTime for each sampled slot from a public RPC (https, no key, no userinfo, no query, not Helius; at most 2 requests per second);
  4. delay = t_recv_ms - block_time * 1000 per slot; print the median, the 90th percentile and the seconds per slot (least-squares slope of block time on slot);
  5. print d = ceil(median delay / seconds per slot): the whole-slot delay to put in StackConfig.feed_delay_slots.

block_time has a resolution of one second, so each delay carries up to +1 s of quantisation (it overstates the delay by about half a second on average;
the median is used and d is therefore not understated). The JSON records the tape files read as path@sha256, the sampled (slot, t_recv_ms, block_time) triples
and the method. Missing blocks (skipped slots) are counted; more than 10% missing refuses the measurement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence
from urllib.parse import urlsplit

import numpy as np

from tools.h5_shadow import sps_ok

DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
STRIDE, MIN_SLOTS, MAX_RPS, MAX_MISSING = 50, 300, 2.0, 0.10
FORBIDDEN_PARTS = ("sealed", "forward", "shadow", "canary", "probe", "walk", "exp011", "exp012", "tip-tape", "meta-capture", "fresh-08", "truth-")
TAPE_RE = re.compile(r"^tape-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl(\.zst)?$")
BLOCK_UNAVAILABLE = (-32004, -32007, -32009)  # block not available / slot skipped or missing long-term storage


class Refused(Exception):
    pass


def check_rpc_url(url: str) -> str:
    """Public RPC only: https, a host that is not Helius, no userinfo, no query, no path (a key can sit in any of them)."""
    try:
        u = urlsplit(url)
    except ValueError as exc:
        raise Refused("--rpc-url is not a URL") from exc
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host:
        raise Refused("--rpc-url must be an https:// URL")
    if "helius" in url.lower() or u.username or u.password or u.query or u.fragment or u.path not in ("", "/"):
        raise Refused("--rpc-url must be a public keyless endpoint: no Helius, no userinfo, no query, no path")
    return url


def check_tape_dir(path: str | Path) -> Path:
    low = str(Path(path).resolve()).lower()
    for part in FORBIDDEN_PARTS:
        if part in low:
            raise Refused(f"tape dir path contains {part!r}: not an arena tape path")
    p = Path(path)
    if not p.is_dir():
        raise Refused(f"{p} is not a directory")
    return p


def tape_files(tape_dir: Path, last_hours: int | None = None) -> list[Path]:
    files = sorted(f for f in tape_dir.iterdir() if TAPE_RE.match(f.name))
    # an hour present both as .jsonl and .jsonl.zst is read once (the .zst is the compacted copy)
    by_hour: dict[str, Path] = {}
    for f in files:
        h = TAPE_RE.match(f.name).group(1)  # type: ignore[union-attr]
        if h not in by_hour or f.name.endswith(".zst"):
            by_hour[h] = f
    out = [by_hour[h] for h in sorted(by_hour)]
    return out[-last_hours:] if last_hours else out


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _lines(p: Path) -> Iterator[bytes]:
    if p.name.endswith(".zst"):
        proc = subprocess.Popen(["zstd", "-dc", "-q", str(p)], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        try:
            assert proc.stdout is not None
            yield from proc.stdout
        finally:
            if proc.stdout:
                proc.stdout.close()
            if proc.wait() != 0:
                raise Refused(f"zstd -dc failed on {p.name} (a truncated file?)")
    else:
        with open(p, "rb") as f:
            yield from f


def first_receive_by_slot(files: Sequence[Path]) -> dict[int, int]:
    """slot -> earliest t_recv_ms over the trade rows. Only the two timing fields of a trade row are kept."""
    out: dict[int, int] = {}
    for p in files:
        for line in _lines(p):
            if b'"trade"' not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("type") != "trade" or r.get("slot") is None or r.get("t_recv_ms") is None:
                continue
            s, t = int(r["slot"]), int(r["t_recv_ms"])
            if s not in out or t < out[s]:
                out[s] = t
    return out


def sample_slots(slots: Sequence[int], stride: int = STRIDE, min_n: int = MIN_SLOTS) -> tuple[list[int], int]:
    """Every `stride`-th distinct slot, with the stride shrunk (never below 1) so that at least `min_n` are sampled. Fewer than `min_n` distinct slots: refused."""
    ss = sorted(slots)
    if len(ss) < min_n:
        raise Refused(f"only {len(ss)} distinct slots in the tape; at least {min_n} are needed")
    st = max(1, min(stride, len(ss) // min_n))
    return ss[::st], st


class RateLimiter:
    """At most `rps` calls per second: wait() sleeps until 1/rps has passed since the previous call."""

    def __init__(self, rps: float = MAX_RPS, clock: Callable[[], float] | None = None, sleep: Callable[[float], None] | None = None) -> None:
        if not 0 < rps <= MAX_RPS:
            raise Refused(f"rps must be in (0, {MAX_RPS}]")
        self.gap, self.clock, self.sleep, self.last = 1.0 / rps, clock or time.monotonic, sleep or time.sleep, None

    def wait(self) -> None:
        now = self.clock()
        if self.last is not None and now - self.last < self.gap:
            self.sleep(self.gap - (now - self.last))
            now = self.clock()
        self.last = now


def rpc_block_time(url: str, slot: int, timeout: float = 20.0, tries: int = 3, sleep: Callable[[float], None] = time.sleep) -> int | None:
    """getBlockTime over HTTPS JSON-RPC. None = the block is not available (skipped slot). A 429 or a network error is retried, then raised."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getBlockTime", "params": [int(slot)]}).encode()
    for k in range(tries):
        try:
            req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
            resp = json.load(urllib.request.urlopen(req, timeout=timeout))
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:  # never echo the URL
            if k == tries - 1:
                raise Refused(f"getBlockTime failed for slot {slot}: {type(exc).__name__}") from None
            sleep(5.0 * (k + 1))
            continue
        if "error" in resp:
            if resp["error"].get("code") in BLOCK_UNAVAILABLE:
                return None
            if k == tries - 1:
                raise Refused(f"getBlockTime error for slot {slot}: code {resp['error'].get('code')}")
            sleep(5.0 * (k + 1))
            continue
        return None if resp.get("result") is None else int(resp["result"])
    return None


def measure(tape_dir: str | Path, fetch: Callable[[int], int | None], limiter: RateLimiter | None = None, last_hours: int | None = None, stride: int = STRIDE,
            min_slots: int = MIN_SLOTS) -> dict[str, Any]:
    d = check_tape_dir(tape_dir)
    files = tape_files(d, last_hours)
    if not files:
        raise Refused("no tape-<hour>.jsonl[.zst] files in the tape dir")
    first = first_receive_by_slot(files)
    sample, st = sample_slots(list(first), stride, min_slots)
    lim = limiter or RateLimiter()
    rows, missing = [], 0
    for slot in sample:
        lim.wait()
        bt = fetch(slot)
        if bt is None:
            missing += 1
            continue
        rows.append((slot, first[slot], int(bt)))
    if missing / len(sample) > MAX_MISSING:
        raise Refused(f"{missing} of {len(sample)} sampled slots had no block time (> {MAX_MISSING:.0%})")
    if len(rows) < min_slots:
        raise Refused(f"only {len(rows)} usable samples (< {min_slots})")
    sl = np.array([r[0] for r in rows], float)
    bt = np.array([r[2] for r in rows], float)
    delay_s = (np.array([r[1] for r in rows], float) - bt * 1000.0) / 1000.0
    sps = float(np.polyfit(sl, bt, 1)[0])
    if not sps_ok(sps):
        raise Refused(f"seconds per slot {sps:.4f} is outside the slot clock's range")
    med, p90 = float(np.median(delay_s)), float(np.percentile(delay_s, 90))
    return {"method": "delay = t_recv_ms - block_time*1000 per sampled slot (first receive of the slot); d = ceil(median / seconds_per_slot); timing only",
            "tapes": [f"{f}@{sha256_file(f)}" for f in files], "distinct_slots": len(first), "stride": st, "sampled": len(sample), "usable": len(rows), "missing": missing,
            "median_delay_s": med, "p90_delay_s": p90, "seconds_per_slot": sps, "feed_delay_slots": max(0, math.ceil(med / sps - 1e-9)),
            "note": "block_time has 1 s resolution: each delay carries up to +1 s of quantisation (the median is used)",
            "samples": [{"slot": int(r[0]), "t_recv_ms": int(r[1]), "block_time": int(r[2])} for r in rows]}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tape-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rpc-url", default=DEFAULT_RPC)
    ap.add_argument("--last-hours", type=int)
    ap.add_argument("--rps", type=float, default=MAX_RPS)
    a = ap.parse_args(argv)
    try:
        url = check_rpc_url(a.rpc_url)
        res = measure(a.tape_dir, lambda s: rpc_block_time(url, s), RateLimiter(a.rps), a.last_hours)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    res["rpc_host"] = urlsplit(url).hostname
    res["generated_ms"] = int(time.time() * 1000)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, sort_keys=True, allow_nan=False) + "\n")
    print(f"tapes {len(res['tapes'])}  distinct slots {res['distinct_slots']}  sampled {res['sampled']} (stride {res['stride']})  usable {res['usable']}  missing {res['missing']}")
    print(f"median delay {res['median_delay_s']:.3f} s  p90 {res['p90_delay_s']:.3f} s  seconds per slot {res['seconds_per_slot']:.4f}")
    print(f"feed_delay_slots d = ceil({res['median_delay_s']:.3f} / {res['seconds_per_slot']:.4f}) = {res['feed_delay_slots']}   -> StackConfig(feed_delay_slots={res['feed_delay_slots']})")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
