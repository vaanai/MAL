"""Runner for the `explore_exit` job template
(templates/explore_exit.schema.json).

This is a NEW file -- it imports tools.exploration_exits (existing, unmodified)
rather than changing it. EXPLORATION ONLY, and only ever reached after
tools/mal_job.py has already enforced role == "exploration" and called
tools.mal_catalog.check_read on every block resolve_data_blocks() below returns
(docs/console-plan.md §2 rule 1).

Wiring status (also recorded in docs/contracts/job-templates.md and the PR body):

  - `resolve_data_blocks` is fully wired: tools.exploration_exits.py hard-fences
    its data to the 3-day fast-box exploration pool (2026-09-19T01 through
    2026-09-21T23, POOL_START/POOL_END there), so this template's `days` param
    maps onto exactly those 3 UTC days on host `fast`.
  - `run` is NOT wired. tools.exploration_exits.py always scores its own fixed
    41-cell exit grid (build_specs()) over the *entire* 71-hour pool, at the
    module-level frozen ENTRY_SIZE (0.5 SOL) and ENTRY_PRIORITY_LAMPORTS
    (0.0005 SOL/side); it has no entry point for "one caller-built exit spec,
    one size_sol/priority_fee_tier, a day subset." Building that entry point
    means changing an existing file, which this PR does not do. `run` raises
    NotImplementedError naming exactly that gap; a follow-up PR wires it.
"""

from __future__ import annotations

from typing import Any, Mapping

from tools.exploration_exits import POOL_END, POOL_START
from tools.mal_templates.entry_filter import next_day

# tools.exploration_exits.py's own hard-fenced pool: 2026-09-19T01..2026-09-21T23,
# `fast` host only. This template can only ever ask for whole UTC days within it.
DAYS: tuple[str, ...] = ("2026-09-19", "2026-09-20", "2026-09-21")
assert POOL_START.startswith(DAYS[0]) and POOL_END.startswith(DAYS[-1])


def _resolve_days(days_param: Any) -> list[str]:
    if days_param == "all":
        return list(DAYS)
    return list(days_param)


def resolve_data_blocks(params: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One `{start_hour, end_hour_exclusive, host, ledger_owner}` block per
    requested UTC day, host `fast`. Pure -- reads no file, opens no data."""
    days = _resolve_days(params["days"])
    return [
        {
            "start_hour": f"{day}T00",
            "end_hour_exclusive": f"{next_day(day)}T00",
            "host": "fast",
            "ledger_owner": "exploration-pool",
        }
        for day in days
    ]


def run(params: Mapping[str, Any]) -> dict[str, Any]:
    """Would return {trades_flat, trades_pressure_s1, n_candidates, n_days,
    peak_rss_mb, notes} for the requested exit family/params over the requested
    days. Not wired -- see module docstring."""
    raise NotImplementedError(
        "explore_exit.run needs tools.exploration_exits to accept a configurable "
        "entry size_sol / priority_fee_tier (today frozen at module-level "
        "ENTRY_SIZE / ENTRY_PRIORITY_LAMPORTS), a single caller-built exit spec "
        "(tp_pct/sl_pct/trail_pct/activation_pct/take_pct/trail_rem_pct/"
        "cap_minutes), and a day subset -- instead of always scoring its own "
        "fixed 41-spec grid over the full 71-hour pool; follow-up PR, not this "
        "one (new files only)."
    )
