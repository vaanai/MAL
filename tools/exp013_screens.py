"""EXP-013 derivation screens (DEC-017 section 5, refit on the expanded pool).

Exploration tooling. These are descriptive screens on exploration-pool rows. They are
not a promotion test and not evidence for one. The tables report, per cohort and under
both fail models (flat 15% and pressure, slope scale 1): the selected fraction, mean SOL
per trade with the gate's 90% bootstrap CI, and total SOL after removing the top 3 trades.

  1. Period transfer: train on the August days only, score the September days; and the
     reverse. Each direction uses the same recipe (fz.leave_one_day_out_oof,
     fz.compute_threshold, fz.fit_frozen_model) on the training period alone; the
     threshold is the 90th percentile of the TRAINING period's own OOF scores. The test
     period is scored once by the model fitted on all training days.
  2. Source screens: the September-only LODO, and LODO within one source only
     (pool A = fast listener, pools C+B = Oracle, pool X = getBlock expansion views;
     the "fast" screen is pool A). Each uses its own pooled-OOF p90 threshold.
  3. Overlap with EXP-012: the share of the candidate's selected mints (pooled OOF at the
     candidate threshold) that EXP-012's frozen model also selects. For the 9 original
     days EXP-012's own OOF scores are used at its frozen threshold; for any other day
     (August) its frozen model.txt scores the row directly, since it never trained on it.
     Reported by period. Only model.txt, threshold.json, features.json and
     oof_scores.json are opened; ARTIFACTS/exp012/read/ is refused.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Sequence

import tools.exp011_freeze as fz

PERIODS = {"august": "2026-08", "september": "2026-09"}
E12_FILES = ("model.txt", "threshold.json", "features.json", "oof_scores.json")
E12_HOLDOUT_START = "2026-09-03T12"
E12_HOLDOUT_END = "2026-09-09T12"


def period_of(day: str) -> str | None:
    for name, prefix in PERIODS.items():
        if day.startswith(prefix):
            return name
    return None


def source_of(row: dict[str, Any]) -> str:
    return {"A": "fast", "C": "oracle", "B": "oracle", "X": "getblock"}.get(row.get("pool", "?"), "unknown")


# --- cohort statistics ---------------------------------------------------------


def _leg(sel: Sequence[dict[str, Any]], key: str) -> dict[str, Any]:
    from tools.exp011_score import _book_trades
    from tools.paper_attention_promote import book_stats

    if not sel:
        return {"mean_sol": None, "mean_ci90_sol": None, "total_sol": None, "ex_top3_sol": None}
    st = book_stats(_book_trades(sel, key))
    return {"mean_sol": st["mean_sol"], "mean_ci90_sol": st["mean_ci90_sol"], "total_sol": st["total_sol"], "ex_top3_sol": st["total_ex_top3_sol"]}


def cohort_stats(selected: Sequence[dict[str, Any]], n_all: int) -> dict[str, Any]:
    """n_all rows were scored; `selected` entered. Both fail models (rows carry `flat` and `press` lamports)."""
    return {
        "n": n_all,
        "n_selected": len(selected),
        "selected_fraction": (len(selected) / n_all) if n_all else None,
        "n_days_selected": len({r["day"] for r in selected}),
        "flat": _leg(selected, "flat"),
        "press": _leg(selected, "press"),
    }


def _trainable(rows: Sequence[dict[str, Any]], days: Sequence[str]) -> str | None:
    """None if a LODO over `days` can run, else why not."""
    if len(days) < 2:
        return f"needs at least 2 days (got {len(days)})"
    if len(rows) < 40:
        return f"needs at least 40 rows (got {len(rows)})"
    if len({1 if r["press"] > 0 else 0 for r in rows}) < 2:
        return "label has a single class"
    return None


# --- 1. period transfer --------------------------------------------------------


def _days_of(rows: Sequence[dict[str, Any]]) -> list[str]:
    return sorted({r["day"] for r in rows})


def transfer_one(train: Sequence[dict[str, Any]], test: Sequence[dict[str, Any]]) -> dict[str, Any]:
    tdays = _days_of(train)
    why = _trainable(train, tdays)
    if why is not None or not test:
        return {"status": "not_computable", "reason": why or "no test rows", "train_days": tdays, "test_days": _days_of(test)}
    oof = fz.leave_one_day_out_oof(train, tdays)
    if not oof:
        return {"status": "not_computable", "reason": "training-period LODO produced no OOF scores", "train_days": tdays, "test_days": _days_of(test)}
    thr = fz.compute_threshold(oof)
    model = fz.fit_frozen_model(train)
    scores = fz._predict(model, [fz._vector(r["features"]) for r in test])
    selected = [r for r, s in zip(test, scores) if s >= thr["threshold"]]
    return {
        "status": "ok",
        "train_days": tdays,
        "test_days": _days_of(test),
        "train_threshold": thr["threshold"],
        "train_n_oof": thr["n_oof"],
        "train_selected_fraction_at_threshold": thr["selected_fraction"],
        "test": cohort_stats(selected, len(test)),
    }


def period_transfer(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    aug = [r for r in rows if period_of(r["day"]) == "august"]
    sep = [r for r in rows if period_of(r["day"]) == "september"]
    return {
        "august_to_september": transfer_one(aug, sep),
        "september_to_august": transfer_one(sep, aug),
    }


# --- 2. source screens ---------------------------------------------------------


def lodo_screen(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """LODO over the days of `rows` alone; threshold = this screen's own pooled OOF p90."""
    days = _days_of(rows)
    why = _trainable(rows, days)
    if why is not None:
        return {"status": "not_computable", "reason": why, "days": days}
    oof = fz.leave_one_day_out_oof(rows, days)
    if not oof:
        return {"status": "not_computable", "reason": "no OOF scores", "days": days}
    thr = fz.compute_threshold(oof)
    by_key = {(r["day"], r["mint"]): r for r in rows}
    selected = [by_key[(o["day"], o["mint"])] for o in oof if o["score"] >= thr["threshold"]]
    return {"status": "ok", "days": days, "n_folds": len(days), "threshold": thr["threshold"], "cohort": cohort_stats(selected, len(oof))}


