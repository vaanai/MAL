# EXP-017 batch 1: two cheap screens (H3, H4; H1 and H2 dropped, section 11) and a cost check (C0) on the cached EXP-015 per-migration cells

| Field | Value |
| --- | --- |
| **Status** | **Pre-registration of an exploration screen. No H-cell outcome has been read.** Written before `tools/exp017_screen.py` is run on outcomes. No edge claim, ever, from this file. A pass earns only one confirmation read of an unread reserved block (fresh-0828 or fresh-0808) under a later pre-registration. |
| **Date** | 2026-10-06 |
| **Tool** | `tools/exp017_screen.py`, tests `tools/test_exp017_screen.py` |
| **Prior (mine, an estimate)** | Low, under 10% that any cell passes all bars. The base book is already known to lose on these dates (see §9). A filter must turn a negative book positive, not just improve it. |

## 1. Data

The cached V-pass rows of the EXP-015 screen: `/data/mal/exp015-screen/scratch/cache/v_P*.rows.jsonl` (schema `exp015_tape_cache_v1`; one record per migration: `mint, spec, day, mig_ms, gap_ms, features, cells`). Cells are k = 6, exit lag 2, size 0.05 SOL, plus report-only k = 4, 8 and lag 0. Universe 33,518 rows (P1A 3,092; P1C 3,372; P1B 2,337; P2 13,494; P3 5,547; P4 5,676), built by `exp015_screen.build_universe`. Dates: 36 UTC dates, 27 non-P1 (P2, P3, P4). **P1 dates are report-only.**

Provenance: job #242 at code sha `cc366d4c7d8597d6429575c165f164cce35ce39d` (recorded in every `v_P*.manifest.json`), V map pin `70914a16…b42e` (`pool_v_0909`, sha256 `70914a1619e4cf6adbb1d1981cbd8a49483f559b230e7dcfc224335a0635b42e`).

**Pinned manifests** (sha256 over lines `<file sha256>  <path relative to scratch>`, sorted by path, joined by newline plus a final newline; computed from file bytes only, no field read):

- Cache (what the screen reads): `72b9bd1a355953c114835a77fd7ea2f2273e75b21393cf5be2d48a16523ba31b`, 12 files (`cache/v_P*.rows.jsonl` and `cache/v_P*.manifest.json`).
- Raw worker output: `0b7c9afae44678a3b8d54451d4be79aad847ad76290f8cd8ff7d478dcc3562ae`, 71 files (`v_P*/*.jsonl`).

## 2. Deciding costs (every cell, all bars)

k = 6 slots from the first PumpSwap print; exit lag 2; fee 505,000 lamports per side; V pricing at the pin; haircut at its conservative end (sell shortfall 16 bps, entry gap 26.08 bps, factor 0.0042038 x proceeds on filled trades); `tp50_sl30`; both fail models (flat 15% and pressure slope scale 1) must pass. Stake 0.05 SOL unless a cell says otherwise. The frozen EXP-012 model is never refit: selection is score >= THR90 = 0.8030766588450794.

## 3. Cells

