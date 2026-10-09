# C1-NF VERIFY (JUDGE-4 §3.3.1): adversarial check of the post-hoc lead

Written 2026-10-09 at about 01:20Z by the C1-NF verifier on mal-research-0. This is exploration only.

**Data read:**
- the exploration tape `/data/mal/audit-1008/tape/trades`, 776 hours: 08-14T12..08-28T12, 09-03T12..09-15T12, 09-18T23..09-25T07;
- `/data/mal/hunt-shared` (tokens, bars);
- the PumpSwap V maps (`pool_v*.json`; nothing under `forward-1002`).

**Not read:** any sealed block (fresh-0802 / 0808 / 0828), forward-1002, walk 2, forward-paper or runner row.

Numbers are copied from the logs and JSON in this directory. They are not rounded up.

## Verdict: SURVIVES-VERIFY

C1-NF is eligible for pre-registration on fresh October data only.

None of the four §3.3.1 item-5 kill rules fires. They were evaluated in the spec's order (cap before the per-mint book) at 1.3 s, on the independent simulator, at both the deciding 505,000-lamport send fee and C1's 55,000.

This is **not** evidence of an edge:
- the cap was chosen after the read;
- the four September blocks have been read by about 40 hunts.

Three conditions must be met before any EXP is frozen (details in §7):
1. make the wallet ledger deterministic and pin it;
2. report `h_top5` and `h_top10` next to `h_top1`;
3. disclose the adverse-fill risk.

## 1. Hash check (§3.3.2)

All ten pinned artifacts match the §3.3.2 table:
- RULE.md, `ml/rule.json`;
- `common2`, `10_meta`, `11_passA`, `12_passC`, `14_export`, `mlcommon`, `16_confirm`;
- `x07_posthoc_gate`.

Two more checks also pass:
- `ml/confirm_primary_trades.npz` = `a5e27eb6…dbf2`;
- `sha256sum -c ml/frozen_sha256.txt` is OK on all 10 files.

## 2. Rebuild and reproduction of the frozen primary

**How it ran:**
- MiScusi job #415, about 33 min. The steps were `01_wallet_daily` → `10_meta` → `11_passA` per day (2 workers) → `12_passC` → `14_export` → the pinned `16_confirm`.
- Only `common2.py` (`O`, `TMP`) and `01_wallet_daily.py` (`O`, temp dir, zstd) were path-patched; see `PATCHES.diff`.
- Every other pinned script ran byte-identical (same sha256).

**What matched C1 exactly:**
- `work/universe`, `creates`, `mkt` and `out/mout` equal C1's surviving copies row for row (EXCEPT ALL = 0 both ways).
- Pass A grid / pre / cand counts equal C1's logs on all 36 days.
- `disc.npz` is 806,592 × 107 and `conf.npz` is 625,889 × 107, as C1's were.

