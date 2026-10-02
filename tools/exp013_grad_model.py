"""EXP-013 PR2: graduation-classifier model and nested leave-one-day-out (plan:
EXP/EXP-013-graduation-classifier-plan.md, Amendments 1-4).

Same recipe as EXP-012's freeze (tools/exp011_freeze.py: S2 lgb_medium, seed 1, deterministic,
num_threads 1, scale_pos_weight neg/pos) on the 22 grad features. The label is 1{press > 0} on
the primary k=4 row; MISS rows stay in training with label 0. Censored (mint, k) pairs are not
in the table and never enter.

Selection is by mint: features are cut at the trigger, so they are identical across k. The k=4
rows are the training/scoring universe; `pnl_at_k` joins the selected mints to their k-row.

Guard (plan Amendment 1 section 6): nothing here runs on a real table (a run dir under /data/mal/)
before 2026-10-04T12:00:00Z unless `allow_fixture` is set, which is for synthetic data only. The
reserved/forbidden locations of tools/exp013_pool.py are refused always. This module writes
nothing to ARTIFACTS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import tools.exp011_freeze as fz
from tools.exp013_grad_trigger import GRAD_FEATURE_NAMES, PRIMARY_K
from tools.exp013_pool import _is_forbidden

FEATURE_NAMES: list[str] = list(GRAD_FEATURE_NAMES)
if len(FEATURE_NAMES) != 22 or len(set(FEATURE_NAMES)) != 22:
    raise ImportError(f"expected 22 unique grad features, got {len(FEATURE_NAMES)}")

SEED = fz.SEED
LGB_PARAMS = fz.LGB_PARAMS
REAL_DATA_PREFIX = "/data/mal"
REAL_DATA_CUTOFF = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)
MIN_TRAIN_ROWS = 20


# --- guard ---------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def assert_run_dir_allowed(run_dir: Path | str, *, now: datetime | None = None, allow_fixture: bool = False) -> Path:
    """Returns realpath(run_dir). Refuses forbidden locations always, and anything under /data/mal/
    before REAL_DATA_CUTOFF, with or without allow_fixture. allow_fixture only means "synthetic data
    outside /data/mal"; it never opens a real path early."""
    real = os.path.realpath(str(run_dir))
    bad = _is_forbidden(real)
    if bad is not None:
        raise SystemExit(f"{run_dir!r} resolves to {real!r}, inside a forbidden location ({bad})")
    under_real = real == REAL_DATA_PREFIX or real.startswith(REAL_DATA_PREFIX + "/")
    if under_real:
        t = now if now is not None else _now()
        if t < REAL_DATA_CUTOFF:
            raise SystemExit(
                f"{real!r} is real data and it is {t.strftime('%Y-%m-%dT%H:%M:%SZ')}, before {REAL_DATA_CUTOFF.strftime('%Y-%m-%dT%H:%M:%SZ')} "
                f"(plan Amendment 1 section 6): no model, LODO or screen output from real data yet (allow_fixture={allow_fixture} does not apply under {REAL_DATA_PREFIX})"
            )
    return Path(real)


REQUIRED_ROW_KEYS = ("mint", "day", "entry_land_k", "filled", "flat", "press", "features")


