#!/usr/bin/env python3
"""kcurve-boost pricing adapter: K-1 (opening reserves) and K0..K12 (END of slot m + k), exit at s0 + 330 s. PAPER ONLY. In-sample exploration.

Registration: docs/research/kcurve-boost/PREREG.md (sha256 338567229c03257c696124ce5aa09a21b1f66735276a3d8de6060c362968fcc2 at main 82c230e1),
DECLARE notebook n_QvBXoCeREuSVtQ; QP ruling /data/mal/hunt-1008/ops/manager18/QP-RULING-1030-kcurve-prereg.md (Q1, Q2, C1-C5).

    python docs/research/kcurve-boost/kcurve_price.py --precount --out DIR   (outcome-blind: counts and refusals, no price)
    python docs/research/kcurve-boost/kcurve_price.py --out DIR              (the four deciding blocks)
    python docs/research/kcurve-boost/kcurve_price.py --solo-fresh-0903 --out DIR   (report-only, never a verdict)

WHAT THIS FILE DOES AND DOES NOT DO (Q2, C5 vii). It builds ONLY the seat states of each trip. Loading, the path and hour guards, V, every leg, statistic,
gate, kill, worst-in-slot and screen-p call are the kit's own functions, IMPORTED from tools.arena_screen / arena_stack / arena_report_legs at the
pinned sha256 (checked at import; a mismatch refuses). It defines no pricing or statistics function.

SEAT STATES (PREREG sections 4-6):
  m       the slot of the pool's first print in the block; s0 = that root print's block time.
  root    the one print of slot m whose pre-trade base reserve is not any slot-m print's post-trade base reserve (b - tok for a buy, b + tok for a sell);
          its pre-trade base must equal SEED_B = 206,900,000,000,000. No within-slot order is needed.
  K-1     entry state = (q_root + V, b_root): ahead of every print. ssb / nearby = mint_states' arrays at x = m (K0's, C4). Worst-in-slot entry = the
          opening state itself; w_mismatch checks its exit only (C4).
  Kk      entry state = END of slot m + k = arena_stack.mint_states(decision slot m + k, latency 0): the pre-trade state of the first print at slot
          >= m + k + 1 (or the derived state after the last print). ssb / nearby from the same call.
  ref     the 1.15 guard reference = the opening price (q_root + V) / b_root for every cell (Q7).
  exit    xs = m + rint(330 x slots per second of m's hour); the sell lands by arena_screen.pool_states (feed delay 7 + exit lag), with the kit's
          late-print re-derivation and block-edge exclusion (_price_store's rule). Report-only exits: 300 s and 360 s.
UNIVERSE U (section 4): WSOL pool, V in [17.5e9, 17.7e9] at the root; unique seed root; a create row of the mint in the block with is_mayhem_mode
false; organic (s0 - create time >= 60 s). Report-only universe: U without the organic item.

RE-DELIVERED DUPLICATES (QP ruling /data/mal/hunt-1008/ops/manager18/QP-RULING-1031-kcurve-dupes.md, sha256 39797d2a..., condition 1):
  "Within one pool, a row identical to an earlier row of the same hour file on (slot, tx_index, event_index, side, sol_lamports, token_raw,
  quote_reserve, base_reserve, trader, block time or event_ts) is a re-delivered duplicate and is dropped before any use: the root test, the
  entry, exit and worst-in-slot states, and ssb/nearby." "Rows sharing (signature, event_index) whose trade fields differ are NOT collapsed."
  One function, `dedupe_keep`, on the kit's HourRows arrays (which carry the trader crc32, not the signature), used by both `opening()` and
  `_cols()`; the kit stays byte-identical. Counted per block and UTC day; dropped rows above 1% of a block's loaded rows = STOP_RETURN_TO_QP.

3a EXEMPTION (hunt23 RULES Amendment 1 item 3a; C1): only the four OUTCOMES blocks, or fresh-0903 alone (arena_screen.check_block_path(path,
count_only=False)); no arena tape; no hour at or after 2026-10-01T00:00Z (refused before any file is opened); every output carries STAMP.
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
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402

KIT_PINS = {
    "tools/arena_screen.py": "b37282c7d23d3bfca310f2574b8bc2b9b0981e0e73ee2537e118f779244fbb57",
    "tools/arena_stack.py": "a39b2ba68dd46b7d3a555fb8e06580718300f354020c1f91280ab7a0e0856778",
    "tools/arena_paper.py": "9d2fdb04a3da9bb38de9e30c91154f5a9ab2de513e6f4d7936c1eb1a54863514",
    "tools/arena_report_legs.py": "65f4d471e3ad19308a038c4d6563f22322134013f09c0afe2d64c648964a0d8b",
}
PREREG_SHA256 = "338567229c03257c696124ce5aa09a21b1f66735276a3d8de6060c362968fcc2"
NOT_MEASURED = {
    "completing_buy_sizes": "PREREG section 12 item 4: NOT MEASURED by this adapter (it reads no migrations/ or bonding rows); see R-b for the probe's figures",
    "missing_create_first_bonding_print_ge_60s": ("PREREG section 4 item 3 report-only: NOT MEASURED (missing-create pools whose mint's first bonding print in the "
                                                  "block is >= 60 s before s0; needs bonding rows, which the kit loader does not load). Disclosed in the DECLARE-AMEND."),
}
REPORT_ONLY_NOTES = {
    "R-b_completing_buy_sizes": ("NOT computed by this adapter (it never reads migrations/ or bonding rows). Structure probe, explore-0814/w1 48 h "
                                  "(docs/research/kcurve-boost/probe/structure_probe_output.jsonl; PREREG section 1.2), all WSOL completes incl. instant "
                                  "launches: completing curve buy SOL p10 0.0092, p25 0.0517, p50 0.9037, p75 24.7584, p90 85.0054; share >= 0.25 SOL "
                                  "0.5979. Report-only; decides nothing."),
    "R-d_oracle_live_order": ("oracle-live has no tx_index: the END state of slot m + k is the pre-trade state of the next slot's first print in tape "
                              "order (the kit's inherited convention); K-1's root is order-free."),
    "dup_rows_qp_1031": ("QP-RULING-1031-kcurve-dupes: oracle-live carries re-delivered duplicate rows (about 0.4% of rows, about 5% of seed openings in "
                         "QP's 3-hour count) that the 10-01 byte-identical dedupe missed (they differ only in t_recv_ms). They are dropped here before any use "
                         "(counts rows_dup_dropped, u_all_dup_dropped_in_slot_m, root_not_unique_before_dedupe). Rows that share (signature, event_index) but "
                         "differ (about 2% of repeated keys, about 0.007% of rows) are NOT collapsed. Prices are unaffected by duplicates; ssb/nearby were."),
    "R-e_mayhem_flag": "a block whose create rows lack is_mayhem_mode puts its pools in mayhem_or_unknown; the U < 100 refusal then stops the run: return to QP, never loosen the filter.",
}
STAMP = "EXPLORATION-ONLY, FIRST 360 S: no October hour; not registrable before EXP-024's end state."


class Refused(Exception):
    """A guard refused before anything was priced."""


def file_sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_kit_pins(pins: dict[str, str] = KIT_PINS, root: Path = REPO) -> None:
    bad = [f"{f} {file_sha256(root / f)} != {s}" for f, s in pins.items() if file_sha256(root / f) != s]
    if bad:
        raise Refused("kit_pin_mismatch: " + "; ".join(bad))


check_kit_pins()  # C5 (v): at import, before the kit is imported

from tools import arena_report_legs as RL  # noqa: E402
from tools import arena_screen as S  # noqa: E402
from tools import arena_stack as F  # noqa: E402

SEED_B = 206_900_000_000_000
KS = (0, 1, 2, 3, 5, 8, 12)
CELLS = ("K-1",) + tuple(f"K{k}" for k in KS)
GROUP_OF = {c: ("A" if c == "K-1" else "B") for c in CELLS}
HOLD_S = 330.0
REPORT_HOLDS = (300.0, 360.0)
ORGANIC_S = 60
Q_END_SOL = 115.0054
HOUR_CAP = datetime(2026, 10, 1, tzinfo=timezone.utc)
DECIDING_STAKE_SOL = 0.25
LAT = S.LAT_DECIDING  # the key under which the kit's legs() / worst_in_slot_legs() read the entry state; NOT a latency here
KEEP_EXTRA_SLOTS = S.GRACE + 2_000  # a pool's rows are kept until the stream passes its latest exit need + GRACE (+ margin)
MAX_WORKERS = 2
# COUNTS refusals (PREREG section 12 item 4), checked per canonical block before any price is written
MIN_U_POOLS = 100
MAX_ROOT_NOT_UNIQUE = 0.02
MAX_V_UNKNOWN = 0.05
MAX_CREATE_MISSING = 0.30  # STOP and return to QP (C3)
MAX_DUP_DROPPED = 0.01  # STOP and return to QP (QP-RULING-1031-kcurve-dupes, condition 3)


# ------------------------------------------------------------------------------------------------------------------------------ guards (3a items 1, 3; C5 vi)
def check_blocks(blocks: Sequence[str]) -> list[tuple[str, str, list]]:
    """(name, path, files) per block; refuses a non-exploration root, the arena tape, a repeated block, a set other than the four or fresh-0903
    alone, and any hour at or after 2026-10-01T00Z, all before any file is opened."""
    specs, reals = [], []
    for b in blocks:
        if S.check_block_path(b, count_only=False) != "clean":
            raise Refused(f"not_clean: {b}")
        real = os.path.realpath(b)
        if any(S._under(real, o) or S._under(o, real) for o in reals):
            raise Refused(f"repeated_or_nested_block: {b}")
        reals.append(real)
        files = S.discover(b, "clean")
        check_hours(files)
        specs.append((os.path.relpath(real, S.CLEAN_VIEW), b, files))
    four = sorted(os.path.realpath(r) for r in S.BLOCK_ROOTS)
    solo = [[os.path.realpath(r)] for r in S.EXTRA_SOLO_BLOCK_ROOTS]
    if sorted(reals) != four and sorted(reals) not in solo:
        raise Refused("blocks: pass the four deciding blocks once each, or fresh-0903 alone (report-only)")
    return specs


def check_hours(files: Sequence[tuple[datetime, str]]) -> None:
    late = [f for t, f in files if t is None or t >= HOUR_CAP]
    if late:
        raise Refused(f"hour_at_or_after_2026-10-01T00Z (3a item 1): {late[0]}")


def check_out(out: str | os.PathLike) -> str:
    real = S.check_out_dir(out)
    for p in (os.path.abspath(str(out)), real):
        w = S._forbidden_word(p)
        if w:
            raise Refused(f"out_dir_forbidden_word:{w}: {out}")
    return real


def check_stake(stake_sol: float) -> None:
    if stake_sol != DECIDING_STAKE_SOL:
        raise Refused(f"deciding stake must be {DECIDING_STAKE_SOL} SOL (PREREG section 5), got {stake_sol}")


# ------------------------------------------------------------------------------------------------------------------------------ creates (structure only)
def _lines(path: Path):
    if path.suffix == ".zst":
        out = subprocess.run(["zstd", "-dc", "-q", str(path)], capture_output=True, check=True).stdout
        return out.splitlines()
    return path.read_bytes().splitlines()


def load_creates(block_path: str, read: list | None = None) -> dict[str, tuple]:
    """mint -> (create time s, is_mayhem_mode, signature) from the block's own creates/ files (w*/creates for explore-0814; oracle-live's observe
    files: ws `ingest_hot` rows with txType create, time = t_ws, a processed receive time). Files under the block root only, before 2026-10-01."""
    root = os.path.realpath(block_path)
    p = Path(block_path)
    out: dict[str, tuple] = {}
    for f in sorted(set(p.glob("creates/*")) | set(p.glob("w*/creates/*"))):
        rf = os.path.realpath(f)
        if not S._under(rf, root) or S._forbidden_word(rf):
            raise Refused(f"creates_file_outside_block_or_forbidden: {f}")
        day = f.name.split("-", 1)[1][:10] if "-" in f.name else ""
        if not day or day >= "2026-10-01":
            raise Refused(f"creates_file_date (3a item 1): {f}")
        if read is not None:
            read.append(str(f))
        for line in _lines(f):
            if b'"create"' not in line:
                continue
            r = json.loads(line)
            if r.get("type") == "create" and r.get("mint"):
                out.setdefault(r["mint"], (r.get("block_time") or r.get("event_ts"), r.get("is_mayhem_mode"), r.get("signature")))
            elif r.get("type") == "ingest_hot" and r.get("txType") == "create" and r.get("mint"):
                t = r.get("t_ws")
                dt = datetime.fromisoformat(t) if t else None
                ts = (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp() if dt else None  # an offset-less t_ws is UTC
                out.setdefault(r["mint"], (ts, (r.get("ws_payload") or {}).get("is_mayhem_mode"), r.get("signature")))
    return out


# ------------------------------------------------------------------------------------------------------------------------------ seat states
def root_of(b: np.ndarray, tk: np.ndarray, ib: np.ndarray) -> list[int]:
    """Indices (within the slot's prints) whose pre-trade base reserve is not the post-trade base reserve of any print of the slot."""
    post = set((np.where(ib, b - tk, b + tk)).tolist())
    return [i for i, x in enumerate(b.tolist()) if x not in post]


def dedupe_keep(hr: Any) -> np.ndarray:
    """Keep-mask over the hour's rows: False on a row identical to an EARLIER row (tape order within the pool) of the same pool on (slot, tx_index,
    event_index, side, sol_lamports, token_raw, quote_reserve, base_reserve, trader, block time). Rows that share a key but differ in any of these
    fields are kept. The single dedupe of this adapter (QP-RULING-1031-kcurve-dupes, condition 1)."""
    if hr.n == 0:
        return np.zeros(0, bool)
    pid = np.repeat(np.arange(len(hr.ptr) - 1), np.diff(hr.ptr))
    key = np.column_stack([pid, hr.sl, hr.tx, hr.ev, hr.isbuy.astype(np.int64), hr.sol, hr.tok, hr.q, hr.b, hr.trader, hr.bt]).astype(np.float64)
    _, first = np.unique(key, axis=0, return_index=True)  # every field < 2**53, so float64 is exact
    keep = np.zeros(hr.n, bool)
    keep[first] = True
    return keep


def opening(hr: Any, i: int, keep: np.ndarray | None = None) -> dict | None:
    """Structure of pool i's first slot in this hour, on the deduped rows (the seed test is the caller's). `raw_unique` is the root test on the rows
    before the dedupe (counted only)."""
    keep = dedupe_keep(hr) if keep is None else keep
    rs = hr.rows(i)
    k = keep[rs]
    sl_all = hr.sl[rs]
    m = int(sl_all[0])
    j_raw = np.flatnonzero(sl_all == m)
    raw_unique = len(root_of(hr.b[rs][j_raw], hr.tok[rs][j_raw], hr.isbuy[rs][j_raw])) == 1
    sl = sl_all[k]
    b, tok, ib = hr.b[rs][k], hr.tok[rs][k], hr.isbuy[rs][k]
    j = np.flatnonzero(sl == m)
    roots = root_of(b[j], tok[j], ib[j])
    seed_cand = bool((b[j] == SEED_B).any())
    dropped_m = int(len(j_raw) - len(j))
    if len(roots) != 1:
        return {"unique": False, "raw_unique": raw_unique, "seed_cand": seed_cand, "dropped_m": dropped_m}
    r = int(j[roots[0]])  # index within the pool's DEDUPED rows, the same rows _cols() returns
    return {"unique": True, "raw_unique": raw_unique, "seed_cand": seed_cand, "dropped_m": dropped_m, "m": m, "r": r, "b0": float(b[r]),
            "q0": float(hr.q[rs][k][r]), "v0": float(hr.v[rs][k][r]), "s0": int(hr.bt[rs][k][r])}


def _cols(hr: Any, i: int, keep: np.ndarray | None = None) -> dict:
    keep = dedupe_keep(hr) if keep is None else keep
    rs = hr.rows(i)
    k = keep[rs]
    return {"sl": hr.sl[rs][k].copy(), "ib": hr.isbuy[rs][k].copy(), "so": hr.sol[rs][k].copy(), "tk": hr.tok[rs][k].copy(), "q": hr.q[rs][k].copy(),
            "b": hr.b[rs][k].copy(), "v": hr.v[rs][k].copy()}


def exit_states(a: dict, m: int, rate_m: float, xs: int, sps_x: float, seg_end: int) -> tuple[float, float, int, bool, bool]:
    """(Qx, Bx, need, late, excluded) by the kit's own pool_states and _price_store rule for one trip."""
    sd, sps_d = np.array([m]), np.array([1.0 / rate_m])
    need = int(xs + S.D + int(F.exit_lag_vec(np.array([sps_x]))[0]) + 1)
    sl = a["sl"]
    n = len(sl)
    jx = int(np.searchsorted(sl, need, "left"))
    has = jx < n
    late = bool(has and sl[jx] >= need + S.GRACE)
    excl = bool((not has) and need + S.GRACE > seg_end)
    src = {k: v[:jx] for k, v in a.items()} if late else a
    st = S.pool_states(src, sd, sps_d, np.array([xs]), np.array([sps_x]))
    return float(st["Qx"][0]), float(st["Bx"][0]), need, late, excl


def seat_trips(pool: str, a: dict, info: dict, rate_of, seg_end: int) -> list[dict]:
    """One trip per cell for one pool: entry seat states, the deciding exit (330 s) and the report-only exits, worst-in-slot states, START states."""
    m, r, rate_m = info["m"], info["r"], info["rate"]
    sps_m = 1.0 / rate_m
    Q0, B0 = a["q"][r] + a["v"][r], a["b"][r]
    ref = Q0 / B0
    ex = {}
    for H in (HOLD_S,) + REPORT_HOLDS:
        xs = m + int(np.rint(H * rate_m))
        ex[H] = (xs,) + exit_states(a, m, rate_m, xs, 1.0 / rate_of(xs), seg_end)
    xs, Qx, Bx, need, late, excl = ex[HOLD_S]
    sa = RL.state_arrays(S._ArrHist(pool, a), need if late else None)
    xl = xs + S.D + int(F.exit_lag_vec(np.array([1.0 / rate_of(xs)]))[0])
    idx_x, je_x = RL.slot_states(sa, xl)
    wx = RL.worst_index(sa, idx_x, "sell")
    x_ok = S._same(sa["QX"][je_x], Qx) and S._same(sa["BX"][je_x], Bx)
    x_vbad = S._no_v(sa, idx_x)
    sd = np.array([m + k for k in KS], np.int64)
    ms = F.mint_states(a["sl"], a["ib"], a["so"], a["tk"], a["q"], a["b"], a["v"], sd, np.full(len(KS), sps_m), lats=(0.0,), rounding="ceil")
    full = RL.state_arrays(S._ArrHist(pool, a))
    out = []
    base = {"ref": ref, "Qx": Qx, "Bx": Bx, "wQx": float(sa["QX"][wx]), "wBx": float(sa["BX"][wx]), "excl": excl, "late": late,
            **{f"Qx{int(H)}": ex[H][1] for H in REPORT_HOLDS}, **{f"Bx{int(H)}": ex[H][2] for H in REPORT_HOLDS},
            **{f"excl{int(H)}": ex[H][5] for H in REPORT_HOLDS}}
    # K-1: the opening state, pressure inputs of K0 (x = m)
    out.append({**base, "cell": "K-1", "Qe": float(Q0), "Be": float(B0), "ssb": float(ms["ssb"][0.0][0]), "nearby": float(ms["nearby"][0.0][0]),
                "wQe": float(Q0), "wBe": float(B0), "w_vbad": x_vbad or not math.isfinite(a["v"][r]), "w_mismatch": not x_ok, "Qs": float("nan"), "Bs": float("nan")})
    for t, k in enumerate(KS):
        idx_e, je_e = RL.slot_states(sa, m + k)
        we = RL.worst_index(sa, idx_e, "buy")
        Qe, Be = float(ms["Qe"][0.0][t]), float(ms["Be"][0.0][t])
        e_ok = S._same(sa["QX"][je_e], Qe) and S._same(sa["BX"][je_e], Be)
        js = int(np.searchsorted(full["sl"], m + k, "left"))
        out.append({**base, "cell": f"K{k}", "Qe": Qe, "Be": Be, "ssb": float(ms["ssb"][0.0][t]), "nearby": float(ms["nearby"][0.0][t]),
                    "wQe": float(sa["QX"][we]), "wBe": float(sa["BX"][we]), "w_vbad": x_vbad or S._no_v(sa, idx_e), "w_mismatch": not (x_ok and e_ok),
                    "Qs": float(full["QX"][js]) if k >= 1 else float("nan"), "Bs": float(full["BX"][js]) if k >= 1 else float("nan")})
    return out


# ------------------------------------------------------------------------------------------------------------------------------ the pass over one block
class BlockPass:
    def __init__(self, bi: int, name: str, creates: dict, price: bool) -> None:
        self.bi, self.name, self.creates, self.price = bi, name, creates, price
        self.seen: set[str] = set()
        self.store: dict[str, dict] = {}
        self.clk_min: list[int] = []
        self.clk_rate: list[float] = []
        self.counts: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        self.trips: list[dict] = []
        self.days: set[int] = set()
        self.sps_hours: list[float] = []  # seconds per slot of every loaded hour (R-a)
        self.seg_end = -1

    def rate_of(self, slot: int) -> float:
        i = max(0, int(np.searchsorted(np.array(self.clk_min), slot, "right")) - 1)
        return self.clk_rate[i]

    def hour(self, hr: Any, prev_T: int | None) -> None:
        if hr.n_no_ts:  # rows build_hour refused for a missing timestamp: counted, never silently dropped (reviewer on #1135)
            C0 = self.counts[datetime.fromtimestamp(hr.T, tz=timezone.utc).strftime("%Y-%m-%d")]
            C0["rows_no_timestamp"] += int(hr.n_no_ts)
            C0["hours_with_missing_timestamp"] += 1
        if hr.n == 0 or (prev_T is not None and hr.T != prev_T + 3600):
            self.flush(all_=True)
            self.clk_min, self.clk_rate = [], []
            if hr.n == 0:
                return
        self.days.add(hr.T // 86400)
        keep = dedupe_keep(hr)
        Cd = self.counts[datetime.fromtimestamp(hr.T, tz=timezone.utc).strftime("%Y-%m-%d")]
        Cd["rows_loaded"] += int(hr.n)
        Cd["rows_dup_dropped"] += int(hr.n - keep.sum())
        self.clk_min.append(hr.min_slot)
        self.clk_rate.append(hr.slots_per_s)
        self.sps_hours.append(1.0 / hr.slots_per_s)
        self.seg_end = max(self.seg_end, hr.max_slot)
        for i, pool in enumerate(hr.pools.tolist()):
            if pool in self.store:
                self.store[pool]["chunks"].append(_cols(hr, i, keep))
                continue
            if pool in self.seen:
                continue
            self.seen.add(pool)
            o = opening(hr, i, keep)
            if o is None:
                continue
            day = datetime.fromtimestamp(hr.T, tz=timezone.utc).strftime("%Y-%m-%d")
            C = self.counts[day]
            C["seed_candidates"] += o["seed_cand"]
            C["root_not_unique_before_dedupe"] += bool(o["seed_cand"] and not o["raw_unique"])
            if not o["unique"]:
                C["root_not_unique"] += o["seed_cand"]
                continue
            if o["b0"] != SEED_B:
                continue
            C["seed_opening"] += 1
            v = o["v0"]
            if not math.isfinite(v):
                C["v_unknown"] += 1
                continue
            if not (S.V_LO <= v <= S.V_HI):
                C["v_out_of_band"] += 1
                continue
            C["seed_opening_vband"] += 1
            mint = str(hr.mints[i])
            cr = self.creates.get(mint)
            if cr is None:
                C["create_missing"] += 1
                continue
            if cr[1] is not False:
                C["mayhem_or_unknown"] += 1
                continue
            organic = bool(cr[0] is not None and o["s0"] - cr[0] >= ORGANIC_S)
            C["u_all"] += 1
            C["u_organic"] += organic
            fee = float(F.fee_ppm(np.array([o["q0"] + v]), np.array([o["b0"]]))[0])
            C["opening_fee_not_12500"] += fee != 12_500
            C["u_all_dup_dropped_in_slot_m"] += o["dropped_m"]
            self.store[pool] = {"chunks": [_cols(hr, i, keep)], "info": {"m": o["m"], "r": o["r"], "rate": hr.slots_per_s, "T": hr.T, "organic": organic,
                                                                    "crc": zlib.crc32(pool.encode()), "day": day}}
        self.flush(all_=False, now_min=hr.min_slot)

    def flush(self, all_: bool, now_min: int | None = None) -> None:
        for pool in list(self.store):
            s = self.store[pool]
            info = s["info"]
            if not all_:
                xs_max = info["m"] + int(np.rint(max((HOLD_S,) + REPORT_HOLDS) * info["rate"]))
                if now_min is None or now_min <= xs_max + KEEP_EXTRA_SLOTS:
                    continue
            del self.store[pool]
            a = {k: np.concatenate([c[k] for c in s["chunks"]]) for k in s["chunks"][0]}
            C = self.counts[info["day"]]
            trips = seat_trips(pool, a, info, self.rate_of, self.seg_end)
            # R-c: structure counts of the same trips (no P&L): END-consistency and V faults that would make worst-in-slot "not measured"
            # (every cell PROVISIONAL), late-print re-derivations and block-edge exclusions, deciding universe (organic) and all
            tag = "u_organic" if info["organic"] else "u_nonorganic"
            C[f"{tag}:block_edge_excluded"] += bool(trips[0]["excl"])
            C[f"{tag}:late_print_derived"] += bool(trips[0]["late"])
            for t in trips:
                if not t["excl"]:
                    C[f"{tag}:{t['cell']}:w_mismatch"] += bool(t["w_mismatch"])
                    C[f"{tag}:{t['cell']}:w_vbad"] += bool(t["w_vbad"])
            if not self.price:
                continue
            for t in trips:
                t.update(blk=self.bi, T=info["T"], organic=info["organic"], pool=info["crc"])
                self.trips.append(t)

    def finish(self) -> None:
        self.flush(all_=True)


# ------------------------------------------------------------------------------------------------------------------------------ refusals and statistics
def refusals(counts_by_block: dict[str, dict]) -> list[str]:
    """PREREG section 12 item 4 (with C3), per canonical block. Any reason = no price is written."""
    why = []
    for name, per_day in counts_by_block.items():
        if S.canonical_block(name) is None and name.split("/")[0] not in [os.path.basename(r) for r in S.EXTRA_SOLO_BLOCK_ROOTS]:
            continue
        t = collections.Counter()
        for c in per_day.values():
            t.update(c)
        if t["u_organic"] < MIN_U_POOLS:
            why.append(f"{name}: U pools {t['u_organic']} < {MIN_U_POOLS}")
        if t["seed_candidates"] and t["root_not_unique"] / t["seed_candidates"] > MAX_ROOT_NOT_UNIQUE:
            why.append(f"{name}: root not unique {t['root_not_unique']}/{t['seed_candidates']} > {MAX_ROOT_NOT_UNIQUE}")
        if t["seed_opening"] and t["v_unknown"] / t["seed_opening"] > MAX_V_UNKNOWN:
            why.append(f"{name}: V unknown {t['v_unknown']}/{t['seed_opening']} > {MAX_V_UNKNOWN}")
        if t["rows_loaded"] and t["rows_dup_dropped"] / t["rows_loaded"] > MAX_DUP_DROPPED:
            why.append(f"STOP_RETURN_TO_QP {name}: duplicate rows dropped {t['rows_dup_dropped']}/{t['rows_loaded']} > {MAX_DUP_DROPPED}")
        if t["rows_no_timestamp"]:
            why.append(f"STOP_RETURN_TO_QP {name}: {t['rows_no_timestamp']} rows refused for a missing timestamp in {t['hours_with_missing_timestamp']} hours")
        if t["seed_opening_vband"] and t["create_missing"] / t["seed_opening_vband"] > MAX_CREATE_MISSING:
            why.append(f"STOP_RETURN_TO_QP {name}: create missing {t['create_missing']}/{t['seed_opening_vband']} > {MAX_CREATE_MISSING}")
    return why


def kit_st(trips: list[dict], qx: str = "Qx", bx: str = "Bx", qe: str = "Qe", be: str = "Be") -> dict:
    """The kit's per-trip state dict (entry keyed by LAT, as legs() / worst_in_slot_legs() / screen_p_cell() read it)."""
    g = lambda k: np.array([t[k] for t in trips], dtype=float)  # noqa: E731
    return {"ref": g("ref"), "Qe": {LAT: g(qe)}, "Be": {LAT: g(be)}, "ssb": {LAT: g("ssb")}, "nearby": {LAT: g("nearby")}, "Qx": g(qx), "Bx": g(bx),
            "wQe": {LAT: g("wQe")}, "wBe": {LAT: g("wBe")}, "wQx": g("wQx"), "wBx": g("wBx"),
            "w_vbad": np.array([t["w_vbad"] for t in trips], bool), "w_mismatch": np.array([t["w_mismatch"] for t in trips], bool),
            "T": np.array([t["T"] for t in trips], np.int64), "blk": np.array([t["blk"] for t in trips], np.int64)}


def sub_stats(st: dict, names: list[str], cfg, stake: float = DECIDING_STAKE_SOL) -> dict:
    day = st["T"] // 86400
    out = {}
    for rule in S.STRAND_RULES:
        L = S.legs(st, stake * 1e9, LAT, rule, cfg)
        for fail in S.FAILS:
            out[f"{rule}|{fail}"] = S.stat(L[fail], day, st["blk"], names, stake)
    return out


def cell_result(cell: str, trips: list[dict], excluded: int, names: list[str], explore_days: int) -> dict:
    cfg0, cfgC, cfg160 = S.screen_cfg(S.RENT_DECIDING), S.screen_cfg(S.RENT_CHARGED), S.screen_cfg(S.RENT_DECIDING, send=160_000)
    n = len(trips)
    if n == 0:
        return {"attempts": 0, "kill": True, "kill_reasons": ["no attempts"], "verdict": "KILLED", "stamp": STAMP}
    st = kit_st(trips)
    stats = sub_stats(st, names, cfg0)
    gmask = {g: np.array([S.block_group(names[b]) == g for b in st["blk"]]) for g in S.GROUPS}
    groups = {}
    day = st["T"] // 86400
    for rule in S.STRAND_RULES:
        L = S.legs(st, DECIDING_STAKE_SOL * 1e9, LAT, rule, cfg0)
        for fail in S.FAILS:
            for g, msk in gmask.items():
                groups[(g, rule, fail)] = S.stat(L[fail][msk], day[msk], st["blk"][msk], names) if msk.any() else None
    subs = {(r, f): stats[f"{r}|{f}"] for r, f in S.SUB_CELLS}
    n_explore = int(gmask["explore-0814"].sum())
    kill, reasons = S.kill_verdict(subs, names, groups, excl_share=excluded / (n + excluded), explore_att_per_day=(n_explore / explore_days) if explore_days else None,
                                   min_att_per_day=20.0)
    wis = S.worst_in_slot_legs(st, cfg0)
    verdict = "KILLED" if kill else "SURVIVES"
    if wis["measured"]:
        bad = [k for k in S.SUB_CELLS if not wis["sub_cells"]["|".join(k)]["lower"]["mean_sol"] > 0]
        if bad:
            kill = True
            verdict = "KILLED"
            reasons.append("worst-in-slot (lower of worst-in-slot and no-guard-rescue) mean <= 0 in " + ", ".join("/".join(k) for k in bad))
    elif not kill:
        verdict = "PROVISIONAL (worst-in-slot not measured)"
    res: dict[str, Any] = {"cell": cell, "group": GROUP_OF[cell], "attempts": n, "excluded_block_edge": excluded, "excluded_share": excluded / (n + excluded),
                           "explore_attempts_per_day": (n_explore / explore_days) if explore_days else None, "gate": {k: S.gate_row(v) for k, v in stats.items()},
                           "stats_rent0_send505k": stats, "groups": {"|".join(k): v for k, v in groups.items()}, "kill": kill, "kill_reasons": reasons,
                           "verdict": verdict, "worst_in_slot": wis, "stamp": STAMP}
    rep: dict[str, Any] = {"rent_charged": sub_stats(st, names, cfgC), "send_160k": sub_stats(st, names, cfg160), "stake_1_0_sol": sub_stats(st, names, cfg0, 1.0)}
    fl = S.legs(st, DECIDING_STAKE_SOL * 1e9, LAT, "total", cfg0)["filled"]
    fee = F.fee_ppm(st["Qe"][LAT], st["Be"][LAT])
    rep["impact"] = {str(s): (float(np.mean(s * 1e9 * (1 - fee[fl] / 1e6) / st["Qe"][LAT][fl])) if fl.any() else None) for s in (0.25, 1.0)}
    for H in REPORT_HOLDS:
        keep = [t for t in trips if not t[f"excl{int(H)}"]]
        rep[f"exit_{int(H)}s"] = sub_stats(kit_st(keep, qx=f"Qx{int(H)}", bx=f"Bx{int(H)}"), names, cfg0) if keep else None
    if cell != "K-1" and cell != "K0":
        rep["start_of_slot"] = sub_stats(kit_st(trips, qe="Qs", be="Bs"), names, cfg0)
    if cell == "K-1":
        M = DECIDING_STAKE_SOL * (DECIDING_STAKE_SOL / Q_END_SOL + DECIDING_STAKE_SOL / (st["Qe"][LAT] / 1e9) + DECIDING_STAKE_SOL / (st["Qx"] / 1e9))
        mbar = float(np.nanmean(M))
        rep["remainder_margin"] = {"M_bar_sol": mbar, "formula": "0.25 x (0.25/115.0054 + 0.25/Qe + 0.25/Qx), Qe and Qx in SOL incl. V"}
        rep["opening_fee_not_12500"] = int((fee != 12_500).sum())
        if kill and all(stats[f"{r}|{f}"] is not None and stats[f"{r}|{f}"]["mean"] > -mbar for r, f in S.SUB_CELLS):
            res["verdict"] = "K-1 KILLED within the remainder margin"
    kstats = {f"{LAT}|{r}|rent0|{f}": stats[f"{r}|{f}"] for r, f in S.SUB_CELLS}
    rep["screen_p"] = S.screen_p_cell(st, cfg0, kstats)
    res["report_only"] = rep
    return res


def counts_md(counts: dict, why: list[str], k_seconds: dict) -> str:
    """COUNTS.md text: the stamp, the refusals, per-block totals (structure only), k in seconds per block (R-a) and the report-only notes (R-b, R-d, R-e)."""
    lines = [f"# kcurve-boost COUNTS (outcome-blind precount)", "", f"**{STAMP}**", "", "No price, P&L or outcome is computed here. No book has cleared the promotion gate.", "",
             "## Refusals (PREREG section 12 item 4)", ""] + ([f"- {w}" for w in why] or ["- none"]) + ["", "## Per-block totals", ""]
    for name, per_day in counts.items():
        tot = collections.Counter()
        for c in per_day.values():
            tot.update(c)
        lines.append(f"- **{name}** ({len(per_day)} UTC days): " + ", ".join(f"{k} {v}" for k, v in sorted(tot.items())))
    lines += ["", "## k in seconds per block (R-a; k x mean seconds per slot of the block's hours)", ""]
    for name, ks in k_seconds.items():
        lines.append(f"- {name}: " + ", ".join(f"{c} {v}" for c, v in ks.items()))
    lines += ["", "## Registered in the PREREG but not measured", ""] + [f"- {k}: {v}" for k, v in NOT_MEASURED.items()]
    lines += ["", "## Report-only notes", ""] + [f"- {k}: {v}" for k, v in REPORT_ONLY_NOTES.items()] + [""]
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------------------------------------ driver
def run(blocks: Sequence[str], out: str, precount: bool, stake_sol: float = DECIDING_STAKE_SOL, workers: int = 1, log=print) -> int:
    check_stake(stake_sol)
    outp = Path(check_out(out))
    specs = check_blocks(blocks)
    prereg = REPO / "docs" / "research" / "kcurve-boost" / "PREREG.md"
    if file_sha256(prereg) != PREREG_SHA256:
        raise Refused(f"PREREG sha256 at this checkout is not {PREREG_SHA256}")
    vpaths = S.check_vmap_paths(S.VMAP_DEFAULT)
    vmap = S.VMap(vpaths)
    names, passes = [], []
    inputs: dict[str, str] = {}  # every input file read (creates/ and trades/), by sha256 (C3: the precount lists its input sha256)
    explore_days = 0
    for bi, (name, path, files) in enumerate(specs):
        names.append(name)
        cr_files: list[str] = []
        bp = BlockPass(bi, name, load_creates(path, cr_files), price=not precount)
        for f in cr_files + [f for _, f in files]:
            inputs[os.path.relpath(os.path.realpath(f), S.CLEAN_VIEW)] = file_sha256(Path(f))
        prev_T = None
        for hr in S.iter_hours(files, name, "backfill", vmap, min(max(1, workers), MAX_WORKERS)):
            bp.hour(hr, prev_T)
            prev_T = hr.T if hr.n else None
        bp.finish()
        if S.block_group(name) == "explore-0814":
            explore_days = len(bp.days)
        passes.append(bp)
        log(json.dumps({"block": name, "hours": len(files), "pools_tracked_trips": len(bp.trips)}))
    counts = {bp.name: {d: dict(c) for d, c in sorted(bp.counts.items())} for bp in passes}
    sps = {bp.name: (float(np.mean(bp.sps_hours)) if bp.sps_hours else None) for bp in passes}  # R-a: k in seconds per block (k x mean s/slot)
    k_seconds = {n: ({"K-1": "ahead of every print (the seat precedes slot m)"} | {f"K{k}": (k * s if s else None) for k in KS} | {"seconds_per_slot_mean": s})
                 for n, s in sps.items()}
    why = refusals(counts)
    prov = {"stamp": STAMP, "prereg_sha256": PREREG_SHA256, "adapter_sha256": file_sha256(Path(__file__)), "kit": KIT_PINS,
            "vmap": [{"path": v, "sha256": S.file_sha256(v)} for v in vpaths], "blocks": names, "inputs_sha256": dict(sorted(inputs.items())),
            "hours": {n: [t.strftime("%Y-%m-%dT%H") for t, _ in sp[2]] for n, sp in zip(names, specs)},
            "written_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    outp.mkdir(parents=True, exist_ok=True)
    if precount or why:
        doc = {"stamp": STAMP, "provenance": prov, "counts_per_block_per_day": counts, "refusals": why, "k_seconds_per_block": k_seconds,
               "report_only_notes": REPORT_ONLY_NOTES, "not_measured": NOT_MEASURED}
        (outp / "counts.json").write_text(json.dumps(doc, indent=1, default=float) + "\n")
        (outp / "COUNTS.md").write_text(counts_md(counts, why, k_seconds))
        log(json.dumps({"precount": precount, "refusals": why}))
        return 4 if why else 0
    trips = [t for bp in passes for t in bp.trips]
    deciding = [t for t in trips if t["organic"]]
    cells: dict[str, Any] = {}
    for c in CELLS:
        ct = [t for t in deciding if t["cell"] == c]
        keep = [t for t in ct if not t["excl"]]
        cells[c] = cell_result(c, keep, len(ct) - len(keep), names, explore_days)
        allu = [t for t in trips if t["cell"] == c and not t["excl"]]
        cells[c]["report_only"]["universe_without_organic"] = sub_stats(kit_st(allu), names, S.screen_cfg(S.RENT_DECIDING)) if allu else None
    solo = len(names) == 1
    survivors = {}
    for g in ("A", "B"):
        ok = [c for c in CELLS if GROUP_OF[c] == g and cells[c]["verdict"] == "SURVIVES"]
        pick = max(ok, key=lambda c: cells[c]["stats_rent0_send505k"]["total|press"]["mean"]) if ok and not solo else None
        survivors[g] = pick
    doc = {"stamp": STAMP, "report_only_solo_block": solo, "provenance": prov, "counts_per_block_per_day": counts, "k_seconds_per_block": k_seconds,
           "report_only_notes": REPORT_ONLY_NOTES, "not_measured": NOT_MEASURED, "cells": cells,
           "survivor_pick_per_group": survivors}
    (outp / "results.json").write_text(json.dumps(doc, indent=1, default=float) + "\n")
    log(json.dumps({"written": str(outp / "results.json"), "verdicts": {c: cells[c]["verdict"] for c in CELLS}, "survivors": survivors}))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--precount", action="store_true")
    ap.add_argument("--solo-fresh-0903", action="store_true")
    ap.add_argument("--stake-sol", type=float, default=DECIDING_STAKE_SOL)
    ap.add_argument("--workers", type=int, default=1)
    a = ap.parse_args(argv)
    blocks = list(S.EXTRA_SOLO_BLOCK_ROOTS) if a.solo_fresh_0903 else list(S.BLOCK_ROOTS)
    try:
        return run(blocks, a.out, a.precount, a.stake_sol, a.workers)
    except (Refused, S.Refused) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
