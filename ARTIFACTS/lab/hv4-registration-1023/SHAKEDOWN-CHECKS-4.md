# Haiku v4 shakedown pass checks, revision 4 (manager, written 2026-10-20T05:09:39Z, before the v2 stub starts; no v2 stub output exists)

Revision 4 is written on quant-proof's design ruling on PR #768 (comment 6281516193, section "SHAKEDOWN-CHECKS revision 4: required lines"), its held-out clarification (comment 6281540017) and its review of the draft on PR #770 (comment 6282420698). SHAKEDOWN-CHECKS-3.md (sha256 55fbba0559327c1e18728596506de11fdf0408ae9bedcee49a21966d9314a3af) and SHAKEDOWN-CHECKS-3-NOTES-1.md (sha256 0f093e68140f021f53979daa7b6251f6b2561b0194e46f3576e77e44fbf1426c) apply unchanged, except for the changes below. Counts and timings only. The stub's engine ledger is never opened. StubModel trades are not evidence for any book.

## Changes against revision 3
1. **Design pin.** The Run section's DESIGN.md pin (sha256 d6e643c7a74170e8991f17c1ec2032995357aa4fed10274269099d9c4624e638, with Note N3) is replaced by DESIGN.md with Note N4: sha256 bcd26affadf1c9900852ee9f2f3489b46c418b5ee393f75d154d965ee6e176d8. The run is at the merged sha of #771 (which replaced #768; frozen code d0d4ca8, policy v2 with N4), which becomes HV4_FROZEN_SHA.
2. **Check 4.** The text is unchanged: cards with a null holders block are at most 10% of cards, for CON and for BIG separately. Under N4 a holders block up to 15 min old counts as present. The age of every block is on the card and is reported below.
3. **Check 5 limits.** Note N4 at the DESIGN.md sha256 pinned in change 1 sets CAP to 2,000, and at the run sha `hv4_prefetch.HOURLY_BUDGET_V2` is 2,000 and `hv4_chain.DEFAULT_DAILY_CALL_CAP` is 48,000. If either does not hold, this file does not apply and check 5 stays at 1,500 and 40,000. Under this file, chain calls must be at most **2,000** in every rolling hour and at most **48,000** per UTC day. Everything else in check 5 (inputs, rolling-hour method, named hours, the expected-0 counters under NOTES-1 N-3) is unchanged. With a fresh root the 48,000 day limit still cannot bind on its own (24 x 2,000 = 48,000); the per-day difference between counter_day_calls and the minute sum is reported (NOTES-1 F4).
4. **New check 12.** The count of `card_refused` rows with reason `holders_stale` must be 0 in total over the decision logs of W, including rows that have no scan row; the CON/BIG split is reported. Any nonzero count is a FAIL: under N4 it means the holders limit constant disagrees between the card check, the prefetcher's serve and the runner's direct chain path.
5. **Reported per persona, no limit:**
   - `holders_age_s`: p50, p90 and max over cards that have a block;
   - the number of cards whose block is older than 5 min, and older than 12 min;
   - null-holders cards by cause (no holders read before T; the newest read is older than 15 min) and by the pool's state at that scan (promoted to hot at that scan, hot before, warm, other), from the `holders_null_served` obs rows;
   - warm holders reads booked, warm holders calls booked, and warm pools deferred, per hour (chain_hour keys `warm_holders_reads_hour`, `warm_holders_calls_hour`, `deferred_warm_hold`).
   A key missing from a row is reported as "not reported". It never passes or fails a check by itself.
6. **Report tool.** `tools/hv4_shakedown_report.py --checks-rev 4` at a reviewed sha. It pins this file's sha256 together with CHECKS-3 and NOTES-1, and a revision 4 PASS requires that pin to match this file.

## Declarations before T0
- The revision 3 declarations apply.
- The registration records PR pins this file's sha256, CHECKS-3's sha256 and NOTES-1's sha256, plus DESIGN.md's sha256 (Amendment A1, Notes N1–N4), n_bots 9, prefetch policy v2 with N4, CAP 2,000, and obs on.
- Helius credits per UTC day from the chain_hour rows are recorded under CLAUDE.md "Credits".
