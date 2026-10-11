#!/usr/bin/env python3
"""DEC-026 item 13 (parity task 3) and item 14(a): the shadow's replay with a state-mode switch, in a SEPARATE harness.

Why a separate file. QP-1010 part (a) P2 asks for two replays on the same inputs: run A in state mode `exact` (the batch's state: the PRE-trade
reserves of the pool's first print with slot >= SD, as #506's harness tools/c1nf_parity.py defines it) and run B in state mode `live` (prints
with slot < SD only; the fee-model post-trade estimate), the mode the canary runs. tools/c1nf_shadow.py has no exact switch, and changing it
would move the soak's pinned blob (c2dafcdc, MiScusi #658). This harness leaves tools/c1nf_shadow.py and tools/c1nf_features.py byte-unchanged:

  * `--state-mode live`  runs tools.c1nf_shadow.run_replay itself, with an engine built exactly as tools.c1nf_shadow.build_engine builds it
                         (same class, ledger_keep_days and print_window). Its picks are the shadow's replay picks by construction (tested).
  * `--state-mode exact`  runs the same run_replay, with the evaluation point of #506's harness (tools/c1nf_parity.py:225-230). That
                         harness evaluates every decision live at SD and, for a pool that prints again before the batch's horizon
                         (graduation day D + 2 days, 02:00Z; tools/c1nf_parity.end_hour_of), re-evaluates it when that next print
                         arrives, before the print is ingested, with next_state = the print's PRE-trade (quote_reserve, base_reserve).
                         Every other table is read at that moment too: the creator record (_passc: cr_known_out, cr_lmax6, cr_lend6,
                         cr_big6 read Q.outcome or Q.outcome_now() of the creator's other pools, which a print of Q between SD and P's
                         next print changes), the market minutes, the create, graduation and narrative lists. ExactShadow does the same:
                         at decision time it asks a look-ahead index (built from the same tape hours before the replay) whether the
                         pool has a print with slot >= SD before the horizon. If not (or that print's state is not a valid state), the
                         pool is decided at SD, live, as #506 keeps the live row ("no later print for the pool in the window -> exact
                         == live"). If so, the decision is DEFERRED: it runs, through the shadow's own Shadow._decide (model, PRED_MIN,
                         cap, seal, book, decision state, pick schema unchanged), when the pool's next PumpSwap row reaches _on_trade,
                         before Shadow._on_trade ingests it, with that row's (quote_reserve, base_reserve) as next_state.
                         The book stays the shadow's: a mint's deferred decisions resolve in T order, before any later decision of the
                         same pool, and keep their own T, SD and decision bt (book, pick t_ms). The pool's decision state (its last
                         print with slot < SD) is unchanged, since no print of the pool arrives in between. Exact mode prices no
                         outcome (a deferred Pending would see prints a live one never sees): no c1nf_outcome record is written.
                         Cross-harness equality on a synthetic creator fixture: tools/test_c1nf_parity_exact.py.

Not part of the evaluation point, and shared by run A and run B (the cross-harness check on 09-20 measures them): the shadow's SD (the
slot of the first row of a new minute) against #506's (the least trade slot with block_time >= T), the row order inside a slot (row_key
against #506's creates-trades-migrations order), and #506's hourly expire (pools freed at g0 + 49 h, outcome frozen) where replay never
expires.

Outputs in --out-dir (which must be empty or absent):
  c1nf-prewindow-picks-*.jsonl, c1nf-*.jsonl     the shadow's own sink (every replay pick is a pre-window pick)
  parity-stage1-<mode>.npz                       every stage-1 decision row with a wallet snapshot: pool, mint, T, sd, h_top1, vec (float32)
  parity-summary-<mode>.json                     mode, input sha256s and git blobs, look-ahead counters, engine health, picks per UTC day,
                                                 and P3's list md5 (lines `mint,decision_T_ms,SD_slot`, sorted, newline-terminated) per day
                                                 and overall, BEFORE tie removal (P3 recomputes it after removing boundary ties and
                                                 their book cascades unless both sides have 0 ties). It opens no outcome or P&L column.
                                                 Exact mode adds `exact`: deferred / resolved / unresolved decisions and row-vs-index
                                                 state mismatches; a run with an unresolved deferral or a mismatch exits 1.

  python tools/c1nf_parity_exact.py --state-mode exact --replay-from 2026-09-03T12 --replay-hours 288 \
      --model-manifest M.json --ledger-root ROOT --out-dir OUT_A
  python tools/c1nf_parity_exact.py --state-mode exact --replay-from 2026-09-03T12 --replay-hours 288 --probe-lookahead --out-dir OUT_P
      (builds the look-ahead index only, prints its size and the process VmHWM, writes parity-lookahead-probe.json, and exits)
  python tools/c1nf_parity_exact.py cross-compare REPLAY506.npz OUT_A/parity-stage1-exact.npz [--skip-wallet] [--json J]
      (builds the look-ahead index only, prints its size and the process VmHWM, writes parity-lookahead-probe.json, and exits)

Exploration tape only: tools.c1nf_shadow.refuse_replay runs before any read (exploration hours, forbidden path tokens). No CAP-PICK oracle,
no seal (replay). Heavy runs are MiScusi jobs, memory-capped; this file does not decide the cap.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools import c1nf_shadow as cs  # noqa: E402
from tools.c1nf_features import FeatureEngine, utc_day  # noqa: E402

STATE_LIVE, STATE_EXACT = "live", "exact"
STATE_MODES = (STATE_LIVE, STATE_EXACT)
HORIZON_DAYS, HORIZON_HOUR = 2, 2              # tools/c1nf_parity.end_hour_of: the batch loads D00 .. D+2 T01 for graduation day D
EXACT_LEDGER_KEEP_DAYS = 3                     # a deferred decision of day D can resolve on D+2 while T moves on: keep 3 as-of snapshots so the
                                               # engine does not reopen one per minute (FeatureEngine._ledger_for: a reopened day has the same values)
EXIT_EXACT_CHECK = 1                           # exact mode: an unresolved deferral or a row/index state mismatch


def horizon_bt(g0: Optional[int]) -> Optional[int]:
    """First block_time past the batch's horizon for a pool that graduated at g0 (None: no horizon known, no cut)."""
    if g0 is None:
        return None
    d = datetime.strptime(utc_day(int(g0)), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int((d + timedelta(days=HORIZON_DAYS, hours=HORIZON_HOUR)).timestamp())


def _num(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


class Lookahead:
    """Per canonical pool, its PumpSwap prints in (slot, tx_index, event_index) order: slot, block_time and the PRE-trade reserves the tape
    row carries (quote_reserve, base_reserve). next_state(pool, sd, horizon) = the first print with slot >= sd, or None."""

    def __init__(self, pool_v: Mapping[str, float]) -> None:
        self.pool_v = pool_v
        self._parts: dict[str, list[tuple[np.ndarray, ...]]] = {}
        self._idx: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}
        self.rows = 0
        self.c: dict[str, int] = {"asked": 0, "hit": 0, "no_pool": 0, "none_after_sd": 0, "past_horizon": 0, "invalid_reserves": 0}
        self._last_key: dict[str, tuple[int, int, int]] = {}

    def add_columns(self, venue: Sequence[Any], pool: Sequence[Any], slot: Sequence[Any], tx: Sequence[Any], ev: Sequence[Any],
                    bt: Sequence[Any], q: Sequence[Any], b: Sequence[Any]) -> int:
        """One tape hour (any row order; sorted here, stable, by (slot, tx_index or 0, event_index or 0) like row_key). Hours must come in
        order: a print that sorts before a pool's last added print raises (the index would no longer be the tape's order)."""
        keep = [i for i in range(len(venue)) if venue[i] == "pumpswap" and pool[i] in self.pool_v]
        if not keep:
            return 0
        k_slot = np.array([int(slot[i]) for i in keep], dtype=np.int64)
        k_tx = np.array([int(tx[i] or 0) if _num(tx[i]) is not None else 0 for i in keep], dtype=np.int64)
        k_ev = np.array([int(ev[i] or 0) if _num(ev[i]) is not None else 0 for i in keep], dtype=np.int64)
        order = np.lexsort((k_ev, k_tx, k_slot))                 # stable: equal keys keep tape order
        by_pool: dict[str, list[int]] = {}
        for j in order.tolist():
            by_pool.setdefault(pool[keep[j]], []).append(j)
        for p, js in by_pool.items():
            first = (int(k_slot[js[0]]), int(k_tx[js[0]]), int(k_ev[js[0]]))
            last = self._last_key.get(p)
            if last is not None and first < last:
                raise ValueError(f"{p}: rows added out of order ({first} after {last})")
            self._last_key[p] = (int(k_slot[js[-1]]), int(k_tx[js[-1]]), int(k_ev[js[-1]]))
            ii = [keep[j] for j in js]
            qq = np.array([_num(q[i]) if _num(q[i]) is not None else np.nan for i in ii], dtype=np.float64)
            bb = np.array([_num(b[i]) if _num(b[i]) is not None else np.nan for i in ii], dtype=np.float64)
            tt = np.array([int(bt[i]) if _num(bt[i]) is not None else -1 for i in ii], dtype=np.int64)
            self._parts.setdefault(p, []).append((k_slot[js], tt, qq, bb))
        self.rows += len(keep)
        return len(keep)

    def add_rows(self, rows: Iterable[Mapping[str, Any]]) -> int:
        rows = [r for r in rows if r.get("_k", "trades") == "trades"]
        cols = ("venue", "pool", "slot", "tx_index", "event_index", "block_time", "quote_reserve", "base_reserve")
        return self.add_columns(*[[r.get(c) for r in rows] for c in cols])

    def freeze(self) -> "Lookahead":
        for p, parts in self._parts.items():
            self._idx[p] = tuple(np.concatenate([x[k] for x in parts]) for k in range(4))  # type: ignore[assignment]
        self._parts.clear()
        return self

    def nbytes(self) -> int:
        return int(sum(a.nbytes for v in self._idx.values() for a in v))

    def next_state(self, pool: str, sd: int, horizon: Optional[int] = None) -> Optional[tuple[float, float]]:
        self.c["asked"] += 1
        ix = self._idx.get(pool)
        if ix is None:
            self.c["no_pool"] += 1
            return None
        slots, bts, qs, bs = ix
        i = int(np.searchsorted(slots, int(sd), side="left"))
        if i >= len(slots):
            self.c["none_after_sd"] += 1
            return None
        if horizon is not None and bts[i] >= horizon:
            self.c["past_horizon"] += 1
            return None
        qv, bv = float(qs[i]), float(bs[i])
        if not (math.isfinite(qv) and math.isfinite(bv)):
            self.c["invalid_reserves"] += 1                 # the engine would mark the pool bad at that print; the decision stays live
            return None
        self.c["hit"] += 1
        return qv, bv


class StateModeEngine(FeatureEngine):
    """tools.c1nf_features.FeatureEngine with the shadow's build_engine settings, plus the state-mode switch and a stage-1 recorder.
    live: features_at and alive_pools are the base methods, unchanged. exact: ExactShadow sets `only_pools` (alive_pools restricted to a
    subset, in the base order) and `inject` ({pool: next_state} for the deferred decision it is resolving); nothing else differs, except
    that exact mode keeps EXACT_LEDGER_KEEP_DAYS as-of snapshots (the same values)."""

    def __init__(self, ledger: Any = None, *, v_source: str, state_mode: str, lookahead: Optional[Lookahead] = None, record: bool = True) -> None:
        if state_mode not in STATE_MODES:
            raise ValueError(f"state_mode must be one of {STATE_MODES}, got {state_mode!r}")
        if state_mode == STATE_EXACT and lookahead is None:
            raise ValueError("state_mode 'exact' needs a look-ahead index")
        if state_mode == STATE_EXACT and v_source != cs.V_SOURCE_REPLAY:
            raise ValueError("state_mode 'exact' is defined for the exploration replay (v_source 'const') only")
        keep = EXACT_LEDGER_KEEP_DAYS if state_mode == STATE_EXACT else cs.LIVE_LEDGER_KEEP_DAYS
        super().__init__(ledger=ledger, v_source=v_source, ledger_keep_days=keep, print_window=cs.LIVE_PRINT_WINDOW)
        self.state_mode, self.lookahead, self.record = state_mode, lookahead, record
        self.stage1_rows: list[tuple[str, Optional[str], int, int, float, np.ndarray]] = []
        self.exact_injected = 0
        self.only_pools: Optional[Sequence[str]] = None
        self.inject: Optional[dict[str, tuple[float, float]]] = None

    def alive_pools(self, T: int) -> list[str]:
        base = super().alive_pools(T)
        if self.only_pools is None:
            return base
        keep = set(self.only_pools)
        return [p for p in base if p in keep]

    def features_at(self, pool: str, T: int, sd: Optional[int] = None, next_state: Optional[tuple] = None) -> Any:
        if next_state is None and self.inject is not None:
            next_state = self.inject.get(pool)
            if next_state is not None:
                self.exact_injected += 1
        res = super().features_at(pool, T, sd, next_state=next_state) if next_state is not None else super().features_at(pool, T, sd)
        if self.record and res is not None and res.stage1 and res.wallet_ok is not False:
            self.stage1_rows.append((res.pool, getattr(res, "mint", None), int(res.t), int(res.sd), float(res.h_top1),
                                     np.asarray(res.vec, dtype=np.float32).copy()))
        return res


class ExactShadow(cs.Shadow):
    """Run A's evaluation point (module docstring): a decision whose pool prints again (slot >= SD, before the horizon, valid state) is
    deferred to that print and resolved through Shadow._decide itself, so the decision code is the shadow's, byte for byte."""

    def __init__(self, engine: Any, *a: Any, **kw: Any) -> None:
        if getattr(engine, "state_mode", None) != STATE_EXACT:
            raise ValueError("ExactShadow needs a StateModeEngine in state mode 'exact'")
        super().__init__(engine, *a, **kw)
        self.lookahead: Lookahead = engine.lookahead
        self.deferred: dict[str, list[tuple[int, int, int, tuple[float, float]]]] = {}
        self.xc: collections.Counter = collections.Counter()
        self._clock_ms_override: Optional[int] = None

    def clock_ms(self) -> int:
        return self._clock_ms_override if self._clock_ms_override is not None else super().clock_ms()

    def _decide(self, T: int, sd: int, bt: int) -> None:
        eng = self.engine
        now: list[str] = []
        for pool in eng.alive_pools(T):
            P = eng._pools.get(pool)
            ns = self.lookahead.next_state(pool, int(sd), horizon_bt(P.g0)) if P is not None else None
            if ns is not None and not (ns[0] + P.V > 0 and ns[1] > 0):   # features_at would answer None there; #506 keeps the live row
                self.xc["invalid_next_state_live"] += 1
                ns = None
            if ns is None:
                now.append(pool)
            else:
                self.deferred.setdefault(pool, []).append((int(T), int(sd), int(bt), ns))
                self.xc["deferred"] += 1
        self.xc["decided_at_sd"] += len(now)
        eng.only_pools = now
        try:
            super()._decide(T, sd, bt)
        finally:
            eng.only_pools = None

    def _on_trade(self, row: Mapping[str, Any], slot: int, bt: int) -> None:
        if row.get("venue") == "pumpswap":
            pool = row.get("pool")
            if pool is not None and self.deferred.get(pool):
                self._resolve_deferred(pool, row, slot)
        super()._on_trade(row, slot, bt)

    def _resolve_deferred(self, pool: str, row: Mapping[str, Any], slot: int) -> None:
        """#506 (tools/c1nf_parity.py:225-230): every pending decision of the pool, at its next PumpSwap print, before on_trade, with the
        row's own (quote_reserve, base_reserve). The row must be the print the look-ahead named (counted; the run exits 1 if not)."""
        keep: list[tuple[int, int, int, tuple[float, float]]] = []
        eng = self.engine
        for T, sd, bt, ns in self.deferred.pop(pool):
            if slot < sd:                                     # cannot happen in slot order; kept for the print it was deferred to
                keep.append((T, sd, bt, ns))
                continue
            q, b = _num(row.get("quote_reserve")), _num(row.get("base_reserve"))
            if q is None or b is None or (q, b) != (float(ns[0]), float(ns[1])):
                self.xc["row_state_mismatch"] += 1
                if q is None or b is None:
                    continue
            minutes = self.c["minutes"]
            eng.only_pools, eng.inject, self._clock_ms_override = [pool], {pool: (q, b)}, int(bt) * 1000
            try:
                cs.Shadow._decide(self, T, sd, bt)
            finally:
                eng.only_pools, eng.inject, self._clock_ms_override = None, None, None
            self.c["minutes"] = minutes                       # the minute was counted once, at T
            self.xc["resolved"] += 1
        if keep:
            self.deferred[pool] = keep

    def _resolve(self, pend: Any, final: bool) -> None:
        pend.done = True                                      # exact mode prices no outcome (module docstring)
        self.xc["outcomes_not_priced"] += 1

    def finish(self, reason: str = "end") -> None:
        self.xc["unresolved"] = sum(len(v) for v in self.deferred.values())
        super().finish(reason)

    def exact_report(self) -> dict:
        r: dict[str, Any] = {k: int(self.xc.get(k, 0)) for k in ("deferred", "resolved", "unresolved", "decided_at_sd", "invalid_next_state_live",
                                                                "row_state_mismatch", "outcomes_not_priced")}
        r["ok"] = r["unresolved"] == 0 and r["row_state_mismatch"] == 0 and r["deferred"] == r["resolved"]
        return r


def run_replay_mode(args: argparse.Namespace, models: Any, engine: StateModeEngine, sink: Any) -> tuple[int, Optional[ExactShadow]]:
    """tools.c1nf_shadow.run_replay for both modes (the same tape rows, Shadow arguments and finish). Exact mode builds ExactShadow in place
    of Shadow for this one call (run_replay constructs `Shadow` by its module-global name); live mode is cs.run_replay, untouched."""
    if engine.state_mode != STATE_EXACT:
        return cs.run_replay(args, models, engine, sink), None
    made: list[ExactShadow] = []
    orig = cs.Shadow

    def factory(*a: Any, **kw: Any) -> ExactShadow:
        sh = ExactShadow(*a, **kw)
        made.append(sh)
        return sh

    cs.Shadow = factory  # type: ignore[assignment,misc]
    try:
        rc = cs.run_replay(args, models, engine, sink)
    finally:
        cs.Shadow = orig  # type: ignore[misc]
    if len(made) != 1:
        raise RuntimeError(f"run_replay built {len(made)} shadows, expected 1")
    return rc, made[0]


# ---- P3's list --------------------------------------------------------------------------------------------------------------------------
def p3_line(p: Mapping[str, Any]) -> str:
    return f"{p['mint']},{int(p['decision_T_ms'])},{int(p['SD_slot'])}"


def p3_md5(picks: Iterable[Mapping[str, Any]]) -> dict:
    """QP-1010 P3: L = lines mint,decision_T_ms,SD_slot (decimal), sorted, newline-terminated; md5 per UTC day of decision_T and overall."""
    lines = sorted(p3_line(p) for p in picks)
    days: dict[str, list[str]] = {}
    for ln in lines:
        days.setdefault(utc_day(int(ln.split(",")[1]) // 1000), []).append(ln)
    md5 = lambda ls: hashlib.md5("".join(x + "\n" for x in ls).encode()).hexdigest()  # noqa: E731
    return {"n": len(lines), "md5": md5(lines), "days": {d: {"n": len(v), "md5": md5(v)} for d, v in sorted(days.items())}}


def read_picks(out_dir: str | Path) -> list[dict]:
    out = []
    for f in sorted(Path(out_dir).glob("c1nf-*picks-*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("type") == "c1nf_pick":
                    out.append(r)
    return out


# ---- the cross-harness check (QP on ab1df5f: before run A) -------------------------------------------------------------------------------
WALLET_PREFIXES = ("wb5_", "ws5_", "wb15_", "ws15_", "sk_net")


def wallet_feature(name: str) -> bool:
    return name.startswith(WALLET_PREFIXES)


def cross_compare(replay506: Mapping[str, Any], stage1_594: Mapping[str, Any], *, skip_wallet: bool = False) -> dict:
    """#506's `replay --day D` npz (exact_pool, exact_T, exact_stage1, exact_vec) against this harness's parity-stage1-exact.npz (pool, T,
    vec float32) on the shared stage-1 (pool, T) rows: float32 vectors, NaN equal to NaN, the differing-row count per feature name.
    skip_wallet: compare only the 65 non-wallet columns (when the two runs did not read the same as-of ledger). Opens no outcome."""
    from tools.c1nf_features import FEATURE_NAMES

    a = {(str(p), int(t)): i for i, (p, t, s1) in enumerate(zip(replay506["exact_pool"], replay506["exact_T"], replay506["exact_stage1"])) if s1}
    b = {(str(p), int(t)): i for i, (p, t) in enumerate(zip(stage1_594["pool"], stage1_594["T"]))}
    shared = sorted(set(a) & set(b))
    cols = [j for j, n in enumerate(FEATURE_NAMES) if not (skip_wallet and wallet_feature(n))]
    A = np.asarray(replay506["exact_vec"], dtype=np.float64)[[a[k] for k in shared]][:, cols].astype(np.float32) if shared else np.zeros((0, len(cols)), np.float32)
    B = np.asarray(stage1_594["vec"], dtype=np.float32)[[b[k] for k in shared]][:, cols] if shared else np.zeros((0, len(cols)), np.float32)
    diff = ~((A == B) | (np.isnan(A) & np.isnan(B)))
    by = {FEATURE_NAMES[j]: int(diff[:, i].sum()) for i, j in enumerate(cols)}
    return {"shared_rows": len(shared), "only_506": len(set(a) - set(b)), "only_594": len(set(b) - set(a)), "columns": len(cols),
            "rows_differing": int(diff.any(axis=1).sum()), "by_feature": {n: c for n, c in by.items() if c}}


# ---- inputs -----------------------------------------------------------------------------------------------------------------------------
def pool_v_from_tokens(tokens: str) -> dict[str, float]:
    """The same pool -> V0 map tools.c1nf_shadow.run_replay builds (V band applied)."""
    import pandas as pd  # lazy: the audit venv

    tk = pd.read_parquet(tokens, columns=["mint", "pool", "v0_lamports"])
    tk = tk[(tk.v0_lamports >= cs.V_LO) & (tk.v0_lamports <= cs.V_HI)]
    return dict(zip(tk.pool, tk.v0_lamports.astype(float)))


def lookahead_from_tape(tape_dir: str, hours: Sequence[str], pool_v: Mapping[str, float]) -> Lookahead:
    import pandas as pd  # lazy: the audit venv

    la = Lookahead(pool_v)
    cols = ["venue", "pool", "slot", "tx_index", "event_index", "block_time", "quote_reserve", "base_reserve"]
    for h in hours:
        t = pd.read_parquet(f"{tape_dir}/trades/{h}.parquet", columns=cols)
        t = t[(t.venue == "pumpswap") & t.pool.isin(pool_v.keys())]
        la.add_columns(*[t[c].astype(object).where(t[c].notna(), None).tolist() for c in cols])
    return la.freeze()


def sha256_file(path: str | Path) -> str:
    return cs.sha256_file(path)


def git_blob(path: str | Path) -> Optional[str]:
    data = Path(path).read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def vm_hwm_kb() -> Optional[int]:
    return cs.proc_mem_kb().get("VmHWM")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="C1-NF parity replay with a state-mode switch (DEC-026 items 13 and 14a). Exploration tape only.")
    p.add_argument("--state-mode", required=True, choices=STATE_MODES)
    p.add_argument("--replay-from", required=True, help="first exploration-tape hour YYYY-MM-DDTHH (the segment start)")
    p.add_argument("--replay-hours", type=int, required=True)
    p.add_argument("--replay-tape", default=cs.TAPE_DIR)
    p.add_argument("--tokens", default=cs.TOKENS_PARQUET)
    p.add_argument("--model", help="LightGBM model file (with --model-sha256)")
    p.add_argument("--model-sha256")
    p.add_argument("--model-manifest", help='JSON {"models":[{"from_day","file","sha256"}]} (item 13: 21 walk-forward files)')
    p.add_argument("--ledger-root", help="tools/c1nf_wallet_ledger.py output root (asof/asof-<day>)")
    p.add_argument("--decide-from")
    p.add_argument("--decide-to")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--probe-lookahead", action="store_true", help="build the look-ahead index only, report its size and VmHWM, and exit")
    return p


def main_cross(argv: Sequence[str]) -> int:
    p = argparse.ArgumentParser(prog="c1nf_parity_exact.py cross-compare")
    p.add_argument("replay506", help="tools/c1nf_parity.py replay --day D output (npz)")
    p.add_argument("stage1_594", help="this harness's parity-stage1-exact.npz")
    p.add_argument("--skip-wallet", action="store_true", help="the 65 non-wallet columns only")
    p.add_argument("--json")
    a = p.parse_args(argv)
    out = cross_compare(np.load(a.replay506, allow_pickle=False), np.load(a.stage1_594, allow_pickle=False), skip_wallet=a.skip_wallet)
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(json.dumps(out, sort_keys=True))
    return cs.EXIT_OK if out["rows_differing"] == 0 and out["shared_rows"] > 0 else EXIT_EXACT_CHECK


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["cross-compare"]:
        return main_cross(argv[1:])
    args = build_parser().parse_args(argv)
    hours = cs.replay_hours(args.replay_from, args.replay_hours)
    try:
        cs.refuse_replay([args.replay_tape, args.out_dir, args.tokens] + [x for x in (args.model, args.model_manifest, args.ledger_root) if x], hours)
    except cs.Refused as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return cs.EXIT_REFUSED
    out = Path(args.out_dir)
    if out.exists() and any(out.iterdir()):
        print(f"refusing: {out} is not empty (one run per out dir; the first complete run is the one reported)", file=sys.stderr)
        return cs.EXIT_REFUSED
    if not args.probe_lookahead and not (args.model_manifest or (args.model and args.model_sha256)):
        print("refusing: --model with --model-sha256, or --model-manifest, is required", file=sys.stderr)
        return cs.EXIT_USAGE
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    la = None
    if args.state_mode == STATE_EXACT or args.probe_lookahead:
        la = lookahead_from_tape(args.replay_tape, hours, pool_v_from_tokens(args.tokens))
        probe = {"rows": la.rows, "pools": len(la._idx), "index_bytes": la.nbytes(), "seconds": round(time.time() - t0, 1), "VmHWM_kb": vm_hwm_kb(),
                 "hours": [hours[0], hours[-1]]}
        print("lookahead " + json.dumps(probe), flush=True)
        if args.probe_lookahead:
            (out / "parity-lookahead-probe.json").write_text(json.dumps(probe, indent=1) + "\n")
            return cs.EXIT_OK
    models = cs.ModelSet.from_manifest(args.model_manifest) if args.model_manifest else cs.ModelSet.single(args.model, args.model_sha256)
    ledger = cs.AsofDirLedger(args.ledger_root) if args.ledger_root else None
    engine = StateModeEngine(ledger, v_source=cs.V_SOURCE_REPLAY, state_mode=args.state_mode, lookahead=la)
    sink = cs.JsonlSink(out)
    try:
        rc, xsh = run_replay_mode(args, models, engine, sink)
    finally:
        sink.close()
    exact = xsh.exact_report() if xsh is not None else None
    if exact is not None and not exact["ok"] and rc == cs.EXIT_OK:
        rc = EXIT_EXACT_CHECK
    rows = engine.stage1_rows
    np.savez_compressed(out / f"parity-stage1-{args.state_mode}.npz", pool=np.array([r[0] for r in rows]), mint=np.array([r[1] or "" for r in rows]),
                        T=np.array([r[2] for r in rows], dtype=np.int64), sd=np.array([r[3] for r in rows], dtype=np.int64),
                        h_top1=np.array([r[4] for r in rows], dtype=np.float64),
                        vec=np.stack([r[5] for r in rows]) if rows else np.zeros((0, cs.N_FEATURES), dtype=np.float32))
    here = Path(__file__).resolve()
    tools_dir = here.parent
    summary = {
        "state_mode": args.state_mode, "hours": [hours[0], hours[-1]], "replay_hours": args.replay_hours, "decide_from": args.decide_from,
        "decide_to": args.decide_to, "exit": rc, "seconds": round(time.time() - t0, 1), "VmHWM_kb": vm_hwm_kb(),
        "blobs": {n: git_blob(tools_dir / n) for n in ("c1nf_shadow.py", "c1nf_features.py", "c1nf_parity_exact.py")},
        "sha256": {"tokens": sha256_file(args.tokens), "model_manifest": sha256_file(args.model_manifest) if args.model_manifest else None,
                   "models": models.shas},
        "lookahead": None if la is None else {"rows": la.rows, "pools": len(la._idx), "index_bytes": la.nbytes(), **la.c},
        "exact_injected": engine.exact_injected, "exact": exact, "stage1_rows": len(rows), "engine_health": cs.clean(engine.health()),
        "p3": p3_md5(read_picks(out)),
    }
    try:
        summary["git_head"] = subprocess.run(["git", "-C", str(tools_dir), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        summary["git_head"] = None
    (out / f"parity-summary-{args.state_mode}.json").write_text(json.dumps(cs.clean(summary), indent=1, sort_keys=True) + "\n")
    print(f"done: mode {args.state_mode} picks {summary['p3']['n']} md5 {summary['p3']['md5']} stage1 {len(rows)} "
          f"exact_injected {engine.exact_injected} exact {json.dumps(exact)} {summary['seconds']}s VmHWM {summary['VmHWM_kb']} kB", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
