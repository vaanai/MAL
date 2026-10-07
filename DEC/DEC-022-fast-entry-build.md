# DEC-022: Fast-entry build (proposal)

| Field | Value |
| --- | --- |
| **Status** | **Proposed 2026-10-07** (manager9). Nothing in it is live. Phase A is paper only and needs no key. Phase B needs the owner's go, and Helm for custody and host changes. |
| **Decider** | Vaan (owner). Helm owns key custody and any host or network change. |
| **Builds on** | DEC-016 Am.2 (seal), DEC-019 (probe, decommissioned 10-07), DEC-020 §9 (size alone is not a profit path), DEC-021 §7 (pre-live checks). |
| **Does not amend** | The promotion gate, the DEC-014 block budget, EXP-012's 10-16 read, or EXP-021. |

## 1. Why

- **Entry latency is the measured constraint on live.**
  - On the decommissioned probe, live landing k had p50 5.
  - Under V pricing, EXP-012's edge is gone by k ≥ 12 ([exp012-latency-virtual-2026-10-04.md](../ARTIFACTS/lab/exp012-latency-virtual-2026-10-04.md)).
  - EXP-020's END-bound grid, report-only, puts the paired k2 − k6 at 0.5 SOL at +1.023% of stake on the flat leg and +0.747% on the pressure leg, with both CI90-date lower bounds above 0. No kept book is positive without 2026-08-21.
- **Speed is a relative lever, not a profit path on its own** (DEC-020 §9). It is worth building because every later candidate needs it, EXP-021 included.
- **The lead is measured:** [fast-entry-trigger-2026-10-07.md](../ARTIFACTS/lab/fast-entry-trigger-2026-10-07.md).
  - The processed `migrate` stream leads the tip's first PumpSwap print by p50 1,012 ms (p10 715, p90 1,504), on 262 of 284 graduations.
  - The earlier of stream and tip `complete` covers 284 of 284, with p50 996 ms.
  - Today's early arm, at tip `complete`, has p50 lead 0.

## 2. What the build is

1. **Trigger.** Use the earlier of two signals per mint:
   - a decoded `migrate` from the processed stream (`tools/fast_grad_stream.py`, its watch-set logic unchanged);
   - the tip `complete`.
   A migrate seen first on the stream never waits for the tip.
2. **Gate.** Answer the EXP gate at the trigger, as the early arm already does (evaluation delay p50 0 ms, job #368). Use the same frozen model, threshold and features as the book being traded. A stream row must name the mint, and its canonical pool must equal the derived PDA, or the mint is skipped.
3. **Send path.** Pre-build the buy:
   - cached blockhash;
   - ATA creation in the same tx;
   - the PDA computed from the mint, not read from RPC.
   Send at the trigger. Keep every DEC-019 code-level clamp: whitelist before signing, no re-buy, loss cap.
4. **Price guard.** At the probe, 9 of 55 fixed-build trades had the price fall 10–15% between read and landing (entry-model item). The buy carries a max-price bound computed from the trigger-time reserves. If the bound is not met, the trade is skipped and logged.
5. **Exit.** Unchanged from the last reviewed build, except that exit lag is measured, not assumed. The primary is lag 2, with lag 5 as the pessimistic leg (DEC-019 §7).

## 3. Phases

- **Phase A (paper, no key, now).**
  - Run the stream as a MiScusi job on fast-0, ≤ 1.9 GB.
  - Log per mint: trigger source, trigger time, gate answer, and the would-send time.
  - Compare against the tip's first PumpSwap print and the paper runner's entry slot. This is timing only, and the DEC-016 seal still holds.
  - Exit criteria for Phase A:
    - at least 24 h of stream data;
    - reconnects handled;
    - would-send lead p50 ≥ 600 ms over the first-print receive time;
    - coverage ≥ 95% with the tip fallback;
    - no decode error rate above 0.1%.
- **Phase B (live calibration, owner's go only).**
  - It needs the reviewed build, Helm's custody, and the 1 SOL.
  - It also needs a book that has cleared a fresh-block confirmation, so the trades are worth paying for.
  - It measures landing k against the trigger slot. **Pass:** landing k p50 ≤ 3 over at least 30 attempts. Otherwise stop and report.
  - Size stays 0.05 SOL until a confirmed book and DEC-020's conditions allow 0.25.

## 4. Costs and risks

- **Credits.** The 6 h test used about 33k credits by the stream's own estimate (assumed 3 per 100 KB). A permanent stream, about 130k/day, is reported per day under the owner's credit rule.
- **Misses.** About 8% of graduations reach the first swap within 0–1 slot of `complete` and give no lead from either signal. The build does not chase them.
- **Fast-0 memory.** The stream and the runner share fast-0, so the stream job keeps the 1.9 GB cap.

## 5. Asked of the owner

- **Phase A starts after this merges.** It is paper only and needs no key, under the owner's standing autonomy for routine steps. The owner can stop it at any time.
- **Phase B is a separate go,** asked only when the §3 Phase B preconditions hold.
