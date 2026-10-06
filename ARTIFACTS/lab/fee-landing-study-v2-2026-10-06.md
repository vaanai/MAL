# Fee vs landing study v2, 2026-10-06 (job #216): observational, no fee change

**Observational only. Not edge evidence. Any fee change is a DEC-019 amendment for the owner and Helm.**

Job #216 ran `tools.fee_landing_study` at 7366a0f (#355) on research-0, over the explore-0814 w1 view, days 2026-08-26 and 2026-08-27.
- Coverage: getBlock for slots mig..mig+16 of 2,367 migrations. 37,912 slots fetched, 0 missing, 40 skipped, 0 errors.
- Credits: 37,912 of the 60,000 cap.
- Data: 28,623 PumpSwap buys; 26,983 first buys per wallet per pool, of which 20,591 had no detected tip.
- Full tables: [fee-landing-v2/report.md](fee-landing-v2/report.md) and `report.json`.

## Deciding read (no-tip first buys, section (c))

| our-equivalent CU-price fee | n | P(k ≤ 6) [90% CI] | late share k 9–16 [90% CI] |
|---|---:|---|---|
| ≤ 150k | 13,049 | 0.601 [0.587, 0.613] | 0.275 [0.264, 0.289] |
| 150k–250k | 982 | 0.252 [0.225, 0.279] | 0.632 [0.602, 0.662] |
| 250k–500k | 1,725 | 0.493 [0.470, 0.518] | 0.361 [0.337, 0.383] |
| > 500k | 2,332 | 0.341 [0.317, 0.364] | 0.527 [0.503, 0.551] |

Section (b): of no-tip buys landing at k ≤ 6 (n = 9,931), 80.2% paid ≤ 150k our-equivalent, 82.9% paid ≤ 250k and 91.7% paid ≤ 500k.

Section (d): inside a slot, the higher-CU-price buy came first in 61.4% of unequal-price pairs (no-tip). Mean Spearman 0.206: a weak ordering effect.

## Reading

- **Fee does not decide whether a buy lands early.** Cheap buys land at k ≤ 6 more often than every more expensive bucket. Who lands early is set by when they send, not what they pay. This is observational and confounded, since fast bots may also price low. It does not show that a lower fee would land *us* as early.
- Inside a slot, fee buys a little ordering (61% / Spearman 0.21), not the slot.
- **Consequence for the live path:** entry latency (send time) stays the binding lever ([exp012-latency-virtual-2026-10-04.md](exp012-latency-virtual-2026-10-04.md)). Fee is secondary.
- **No change proposed now.** The probe keeps 500k priority (DEC-019 Am.1a). At the 0.05 SOL probe size, 2 × 500k is about 2% of size. At the 0.25 SOL rung it is about 0.4%. Cutting it saves little at trial size and risks the one thing that matters (feedback: never optimise a probe-size artifact).
- If a fee change is ever wanted, the only valid test is a live A/B, as a DEC-019 amendment for the owner and Helm.
