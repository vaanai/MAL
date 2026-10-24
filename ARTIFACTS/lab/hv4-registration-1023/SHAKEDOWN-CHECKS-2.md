# Haiku v4 shakedown pass checks, revision 2 (manager12, written 2026-10-19T14:22:47Z); SUPERSEDES SHAKEDOWN-CHECKS.md (sha256 bd6ddf45145a9eeeaf377dd4834df748868eb9904b9e0ae2da7cdc04e3c3082e)

Correction 2026-10-19T14:22:57Z: the first write (sha256 45546ba32e146cb494893150b43e4ad14840d53b9fb2f78f0e220437bb4e028d) lost three field names in check 6 to shell backtick expansion; restored within minutes, before any stub run or any reference to the file. Nothing else changed.

Revised per quant-proof's OK on PR #739 at 8eaf22d (comment 6269421920, items 3a-3f). Written before the 24 h stub at the merged #739 sha starts. Counts and timings only. The stub's engine ledger is never opened; only decision logs (incl. SCAN timing rows), cards, status files and credit counters are read. StubModel trades are not evidence for any book.

## Run (unchanged from revision 1)
- One 24 h stub (HV4_MODE=stub, StubModel) at the merged sha that becomes HV4_FROZEN_SHA, MAL_HV4_RPC=helius set explicitly, lane H off (DESIGN Note N2), HV4_FEED_DELAY_SLOTS=7 (job #1218), own root/registry/ledger under /data/mal/arena/_shakedown-hv4-1019b, script ops/hv4-feed-shakedown.sh stub.
- One deliberate restart about 12 h in (cancel, ps-verify, resubmit the same command at the same sha and root; the script reuses the registered clock).
- Start by 2026-10-20T00:00Z for T0 = 2026-10-21T00:00Z; otherwise T0 moves to the first 00:00Z or 12:00Z at least 24 h after its start.

## Pass checks (all must hold)
1. 0 as_of_refusal rows and 0 LeakageError.
2. chain_stale <= 2% of cards on hot pools (tagged, on the board, or top 25 by 60-min volume), for CON and for BIG separately.
3. Every chain_unavailable card's pool was first seen by the scanner within one mint refresh interval (10 min) before that scan.
4. Cards with a null holders block <= 10% of cards, for CON and for BIG separately.
5. Chain calls <= 1,500 in every rolling hour and <= 40,000 per UTC day, computed from the chain counter's own running call count (chain-credits.json / credit_report), not only from the prefetcher's spend file; the hour that crosses 00:00Z and the hour that crosses the restart are named, each <= 1,500 with both processes counted. spend_state_reset, spend_write_errors and state_write_errors reported, each expected 0. Helius credits reported.
6. Timing (SCAN log rows `scan_timing` field `scan_wall_ms`, and per-minute `tick_timing` rows): scan wall time p99 <= 2,000 ms and max <= 5,000 ms; exit-check delay (`tick_timing.exit_check_delay_ms` p99/max) p99 <= 2,000 ms and max <= 10,000 ms, outside the deliberate restart and any listed feed gap. The stub does not measure run-mode scan time with Haiku calls inside the scan; that is stated, not assumed.
7. sell_alert = 0 and sell_orphan = 0, or each explained by a cause outside the rule; book drop rows with the "already has a sell" key = 0 (decision logs only).
8. After the deliberate restart: the start row shows lane_h false and the same frozen_sha; the decision-log chain verifies; no duplicate decision rows; chain-credits.json and chain-prefetch-spend.json reload with no reset.
9. Feed: arena feed #1207 writes throughout; data_loss gaps reported; disk reserve not paused (if paused, the shakedown is extended, not passed).
10. Registration printed {"lane_h": false, "n_bots": 9}, and no BST log, book or bot_id exists anywhere in the shakedown files.
Reported per persona: scans, cards, setups by type, chain_unavailable (and how many carried a setup tag), chain_stale, null-holders cards, stub LLM calls, X-arm entries and exits (counts only).
A failed check means fix, new sha, new 24 h stub; T0 moves.

## Declarations before T0 (unchanged)
- A trade whose sell was refused or delayed is kept and counted at the fill it actually got; never dropped or re-priced.
- Every read reports trades with a refused or delayed sell, sell_alert and sell_orphan counts, and the longest wait from the first sell decision to the landed sell.
- The registration records PR pins DESIGN.md's sha256 (with Amendment A1 and Notes N1, N2), this file's sha256, and n_bots 9; it records that solders is out of scope unless the read tool imports tools.h5_executor.
