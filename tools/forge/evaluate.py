"""forge evaluate: DSL validation, vectorised strategy evaluation on the event table, metrics and the fitness. PAPER only.

evaluate(spec, split) -> metrics dict. All hours of a split are loaded once per process (features in RAM, paths memory-mapped); an evaluation then costs a boolean
mask over the features, a cooldown pass, a path scan over the selected events only, and a bootstrap.

DSL (see validate_spec): {"entry": [{"f","op","v"}, ...<=6], "venue": 0|1|"any", "cooldown_s", "one_per_pool", "exit": {"tp","sl","trail","max_hold_s"}, "stake_sol"}.
  cooldown_s: after a candidate is kept in a pool, further candidates of that pool within cooldown_s are dropped (pass 1, entry times only).
  one_per_pool: a candidate whose decision time is before the previous kept trade's exit time in the same pool is dropped (pass 2, on simulated exits).

Fill and exit (events.py has the fill): stake S buys at the fill print's PRE-state (Qf, Bf) after the fee (PumpSwap tier / curve 1.25%) and constant-product impact.
The exit path is the pool spot ratio on the grid (common.GRID_S, seconds after the fill). Pool state at ratio r is rebuilt from constant product k = Qf*Bf
(Qx = sqrt(k r Qf/Bf)...), our own buy is re-injected like arena_stack.round_trip (Qa = Qx + net, Ba = Bx - tokens), proceeds = tokens*Qa/(Ba+tokens)*(1-fee_out).
Net = proceeds - stake - 2 * 160,000 lamports. Rent 0. No haircut. A dead pool (price 0) or Ba <= 0 loses the stake and the buy send only.
Exit rule at grid point k (checked on non-gap, non-censored points only): stop (sl or trail) beats tp at the same point (pessimistic); a tp/sl/trail trigger executes
at point k+1 (one grid step of exit lag, >= 5 s); max_hold executes at the first grid point >= max_hold_s; a curve that completes before any exit is closed at its
last curve point ("migrated"). A trade whose exit point is censored (past the split end) is dropped and counted in `n_censored`.

FITNESS (one function, `fitness`): -inf if n < 40 or n_days < 6; -inf if ex_top3_total <= 0; else CI90_low * sqrt(trades_per_day), where CI90_low is the 5th percentile of
1,000 bootstrap means of net SOL per trade (seed 1, arena_stack.boot_lo) and trades_per_day = n / days in the split. If best_day_share > 0.5 the fitness is halved when it is
positive (a negative fitness is left as is: the penalty never rewards).
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from tools import arena_stack as F
from tools.forge import common as C

DEFAULT_ROOT = "/data2/mal/forge"
OPS = {"<": np.less, "<=": np.less_equal, ">": np.greater, ">=": np.greater_equal}
MAX_CAND = 400_000
MIN_N, MIN_DAYS = 40, 6
LAMPORTS = 1e9


class SpecError(ValueError):
    pass


def _num(x, name, lo=None, hi=None, lo_open=False):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise SpecError(f"{name} must be a finite number")
    if lo is not None and (x <= lo if lo_open else x < lo):
        raise SpecError(f"{name} out of range")
    if hi is not None and x > hi:
        raise SpecError(f"{name} out of range")
    return float(x)


def validate_spec(spec: dict) -> dict:
    """Return a normalised copy or raise SpecError."""
    if not isinstance(spec, dict):
        raise SpecError("spec must be an object")
    extra = set(spec) - {"entry", "venue", "cooldown_s", "one_per_pool", "exit", "stake_sol"}
    if extra:
        raise SpecError(f"unknown keys {sorted(extra)}")
    ent = spec.get("entry", [])
    if not isinstance(ent, list) or len(ent) > 6:
        raise SpecError("entry must be a list of at most 6 conditions")
    conds = []
    for c in ent:
        if not isinstance(c, dict) or set(c) != {"f", "op", "v"}:
            raise SpecError("each condition needs exactly f, op, v")
        if c["f"] not in C.FIDX:
            raise SpecError(f"unknown feature {c['f']!r}")
        if c["op"] not in OPS:
            raise SpecError(f"bad op {c['op']!r}")
        conds.append({"f": c["f"], "op": c["op"], "v": _num(c["v"], "v")})
    venue = spec.get("venue", "any")
    if venue not in (0, 1, "any") or isinstance(venue, bool):
        raise SpecError("venue must be 0, 1 or 'any'")
    cd = spec.get("cooldown_s", 0)
    if isinstance(cd, bool) or not isinstance(cd, int) or not 0 <= cd <= 86400:
        raise SpecError("cooldown_s must be an int in [0, 86400]")
    opp = spec.get("one_per_pool", True)
    if not isinstance(opp, bool):
        raise SpecError("one_per_pool must be a bool")
    ex = spec.get("exit")
    if not isinstance(ex, dict) or set(ex) - {"tp", "sl", "trail", "max_hold_s"} or "max_hold_s" not in ex:
        raise SpecError("exit needs max_hold_s and optionally tp, sl, trail")
    tp, sl, tr = ex.get("tp"), ex.get("sl"), ex.get("trail")
    tp = None if tp is None else _num(tp, "tp", 0.0, 10.0, lo_open=True)
    sl = None if sl is None else _num(sl, "sl", 0.0, 0.99, lo_open=True)
    tr = None if tr is None else _num(tr, "trail", 0.0, 0.99, lo_open=True)
    mh = ex["max_hold_s"]
    if isinstance(mh, bool) or not isinstance(mh, int) or not 5 <= mh <= 86400:
        raise SpecError("max_hold_s must be an int in [5, 86400]")
    stake = _num(spec.get("stake_sol", 0.1), "stake_sol", 0.01, 1.0)
    return {"entry": conds, "venue": venue, "cooldown_s": cd, "one_per_pool": opp, "exit": {"tp": tp, "sl": sl, "trail": tr, "max_hold_s": mh}, "stake_sol": stake}


# ------------------------------------------------------------------------------------------------ data
class Split:
    def __init__(self, root: str, hours: list[str]):
        self.hours = []
        xs, ms = [], {k: [] for k in ("t", "t_fill", "pool", "venue", "Qf", "Bf", "fee_in")}
        self.paths, hid, row = [], [], []
        for h in hours:
            d = Path(root) / "events" / h
            if not (d / "DONE").exists() or json.loads((d / "DONE").read_text())["n"] == 0:
                if (d / "DONE").exists():
                    self.hours.append(h)
                continue
            self.hours.append(h)
            xs.append(np.load(d / "X.npy"))
            for k in ms:
                ms[k].append(np.load(d / f"{k}.npy"))
            self.paths.append(np.load(d / "path.npy", mmap_mode="r"))
            n = len(xs[-1])
            hid.append(np.full(n, len(self.paths) - 1, np.int32)); row.append(np.arange(n, dtype=np.int64))
        self.n = sum(len(x) for x in xs)
        self.X = np.asfortranarray(np.concatenate(xs)) if xs else np.zeros((0, C.NF), np.float32)  # column-major: a condition reads one contiguous column
        for k, v in ms.items():
            setattr(self, k, np.concatenate(v) if v else np.zeros(0))
        self.hid = np.concatenate(hid) if hid else np.zeros(0, np.int32)
        self.row = np.concatenate(row) if row else np.zeros(0, np.int64)
        self.n_days_split = max(len(self.hours) / 24.0, 1e-9)

    def gather_paths(self, sel: np.ndarray) -> np.ndarray:
        out = np.empty((len(sel), C.NG), np.uint16)
        h = self.hid[sel]
        for k in np.unique(h):
            m = np.flatnonzero(h == k)
            out[m] = self.paths[k][self.row[sel[m]]]
        return out


_CACHE: dict = {}


def load_split(split: str, root: str = DEFAULT_ROOT) -> Split:
    split = split.lower()
    key = (split, root)
    if key not in _CACHE:
        a, b = C.SPLITS[split]
        _CACHE[key] = Split(root, C.hour_range(a, b))
    return _CACHE[key]


# ------------------------------------------------------------------------------------------------ simulation
def first_true(m: np.ndarray, default: int) -> np.ndarray:
    any_ = m.any(axis=1)
    return np.where(any_, m.argmax(axis=1), default)


def simulate_paths(P: np.ndarray, ex: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """P: uint16 [m, NG]. Returns (kx exit grid index, r_exec ratio, reason code, censored bool). reason: 0 time, 1 sl, 2 trail, 3 tp, 4 migrated."""
    m = len(P)
    R = C.DECODE[P]
    special = P >= C.C_MIGRATED
    valid = ~special
    ar = np.arange(C.NG)
    idx = np.maximum.accumulate(np.where(valid, ar[None, :], 0), axis=1)
    Rf = np.take_along_axis(R, idx, axis=1)
    Rf = np.where(np.isnan(Rf), 1.0, Rf)
    NGi = C.NG
    big = 10 ** 6
    sl_m = np.zeros(P.shape, bool); tr_m = np.zeros(P.shape, bool); tp_m = np.zeros(P.shape, bool)
    if ex["sl"] is not None:
        sl_m = valid & (Rf <= 1.0 - ex["sl"])
    if ex["trail"] is not None:
        M = np.maximum(1.0, np.maximum.accumulate(np.where(valid, Rf, 0.0), axis=1))
        tr_m = valid & (Rf <= M * (1.0 - ex["trail"]))
    if ex["tp"] is not None:
        tp_m = valid & (Rf >= 1.0 + ex["tp"])
    stop = sl_m | tr_m
    kt = first_true(stop | tp_m, big)
    kk = np.minimum(kt, NGi - 1)
    r = np.arange(m)
    is_stop = stop[r, kk] & (kt < big)
    why_trig = np.where(is_stop, np.where(sl_m[r, kk], 1, 2), 3)
    k_time = int(np.searchsorted(C.GRID_S, ex["max_hold_s"], side="left"))
    k_mig = first_true(P == C.C_MIGRATED, big)
    k_cens = first_true(P == C.C_CENS, big)
    dec_trig = np.where(kt < big, kt, big)
    dec_mig = np.where(k_mig < big, k_mig - 1, big)
    dec_time = np.full(m, k_time)
    D = np.stack([dec_trig, dec_mig, dec_time], axis=1)
    which = np.argmin(D, axis=1)
    kx = np.where(which == 0, np.minimum(kt + 1, NGi - 1), np.where(which == 1, np.maximum(k_mig - 1, 0), k_time))
    reason = np.where(which == 0, why_trig, np.where(which == 1, 4, 0))
    censored = kx >= k_cens
    kx = np.minimum(kx, NGi - 1)
    return kx, Rf[r, kx].astype(np.float64), reason, censored


def trade_pnl(r, Qf, Bf, fee_in, venue, stake_lamports):
    """Net lamports of the round trip at exit ratio r (0 = dead)."""
    with np.errstate(all="ignore"):
        net = stake_lamports * (1.0 - fee_in)
        tok = Bf * net / (Qf + net)
        s = r * Qf / Bf
        k = Qf * Bf
        Qx, Bx = np.sqrt(k * s), np.sqrt(k / s)
        Qa, Ba = Qx + net, Bx - tok
        gross = tok * Qa / (Ba + tok)
        proceeds = gross * (1.0 - C.fee_frac(Qa, Ba, venue))
        pnl = proceeds - stake_lamports - 2 * C.SEND_LAMPORTS
    strand = ~(r > 0) | ~np.isfinite(pnl) | (Ba <= 0) | (tok <= 0)
    return np.where(strand, -stake_lamports - C.SEND_LAMPORTS, pnl)


def _cooldown(pool, t, cd_ms):
    """pass 1: indices (into the given arrays, sorted by pool then t) kept"""
    o = np.lexsort((t, pool))
    keep = np.zeros(len(o), bool)
    pl, tl = pool[o].tolist(), t[o].tolist()
    cur, last = None, 0
    for i in range(len(o)):
        if pl[i] != cur or tl[i] - last >= cd_ms:
            keep[i] = True
            cur, last = pl[i], tl[i]
    return o[keep]


def _one_per_pool(pool, t, exit_t):
    o = np.lexsort((t, pool))
    keep = np.zeros(len(o), bool)
    pl, tl, el = pool[o].tolist(), t[o].tolist(), exit_t[o].tolist()
    cur, free = None, 0
    for i in range(len(o)):
        if pl[i] != cur:
            cur, free = pl[i], -(1 << 62)
        if tl[i] >= free:
            keep[i] = True
            free = el[i]
    return o[keep]


def select(data: Split, spec: dict) -> np.ndarray:
    m = np.ones(data.n, bool)
    if spec["venue"] != "any":
        m &= data.venue == spec["venue"]
    for c in spec["entry"]:
        m &= OPS[c["op"]](data.X[:, C.FIDX[c["f"]]], np.float32(c["v"]))
    return np.flatnonzero(m)


def run_trades(data: Split, spec: dict) -> dict:
    """Simulate a validated spec. Returns arrays of the kept trades (pnl_sol, t, t_fill, exit_t, pool, reason) and counters."""
    sel = select(data, spec)
    out = {"n_candidates": int(len(sel)), "n_censored": 0}
    if len(sel) > MAX_CAND:
        out["too_broad"] = True
        sel = sel[:0]
    if spec["cooldown_s"] > 0 and len(sel):
        sel = sel[_cooldown(data.pool[sel], data.t[sel], spec["cooldown_s"] * 1000)]
    stake_l = spec["stake_sol"] * LAMPORTS
    pnl_l, kxs, rsn, cen = [], [], [], []
    B = 20_000
    for s in range(0, len(sel), B):
        ii = sel[s:s + B]
        kx, r, reason, c = simulate_paths(data.gather_paths(ii), spec["exit"])
        pnl_l.append(trade_pnl(r, data.Qf[ii], data.Bf[ii], data.fee_in[ii], data.venue[ii], stake_l))
        kxs.append(kx); rsn.append(reason); cen.append(c)
    if sel.size:
        pnl_l, kx, reason, cen = np.concatenate(pnl_l), np.concatenate(kxs), np.concatenate(rsn), np.concatenate(cen)
    else:
        pnl_l, kx, reason, cen = np.zeros(0), np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0, bool)
    out["n_censored"] = int(cen.sum())
    ok = ~cen
    sel, pnl_l, kx, reason = sel[ok], pnl_l[ok], kx[ok], reason[ok]
    exit_t = data.t_fill[sel] + C.GRID_S[kx] * 1000
    if spec["one_per_pool"] and len(sel):
        kp = _one_per_pool(data.pool[sel], data.t[sel], exit_t)
        sel, pnl_l, exit_t, reason = sel[kp], pnl_l[kp], exit_t[kp], reason[kp]
    o = np.argsort(data.t[sel], kind="stable")
    sel, pnl_l, exit_t, reason = sel[o], pnl_l[o], exit_t[o], reason[o]
    out.update(pnl=pnl_l / LAMPORTS, t=data.t[sel], t_fill=data.t_fill[sel], exit_t=exit_t, pool=data.pool[sel], reason=reason)
    return out


REASONS = ("time", "sl", "trail", "tp", "migrated")


def max_concurrent(start, end) -> int:
    if len(start) == 0:
        return 0
    ev = np.concatenate([np.stack([start, np.ones(len(start), np.int64)], 1), np.stack([end, -np.ones(len(end), np.int64)], 1)])
    ev = ev[np.lexsort((ev[:, 1], ev[:, 0]))]  # at equal time an exit (-1) is processed before an entry
    return int(np.max(np.cumsum(ev[:, 1])))


def fitness(m: dict) -> float:
    """-inf if n < 40 or n_days < 6 or ex_top3_total <= 0; else ci90_low * sqrt(trades_per_day); halved (if positive) when best_day_share > 0.5."""
    if m["n"] < MIN_N or m["n_days"] < MIN_DAYS:
        return -math.inf
    if m["ex_top3_total"] <= 0:
        return -math.inf
    f = m["ci90_low"] * math.sqrt(m["trades_per_day"])
    if m["best_day_share"] is not None and m["best_day_share"] > 0.5 and f > 0:
        f *= 0.5
    return float(f)


def metrics_of(pnl, t, t_fill, exit_t, reason, n_days_split, extra=None) -> dict:
    n = len(pnl)
    base = {"n": n, "n_days": 0, "mean": None, "median": None, "total": 0.0, "ex_top3_total": 0.0, "positive_days": 0, "best_day_share": None, "ci90_low": None,
            "trades_per_day": n / n_days_split, "max_concurrent": 0, "exit_reasons": {}}
    if extra:
        base.update(extra)
    if n:
        day = t // 86_400_000
        ud, inv = np.unique(day, return_inverse=True)
        dt = np.bincount(inv, weights=pnl)
        tot = float(pnl.sum())
        base.update(n_days=int(len(ud)), mean=float(pnl.mean()), median=float(np.median(pnl)), total=tot,
                    ex_top3_total=float(tot - np.sort(pnl)[-3:].sum()), positive_days=int((dt > 0).sum()),
                    best_day_share=(float(dt.max() / tot) if tot > 0 else None), ci90_low=float(F.boot_lo(pnl)),
                    max_concurrent=max_concurrent(t_fill, exit_t),
                    exit_reasons={REASONS[i]: int((reason == i).sum()) for i in range(len(REASONS)) if (reason == i).any()})
    base["fitness"] = fitness(base) if n else -math.inf
    return base


def evaluate(spec: dict, split: str = "train", root: str = DEFAULT_ROOT, data: Split | None = None) -> dict:
    split = split.lower()
    spec = validate_spec(spec)
    data = data or load_split(split, root)
    r = run_trades(data, spec)
    extra = {"n_candidates": r["n_candidates"], "n_censored": r["n_censored"]}
    if r.get("too_broad"):
        extra["too_broad"] = True
    return metrics_of(r["pnl"], r["t"], r["t_fill"], r["exit_t"], r["reason"], data.n_days_split, extra)


# ------------------------------------------------------------------------------------------------ team-facing helpers
_D = {
    "venue": "0 = pump.fun curve, 1 = PumpSwap", "age_s": "seconds since the pool's first print", "mig_age_s": "seconds since migration (PumpSwap; 0/neg for curve)",
    "liq_sol": "liquidity in SOL (quote reserve + V; curve real SOL)", "mcap_sol": "market cap in SOL",
    "ret_30s": "price return over 30 s", "ret_1m": "return over 1 m", "ret_5m": "return over 5 m", "ret_15m": "return over 15 m",
    "ret_1h": "return over 1 h", "ret_6h": "return over 6 h", "dd_high": "drawdown from the pool's running high (fraction)",
    "rise_low": "rise from the pool's running low (fraction)", "ev_is_buy": "1 if the event print is a buy else 0",
    "ev_sol": "SOL size of the event print", "since_last_s": "seconds since the previous trade on the pool",
    "curve_progress": "bonding-curve progress 0..1 (curve venue)", "utc_hour": "UTC hour 0..23",
}
_K = {"n": "trade count", "uniq": "unique traders", "buy_share": "buy share of trades 0..1", "buy_sol": "buy volume SOL", "sell_sol": "sell volume SOL",
      "max_sell": "largest single sell SOL", "max_buy": "largest single buy SOL", "net_flow": "buy minus sell SOL"}
for _w in ("1m", "5m", "1h"):
    for _k, _t in _K.items():
        _D[f"{_k}_{_w}"] = f"{_t} over the last {_w}"
FEATURES = {f: _D.get(f, f) for f in C.FEATURES}  # name -> one-line description (the team reads this)
ANALYST_N = 300_000
ANALYST_H = {"1m": 60, "5m": 300, "30m": 1800, "2h": 7200, "24h": 86400}


def feature_dictionary(root: str = DEFAULT_ROOT) -> dict:
    """name -> description plus TRAIN p5/p50/p95 (cached in <root>/feature_ranges.json; TRAIN only)."""
    cp = Path(root) / "feature_ranges.json"
    if cp.exists():
        rg = json.loads(cp.read_text())
    else:
        d = load_split("train", root)
        sub = np.random.default_rng(1).choice(d.n, min(d.n, 200_000), replace=False) if d.n else np.zeros(0, int)
        rg = {}
        for f in C.FEATURES:
            x = d.X[sub, C.FIDX[f]] if len(sub) else np.zeros(1)
            x = x[np.isfinite(x)]
            q = np.percentile(x, [5, 50, 95]) if len(x) else [0, 0, 0]
            rg[f] = [float(v) for v in q]
        cp.write_text(json.dumps(rg))
    return {f: f"{FEATURES[f]} [TRAIN p5/p50/p95: {rg[f][0]:.4g} / {rg[f][1]:.4g} / {rg[f][2]:.4g}]" for f in C.FEATURES if f in rg}


def analyst_data(root: str = DEFAULT_ROOT, n: int = ANALYST_N):
    """TRAIN only. -> (feats {name: float array}, fwd {horizon: forward NET return fraction at 0.1 SOL, NaN where gap/migrated/censored})."""
    d = load_split("train", root)
    sub = np.sort(np.random.default_rng(1).choice(d.n, min(d.n, n), replace=False))
    feats = {f: d.X[sub, C.FIDX[f]].astype(np.float64) for f in C.FEATURES}
    stake = 0.1 * LAMPORTS
    fwd = {}
    P = d.gather_paths(sub)
    for h, sec in ANALYST_H.items():
        k = int(np.searchsorted(C.GRID_S, sec, side="left"))
        r = C.DECODE[P[:, k]].astype(np.float64)
        pnl = trade_pnl(r, d.Qf[sub], d.Bf[sub], d.fee_in[sub], d.venue[sub], stake) / stake
        fwd[h] = np.where(np.isnan(r), np.nan, pnl)
    return feats, fwd


def main(argv=None):
    ap = argparse.ArgumentParser(description="forge evaluator (paper research)")
    sp = ap.add_subparsers(dest="cmd", required=True)
    e = sp.add_parser("eval")
    e.add_argument("--spec", required=True)
    e.add_argument("--split", choices=("train", "val"), required=True)
    e.add_argument("--root", default=DEFAULT_ROOT)
    a = ap.parse_args(argv)
    spec = json.loads(Path(a.spec).read_text())
    print(json.dumps(evaluate(spec, a.split, a.root), indent=1, default=str))


if __name__ == "__main__":
    main()
