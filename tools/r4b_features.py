"""r4-b feature builder: a line-for-line port of the l3 per-mint feature loop (numpy only).

Source: /data/mal/hunt-1008/l3-laya-v1-postgrad/scripts/02_build.py (per-mint loop, lines ~42-112; post-order-fix md5
535ac7062ab0eb15c2d08bceb9175710), 02a_prep.py (clock, dense market cumsums, mint constants) and common.py (fee tiers, seed state).
Feature semantics are NOT changed. The deliberate differences are the PLAN s1 parameters, all of which reduce to the exploration
behaviour when the exploration values are passed:

* vq        per-print virtual quote reserve V (lamports). None -> constant V0 for every print (exploration path, bit-identical).
* V0        the seed price and c_v use V0.
* seg_end   exploration passes the segment end (grid stops at seg_end - 4h - 600 s); None in forward (grid runs to mbt + 48 h).
* grid      2-min grid from ceil120(mbt + 600) to the end above; a decision exists iff the token printed in (T - 600, T).
* features  use only prints with slot < sd, sd = clock_slot(T); market-minute features use complete minutes only.

``grads_rule`` selects the m_grads_1h / m_grads_10m rule:

* "frozen" (default): GR = all members' mbt, a graduation counts at T iff mbt < T. Membership means the V pool later printed, so a recent
  graduation can count only because of a print after T (r4b-verify REPORT, low CONCERN). Byte-identical to the fixture.
* "asof_pda_first_print" (forward rule, QP ruling 2026-10-16): a graduation counts at decision T iff mbt < T AND its canonical PDA pool's
  first print has slot < sd(T). Raw migration events are NOT counted: pools that never print stay out, matching training. Needs
  ``Prep.gr_first_slot`` (first-print slot per graduation, aligned with the mbt vector; NaN = never printed) or it raises ValueError.

Creator hash ``ch``: forward inputs keep th / ch as uint64 end to end (bond_constants compares them exactly, as the training SQL did).
Training (02_build) compared the uint64 print hashes with a pandas float64 ch, so numpy cast BOTH sides to float64 and hashes that
collide after the 53-bit rounding matched. The frozen models were trained on those features, so ``mint_features`` applies the same float64
cast at the feature boundary (cre_known, cre_sell_post, cre_buy_post) for any ch dtype. FLAG FOR QP: this is a training-fidelity choice,
not an exactness one; the other uint64 uses (unique traders, bond sums) are exact in training and stay exact here.

Print order in forward mode: (slot, coalesce(tx_index, -1), event_index, file_row_number) (see forward_order,
null_txi_tie_groups).

No tape or network access in this module.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# ---- common.py -------------------------------------------------------------------------------------------------------------------
SEED_Q = 67_405_853_863
SEED_B = 206_900_000_000_000
# tools/paper_curve_math.PUMPSWAP_SOL_FEE_TIERS (mcap SOL inclusive lower bound, ppm)
TIERS = (
    (0, 12_500), (420, 12_000), (1470, 11_500), (2460, 11_000), (3440, 10_500), (4420, 10_000), (9820, 9_500), (14740, 9_000),
    (19650, 8_500), (24560, 8_000), (29470, 7_500), (34380, 7_000), (39300, 6_500), (44210, 6_000), (49120, 5_500), (54030, 5_250),
    (58940, 5_000), (63860, 4_750), (68770, 4_500), (73681, 4_250), (78590, 4_000), (83500, 3_750), (88400, 3_500), (93330, 3_250),
    (98240, 3_000),
)
TH = np.array([t for t, _ in TIERS], float)
TPP = np.array([p for _, p in TIERS], float)
MAXH = 4 * 3600
MARGIN = 600


def fee_ppm(Q, B):
    """cap_pick_score.fee_ppm: mcap = q / (b * 1000.0) * 1e9, q in lamports incl V, b raw base units."""
    mcap = np.asarray(Q, float) / (np.asarray(B, float) * 1000.0) * 1e9
    idx = np.searchsorted(TH, mcap + 1e-9, side="right") - 1
    return TPP[np.clip(idx, 0, len(TPP) - 1)]


# 02_build.py FEATS; the frozen model order is features.txt = these names with an f_ prefix.
FEATS = [
    "age_s", "t_since_create", "r_seed", "ret_60", "ret_300", "ret_900", "ret_3600", "ret_14400", "dd_ath", "rebound_atl", "t_since_ath",
    "ath_seed", "rv_300", "rv_900", "rv_3600", "np_300", "np_900", "np_3600", "np_all", "bv_300", "sv_300", "bv_900", "sv_900", "bv_3600",
    "sv_3600", "nf_900_liq", "nf_3600_liq", "bv_all", "sv_all", "lnet_900", "lnet_3600", "nlarge_3600", "ub_900", "us_900", "ub_3600",
    "us_3600", "uniq_all", "buyfrac_900", "avgsz_900", "liq_sol", "fee_tier", "t_since_last", "cre_sell_post", "cre_buy_post", "cre_known",
    "hour_sin", "hour_cos", "m_grads_1h", "m_grads_10m", "m_vps_10m", "m_nps_10m", "m_vbd_10m", "m_ncr_10m", "m_ncr_60m", "m_vps_60m",
    "c_bond_dur", "c_nbond", "c_nbond_tr", "c_bond_buy", "c_bond_sell", "c_cre_bond_sell", "c_pool_delay", "c_v",
]
MODEL_FEATURES = ["f_" + k for k in FEATS]
assert len(FEATS) == 63

_MKT_COLS = ("n_ps", "v_ps", "n_bd", "v_bd", "n_cr")


# ---- 02a_prep.py -----------------------------------------------------------------------------------------------------------------
@dataclass
class Prep:
    """Global clock, dense minute-market cumsums and the graduation time vectors (02a_prep + the head of 02_build)."""

    clock_bt: np.ndarray            # block_time seconds, ascending, unique
    clock_slot: np.ndarray          # first (min) slot seen in that second
    m0: int                         # first market minute (epoch s)
    nm: int                         # number of market minutes
    cum: dict = field(default_factory=dict)   # col -> cumsum array, a[i] = sum of minutes < m0 + 60*i
    gr: np.ndarray = None           # sorted mbt of all members (universe-conditioned)
    gr_first_slot: np.ndarray | None = None   # first-print slot of each graduation's canonical pool, aligned with gr (asof rule)
    _gc: dict = field(default_factory=dict, repr=False)

    def slot_at(self, t):
        """02_build clock_slot: first slot whose block_time >= t (clamped to the last known second)."""
        i = np.searchsorted(self.clock_bt, t, "left")
        i = np.minimum(i, len(self.clock_bt) - 1)
        return self.clock_slot[i]

    def grads_asof(self, T, sd):
        """(m_grads_1h, m_grads_10m) under the as-of rule: mbt in [T-w, T) and first-print slot < sd. Cached per (T, sd) grid point."""
        if self.gr_first_slot is None:
            raise ValueError("grads_rule='asof_pda_first_print' needs Prep.gr_first_slot (first-print slot of each graduation's PDA pool)")
        out = np.zeros((2, len(T)), np.int64)
        for i, (t, s) in enumerate(zip(T.tolist(), sd.tolist())):
            key = (t, s)
            v = self._gc.get(key)
            if v is None:
                lo1, lo2, hi = (np.searchsorted(self.gr, x, "left") for x in (t - 3600, t - 600, t))
                fs = self.gr_first_slot
                v = (int((fs[lo1:hi] < s).sum()), int((fs[lo2:hi] < s).sum()))   # NaN < s is False: never-printed pools stay out
                self._gc[key] = v
            out[0, i], out[1, i] = v
        return out

    def mwin(self, col, t, w):
        """Complete minutes in [t - w, t)."""
        hi = np.clip((t - self.m0) // 60, 0, self.nm).astype(int)
        lo = np.clip((t - w - self.m0) // 60, 0, self.nm).astype(int)
        return self.cum[col][hi] - self.cum[col][lo]


def build_prep(clock_bt, clock_slot, mnt, mkt_cols, gr, gr_first_slot=None) -> Prep:
    """clock_bt/clock_slot: per-second min slot (01_extract clock, grouped as in 02a_prep). mnt: minute starts ascending; mkt_cols: dict
    n_ps, v_ps, n_bd, v_bd, n_cr aligned to mnt (n_cr already left-joined, 0 when absent)."""
    ckb = np.asarray(clock_bt, np.int64)
    cks = np.asarray(clock_slot, np.int64)
    mnt = np.asarray(mnt, np.float64)
    m0 = int(mnt[0])
    nm = int(mnt[-1] - m0) // 60 + 1
    cum = {}
    for k in _MKT_COLS:
        a = np.zeros(nm + 1)
        a[1:][((mnt - m0) // 60).astype(int)] += np.asarray(mkt_cols[k], np.float64)
        cum[k] = np.cumsum(a)
    gr = np.asarray(gr).astype(np.int64)
    o = np.argsort(gr, kind="stable")
    fs = None if gr_first_slot is None else np.asarray(gr_first_slot, np.float64)[o]
    return Prep(ckb, cks, m0, nm, cum, gr[o], fs)


def build_prep_from_rows(clock_rows, mkt_rows, creates_rows, gr, gr_first_slot=None) -> Prep:
    """02a_prep aggregation from per-hour rows: clock_rows (bt, smin) pairs -> min slot per second; mkt_rows (mnt, n_ps, v_ps, n_bd, v_bd)
    summed per minute; creates_rows (mnt, n_cr). All arguments are dicts of equal-length numpy arrays."""
    bt = np.asarray(clock_rows["bt"], np.int64)
    smin = np.asarray(clock_rows["smin"], np.int64)
    o = np.lexsort((smin, bt))
    bt, smin = bt[o], smin[o]
    first = np.concatenate([[True], bt[1:] != bt[:-1]])
    mn = np.asarray(mkt_rows["mnt"], np.int64)
    um, inv = np.unique(mn, return_inverse=True)
    cols = {}
    for k in ("n_ps", "v_ps", "n_bd", "v_bd"):
        s = np.zeros(len(um))
        np.add.at(s, inv, np.asarray(mkt_rows[k], np.float64))
        cols[k] = s
    ncr = np.zeros(len(um))
    cm = np.asarray(creates_rows["mnt"], np.int64)
    pos = np.searchsorted(um, cm)
    okc = (pos < len(um)) & (um[np.minimum(pos, len(um) - 1)] == cm)
    ncr[pos[okc]] = np.asarray(creates_rows["n_cr"], np.float64)[okc]
    cols["n_cr"] = ncr
    return build_prep(bt[first], smin[first], um, cols, gr, gr_first_slot)


@dataclass
class MintConst:
    """One row of mintconst (02a_prep). NaN where the SQL left join is null."""

    mid: int
    mbt: int
    mslot: int
    s0: int
    v: float                 # V0
    cbt: float = float("nan")     # creator block time (create event)
    has_creator: bool = False     # ch is not null
    nb: float = float("nan")
    ntr: float = float("nan")
    bbuy: float = float("nan")
    bsell: float = float("nan")
    cre_sell: float = float("nan")


def bond_constants(bond_isbuy, bond_sol, bond_th, creator_hash, has_creator):
    """02a_prep bonding aggregate for one mint over its bonding prints (slot < mslot + 1). No rows -> all NaN (left join null).
    A mint without a known creator has cre_sell 0 (th = NULL is never true), as in the SQL."""
    n = len(bond_isbuy)
    if n == 0:
        return dict(nb=np.nan, ntr=np.nan, bbuy=np.nan, bsell=np.nan, cre_sell=np.nan)
    ib = np.asarray(bond_isbuy, bool)
    sol = np.asarray(bond_sol, np.float64)
    th = np.asarray(bond_th)
    cre = float(sol[(~ib) & (th == creator_hash)].sum()) if has_creator else 0.0
    return dict(nb=float(n), ntr=float(len(np.unique(th))), bbuy=float(sol[ib].sum()), bsell=float(sol[~ib].sum()), cre_sell=cre)


def forward_order(slot, tx_index, event_index, frn):
    """Total print order for forward mode: (slot, tx_index, event_index, file_row_number); null tx_index (None/NaN/negative) is coalesced to -1 so it sorts FIRST
    within the slot, exactly like training (01_extract.py:20: coalesce(tx_index,-1)), and never creates a tie (frn breaks it). Returns the permutation. frn must be non-null and, within equal
    (slot, tx, evi), unique, otherwise ValueError (no silent ties)."""
    slot = np.asarray(slot, np.int64)
    txi = _coalesce_i(tx_index)
    txi = np.where(txi < 0, -1, txi)   # nulls first, as training (coalesce(tx_index, -1))
    evi = _coalesce_i(event_index)
    if frn is None or np.isnan(np.asarray(frn, float)).any():
        raise ValueError("file_row_number is required for the forward print order")
    frn = np.asarray(frn, np.int64)
    perm = np.lexsort((frn, evi, txi, slot))
    k = np.stack([slot, txi, evi, frn])[:, perm]
    if len(perm) > 1 and (k[:, 1:] == k[:, :-1]).all(axis=0).any():
        raise ValueError("duplicate (slot, tx_index, event_index, file_row_number): order is not total")
    return perm


def mixed_null_txi_slots(mid, slot, tx_index) -> int:
    """Precount helper: number of (mint, slot) pairs that mix null and non-null tx_index. The precount REFUSES if any read hour has such a
    pair (QP requirement): the within-slot order of a mint's prints would then depend on the null placement. Different mints sharing a slot
    do not count. Exploration check 2026-10-16: 0 such pairs in all 36 l3 ut/ days."""
    mid = np.asarray(mid, np.int64)
    slot = np.asarray(slot, np.int64)
    a = np.asarray(tx_index, float)
    null = np.isnan(a) | (a < 0)
    if not null.any() or null.all():
        return 0
    k = np.stack([mid, slot], axis=1)
    kn = np.unique(k[null], axis=0)
    kg = np.unique(k[~null], axis=0)
    both = np.concatenate([kn, kg])
    _, cnt = np.unique(both, axis=0, return_counts=True)
    return int((cnt == 2).sum())


def _coalesce_i(x):
    a = np.asarray(x, float) if not isinstance(x, np.ndarray) or x.dtype.kind not in "iu" else x
    if a.dtype.kind == "f":
        a = np.where(np.isnan(a), -1, a)
    return a.astype(np.int64)


def null_txi_tie_groups(slot, tx_index, event_index) -> int:
    """Precount helper: number of groups of >= 2 rows with null tx_index sharing (slot, event_index) (rows that would tie without frn)."""
    slot = np.asarray(slot, np.int64)
    a = np.asarray(tx_index, float)
    null = np.isnan(a) | (a < 0)
    if not null.any():
        return 0
    k = np.stack([slot[null], _coalesce_i(event_index)[null]], axis=1)
    _, cnt = np.unique(k, axis=0, return_counts=True)
    return int((cnt > 1).sum())


def _prev_same(keys):
    n = len(keys)
    o = np.argsort(keys, kind="stable")
    ks = keys[o]
    p = np.full(n, -1, np.int64)
    same = ks[1:] == ks[:-1]
    p[o[1:][same]] = o[:-1][same]
    return p


@dataclass
class MintResult:
    X: np.ndarray        # (m, 63) float32, FEATS order
    I: np.ndarray        # (m, 3) int64: mid, T, sd
    state: dict | None = None


def mint_features(prep: Prep, c: MintConst, ch, slot, bt, isbuy, sol, tok, q, b, th, *, vq=None, seg_end=None,
                  grads_rule: str = "frozen", return_state: bool = False) -> MintResult | None:
    """Features for one mint at every decision on its 2-min grid (02_build per-mint loop).

    Arrays are the mint's canonical-pool prints ordered by (slot, tx_index, event_index[, frn]); ``ch`` is the creator hash (NaN/None if
    unknown). ``vq``: per-print V (lamports, same length) or None for the constant c.v. Returns None where 02_build `continue`s.
    """
    n = len(slot)
    if n < 3:
        return None
    r = c
    V0 = float(r.v)
    mbt = int(r.mbt)
    sl = np.asarray(slot)
    bt = np.asarray(bt).astype(float)
    ib = np.asarray(isbuy).astype(bool)
    so = np.asarray(sol).astype(float)
    tk = np.asarray(tok).astype(float)
    q = np.asarray(q).astype(float)
    bb = np.asarray(b).astype(float)
    th = np.asarray(th)
    vqa = np.full(n, V0) if vq is None else np.asarray(vq).astype(float)
    assert len(vqa) == n
    bt = np.where(np.isfinite(bt), bt, -1)
    btm = np.maximum.accumulate(bt)
    # state before print i = pre-trade reserves of print i (+V_i); entry n = derived state after the last print (G constant 1.25%)
    qL = q[-1] + so[-1] * (1 - 0.0125) if ib[-1] else q[-1] - so[-1] / (1 - 0.0125)
    bL = bb[-1] - tk[-1] if ib[-1] else bb[-1] + tk[-1]
    QX = np.append(q, qL) + np.append(vqa, vqa[-1])
    BX = np.append(bb, bL)
    if (QX <= 0).any() or (BX <= 0).any():
        return None
    # feature price after print i (approx post-trade, from the row itself)
    qp = np.where(ib, q + so * 0.99, q - so * 1.01) + vqa
    bp = np.where(ib, bb - tk, bb + tk)
    bp = np.where(bp > 0, bp, bb)
    pp = qp / bp
    seed_p = (SEED_Q + V0) / SEED_B
    with np.errstate(divide="ignore", invalid="ignore"):
        lpp = np.log(pp)
    lr = np.diff(np.concatenate([[math.log(seed_p)], lpp]))
    cs2 = np.concatenate([[0], np.cumsum(lr * lr)])
    cbv = np.concatenate([[0], np.cumsum(np.where(ib, so, 0))])
    csv_ = np.concatenate([[0], np.cumsum(np.where(ib, 0, so))])
    lg = so >= 1e9
    cln = np.concatenate([[0], np.cumsum(np.where(lg, np.where(ib, so, -so), 0))])
    cnl = np.concatenate([[0], np.cumsum(lg)])
    cmax = np.maximum.accumulate(lpp)
    cmin = np.minimum.accumulate(lpp)
    iath = np.maximum.accumulate(np.where(lpp >= cmax, np.arange(n), 0))
    ps = _prev_same(th)
    first = np.concatenate([[0], np.cumsum(ps < 0)])
    bidx = np.nonzero(ib)[0]
    sidx = np.nonzero(~ib)[0]
    pb = _prev_same(th[bidx])
    pS = _prev_same(th[sidx])
    cbt_nan = bool(np.isnan(r.cbt))
    ck = 1.0 if (ch is not None and not (isinstance(ch, float) and math.isnan(ch))) else 0.0
    # training compared uint64 th with a float64 ch: both sides were cast to float64 (53-bit rounding). Same cast here for any ch dtype.
    isc = (th.astype(np.float64) == np.float64(ch)) if ck else np.zeros(n, bool)
    ccs = np.concatenate([[0], np.cumsum(np.where(isc & ~ib, so, 0))])
    ccb = np.concatenate([[0], np.cumsum(np.where(isc & ib, so, 0))])
    t_lo = ((mbt + 600 + 119) // 120) * 120
    t_hi = mbt + 48 * 3600 if seg_end is None else min(mbt + 48 * 3600, seg_end - MAXH - MARGIN)
    if t_hi < t_lo:
        return None
    T = np.arange(t_lo, t_hi + 1, 120, dtype=np.int64)
    sd = prep.slot_at(T)
    j = np.searchsorted(sl, sd, "left")
    ok = j > 0
    jj = np.maximum(j - 1, 0)
    ok &= (btm[jj] >= T - 600) & (btm[jj] < T)
    if not ok.any():
        return None
    T = T[ok]
    sd = sd[ok]
    j = j[ok]
    jl = j - 1
    m = len(T)
    P = lpp[jl]
    F = {}
    F["age_s"] = T - mbt
    F["t_since_create"] = (T - r.cbt) if not cbt_nan else np.full(m, np.nan)
    F["r_seed"] = P - math.log(seed_p)
    jw = {}
    for w in (60, 300, 900, 3600, 14400):
        jw[w] = np.searchsorted(btm, T - w, "left")
    for w in (60, 300, 900, 3600, 14400):
        k = jw[w]
        F[f"ret_{w}"] = P - np.where(k > 0, lpp[np.maximum(k - 1, 0)], math.log(seed_p))
    F["dd_ath"] = P - cmax[jl]
    F["rebound_atl"] = P - cmin[jl]
    F["t_since_ath"] = T - btm[iath[jl]]
    F["ath_seed"] = cmax[jl] - math.log(seed_p)
    for w in (300, 900, 3600):
        F[f"rv_{w}"] = cs2[j] - cs2[jw[w]]
        F[f"np_{w}"] = j - jw[w]
        F[f"bv_{w}"] = (cbv[j] - cbv[jw[w]]) / 1e9
        F[f"sv_{w}"] = (csv_[j] - csv_[jw[w]]) / 1e9
    F["np_all"] = j.astype(float)
    liq = qp[jl] / 1e9
    with np.errstate(divide="ignore", invalid="ignore"):
        F["nf_900_liq"] = (F["bv_900"] - F["sv_900"]) / liq
        F["nf_3600_liq"] = (F["bv_3600"] - F["sv_3600"]) / liq
    F["bv_all"] = cbv[j] / 1e9
    F["sv_all"] = csv_[j] / 1e9
    F["lnet_900"] = (cln[j] - cln[jw[900]]) / 1e9
    F["lnet_3600"] = (cln[j] - cln[jw[3600]]) / 1e9
    F["nlarge_3600"] = cnl[j] - cnl[jw[3600]]
    for w in (900, 3600):
        lo = jw[w]
        ub = np.zeros(m)
        us = np.zeros(m)
        blo = np.searchsorted(bidx, lo)
        bhi = np.searchsorted(bidx, j)
        slo = np.searchsorted(sidx, lo)
        shi = np.searchsorted(sidx, j)
        for i in range(m):
            if bhi[i] > blo[i]:
                ub[i] = (pb[blo[i]:bhi[i]] < blo[i]).sum()
            if shi[i] > slo[i]:
                us[i] = (pS[slo[i]:shi[i]] < slo[i]).sum()
        F[f"ub_{w}"] = ub
        F[f"us_{w}"] = us
    F["uniq_all"] = first[j]
    with np.errstate(divide="ignore", invalid="ignore"):
        F["buyfrac_900"] = np.where(F["np_900"] > 0, (np.searchsorted(bidx, j) - np.searchsorted(bidx, jw[900])) / np.maximum(F["np_900"], 1), np.nan)
        F["avgsz_900"] = np.where(F["np_900"] > 0, (F["bv_900"] + F["sv_900"]) / np.maximum(F["np_900"], 1), np.nan)
    F["liq_sol"] = liq
    F["fee_tier"] = fee_ppm(qp[jl], bp[jl])
    F["t_since_last"] = T - btm[jl]
    F["cre_sell_post"] = ccs[j] / 1e9
    F["cre_buy_post"] = ccb[j] / 1e9
    F["cre_known"] = np.full(m, ck)
    hr = (T % 86400) / 3600.0
    F["hour_sin"] = np.sin(2 * np.pi * hr / 24)
    F["hour_cos"] = np.cos(2 * np.pi * hr / 24)
    if grads_rule == "frozen":
        F["m_grads_1h"] = np.searchsorted(prep.gr, T, "left") - np.searchsorted(prep.gr, T - 3600, "left")
        F["m_grads_10m"] = np.searchsorted(prep.gr, T, "left") - np.searchsorted(prep.gr, T - 600, "left")
    elif grads_rule == "asof_pda_first_print":
        g = prep.grads_asof(T, sd)
        F["m_grads_1h"], F["m_grads_10m"] = g[0], g[1]
    else:
        raise ValueError(f"unknown grads_rule {grads_rule!r}")
    F["m_vps_10m"] = prep.mwin("v_ps", T, 600) / 1e9
    F["m_nps_10m"] = prep.mwin("n_ps", T, 600)
    F["m_vbd_10m"] = prep.mwin("v_bd", T, 600) / 1e9
    F["m_ncr_10m"] = prep.mwin("n_cr", T, 600)
    F["m_ncr_60m"] = prep.mwin("n_cr", T, 3600)
    F["m_vps_60m"] = prep.mwin("v_ps", T, 3600) / 1e9
    nan = np.nan
    F["c_bond_dur"] = np.full(m, (mbt - r.cbt) if not cbt_nan else nan)
    F["c_nbond"] = np.full(m, r.nb if not np.isnan(r.nb) else nan)
    F["c_nbond_tr"] = np.full(m, r.ntr if not np.isnan(r.ntr) else nan)
    F["c_bond_buy"] = np.full(m, (r.bbuy or 0) / 1e9)
    F["c_bond_sell"] = np.full(m, (r.bsell or 0) / 1e9)
    F["c_cre_bond_sell"] = np.full(m, (r.cre_sell if not np.isnan(r.cre_sell) else nan) / 1e9)
    F["c_pool_delay"] = np.full(m, float(r.s0 - r.mslot))
    F["c_v"] = np.full(m, V0 / 1e9)
    X = np.column_stack([np.asarray(F[k], float) for k in FEATS]).astype(np.float32)
    I = np.column_stack([np.full(m, c.mid), T, sd]).astype(np.int64)
    st = None
    if return_state:
        st = dict(sl=sl, QX=QX, BX=BX, cbv=cbv, ib=ib, lpp=lpp, pp=pp, P=P, j=j, T=T, sd=sd)
    return MintResult(X, I, st)