def source_screens(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"september_only": lodo_screen([r for r in rows if period_of(r["day"]) == "september"])}
    for src in ("fast", "oracle", "getblock"):
        sub = [r for r in rows if source_of(r) == src]
        out[f"{src}_only"] = lodo_screen(sub) if sub else {"status": "not_computable", "reason": "no rows from this source", "days": []}
    out["note"] = "fast = pool A (fast listener); oracle = pools C + B; getblock = pool X (expansion views). DEC-017 section 5 names the September-only and fast-only screens."
    return out


# --- 3. overlap with EXP-012 ---------------------------------------------------


def _assert_not_read_path(d: Path) -> Path:
    real = Path(os.path.realpath(str(d)))
    if "read" in real.parts and "exp012" in real.parts[: real.parts.index("read")]:
        raise SystemExit(f"{d}: EXP-012's read/ directory is never opened by the refit")
    return real


def e12_file_sha256(e12_dir: Path) -> dict[str, str]:
    d = _assert_not_read_path(e12_dir)
    return {n: hashlib.sha256((d / n).read_bytes()).hexdigest() for n in E12_FILES}


def overlap_with_exp012(rows: Sequence[dict[str, Any]], oof: Sequence[dict[str, Any]], cand_threshold: float, e12_dir: Path) -> dict[str, Any]:
    from tools.exp011_score import load_frozen_spec, score_rows

    d = _assert_not_read_path(e12_dir)
    e12_oof_doc = json.loads((d / "oof_scores.json").read_text(encoding="utf-8"))
    e12_oof_days = set(e12_oof_doc["days"])
    e12_oof = {(o["day"], o["mint"]): o["score"] for o in e12_oof_doc["rows"]}
    model, e12_thr, names = load_frozen_spec(d)

    by_key = {(r["day"], r["mint"]): r for r in rows}
    cand_sel = {(o["day"], o["mint"]) for o in oof if o["score"] >= cand_threshold}
    cand_all = {(o["day"], o["mint"]) for o in oof}

    # EXP-012 scores for every candidate-pool row: its OOF on its own 9 days, its frozen model elsewhere.
    e12_score: dict[tuple[str, str], float] = {}
    source: dict[tuple[str, str], str] = {}
    direct: list[dict[str, Any]] = []
    for k in cand_all:
        if k[0] in e12_oof_days:
            if k in e12_oof:
                e12_score[k] = e12_oof[k]
                source[k] = "e12_oof"
        else:
            direct.append(dict(by_key[k]))
    score_rows(model, direct, names)
    for r in direct:
        k = (r["day"], r["mint"])
        e12_score[k] = r["score"]
        source[k] = "e12_frozen_model"
    e12_sel = {k for k, s in e12_score.items() if s >= e12_thr}

    def block(keys: set[tuple[str, str]], label: str) -> dict[str, Any]:
        c = cand_sel & keys
        e = e12_sel & keys
        both = c & e
        union = c | e
        return {
            "scope": label,
            "n_scored_by_both": len(keys & set(e12_score)),
            "n_candidate_selected": len(c),
            "n_exp012_selected": len(e),
            "n_both": len(both),
            "share_of_candidate_also_in_exp012": (len(both) / len(c)) if c else None,
            "share_of_exp012_also_in_candidate": (len(both) / len(e)) if e else None,
            "jaccard": (len(both) / len(union)) if union else None,
            "n_candidate_selected_without_exp012_score": len(c - set(e12_score)),
        }

    out: dict[str, Any] = {
        "exp012_dir_files_sha256": e12_file_sha256(d),
        "exp012_threshold": e12_thr,
        "candidate_threshold": cand_threshold,
        "all": block(cand_all, "all pool days"),
        "by_period": {},
        "exp012_score_source": {"oof_days": sorted(e12_oof_days), "note": "OOF scores on EXP-012's own 9 days, frozen-model scores on every other day"},
    }
    for name in PERIODS:
        keys = {k for k in cand_all if period_of(k[0]) == name}
        out["by_period"][name] = block(keys, name) if keys else {"scope": name, "n_candidate_selected": 0, "note": "no rows"}
    return out


