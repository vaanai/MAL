# EXP-019: post-migration flow confirmation at k = 8 (two cells on the frozen EXP-012 selection)

| Field | Value |
| --- | --- |
| **Status** | **Pre-registration of an exploration screen. No cell outcome has been read.** Written before `tools/exp019_postmig.py` is run in screen mode. No edge claim, ever, from this file. A pass earns only one confirmation read of an unread reserved block under a later pre-registration. |
| **Date** | 2026-10-06 |
| **Tool** | `tools/exp019_postmig.py`, tests `tools/test_exp019_postmig.py` |
| **Prior (mine, an estimate)** | Low, under 5 % that any cell passes all bars. The frozen book loses on these 27 dates at the deciding costs (EXP-017 section 9: pressure -0.00054 SOL per trade), and the k6 to k8 latency cost is real. |

## 1. Hypothesis, mechanism, constraint

- **Hypothesis.** The first few slots of PumpSwap trading after migration carry information that EXP-012's frozen pre-migration features do not (those cut at `mig_ms`, `causal_events(feat.events, mig_ms)`).
- **Mechanism.** Enter later, at k = 8 instead of k = 6, only when the early post-migration flow confirms.
- **Constraint.** The gain has to beat the latency cost of k6 to k8. Under V, on the 9-day exploration pool, 0.5 SOL entries, the OOF-selected book's pressure mean falls from 0.01246 to 0.00769 and the fill rate from 0.906 to 0.864 ([exp012-latency-virtual-2026-10-04.md](../ARTIFACTS/lab/exp012-latency-virtual-2026-10-04.md)); k = 8 keeps its CI90 lower bounds above 0 only barely. A confirm filter that keeps trades at the same average quality as the frozen book loses that much. The paired bar below prices it: every cell is judged against frozen at k6, not against frozen at k8.

## 2. How this differs from the failed drift veto

[exp012-exit-veto-2026-10-05.md](../ARTIFACTS/lab/exp012-exit-veto-2026-10-05.md): the entry-veto screen's best rule, `drift_gt_25`, was paired +0.00006 over 9/9 days but failed quant-proof. 40 of its 46 vetoes were simulator MISSes (`latency_curve._try_buy`, SLIPPAGE_CAP 0.15 against the migration-slot price), scored as saved fees; on filled trades only it rested on 6 trades and 3/9 days. The exit re-check also found nothing beyond the frozen exit. EXP-019 differs on three counts:

1. **Entry-side confirm at k8, not a veto at k6.** The feature window (slots [mig, mig + 4]) lies after the k6 trigger information and before the k8 landing. The drift veto read the migration-slot price to skip a k6 entry; here the entry itself moves to k8.
2. **Priced at a k8 entry.** Every cell's trade is the cached (k = 8, lag 2) cell: its fill or MISS is simulated at the k8 state, with the same cap. A MISS at k8 is booked as a MISS (one fee), never as a saved trade. A row that a cell does not enter scores x = -(frozen k6 net), so a skipped winner is a loss to the cell and a skipped loser a gain; no skip is ever "free".
3. **Both tries are judged against the realistic comparator.** The paired bar is cell at k8 minus frozen at k6, per frozen-selected migration, and also reports "all frozen-selected rows at k8" (report-only) so the latency cost alone is visible next to the confirm gain.

What does not differ: the simulator cap can still turn a k8 entry into a MISS where k6 would have filled. That cost stays inside the cell, as it should.

## 3. Data and selection

- The cached V-pass rows of the EXP-015 screen (EXP-017 section 1): cache manifest pin `72b9bd1a355953c114835a77fd7ea2f2273e75b21393cf5be2d48a16523ba31b` (12 files), raw manifest pin `0b7c9afa...562ae`, code head `cc366d4`, V map `pool_v_0909` sha256 `70914a16...b42e`. All imported from `tools.exp017_screen`, not copied.
- Cached cells per migration: `tools/exp015_screen.py` `CELL_KEYS = ((6, 2), (6, 0), (4, 2), (8, 2))`, `COMBOS = (k, 0.05 SOL, lag)` for each. So **(k = 8, lag 2, 0.05 SOL) is cached for every migration**; a test asserts it.
- Selection: frozen EXP-012, score >= THR90 = 0.8030766588450794. Never refit. Scope of every cell: the frozen-selected rows of the 27 non-P1 dates (P2, P3, P4). **P1 rows are neither featured nor scored** (their tape resolvers are different; P1 is report-only in the bars and carries no weight here).
- Deciding costs: as EXP-017 section 2 (V pin, lag 2, haircut at the conservative end, 505,000 lamports per side, both fail models, stake 0.05 SOL).

## 4. The migration slot and k

The slot is known from the tape, per mint: `_Mint.add` (`tools/latency_curve.py`, lines 729-734) sets `mig_slot` / `mig_ms` on the **first PumpSwap print of the mint after a bonding print**. The cache's `mig_ms` is that print's `t_recv_ms` (block_time x 1000 on backfill rows). The tool finds the first PumpSwap (wSOL) print at `t == mig_ms`, takes its `(t, slot, tx_index, event_index)`-minimal row, and its slot is `mig_slot`; its `pool` is the migration pool. `_fills_for(mint, migrate=True)` returns `trigger_slot = mint.mig_slot`, and `score_one` enters at `target = trigger_slot + k` with `ENTRY_BOUND = "start"` (`exploration_exits.py:123`): the entry state is the last print with `slot < mig_slot + k`, so the k8 state is priced from a print at slot <= mig_slot + 7, and the k6 state from slot <= mig_slot + 5. The feature window ends at mig_slot + 4, strictly before both. This is the same k as in the cached cells (`entry_land_k=k`).

