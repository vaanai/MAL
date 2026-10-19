"""Outcome-blind extraction parity of the H5 strip states (quant-proof condition on PR #719, comment 6246275907; DEC-031 s1.4).

QUESTION. tools/arena_h5_score.py prices a trade from the shadow's `strip` record. Its `Strip.state_at` takes "the pre-trade state of the first print in a slot later than `slot`,
else the state after the last print" (the END-bound rule of r4b_book.mint_states / sc.py). Does that selection, on the real tape, give the same (Q incl. V, base, ssb, nearby, spot)
at the landing and exit slots as an INDEPENDENT extraction?

TWO SIDES.
  scorer side  : tools.h5_shadow.Engine replayed over the pinned exploration hours exactly as tools/fixtures/arena_h5/build_parity.py does (the pv trigger / outcome / strip / bx10
                 records), then tools.arena_h5_score.price_trade and Strip.state_at / pressure_at / decision_spot, the scorer's own functions, for both bots, both legs, and a decision-age
                 grid (0 to 3,500 ms, the scorer's own cap), so every landing and exit slot the scorer can produce is a candidate. A dense sweep of EVERY slot X from the strip's first
                 slot to its last slot + 3 covers any slot a landing or an exit could take.
  independent  : the pool's prints read straight from the pinned trades parquet (pandas / numpy; the l3 reader's order: slot, tx_index with NaN as -1, event_index), V from the pump
                 V map, then TWO readers: tools.r4b_book.mint_states (the stack's own extraction) and tools.arena_parity.L3Mint (the l3-simlib reader of PR #708). Both imports live
                 INSIDE this test file's functions: DEC-031 s3.1 forbids arena PROCESSES from importing r4b / l3 / laya code; a test is not one.
A third comparison: the shadow's OWN outcome-record states at its recorded landing / exit slots (state fields only: q_sol, base, ssb, nb_lamports, landing_slot; the outcome record's
`net` P&L block is never read) against Strip.state_at.

OUTCOME-BLIND. No P&L, no fill, no exit outcome, no ranking is computed or printed. The outcome and bx10 records are passed to price_trade reduced to their timing fields (the exit
trigger slot, sps, s0, the BOOST slices). Output is counts, field names and state values at a slot.

DATA. Only the pinned exploration hours of ARTIFACTS/lab/arena-records-2026-10-18-parity-sources.txt (the `h5` lines, sha256-checked first), plus the two exploration inputs the pinned
parity set was built with and the shadow's replay needs: tools.h5_shadow.WORK_DIR meta/ and paths/ for 2026-09-20 and 2026-09-21 (pool s0, V, mint; per-pool sps) and the pump V map. Their
sha256 are in the report. No October, forward, sealed or arena output is opened.

RUN.  MAL_H5_PARITY_RUN=1 systemd-run --user --scope -p MemoryMax=12G -p MemorySwapMax=0 /data/mal/audit-1008/venv/bin/python -m tools.test_arena_h5_parity [--json OUT]
      (about 3 minutes, one replay). Without MAL_H5_PARITY_RUN the real-tape test is skipped; the synthetic tests need only numpy.
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python tools/test_arena_h5_parity.py`
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import collections
import hashlib
import json
import math
import os
import sys
import unittest
from pathlib import Path
from typing import Any, Sequence

REPO = Path(__file__).resolve().parents[1]
SOURCES_LIST = REPO / "ARTIFACTS" / "lab" / "arena-records-2026-10-18-parity-sources.txt"
FIXTURE_DIR = REPO / "tools" / "fixtures" / "arena_h5"
TAPE_DIR = "/data/mal/audit-1008/tape"
TAPE_PREFIX = TAPE_DIR + "/"
VMAP_PATH = "/data/mal/pumpswap-virtual/pool_v_0909.json"
HOURS = [f"2026-09-20T{h:02d}" for h in range(24)] + ["2026-09-21T00"]  # PARITY.json "hours" (2026-09-20T00 through 2026-09-21T00)
DAYS = ("2026-09-20", "2026-09-21")
AGES_MS = tuple(sorted(set(range(0, 3501, 100)) | {1200, 3500}))  # the committed fixture's 0 and 1,200 ms, and the scorer's 3,500 ms age cap
SWEEP_AFTER_LAST = 3  # the dense sweep runs to the strip's last slot + this
RUN_ENV = "MAL_H5_PARITY_RUN"
CONVENTION_REL_BOUND = 1e-3  # a sanity bound on the convention-derived quantities (decision spot, state after the last print); the size actually seen is in the report
STRIP_Q_TOL = 0.5  # the strip stores int(round(q)); the shadow's own outcome record keeps the float (used only for that comparison)
MAX_EXAMPLES = 6
FAMILY = "h5"  # the label of the pinned list's lines for this parity


# ---------------------------------------------------------------------------------------------------------------- pins
def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pinned_sources(family: str = FAMILY, path: Path = SOURCES_LIST) -> list[tuple[str, str]]:
    """[(sha256, path)] of the list's lines for one family (`h5` or `engine`): `<family> <sha256> <path>`."""
    out = []
    for ln in path.read_text().splitlines():
        parts = ln.split()
        if len(parts) == 3 and parts[0] == family:
            out.append((parts[1], parts[2]))
    return out


