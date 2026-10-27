"""HV8 calibrator (chunk C2): frozen L2 logistic regression, LODO rank IC, bar 5. Design: docs/research/hv8/DESIGN.md sections 4, 5, 7.

Pure numpy, no clock, no network, no sklearn. Nothing here says HV8 makes money. HV8 has 0 trades.

Row schema (one dict per rated card, in screen order within its set; features and ratings only, NO outcome):
  card_sha, t_d (ISO "2026-08-21T12:02:00Z"), f (dict of FEATURE_FIELDS + max_stake), risk (dict, KEPT_RISK), setup_type, setup_z,
  ratings [flow, dump_risk, trend] ints 0-3 (FIRST ratings only; a row with retest=True raises).
Outcomes come only from load_labels(), which refuses unless RATINGS-PIN.md exists and its sha equals the sha256 of the ratings manifest.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from tools import hv8_rate as R

PIN_PATH = Path(__file__).resolve().parent.parent / "docs" / "research" / "hv8" / "RATINGS-PIN.md"
LAB_FIRST_ISO = "2026-08-21T12:00:00Z"
LAB_END_ISO = "2026-08-28T11:00:00Z"   # t_d must be strictly before; 08-28T12 is a hard refusal elsewhere
N_FOLDS = 7
C_REG = 1.0
ITERS = 50
MIN_SET = 3
GAP_MIN = 0.10
FOLDS_POS_MIN = 6

L8_NAMES = tuple(f"rating.{i}" for i in R.ITEMS) + (R.ANCHOR,)
T8_NAMES = tuple(R.MIRROR[i] for i in R.ITEMS) + (R.ANCHOR,)
_RISK = tuple(f"risk.{k}" for k in R.KEPT_RISK)
T8A_NUMERIC = tuple(f"f.{k}" for k in R.C.FEATURE_FIELDS) + ("f.max_stake",) + _RISK + ("setup.z",)
T8A_NAMES = T8A_NUMERIC + tuple(f"setup_tag.{t}" for t in R.SETUP_TAGS)
FEATURE_SETS = {"L8": L8_NAMES, "T8": T8_NAMES, "T8a": T8A_NAMES}
_LOG = re.compile(r"^f\.(vol_|net_flow_)|_sol")


class CalibError(ValueError):
    pass


def _parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def fold_of(t_d_iso: str) -> int:
    t = _parse_iso(t_d_iso)
    if t >= _parse_iso(LAB_END_ISO) or t < _parse_iso(LAB_FIRST_ISO):
        raise CalibError(f"t_d outside the training window: {t_d_iso}")
    return int((t - _parse_iso(LAB_FIRST_ISO)).total_seconds() // 86400)


def check_rows(rows) -> list[dict]:
    rows = list(rows)
    seen = set()
    for r in rows:
        if r.get("retest"):
            raise CalibError("a retest row is not allowed in calibrator input (first ratings only)")
        if r["card_sha"] in seen:
            raise CalibError("duplicate card_sha")
        seen.add(r["card_sha"])
        rt = r["ratings"]
        if len(rt) != 3 or any((not isinstance(v, int)) or isinstance(v, bool) or not 0 <= v <= 3 for v in rt):
            raise CalibError("ratings must be 3 ints in 0..3")
        fold_of(r["t_d"])
    return rows


def raw_value(row: dict, name: str):
    """Value of a registered feature name for one row (None if null). Unregistered names raise."""
    kind, _, key = name.partition(".")
    if kind == "rating":
        return float(row["ratings"][R.ITEMS.index(key)])
    if kind == "f":
        v = (row.get("f") or {}).get(key)
    elif kind == "risk":
        v = (row.get("risk") or {}).get(key)
    elif name == "setup.z":
        v = row.get("setup_z")
    elif kind == "setup_tag":
        return 1.0 if row.get("setup_type") == key else 0.0
    else:
        raise CalibError(f"unregistered feature {name}")
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _tf(name: str, v: float) -> float:
    return math.copysign(math.log1p(abs(v)), v) if _LOG.search(name) else v


def fit_preproc(rows, names) -> dict:
    cols = []
    for n in names:
        raw = [raw_value(r, n) for r in rows]
        if n.startswith("rating.") or n.startswith("setup_tag."):
            cols.append({"lo": None, "hi": None, "med": 0.0, "mean": 0.0, "sd": 1.0, "_raw": raw}); continue
        vals = np.array([_tf(n, v) for v in raw if v is not None], dtype=float)
        if len(vals) == 0:
            cols.append({"lo": 0.0, "hi": 0.0, "med": 0.0, "mean": 0.0, "sd": 1.0}); continue
        lo, hi = (float(x) for x in np.percentile(vals, [1, 99]))
        v2 = np.clip(vals, lo, hi)
        med = float(np.median(v2))
        fill = np.array([min(max(_tf(n, v), lo), hi) if v is not None else med for v in raw])
        sd = float(fill.std())
        cols.append({"lo": lo, "hi": hi, "med": med, "mean": float(fill.mean()), "sd": sd if sd > 0 else 1.0})
    out = []
    for n, c, in zip(names, cols):
        if "_raw" in c:
            raw = np.array(c.pop("_raw"), dtype=float)
            sd = float(raw.std())
            c["mean"], c["sd"] = float(raw.mean()), sd if sd > 0 else 1.0
        out.append(c)
    return {"names": list(names), "cols": out}


def design(rows, pre: dict) -> np.ndarray:
    X = np.zeros((len(rows), len(pre["names"])))
    for j, (n, c) in enumerate(zip(pre["names"], pre["cols"])):
        for i, r in enumerate(rows):
            v = raw_value(r, n)
            if c["lo"] is None:
                x = v
            else:
                x = c["med"] if v is None else min(max(_tf(n, v), c["lo"]), c["hi"])
            X[i, j] = (x - c["mean"]) / c["sd"]
    return X


def irls(X: np.ndarray, y: np.ndarray, c: float = C_REG, iters: int = ITERS) -> np.ndarray:
    """L2 logistic regression: loss = -loglik + 0.5/c * ||w||^2 (intercept unpenalised), Newton, fixed iterations from zero.
    Returns [intercept, w...]."""
    n, d = X.shape
    A = np.hstack([np.ones((n, 1)), X])
    w = np.zeros(d + 1)
    pen = np.eye(d + 1) / c
    pen[0, 0] = 0.0
    for _ in range(iters):
        z = np.clip(A @ w, -30, 30)
        p = 1.0 / (1.0 + np.exp(-z))
        g = A.T @ (p - y) + pen @ w
        H = (A * (p * (1 - p))[:, None]).T @ A + pen
        w = w - np.linalg.solve(H, g)
    return w


def fit_model(rows, y, names) -> dict:
    rows = check_rows(rows)
    if len(rows) != len(y) or len(rows) == 0:
        raise CalibError("rows and labels must align and be non-empty")
    pre = fit_preproc(rows, names)
    w = irls(design(rows, pre), np.asarray(y, dtype=float))
    model = {"pre": pre, "w": [float(v) for v in w], "train_manifest_sha": hashlib.sha256(
        ",".join(sorted(r["card_sha"] for r in rows)).encode()).hexdigest()}
    model["sha256"] = model_sha(model)
    return model


def model_sha(model: dict) -> str:
    body = {k: v for k, v in model.items() if k != "sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def score(model: dict, rows) -> np.ndarray:
    w = np.asarray(model["w"])
    return w[0] + design(list(rows), model["pre"]) @ w[1:]


# ------------------------------------------------------------------ pin and labels

def read_pin(pin_path: Path | None = None) -> str:
    p = Path(pin_path) if pin_path else PIN_PATH
    if not p.is_file():
        raise CalibError("RATINGS-PIN.md does not exist: labels are refused")
    m = re.search(r"manifest_sha256:\s*`?([0-9a-f]{64})`?", p.read_text())
    if not m:
        raise CalibError("RATINGS-PIN.md has no manifest_sha256 line")
    return m.group(1)


def load_labels(labels_path, manifest_path, pin_path: Path | None = None) -> dict:
    """card_sha -> net return (flat 15% model) or None (censored). The labels file is opened only after the pin check passes."""
    pinned = read_pin(pin_path)
    actual = hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest()
    if actual != pinned:
        raise CalibError("ratings manifest sha does not match RATINGS-PIN.md: labels are refused")
    out = {}
    with open(labels_path) as fh:
        for line in fh:
            if line.strip():
                d = json.loads(line)
                out[d["card_sha"]] = d.get("ret")
    return out


def label_rows(rows, returns: dict):
    """Attach returns; y = 1 if above the set median, 0 if below; ties and censored dropped and counted.
    Returns (labelled_rows, ret list, y list, counts)."""
    rows = check_rows(rows)
    by_set: dict[str, list[dict]] = {}
    for r in rows:
        by_set.setdefault(r["t_d"], []).append(r)
    keep, rets, ys = [], [], []
    cnt = {"censored": 0, "tied": 0, "kept": 0}
    for t, grp in by_set.items():
        vals = [returns.get(r["card_sha"]) for r in grp]
        known = [v for v in vals if v is not None]
        med = float(np.median(known)) if known else None
        for r, v in zip(grp, vals):
            if v is None:
                cnt["censored"] += 1
            elif v == med:
                cnt["tied"] += 1
            else:
                keep.append(r); rets.append(float(v)); ys.append(1 if v > med else 0); cnt["kept"] += 1
    return keep, rets, ys, cnt


# ------------------------------------------------------------------ IC, LODO

def _rank(a) -> np.ndarray:
    a = np.asarray(a, dtype=float)
    order = np.argsort(a, kind="mergesort")
    r = np.empty(len(a))
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and a[order[j + 1]] == a[order[i]]:
            j += 1
        r[order[i:j + 1]] = (i + j) / 2.0
        i = j + 1
    return r


def spearman(a, b) -> float:
    ra, rb = _rank(a), _rank(b)
    sa, sb = ra.std(), rb.std()
    if sa == 0 or sb == 0:
        return 0.0
    return float(np.mean((ra - ra.mean()) * (rb - rb.mean())) / (sa * sb))


def pick_index(scores) -> int:
    """Highest score; ties go to the earliest card in screen order."""
    return int(np.argmax(np.asarray(scores)))  # argmax returns the first maximum


def _set_groups(rows):
    g: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        g.setdefault(r["t_d"], []).append(i)
    return g


def _placebo_rows(rows, groups):
    out = list(rows)
    for t, idx in groups.items():
        if len(idx) < 2:
            continue
        tri = R.placebo_ratings(t, [rows[i]["card_sha"] for i in idx], [rows[i]["ratings"] for i in idx])
        for i, tr in zip(idx, tri):
            out[i] = {**rows[i], "ratings": list(tr)}
    return out


def lodo(rows, rets, ys) -> dict:
    """Per-fold mean within-set Spearman IC (sets with >= 3 labelled cards) for L8, T8, T8a, P8, plus P8's matching-pick share."""
    rows = check_rows(rows)
    folds = np.array([fold_of(r["t_d"]) for r in rows])
    rets, ys = np.asarray(rets, dtype=float), np.asarray(ys, dtype=float)
    ic = {a: [] for a in ("L8", "T8", "T8a", "P8")}
    match = [0, 0]
    for k in range(N_FOLDS):
        tr, te = np.where(folds != k)[0], np.where(folds == k)[0]
        if len(tr) == 0 or len(te) == 0:
            raise CalibError(f"fold {k} is empty")
        trr, ter = [rows[i] for i in tr], [rows[i] for i in te]
        scores = {}
        models = {a: fit_model(trr, ys[tr], FEATURE_SETS[a]) for a in FEATURE_SETS}
        for a, m in models.items():
            scores[a] = score(m, ter)
        groups = _set_groups(ter)
        scores["P8"] = score(models["L8"], _placebo_rows(ter, groups))
        for a in ic:
            vals = [spearman(scores[a][idx], rets[te][idx]) for idx in groups.values() if len(idx) >= MIN_SET]
            ic[a].append(float(np.mean(vals)) if vals else 0.0)
        for idx in groups.values():
            if len(idx) >= MIN_SET:
                match[1] += 1
                match[0] += pick_index(scores["L8"][idx]) == pick_index(scores["P8"][idx])
    return {"ic": ic, "p8_matching_pick_share": match[0] / match[1] if match[1] else None}


