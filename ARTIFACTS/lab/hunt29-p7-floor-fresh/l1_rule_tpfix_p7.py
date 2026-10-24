"""L1 rule with the take-profit reference of the FROZEN spec (iter-r1 spec.py): TP when the post-trade spot WITH OUR POSITION KEPT is >= 1.5 x the POST-BUY MARK
(mark = (Qe + net) / (Be - tokens) at the landing state), not 1.5 x the decision-slot spot of the kit's default resolver. Everything else (trigger, universe, guard, costs, time exit) is the
unchanged kit path. This module patches tools.arena_screen._price_exit at import; it is a reconciliation run (RECONCILE.md), not a new rule and not a retune."""
import sys
sys.dont_write_bytecode = True
import numpy as np
import tools.arena_screen as S
from tools import arena_stack as F
import l1_rule_p7 as l1_rule  # P7: the same module with one extra block key

_orig = S._price_exit
STAKE = 0.25e9
LAT = 1.9


def _price_exit_fix(a, pp, sd, spec, rate, sps):
    if spec.tp is None:
        return _orig(a, pp, sd, spec, rate, sps)
    sl = a['sl']
    floor_slot, xs, clamped = S._exit_clock(spec, sd, rate, sps)
    j0, j1 = int(np.searchsorted(sl, floor_slot, 'left')), int(np.searchsorted(sl, xs, 'right'))
    if j1 <= j0:
        return xs, 0, clamped
    ib, so, tk, q, b, v = a['ib'], a['so'], a['tk'], a['q'], a['b'], a['v']
    n = len(sl)
    qL = q[-1] + so[-1] * (1 - F.FINAL_FEE) if ib[-1] else q[-1] - so[-1] / (1 - F.FINAL_FEE)
    bL = b[-1] - tk[-1] if ib[-1] else b[-1] + tk[-1]
    QX = np.append(q, qL) + np.append(v, v[-1])
    BX = np.append(b, bL)
    k = int(F.k_slots_vec(LAT, np.array([sps]), 'ceil')[0])
    je = int(np.searchsorted(sl, sd + S.D + k + 1, 'left'))
    Qe, Be = float(QX[je]), float(BX[je])
    net = STAKE * (1 - float(F.fee_ppm(Qe, Be)) / 1e6)
    tok = Be * net / (Qe + net)
    if not (np.isfinite(tok) and tok > 0 and tok < Be):
        return _orig(a, pp, sd, spec, rate, sps)
    mark = (Qe + net) / (Be - tok)
    qp = np.where(ib, q + so * 0.99, q - so * 1.01) + v
    bp = np.where(ib, b - tk, b + tk)
    bp = np.where(bp > 0, bp, b)
    seg = ((qp + net) / np.maximum(bp - tok, 1.0))[j0:j1]
    m = np.flatnonzero(seg >= spec.tp * mark)
    if m.size:
        return int(sl[j0 + int(m[0])]), 1, clamped
    return xs, 0, clamped


S._price_exit = _price_exit_fix
_m = sys.modules.get('__main__')  # `python -m tools.arena_screen` runs the kit as __main__, a second module object: patch it too
if _m is not None and hasattr(_m, '_price_exit') and hasattr(_m, 'resolve_exit'):
    _m._price_exit = _price_exit_fix
PATCHED_MAIN = bool(_m is not None and getattr(_m, '_price_exit', None) is _price_exit_fix)
L1Rule = l1_rule.L1Rule
