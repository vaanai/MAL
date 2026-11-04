"""gradscore historical backtest on the exploration blocks. PAPER ONLY. Three SEPARATE subcommands; scoring never sees outcomes.

  python -m tools.gradscore.hist cards  --block DIR --out OUT [--sol-usd 150] [--no-fetch]   -> OUT/cards-<block>.jsonl (+ .meta.json)
  python -m tools.gradscore.hist score  --cards FILE --out OUT --cap-usd N [--workers 4]     -> OUT/scores-<block>.jsonl (+ .meta.json)
  python -m tools.gradscore.hist labels --block DIR --out OUT                                -> OUT/labels-<block>.jsonl (+ .meta.json)

Outcomes are allowed ONLY on the four exploration blocks (explore-0814 w1..w7, oracle-insample-2026-09-22_25,
fast-pool-2026-09-18T23_2026-09-22T00, oracle-live-2026-09-25_27); any other path is refused.
`score` reads only the cards file. `cards` reads only trades with ts < first sight; `labels` only trades with ts >= first sight
(plus the pre-first-sight prints that fix WHICH pool is meant: the WSOL PumpSwap pool with the most SOL volume before first sight).
The join of scores with labels is tools/gradscore/auc.py (needs --allow-outcomes); this module never joins.

First sight = migration (graduation) time + 300 s. Migration time: the `complete` row of migrations/ (event_ts); for oracle-live the
`migrate` row of the observe file (t_ws). A migration is skipped (counted in the meta file) when the block's trades do not cover
[migration, first sight] (migration before the first trade hour, or first sight after the last trade hour end) or when no WSOL PumpSwap print
exists before first sight.

PRICE. Trade rows carry PRE-trade PumpSwap reserves (tools/arena_stack: "rows ... reserves PRE-trade"), and `quote_reserve` excludes the
virtual quote reserve V (arena Q = quote_reserve + V). The `price_sol` field of the rows ignores V, so it is NOT used. Spot SOL per token =
(quote_reserve + V) / base_reserve * 1e-3 (9 vs 6 decimals), V = the kit's pool map (/data/mal/pumpswap-virtual pool_v_0814.json then
pool_v_0909.json, first non-null; the same maps tools/arena_screen uses), else 17_584_505_288 lamports (flagged v_source=default).
The spot at time t is the pre-trade spot of the first print with ts >= t (= the pool state at t); when no print follows inside the block the
last print's pre-trade spot is carried and flagged. mcap = spot * 1e9 (pump supply). USD uses a CONSTANT --sol-usd (default 150; no historical
SOL price exists in the blocks; at 150 a fresh graduate shows ~$62k mcap, matching the rubric's 60-70k). Pool liquidity = 2 * real quote.
No X context exists historically: x_context is absent from every card.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from tools.gradscore import card as cd

FIRST_SIGHT_S = 300
HORIZONS = (3600, 4 * 3600, 24 * 3600)
SOL_USD_DEFAULT = 150.0
V_DEFAULT = 17_584_505_288
VMAP_PATHS = ("/data/mal/pumpswap-virtual/pool_v_0814.json", "/data/mal/pumpswap-virtual/pool_v_0909.json")
WSOL = "So11111111111111111111111111111111111111112"
ALLOWED_BLOCKS = ("/data/mal/clean-view/explore-0814", "/data/mal/clean-view/oracle-insample-2026-09-22_25",
                  "/data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00", "/data/mal/clean-view/oracle-live-2026-09-25_27")
# sha256 of tools/gradscore/prompt.py at da7a3d64 (frozen prompts, variants A and B)
FROZEN_PROMPT_PY_SHA256 = "db6ae0b6ddc733cbbff30613e78bd1f76028c8496fb4d0e13425f0580d474d12"
FORBIDDEN_PREFIX = ("fresh-", "exp011", "forward-", "truth-", "shadow", "canary", "probe", "tip-tape", "_shakedown", "_smoke")
FORBIDDEN_SEG = ("arena", "sealed", "meta-capture", "kalshi")


class Refused(Exception):
    pass


# ----------------------------------------------------------------------------- block layout

def check_block(path, allowed=None):
    allowed = ALLOWED_BLOCKS if allowed is None else allowed
    real = os.path.realpath(path)
    if not any(real == os.path.realpath(a) or real.startswith(os.path.realpath(a) + os.sep) for a in allowed):
        raise Refused(f"block not on the outcomes allow-list: {path}")
    for seg in real.lower().split(os.sep):
        if seg.startswith(FORBIDDEN_PREFIX) or seg in FORBIDDEN_SEG:
            raise Refused(f"forbidden path segment {seg!r}")
    return real


def block_name(block):
    return os.path.basename(os.path.realpath(block).rstrip("/"))


def windows_of(block):
    """[(window_name, dir)]: explore-0814 has w1..w7, the others are one window."""
    subs = sorted(d for d in os.listdir(block) if re.fullmatch(r"w\d+", d) and os.path.isdir(os.path.join(block, d)))
    return [(d, os.path.join(block, d)) for d in subs] if subs else [("all", block)]


def hour_of_name(fn):
    m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2})", fn)
    return int(datetime.strptime(m.group(1), "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp()) if m else None


def trade_hours(wdir):
    d = os.path.join(wdir, "trades")
    out = []
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        h = hour_of_name(fn)
        if h is not None and fn.startswith("trades-"):
            out.append((h, os.path.join(d, fn)))
    return sorted(out)


def read_lines(path):
    """Yield raw byte lines of a .jsonl or .jsonl.zst file."""
    if path.endswith(".zst"):
        p = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE)
        try:
            yield from p.stdout
        finally:
            p.stdout.close()
            p.wait()
    else:
        with open(path, "rb") as f:
            yield from f


def _iso_s(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def load_creates_and_migs(wdir):
    """-> (creates {mint: dict}, migs {mint: migration_ts}, n_dup_migrations). First migration per mint wins; first create per mint wins."""
    creates, migs, ndup = {}, {}, [0]

    def _mig(m, ts):
        if m in migs:
            ndup[0] += 1
        else:
            migs[m] = ts

    cdir, mdir = os.path.join(wdir, "creates"), os.path.join(wdir, "migrations")
    for fn in sorted(os.listdir(cdir)) if os.path.isdir(cdir) else []:
        for line in read_lines(os.path.join(cdir, fn)):
            if b'"create"' not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("txType") == "create" and r.get("stream") == "subscribeNewToken":  # oracle-live observe rows
                m = r.get("mint")
                if m and m not in creates:
                    creates[m] = {"name": r.get("name"), "symbol": r.get("symbol"), "uri": r.get("uri"),
                                  "creator": r.get("traderPublicKey"), "create_ts": _iso_s(r["t_ws"]) if r.get("t_ws") else None}
                if r.get("txType") == "migrate":
                    pass
            elif r.get("type") == "create":
                m = r.get("mint")
                if m and m not in creates:
                    creates[m] = {"name": r.get("name"), "symbol": r.get("symbol"), "uri": r.get("uri"),
                                  "creator": r.get("creator"), "create_ts": r.get("event_ts")}
    for fn in sorted(os.listdir(mdir)) if os.path.isdir(mdir) else []:
        for line in read_lines(os.path.join(mdir, fn)):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("type") == "complete" and r.get("mint") and r.get("event_ts") is not None:
                _mig(r["mint"], int(r["event_ts"]))
    if not migs:  # oracle-live: migrate rows live in the observe files
        for fn in sorted(os.listdir(cdir)) if os.path.isdir(cdir) else []:
            for line in read_lines(os.path.join(cdir, fn)):
                if b'"migrate"' not in line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("txType") == "migrate" and r.get("mint") and r.get("t_ws"):
                    _mig(r["mint"], int(_iso_s(r["t_ws"])))
    return creates, migs, ndup[0]


# ----------------------------------------------------------------------------- V and spot

class VMap:
    def __init__(self, maps=None):
        if maps is None:
            maps = []
            for p in VMAP_PATHS:
                try:
                    with open(p) as f:
                        maps.append(json.load(f)["v"])
                except (OSError, ValueError, KeyError):
                    continue
        self.maps = maps

    def get(self, pool):
        for m in self.maps:
            v = m.get(pool)
            if v is not None:
                return float(v), "map"
        return float(V_DEFAULT), "default"


def spot(q, b, v):
    """SOL per token from PRE-trade reserves: (real quote + V) / base * 1e-3. None if unusable."""
    if not b or b <= 0 or q is None or q < 0:
        return None
    return (q + v) / b * 1e-3


# ----------------------------------------------------------------------------- trade scan

_MINTS = frozenset()
_MINT_RE = re.compile(rb'"mint"\s*:\s*"([^"]{1,64})"')


def _init_worker(mints):
    global _MINTS
    _MINTS = mints


def scan_hour(path):
    """-> list of prints for tracked mints: (ts, slot, txi, ev, mint, pool, is_buy, sol, tok, q, b). WSOL PumpSwap rows only."""
    out = []
    mints = _MINTS
    for line in read_lines(path):
        if b'"pumpswap"' not in line:
            continue
        m = _MINT_RE.search(line)
        if m is None or m.group(1).decode() not in mints:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("venue") != "pumpswap" or r.get("quote_is_wsol") is not True or r.get("quote_mint") not in (None, WSOL):
            continue
        ts = r.get("block_time") if r.get("block_time") is not None else r.get("event_ts")
        if ts is None or r.get("pool") is None:
            continue
        out.append((int(ts), r.get("slot") or 0, r.get("tx_index") or 0, r.get("event_index") or 0, r["mint"], r["pool"],
                    r.get("side") == "buy", r.get("sol_lamports") or 0, r.get("token_raw") or 0, r.get("quote_reserve"), r.get("base_reserve")))
    out.sort(key=lambda p: p[:4])
    return out


def iter_prints(hours, mints, workers=2, scan=scan_hour):
    """Hour-ordered stream of prints (each hour sorted). workers>1 uses processes (spawn-safe: initializer sets the mint set)."""
    paths = [p for _, p in hours]
    if workers <= 1 or len(paths) <= 1:
        _init_worker(frozenset(mints))
        for p in paths:
            yield from scan(p)
        return
    import multiprocessing as mp
    ctx = mp.get_context("fork")
    with ctx.Pool(workers, initializer=_init_worker, initargs=(frozenset(mints),)) as pool:
        for res in pool.imap(scan, paths):
            yield from res


# ----------------------------------------------------------------------------- engine (shared by cards and labels)

class MintState:
    __slots__ = ("mig", "t0", "pre", "chosen", "closed", "prev", "p0", "p0_ts", "mx", "hz", "n_post", "last_ts")

    def __init__(self, mig):
        self.mig, self.t0 = mig, mig + FIRST_SIGHT_S
        self.pre = {}           # pool -> [vol_lamports, buys, sells, last_pre_q, last_pre_b, last_ts, last_print]
        self.chosen = None
        self.closed = False     # first sight passed: the pre list is final
        self.prev = None        # last chosen-pool print awaiting its exact post-trade price: (ts, derived_post_price)
        self.p0 = self.p0_ts = self.mx = self.last_ts = None
        self.hz = {}            # horizon -> (price, ts of the last trade at or before t0+h)
        self.n_post = 0


def choose_pool(pre):
    """Most SOL volume at or before first sight; ties by pool address (deterministic)."""
    if not pre:
        return None
    return sorted(pre.items(), key=lambda kv: (-kv[1][0], kv[0]))[0][0]


def post_price(q, b, sol, tok, is_buy, v):
    """Derived post-trade spot of one print (arena_stack.mint_states convention); used only for the last print of a pool."""
    qp = (q + sol * 0.99 if is_buy else q - sol * 1.01)
    bp = (b - tok if is_buy else b + tok)
    return spot(qp, bp if bp > 0 else b, v)


def _resolve(s, price):
    """The pending print's post-trade price is now known (= the next print's pre-trade spot, or derived at the end)."""
    if s.prev is None or price is None or price <= 0:
        return
    ts = s.prev[0]
    if ts <= s.t0:
        s.p0, s.p0_ts = price, ts            # entry: last trade price at or before t0
        for h in HORIZONS:
            s.hz[h] = (price, ts)
    else:
        s.n_post += 1
        for h in HORIZONS:
            if ts <= s.t0 + h:
                s.hz[h] = (price, ts)
        if ts <= s.t0 + HORIZONS[-1]:
            s.mx = price if s.mx is None else max(s.mx, price)  # strictly after t0 only
        s.last_ts = ts


def _close(s, vmap):
    s.closed, s.chosen = True, choose_pool(s.pre)
    if s.chosen is not None:
        ts, q, b, sol, tok, buy = s.pre[s.chosen][6]
        s.prev = (ts, post_price(q, b, sol, tok, buy, vmap.get(s.chosen)[0]))


def run_engine(prints, migs, mode, vmap, block_end):
    """Prints must be time-ordered. Pre-sight = ts <= t0, post = ts > t0.
    mode 'cards': uses ONLY pre-sight prints. mode 'labels': WHICH pool is chosen from pre-sight prints; then the chosen pool's prints:
    entry p0 = price of the last trade at or before t0; the 24h max uses only trades strictly after t0; the price at +h is the last trade at or
    before t0+h (with its ts). A trade's price = the pool's post-trade spot = the next print's pre-trade spot (derived for the pool's last print)."""
    st = {m: MintState(t) for m, t in migs.items()}
    for ts, _sl, _tx, _ev, mint, pool, is_buy, sol, tok, q, b in prints:
        s = st.get(mint)
        if s is None or ts < s.mig:
            continue
        if ts <= s.t0:
            a = s.pre.setdefault(pool, [0, 0, 0, None, None, None, None])
            a[0] += sol
            a[1] += 1 if is_buy else 0
            a[2] += 0 if is_buy else 1
            a[3], a[4], a[5], a[6] = q, b, ts, (ts, q, b, sol, tok, is_buy)
            continue
        if mode == "cards":
            continue
        if not s.closed:
            _close(s, vmap)
        if s.chosen is None or pool != s.chosen:
            continue
        v, _src = vmap.get(pool)
        p = spot(q, b, v)
        if p is None or p <= 0:
            continue
        _resolve(s, p)
        s.prev = (ts, post_price(q, b, sol, tok, is_buy, v))
    for s in st.values():
        if not s.closed:
            _close(s, vmap)
        if mode != "cards" and s.prev is not None:
            _resolve(s, s.prev[1])
    return st


def usable(mig, window_start, window_end):
    if mig < window_start:
        return "no_trade_coverage_start"
    if mig + FIRST_SIGHT_S > window_end:
        return "no_trade_coverage_end"
    return None


def gather(window_dir):
    hours = trade_hours(window_dir)
    if not hours:
        raise Refused(f"no trade hours in {window_dir}")
    return hours, hours[0][0], hours[-1][0] + 3600


# ----------------------------------------------------------------------------- IPFS metadata

class MetaFetcher:
    """uri -> metadata dict | None. Disk cache (positive and negative), rate limit on remote hosts, short timeouts."""

    def __init__(self, cache_dir, kubo="http://127.0.0.1:8080/ipfs/", gateways=("https://ipfs.io/ipfs/",), timeout_s=4.0, min_interval_s=0.25,
                 opener=None, sleep=time.sleep, clock=time.monotonic):
        self.dir, self.kubo, self.gateways, self.timeout = cache_dir, kubo, gateways, timeout_s
        self.min_interval, self.opener, self.sleep, self.clock = min_interval_s, opener or self._open, sleep, clock
        self._last = 0.0
        self.stats = {"cached": 0, "fetched": 0, "failed": 0, "kubo_ok": 0}
        os.makedirs(cache_dir, exist_ok=True)

    @staticmethod
    def _open(url, timeout):
        req = urllib.request.Request(url, headers={"User-Agent": "mal-gradscore-hist/0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return r.read(200_000).decode("utf-8", "replace")

    def _path(self, uri):
        return os.path.join(self.dir, hashlib.sha256(uri.encode()).hexdigest() + ".json")

    def urls(self, uri):
        m = re.search(r"/ipfs/([A-Za-z0-9]+)", uri) or re.match(r"ipfs://([A-Za-z0-9]+)", uri)
        out = []
        if m:
            cid = m.group(1)
            rest = ""
            out.append(self.kubo.rstrip("/") + "/" + cid + rest)
            out += [g.rstrip("/") + "/" + cid + rest for g in self.gateways]
        if uri.startswith(("http://", "https://")) and uri not in out:
            out.append(uri)
        return out

    def get(self, uri):
        if not isinstance(uri, str) or not uri.strip():
            return None
        p = self._path(uri)
        if os.path.exists(p):
            try:
                with open(p) as f:
                    self.stats["cached"] += 1
                    return json.load(f).get("meta")
            except (OSError, ValueError):
                pass
        meta = None
        for i, url in enumerate(self.urls(uri)):
            remote = not url.startswith("http://127.0.0.1")
            if remote:
                wait = self.min_interval - (self.clock() - self._last)
                if wait > 0:
                    self.sleep(wait)
                self._last = self.clock()
            try:
                doc = json.loads(self.opener(url, self.timeout))
            except Exception:
                continue
            if isinstance(doc, dict):
                meta = doc
                self.stats["kubo_ok"] += 0 if remote else 1
                break
        self.stats["fetched" if meta else "failed"] += 1
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"uri": uri, "meta": meta}, f)
        os.replace(tmp, p)
        return meta


# ----------------------------------------------------------------------------- cards

def make_card(mint, st, create, meta, sol_usd, vmap):
    """Card from pre-first-sight state only. Returns (card, extra) or (None, reason)."""
    if st.chosen is None:
        return None, "no_pool_at_first_sight"
    vol, buys, sells, q, b = st.pre[st.chosen][:5]
    v, vsrc = vmap.get(st.chosen)
    sp = spot(q, b, v)
    if sp is None or sp <= 0:
        return None, "unusable_pool_state"
    disc = {"age_min": FIRST_SIGHT_S / 60.0, "buys_5m": buys, "sells_5m": sells, "vol_5m": vol / 1e9 * sol_usd,
            "liq_usd": 2 * q / 1e9 * sol_usd, "mcap_usd": sp * 1e9 * sol_usd,
            "name": (create or {}).get("name"), "symbol": (create or {}).get("symbol")}
    card = cd.build_card(disc, {}, {}, meta, None)
    return card, {"pool": st.chosen, "v_source": vsrc}


def cmd_cards(block, out, sol_usd=SOL_USD_DEFAULT, fetch=True, workers=2, fetcher=None, vmap=None):
    block = check_block(block)
    name = block_name(block)
    os.makedirs(out, exist_ok=True)
    vmap = vmap or VMap()
    fetcher = fetcher or (MetaFetcher(os.path.join(out, "meta-cache")) if fetch else None)
    path = os.path.join(out, f"cards-{name}.jsonl")
    counts = {"graduations": 0, "cards": 0, "with_uri": 0, "with_metadata": 0, "with_create": 0}
    skips = {}
    seen = set()
    counts["duplicate_migrations"] = 0
    with open(path, "w") as fo:
        for wname, wdir in windows_of(block):
            creates, migs, ndup = load_creates_and_migs(wdir)
            counts["duplicate_migrations"] = counts.get("duplicate_migrations", 0) + ndup
            hours, w0, w1 = gather(wdir)
            counts["graduations"] += len(migs)
            use = {}
            for m, t in migs.items():
                if m in seen:  # one row per mint: first migration wins across windows too
                    counts["duplicate_migrations"] += 1
                    continue
                seen.add(m)
                why = usable(t, w0, w1)
                if why:
                    skips[why] = skips.get(why, 0) + 1
                else:
                    use[m] = t
            last_t0 = max((t + FIRST_SIGHT_S for t in use.values()), default=0)
            hs = [(h, p) for h, p in hours if h <= last_t0 and h + 3600 > min(use.values(), default=0)]
            st = run_engine(iter_prints(hs, use, workers), use, "cards", vmap, w1) if use else {}
            for mint in sorted(use, key=lambda m: (use[m], m)):
                cr = creates.get(mint)
                meta = fetcher.get(cr.get("uri")) if (fetcher and cr and cr.get("uri")) else None
                card, extra = make_card(mint, st[mint], cr, meta, sol_usd, vmap)
                if card is None:
                    skips[extra] = skips.get(extra, 0) + 1
                    continue
                row = {"mint": mint, "window": wname, "migration_ts": use[mint], "first_sight_ts": use[mint] + FIRST_SIGHT_S,
                       "card_text": cd.card_text(card), "card_sha256": cd.card_sha256(card), "has_create": cr is not None,
                       "has_uri": bool(cr and cr.get("uri")), "has_metadata": meta is not None, "has_x_context": False,
                       "creator": (cr or {}).get("creator"), "create_ts": (cr or {}).get("create_ts"), "uri": (cr or {}).get("uri"),
                       "sol_usd_assumed": sol_usd, **extra}
                fo.write(json.dumps(row, sort_keys=True) + "\n")
                counts["cards"] += 1
                counts["with_uri"] += row["has_uri"]
                counts["with_metadata"] += row["has_metadata"]
                counts["with_create"] += row["has_create"]
    meta_out = {"block": name, "counts": counts, "skips": skips, "sol_usd_assumed": sol_usd, "x_context": "absent (none exists historically)",
                "fetch": fetcher.stats if fetcher else None}
    _write_json(path[:-6] + ".meta.json", meta_out)
    return meta_out


# ----------------------------------------------------------------------------- labels

def cmd_labels(block, out, workers=2, vmap=None):
    block = check_block(block)
    name = block_name(block)
    os.makedirs(out, exist_ok=True)
    vmap = vmap or VMap()
    path = os.path.join(out, f"labels-{name}.jsonl")
    counts = {"graduations": 0, "labels": 0}
    skips = {}
    seen = set()
    counts["duplicate_migrations"] = 0
    with open(path, "w") as fo:
        for wname, wdir in windows_of(block):
            _creates, migs, ndup = load_creates_and_migs(wdir)
            counts["duplicate_migrations"] = counts.get("duplicate_migrations", 0) + ndup
            hours, w0, w1 = gather(wdir)
            counts["graduations"] += len(migs)
            use = {}
            for m, t in migs.items():
                if m in seen:  # one row per mint: first migration wins across windows too
                    counts["duplicate_migrations"] += 1
                    continue
                seen.add(m)
                why = usable(t, w0, w1)
                if why:
                    skips[why] = skips.get(why, 0) + 1
                else:
                    use[m] = t
            hs = [(h, p) for h, p in hours if h + 3600 > min(use.values(), default=0)]
            st = run_engine(iter_prints(hs, use, workers), use, "labels", vmap, w1) if use else {}
            for mint in sorted(use, key=lambda m: (use[m], m)):
                row = label_row(mint, wname, st[mint], w1)
                if row is None:
                    skips["no_pool_at_first_sight"] = skips.get("no_pool_at_first_sight", 0) + 1
                    continue
                fo.write(json.dumps(row, sort_keys=True) + "\n")
                counts["labels"] += 1
                counts["no_data"] = counts.get("no_data", 0) + bool(row["no_data"])
                for tag in ("1h", "4h", "24h"):
                    counts[f"censored_{tag}"] = counts.get(f"censored_{tag}", 0) + bool(row[f"censored_{tag}"])
    meta_out = {"block": name, "counts": counts, "skips": skips,
                "price": "pre-trade spot (quote_reserve + V) / base_reserve * 1e-3; V from the kit's pool map else 17_584_505_288"}
    _write_json(path[:-6] + ".meta.json", meta_out)
    return meta_out


def label_row(mint, wname, s, block_end):
    """One label row (QP-RULING-1104-gradscore items 1, 2, 7). max_mult_24h >= 3 is the primary label; ret_4h is report-only."""
    if s.chosen is None or s.p0 is None:
        return None
    no_data = s.n_post == 0  # no trade after t0 at all
    row = {"mint": mint, "window": wname, "migration_ts": s.mig, "first_sight_ts": s.t0, "pool": s.chosen, "quote_mint": WSOL,
           "block_end_ts": block_end, "p0": s.p0, "p0_ts": s.p0_ts, "n_trades_after": s.n_post, "no_data": no_data}
    for h, tag in zip(HORIZONS, ("1h", "4h", "24h")):
        row[f"censored_{tag}"] = block_end < s.t0 + h
        pr, pts = s.hz.get(h, (None, None))
        row[f"price_{tag}"] = None if no_data else pr
        row[f"price_ts_{tag}"] = None if no_data else pts
        row[f"mult_{tag}"] = None if no_data or pr is None else pr / s.p0
    row["max_mult_24h"] = None if no_data or s.mx is None else s.mx / s.p0
    row["ret_4h"] = None if no_data or row["price_4h"] is None else row["price_4h"] / s.p0 - 1.0
    return row


# ----------------------------------------------------------------------------- score

def _check_frozen():
    """Refuse to score unless prompt.py is byte-identical to the one frozen at da7a3d64."""
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompt.py")
    with open(here, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    if FROZEN_PROMPT_PY_SHA256 and sha != FROZEN_PROMPT_PY_SHA256:
        raise Refused(f"prompt.py sha256 {sha} is not the frozen da7a3d64 file")
    return sha


def read_done(path):
    done = set()
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("action") == "SCORED" or str(r.get("skip_reason", "")).startswith("parse_"):
                    done.add((r["mint"], r["variant"]))
    return done


def cmd_score(cards, out, cap_usd, workers=4, caller=None, variants=("A", "B")):
    """Reads ONLY the cards file. Never opens a labels file."""
    from tools.gradscore import prompt as pr, score as sc
    name = os.path.basename(cards)
    name = name[len("cards-"):-len(".jsonl")] if name.startswith("cards-") and name.endswith(".jsonl") else name
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, f"scores-{name}.jsonl")
    prompt_sha = _check_frozen()
    if caller is None:
        from tools.haiku_caller import HaikuCaller
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise Refused("ANTHROPIC_API_KEY not set")
        caller = HaikuCaller(os.path.join(out, "haiku", f"hist-ledger-{name}.jsonl"), float(cap_usd), model=sc.MODEL)
    done = read_done(path)
    work = []
    with open(cards) as f:
        for line in f:
            c = json.loads(line)
            for v in variants:
                if (c["mint"], v) not in done:
                    work.append((c, v))
    stats = {"todo": len(work), "scored": 0, "skipped": 0, "cost_usd": 0.0, "cap_hit": False}
    stop = {"cap": False}

    def one(item):
        c, v = item
        if stop["cap"]:
            return None
        r = sc.score_one(caller, v, c["card_text"])
        if r.pop("cap"):
            stop["cap"] = True
            return None
        return dict(r, mint=c["mint"], window=c.get("window"), card_sha256=c["card_sha256"], first_sight_ts=c.get("first_sight_ts"))

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for row in ex.map(one, work):
            if row is None:
                continue
            if str(row.get("skip_reason", "")).startswith("caller_"):  # transport error: not written, retried on resume
                stats["skipped"] += 1
                continue
            sc.append_jsonl(path, row)
            stats["cost_usd"] += row.get("cost_usd") or 0.0
            stats["scored" if row["action"] == "SCORED" else "skipped"] += 1
    stats["cap_hit"] = stop["cap"]
    meta = {"cards": os.path.basename(cards), "prompt_py_sha256": prompt_sha, "prefix_sha256": {v: pr.prefix_sha256(v) for v in variants},
            "model": sc.MODEL, "cap_usd": cap_usd, "this_run": stats}
    _write_json(os.path.join(out, f"scores-{name}.meta.json"), meta)
    return meta


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("cards")
    a.add_argument("--block", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--sol-usd", type=float, default=SOL_USD_DEFAULT)
    a.add_argument("--no-fetch", action="store_true")
    a.add_argument("--workers", type=int, default=2)
    b = sub.add_parser("score")
    b.add_argument("--cards", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--cap-usd", type=float, required=True)
    b.add_argument("--workers", type=int, default=4)
    c = sub.add_parser("labels")
    c.add_argument("--block", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--workers", type=int, default=2)
    a_ = ap.parse_args(argv)
    try:
        if a_.cmd == "cards":
            res = cmd_cards(a_.block, a_.out, a_.sol_usd, not a_.no_fetch, a_.workers)
        elif a_.cmd == "score":
            res = cmd_score(a_.cards, a_.out, a_.cap_usd, a_.workers)
        else:
            res = cmd_labels(a_.block, a_.out, a_.workers)
    except Refused as e:
        print("REFUSED:", e, file=sys.stderr)
        return 2
    if a_.cmd == "labels":
        res = {k: v for k, v in res.items() if k != "price"}
    print(json.dumps(res, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
