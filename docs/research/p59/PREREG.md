# P59 PREREG: FLOOR-SLOW overlays A40 and A41 (written before any overlay was priced)

Written 2026-10-26T22:53Z (`date -u`) on branch claude/p59-floor-overlays from origin/main a8070b41. Lane P59, manager17.
**Exploration only. No book has cleared the promotion gate. FLOOR has 0 out-of-sample trades. Nothing here says any variant makes money.** Registers nothing.

## 0. Question
Should FLOOR-SLOW (draft PR #981: rule `arena_rule_l1.py`, rate 4.6/day, H 33, kill line 4.0 attempts/day) include library overlay A40 and/or A41?
Legitimate only if the rule is fixed before the clock and justified on exploration data that is not FLOOR G1 or any sealed hour.

## 1. Data and seals
- Pricer: `tools/arena_screen.py` at a8070b41 (sha256 b37282c7...), rule `l1_rule_tpfix.py:L1Rule` (hunt24 l4-audit, sha256 fba5dcc1...; `l1_rule.py` sha256 cefdc4c6...), `--workers 2`, deciding cell 1.9 s, 0.25 SOL, rent 0 (kit deciding), **160,000 per send** (`screen_cfg` send=160000, the L3 REPRICE wrapper method), E1 floor, both fail models (flat 15%, pressure slope 1), strand rule total.
- Blocks (exploration pool per the ledger and L3 REPRICE; only these four):
  - DISCOVERY = `/data/mal/clean-view/explore-0814` (August, 14 days).
  - CONFIRMATION = `oracle-insample-2026-09-22_25`, `fast-pool-2026-09-18T23_2026-09-22T00`, `oracle-live-2026-09-25_27` (September, pooled).
  - The Aug/Sep split is my declaration. I did not verify that the library's "discovery +7.2339% / confirmation +4.4670%" used the same split. fresh-0903 is NOT used (tuned-on-neighbour, P7).
- Not opened: FLOOR G1 forward/arena outputs, any October hour, arena tape, HV4/Q50, DBC, meta-capture, LaunchLab holdout. The 09-25..09-27 block contains the void stale-fill window of forward paper only as an archive block; it is the same block L3 already priced.
- Control: the base run must reproduce L3 REPRICE `l1:l1` (n 332, flat mean 6.42%, flat +0.01605 / pressure +0.01285 SOL/trade, 18/19 days positive). If not, stop and report; no overlay number is then quoted.

## 2. The overlays, as the library defines them
- **A40** (lib-a-A40, STRATEGY-TREE F10 row 10a): "Force an exit after 300 seconds, however the price has moved." Evidence: d07 "only robust exit of 89"; g_reachable holding to 420 s cost -9.337% of stake. Mapping to FLOOR (my adaptation, declared now): the time exit moves from decision + 24 h to decision + 300 s (`ExitSpec(300.0, tp=1.5)`); the +50% take-profit (post-buy mark, tpfix) stays active inside the 300 s; no stop; everything else unchanged. Re-entry cooldown (60 s after the exit decision) is unchanged, so a shorter hold can change the trip set.
- **A41** (lib-a-A41, row 10b): "Skip a buy when the on-chain price is more than 15% above the starting (seed) price." Evidence: g_reachable +1.047 pp per attempt. Source rule (h4-boostmid RULE.md:26): buy fails and pays one send if (net/tokens) > 1.15 x decision-time spot. **`tools/arena_stack.py:27 GUARD = 1.15` already implements exactly this in the FLOOR pricer** (guarded buy reverts, pays one send; pre-declared mapping: seed = decision-time spot). So A41 as the library defines it is already inside the base. Pre-declared: A41 = the base, delta exactly 0 by construction, verdict DROP (REDUNDANT, nothing to add). Diagnostic only, not a decision input: a guard-OFF ablation (`guard_ratio=inf`) of the base, to say what the existing guard contributes. No other guard ratio is tried.
- **A40+A41** = A40 (A41 adds nothing).

## 3. Variants priced
base; +A40; +A41 (= base, checked equal to the digit); +both (= +A40, checked equal); diagnostic: base with guard off.

## 4. Kill lines (fixed now; KEEP only if every line holds, else DROP)
For an overlay X, on CONFIRMATION (Sep pooled), kit deciding cell, 160,000 per send:
1. mean delta per attempt (X minus base, percentage points of the 0.25 SOL stake, mean over attempts, as the kit's `mean` column) >= +1.0 pp under BOTH the flat 15% and the pressure-fail slope-1 model.
2. X's total SOL on confirmation after removing the top 3 trades > 0 under both models.
3. X's total SOL after removing the best calendar day > 0 under both models.
4. X's attempts/day (kit trades per calendar day incl. guarded attempts) >= 4.0 on confirmation, and on discovery. Also printed: ratio to base. Caveat disclosed in advance: the kit rate is the in-sample screen count (base 13.3/day in L3), not the arena-tape P0 rate (4.6) that sets H; line 4 is the task's kill line applied to the kit count.
5. Declared extra (stricter than the task text): discovery mean delta > 0 under both models (sign must agree).
Also printed, not decision lines: trade-level CI90 lower bound (1,000 draws, seed 1) of X per model, days positive, median trade.

## 5. Winner's curse and limits, stated in advance
- A40 and A41 were already picked as the best of many exits/guards (A40 "the only robust exit of 89"); their quoted evidence is on the g_reachable BOOST-window population (holds of 300-420 s on V-era post-migration pools), NOT on FLOOR's population (PumpSwap pools aged 24-200 h, q0.5 cap, 24 h hold). Transfer to FLOOR is an assumption, and A40 turns a slow 24 h book into a 5-minute book: a different strategy, not an overlay.
- The four blocks are in-sample for the FLOOR base (best of 51 cells, REPRICE). Confirmation is Sep, but the base rule was already tuned on Aug/Sep, so confirmation here is not out-of-sample for the base. Nothing here is gate evidence (DEC-031 Am.8 item 3).
- No tuning: one cap (300 s), one guard ratio (1.15), one run each. A KEEP would still require a new rule-file blob, new pins, an md5 decision-equivalence proof, a P0 recount (the 4.0 bar is on arena-tape attempts) and QP before registration; a KEEP here is permission to ask, not a registration.
- Also true: even a KEEP would not be clean to add to #981's byte-identical rule file pin (blob cf4cf1c4).