def load_table(run_dir: Path | str, *, now: datetime | None = None, allow_fixture: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(rows, manifest) of an exp013_grad_table run dir. The bytes are read once and those bytes are
    hashed and parsed. Refuses a table.md5 mismatch (or missing), a manifest n_rows/table_md5 that
    disagrees with what was loaded, and a row missing a required key."""
    d = assert_run_dir_allowed(run_dir, now=now, allow_fixture=allow_fixture)
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
    if "table_md5" in manifest and manifest["table_md5"] != got:
        raise SystemExit(f"manifest table_md5 {manifest['table_md5']} != loaded {got}")
    if "n_rows" in manifest and manifest["n_rows"] != len(rows):
        raise SystemExit(f"manifest n_rows {manifest['n_rows']} != loaded {len(rows)}")
    return rows, manifest


# --- rows, features, label -------------------------------------------------------


def training_rows(rows: Sequence[dict[str, Any]], k: int = PRIMARY_K) -> list[dict[str, Any]]:
    """The k rows (entry_land_k == k), MISS rows included."""
    return [r for r in rows if r["entry_land_k"] == k]


def label(row: dict[str, Any]) -> int:
    return 1 if row["press"] > 0 else 0


def vector(feats: dict[str, float]) -> list[float]:
    """The 22 features in FEATURE_NAMES order. A missing name is a hard error; NaN passes through."""
    missing = [n for n in FEATURE_NAMES if n not in feats]
    if missing:
        raise ValueError(f"missing features: {missing}")
    return [float(feats[n]) for n in FEATURE_NAMES]


def _params(scale: float) -> dict[str, Any]:
    return {
        "objective": "binary",
        "metric": "binary_logloss",
        "num_threads": 1,
        "verbosity": -1,
        "learning_rate": LGB_PARAMS["learning_rate"],
        "num_leaves": LGB_PARAMS["num_leaves"],
        "min_data_in_leaf": LGB_PARAMS["min_data_in_leaf"],
        "feature_fraction": 0.9,
        "bagging_fraction": 0.9,
        "bagging_freq": 1,
        "scale_pos_weight": scale,
        "seed": SEED,
        "deterministic": True,
        "force_row_wise": True,
    }


def fit(rows: Sequence[dict[str, Any]]) -> Any:
    """LightGBM on the 22 features, label 1{press > 0}. Needs both classes."""
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
    return lgb.train(_params(scale), train, num_boost_round=LGB_PARAMS["rounds"])


def predict(model: Any, rows: Sequence[dict[str, Any]]) -> list[float]:
    import numpy as np

    xa = np.asarray([vector(r["features"]) for r in rows], dtype=np.float64)
    return [float(v) for v in model.predict(xa, num_threads=1)]


def fit_final(rows: Sequence[dict[str, Any]]) -> Any:
    """The model on all days of the k=4 rows (the future freeze). Writes nothing."""
    tr = training_rows(rows)
    if not tr:
        raise ValueError("fit_final: no k=4 rows")
    if len({label(r) for r in tr}) < 2:
        raise ValueError("fit_final: final training pool must have both classes")
    return fit(tr)


# --- LODO ------------------------------------------------------------------------


def _by_day(rows: Sequence[dict[str, Any]], days: Sequence[str]) -> tuple[dict[str, list[dict[str, Any]]], int]:
    """k=4 rows grouped by day, and the number of k=4 rows dropped because their day is not in `days`."""
    out: dict[str, list[dict[str, Any]]] = {d: [] for d in days}
    dropped = 0
    for r in training_rows(rows):
        if r["day"] in out:
            out[r["day"]].append(r)
        else:
            dropped += 1
    return out, dropped


def row_counts(rows: Sequence[dict[str, Any]], days: Sequence[str]) -> dict[str, int]:
    """Everything that can disappear before scoring."""
    k4 = training_rows(rows)
    k4_mints = {r["mint"] for r in k4}
    day_set = set(days)
    return {
        "n_rows_total": len(rows),
        "n_k4_rows": len(k4),
        "n_dropped_out_of_days": sum(1 for r in k4 if r["day"] not in day_set),
        "n_mints_without_k4": len({r["mint"] for r in rows} - k4_mints),
    }


def _lodo_scores(by_day: dict[str, list[dict[str, Any]]], days: Sequence[str]) -> tuple[list[tuple[dict[str, Any], float]], list[str]]:
    """Leave-one-day-out over `days`: (row, score) pairs and the held-out days of skipped folds."""
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
    """Each day's k=4 rows scored by the model trained on the other days. Returns (oof, info);
    info lists the skipped folds (held-out days) and the row counts."""
    by_day, dropped = _by_day(rows, days)
    pairs, skipped = _lodo_scores(by_day, days)
    oof = [{"day": r["day"], "mint": r["mint"], "score": s, "label": label(r), "filled": bool(r["filled"])} for r, s in pairs]
    return oof, {"skipped_folds": skipped, "n_skipped_folds": len(skipped), "n_dropped_out_of_days": dropped, "n_oof": len(oof)}


def pooled_threshold(oof: Sequence[dict[str, Any]], pct: float = 0.90) -> dict[str, Any]:
    """The confirmation-freeze threshold: same rule as exp011_freeze.compute_threshold (non-interpolating)."""
    if not oof:
        raise ValueError("pooled_threshold: empty oof")
    return fz.compute_threshold(oof, pct)


def _outer_fold(args: tuple[str, list[str], dict[str, list[dict[str, Any]]]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    outer_day, days, by_day = args
    inner_days = [d for d in days if d != outer_day]
    # Day d's rows are read below only as the test set: never for its threshold or its model.
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
    """For each outer day d: threshold = p90 of the pooled inner-LODO OOF scores over the other days
    (never touching d); score d's k=4 rows with the model trained on the other days; select
    score >= threshold. Returns (selected, fold_info, counts): one selected record per (day, mint) with
    score and threshold, one fold_info record per outer day (skipped folds say why), counts from row_counts. n_jobs > 1
    parallelizes over outer days (spawn) and is bit-identical to n_jobs=1."""
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


def pnl_at_k(selected: Sequence[dict[str, Any]], rows: Sequence[dict[str, Any]], k: int) -> dict[str, Any]:
    """Join the selected mints to their k-row. A selected mint with no k-row (censored at that k) is
    counted in n_missing_k and listed, never dropped silently."""
    by_mint: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r["entry_land_k"] == k:
            if r["mint"] in by_mint:
                raise ValueError(f"duplicate (mint, k) = ({r['mint']!r}, {k})")
            by_mint[r["mint"]] = r
    records: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for s in selected:
        r = by_mint.get(s["mint"])
        if r is None:
            missing.append({"day": s["day"], "mint": s["mint"]})
            continue
        records.append(
            {"day": s["day"], "mint": s["mint"], "k": k, "score": s["score"], "threshold": s["threshold"],
             "filled": bool(r["filled"]), "flat": r["flat"], "press": r["press"]}
        )
    return {"k": k, "n_selected": len(selected), "n_joined": len(records), "n_missing_k": len(missing), "missing": missing, "records": records}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EXP-013 grad model: nested LODO selection counts (no PnL claims)")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--allow-fixture", action="store_true", help="synthetic data only; bypasses the real-data time cutoff")
    ap.add_argument("--n-jobs", type=int, default=1)
    args = ap.parse_args(argv)
    rows, _manifest = load_table(args.run_dir, allow_fixture=args.allow_fixture)
    days = sorted({r["day"] for r in training_rows(rows)})
    selected, fold_info, counts = nested_lodo_select(rows, days, n_jobs=args.n_jobs)
    print(json.dumps({"n_days": len(days), "n_selected": len(selected), **counts, "fold_info": fold_info}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
