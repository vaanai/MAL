"""Runner for the `explore_exit` job template
(templates/explore_exit.schema.json).

EXPLORATION ONLY, and only ever reached after tools/mal_job.py has already
enforced role == "exploration" and called tools.mal_catalog.check_read on every
block resolve_data_blocks() below returns (docs/console-plan.md §2 rule 1).

Fully wired:

  - `resolve_data_blocks` returns blocks covering EVERY pool hour `run` will
    open: the home hours of the requested UTC days plus the trailing
    DEFAULT_BUFFER_HOURS hours past each chunk end (clipped at the pool end,
    2026-09-21T23), because the exit walk needs forward tape. Contiguous hours
    are merged into one block. Pure -- opens no file.
  - `run` builds one exit spec from the params and calls
    tools.exploration_exits.score_one_spec (the frozen 41-spec grid run is
    unchanged), then reports each fill-or-miss as one trade per fail model.

Not supported (refused, not silently ignored): `mcap_band` (the scorer has no
band filter) and `priority_fee_tier` p90 (no audited lamport value; p50 and p75
are the fee-audit-2026-09-27 slot+1 medians).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Mapping

from tools import exploration_exits as ex
from tools.exploration_exits import POOL_END, POOL_START

# tools.exploration_exits.py's own hard-fenced pool: 2026-09-19T01..2026-09-21T23,
# `fast` host only. This template can only ever ask for whole UTC days within it.
DAYS: tuple[str, ...] = ("2026-09-19", "2026-09-20", "2026-09-21")
assert POOL_START.startswith(DAYS[0]) and POOL_END.startswith(DAYS[-1])

DEFAULT_BUFFER_HOURS = ex.DEFAULT_BUFFER_HOURS
# slot+1 priority lamports per side (ARTIFACTS/lab/fee-audit-2026-09-27.md).
PRIORITY_LAMPORTS = {"p50": 58_000, "p75": ex.ENTRY_PRIORITY_LAMPORTS}
_HOUR_FMT = "%Y-%m-%dT%H"


def _resolve_days(days_param: Any) -> list[str]:
    if days_param == "all":
        return list(DAYS)
    return sorted(set(days_param))


def resolve_data_blocks(params: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One `{start_hour, end_hour_exclusive, host, ledger_owner}` block per
    contiguous run of pool hours `run` will open (requested days + trailing
    buffer hours), host `fast`. Pure -- reads no file, opens no data."""
    hours = ex._hours_for_days(_resolve_days(params["days"]), DEFAULT_BUFFER_HOURS)
    runs: list[list[str]] = []
    for h in hours:
        prev = runs[-1][-1] if runs else None
        if prev is not None and datetime.strptime(prev, _HOUR_FMT) + timedelta(hours=1) == datetime.strptime(h, _HOUR_FMT):
            runs[-1].append(h)
        else:
            runs.append([h])
    return [
        {
            "start_hour": run[0],
            "end_hour_exclusive": (datetime.strptime(run[-1], _HOUR_FMT) + timedelta(hours=1)).strftime(_HOUR_FMT),
            "host": "fast",
            "ledger_owner": "exploration-pool",
        }
        for run in runs
    ]


def build_spec(params: Mapping[str, Any]) -> dict[str, Any]:
    """One exit spec in the exploration_exits.build_specs() dict shape."""
    family = params["family"]
    cap_min = params.get("cap_minutes", 30)
    cap_ms = int(round(cap_min * 60 * 1000))
    cap_txt = f"{cap_min:g}"
    if family in ("tp_sl_grid", "time_cap"):
        tp, sl = params["tp_pct"], params["sl_pct"]
        if family == "time_cap":
            sid = f"timecap_{cap_txt}m_tp{tp:g}_sl{sl:g}"
        else:
            sid = f"tpsl_tp{tp:g}_sl{sl:g}"
        return {"id": sid, "family": family, "type": "tpsl", "tp": tp / 100.0, "sl": sl / 100.0,
                "cap_ms": cap_ms, "desc": f"+{tp:g}% / -{sl:g}%, {cap_txt} min cap"}
    if family == "trailing_stop":
        trail, act = params["trail_pct"], params.get("activation_pct")
        act_id = "" if act is None else f"_act{act:g}"
        return {"id": f"trail_{trail:g}{act_id}", "family": family, "type": "trail", "trail": trail / 100.0,
                "activation": None if act is None else act / 100.0, "cap_ms": cap_ms,
                "desc": f"trail {trail:g}%" + ("" if act is None else f", arms at +{act:g}%") + f", {cap_txt} min cap"}
    if family == "partial_ladder":
        take, rem = params["take_pct"], params["trail_rem_pct"]
        return {"id": f"ladder_take{take:g}_trail{rem:g}", "family": family, "type": "ladder",
                "take": take / 100.0, "trail_rem": rem / 100.0, "cap_ms": cap_ms,
                "desc": f"sell half at +{take:g}%, trail rest {rem:g}%, {cap_txt} min cap"}
    raise ValueError(f"unknown family {family!r}")


def rows_to_trades(
    rows: list[dict[str, Any]], size_lamports: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(trades_flat, trades_pressure_s1) as {sol, pct, day, filled}."""

    def one(r: dict[str, Any], key: str) -> dict[str, Any]:
        return {"sol": r[key] / 1e9, "pct": r[key] / size_lamports * 100.0, "day": r["day"], "filled": bool(r["filled"])}

    return [one(r, "flat") for r in rows], [one(r, "press") for r in rows]


def run(params: Mapping[str, Any]) -> dict[str, Any]:
    """{trades_flat, trades_pressure_s1, n_candidates, n_days, peak_rss_mb, notes}
    for the requested exit over the requested days (result-json.md)."""
    if params.get("mcap_band"):
        raise ValueError("mcap_band is not supported by tools.exploration_exits (no band filter); pass null")
    tier = params.get("priority_fee_tier", "p75")
    if tier not in PRIORITY_LAMPORTS:
        raise ValueError(f"priority_fee_tier {tier!r} has no audited lamport value; use one of {sorted(PRIORITY_LAMPORTS)}")
    days = _resolve_days(params["days"])
    spec = build_spec(params)
    size_sol = float(params["size_sol"])
    root = params.get("data_root") or os.environ.get(ex.POOL_ROOT_ENV) or None
    out = ex.score_one_spec(
        spec,
        days=days,
        entry_size_sol=size_sol,
        priority_lamports=PRIORITY_LAMPORTS[tier],
        root=root,
        max_workers=2,  # schema x-resources cores = 2
        buffer_hours=DEFAULT_BUFFER_HOURS,
    )
    rows = out["rows"]
    size_lamports = int(round(size_sol * ex.LAMPORTS_PER_SOL))
    trades_flat, trades_press = rows_to_trades(rows, size_lamports)
    return {
        "trades_flat": trades_flat,
        "trades_pressure_s1": trades_press,
        "n_candidates": len(rows),
        "n_days": len(days),
        "peak_rss_mb": out["peak_rss_mb"],
        "notes": (
            f"spec={spec['id']} ({spec['desc']}); size_sol={size_sol:g}; priority={tier} "
            f"({PRIORITY_LAMPORTS[tier]} lamports/side); {len(out['hours_read'])} pool hours read "
            f"(home + {DEFAULT_BUFFER_HOURS}h buffer); misses count as trades; rows are keyed by migration day; "
            "exploration only, not a promote claim."
        ),
    }
