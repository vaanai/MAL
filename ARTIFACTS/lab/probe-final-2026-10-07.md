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

### Realized P&L against actual tx fees (price vs fees)

| group | n | realized | tx fees | before tx fees | tx fees per trip |
| --- | ---: | ---: | ---: | ---: | ---: |
| 8a6849b | 6 | -100,536,289 | 6,060,000 | -94,476,289 | 1,010,000 |
| a25eb17 | 15 | 27,606,913 | 15,150,000 | 42,756,913 | 1,010,000 |
| 7004b16 | 7 | -80,432,850 | 7,575,000 | -72,857,850 | 1,082,143 |
| faa3192 | 33 | -56,887,764 | 33,330,000 | -23,557,764 | 1,010,000 |
| all (pooled, not a result) | 61 | -210,249,990 | 62,115,000 | -148,134,990 | 1,018,279 |
| faa3192 first 28 | 28 | -14,007,586 | 28,280,000 | 14,272,414 | 1,010,000 |
| faa3192 after the first 28 | 5 | -42,880,178 | 5,050,000 | -37,830,178 | 1,010,000 |

### True exit lag, tape crossing to sell landing (slots), variant `sim_correct`

| build | n | trigger_slot_diff min/p50/p90/max | lag all min/p50/p90/max | lag sl min/p50/p90/max | lag < 0 | lag > 10 | reason disagree |
| --- | ---: | --- | --- | --- | ---: | ---: | ---: |
| 8a6849b | 6 | -524 / -3 / 0 / 0 | 3 / 6 / 528 / 528 | 3 / 7 / 528 / 528 | 0 | 2 | 1 |
| a25eb17 | 15 | -5 / 1 / 2 / 125 | -124 / 1 / 6 / 7 | 0 / 1 / 6 / 6 | 1 | 0 | 0 |
| 7004b16 | 7 | -6 / 1 / 425 / 425 | -424 / 0 / 8 / 8 | -424 / 0 / 8 / 8 | 3 | 0 | 0 |
| faa3192 | 33 | -149 / 0 / 1 / 48 | -46 / 1 / 10 / 151 | -46 / 1 / 44 / 151 | 2 | 3 | 0 |
| fixed builds | 55 | -149 / 0 / 5 / 425 | -424 / 1 / 8 / 151 | -424 / 1 / 10 / 151 | 6 | 3 | 0 |

Trades with no tape or no sim trigger: 0.

### True exit lag, tape crossing to sell landing (slots), variant `live_mark`

| build | n | trigger_slot_diff min/p50/p90/max | lag all min/p50/p90/max | lag sl min/p50/p90/max | lag < 0 | lag > 10 | reason disagree |
| --- | ---: | --- | --- | --- | ---: | ---: | ---: |
| 8a6849b | 6 | -524 / -3 / 0 / 0 | 3 / 6 / 528 / 528 | 3 / 7 / 528 / 528 | 0 | 2 | 1 |
| a25eb17 | 15 | -11 / 0 / 1 / 2 | 0 / 1 / 7 / 13 | 0 / 1 / 5 / 5 | 0 | 1 | 1 |
| 7004b16 | 7 | -6 / 0 / 1 / 1 | 0 / 2 / 8 / 8 | 0 / 1 / 8 / 8 | 0 | 0 | 0 |
| faa3192 | 33 | -149 / 0 / 1 / 48 | -46 / 1 / 10 / 151 | -46 / 1 / 44 / 151 | 2 | 3 | 0 |
| fixed builds | 55 | -149 / 0 / 1 / 48 | -46 / 1 / 8 / 151 | -46 / 1 / 10 / 151 | 2 | 4 | 1 |

Trades with no tape or no sim trigger: 0.

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

