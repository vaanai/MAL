# The `result.v1` contract

Every exploration/scoring tool emits one `result.json` per run, matching
[`schemas/result.v1.schema.json`](../../schemas/result.v1.schema.json). The
MAL Console renders one of these as a "result card" (console-plan.md §5).
Built by [`tools/mal_result.py`](../../tools/mal_result.py) — new tools
should call `build_result` / `write_result` rather than hand-rolling JSON.

**Every result card shows "variant N of M tried on this data."**

## Top-level fields

- `schema_version` — always `"result.v1"`.
- `tool`, `git_sha`, `command`, `config` — what ran, at what commit, knobs.
- `role` — `exploration | confirmation-oneshot | ops`.
- `data_blocks` — hours read: `{start_hour, end_hour_exclusive, host, ledger_owner}`.
- `stage` — edge-ladder stage, `idea` through `retired`.
- `metrics.flat` / `metrics.pressure_s1` — §5 numbers, one leg per fail
  model (flat 15%, pressure scale 1).
- `gate.flat` / `gate.pressure_s1` / `gate.pass_both` — LAB_STATE's
  promotion gate, ticked per leg; `pass_both` true only if both pass.
- `tries` — `{data_key, variant_n, of_m}`: the "variant N of M" line.
- `runtime_s`, `peak_rss_mb`, `created_utc`, optional `notes`.

## Metrics leg fields

`n_trades`, `selected_fraction`, `trades_per_day`, `sol_per_day`,
`mean_pct`/`median_pct`, `mean_sol`/`median_sol`, `ci90_lo_sol`,
`total_sol`, `ex_top3_sol`, `days`, `days_positive`, `fill_rate`,
`fill_cond_mean_pct`, `top5_profit_share`, `by_segment`. Nullable wherever
a tool cannot compute the value.

`ci90_lo_sol`/`total_sol`/`ex_top3_sol` come from
`tools.paper_attention_promote.book_stats` — the same cluster bootstrap
(1,000 draws, seed 1, p5 of means) the promotion gate already uses
(`tools/exp011_score.py::compute_gate`); not re-derived here.
`top5_profit_share` is the share of gross profit (winners only) from the 5
largest winners (§5 item 9), `None` with no winning trades.

## Gate leg fields

`n_ge_100`, `days_ge_5`, `majority_days_positive`, `ci90_lo_gt_0`,
`ex_top3_gt_0`, `pass` — exactly LAB_STATE.md's promotion gate.

## The tries log

`data/tries.jsonl` (override via `MAL_TRIES_LOG`), one JSON line per run,
appended under an `fcntl` exclusive lock. `data_key(data_blocks)` is the
first 16 hex chars of the sha256 of the blocks' canonical sorted JSON.
`append_try(...)` returns `{data_key, variant_n}`; `tries_summary(...)`
returns `{data_key, of_m}`. Call `append_try` first, then `tries_summary`,
then `build_result(..., tries={...})`.

## Validating

```
python3 -m tools.mal_result validate path/to/result.json
```

Prints `OK` and exits 0, or prints each schema error and exits 1.