- **C0 (report-only, outside the Holm family, no try).** Frozen selection at 0.25 and 0.5 SOL on the 27 non-P1 dates. Needs cells re-simulated at those sizes (V impact grows with size). The cache has 0.05 SOL cells only, so the sized cells come from `tools/exp017_resim.py` (section 12), which reuses `exp015_screen.e15_v_patch` with the same deciding costs.
- **H1, mayhem veto. DROPPED before any outcome read (section 11); report-only counts remain.** Frozen selection minus mints whose create row has `is_mayhem_mode == True`. A row's field counts only if it is a boolean on a create row whose `block_time * 1000 <= mig_ms` (the entry cutoff is later still, so the field is pre-cutoff). Otherwise the field is missing and the mint is not vetoed. Coverage is reported per source.
- **H2, V0-class veto. DROPPED before any outcome read (section 11); report-only counts remain.** Frozen selection restricted to mints whose canonical pool (`canonical_pool(mint)`, deterministic from the mint) has V0 in [16.70, 18.46] SOL (17.58 SOL +/- 5%) in the pinned map. V0 = 0, null/absent and any other value are vetoed.
- **H3, regime gate.** Trade the frozen selection only when the mean flat-leg haircut net of **all** unfiltered migrations with `mig_ms` in (t - 6 h, t - 35 min] is above 0 (level 0, single level, no tuning), with at least 30 rows in the window (else the gate is closed). t = the row's `mig_ms`. Causal: the hold is capped at landing + 30 minutes, landing is about 2.4 s after the first print, plus the exit lag, so migrations at least 35 minutes old (the EXP-015 purge, `PURGE_MIN`) have closed holds. The first 24 h of each block's counted window are left-censored and leave both H3 and its paired comparison; the effect on n is reported (§7). The gate uses the flat leg for both legs' books (declared, not tuned).
- **H4, size by score.** Stake 2x (0.10 SOL, re-simulated cells) for frozen score >= THR95 = 0.8352960347743753, 1x in [THR90, THR95). THR95 is the p95 (index round(0.95 (n-1))) of the 8,801 out-of-fold scores in `ARTIFACTS/exp012/oof_scores.json`, whose p90 is THR90 (a test asserts both). Uses the frozen score; no level is fit on these dates. Uses the 0.10 SOL cells from the sealed sized cache (section 12).

Each remaining H cell (H3, H4) is **1 try**, logged in `data/tries.jsonl` (`exp017_h3`, `exp017_h4`), one `started` line each at the spend point and one `completed` line per pool group.

## 4. Bars (27 non-P1 dates; adapted from EXP-015 bars 1-6)

A cell passes only if all hold, under both fail models:

1. **B1 gate:** n >= 100 trades, >= 5 dates with trades, majority of the 27 dates positive, CI lower bound of mean SOL per trade > 0 (book-stats bootstrap, 1,000 draws, seed 1, 5th percentile, and the date-cluster bootstrap), total ex-top-3 > 0.
2. **B2 paired vs frozen EXP-012:** x_m = (cell net - frozen net) per migration over every row in scope: mean > 0, date-cluster CI90 lower bound > 0, x total ex-top-3 > 0, **and Holm-significant** (§5).
3. **B3 concentration:** no date above 20% of the positive-date total; total excluding the best date > 0.
4. **B4:** P2 + P4 only, mean > 0.
5. **B5 August replication:** P2 only, mean > 0 and a majority of P2 dates positive. No level is fit on any block (H3's level is 0, H4's thresholds come from the EXP-012 freeze), so this replaces "train September, score August"; if a level were fit it would be train-on-September, score-on-August.
6. **B6 September stability:** P3 + P4 only, mean > 0.

P1 numbers are reported, never gating.

## 5. Multiplicity

