#!/usr/bin/env python3
"""dbc_p18_run: the P18 live PAPER process (B1): Helius feed -> tools.dbc_fwd.Engine (m10, byte-identical rule, the pinned module is NOT edited) -> hash-chained decision log.
PAPER ONLY: no key but the read-only Helius feed key, no wallet, no send, no price. Registers nothing: no bot_id, no P0, no bots.json line.

What this module adds around the pinned engine (QP ruling 2026-10-26, p33 Q1/Q4):
* `PrunedEngine(Engine)`: pool pruning, decision-neutral (md5 proof: tools/dbc_p18_equiv.py, exploration hours only). Two exact reductions: (1) FRONT TRIM of a pool's rows
  before the tail start that no later call reads (anchor logic of dbc_fwd._tail_start; the 60 s window, the previous group and every pending position's own window are kept,
  prefix sums stay absolute); (2) DROP of a completed pool (dbc_screen.universe needs slot < complete slot, so it cannot trigger again) once it has no open position and no
  unclosed row. A pool that is merely idle is never dropped (it can still trigger); a pool with an open position is never touched. Proof-only oracle variants are not supported.
* drop margin: a completed pool is dropped only DROP_MARGIN_SLOTS (600) slots after its completion slot, so a late row that still belongs to the pool's pre-completion
  slots reaches the pinned engine. Rows of a dropped pool (any slot, in particular slot >= comp_slot) are NOT given to the engine: they skip the fee-mode evidence and the
  pool history and are only counted (`rows_after_drop`, expected 0; late rows with slot < comp_slot after the margin are likewise only counted).
* start guard: no live process, probe or decision log before the m10 read printed "LOOK CONSUMED" (the claim of the one-look marker, checked by lstat only: the marker is never
  opened) AND not before 2026-11-01T00:10Z. `--test` runs only on synthetic fixtures or on exploration tape hours [2026-10-14T00, 2026-10-17T00) and never opens a socket.
* live decision rows carry t_recv_ms (receive time of the trigger row), t_decide_ms and max_slot_seen; counters read DECISION rows only (never exit rows).
* memory bar: RSS above 1.5 GiB stops the process (exit 6, stop row); feed credits are printed (status.json, stdout, stop row).
* restart: a start row quoting the previous head (seq, sha) and the decisions with no exit row (their engine state is lost: open positions are not resumed).
* slot-close TODO: the engine decides a slot only when a row of a LATER slot arrives (p90 3 slots measured). `Runner.on_slot_signal` is the hook for closing on a separate
  next-slot signal; it is NOT wired to any subscription in this PR. `late_rows` (rows that arrive after their slot was decided) and the live-vs-backfill cross-check are the
  guards a later wiring needs, and are counted here.

    python3 -m tools.dbc_p18_run --test --fixture tools/fixtures/dbc_txs_2026-10-14T12.json.gz --out DIR
    python3 -m tools.dbc_p18_run --test --hours 2026-10-14T00 2026-10-14T08 --out DIR
    python3 -m tools.dbc_p18_run --live --env-file /var/lib/mal/fast-listener/helius.env --credit-cap N --out DIR      # refused until the guard opens
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import asyncio
import bisect
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from tools import dbc_fwd as F
from tools import dbc_fwd_feed as FF
from tools import dbc_price as L
from tools import dbc_screen as S
from tools.arena_declog import DecisionLog

EARLIEST_LIVE = datetime(2026, 11, 1, 0, 10, tzinfo=timezone.utc)
MARKER = "/data/mal/hunt-1008/push-1016/hunt29/dbc/HOLDOUT_READ_DONE_v3"  # tools.dbc_fwd_read.MARKER (a test pins equality); lstat only, never opened
TEST_MIN = datetime(2026, 10, 14, 0, tzinfo=timezone.utc)
TEST_MAX = datetime(2026, 10, 17, 0, tzinfo=timezone.utc)  # exclusive
FIXTURE_DIR = os.path.realpath(str(Path(__file__).resolve().parent / "fixtures"))
WATCHDOG_S = 5.0  # RSS check period (live)
MEM_BAR_MIB = 1536.0  # QP bar: RSS < 1.5 GiB
EXIT_REFUSED, EXIT_MEM = 2, 6
WARMUP_S = 3600.0
FORBIDDEN = (*F.FORBIDDEN_PATH_PARTS, "meta-capture", "holdout-read", "fwd-read")
L90_LINE_S, FEED_LAG_FLOOR_S, SEND_P90_S = 2.5, 0.76, 0.453  # QP Q4 item 3 components (send leg carried from the pump probe)


class StartRefused(Exception):
    """The start guard refused: no socket was opened and no log was written."""


# ------------------------------------------------------------------------------------------------------------------------------ guard
def start_guard(*, live: bool, test: bool, now: datetime, marker_exists: Callable[[str], bool] | None = None, marker: str = MARKER, paths: tuple[str, ...] = (),
                hours: tuple[datetime, datetime] | None = None, fixture: bool | str = False) -> None:
    """Raise StartRefused unless the start is allowed. live and test are exclusive. Test: synthetic fixture, or tape hours inside the exploration window; never a socket."""
    if live == test:
        raise StartRefused("exactly one of --live / --test is required")
    for p in paths:
        for q in (p, os.path.realpath(p)):
            if any(x in q for x in FORBIDDEN):
                raise StartRefused(f"sealed path refused: {p}")
    if test:
        if hours is not None:
            a, b = hours
            if a < TEST_MIN or b > TEST_MAX or a >= b:
                raise StartRefused(f"test hours [{a:%Y-%m-%dT%H}, {b:%Y-%m-%dT%H}) are not inside the exploration window [2026-10-14T00, 2026-10-17T00)")
        elif not fixture:
            raise StartRefused("--test needs --fixture (synthetic) or --hours (exploration)")
        elif isinstance(fixture, str) and not os.path.realpath(fixture).startswith(FIXTURE_DIR + os.sep):
            raise StartRefused(f"--fixture must live under {FIXTURE_DIR}")
        return
    if now < EARLIEST_LIVE:
        raise StartRefused(f"live refused before {EARLIEST_LIVE:%Y-%m-%dT%H:%MZ} (now {now:%Y-%m-%dT%H:%M:%SZ})")
    exists = marker_exists or (lambda p: os.path.lexists(p))  # lstat only: the marker file is never opened
    if not exists(marker):
        raise StartRefused("live refused: the m10 read has not printed LOOK CONSUMED (one-look marker absent)")


# ------------------------------------------------------------------------------------------------------------------------------ pruning
class PrunedEngine(F.Engine):
    """dbc_fwd.Engine plus pool pruning (see module doc). Decisions are identical to the unpruned engine; stats gain trim_* / pools_dropped / rows_after_drop counters.
    `live_fields`: add t_recv_ms and max_slot_seen to DECISION rows (live logs only; the equivalence logs do not carry them)."""

    TRIM_MIN_ROWS = 256
    TRIM_MARGIN_S = 60.0
    SWEEP_SLOTS = 600
    # A completed pool is dropped only when cur_slot > comp_slot + DROP_MARGIN_SLOTS. In a live feed a LATE row (slot < comp_slot, arriving after later slots) can still
    # trigger in the pinned engine, so the margin must exceed the late-row tail. Measured on tape (72 h exploration): slot-close wait p50 1 / p90 3 / p99 16 / max 124 slots
    # (MEASURE.md M-a), tape late_rows 0; no live late-row tail is measured yet. 600 slots (about 130 s) is 4.8x that max and costs only the completed pools of the last
    # ~2 minutes (a few dozen pools), so memory is not the constraint.
    DROP_MARGIN_SLOTS = 600

    def __init__(self, *a: Any, live_fields: bool = False, **kw: Any) -> None:
        if kw.get("oracle_rows") is not None or kw.get("oracle_mode") is not None:
            raise ValueError("PrunedEngine does not support the proof-only oracles (absolute indices)")
        super().__init__(*a, **kw)
        self.live_fields = live_fields
        self.comp_names: dict[str, None] = {}
        self.dead: dict[str, int] = {}
        self.trimmed: dict[str, int] = {}  # pool -> rows trimmed off the front
        self.pre0: dict[str, tuple[float, float, int]] = {}  # pool -> prefix sums before the first retained row
        self._next_sweep = 0
        self.stats.update({"trim_calls": 0, "trim_rows": 0, "trim_window_clamped": 0, "pools_dropped": 0, "rows_after_drop": 0})

    # -- input
    def on_complete(self, r: dict[str, Any]) -> None:
        if r["pool"] in self.dead:
            self.stats["completes"] += 1
            self.advance(int(r["slot"]))
            self.stats["rows_after_drop"] += 1
            return
        super().on_complete(r)
        self.comp_names[r["pool"]] = None

    def on_trade(self, r: dict[str, Any]) -> None:
        if r["pool"] in self.dead:
            self.stats["rows"] += 1
            if L.usable_row(r):
                self.stats["sol_rows"] += 1
                if int(r["slot"]) < self.cur_slot:
                    self.stats["late_rows"] += 1
                self.advance(int(r["slot"]))
                self.stats["rows_after_drop"] += 1  # a dropped completed pool printed again: its evidence is not counted (expected 0)
            return
        super().on_trade(r)

    def advance(self, slot: int) -> None:
        old = self.cur_slot
        super().advance(slot)
        if slot > old and self.comp_names and slot >= self._next_sweep:
            self._next_sweep = slot + self.SWEEP_SLOTS
            self._sweep_completed()

    # -- hooks around the pinned close / decide
    def _close(self, p: F.PoolHist) -> None:
        super()._close(p)
        self._trim(p)

    def _decide(self, p: F.PoolHist, lo_new: int, hi: int) -> None:
        bt = p.cols["bt"]
        if self.trimmed.get(p.name) and bisect.bisect_left(bt, bt[lo_new] - F.WIN_S) == 0 and bt[0] > bt[lo_new] - F.WIN_S:
            self.stats["trim_window_clamped"] += 1  # must stay 0
        super()._decide(p, lo_new, hi)

    def _prefilter(self, p: F.PoolHist, lo_new: int, hi: int) -> bool:
        """dbc_fwd.Engine._prefilter, line for line, with the absolute prefix base (`pre0`) in place of 0 for a front-trimmed pool."""
        pre = self.pre0.get(p.name)
        if pre is None:
            return super()._prefilter(p, lo_new, hi)
        if p.hook:
            return False
        bt = p.cols["bt"]
        g0 = lo_new
        for i in range(lo_new, hi):
            if p.cols["side"][i] != 1:
                continue
            if bt[i] < self.first_ok_bt or p.cols["thr"][i] < S.THR_MIN:
                continue
            prog = p.cols["qres"][i] / p.cols["thr"][i] if p.cols["thr"][i] else float("inf")
            if not (0.02 <= prog <= 0.35):
                continue
            w = bisect.bisect_left(bt, bt[i] - F.WIN_S)
            if w == 0 and bt[0] > bt[i] - F.WIN_S:
                self.stats["trim_window_clamped"] += 1
            nb = p.n_buy[hi - 1] - (p.n_buy[w - 1] if w else pre[2])
            if nb < F.CELL.params[0]:
                continue
            buy_up = p.sum_buy_ain[hi - 1] - (p.sum_buy_ain[w - 1] if w else pre[0])
            sell_lo = (p.sum_sell_q[g0 - 1] if g0 else pre[1]) - (p.sum_sell_q[w - 1] if w else pre[1]) if g0 > w else 0.0
            if buy_up - sell_lo >= F.CELL.params[1] * F.SOL * (1 - 1e-9) - 1:
                return True
        return False

    def _append(self, ms: int, body: dict[str, Any]) -> dict[str, Any] | None:
        if self.live_fields and body.get("type") == "decision":
            if body.get("bt_src") == "recv":
                body["t_recv_ms"] = int(round(float(body["block_time"]) * 1000))
            body["max_slot_seen"] = int(self.cur_slot)
        return super()._append(ms, body)

    # -- pruning
    def _sweep_completed(self) -> None:
        for name in list(self.comp_names):
            p = self.pools.get(name)
            if p is None:
                del self.comp_names[name]
                continue
            if (p.comp_slot is None or p.comp_slot + self.DROP_MARGIN_SLOTS >= self.cur_slot or name in self.open or p.closed < p.n() or p.pend_live is not None
                    or p.pend_screen is not None or p.q_screen):
                continue
            self.dead[name] = p.comp_slot
            del self.pools[name]
            del self.comp_names[name]
            self.trimmed.pop(name, None)
            self.pre0.pop(name, None)
            self.stats["pools_dropped"] += 1

    def _trim(self, p: F.PoolHist) -> None:
        """Drop the rows of `p` that no later call can read. Later reads start at `_tail_start(p, x)` for x >= R, or at the previous group (_close). R = the window start of the
        next new row (bt is non-decreasing; plus a margin) and, for each pending position, its own window start and row. Indices of p and of its pending triggers shift."""
        n = p.n()
        if n < self.TRIM_MIN_ROWS or p.closed < n:
            return
        bt = p.cols["bt"]
        ref = bisect.bisect_left(bt, bt[n - 1] - F.WIN_S - self.TRIM_MARGIN_S)
        pend = [t for t in (p.pend_live, p.pend_screen, *p.q_screen) if t is not None]
        for t in pend:
            ref = min(ref, bisect.bisect_left(bt, t["bt"] - F.WIN_S - self.TRIM_MARGIN_S), int(t["row"]))
        t0 = self._tail_start(p, ref)
        if t0 < max(self.TRIM_MIN_ROWS // 2, n // 2):
            return  # amortise: copy only when at least half of the retained rows go
        gi = bisect.bisect_left(p.gstart, t0)
        if not (gi < len(p.gstart) and p.gstart[gi] == t0):
            return
        self.pre0[p.name] = (p.sum_buy_ain[t0 - 1], p.sum_sell_q[t0 - 1], p.n_buy[t0 - 1])
        for k in list(p.cols):
            p.cols[k] = p.cols[k][t0:]
        p.sig = p.sig[t0:]
        p.sum_buy_ain, p.sum_sell_q, p.n_buy = p.sum_buy_ain[t0:], p.sum_sell_q[t0:], p.n_buy[t0:]
        p.gstart = [g - t0 for g in p.gstart[gi:]]
        p.anchors = [a - t0 for a in p.anchors if a >= t0]
        p.closed -= t0
        for t in pend:
            t["row"] = int(t["row"]) - t0
        self.trimmed[p.name] = self.trimmed.get(p.name, 0) + t0
        self.stats["trim_calls"] += 1
        self.stats["trim_rows"] += t0


# ------------------------------------------------------------------------------------------------------------------------------ counters (decision rows only)
def _q(a: list[float], q: float) -> float | None:
    return float(np.quantile(np.asarray(a, float), q)) if a else None


def l90_report(decisions: list[dict[str, Any]], probe_feed_lag_p90_s: float | None = None) -> dict[str, Any]:
    """QP Q4 item 3: L90 = p90(t_decide - t_recv_trigger) + max(0.76 s, probe feed-lag p90) + 0.453 s + 3 x slot_s, bar <= 2.5 s. Reads ONLY rows of type 'decision'
    (never an exit row). Returns the components; `bar_met` is None until the probe lag and at least one decision with both stamps exist."""
    dec = [r for r in decisions if r.get("type") == "decision"]
    d = [(r["t_decide_ms"] - r["t_recv_ms"]) / 1000.0 for r in dec if "t_decide_ms" in r and "t_recv_ms" in r]
    ss = [float(r["slot_s"]) for r in dec if "slot_s" in r]
    p90 = _q(d, 0.9)
    slot_s = float(np.median(ss)) if ss else None
    feed = max(FEED_LAG_FLOOR_S, probe_feed_lag_p90_s) if probe_feed_lag_p90_s is not None else None
    L90 = p90 + feed + SEND_P90_S + 3 * slot_s if None not in (p90, feed, slot_s) else None
    behind = [r["max_slot_seen"] - r["slot"] for r in dec if "max_slot_seen" in r]
    return {"n_decisions": len(dec), "n_with_stamps": len(d), "decide_minus_recv_s": {"p50": _q(d, 0.5), "p90": p90, "max": max(d) if d else None},
            "slots_behind_at_decide": {"p50": _q(behind, 0.5), "p90": _q(behind, 0.9), "max": max(behind) if behind else None}, "feed_lag_term_s": feed,
            "send_term_s": SEND_P90_S, "slot_s": slot_s, "L90_s": L90, "line_s": L90_LINE_S, "bar_met": (L90 <= L90_LINE_S) if L90 is not None else None}


def late_rows_ok(stats: dict[str, Any], bar: float = 0.01) -> dict[str, Any]:
    """late_rows share of SOL rows (bar <= 1%): rows that arrived after their slot was decided."""
    n = int(stats.get("sol_rows", 0))
    share = (stats.get("late_rows", 0) / n) if n else None
    return {"late_rows": int(stats.get("late_rows", 0)), "sol_rows": n, "share": share, "bar": bar, "ok": (share <= bar) if share is not None else None}


def xcheck(live_keys: set[str], backfill_keys: set[str], explained: set[str] = frozenset(), bar: float = 0.02) -> dict[str, Any]:  # type: ignore[assignment]
    """Live-vs-backfill trigger-set cross-check (bar: unexplained difference <= 2% of the union). Keys = dbc_fwd.key_of(pool, slot, S1). `explained` = keys whose
    absence is accounted for (gap windows, warm-up hour). Counts only."""
    union = live_keys | backfill_keys
    diff = (live_keys ^ backfill_keys) - set(explained)
    share = (len(diff) / len(union)) if union else None
    return {"union": len(union), "live_only": len(live_keys - backfill_keys), "backfill_only": len(backfill_keys - live_keys), "explained": len((live_keys ^ backfill_keys) & set(explained)),
            "unexplained": len(diff), "share": share, "bar": bar, "ok": (share <= bar) if share is not None else None}


# ------------------------------------------------------------------------------------------------------------------------------ runner
def rss_mib() -> float:
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576.0


class Runner:
    """Rows in, decision log out. One code path for live, fixture and exploration-hour test modes."""

    def __init__(self, out: str | Path, *, clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000, prune: bool = True, live: bool = False, bt_src: str = "recv",
                 start_ts: float | None = None, rss: Callable[[], float] = rss_mib, mem_bar_mib: float = MEM_BAR_MIB, meter: FF.Meter | None = None,
                 cfg_summary: str | None = None) -> None:
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.clock_ms, self.prune, self.live, self.bt_src = clock_ms, prune, live, bt_src
        self.rss, self.mem_bar, self.meter = rss, mem_bar_mib, meter
        self.log = DecisionLog(str(self.out / "declog"))
        L.enable_swap3_pricing()
        L.load_config_thresholds(cfg_summary or str(Path(__file__).resolve().parent.parent / "docs/research/dbc-p17/data/cfg_summary.json"))
        self.start_ts = start_ts
        self.eng: PrunedEngine | F.Engine | None = None
        self.live_ss: dict[int, float] = {}
        self.cur_hk: int | None = None
        self.hr: dict[str, Any] = {}
        self.mem_exceeded = False
        self.stopped = False
        self.t_started = time.time()
        if start_ts is not None:
            self._ensure(start_ts)

    # -- lifecycle
    def _start_row(self, t_s: float) -> None:
        body: dict[str, Any] = {"type": "start", "book": F.BOOK, "mode": "live" if self.live else "test", "cell": "m10", "exit_rule": F.EXIT_RULE, "schema": F.SCHEMA,
                                "code_sha256": {**F.code_shas(), "dbc_p18_run.py": F.sha256_file(__file__)}, "bt_src": self.bt_src, "prune": self.prune,
                                "warmup_s": WARMUP_S, "mem_bar_mib": self.mem_bar}
        try:
            prev_sha, prev_seq = self.log.verify(F.BOOK)
        except Exception:  # noqa: BLE001 - chain errors must stop the process, not be hidden
            raise
        if prev_seq >= 0:
            rows = self.log.rows(F.BOOK)
            exited = {r.get("decision_seq") for r in rows if r.get("type") == "exit"}
            body.update({"restart": True, "prev_head": prev_sha, "prev_seq": prev_seq,
                         "orphan_decision_seqs": [r["seq"] for r in rows if r.get("type") == "decision" and r["seq"] not in exited]})
        self.log.append(F.BOOK, int(t_s * 1000), body)

    def _ensure(self, t_s: float) -> None:
        if self.eng is not None:
            return
        first_ok = (self.start_ts if self.start_ts is not None else t_s) + WARMUP_S
        meta = {"bt_src": self.bt_src}
        kw: dict[str, Any] = dict(ss_live=lambda hk: S.slot_s_for(self.live_ss, hk), screen_policy=False, log=self.log, meta=meta,
                                  now_ms=self.clock_ms if self.live else None)
        self.eng = PrunedEngine(first_ok, live_fields=self.live, **kw) if self.prune else F.Engine(first_ok, **kw)
        self._start_row(t_s)

    # -- rows
    def on_row(self, r: dict[str, Any]) -> None:
        t = r.get("type")
        if t in ("gap", "gap_end", "stop"):
            self.log.append(F.BOOK, int(r.get("t_ms") or r.get("end_ms") or self.clock_ms()), {k: v for k, v in r.items()})
            return
        if t not in ("trade", "curve_complete"):
            return
        bt = L.block_time_s(r)
        self._ensure(bt)
        if t == "trade" and L.usable_row(r):
            hk = int(bt // 3600)
            if self.cur_hk is None:
                self.cur_hk = hk
            elif hk > self.cur_hk:
                self._roll_hour()
                self.cur_hk = hk
            h = self.hr
            h["n"] = h.get("n", 0) + 1
            h["bt0"], h["bt1"] = min(h.get("bt0", bt), bt), max(h.get("bt1", bt), bt)
            sl = int(r["slot"])
            h["s0"], h["s1"] = min(h.get("s0", sl), sl), max(h.get("s1", sl), sl)
        assert self.eng is not None
        self.eng.on_row(r)

    def _roll_hour(self) -> None:
        h, hk = self.hr, self.cur_hk
        assert self.eng is not None and hk is not None
        if h.get("n", 0) > 50 and h["bt1"] > h["bt0"]:  # dbc_fwd.slot_s_of's rule
            self.live_ss[hk + 1] = float((h["bt1"] - h["bt0"]) / max(h["s1"] - h["s0"], 1))
        self.hr = {}
        st = self.eng.stats
        self.log.append(F.BOOK, (hk + 1) * 3600 * 1000 - 1, {"type": "hb", "book": F.BOOK, "hour": datetime.fromtimestamp(hk * 3600, timezone.utc).strftime("%Y-%m-%dT%H"),
                                                              "decisions": len(self.eng.decisions_live), "trigger_prints": st["trigger_prints"], "sol_rows": st["sol_rows"],
                                                              "late_rows": st["late_rows"]})
        self.tick()

    def on_slot_signal(self, slot: int) -> None:
        """TODO (not wired): close `slot` on a separate next-slot signal instead of the next DBC row. A row of the closed slot that arrives later is counted in late_rows."""
        if self.eng is not None:
            self.eng.advance(int(slot) + 1)

    def finish(self) -> None:
        """Test modes only: resolve pending positions with the data present. Never called by the live loop."""
        if self.eng is not None:
            self.eng.finish()

    # -- status
    def decision_rows(self) -> list[dict[str, Any]]:
        return [r for r in self.log.rows(F.BOOK) if r.get("type") == "decision"]  # decision rows only (QP condition 6)

    def tick(self) -> dict[str, Any]:
        mem = self.rss()
        if mem > self.mem_bar:
            self.mem_exceeded = True
        st: dict[str, Any] = {"pid": os.getpid(), "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "rss_mib": round(mem, 1), "mem_bar_mib": self.mem_bar,
                              "mem_exceeded": self.mem_exceeded, "uptime_s": round(time.time() - self.t_started, 1)}
        if self.eng is not None:
            st.update({"pools": len(self.eng.pools), "rows_retained": sum(p.n() for p in self.eng.pools.values()), "stats": dict(self.eng.stats),
                       "late_rows": late_rows_ok(self.eng.stats), "decisions": len(self.eng.decisions_live)})
        if self.meter is not None:
            st["feed"] = self.meter.row()
        tmp = self.out / "status.json.tmp"
        tmp.write_text(json.dumps(st, default=float))
        os.replace(tmp, self.out / "status.json")
        return st

    def report(self) -> dict[str, Any]:
        st = self.tick()
        st["latency"] = l90_report(self.decision_rows())
        return st


# ------------------------------------------------------------------------------------------------------------------------------ modes
def run_fixture(path: str, out: str, prune: bool = True) -> dict[str, Any]:
    run = Runner(out, prune=prune, live=False, bt_src="recv")
    for msg, t in FF.fixture_notifications(path):
        for r in FF.rows_from_notification(msg, t):
            run.on_row(r)
    run.finish()
    return run.report()


def run_hours(start: datetime, end: datetime, out: str, root: str | None = None, prune: bool = True) -> dict[str, Any]:
    run = Runner(out, prune=prune, live=False, bt_src="tape_block_time", start_ts=start.timestamp())
    for h in L.exploration_hours(start, end):
        f = L.hour_file(h, root)
        if not os.path.exists(f):
            continue
        r = F.read_hour_ordered(f, root)
        if r["incomplete"]:
            continue
        for row in r["rows"]:
            run.on_row(row)
    run.finish()
    return run.report()


async def run_live(out: str, env_file: str, credit_cap: float | None, prune: bool = True) -> int:
    from tools import venue_backfill as VB  # noqa: PLC0415

    try:
        import websockets  # noqa: PLC0415
    except ImportError:
        print("REFUSED: python package 'websockets' is not installed in this interpreter", file=sys.stderr)
        return EXIT_REFUSED
    now = datetime.now(timezone.utc)
    start_guard(live=True, test=False, now=now)
    url = FF.ws_url(VB.read_key(env_file))
    meter, gaps = FF.Meter(credit_cap), FF.Gaps()
    run = Runner(out, prune=prune, live=True, bt_src="recv", start_ts=now.timestamp(), meter=meter)
    stop = asyncio.Event()

    async def watchdog() -> None:
        n = 0
        while not stop.is_set():
            await asyncio.sleep(WATCHDOG_S)
            st = run.tick()
            n += 1
            if n % int(60 / WATCHDOG_S) == 0:
                print(json.dumps({k: st.get(k) for k in ("utc", "rss_mib", "pools", "rows_retained", "decisions", "feed")}), flush=True)
            if run.mem_exceeded:
                stop.set()

    wd = asyncio.create_task(watchdog())
    try:
        rc = await FF.run_feed(url, run.on_row, stop, connect=websockets.connect, meter=meter, gaps=gaps)
    finally:
        stop.set()
        wd.cancel()
    if run.mem_exceeded and rc == 0:
        rc = EXIT_MEM
    reason = {0: "stop", FF.EXIT_CAP: "credit_cap", EXIT_MEM: "memory_bar"}.get(rc, "stop")
    run.log.append(F.BOOK, run.clock_ms(), {"type": "stop", "reason": reason, **meter.row(), "rss_mib": round(run.rss(), 1)})
    print(json.dumps({"stop": reason, "feed": meter.row(), "rss_mib": round(run.rss(), 1)}), flush=True)
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--fixture", default="")
    ap.add_argument("--hours", nargs=2, metavar=("START", "END"))
    ap.add_argument("--root", default=None)
    ap.add_argument("--env-file", default="")
    ap.add_argument("--credit-cap", type=float, default=None)
    ap.add_argument("--no-prune", action="store_true")
    ap.add_argument("--out", required=True)
    ns = ap.parse_args(argv)
    pa = lambda v: datetime.strptime(v, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)  # noqa: E731
    hours = (pa(ns.hours[0]), pa(ns.hours[1])) if ns.hours else None
    try:
        start_guard(live=ns.live, test=ns.test, now=datetime.now(timezone.utc), paths=tuple(p for p in (ns.fixture, ns.root or "", ns.out) if p), hours=hours, fixture=ns.fixture or False)
    except StartRefused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return EXIT_REFUSED
    if ns.live:
        if not ns.env_file:
            print("REFUSED: --env-file is required", file=sys.stderr)
            return EXIT_REFUSED
        return asyncio.run(run_live(ns.out, ns.env_file, ns.credit_cap, not ns.no_prune))
    res = run_hours(hours[0], hours[1], ns.out, ns.root, not ns.no_prune) if hours else run_fixture(ns.fixture, ns.out, not ns.no_prune)
    print(json.dumps(res, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