Projection (fixed tx and failed-attempt costs scale with 0.05/stake; the pool fee percentage does not): **4.44% at 0.05, 2.79% at 0.25, 2.59% at 0.5** pooled; faa3192 alone 4.47 / 2.83 / 2.63. The earlier finding was about 4.5 / 2.9 / 2.65. The new numbers agree to within the pool-fee estimate (about 0.1 point). **On the final data, price, not fees, made faa3192 lose, so a bigger stake does not by itself fix it.** faa3192, 33 trips: realized −56,887,764 lamports; actual tx fees from the ledger 33 × 1,010,000 = 33,330,000 (every trip paid exactly 1,010,000); before tx fees −56,887,764 + 33,330,000 = −23,557,764, still negative. The first 28 trips (the 10-06 cut) were −14,007,586 realized and +14,272,414 before fees; the 5 trips after them were −42,880,178 realized and −37,830,178 before fees (5 × 1,010,000 = 5,050,000). Those 5 trips, not fees, took the build from near break-even to −56.9M. Per-build rows are in the table "Realized P&L against actual tx fees" above.

**Price impact is not in the projection above.** The projection scales only the fixed tx fees. `tools/probe_sim_calibration.py` re-simulates each entry at other sizes against the same pool state (V-priced; now 0.05, 0.1, 0.25, 0.5 SOL, over the 61 trips). Entry price versus spot, bps, p50 (p90): 0.05 SOL 127.2 (132.8); 0.1 SOL 132.9 (139.1); 0.25 SOL 150.1 (157.8); 0.5 SOL 178.8 (188.9). The roughly 125 bps floor is the pool fee already in the cost above. The size-driven part, relative to 0.05 SOL, is +22.9 bps at 0.25 and +51.6 bps at 0.5, on the entry only. The sell leg's impact was not simulated; if it is similar (inference), the round trip adds about 0.46% of stake at 0.25 and about 1.03% at 0.5, which would put the all-in round trip at about 3.25% and 3.62% rather than 2.79% and 2.59%. So a larger stake does not lower the cost per trade monotonically: the fixed fee shrinks, impact grows.

## Calibration for the simulator

