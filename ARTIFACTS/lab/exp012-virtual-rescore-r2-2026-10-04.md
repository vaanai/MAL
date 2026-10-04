# EXP-012 V-corrected re-score, r2 (correction analysis, not a new read), 2026-10-04

**This is a correction analysis.** It re-prices the frozen EXP-012 entries with the PumpSwap virtual quote reserve V. It is not a new read, not evidence for the gate, and not a promote. The EXP-012 verdict stays with the 10-16 FINAL forward read (DEC-016 Amendments 1–4).

**Run details:**
- `tools.exp012_virtual_rescore rescore --run-id r2`, code `bbcaeb6` (same as main `79b34c2` for this module and the adapter).
- The rows were built by PID 3506243, outside MiScusi. Its report step was finished by MiScusi job #109 (`j_YWj8WFQHqo-F7Q`).
- V map: `/data/mal/pumpswap-virtual/pool_v.json`, sha256 `bea2324ee48f6d4b741ff4604d0a5fc0074a2b62635afc3dbd29348a39f891e5`. `mcap_mode="v"`.
- `report.json` sha256: `82625fc44fc0a30e49d4ae028821a276b1db2143f7ea9d07ae2f3366d129721a`.

## r2 reproduces r1

Every gate number below is identical to r1 (#281), to the last digit, in both books and both pricings. The unpatched pass reproduces the original read: 451 of 451 entered, 451 of 451 `flat` values equal. The entered set is identical between frozen and corrected pricing.

## Spent read block (n = 451), frozen → V-corrected

| Metric | Frozen | Corrected |
| --- | ---: | ---: |
| flat mean SOL/trade | 0.03487 | 0.02527 |
| flat CI90 | [0.01875, 0.05133] | [0.00928, 0.04232] |
| flat ex-top-3 SOL | 14.040 | 9.861 |
| flat days positive | 6/7 | 5/7 |
| pressure mean SOL/trade | 0.02054 | 0.01534 |
| **pressure CI90** (`book_stats` on the pressure leg alone; follow-up 4) | [0.01103, 0.02987] | **[0.00605, 0.02480]** |
| pressure ex-top-3 SOL | 8.217 | 6.014 |
| pressure days positive | 6/7 | 5/7 |
| fill rate, entered | 0.9889 | 0.9778 |
| gate (both legs) | PASS | PASS |

**Exit mix, entered:**

| Exit | Frozen | Corrected |
| --- | ---: | ---: |
| trigger gain | 262 | 247 |
| trigger loss | 177 | 188 |
| none | 5 | 10 |
| cap | 7 | 6 |

Pressure leg by day, corrected (SOL): 09-03 +1.8101, 09-04 +2.7909, 09-05 −0.2401, 09-06 +0.5420, 09-07 +1.9650, 09-08 −0.0212, 09-09 +0.0722.

**Adapter counts, read block:**
- 65,813,366 PumpSwap prints;
- 65,778,977 corrected;
- 34,389 with no V, on 7 pools;
- 15,541,611 bonding prints untouched.

## Exploration OOF book (n = 891, nested LODO selection, not refit)

| Metric | Frozen | Corrected |
| --- | ---: | ---: |
| flat mean SOL/trade | 0.03716 | 0.03020 |
| flat CI90 | [0.02528, 0.04805] | [0.01928, 0.04113] |
| pressure mean SOL/trade | 0.02336 | 0.01879 |
| pressure ex-top-3 SOL | 19.458 | 15.587 |
| days positive (flat) | 9/9 | 9/9 |

All 891 entries matched, and all 891 had identical features.

**Unfiltered migrate-direct baseline (n = 8,801):**
- flat mean +0.133% → **−0.144%**;
- pressure mean +0.019% → **−0.196%**;
- fill rate 0.3468 → 0.3333.

## What this says and does not say

- **Says:** under V pricing, the selected book keeps a positive mean, and both CI90 lower bounds stay above 0 on the spent block. The pressure-leg lower bound is thin, at 0.00605 SOL per 0.5 SOL entry. Without the selection, the baseline is negative, so the selection carries the edge.
- **Does not say:**
  - Anything about the forward window, or about live fills.
  - Anything about latency. Every number here enters at slot+1. The exploration latency curve (`exp012-latency-sensitivity-2026-10-02.md`, V-less) lost about 20% of the pressure mean at k = 4–6.
  - V and latency together have not been measured; the losses may compound.
- **Winner's curse** applies to the OOF book.
