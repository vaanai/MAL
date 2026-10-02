# EXP-013 (plan, exploration stage): graduation-completion classifier

| Field | Value |
| --- | --- |
| **Status** | **Exploration plan.** It fixes the screen and the tries cap before any code or result exists. It is not a pre-registration. A confirmation pre-registration follows only if this screen passes. |
| **Date** | 2026-10-02 |
| **Owner direction** | Push toward profit with a genuinely different signal (after DEC-017's three variants failed, [dec017-candidates note](../ARTIFACTS/lab/dec017-candidates-2026-10-02.md)). |
| **Target confirmation block** | The reserved, unread backup block `[2026-08-28T12, 2026-09-03T12)` (docs/HOLDOUT_LEDGER.md), read once, k = 1, if and only if this screen passes. |
| **Prior odds (manager's honest estimate)** | About 20% or less of clearing the gate. Every non-migrate family tried before lost after the round-trip fee, and the getBlock-only slice has been the weakest source. |

## Hypothesis

At 80% bonding-curve progress, the price path to completion is fixed by the curve (vSOL about 73 → 115, about +145% to graduation). The bet is P(complete within the cap). It is independent of EXP-012 in three ways:
- about half the triggers never graduate, and EXP-012 never sees those mints;
- the holding window ends where EXP-012's entry starts;
- the target is different.

## Fixed design (exploration)

- **Trigger:** the first `pump_bonding` print with curve progress ≥ 0.80.
- **Features:** EXP-012's 18 features, cut at the trigger print, plus four new ones:
  - progress velocity over 60 s;
  - SOL in over 30 s;
  - distinct buyers over 60 s;
  - seconds since create.
- **Model:** S2 `lgb_medium` (EXP-012's hyperparameters, seed 1); threshold at the 90th percentile of pooled OOF.
- **Exit:** buy on the curve; hold through migration; sell at the PumpSwap state at migration + 4 slots. Stop at −30% on the curve; 30-minute cap.
- **Execution:** 0.5 SOL, direct route, both fail models. **Entry at slot + 4 is primary**; slot + 1 is reported as reference only.
- **Data:** the 9-day exploration pool plus verified expansion views (`explore-0814/wN` with `VIEW.sha256`). Never the backup block, the EXP-012 holdout, the EXP-011 block or the forward walk.
- **Tries cap:** at most **3** tries (configurations), each logged in `data/tries.jsonl`.

## Screen (stated before any computation)

Nested leave-one-day-out over all clean days. The screen passes only if **all** of these hold under **both** fail models:
1. pooled mean SOL/trade > 0, with 90% CI lower bound > 0 (1,000 draws, seed 1);
2. ex-top-3 total SOL > 0;
3. more than half of the days positive;
4. 1–3 hold again **on the getBlock-only days alone** (pool A plus the expansion days);
5. the pooled mean stays > 0 at entry slot + 8;
6. mint Jaccard with EXP-012's OOF-selected set ≤ 0.5. The daily-PnL correlation with EXP-012 is reported.

On a FAIL the family is closed and not re-tuned.

## Amendment 1 (2026-10-02, before any real-data run): design clarifications from PR1 (#244)

Fixed before any table is built on real data:

1. **Stop sell delay.** The stop's sell lands k slots after the stop print, the same k as the entry. This is conservative, because lag applies to exits too.
2. **Priority fee.** 500,000 lamports at every k (EXP-012's convention).
3. **Pressure curve.** Evaluated at the entry state and applied to both legs, including the PumpSwap sell, as in EXP-012. Disclosed: the post-migration sell may face more contention than this models.
4. **Missed entries.** An entry after the curve completed or migrated is a MISS that costs the priority fee. It is a row with label 0, kept in training, like EXP-012's MISS rows.
5. **Day and overlap.** A row's day is the trigger day. Screen item 6:
   - Jaccard is computed by mint;
   - the daily-PnL correlation pairs trigger days with EXP-012's migration days, and that mismatch is disclosed.
6. **The screen runs exactly once**, on the 9-day pool plus every `explore-0814/wN` view whose `VIEW.sha256` exists at **2026-10-04T12:00:00Z**. Table builds before then are for debugging only: no model, no LODO and no screen output is computed or read from them. The run's manifest pins the view shas.

## Amendment 2 (2026-10-02, before any real-data run): screen item 4 made explicit and stricter; number reuse; execution assumptions

From a review of #243. No EXP-013 code has run on real data, and no model, LODO or screen output exists.

1. **What "getBlock-only" means.** "Pool A" is the **fast-box getBlock backfill**: the `pump_history_backfill` walker output in `/var/lib/mal/backfill-fast`, served as clean view `fast-pool-2026-09-18T23_2026-09-22T00`. Its rows carry `source: "backfill"` and no `t_recv_ms`, the same feed as the expansion walks. "Fast" names the host it was walked on, **not** the fast live listener. Pools B and C are the Oracle live tape, the non-getBlock source.
2. **Screen item 4 is replaced by two separate items. Both must pass**, under both fail models, with bars 1–3 (mean and CI lower bound > 0, ex-top-3 > 0, more than half of days positive):
   - **4a. All getBlock-sourced days.** Pool A `[2026-09-19T01, 2026-09-22T00)`, plus every `explore-0814/wN` view verified by 2026-10-04T12:00Z (Amendment 1 §6).
   - **4b. The August expansion days alone.** Only the `explore-0814/wN` views from 4a. This is the out-of-period getBlock slice, the source the prior calls weakest, and it gets its own hard bar.

   This is stricter than the original wording, since 4b is an added requirement. No bar is relaxed.
3. **Pinned days.** The screen run's manifest lists each view's hour range and the sha256 of its `VIEW.sha256` file. It also lists the clean-view `VIEW.sha256` for pools A, B and C. The screen refuses a view that isn't listed there.
4. **Number reuse.** The earlier `exp013-candidate` / `tools/exp013_*` refit tooling (#239–#242) was DEC-017 candidate (a), the expanded-pool refit of EXP-012's recipe. It was never a registered EXP-013 and is closed (FAIL, [dec017-candidates note](../ARTIFACTS/lab/dec017-candidates-2026-10-02.md)). This plan is a new, independent family that reuses the number. Its code lives in `tools/exp013_grad_*`.
5. **Execution assumptions, stated in full:**
   - 0.5 SOL per entry, direct route (portal fee 0), priority 500,000 lamports per side at every k;
   - curve buy and PumpSwap sell priced by the existing curve math in `tools/latency_curve.py` and `tools/paper_curve_math.py`, including pool and protocol fees as implemented there;
   - fail models: flat 15% and the pressure curve at scale 1, both as expected values (`mixed_net`), as in EXP-012;
   - a MISS costs the priority fee.
6. **Holdout.** Nothing here changes ledger ownership. The backup block stays reserved and unread until a pre-registration merges after a clean screen.

## Amendment 3 (2026-10-02, before any real-data run): two execution details aligned with EXP-012 and made conservative

From the #244 review:
1. **Cap exit.** The 30-minute cap's sell lands **k slots after the cap instant**, the same k as the entry, like the stop and migration sells. Every exit lags.
2. **Missing entry state.** It is scored as a **MISS** (priority fee lost, label 0), as in EXP-012, not censored.

Rows near each pool's first and last day are flagged in the table: censored mints, capped `secs_since_create`, and mints absent because they trigger after the pool end. The screen reports bars 1–3 with and without the flagged edge days. **The pass decision uses the full set**, so no bar moves.
