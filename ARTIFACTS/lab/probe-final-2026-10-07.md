# DEC-019 section 7 closing note: live execution probe, final (2026-10-07)

**This is an execution measurement at 0.05 SOL, not a book result and not gate evidence.** Probe trades never count toward any gate. n = 61 closed round trips.

**Final state.** The owner stopped the probe early; Helm placed STOP at 2026-10-07T01:10:50Z. 62 of 90 live attempts, realized −0.210755 SOL (−210,754,990 lamports), 0 open, last build `faa3192`. Live since 2026-10-05T14:46:17Z.

**Source.** Read-only copy of the fills ledger `/data/mal/probe-final/probe-fills-20261007.jsonl`, sha256 `8c0567ff872e2e62e79d53f8eb4adce88e236cdfa8b8e7d8674a7cd3dd78fdbc`, 196 rows (live buy 62, live sell 62, live skip 31, dryrun buy 21, dryrun sell 20). Only `mode == "live"` rows are used. Tool: `tools/probe_final_report.py` (this PR), which prints every table below; rerun it on that file to reproduce them. No key, no tape, no forward-paper file was read.

**Reconciliation.** The 61 round-trip `pnl_lamports` sum to −210,249,990. One buy failed (`slippage_exceeded`, custom error 6040, tx fee 505,000, no tokens). Together: −210,754,990, which equals the executor's −0.210755 SOL exactly. A sell on 7004b16 failed once (`slippage_exceeded`, 6004) and was retried; its 505,000 fee is carried in the retried sell's `failed_attempt_cost_lamports` and so is already inside that trip's P&L.

## What the probe measured

