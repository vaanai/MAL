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

1. **Entry-side confirm at k8, not a veto at k6.** The feature window (slots [mig, mig + 2]) lies after the k6 trigger information and before the k8 landing. The drift veto read the migration-slot price to skip a k6 entry; here the entry itself moves to k8.
2. **Priced at a k8 entry, and the MISS mechanism is guarded, not removed.** Every cell's trade is the cached (k = 8, lag 2) cell: its fill or MISS is simulated at the k8 state, with the same cap. `cell_nets` books a k8 MISS as -fee (one fee). On a row where k8 MISSes and frozen k6 lost, x = cell - frozen is therefore **positive without any information from the confirm**: this is the same mechanism that sank `drift_gt_25` (40 of 46 vetoes were MISSes). A skipped row scores x = -(frozen k6 net), so a skipped winner is a loss and a skipped loser a gain, but an entered k8 MISS can also be a gain. Section 7 pre-declares a report-only MISS breakdown and a reading rule that removes a MISS-driven pass from the winners.
3. **Both tries are judged against the realistic comparator.** The paired bar is cell at k8 minus frozen at k6, per frozen-selected migration, and also reports "all frozen-selected rows at k8" (report-only) so the latency cost alone is visible next to the confirm gain.

What does not differ: the simulator cap can still turn a k8 entry into a MISS where k6 would have filled. That cost stays inside the cell, as it should.

## 3. Data and selection

- The cached V-pass rows of the EXP-015 screen (EXP-017 section 1): cache manifest pin `72b9bd1a355953c114835a77fd7ea2f2273e75b21393cf5be2d48a16523ba31b` (12 files), raw manifest pin `0b7c9afa...562ae`, code head `cc366d4`, V map `pool_v_0909` sha256 `70914a16...b42e`. All imported from `tools.exp017_screen`, not copied.
- Cached cells per migration: `tools/exp015_screen.py` `CELL_KEYS = ((6, 2), (6, 0), (4, 2), (8, 2))`, `COMBOS = (k, 0.05 SOL, lag)` for each. So **(k = 8, lag 2, 0.05 SOL) is cached for every migration**; a test asserts it.
- Selection: frozen EXP-012, score >= THR90 = 0.8030766588450794. Never refit. Scope of every cell: the frozen-selected rows of the 27 non-P1 dates (P2, P3, P4). **P1 rows are neither featured nor scored** (their tape resolvers are different; P1 is report-only in the bars and carries no weight here). No `result.v1` file is written; results live in `screen.json` and `screen.md`.
- Deciding costs: as EXP-017 section 2 (V pin, lag 2, haircut at the conservative end, 505,000 lamports per side, both fail models, stake 0.05 SOL).

## 4. The migration slot and k

The slot is known from the tape, per mint: `_Mint.add` (`tools/latency_curve.py`, lines 729-734) sets `mig_slot` / `mig_ms` on the **first PumpSwap print of the mint after a bonding print**. The cache's `mig_ms` is that print's `t_recv_ms` (block_time x 1000 on backfill rows). The tool finds the first PumpSwap (wSOL) print at `t == mig_ms`, takes its `(t, slot, tx_index, event_index)`-minimal row, and its slot is `mig_slot`; its `pool` is the migration pool. `_fills_for(mint, migrate=True)` returns `trigger_slot = mint.mig_slot`, and `score_one` enters at `target = trigger_slot + k` with `ENTRY_BOUND = "start"` (`exploration_exits.py:123`): the entry state is the last print with `slot < mig_slot + k`, so the k8 state is priced from a print at slot <= mig_slot + 7, and the k6 state from slot <= mig_slot + 5. The feature window ends at mig_slot + 2, strictly before both. This is the same k as in the cached cells (`entry_land_k=k`).

**Window ruling (manager, 2026-10-06).** The deciding k6 cell assumes the decision is made at the migration and lands 6 slots later; live k p50 is 5 slots from migration. A k8 entry therefore leaves room for features only through mig_slot + 2, which keeps the same 6-slot decision-to-landing budget (8 - 2 = 6). An earlier draft used mig_slot + 4, which would have assumed 4 slots decision-to-landing, 2 slots faster than the k6 cell prices; that window is withdrawn.

## 5. Features (tape only; nothing from outcomes)

