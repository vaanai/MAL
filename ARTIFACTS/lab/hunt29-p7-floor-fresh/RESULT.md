# P7 FLOOR-FRESH result (exploration sanity check, NOT gate evidence)

Run 2026-10-24 (MiScusi job #1444, mal-research-0, background). Declaration first: DECLARE.md (sha256 5bd0d313..., taken 2026-10-24T00:53:19Z), written before any count or price. Kit: kit PR #848 head 6a94572b (clean tree), **kit PR unreviewed** at the time of the run. Rule: FLOOR v2 `l1` kit form (l1_rule_tpfix_p7.py, one block-key line added), 160,000 lamports per send, 1.9 s leg, 0.25 SOL, rent 0, strand rule total, both fail models, kit defaults. Block: fresh-0903 `[2026-09-03T12, 2026-09-09T12)`, 144 h (exploration pool since the 2026-10-06 ledger move). One cell, no variant.

## Numbers beside F0 (hunt28 l3 `l1` at 160k, four blocks, in-sample)
| read | P7 fresh-0903 | F0 (4 blocks) |
|---|---|---|
| attempts / fills | 62 / 62 (counts-only upper bound after overlap: 83) | 332 |
| trades/day (kit: attempts / 7 UTC dates) | 8.9 (62 over the 6.0 days of hours = 10.3) | 13.3 |
| flat mean SOL/trade | +0.00733 (+2.93%) | +0.01605 |
| pressure mean SOL/trade | +0.00506 (+2.03%) | +0.01285 |
| total SOL flat / pressure | +0.454 / +0.314 | about +5.3 / +4.3 (332 x mean, derived) |
| ex-top-3 SOL flat / pressure | +0.009 / -0.065 | +4.405 / +3.490 |
| days positive (days with a trade) flat / pressure | 4 of 5 / 4 of 5 | 18 of 19 / 18 of 19 |
| drop-best-day SOL flat / pressure | +0.118 / +0.026 | +4.407 / +3.493 |
| CI90 lower bound SOL/trade (1,000 draws, seed 1) flat / pressure | -0.00322 / -0.00470 | +0.01118 / +0.00861 |
| impact at 0.25 SOL | 1.37% | 1.37% |

Other kit facts: exits 58 time, 4 take-profit; block-edge exclusions 21 (21 of 83 = 25.3%, over the 5% cap: not decidable in-sample; F0 22.4%); top-day share of total 74% flat, 92% pressure; kit flags KILLED (n 62 < 100, trade CI lower bound <= 0, pressure ex-top-3 <= 0, exclusion share, 0 of 4 canonical blocks for a one-block run); strand rule `sc` (report only) flat +0.0120 / pressure +0.0097 SOL/trade; worst-in-slot equals the end leg to 1e-7. Counts: 19,527 triggers, 83 after overlap, 0 on 09-03 (the 24 h age warm-up and the block edge), 4 to 21 per day after.
Per-block split: fresh-0903 is one kit block, and the kit output carries no per-day totals, so the declared three-walker-segment split is NOT available. Group stats (explore-0814 alone, September pooled) are n/a.

## Reading, in words
On this neighbouring block the same rule has the same sign on the mean under both fail models, but at 46% / 39% of F0's flat / pressure mean, on 62 trades, with the ex-top-3 total at zero (flat) or negative (pressure), the lower CI bound below zero, and the total carried by one day. It does not reproduce F0's breadth; it neither refutes nor confirms it at this n. Disclosure: the same rule family and kit were tuned on neighbouring data, and fresh-0903 has many prior readers (kit PR #848 lists them), so winner's curse and multiplicity apply. Nothing here is gate evidence, no sentence says FLOOR makes money, quant-proof decides.
