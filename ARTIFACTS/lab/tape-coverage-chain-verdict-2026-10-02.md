# Trade tape coverage against chain truth: DEC-015 §2.2 verdict, 2026-10-02

DEC-015 §2.2 feed check, the first run with ground truth. Window `[2026-10-01T17, 2026-10-01T19)`, 2 complete UTC hours. Read-only on all three hosts.

- **Fast tape:** read locally on `mal-fast-0`, in MiScusi job #36 (`j_EbMx_nDDcs-CiQ`).
- **Oracle tape:** read over `ssh mal-core-0`.
- **Chain truth:** read over `ssh mal-research-0` from `/data/mal/blocks/truth-1001` (MiScusi job #35, getBlock, 26,988 credits).

Generated report: [tape-coverage-chain-2026-10-01.md](tape-coverage-chain-2026-10-01.md) (JSON alongside). The numbers below are copied from it.

## Verdict

**DEC-015 §2.2: FAIL.** Over the same 2 hours the fast trade tape holds **84.759%** of chain trade identities, against a 95% bar. Oracle's tape holds **66.241%**.

| Slice | Chain rows | Fast coverage | Oracle coverage |
| --- | ---: | ---: | ---: |
| 2026-10-01T17 | 1,043,986 | 82.416% | 60.897% |
| 2026-10-01T18 | 1,149,407 | 86.888% | 71.096% |
| **Pooled** | 2,193,393 | **84.759%** | **66.241%** |

- **By venue (fast / Oracle):** bonding 83.021% / 63.810%; PumpSwap 85.167% / 66.812%.
- **Signature level:** fast 84.664%, Oracle 66.173% of 2,119,549 chain signatures.
- **Fast against Oracle** (the original §2.2 bar) is also a FAIL: 91.334% pooled. This number is not meaningful as a quality bar, because Oracle's tape is itself the more incomplete of the two.
- **Tape rows with no chain counterpart:** fast 0.819%, Oracle 1.019%. Some of these are edge rows from before 17:00 received after it (the margin hours 16 and 19 were not walked).

## The chain side was checked before it was read

`tools.backfill_verify --content` on `truth-1001` for `[17, 19)`:

- 2/2 hours sealed, 0 hours flagged.
- 0 duplicate rows in trades, creates and migrations.
- Trades: 1,043,986 and 1,149,407.
- Slots done 13,452 / 13,355 of spans 13,460 / 13,367.

## Why the fast tape misses trades (diagnosis, not proof)

The fast tape (`mal-fast-trade-tape`, trial drop-in) subscribes to the public `logsSubscribe` websocket at `api.mainnet-beta.solana.com`.

**Reconnects.** In the window it logged 64 websocket closes (code 1006), from heartbeat `reconnects` 20 at 17:00:36Z to 84 at 19:00:36Z, and 55 slot jumps. Those 12 windows show 0 to 14 reconnects each. The worst coverage hour, 17, is the one with the most closes (32 against 16 in hour 18).

**Receive lag.** Lag behind block time in the 12 stats windows ending 17:05Z to 18:55Z: p50 1.28 to 7.152 s, p99 11.43 to 39.557 s. That is above the runner's 5,000 ms stale cap (`STALE_ACTION_MS`), so a forward runner on this feed would also drop many of the rows it does get.

**The socket has been quiet since the window ending 2026-10-02T04:25:40Z.** The 26 windows ending 04:35:41Z to 08:45:43Z show 0 reconnects, lag p50 1.166 to 1.760 s, and p99 1.736 to 16.134 s. Across all 98 trial windows: median p50 1.43 s, median p99 10.3 s, and p99 above 5 s in 73 of 98 windows.

**Not ruled out:**
- The public endpoint dropping notifications silently, without a reconnect.
- Rows lost around each reconnect, beyond the 1 s sleep.

A second chain-truth window in the quiet stretch separates the two: `[2026-10-02T06, 08)`, walked as `/data/mal/blocks/truth-1002` by MiScusi job #44 (ledger #217). If coverage there is at least 95%, the loss is reconnects, which redundant sockets could fix. If it is still well below, the public feed drops trades silently and cannot carry a gate book.

## What this means

1. **The fast tape, as configured, cannot feed the EXP-012 forward book.** EXP-012 was fitted and read on getBlock walks, which are chain-complete. A forward book on a feed that misses 15% of trades (and more in bad hours) sees different features and different fills than the replay did.
2. **The 9 Oracle forward books ran on a tape holding about two-thirds of chain trades** in this window. That is context for the 2026-10-05 kill review (feed quality, alongside the lag breaches). It does not change the review procedure.
3. **Training data is unaffected.** EXP-011 and EXP-012 data and the exploration pool all come from getBlock walks, not from either tape.

## Options for a gate-grade live tape on `mal-fast-0` (for DEC-015, not decided here)

- **A. getBlock tip follower (Helius).**
  - Coverage: complete by construction, with the same source, decoder and row shape as the training walks (`tools/pump_history_backfill`).
  - Cost: 13,355 to 13,452 slots per hour here, about 3.7 slots/s. At 1 credit per getBlock that is about 0.32M credits/day, about 9.7M/month, inside the ~40M/month budget. It needs at least 4 rps (6 with headroom) against the 50 rps plan, beside the walkers' 40.
  - Latency: unmeasured. It is confirmed-block time plus the fetch of a block of about 1.2 MB (16.7 GB wire per hour here).
  - Next step: a short latency probe.
- **B. Redundant public sockets.**
  - Two or more concurrent `logsSubscribe` connections, deduplicated by `(signature, event_index)`. $0.
  - It fixes losses from reconnects only, and does nothing about a p99 lag above the stale cap.
  - Decided by the truth-1002 result.
- **C. Helius websocket feed** (standard `logsSubscribe` on the paid endpoint). Coverage, lag and credit cost on this plan are all unmeasured.

## Not measured here

- Chain rows are stamped by `block_time` in seconds, so this report computes no lag against chain. The lag figures above are the tape's own counters.
- Values (amounts, reserves) were not compared, only identities.
- Nothing here is about any book's P&L.