def verify_pins(entries: Sequence[tuple[str, str]]) -> dict[str, Any]:
    """sha256 every pinned file against the list. Returns counts; a missing or differing file is listed (path only)."""
    bad, missing = [], []
    for want, p in entries:
        if not os.path.exists(p):
            missing.append(p)
        elif sha256_file(p) != want:
            bad.append(p)
    return {"files": len(entries), "ok": len(entries) - len(bad) - len(missing), "mismatch": bad, "missing": missing}


# ---------------------------------------------------------------------------------------------------------------- tally
class Tally:
    """Counts of comparisons and mismatches by key, with a few examples (slot, field, both values) per key."""

    def __init__(self) -> None:
        self.n: collections.Counter = collections.Counter()
        self.bad: collections.Counter = collections.Counter()
        self.examples: dict[str, list[dict]] = collections.defaultdict(list)

    @staticmethod
    def _same(a: Any, b: Any, tol: float) -> bool:
        if isinstance(a, str) or isinstance(b, str) or a is None or b is None:
            return a == b
        fa, fb = float(a), float(b)
        if math.isnan(fa) or math.isnan(fb):
            return math.isnan(fa) and math.isnan(fb)
        return abs(fa - fb) <= tol

    def cmp(self, key: str, a: Any, b: Any, ctx: dict, tol: float = 0.0) -> bool:
        self.n[key] += 1
        ok = self._same(a, b, tol)
        if not ok:
            self.bad[key] += 1
            if len(self.examples[key]) < MAX_EXAMPLES:
                self.examples[key].append({**ctx, "scorer_or_left": a, "other_or_right": b})
        return ok

    def table(self) -> dict[str, dict[str, int]]:
        return {k: {"compared": self.n[k], "mismatches": self.bad[k]} for k in sorted(self.n)}

    def total(self) -> tuple[int, int]:
        return sum(self.n.values()), sum(self.bad.values())


