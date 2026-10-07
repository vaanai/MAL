# EXP-020: stake size x entry slot grid on the frozen EXP-012 selection (REPORT-ONLY measurement)

| Field | Value |
| --- | --- |
| **Status** | **Plan. Report-only. No outcome has been read.** Written before `tools/exp020_grid.py --resim` or `--report` is run. It is not a challenger and makes no edge claim, ever. |
| **Date** | 2026-10-06 |
| **Tool** | `tools/exp020_grid.py`, tests `tools/test_exp020_grid.py`; re-sim machinery `tools/exp017_resim.py` (`run_pass` now takes an optional combo list; default unchanged), cache checks `tools/exp017_screen.py` |
| **Decides** | Nothing by itself. It logs one tries line, `exp020_grid`, as a **measurement of an execution parameter**. |

## 1. Questions

On the frozen EXP-012 selection (score >= THR90 = 0.8030766588450794, never refit) on the 27 non-P1 dates (EXP-015 cache universe, P2 + P3 + P4):

1. How do mean P&L per trade, and per unit stake, vary with **stake size** (0.25, 0.5, 1.0 SOL)?
2. How do they vary with **entry slot k** (2, 3, 4, 6), at realistic exit lag 2?

Why: the book is about +1.1 % of stake before fixed fees and -0.88 % after, at 0.05 SOL. The fixed fees (2 x 505,000 lamports) fall from 2.02 % to 0.20 % of the round trip at 0.5 SOL. The V-priced latency curve suggests k = 2 adds about +0.75 % of stake over k = 6 ([exp012-latency-virtual-2026-10-04.md](../ARTIFACTS/lab/exp012-latency-virtual-2026-10-04.md)). DEC-020 (the 0.25 step) and a planned faster-entry path both need numbers at these points.

## 2. Costs (as EXP-015 and EXP-017)

V pricing at the pin `70914a1619e4cf6adbb1d1981cbd8a49483f559b230e7dcfc224335a0635b42e` (`pool_v_0909`), exit lag 2, `tp50_sl30`, fee 505,000 lamports per side (a MISS pays one), haircut at its conservative end applied by `exp015_screen.cell_nets`, both fail models (flat 15 % and pressure slope scale 1). V impact grows with size, so every size is **re-simulated**, not scaled.

## 3. Grid (pre-declared)

- k in {2, 3, 4, 6} x size in {0.25, 0.5, 1.0} SOL, lag 2: 12 cells, 14 combos with the two controls.
- Controls (6, 0.05 SOL, lag 2) and (4, 0.05 SOL, lag 2): re-simulated and compared per mint, by sha256 of [mint, mig_ms, features, cell], with the EXP-015 cache's (6, 2) and (4, 2) cells (code `cc366d4`, job #242) before anything is read. Any mismatch or empty comparison refuses (as EXP-017 R3).
- No other cell, size or k is added after the cache is pinned.

## 4. Outputs per cell (both fail models, selected non-P1 rows)

n, filled, MISS share (status == MISS over n), mean SOL, mean as % of stake, CI90 of the mean (1,000 draws, seed 1, 5th / 95th percentile) under **both** resamplers (trades; whole UTC dates), total SOL, total ex-top-3, dates positive (of dates with trades, over 27 in scope). A row whose cell is censored or absent is excluded and counted as `n_without_cell`; any combo with more than 1 % such rows refuses the report. This is counted from cell presence and the `censored` flag only (no net field) **before** the tries line, so that refusal burns no try.

For each k < 6 cell, **paired x vs k = 6 at the same size**: x_m = net(k, size) - net(6, size) per migration over rows where both cells exist (`n_pairs`, `n_dropped`), mean x in SOL and in % of stake, CI90 by date-cluster and trade resamplers, total x. There are no bars, no pass/fail and no Holm family: these are descriptive numbers.

## 5. What this does and does not say (stated plainly)

