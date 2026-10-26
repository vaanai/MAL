# P40 DECLARE: p37-curve-floor, COUNTS ONLY. Written before any trigger count, label or price.

Idea: docs/research/p37-idea-sprint/IDEAS.md section 3.3 (PR #946, branch claude/p37-idea-sprint). Closest dead row: p20-dbc-start-floor (docs/research/dbc-p21/REPORT.md: n 150, 97-100% of trips at the time cap). Lane P40, manager17.

This lane computes counts and count labels only. It computes no P&L, no fill, no mark-to-exit, no mean, no CI. It does not price.

## What preceded this file
- Read: IDEAS.md (3.3 and the kill rules), docs/HOLDOUT_LEDGER.md (rows "Exploration expansion", "Fresh confirmation block", "Fast [2026-09-09T12, 2026-09-15T12)": all three are **exploration pool**), the seals brief (/data/mal/hunt-1008/push-1016/hunt30/SEALS-BRIEF.md, sha256 823cc062...), /data/mal/hunt-shared/README.md, bc-bigwin DECLARE.md / sim.py / REPORT.md, the P21 DECLARE.
- Schema only: column names of one bars_1m day file and of tokens.parquet; min/max minute_ms per block dir; the median of last_quote_reserve x last_base_reserve on the 2026-08-20 bonding bars (3.219e25 = 30e9 lamports x 1.073e15 raw, the pump curve's k0). No trigger, peak, floor or label was counted before this file.

## Hours and data (allowed: explore-0814, fresh-0903, exp011-0909 only)
- explore-0814 `[2026-08-14T12, 2026-08-28T12)`; fresh-0903 `[2026-09-03T12, 2026-09-09T12)`; exp011-0909 `[2026-09-09T12, 2026-09-15T12)`.
- Source: `/data/mal/hunt-shared/bars_1m/{explore-0814,fresh-0903,exp011-0909}/*.parquet`, rows with `venue = 'pump_bonding'` only, plus `tokens.parquet` columns `mint, create_ms, has_create, is_mayhem_mode, complete_ms` only. Bars are additionally filtered to minute_ms inside the three blocks' hours. Never read: fast-pool-0918, oracle-insample-0922, any October hour, arena tape, meta-capture, any PumpSwap row, any sealed block.
- Segments: S1 = explore-0814; S2 = fresh-0903 + exp011-0909, contiguous `[2026-09-03T12, 2026-09-15T12)`, so a mint path continues across 09-09T12. A trigger belongs to the block that contains its decision instant.
- Domain: pump bonding-curve rows only (pre-graduation). The graduation label is a D1 outcome on Aug/Sep exploration hours (allowed for exploration). No D2/D3 row, no PumpSwap pool, no first-360 s window is read.

## Curve facts used
- Bonding rows carry POST-trade reserves. q = last_quote_reserve / 1e9 = virtual SOL; k0 = 3.219e25 (lamports x raw). Spot price (SOL per whole token) = q_lamports^2 / (1000 k0) on a clean curve, so a bar price P maps to q_eq(P) = sqrt(P x 1000 x k0) / 1e9 SOL.
- Progress p = (q - 30) / 85. Floor band: p <= 0.01, i.e. q <= 30.85 SOL (spot <= (30.85/30)^2 = 1.0575x start). Peak rule: p >= 0.30, i.e. q >= 55.5 SOL.

## Universe (clean curve, as of each bar)
- `has_create` true, create_ms inside the mint's segment, `is_mayhem_mode` not true (mayhem curves are not constant-product).
- The mint's first bonding bar has q in [29.5, 31] SOL (bc-bigwin-2 clean start).
- A bar is clean if: q in [29.5, 115.1] SOL; last_base_reserve > 0; |q_lamports x base / k0 - 1| <= 0.01; q_eq(high) <= 115.1 SOL; q_eq(low) >= 29.5 SOL. The mint leaves the universe at its first non-clean bar (no trigger at or after that bar; counted as `dirty_pre`).

## Trigger (one per mint, the first instant that qualifies)
Decision instants T are UTC minute boundaries. With bars of the mint with minute_ms < T only:
- (a) peak: the running max of q_eq(high) over the mint's bars >= 55.5 SOL (p >= 0.30);
- (b) age of the peak: T >= end of the first minute that attained that running max + 30 min;
- (c) floor: the close state of the last bar before T has q <= 30.85 SOL;
- (d) quiet = 10 min on the floor: the spot never left the floor band in [T - 10 min, T). A minute counts as on the floor if (no bar in it and the last close before it is in band) or (its bar has q_eq(high) <= 30.85 and the previous bar's close q <= 30.85). The previous-close term covers a minute that starts above the band and drops into it;
- (e) no `complete_ms` <= T;
- (f) every bar of the mint up to T is clean; T < segment end.
Descriptive count only (no rule uses it): the same trigger with the peak measured on close-state q instead of q_eq(high).

## Count labels after T (window (T, T + 24 h]; bars with minute_ms >= T)
A trigger is **censored** if T + 24 h > segment end. Labels stop at the first non-clean bar after T; a trigger with no tp touch before that bar and the bar inside the window is **path-invalid**.
- `tp_touch`: some bar in the window has q_eq(high) >= q_tp, where q_tp is the curve state at which the gross mark of a 0.25 SOL entry equals 2 x 0.25 SOL. Entry tokens t = b_T - q_T b_T / (q_T + 0.9875 x 0.25e9) on the decision-state reserves (1.25% buy fee, no latency, no own-impact in the later path); tp when t x spot >= 0.5 SOL. Gross mark: no sell impact, no sell fee. Sensitivity: fee 1.00%.
- `time_cap` = valid, uncensored trigger with no tp_touch in the window (no stop exists, so a trip without tp ends at the cap; graduation is far above q_tp).
- `p30`: some bar in the window with q_eq(high) >= 55.5 SOL. `grad`: complete_ms in (T, T + 24 h]. Both descriptive (the spec's revival level and graduation).
- `rebound`: first bar in the window with q_eq(high)^2 >= 1.10 x q_T^2 (spot +10% over the decision spot). Time = that bar's minute_ms - T (minute resolution, lower bound by up to 60 s). Also time to tp_touch, p30, grad.
- Every label is a curve-state touch from the tape, not a fill. Our own buy is not in the tape path.

## Depth at the trigger (0.25 SOL)
Per trigger: q_T (virtual SOL), real SOL on the curve = q_T - 30, spot move of a 0.25 SOL buy = ((q_T + 0.9875 x 0.25) / q_T)^2 - 1, and the average-fill premium over spot including the 1.25% fee = (0.25 / t) / (q_T / b_T) - 1. Distribution: min, p10, median, p90, max.

## Outputs
Per block and pooled: triggers; triggers per UTC day (each day listed); censored; path-invalid; valid uncensored; tp_touch, time_cap, p30, grad, rebound counts and shares; median (and p25/p75) minutes to rebound, tp_touch, p30, grad among those that reach them; per-day spread (triggers, time-cap share); depth distribution; exclusion counts (mayhem, unclean start, dirty_pre, completed before T). Written to `data/counts.json`.

Complete UTC days used for triggers/day: 2026-08-15..2026-08-27 (13, explore-0814) and 2026-09-04..2026-09-14 (11; 09-04..09-08 fresh-0903, 09-10..09-14 exp011-0909, 09-09 split at 12:00 and counted whole in S2). Partial days 08-14, 08-28, 09-03, 09-15 are listed but excluded from the medians.

## Kill rules (from IDEAS.md 3.3, fixed; applied to the counts only)
- **K0**: median triggers/day over the 24 complete UTC days < 20 -> KILL.
- **K1** (the p20-dbc-start-floor / lib-b-E14 time-cap analogue): time_cap share >= 95% of valid uncensored triggers -> KILL. Primary denominator excludes censored and path-invalid triggers. Sensitivities printed, not deciding: path-invalid counted as time cap; path-invalid counted as tp; fee 1.00%.
- K2 is a pricing rule and is not applied here.
- Verdict: KILL if K0 or K1 fires, else ADVANCE-TO-PRICING-DECLARE (a separate DECLARE, one pricing run, both fail models, 160,000 lamports per send, K2). No band, peak, quiet, cap or tp retune on these hours after any count, whichever way the verdict goes.

## Disclosures
- The idea was written after p20-dbc-start-floor and bc-bigwin results were seen.
- Minute bars: the peak, floor and touches are at bar resolution (high is the max post-trade spot of the minute's prints); intra-minute order is not used. bc-bigwin found 20% of mints with an unclean start and 32% of completes with a last reserve below 100 SOL (cause unresolved); the k-consistency filter is meant to drop those rows, and dropped mints are counted.
- No price, no P&L, no fail model; m is unchanged by this lane (no priced cell).
- No sentence in this lane says the idea makes money. No book has cleared the promotion gate.