**Disclosed optimism.** The frozen k6 cell assumes 6 slots from the trigger print to landing. A decision taken on slots up to mig_slot + 4 that lands at mig_slot + 8 assumes 4 slots from decision to landing. That is 2 slots faster than the pipeline the k6 cell prices. It is the window the manager fixed; a pass must be re-checked with the window ending at mig_slot + 2 (6 slots before landing) before any confirmation read is registered.

## 5. Features (tape only; nothing from outcomes)

Rows: PumpSwap, `quote_is_wsol` true, on the migration pool, slot in [mig_slot, mig_slot + 4]. A row at mig_slot + 5 or later is ignored (test). Duplicate `(signature, event_index)` rows are dropped. Loading is exactly as PR #424 (`tools/exp018_wallet_skill.py` at b38f541): `e15.guard_p2/p3/p4`, `MultiViewHours`, `make_p3_hours` (P3 files are `trades-<hour>.deduped.jsonl.zst`); the resolver is copied from there and marked `# from exp018 (#424)`. Refusals: any hour without a trades file; a non-zero zstd exit. A mint whose needed hours (the hour of `mig_ms` and of `mig_ms + 10 s`) are not all inside its series is `truncated_window` and has no features.

- **Net SOL buy flow:** sum of buy `sol_lamports` minus sum of sell `sol_lamports`.
- **Distinct buyer count:** distinct `trader` among buys. Reported, used by no cell.
- **Price change:** last print price / first print price - 1, with the simulator's own pricing (`print_from_trade_row` wrapped by `pumpswap_virtual_adapter.make_wrapper`, pin `pool_v_0909`: post-trade vault + V). A single-print window has change 0.
- **Largest single sell share:** max over sells of `sol_lamports / (pre-trade quote_reserve + V)`, V applied only when V > 0 (as the adapter). No sell: 0.

## 6. Cells (2 tries, Holm k = 2)

Each layers on frozen selection, enters at (k = 8, lag 2), and is paired per migration against frozen at (k = 6, lag 2). Thresholds are fixed here; none is tuned, so no nested leave-one-day-out is needed.

- **A, momentum confirm:** price change > 0 **and** net flow > 0.
- **B, no-dump confirm:** largest single sell share < 0.05.

Missing features (no migration print, truncated window, no price) never confirm. A cell that keeps 0 % or 100 % of the selection is refused.

## 7. Bars (as EXP-017 section 4; 27 non-P1 dates)

Bars 1-6 under both fail models, through `e17.evaluate_cell` on a view of the universe whose primary cell is the (8, 2) cell. B2 paired is over the frozen-selected rows (x = cell net at k8 minus frozen net at k6, 0 where neither enters), Holm over {A, B} at alpha 0.05 (thresholds 0.025, 0.05; one-sided date-cluster bootstrap p, 10,000 draws, seed 1, max over legs). B1 majority of days is over dates with at least one eligible row, eligible = frozen-selected. P1 is not scored. Note the paired mean is per frozen-selected migration, not per all migrations.

## 8. Refusals (before `started`; no try is spent)

- Cache or raw manifest differs from the pin; a cache head or V map differs; a view path in a reserved fragment (via the exp015 guards).
- Any hour of a series has no file; zstd exits non-zero.
- A cell keeps 0 % or 100 % of the frozen selection (in `--precount`, after the report is printed and written).
- **Any frozen-selected row lacks an uncensored (8, lag 2) cell** (a missing cell would score x = -frozen net).
- Feature coverage of the frozen-selected rows below 99 %.
- `--features` missing or its sha256 differs from `precount.json`; `--tries-log` missing or relative; the canonical log missing or without an `exp015_` line; any earlier exp019 line in either log; `RUN.lock`.

## 9. Multiplicity and disclosures

- The same 27 dates were read by EXP-015, EXP-016, EXP-017 and EXP-018; Holm covers only A and B. Treat any p as optimistic by the number of earlier looks (`data/tries.jsonl`, 97 lines on 2026-10-06 before this tool; per-pool prior counts are written to screen.json).
- The cached cells include the simulator's SLIPPAGE_CAP MISS rule (see section 2).
- Timing: DEC-021 walk 2 registers before 2026-10-16T01. A pass here needs its own pre-registration and a one-shot read of an unread block.

## 10. Commands

Both run on mal-research-0 as MiScusi jobs, one heavy job at a time, from a checkout of the merged branch (so `data/tries.jsonl` is the canonical log).

1. `nice -n 19 /data/mal/venv/bin/python -m tools.exp019_postmig --precount --workers 4 --out-dir /data/mal/exp019-screen` (outcome-blind; writes `features.jsonl`, `precount.json`; takes no tries log).
2. `nice -n 19 /data/mal/venv/bin/python -m tools.exp019_postmig --features /data/mal/exp019-screen/features.jsonl --out-dir /data/mal/exp019-screen --tries-log /data/mal/ops/tries-exp019-screen.jsonl` (screen; `started` and `completed` lines go to both logs).

**Estimate (not measured):** one pass over 28 + 12 = 40 days of hourly tape, but only lines containing `"pumpswap"` are parsed and only the frozen-selected mints (about 2,349 non-P1) are kept; dominated by zstd and line scanning, roughly the cost of the EXP-018 precount, 0.5-1.5 h at 4 workers. Memory: workers hold one hour of candidate rows for a few hundred mints; expect under 4 GB total. The screen itself runs in seconds to a minute (10,000-draw date bootstraps).