- **REPORT-ONLY. Not gate evidence; no row is a promotion read.** It decides nothing by itself. Tries: one line, `exp020_grid`, status `report`, logged to the ops log and the canonical `data/tries.jsonl` at the spend point, before any net is evaluated.
- **Entry bound is the most optimistic point.** `ENTRY_BOUND = "start"` (`tools/exploration_exits.py:123`; `tools/latency_curve.py:149-156`) fills at the pool state before any trade in the landing slot. Slots mig+2 and mig+3 are the most contested, so part of any k<6 gain can come from this assumption alone. Every paired x vs k6 is therefore an upper bound. The same bound applies to every cell, k = 6 included.
- **Sim vs live residual (sign not settled).** MiScusi job #264 (notebook, 2026-10-06 08:35Z), build faa3192, 28 closed trades, exits agreeing 28/28: live minus sim mean +465,390 and median +34,540 lamports per trade at 0.05 SOL (live total -14,007,586 vs sim -27,038,518). On average live is slightly better than sim over this window, but with high variance and one build only. The earlier n = 8 figure for the same build, -384,022 lamports per trade (`DEC/DEC-021-champion-challenger.md:54`), was negative, so the sign is not settled. The grid carries no live-minus-sim adjustment either way.
- **The base is tuned.** These 27 dates have had 100+ tries on them (EXP-017 section 7: P1 79, P2 14, P3 6, P4 6 at 97 logged lines; more since). The frozen selection was chosen on this pool family. Level and sign are winner's-curse biased. Treat any positive cell as an upper bound, not an estimate of live P&L.
- **Any operating-point change** (size or k) still needs a fresh-block confirmation under a separate pre-registration and live calibration. DEC-021 section 7: a k change needs a live calibration first.
- **The k < 6 cells assume landing at k is achievable.** The live k p50 today is 5 (p90 6; MiScusi job #285, `docs/HANDOFF.md` "Measured today"). The simulator lands at k by assumption; MISS share here is the simulator's, not a live landing rate. A larger stake fills a different set of trades (impact changes which fills and MISSes occur), so % of stake across sizes is not a like-for-like set; read it with the MISS share, shown beside it.
- Larger stakes assume the same fill logic holds against bigger price impact; the V model prices impact, but live depth beyond V is not measured. No claim about any book's SOL or the promotion gate follows from this file.

## 6. Refusals (before the tries line; no try spent)

- No `GRID_MANIFEST_SHA256 = <sha>` line in this file, or two different lines; grid cache manifest sha256 differs from the pin; any sources' manifest meta has a different V map sha, combo list, `selected_sha256` (re-derived from the pinned EXP-015 cache), head, or a rows file that does not hash to its manifest.
- EXP-015 cache or raw manifest differs from the EXP-017 pins; cache heads differ.
- Equivalence control fails or is empty.
- `--tries-log` missing or relative; canonical log missing or without `exp015_` lines; an earlier `exp020_grid` line in either log; a RUN.lock without a record.
- Any view path in a reserved fragment.
- Any cell with more than 1 % of rows missing (after the line: the report aborts and the try stays spent, recorded).

## 7. Commands and estimates (mal-research-0, MiScusi jobs, one heavy job at a time, 4 workers, <= 48 GB, `nice -n 19`)

Selection: only the frozen-selected mints (3,322 over all six sources; about 2,349 non-P1) are simulated, so tape loading dominates and memory matches the EXP-017 re-sim (workers capped at 4 by `WORKERS_CAP`).

1. Precount (outcome-blind, seconds; reads no net, no tape):
   `/data/mal/venv/bin/python -m tools.exp020_grid --precount --p1-fast-dir /data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00 --p1-oracle-insample-dir /data/mal/clean-view/oracle-insample-2026-09-22_25 --p1-oracle-live-dir /data/mal/clean-view/oracle-live-2026-09-25_27 --out-dir /data/mal/exp020-grid`
   Expect 3,322 selected, 14 combos, 46,508 cells to simulate.
2. Guards, then re-sim (same view arguments as the EXP-015 screen and `tools.exp017_resim`: `--p2-view-dir /data/mal/clean-view/explore-0814/w1 ... w7`, `--p3-root /data/mal/blocks-clean/fresh-0903`, `--p4-view-dir /data/mal/clean-view/exp011-0909/b` and `/c`, `--max-workers 4`): `... -m tools.exp020_grid --resim <views> --out-dir /data/mal/exp020-grid --guards-only`, then without `--guards-only`. It writes `/data/mal/exp020-grid/grid_cache/` (mode 0400) and `GRID.manifest.sha256`, prints counts and the manifest sha only. Nobody opens the rows.
3. Pin amendment: add one line `GRID_MANIFEST_SHA256 = <sha>` to this file, merged before step 4.
4. Report: `... -m tools.exp020_grid --report --scratch /data/mal/exp015-screen/scratch --out-dir /data/mal/exp020-grid --report-dir /data/mal/exp020-grid/report --tries-log /data/mal/ops/tries-exp020-grid.jsonl` (job checkout of the merged branch, so `data/tries.jsonl` is the canonical log). Minutes.

**Runtime (estimate, not measured):** the EXP-015 V pass (all 33,518 migrations, 5 cells each) took about 2.4 h. The EXP-017 re-sim at 4 combos is job #319, running now; this grid has 14 combos on the same mints. Expected roughly 1.5 to 3 h wall time, tape-load dominated, with per-cell simulation about 3x the 4-combo cost. I will rescale from job #319's wall time when it finishes. Do not start this while #319 or another heavy replay runs (one heavy job at a time; check `systemctl show user-1002.slice -p MemoryCurrent`).

## Amendment 2026-10-07: ex-best-date column (report-only, before any outcome read)

Added 2026-10-07, before any outcome was read: the grid re-sim (job #330) is still running and `--report` has not been run. This is a stricter report-only column, not a loosening. It changes no gate, bar, existing column or pin.

Why: on the EXP-017 C0 size report the positive 0.25 and 0.5 SOL totals turned negative once the single best UTC date (2026-08-21) was dropped. Ex-top-3 trades did not catch that. EXP-020 must show the same check.

Added to each cell, on both legs (flat and pressure), next to `ex_top3_sol`, and to the markdown tables: `best_date` and `best_date_sol` (the UTC date with the largest total, ties to the earliest date, and that total), `ex_best_date_sol` (total minus that date's total) and `ex_best_date_dates_positive` (positive-date count without that date). It mirrors the best-date logic of `concentration_bar` in EXP-015/EXP-017. It is report-only and gates nothing.

## Amendment 2026-10-07: grid-cache pin (before any outcome read)

The outcome-blind `--resim` ran as job #330 (code c27d9ef, 4 workers, 22 GB, 2 h 21 m, rc 0). Precount and `--guards-only` passed (`n_selected` 3,322). Per-source row counts match the EXP-017 sized pin (§13 there): P1A 315, P1C 404, P1B 254, P2 1,485, P3 451, P4 413. The resim prints only counts and short hashes; no net, mean or total was printed or opened. `--report` runs once, after this line is merged, at the merged head.

GRID_MANIFEST_SHA256 = fe3eea656e14b8f3a92d47bd04c60563bc1157cab04d962076a883e6ac266b24