1. Whether the EXP-012 migrate book's orders land, and how many slots after the pool-state read.
2. The real fee stack at 0.05 SOL and what part of it shrinks with size.
3. The lag from the exit rule firing to the sell landing, in slots (the simulator's "exit lag").
4. How much worse than the quote the fills were, on stops in particular.

It did not measure whether the book has an edge. The probe wallet stake was 0.05 SOL per trade, max 3 open, priority 500,000 lamports.

## Builds

Never pooled for P&L. The boundaries are those in `tools/probe_sim_calibration.py` `BUILDS` (a trade belongs to the last boundary at or before its buy):

| build | from (UTC) | note |
| --- | --- | --- |
| `8a6849b` | start | executor waited for the runner's post-latency `enter` row; 5 s exit poll (bug fixed in #315); send-state mark |
| `a25eb17` | 2026-10-05T17:53:03Z | #307 decision-time intents, #314, #315 |
| `7004b16` | 2026-10-05T20:39:26Z | #324 own-buy double count |
| `faa3192` | 2026-10-05T23:09:56Z | mark from our own buy tx (#331), log-only drift (#332) |

The first three use the send-state mark; only `faa3192` uses the buy-tx mark. The row "all (pooled, not a result)" in each table is arithmetic for tie-out and for the fee projection, not a performance claim.

## Tables (from `tools/probe_final_report.py`)

### Per build (round trips; builds never pooled, last row is arithmetic only)

| build | n | tp | sl | time_stop | realized lamports | mean | median | mean % of 0.05 SOL | failed buys | realized incl. failed buys |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 8a6849b | 6 | 1 | 5 | 0 | -100,536,289 | -16,756,048 | -19,751,192 | -33.51 | 0 | -100,536,289 |
| a25eb17 | 15 | 6 | 8 | 1 | 27,606,913 | 1,840,461 | -17,858,459 | 3.68 | 0 | 27,606,913 |
| 7004b16 | 7 | 2 | 5 | 0 | -80,432,850 | -11,490,407 | -15,317,442 | -22.98 | 0 | -80,432,850 |
| faa3192 | 33 | 14 | 19 | 0 | -56,887,764 | -1,723,872 | -17,162,178 | -3.45 | 1 | -57,392,764 |
| all (pooled, not a result) | 61 | 23 | 37 | 1 | -210,249,990 | -3,446,721 | -17,231,006 | -6.89 | 1 | -210,754,990 |

### Fee split per round trip (mean lamports; sell-leg pool fee is an estimate)

| build | n | base | priority | pool buy est | pool sell est | rent charged | rent refunded | rent net | failed attempts | RT cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 8a6849b | 6 | 10,000 | 1,000,000 | 608,333 | 419,130 | 1,513,840 | 1,513,840 | 0 | 0 | 2,037,464 |
| a25eb17 | 15 | 10,000 | 1,000,000 | 610,000 | 645,259 | 1,513,840 | 1,513,840 | 0 | 0 | 2,265,259 |
| 7004b16 | 7 | 10,000 | 1,000,000 | 610,714 | 482,623 | 1,513,840 | 1,513,840 | 0 | 72,143 | 2,175,480 |
| faa3192 | 33 | 10,000 | 1,000,000 | 610,606 | 601,589 | 1,513,840 | 1,513,840 | 0 | 15,303 | 2,237,498 |
| all (pooled, not a result) | 61 | 10,000 | 1,000,000 | 610,246 | 580,729 | 1,513,840 | 1,513,840 | 0 | 16,557 | 2,217,532 |

### Round-trip cost as % of stake, and projection (fixed parts scale down with stake, pool fee % does not)

| build | base % | priority % | pool est % | failed % | rent net % | RT cost % at 0.05 | at 0.25 | at 0.5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 8a6849b | 0.02 | 2.00 | 2.05 | 0.00 | 0.00 | 4.07 | 2.46 | 2.26 |
| a25eb17 | 0.02 | 2.00 | 2.51 | 0.00 | 0.00 | 4.53 | 2.91 | 2.71 |
| 7004b16 | 0.02 | 2.00 | 2.19 | 0.14 | 0.00 | 4.35 | 2.62 | 2.40 |
| faa3192 | 0.02 | 2.00 | 2.42 | 0.03 | 0.00 | 4.47 | 2.83 | 2.63 |
| all (pooled, not a result) | 0.02 | 2.00 | 2.38 | 0.03 | 0.00 | 4.44 | 2.79 | 2.59 |

### Entry latency (ms; slots = landed_slot − pool-state read slot)

| build | n | dec→send p50/p90/max | send→confirm p50/p90/max | dec→landed p50/p90/max | read→landed slots p50/p90/max |
| --- | ---: | --- | --- | --- | --- |
| 8a6849b | 6 | 2,802 / 3,326 / 3,326 | 1,164 / 1,187 / 1,187 | 3,989 / 4,490 / 4,490 | 2 / 2 / 2 |
| a25eb17 | 15 | 320 / 488 / 628 | 1,182 / 1,259 / 1,261 | 1,567 / 1,655 / 1,873 | 2 / 3 / 3 |
| 7004b16 | 7 | 260 / 315 / 315 | 1,179 / 1,677 / 1,677 | 1,426 / 1,952 / 1,952 | 2 / 5 / 5 |
| faa3192 | 33 | 221 / 453 / 1,937 | 1,180 / 1,191 / 1,250 | 1,400 / 1,602 / 2,435 | 2 / 3 / 4 |
| all (pooled, not a result) | 61 | 260 / 1,937 / 3,326 | 1,177 / 1,245 / 1,677 | 1,424 / 2,435 / 4,490 | 2 / 3 / 5 |

### Exit lag in slots (sell landed_slot − the snapshot slot the exit fired on)

| build | n | all p50/p90/max | sl p50/p90/max | tp p50/p90/max | dec→send ms | send→confirm ms |
| --- | ---: | --- | --- | --- | --- | --- |
| 8a6849b | 6 | 4 / 4 / 4 | 4 / 4 / 4 | 3 / 3 / 3 | 1,004 / 1,011 / 1,011 | 1,127 / 1,143 / 1,143 |
| a25eb17 | 15 | 2 / 2 / 2 | 2 / 2 / 2 | 1 / 2 / 2 | 30 / 41 / 79 | 480 / 1,211 / 1,326 |
| 7004b16 | 7 | 1 / 2 / 2 | 1 / 2 / 2 | 1 / 2 / 2 | 36 / 291 / 291 | 486 / 1,481 / 1,481 |
| faa3192 | 33 | 1 / 2 / 2 | 2 / 2 / 2 | 1 / 2 / 2 | 23 / 31 / 61 | 479 / 1,101 / 1,113 |
| all (pooled, not a result) | 61 | 2 / 2 / 4 | 2 / 4 / 4 | 1 / 2 / 3 | 26 / 291 / 1,011 | 481 / 1,127 / 1,481 |

### Stop-loss exits (gap-through)

| build | sl n | exit vs quote bps p50/p90/max | trigger ret p50 / min | realized ret vs entry p50 / min | trigger < −40% | filled < −40% | filled < −50% |
| --- | ---: | --- | --- | --- | ---: | ---: | ---: |
| 8a6849b | 5 | -326 / 56 / 56 | -0.350 / -0.737 | -0.366 / -0.744 | 2 | 2 | 1 |
| a25eb17 | 8 | -12 / 64 / 64 | -0.351 / -0.434 | -0.359 / -0.448 | 1 | 1 | 0 |
| 7004b16 | 5 | -161 / 220 / 220 | -0.365 / -0.896 | -0.329 / -0.913 | 1 | 2 | 1 |
| faa3192 | 19 | -0 / 256 / 320 | -0.343 / -0.955 | -0.355 / -0.956 | 3 | 3 | 3 |
| all (pooled, not a result) | 37 | -1 / 220 / 320 | -0.351 / -0.955 | -0.357 / -0.956 | 7 | 8 | 5 |

### Skips (live)

| reason | n |
| --- | ---: |
| limit:stop_file | 31 |

### Hour of day (UTC hour of the buy; small n per bucket, a sampling picture only)

| UTC hour | trips | P&L lamports | mean |
| --- | ---: | ---: | ---: |
| 00 | 9 | -78,379,454 | -8,708,828 |
| 01 | 4 | 53,797,964 | 13,449,491 |
| 02 | 4 | 60,706,019 | 15,176,505 |
| 03 | 1 | 21,594,277 | 21,594,277 |
| 04 | 1 | -19,235,138 | -19,235,138 |
| 06 | 6 | -37,204,367 | -6,200,728 |
| 07 | 4 | -26,846,586 | -6,711,646 |
| 08 | 2 | -38,168,976 | -19,084,488 |
| 14 | 1 | -20,640,226 | -20,640,226 |
| 15 | 3 | -81,707,464 | -27,235,821 |
| 16 | 2 | 1,811,401 | 905,700 |
| 18 | 2 | 52,355,309 | 26,177,654 |
| 19 | 7 | -50,883,122 | -7,269,017 |
| 20 | 7 | 18,349,032 | 2,621,290 |
| 21 | 6 | -72,647,156 | -12,107,859 |
| 23 | 2 | 6,848,497 | 3,424,248 |


Notes on the tables:
- **Entry slots.** `pool_slot`, `state_slot` and `snapshot_slot` on a buy are all the slot of the executor's own pool-state read (on all 34 rows that carry `pool_slot`, state_slot − pool_slot = 0). They are **not** the migration slot. The ledger therefore holds no k against the migration. The slot column above is `landed_slot − snapshot_slot` (= `slots_between`).
- **decision → landed** is `confirm_seen_ms − decision_t_ms`: the time until the executor saw the landing. It is an upper bound on the true landing time.
- **Entry timing fields** (`latency.*`) exist only on the early builds, and their `chain_to_recv_ms` is measured to block time, not to the migration slot. A conversion to k from them gave about 7 slots on 8a6849b where the tip-tape k was 11–15, so the conversion is not used.
- **Exit lag** is `landed_slot − snapshot_slot` on the sell row, where `snapshot_slot` is the pool snapshot the exit rule fired on. `first_exit_snap_slot` (33 sells, faa3192 only) is the first snapshot after the buy landed, 4–5 slots after the buy: when the position started to be watched, not the trigger. It is not used for lag.
- **Rent** is refundable ATA rent: 1,513,840 lamports charged on each buy and 1,513,840 refunded on each sell, so net 0 on all 61 trips. It is not a cost. The earlier "extra ~1.51M debited per buy" item (DEC-019 §7 note, 2026-10-06) is therefore closed: refundable rent, not cost.
- **Pool fee** is an estimate. The buy row logs `pool_fee_est_lamports` (about 600,000, 1.2% of the stake). The sell leg logs none; the table applies the same rate to gross SOL out.

## Fee split and cross-check

Round-trip cost at 0.05 SOL is 4.44% of stake pooled (4.07% to 4.53% by build, the spread being the pool-fee estimate). It is made up of:
- tx fees 2.02% (base 10,000 + priority 1,000,000 lamports per round trip);
- pool fee about 2.4% (estimate);
- failed attempts 0.03% (two failed attempts, 1,010,000 lamports in total over 61 trips);
- rent 0% net.

Projection (fixed tx and failed-attempt costs scale with 0.05/stake; the pool fee percentage does not): **4.44% at 0.05, 2.79% at 0.25, 2.59% at 0.5** pooled; faa3192 alone 4.47 / 2.83 / 2.63. The earlier finding was about 4.5 / 2.9 / 2.65. The new numbers agree to within the pool-fee estimate (about 0.1 point). The fee, not price, is what kept faa3192 below zero before: +14.3M lamports before tx fees on 28 trips at the 10-06 cut (DEC-019 §7 note).

## Calibration for the simulator

**Exit lag.**
- All 55 round trips on the fixed builds (a25eb17, 7004b16, faa3192): lag of 1 slot on 28, 2 slots on 27, never more. p50 1, p90 2, max 2.
- Stop-loss exits only (the leg that matters): p50 2, p90 2, max 2 on faa3192 (n = 19) and on all fixed builds (n = 32: 14 at 1 slot, 18 at 2). Take-profit exits: 14 at 1 slot, 8 at 2 (n = 22).
- The pre-fix build 8a6849b, with its 5 s poll, was 4 slots (n = 6). It is not representative of any current or planned build.
- **Recommendation:** keep the lab's "exit lag 2" as the primary realistic setting. It is now measured (p50 for stops 2, p90 for everything 2), not assumed. For a conservative leg use 2 as the measured p90; run 3 only as a labelled stress test, since no fixed-build trade exceeded 2. This does not test 1; tp exits at lag 1 are not a reason to lower stops.
- **Gap-through on stops.** 37 stops over all builds; 7 had already crashed past −40% in the snapshot that fired them (trigger ret min −0.955), and 5 filled worse than −50% against entry. On faa3192, 19 stops, 3 triggered below −40% and filled below −50% (worst −0.955). Fill versus the quote at the trigger is small (faa3192 p50 −0.05 bps; the worst, −171.5 bps, is far smaller than the gap). So the damage is the price already gone between two polls (a 400 ms poll plus a 1-2 slot landing), not slippage at send. A faster exit lag does not fix a single-block rug; at most a stop before the collapse would.

**Entry k.**
- From the ledger alone, the landing is 2 slots after the executor's pool-state read at p50, 3 at p90, 4 at max on faa3192 (n = 33); across all builds 2 / 3 / 5. Landed at read +1 on 4 of 62, +2 on 44, +3 on 11, +4 on 2, +5 on 1.
- k against the migration slot is not in the ledger (see the note above). The measured values from the tip tape in earlier notes: 8a6849b was 11–15 slots from the migration tx (17–22 from the `complete` event; [probe-live-2026-10-05.md](probe-live-2026-10-05.md)); faa3192 was p50 5, p90 6, max 15 (job #285, quoted in the DEC-019 §7 note of 2026-10-06). I did not recompute these here: the tape was out of scope and I have not re-verified them.
- Against the simulator: the k6 deciding cell is near the current p90 (6) and above the current p50 (5). The EXP-020 k2 lead is not reachable by the current build: nothing below 5 was observed at p50.
- **Can live land at k2 to k3?** Not on this build, and the ledger does not show that it can on any. What the numbers say:
  - Of the entry time, decision → send is already small (faa3192 p50 221 ms, p90 453 ms, max 1,937 ms), and the landing after the read is 2 slots (about 800 ms) at p50. Those two parts are about 1 s. The rest of k comes before the executor sees the signal.
  - That part is the trigger. The runner trigger is the `complete` event from the tip follower. The migration-stream probe (job #127) saw the processed migrate tx about 0.7–1.5 s earlier on the same slot (3 events only, per LAB_STATE). One to three slots is the most a processed-migrate trigger can buy, so a trigger at processed commitment is necessary. I infer, not measure, that even with that the best case is k of about 3 to 4: about 1 slot to see the event, then read, build and send, then 1 to 2 slots to land.
  - Priority fee or a Jito tip addresses only the landing slots (the 2 → 1 part: landed at +1 on only 4 of 62 buys). 500,000 lamports priority did not make +1 the normal case. A tip is untested here, so its value is unknown.
  - So k2 needs a trigger earlier than anything the probe ran, and k3 needs it plus a landing at +1. Neither was observed. A trial should not assume them.

## What the next live build must change

1. **Trigger on the processed migrate event**, not the `complete` event. This is the only lever that moves k by more than 1 slot.
2. **Maximum-k and stale-signal guard.** `max_signal_age_s` was 120; a signal decided 6.2 s after migration was traded (−30.6%). Size the guard from the k distribution (p90 6, max 15) in its own review.
3. **Log the migration slot on every buy row** (and the tip slot of the trigger), so k is in the ledger and not rebuilt from the tape. `pool_slot` is a state-read slot today.
4. **Log the sell-side pool fee** and the send slot, so the fee split needs no estimate.
5. **Retry and failure handling.** Two attempts failed on slippage (one buy, error 6040; one sell, error 6004). The failed sell was a stop on a crash (trigger ret −0.69; min_sol_out 11,965,405 against a 14,076,948 quote) and was retried once. Review the min-out margin and the retry on a falling pool.
6. **Test a priority fee or Jito tip against the landing slot** as its own measured change, one at a time. Not mid-build (DEC-021 §7).
7. **Size.** Fixed costs fall to 0.4% of stake at 0.25 SOL and 0.2% at 0.5; the pool fee (about 2.4%) does not. At 0.25 the round trip still costs about 2.8%. A trial's book must beat that, on top of the gate.

## Hour-of-day sampling

See the hour table. Daily-loss-cap gating (DEC-019 §7 note) made attempts cluster before about 08Z on 10-06, so hours 00–08Z carry 31 of 61 trips and hours 05Z, 09–13Z, 17Z and 22Z none. Small n per bucket: the bucket P&L range (−81.7M at hour 15 on 3 trips to +60.7M at hour 02 on 4 trips) is noise at this n and carries no claim about time of day. A deployment around the clock would draw from other hours than this sample did.

## Caveats

- **n = 61 round trips, one failed buy.** A measurement, not edge evidence, and not part of any gate.
- **Build mixing.** 6, 15, 7 and 33 trips per build, with different latency, exit poll and mark rules. P&L is shown per build; the pooled row is for tie-out only. Do not quote the pooled mean or the 23/37/1 split as a book result.
- **Exit-lag headline.** The slot lag is 55 fixed-build trips, 2 distinct values; the p90 equals the max, so it says "never above 2 in 55", not that 3 cannot happen.
- **k against the migration** is quoted from earlier notes and the DEC, not recomputed here, and not in the ledger.
- **Fees.** The pool fee is an estimate; the rent split uses logged amounts.
- **Quant-proof** has not reviewed this note. It makes no claim that a book made money; any sentence comparing live to paper needs that review first (DEC-019 §7).
- Mark source and exit decisions: the sim reproduced 35 of 36 live exits at the 10-06 cut (see [probe-calibration-2026-10-06.md](probe-calibration-2026-10-06.md)); this note does not rerun that check on the 61.