Holm across H3 and H4 (k = 2) at family alpha 0.05 on the one-sided paired date-cluster bootstrap p of mean x (10,000 draws, seed 1, p = (1 + #{draw mean <= 0}) / (1 + draws), max over the two legs). C0 is outside the family.

## 6. Refusals (checked before the `started` line; no try is spent)

- Cache manifest or raw manifest sha256 differs from §1; any `v_P*.manifest.json` head differs from the pinned code sha, or its V map sha differs from the pin.
- V map sha differs from the pin; any view path in a reserved fragment (fresh-0808, fresh-0828, forward, raw walkers).
- Zero non-P1 trades in H4 (the outcome-blind cell). Mayhem coverage is report-only now (H1 is dropped). H3's gate reads outcomes, so an empty H3 is detected after `started` and reported as a failed cell, try spent.
- `--sized-cache` absent, or its manifest differs from the plan's `SIZED_MANIFEST_SHA256 = <sha>` line (added by an amendment after the outcome-blind re-sim build, section 12).
- Any earlier `exp017` line in the tries log, or `RUN.lock`: a second run is refused.

**Open before the run:** the sized cache (H4, C0) does not exist yet; see §3 and §8.

## 7. Disclosures

- **Cache origin:** EXP-015 screen, job #242, code `cc366d4`.
- **Tries already on these views** (`data/tries.jsonl`, 97 lines on 2026-10-06; lines touching each pool's counted window, this tool excluded): P1 79, P2 14, P3 6, P4 6. EXP-017 adds 2 on each (H3, H4).
- **Lab-wide alpha dilution:** the 97 logged tries are not corrected for here; Holm covers only these two (H3, H4). Treat any p as optimistic by the number of earlier looks at the same 27 dates.
- **Timing:** DEC-021 walk 2 registers before 2026-10-16T01. A pass here needs a separate pre-registration and a confirmation read of an unread block, so it cannot join walk 2 unless that pre-registration also lands before the deadline. This plan does not claim it will.
- **H3 left-censor effect on n (outcome-blind, rows of the universe):** P1 962, P2 951, P3 905, P4 1,013 rows removed; of the frozen-selected rows 99 (P1), 74, 85, 71 (non-P1: 230 of 2,349).
- **H3 left-censor and the majority bar (outcome-blind):** the first 24 h of each block's counted window are left-censored, so 2026-08-15 (P2 counted window starts 12:00) and 2026-09-03 (P3 starts 12:00) have 0 eligible H3 rows. "Majority of days positive" is taken over the dates with at least one eligible row in scope (the gate's "of those days"), so H3 judges fewer than 27 dates (the count is in screen.json); H4 keeps the dates of its rows.
- **Outcome-blind counts (precount):** frozen-selected non-P1 2,349 (P2 1,485; P3 451; P4 413). H1 2,347; H2 2,345; H4 tiers on non-P1: 1x 1,194, 2x 1,155 (before any re-simulation).

## 8. Precount findings

- V0 = 0 pools and mayhem mints are almost the same set (e.g. P3: 1,459 V0 = 0 pools, 1,459 mayhem mints). H1 and H2 are therefore nearly one hypothesis tested twice; Holm pays for both.
- Both vetoes remove about 2 of the 2,349 frozen-selected non-P1 rows. A filter that removes 2 rows cannot move a mean of this size. *(Superseded by section 11: H1 and H2 were dropped from the family before any outcome read.)*
- Create-row coverage of `is_mayhem_mode`: in the first precount, with a strict `create < migration` rule, coverage was 51-65% (P1B 0%: ingest_hot rows carry no field). The cause is not a cache bug (the cache agrees with the view's `complete` rows): 43.9% of non-mayhem mints graduate in the same slot as their create (in explore-0814 w1, 612 of 813 do it inside the create tx itself, bundled launches), so their migration second equals the create second; the non-mayhem median gap is 10 s and mayhem gaps are never 0. A strict test drops the same-slot mints. The rule above is `<=`. Post-fix coverage (outcome-blind precount): P2, P3, P4 and P1A, P1C 100%; P1B 0% (report-only). The 90% refusal is therefore not triggered. The `<=` rule is a precount-time ruling made before any outcome was read; the strict rule would have refused the run.

## 9. What is already known (so nobody reads this as a blind test)

The frozen EXP-012 book on these 27 non-P1 dates at the deciding costs was reported in the EXP-015 result: n = 2,349, per-trade flat -0.00044 SOL, pressure -0.00054 SOL. No H-cell outcome has been read. B1 requires the filtered book to be positive in absolute terms.

## 10. What a pass earns

Nothing but a pre-registered one-shot read of fresh-0828 or fresh-0808. Not the promotion gate, not a live trial.

## 11. Pre-read change, 2026-10-06: H1 and H2 dropped (manager ruling, before any outcome read)

By the outcome-blind selected-set counts of section 7, each of H1 (2,347) and H2 (2,345) keeps all but about 2 of the 2,349 frozen-selected non-P1 rows (V0 = 0 pools and mayhem mints are almost the same set). A filter that removes 2 rows cannot move the paired bar, so testing it would only spend tries and Holm power. The family is now **H3 and H4 (k = 2)**; Holm thresholds are 0.025 and 0.05. The mayhem and V0-class coverage and veto counts stay in `--precount` and in `screen.json` as report-only. No H1 or H2 try is logged. No outcome of any cell was read before this change.

## 12. Sized cache (C0, H4): `tools/exp017_resim.py`

- **What:** the frozen-EXP-012-selected mints (score >= THR90, 3,322 outcome-blind rows over all sources; selection from the pinned cache's features with the net fields dropped at parse time) re-simulated at 0.10, 0.25 and 0.50 SOL, k = 6, exit lag 2, V pin 0909. Haircut, 505k fee per side and both fail models are applied by the screen exactly as for the 0.05 SOL cells. H3 needs no sized cells (its gate uses the unfiltered 0.05 SOL cache).
- **Design (sealed dir):** the re-sim writes `<out-dir>/sized_cache/v_P*.rows.jsonl` (same schema as the EXP-015 cache) and `SIZED.manifest.sha256`, and prints only counts and hashes. It is outcome-producing but is not a try and not a read; nobody opens the files. The screen consumes the directory only after an amendment adds a single line `SIZED_MANIFEST_SHA256 = <64 hex>` to this file, and re-hashes before use.
- **Commands (mal-research-0, as a MiScusi job, <= 48 GB, 4 workers):**
  1. `python -m tools.exp017_resim --precount --p1-fast-dir ... --out-dir /data/mal/exp017-resim` (count-only, seconds).
  2. The full re-sim command in the module docstring (same view arguments as the EXP-015 screen, `--max-workers 4`).
  3. Amendment line with the printed manifest sha.
  4. `python -m tools.exp017_screen --sized-cache /data/mal/exp017-resim/sized_cache --out-dir /data/mal/exp017-screen`.
- **Runtime estimate (not measured):** one V pass over six sources at 4 workers, only the 3,322 selected mints simulated at three sizes. The EXP-015 V pass (all migrations, five cells each) took about 2.4 h wall time; this is expected at roughly 1-2 h, dominated by tape loading. The screen itself runs in minutes (10,000-draw bootstraps on 27 dates).
- **Pre-`started` checks added to the screen (quant-proof on 174b7fe):**
  - *Decision-equivalence proof:* the re-sim also simulates the 0.05 SOL cell (k = 6, lag 2). Before `started` the screen compares, per mint, the sha256 of [mint, mig_ms, features, that cell] with the EXP-015 cache (code `cc366d4`). Only the match count is printed; any mismatch or an empty comparison refuses.
  - *H4 cells:* with blind keys only (presence, `censored`), the screen counts H4 2x-tier rows that lack an uncensored (6, 2, 0.10 SOL) cell and refuses if the count is above 0 (a missing cell would score x = -frozen net).
  - *Sized-cache meta:* each source's manifest must carry the pinned V map sha, the combos, the `selected_sha256` re-derived from the pinned cache, and one common head; its rows file must hash to its manifest. The pin covers `v_P*.rows.jsonl` and `v_P*.manifest.json`.
- **Seal (honest limit):** the sized files are written mode 0400. That stops accidents, not people; the seal relies on nobody looking at the files before the screen runs.
- **C0 uniform-0.10 row (report-only):** C0 also reports the frozen selection at a uniform 0.10 SOL. If H4 passes without beating uniform 0.10, it is read as a size effect, not a score effect, and earns nothing.
- **Memory:** tape workers capped at 4 (`WORKERS_CAP`), the same ceiling as the EXP-015 passes. One heavy job at a time.
