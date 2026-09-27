---
cursor:
  subagentId: "bc-eb57b4f6-05b1-5a36-9135-d64d6e6c973c"
---

# Migrate direct, frozen

Frozen at **2026-09-27T13:06:36Z**. No parameter below changes after this timestamp.

The cell was the best of 972 on the selection window. That window is closed. This file is the only specification for the out-of-sample test.

## Cell

- Trigger: migrate (first PumpSwap print after a bonding print on that mint)
- Fill bound: slot+1 start (state is the last print with `slot < migration_slot + 1`)
- Slippage cap: 15% (`DEFAULT_SLIPPAGE_CAP = 0.15`)
- Exit: tp50_sl30 (one sell; +50% / −30%, else the 30 minute cap)
- Route: direct (`portal_fee_ppm = 0`)
- Priority: 0.0005 SOL per side (500,000 lamports). This is the slot+1 p75 of landed buys on the selection window. No Jito tip.
- Sizes: 0.5 SOL primary (500,000,000 lamports), and 0.05 SOL (50,000,000 lamports)
- Fail models: flat 15% on sends, and the pressure curve at scale 1. Both are gates. Scale 2 is not used.
- Pressure curve, frozen, not refit on the test set: intercept −1.4548727851312098, slot slope 0.8, SOL slope 0.35, scale 1. Same curve as the selection-window fit (`mixed_net` in `tools/latency_curve.py`).

## Promotion rule

The current rule, unchanged:

at least 100 out-of-sample trades, at least 5 distinct UTC days with a majority of those days positive, lower 90% CI bound of mean SOL per trade > 0, and total SOL still positive after removing the top 3 trades. The book must clear that bar under both the flat 15% fail rate and the pressure-fail model at slope scale 1. Scale 2 is reported and is not a gate.

The bootstrap is 1,000 draws, seed 1. The lower bound is the 5th percentile of those means.

## Windows

Selection window, already seen, not scored again: 2026-09-22T10:00:00Z through 2026-09-25T06:58:00Z.

Backward test: sealed backfill hours whose block interval ends at or before 2026-09-22T10:00:00Z. Block time is the receive clock, the same substitution the selection run used when `t_recv_ms` is missing. Hours in the selection window are not read.

Forward test: from the 2026-09-28T00:00:00Z clean clock, sealed live-tape hours only, real `t_recv_ms` only, size 0.5 SOL only. No 0.5 SOL book on the live runner. Live size ceilings stay where they are.

## Not in this test

A different priority, a tip, portal routing, another exit, another landing bound, a refit of the pressure intercept, or a live 0.5 SOL book.
