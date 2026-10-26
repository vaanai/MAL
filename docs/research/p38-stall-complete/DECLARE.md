# P38 `p37-stall-to-complete`: counts-only declaration

Lane P38, manager17. Written 2026-10-26 (`date -u` 2026-10-26T17:19:34Z) **before any trigger was evaluated or any completion among stalled curves was counted.**

**Scope.** Counts only. No price, no return, no P&L, no PumpSwap print, no V, no BOOST outcome. The only outcome is the D1 label "the curve reached `complete`" (yes/no), and its time. This is exploration data, never gate evidence. No sentence here or in the result says the idea makes money. No book has cleared the promotion gate.

**Source spec (fixed, not changed here).** `docs/research/p37-idea-sprint/IDEAS.md` §3.2 on `origin/claude/p37-idea-sprint` at `6bb87cba277bf7d093bb19daa559283118b6a0f2` (PR #946), file sha256 `a668b689f7151305e16580ebb5b31df0742e6ead311e64568c2e3d14f0100ba1`.

## 1. Data (allowed blocks only)

| Block | Hours | Ledger status (docs/HOLDOUT_LEDGER.md at origin/main `6a72511c`) |
| --- | --- | --- |
| explore-0814 | `[2026-08-14T12, 2026-08-28T12)`, 336 h | exploration pool |
| fresh-0903 | `[2026-09-03T12, 2026-09-09T12)`, 144 h | exploration pool (spent confirmation block) |
| exp011-0909 | `[2026-09-09T12, 2026-09-15T12)`, 144 h | exploration pool (spent confirmation block) |

Reader: the audit's Parquet copy of these clean views, `/data/mal/audit-1008/tape/{trades,migrations}/<hour>.parquet`, whose `manifest.json` maps each hour to `/data/mal/clean-view/explore-0814/…`, `/data/mal/blocks-clean/fresh-0903/…` or `/data/mal/clean-view/exp011-0909/…`. The tool hard-codes the three hour ranges and refuses any hour outside them, and refuses any manifest source path that is not one of those three roots. No October hour, no arena tape, no forward-1016, no meta-capture, no sealed block (fresh-0802/0808/0828), no HV4/Q50/DBC path. Only `venue = 'pump_bonding'` trade rows and `type = 'complete'` migration rows are loaded. PumpSwap rows are never loaded.

**What I looked at before writing this file (data quality only).** Column schemas; on the 10 hours `2026-08-20T10..T19`: the bonding `quote_reserve` quantiles, the curve invariant `quote_reserve × base_reserve / 3.219e25` (86.6% of bonding rows within 0.1%), reserve continuity between consecutive prints of a mint (99.95% of buys, 99.97% of sells exact), and the last bonding reserve before each `complete` row by band. No stall trigger was evaluated, and no completion rate of any stalled or near-complete set was computed.

## 2. Definitions

- **Units.** Bonding rows carry POST-trade virtual reserves: `quote_reserve` = virtual SOL (lamports), `base_reserve` = virtual tokens (raw, 6 decimals). Time is `block_time` (whole seconds). Order within a mint is `(slot, tx_index, event_index)`.
- **Progress** `p = (vSOL − 30) / 85`, vSOL in SOL. This is the SOL-based progress IDEAS §3.2 uses ("vSOL going from 30 to 115"; p 0.9 ↔ vSOL 106.5). It is **not** pump.fun's token-based UI progress (token-based 0.90 is about p 0.70 here). Complete ↔ vSOL ≈ 115.005.
- **Clean curve** (IDEAS §3.2 step 0: "bonding reserve starts near 30 SOL and stays in band"). Every bonding print of the mint in the block, up to and including the decision instant, satisfies `|quote_reserve × base_reserve / 3.219e25 − 1| < 0.001` (the standard 30 SOL × 1,073,000,000-token curve) and `29.9 SOL ≤ vSOL ≤ 115.1 SOL`. A mint with any failing print at or before the decision is not clean and cannot trigger.
- **Stall trigger (the exact rule).** The state at time s is the post-state of the last print with `block_time ≤ s`. At time t, the window holds the state of the last print with `block_time ≤ t − 1200` (it must exist on the tape, in the same block) and every print with `t − 1200 < block_time ≤ t`. The condition holds at t when, over that window, `min p ≥ 0.90` and `max p − min p < 0.02` ("sat at 90% progress or more for at least 20 min, moving less than 2 pp"), the curve is clean, and the curve has not completed: no `complete` row with `block_time < t` and no window state with vSOL ≥ 115.0 SOL. The **decision instant** t is the first time the condition holds (candidates: every print time and every print time + 1200 s). **One trigger per mint per block** (its first).
- **Complete.** The mint's `complete` row in `migrations` (first by slot). Time to completion = `complete.block_time − t`.
- **Label** `completed_6h`: a complete row with `block_time ≤ t + 21600`. **Censored**: no complete row before the block end and `t + 21600 > block end`. Censored triggers are counted and reported, and are left out of the P(complete) denominator. Blocks are processed separately; no cross-block join (fresh-0903 and exp011-0909 are contiguous, but are not joined).
- **Race share.** Among triggers with `completed_6h`, the share whose complete row has `block_time − t ≤ 4.4 s` (MAL's ~4.4 s effective entry, P32 AUDIT; with whole-second times this is `≤ 4 s`). Also reported: the IDEAS step-1 definition, `≤ 1.9 s` (i.e. `≤ 1 s`). A complete row in the same second as t counts as a race. A complete by another wallet is the only kind possible here (no MAL wallet is on these hours).
- **Day.** UTC date of the decision instant. Full UTC days: 08-15..08-27 (13) and 09-04..09-14 (11), 24 days. The half days 08-14, 08-28, 09-03, 09-15 are reported but not used in the medians.

## 3. Coverage (IDEAS §3.2 step 0, and the manager's step 2)

- Hours present per block for trades and migrations, and any hour in `/data/mal/audit-1008/tape/LENIENT.txt` (parsed with `ignore_errors`) that falls in the blocks.
- **Print-level coverage of a window** = reserve continuity: every consecutive pair of the mint's prints in the window (starting from the last print at or before the window start) satisfies pre-state(next) = post-state(previous) exactly, in both vSOL and vTok (buy: `vSOL_pre = q − sol_lamports`, `vTok_pre = b + token_raw`; sell: the signs flip). A break means a missing or mis-ordered print.
- Reported per block: (a) completes (clean mints whose first tape print is ≥ 30 min before the complete row): share with a continuous chain over `[complete − 30 min, complete]` and share whose completing print (vSOL ≥ 115.0) is on the tape; (b) triggers: share with a continuous chain over the 20-min stall window; (c) triggers that do not complete within 6 h: share with a continuous chain over `[t − 20 min, min(t + 6 h, block end)]`.
- **Refusal R-DATA (IDEAS step 0):** if fewer than 50% of triggers (pooled over the three blocks) have print-level coverage over the 20-min stall window, stop and report "no data". No count verdict then.

## 4. Counts

Per block and pooled: triggers; triggers/day (per full UTC day, with median, min, max); censored triggers; P(complete ≤ 6 h | trigger) with n; time to completion (median, quartiles, share ≤ 1 h); race share at 4.4 s and at 1.9 s; per-day spread of triggers and of P(complete ≤ 6 h). Also, as information only: P(complete by block end), and the trigger p at the decision (quartiles). No price, no return.

## 5. Kill rules (copied from IDEAS §3.2, fixed)

> **Kill rules (fixed now).** K0: median triggers/day < 8. K0b: race share > 10%. K0c: P(complete ≤ 6 h | trigger) < 0.5. K1: at the pricing, mean ≤ 0 under either model, CI90 lower bound ≤ 0, ex-top-3 ≤ 0, or not a majority of the 26 days positive. The rule is not re-tuned on these blocks after any count.

Applied here: K0 on the pooled median over the 24 full UTC days; K0b on the 4.4 s race share (the stricter of the two windows; the 1.9 s share is also shown and K0b also fails if it is above 10%); K0c on the pooled P(complete ≤ 6 h | trigger), censored triggers excluded. K1 is a pricing rule and is **not** applied here. Break-even per IDEAS §3.2 is P(complete) of about 0.65; it is reported next to the count, but it is not a kill rule of this lane.

**Verdict.** KILL if any of K0, K0b, K0c fails. Otherwise ADVANCE-TO-PRICING-DECLARE (a separate, later declaration with its own QP questions, including the EXP-024 Look-2 first-360 s question). An ADVANCE is not a result and says nothing about money.

## 6. Pre-declared refusals

- R-SEAL: any hour outside the three ranges, any manifest source outside the three roots, or any non-bonding / non-complete row class requested → the tool exits non-zero before reading.
- R-DATA: §3, under 50% stall-window coverage → "no data".
- R-THIN: fewer than 30 uncensored triggers pooled → report the counts, verdict NOT_DECIDABLE for K0c and K0b (K0 still applies).
- R-NOTUNE: no change to 0.90, 2 pp, 20 min, 6 h, the clean rule or the race window after the first count. Any variant is a new lane, and is disclosed as having seen these counts.
- R-NOPRICE: no PumpSwap row, no V, no curve-exit price, no return is computed in this lane, including "just to see".
