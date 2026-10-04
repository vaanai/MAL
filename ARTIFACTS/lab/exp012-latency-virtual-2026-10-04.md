# EXP-012 entry latency under V pricing (exploration, not evidence), 2026-10-04

**Exploration only.** This reads the 9-day exploration pool EXP-012 was frozen on. It is not a holdout, not evidence for the gate, and not a promote. Every number below is copied from [exp012-latency-virtual/latency_sensitivity.md](exp012-latency-virtual/latency_sensitivity.md) (JSON alongside, sha256 `472493ee5010244cb57d501fbb85ea7c3a87b27f07be78c187a6bca17025c7a6`).

**Run details:**
- MiScusi job #110 (`j_R2te6p2ISY00Aw`), code `fd449ff` (#287 wrapper `tools/exp012_latency_virtual.py` around the unchanged #224 latency tool).
- Wall time 4,119 s; no Helius credits.
- V map `/data/mal/pumpswap-virtual/pool_v.json` (sha256 `bea2324e…91e5`), `mcap_mode="v"`.
- Adapter counts: 76,022,357 PumpSwap prints; 76,020,312 corrected; 2,045 with no V; 26,302,547 bonding prints untouched.
- Roots: the three clean views in `/data/mal/exp012/row_counts.json`, `--verify-view`.
- Selection: the stored OOF score ≥ the frozen threshold, so 881 of 8,801 rows, the same as the V-less curve.

## Question

V pricing (#281) cut the k = 1 pressure mean by 19.36%; entry latency alone (#224, V-less) cut it by 19.02% at k = 4 and 21.51% at k = 6. They had never been measured together. DEC-016 Amendments 3 (a) and 4 judge live support on the V-corrected book at the measured k(p50) and k(p90). This curve says how much room there is.

## Result (OOF-selected book, n = 881, 0.5 SOL entries)

| k | fill | flat mean | flat CI90 | press mean | press CI90 | press ex-top-3 |
| ---: | ---: | ---: | --- | ---: | --- | ---: |
| 1 | 0.967 | 0.03021 | [0.01853, 0.04172] | 0.01866 | [0.01114, 0.02631] | 15.297 |
| 2 | 0.947 | 0.02601 | [0.01464, 0.03721] | 0.01620 | [0.00886, 0.02363] | 13.336 |
| 4 | 0.914 | 0.02105 | [0.00996, 0.03169] | 0.01332 | [0.00653, 0.02004] | 10.898 |
| 6 | 0.906 | 0.01879 | [0.00759, 0.02998] | 0.01246 | [0.00540, 0.01969] | 10.167 |
| 8 | 0.864 | 0.01176 | [0.00064, 0.02225] | 0.00769 | [0.00117, 0.01418] | 5.977 |
| 12 | 0.825 | 0.00581 | [−0.00479, 0.01651] | 0.00335 | [−0.00413, 0.01051] | 2.249 |
| 16 | 0.811 | 0.00285 | [−0.00755, 0.01304] | 0.00326 | [−0.00376, 0.01011] | 2.028 |
| 24 | 0.793 | 0.00368 | [−0.00636, 0.01387] | 0.00282 | [−0.00455, 0.01032] | 1.690 |

**Unfiltered (n = 8,801), V-priced:**
- pressure mean −0.00099 at k = 1 and −0.00245 at k = 24;
- negative at every k;
- CI90 entirely below 0 from k = 8 on for the pressure model, and from k = 12 on for the flat model (flat upper bound at k = 8 is +0.00030).

**Next to the V-less curve** (`exp012-latency-sensitivity-2026-10-02.md`), pressure mean, selected book:

| k | V-less | V |
| ---: | ---: | ---: |
| 1 | 0.02315 | 0.01866 |
| 4 | 0.01874 | 0.01332 |
| 6 | 0.01817 | 0.01246 |
| 8 | 0.01387 | 0.00769 |
| 12 | 0.01326 | 0.00335 |

## What this says

- **The two cuts are larger together than added.**
  - At k = 12 on the pressure model, adding the V cut and the latency cut would leave 0.00878. The measured value is 0.00335.
  - The combined cut exceeds the sum at every k from 2 to 12, on both fail models. No CI was computed on that difference.
  - The selected mean stays above 0 at every k; it is the margin that shrinks, not a losing book.
- **Lower bounds by k.** Under V pricing, the selected book keeps both CI90 lower bounds above 0 through k = 8, but only barely at k = 8 (flat 0.00064, pressure 0.00117).
- **At k ≥ 12 every lower bound is below 0.** The pressure mean is under 18% of its k = 1 value: 17.95%, 17.49% and 15.12% at k = 12, 16 and 24 (raw ratios, truncated).
  - Without V, k = 12 kept lower bounds of 0.01005 flat and 0.00613 pressure. With V it does not.
- **Entry latency is the largest measured sensitivity on this pool.**
  - It is not yet established as the binding live constraint. The forward read, the probe's real fills and winner's-curse decay are all unmeasured.
- **Implication for DEC-016 Am.3 (a) and Am.4.**
  - The live-support check delays every exit by k − 1 and applies the trial terms. It also needs at least 5 UTC days with a majority positive. This curve does none of that, so the check can fail at a lower k than this curve suggests.
  - k = 8 passes here only barely, and k = 12 fails. A fast-0 k(p50) of about 8 or more slots makes live support unlikely even if the 10-16 FINAL read passes.
  - The real k is unmeasured. It will come from the runner latency export after the install, and from the DEC-019 probe's stage timestamps and landed slots.
- **The `promote_gate: true` labels** in the JSON for k ≤ 8 are the tool's label on exploration rows. They are not a gate pass.

## What this does not say

- **Exits are still at the frozen k = 1**, so live decay is at least what is shown.
- **The selection score is computed at k = 1 timing.**
- **Same 9 days the recipe was tuned on**, so winner's curse applies. It is a sensitivity curve, never a promote.
- **Nothing about live fills, the forward window, or seconds.** The slot-to-seconds conversion depends on the measured slot time.

## Use

1. Measure the fast-0 decision-to-land latency in slots as soon as the runner is up (Am.3 latency export) and from the probe.
2. If k(p50) is above about 6, latency work comes before anything else on the live path.
