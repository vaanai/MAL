"""Runner for the `explore_entry_filter` job template
(templates/explore_entry_filter.schema.json).

This is a NEW file -- it imports tools.exploration_exits and
tools.exploration_entry_model_b3 (both existing, unmodified) rather than changing
them. EXPLORATION ONLY, and only ever reached after tools/mal_job.py has already
enforced role == "exploration" and called tools.mal_catalog.check_read on every
block resolve_data_blocks() below returns (docs/console-plan.md §2 rule 1).

Wiring status (also recorded in docs/contracts/job-templates.md and the PR body):

  - `resolve_data_blocks` is fully wired: it maps the template's `days` param onto
    the exact 9 exploration-pool UTC days tools.exploration_entry_model_b3 already
    reads (pool A fast-box 2026-09-19/20/21, pool C Oracle in-sample 2026-09-22
    through 2026-09-24, the split day 2026-09-25, pool B Oracle live tape
    2026-09-26/27 -- docs/HOLDOUT_LEDGER.md), one block per day.
  - `run` is NOT wired. tools.exploration_entry_model_b3 always trains all three
    SETTINGS_B3 x both exits x the full 9-day leave-one-day-out grid, at the
    module-level frozen ENTRY_SIZE (0.5 SOL) and ENTRY_PRIORITY_LAMPORTS
    (0.0005 SOL/side); it has no entry point for "one model, one exit, one
    size_sol/priority_fee_tier, a day subset, and a single select_top_pct-or-
    threshold cut." Building that entry point means changing an existing file,
    which this PR does not do. `run` raises NotImplementedError naming exactly
    that gap; a follow-up PR wires it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping

from tools.exploration_exits import SPECS as _EXIT_SPECS

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


def next_day(day: str) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


def _resolve_days(days_param: Any) -> list[str]:
    if days_param == "all":
        return list(ALL_DAYS)
    return list(days_param)


def resolve_data_blocks(params: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One `{start_hour, end_hour_exclusive, host, ledger_owner}` block per
    requested UTC day. Pure -- reads no file, opens no data."""
    days = _resolve_days(params["days"])
    blocks: list[dict[str, Any]] = []
    for day in days:
        host = DAY_HOST[day]
        blocks.append(
            {
                "start_hour": f"{day}T00",
                "end_hour_exclusive": f"{next_day(day)}T00",
                "host": host,
                "ledger_owner": "exploration-pool",
            }
        )
    return blocks


def run(params: Mapping[str, Any]) -> dict[str, Any]:
    """Would return {trades_flat, trades_pressure_s1, n_candidates, n_days,
    peak_rss_mb, notes} for the requested (model, exit, select_top_pct-or-
    threshold, days) cell. Not wired -- see module docstring."""
    raise NotImplementedError(
        "explore_entry_filter.run needs tools.exploration_entry_model_b3 to accept "
        "a configurable entry size_sol / priority_fee_tier (today frozen at "
        "module-level ENTRY_SIZE=0.5 SOL / ENTRY_PRIORITY_LAMPORTS) and a single "
        "(model, exit, day-subset, select_top_pct-or-threshold) run, instead of "
        "its fixed all-settings x all-exits x all-9-days leave-one-day-out grid; "
        "follow-up PR, not this one (new files only)."
    )
