# DEC-022: Fast-entry build (proposal)

| Field | Value |
| --- | --- |
| **Status** | **Proposed 2026-10-07** (manager9). Nothing in it is live. Phase A is paper only and needs no key. Phase B needs the owner's go, and Helm for custody and host changes. |
| **Decider** | Vaan (owner). Helm owns key custody and any host or network change. |
| **Builds on** | DEC-016 Am.2 (seal), DEC-019 (probe, decommissioned 10-07), DEC-020 §9 (size alone is not a profit path), DEC-021 §7 (pre-live checks). |
| **Does not amend** | The promotion gate, the DEC-014 block budget, EXP-012's 10-16 read, or EXP-021. |

## 1. Why

**Entry latency is one measured constraint on live. Round-trip cost is the other** (DEC-020 §9).
- On the decommissioned probe, live landing k had p50 5. That figure is from job #285; the probe's final note did not re-verify it.
- Under V pricing, EXP-012's CI90 lower bounds fall below 0 at k ≥ 12. The mean stays positive but small ([exp012-latency-virtual-2026-10-04.md](../ARTIFACTS/lab/exp012-latency-virtual-2026-10-04.md)).

**What EXP-020's END-bound grid says (report-only).** The paired k2 − k6 at 0.5 SOL is +1.023% of stake on the flat leg (CI90-date [0.109, 1.851]) and +0.747% on the pressure leg ([0.183, 1.276]).
- This is the best of 4 paired cells per leg.
- It was measured on 27 dates that have had 100+ tries.
- On the flat leg, k2 at 0.25 SOL and both k3 cells have lower bounds below 0.
- It assumes landing at k2 is achievable.
- No kept book is positive without 2026-08-21.

**Speed is a relative lever, not a profit path on its own** (DEC-020 §9). It is worth building because every later candidate needs it, EXP-021 included.

