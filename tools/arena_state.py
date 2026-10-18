#!/usr/bin/env python3
"""Per-pool / per-curve state from the arena tape (tools/arena_feed.py). Paper only. Pure python, no network, no keys.

    st = ArenaState("/data/mal/arena/tape")          # live: warm up on the last 125 min of tape, then tail
    st = ArenaState(tape_dir, start_ms=0)            # replay the whole dir (tests, backtests)
    st.poll()                                        # process new complete lines; returns the number of rows applied
    st.poll(until_ms=t)                              # replay: stop before the first row with t_recv_ms > t (step the clock)
    st.pool(id) / st.pools(pred) / st.snapshot(id)   # id = pool address (pumpswap) or mint (pumpfun)
    st.on_trade = lambda pool, row: ...              # called after each trade is applied (st.add_on_trade for more)

Files: the open hour is a `tape-<hour>.jsonl` followed by byte offset; a closed hour is a `tape-<hour>.jsonl.zst` (tools/arena_feed compacts it,
byte-exact), streamed through the zstd CLI. When a .jsonl the reader was following is compacted, it continues in the .zst at the same decompressed
offset, so every row is applied exactly once. While both files of an hour exist the .jsonl is used. Needs the zstd CLI only for .zst hours.

The state clock is `now_ms`: the largest t_recv_ms applied. Nothing reads the wall clock except the live warm-up default, so a replay
is deterministic. Reserve conventions (tools/arena_tape_schema.md): PumpSwap rows carry PRE-trade reserves with V, so the post-trade
spot is derived here (the same algebra as tools.h5_shadow.post_state); bonding rows carry POST-trade reserves, used as they are.

GAPS. Every gap row becomes a span [from_ms, to_ms] (an open silence / feed-restart record and its closed record are one span). `gap_spans(t0, t1,
prog)` / `has_gap(t0, t1, prog)` answer "does [decision, exit] hold a gap?" for the last 24 h of state-clock time (feed gaps of that program, process
restarts, disk-reserve pauses). A snapshot's `gaps_since_first_seen` counts data-loss spans since the key was first seen, from counters that are never
pruned. What the state cannot tell: a window just BEFORE a decision that holds a gap is only visible through `gap_spans`; no field of a key says so.

Per key it keeps: last post-trade spot price (SOL/token, V where it applies) and reserves; age since create / pool_create and curve
progress; rolling 1 / 5 / 15 / 60 min buy and sell SOL and counts, unique signers and net flow; the top 5 net buyers and sellers (since
the key was first seen); the creator's net SOL; ATH and drawdown from ATH; 1-min OHLCV candles for the last 120 min. Keys idle more than
6 h are evicted. Gap rows are kept, and a key's snapshot counts the gaps on its program's feed since the key was first seen.
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python tools/arena_state.py`
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import collections
import heapq
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from tools.arena_zst import CompactError, open_zst
from tools.paper_curve_math import curve_progress

LAMPORTS = 1_000_000_000
TOKEN_SCALE = 1_000_000
WSOL_MINT = "So11111111111111111111111111111111111111112"
MIN_MS = 60_000
WINDOWS_MIN = (1, 5, 15, 60)
CANDLE_MIN = 120
EVICT_IDLE_MS = 6 * 3600 * 1000
SWEEP_EVERY_MS = 60_000
WARM_MIN_DEFAULT = 125  # live default: enough tape for the 120-min candles
READ_CHUNK = 8 * 1024 * 1024
GAP_KEEP_MS = 24 * 3600 * 1000  # gap SPANS are kept this long (by the state clock); the per-prog COUNTS are never pruned
GAP_EXTRA = ("closed", "from_slot", "to_slot", "missed_slots", "silent_ms", "reason", "count", "clean_stop", "torn_tail", "rows_dropped", "free_pct")
TOP_N = 5


def _spot(q_eff: float, base: float) -> float | None:
    """SOL per token from an effective quote reserve (lamports, V included) and a base reserve (raw units)."""
    if q_eff is None or base is None or q_eff <= 0 or base <= 0:
        return None
    return (q_eff / LAMPORTS) / (base / TOKEN_SCALE)


def pumpswap_post(q_eff_pre: float, b_pre: float, buy: bool, sol: int, tok: int, lp_fee: Any, pool_quote_amount: Any) -> tuple[float, float]:
    """(Q incl. V, base) AFTER a PumpSwap print from its PRE-trade state. With the event's own fields a buy adds pool_quote_amount + lp_fee and a
    sell removes pool_quote_amount - lp_fee (protocol / creator fees leave Q unchanged: stored V falls by what the vault keeps). Without them,
    constant product on the base move. Base moves by exactly token_raw."""
    db = float(-tok if buy else tok)
    b_post = b_pre + db
    if pool_quote_amount:
        lp = lp_fee or 0
        dq = float(pool_quote_amount + lp if buy else -(pool_quote_amount - lp))
    elif b_post > 0:
        dq = q_eff_pre * b_pre / b_post - q_eff_pre
    else:
        dq = 0.0
    return q_eff_pre + dq, b_post


class _Hour:
    """Read position in one hour: bytes taken from its .jsonl (the same offset in the decompressed .jsonl.zst), the zst generator, done flag."""

    __slots__ = ("off", "gen", "done")

    def __init__(self) -> None:
        self.off, self.gen, self.done = 0, None, False


class Pool:
    """State of one pool (prog pumpswap, id = pool address) or one bonding curve (prog pumpfun, id = mint)."""

    __slots__ = ("id", "prog", "mint", "pool", "created_ms", "create_slot", "created_kind", "first_seen_ms", "last_ms", "last_slot", "n_trades", "creator",
                 "q_real", "base", "v", "price", "ath", "ath_ms", "last_trade", "events", "net", "creator_in", "creator_out", "candles", "complete_ms",
                 "name", "symbol", "gaps_at_first_seen", "origin_create_ms", "quote_mint")

    def __init__(self, key: str, prog: str, t_ms: int, gaps_at_first_seen: int) -> None:
        self.id, self.prog = key, prog
        self.mint: str | None = key if prog == "pumpfun" else None
        self.pool: str | None = key if prog == "pumpswap" else None
        self.created_ms: int | None = None
        self.create_slot: int | None = None
        self.created_kind: str | None = None  # "create" | "pool_create" | None (joined mid-life)
        self.first_seen_ms, self.last_ms, self.last_slot = t_ms, t_ms, None
        self.n_trades = 0
        self.creator: str | None = None
        self.q_real: int | None = None  # real quote reserve, post-trade (pumpswap derived, bonding virtual SOL from the tape)
        self.base: float | None = None  # base reserve, post-trade
        self.v = 0  # PumpSwap stored V (lamports) from the latest print; 0 on bonding
        self.price: float | None = None
        self.ath: float | None = None
        self.ath_ms: int | None = None
        self.last_trade: dict[str, Any] | None = None
        self.events: collections.deque = collections.deque()  # (t_ms, sol_lamports, is_buy, trader)
        self.net: dict[str, int] = {}  # trader -> net lamports bought (buys +, sells -)
        self.creator_in = 0  # lamports the creator spent buying here
        self.creator_out = 0  # lamports the creator received selling here
        self.candles: collections.OrderedDict[int, list] = collections.OrderedDict()  # minute -> [o, h, l, c, vol_lam, n, buy_lam, sell_lam]
        self.complete_ms: int | None = None
        self.name: str | None = None
        self.symbol: str | None = None
        self.gaps_at_first_seen = gaps_at_first_seen
        self.origin_create_ms: int | None = None  # a pumpswap pool: when its mint's bonding curve was created, if seen
        self.quote_mint: str | None = WSOL_MINT if prog == "pumpfun" else None  # None = unverified (units of a non-WSOL pool are NOT SOL)


class ArenaState:
    def __init__(self, tape_dir: str | Path, start_ms: int | None = None, *, warm_min: int = WARM_MIN_DEFAULT,
                 on_trade: Callable[[Pool, dict], None] | None = None) -> None:
        self.dir = Path(tape_dir)
        self.start_ms = int(start_ms) if start_ms is not None else int(time.time() * 1000) - warm_min * MIN_MS
        self.on_trade = on_trade
        self._extra_cbs: list[Callable[[Pool, dict], None]] = []
        self.now_ms = 0
        self._pools: dict[str, Pool] = {}
        self._by_mint: dict[str, set[str]] = {}
        self._hours: dict[str, _Hour] = {}  # hour key -> read position
        self._buf: collections.deque = collections.deque()
        self._last_sweep = 0
        self._spans: dict[tuple, dict] = {}  # (prog, kind, from_ms) -> span; an open and its closed record (same from_ms) are ONE span
        self.gaps_total = collections.Counter()  # prog -> distinct gap spans ever seen (any data_loss); never pruned
        self.loss_spans = collections.Counter()  # prog -> distinct data_loss spans ever seen; never pruned
        self.last_hb: dict[str, dict] = {}
        self.counters: collections.Counter = collections.Counter()

    # ---- hooks ---------------------------------------------------------------------------------------------
    def add_on_trade(self, fn: Callable[[Pool, dict], None]) -> None:
        self._extra_cbs.append(fn)

    # ---- file following ------------------------------------------------------------------------------------
    @staticmethod
    def _hour_start_ms(key: str) -> int | None:
        try:
            return int(datetime.strptime(key, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            return None

    def _hour_files(self) -> list[tuple[str, Path | None, Path | None]]:
        """(hour key, .jsonl or None, .jsonl.zst or None) for every hour that can hold rows at or after start_ms, oldest first."""
        found: dict[str, list[Path | None]] = {}
        for p in self.dir.glob("tape-*.jsonl"):
            found.setdefault(p.name[len("tape-"):-len(".jsonl")], [None, None])[0] = p
        for p in self.dir.glob("tape-*.jsonl.zst"):
            found.setdefault(p.name[len("tape-"):-len(".jsonl.zst")], [None, None])[1] = p
        out = []
        for key in sorted(found):
            h = self._hour_start_ms(key)
            if h is not None and h + 3_600_000 > self.start_ms:
                out.append((key, found[key][0], found[key][1]))
        return out

    def _ingest(self, data: bytes) -> None:
        for ln in data.split(b"\n"):
            if not ln.strip():
                continue
            try:
                row = json.loads(ln)
            except ValueError:
                self.counters["bad_lines"] += 1
                continue
            if isinstance(row, dict) and isinstance(row.get("t_recv_ms"), int):
                self._buf.append(row)
            else:
                self.counters["bad_lines"] += 1

    @staticmethod
    def _zst_chunks(path: Path, skip: int) -> Iterator[bytes]:
        """Complete lines of a .jsonl.zst in chunks of about READ_CHUNK bytes, after skipping the first `skip` decompressed bytes (the bytes a reader
        already took from the .jsonl before it was compacted; compaction is byte-exact, so the offset carries over)."""
        r = open_zst(path)
        try:
            left = skip
            while left > 0:
                b = r.read(min(left, 1 << 20))
                if not b:
                    break
                left -= len(b)
            carry = b""
            while True:
                b = r.read(READ_CHUNK)
                if not b:
                    break
                data = carry + b
                cut = data.rfind(b"\n")
                if cut < 0:
                    carry = data
                    continue
                carry = data[cut + 1:]
                yield data[:cut]
            if carry.strip():
                yield carry
        finally:
            r.close()

    def _fill(self) -> None:
        """Read the next chunk of complete lines: a live .jsonl by byte offset, a compacted .jsonl.zst by streaming decompress. Oldest hour first; the
        newest two hours are re-checked every poll (rollover, late rows). While both files of an hour exist (compaction verifying) the .jsonl is used."""
        hours = self._hour_files()
        for idx, (key, j, z) in enumerate(hours):
            st = self._hours.setdefault(key, _Hour())
            if st.done:
                continue
            if j is not None and st.gen is None:
                try:
                    size = j.stat().st_size
                    data = b""
                    if size > st.off:  # the open and the read are inside the try too: a compaction can delete the file at any point after the listing
                        with j.open("rb") as fh:
                            fh.seek(st.off)
                            data = fh.read(min(size - st.off, READ_CHUNK))
                except OSError:
                    j = None  # compacted (deleted) between the listing and now: continue in the .zst at the same offset
                else:
                    if size > st.off:
                        cut = data.rfind(b"\n")
                        if cut >= 0:  # a partial last line is left for the next poll
                            st.off += cut + 1
                            self._ingest(data[:cut])
                        if self._buf:
                            return  # apply this chunk before reading further
                    elif idx < len(hours) - 2:
                        st.done = True  # fully read and not one of the two newest hours: finished
                    continue
            if z is not None:  # the .jsonl is gone (closed hour, compacted) or never existed
                try:
                    if st.gen is None:
                        st.gen = self._zst_chunks(z, st.off)
                    data = next(st.gen, None)
                except (CompactError, OSError):
                    self.counters["zst_errors"] += 1
                    st.gen, st.done = None, True
                    continue
                if data is None:
                    st.gen, st.done = None, True
                    continue
                self._ingest(data)
                if self._buf:
                    return

    def poll(self, max_rows: int | None = None, until_ms: int | None = None) -> int:
        """Apply new rows; returns how many were applied. until_ms: stop before the first row later than it (that row stays queued)."""
        n = 0
        while max_rows is None or n < max_rows:
            if not self._buf:
                self._fill()
                if not self._buf:
                    break
            row = self._buf[0]
            t = row["t_recv_ms"]
            if until_ms is not None and t > until_ms:
                break
            self._buf.popleft()
            if t < self.start_ms:
                self.counters["rows_before_start"] += 1
                continue
            try:
                self.apply(row)
            except Exception:  # noqa: BLE001 - one malformed row must not abort the poll; it is counted
                self.counters["apply_errors"] += 1
                continue
            n += 1
        return n

    # ---- applying rows -------------------------------------------------------------------------------------
    def apply(self, row: dict) -> None:
        t = int(row["t_recv_ms"])
        if t > self.now_ms:
            self.now_ms = t
        typ = row.get("type")
        if typ == "trade":
            self._trade(row, t)
        elif typ == "create":
            self._create(row, t)
        elif typ == "complete":
            p = self._get(row.get("mint"), "pumpfun", t, make=True)
            if p is None:
                self.counters["rows_without_key"] += 1
            else:
                p.complete_ms = t
        elif typ == "pool_create":
            self._pool_create(row, t)
        elif typ == "pool_info":  # resolver row: mint / quote mint of a pool whose CreatePool the feed did not see
            p = self._get(row.get("pool"), "pumpswap", t, make=True)
            if p is None:
                self.counters["rows_without_key"] += 1
            else:
                p.quote_mint = row.get("quote_mint") or p.quote_mint
                self._link_mint(p, row.get("mint"))
        elif typ == "gap":
            self._gap(row, t)
        elif typ == "hb":
            self.last_hb[str(row.get("prog"))] = row
        self.counters["rows"] += 1
        if t - self._last_sweep >= SWEEP_EVERY_MS:
            self._last_sweep = t
            self.sweep()

    def _gap(self, row: dict, t: int) -> None:
        prog, kind = str(row.get("prog") or "all"), row.get("kind")
        fr = next((row[k] for k in ("from_ms", "paused_since_ms") if isinstance(row.get(k), int)), t)
        to = row["to_ms"] if isinstance(row.get("to_ms"), int) else t
        loss = bool(row.get("data_loss"))
        sp = self._spans.get((prog, kind, fr))
        if sp is None:
            self._spans[(prog, kind, fr)] = {"prog": prog, "kind": kind, "from_ms": fr, "to_ms": max(fr, to), "data_loss": loss, **{k: row[k] for k in GAP_EXTRA if k in row}}
            self.gaps_total[prog] += 1
            self.loss_spans[prog] += loss
            return
        sp["to_ms"] = max(sp["to_ms"], to)
        sp.update({k: row[k] for k in GAP_EXTRA if k in row})
        if loss and not sp["data_loss"]:
            sp["data_loss"] = True
            self.loss_spans[prog] += 1

    def _gaps_before(self, prog: str) -> int:
        """Data-loss gap spans seen so far on `prog`'s feed (and on 'all'): the count a key stores when it is first seen. Never pruned."""
        return self.loss_spans[prog] + (self.loss_spans["all"] if prog != "all" else 0)

    def gap_spans(self, t0: int | None = None, t1: int | None = None, prog: str | None = None, data_loss_only: bool = True) -> list[dict]:
        """Gap spans [from_ms, to_ms] (t_recv_ms, inclusive) that overlap [t0, t1], oldest first; spans of `prog` and of 'all' (process restarts, the
        disk reserve). Spans are kept for GAP_KEEP_MS of state-clock time; older ones are gone (the counts are not)."""
        out = [dict(s) for s in self._spans.values()
               if (not data_loss_only or s["data_loss"]) and (prog is None or s["prog"] in (prog, "all"))
               and (t0 is None or s["to_ms"] >= t0) and (t1 is None or s["from_ms"] <= t1)]
        return sorted(out, key=lambda s: (s["from_ms"], s["to_ms"], s["kind"]))

    def has_gap(self, t0: int, t1: int, prog: str | None = None) -> bool:
        """True when a data-loss gap on `prog`'s feed (or a process restart / disk reserve) overlaps [t0, t1]: a decision window that holds one is not clean."""
        return bool(self.gap_spans(t0, t1, prog))

    def _get(self, key: str | None, prog: str, t: int, *, make: bool) -> Pool | None:
        if not key:
            return None
        p = self._pools.get(key)
        if p is None and make:
            p = self._pools[key] = Pool(key, prog, t, self._gaps_before(prog))
        return p

    def _create(self, row: dict, t: int) -> None:
        p = self._get(row.get("mint"), "pumpfun", t, make=True)
        if p is None:
            self.counters["rows_without_key"] += 1
            return
        p.created_ms, p.create_slot, p.created_kind = t, row.get("slot"), "create"
        p.creator = row.get("creator") or row.get("trader") or p.creator
        p.name, p.symbol = row.get("name"), row.get("symbol")
        p.last_ms = t
        q, b = row.get("quote_reserve"), row.get("base_reserve")  # initial virtual reserves (post-create)
        if isinstance(q, int) and isinstance(b, int) and p.price is None:
            p.q_real, p.base, p.price = q, float(b), _spot(q, b)
            p.ath, p.ath_ms = p.price, t

    def _pool_create(self, row: dict, t: int) -> None:
        p = self._get(row.get("pool"), "pumpswap", t, make=True)
        if p is None:
            self.counters["rows_without_key"] += 1
            return
        p.created_ms, p.create_slot, p.created_kind = t, row.get("slot"), "pool_create"
        p.last_ms = t
        p.quote_mint = row.get("quote_mint") or p.quote_mint
        self._link_mint(p, row.get("mint"))

    def _link_mint(self, p: Pool, mint: str | None) -> None:
        if not mint:
            return
        p.mint = mint
        self._by_mint.setdefault(mint, set()).add(p.id)
        curve = self._pools.get(mint)
        if curve is not None and curve is not p:
            p.origin_create_ms = curve.created_ms
            p.creator = p.creator or curve.creator

    def _trade(self, row: dict, t: int) -> None:
        prog = row.get("prog")
        key = row.get("mint") if prog == "pumpfun" else row.get("pool")
        p = self._get(key, prog, t, make=True) if prog in ("pumpswap", "pumpfun") else None
        if p is None:
            self.counters["trade_without_key"] += 1
            return
        if prog == "pumpswap" and row.get("mint") and p.mint is None:
            self._link_mint(p, row["mint"])
        if prog == "pumpswap" and row.get("quote_mint") and p.quote_mint is None:
            p.quote_mint = row["quote_mint"]
        buy = row.get("side") == "buy"
        sol, tok = int(row.get("sol_lamports") or 0), int(row.get("token_raw") or 0)
        q, b = int(row.get("quote_reserve") or 0), int(row.get("base_reserve") or 0)
        if prog == "pumpswap":
            v = row.get("virtual_quote_reserves")
            p.v = int(v) if v is not None else p.v
            q_eff_post, b_post = pumpswap_post(q + p.v, b, buy, sol, tok, row.get("lp_fee"), row.get("pool_quote_amount"))
            p.q_real, p.base = int(q_eff_post - p.v), b_post
            price = _spot(q_eff_post, b_post)
        else:
            q_eff_post, b_post = q, b  # bonding rows are already POST-trade
            p.q_real, p.base = q, float(b)
            price = _spot(q, b)
            if row.get("creator") and not p.creator:
                p.creator = row["creator"]
        p.last_ms, p.last_slot = t, row.get("slot")
        p.n_trades += 1
        trader = row.get("trader") or ""
        p.events.append((t, sol, buy, trader))
        cut = t - 60 * MIN_MS
        while p.events and p.events[0][0] < cut:
            p.events.popleft()
        p.net[trader] = p.net.get(trader, 0) + (sol if buy else -sol)
        if p.creator and trader == p.creator:
            if buy:
                p.creator_in += sol
            else:
                p.creator_out += sol
        exec_price = (sol / LAMPORTS) / (tok / TOKEN_SCALE) if tok > 0 and sol > 0 else None
        p.last_trade = {"slot": row.get("slot"), "t_ms": t, "side": row.get("side"), "sol": sol / LAMPORTS, "exec_price": exec_price}
        if price is not None:
            p.price = price
            if p.ath is None or price > p.ath:
                p.ath, p.ath_ms = price, t
            self._candle(p, t, price, sol, buy)
        for cb in ([self.on_trade] if self.on_trade is not None else []) + self._extra_cbs:
            try:
                cb(p, row)
            except Exception:  # noqa: BLE001 - a bad bot must not stop the state
                self.counters["on_trade_errors"] += 1

    @staticmethod
    def _candle(p: Pool, t: int, price: float, sol: int, buy: bool) -> None:
        m = t // MIN_MS
        c = p.candles.get(m)
        if c is None:
            p.candles[m] = [price, price, price, price, sol, 1, sol if buy else 0, 0 if buy else sol]
            while len(p.candles) > CANDLE_MIN:
                p.candles.popitem(last=False)
        else:
            c[1], c[2], c[3] = max(c[1], price), min(c[2], price), price
            c[4] += sol
            c[5] += 1
            c[6 if buy else 7] += sol

    def sweep(self) -> int:
        """Evict keys idle more than 6 h (by the state clock). Returns how many."""
        cut = self.now_ms - EVICT_IDLE_MS
        dead = [k for k, p in self._pools.items() if p.last_ms < cut]
        for k in dead:
            p = self._pools.pop(k)
            if p.mint and p.mint in self._by_mint:
                self._by_mint[p.mint].discard(k)
                if not self._by_mint[p.mint]:
                    del self._by_mint[p.mint]
        self.counters["evicted"] += len(dead)
        for k in [k for k, s in self._spans.items() if s["to_ms"] < self.now_ms - GAP_KEEP_MS]:
            del self._spans[k]
        return len(dead)

    # ---- queries -------------------------------------------------------------------------------------------
    def pool(self, key: str) -> Pool | None:
        return self._pools.get(key)

    def pools(self, predicate: Callable[[Pool], bool] | None = None) -> list[Pool]:
        """Every key, SOL-quoted or not. A PumpSwap pool quoted in another token has sol / price / volume fields in that token's units, and a pool
        whose quote is not yet verified (`quote_mint` None) may be one: use pools_wsol() for anything priced or summed in SOL."""
        return [p for p in self._pools.values() if predicate is None or predicate(p)]

    def pools_wsol(self, predicate: Callable[[Pool], bool] | None = None) -> list[Pool]:
        """Only keys whose quote is VERIFIED to be WSOL (every bonding curve; a PumpSwap pool once pool_create, pool_info or a resolved trade said so)."""
        return [p for p in self._pools.values() if p.quote_mint == WSOL_MINT and (predicate is None or predicate(p))]

    def pools_for_mint(self, mint: str) -> list[Pool]:
        out = [self._pools[k] for k in sorted(self._by_mint.get(mint, ())) if k in self._pools]
        if mint in self._pools:
            out.insert(0, self._pools[mint])
        return out

    def __len__(self) -> int:
        return len(self._pools)

    def windows(self, p: Pool) -> dict[str, dict[str, Any]]:
        """Rolling windows ending at the state clock: buy / sell SOL and counts, unique signers, net flow (SOL)."""
        out: dict[str, dict[str, Any]] = {}
        acc = {w: [0, 0, 0, 0, set()] for w in WINDOWS_MIN}  # buy_lam, sell_lam, n_buys, n_sells, signers
        cuts = {w: self.now_ms - w * MIN_MS for w in WINDOWS_MIN}
        for t, sol, buy, trader in reversed(p.events):
            if t < cuts[60]:
                break
            for w in WINDOWS_MIN:
                if t >= cuts[w]:
                    a = acc[w]
                    if buy:
                        a[0] += sol
                        a[2] += 1
                    else:
                        a[1] += sol
                        a[3] += 1
                    a[4].add(trader)
        for w, a in acc.items():
            out[f"{w}m"] = {"buy_sol": a[0] / LAMPORTS, "sell_sol": a[1] / LAMPORTS, "n_buys": a[2], "n_sells": a[3], "unique_signers": len(a[4]),
                            "net_flow_sol": (a[0] - a[1]) / LAMPORTS}
        return out

    def top_net(self, p: Pool, n: int = TOP_N) -> dict[str, list[dict[str, Any]]]:
        buyers = heapq.nlargest(n, ((k, v) for k, v in p.net.items() if v > 0), key=lambda kv: kv[1])
        sellers = heapq.nsmallest(n, ((k, v) for k, v in p.net.items() if v < 0), key=lambda kv: kv[1])
        return {"buyers": [{"trader": k, "net_sol": v / LAMPORTS} for k, v in buyers], "sellers": [{"trader": k, "net_sol": v / LAMPORTS} for k, v in sellers]}

    def candles(self, p: Pool, n: int = CANDLE_MIN, fill: bool = False) -> list[dict[str, Any]]:
        """Last n minutes of 1-min candles ending at the state clock's minute. fill=True carries the last close through empty minutes."""
        last_m = self.now_ms // MIN_MS
        out: list[dict[str, Any]] = []
        prev_close: float | None = None
        for m in range(last_m - min(n, CANDLE_MIN) + 1, last_m + 1):
            c = p.candles.get(m)
            if c is not None:
                prev_close = c[3]
                out.append({"t_min_ms": m * MIN_MS, "o": c[0], "h": c[1], "l": c[2], "c": c[3], "vol_sol": c[4] / LAMPORTS, "n": c[5],
                            "buy_sol": c[6] / LAMPORTS, "sell_sol": c[7] / LAMPORTS})
            elif fill and prev_close is not None:
                out.append({"t_min_ms": m * MIN_MS, "o": prev_close, "h": prev_close, "l": prev_close, "c": prev_close, "vol_sol": 0.0, "n": 0,
                            "buy_sol": 0.0, "sell_sol": 0.0})
        return out

    def creator_net_sol(self, p: Pool) -> float | None:
        """SOL the creator took out (sells - buys) on this key; None when the creator is unknown."""
        return (p.creator_out - p.creator_in) / LAMPORTS if p.creator else None

    def snapshot(self, key: str, *, candles: bool = True) -> dict[str, Any] | None:
        """JSON-able view of one key at the state clock, or None when unknown."""
        p = self._pools.get(key)
        if p is None:
            return None
        base_ui = p.base / TOKEN_SCALE if p.base is not None else None
        age_ref = p.created_ms if p.created_ms is not None else None
        curve_net = None
        if p.prog == "pumpswap" and p.mint:  # the creator's take on the bonding curve this pool came from, if that curve was seen
            curve = self._pools.get(p.mint)
            if curve is not None and curve.prog == "pumpfun":
                curve_net = self.creator_net_sol(curve)
        prog_gaps = self._gaps_before(p.prog) - p.gaps_at_first_seen
        snap: dict[str, Any] = {
            "id": p.id, "prog": p.prog, "mint": p.mint, "pool": p.pool, "now_ms": self.now_ms, "quote_mint": p.quote_mint,
            "quote_wsol": (p.quote_mint == WSOL_MINT) if p.quote_mint is not None else None,  # None = unverified: sol / price fields may not be SOL
            "price": p.price, "reserves": {"quote_sol": p.q_real / LAMPORTS if p.q_real is not None else None, "base_tokens": base_ui,
                                           "v_sol": p.v / LAMPORTS, "quote_eff_sol": (p.q_real + p.v) / LAMPORTS if p.q_real is not None else None},
            "created_ms": p.created_ms, "created_kind": p.created_kind, "created_known": p.created_ms is not None,
            "age_s": (self.now_ms - age_ref) / 1000.0 if age_ref is not None else None,
            "age_since_first_seen_s": (self.now_ms - p.first_seen_ms) / 1000.0, "origin_create_ms": p.origin_create_ms,
            "curve_progress": 1.0 if (p.prog == "pumpswap" or p.complete_ms is not None) else (curve_progress("pump_bonding", int(p.base)) if p.base is not None else None),
            "complete_ms": p.complete_ms, "last_ms": p.last_ms, "idle_s": (self.now_ms - p.last_ms) / 1000.0, "last_slot": p.last_slot,
            "last_trade": p.last_trade, "n_trades": p.n_trades, "windows": self.windows(p), "top_net": self.top_net(p),
            "creator": p.creator, "creator_net_sol": self.creator_net_sol(p), "creator_net_sol_curve": curve_net,
            "ath": p.ath, "ath_ms": p.ath_ms, "drawdown_from_ath": (1.0 - p.price / p.ath) if (p.price is not None and p.ath) else None,
            "name": p.name, "symbol": p.symbol, "gaps_since_first_seen": prog_gaps, "gap_span_retention_h": GAP_KEEP_MS / 3_600_000, "unique_traders": len(p.net),
        }
        if candles:
            snap["candles"] = self.candles(p)
        return snap


