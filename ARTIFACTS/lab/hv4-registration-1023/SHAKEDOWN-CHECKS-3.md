# Haiku v4 shakedown pass checks, revision 3 (manager, written 2026-10-19T23:48:29Z); SUPERSEDES SHAKEDOWN-CHECKS-2.md (sha256 af5052d5bae4efb683d745ed0fbb9aa6c680bc44a79ddfaf3cef7c3110831b18)

Revised per quant-proof's design OK on PR #768 at 7b499d7 (comment 6279836164). Written before the v2 stub starts. Limits unchanged from revision 2. What changes: checks 1, 3, 5 and 8 read their inputs from the observability side file <root>/hv4-obs.jsonl (schema hv4_obs_v1); rule M is added; the restart and the end get timing rules. Counts and timings only. The stub's engine ledger is never opened. Only decision logs (incl. SCAN timing rows), cards, status files, credit counters and hv4-obs.jsonl are read. StubModel trades are not evidence for any book.

## Run
- One 24 h stub (HV4_MODE=stub, StubModel) at the merged sha (#767 and #768) that becomes HV4_FROZEN_SHA. MAL_HV4_RPC=helius set explicitly. Lane H off (DESIGN Note N2). HV4_FEED_DELAY_SLOTS=7. Prefetch policy v2 and obs rows on: MAL_HV4_PREFETCH_POLICY and MAL_HV4_OBS are not set, and the runner refuses any other setting in live mode. Its own new root/registry/ledger. Script ops/hv4-feed-shakedown.sh stub. DESIGN.md pinned at its sha256 with Note N3: d6e643c7a74170e8991f17c1ec2032995357aa4fed10274269099d9c4624e638.
- One deliberate restart about 12 h in, in an hour that does not start at 00:00Z. Cancel between HH:02Z and HH:10Z, and only after the old process's chain_hour and runner_hour rows for the hour ending at HH:00Z are in hv4-obs.jsonl. Then ps-verify, and resubmit the same command at the same sha and root.
- End: stop between HH:02Z and HH:10Z, once the chain_hour and runner_hour rows are written for the first hour that closes at least 24 h after the first start row. The measured window W runs from the first start row to the end of that hour.
- T0 = the first 00:00Z or 12:00Z that is at least 24 h after the stub's first start row and not before DESIGN's floor (2026-10-21T00:00Z).

## Rule M (measurement)
Checks 1, 3, 5 and 8 are measured from hv4-obs.jsonl. A check is UNMEASURED, and an UNMEASURED check fails, if any of these holds inside W:
- a runner_hour row has obs_write_errors > 0;
- a UTC hour that closes during a process's life has no chain_hour row, or no runner_hour row, from that process;
- a decision-log start row has no runner_start row or no prefetch_start row from the same pid;
- a chain_hour row in the hour before the restart has counter_state_write_errors > 0 (prior_minutes would then be incomplete).

## Pass checks (all must hold)
1. 0 as_of_refusal rows and 0 LeakageError, both in the decision logs AND in hook_errors of every runner_hour row (key "LeakageError"; counts are cumulative per pid). Reported: the minutes of each process after its last runner_hour row, for which hook_errors are not covered.
2. chain_stale <= 2% of cards on hot pools (tagged, on the board, or top 25 by 60-min volume), for CON and for BIG separately. (revision 2 text, unchanged)
3. Every chain_unavailable card's pool was first seen by the scanner within one mint refresh interval (10 min) before that scan. Measured: for each chain_unavailable card (persona, pool, scan instant T from the decision log), take the latest first_seen_by_scanner row with the same pool, the same pid and scan_ms <= T. Its entry may be "first" or "re_entry"; a re-entry counts, because the prefetcher drops a pool's facts at the 30 min drop. The card passes only if such a row exists and T - scan_ms <= 600,000 ms. A card with no such row fails the check. Reported: counts by entry type, T - scan_ms p50 and max, and how many carried a setup tag.
4. Cards with a null holders block <= 10% of cards, for CON and for BIG separately. (revision 2 text, unchanged)
5. Chain calls <= 1,500 in every rolling hour and <= 40,000 per UTC day, computed from the chain counter's own per-minute record, not from the prefetcher's spend file.
   Inputs: per_minute_calls of every chain_hour row in W (each pid's own calls), plus the prior_minutes of the second process's prefetch_start row for the first process's minutes after its last chain_hour row. Where prior_minutes and the first process's own chain_hour row cover the same minute, they must agree; a disagreement fails the check. Each process's minutes are counted once.
   Rolling hour = every run of 60 consecutive minute buckets in W, with the two processes' counts added minute by minute. The hour that crosses 00:00Z and the hour that crosses the restart are named, each <= 1,500 with both processes counted.
   UTC day = counter_day_calls of the chain_hour row whose hour ends at 00:00Z; for a day still open at the end of W, the sum of its per-minute calls in W. Each <= 40,000.
   Reported, each expected 0: spend_state_reset (both prefetch_start rows and the last chain_hour row of each pid); spend_write_errors and counter_state_write_errors (the last chain_hour row of each pid). Helius credits reported per UTC day from the chain_hour credits.
   Also reported, no limit: the largest per-minute count (the 2 rps token bucket allows 120); budget_deferred by class per hour; the spend file's hourly figure next to the counter's.
6. Timing (SCAN log rows `scan_timing` field `scan_wall_ms`, and per-minute `tick_timing` rows): scan wall time p99 <= 2,000 ms and max <= 5,000 ms; exit-check delay (`tick_timing.exit_check_delay_ms` p99/max) p99 <= 2,000 ms and max <= 10,000 ms, outside the deliberate restart and any listed feed gap. The stub does not measure run-mode scan time with Haiku calls inside the scan; that is stated, not assumed. (revision 2 text, unchanged; each process start excluded by the rule in QP-RULING-1019-startup.md (a); scan p99 is the nearest-rank p99 over the scans outside those windows, as quant-proof declared on PR #764, comment 6278593034 item 3)
7. sell_alert = 0 and sell_orphan = 0, or each explained by a cause outside the rule; book drop rows with the "already has a sell" key = 0 (decision logs only). (revision 2 text, unchanged)
8. After the deliberate restart: the start row shows lane_h false and the same frozen_sha; the decision-log chain verifies; no duplicate decision rows; chain-credits.json and chain-prefetch-spend.json reload with no reset. Measured from the obs file:
   - both runner_start rows show the same frozen_sha;
   - both prefetch_start rows show policy "v2" and spend_state_reset 0;
   - the second prefetch_start row's spent_at_load > 0 if the first process booked calls in the 60 min before the cancel;
   - its counter_day_calls_at_start >= the first process's last chain_hour counter_day_calls for the same UTC day (a smaller value is a reset).
9. Feed: arena feed #1207 writes throughout; data_loss gaps reported; disk reserve not paused (if paused, the shakedown is extended, not passed). (revision 2 text, unchanged)
10. Registration printed {"lane_h": false, "n_bots": 9}, and no BST log, book or bot_id exists anywhere in the shakedown files. (revision 2 text, unchanged)
11. (added by the manager before the v2 stub, on quant-proof's recommendation in comment 6279836164) hook_errors other than LeakageError = 0 in every runner_hour row of W, or each explained by a cause outside the rule.

Reported per persona: scans, cards, setups by type, chain_unavailable (and how many carried a setup tag), chain_stale, null-holders cards, stub LLM calls, X-arm entries and exits (counts only) (revision 2 list, unchanged). Also reported from the obs file, no new limit:
- runner_hour hook_errors by type and housekeep errors per hour (cumulative per pid, differenced);
- rss_mb per hour (QP-M1);
- obs rows written;
- chain_stale_served rows split hot/cold, with age_ms p50 and max (check 2 diagnosis), and any hot chain_stale card that has no chain_stale_served row at the same pool and scan_ms.
A failed check means fix, new sha, new 24 h stub; T0 moves.

## Declarations before T0 (revision 2 text, unchanged), plus:
- A trade whose sell was refused or delayed is kept and counted at the fill it actually got; never dropped or re-priced.
- Every read reports trades with a refused or delayed sell, sell_alert and sell_orphan counts, and the longest wait from the first sell decision to the landed sell.
- solders is out of scope unless the read tool imports tools.h5_executor.
- The registration records PR pins DESIGN.md's sha256 (with Amendment A1 and Notes N1, N2, N3), this file's sha256, n_bots 9, prefetch policy v2 and obs on.