Rows: PumpSwap, `quote_is_wsol` true, on the migration pool, slot in [mig_slot, mig_slot + 2]. A row at mig_slot + 3 or later is ignored (test). Duplicate `(signature, event_index)` rows are dropped. Loading is exactly as PR #424 (`tools/exp018_wallet_skill.py` at b38f541): `e15.guard_p2/p3/p4`, `MultiViewHours`, `make_p3_hours` (P3 files are `trades-<hour>.deduped.jsonl.zst`); the resolver is copied from there and marked `# from exp018 (#424)`. Refusals: any hour without a trades file; a non-zero zstd exit. Rows whose `block_time` is not an integer are dropped. A mint whose needed hours (the hours of `mig_ms - 60 s`, `mig_ms` and `mig_ms + 10 s`) are not all inside its series is `truncated_window` and has no features.

- **Net SOL buy flow:** sum of buy `sol_lamports` minus sum of sell `sol_lamports`.
- **Distinct buyer count:** distinct `trader` among buys. Reported, used by no cell.
- **Price change:** last print price / first print price - 1, with the simulator's own pricing (`print_from_trade_row` wrapped by `pumpswap_virtual_adapter.make_wrapper`, pin `pool_v_0909`: post-trade vault + V). A single-print window has change 0.
- **Largest single sell share:** max over sells of `sol_lamports / (pre-trade quote_reserve + V)`, V applied only when V > 0 (as the adapter). No sell: 0.

## 6. Cells (2 tries, Holm k = 2)

Each layers on frozen selection, enters at (k = 8, lag 2), and is paired per migration against frozen at (k = 6, lag 2). Thresholds are fixed here; none is tuned, so no nested leave-one-day-out is needed.

- **A, momentum confirm:** price change > 0 **and** net flow > 0.
- **B, no-dump confirm (SUPERSEDED by Amendment 1: never evaluated, never logged):** largest single sell share < 0.05.

Missing features (no migration print, truncated window, no price) never confirm. A cell that keeps 0 % or 100 % of the selection is refused.

## 7. Bars (as EXP-017 section 4; 27 non-P1 dates)

Bars 1-6 under both fail models, through `e17.evaluate_cell` on a view of the universe whose primary cell is the (8, 2) cell. B2 paired is over the frozen-selected rows (x = cell net at k8 minus frozen net at k6, 0 where neither enters), Holm over {A, B} at alpha 0.05 (thresholds 0.025, 0.05; one-sided date-cluster bootstrap p, 10,000 draws, seed 1, max over legs). B1 majority of days is over dates with at least one eligible row, eligible = frozen-selected. P1 is not scored. Note the paired mean is per frozen-selected migration, not per all migrations.

**MISS breakdown (report-only, per cell and per leg, in screen.json).** Number of entered rows whose k8 cell is a MISS; sum of x on rows with k8 MISS and k6 filled (and on all k8-MISS rows); the positive paired sum (sum of x over rows with x > 0) and its share from entered k8-MISS rows; paired stats (mean x, date-cluster CI90, p, ex-top-3) on the rows left after removing the entered k8-MISS rows (the k8-filled entered rows plus the rows the cell skips). The same breakdown for "all frozen-selected at k8" is report-only.