# --- driver ---------------------------------------------------------------------


def run_screens(rows: Sequence[dict[str, Any]], oof: Sequence[dict[str, Any]], cand_threshold: float, e12_dir: Path) -> dict[str, Any]:
    return {
        "schema": "exp013_screens_v1",
        "note": "DEC-017 section 5 derivation screens. Descriptive, exploration-pool only; not a promotion test.",
        "bootstrap": {"draws": 1000, "seed": 1, "pctile": 5},
        "period_transfer": period_transfer(rows),
        "source_screens": source_screens(rows),
        "overlap_with_exp012": overlap_with_exp012(rows, oof, cand_threshold, e12_dir),
    }


def _f(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, (list, tuple)):
        return f"[{v[0]:+.4f}, {v[1]:+.4f}]"
    return f"{v:+.4f}" if isinstance(v, float) else str(v)


def _cohort_line(label: str, c: dict[str, Any]) -> str:
    return (
        f"| {label} | {c['n']} | {c['n_selected']} | {_f(c['selected_fraction'])} | {_f(c['flat']['mean_sol'])} | {_f(c['flat']['mean_ci90_sol'])} | {_f(c['flat']['ex_top3_sol'])} "
        f"| {_f(c['press']['mean_sol'])} | {_f(c['press']['mean_ci90_sol'])} | {_f(c['press']['ex_top3_sol'])} |"
    )


_HEAD = [
    "| Cohort | n | selected | fraction | flat mean SOL | flat CI90 | flat ex-top-3 SOL | press mean SOL | press CI90 | press ex-top-3 SOL |",
    "| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- | ---: |",
]


def screens_markdown(doc: dict[str, Any]) -> str:
    lines = ["# EXP-013 derivation screens (exploration, no result)", "", doc["note"], "", "## Period transfer", "", *_HEAD]
    for name, t in doc["period_transfer"].items():
        lines.append(_cohort_line(name, t["test"]) if t["status"] == "ok" else f"| {name} | not computable: {t['reason']} | | | | | | | | |")
    lines += ["", "## Source screens (own LODO, own p90 threshold)", "", *_HEAD]
    for name, t in doc["source_screens"].items():
        if name == "note":
            continue
        lines.append(_cohort_line(name, t["cohort"]) if t["status"] == "ok" else f"| {name} | not computable: {t['reason']} | | | | | | | | |")
    o = doc["overlap_with_exp012"]
    lines += ["", "## Overlap with EXP-012", "", "| Scope | candidate selected | EXP-012 selected | both | share of candidate in EXP-012 | share of EXP-012 in candidate | Jaccard |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for b in [o["all"], *o["by_period"].values()]:
        if "n_both" not in b:
            lines.append(f"| {b['scope']} | 0 | | | | | |")
            continue
        lines.append(f"| {b['scope']} | {b['n_candidate_selected']} | {b['n_exp012_selected']} | {b['n_both']} | {_f(b['share_of_candidate_also_in_exp012'])} | {_f(b['share_of_exp012_also_in_candidate'])} | {_f(b['jaccard'])} |")
    return "\n".join(lines) + "\n"
