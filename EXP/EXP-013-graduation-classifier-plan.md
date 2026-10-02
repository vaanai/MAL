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