**Exit lag, measured from the price crossing.** The first draft of this note counted lag from the snapshot the live exit fired on (sell `landed_slot` − `snapshot_slot`). That is not what the simulator counts from; the simulator counts from the tape row where price first crosses. I ran `tools/probe_sim_calibration.py` on the final fills against the archived tip tape (`fast-trades-tip`, 45 hourly files 2026-10-05T05 to 2026-10-07T01, each verified against its `.sha256` after decompression, 0 mismatches; the run's hours are all present). All 61 trades have tape and a sim trigger (0 missing). Seal: it simulates only the 61 live-traded mints.
- True lag = sell `landed_slot` − sim trigger slot. `trigger_slot_diff` = sim trigger slot − live `snapshot_slot` (tables above, both variants: `sim_correct` is the tool's primary, `live_mark` uses the mark the live build used).
- **Fixed builds (a25eb17, 7004b16, faa3192; n = 55), `live_mark`:** all exits p50 1, p90 8, max 151; stops (n = 32) p50 1, p90 10, max 151. Primary variant `sim_correct`: all exits p50 1, p90 8, max 151; stops p50 1, p90 10, max 151 (6 of 55 negative, min −424, where the sim crossed after the live sell).
- `trigger_slot_diff` on fixed builds, `live_mark`: p50 0, p90 1, max 48, min −149. So on most trades the live exit fires on the same snapshot the sim crosses on. The tail: the sim crossed many slots before the live snapshot on 7tsWZfjg (−149), E2ye1pnG (−42) and FKcuvH3E (−14), all faa3192, which gives true lags of 151, 44 and 15; and after it on 3Y4FQ9Q2 (+38) and 5AbVQzLZ (+48), which give negative lags. I did not separate sim-versus-live path or mark differences from live poll misses in those trades; the tail is reported as measured.
- 8a6849b, with its 5 s poll, is 6 / 528 for p50 / p90 (n = 6). It is not representative of any later build.
- The snapshot-based lag from the first draft (p50 1, p90 2, max 2 on 55) is a different quantity: the landing delay after the executor decided. It stays true as the landing lag, but it is not the simulator's exit lag.
- **Recommendation:** the realistic exit lag for the simulator is the measured p90, **8 slots for all exits and 10 for stops**, with p50 1. **"Exit lag 2" is the optimistic leg**: it matches the median-to-p90 of the landing delay only, and under-states the tail from the crossing. This measurement does **not** support "exit lag 2 is now measured". Run the lab's realistic cells at the stop p90 (10) as the pessimistic leg and 2 as the optimistic leg, and report both. The tail rests on 55 trades and a handful of outliers (4 with lag > 10, 2 negative), so the p90 is a rough value.
- **Gap-through on stops.** 37 stops over all builds; 7 had already crashed past −40% in the snapshot that fired them (trigger ret min −0.955), and 5 filled worse than −50% against entry. On faa3192, 19 stops, 3 triggered below −40% and filled below −50% (worst −0.955). Fill versus the quote at the trigger is small (faa3192 p50 −0.05 bps; the worst, −171.5 bps, is far smaller than the gap). So the damage is the price already gone between two polls (a 400 ms poll plus a 1-2 slot landing), not slippage at send. A faster exit lag does not fix a single-block rug; at most a stop before the collapse would.

**Entry k.**
- From the ledger alone, the landing is 2 slots after the executor's pool-state read at p50, 3 at p90, 4 at max on faa3192 (n = 33); across all builds 2 / 3 / 5. Landed at read +1 on 4 of 61 round trips, +2 on 43, +3 on 11, +4 on 2, +5 on 1 (faa3192: 3 / 26 / 3 / 1 at +1 / +2 / +3 / +4, of 33).
- k against the migration slot is not in the ledger (see the note above). The measured values from the tip tape in earlier notes: 8a6849b was 11–15 slots from the migration tx (17–22 from the `complete` event; [probe-live-2026-10-05.md](probe-live-2026-10-05.md)); faa3192 was p50 5, p90 6, max 15 (job #285, quoted in the DEC-019 §7 note of 2026-10-06). I did not recompute these here: the tape was out of scope and I have not re-verified them.
- Against the simulator: the k6 deciding cell is near the current p90 (6) and above the current p50 (5). The EXP-020 k2 lead is not reachable by the current build: nothing below 5 was observed at p50.
- **Can live land at k2 to k3?** Not on this build, and the ledger does not show that it can on any. What the numbers say:
  - Of the entry time, decision → send is already small (faa3192 p50 221 ms, p90 453 ms, max 1,937 ms), and the landing after the read is 2 slots (about 800 ms) at p50. Those two parts are about 1 s. The rest of k comes before the executor sees the signal.
  - That part is the trigger. The runner trigger is the `complete` event from the tip follower. The migration-stream probe (job #127) saw the processed migrate tx about 0.7–1.5 s earlier on the same slot (3 events only, per LAB_STATE). One to three slots is the most a processed-migrate trigger can buy, so a trigger at processed commitment is necessary. I infer, not measure, that even with that the best case is k of about 3 to 4: about 1 slot to see the event, then read, build and send, then 1 to 2 slots to land.
  - Priority fee or a Jito tip addresses only the landing slots (the 2 → 1 part: landed at +1 on only 4 of 61 round trips). 500,000 lamports priority did not make +1 the normal case. A tip is untested here, so its value is unknown.
  - So k2 needs a trigger earlier than anything the probe ran, and k3 needs it plus a landing at +1. Neither was observed. A trial should not assume them.

## What the next live build must change

1. **Trigger on the processed migrate event**, not the `complete` event. This is the only lever that moves k by more than 1 slot.
2. **Maximum-k and stale-signal guard.** `max_signal_age_s` was 120; a signal decided 6.2 s after migration was traded (−30.6%). Size the guard from the k distribution (p90 6, max 15) in its own review.
3. **Log the migration slot on every buy row** (and the tip slot of the trigger), so k is in the ledger and not rebuilt from the tape. `pool_slot` is a state-read slot today.
4. **Log the sell-side pool fee** and the send slot, so the fee split needs no estimate.
5. **Retry and failure handling.** Two attempts failed on slippage (one buy, error 6040; one sell, error 6004). The failed sell was a stop on a crash (trigger ret −0.69; min_sol_out 11,965,405 against a 14,076,948 quote) and was retried once. Review the min-out margin and the retry on a falling pool.
6. **Test a priority fee or Jito tip against the landing slot** as its own measured change, one at a time. Not mid-build (DEC-021 §7).
7. **Size.** Fixed costs fall to 0.4% of stake at 0.25 SOL and 0.2% at 0.5; the pool fee (about 2.4%) does not. At 0.25 the round trip still costs about 2.8% before price impact, and about 3.25% with the entry impact doubled for the sell leg (inference, section above). On the final data price, not fees, made faa3192 lose, so size alone does not fix it. A trial's book must beat the all-in cost, on top of the gate.

## Build boundary

The `a25eb17` boundary is 1791222783000 (2026-10-05T17:53:03Z, Helm's 10:53 AM PT switch). The first draft used 1791223983000 (18:13:03Z) in `tools/probe_final_report.py` and `tools/probe_sim_calibration.py`, 20 minutes late. No trade moved between builds on the correction: 6 / 15 / 7 / 33 trips per build before and after (no buy fell in the 17:53:03Z to 18:13:03Z window).

## Probe wallet on chain

`tools/probe_rent_audit.py` (this PR) reads only the public address `5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk` (from the DEC-019 notes; no key file was opened) through the research-0 Helius env file at 4 rps (130 RPC calls; key read in Python, never printed), finalized commitment.
- **Balance now:** 298,773,781 lamports (0.298773781 SOL), context slot 454261347.
- **Funding deposit:** one transfer in, +509,528,770 lamports net of the tx fee (slot 453609941, 2026-10-05T14:45:10Z), from `5ANMBJ8iun8MJvjDgJqVRgz4EsUFSUUQ8MpRXbk2eufi`. The deposit tx is the wallet's first of 126; the tie below holds with a start balance of 0.
- **Other transfers in:** the address-poisoning dust, +1 lamport at 2026-10-06T03:01:52Z (slot 453774457; the 5,000-lamport tx fee was paid by the sender). **Transfers out: none** (0 withdrawals).
- **Ledger signatures:** all 124 live buy and sell signatures (62 + 62) found on chain; their wallet deltas sum to −210,754,990 lamports, which is the executor's −0.210755 SOL exactly (61 round trips −210,249,990 plus the failed buy's −505,000).
- **Tie-out:** 509,528,770 + 1 − 210,754,990 = 298,773,781 = the balance now. It ties to the lamport, over 126 signatures, 0 missing. The sum of all per-tx wallet deltas is also 298,773,781.
- **Token accounts:** none remain (getTokenAccountsByOwner, Token and Token-2022, context slot 454261472). Rent is therefore fully refunded on chain, not just in the ledger: no rent is sitting in open ATAs.
- The deposit tx's own delta (509,528,770) and the 509,528,771 total inflow differ by the 1-lamport dust.

## Hour-of-day sampling

See the hour table. Daily-loss-cap gating (DEC-019 §7 note) made attempts cluster before about 08Z on 10-06, so hours 00–08Z carry 31 of 61 trips and hours 05Z, 09–13Z, 17Z and 22Z none. Small n per bucket: the bucket P&L range (−81.7M at hour 15 on 3 trips to +60.7M at hour 02 on 4 trips) is noise at this n and carries no claim about time of day. A deployment around the clock would draw from other hours than this sample did.

## Caveats

- **n = 61 round trips, one failed buy.** A measurement, not edge evidence, and not part of any gate.
- **Build mixing.** 6, 15, 7 and 33 trips per build, with different latency, exit poll and mark rules. P&L is shown per build; the pooled row is for tie-out only. Do not quote the pooled mean or the 23/37/1 split as a book result.
- **Exit-lag headline.** The true lag (crossing to landing) is 55 fixed-build trips with a heavy tail (max 151); p90 8 for all exits and 10 for stops is a rough value from a few outliers. Sim-versus-live path and mark differences are not separated from live poll misses.
- **k against the migration** is quoted from earlier notes and the DEC, not recomputed here, and not in the ledger.
- **Fees.** The pool fee is an estimate; the rent split uses logged amounts and is confirmed on chain (next section). The size projection includes entry price impact only, with the sell leg's impact inferred.
- **Quant-proof** has not reviewed this note. It makes no claim that a book made money; any sentence comparing live to paper needs that review first (DEC-019 §7).
- Mark source and exit decisions: the sim reproduced 35 of 36 live exits at the 10-06 cut (see [probe-calibration-2026-10-06.md](probe-calibration-2026-10-06.md)); this note does not rerun that check on the 61.