**Reading rule (pre-declared, mirrors EXP-017's size-effect rule).** If, on either leg, more than 50 % of the positive paired sum on the non-P1 rows comes from entered k8-MISS rows, a cell that otherwise passes (Holm and bars 1-6) prints "<cell>: MISS-driven -- earns nothing" and is removed from the winners. Exactly 50 % is not MISS-driven. Tests cover each branch.

## 8. Refusals (before `started`; no try is spent)

- Cache or raw manifest differs from the pin; a cache head or V map differs; a view path in a reserved fragment (via the exp015 guards).
- Any hour of a series has no file; zstd exits non-zero.
- A cell keeps 0 % or 100 % of the frozen selection (in `--precount`, after the report is printed and written).
- **Any frozen-selected row lacks an uncensored (8, lag 2) cell** (a missing cell would score x = -frozen net).
- Feature coverage of the frozen-selected rows below 99 %.
- Any frozen-selected row lacks an uncensored (6, lag 2) cell (must be 0 by construction of the universe).
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

**Precount extras (outcome-blind).** Mints whose candidates at `t == mig_ms` span more than one slot or more than one pool; candidate rows lacking side or pool; per cell, the dates with at least one entered row.

## Amendment 1 (2026-10-06, manager rulings, made before any outcome was read)

Precount #3 (job #322 at bf5776c) refused as designed. Facts it reported (outcome-blind): feature coverage 1.0 on every source; frozen-selected non-P1 rows 2,349 (P2 1,485; P3 451; P4 413); pass_A 1,727 (73.5 %); pass_B 2,202 (93.7 %); frozen-selected rows without an uncensored (6, 2) cell 0; without an uncensored (8, 2) cell 2 (1 in P2, 1 in P4). `features_sha256 = 2a9a89743515291cf7a83ffaefa6b6fbd663998fb5070a166d7d59b7606bc769`, file `/data/mal/exp019-screen/features.jsonl`.

1. **Censored k8 rows.** Frozen-selected rows lacking an uncensored (8, lag 2) cell leave the scored scope of **both** arms (the cell and frozen), so x is computed only where both arms are defined. Censoring is a tape-end timing property and is outcome-blind. They are counted and reported (`excluded_k8_censored` in screen.json, by source). The screen refuses if more than 5 rows are excluded. Today's count is 2. This replaces the section 8 refusal "any frozen-selected row lacks an uncensored (8, lag 2) cell" and the scoring "x = -frozen net" for such rows.
2. **Cell B is dropped.** It keeps 93.7 % of the selection, so its test would be dominated by the k6 to k8 shift itself, which the latency curve already prices as costly. The family is **A alone: Holm k = 1 (threshold 0.05), 1 try**. B is never evaluated and never logged (`exp019_b` is never written). Where sections 1 to 9 say "two tries", "Holm k = 2" or "A and B", read "one try, Holm k = 1, A".
3. **Features pinned.** The screen refuses unless `--features` is exactly `/data/mal/exp019-screen/features.jsonl` with sha256 `2a9a89743515291cf7a83ffaefa6b6fbd663998fb5070a166d7d59b7606bc769` (as EXP-018's `check_features_pin`).
4. **No feature or precount change.** `features_from_rows`, the tape pass, the confirm functions and the precount report are byte-for-byte as at bf5776c, so precount #3 stands. The precount still reports B's share (report only).

Screen command (mal-research-0, MiScusi job, from a checkout of the merged branch):

`nice -n 19 /data/mal/venv/bin/python -m tools.exp019_postmig --features /data/mal/exp019-screen/features.jsonl --out-dir /data/mal/exp019-screen --tries-log /data/mal/ops/tries-exp019-screen.jsonl`

## Result (2026-10-06, job #328)

**SCREEN NONE, family closed.** One try (cell A alone, Holm k = 1) at head `40f24b3`, MiScusi job #328 on research-0. Outputs: `/data/mal/exp019-screen/` (`screen.md`, `screen.json`, `features.jsonl`, `precount.json`, `RUN.record.json`). Exploration only, no edge claim; nothing goes to confirmation. Cell B was dropped before any read and never evaluated or logged (Amendment 1). Numbers are copied from `screen.json`.

Scored scope: 27 non-P1 dates, 2,347 frozen-selected rows (2,349 before the two censored rows were excluded; P2 1, P4 1). Cell A entered 1,726 rows.

**A paired vs frozen k6 (B2), `x = A net at k8 - frozen net at k6` per migration, 2,347 migrations**

| Leg | mean x (SOL) | CI90 (date-cluster) | p (one-sided) | ex-top-3 of sum (SOL) |
|---|---|---|---|---|
| flat | -0.0005277499177288448 | [-0.0009838516044208916, -0.00006730177998728579] | 0.9715028497150285 | -1.41221658521901 |
| pressure s1 | -0.00021693631323280808 | [-0.0005051206801364743, +0.0000791486529114597] | 0.885011498850115 | -0.6256226594935912 |

Holm input p = max over legs = 0.9715 against threshold 0.0500: not rejected.

**Cell A book (B1)**

| Leg | n | mean (SOL) | CI lo (book) | CI lo (date-cluster) | total (SOL) | ex-top-3 (SOL) | dates positive / with trades |
|---|---|---|---|---|---|---|---|
| flat | 1,726 | -0.0013183461738122827 | -0.0021179956216975664 | -0.0024353233013674033 | -2.275465496 | -2.497030026 | 9 / 27 |
| pressure s1 | 1,726 | -0.0010321479073001159 | -0.00154915646712051 | -0.0017900805278213486 | -1.781487288 | -1.920445299 | 10 / 27 |

**Frozen k6 book, same 27-date scope (report-only)**

| Leg | n | mean (SOL) | CI lo (book) | total (SOL) | ex-top-3 (SOL) | dates positive / with trades |
|---|---|---|---|---|---|---|
| flat | 2,347 | -0.000441770938219003 | -0.001110256769791223 | -1.036836392 | -1.320915882 | 9 / 27 |
| pressure s1 | 2,347 | -0.0005421123813378781 | -0.0009409133161269706 | -1.272337759 | -1.44515076 | 10 / 27 |

**MISS breakdown (report-only; `miss_driven` = false)**

- 289 of the 1,726 entered rows were k8 MISS.
- Share of the positive paired sum from entered k8-MISS rows: flat 0.04994059551126894 (0.3815044607805131 of 7.639165229706318 SOL); pressure 0.03549630230629224 (0.20444690815402425 of 5.759667764542985 SOL). Far below the 50 % rule, so the MISS rule did not bind.
- Sum of x on k8-MISS rows (all with k6 filled): flat -0.4617295812237961, pressure -0.24438555590081257.
- Paired without the k8-MISS rows (2,058 migrations): flat mean x -0.00037750217477444263, CI90 [-0.0008869971199598131, +0.00011287547270029974], p 0.8895110488951105; pressure mean x -0.0001286511036232206, CI90 [-0.00045143603309131495, +0.00020222555282826884], p 0.7343265673432656.

**Bars**

| Bar | Pass | Note |
|---|---|---|
| B1 gate | FAIL | CI lo below 0, ex-top-3 below 0, 9 / 27 (flat) and 10 / 27 (pressure) dates positive |
| B2 paired + Holm | FAIL | flat p 0.9715, mean x negative on both legs |
| B3 concentration | FAIL | max date share of positive total 0.3979 (flat) / 0.4026 (pressure); ex-best-date total -2.7798570849999997 / -2.0862968079999997 |
| B4 P2 + P4 | FAIL | n 1,336, 21 dates; mean -0.001504373005988024 (flat) / -0.0011646160306886227 (pressure); dates positive 7 / 8 |
| B5 P2 | FAIL | n 1,023, 14 dates; mean -0.0018170258025415444 / -0.0014072173069403714; dates positive 4 / 4 |
| B6 P3 + P4 | FAIL | n 703, 13 dates; mean -0.0005926715504978663 / -0.00048634990469416785; dates positive 5 / 6 |

**Caveats (from `screen.md`)**
- Exploration. Cells reuse the 27 non-P1 dates EXP-015, EXP-017 and EXP-018 already looked at; Holm k = 1 (cell A alone; B was dropped by Amendment 1).
- x is per frozen-selected migration with an uncensored k8 cell; a row the cell does not enter scores minus the frozen net.
- Features use slots up to mig_slot + 2 and the entry lands at mig_slot + 8: a decision-to-landing budget of 8 - 2 = 6 slots, the same as the frozen k6 cell.
- A k8 MISS is booked as minus the fee; on a row where k8 MISSes and frozen k6 lost, x is positive without any confirmation information. The MISS breakdown and the MISS-driven rule (section 7) guard against that.
- The k8 cells are the cached EXP-015 cells (V pin, lag 2, haircut, 505k per side, 0.05 SOL, both fail models); SLIPPAGE_CAP 0.15 against the migration-slot price still applies and can turn a later entry into a MISS.
- P1 dates are neither featured nor scored.

**Amendment 1 applied.** B dropped (never evaluated, never logged); 2 censored k8 rows excluded from both arms (P2 1, P3 0, P4 1).

No `result.v1` file was written to the out-dir. Tries: one `started` and four `completed` lines, synced to `data/tries.jsonl`. Lab note: [exp019-screen-2026-10-06.md](../ARTIFACTS/lab/exp019-screen-2026-10-06.md).
