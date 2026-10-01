"""Runner for the `explore_entry_filter` job template
(templates/explore_entry_filter.schema.json).

EXPLORATION ONLY, and only ever reached after tools/mal_job.py has already
enforced role == "exploration" and called tools.mal_catalog.check_read on every
block resolve_data_blocks() below returns (docs/console-plan.md §2 rule 1).

Fully wired:

  - `resolve_data_blocks` returns blocks covering EVERY hour `run` will open:
    the home hours of the requested UTC days in each pool, the trailing
    DEFAULT_BUFFER_HOURS buffer hours past each chunk end (clipped at each
    pool's end), the 24 creator-history lookback hours (pools A/C, clipped at
    the pool start) and the PumpPortal observe day files pool B reads (the
    requested days and the day before each, within 2026-09-25..27; the
    2026-09-28 file is never opened). Contiguous hours on the same host merge
    into one block. Pure -- opens no file.
  - `run` calls tools.exploration_entry_model_b3.score_one_cell (one model
    setting, one exit, size_sol, priority lamports, a day subset, leave-one-day-
    out, select_top_pct or threshold) and reports each selected migration as
    one trade per fail model. The frozen B3 grid (its main()) is unchanged.

Not supported (refused, not silently ignored): `mcap_band` (no band filter in
the scorer); `priority_fee_tier` p90 (no audited lamport value -- only p50 and
p75, the fee-audit-2026-09-27 slot+1 figures, are known); `threshold` with a
regression model (its score is net-percent, not a probability).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping

from tools import exploration_entry_model_b3 as b3
from tools.exploration_exits import SPECS as _EXIT_SPECS
from tools.mal_templates.exit import rows_to_trades

EXIT_IDS: tuple[str, ...] = tuple(s["id"] for s in _EXIT_SPECS)

# Which host seals each exploration-pool UTC day this template may read
# (docs/HOLDOUT_LEDGER.md: "Fast pre-cut" + the EXP-009 exclusion hours put
# 2026-09-19..21 on `fast`; "Oracle in-sample" + "Oracle live tape, pre-clean-clock"
# put 2026-09-22..27 on `oracle`). Every hour of every day below is exploration-pool
# owned -- see tools/test_mal_job.py's cross-check against the real ledger.
DAY_HOST: dict[str, str] = {
    "2026-09-19": "fast",
    "2026-09-20": "fast",
    "2026-09-21": "fast",
    "2026-09-22": "oracle",
    "2026-09-23": "oracle",
    "2026-09-24": "oracle",
    "2026-09-25": "oracle",
    "2026-09-26": "oracle",
    "2026-09-27": "oracle",
}
ALL_DAYS: tuple[str, ...] = tuple(sorted(DAY_HOST))
assert ALL_DAYS == b3.DAYS_ALL

# Slot+1 priority lamports per side (ARTIFACTS/lab/fee-audit-2026-09-27.md).
PRIORITY_LAMPORTS = {"p50": 58_000, "p75": 500_000}
_HOUR_FMT = "%Y-%m-%dT%H"


def next_day(day: str) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


def _resolve_days(days_param: Any) -> list[str]:
    if days_param == "all":
        return list(ALL_DAYS)
    return sorted(set(days_param))


def resolve_data_blocks(params: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One `{start_hour, end_hour_exclusive, host, ledger_owner}` block per
    contiguous run of hours (per host) `run` will open. Pure -- reads no file."""
    by_host = b3.cell_hours_by_host(_resolve_days(params["days"]), b3.DEFAULT_BUFFER_HOURS)
    blocks: list[dict[str, Any]] = []
    for host in sorted(by_host):
        runs: list[list[str]] = []
        for h in by_host[host]:
            prev = runs[-1][-1] if runs else None
            if prev is not None and datetime.strptime(prev, _HOUR_FMT) + timedelta(hours=1) == datetime.strptime(h, _HOUR_FMT):
                runs[-1].append(h)
            else:
                runs.append([h])
        for run in runs:
            blocks.append(
                {
                    "start_hour": run[0],
                    "end_hour_exclusive": (datetime.strptime(run[-1], _HOUR_FMT) + timedelta(hours=1)).strftime(_HOUR_FMT),
                    "host": host,
                    "ledger_owner": "exploration-pool",
                }
            )
    return sorted(blocks, key=lambda b: (b["start_hour"], b["host"]))


def run(params: Mapping[str, Any]) -> dict[str, Any]:
    """{trades_flat, trades_pressure_s1, n_candidates, n_days, peak_rss_mb, notes}
    for the requested (model, exit, select_top_pct-or-threshold, days) cell
    (result-json.md). Selected migrations only; a selected migration that does not
    fill is a trade (filled=false) at its miss cost."""
    if params.get("trigger", "migrate") != "migrate":
        raise ValueError("only the migrate trigger is supported")
    if params.get("mcap_band"):
        raise ValueError("mcap_band is not supported by tools.exploration_entry_model_b3 (no band filter); pass null")
    tier = params.get("priority_fee_tier", "p75")
    if tier not in PRIORITY_LAMPORTS:
        raise ValueError(f"priority_fee_tier {tier!r} has no audited lamport value; use one of {sorted(PRIORITY_LAMPORTS)}")
    days = _resolve_days(params["days"])
    size_sol = float(params["size_sol"])
    out = b3.score_one_cell(
        model=params["model"],
        exit_id=params["exit"],
        size_sol=size_sol,
        priority_lamports=PRIORITY_LAMPORTS[tier],
        days=days,
        select_top_pct=params.get("select_top_pct"),
        threshold=params.get("threshold"),
        fast_root=params.get("fast_pool_root") or None,
        insample_root=params.get("insample_pool_root") or None,
        live_root=params.get("live_pool_root") or None,
        max_workers=2,  # schema x-resources cores = 2
    )
    rows = out["rows"]
    size_lamports = int(round(size_sol * b3.LAMPORTS_PER_SOL))
    trades_flat, trades_press = rows_to_trades(rows, size_lamports)
    trained = sorted(d for d, f in out["folds"].items() if f["trained"])
    cut = f"top {params['select_top_pct']:g}% per held-out day" if params.get("select_top_pct") is not None else f"score >= {params['threshold']:g}"
    return {
        "trades_flat": trades_flat,
        "trades_pressure_s1": trades_press,
        "n_candidates": out["n_candidates"],
        "n_days": out["n_days"],
        "peak_rss_mb": out["peak_rss_mb"],
        "notes": (
            f"model={params['model']}; exit={params['exit']}; select={cut}; size_sol={size_sol:g}; priority={tier} "
            f"({PRIORITY_LAMPORTS[tier]} lamports/side); leave-one-day-out over {len(days)} day(s), "
            f"{len(trained)} trained fold(s) ({', '.join(trained) or 'none'}); {len(out['hours_read'])} pool hours read "
            "(home + 2h buffer + 24h creator lookback, strict block_time-in-hour); n_candidates counts every scored "
            "migration in the requested days before selection; rows are keyed by migration day; an untrained fold "
            "(<20 training rows or a single-class label) selects nothing; exploration only, not a promote claim."
        ),
    }
