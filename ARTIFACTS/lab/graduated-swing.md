---
cursor:
  subagentId: "bc-bcf25b45-d448-5435-8300-f5f61e11fedf"
---

# Graduated PumpSwap swing

Paper only. Nothing promotes. Recorder, forward paper, attention, backfill, and funding were left running. Host book: `/var/lib/mal/paper/graduated-swing/out/scoreboard.json`. Code: https://github.com/vaanai/MAL/pull/94 (`cursor/graduated-swing-fedf`). Do not merge.

## Gated summary

Nothing promotes under `tools.laya_v0.book_stats` (n≥100, ≥5 UTC days, majority of days positive, 90% CI of mean SOL > 0, total still positive after dropping the top 3). One UTC day, so the day rule fails every book.

Graduation market cap is about **411 SOL**, still the **0–420** canonical tier: creator **0.30%**, protocol **0.93%**, LP **0.02%**, total **1.25%** — the same venue fee as the bonding curve (pump.fun/docs/fees, page 20 May 2026, re-read 2026-09-25). PumpPortal Local adds **0.5% per side** (round trip **3.46%**). Direct routing drops that to **2.48%**. Two priority fees of 0.001 SOL are **4.0%** of a 0.05 SOL ticket and **0.4%** of 0.5 SOL.

Measured migration-print quote (n=415): p10 **0.06 SOL**, p50 **68 SOL**, p90 **278 SOL**. Entry impact vs spot at the p50 pool is **0.07%** at 0.05 SOL and **0.73%** at 0.5 SOL. Impact is not the loss. A filled flat round trip at 0.05 with portal plus priority costs about **0.00373 SOL**. Direct saves about **0.00049 SOL** per 0.05 ticket, which does not flip a book whose mean is −0.002 or worse.

Buy-all of 415 in-window graduations (curve print strictly before the first PumpSwap print, migration at or after 2026-09-25 07:00Z, tape through 18:25Z). 0.05 SOL, flat 15% fail, misses kept. Least-bad large clock is **mig+30m / hold 30m**: n=381, median **−0.0012**, mean **−0.0021**, win **9.4%**, total **−0.81**. mig+1m / hold 60m: n=385, median **−0.035**, mean **−0.014**, win **12%**. Attention hold 60m is the nearest miss: n=70, mean **+0.013**, median **−0.042**, total **+0.93**, ex top 3 **−0.89**, CI crosses 0.

LightGBM on the flat-15% label, causal RankWindow. Every fold picked **tp50_sl30**. Best model slice is mig+15m top 20%: n=26, mean **+0.0026**, median **+0.017**, ex top 3 **−0.003**. 0.5 SOL holds lose more SOL (mig+1m hold 60m mean **−0.119**). Fee economics do not change the bonding-curve picture.

## Book

| | |
| --- | --- |
| Window | 2026-09-25 07:00:00Z → 18:25:57Z |
| Graduations | 415 (11,818 PumpSwap mints seen; the rest had no earlier bonding print) |
| Decisions | 2,103 (migration + 1/5/15/30/60 min, plus genuine attention after migration) |
| Attention rows | 1,072 stored, 735 genuine |
| Kept prints | 2,945,496 (125,270 live/backfill dupes dropped; live receive time wins) |
| Backfill clock | `t_recv_ms = block_time +` draw from 10,797,885 live chain→receive lags (reservoir 100,000). None missing a block time. |
| Model | lightgbm 4.7, `num_threads=1`, nice 19, ionice idle |
| Fail models | flat 15%; pressure scale 1 fit on mig+1m hold-30m sends, intercept −1.783 |

Promotion is the shared rule. A direct `book_stats` call is what the flag uses. Pressure does not flip any large book positive.

## Fee config (in `tools.graduated_swing`)

| route | size SOL | fee-only loss | entry impact on ~85 SOL grad pool | with 0.002 SOL priority |
| --- | ---: | ---: | ---: | ---: |
| Portal Local | 0.05 | 3.46% | 0.058% | 7.46% |
| Direct | 0.05 | 2.48% | 0.058% | 6.48% |
| Portal Local | 0.5 | 3.46% | 0.58% | 3.86% |
| Direct | 0.5 | 2.48% | 0.58% | 2.88% |

Unwinding into the post-buy pool is a constant-product identity: the round trip versus that book is fees, not impact. Entry impact versus the pre-trade spot is `net_in / quote`. LP stays in the pool; protocol and creator do not. Non-canonical 0.30% is not applied. Migrated pump.fun pools are canonical.

## Buy-all, 0.05 SOL, flat 15%

Median **−0.001** is one priority fee: the mass of the book is a miss or a scratch, not a filled winner. Raw (0% fail) is a little worse and still negative. Wins stay well under the bonding-curve 13.5% on the longer holds.

| point | rule | n | median | mean | win | total |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| mig_1 | hold_5m | 414 | −0.0010 | −0.0040 | 29% | −1.65 |
| mig_1 | hold_60m | 385 | −0.0347 | −0.0137 | 12% | −5.29 |
| mig_5 | hold_30m | 399 | −0.0086 | −0.0092 | 15% | −3.67 |
| mig_15 | hold_30m | 395 | −0.0026 | −0.0066 | 11% | −2.62 |
| mig_30 | hold_30m | 381 | −0.0012 | −0.0021 | 9% | −0.81 |
| mig_60 | hold_30m | 361 | −0.0022 | −0.0035 | 6% | −1.25 |
| attn | hold_15m | 89 | −0.0106 | −0.0012 | 38% | −0.11 |
| attn | hold_60m | 70 | −0.0420 | +0.0134 | 30% | +0.93 |
| attn | hold_120m | 21 | −0.0437 | −0.0346 | 5% | −0.73 |

0.5 SOL, same clocks, holds only: means are several times more negative in SOL. mig+30m hold 30m mean **−0.0044** (still negative). mig+1m hold 60m mean **−0.119**, total **−46 SOL** across independent 0.5 tickets. A lower fee *percentage* does not survive the price path.

## Model

Walk-forward on decision time. Train label is pnl>0 after the flat 15% mix. Exit rule chosen on the training fold only: **tp50_sl30** on all four folds. OOS top-k is `RankWindow` (same object as forward paper).

| point | frac | n | median | mean | ex top 3 | promote |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| mig_15 | 0.20 | 26 | +0.0173 | +0.0026 | −0.003 | no |
| mig_15 | 1.00 | 146 | −0.0010 | −0.0022 | −0.41 | no |
| mig_30 | 1.00 | 137 | −0.0010 | −0.0020 | −0.36 | no |
| attn | 1.00 | 99 | −0.0156 | −0.0051 | — | no |

## Nearest miss

**Attention, hold 60m.** n=70 (needs 100), 1 UTC day (needs 5), mean +0.0134, 90% CI **[−0.022, +0.061]**, total +0.93, total without the top 3 trades **−0.89**. Median is −0.042. The mean is three winners. Pressure scale 1 still has a positive mean (+0.008) and the same tail. Not a watch that clears the bar.

Next large-n book, mig+30m hold 30m, has n=381 and a mean CI that still crosses below zero, one negative day, and a worse total after the top 3.

## What this does not say

The attention poller has been up since 15:26Z, so the attention book is a few hours, not the whole tape. Holds of 120–240 minutes are heavily censored (attn hold 240m n=2). Totals are independent tickets, not a 1 SOL bankroll. Backfill hours on disk at the run were 05, 06, 07, and 16; live hourly tapes cover the rest. A PumpSwap print with no earlier bonding print was not called a migration.
