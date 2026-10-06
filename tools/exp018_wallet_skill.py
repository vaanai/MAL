#!/usr/bin/env python3
"""EXP-018: causal wallet skill at migration. EXPLORATION ONLY. NO EDGE CLAIM.

Plan (follow it exactly): EXP/EXP-018-wallet-skill-plan.md.

Modes
  --precount   OUTCOME-BLIND. Streams the clean-view tape of the scored series (explore-0814, and fresh-0903 + exp011-0909 as one contiguous
               series), builds per-mint skill features at the cutoff (the universe row's mig_ms, EXP-012's causal_events boundary), writes features.jsonl and precount.json (per-source
               rows, wallets, mints with >= 1 skilled holder, coverage, warm-up effect on n). It never opens a net: the EXP-015 cache rows are
               parsed with an object hook that DROPS net0 / status / sides / p_press at parse time.
  (screen)     needs --features (the file --precount wrote; its sha256 is checked against precount.json). Refuses before `started` on any pin,
               coverage or zero-cell failure; then takes RUN.lock (O_EXCL), logs two `started` tries (W1, W2), evaluates, writes screen.json,
               screen.md, result.v1 files and the `completed` tries. A second run is refused.

Reuse. This module imports tools.exp015_screen (on main) and COPIES the cache-reading, pin, paired / Holm / bootstrap code from
tools/exp017_screen.py at PR #423 (head 1bf5f8bf314da34d3607c725777de28f9a49e8b4), because #423 is not merged. Copied blocks are marked `# from exp017`.
The FIFO accounting is tools/wallet_leaderboard.py's (constants imported; the round-trip P&L is its `match_sell` total, proved equal by a test).
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import random
import sys
import time
from array import array
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

import tools.exp015_screen as e15
from tools import mal_result
from tools.wallet_leaderboard import DUST_TOKEN_RAW, TX_FEE_LAMPORTS

TOOL = "tools.exp018_wallet_skill"
SCHEMA = "exp018_screen_v1"
FEATURES_SCHEMA = "exp018_features_v1"
REPO_ROOT = Path(__file__).resolve().parent.parent
PLAN = REPO_ROOT / "EXP" / "EXP-018-wallet-skill-plan.md"
LAMPORTS = 1_000_000_000

# --- pins (fixed in the plan before any outcome is read) -------------------------------------------------------------
DEFAULT_SCRATCH = "/data/mal/exp015-screen/scratch"
CACHE_MANIFEST_SHA256 = "72b9bd1a355953c114835a77fd7ea2f2273e75b21393cf5be2d48a16523ba31b"  # 12 files, scratch/cache/v_P*.{rows.jsonl,manifest.json}
CACHE_CODE_SHA = "cc366d4c7d8597d6429575c165f164cce35ce39d"
MIN_TRIPS = 5  # N: closed round trips before a wallet counts as skilled
WARMUP_HOURS = 72
WINDOW_MS = 60_000  # the last 60 s before the cutoff
IDLE_EVICT_HOURS = 24  # positions of a mint idle this long are dropped (counted); a mint with a pending snapshot is never evicted
PURGE_MIN = e15.PURGE_MIN  # 35 min around a held-out date for the nested median
CELLS = ("W1", "W2")
FAMILY_ALPHA = 0.05
BOOT_DRAWS_P, BOOT_SEED = 10_000, 1
LEGS = e15.LEGS
STAKE_LAMPORTS = int(round(e15.SIZE_SOL * LAMPORTS))
PRIMARY = e15.PRIMARY_CELL
NET_KEYS = frozenset({"net0", "status", "sides", "p_press"})
SCORED_SOURCES = ("P2", "P3", "P4")
OUT_SCREEN, OUT_MD, OUT_FEATURES, OUT_PRECOUNT = "screen.json", "screen.md", "features.jsonl", "precount.json"

# series: contiguous hour sets, carried in time order. The warm-up (WARMUP_HOURS) is counted from each series' first hour. Hours resolve through
# EXP-015's own guards and loaders (resolve_series): P2 via guard_p2 + MultiViewHours, P3 via guard_p3 + make_p3_hours (trades-<h>.deduped.jsonl.zst),
# P4 via guard_p4 + MultiViewHours.
SERIES: dict[str, dict[str, Any]] = {
    "S_P2": {"sources": ("P2",), "start": "2026-08-14T12", "end": "2026-08-28T12"},
    "S_P34": {"sources": ("P3", "P4"), "start": "2026-09-03T12", "end": "2026-09-15T12"},
}
DEFAULT_P2_VIEWS = [f"{e15.P2_BASE}/w{i}" for i in range(1, 8)]
DEFAULT_P4_VIEWS = ["/data/mal/clean-view/exp011-0909/b", "/data/mal/clean-view/exp011-0909/c"]
CANONICAL_TRIES_LOG = "/data/mal/ops/tries/tries.jsonl"  # as scripts/research/exp013-screen-run.sh sets MAL_TRIES_LOG
BONDING_FEE_PPM = 12_500  # tools/paper_curve_math.BONDING_FEE_PPM (1.25%), each side; bonding rows' sol_lamports is PRE-fee
BANNER = "EXP-018 wallet skill: EXPLORATION ONLY, NO EDGE CLAIM. A pass earns one confirmation read on an unread reserved block under a later pre-registration."


class Refused(Exception):
    """A pre-declared refusal. Before `started`: nothing is logged, no try is spent."""


# --- tape rows --------------------------------------------------------------------------------------------------------


def parse_row(r: Mapping[str, Any]) -> tuple[int, int, int, int, str, str, str, int, int, bool] | None:
    """(t_ms, slot, tx_index, event_index, mint, trader, side, sol_lamports, token_raw, is_bonding) or None. Same admission rules as
    tools.wallet_leaderboard.parse_trade: a trade row, buy or sell, a pump_bonding row or a PumpSwap row with quote_is_wsol True, resolved mint,
    positive integer amounts."""
    if r.get("type") not in (None, "trade"):
        return None
    mint, trader, side = r.get("mint"), r.get("trader"), r.get("side")
    if not isinstance(mint, str) or not mint or not isinstance(trader, str) or not trader or side not in ("buy", "sell"):
        return None
    if r.get("mint_source") == "unresolved":
        return None
    venue = r.get("venue")
    if venue not in ("pump_bonding", "pumpswap"):
        return None
    if venue == "pumpswap" and r.get("quote_is_wsol") is not True:
        return None
    if r.get("quote_is_wsol") is False:
        return None
    try:
        slot, sol, tok = int(r["slot"]), int(r["sol_lamports"]), int(r["token_raw"])
        t = r.get("t_recv_ms")
        if t is None and isinstance(r.get("block_time"), int):
            t = r["block_time"] * 1000  # as the EXP-015/016 loaders do for getBlock rows
        t = int(t)
        tx, ev = int(r.get("tx_index") or 0), int(r.get("event_index") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    if sol <= 0 or tok <= 0:
        return None
    return t, slot, tx, ev, mint, trader, side, sol, tok, venue == "pump_bonding"


# --- the causal skill engine ------------------------------------------------------------------------------------------


class SkillState:
    """Streaming wallet skill. Rows are fed in (t_recv_ms, slot, tx_index, event_index) order within an hour; `snapshot` calls are made by the caller
    when the first row with t_recv_ms >= cutoff arrives, so a snapshot sees exactly the rows with t_recv_ms < cutoff (the boundary of
    tools.exploration_entry_model.causal_events(feat.events, mig_ms), which the frozen EXP-012 model's features use).

    Position per (mint, wallet): [tokens, open_cost, realized, n_trades, bought]. Buy: tokens += t, open_cost += sol. Sell: clamp to inventory (the
    unmatched part is dropped, as match_sell does), realized += proceeds - cost of the matched share; when the inventory falls to dust the trip
    CLOSES: net = realized - TX_FEE * n_trades is added to the wallet's total, the trip count rises, and the position resets (bought stays 1).
    A round trip's P&L is the sum over its sells of (proceeds - matched cost), which equals proceeds - cost whatever the lot order, so this equals
    wallet_leaderboard.match_sell's total (tested)."""

    def __init__(self, min_trips: int = MIN_TRIPS) -> None:
        self.min_trips = min_trips
        self.wid: dict[str, int] = {}
        self.pnl = array("q")
        self.trips = array("q")
        self.mid: dict[str, int] = {}
        self.pos: dict[int, dict[int, list[int]]] = {}
        self.last_hour: dict[int, int] = {}
        self.buys: dict[int, list[tuple[int, int, int]]] = {}  # tracked mints only: (t_ms, wallet id, sol) of bonding buys in the window
        self.track_cutoff: dict[int, int] = {}
        self.n_skilled = 0  # running count of skilled wallets
        self.stats = {"rows": 0, "late_rows": 0, "evicted_mints": 0, "evicted_positions": 0, "unmatched_sells": 0, "trips_closed": 0}

    def _w(self, w: str) -> int:
        i = self.wid.get(w)
        if i is None:
            i = self.wid[w] = len(self.pnl)
            self.pnl.append(0)
            self.trips.append(0)
        return i

    def _m(self, m: str) -> int:
        i = self.mid.get(m)
        if i is None:
            i = self.mid[m] = len(self.mid)
        return i

    def track(self, mint: str, cutoff_ms: int) -> None:
        mi = self._m(mint)
        self.track_cutoff[mi] = cutoff_ms
        self.buys.setdefault(mi, [])

    def skilled(self, w: int) -> bool:
        return self.trips[w] >= self.min_trips and self.pnl[w] > 0

    def feed(self, t_ms: int, slot: int, mint: str, trader: str, side: str, sol: int, tok: int, bonding: bool, hour_idx: int) -> None:
        self.stats["rows"] += 1
        mi, wi = self._m(mint), self._w(trader)
        self.last_hour[mi] = hour_idx
        book = self.pos.setdefault(mi, {})
        p = book.get(wi)
        if bonding:  # pre-fee row amount -> what the wallet paid / received (R2); pumpswap rows are already user-side
            fee = sol * BONDING_FEE_PPM // 1_000_000
            raw_sol, sol = sol, (sol + fee if side == "buy" else sol - fee)
        else:
            raw_sol = sol
        if side == "buy":
            if p is None:
                p = book[wi] = [0, 0, 0, 0, 0]
            p[0] += tok
            p[1] += sol
            p[3] += 1
            if bonding and mi in self.track_cutoff and self.track_cutoff[mi] - WINDOW_MS <= t_ms < self.track_cutoff[mi]:
                self.buys[mi].append((t_ms, wi, raw_sol))
            if bonding:
                p[4] = 1
            return
        if p is None or p[0] <= 0:
            self.stats["unmatched_sells"] += 1
            return
        inv = p[0]
        sell_tok, sell_sol = tok, sol
        if sell_tok > inv:
            sell_sol = sol - int(round(sol * (1.0 - inv / sell_tok)))
            sell_tok = inv
        cost = p[1] * sell_tok // inv if inv else 0
        p[2] += sell_sol - cost
        p[1] -= cost
        p[0] -= sell_tok
        p[3] += 1
        if p[0] <= DUST_TOKEN_RAW:
            net = p[2] - TX_FEE_LAMPORTS * p[3]
            was = self.skilled(wi)
            self.pnl[wi] += net
            self.trips[wi] += 1
            self.n_skilled += int(self.skilled(wi)) - int(was)
            self.stats["trips_closed"] += 1
            p[0], p[1], p[2], p[3] = 0, 0, 0, 0

    def evict_idle(self, hour_idx: int) -> None:
        dead = [m for m, h in self.last_hour.items() if h < hour_idx - IDLE_EVICT_HOURS and m not in self.track_cutoff]
        for m in dead:
            self.stats["evicted_positions"] += sum(1 for p in self.pos.get(m, {}).values() if p[0] > 0)
            self.pos.pop(m, None)
            del self.last_hour[m]
        self.stats["evicted_mints"] += len(dead)

    def snapshot(self, mint: str) -> dict[str, Any]:
        """Features of `mint` at its cutoff, from the state as it stands (the caller guarantees: rows with t_recv_ms < cutoff only)."""
        mi = self.mid[mint]
        book = self.pos.get(mi, {})
        held = n_holders = n_buyers = sk_buyers = sk_holders = 0
        for wi, p in book.items():
            if not p[4]:
                continue
            n_buyers += 1
            s = self.skilled(wi)
            sk_buyers += int(s)
            if p[0] > DUST_TOKEN_RAW:
                n_holders += 1
                if s:
                    held += p[1]
                    sk_holders += 1
        tot = sk = 0
        for _t, wi, sol in self.buys.get(mi, ()):
            tot += sol
            if self.skilled(wi):
                sk += sol
        self.track_cutoff.pop(mi, None)
        self.buys.pop(mi, None)
        return {"skilled_holder_lamports": held, "skilled_holder_count": sk_holders, "skilled_buyer_count": sk_buyers, "n_buyers": n_buyers, "n_holders": n_holders,
                "win_buy_lamports": tot, "win_skilled_buy_lamports": sk, "skilled_share": (sk / tot) if tot else 0.0, "n_wallets": len(self.pnl),
                "n_skilled_wallets": self.n_skilled, "rows_before": self.stats["rows"]}


def run_series(hours: Sequence[str], rows_of_hour: Callable[[str], Iterable[Mapping[str, Any]]], cutoffs: Mapping[str, int], min_trips: int = MIN_TRIPS,
               progress: Callable[[str], None] | None = None) -> tuple[dict[str, dict[str, Any]], SkillState]:
    """Feed the series' hours in time order. `cutoffs` = mint -> cutoff in ms (the universe row's mig_ms). Returns mint -> features (mints whose cutoff the
    tape reached) and the final state. A row with t_recv_ms < the largest cutoff already snapshotted is counted as late and still applied (it cannot
    have been seen by any earlier snapshot, so it cannot leak)."""
    st = SkillState(min_trips)
    for m, c in cutoffs.items():
        st.track(m, c)
    pending = sorted((c, m) for m, c in cutoffs.items())
    ptr = 0
    out: dict[str, dict[str, Any]] = {}
    max_flushed = -1
    for hi, h in enumerate(hours):
        st.evict_idle(hi)
        buf = []
        for r in rows_of_hour(h):
            t = parse_row(r)
            if t is not None:
                buf.append(t)
        buf.sort(key=lambda t: (t[0], t[1], t[2], t[3]))
        for t_ms, slot, _tx, _ev, mint, trader, side, sol, tok, bonding in buf:
            while ptr < len(pending) and pending[ptr][0] <= t_ms:
                c, m = pending[ptr]
                out[m] = {**st.snapshot(m), "cutoff_ms": c}
                max_flushed = max(max_flushed, c)
                ptr += 1
            if t_ms < max_flushed:
                st.stats["late_rows"] += 1
            st.feed(t_ms, slot, mint, trader, side, sol, tok, bonding, hi)
        if progress:
            progress(f"{h} rows={st.stats['rows']} wallets={len(st.pnl)} snapshots={len(out)}")
    return out, st


# --- loaders (real layout: P2/P4 <view>/trades/trades-<hour>.jsonl.zst; P3 <walker>/trades/trades-<hour>.deduped.jsonl.zst; resolved by EXP-015's loaders) ---


import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class SplitHours:
    """Picklable resolver: hours in `first_hours` resolve through `first`, the rest through `second` (P3 then P4)."""

    first: Any
    second: Any
    first_hours: frozenset

    def __call__(self, h: str) -> dict[str, Any]:
        return (self.first if h in self.first_hours else self.second)(h)


def check_hours_resolve(series: str, hours: Sequence[str], fn: Callable[[str], Mapping[str, Any]]) -> None:
    """Every hour of the series must resolve to an existing trades file, or refuse (also in precount)."""
    missing: list[str] = []
    for h in hours:
        try:
            path = Path(fn(h)["trade"])
        except (SystemExit, AssertionError, KeyError) as exc:
            missing.append(f"{h} ({type(exc).__name__}: {exc})")
            continue
        if not path.is_file():
            missing.append(f"{h} ({path})")
    if missing:
        raise Refused(f"series {series}: {len(missing)} of {len(hours)} hours have no trades file (first: {missing[0]})")


def resolve_series(p2_views: Sequence[str], p3_root: str, p4_views: Sequence[str], verify: bool = True, enforce_base: bool = True) -> dict[str, dict[str, Any]]:
    """Resolve both series through EXP-015's own guards and loaders (VIEW.sha256, tiling, dedupe manifests, reserved paths). Refuses on any missing hour."""
    import tools.exp012_backcheck as bc

    try:
        g2 = e15.guard_p2(p2_views, verify, enforce_base)
        g3 = e15.guard_p3(p3_root, verify, enforce_base)
        g4 = e15.guard_p4(list(p4_views), verify)
    except e15.Refused as exc:
        raise Refused(str(exc)) from None
    if g4 is None:
        raise Refused("S_P34 needs the P4 views (exp011-0909)")
    p3_hours = e15.block_hours("P3")
    p4_hours = list(g4["pool"])
    out = {
        "S_P2": {"hours": list(g2["pool"]), "fn": bc.MultiViewHours(dict(g2["roots"]))},
        "S_P34": {"hours": p3_hours + p4_hours, "fn": SplitHours(e15.make_p3_hours(g3["walkers"]), bc.MultiViewHours(dict(g4["roots"])), frozenset(p3_hours))},
    }
    for k, d in out.items():
        spec = SERIES[k]
        if bc.hours_range(spec["start"], spec["end"]) != d["hours"]:
            raise Refused(f"series {k}: hours are not the contiguous {spec['start']}..{spec['end']} range")
        check_hours_resolve(k, d["hours"], d["fn"])
    return out


def read_trade_file(path: Path | str, hour: str, counters: dict[str, int]) -> Iterable[dict[str, Any]]:
    """Local reader. zstd exiting non-zero refuses. Rows whose block_time lies outside the file's hour are dropped and counted
    (as exploration_exits' strict_hours does for creates); a row without an integer block_time is kept."""
    lo = e15.hour_ms(hour) // 1000
    hi = lo + 3600
    path = Path(path)
    proc = None
    if path.name.endswith(".zst"):
        proc = subprocess.Popen(["zstd", "-dc", "-q", str(path)], stdout=subprocess.PIPE)
        assert proc.stdout is not None
        lines: Iterable[Any] = proc.stdout
    else:
        lines = open(path, "rb")
    try:
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                r = json.loads(raw)
            except ValueError:
                counters["bad_json"] = counters.get("bad_json", 0) + 1
                continue
            if not isinstance(r, dict):
                continue
            bt = r.get("block_time")
            if isinstance(bt, int) and not isinstance(bt, bool) and not (lo <= bt < hi):
                counters["out_of_hour_rows"] = counters.get("out_of_hour_rows", 0) + 1
                continue
            yield r
    finally:
        if proc is not None:
            assert proc.stdout is not None
            proc.stdout.close()
            rc = proc.wait()
        else:
            lines.close()  # type: ignore[union-attr]
            rc = 0
    if rc != 0:
        raise Refused(f"zstd exited {rc} on {path}")


def make_rows_of_hour(fn: Callable[[str], Mapping[str, Any]], counters: dict[str, int]) -> Callable[[str], Iterable[Mapping[str, Any]]]:
    def rows_of_hour(h: str) -> Iterable[Mapping[str, Any]]:
        return read_trade_file(fn(h)["trade"], h, counters)

    return rows_of_hour


def cutoffs_from_universe(universe: Sequence[Mapping[str, Any]], sources: Sequence[str]) -> dict[str, int]:
    """mint -> mig_ms of the EXP-015 universe row: the `mint.mig_ms` EXP-012's features cut at (`causal_events(feat.events, mig_ms)`)."""
    return {u["mint"]: int(u["mig_ms"]) for u in universe if u["source"] in sources}


# --- cache (from exp017) ----------------------------------------------------------------------------------------------


def _file_sha256(path: str | Path) -> str:  # from exp017
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_manifest(scratch: str | Path) -> tuple[list[str], str]:  # from exp017 (CACHE_PATTERNS only)
    lines = []
    for pat in ("cache/v_P*.rows.jsonl", "cache/v_P*.manifest.json"):
        for p in sorted(glob.glob(os.path.join(str(scratch), pat))):
            lines.append(f"{_file_sha256(p)}  {os.path.relpath(p, str(scratch))}")
    lines.sort(key=lambda s: s.split("  ", 1)[1])
    return lines, hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


def check_cache(scratch: str | Path, pin: str = CACHE_MANIFEST_SHA256) -> dict[str, Any]:  # from exp017 (check_manifests + check_cache_heads, cache half)
    lines, sha = cache_manifest(scratch)
    if sha != pin:
        raise Refused(f"cache manifest sha256 {sha} ({len(lines)} files) != pinned {pin}")
    for src in e15.SOURCES:
        p = Path(scratch) / "cache" / f"v_{src}.manifest.json"
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise Refused(f"{p} unreadable") from None
        if (m.get("meta") or {}).get("head") != CACHE_CODE_SHA or (m.get("meta") or {}).get("vmap_sha256") != e15.VMAP_0909_SHA256:
            raise Refused(f"{p}: head/vmap pin differs from CACHE_CODE_SHA / the pinned V map")
    return {"n_files": len(lines), "sha256": sha, "pinned": pin}


def _drop_net(d: dict[str, Any]) -> dict[str, Any]:  # from exp017
    return {k: v for k, v in d.items() if k not in NET_KEYS}


def read_cache_rows(path: str | Path, blind: bool) -> list[dict[str, Any]]:  # from exp017
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line, object_hook=_drop_net) if blind else json.loads(line))
    return rows


