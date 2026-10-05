# EXP-012 operating point under V pricing (exploration, best-of-N, not evidence), 2026-10-05

**Exploration, best-of-N, not a promote, not gate evidence.** TEMPLATE: the manager fills the `<...>` fields after the run, copying numbers from `exp012-operating-point/operating_point.md` (JSON alongside). Do not round up. Delete this line when filled.

This reads the 9-day exploration pool EXP-012 was frozen on, the same days the model was fit on (OOF scores remove the model's own-day fit, not the winner's curse of choosing a cell). It never touches the EXP-012 holdout, the backup block, the EXP-011 block or the forward walk (2026-10-02 onwards).

**Run details:**
- MiScusi job `<id>`, code `<sha>` (`tools/exp012_operating_point.py`, PR #`<n>`).
- Wall time `<s>`; no Helius credits.
- V map `/data/mal/pumpswap-virtual/pool_v.json` (sha256 `<..>`), `mcap_mode="v"`. Adapter counts: `<prints / corrected / no V>`.
- Roots: the three clean views, `--verify-view`. Result JSON sha256 `<..>`.
- Cells tried: **N = `<640>`** (5 thresholds x 4 k x 4 sizes x 2 fees x 4 drift rules), all logged in `data/tries.jsonl` (`variant_n` `<a>`..`<b>`).

## Question

Given what the live probe measured (build a25eb17: entry lands k = 5-6; per-side fee 505,000 lamports at priority 500k, 155,000 proposed at 150k; sell fills about -11 to -16 bps vs quote; entry vs quote about +-300 bps), which EXP-012 operating point maximizes expected SOL per trade and per day after costs?

Knobs: entry score threshold {0.70, 0.75, 0.8031 frozen, 0.85, 0.90}; entry latency k {4, 5, 6, 8}; size {0.05, 0.1, 0.25, 0.5} SOL (V-priced impact); per-side fee {155k, 505k}; entry-drift skip {none, 10%, 20%, 30%} (skip if the price at landing is more than X% above the price at slot+1).

## Frozen operating point (reference)

Threshold 0.8031, k = 6, 0.05 SOL, fee 505k, no drift skip.

| n | trades/day | flat mean | flat CI90 | flat SOL/day | flat ex-top3 | flat days+ | press mean | press CI90 | press SOL/day | press ex-top3 | press days+ |
| ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| `<>` | `<>` | `<>` | `<>` | `<>` | `<>` | `<>/9` | `<>` | `<>` | `<>` | `<>` | `<>/9` |

## Nested leave-one-day-out estimate of the selection procedure

Pick the best cell on 8 days (pooled pressure objective), score it on the 9th, pool. Same approach as the DEC-017 section 5 nested estimate in `exp012-exit-sensitivity-2026-10-02.md`. Two objectives: pressure SOL/day, and pressure mean SOL/trade (>= 20 training trades).

| objective | held-out n | distinct cells chosen | flat mean (CI90) | press mean (CI90) | press SOL/day | press ex-top3 | press days+ |
| --- | ---: | ---: | --- | --- | ---: | ---: | ---: |
| SOL/day | `<>` | `<>` | `<>` | `<>` | `<>` | `<>` | `<>/9` |
| SOL/trade | `<>` | `<>` | `<>` | `<>` | `<>` | `<>` | `<>/9` |

## In-sample best cells (optimistic by construction: best of N)

Top of the in-sample table by pressure SOL/day and by pressure mean per trade, copied from the report with n, CI90 and days positive. State the gap to the nested estimate.

`<table>`

## One knob at a time from the frozen point

`<table: threshold, k, size, fee, drift deviations>`

## What this does and does not say

- `<Which knob moves the pressure mean most, and whether the CI90 lower bound is above 0 anywhere. Quote, do not extrapolate.>`
- `<Where the drift skip helps or hurts; trades/day cost of each threshold.>`
- Exit delay stays at the frozen k=1 'start'; live exit lags too. The probe's sell shortfall (-11 to -16 bps) and +-300 bps entry noise are not added to the replay.
- The selection score is the stored OOF score at k=1 timing, not recomputed per k.
- It is not the promotion gate: the gate needs out-of-sample trades and days. Nothing here is out of sample for the cell choice. The EXP-012 forward read (DEC-016) is the evidence; this note can only inform which point to run it at, and any change of operating point has to be pre-registered before that read.

## Decision

`<none, or: which knobs to carry into the live trial pre-registration, and which to leave frozen>`
