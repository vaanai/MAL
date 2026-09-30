# Job templates: MiScusi → `tools.mal_job`

The owner and DeepSeek never run research code directly (console-plan.md §2
rule 1, §7); they run one of these two MiScusi **templates**, which land on
`python3 -m tools.mal_job <template>` (MiScusi Console API §3) — the only
entry point. Both are `role: exploration` only. Pipeline: validate params
against `templates/<template>.schema.json` → require
`MISCUSI_JOB_ROLE == "exploration"` → resolve data blocks and call
`tools.mal_catalog.check_read` on every one (any DENY exits 3, before data is
touched) → call the runner → write `result.v1` + `$MISCUSI_RESULT`.

## `explore_entry_filter`

```json
{
  "name": "explore_entry_filter",
  "command": "python3 -m tools.mal_job explore_entry_filter",
  "params": {"trigger": "migrate", "model": "s2_clf", "select_top_pct": 10,
             "exit": "tpsl_tp50_sl30", "size_sol": 0.5, "priority_fee_tier": "p75",
             "mcap_band": null, "days": "all"},
  "resources": {"mem_gb": 8, "cpus": 2, "time_limit_min": 60},
  "role": "exploration", "allowedRoles": ["exploration"]
}
```

Schema: [`templates/explore_entry_filter.schema.json`](../../templates/explore_entry_filter.schema.json).
`select_top_pct`/`threshold` are mutually exclusive — set exactly one. May
read the 9-day exploration pool (`tools.mal_templates.entry_filter.ALL_DAYS`,
2026-09-19..27, fast+Oracle — mirrors `tools/exploration_entry_model_b3.py`).

## `explore_exit`

```json
{
  "name": "explore_exit",
  "command": "python3 -m tools.mal_job explore_exit",
  "params": {"trigger": "migrate", "family": "tp_sl_grid", "tp_pct": 50,
             "sl_pct": 30, "size_sol": 0.5, "priority_fee_tier": "p75",
             "mcap_band": null, "days": "all"},
  "resources": {"mem_gb": 6, "cpus": 2, "time_limit_min": 45},
  "role": "exploration", "allowedRoles": ["exploration"]
}
```

Schema: [`templates/explore_exit.schema.json`](../../templates/explore_exit.schema.json).
Required params depend on `family`: `tp_sl_grid`/`time_cap` need `tp_pct` +
`sl_pct` (`time_cap` also `cap_minutes`); `trailing_stop` needs `trail_pct`;
`partial_ladder` needs `take_pct` + `trail_rem_pct`. May read only the 3-day
fast-box pool (`tools.mal_templates.exit.DAYS`, 2026-09-19..21 — mirrors
`tools/exploration_exits.py`'s own hard fence).

## Wiring status

Both `resolve_data_blocks` are fully wired and tested against the real
`docs/HOLDOUT_LEDGER.md`. Both `run` raise `NotImplementedError` naming the
change an existing file would need (configurable `size_sol`/
`priority_fee_tier` instead of frozen module constants, a single-cell entry
point instead of the existing full-grid scripts) — a follow-up PR; this one
creates new files only. Refusal logic (schema, role, catalog) is complete.