**What was measured** ([fast-entry-trigger-2026-10-07.md](../ARTIFACTS/lab/fast-entry-trigger-2026-10-07.md)).
- **Current trigger:** today's early arm, at tip `complete`, has p50 lead 0 over the runner's current trigger time (the tip's receive of the first PumpSwap print).
- **Processed `migrate` stream:** p50 1,012 ms lead on 262 of 284 graduations.
- **Earlier of the two:** p50 996 ms, on all 284.

**What the lead is not.** It is not a lead over the first swap on chain. It is mostly confirmation and getBlock delay, and it does not lower landing k one for one. The probe note infers a best case of k ≈ 3–4 with a processed trigger.

## 2. What the build is

1. **Trigger.** Use the earlier of two signals per mint:
   - a decoded `migrate` from the processed stream (`tools/fast_grad_stream.py`, its watch-set logic unchanged);
   - the tip `complete`.
   A migrate seen first on the stream never waits for the tip.
2. **Gate.** Answer the EXP gate at the trigger, as the early arm already does (evaluation delay p50 0 ms, job #368).
   - Use the same frozen model, threshold and features as the book being traded.
   - A stream row must name the mint, and its canonical pool must equal the derived PDA, or the mint is skipped.
3. **Send path.** Pre-build the buy:
   - cached blockhash;
   - ATA creation in the same tx;
   - the PDA computed from the mint, not read from RPC.
   Send at the trigger. Keep every DEC-019 code-level clamp: whitelist before signing, no re-buy, loss cap.
4. **Price guard.** The buy carries a max-price bound computed from the trigger-time reserves, against the price rising between trigger and landing. If the bound is not met, the trade is skipped and logged.
   - This is not motivated by the probe's 9 of 55 trades with |entry gap| > 500 bps. Those were price falls that filled better than quoted, which is a simulator calibration item.
5. **Exit.** Unchanged from the last reviewed build, except that exit lag is measured, not assumed. The primary is lag 2, with lag 5 as the pessimistic leg and the all-trade stop p90 of 10 as a labelled stress leg ([probe-final-2026-10-07.md](../ARTIFACTS/lab/probe-final-2026-10-07.md); HANDOFF).

## 3. Phases

**Phase A (paper, no key, now).** Run the stream as a MiScusi job on fast-0, ≤ 1.9 GB, as a sidecar.
- **What it logs** per mint: trigger source, trigger time, and the would-send time.
- **Comparison:** against the tip's receive of the first PumpSwap print, and the migrate tx slot.
- **Seal rules** (DEC-016 Am.2), which hold until the 10-16 read:
  - It reads only allowlisted fields.
  - It logs no price or reserve for entered EXP-012 mints.
  - It reads no runner outcome.
  - If the gate is computed inside the runner, that is a runner change and carries the md5 decision-equivalence replay proof.
- **Exit criteria.** These are **engineering acceptance**, chosen after the 6 h test. They are not evidence, and they are set with a margin below the 6 h figures so ordinary noise does not decide them:
  - at least 24 h of stream data;
  - reconnects handled;
  - stream `migrate` coverage ≥ 88% of tip graduations (6 h: 92.3%);
  - the share of graduations with would-send lead > 0 is ≥ 88% (6 h: 94.0%);
  - would-send lead p50 ≥ 600 ms (6 h: 996 ms);
  - decode error rate ≤ 0.1% (6 h: 0 decode errors in 214,004 messages).
- **Runner non-interference (DEC-016 Am.3 measures the runner's timing).** The sidecar shares fast-0 with the sealed paper runner, so the runner's lag is read from `heartbeat.jsonl`, using its allowlisted fields only.
  - **Baseline, job #377:**

    | Window | Lag p50 / p90 (ms) | `ok` false |
    |---|---|---|
    | 10-07 12:28–18:28Z, before the sidecar | 200 / 536 | 0 |
    | 10-07 18:28–23:39Z, sidecar running | 206 / 592 | 0 |
    | 10-06 18:28–24:00Z, no sidecar | 220 / 716 | 0 |
    | 10-05 18:28–24:00Z, no sidecar | 202 / 562 | 0 |

    So far the sidecar has not moved the runner's timing.
  - **Stop rule:** the sidecar stops, and the reason is recorded, if in any fixed 6 h window counted from the sidecar's start the runner's lag p90 exceeds 900 ms, or any heartbeat shows `ok` false. 900 ms is 716 × 1.25 = 895, rounded up; it is about 26% above the highest no-sidecar evening control, 716 ms.

**Phase B (live calibration, owner's go only).**
- **Preconditions:**
  - the reviewed build;
  - Helm's custody;
  - the 1 SOL;
  - a book that has cleared a fresh-block confirmation, so the trades are worth paying for.
- **Measure:** landing k = landed slot − migrate tx slot. This is the simulator's anchor, `_migration_slot`, not the trigger slot.
- **Pass:** landing k **p50 ≤ 2 and p90 ≤ 4**, over at least 30 attempts. Report the full distribution. Otherwise stop and report.
  - **EXP-020** (END-bound grid, report-only) has paired speed support only at k2 and 0.5 SOL, under both fail models: flat CI90 date/trade lower bounds 0.109 / 0.233.
    - Flat k2 at 0.25 SOL (−0.161 / −0.125) and flat k3 at both sizes (−0.541 / −0.445 and −0.402 / −0.252) are below 0.
    - These are differences against k6, not an edge: every cell's CI90 trade lower bound is below 0, and every cell's total is negative without 2026-08-21.
    - There is no 0.05 SOL cell for k < 6, so Phase B at 0.05 SOL measures landing only. It has no paired P&L support.
  - A result with p50 of 3 does **not** support the EXP-020 speed result, and no book may then assume k2 in its simulation.
  - A pass does not let a book assume k2 on every trade either. Books are re-scored at the measured p50 and p90 (DEC-016 Am.3 (a)).
- **Size** stays 0.05 SOL until a confirmed book and DEC-020's conditions allow 0.25.

## 4. Costs and risks

- **Credits.** The 6 h test used 32,923 credits by the stream's own estimate (assumed 3 per 100 KB). A permanent stream, about 130k/day, is reported per day under the owner's credit rule.
- **Misses.** About 8% of graduations (22 of 284) get no stream `migrate`.
  - All 22 reach the first swap within 0–1 slot of `complete`.
  - The tip `complete` gives no lead on 17 of them (6.0% of all graduations) and 94–836 ms on the other 5.
  - The build does not chase them.
- **Fast-0 memory.** The stream and the runner share fast-0, so the stream job keeps the 1.9 GB cap.

## 5. Asked of the owner

- **Phase A starts after this merges.** It is paper only and needs no key, under the owner's standing autonomy for routine steps. The owner can stop it at any time.
- **Phase B is a separate go,** asked only when the §3 Phase B preconditions hold.
