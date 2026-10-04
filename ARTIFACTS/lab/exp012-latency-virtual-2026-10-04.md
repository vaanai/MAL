# EXP-012 entry latency under V pricing (exploration, not evidence), 2026-10-04

**Exploration only.** This reads the 9-day exploration pool EXP-012 was frozen on. It is not a holdout, not evidence for the gate, and not a promote. Every number below is copied from [exp012-latency-virtual/latency_sensitivity.md](exp012-latency-virtual/latency_sensitivity.md) (JSON alongside, sha256 `472493ee5010244cb57d501fbb85ea7c3a87b27f07be78c187a6bca17025c7a6`).

**Run details:**
- MiScusi job #110 (`j_R2te6p2ISY00Aw`), code `fd449ff` (#287 wrapper `tools/exp012_latency_virtual.py` around the unchanged #224 latency tool).
- Wall time 4,120 s; no Helius credits.
- V map `/data/mal/pumpswap-virtual/pool_v.json` (sha256 `bea2324e…91e5`), `mcap_mode="v"`.
- Adapter counts: 76,022,357 PumpSwap prints; 76,020,312 corrected; 2,045 with no V; 26,302,547 bonding prints untouched.
- Roots: the three clean views in `/data/mal/exp012/row_counts.json`, `--verify-view`.
- Selection: the stored OOF score ≥ the frozen threshold, so 881 of 8,801 rows, the same as the V-less curve.

## Question

V pricing (#281) and entry latency (#224) each cut EXP-012's mean by about a fifth. They had never been measured together. DEC-016 Amendments 3 (a) and 4 judge live support on the V-corrected book at the measured k(p50) and k(p90). This curve says how much room there is.

## Result (OOF-selected book, n = 881, 0.5 SOL entries)

| k | fill | flat mean | flat CI90 | press mean | press CI90 | press ex-top-3 |
| ---: | ---: | ---: | --- | ---: | --- | ---: |
| 1 | 0.967 | 0.03021 | [0.01854, 0.04172] | 0.01867 | [0.01114, 0.02631] | 15.297 |
| 2 | 0.948 | 0.02601 | [0.01465, 0.03722] | 0.01620 | [0.00886, 0.02364] | 13.337 |
| 4 | 0.915 | 0.02105 | [0.00996, 0.03170] | 0.01332 | [0.00653, 0.02005] | 10.898 |
| 6 | 0.907 | 0.01880 | [0.00760, 0.02999] | 0.01246 | [0.00541, 0.01969] | 10.168 |
| 8 | 0.865 | 0.01177 | [0.00065, 0.02226] | 0.00769 | [0.00118, 0.01418] | 5.977 |
| 12 | 0.825 | 0.00581 | [−0.00479, 0.01652] | 0.00335 | [−0.00412, 0.01051] | 2.250 |
| 16 | 0.812 | 0.00285 | [−0.00755, 0.01304] | 0.00327 | [−0.00375, 0.01012] | 2.028 |
| 24 | 0.793 | 0.00369 | [−0.00635, 0.01388] | 0.00282 | [−0.00455, 0.01033] | 1.691 |

**Unfiltered (n = 8,801), V-priced:**
- pressure mean −0.00098 at k = 1 and −0.00245 at k = 24;
- negative at every k;
- CI90 entirely below 0 from k = 8 on.

**Next to the V-less curve** (`exp012-latency-sensitivity-2026-10-02.md`), pressure mean, selected book:

| k | V-less | V |
| ---: | ---: | ---: |
| 1 | 0.02315 | 0.01867 |
| 4 | 0.01875 | 0.01332 |
| 6 | 0.01817 | 0.01246 |
| 8 | 0.01387 | 0.00769 |
| 12 | 0.01327 | 0.00335 |

## What this says

- **The losses compound.** Under V pricing, the selected book keeps both CI90 lower bounds above 0 through k = 8, but only barely at k = 8 (flat 0.00065, pressure 0.00118).
- **At k ≥ 12 every lower bound is below 0.** The pressure mean is 0.00335 or less, about 18% or less of its k = 1 value.
- **Entry latency is the binding constraint for live**, more than anything else now known. Without the V correction, k = 12 still looked comfortable (V-less pressure 0.01327). With it, it does not.
- **Implication for DEC-016 Am.3 (a)/Am.4.** If the fast-0 runner plus executor lands at a k(p50) of about 10 or more slots, live support is unlikely even if the 10-16 FINAL read passes.
  - The real k is unmeasured. It will come from the runner latency export after the install, and from the DEC-019 probe's landed slots.

## What this does not say

- **Exits are still at the frozen k = 1**, so live decay is at least what is shown.
- **The selection score is computed at k = 1 timing.**
- **Same 9 days the recipe was tuned on**, so winner's curse applies. It is a sensitivity curve, never a promote.
- **Nothing about live fills, the forward window, or seconds.** The slot-to-seconds conversion depends on the measured slot time.

## Use

1. Measure the fast-0 decision-to-land latency in slots as soon as the runner is up (Am.3 latency export) and from the probe.
2. If k(p50) is above about 6, latency work comes before anything else on the live path.
