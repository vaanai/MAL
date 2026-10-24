"""P3 EXITS dispatcher (hunt29). PAPER ONLY, exploration screen. Patches tools.arena_screen._price_exit with an exit resolver that adds, for named cells only:
  * tp against the POST-BUY MARK (the frozen FLOOR spec, hunt24 l1_rule_tpfix.py; used for every cell with tp set and no sl / trail),
  * an armed trail ("trailing after +X"), and a burst sell (buy SOL in the trailing 60 s window), each optionally with the tp.
Everything else (time-only, sl / trail cells) goes to the kit's original resolver unchanged. Causality: a decision at print j uses rows with index <= j only.
Reason codes (kit codes only): 1 = tp, 3 = trail-after OR burst (a cell carries one of the two, never both), 0 = time cap."""
import numpy as np
import tools.arena_screen as S
from tools import arena_stack as F

STAKE = 0.25e9
LAT = 1.9
BURST_WIN_S = 60.0
CUSTOM = {}  # cell name -> options: trail_arm, trail (x running max of R), burst (lamports), burst_rel, burst_min
_orig = S._price_exit


def register(cell, **opt):
    CUSTOM[cell] = opt


def mark_state(a, sd, sps):
    """(mark, net, tok, je) at the landing state, as tpfix: None when unusable."""
    sl, ib, so, tk, q, b, v = a["sl"], a["ib"], a["so"], a["tk"], a["q"], a["b"], a["v"]
    qL = q[-1] + so[-1] * (1 - F.FINAL_FEE) if ib[-1] else q[-1] - so[-1] / (1 - F.FINAL_FEE)
    bL = b[-1] - tk[-1] if ib[-1] else b[-1] + tk[-1]
    QX = np.append(q, qL) + np.append(v, v[-1])
    BX = np.append(b, bL)
    k = int(F.k_slots_vec(LAT, np.array([sps]), "ceil")[0])
    je = int(np.searchsorted(sl, sd + S.D + k + 1, "left"))
    Qe, Be = float(QX[je]), float(BX[je])
    net = STAKE * (1 - float(F.fee_ppm(Qe, Be)) / 1e6)
    tok = Be * net / (Qe + net)
    if not (np.isfinite(tok) and 0 < tok < Be):
        return None
    return (Qe + net) / (Be - tok), net, tok


def r_series(a, net, tok):
    q, b, v, ib, so, tk = a["q"], a["b"], a["v"], a["ib"], a["so"], a["tk"]
    qp = np.where(ib, q + so * 0.99, q - so * 1.01) + v
    bp = np.where(ib, b - tk, b + tk)
    bp = np.where(bp > 0, bp, b)
    return (qp + net) / np.maximum(bp - tok, 1.0)


def exit_p3(a, pp, sd, spec, rate, sps):
    cell = getattr(spec, "cell", None)
    opt = CUSTOM.get(cell)
    if opt is None and not (spec.tp is not None and spec.sl is None and spec.trail is None):
        return _orig(a, pp, sd, spec, rate, sps)
    opt = opt or {}
    sl = a["sl"]
    floor_slot, xs, clamped = S._exit_clock(spec, sd, rate, sps)
    j0, j1 = int(np.searchsorted(sl, floor_slot, "left")), int(np.searchsorted(sl, xs, "right"))
    if j1 <= j0:
        return xs, 0, clamped
    ms = mark_state(a, sd, sps)
    if ms is None:
        return _orig(a, pp, sd, spec, rate, sps)
    mark, net, tok = ms
    seg = r_series(a, net, tok)[j0:j1]
    best = None  # (index in seg, code); strict '<' keeps the earlier-evaluated rule on a tie (tp first)
    if spec.tp is not None:
        m = np.flatnonzero(seg >= spec.tp * mark)
        if m.size:
            best = (int(m[0]), 1)
    if "trail_arm" in opt:
        ar = np.flatnonzero(seg >= opt["trail_arm"] * mark)
        if ar.size:
            cm = np.maximum.accumulate(seg)
            hit = seg <= opt["trail"] * cm
            hit[: int(ar[0]) + 1] = False
            m = np.flatnonzero(hit)
            if m.size and (best is None or m[0] < best[0]):
                best = (int(m[0]), 3)
    if "burst" in opt or "burst_rel" in opt:
        W = int(round(BURST_WIN_S * rate))
        cb = np.concatenate([[0.0], np.cumsum(np.where(a["ib"], a["so"], 0.0))])
        if "burst_rel" in opt:
            hi_ = int(np.searchsorted(sl, sd, "right"))
            lo_ = int(np.searchsorted(sl, sd - W, "right"))
            thr = max(opt["burst_rel"] * (cb[hi_] - cb[lo_]), opt["burst_min"])
        else:
            thr = opt["burst"]
        idx = np.arange(j0, j1)
        ws = np.searchsorted(sl, sl[idx] - W, "right")
        m = np.flatnonzero(cb[idx + 1] - cb[ws] >= thr)
        if m.size and (best is None or m[0] < best[0]):
            best = (int(m[0]), 3)
    if best is not None:
        return int(sl[j0 + best[0]]), best[1], clamped
    return xs, 0, clamped


def install():
    S._price_exit = exit_p3


for _c, _o in (("TR30", dict(trail_arm=1.30, trail=0.80)), ("TR50", dict(trail_arm=1.50, trail=0.80)), ("TR100", dict(trail_arm=2.00, trail=0.80)),
               ("B2", dict(burst=2e9)), ("B5", dict(burst=5e9)), ("BR", dict(burst_rel=3.0, burst_min=1e9)),
               ("GB2", dict(burst=2e9)), ("GBR", dict(burst_rel=3.0, burst_min=1e9))):
    register(_c, **_o)
