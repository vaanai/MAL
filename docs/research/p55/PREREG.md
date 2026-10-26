# P55 pre-registration: BOOST-end exit for the frozen H5/Q50 BOOST-floor rule

Written before any pricing. No exit time, delta or P&L of this rule was computed before this file was committed. Library rows: lib-c-N3, w2-pump-idl-3. Exploration only; a pass is a hypothesis, never a promote, and no book has cleared the promotion gate.

## Population (frozen, exploration blocks only)

- `/data/mal/hunt-1008/h5-lossfilter/frozen_binding_trades.parquet`: the frozen H5 binding cell (D=40, leg b = landing 1.9 s after trigger, stake 0.1 SOL, exit `end`). 1,702 discovery trades (explore-0814, 2026-08-14..08-28) and 1,124 confirmation trades (fresh-0903, exp011-0909, fast-pool-0918, oracle-insample-0922, up to 2026-09-25T06). All are exploration-pool blocks in docs/HOLDOUT_LEDGER.md. No October hour, no fresh-0802/0808/0828, no EXP-009 block, no forward, arena or meta-capture file is read.
- Paths: `/data/mal/audit-1008/work/g_reachable_cap_book_rescore/{meta,paths}/<day>.parquet` (PumpSwap PRE-trade reserves). State after print i is the pre-trade state of print i+1, as in the frozen s14 code.

## Causal BOOST detector (proxy; the event field is not on the tape, see REPORT step 1)

At print j the detector uses rows 0..j only: BOOST wallet = buy-only wallet with at least 3 buys, each 0.2..2.0 SOL, total <= 17.7 SOL, within the first 1,600 slots (most buys wins); same definition as the frozen rule, re-evaluated causally. `spent(j)` = cumulative buy SOL of that wallet through print j. Proxy vault remaining `R(j) = 17.585 - spent(j)` SOL. This proxy is not `boost_vault_remaining`; the IDL gives no units or zero behaviour for that field and it is absent from our tape.

## Exit rule (fixed now; one parameter set, no grid)

After the landing slot X of the entry, the exit trigger is the first print j with slot > X where:

- E1 (vault end): `R(j) <= 1.0` SOL; or
- E2 (buys stop): the causal BOOST wallet has made at least one buy before j, `R(j) > 1.0`, and `slot_j - slot_lastBoostBuy >= ceil(36 s / sps)` (36 s = three median TWAP slice gaps of about 12 s).

Final exit trigger = min(E1/E2 trigger, the frozen exit trigger `s0 + round(330 s / sps)`). If no BOOST wallet is identified causally by X, or neither fires, the frozen exit stands. The sell lands `el` slots after the trigger, END bound, same sell math, fee tier and 55,000-lamport priority per send as the frozen code.

- Primary exit lag: el = 2 slots (the realistic primary). The frozen baseline is re-priced at the same el = 2 for the paired delta.
- Sensitivity, report-only, never selected among: el = ceil(0.55 s / sps) (the frozen s14 convention), E1 alone, E2 alone.
- Validation gate on the tool: with the frozen lag and the frozen exit, the tool must reproduce the stored `pnl` of frozen_binding_trades; report the max absolute difference.

## Fail models

Applied to the new and the baseline P&L per trade, entry-side p_fail unchanged (the exit rule does not change the buy): flat `0.85*pnl + 0.15*(-55,000)`; pressure-fail `(1-p)*pnl + p*(-55,000)` with the stored per-trade p (curve slopes 0.8 / 0.35 at scale 1, from `tools/latency_curve`). The tool checks the stored `flat` and `press` columns reproduce.

## Metric and KILL lines

Paired per-trade delta in percentage points of the 0.1 SOL stake: `(pnl_new - pnl_base)/1e8*100`, after each fail model, pooled over discovery + confirmation, and reported per set.

KILL if any of these holds:

1. Mean delta < +1.0 pp under the flat 15% model OR under the pressure-fail model at slope 1.
2. Driven by top 3: after removing the 3 trades with the largest delta, total delta <= 0 or mean delta < +0.5 pp (either model).
3. Driven by one day: after removing the single UTC day with the largest total delta, total delta <= 0 or mean delta < +0.5 pp (either model).
4. Mean delta has the wrong sign in either the discovery set or the confirmation set (both under both models).
5. Fewer than 30 trades have a changed exit (nothing to measure): report INSUFFICIENT, treated as KILL for CONTINUE purposes.

CONTINUE means only: worth a pre-registered forward test on a new block. Also reported, not decisive: count of trades with changed exit, changed-exit share, delta on changed trades only, effect on trades at or below -50% and on winners (the fixed-stop cost, 14.29 to 21.84% of winners down 30%), and bootstrap lower bound of the mean delta (date-cluster, 1,000 draws, seed 1).

## Seals

Aug/Sep exploration blocks only; no HV4/Q50 output, no DBC forward or [10-17,10-24) window, no meta-capture, no arena rows, EXP-024 Look-2 seal untouched (no October hour), FLOOR G1, LaunchLab holdout and D1 untouched. This analysis reuses discovery/confirmation sets already read by many families (m >= 14 per EXP-024 sec. 9), so even a pass is in-sample for the lab.
