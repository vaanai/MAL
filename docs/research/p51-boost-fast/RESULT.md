# P51 BOOST-fast: RESULT — CLOSE (exploration, in-sample, not an edge claim)

Written 2026-10-26T20:33Z (date -u). Declared first in [DECLARE.md](DECLARE.md) (commit `d32e3d9f`). No book has cleared the promotion gate. Nothing here says a book makes money.

**Run.** MiScusi job #1685 (`j_oj1asvIjoPtb6w`), mal-research-0, code `d61d3833`, wall about 1 min, MemoryMax 8G, no Helius credits. Tool `tools/p51_boost_fast_sweep.py` over the unchanged `tools/boostfloor_score.py` (sha256 `030114221cfaadea7ec64bc0dcd0d8e8131d2cac7be049e5a363511e5e781e9f`, printed by the run). Q50 rule sha256 at run `b3f8ecbf8731dc322a035a0b3996b7ed3f0b8c508cc6947014e3de1aa9524fd4` (equal to the pin). Output `/data/mal/hunt-1008/p51-boost-fast/`: `summary.json` sha256 `da32b1df1bcde8f2ede8b74d0ed789fd278a760ff4865b2e009be1a75cdbda4d`, `rows.csv` sha256 `b813a822a6c9800a19e68adec3bcb724ec0034c53d6e56290665aa013ac0e1a1`. Pools 24,323: 4,455 ok, 19,867 no trigger, 1 sps out of range. Inputs: the five exploration blocks only; no October hour.

## Verdict (deciding cell: Q50, 0.25 SOL, k = 5 vs k = 9)

| Fail model | Pooled paired improvement (pp of stake per trade) | CI90 (date cluster) | Blocks improving |
| --- | ---: | --- | ---: |
| flat | +0.6068404910669546 | [−0.0440, +1.2821] | 4 of 5 |
| pressure | +0.7133898466332986 | [+0.0603, +1.5136] | 4 of 5 |

Kill rule: ≥ +1.0 pp under both models **and** ≥ 3 of 5 blocks. The block count passes; the size fails under both models. **P51 CLOSES.**

## Improvement vs k = 9, Q50, 0.25 SOL (paired, n = 4,455 each; pp per trade)

| Cell | flat pooled | flat CI90 | press pooled | press CI90 |
| --- | ---: | --- | ---: | --- |
| k8 | +0.0482 | [−0.3441, +0.4209] | +0.4915 | [+0.1296, +0.9133] |
| k7 | −0.1013 | [−0.5561, +0.2888] | +0.0227 | [−0.2488, +0.3291] |
| k6 (k−3) | +0.2406 | [−0.4216, +0.9060] | +0.2274 | [−0.5985, +1.0627] |
| **k5 (k−4)** | **+0.6068** | [−0.0440, +1.2821] | **+0.7134** | [+0.0603, +1.5136] |
| k4 (k−5) | +0.4764 | [−0.4378, +1.4313] | +0.5618 | [−0.2284, +1.4989] |

Per block, k5 − k9 (flat / press, pp): exp011-0909 +0.1335 / −0.7198; explore-0814 −0.0112 / +0.2781; fast-pool-0918 +2.3925 / +0.9863; fresh-0903 +0.5250 / +1.2035; oracle-insample-0922 +2.7262 / +3.6337.

The curve is not monotone (k4 is below k5; k7 below k8 and k9 under flat). Over k = 4..9 the per-trade mean moves by under 1 pp on a level of about 8–9%: on these blocks the Q50 entry is nearly flat in k.

**Time-matched view (report-only).** Median sps per block (from the run): explore-0814 0.3681, fresh-0903 0.3163, exp011-0909 0.3163, fast-pool-0918 0.2669, oracle-insample-0922 0.2663 (the DECLARE's "about 0.37–0.40" was too high for the September blocks). The seconds legs, both vs k9: d2.485 ("Helius") flat −0.2961 / press −0.0836; d3.302 ("today") flat −0.0660 / press +0.1937. The same 4,455 pools trade in every cell, so the Helius-minus-today difference is flat −0.2301 pp, press −0.2773 pp (my arithmetic from those four numbers). At the measured 0.817 s gain, no improvement is visible.

## Levels by k, Q50, 0.25 SOL, pooled (36 UTC days, n = 4,455)

In-sample levels on blocks where H5 was discovered and confirmed. Winner's curse applies. These are **not** evidence and not a gate read. The H5 evidence is the EXP-024 forward reads, which have not passed; Look 2 is sealed to its end state. The `gate_arithmetic: true` labels in summary.json are arithmetic on in-sample rows.

| k | flat mean % | flat CI90 low % | flat ex-top-3 SOL | days + | press mean % | press CI90 low % | press ex-top-3 SOL | days + |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 9 | 8.5547 | 5.9636 | 80.0120 | 27 | 7.4650 | 5.4686 | 72.1416 | 31 |
| 6 | 8.7953 | 5.9935 | 82.8280 | 28 | 7.6924 | 5.4575 | 74.0170 | 30 |
| 5 | 9.1615 | 6.2488 | 85.9784 | 26 | 8.1784 | 5.8401 | 76.8488 | 29 |
| 4 | 9.0311 | 6.0229 | 84.7351 | 26 | 8.0268 | 5.7055 | 77.6266 | 29 |

Per block, k9 → k5 flat mean % (n): explore-0814 6.5987 → 6.5875 (2,430); fresh-0903 14.0310 → 14.5560 (529); exp011-0909 9.5231 → 9.6566 (575); fast-pool-0918 5.5774 → 7.9698 (403); oracle-insample-0922 13.3789 → 16.1051 (518). Each block keeps a CI90 low above 0 at k9 and at k5 under both models (full per-block table in summary.json `levels.*.by_block`).

**Impact at 0.5 SOL.** Means drop by about 0.04 pp vs 0.25 SOL at every k (k9 flat 8.5130 vs 8.5547; k5 flat 9.1168 vs 9.1615); improvements are unchanged to two decimals (k5 flat +0.6038, press +0.7101).

**H5 v1 (Q* = 40, report-only).** k5 − k9: flat +0.2554, press +0.8734; k4 − k9: flat +0.9576, press +1.1890 (exp011-0909 negative at every k ≤ 8: −3.1222 / −2.8694 at k4). Not deciding.

## Capacity (declared formula)

oracle-insample-0922: 518 Q50 trades at k9 over 79 h = 157.367 trades/day × 0.25 SOL × improvement: flat 0.2387 SOL/day, press 0.2807 SOL/day. This assumes every trigger fills and no concurrency cap; October trade rates were not used (sealed). It is an upper sketch of the improvement only, not of the book.

## What was not measured

- Live landing at k ≤ 6 on any feed: the Helius path is a model (feed p50 gap 0.817 s from the QP ruling and P47), not a fill.
- October regime (200 ms slots, post-upgrade): sealed for H5 (EXP-024 Look 2). Slot-unit transfer from Aug/Sep (0.27–0.37 s/slot) to October (about 0.21 s/slot) is untested.
- Exit lag is the rule's (0.55 s); a faster feed was not credited on the exit.
- Arena-layer terms (DEC-031) are not applied.

## Section 4 of the brief (only if PASS)

Not applicable: P51 closed. For the record, a Helius-fed BOOST-fast book would need a new registration (a successor under DEC-014, own clean clock, both fail models), a live Helius transactionSubscribe feed component, and about 175k Helius credits per day (manager's estimate, not measured here).