**The frozen primary, rebuilt with the pinned scorer** (C1's numbers in brackets):

| latency | leg | n | days + | mean % | trade CI90 lo | date CI90 lo | total SOL |
|---|---|---|---|---|---|---|---|
| 1.3 s | flat | 8,797 [8,799] | 4/21 | −2.212 [−2.238] | −2.661 | −2.972 | −48.6502 |
| 1.3 s | pressure | 8,797 | 3/21 | −1.765 [−1.794] | −2.141 | −2.365 | −38.8164 |
| 1.9 s | flat | 8,776 [8,779] | 4/21 | −2.309 | −2.789 | −3.140 | −50.6690 |
| 1.9 s | pressure | 8,776 | 4/21 | −1.747 | −2.148 | −2.435 | −38.3305 |

**Row-level status agreement:**
- 8,733 trades are in both books.
- That is **99.25% of C1's 8,799** and 99.27% of the rebuilt 8,797, but the symmetric Jaccard is **98.53%**.
- On all 8,733 shared trades, the nofail and flat P&L are equal to within 1 lamport.
- The pressure values differ only because the intercept is refit on a slightly different book.
- The largest prediction difference on C1's trades is 0.0049.

**Cause of the gap, which is a reproducibility defect in the pinned pipeline, not a leak:**
- `01_wallet_daily.py` is not bit-reproducible. DuckDB sums floats in parallel.
- Two runs of the same day differ on 38,107–69,660 wallet rows (by ≤ 3e-8 SOL).
- `cash` changes sign for 3 wallets per day. That flips `skill = cash > 0`, which shifts the wallet features and the LightGBM trees.
- The other parts are deterministic:
  - `11_passA.py` rerun on 08-16 gave 0 differing cells in 43,789 × 123;
  - LightGBM gave max |Δpred| = 0.0 between two runs on the rebuilt data.
- C1 deleted its `wl/`, so its exact features cannot be recovered.
- The §3.3.1 ≥ 99% bar holds per C1 trade (99.25%) but not as a Jaccard (98.53%).

**The C1-NF subset:**
- On C1's own 8,799 trades, `h_top1` ≤ 0.5 gives exactly C1's n 422 and flat +8.454%.
- The rebuilt spec-order book has 419 trades and shares 411 of those 422.

## 3. Causality of `h_top1`, and other leaks

**Code (`11_passA.py`, lines 251–254):**
- `hold = np.bincount(tidx[:i1], weights=stok[:i1])`, with `i1 = searchsorted(slot, SD, 'left')`. Only prints with slot < SD enter.
- `df` is the canonical pool only (join on `pool` and `mint`, `venue = 'pumpswap'`).
- Sign: buys +tokens, sells −tokens. Pre-graduation holders are not seen.

**Empirical check:**
- My own code recomputed `h_top1` from the raw tape: raw trader strings, SD as the suffix minimum over the clock, and the canonical pool re-derived from the tape (it matched `universe.pool` on all 50,514 rows).
- It equals the pipeline's value on all 49,766 confirmation picks: max relative difference 5.9e-8 (float32), the same NaN pattern, and the same side of 0.5 for 100%.
- `h_top5`, `v5`, `new5` and `qreal` match equally well. I did not recompute `surge` independently.

**Verdict: `h_top1` is causal.**

**Other checks:**
- **Features.** The 107 features contain no landing-time or future field.
- **As-of tables.** Creator outcomes are used only once known (grad + 6 h ≤ t, own token excluded). Narrative and market features are as-of, and the wallet ledger uses prior days only.
- **Universe.** The filter is known by grad + 120 s.
- **Label timing (minor).** Each day 0–139 training rows (≤ 0.03% of 367k–688k) have an E3 exit at or after the scored day's 00:00Z. These are quiet pools whose next print came more than 1 h later. Negligible.
- **Slot time.** `hsec` (seconds per slot) is measured over the whole UTC hour. It sets only the landing-slot count, not a feature.

## 4. The independent simulator

`v/v2_sim.py` was written for this VERIFY and shares no code with `11_passA`. It rebuilds from the raw hour files:
- **Pool and V:** the canonical pool, and V from the maps.
- **Clock:** SD = the suffix-minimum slot with block_time ≥ t; s/slot from the hour's clock endpoints.
- **Entry:** landing slot X = SD + round(lat / s), END / START / WORST-in-slot bounds, the fee tier from the repo's `tools/paper_curve_math.py`, and the 1.15× guard.
- **Exit:** at clock time(X) + 300 s, then SD(deadline) + ceil(lag / s). C1 used the pool's next print; this simulator uses the clock.
- **Other:** own trade applied; the pressure inputs; my own book, legs and statistics code (`v/v3_report.py`), with the gate's seeds and draws.

**Agreement with C1's columns:**
- On all 49,766 picks: guard agreement 100%, correlation 0.9970, 99.4% of rows within 1% of stake.
- On the 419 C1-NF rows: correlation 0.9995, mean difference −0.178 pp, 89.0% within 1% of stake.
- Median hold 300 s (p95 301 s); no sell came after the pool's last print.
- **Order:** cap before the book or after it gives the same 419 trades. Farm rows never block a non-farm row of the same mint here.

## 5. Item-4 numbers

All rows below use the independent simulator, spec order (`h_top1` ≤ 0.5, not NaN, before the book), E3, 0.25 SOL, END bound and a 0.55 s sell lag unless the row says otherwise. Each cell is flat / pressure. "Mean" is % of stake per attempt.

### 5.1 Deciding cell (1.3 s, 505k per send)

| stat | flat | pressure |
|---|---|---|
| n / days positive | 419 / 18 of 21 | 419 / 18 of 21 |
| mean % | +8.151 | +7.220 |
| trade CI90 lo % | +5.400 | +4.935 |
| date-cluster CI90 lo % | +5.304 | +4.837 |
| total SOL | +8.5383 | +7.5632 |
| ex-top-3 / ex-top-10 SOL | +7.4878 / +5.9402 | +6.6115 / +5.3158 |
| ex-best-day SOL | +7.2808 | +6.5075 |
| ex-best-block SOL (best block = fast-pool-0918) | +5.6841 | +5.0723 |
| day-level t p (one-sided, 21 days) | 0.003979 | 0.001522 |

**Per block** (n; mean % flat / pressure):
- fresh-0903 (128): +8.823 / +7.744
- exp011-0909 (155): +5.898 / +5.182
- fast-pool-0918 (88): +12.974 / +11.322
- oracle-insample-0922 (48): +4.794 / +4.887

At C1's 55k cost the same cell is flat +8.484 / pressure +7.528, date CI90 lo +5.637 / +5.144, day-level t p 0.002761 / 0.000995. On C1's own columns at 55k: flat +8.635 / pressure +7.660, n 419.

### 5.2 Legs, at 505k per send

Each cell gives the mean % (date CI90 lo %).

| leg | 1.3 s flat | 1.3 s pressure | 1.9 s flat | 1.9 s pressure |
|---|---|---|---|---|
| END, 0.55 s lag | +8.151 (5.304) | +7.220 (4.837) | +8.203 (5.407) | +6.902 (4.584) |
| START bound | +8.265 (5.409) | +7.281 (4.891) | +8.205 (5.376) | +6.873 (4.485) |
| worst-in-slot (buy and sell) | +7.456 (4.628) | +6.656 (4.274) | +7.440 (4.603) | +6.290 (3.934) |
| sell lag 2 s | +8.147 (5.374) | +7.208 (4.919) | +8.209 (5.535) | +6.884 (4.645) |
| sell lag 5 s | +8.571 (5.725) | +7.590 (5.276) | +8.605 (5.920) | +7.244 (4.973) |
| + rent 2,039,280 per fill | +7.458 (4.611) | +6.640 (4.258) | +7.511 (4.716) | +6.323 (4.006) |
| stacked: worst-in-slot + rent | +6.762 (3.934) | +6.076 (3.695) | +6.749 (3.912) | +5.711 (3.364) |

**Days positive:**
- the rent leg: 16/21 flat and 17/21 pressure at 1.3 s;
- worst-in-slot: 17/21 on both legs at 1.3 s.

**Report-only latencies, END, 505k:**
- 3.0 s: +8.260 / +6.823;
- 4.0 s: +8.080 / +6.778, date CI90 lo +5.397 / +4.434;
- 4.0 s with rent: +7.392 / +6.202.

### 5.3 Caps (1.3 s, END)

| filter before the book | n | 505k flat / pressure | 55k flat / pressure |
|---|---|---|---|
| no cap (the frozen primary in this simulator) | 8,760 | −2.536 / −2.039 | −2.203 / −1.731 |
| `h_top1` ≤ 0.3 | 418 | +8.374 / +7.425 | +8.707 / +7.732 |
| **`h_top1` ≤ 0.5** | 419 | +8.151 / +7.220 | +8.484 / +7.528 |
| `h_top1` ≤ 0.7 | 421 | +7.708 / +6.826 | +8.041 / +7.134 |
| `h_top5` ≤ 0.5 | 418 | +8.374 / +7.425 | +8.707 / +7.732 |

The cut is not a knife edge, because `h_top1` is bimodal among the 49,766 picks:

| `h_top1` range | picks |
|---|---|
| ≤ 0.1 | 1,430 |
| 0.1–0.3 | 64 |
| 0.3–0.5 | 1 |
| 0.5–0.7 | 3 |
| 0.7–0.9 | 1,750 |
| > 0.9 | 46,518 |

### 5.4 The other `x06` splits (1.3 s, before the book; after the book gives the same or ±11 trades)

| split | n | 505k flat / pressure | 55k flat / pressure | days + (flat, 505k) |
|---|---|---|---|---|
| `qreal` < 400 | 1,242 | −0.367 / −0.617 | −0.034 / −0.309 | 14/21 |
| `qreal` ≥ 400 | 7,529 | −2.878 / −2.256 | −2.545 / −1.948 | 7/21 |
| `age` < 1,800 | 1,670 | −1.837 / −1.810 | −1.504 / −1.502 | 8/21 |
| `age` < 1,800 & `wb5_new` > 0.9 & `qreal` < 400 | 399 | +8.533 / +7.434 | +8.866 / +7.742 | 19/21 |
| `h_top1` > 0.5 (farm) | 8,341 | −3.072 / −2.468 | −2.739 / −2.160 | 2/21 |

### 5.5 Effect of the cap on the discovery walk-forward book (E3, threshold 0.02, test days 08-19..08-28)

| book | n | flat / pressure | days + |
|---|---|---|---|
| C1 columns, no cap | 219 [C1: 218] | +10.578 / +8.759 | 10/10 |
| C1 columns, cap before the book | 212 | +10.672 / +8.857 | 10/10 |
| simulator 505k, no cap | 218 | +10.266 / +8.444 | 10/10 |
| simulator 505k, cap | 211 | +10.361 / +8.542 | 10/10 |

The cap is near-inert there, as expected: it removes 7 trades.

## 6. More adversarial checks (report-only)

**Ranking power under the cap** (C1 columns, 505k, flat / pressure, n):

| filter | flat / pressure | n | days + |
|---|---|---|---|
| stage 1 + cap, no model | −5.540 / −4.351 | 32,947 | 0/21 |
| threshold > 0.0 | +4.594 / +4.026 | 764 | — |
| > 0.01 | +6.150 / +5.432 | 583 | — |
| > 0.015 | +7.730 / +6.728 | 500 | — |
| > 0.02 | +8.302 / +7.352 | 419 | — |
| > 0.025 | +9.096 / +7.968 | 357 | — |
| > 0.03 | +10.599 / +8.987 | 300 | — |
| > 0.05 | +12.020 / +10.460 | 144 | — |

The mean rises steadily with the threshold. The model, not the cap alone, carries the result.

**Concentration** (1.3 s, 505k):
- 362 mints, at most 3 trades per mint, 249 mints positive.
- Mint-cluster CI90 lo: +5.609 / +5.024.
- Total without the top 5 mints: +7.010 / +6.191 SOL.

**Tail** (nofail, 505k): p1 −99.29%, p5 −97.81%, median +10.17%, p95 +63.17%. 22 of 419 trades (5.3%) lose ≥ 90%; the win rate is 66.8%.

**Halves** (flat / pressure):
- 09-03..09-12: n 223, 10/10 days, +6.778 / +5.924, date CI90 lo +3.440 / +3.135;
- 09-13..09-25: n 196, 8/11 days, +9.713 / +8.695 (the negative days are 09-15, 09-18 and 09-25, with n of 3, 1 and 4).

**Out-of-support medians** (discovery walk-forward picks vs September C1-NF picks):

| feature | discovery | September |
|---|---|---|
| `qreal` | 142.65 | 142.80 |
| `h_top1` | 0.0365 | 0.0236 |
| `wb5_new` | 0.984 | 0.985 |
| `v5` | 628.7 | 871.8 |
| `age` (s) | 995 | 655 |
| `new5` | 414 | 886 |

**Adverse-fill bound** (a stress test, not a spec leg): if the failed sends were the book's best fills, the mean (1.3 s, 505k, nofail) would be:
- +1.855% if the best 10% fail;
- −5.116% if the best 28.9% fail.

The edge sits in the right tail of hot entries, so correlated fill failure is the main untested live risk.

## 7. Kill rules (§3.3.1 item 5; spec order, 1.3 s)

| rule | at 505k (deciding) | at 55k | fires? |
|---|---|---|---|
| date CI90 lo ≤ 0 on either leg | +5.304 / +4.837 | +5.637 / +5.144 | no |
| ≥ 2 of 4 blocks ≤ 0 on pressure | 0 of 4 | 0 of 4 | no |
| worst-in-slot mean ≤ 0 | +7.456 / +6.656 | +7.789 / +6.964 | no |
| caps 0.3 and 0.7 both ≤ 0 flat | +8.374 and +7.708 | +8.707 and +8.041 | no |

**Result: no kill rule fires, so the verdict is SURVIVES-VERIFY.**

**Conditions before an EXP is frozen:**
1. **Determinism.** Make `01_wallet_daily.py` deterministic, for example by summing integer lamports or using a single thread with an ordered sum. Pin it, and show bit-identical reruns. Until then the October adapter cannot reproduce itself, and this VERIFY's primary agreement stays 98.53% (Jaccard).
2. **Gameability.** `h_top1` is per wallet: a farm operator can defeat it by splitting the bag across wallets. Pre-declare `h_top5` and `h_top10` as reported lines. They matched `h_top1` exactly in September (`h_top5` ≤ 0.5 also gave n 418).
3. **Disclosure.** The cap is post hoc (about 4 splits), on top of C1's best-of-about-24 cell after a 144-cell scan, and about 40 hunts have read September. State the adverse-fill risk (§6) next to the power table. A September PASS here is not gate evidence.

## 8. What I could not check

- **C1's exact features.** C1's `wl/` and npz are deleted, and the ledger is non-deterministic, so a bit-exact replay of C1's run is impossible.
- **The stage-2 model.** I did not re-implement the 107 features or the LightGBM training. I re-ran the pinned code, which is deterministic. The independent parts are:
  - `h_top1` and `h_top5`;
  - the `v5`, `new5` and `qreal` cross-checks (`surge` was not independently recomputed);
  - the canonical pool and V;
  - the clock, fills and exits;
  - the legs, book and statistics.
- **Fill failure in hot pools.** Whether the pressure model's failure pattern holds there; §6 gives only a bound.
- **Rent.** Whether the executor closes the ATA on every sell. Rent is reported both ways.
- **The October adaptations.** PDA canonical pool, event-V and 200 ms slots are out of scope; nothing from October was read.
- **Multiplicity.** No September check can remove it. Only the fresh read can.

## Files

All in `/data/mal/hunt-1008/c1nf-verify/` (`.nobackup` is set; 1.9 GB; DuckDB temp removed).

**Rebuild:**
- `scripts/` holds the pinned copies and `run_rebuild.sh`. `PATCHES.diff` and `scripts_sha256.txt` record the path patches and hashes.
- `work/`, `wl/`, `out/{candx,mout}`.
- `ml/{disc,conf}.npz` and `ml/confirm_primary{.json,_trades.npz}` (the rebuilt primary).

**Verify:**
- `v/v1_wf.py`: walk-forward predictions → `v/preds.npz`, `v/cands.npz`, `v/v1_wf.json`.
- `v/v2_sim.py`: the independent simulator → `v/sim.parquet` and `v/sim2.parquet` (with 3.0 s and 4.0 s).
- `v/v3_report.py` → `v/results.json`.
- `v/v4_extra.py` → `v/extra.json`.
- `v/v5_checks.py` → `v/stacked.json`.

**Logs:** `logs/` (`confirm_primary.log`, `v1_wf.log`, `v2_sim*.log`, `v3_report.log`, `v4_extra.log`, `v5_checks.log`).

**MiScusi jobs:** #415 (rebuild), #420 (v1 + v2), #421 (v3), #423 (v4). The wallet-ledger and pass-A rerun tests were inline checks; their numbers are in §2.
