# Haiku v4 shakedown checks revision 3, records notes 1 (manager, written 2026-10-20T00:40:29Z, before the v2 stub starts; no stub output exists)

Addendum to SHAKEDOWN-CHECKS-3.md (sha256 55fbba0559327c1e18728596506de11fdf0408ae9bedcee49a21966d9314a3af), which stays unchanged. These notes pin the pass rules that quant-proof's review of the report tool (PR #770 at 74d13ca, comment 6280560461) ruled "OK with a records note, pinned before the stub starts" (points 2, 3, 5), plus two clarifications from the same review (F2, F3). Each is equal to or stricter than the CHECKS-3 text. Nothing here loosens a limit. Counts and timings only.

## N-2 (QP point 2): extra Rule M guards
In addition to the four Rule M conditions, checks 1, 3, 5 and 8 are UNMEASURED (so a completed window FAILS) if any of these holds inside W:
- one pid appears in two runner_start rows;
- a runner_start row has no decision-log start row joined to it (orphan runner_start);
- chain_hour or runner_hour rows come from a pid that joins to no start row;
- two hour rows of the same kind exist for the same pid and the same hour (duplicate hour rows);
- a start row or hour row is malformed or carries no pid.
Join of a decision-log start row to its runner_start: by pid when the start row carries pid (live mode, PR #768 N10); otherwise by time order (the runner_start with the greatest wall_ms at or before the start row, after the previous start row). The report names the join used. An ambiguous join fires Rule M.

## N-3 (QP point 3): check 5's expected-0 counters are pass conditions
spend_state_reset (both prefetch_start rows and the last chain_hour row of each pid), spend_write_errors and counter_state_write_errors (the last chain_hour row of each pid): any value above 0 FAILS check 5. A missing value makes check 5 UNMEASURED.

## N-5 (QP point 5): check 8 "booked calls in the 60 min before the cancel"
"The first process booked calls in the 60 min before the cancel" is read from the chain counter's own per-minute series (chain_hour per_minute_calls plus the second process's prefetch_start prior_minutes) for the whole minute buckets that start after (second process's prefetch_start `process_start_ms` - 60 min) and before `process_start_ms`, which is the hour `spent_at_load` covers (`_spent_last_hour` at load). A first process with no call in those minutes is exempt. A first process that stopped calling more than 60 min before the load is exempt. If `process_start_ms` is missing, the condition is UNMEASURED. The counter counts every attempt by any caller, which is at least the prefetcher's booking, so the condition "spent_at_load > 0" is required at least as often as under the literal reading.

(Revision note, 2026-10-20T00:57:18Z: N-5 corrected before the v2 stub started, on quant-proof's re-review of PR #770 at 4745ade, comment 6280783650. The first version, sha256 be6df321c0d89804b67fac5f1047748af2ee3ea69733f3c27f48dc47ead3a060, said "the 60 minutes ending at the first process's last recorded minute", which mis-stated the ruling; it is kept as SHAKEDOWN-CHECKS-3-NOTES-1.md.bak-v1-be6df321. No stub output existed at either version.)

## N-F2: a LeakageError anywhere is a check 1 failure
A runner_hour hook_errors or housekeep error key ending in "LeakageError" (for example "housekeep:LeakageError") counts in check 1. Check 11's "explained by a cause outside the rule" never applies to a LeakageError.

## N-F3: verdict wording
In a completed window, a check that is UNMEASURED for a reason outside Rule M gives the verdict NOT_PASSED (never PASS, never INCOMPLETE), with the UNMEASURED check named. A failed or not-passed shakedown means fix, new sha, new 24 h stub; T0 moves (as CHECKS-3).

## Also recorded (QP F4, F5; no rule change)
- F4: with a fresh root the 40,000 per UTC day limit cannot bind on its own (24 disjoint hours x 1,500 = 36,000). The report shows, for each closed day, counter_day_calls next to the minute sum in W and their difference.
- F5: first-process calls after its last successful counter state save (if the final saves fail), and calls made while two processes overlap, are invisible to every record and are counted as 0. The guard is the ps-verify step of the restart and stop protocol; the report lists this under "Not measured".

The registration records PR pins this file's sha256 next to SHAKEDOWN-CHECKS-3.md's.
