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
`select_top_pct`/`threshold` are mutually exclusive — set exactly one.
`select_top_pct` takes the top N percent of each held-out day by model score
(`k = max(1, round(n*N/100))`); `threshold` takes `score >= threshold` (the
EXP-011 enter rule) and is valid for `s2_clf` only (a probability — the two
regression settings score in net-percent and are refused). Leave-one-day-out
runs over exactly the requested `days`. May read the 9-day exploration pool
(`tools.mal_templates.entry_filter.ALL_DAYS`, 2026-09-19..27, fast+Oracle —
mirrors `tools/exploration_entry_model_b3.py`). Optional root params
`fast_pool_root`, `insample_pool_root`, `live_pool_root`.

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

**`explore_exit`: fully wired** (`resolve_data_blocks` and `run`).
`run` builds one exit spec from the params and calls
`tools.exploration_exits.score_one_spec` (one caller-built spec, `size_sol`,
priority tier p50 = 58,000 / p75 = 500,000 lamports per side, a UTC-day
subset). The frozen 41-spec grid run is unchanged (md5 decision-equivalence
test in `tools/test_exploration_exits_score_one.py`). `resolve_data_blocks`
returns every hour `run` opens, including the trailing 2-hour buffer past each
chunk end (clipped at 2026-09-21T23); contiguous hours merge into one block.
Data root: optional `data_root` param, else env `MAL_FAST_POOL_ROOT`, else
`/var/lib/mal/backfill-fast` (on mal-research-0:
`/data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00`). **Two separate
gates:** `check_read` gates the hour labels only; the root is gated by
`tools.exploration_exits.POOL_ROOT_ALLOWLIST` (`/var/lib/mal/backfill-fast`
and the research-0 clean pool). `Path(root).resolve()` (symlinks and `..`
followed) must equal a resolved allowlist entry, for the param and for the
env value, or `score_one_spec` raises `ValueError` before any file is
opened (so `backfill-fast-b`/`-c` and `/data/mal/blocks/...` are refused).
`score_one_spec` also drops rows whose `block_time` is outside the hour file
being read (the default grid path does not). Refused, not
ignored: non-null `mcap_band`, `priority_fee_tier` p90 (no audited value).
Verified on a fixture root only; not yet run on real data.

**`explore_entry_filter`: fully wired** (`resolve_data_blocks` and `run`).
`run` calls `tools.exploration_entry_model_b3.score_one_cell` — one model
setting, one exit, `size_sol`, priority tier p50 = 58,000 / p75 = 500,000
lamports per side (`ARTIFACTS/lab/fee-audit-2026-09-27.md`), a UTC-day subset,
leave-one-day-out over those days, one `select_top_pct`-or-`threshold` cut. It
reuses `run_worker_features`/`score_one`/`fit_setting_b3` (which gained optional
`specs`/`size`/`priority`/`strict_hours` arguments whose defaults reproduce the
frozen grid). The default B3 grid path (`main()`) is unchanged: md5
decision-equivalence test on a three-pool fixture in
`tools/test_exploration_entry_filter_cell.py` (golden captured on `a788cc1`
before any edit: feature rows, the `--out-json` bytes and the `--out-md` bytes).
Results: `trades_flat`/`trades_pressure_s1` are the *selected* migrations only
(a selected migration that does not fill is a trade with `filled=false`);
`n_candidates` is every migration scored in the requested days before
selection; `n_days` is the number of requested days; a fold with fewer than 20
training rows or a single-class label is untrained and selects nothing (the
notes list the trained folds).

`resolve_data_blocks` returns every hour `run` opens, per host (`fast` =
pool A, `oracle` = pools C and B), contiguous hours merged: the home hours of
the requested days in each pool, the trailing 2-hour buffer past each chunk end
(clipped at each pool's end), the 24 creator-history lookback hours before each
home hour (pools A/C, clipped at the pool start — creator history is built from
those hours only, not the whole pool), and pool B's PumpPortal
`observe-<day>.jsonl` day files (the requested days and the day before each,
within 2026-09-25..27; `observe-2026-09-28` is never opened). A spy test on the
three hour-info functions and the day-file opener asserts blocks == hours opened
for nine day subsets, and `check_read` is asserted ALLOW on the real ledger.

Data roots, three slots (A fast, C Oracle in-sample, B Oracle live): param
(`fast_pool_root`/`insample_pool_root`/`live_pool_root`), else env
(`MAL_FAST_POOL_ROOT`/`MAL_INSAMPLE_POOL_ROOT`/`MAL_LIVE_POOL_ROOT`), else the
old default path. **Two separate gates:** `check_read` gates hour labels only;
each root is gated by a per-slot allowlist — A: `/var/lib/mal/backfill-fast`
and `/data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00`; C:
`/home/claude/data/oracle-insample-2026-09-22_25` and
`/data/mal/clean-view/oracle-insample-2026-09-22_25`; B:
`/home/claude/data/oracle-live-2026-09-25_27` and
`/data/mal/clean-view/oracle-live-2026-09-25_27`. `Path(root).resolve()`
(symlinks and `..` followed) must equal a resolved entry of that slot's list,
for the param and for the env value, or `score_one_cell` raises `ValueError`
before any file is opened (so `/data/mal/blocks/*` holdouts, a right-kind root
in the wrong slot, and `backfill-fast-b`/`-c` are refused). The new path drops
rows and creates whose `block_time` is outside the hour file being read (the
grid path does not). Refused, not ignored: non-null `mcap_band`,
`priority_fee_tier` p90 (no audited value), `threshold` with a regression model.
Verified on a fixture root only; not yet run on real data.
