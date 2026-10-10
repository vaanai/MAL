# C1 cascade post-grad — FROZEN RULE (v2)

Frozen 2026-10-08 before any CONFIRMATION label is read. Chosen on DISCOVERY (graduation days 2026-08-14..2026-08-28) only.
The sha256 of this file, of `ml/rule.json`, `ml/rule_secondary.json` and of every script below is recorded in REPORT.md. None of them changes after that.

## Universe and decision points (scripts/10_meta.py, scripts/11_passA.py)
- Every pump.fun `complete` graduation whose canonical PumpSwap pool (hunt-shared `tokens.pool`) has V in [17.5e9, 17.7e9] lamports and whose first
  PumpSwap print is within [-5 s, +120 s] of the `complete` row. Known at graduation time; no survivorship filter.
- Decision grid: whole UTC minutes from grad + 10 min to grad + 24 h, clipped so decision + 3,600 s + 300 s stays inside the tape segment
  (S2 [2026-09-03T12, 2026-09-15T12), S3 [2026-09-18T23, 2026-09-25T07)). A point is alive if the pool printed in the previous 60 min.
- Features use only prints with slot < decision slot (= first tape slot whose block_time >= T), the prior-day wallet ledger (tape days strictly
  before the decision's UTC day), and as-of cross-token tables (scripts/12_passC.py).

## Stage 1 (LAYA filter)
`(v5 >= 1 SOL and surge >= 3) or new5 >= 20`, and real quote reserve (quote_reserve, without V) >= 20 SOL, inside the pass-A superset
`v5 >= 0.5 and (surge >= 1.5 or new5 >= 8)`. v5 = PumpSwap SOL volume of the last 300 s; surge = v5 / (v60 / 12 + 0.05); new5 = first-time buyers of
the pool in the last 300 s. (`ml/rule.json` "stage1": [1.0, 3.0, 20.0, 20.0].)

## Stage 2 (smart model) — scripts/16_confirm.py with ml/rule.json
- Features: the 107 columns exported by scripts/14_export.py (stage-1 flow features, trajectory shape, PumpSwap holder concentration, curve-holder
  selling, wallet intelligence of the 5 / 15 min buyers and sellers from the prior-day ledger, creator track record, narrative heat, market activity,
  pre-grad bonding stats, hour of day). No landing-time or future field.
- Model: LightGBM regression, objective huber (alpha 0.05), learning rate 0.03, 31 leaves, min_data_in_leaf 300, feature_fraction 0.7,
  bagging 0.8 / 1, lambda_l2 10, 400 rounds, seed 1, deterministic, num_threads 3 (mlcommon.lgb_params).
  Target y = clip(pnl_E3 at 1.3 s / 0.25 SOL, -0.5, 1.0) on stage-1 rows.
- WALK-FORWARD (pre-declared): for each CONFIRMATION UTC decision day D, retrain from scratch on every stage-1 row with t < D 00:00Z - 3,600 s
  (all DISCOVERY rows and earlier CONFIRMATION rows; every such label is resolved before D), then score day D's stage-1 rows.
- BUY when the prediction > 0.02 (2% of stake, net).

## Execution and exit (as pass A computed them; no refit)
- Size 0.25 SOL; buy lands at decision slot + round(1.3 s / measured seconds-per-slot of that UTC hour) (primary) or 1.9 s (binding), END bound,
  own trade applied, PumpSwap fee tier on (quote + V, base); 55,000 lamports per send on buy and sell; guard: the buy reverts (one send fee) if
  size / tokens out > 1.15 x the decision-time spot.
- Exit E3: time exit. Deadline = first print whose block_time >= block_time(last print at or before the landing slot) + 300 s; the sell fills at the
  state before the first print with slot >= deadline-print slot + ceil(0.55 s / s-per-slot) + 1 (state after the last print if none).
- Book: rows in time order; a mint holds at most one position; a selected row is taken only if t >= the previous exit time + 60 s for that mint.

## Fail legs and statistics (mlcommon.legs, mlcommon.gate)
- flat: a fill pays 0.85 pnl + 0.15 (-55,000); pressure: p = sigmoid(c + 0.8 log1p(same-slot buys at landing) + 0.35 log1p(buy SOL in the 2 s up to
  landing)), c fitted so the mean p over the book's fills is 0.289; a guarded row is -55,000 on every leg.
- Per leg: n, days, days positive, mean % of stake, trade-level CI90 lower bound (1,000 draws, seed 1, 5th pct), date-cluster CI90 lower bound
  (1,000 day resamples, seed 1), total SOL, ex-top-3, ex-best-day, per block, both latencies.
- Gate (both flat and pressure, primary latency deciding, binding reported): n >= 100, >= 5 days with a majority positive, trade-level CI90 lower
  bound > 0, total ex top-3 > 0. The date-cluster CI90 lower bound is reported next to it and called out if <= 0.
- Verdict: "promising" if the gate is met or nearly met on both legs; "weak" if positive but not near; "dead" otherwise.

## Secondary book (REPORT-ONLY, never decides the verdict) — ml/rule_secondary.json
Same everything, exit E7 (time exit 900 s), target pnl_E7, threshold 0.06. It measures whether the discovery "ramp farm" pattern persists.

## The one CONFIRMATION run
1. `14_export.py ml/conf.npz <graduation days 2026-09-03 .. 2026-09-25>` (all 21 confirmation graduation days present on the tape).
2. `16_confirm.py ml/rule.json ml/disc.npz ml/conf.npz ml/confirm_primary.json` and the same with `ml/rule_secondary.json` -> `ml/confirm_secondary.json`.
3. Copy the numbers into REPORT.md. No re-run with changed settings.