# ---- small CLI ----------------------------------------------------------------------------------------------
def _parse_time(s: str) -> int:
    if s.isdigit():
        return int(s)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return int((dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp() * 1000)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Replay an arena tape dir and print per-pool state (read-only)")
    ap.add_argument("--tape-dir", required=True)
    ap.add_argument("--start", default="0", help="ISO UTC time or epoch ms to replay from (default 0 = the whole dir)")
    ap.add_argument("--until", default=None, help="ISO UTC time or epoch ms to stop at")
    ap.add_argument("--top", type=int, default=10, help="print the top N keys by 5-min volume")
    ap.add_argument("--id", default=None, help="print this key's full snapshot as JSON")
    ap.add_argument("--summary", action="store_true", help="counts only")
    args = ap.parse_args(argv)
    st = ArenaState(args.tape_dir, start_ms=_parse_time(args.start))
    n = st.poll(until_ms=_parse_time(args.until) if args.until else None)
    print(f"rows_applied={n} keys={len(st)} now_ms={st.now_ms} bad_lines={st.counters['bad_lines']} gaps={dict(st.gaps_total)} evicted={st.counters['evicted']}")
    if args.summary:
        return 0
    if args.id:
        print(json.dumps(st.snapshot(args.id), indent=1))
        return 0
    ranked = sorted(st.pools(), key=lambda p: -sum(s for t, s, _b, _tr in p.events if t >= st.now_ms - 5 * MIN_MS))[: args.top]
    for p in ranked:
        w = st.windows(p)["5m"]
        print(f"{p.prog:8s} {p.id} price={p.price} 5m_vol={w['buy_sol'] + w['sell_sol']:.3f}SOL signers={w['unique_signers']} net={w['net_flow_sol']:+.3f} trades={p.n_trades}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