class RelDev:
    """Relative deviations of a quantity that the two sides derive by DIFFERENT conventions (not selected from the tape): reported with their size, never silently allowed."""

    def __init__(self) -> None:
        self.v: dict[str, list[float]] = collections.defaultdict(list)

    def add(self, key: str, a: float, b: float) -> None:
        self.v[key].append(abs(float(a) - float(b)) / max(abs(float(b)), 1e-300))

    def table(self) -> dict[str, dict[str, Any]]:
        out = {}
        for k, xs in sorted(self.v.items()):
            ys = sorted(xs)
            out[k] = {"n": len(ys), "n_nonzero": sum(1 for x in ys if x > 0), "max_rel": ys[-1] if ys else None, "median_rel": ys[len(ys) // 2] if ys else None,
                      "p90_rel": ys[int(0.9 * (len(ys) - 1))] if ys else None}
        return out


# ---------------------------------------------------------------------------------------------------------------- the independent reader (numpy; test-only imports)
def _readers():
    """(r4b_book, L3Mint): imported here, inside the test, never at module level of anything an arena process loads."""
    from tools import arena_parity as ap
    from tools import r4b_book as bk

    return bk, ap.L3Mint


def load_tape_prints(tape: str, hours: Sequence[str], pools: set[str]):
    """Every PumpSwap print of `pools` in the pinned trades files, as one DataFrame in file order (hour, then row)."""
    import numpy as np
    import pandas as pd

    cols = ["venue", "pool", "slot", "tx_index", "event_index", "side", "sol_lamports", "token_raw", "quote_reserve", "base_reserve"]
    parts = []
    for hi, h in enumerate(hours):
        t = pd.read_parquet(f"{tape}/trades/{h}.parquet", columns=cols)
        t = t[(t.venue == "pumpswap") & t.pool.isin(pools)].copy()
        t["file_ord"] = hi * 10**9 + np.arange(len(t))  # hour order, then row order in the hour: the ties' last key
        parts.append(t)
    return pd.concat(parts, ignore_index=True)


def pool_arrays(df, pool: str, order: str = "l3") -> dict[str, Any]:
    """One pool's prints, ordered. order "l3": the l3 reader's (slot, tx_index NaN as -1, event_index), file order breaking ties. order "feed": per hour (slot, tx_index NaN last,
    event_index), hours in order: how tools.h5_shadow.replay_rows feeds the shadow (a diagnostic only, never the comparison's reference)."""
    import numpy as np

    sub = df[df.pool == pool]
    if order == "l3":
        ix = np.lexsort((sub.file_ord.values, sub.event_index.values, sub.tx_index.fillna(-1).values, sub.slot.values))
    elif order == "feed":
        hour = sub.file_ord.values // 10**9
        ix = np.lexsort((sub.file_ord.values, sub.event_index.values, sub.tx_index.fillna(1e18).values, sub.slot.values, hour))
    else:
        raise ValueError(order)
    s = sub.iloc[ix]
    return {"sl": s.slot.values.astype(np.int64), "ib": (s.side.values == "buy"), "so": s.sol_lamports.values.astype(float), "tk": s.token_raw.values.astype(float),
            "q": s.quote_reserve.values.astype(float), "b": s.base_reserve.values.astype(float), "tx_nan": int(s.tx_index.isna().sum())}


class PoolRef:
    """The two independent readers on one pool's prints (cut at `last_slot`, the last print slot the shadow tracked), V from the V map."""

    def __init__(self, arr: dict[str, Any], vq: float, sps: float, last_slot: int) -> None:
        import numpy as np

        bk, L3Mint = _readers()
        self.bk, self.np = bk, np
        keep = arr["sl"] <= last_slot
        self.n_beyond = int((~keep).sum())  # tape prints after the shadow's last tracked slot (informational)
        self.sl, self.ib, self.so, self.tk, self.q, self.b = (arr[k][keep] for k in ("sl", "ib", "so", "tk", "q", "b"))
        self.n = len(self.sl)
        self.vq, self.sps, self.last_slot = float(vq), float(sps), int(last_slot)
        self.l3 = L3Mint(self.sl, self.ib, self.so, self.tk, self.q, self.b, np.full(self.n, self.vq))

    def r4b_at(self, xs: Sequence[int]) -> dict[str, Any]:
        """mint_states at landing slot X = sd (latency 0): Qe, Be, ssb, nearby at every X in `xs`."""
        o = self.bk.mint_states(self.sl, self.ib, self.so, self.tk, self.q, self.b, self.vq, self.np.asarray(xs, dtype=self.np.int64), self.sps, lats=(0.0,), rounding="ceil")
        return {"Q": o["Qe"][0.0], "B": o["Be"][0.0], "ssb": o["ssb"][0.0], "nb": o["nearby"][0.0]}

    def src_at(self, xs: Sequence[int]) -> list[str]:
        je = self.np.searchsorted(self.sl, self.np.asarray(xs) + 1, "left")
        return ["next_pre" if j < self.n else "post" for j in je]

    def post_last(self) -> tuple[float, float]:
        return float(self.l3.QX[self.n]), float(self.l3.BX[self.n])


# ---------------------------------------------------------------------------------------------------------------- the scorer side
def reduced_outcome(o: dict) -> dict:
    """The outcome record cut to the timing fields price_trade reads (no per-leg net, no ladder): the check never carries a P&L field."""
    return {"complete": bool(o.get("complete")), "legs": {"timing_only": True}, "gap": False, "sps": float(o["sps"]), "s0": int(o["s0"]), "trigger_slot": int(o["trigger_slot"]),
            "exit": {"trigger_slot": int(o["exit"]["trigger_slot"])}}


def reduced_bx(bx: dict | None) -> dict | None:
    if bx is None or "error" in bx or "slices" not in bx or "legs" not in bx:
        return bx if bx is None else {"error": "reduced"}
    return {"slices": bx["slices"], "legs": {}}


def replay_records(tape: str, hours: Sequence[str]) -> tuple[list[dict], dict, dict]:
    """The shadow engine in fresh-forward configuration over the pinned hours: the same call as tools/fixtures/arena_h5/build_parity.replay_records."""
    from tools import h5_shadow as h5

    rows, meta, sps_pool = h5.replay_rows(tape, h5.WORK_DIR, list(hours))
    recs: list[dict] = []
    eng = h5.Engine(recs.append, boost_mode="auto", sps_fn=lambda p: sps_pool.get(p.pool), classifier=h5.PreEventClassifier(), bx10_enabled=True, fresh_start_ms=0, seal_start_ms=None,
                    h5_look2_start_ms=None)
    h5.replay(rows, meta, eng)
    return recs, meta, sps_pool


def scorer_cases(recs: Sequence[dict], excl: collections.Counter) -> list[dict]:
    """Every (pool, bot, decision age, leg) the scorer prices on the replay, from tools.arena_h5_score.price_trade. State, slot and pressure fields only."""
    from tools import arena_h5_score as sc

    trig = {r["pool"]: r for r in recs if r["type"] == "trigger" and r["variant"] == "pv"}
    out_ = {r["pool"]: r for r in recs if r["type"] == "outcome" and r["variant"] == "pv"}
    bxs = {r["pool"]: r for r in recs if r["type"] == "outcome_bx10" and r["variant"] == "pv"}
    strips = {r["pool"]: r for r in recs if r["type"] == "strip"}
    cases: list[dict] = []
    for pool in sorted(out_):
        o, t = reduced_outcome(out_[pool]), trig[pool]
        if not t.get("block_time"):
            excl["no_block_time"] += 1
            continue
        for bot in sc.BOTS:
            for age in AGES_MS:
                tt = {**t, "t_detect_ms": int(t["block_time"]) * 1000 + age, "gap": False, "base_breaks_unresolved_settled": 0}
                try:
                    priced = sc.price_trade(bot, o, tt, strips.get(pool), reduced_bx(bxs.get(pool)), None, {})
                except sc.Excluded as e:
                    excl[e.reason] += 1
                    continue
                for shadow_leg, sfx, lat in sc.LEGS:
                    lg = priced["legs"][shadow_leg]
                    if not lg["ok"]:
                        excl["leg_exit_not_after_landing"] += 1
                        continue
                    cases.append({"pool": pool, "bot": bot, "age_ms": age, "leg": sfx, "lat_s": lat, "tslot": int(out_[pool]["trigger_slot"]), "sps": float(o["sps"]),
                                  "xtrig": lg["exit_trigger"], "land": lg["landing"], "xland": lg["exit_landing"], "qe": lg["qe"], "be": lg["be"], "qx": lg["qx"], "bx": lg["bx"],
                                  "ssb": lg["ssb"], "nb": lg["nb"], "src": lg["state_src"], "spot": priced["spot"], "el": priced["el"]})
    return cases


# ---------------------------------------------------------------------------------------------------------------- the comparisons
class Acc:
    """What one run accumulates: exact-equality tallies, relative deviations of the convention-derived quantities, and diagnostics (counts)."""

    def __init__(self) -> None:
        self.T = Tally()
        self.R = RelDev()
        self.D: collections.Counter = collections.Counter()
        self.margin: dict[str, int | None] = {"min_slots_from_landing_to_strip_end": None, "min_slots_from_exit_landing_to_strip_end": None}

    def margin_min(self, key: str, v: int) -> None:
        cur = self.margin[key]
        self.margin[key] = v if cur is None else min(cur, v)


def _src_pair(c: dict) -> tuple[str, str]:
    s = c["src"]
    return (s[0], s[1]) if isinstance(s, (tuple, list)) else (s, s)


def compare_pool(pool: str, rec: dict[str, Any], ref: PoolRef, cases: Sequence[dict], A: Acc, v_meta: int | None, v_map: int | None) -> None:
    """All comparisons for one pool. `rec` holds the pool's trigger, outcome (state fields only) and strip. State keys carry the state source (`next_pre`: a recorded pre-trade
    reserve; `post`: the derived state after the last print, where the shadow and the stack use different fee conventions)."""
    from tools import arena_h5_score as sc

    T, R, D = A.T, A.R, A.D
    strip_rec, trig, out = rec["strip"], rec["trigger"], rec["outcome"]
    strip = sc.Strip(strip_rec)
    sps = ref.sps
    ctx0 = {"pool": pool}
    last_slot = int(strip_rec["post_last"][0])

    # -- V and the print list
    T.cmp("inputs|V (shadow meta vs V map)", v_meta, v_map, ctx0)
    T.cmp("strip|n_prints (shadow count vs tape prints up to its last slot)", strip_rec["n_prints"], ref.n, ctx0)
    start = strip_rec["from_slot"]
    ix0 = int(ref.np.searchsorted(ref.sl, start, "left"))
    for j, (slot, qpv, _qfv, base) in enumerate(strip_rec["rows"]):
        i = ix0 + j
        ok = i < ref.n
        T.cmp("strip rows|slot", slot, int(ref.sl[i]) if ok else None, {**ctx0, "row": j})
        T.cmp("strip rows|Q incl. V (pre-trade)", qpv, float(ref.l3.QX[i]) if ok else None, {**ctx0, "row": j, "slot": slot})
        T.cmp("strip rows|base (pre-trade)", base, float(ref.l3.BX[i]) if ok else None, {**ctx0, "row": j, "slot": slot})
    T.cmp("strip rows|count (rows from the first trigger slot)", len(strip_rec["rows"]), ref.n - ix0, ctx0)
    pq, pb = ref.post_last()
    T.cmp("post_last|slot", strip_rec["post_last"][0], int(ref.sl[-1]), ctx0)
    T.cmp("post_last|base (derived after the last print)", strip_rec["post_last"][3], pb, ctx0)
    R.add("post_last Q incl. V (shadow strip vs stack, derived after the last print)", strip_rec["post_last"][1], pq)  # a convention: see the module doc and the report

    # -- spot at the decision: the post-trade price right after the trigger print (a convention: the shadow's pool arithmetic vs the stack's 0.99 / 1.01 seed price)
    tslot = int(trig["slot"])
    cand = ref.np.flatnonzero((ref.sl == tslot) & (~ref.ib) & (ref.b == float(trig["base_pre"])) & (ref.tk == float(trig["sell_token_raw"])))
    T.cmp("spot|trigger print located once on the tape (slot, sell, base_pre, token_raw)", 1, len(cand), ctx0)
    if len(cand) >= 1:
        R.add("spot (scorer decision_spot vs L3Mint post-trade price of the trigger print)", sc.decision_spot(trig), float(ref.l3.pp[int(cand[0])]))

    # -- case-based: the scorer's own price_trade outputs at every age, leg and bot
    seen_e: set[tuple[int, str]] = set()
    lat_effs = sorted({round(c["lat_s"] + c["age_ms"] / 1000.0, 9) for c in cases})
    bk = ref.bk
    mint_lands = bk.mint_states(ref.sl, ref.ib, ref.so, ref.tk, ref.q, ref.b, ref.vq, ref.np.asarray([tslot]), sps, lats=tuple(lat_effs), rounding="ceil") if cases else None
    lag_ind = int(bk.exit_lag_vec(sps))
    for c in cases:
        le = round(c["lat_s"] + c["age_ms"] / 1000.0, 9)
        s_e, s_x = _src_pair(c)
        D[f"cases landing state_src={s_e}"] += 1
        D[f"cases exit state_src={s_x}"] += 1
        A.margin_min("min_slots_from_landing_to_strip_end", last_slot - c["land"])
        A.margin_min("min_slots_from_exit_landing_to_strip_end", last_slot - c["xland"])
        ctx = {**ctx0, "bot": c["bot"][:4], "age_ms": c["age_ms"], "leg": c["leg"]}
        T.cmp("case landing|slot (scorer landing_slot vs k_slots_vec)", c["land"], tslot + int(bk.k_slots_vec(le, sps, "ceil")), ctx)
        T.cmp("case exit|lag slots (scorer vs exit_lag_vec)", c["el"], lag_ind, ctx)
        T.cmp("case exit|landing slot (exit trigger + lag)", c["xland"], c["xtrig"] + lag_ind, ctx)
        seen_e.add((c["land"], c["leg"]))
        e_ctx, x_ctx = {**ctx, "slot": c["land"]}, {**ctx, "slot": c["xland"]}
        # entry via mint_states(sd = trigger slot, latency = leg + age) and via L3Mint.entry
        l3 = ref.l3.entry(tslot, le, sps)
        for fld, mine, a1, a2 in (("Q incl. V", c["qe"], mint_lands["Qe"][le][0], l3[0]), ("base", c["be"], mint_lands["Be"][le][0], l3[1]), ("ssb", c["ssb"], mint_lands["ssb"][le][0], l3[2]),
                                  ("nearby lamports", c["nb"], mint_lands["nearby"][le][0], l3[3])):
            T.cmp(f"case landing [{s_e}]|{fld} vs mint_states", mine, float(a1), e_ctx)
            T.cmp(f"case landing [{s_e}]|{fld} vs L3Mint", mine, float(a2), e_ctx)
        # exit via mint_states(clock_slot_marks = exit trigger slot) and L3Mint.exit_state
        xo = bk.mint_states(ref.sl, ref.ib, ref.so, ref.tk, ref.q, ref.b, ref.vq, ref.np.asarray([tslot]), sps, lats=(0.0,), rounding="ceil", t_marks=ref.np.asarray([0]),
                            slot_s_marks=ref.np.asarray([sps]), clock_slot_marks=ref.np.asarray([c["xtrig"]]))
        lx = ref.l3.exit_state(c["xtrig"], sps)
        for fld, mine, a1, a2 in (("Q incl. V", c["qx"], xo["Qx"][0], lx[0]), ("base", c["bx"], xo["Bx"][0], lx[1])):
            T.cmp(f"case exit [{s_x}]|{fld} vs mint_states", mine, float(a1), x_ctx)
            T.cmp(f"case exit [{s_x}]|{fld} vs L3Mint", mine, float(a2), x_ctx)
        T.cmp("case landing|state_src label", s_e, ref.src_at([c["land"]])[0], e_ctx)
        T.cmp("case exit|state_src label", s_x, ref.src_at([c["xland"]])[0], x_ctx)
    D["distinct (landing slot, leg) pairs priced"] += len(seen_e)

    # -- the shadow's own recorded landing / exit slots: its outcome-record STATES against Strip.state_at (a complete outcome only; the scorer excludes an incomplete one)
    if not out.get("complete"):
        D["outcome records incomplete (skipped here; the scorer excludes them)"] += 1
    else:
        for leg, lname in (("primary", "1p3"), ("binding", "1p9")):
            ent = out["legs"][leg]
            X = int(ent["landing_slot"])
            q, b, src = strip.state_at(X)
            ssb, nb = strip.pressure_at(X, sps)
            ctx = {**ctx0, "leg": lname, "slot": X}
            T.cmp(f"shadow outcome landing [{src}]|Q incl. V (+-0.5 lamport: the strip stores an int)", q, float(ent["q_sol"]) * 1e9, ctx, tol=STRIP_Q_TOL)
            T.cmp(f"shadow outcome landing [{src}]|base", b, float(ent["base"]), ctx)
            T.cmp(f"shadow outcome landing [{src}]|ssb", ssb, ent["ssb"], ctx)
            T.cmp(f"shadow outcome landing [{src}]|nearby lamports", nb, ent["nb_lamports"], ctx)
            T.cmp("shadow outcome landing|state_src label", src, ent["state_src"], ctx)
            T.cmp("shadow outcome landing|slot (trigger record vs outcome record)", int(trig["landing_slot_" + leg]), X, ctx)
        ex = out["exit"]
        X = int(ex["landing_slot"])
        q, b, src = strip.state_at(X)
        ctx = {**ctx0, "slot": X}
        T.cmp(f"shadow outcome exit [{src}]|Q incl. V (+-0.5 lamport: the strip stores an int)", q, float(ex["q_sol"]) * 1e9, ctx, tol=STRIP_Q_TOL)
        T.cmp(f"shadow outcome exit [{src}]|base", b, float(ex["base"]), ctx)
        T.cmp("shadow outcome exit|state_src label", src, ex["state_src"], ctx)
        T.cmp("shadow outcome exit|slot (trigger record vs outcome record)", int(trig["exit_landing_slot"]), X, ctx)

    # -- the dense sweep: EVERY slot X from the strip's first slot to its last slot + SWEEP_AFTER_LAST (any landing or exit slot is one of these)
    xs = list(range(int(start), int(ref.last_slot) + SWEEP_AFTER_LAST + 1))
    r4 = ref.r4b_at(xs)
    srcs = ref.src_at(xs)
    for n_, X in enumerate(xs):
        q, b, src = strip.state_at(X)
        ssb, nb = strip.pressure_at(X, sps)
        l3 = ref.l3.entry(X, 0.0, sps)
        ctx = {**ctx0, "slot": X}
        D[f"dense sweep slots state_src={src}"] += 1
        T.cmp("dense sweep|state_src label", src, srcs[n_], ctx)
        for fld, mine, a1, a2 in (("Q incl. V", q, r4["Q"][n_], l3[0]), ("base", b, r4["B"][n_], l3[1]), ("ssb", ssb, r4["ssb"][n_], l3[2]), ("nearby lamports", nb, r4["nb"][n_], l3[3])):
            if src == "post" and fld == "Q incl. V":
                R.add("dense sweep Q incl. V at a `post` slot (shadow strip vs mint_states)", mine, float(a1))
                R.add("dense sweep Q incl. V at a `post` slot (shadow strip vs L3Mint)", mine, float(a2))
                continue  # a convention (see RelDev); every other field and the `next_pre` Q are exact comparisons
            T.cmp(f"dense sweep [{src}]|{fld} vs mint_states", mine, float(a1), ctx)
            T.cmp(f"dense sweep [{src}]|{fld} vs L3Mint", mine, float(a2), ctx)


def fixture_tie(cases: Sequence[dict], T: Tally) -> None:
    """My scorer-side cases at ages 0 and 1,200 ms equal the committed parity set's trade cases (the set the cost layer was proven on): same replay, same price_trade."""
    want = {}
    for ln in (FIXTURE_DIR / "parity_cases.jsonl").read_text().splitlines():
        c = json.loads(ln)
        if not c["id"].startswith("edge|"):
            want[c["id"].rsplit("|", 1)[0]] = c  # stake does not change the states
    have = {f"{c['pool']}|{c['bot'][:4]}|{c['age_ms']}|{c['leg']}": c for c in cases if c["age_ms"] in (0, 1200)}
    T.cmp("fixture tie|case ids (committed parity_cases vs this replay)", len(want), len(have), {})
    for cid, w in sorted(want.items()):
        h = have.get(cid)
        ctx = {"id": cid}
        for fld, mine in (("Qe", "qe"), ("Be", "be"), ("spot", "spot"), ("Qx", "qx"), ("Bx", "bx"), ("ssb", "ssb"), ("nb", "nb")):
            T.cmp(f"fixture tie|{fld}", w[fld], None if h is None else h[mine], ctx)


def run_parity(tape: str = TAPE_DIR, hours: Sequence[str] = HOURS, log=lambda *_: None) -> dict[str, Any]:
    """The whole check; returns the report (counts, shas, mismatch examples). Raises if a pinned file does not match its sha256 (nothing is read before that)."""
    entries = pinned_sources()
    assert len(entries) == 3 * len(hours), f"expected {3 * len(hours)} pinned h5 files, the list has {len(entries)}"
    assert {Path(p).stem for _, p in entries} == set(hours), "the pinned list's hours differ from the parity hours"
    assert all(p.startswith(TAPE_PREFIX) for _, p in entries), "a pinned path is outside the exploration tape"
    pins = verify_pins(entries)
    log("pins", pins)
    if pins["ok"] != pins["files"]:
        raise RuntimeError(f"pinned file check failed: {pins}")

    from tools import h5_shadow as h5

    work = {f"{k}/{d}.parquet": sha256_file(f"{h5.WORK_DIR}/{k}/{d}.parquet") for k in ("meta", "paths") for d in DAYS}
    inputs = {"vmap": {"path": VMAP_PATH, "sha256": sha256_file(VMAP_PATH)}, "shadow_work_dir": h5.WORK_DIR, "shadow_work_files_sha256": work}
    recs, meta, _sps_pool = replay_records(tape, hours)
    log("replay records", len(recs))
    trig = {r["pool"]: r for r in recs if r["type"] == "trigger" and r["variant"] == "pv"}
    out_ = {r["pool"]: r for r in recs if r["type"] == "outcome" and r["variant"] == "pv"}
    strips = {r["pool"]: r for r in recs if r["type"] == "strip"}
    pools = sorted(p for p in out_ if p in strips and p in trig)
    excl: collections.Counter = collections.Counter()
    cases = scorer_cases(recs, excl)
    by_pool: dict[str, list[dict]] = collections.defaultdict(list)
    for c in cases:
        by_pool[c["pool"]].append(c)
    log("scorer cases", len(cases), "pools", len(pools))

    vmap = json.loads(Path(VMAP_PATH).read_text())["v"]
    df = load_tape_prints(tape, hours, set(pools))
    A = Acc()
    fixture_tie(cases, A.T)
    for i, pool in enumerate(pools):
        strip_rec = strips[pool]
        arr = pool_arrays(df, pool, "l3")
        feed = pool_arrays(df, pool, "feed")
        A.D["pools with tx_index NaN prints"] += arr["tx_nan"] > 0
        A.D["pools where the shadow's feed order differs from the l3 order"] += not all(bool((arr[k] == feed[k]).all()) for k in ("sl", "ib", "so", "tk", "q", "b"))
        ref = PoolRef(arr, float(vmap[pool]), float(out_[pool]["sps"]), int(strip_rec["post_last"][0]))
        A.D["tape prints after the strip's last slot (pool life over; not compared)"] += ref.n_beyond
        A.D["pools with such prints"] += ref.n_beyond > 0
        compare_pool(pool, {"strip": strip_rec, "trigger": trig[pool], "outcome": out_[pool]}, ref, by_pool.get(pool, []), A, meta[pool]["v"], vmap[pool])
        if (i + 1) % 10 == 0:
            log("pools compared", i + 1, "of", len(pools))
    n, bad = A.T.total()
    return {"pins": pins, "sources_list_sha256": sha256_file(SOURCES_LIST), "inputs": inputs, "hours": [hours[0], hours[-1]], "n_hours": len(hours),
            "pools_pv_triggered": len(pools), "scorer_cases": len(cases), "scorer_cases_excluded_by_price_trade": dict(excl), "ages_ms": list(AGES_MS),
            "diagnostics": dict(A.D), "margins_slots": A.margin, "exact_comparisons_total": n, "exact_mismatches_total": bad, "by_field": A.T.table(),
            "convention_derived_quantities": A.R.table(), "mismatch_examples": {k: v for k, v in A.T.examples.items()}}


# ---------------------------------------------------------------------------------------------------------------- tests
def _need_numpy():
    try:
        import numpy  # noqa: F401
        from tools import arena_parity, r4b_book  # noqa: F401
    except Exception as e:  # noqa: BLE001
        raise unittest.SkipTest(f"numpy / r4b_book / arena_parity not importable here: {e!r}")


class PinTests(unittest.TestCase):
    def test_list_has_the_h5_hours(self):
        e = pinned_sources()
        self.assertEqual(len(e), 3 * len(HOURS))
        self.assertEqual({Path(p).stem for _, p in e}, set(HOURS))
        self.assertEqual({Path(p).parent.name for _, p in e}, {"trades", "creates", "migrations"})
        self.assertTrue(all(len(s) == 64 for s, _ in e))
        self.assertTrue(all(p.startswith(TAPE_PREFIX) for _, p in e))

    def test_verify_pins_detects_a_change(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x.parquet"
            f.write_bytes(b"abc")
            good = hashlib.sha256(b"abc").hexdigest()
            self.assertEqual(verify_pins([(good, str(f))])["ok"], 1)
            r = verify_pins([("0" * 64, str(f)), (good, str(f) + ".gone")])
            self.assertEqual((r["ok"], len(r["mismatch"]), len(r["missing"])), (0, 1, 1))


def _synthetic_strip(seed: int, n: int = 160):
    """A random pool: prints with gaps and several prints per slot; the shadow's strip record built from it with the replay formulas, and the arrays for the readers."""
    import random

    import numpy as np

    from tools import h5_shadow as h5

    rng = random.Random(seed)
    v = 17_584_505_288
    slot, q, b = 1000, 30_000_000_000, 400_000_000_000_000
    prints, rows = [], []
    for _ in range(n):
        slot += rng.choice((0, 0, 1, 1, 2, 5, 9))
        buy = rng.random() < 0.5
        tok = rng.randrange(10**11, 10**13)
        dq, db = h5.post_state(q + v, b, buy, 0, tok, None, None)
        sol = int(abs(dq))
        prints.append((slot, buy, sol, tok, q, b))
        rows.append([slot, q + v, q + v, b])
        q, b = int(round(q + dq)), int(round(b + db))
    last = prints[-1]
    qq = last[4] + (last[2] * (1 - 0.0125) if last[1] else -last[2] / (1 - 0.0125))
    bl = last[5] - last[3] if last[1] else last[5] + last[3]
    rec = {"rows": rows, "post_last": [last[0], int(round(qq + v)), int(round(qq + v)), int(bl)], "buys": [[s, so] for s, bu, so, *_ in prints if bu], "from_slot": rows[0][0]}
    arr = {"sl": np.array([p[0] for p in prints]), "ib": np.array([p[1] for p in prints]), "so": np.array([float(p[2]) for p in prints]), "tk": np.array([float(p[3]) for p in prints]),
           "q": np.array([float(p[4]) for p in prints]), "b": np.array([float(p[5]) for p in prints]), "tx_nan": 0}
    return rec, arr, float(v)


class HarnessTests(unittest.TestCase):
    """The comparison machinery itself, on synthetic pools: the readers equal Strip.state_at on every slot, and a one-unit change in the strip is caught."""

    def setUp(self):
        _need_numpy()

    def _dense(self, rec, arr, v, T):
        from tools import arena_h5_score as sc

        sps = 0.2667
        strip = sc.Strip({**rec, "truncated": False})
        ref = PoolRef(arr, v, sps, int(rec["post_last"][0]))
        xs = list(range(rec["from_slot"], rec["post_last"][0] + SWEEP_AFTER_LAST + 1))
        r4 = ref.r4b_at(xs)
        for n_, X in enumerate(xs):
            q, b, src = strip.state_at(X)
            ssb, nb = strip.pressure_at(X, sps)
            l3 = ref.l3.entry(X, 0.0, sps)
            for key, a, c in (("Q", q, r4["Q"][n_]), ("B", b, r4["B"][n_]), ("ssb", ssb, r4["ssb"][n_]), ("nb", nb, r4["nb"][n_]), ("Q3", q, l3[0]), ("B3", b, l3[1]),
                              ("ssb3", ssb, l3[2]), ("nb3", nb, l3[3])):
                T.cmp(key, a, float(c), {"slot": X})
            T.cmp("src", src, ref.src_at([X])[0], {"slot": X})

    def test_strip_equals_both_readers_on_every_slot_of_synthetic_pools(self):
        for seed in range(5):
            rec, arr, v = _synthetic_strip(seed)
            T = Tally()
            self._dense(rec, arr, v, T)
            n, bad = T.total()
            self.assertGreater(n, 1000)
            # the derived post-last state uses the shadow's formula in the strip and the stack's 1.25% in the readers: only Q of a `post` slot may differ, and only slightly
            self.assertEqual({k: T.bad[k] for k in T.bad if k not in ("Q", "Q3")}, {})
            for k in ("Q", "Q3"):
                for ex in T.examples[k]:
                    self.assertGreaterEqual(ex["slot"], rec["post_last"][0])

    def test_a_changed_strip_row_is_caught(self):
        rec, arr, v = _synthetic_strip(11)
        rec["rows"][len(rec["rows"]) // 2][3] += 1  # one unit of base in the middle of the strip
        T = Tally()
        self._dense(rec, arr, v, T)
        self.assertGreater(T.bad["B"], 0)
        self.assertGreater(T.bad["B3"], 0)

    def test_a_shifted_slot_is_caught(self):
        rec, arr, v = _synthetic_strip(12)
        mid = len(rec["rows"]) // 2
        rec["rows"][mid][0] += 3  # a print recorded three slots late
        rec["rows"][mid:] = [r for r in rec["rows"][mid:]]
        T = Tally()
        self._dense(rec, arr, v, T)
        self.assertGreater(sum(T.bad[k] for k in ("Q", "B", "ssb", "nb")), 0)


@unittest.skipUnless(os.environ.get(RUN_ENV) == "1" and os.path.isdir(TAPE_DIR), f"real-tape parity: set {RUN_ENV}=1 on the host that holds {TAPE_DIR}")
class RealTapeParity(unittest.TestCase):
    def test_h5_strip_states_equal_the_independent_extraction(self):
        _need_numpy()
        rep = run_parity()
        self.assertEqual(rep["exact_mismatches_total"], 0, json.dumps(rep["mismatch_examples"], indent=1, default=str)[:4000])
        # the quantities the two sides DERIVE by different conventions are reported with their size (see the PR body); a bound only catches a gross change
        for k, v in rep["convention_derived_quantities"].items():
            self.assertLess(v["max_rel"], CONVENTION_REL_BOUND, k)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", help="write the full report here")
    a = ap.parse_args(argv)
    rep = run_parity(log=lambda *x: print(*x, file=sys.stderr, flush=True))
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=1, sort_keys=True, default=str) + "\n")
    brief = {k: v for k, v in rep.items() if k not in ("by_field", "mismatch_examples", "convention_derived_quantities")}
    print(json.dumps(brief, indent=1, sort_keys=True, default=str))
    print(json.dumps(rep["by_field"], indent=1, sort_keys=True))
    print(json.dumps(rep["convention_derived_quantities"], indent=1, sort_keys=True))
    for k, v in rep["mismatch_examples"].items():
        print("EXAMPLES", k, json.dumps(v, default=str))
    return 0 if rep["exact_mismatches_total"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
