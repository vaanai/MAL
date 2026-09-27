---
cursor:
  subagentId: "bc-300a0168-9d56-54e4-a347-697be256e5fa"
---

# Gross edge (paper, sealed / honest-latency only)

**Lead:** No frozen book shows a durable **gross** (pre–portal/venue/priority) edge on uncontaminated evidence. Best gross means sit near **0%** on 0.05 SOL tickets while round-trip fixed costs are **~7.46%** at that size. Promotion gate unchanged (still net-positive after fees).

## Fee identity (code: `paper_curve_math.py`, `PRIORITY_FEE_LAMPORTS = 1_000_000`)

Constants: PumpPortal Local **0.5%** per side (`PORTAL_FEE_PPM = 5_000`), bonding/PumpSwap canonical tier at graduation **1.25%** per side (`BONDING_FEE_PPM = 12_500`), priority **0.001 SOL per side** (not scaled with size). Fees apply **sequentially** (portal then venue) on each leg; rent omitted on a clean round trip (recovered on sell).

Per-side fraction (portal then venue):

\[
f_{\text{side}} = 1 - (1 - 0.005)(1 - 0.0125) = 0.0174375 \;(1.74375\%)
\]

Round-trip portal+venue only (price unchanged):

\[
f_{\text{pv}} = 1 - (1 - f_{\text{side}})^2 = 0.03457093359375 \;(3.457\%)
\]

Priority round trip at ticket size \(S\) SOL:

\[
f_{\text{pri}} = \frac{2 \times 0.001}{S}
\]

Total unchanged-price loss fraction:

\[
f_{\text{total}} = f_{\text{pv}} + f_{\text{pri}}
\]

| Ticket SOL | \(f_{\text{pv}}\) | \(f_{\text{pri}}\) | \(f_{\text{total}}\) | Lamports loss (no price move) |
|---:|---:|---:|---:|---:|
| 0.05 | 3.457% | 4.000% | **7.457%** | 3,728,547 |
| 0.25 | 3.457% | 0.800% | 4.257% | 10,642,735 |
| 0.50 | 3.457% | 0.400% | 3.857% | 19,285,469 |

The analytics **−7.46%** median/mode at 0.05 SOL is **confirmed** (report rounded; exact **7.457%**). Same simulator: unchanged bonding hold_30s trade has `pnl + fee_stack_lamports ≈ 0`.

Direct routing (portal 0) drops only the 0.5%/side portal slice; venue 1.25% remains.

## Metric change (PR)

`book_stats` (LAYA + attention promote) now exposes **`mean_gross_sol`**, **`median_gross_sol`**, **`total_gross_sol`** when each trade carries **`gross_pnl = raw_pnl + fee_stack`** from the tape simulator (`fee_stack_lamports` on each fill). **`promote`** still uses net **`mean_sol`** only.

## Frozen books — gross vs net (0.05 SOL, uncontaminated)

**Sources:** `mal-report-2026-09-27.md` §C4/C7 (honest-latency ≤5 s recv→decision; excludes post-#95 lagged forward ledger). **Not recomputed** on this VM: backward holdout JSON, LAYA forward holdout scoreboard, graduated in-sample tables (no host tape here).

Gross columns are **price edge before portal/venue/priority**; net is after full sim fees (headline 15% fail mix in forward aggregates; C7 gross/net pairing uses per-trade fee estimate on the honest-latency slice).

| Frozen book | n (honest-lat.) | Gross mean ret | Net mean ret (same slice) |
|---|---:|---:|---:|
| laya_0.7 | 564 | **+0.19%** | −7.3% |
| migrate_tp50_sl30 | 97 | **+0.20%** | −7.3% |
| migrate_hold_30s | 141 | −0.63% (median **+2.0%**) | −8.1% |
| laya_0.6 | 931 | −5.52% | −12.9% |
| t30_top1_hold30 | 94 | −7.73% | −15.1% |
| buyers8_top5_ladder2x | 42 | −1.11% | −8.5% |
| buy_all baseline | 2,081 | −25.66% | −32.7% |

**Backward holdout** (sealed backfill, net only in report): buyers8 / t30 / migrate / mig15 means **−0.0030 / −0.0039 / −0.0024 / −0.0013 SOL** at 0.05 SOL — all negative **net**; gross not rescored here.

### Larger tickets (fee identity only, not rescored PnL)

If gross **return** were unchanged in % terms, net would improve mainly via lower \(f_{\text{pri}}\):

| Ticket | Approx. round-trip fee floor | Break-even gross needed |
|---:|---:|---:|
| 0.05 SOL | 7.46% | >7.46% |
| 0.25 SOL | 4.26% | >4.26% |
| 0.50 SOL | 3.86% | >3.86% |

No full 0.25/0.5 SOL book rescore was run (would replay fills; no sealed tape mounted).

## Verdict

Ranking helps (~26 pp gross vs buy_all on laya_0.7) but **does not produce gross edge** large enough to pay **~7.5%** structural drag at 0.05 SOL. Nothing in the frozen set is gross-positive with n and days that could pass promotion even if fees were ignored.
