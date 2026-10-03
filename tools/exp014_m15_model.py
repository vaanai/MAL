"""EXP-014 PR2: the migration + 15 min PumpSwap selector model and nested leave-one-day-out
(plan: EXP/EXP-014-mig15-pumpswap-selector-plan.md, Fixed design items 8-11 and 14).

Same recipe as tools/exp013_grad_model.py (S2 lgb_medium, seed 1, deterministic, num_threads 1,
scale_pos_weight neg/pos), on the 27 EXP-014 features, the primary row d = 4 (MISS rows included),
label 1{press > 0}, a row's day = the UTC day of T. The recipe pieces (`_params`, `label`,
`pooled_threshold`, `pnl_at_k`, the percentile rule) are IMPORTED from tools.exp013_grad_model and
tools.exp011_freeze, not copied. Only the feature list, the row filter and the real-data cutoff differ,
so the fit/predict/LODO orchestration is restated here: EXP-013's module is not edited (its single
screen run must stay byte-identical).

Rows with `excluded_by_time` (plan item 11, judged by T alone) are removed by `eligible_rows` before
training and selection, exactly as EXP-013's screen removes trigger-time-excluded rows.

Guard: nothing here runs on a real table (a run dir under /data/mal/) before 2026-10-05T12:00:00Z.
Writes nothing to ARTIFACTS.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import tools.exp011_freeze as fz
import tools.exp013_grad_model as gm
from tools.exp013_pool import _is_forbidden
from tools.exp014_m15_trigger import FEATURE_NAMES as _TRIGGER_FEATURES
from tools.exp014_m15_trigger import PRIMARY_K

FEATURE_NAMES: list[str] = list(_TRIGGER_FEATURES)
if len(FEATURE_NAMES) != 27 or len(set(FEATURE_NAMES)) != 27:
    raise ImportError(f"expected 27 unique EXP-014 features, got {len(FEATURE_NAMES)}")

SEED = gm.SEED
REAL_DATA_PREFIX = gm.REAL_DATA_PREFIX
REAL_DATA_CUTOFF = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
MIN_TRAIN_ROWS = gm.MIN_TRAIN_ROWS
REQUIRED_ROW_KEYS = ("mint", "day", "mig_day", "entry_land_k", "trigger_ms", "filled", "flat", "press", "excluded_by_time", "pool", "features")
TABLE_SCHEMA = "exp014_m15_table_v1"

label = gm.label
pooled_threshold = gm.pooled_threshold
pnl_at_k = gm.pnl_at_k


def _now() -> datetime:
    return datetime.now(timezone.utc)


def assert_run_dir_allowed(run_dir: Path | str, *, now: datetime | None = None) -> Path:
    """realpath(run_dir). Forbidden locations always refused; anything under /data/mal before the cutoff refused."""
    real = os.path.realpath(str(run_dir))
    bad = _is_forbidden(real)
    if bad is not None:
        raise SystemExit(f"{run_dir!r} resolves to {real!r}, inside a forbidden location ({bad})")
    if real == REAL_DATA_PREFIX or real.startswith(REAL_DATA_PREFIX + "/"):
        t = now if now is not None else _now()
        if t < REAL_DATA_CUTOFF:
            raise SystemExit(f"{real!r} is real data and it is {t.strftime('%Y-%m-%dT%H:%M:%SZ')}, before {REAL_DATA_CUTOFF.strftime('%Y-%m-%dT%H:%M:%SZ')}: no model, LODO or screen output yet")
    return Path(real)


def manifest_names_real_roots(manifest: dict[str, Any]) -> bool:
    """True if the table manifest names a view root under /data/mal (a real-data table, wherever its run dir is)."""
    roots = [*(manifest.get("roots") or {}).values(), *(v.get("root", "") for v in manifest.get("extra_views", []))]
    return any((r := os.path.realpath(str(x))) == REAL_DATA_PREFIX or r.startswith(REAL_DATA_PREFIX + "/") for x in roots if x)


def load_table(run_dir: Path | str, *, now: datetime | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(rows, manifest) of an exp014_m15_table run dir. Bytes are read once; those bytes are hashed and parsed."""
    d = assert_run_dir_allowed(run_dir, now=now)
    md5_path = d / "table.md5"
    if not md5_path.exists():
        raise SystemExit(f"{md5_path} missing: refusing an unverified table")
    data = (d / "table.jsonl").read_bytes()
    want, got = md5_path.read_text(encoding="utf-8").strip(), hashlib.md5(data).hexdigest()
    if got != want:
        raise SystemExit(f"table md5 mismatch: {got} != {want}")
    rows = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    for n, r in enumerate(rows, 1):
        absent = [key for key in REQUIRED_ROW_KEYS if key not in r]
        if absent:
            raise SystemExit(f"table row {n} is missing required keys {absent}")
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != TABLE_SCHEMA:
        raise SystemExit(f"manifest schema {manifest.get('schema')!r} != {TABLE_SCHEMA!r}")
    if manifest_names_real_roots(manifest):
        t = now if now is not None else _now()
        if t < REAL_DATA_CUTOFF:
            raise SystemExit(f"the table manifest names real {REAL_DATA_PREFIX} view roots and it is {t.strftime('%Y-%m-%dT%H:%M:%SZ')}, before {REAL_DATA_CUTOFF.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    if "table_md5" in manifest and manifest["table_md5"] != got:
        raise SystemExit(f"manifest table_md5 {manifest['table_md5']} != loaded {got}")
    if "n_rows" in manifest and manifest["n_rows"] != len(rows):
        raise SystemExit(f"manifest n_rows {manifest['n_rows']} != loaded {len(rows)}")
    return rows, manifest


# --- rows, features ----------------------------------------------------------------


def eligible_rows(rows: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rows without `excluded_by_time`, and the count of what was removed (by d)."""
    keep = [r for r in rows if not r["excluded_by_time"]]
    by_d: dict[str, int] = {}
    for r in rows:
        if r["excluded_by_time"]:
            by_d[str(r["entry_land_k"])] = by_d.get(str(r["entry_land_k"]), 0) + 1
    return keep, {"n_rows": len(rows), "n_eligible": len(keep), "n_excluded_by_time": len(rows) - len(keep), "excluded_by_d": dict(sorted(by_d.items()))}


def training_rows(rows: Sequence[dict[str, Any]], d: int = PRIMARY_K) -> list[dict[str, Any]]:
    """The d rows (entry_land_k == d), MISS rows included. Excluded rows must already be removed (`eligible_rows`);
    a row still carrying excluded_by_time is a hard error."""
    out = [r for r in rows if r["entry_land_k"] == d]
    if any(r.get("excluded_by_time") for r in out):
        raise ValueError("training_rows: a row with excluded_by_time reached the model")
    return out


def vector(feats: dict[str, float]) -> list[float]:
    missing = [n for n in FEATURE_NAMES if n not in feats]
    if missing:
        raise ValueError(f"missing features: {missing}")
    return [float(feats[n]) for n in FEATURE_NAMES]


def fit(rows: Sequence[dict[str, Any]]) -> Any:
    """LightGBM on the 27 features, label 1{press > 0}. Needs both classes."""
    import lightgbm as lgb
    import numpy as np

    y = [label(r) for r in rows]
    if len(set(y)) < 2:
        raise ValueError("training rows need both classes")
    xa = np.asarray([vector(r["features"]) for r in rows], dtype=np.float64)
    ya = np.asarray(y, dtype=np.int32)
    pos = int(ya.sum())
    scale = (len(ya) - pos) / pos
    train = lgb.Dataset(xa, label=ya, feature_name=list(FEATURE_NAMES))
    return lgb.train(gm._params(scale), train, num_boost_round=gm.LGB_PARAMS["rounds"])


def predict(model: Any, rows: Sequence[dict[str, Any]]) -> list[float]:
    import numpy as np

    xa = np.asarray([vector(r["features"]) for r in rows], dtype=np.float64)
    return [float(v) for v in model.predict(xa, num_threads=1)]


def fit_final(rows: Sequence[dict[str, Any]]) -> Any:
    tr = training_rows(rows)
    if not tr:
        raise ValueError("fit_final: no d=4 rows")
    if len({label(r) for r in tr}) < 2:
        raise ValueError("fit_final: final training pool must have both classes")
    return fit(tr)


# --- LODO --------------------------------------------------------------------------


def _by_day(rows: Sequence[dict[str, Any]], days: Sequence[str]) -> tuple[dict[str, list[dict[str, Any]]], int]:
    out: dict[str, list[dict[str, Any]]] = {d: [] for d in days}
    dropped = 0
    for r in training_rows(rows):
        if r["day"] in out:
            out[r["day"]].append(r)
        else:
            dropped += 1
    return out, dropped


def row_counts(rows: Sequence[dict[str, Any]], days: Sequence[str]) -> dict[str, int]:
    k4 = training_rows(rows)
    day_set = set(days)
    return {
        "n_rows_total": len(rows),
        "n_k4_rows": len(k4),
        "n_dropped_out_of_days": sum(1 for r in k4 if r["day"] not in day_set),
        "n_mints_without_k4": len({r["mint"] for r in rows} - {r["mint"] for r in k4}),
    }


def _lodo_scores(by_day: dict[str, list[dict[str, Any]]], days: Sequence[str]) -> tuple[list[tuple[dict[str, Any], float]], list[str]]:
    out: list[tuple[dict[str, Any], float]] = []
    skipped: list[str] = []
    for held in days:
        train = [r for d in days if d != held for r in by_day.get(d, [])]
        test = by_day.get(held, [])
        if len(train) < MIN_TRAIN_ROWS or not test or len({label(r) for r in train}) < 2:
            skipped.append(held)
            continue
        out.extend(zip(test, predict(fit(train), test)))
    return out, skipped


def outer_lodo_oof(rows: Sequence[dict[str, Any]], days: Sequence[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_day, dropped = _by_day(rows, days)
    pairs, skipped = _lodo_scores(by_day, days)
    oof = [{"day": r["day"], "mint": r["mint"], "score": s, "label": label(r), "filled": bool(r["filled"])} for r, s in pairs]
    return oof, {"skipped_folds": skipped, "n_skipped_folds": len(skipped), "n_dropped_out_of_days": dropped, "n_oof": len(oof)}


def _outer_fold(args: tuple[str, list[str], dict[str, list[dict[str, Any]]]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    outer_day, days, by_day = args
    inner_days = [d for d in days if d != outer_day]
    # The outer day is read below only as the test set: never for its threshold or its model.
    inner_pairs, inner_skipped = _lodo_scores({d: by_day[d] for d in inner_days}, inner_days)
    inner_scores = sorted(s for _, s in inner_pairs)
    threshold = fz._percentile(inner_scores, 0.90) if inner_scores else None
    test = by_day[outer_day]
    train = [r for d in inner_days for r in by_day[d]]
    info: dict[str, Any] = {
        "outer_day": outer_day, "trained": False, "skip_reason": None, "threshold": threshold, "n_inner_oof": len(inner_scores),
        "n_inner_skipped_folds": len(inner_skipped), "inner_skipped_days": inner_skipped, "n_test": len(test), "n_selected": 0, "selected_fraction": None,
    }
    selected: list[dict[str, Any]] = []
    if not test:
        info["skip_reason"] = "no test rows"
    elif threshold is None:
        info["skip_reason"] = "no inner out-of-fold scores"
    elif len(train) < MIN_TRAIN_ROWS:
        info["skip_reason"] = f"fewer than {MIN_TRAIN_ROWS} training rows"
    elif len({label(r) for r in train}) < 2:
        info["skip_reason"] = "single label class in training rows"
    else:
        info["trained"] = True
        for r, s in zip(test, predict(fit(train), test)):
            if s >= threshold:
                selected.append({"day": r["day"], "mint": r["mint"], "score": s, "threshold": threshold})
        info["n_selected"] = len(selected)
        info["selected_fraction"] = len(selected) / len(test)
    return selected, info


def nested_lodo_select(
    rows: Sequence[dict[str, Any]], days: Sequence[str], n_jobs: int = 1
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Nested LODO over T days: for each outer day, threshold = p90 of the inner pooled OOF scores (index
    round(0.90 (n-1)), never touching that day); score its d=4 rows with the model trained on the other days;
    select score >= threshold. n_jobs > 1 parallelizes over outer days (spawn), bit-identical to n_jobs=1."""
    days = list(days)
    by_day, _ = _by_day(rows, days)
    jobs = [(d, days, by_day) for d in days]
    if n_jobs > 1 and len(jobs) > 1:
        with mp.get_context("spawn").Pool(processes=min(n_jobs, len(jobs))) as pool:
            results = pool.map(_outer_fold, jobs, chunksize=1)
    else:
        results = [_outer_fold(j) for j in jobs]
    selected = [s for sel, _ in results for s in sel]
    return selected, [info for _, info in results], row_counts(rows, days)