# ------------------------------------------------------------------ bar 5

def bar5(ic: dict) -> dict:
    """Bar 5 (DESIGN section 5): (a) mean gap L8-T8 >= 0.10, (b) gap > 0 on >= 6 of 7 folds, (c) mean L8 > mean T8a, (d) mean L8 > mean P8.
    g_s = 0.5 * max(0, g - 1.645 * sd_folds(g_k) / sqrt(7)), sd with ddof=1. Kill-only."""
    L, T, A, P = (np.asarray(ic[k], dtype=float) for k in ("L8", "T8", "T8a", "P8"))
    if not all(len(x) == N_FOLDS for x in (L, T, A, P)):
        raise CalibError("bar 5 needs 7 fold ICs per arm")
    gk = L - T
    g = float(gk.mean())
    se = float(gk.std(ddof=1) / math.sqrt(N_FOLDS))
    a, b = g >= GAP_MIN, int((gk > 0).sum()) >= FOLDS_POS_MIN
    c, d = bool(L.mean() > A.mean()), bool(L.mean() > P.mean())
    return {"gap": g, "se": se, "folds_positive": int((gk > 0).sum()), "a": bool(a), "b": bool(b), "c": c, "d": d,
            "pass": bool(a and b and c and d), "g_shrunk": 0.5 * max(0.0, g - 1.645 * se)}