def load_universe(scratch: str | Path, blind: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:  # from exp017
    v_rows = {src: read_cache_rows(Path(scratch) / "cache" / f"v_{src}.rows.jsonl", blind) for src in e15.SOURCES}
    return e15.build_universe(v_rows, {}, True)


def frozen_scores(universe: Sequence[Mapping[str, Any]], artifact_dir: Path | None = None, scorer: Callable[[Any], Sequence[float]] | None = None) -> list[float]:  # from exp017
    import numpy as np

    import tools.exp011_freeze as fz
    import tools.exp011_score as e11
    from tools.exp012_latency_sensitivity import DEFAULT_ARTIFACT_DIR

    x = np.asarray([u["features"] for u in universe], dtype=np.float64).reshape(len(universe), len(fz.FROZEN_FEATURE_NAMES))
    if scorer is None:
        model, _thr, names = e11.load_frozen_spec(artifact_dir or DEFAULT_ARTIFACT_DIR)
        assert list(names) == list(fz.FROZEN_FEATURE_NAMES)
        return [float(s) for s in model.predict(x, num_threads=1)]
    return [float(s) for s in scorer(x)]


# --- feature build (precount mode) -----------------------------------------------------------------------------------


def series_of(source: str) -> str:
    return next(k for k, s in SERIES.items() if source in s["sources"])


def series_start_ms(series: str) -> int:
    return e15.hour_ms(SERIES[series]["start"])


def scored_flag(u: Mapping[str, Any]) -> bool:
    """Warm-up exclusion: a migration enters scoring only if mig_ms is at least WARMUP_HOURS after its series' first hour."""
    return u["mig_ms"] >= series_start_ms(series_of(u["source"])) + WARMUP_HOURS * 3_600_000


def build_series_features(series: str, cutoffs: Mapping[str, int], hours: Sequence[str], fn: Callable[[str], Mapping[str, Any]],
                          progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    counters: dict[str, int] = {}
    feats, st = run_series(hours, make_rows_of_hour(fn, counters), cutoffs, progress=progress)
    return {"series": series, "features": feats, "hours": len(hours), "hours_with_file": len(hours), "state": st.stats, "reader": counters,
            "n_wallets": len(st.pnl), "n_skilled_wallets_final": st.n_skilled}


def _series_worker(args: tuple[str, dict[str, int], list[str], Any]) -> dict[str, Any]:
    series, cutoffs, hours, fn = args
    return build_series_features(series, cutoffs, hours, fn, progress=lambda m: print(f"[{series}] {m}", file=sys.stderr, flush=True))


def precount_report(universe: Sequence[Mapping[str, Any]], built: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    feats: dict[str, Mapping[str, Any]] = {}
    for b in built:
        feats.update(b["features"])
    by_source: dict[str, dict[str, Any]] = {}
    for u in universe:
        if u["source"] not in SCORED_SOURCES:
            continue
        d = by_source.setdefault(u["source"], {"n": 0, "with_features": 0, "scored_after_warmup": 0, "warmup_excluded": 0, "with_skilled_holder": 0, "scored_with_skilled_holder": 0})
        f = feats.get(u["mint"])
        sc = scored_flag(u)
        d["n"] += 1
        if f is not None and f.get("rows_before", 0) <= 0:
            d["snapshots_without_tape"] = d.get("snapshots_without_tape", 0) + 1
            f = None  # a snapshot counts only when its series had tape before the cutoff
        d["with_features"] += int(f is not None)
        d["scored_after_warmup"] += int(sc and f is not None)
        d["warmup_excluded"] += int(not sc)
        sk = f is not None and f["skilled_holder_lamports"] > 0
        d["with_skilled_holder"] += int(sk)
        d["scored_with_skilled_holder"] += int(sk and sc)
    for d in by_source.values():
        d["coverage"] = d["with_features"] / d["n"] if d["n"] else None
    dates = sorted({u["date"] for u in universe if u["source"] in SCORED_SOURCES and scored_flag(u)})
    return {"by_source": by_source, "series": {b["series"]: {k: b[k] for k in ("hours", "hours_with_file", "state", "reader", "n_wallets", "n_skilled_wallets_final")} for b in built},
            "scored_dates": dates, "n_scored_dates": len(dates), "min_trips": MIN_TRIPS, "warmup_hours": WARMUP_HOURS, "outcome_blind": True}


def write_features(path: Path, universe: Sequence[Mapping[str, Any]], built: Sequence[Mapping[str, Any]]) -> str:
    feats: dict[str, Mapping[str, Any]] = {}
    for b in built:
        feats.update(b["features"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for u in sorted(universe, key=lambda u: u["mint"]):
            f = feats.get(u["mint"])
            if f is not None and u["source"] in SCORED_SOURCES:
                fh.write(json.dumps({"schema": FEATURES_SCHEMA, "mint": u["mint"], "source": u["source"], **f}, sort_keys=True) + "\n")
    return _file_sha256(path)


# --- cells ------------------------------------------------------------------------------------------------------------


def median(xs: Sequence[float]) -> float | None:
    s = sorted(xs)
    n = len(s)
    return None if n == 0 else s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def purged(u: Mapping[str, Any], date: str) -> bool:
    a = e15.date_start_ms(date)
    z = a + 86_400_000
    return a - PURGE_MIN * 60_000 <= u["mig_ms"] < z + PURGE_MIN * 60_000


def nested_thresholds(universe: Sequence[Mapping[str, Any]], frozen: Sequence[bool], share: Sequence[float | None], scope: Sequence[int]) -> dict[str, float | None]:
    """tau_d = median skilled_share over frozen-selected scored rows on every OTHER scored date, rows within PURGE_MIN of date d dropped.
    Uses no outcome."""
    dates = sorted({universe[i]["date"] for i in scope})
    out: dict[str, float | None] = {}
    for d in dates:
        pool = [share[i] for i in scope if frozen[i] and share[i] is not None and universe[i]["date"] != d and not purged(universe[i], d)]
        out[d] = median(pool)
    return out


def build_masks(universe: Sequence[Mapping[str, Any]], scores: Sequence[float], feats: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    n = len(universe)
    scope = [i for i, u in enumerate(universe) if u["block"] != "P1" and u["mint"] in feats and feats[u["mint"]].get("rows_before", 1) > 0 and scored_flag(u)]
    inscope = set(scope)
    frozen = [bool(i in inscope and scores[i] >= e15.FROZEN_THRESHOLD) for i in range(n)]
    share = [feats[u["mint"]]["skilled_share"] if i in inscope else None for i, u in enumerate(universe)]
    w1 = [bool(frozen[i] and feats[universe[i]["mint"]]["skilled_holder_lamports"] > 0) if i in inscope else False for i in range(n)]
    taus = nested_thresholds(universe, frozen, share, scope)
    w2 = [bool(frozen[i] and share[i] is not None and taus.get(universe[i]["date"]) is not None and share[i] >= taus[universe[i]["date"]]) for i in range(n)]
    return {"scope": scope, "frozen": frozen, "W1": w1, "W2": w2, "taus": taus}


# --- evaluation (copied from exp017, scope/dates parameterised) ------------------------------------------------------


def row_net(u: Mapping[str, Any]) -> dict[str, float] | None:
    return e15.cell_nets(u["cells"].get(PRIMARY))


def trades_of(universe: Sequence[Mapping[str, Any]], mask: Sequence[bool], rows: Sequence[int]) -> list[dict[str, Any]]:
    out = []
    for i in rows:
        if not mask[i]:
            continue
        nn = row_net(universe[i])
        if nn is None:
            continue
        u = universe[i]
        out.append({"mint": u["mint"], "day": u["date"], "filled": bool(u["cells"][PRIMARY].get("filled")), "flat": nn["flat"], "press": nn["press"], "source": u["source"], "block": u["block"],
                    "stake": STAKE_LAMPORTS})
    return out


def boot_p(by_date: Mapping[str, Sequence[float]], draws: int = BOOT_DRAWS_P, seed: int = BOOT_SEED) -> float | None:  # from exp017
    keys = sorted(k for k, v in by_date.items() if len(v))
    if not keys:
        return None
    sums = [sum(by_date[k]) for k in keys]
    cnts = [len(by_date[k]) for k in keys]
    rng = random.Random(seed)
    le0 = 0
    for _ in range(draws):
        pick = [rng.randrange(len(keys)) for _ in keys]
        n = sum(cnts[i] for i in pick)
        if n and sum(sums[i] for i in pick) / n <= 0:
            le0 += 1
    return (1 + le0) / (1 + draws)


def paired(universe: Sequence[Mapping[str, Any]], mask: Sequence[bool], fmask: Sequence[bool], rows: Sequence[int]) -> dict[str, Any]:  # from exp017, masks instead of nets
    out: dict[str, Any] = {"n_migrations": len(rows)}
    ok, ps = True, []
    for leg in LEGS:
        by_date: dict[str, list[float]] = {}
        for i in rows:
            nn = row_net(universe[i])
            a = nn[leg] if (mask[i] and nn is not None) else 0.0
            b = nn[leg] if (fmask[i] and nn is not None) else 0.0
            by_date.setdefault(universe[i]["date"], []).append(a - b)
        allx = sorted((v for xs in by_date.values() for v in xs), reverse=True)
        mean = (sum(allx) / len(allx) / LAMPORTS) if allx else None
        ci = e15.date_cluster_ci(by_date)
        p = boot_p(by_date)
        ps.append(p)
        ex3 = (sum(allx[3:]) / LAMPORTS) if len(allx) > 3 else None
        leg_ok = mean is not None and mean > 0 and ci is not None and ci[0] > 0 and ex3 is not None and ex3 > 0
        ok = ok and leg_ok
        out[leg] = {"mean_x_sol": mean, "ci90_date_sol": ci, "p_one_sided": p, "x_sum_ex_top3_sol": ex3, "pass": bool(leg_ok)}
    out["p"] = None if any(p is None for p in ps) else max(ps)
    out["pass_bar"] = bool(ok)
    return out


def holm(pvals: Mapping[str, float | None], alpha: float = FAMILY_ALPHA) -> dict[str, Any]:  # from exp017
    items = sorted(((c, 1.0 if p is None else p) for c, p in pvals.items()), key=lambda t: (t[1], t[0]))
    m = len(items)
    out: dict[str, Any] = {}
    stop = False
    for j, (c, p) in enumerate(items):
        thr = alpha / (m - j)
        rej = (not stop) and p <= thr
        stop = stop or not rej
        out[c] = {"p": pvals[c], "threshold": thr, "reject": bool(rej)}
    return out


def evaluate_cell(universe: Sequence[Mapping[str, Any]], mask: Sequence[bool], fmask: Sequence[bool], scope: Sequence[int]) -> dict[str, Any]:
    """Bars 1-6 on the scored non-P1 rows. n_dates for the majority-of-days rule is the number of UTC dates with >= 1 scored row (warm-up
    shrinks it; disclosed). B1 gate; B2 paired vs frozen (Holm applied by the caller); B3 concentration; B4 P2+P4 mean > 0 both legs; B5 P2 mean > 0 and
    majority of P2 scored dates positive; B6 P3+P4 mean > 0."""
    scope_dates = sorted({universe[i]["date"] for i in scope})
    tr = trades_of(universe, mask, scope)
    rep = e15.scope_report(tr, len(scope_dates))
    pr = paired(universe, mask, fmask, scope)
    bars: dict[str, Any] = {"B1": {"report": rep, "pass": bool(rep["gate_all"])}, "B2": {"report": pr, "pass": bool(pr["pass_bar"])},
                            "B3": {"pass": bool(rep["concentration_all"]), "concentration": {leg: rep[leg]["concentration"] for leg in LEGS}}}

    def mean_pos(blocks: Sequence[str], majority: bool = False) -> dict[str, Any]:
        sub = [x for x in tr if x["block"] in blocks]
        nd = len({universe[i]["date"] for i in scope if universe[i]["block"] in blocks})
        res, ok = {}, True
        for leg in LEGS:
            st = e15.leg_stats(sub, leg)
            leg_ok = st["mean_sol"] is not None and st["mean_sol"] > 0 and (not majority or st["dates_positive"] * 2 > nd)
            ok = ok and leg_ok
            res[leg] = {"n": st["n"], "mean_sol": st["mean_sol"], "dates_positive": st["dates_positive"], "n_dates": nd, "pass": bool(leg_ok)}
        return {"detail": res, "pass": bool(ok)}

    bars["B4"], bars["B5"], bars["B6"] = mean_pos(["P2", "P4"]), mean_pos(["P2"], True), mean_pos(["P3", "P4"])
    return {"bars": bars, "bars_all": all(b["pass"] for b in bars.values()), "p": pr["p"], "n_trades": len(tr), "n_scope_dates": len(scope_dates)}


# --- guards, tries, lock, outputs ------------------------------------------------------------------------------------

CELL_DESC = {
    "W1": "frozen EXP-012 selection minus entries whose skilled_holder_lamports == 0 (skill: net FIFO P&L > 0 over >= 5 round trips closed before the cutoff)",
    "W2": "frozen EXP-012 selection restricted to skilled_share >= the median over the frozen-selected rows of the other scored dates (nested leave-one-day-out)",
}


def prior_exp018_lines(log: Path) -> list[dict[str, Any]]:
    out = []
    if Path(log).is_file():
        for line in Path(log).read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("tool") == TOOL or str((rec.get("config") or {}).get("key", "")).startswith("exp018_"):
                out.append(rec)
    return out


def log_cell_tries(log: Path, out_dir: Path, status: str) -> dict[str, dict[str, Any]]:
    groups = {"universe": e15.UNIVERSE_BLOCKS} if status == "started" else e15.pool_group_blocks(True)
    info: dict[str, dict[str, Any]] = {}
    for c in CELLS:
        for g, blocks in groups.items():
            info[c] = mal_result.append_try(
                log, tool=TOOL, role="exploration", data_blocks=list(blocks), result_path=out_dir / OUT_SCREEN,
                config={"key": f"exp018_{c.lower()}", "experiment": "EXP-018", "cell": c, "status": status, "pool_group": g, "desc": CELL_DESC[c], "k": 6, "exit_lag": 2,
                        "fee_lamports": e15.FEE, "pricing": "V", "min_trips": MIN_TRIPS})
    return info


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def check_features_file(features: Path, out_dir: Path) -> str:
    pc = out_dir / OUT_PRECOUNT
    if not pc.is_file():
        raise Refused(f"{pc} missing: run --precount first")
    want = json.loads(pc.read_text(encoding="utf-8")).get("features_sha256")
    got = _file_sha256(features)
    if want != got:
        raise Refused(f"features sha256 {got} != the one --precount recorded ({want})")
    return got


def load_features(path: Path) -> dict[str, dict[str, Any]]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["mint"]] = r
    return out


def zero_cell_check(masks: Mapping[str, Any]) -> None:
    zero = [c for c in CELLS if sum(masks[c]) == 0]
    if zero:
        raise Refused(f"zero-cell refusal: {zero} select no scored migration")
    if sum(masks["frozen"]) == 0:
        raise Refused("the frozen book selects no scored migration")


COVERAGE_MIN = 0.90
MIN_FROZEN_SCORED = 100


def check_tries_log_arg(arg: str | None, canonical: Path) -> None:
    if arg is not None and Path(arg).resolve() != Path(canonical).resolve():
        raise Refused(f"--tries-log {arg} is not the canonical {canonical}")


def check_canonical_tries_log(path: Path) -> None:
    """Guards a wrong MAL_TRIES_LOG: the canonical log must exist and already hold exp015 lines."""
    p = Path(path)
    if not p.is_file():
        raise Refused(f"tries log {p} does not exist: set MAL_TRIES_LOG to the canonical {CANONICAL_TRIES_LOG}")
    if not e15.prior_exp015_lines(p):
        raise Refused(f"tries log {p} has no exp015 lines: not the canonical log ({CANONICAL_TRIES_LOG}); check MAL_TRIES_LOG")


def check_w2_flag(pre: Mapping[str, Any], universe: Sequence[Mapping[str, Any]], masks: Mapping[str, Any], feats: Mapping[str, Mapping[str, Any]]) -> None:
    """Recompute selection_report from the screen's own masks; refuse if its w2_degenerate disagrees with precount.json."""
    mine = selection_report(universe, masks, feats)["w2_degenerate"]
    theirs = (pre.get("selection") or {}).get("w2_degenerate")
    if bool(mine) != bool(theirs):
        raise Refused(f"w2_degenerate recomputed {mine} != precount.json {theirs}")


def set_cells(cells: Sequence[str]) -> None:
    global CELLS
    CELLS = tuple(cells)


def selection_report(universe: Sequence[Mapping[str, Any]], masks: Mapping[str, Any], feats: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Outcome-blind (features and frozen scores only). W2 is degenerate, and dropped before the read, when tau_d is 0 (or undefined) on every date:
    skilled_share >= 0 is always true, so W2 would equal the frozen book."""
    sel = [i for i in masks["scope"] if masks["frozen"][i]]
    n = len(sel)
    pos_h = sum(1 for i in sel if feats[universe[i]["mint"]]["skilled_holder_lamports"] > 0)
    pos_s = sum(1 for i in sel if feats[universe[i]["mint"]]["skilled_share"] > 0)
    taus = masks["taus"]
    degenerate = all(t is None or t == 0.0 for t in taus.values())
    return {"frozen_selected_scored": n, "share_skilled_holder_gt0": (pos_h / n) if n else None, "share_skilled_share_gt0": (pos_s / n) if n else None,
            "n_skilled_holder_gt0": pos_h, "n_skilled_share_gt0": pos_s, "tau_by_date": taus, "w2_degenerate": bool(degenerate)}


def check_precount(precount: Mapping[str, Any], masks: Mapping[str, Any]) -> None:
    """Plan section 7 item 8: coverage below 90% on a scored source, fewer than 100 frozen-selected scored migrations, or no skilled holder at all."""
    by = precount.get("by_source") or {}
    bad = [s for s in SCORED_SOURCES if s in by and (by[s]["coverage"] or 0.0) < COVERAGE_MIN]
    if bad:
        raise Refused(f"feature coverage below {COVERAGE_MIN:.0%} on {bad}: {[by[s]['coverage'] for s in bad]}")
    if sum(masks["frozen"]) < MIN_FROZEN_SCORED:
        raise Refused(f"only {sum(masks['frozen'])} frozen-selected scored migrations (< {MIN_FROZEN_SCORED})")
    if not any(d["with_skilled_holder"] for d in by.values()):
        raise Refused("no mint with a skilled holder in any scored source")


def decide(cells: Mapping[str, Mapping[str, Any]], hm: Mapping[str, Mapping[str, Any]]) -> str:
    wins = [c for c in CELLS if hm[c]["reject"] and cells[c]["bars_all"]]
    if not wins:
        return "SCREEN NONE: no cell is Holm-significant with bars 1-6 passing. Nothing goes to confirmation. The family is closed."
    return f"SCREEN PASS: {', '.join(wins)} clear Holm (k=2) and bars 1-6. This means 'worth one confirmation read', never 'has an edge'."


def _fmt(v: Any) -> str:
    return "n/a" if v is None else f"{v:+.5f}" if isinstance(v, float) else str(v)


def render_md(rep: Mapping[str, Any]) -> str:
    L = [f"# EXP-018 wallet-skill screen", "", f"**{BANNER}**", "", f"Outcome: {rep['outcome']}", "",
         "| Cell | n trades | n dates | p (paired) | Holm threshold | Holm reject | B1 | B2 | B3 | B4 | B5 | B6 | all bars |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in CELLS:
        r, h = rep["cells"][c], rep["holm"][c]
        L.append(f"| {c} | {r['n_trades']} | {r['n_scope_dates']} | {_fmt(h['p'])} | {h['threshold']:.4f} | {h['reject']} | " + " | ".join(str(r["bars"][f"B{i}"]["pass"]) for i in range(1, 7)) + f" | {r['bars_all']} |")
    L += ["", f"Frozen book (scored scope): n selected {rep['n_frozen_selected']}; " + "; ".join(f"{c} {rep['n_selected'][c]}" for c in CELLS) + ".", "", "## Caveats"] + [f"- {x}" for x in CAVEATS]
    return "\n".join(L) + "\n"


CAVEATS = (
    "Exploration. The cells reuse the non-P1 dates EXP-015 already read; the frozen cell is itself best-of-many (winner's curse).",
    "The first 72 h of each series are excluded from scoring; the dates that remain are fewer than 27 and the majority-of-days rules use that count.",
    "Skill is a trade-flow statistic: SPL transfers are not on the tape, and a wallet's other addresses are not linked.",
    "The date-cluster bootstrap seed (1) is shared with EXP-015 / EXP-017, so p-values are not independent across screens.",
    "Bonding fees (1.25% per side) are charged to wallet cash flow; priority fees and tips are not on the tape and are excluded.",
    "P1 dates are not scored here (their cutoff clock is derived, and P1 is report-only in EXP-015).",
)


def run_screen(universe: Sequence[Mapping[str, Any]], masks: Mapping[str, Any]) -> dict[str, Any]:
    scope = masks["scope"]
    res = {c: evaluate_cell(universe, masks[c], masks["frozen"], scope) for c in CELLS}
    hm = holm({c: res[c]["p"] for c in CELLS})
    for c in CELLS:
        res[c]["bars"]["B2"]["pass"] = bool(res[c]["bars"]["B2"]["pass"] and hm[c]["reject"])
        res[c]["bars_all"] = all(b["pass"] for b in res[c]["bars"].values())
    trades = {c: trades_of(universe, masks[c], scope) for c in CELLS}
    return {"cells": res, "holm": hm, "trades": trades, "outcome": decide(res, hm), "caveats": list(CAVEATS),
            "n_frozen_selected": sum(masks["frozen"]), "n_selected": {c: sum(masks[c]) for c in CELLS}, "taus": masks["taus"]}


def _rv1(x: Mapping[str, Any], leg: str) -> dict[str, Any]:
    sol = x[leg] / LAMPORTS
    return {"sol": sol, "pct": sol / (x["stake"] / LAMPORTS), "day": x["day"], "filled": bool(x["filled"])}


def write_results(out_dir: Path, trades: Mapping[str, Sequence[Mapping[str, Any]]], info: Mapping[str, Mapping[str, Any]], log: Path, head: str, runtime_s: float) -> None:
    for c in CELLS:
        t = trades[c]
        tries = {**info[c], "of_m": mal_result.tries_summary(log, info[c]["data_key"])["of_m"]}
        res = mal_result.build_result(
            tool=TOOL, git_sha=head, command="python -m tools.exp018_wallet_skill", config={"cell": c, "desc": CELL_DESC[c]}, role="exploration",
            data_blocks=e15.pool_group_blocks(True)["P2"], stage="screen", trades_flat=[_rv1(x, "flat") for x in t], trades_pressure_s1=[_rv1(x, "press") for x in t],
            tries=tries, runtime_s=runtime_s, notes="EXP-018 exploration screen; no edge claim")
        mal_result.write_result(out_dir / f"result_{c}.json", res)


# --- CLI --------------------------------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scratch", default=DEFAULT_SCRATCH)
    ap.add_argument("--out-dir", type=Path, default=Path("/data/mal/exp018-screen"))
    ap.add_argument("--tries-log", default=None)
    ap.add_argument("--vmap", default=e15.VMAP_0909_PATH)
    ap.add_argument("--artifact-dir", type=Path, default=None)
    ap.add_argument("--features", type=Path, default=None, help="features.jsonl written by --precount (screen mode)")
    ap.add_argument("--precount", action="store_true", help="outcome-blind tape pass: features.jsonl + precount.json; reads no net")
    ap.add_argument("--p2-view-dir", action="append", default=None, help="explore-0814 view dirs (default w1..w7)")
    ap.add_argument("--p3-root", default=e15.P3_BASE)
    ap.add_argument("--p4-view-dir", action="append", default=None, help="exp011-0909 view dirs (default b, c)")
    ap.add_argument("--workers", type=int, default=2, help="one process per series (2 series); capped at 2")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    from tools.exp012_exit_sensitivity import resolve_tries_path

    args = _parser().parse_args(argv)
    out_dir: Path = args.out_dir
    tries_path: Path | None = None
    try:
        cache = check_cache(args.scratch)
        e15.check_vmap(args.vmap, e15.VMAP_0909_SHA256, "V map")
        resolved = resolve_series(args.p2_view_dir or DEFAULT_P2_VIEWS, args.p3_root, args.p4_view_dir or DEFAULT_P4_VIEWS)
        if args.precount:
            if args.tries_log is not None:
                raise Refused("--precount reads no tries log; do not pass --tries-log")
            universe, _stats = load_universe(args.scratch, blind=True)
            jobs = [(k, cutoffs_from_universe(universe, SERIES[k]["sources"]), resolved[k]["hours"], resolved[k]["fn"]) for k in SERIES]
            if min(args.workers, 2) > 1:
                from concurrent.futures import ProcessPoolExecutor

                with ProcessPoolExecutor(max_workers=2) as ex:
                    built = list(ex.map(_series_worker, jobs))
            else:
                built = [_series_worker(j) for j in jobs]
            rep = precount_report(universe, built)
            feats_all: dict[str, Mapping[str, Any]] = {}
            for bt in built:
                feats_all.update(bt["features"])
            masks = build_masks(universe, frozen_scores(universe, args.artifact_dir), feats_all)
            rep["selection"] = selection_report(universe, masks, feats_all)
            sha = write_features(out_dir / OUT_FEATURES, universe, built)
            write_json(out_dir / OUT_PRECOUNT, {**rep, "features_sha256": sha, "cache": cache})
            print(json.dumps({"mode": "precount", **rep, "features_sha256": sha}, indent=2, default=str))
            return 0
        tries_path = Path(resolve_tries_path(None))
        check_tries_log_arg(args.tries_log, tries_path)
        check_canonical_tries_log(tries_path)
        prior = prior_exp018_lines(tries_path)
        if prior:
            raise Refused(f"{len(prior)} earlier exp018 line(s) in {tries_path}: a second run is refused")
        if args.features is None:
            raise Refused("screen mode needs --features (run --precount first)")
        check_features_file(args.features, out_dir)
        pre = json.loads((out_dir / OUT_PRECOUNT).read_text(encoding="utf-8"))
        if (pre.get("selection") or {}).get("w2_degenerate"):
            set_cells(("W1",))  # pre-declared: tau_d is 0 on every date, W2 would equal the frozen book; dropped before the read, one try
        e15.check_run_lock(out_dir)
        universe, _stats = load_universe(args.scratch, blind=False)
        feats = load_features(args.features)
        scores = frozen_scores(universe, args.artifact_dir)
        masks = build_masks(universe, scores, feats)
        zero_cell_check(masks)
        check_w2_flag(pre, universe, masks, feats)
        check_precount(pre, masks)
        head = e15.git_state()["head"]
        e15.take_lock(out_dir, head, hashlib.sha256(json.dumps(sorted(vars(args).items(), key=lambda kv: kv[0]), default=str).encode()).hexdigest())
    except (Refused, e15.Refused) as exc:
        print(f"refusing (before started, no tries line): {exc}", file=sys.stderr)
        return 2
    assert tries_path is not None
    t0, started, status = time.time(), False, "aborted_after_read"
    try:
        log_cell_tries(tries_path, out_dir, "started")
        started = True
        rep = run_screen(universe, masks)
        rep.update({"schema": SCHEMA, "head": head, "cache": cache, "min_trips": MIN_TRIPS, "warmup_hours": WARMUP_HOURS})
        trades = rep.pop("trades")
        write_json(out_dir / OUT_SCREEN, rep)
        (out_dir / OUT_MD).write_text(render_md(rep), encoding="utf-8")
        info = log_cell_tries(tries_path, out_dir, "completed")
        write_results(out_dir, trades, info, tries_path, head, time.time() - t0)
        status = "completed"
        print(rep["outcome"])
        return 0
    except Exception as exc:  # noqa: BLE001 - tries are spent after `started`
        print(f"aborted after started: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    finally:
        e15.write_record(out_dir, status, started, {c: status for c in CELLS})


if __name__ == "__main__":
    raise SystemExit(main())
